"""The blind timer's actions (live.actions) on a frozen clock."""

import datetime

import pytest
from django.utils import timezone

from club.models import BlindLevel, Game, SeasonKind
from club.tests.factories import make_season, make_structure
from live import actions
from live.actions import RuleError
from live.models import ActionKind, BlindTimer, LiveAction
from live.tests.conftest import key, live_game, seat

pytestmark = pytest.mark.django_db

START = datetime.datetime(2026, 9, 28, 17, 0, tzinfo=datetime.UTC)


class FrozenClock:
    def __init__(self):
        self.now = START

    def advance(self, minutes=0, seconds=0):
        self.now += datetime.timedelta(minutes=minutes, seconds=seconds)


@pytest.fixture
def frozen(monkeypatch):
    frozen = FrozenClock()
    monkeypatch.setattr(timezone, "now", lambda: frozen.now)
    return frozen


@pytest.fixture
def structure(db):
    # 1-3: levels of 20 min, 4: the add-on break (15), 5: level, 6: break (10), 7: last level.
    return make_structure()


@pytest.fixture
def timed(frozen, structure):
    """A live tournament with the test structure's timer, paused at level 1."""
    game = live_game(SeasonKind.TOUR)
    actions.start_timer(game.pk, key(), None, structure.pk)
    return game


def state(game):
    timer = BlindTimer.objects.get(game=game)
    return actions._clock(timer, timezone.now())


def where(game) -> tuple[int, str]:
    """(position, remaining 'mm:ss') of the clock now."""
    current = state(game)
    return current.current.position, current.remaining_text


def run(game):
    return actions.set_paused(game.pk, key(), None, False)


def pause(game):
    return actions.set_paused(game.pk, key(), None, True)


def minutes_of(game) -> list[int]:
    return list(BlindTimer.objects.get(game=game).levels.values_list("minutes", flat=True))


class TestStart:
    def test_new_timer_is_paused_at_level_one(self, timed, frozen):
        frozen.advance(minutes=30)
        assert where(timed) == (1, "20:00")
        assert state(timed).paused
        timer = BlindTimer.objects.get(game=timed)
        assert timer.structure_name == "Тестовая структура"
        assert len(timer.display_token) >= 40

    def test_start_game_with_the_season_default(self, frozen, structure):
        season = make_season(2026, SeasonKind.TOUR, default_blinds=structure)
        game = actions.start_game(
            key(), None, season=season, date=datetime.date(2026, 9, 28), blinds="season"
        ).action.game
        assert minutes_of(game) == [20, 20, 20, 15, 20, 10, 20]

    def test_start_game_without_a_timer(self, frozen, structure):
        season = make_season(2026, SeasonKind.TOUR, default_blinds=structure)
        game = actions.start_game(
            key(), None, season=season, date=datetime.date(2026, 9, 28), blinds=None
        ).action.game
        assert not BlindTimer.objects.filter(game=game).exists()

    def test_season_without_a_default_starts_without_a_timer(self, frozen):
        season = make_season(2026, SeasonKind.TOUR)
        game = actions.start_game(
            key(), None, season=season, date=datetime.date(2026, 9, 28), blinds="season"
        ).action.game
        assert not BlindTimer.objects.filter(game=game).exists()

    def test_cash_games_get_no_timer(self, frozen, structure):
        season = make_season(2026, SeasonKind.CASH)
        game = actions.start_game(
            key(), None, season=season, date=datetime.date(2026, 9, 28), blinds=structure.pk
        ).action.game
        assert not BlindTimer.objects.filter(game=game).exists()

    def test_resuming_a_game_keeps_its_timer(self, frozen, structure):
        season = make_season(2026, SeasonKind.TOUR)
        date = datetime.date(2026, 9, 28)
        game = actions.start_game(
            key(), None, season=season, date=date, blinds=structure.pk
        ).action.game
        again = actions.start_game(key(), None, season=season, date=date, blinds=structure.pk)
        assert again.action.game == game
        assert BlindTimer.objects.filter(game=game).count() == 1

    def test_attach_later_once(self, frozen, structure):
        game = live_game(SeasonKind.TOUR)
        outcome = actions.start_timer(game.pk, key(), None, structure.pk)
        assert outcome.action.summary == "Таймер: Тестовая структура"
        with pytest.raises(RuleError, match="уже есть таймер"):
            actions.start_timer(game.pk, key(), None, structure.pk)

    def test_cash_game_cannot_attach(self, frozen, structure):
        with pytest.raises(RuleError):
            actions.start_timer(live_game(SeasonKind.CASH).pk, key(), None, structure.pk)

    def test_structure_without_levels_refused(self, frozen):
        empty = make_structure(name="Пустая", rows=[("Перерыв", 10)])
        with pytest.raises(RuleError, match="нет уровней"):
            actions.start_timer(live_game(SeasonKind.TOUR).pk, key(), None, empty.pk)


class TestCopyIsolation:
    def test_template_edits_do_not_reach_the_game(self, timed, structure):
        BlindLevel.objects.filter(structure=structure).update(minutes=5)
        BlindLevel.objects.create(
            structure=structure, position=8, small_blind=1, big_blind=2, minutes=1
        )
        structure.name = "Другая"
        structure.save()

        assert minutes_of(timed) == [20, 20, 20, 15, 20, 10, 20]
        assert BlindTimer.objects.get(game=timed).structure_name == "Тестовая структура"

    def test_game_edits_do_not_reach_the_template(self, timed, structure):
        actions.bulk_minutes(timed.pk, key(), None, 1, 30)
        assert set(structure.levels.values_list("minutes", flat=True)) == {20, 15, 10}

    def test_deleting_the_template_keeps_the_copy(self, timed, structure):
        structure.delete()
        assert len(minutes_of(timed)) == 7


class TestRunning:
    def test_counts_after_start(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=5, seconds=23)
        assert where(timed) == (1, "14:37")

    def test_pause_freezes_and_resume_continues(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=5)
        pause(timed)
        frozen.advance(minutes=40)
        assert where(timed) == (1, "15:00")
        run(timed)
        frozen.advance(minutes=3)
        assert where(timed) == (1, "12:00")

    def test_runs_through_levels_and_breaks(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=65)
        assert where(timed) == (4, "10:00")
        assert state(timed).at_addon_break
        frozen.advance(minutes=10)
        assert where(timed) == (5, "20:00")

    def test_first_start_and_later_resume_are_logged_apart(self, timed, frozen):
        assert run(timed).action.summary == "Таймер запущен"
        frozen.advance(minutes=1)
        pause(timed)
        frozen.advance(minutes=1)
        summary = run(timed).action.summary
        assert summary == "Таймер: продолжен, уровень 1 · 25 / 50"

    def test_same_state_twice_refused(self, timed):
        with pytest.raises(RuleError, match="уже на паузе"):
            pause(timed)
        run(timed)
        with pytest.raises(RuleError, match="уже идёт"):
            run(timed)

    def test_last_level_stops_at_zero(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=200)
        current = state(timed)
        assert (current.current.position, current.remaining_text, current.finished) == (
            7,
            "00:00",
            True,
        )

    def test_level_added_after_the_end_starts_fresh(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=200)  # ended 75 minutes ago
        actions.add_level(timed.pk, key(), None, small=300, big=600, ante=50, minutes=20)
        assert where(timed) == (8, "20:00")
        frozen.advance(minutes=1)
        assert where(timed) == (8, "19:00")


class TestSteps:
    def test_next_starts_the_next_row_in_full(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=7)
        outcome = actions.step_level(timed.pk, key(), None, 1, 1)
        assert outcome.action.summary == "Таймер: уровень 2 · 50 / 100"
        assert where(timed) == (2, "20:00")

    def test_previous(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=25)
        actions.step_level(timed.pk, key(), None, 2, -1)
        assert where(timed) == (1, "20:00")

    def test_step_while_paused_stays_paused(self, timed, frozen):
        actions.step_level(timed.pk, key(), None, 1, 1)
        frozen.advance(minutes=10)
        assert where(timed) == (2, "20:00")
        assert state(timed).paused

    def test_stale_screen_does_not_skip_two_levels(self, timed):
        actions.step_level(timed.pk, key(), None, 1, 1)
        with pytest.raises(RuleError, match="Уровень уже сменился"):
            actions.step_level(timed.pk, key(), None, 1, 1)
        assert where(timed)[0] == 2

    def test_step_follows_automatic_changes(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=45)  # level 3 by time
        actions.step_level(timed.pk, key(), None, 3, 1)
        assert where(timed) == (4, "15:00")

    def test_bounds(self, timed, frozen):
        with pytest.raises(RuleError, match="первый"):
            actions.step_level(timed.pk, key(), None, 1, -1)
        run(timed)
        frozen.advance(minutes=200)
        with pytest.raises(RuleError, match="последний"):
            actions.step_level(timed.pk, key(), None, 7, 1)

    def test_plus_minute(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=18)
        actions.add_minute(timed.pk, key(), None)
        assert where(timed) == (1, "03:00")
        frozen.advance(minutes=3)
        assert where(timed) == (2, "20:00")

    def test_plus_minute_at_the_start_of_a_level(self, timed):
        actions.add_minute(timed.pk, key(), None)
        assert where(timed) == (1, "21:00")

    def test_plus_minute_after_the_end(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=200)
        actions.add_minute(timed.pk, key(), None)
        assert where(timed) == (7, "01:00")


class TestLevelEdits:
    def test_future_level(self, timed):
        outcome = actions.edit_level(
            timed.pk, key(), None, 5, minutes=25, small=200, big=400, ante=50
        )
        assert outcome.action.summary == "Таймер: уровень 4 · 200 / 400, анте 50, 25 мин"
        assert outcome.action.before["minutes"] == 20
        assert outcome.action.after["small_blind"] == 200
        level = BlindTimer.objects.get(game=timed).levels.get(position=5)
        assert (level.small_blind, level.big_blind, level.ante, level.minutes) == (200, 400, 50, 25)

    def test_break_minutes(self, timed):
        outcome = actions.edit_level(timed.pk, key(), None, 4, minutes=20)
        assert outcome.action.summary == "Таймер: Перерыв · аддон, 20 мин"
        assert minutes_of(timed)[3] == 20

    def test_played_level_refused(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=25)
        with pytest.raises(RuleError, match="уже сыгран"):
            actions.edit_level(timed.pk, key(), None, 1, minutes=30)
        with pytest.raises(RuleError, match="уже сыгран"):
            actions.edit_level(timed.pk, key(), None, 1, minutes=20, small=10, big=20, ante=0)

    def test_current_minutes_not_below_the_time_played(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=12)
        with pytest.raises(RuleError, match="идёт уже 12 мин"):
            actions.edit_level(timed.pk, key(), None, 1, minutes=12)
        actions.edit_level(timed.pk, key(), None, 1, minutes=15)
        assert where(timed) == (1, "03:00")

    def test_current_blinds_in_the_first_minute(self, timed, frozen):
        run(timed)
        frozen.advance(seconds=50)
        actions.edit_level(timed.pk, key(), None, 1, minutes=20, small=20, big=40, ante=0)
        frozen.advance(seconds=20)
        with pytest.raises(RuleError, match="на паузе или в первую минуту"):
            actions.edit_level(timed.pk, key(), None, 1, minutes=20, small=25, big=50, ante=0)
        # Minutes alone are fine; so are the same blinds.
        actions.edit_level(timed.pk, key(), None, 1, minutes=25, small=20, big=40, ante=0)

    def test_current_blinds_while_paused(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=5)
        pause(timed)
        actions.edit_level(timed.pk, key(), None, 1, minutes=20, small=30, big=60, ante=0)
        assert state(timed).current.small_blind == 30

    @pytest.mark.parametrize(
        ("fields", "message"),
        [
            ({"minutes": 0}, "Минуты"),
            ({"minutes": 241}, "Минуты"),
            ({"minutes": 20, "small": 100, "big": 50, "ante": 0}, "Блайнды"),
            ({"minutes": 20, "small": 0, "big": 50, "ante": 0}, "Блайнды"),
        ],
    )
    def test_invalid_values(self, timed, fields, message):
        with pytest.raises(RuleError, match=message):
            actions.edit_level(timed.pk, key(), None, 5, **fields)

    def test_bulk_minutes_from_a_level_on(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=10)
        outcome = actions.bulk_minutes(timed.pk, key(), None, 2, 15)
        assert outcome.action.summary == "Таймер: с уровня 2 по 15 мин"
        # Levels 2..5 (positions 2, 3, 5, 7); the breaks keep theirs.
        assert minutes_of(timed) == [20, 15, 15, 15, 15, 10, 15]

    def test_bulk_minutes_include_the_current_level(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=5)
        actions.bulk_minutes(timed.pk, key(), None, 1, 10)
        assert where(timed) == (1, "05:00")

    def test_bulk_minutes_refuse_played_and_too_short(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=32)  # level 2, 12 minutes in
        with pytest.raises(RuleError, match="уже сыгран"):
            actions.bulk_minutes(timed.pk, key(), None, 1, 30)
        with pytest.raises(RuleError, match="идёт уже 12 мин"):
            actions.bulk_minutes(timed.pk, key(), None, 2, 10)
        assert minutes_of(timed) == [20, 20, 20, 15, 20, 10, 20]

    def test_edits_are_logged_but_not_undoable(self, timed):
        outcome = actions.bulk_minutes(timed.pk, key(), None, 1, 30)
        assert not outcome.action.undoable
        with pytest.raises(RuleError, match="не отменяется"):
            actions.undo(outcome.action.pk, None)

    def test_add_level_validates(self, timed):
        with pytest.raises(RuleError, match="Блайнды"):
            actions.add_level(timed.pk, key(), None, small=500, big=400, ante=0, minutes=20)
        outcome = actions.add_level(timed.pk, key(), None, small=300, big=600, ante=0, minutes=20)
        assert outcome.action.summary == "Таймер: добавлен уровень 6 · 300 / 600"
        assert len(minutes_of(timed)) == 8


class TestAddBreak:
    def test_break_after_the_last_row(self, timed):
        outcome = actions.add_break(timed.pk, key(), None, label=" Перерыв на чай ", minutes=10)
        assert outcome.action.summary == "Таймер: добавлен перерыв «Перерыв на чай», 10 мин"
        level = BlindTimer.objects.get(game=timed).levels.get(position=8)
        assert (level.label, level.minutes, level.is_break, level.addon_break) == (
            "Перерыв на чай",
            10,
            True,
            False,
        )
        assert state(timed).level_count == 5

    def test_one_addon_break(self, timed):
        with pytest.raises(RuleError, match="Перерыв на аддон уже есть: «Перерыв · аддон»"):
            actions.add_break(timed.pk, key(), None, label="Ещё аддон", minutes=10, addon=True)
        assert len(minutes_of(timed)) == 7

    def test_addon_break_when_there_is_none(self, frozen):
        game = live_game(SeasonKind.TOUR)
        plain = make_structure(name="Без аддона", rows=[(25, 50, 20), (50, 100, 20)])
        actions.start_timer(game.pk, key(), None, plain.pk)
        actions.add_break(game.pk, key(), None, label="Аддон", minutes=15, addon=True)
        assert state(game).addon_row.label == "Аддон"
        assert state(game).rebuys_until.number == 2

    @pytest.mark.parametrize(
        ("label", "minutes", "message"),
        [
            (" ", 10, "Укажите название перерыва"),
            ("Перерыв", 0, "Минуты"),
            ("x" * 61, 10, "длинное"),
        ],
    )
    def test_invalid(self, timed, label, minutes, message):
        with pytest.raises(RuleError, match=message):
            actions.add_break(timed.pk, key(), None, label=label, minutes=minutes)

    def test_break_after_the_end_starts_fresh(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=200)
        actions.add_break(timed.pk, key(), None, label="Перерыв", minutes=10)
        assert where(timed) == (8, "10:00")


class TestUndo:
    def test_undo_pause(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=5)
        paused = pause(timed)
        frozen.advance(minutes=1)
        actions.undo(paused.action.pk, None)
        assert where(timed) == (1, "14:00")
        assert not state(timed).paused

    def test_undo_next_level(self, timed, frozen):
        run(timed)
        frozen.advance(minutes=5)
        step = actions.step_level(timed.pk, key(), None, 1, 1)
        frozen.advance(minutes=1)
        actions.undo(step.action.pk, None)
        assert where(timed) == (1, "14:00")

    def test_undo_after_another_press_refused(self, timed, frozen):
        run(timed)
        step = actions.step_level(timed.pk, key(), None, 1, 1)
        actions.add_minute(timed.pk, key(), None)
        with pytest.raises(RuleError, match="уже изменили"):
            actions.undo(step.action.pk, None)

    def test_stage_undo_survives_timer_presses(self, frozen, structure):
        game = live_game(SeasonKind.TOUR)
        seat(game, "Альфа", "Браво")
        actions.start_timer(game.pk, key(), None, structure.pk)
        change = actions.advance_stage(game.pk, key(), None, Game.Stage.REBUYS)
        run(game)
        actions.add_minute(game.pk, key(), None)

        actions.undo(change.action.pk, None)

        game.refresh_from_db()
        assert game.live_stage == Game.Stage.REBUYS


class TestEngine:
    def test_same_key_applies_once(self, timed):
        same = key()
        first = actions.step_level(timed.pk, same, None, 1, 1)
        again = actions.step_level(timed.pk, same, None, 1, 1)
        assert (first.replayed, again.replayed) == (False, True)
        assert where(timed)[0] == 2
        assert LiveAction.objects.filter(kind=ActionKind.LEVEL_NEXT).count() == 1

    def test_no_timer(self, frozen):
        game = live_game(SeasonKind.TOUR)
        with pytest.raises(RuleError, match="нет таймера"):
            run(game)

    def test_finished_game_is_gone(self, timed):
        Game.objects.filter(pk=timed.pk).update(live_stage="")
        with pytest.raises(actions.GameGone):
            run(timed)

    def test_reset_link_revokes_the_old_token(self, timed):
        old = BlindTimer.objects.get(game=timed).display_token
        actions.reset_link(timed.pk, key(), None)
        new = BlindTimer.objects.get(game=timed).display_token
        assert new != old
        assert len(new) >= 40
