import datetime

import pytest
from django.contrib.admin.models import DELETION, LogEntry
from django.core.exceptions import ValidationError
from django.utils import timezone

from club.models import Game, Player, Result, Season, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season
from live import actions
from live.actions import RuleError
from live.models import LiveAction
from live.tests.conftest import key, live_game

pytestmark = pytest.mark.django_db


def row(result) -> dict:
    result.refresh_from_db()
    return actions._snapshot(result)


def stage(game) -> str:
    game.refresh_from_db()
    return game.live_stage


def to_stage(game, target):
    """Walk a live tournament forward to ``target``."""
    order = [Game.Stage.REBUYS, Game.Stage.ADDON, Game.Stage.FINAL]
    while stage(game) != target:
        actions.advance_stage(game.pk, key(), None, game.live_stage)
        assert order.index(stage(game)) <= order.index(target)


def undo_restores_exactly(result, do):
    """Run ``do`` (which changes ``result``), undo it, and check every field is back."""
    before = row(result)
    outcome = do()
    assert row(result) != before
    actions.undo(outcome.action.pk, None)
    assert row(result) == before
    return outcome


class TestSeat:
    def test_tour_entry(self, tour):
        game, (alpha, *_) = tour
        assert (alpha.buyin, alpha.rebuys, alpha.addon, alpha.out_order) == (100, 0, False, None)

    def test_cash_step(self, cash):
        game, (alpha, _) = cash
        assert (alpha.buyin, alpha.rebuys, alpha.addon) == (50, None, None)

    def test_prices_come_from_the_season(self):
        game = live_game(SeasonKind.TOUR, entry_price=150)
        player = make_player()
        result = actions.seat(game.pk, key(), None, player.pk).action.result
        assert result.buyin == 150

    def test_twice_refused(self, tour):
        game, (alpha, *_) = tour
        with pytest.raises(RuleError, match="уже за столом"):
            actions.seat(game.pk, key(), None, alpha.player_id)

    def test_tour_only_in_stage_one(self, tour):
        game, _ = tour
        to_stage(game, Game.Stage.ADDON)
        with pytest.raises(RuleError, match="только на этапе 1"):
            actions.seat(game.pk, key(), None, make_player().pk)

    def test_new_player(self, tour):
        game, _ = tour
        outcome = actions.seat_new(game.pk, key(), None, "Дельта", "Дед")
        player = Player.objects.get(name="Дельта")
        assert (player.nickname, player.slug) == ("Дед", "ded")
        assert outcome.action.result.player == player

    def test_new_player_validated(self, tour):
        game, (alpha, *_) = tour
        with pytest.raises(ValidationError):
            actions.seat_new(game.pk, key(), None, alpha.player.name, "")
        assert Result.objects.filter(game=game).count() == 3

    def test_undo_deletes_the_row_and_keeps_the_player(self, tour):
        game, (alpha, *_) = tour
        action = LiveAction.objects.get(result=alpha)
        actions.undo(action.pk, None)
        assert not Result.objects.filter(pk=alpha.pk).exists()
        assert Player.objects.filter(pk=alpha.player_id).exists()

    def test_undo_refused_after_a_rebuy(self, tour):
        game, (alpha, *_) = tour
        seat_action = LiveAction.objects.get(result=alpha)
        actions.rebuy(game.pk, key(), None, alpha.pk)
        with pytest.raises(RuleError, match="уже изменили"):
            actions.undo(seat_action.pk, None)
        assert Result.objects.filter(pk=alpha.pk).exists()


class TestRebuy:
    def test_adds_price_and_count(self, tour):
        game, (alpha, *_) = tour
        actions.rebuy(game.pk, key(), None, alpha.pk)
        actions.rebuy(game.pk, key(), None, alpha.pk)
        assert (row(alpha)["buyin"], row(alpha)["rebuys"]) == (200, 2)

    def test_historical_null_count_starts_at_one(self, tour):
        game, (alpha, *_) = tour
        Result.objects.filter(pk=alpha.pk).update(rebuys=None)
        actions.rebuy(game.pk, key(), None, alpha.pk)
        assert row(alpha)["rebuys"] == 1

    def test_undo(self, tour):
        game, (alpha, *_) = tour
        actions.rebuy(game.pk, key(), None, alpha.pk)
        undo_restores_exactly(alpha, lambda: actions.rebuy(game.pk, key(), None, alpha.pk))

    def test_undo_restores_even_if_the_price_changed(self, tour):
        game, (alpha, *_) = tour
        outcome = actions.rebuy(game.pk, key(), None, alpha.pk)
        Season.objects.filter(pk=game.season_id).update(rebuy_price=70)
        actions.undo(outcome.action.pk, None)
        assert row(alpha)["buyin"] == 100

    @pytest.mark.parametrize("target", [Game.Stage.ADDON, Game.Stage.FINAL])
    def test_closed_after_stage_one(self, tour, target):
        game, (alpha, *_) = tour
        to_stage(game, target)
        with pytest.raises(RuleError, match="Ребаи закрыты"):
            actions.rebuy(game.pk, key(), None, alpha.pk)

    def test_eliminated_player_cannot_rebuy(self, tour):
        game, (alpha, *_) = tour
        actions.eliminate(game.pk, key(), None, alpha.pk)
        with pytest.raises(RuleError, match="уже не в игре"):
            actions.rebuy(game.pk, key(), None, alpha.pk)

    def test_result_of_another_game_refused(self, tour):
        game, _ = tour
        other = make_result(make_game(make_season(2025)), make_player())
        with pytest.raises(RuleError, match="нет в игре"):
            actions.rebuy(game.pk, key(), None, other.pk)


class TestAddon:
    def test_on_and_off(self, tour):
        game, (alpha, *_) = tour
        to_stage(game, Game.Stage.ADDON)
        actions.set_addon(game.pk, key(), None, alpha.pk, True)
        assert (row(alpha)["buyin"], row(alpha)["addon"]) == (150, True)
        actions.set_addon(game.pk, key(), None, alpha.pk, False)
        assert (row(alpha)["buyin"], row(alpha)["addon"]) == (100, False)

    def test_same_state_twice_refused(self, tour):
        game, (alpha, *_) = tour
        to_stage(game, Game.Stage.ADDON)
        actions.set_addon(game.pk, key(), None, alpha.pk, True)
        with pytest.raises(RuleError, match="уже отмечен"):
            actions.set_addon(game.pk, key(), None, alpha.pk, True)
        assert row(alpha)["buyin"] == 150

    def test_undo(self, tour):
        game, (alpha, *_) = tour
        to_stage(game, Game.Stage.ADDON)
        undo_restores_exactly(
            alpha, lambda: actions.set_addon(game.pk, key(), None, alpha.pk, True)
        )

    @pytest.mark.parametrize("target", [Game.Stage.REBUYS, Game.Stage.FINAL])
    def test_only_during_the_break(self, tour, target):
        game, (alpha, *_) = tour
        to_stage(game, target)
        with pytest.raises(RuleError, match="в перерыве"):
            actions.set_addon(game.pk, key(), None, alpha.pk, True)

    def test_not_for_eliminated_players(self, tour):
        game, (alpha, *_) = tour
        actions.eliminate(game.pk, key(), None, alpha.pk)
        to_stage(game, Game.Stage.ADDON)
        with pytest.raises(RuleError, match="уже не в игре"):
            actions.set_addon(game.pk, key(), None, alpha.pk, True)


class TestEliminate:
    def test_order(self, tour):
        game, (alpha, bravo, charlie) = tour
        actions.eliminate(game.pk, key(), None, bravo.pk)
        actions.eliminate(game.pk, key(), None, alpha.pk)
        assert (row(bravo)["out_order"], row(alpha)["out_order"]) == (1, 2)
        assert row(charlie)["out_order"] is None

    def test_last_player_is_the_winner(self, tour):
        game, (alpha, bravo, charlie) = tour
        actions.eliminate(game.pk, key(), None, alpha.pk)
        actions.eliminate(game.pk, key(), None, bravo.pk)
        with pytest.raises(RuleError, match="победитель"):
            actions.eliminate(game.pk, key(), None, charlie.pk)

    def test_not_during_the_break(self, tour):
        game, (alpha, *_) = tour
        to_stage(game, Game.Stage.ADDON)
        with pytest.raises(RuleError, match="В перерыве"):
            actions.eliminate(game.pk, key(), None, alpha.pk)

    def test_in_final_stage(self, tour):
        game, (alpha, *_) = tour
        to_stage(game, Game.Stage.FINAL)
        actions.eliminate(game.pk, key(), None, alpha.pk)
        assert row(alpha)["out_order"] == 1

    def test_undo(self, tour):
        game, (alpha, *_) = tour
        undo_restores_exactly(alpha, lambda: actions.eliminate(game.pk, key(), None, alpha.pk))

    def test_restore_and_its_undo(self, tour):
        game, (alpha, bravo, _) = tour
        actions.eliminate(game.pk, key(), None, alpha.pk)
        actions.eliminate(game.pk, key(), None, bravo.pk)
        undo_restores_exactly(alpha, lambda: actions.restore(game.pk, key(), None, alpha.pk))

    def test_restore_undo_refused_when_the_order_was_taken(self, tour):
        game, (alpha, bravo, _) = tour
        actions.eliminate(game.pk, key(), None, alpha.pk)
        restored = actions.restore(game.pk, key(), None, alpha.pk)
        actions.eliminate(game.pk, key(), None, bravo.pk)  # takes out_order 1 again
        with pytest.raises(RuleError, match="уже изменили"):
            actions.undo(restored.action.pk, None)
        assert (row(alpha)["out_order"], row(bravo)["out_order"]) == (None, 1)

    def test_restore_a_player_still_in_refused(self, tour):
        game, (alpha, *_) = tour
        with pytest.raises(RuleError, match="и так в игре"):
            actions.restore(game.pk, key(), None, alpha.pk)


class TestStages:
    def test_forward(self, tour):
        game, _ = tour
        actions.advance_stage(game.pk, key(), None, Game.Stage.REBUYS)
        assert stage(game) == Game.Stage.ADDON
        actions.advance_stage(game.pk, key(), None, Game.Stage.ADDON)
        assert stage(game) == Game.Stage.FINAL

    def test_stale_screen_does_not_skip_a_stage(self, tour):
        game, _ = tour
        actions.advance_stage(game.pk, key(), None, Game.Stage.REBUYS)
        with pytest.raises(RuleError, match="Этап уже сменился"):
            actions.advance_stage(game.pk, key(), None, Game.Stage.REBUYS)
        assert stage(game) == Game.Stage.ADDON

    def test_final_is_the_last_live_stage(self, tour):
        game, _ = tour
        to_stage(game, Game.Stage.FINAL)
        with pytest.raises(RuleError):
            actions.advance_stage(game.pk, key(), None, Game.Stage.FINAL)

    def test_undo_while_latest(self, tour):
        game, _ = tour
        outcome = actions.advance_stage(game.pk, key(), None, Game.Stage.REBUYS)
        actions.undo(outcome.action.pk, None)
        assert stage(game) == Game.Stage.REBUYS

    def test_undo_refused_after_later_actions(self, tour):
        game, (alpha, *_) = tour
        outcome = actions.advance_stage(game.pk, key(), None, Game.Stage.REBUYS)
        actions.set_addon(game.pk, key(), None, alpha.pk, True)
        with pytest.raises(RuleError, match="уже изменили"):
            actions.undo(outcome.action.pk, None)
        assert stage(game) == Game.Stage.ADDON

    def test_undo_allowed_again_once_later_actions_are_undone(self, tour):
        game, (alpha, *_) = tour
        outcome = actions.advance_stage(game.pk, key(), None, Game.Stage.REBUYS)
        addon = actions.set_addon(game.pk, key(), None, alpha.pk, True)
        actions.undo(addon.action.pk, None)
        actions.undo(outcome.action.pk, None)
        assert stage(game) == Game.Stage.REBUYS


class TestCash:
    def test_top_up_and_undo(self, cash):
        game, (alpha, _) = cash
        actions.top_up(game.pk, key(), None, alpha.pk)
        assert row(alpha)["buyin"] == 100
        undo_restores_exactly(alpha, lambda: actions.top_up(game.pk, key(), None, alpha.pk))

    def test_exit_and_undo(self, cash):
        game, (alpha, _) = cash
        outcome = undo_restores_exactly(
            alpha, lambda: actions.cash_exit(game.pk, key(), None, alpha.pk, 11230, 110)
        )
        assert outcome.action.after["chips_out"] == 11230
        assert outcome.action.after["out_order"] == 1

    def test_exit_writes_chips_payout_and_order(self, cash):
        game, (alpha, bravo) = cash
        actions.cash_exit(game.pk, key(), None, bravo.pk, 0, 0)
        actions.cash_exit(game.pk, key(), None, alpha.pk, 11230, 110)
        values = row(alpha)
        assert (values["chips_out"], values["payout"], values["out_order"]) == (11230, 110, 2)
        assert row(bravo)["chips_out"] == 0

    def test_no_top_up_after_leaving(self, cash):
        game, (alpha, _) = cash
        actions.cash_exit(game.pk, key(), None, alpha.pk, 5000, 50)
        with pytest.raises(RuleError, match="уже не в игре"):
            actions.top_up(game.pk, key(), None, alpha.pk)

    def test_return_then_exit_again_adds_up(self, cash):
        game, (alpha, _) = cash
        actions.cash_exit(game.pk, key(), None, alpha.pk, 2000, 20)
        actions.cash_return(game.pk, key(), None, alpha.pk)
        values = row(alpha)
        assert (values["buyin"], values["out_order"], values["chips_out"]) == (100, None, 2000)
        actions.cash_exit(game.pk, key(), None, alpha.pk, 7530, 75)
        values = row(alpha)
        assert (values["buyin"], values["chips_out"], values["payout"]) == (100, 9530, 95)

    def test_return_undo(self, cash):
        game, (alpha, _) = cash
        actions.cash_exit(game.pk, key(), None, alpha.pk, 2000, 20)
        undo_restores_exactly(alpha, lambda: actions.cash_return(game.pk, key(), None, alpha.pk))

    def test_close_needs_everyone_out(self, cash):
        game, (alpha, bravo) = cash
        actions.cash_exit(game.pk, key(), None, alpha.pk, 5000, 50)
        with pytest.raises(RuleError, match="Не все вышли"):
            actions.close_cash(game.pk, key(), None)
        actions.cash_exit(game.pk, key(), None, bravo.pk, 5000, 50)
        actions.close_cash(game.pk, key(), None)
        assert stage(game) == ""

    def test_tour_actions_refused_in_cash(self, cash):
        game, (alpha, _) = cash
        with pytest.raises(RuleError):
            actions.rebuy(game.pk, key(), None, alpha.pk)
        with pytest.raises(RuleError):
            actions.eliminate(game.pk, key(), None, alpha.pk)

    def test_cash_actions_refused_in_tour(self, tour):
        game, (alpha, *_) = tour
        with pytest.raises(RuleError):
            actions.top_up(game.pk, key(), None, alpha.pk)
        with pytest.raises(RuleError):
            actions.cash_exit(game.pk, key(), None, alpha.pk, 100, 1)


class TestResults:
    def test_save_finishes_the_game(self, tour):
        game, (alpha, bravo, charlie) = tour
        to_stage(game, Game.Stage.FINAL)
        rows = {alpha.pk: (1, 200), bravo.pk: (2, 100), charlie.pk: (3, 0)}
        actions.save_results(game.pk, key(), None, rows)
        assert stage(game) == ""
        assert [(r["place"], r["payout"]) for r in map(row, (alpha, bravo, charlie))] == [
            (1, 200),
            (2, 100),
            (3, 0),
        ]

    def test_saving_is_allowed_when_the_balance_does_not_match(self, tour):
        game, (alpha, bravo, charlie) = tour
        to_stage(game, Game.Stage.FINAL)
        actions.save_results(
            game.pk, key(), None, {alpha.pk: (1, 10), bravo.pk: (None, 0), charlie.pk: (None, 0)}
        )
        assert stage(game) == ""

    def test_only_in_final_stage(self, tour):
        game, (alpha, bravo, charlie) = tour
        with pytest.raises(RuleError):
            actions.save_results(game.pk, key(), None, {})

    def test_player_list_must_match(self, tour):
        game, (alpha, *_) = tour
        to_stage(game, Game.Stage.FINAL)
        with pytest.raises(RuleError, match="изменился"):
            actions.save_results(game.pk, key(), None, {alpha.pk: (1, 300)})

    def test_finished_game_takes_no_more_actions(self, tour):
        game, (alpha, bravo, charlie) = tour
        to_stage(game, Game.Stage.FINAL)
        rows = {alpha.pk: (1, 300), bravo.pk: (2, 0), charlie.pk: (3, 0)}
        actions.save_results(game.pk, key(), None, rows)
        with pytest.raises(actions.GameGone):
            actions.eliminate(game.pk, key(), None, alpha.pk)

    def test_undo_refused_once_finished(self, tour):
        game, (alpha, bravo, charlie) = tour
        outcome = actions.rebuy(game.pk, key(), None, alpha.pk)
        to_stage(game, Game.Stage.FINAL)
        actions.save_results(
            game.pk, key(), None, {alpha.pk: (1, 350), bravo.pk: (2, 0), charlie.pk: (3, 0)}
        )
        with pytest.raises(actions.GameGone):
            actions.undo(outcome.action.pk, None)


class TestIdempotency:
    def test_same_key_applies_once(self, tour):
        game, (alpha, *_) = tour
        same = key()
        first = actions.rebuy(game.pk, same, None, alpha.pk)
        second = actions.rebuy(game.pk, same, None, alpha.pk)
        assert (first.replayed, second.replayed) == (False, True)
        assert second.action == first.action
        assert row(alpha)["buyin"] == 150

    @pytest.mark.parametrize(
        "do",
        [
            lambda game, r, k: actions.eliminate(game.pk, k, None, r.pk),
            lambda game, r, k: actions.advance_stage(game.pk, k, None, Game.Stage.REBUYS),
            lambda game, r, k: actions.seat(game.pk, k, None, make_player().pk),
        ],
        ids=["eliminate", "stage", "seat"],
    )
    def test_replay_skips_rule_checks(self, tour, do):
        # A retry of an applied request must not turn into an error ("уже не в игре").
        game, (alpha, *_) = tour
        same = key()
        do(game, alpha, same)
        assert do(game, alpha, same).replayed

    def test_undo_twice(self, tour):
        game, (alpha, *_) = tour
        outcome = actions.rebuy(game.pk, key(), None, alpha.pk)
        actions.undo(outcome.action.pk, None)
        assert actions.undo(outcome.action.pk, None).replayed
        assert row(alpha)["buyin"] == 100

    def test_start_twice(self, organizer):
        season = make_season(2026, SeasonKind.TOUR)
        same = key()
        date = datetime.date(2026, 9, 28)
        first = actions.start_game(same, organizer, season=season, date=date)
        second = actions.start_game(same, organizer, season=season, date=date)
        assert second.replayed and second.action.game == first.action.game
        assert Game.objects.count() == 1

    def test_cancel_twice(self, organizer):
        # The second request finds no game: nothing more happens, and the view goes to the list.
        game = live_game(SeasonKind.CASH)
        actions.cancel_game(game.pk, organizer)
        with pytest.raises(actions.GameGone):
            actions.cancel_game(game.pk, organizer)
        assert LogEntry.objects.count() == 1


class TestUndoRules:
    def test_window(self, tour):
        game, (alpha, *_) = tour
        outcome = actions.rebuy(game.pk, key(), None, alpha.pk)
        LiveAction.objects.filter(pk=outcome.action.pk).update(
            created_at=timezone.now() - actions.UNDO_WINDOW - datetime.timedelta(seconds=1)
        )
        with pytest.raises(RuleError, match="слишком много времени"):
            actions.undo(outcome.action.pk, None)

    def test_row_changed_since(self, tour):
        game, (alpha, *_) = tour
        first = actions.rebuy(game.pk, key(), None, alpha.pk)
        actions.rebuy(game.pk, key(), None, alpha.pk)
        with pytest.raises(RuleError, match="уже изменили"):
            actions.undo(first.action.pk, None)
        assert row(alpha)["buyin"] == 200

    def test_finish_is_final(self, cash):
        game, (alpha, bravo) = cash
        actions.cash_exit(game.pk, key(), None, alpha.pk, 5000, 50)
        actions.cash_exit(game.pk, key(), None, bravo.pk, 5000, 50)
        outcome = actions.close_cash(game.pk, key(), None)
        with pytest.raises(actions.GameGone):
            actions.undo(outcome.action.pk, None)


class TestStartAndCancel:
    def test_start_tour_and_cash(self, organizer):
        date = datetime.date(2026, 9, 28)
        tour = actions.start_game(key(), organizer, kind=SeasonKind.TOUR, date=date).action.game
        cash = actions.start_game(key(), organizer, kind=SeasonKind.CASH, date=date).action.game
        assert (tour.live_stage, cash.live_stage) == (Game.Stage.REBUYS, Game.Stage.CASH)
        assert tour.started_at is not None
        assert tour.live_actions.get().user == organizer

    def test_resumes_a_live_game_of_the_same_day(self):
        game = live_game(SeasonKind.TOUR)
        outcome = actions.start_game(key(), None, season=game.season, date=game.date)
        assert outcome.action.game == game

    def test_refuses_a_finished_game_of_the_same_day(self):
        season = make_season(2026)
        make_game(season, day=28, month=9)
        with pytest.raises(RuleError, match="уже записана"):
            actions.start_game(key(), None, season=season, date=datetime.date(2026, 9, 28))

    def test_date_must_be_in_the_season_year(self):
        with pytest.raises(RuleError, match="2025"):
            actions.start_game(
                key(), None, season=make_season(2025), date=datetime.date(2026, 1, 3)
            )

    def test_new_year_season_copies_the_latest_rules(self):
        make_season(2024, SeasonKind.TOUR, paid_places=4, entry_price=200)
        make_season(
            2025,
            SeasonKind.TOUR,
            paid_places=2,
            chips_per_lei=50,
            entry_price=150,
            rebuy_price=70,
            addon_price=60,
            rebuy_minutes=90,
            cash_step=20,
            payout_weights="6,4",
            payout_round=10,
        )
        make_season(2025, SeasonKind.CASH, chips_per_lei=10)

        date = datetime.date(2026, 1, 3)
        game = actions.start_game(key(), None, kind=SeasonKind.TOUR, date=date).action.game

        season = game.season
        assert (season.year, season.kind) == (2026, SeasonKind.TOUR)
        assert (season.paid_places, season.chips_per_lei, season.entry_price) == (2, 50, 150)
        assert (season.rebuy_price, season.addon_price, season.rebuy_minutes) == (70, 60, 90)
        assert season.cash_step == 20
        assert (season.payout_weights, season.payout_round) == ("6,4", 10)

    def test_first_season_of_a_kind_gets_the_defaults(self):
        season = actions.next_season(SeasonKind.CASH, 2026)
        assert (season.chips_per_lei, season.cash_step) == (100, 50)

    def test_existing_season_is_used(self):
        season = make_season(2026, SeasonKind.TOUR, entry_price=120)
        assert actions.next_season(SeasonKind.TOUR, 2026) == season
        assert Season.objects.count() == 1

    def test_cancel_deletes_the_game_and_its_log_and_writes_a_log_entry(self, organizer):
        game = actions.start_game(
            key(), organizer, season=make_season(2026), date=datetime.date(2026, 9, 28)
        ).action.game
        assert game.live_actions.count() == 1  # "игра начата"

        actions.cancel_game(game.pk, organizer)

        assert not Game.objects.filter(pk=game.pk).exists()
        assert not LiveAction.objects.exists()
        entry = LogEntry.objects.get()
        assert entry.action_flag == DELETION
        assert (entry.user, entry.object_id, entry.object_repr) == (
            organizer,
            str(game.pk),
            "Турнир 2026, 28.09.2026",
        )
        assert entry.content_type.model_class() is Game
        assert entry.get_change_message() == "Отменена на экране живой игры."

    def test_cancel_refused_once_anyone_is_seated(self, tour, organizer):
        game, _ = tour
        with pytest.raises(RuleError, match="уже за столом"):
            actions.cancel_game(game.pk, organizer)
        assert Game.objects.filter(pk=game.pk).exists()
        assert not LogEntry.objects.exists()

    def test_cancel_refused_for_a_finished_game(self, organizer):
        game = make_game(make_season(2026))
        with pytest.raises(actions.GameGone):
            actions.cancel_game(game.pk, organizer)
        assert Game.objects.filter(pk=game.pk).exists()
