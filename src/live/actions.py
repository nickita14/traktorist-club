"""Everything the live game screens change, as small atomic operations.

Each operation runs in one transaction that first locks the game row, so actions on one game are
serialized: two organizers on two phones cannot interleave, and rule checks (stage, who is still
in) see the state the change applies to. Values change through ``F()`` expressions and subqueries
in the ``UPDATE`` itself, never computed in Python.

Every request carries an idempotency key (see LiveAction). A key that is already logged changes
nothing: the caller gets the logged action back with ``replayed`` set.

Rule violations raise RuleError with a message for the organizer; they are not server errors.
"""

import datetime
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from django.contrib.admin.models import DELETION, LogEntry
from django.db import IntegrityError, transaction
from django.db.models import F, Max, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from club.models import BlindStructure, Game, LiveStage, Player, Result, Season, SeasonKind
from live import clock
from live.models import TIMER_KINDS, ActionKind, BlindTimer, LiveAction, TimerLevel, display_token

# Result fields an action may touch; undo compares and restores all of them.
SNAPSHOT_FIELDS = ("buyin", "payout", "place", "chips_out", "rebuys", "addon", "out_order")

# The toast shows "отменить" for a few seconds; the server accepts it a little longer, for a
# slow network.
UNDO_WINDOW = datetime.timedelta(minutes=2)


class RuleError(Exception):
    """The action does not fit the game's current state; the message is for the organizer."""


class GameGone(Exception):
    """The game does not exist (any more) or is not live."""


@dataclass(frozen=True)
class Outcome:
    action: LiveAction
    replayed: bool = False


@dataclass(frozen=True)
class Change:
    """What an operation did: the logged part of a LiveAction."""

    kind: str
    summary: str
    result_id: int | None = None
    before: dict | None = None
    after: dict | None = None


def _lock(game_pk: int, *, live: bool = True) -> Game:
    """The game with its season, its row locked until the transaction ends."""
    try:
        game = Game.objects.select_for_update(of=("self",)).select_related("season").get(pk=game_pk)
    except Game.DoesNotExist as error:
        raise GameGone from error
    if live and not game.is_live:
        raise GameGone
    return game


def _logged(key: uuid.UUID) -> LiveAction | None:
    return LiveAction.objects.filter(key=key).first()


def perform(
    game_pk: int, key: uuid.UUID, user, operation: Callable[[Game], Change], *, live: bool = True
) -> Outcome:
    """Run ``operation`` on the locked game once per ``key`` and log it."""
    with transaction.atomic():
        try:
            game = _lock(game_pk, live=live)
        except GameGone:
            # The game may be finished by now, but this very request was applied before it.
            existing = _logged(key)
            if existing is not None:
                return Outcome(existing, replayed=True)
            raise
        existing = _logged(key)
        if existing is not None:
            return Outcome(existing, replayed=True)
        change = operation(game)
        action = LiveAction.objects.create(
            key=key,
            game=game,
            result_id=change.result_id,
            kind=change.kind,
            summary=change.summary,
            before=change.before,
            after=change.after,
            user=user if user and user.is_authenticated else None,
        )
        return Outcome(action)


# Reading and writing one result row.


def _row(game: Game, result_pk: int) -> Result:
    result = Result.objects.filter(game=game, pk=result_pk).select_related("player").first()
    if result is None:
        raise RuleError("Этого игрока нет в игре.")
    return result


def _snapshot(result: Result) -> dict:
    return {field: getattr(result, field) for field in SNAPSHOT_FIELDS}


def _update(result: Result, kind: str, summary: str, **values) -> Change:
    """Apply ``values`` (expressions) to ``result`` and return the change with both snapshots."""
    before = _snapshot(result)
    Result.objects.filter(pk=result.pk).update(**values)
    result.refresh_from_db(fields=SNAPSHOT_FIELDS)
    return Change(kind, summary, result.pk, before, _snapshot(result))


def _next_out_order():
    """1 + the game's highest out_order, computed inside the UPDATE."""
    highest = (
        Result.objects.filter(game=OuterRef("game"))
        .order_by()
        .values("game")
        .annotate(highest=Max("out_order"))
        .values("highest")
    )
    return Coalesce(Subquery(highest), 0) + 1


def _require_stage(game: Game, *stages: str, message: str) -> None:
    if game.live_stage not in stages:
        raise RuleError(message)


def _require_in(result: Result) -> None:
    if result.out_order is not None:
        raise RuleError(f"{result.player.label} уже не в игре.")


def _require_out(result: Result) -> None:
    if result.out_order is None:
        raise RuleError(f"{result.player.label} и так в игре.")


def _require_kind(game: Game, kind: str) -> None:
    if game.season.kind != kind:
        raise RuleError("В этой игре так нельзя.")


# Starting and seating.


def next_season(kind: str, year: int) -> Season:
    """The season of ``kind`` for ``year``, created with the rules of the latest one if missing."""
    season = Season.objects.filter(kind=kind, year=year).first()
    if season is not None:
        return season
    latest = Season.objects.filter(kind=kind).order_by("-year").first()
    copied = (
        "chips_per_lei",
        "paid_places",
        "entry_price",
        "rebuy_price",
        "addon_price",
        "rebuy_minutes",
        "payout_weights",
        "payout_round",
        "cash_step",
        "default_blinds",
    )
    rules = {field: getattr(latest, field) for field in copied} if latest else {}
    season, _ = Season.objects.get_or_create(kind=kind, year=year, defaults=rules)
    return season


# ``blinds`` for start_game: the season's default structure.
SEASON_BLINDS = "season"


def start_game(
    key: uuid.UUID,
    user,
    *,
    season: Season | None = None,
    kind: str | None = None,
    date: datetime.date,
    location: str = "",
    blinds: int | str | None = None,
) -> Outcome:
    """Create a live game, or return the one this key already created.

    Without ``season``, the season of ``kind`` for the date's year is created if needed. A
    finished game on the same season and date is refused; a live one is resumed.

    ``blinds`` gives a tournament its blind timer: a BlindStructure pk, SEASON_BLINDS for the
    season's default, or None for no timer. The game gets its own copy, paused at level 1.
    """
    with transaction.atomic():
        existing = _logged(key)
        if existing is not None:
            return Outcome(existing, replayed=True)
        if season is None:
            season = next_season(kind, date.year)
        if date.year != season.year:
            raise RuleError(f"Дата должна быть в {season.year} году.")
        stage = LiveStage.REBUYS if season.kind == SeasonKind.TOUR else LiveStage.CASH
        game = Game.objects.filter(season=season, date=date).first()
        if game is not None and not game.is_live:
            raise RuleError("В этот день игра этого сезона уже записана.")
        if game is None:
            try:
                with transaction.atomic():
                    game = Game.objects.create(
                        season=season,
                        date=date,
                        location=location,
                        live_stage=stage,
                        started_at=timezone.now(),
                    )
            except IntegrityError:
                # The other phone started the same game a moment ago.
                game = Game.objects.get(season=season, date=date)
        structure = _structure(season, blinds)
        if structure is not None and not BlindTimer.objects.filter(game=game).exists():
            _copy_structure(game, structure)
        action = LiveAction.objects.create(
            key=key,
            game=game,
            kind=ActionKind.START,
            summary=f"Игра начата: {season}, {date:%d.%m.%Y}",
            user=user if user and user.is_authenticated else None,
        )
        return Outcome(action)


def seat(game_pk: int, key: uuid.UUID, user, player_pk: int) -> Outcome:
    """Seat an existing player with the entry price (tour) or one cash step."""

    def operation(game: Game) -> Change:
        player = Player.objects.filter(pk=player_pk).first()
        if player is None:
            raise RuleError("Такого игрока нет.")
        return _seat(game, player)

    return perform(game_pk, key, user, operation)


def seat_new(game_pk: int, key: uuid.UUID, user, name: str, nickname: str) -> Outcome:
    """Create a player (validated like the admin form) and seat them in one action."""

    def operation(game: Game) -> Change:
        player = Player(name=name, nickname=nickname)
        player.full_clean()  # ValidationError goes back to the form
        player.save()
        return _seat(game, player)

    return perform(game_pk, key, user, operation)


def _seat(game: Game, player: Player) -> Change:
    season = game.season
    if season.kind == SeasonKind.TOUR:
        _require_stage(
            game, LiveStage.REBUYS, message="Новых игроков можно сажать только на этапе 1."
        )
        fields = {"buyin": season.entry_price, "rebuys": 0, "addon": False}
    else:
        fields = {"buyin": season.cash_step}
    if Result.objects.filter(game=game, player=player).exists():
        raise RuleError(f"{player.label} уже за столом.")
    result = Result.objects.create(game=game, player=player, **fields)
    return Change(
        ActionKind.SEAT,
        f"За стол: {player.label}, {fields['buyin']}",
        result.pk,
        None,
        _snapshot(result),
    )


# Tournament.


def rebuy(game_pk: int, key: uuid.UUID, user, result_pk: int) -> Outcome:
    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.TOUR)
        _require_stage(game, LiveStage.REBUYS, message="Ребаи закрыты.")
        result = _row(game, result_pk)
        _require_in(result)
        price = game.season.rebuy_price
        return _update(
            result,
            ActionKind.REBUY,
            f"Ребай: {result.player.label} +{price}",
            buyin=F("buyin") + price,
            rebuys=Coalesce(F("rebuys"), 0) + 1,
        )

    return perform(game_pk, key, user, operation)


def set_addon(game_pk: int, key: uuid.UUID, user, result_pk: int, on: bool) -> Outcome:
    """Mark or unmark the add-on. The target state is explicit, so a stale screen cannot flip it
    the wrong way."""

    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.TOUR)
        _require_stage(game, LiveStage.ADDON, message="Аддон берут только в перерыве.")
        result = _row(game, result_pk)
        _require_in(result)
        label = result.player.label
        price = game.season.addon_price
        if bool(result.addon) == on:
            raise RuleError(f"У {label} аддон уже {'отмечен' if on else 'снят'}.")
        if on:
            return _update(
                result,
                ActionKind.ADDON_ON,
                f"Аддон: {label} +{price}",
                buyin=F("buyin") + price,
                addon=True,
            )
        return _update(
            result,
            ActionKind.ADDON_OFF,
            f"Аддон снят: {label} −{price}",
            buyin=F("buyin") - price,
            addon=False,
        )

    return perform(game_pk, key, user, operation)


def eliminate(game_pk: int, key: uuid.UUID, user, result_pk: int) -> Outcome:
    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.TOUR)
        _require_stage(
            game, LiveStage.REBUYS, LiveStage.FINAL, message="В перерыве никто не выбывает."
        )
        result = _row(game, result_pk)
        _require_in(result)
        if game.results.filter(out_order__isnull=True).count() <= 1:
            raise RuleError(f"{result.player.label} остался один: это победитель.")
        return _update(
            result,
            ActionKind.ELIMINATE,
            f"Выбыл: {result.player.label}",
            out_order=_next_out_order(),
        )

    return perform(game_pk, key, user, operation)


def restore(game_pk: int, key: uuid.UUID, user, result_pk: int) -> Outcome:
    """Bring an eliminated player back (a wrong tap)."""

    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.TOUR)
        result = _row(game, result_pk)
        _require_out(result)
        return _update(
            result,
            ActionKind.RESTORE,
            f"Вернули в игру: {result.player.label}",
            out_order=None,
        )

    return perform(game_pk, key, user, operation)


STAGE_STEPS = {
    LiveStage.REBUYS: (LiveStage.ADDON, "Ребаи закрыты, перерыв на аддон"),
    LiveStage.ADDON: (LiveStage.FINAL, "Начат финальный этап"),
}


def advance_stage(game_pk: int, key: uuid.UUID, user, from_stage: str) -> Outcome:
    """Move a tournament to its next stage. ``from_stage`` is what the organizer saw, so a second
    phone pressing the same button does not skip a stage."""

    def operation(game: Game) -> Change:
        if game.live_stage != from_stage or from_stage not in STAGE_STEPS:
            raise RuleError("Этап уже сменился.")
        to_stage, summary = STAGE_STEPS[from_stage]
        Game.objects.filter(pk=game.pk).update(live_stage=to_stage)
        return Change(
            ActionKind.STAGE,
            summary,
            before={"live_stage": from_stage},
            after={"live_stage": to_stage},
        )

    return perform(game_pk, key, user, operation)


def save_results(
    game_pk: int, key: uuid.UUID, user, rows: Mapping[int, tuple[int | None, int]]
) -> Outcome:
    """Store place and payout per result (``rows``: result pk -> (place, payout)) and finish the
    tournament. Saving is allowed whether or not the balance matches."""

    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.TOUR)
        _require_stage(game, LiveStage.FINAL, message="Результаты вносят после финального этапа.")
        results = {result.pk: result for result in game.results.all()}
        if set(rows) != set(results):
            raise RuleError("Список игроков изменился, откройте результаты заново.")
        for pk, (place, payout) in rows.items():
            results[pk].place, results[pk].payout = place, payout
        Result.objects.bulk_update(results.values(), ["place", "payout"])
        Game.objects.filter(pk=game.pk).update(live_stage="")
        return Change(ActionKind.FINISH, f"Турнир завершён: {game}")

    return perform(game_pk, key, user, operation)


# Cash.


def top_up(game_pk: int, key: uuid.UUID, user, result_pk: int) -> Outcome:
    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.CASH)
        result = _row(game, result_pk)
        _require_in(result)
        step = game.season.cash_step
        return _update(
            result,
            ActionKind.TOPUP,
            f"Докупка: {result.player.label} +{step}",
            buyin=F("buyin") + step,
        )

    return perform(game_pk, key, user, operation)


def cash_exit(game_pk: int, key: uuid.UUID, user, result_pk: int, chips: int, paid: int) -> Outcome:
    """A player leaves the cash table with ``chips`` and is paid ``paid`` lei.

    A player who comes back and leaves again adds to chips_out and payout, so chip value minus
    payout (the pot share) and the net stay right.
    """

    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.CASH)
        result = _row(game, result_pk)
        _require_in(result)
        return _update(
            result,
            ActionKind.EXIT,
            f"Выход: {result.player.label}, выдано {paid}",
            chips_out=Coalesce(F("chips_out"), 0) + chips,
            payout=F("payout") + paid,
            out_order=_next_out_order(),
        )

    return perform(game_pk, key, user, operation)


def cash_return(game_pk: int, key: uuid.UUID, user, result_pk: int) -> Outcome:
    """A player who left sits down again with one cash step."""

    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.CASH)
        result = _row(game, result_pk)
        _require_out(result)
        step = game.season.cash_step
        return _update(
            result,
            ActionKind.RETURN,
            f"Вернулся: {result.player.label} +{step}",
            buyin=F("buyin") + step,
            out_order=None,
        )

    return perform(game_pk, key, user, operation)


def close_cash(game_pk: int, key: uuid.UUID, user) -> Outcome:
    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.CASH)
        if not game.results.exists():
            raise RuleError("За столом никого не было: игру можно только отменить.")
        if game.results.filter(out_order__isnull=True).exists():
            raise RuleError("Не все вышли из-за стола.")
        Game.objects.filter(pk=game.pk).update(live_stage="")
        return Change(ActionKind.FINISH, f"Вечер закрыт: {game}")

    return perform(game_pk, key, user, operation)


# Any game.


def cancel_game(game_pk: int, user) -> None:
    """Delete a live game started by mistake. Only while nobody is seated.

    The game's action log goes with it, so the deletion is recorded like an admin deletion: a
    LogEntry with the game and the user. Idempotent by nature: a repeated request finds no game
    and raises GameGone.
    """
    with transaction.atomic():
        game = _lock(game_pk)
        if game.results.exists():
            raise RuleError("Игроки уже за столом: такую игру отменить нельзя.")
        LogEntry.objects.log_actions(
            user.pk,
            [game],
            DELETION,
            change_message="Отменена на экране живой игры.",
            single_object=True,
        )
        game.delete()


# Blind timer.


def _structure(season: Season, blinds: int | str | None) -> BlindStructure | None:
    if season.kind != SeasonKind.TOUR or blinds is None:
        return None
    if blinds == SEASON_BLINDS:
        return season.default_blinds
    structure = BlindStructure.objects.filter(pk=blinds).first()
    if structure is None:
        raise RuleError("Такой структуры блайндов нет.")
    return structure


def _copy_structure(game: Game, structure: BlindStructure) -> BlindTimer:
    """The game's own copy of ``structure``, paused at its first row until "ПУСК"."""
    levels = list(structure.levels.all())
    if not any(level.big_blind is not None for level in levels):
        raise RuleError(f"В структуре «{structure.name}» нет уровней.")
    now = timezone.now()
    timer = BlindTimer.objects.create(
        game=game, structure_name=structure.name, position=1, started_at=now, paused_at=now
    )
    fields = ("small_blind", "big_blind", "ante", "minutes", "label", "addon_break")
    TimerLevel.objects.bulk_create(
        TimerLevel(timer=timer, position=number, **{f: getattr(level, f) for f in fields})
        for number, level in enumerate(levels, start=1)
    )
    return timer


def start_timer(game_pk: int, key: uuid.UUID, user, structure_pk: int) -> Outcome:
    """Give a tournament started without a timer its copy of a structure."""

    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.TOUR)
        if BlindTimer.objects.filter(game=game).exists():
            raise RuleError("У игры уже есть таймер.")
        structure = _structure(game.season, structure_pk)
        timer = _copy_structure(game, structure)
        return Change(ActionKind.TIMER, f"Таймер: {timer.structure_name}")

    return perform(game_pk, key, user, operation)


# Timer fields an action may change; undo compares and restores them.
TIMER_FIELDS = ("position", "started_at", "paused_at")


def _timer_snapshot(timer: BlindTimer) -> dict:
    return {
        "position": timer.position,
        "started_at": timer.started_at.isoformat(),
        "paused_at": timer.paused_at.isoformat() if timer.paused_at else None,
    }


def _timer(game: Game) -> BlindTimer:
    timer = BlindTimer.objects.filter(game=game).first()
    if timer is None:
        raise RuleError("У этой игры нет таймера.")
    return timer


def _clock(timer: BlindTimer, now: datetime.datetime) -> clock.Clock:
    rows = clock.rows_of(timer.levels.all())
    return clock.clock_at(rows, timer.position, timer.started_at, timer.paused_at, now)


def _row_name(row: clock.Row) -> str:
    if row.is_break:
        return row.label
    return f"уровень {row.number} · {row.small_blind} / {row.big_blind}"


def _set_timer(timer: BlindTimer, kind: str, summary: str, **values) -> Change:
    before = _timer_snapshot(timer)
    BlindTimer.objects.filter(pk=timer.pk).update(**values)
    timer.refresh_from_db(fields=TIMER_FIELDS)
    return Change(kind, summary, before=before, after=_timer_snapshot(timer))


def set_paused(game_pk: int, key: uuid.UUID, user, paused: bool) -> Outcome:
    """Pause or run the clock. The target state is explicit, so a stale screen cannot flip it the
    wrong way."""

    def operation(game: Game) -> Change:
        timer = _timer(game)
        now = timezone.now()
        current = _clock(timer, now)
        if current.paused == paused:
            raise RuleError("Таймер уже на паузе." if paused else "Таймер уже идёт.")
        position, started_at = clock.settled(current, timer.paused_at, now)
        if paused:
            return _set_timer(
                timer,
                ActionKind.PAUSE,
                f"Таймер: пауза, {_row_name(current.current)}",
                position=position,
                started_at=started_at,
                paused_at=now,
            )
        first = position == 1 and started_at == timer.paused_at
        return _set_timer(
            timer,
            ActionKind.RESUME,
            "Таймер запущен" if first else f"Таймер: продолжен, {_row_name(current.current)}",
            position=position,
            started_at=started_at + (now - timer.paused_at),
            paused_at=None,
        )

    return perform(game_pk, key, user, operation)


def step_level(game_pk: int, key: uuid.UUID, user, from_position: int, step: int) -> Outcome:
    """Next (``step`` 1) or previous (-1) row, from the row the organizer saw: two devices
    pressing at once do not skip two levels. The new row starts from its full time."""

    def operation(game: Game) -> Change:
        timer = _timer(game)
        now = timezone.now()
        current = _clock(timer, now)
        if current.current.position != from_position:
            raise RuleError("Уровень уже сменился.")
        index = current.index + step
        if not 0 <= index < len(current.rows):
            raise RuleError("Это последний уровень." if step > 0 else "Это первый уровень.")
        row = current.rows[index]
        return _set_timer(
            timer,
            ActionKind.LEVEL_NEXT if step > 0 else ActionKind.LEVEL_PREV,
            f"Таймер: {_row_name(row)}",
            position=row.position,
            started_at=timer.paused_at or now,
        )

    return perform(game_pk, key, user, operation)


def add_minute(game_pk: int, key: uuid.UUID, user) -> Outcome:
    def operation(game: Game) -> Change:
        timer = _timer(game)
        now = timezone.now()
        position, started_at = clock.settled(_clock(timer, now), timer.paused_at, now)
        return _set_timer(
            timer,
            ActionKind.PLUS_MINUTE,
            "Таймер: +1 минута",
            position=position,
            started_at=started_at + datetime.timedelta(minutes=1),
        )

    return perform(game_pk, key, user, operation)


# Editing a game's levels. Played rows are locked; the current row's minutes cannot drop below
# the time it has run, and its blinds can be fixed only while paused or in its first minute.

LEVEL_FIELDS = ("small_blind", "big_blind", "ante", "minutes", "label")
MAX_MINUTES = 240


def _level_snapshot(level: TimerLevel) -> dict:
    return {"position": level.position} | {field: getattr(level, field) for field in LEVEL_FIELDS}


def _check_minutes(current: clock.Clock, row: clock.Row, minutes: int) -> None:
    if not 1 <= minutes <= MAX_MINUTES:
        raise RuleError(f"Минуты: от 1 до {MAX_MINUTES}.")
    if current.played(row):
        raise RuleError(f"{_row_name(row).capitalize()} уже сыгран: его не меняют.")
    if row == current.current and datetime.timedelta(minutes=minutes) <= current.elapsed:
        played = int(current.elapsed.total_seconds()) // 60
        raise RuleError(f"Уровень идёт уже {played} мин: сделайте больше.")


def _check_blinds(small: int, big: int, ante: int) -> None:
    if not 1 <= small <= big:
        raise RuleError("Блайнды: малый от 1 и не больше большого.")
    if ante < 0:
        raise RuleError("Анте: от 0.")


def edit_level(
    game_pk: int,
    key: uuid.UUID,
    user,
    position: int,
    *,
    minutes: int,
    small: int | None = None,
    big: int | None = None,
    ante: int | None = None,
) -> Outcome:
    """Set a row's minutes and, for a level, its blinds and ante."""

    def operation(game: Game) -> Change:
        timer = _timer(game)
        current = _clock(timer, timezone.now())
        level = TimerLevel.objects.filter(timer=timer, position=position).first()
        if level is None:
            raise RuleError("Такого уровня нет.")
        row = next(row for row in current.rows if row.position == position)
        _check_minutes(current, row, minutes)
        values = {"minutes": minutes}
        if not row.is_break and None not in (small, big, ante):
            _check_blinds(small, big, ante)
            values |= {"small_blind": small, "big_blind": big, "ante": ante}
            changed = (small, big, ante) != (row.small_blind, row.big_blind, row.ante)
            if changed and row == current.current and not current.fix_blinds_open:
                raise RuleError(
                    "Блайнды идущего уровня правят только на паузе или в первую минуту."
                )
        before = _level_snapshot(level)
        TimerLevel.objects.filter(pk=level.pk).update(**values)
        level.refresh_from_db()
        if row.is_break:
            summary = f"Таймер: {row.label}, {minutes} мин"
        else:
            blinds = f"{level.small_blind} / {level.big_blind}"
            with_ante = f", анте {level.ante}" if level.ante else ""
            summary = f"Таймер: уровень {row.number} · {blinds}{with_ante}, {minutes} мин"
        return Change(ActionKind.LEVEL_EDIT, summary, before=before, after=_level_snapshot(level))

    return perform(game_pk, key, user, operation)


def bulk_minutes(game_pk: int, key: uuid.UUID, user, from_number: int, minutes: int) -> Outcome:
    """ "С уровня N и дальше по M мин": every level numbered N or later (breaks keep theirs)."""

    def operation(game: Game) -> Change:
        timer = _timer(game)
        current = _clock(timer, timezone.now())
        rows = [row for row in current.rows if row.number is not None and row.number >= from_number]
        if not rows:
            raise RuleError(f"Уровня {from_number} нет.")
        for row in rows:
            _check_minutes(current, row, minutes)
        levels = TimerLevel.objects.filter(timer=timer, position__in=[row.position for row in rows])
        before = dict(levels.values_list("position", "minutes"))
        levels.update(minutes=minutes)
        return Change(
            ActionKind.BULK_MINUTES,
            f"Таймер: с уровня {from_number} по {minutes} мин",
            before={"minutes": before},
            after={"minutes": dict.fromkeys(before, minutes)},
        )

    return perform(game_pk, key, user, operation)


def add_level(
    game_pk: int, key: uuid.UUID, user, *, small: int, big: int, ante: int, minutes: int
) -> Outcome:
    """A level after the last row. If the clock had run out, the new level starts now."""

    def operation(game: Game) -> Change:
        timer = _timer(game)
        now = timezone.now()
        current = _clock(timer, now)
        _check_blinds(small, big, ante)
        if not 1 <= minutes <= MAX_MINUTES:
            raise RuleError(f"Минуты: от 1 до {MAX_MINUTES}.")
        if current.finished:
            position, started_at = clock.settled(current, timer.paused_at, now)
            BlindTimer.objects.filter(pk=timer.pk).update(position=position, started_at=started_at)
        level = TimerLevel.objects.create(
            timer=timer,
            position=current.rows[-1].position + 1,
            small_blind=small,
            big_blind=big,
            ante=ante,
            minutes=minutes,
        )
        number = current.level_count + 1
        return Change(
            ActionKind.LEVEL_ADD,
            f"Таймер: добавлен уровень {number} · {small} / {big}",
            after=_level_snapshot(level),
        )

    return perform(game_pk, key, user, operation)


def reset_link(game_pk: int, key: uuid.UUID, user) -> Outcome:
    """A new secret link to the display; the old one stops working."""

    def operation(game: Game) -> Change:
        timer = _timer(game)
        BlindTimer.objects.filter(pk=timer.pk).update(display_token=display_token())
        return Change(ActionKind.LINK, "Табло: новая ссылка, старая больше не работает")

    return perform(game_pk, key, user, operation)


def undo(action_pk: int, user) -> Outcome:
    """Restore what ``action_pk`` changed, if nothing touched it since.

    Idempotent: undoing an undone action changes nothing. A result row is restored only while it
    still holds the action's ``after`` values; a stage change only while it is the game's latest
    action.
    """
    with transaction.atomic():
        action = LiveAction.objects.filter(pk=action_pk).first()
        if action is None or action.game_id is None:
            raise GameGone
        game = _lock(action.game_id)
        action.refresh_from_db()
        if action.undone_at is not None:
            return Outcome(action, replayed=True)
        if not action.undoable:
            raise RuleError("Это действие не отменяется.")
        if timezone.now() - action.created_at > UNDO_WINDOW:
            raise RuleError("Отменить уже нельзя: прошло слишком много времени.")
        _undo_change(game, action)
        action.undone_at = timezone.now()
        action.save(update_fields=["undone_at"])
        return Outcome(action)


CHANGED_SINCE = "Отменить нельзя: после этого запись уже изменили."


def _undo_change(game: Game, action: LiveAction) -> None:
    if action.kind == ActionKind.STAGE:
        # Timer presses in between do not touch the stage.
        latest = (
            game.live_actions.filter(undone_at__isnull=True)
            .exclude(kind__in=TIMER_KINDS)
            .order_by("-pk")
            .first()
        )
        if latest != action:
            raise RuleError(CHANGED_SINCE)
        changed = Game.objects.filter(pk=game.pk, **action.after).update(**action.before)
    elif action.kind in TIMER_KINDS:
        changed = BlindTimer.objects.filter(game=game, **action.after).update(**action.before)
    elif action.kind == ActionKind.SEAT:
        changed, _ = Result.objects.filter(pk=action.result_id, **action.after).delete()
    else:
        try:
            with transaction.atomic():
                changed = Result.objects.filter(pk=action.result_id, **action.after).update(
                    **action.before
                )
        except IntegrityError:
            changed = 0  # the old out_order was taken by someone else meanwhile
    if not changed:
        raise RuleError(CHANGED_SINCE)
