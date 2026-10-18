"""The steps before play (lineup, entry collection), the entry mark, removing a player who never
played, and late entries."""

import datetime

import pytest
from django.utils import timezone

from club.models import Game, Player, Result, SeasonKind
from club.tests.factories import make_player
from live import actions
from live.actions import RuleError
from live.models import LiveAction
from live.tests.conftest import key, lineup_game, live_game, seat

pytestmark = pytest.mark.django_db


def stage(game) -> str:
    game.refresh_from_db()
    return game.live_stage


def change(game, to=None):
    return actions.advance_stage(game.pk, key(), None, stage(game), to)


def fields(result) -> dict:
    """Everything stored about a result row."""
    return Result.objects.filter(pk=result.pk).values().get()


@pytest.fixture(params=[SeasonKind.TOUR, SeasonKind.CASH])
def lineup(request):
    """A tournament (entry 100) or cash game (step 50) in its lineup with three players."""
    game = lineup_game(request.param, entry_price=100)
    return game, seat(game, "Альфа", "Браво", "Чарли")


class TestLineupSeating:
    def test_seated_unpaid_with_the_entry(self, lineup):
        game, results = lineup
        entry = 100 if game.season.kind == SeasonKind.TOUR else 50
        assert {(r.buyin, r.entry_paid) for r in results} == {(entry, False)}

    def test_paid_is_ignored_in_the_lineup(self, lineup):
        game, _ = lineup
        result = actions.seat(game.pk, key(), None, make_player().pk, paid=True).action.result
        assert result.entry_paid is False

    def test_refused_during_the_collection(self, lineup):
        game, _ = lineup
        change(game)
        with pytest.raises(RuleError, match="вернитесь к составу"):
            actions.seat(game.pk, key(), None, make_player().pk)


class TestUnseatInLineup:
    def test_removes_the_row_entirely(self, lineup):
        game, (alpha, *_) = lineup
        outcome = actions.unseat(game.pk, key(), None, alpha.pk)
        assert not Result.objects.filter(pk=alpha.pk).exists()
        assert outcome.action.kind == LiveAction.Kind.UNSEAT
        assert outcome.action.summary == "Убран из состава: Альфа"
        assert game.results.count() == 2

    def test_undo_restores_it_exactly(self, lineup):
        game, (alpha, *_) = lineup
        before = fields(alpha)
        seat_action = LiveAction.objects.get(kind=LiveAction.Kind.SEAT, result=alpha)
        outcome = actions.unseat(game.pk, key(), None, alpha.pk)
        seat_action.refresh_from_db()
        assert seat_action.result_id is None

        actions.undo(outcome.action.pk, None)

        assert fields(alpha) == before
        seat_action.refresh_from_db()
        assert seat_action.result_id == alpha.pk

    def test_undo_refused_once_seated_again(self, lineup):
        game, (alpha, *_) = lineup
        outcome = actions.unseat(game.pk, key(), None, alpha.pk)
        actions.seat(game.pk, key(), None, alpha.player_id)
        with pytest.raises(RuleError, match="уже изменили"):
            actions.undo(outcome.action.pk, None)
        assert game.results.filter(player=alpha.player_id).count() == 1

    def test_same_key_twice_removes_once(self, lineup):
        game, (alpha, *_) = lineup
        request = key()
        first = actions.unseat(game.pk, request, None, alpha.pk)
        second = actions.unseat(game.pk, request, None, alpha.pk)
        assert second.replayed and second.action == first.action


class TestStageChain:
    def test_there_and_back_and_on(self, lineup):
        game, _ = lineup
        play = Game.Stage.REBUYS if game.season.kind == SeasonKind.TOUR else Game.Stage.CASH
        for to, expected in [
            (None, Game.Stage.COLLECT),
            ("lineup", Game.Stage.LINEUP),
            ("collect", Game.Stage.COLLECT),
            (None, play),
        ]:
            previous = stage(game)
            outcome = change(game, to)
            assert stage(game) == expected
            # Each step is undoable while it is the latest one.
            actions.undo(outcome.action.pk, None)
            assert stage(game) == previous
            change(game, to)
            assert stage(game) == expected

    def test_start_restarts_the_game_clock_and_undo_puts_it_back(self, lineup):
        game, _ = lineup
        created = timezone.now() - datetime.timedelta(minutes=40)
        Game.objects.filter(pk=game.pk).update(started_at=created)
        change(game)

        outcome = change(game)

        game.refresh_from_db()
        assert timezone.now() - game.started_at < datetime.timedelta(minutes=1)
        actions.undo(outcome.action.pk, None)
        game.refresh_from_db()
        assert (game.live_stage, game.started_at) == (Game.Stage.COLLECT, created)

    def test_needs_two_players(self):
        game = lineup_game(SeasonKind.TOUR)
        seat(game, "Альфа")
        with pytest.raises(RuleError, match="хотя бы 2 игрока"):
            change(game)
        assert stage(game) == Game.Stage.LINEUP

    def test_starting_with_unpaid_players_is_allowed(self, lineup):
        game, (alpha, *_) = lineup
        change(game)
        actions.set_paid(game.pk, key(), None, alpha.pk, True)
        change(game)
        assert game.results.filter(entry_paid=False).count() == 2
        assert stage(game) in (Game.Stage.REBUYS, Game.Stage.CASH)

    @pytest.mark.parametrize(
        ("kind", "start", "to"),
        [
            (SeasonKind.TOUR, Game.Stage.COLLECT, Game.Stage.CASH),
            (SeasonKind.CASH, Game.Stage.COLLECT, Game.Stage.REBUYS),
            (SeasonKind.TOUR, Game.Stage.LINEUP, Game.Stage.REBUYS),
            (SeasonKind.TOUR, Game.Stage.REBUYS, Game.Stage.LINEUP),
        ],
    )
    def test_no_other_changes(self, kind, start, to):
        game = lineup_game(kind)
        seat(game, "Альфа", "Браво")
        Game.objects.filter(pk=game.pk).update(live_stage=start)
        with pytest.raises(RuleError, match="Такой смены этапа нет"):
            change(game, to)
        assert stage(game) == start

    def test_stale_screen_does_not_change_twice(self, lineup):
        game, _ = lineup
        actions.advance_stage(game.pk, key(), None, "lineup")
        with pytest.raises(RuleError, match="уже сменился"):
            actions.advance_stage(game.pk, key(), None, "lineup")
        assert stage(game) == Game.Stage.COLLECT


class TestPaidMark:
    def test_marks_without_touching_the_buy_in(self, lineup):
        game, (alpha, *_) = lineup
        buyin = alpha.buyin
        outcome = actions.set_paid(game.pk, key(), None, alpha.pk, True)
        alpha.refresh_from_db()
        assert (alpha.entry_paid, alpha.buyin) == (True, buyin)
        assert outcome.action.kind == LiveAction.Kind.PAID_ON

    def test_same_key_twice_changes_once(self, lineup):
        game, (alpha, *_) = lineup
        request = key()
        actions.set_paid(game.pk, request, None, alpha.pk, True)
        second = actions.set_paid(game.pk, request, None, alpha.pk, True)
        assert second.replayed
        assert LiveAction.objects.filter(kind=LiveAction.Kind.PAID_ON).count() == 1

    def test_stale_state_refused(self, lineup):
        game, (alpha, *_) = lineup
        actions.set_paid(game.pk, key(), None, alpha.pk, True)
        with pytest.raises(RuleError, match="уже принят"):
            actions.set_paid(game.pk, key(), None, alpha.pk, True)

    def test_clear_and_undo(self, lineup):
        game, (alpha, *_) = lineup
        actions.set_paid(game.pk, key(), None, alpha.pk, True)
        outcome = actions.set_paid(game.pk, key(), None, alpha.pk, False)
        alpha.refresh_from_db()
        assert alpha.entry_paid is False

        actions.undo(outcome.action.pk, None)

        alpha.refresh_from_db()
        assert alpha.entry_paid is True

    def test_accepted_after_the_start(self, lineup):
        game, (alpha, *_) = lineup
        change(game)
        change(game)
        actions.set_paid(game.pk, key(), None, alpha.pk, True)
        alpha.refresh_from_db()
        assert alpha.entry_paid is True


class TestLateEntry:
    @pytest.mark.parametrize("paid", [True, False])
    def test_tour_during_rebuys(self, paid):
        game = live_game(SeasonKind.TOUR)
        outcome = actions.seat(game.pk, key(), None, make_player().pk, paid=paid)
        assert outcome.action.result.entry_paid is paid
        assert outcome.action.summary.endswith(" · внёс" if paid else " · не сдал")

    @pytest.mark.parametrize("paid", [True, False])
    def test_new_player_in_cash(self, paid):
        game = live_game(SeasonKind.CASH)
        outcome = actions.seat_new(game.pk, key(), None, "Дельта", "", paid=paid)
        assert (outcome.action.result.buyin, outcome.action.result.entry_paid) == (50, paid)

    def test_undo_deletes_the_late_row(self):
        game = live_game(SeasonKind.TOUR)
        outcome = actions.seat(game.pk, key(), None, make_player().pk, paid=True)
        actions.undo(outcome.action.pk, None)
        assert not game.results.exists()


class TestUnseatAfterTheStart:
    @pytest.fixture
    def playing(self):
        game = live_game(SeasonKind.TOUR, entry_price=100)
        return game, seat(game, "Альфа", "Браво", "Чарли")

    def test_allowed_for_a_player_nothing_happened_to(self, playing):
        game, (alpha, *_) = playing
        before = fields(alpha)
        outcome = actions.unseat(game.pk, key(), None, alpha.pk)
        assert not Result.objects.filter(pk=alpha.pk).exists()
        actions.undo(outcome.action.pk, None)
        assert fields(alpha) == before

    def test_allowed_for_a_paid_player(self, playing):
        game, (alpha, *_) = playing
        actions.set_paid(game.pk, key(), None, alpha.pk, True)
        actions.unseat(game.pk, key(), None, alpha.pk)
        assert not Result.objects.filter(pk=alpha.pk).exists()

    @pytest.mark.parametrize(
        "update",
        [
            {"rebuys": 1, "buyin": 150},
            {"addon": True, "buyin": 150},
            {"out_order": 1},
            {"payout": 10},
            {"buyin": 150},
        ],
        ids=["rebuy", "addon", "out", "payout", "buyin"],
    )
    def test_refused_once_anything_happened(self, playing, update):
        game, (alpha, *_) = playing
        Result.objects.filter(pk=alpha.pk).update(**update)
        with pytest.raises(RuleError, match="уже в игре: убрать нельзя"):
            actions.unseat(game.pk, key(), None, alpha.pk)
        assert Result.objects.filter(pk=alpha.pk).exists()

    def test_refused_in_cash_with_chips_counted(self):
        game = live_game(SeasonKind.CASH)
        alpha, _ = seat(game, "Альфа", "Браво")
        Result.objects.filter(pk=alpha.pk).update(chips_out=0)
        with pytest.raises(RuleError, match="убрать нельзя"):
            actions.unseat(game.pk, key(), None, alpha.pk)

    def test_refused_after_a_real_rebuy(self, playing):
        game, (alpha, *_) = playing
        actions.rebuy(game.pk, key(), None, alpha.pk)
        with pytest.raises(RuleError):
            actions.unseat(game.pk, key(), None, alpha.pk)


class TestEliminatePlace:
    def test_stale_place_refused(self):
        game = live_game(SeasonKind.TOUR)
        alpha, bravo, _, _ = seat(game, "Альфа", "Браво", "Чарли", "Дельта")
        actions.eliminate(game.pk, key(), None, alpha.pk, place=4)
        with pytest.raises(RuleError, match="выбыл кто-то ещё"):
            actions.eliminate(game.pk, key(), None, bravo.pk, place=4)
        actions.eliminate(game.pk, key(), None, bravo.pk, place=3)
        bravo.refresh_from_db()
        assert bravo.out_order == 2


class TestCancelBeforePlay:
    @pytest.mark.parametrize("stage_name", [Game.Stage.LINEUP, Game.Stage.COLLECT])
    def test_with_players_seated(self, organizer, lineup, stage_name):
        game, _ = lineup
        Game.objects.filter(pk=game.pk).update(live_stage=stage_name)
        actions.cancel_game(game.pk, organizer)
        assert not Game.objects.filter(pk=game.pk).exists()
        assert not Result.objects.exists()

    def test_still_refused_once_playing(self, organizer):
        game = live_game(SeasonKind.TOUR)
        seat(game, "Альфа")
        with pytest.raises(RuleError, match="такую игру отменить нельзя"):
            actions.cancel_game(game.pk, organizer)


class TestNoUnseatInCollect:
    def test_refused(self, lineup):
        game, (alpha, *_) = lineup
        change(game)
        assert not actions.unseat_allowed(alpha, Game.objects.get(pk=game.pk))
        with pytest.raises(RuleError, match="Состав утверждён: вернитесь к составу"):
            actions.unseat(game.pk, key(), None, alpha.pk)
        assert Result.objects.filter(pk=alpha.pk).exists()


class TestStaleStage:
    @pytest.fixture(params=["collect", "play"])
    def moved(self, request, lineup):
        """The lineup's controls were rendered; then the game moved on."""
        game, results = lineup
        change(game)
        if request.param == "play":
            change(game)
        return game, results

    def test_seat(self, moved):
        game, _ = moved
        with pytest.raises(RuleError, match="Пока вы смотрели, этап сменился."):
            actions.seat(game.pk, key(), None, make_player().pk, paid=False, stage="lineup")
        assert game.results.count() == 3

    def test_seat_new(self, moved):
        game, _ = moved
        with pytest.raises(RuleError, match="этап сменился"):
            actions.seat_new(game.pk, key(), None, "Дельта", "", paid=False, stage="lineup")
        assert not Player.objects.filter(name="Дельта").exists()

    def test_unseat(self, moved):
        game, (alpha, *_) = moved
        with pytest.raises(RuleError, match="этап сменился"):
            actions.unseat(game.pk, key(), None, alpha.pk, stage="lineup")
        assert Result.objects.filter(pk=alpha.pk).exists()

    def test_the_same_stage_passes(self, lineup):
        game, (alpha, *_) = lineup
        actions.unseat(game.pk, key(), None, alpha.pk, stage="lineup")
        actions.seat(game.pk, key(), None, make_player().pk, stage="lineup")
        assert game.results.count() == 3


class TestLateEntryNeedsPaid:
    def test_seat_without_paid_refused(self):
        game = live_game(SeasonKind.TOUR)
        with pytest.raises(RuleError, match="внёс ли игрок деньги"):
            actions.seat(game.pk, key(), None, make_player().pk)
        assert not game.results.exists()

    def test_seat_new_without_paid_refused(self):
        game = live_game(SeasonKind.CASH)
        with pytest.raises(RuleError, match="внёс ли игрок деньги"):
            actions.seat_new(game.pk, key(), None, "Дельта", "")
        assert not Player.objects.filter(name="Дельта").exists()

    def test_lineup_needs_no_paid(self):
        game = lineup_game(SeasonKind.TOUR)
        result = actions.seat(game.pk, key(), None, make_player().pk).action.result
        assert result.entry_paid is False


class TestSupersededUnseat:
    def test_only_the_latest_unseat_undoes(self, lineup):
        game, (alpha, *_) = lineup
        first = actions.unseat(game.pk, key(), None, alpha.pk)
        again = actions.seat(game.pk, key(), None, alpha.player_id).action.result
        latest = actions.unseat(game.pk, key(), None, again.pk)

        with pytest.raises(RuleError, match="уже изменили"):
            actions.undo(first.action.pk, None)
        assert not game.results.filter(player=alpha.player_id).exists()

        actions.undo(latest.action.pk, None)
        assert game.results.get(player=alpha.player_id).pk == again.pk
        # The seat after the first unseat still stands: the first stays superseded.
        with pytest.raises(RuleError, match="уже изменили"):
            actions.undo(first.action.pk, None)

    def test_seat_snapshot_names_the_player(self, lineup):
        game, (alpha, *_) = lineup
        seat_action = LiveAction.objects.get(kind=LiveAction.Kind.SEAT, result=alpha)
        assert seat_action.after["player_id"] == alpha.player_id

    def test_old_seat_snapshot_without_player_does_not_crash(self, lineup):
        game, (alpha, *_) = lineup
        first = actions.unseat(game.pk, key(), None, alpha.pk)
        again = actions.seat(game.pk, key(), None, alpha.player_id)
        # Logged before the player was in SEAT snapshots.
        old = again.action
        old.after = {k: v for k, v in old.after.items() if k != "player_id"}
        old.save(update_fields=["after"])

        # Not recognised as superseded, but the unique clash still refuses it.
        with pytest.raises(RuleError, match="уже изменили"):
            actions.undo(first.action.pk, None)
        # And the old SEAT itself still undoes.
        actions.undo(old.pk, None)
        assert not game.results.filter(player=alpha.player_id).exists()
