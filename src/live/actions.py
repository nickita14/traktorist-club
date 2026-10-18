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

from club.models import (
    LIVE_STAGES_BY_KIND,
    BlindStructure,
    Game,
    LiveStage,
    Player,
    Result,
    Season,
    SeasonKind,
)
from live import clock
from live.models import TIMER_KINDS, ActionKind, BlindTimer, LiveAction, TimerLevel, display_token

# Result fields an action may touch; undo compares and restores all of them.
SNAPSHOT_FIELDS = (
    "buyin",
    "payout",
    "place",
    "chips_out",
    "rebuys",
    "addon",
    "out_order",
    "entry_paid",
)

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


def entry_amount(season: Season) -> int:
    """What a seat costs: the tournament entry, or one cash step."""
    return season.entry_price if season.kind == SeasonKind.TOUR else season.cash_step


# The steps before play: who sits down, then who handed over the entry.
PRE_GAME = (LiveStage.LINEUP, LiveStage.COLLECT)


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
    finished game on the same season and date is refused; a live one is resumed. A new game
    starts with its lineup; play starts after the entry collection (change_stage).

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
                        live_stage=LiveStage.LINEUP,
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


def seat(game_pk: int, key: uuid.UUID, user, player_pk: int, *, paid: bool = False) -> Outcome:
    """Seat an existing player with the entry price (tour) or one cash step.

    ``paid``: the entry is already in the cash box (a late entry who paid at once). In the lineup
    nobody has paid yet: the collection step marks it.
    """

    def operation(game: Game) -> Change:
        player = Player.objects.filter(pk=player_pk).first()
        if player is None:
            raise RuleError("Такого игрока нет.")
        return _seat(game, player, paid)

    return perform(game_pk, key, user, operation)


def seat_new(
    game_pk: int, key: uuid.UUID, user, name: str, nickname: str, *, paid: bool = False
) -> Outcome:
    """Create a player (validated like the admin form) and seat them in one action."""

    def operation(game: Game) -> Change:
        player = Player(name=name, nickname=nickname)
        player.full_clean()  # ValidationError goes back to the form
        player.save()
        return _seat(game, player, paid)

    return perform(game_pk, key, user, operation)


def can_seat(game: Game) -> bool:
    """New players sit down in the lineup, and later while a tournament's rebuys are open or at
    any time of a cash game. Not during the collection: the lineup is confirmed there."""
    return game.live_stage in (LiveStage.LINEUP, LiveStage.REBUYS, LiveStage.CASH)


def _seat(game: Game, player: Player, paid: bool) -> Change:
    season = game.season
    if not can_seat(game):
        if game.live_stage == LiveStage.COLLECT:
            raise RuleError("Состав уже утверждён: вернитесь к составу, чтобы добавить игрока.")
        raise RuleError("Новых игроков можно сажать только на этапе 1.")
    lineup = game.live_stage == LiveStage.LINEUP
    fields = {"buyin": entry_amount(season), "entry_paid": paid and not lineup}
    if season.kind == SeasonKind.TOUR:
        fields |= {"rebuys": 0, "addon": False}
    if Result.objects.filter(game=game, player=player).exists():
        raise RuleError(f"{player.label} уже за столом.")
    result = Result.objects.create(game=game, player=player, **fields)
    summary = f"За стол: {player.label}, {fields['buyin']}"
    if not lineup:
        summary += " · внёс" if paid else " · не сдал"
    return Change(ActionKind.SEAT, summary, result.pk, None, _snapshot(result))


def unseat_allowed(result: Result, game: Game) -> bool:
    """Whether a seated player can be taken off the list as if they never sat down.

    In the lineup anyone can. Later only a player nothing has happened to yet: no rebuy or
    add-on, still in, nothing paid out, no chips counted, and the single entry as the buy-in.
    """
    if game.live_stage == LiveStage.LINEUP:
        return True
    return (
        not result.rebuys
        and not result.addon
        and result.out_order is None
        and result.payout == 0
        and result.chips_out is None
        and result.buyin == entry_amount(game.season)
    )


def unseat(game_pk: int, key: uuid.UUID, user, result_pk: int) -> Outcome:
    """Remove a player who never played: the row is deleted, nothing is left behind.

    The snapshot keeps the row and the actions that pointed at it (deleting the row clears
    their link), so undo puts both back exactly.
    """

    def operation(game: Game) -> Change:
        result = _row(game, result_pk)
        if not unseat_allowed(result, game):
            raise RuleError(f"{result.player.label} уже в игре: убрать нельзя.")
        before = {"id": result.pk, "player_id": result.player_id} | _snapshot(result)
        before["action_ids"] = list(
            LiveAction.objects.filter(result=result).order_by("pk").values_list("pk", flat=True)
        )
        result.delete()
        return Change(ActionKind.UNSEAT, f"Убран из состава: {result.player.label}", before=before)

    return perform(game_pk, key, user, operation)


def set_paid(game_pk: int, key: uuid.UUID, user, result_pk: int, paid: bool) -> Outcome:
    """Mark the entry as handed over, or clear the mark. Only the mark: the buy-in is recorded
    at seating either way. The target state is explicit, so a stale screen cannot flip it."""

    def operation(game: Game) -> Change:
        result = _row(game, result_pk)
        label = result.player.label
        if result.entry_paid == paid:
            raise RuleError(f"У {label} вход уже {'принят' if paid else 'не отмечен'}.")
        amount = entry_amount(game.season)
        if paid:
            return _update(
                result, ActionKind.PAID_ON, f"Вход принят: {label}, {amount}", entry_paid=True
            )
        return _update(result, ActionKind.PAID_OFF, f"Вход не сдан: {label}", entry_paid=False)

    return perform(game_pk, key, user, operation)


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


def eliminate(
    game_pk: int, key: uuid.UUID, user, result_pk: int, place: int | None = None
) -> Outcome:
    """A tournament player busts. ``place`` is the place the organizer was shown: if someone
    else busted in between, the place would differ, so the tap is refused."""

    def operation(game: Game) -> Change:
        _require_kind(game, SeasonKind.TOUR)
        _require_stage(
            game, LiveStage.REBUYS, LiveStage.FINAL, message="В перерыве никто не выбывает."
        )
        result = _row(game, result_pk)
        _require_in(result)
        in_game = game.results.filter(out_order__isnull=True).count()
        if in_game <= 1:
            raise RuleError(f"{result.player.label} остался один: это победитель.")
        if place is not None and place != in_game:
            raise RuleError("Пока вы подтверждали, выбыл кто-то ещё: проверьте место.")
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


# Every stage change there is: (from, to) -> summary. The first step from a stage is its
# forward step. Starting play also restarts the game's clock (see change_stage).
STAGE_CHANGES = {
    (LiveStage.LINEUP, LiveStage.COLLECT): "Состав верен, сбор входа",
    (LiveStage.COLLECT, LiveStage.LINEUP): "Назад к составу",
    (LiveStage.COLLECT, LiveStage.REBUYS): "Игра начата",
    (LiveStage.COLLECT, LiveStage.CASH): "Игра начата",
    (LiveStage.REBUYS, LiveStage.ADDON): "Ребаи закрыты, перерыв на аддон",
    (LiveStage.ADDON, LiveStage.FINAL): "Начат финальный этап",
}

# The forward step of a stage where it does not depend on the season kind.
NEXT_STAGE = {
    LiveStage.LINEUP: LiveStage.COLLECT,
    LiveStage.REBUYS: LiveStage.ADDON,
    LiveStage.ADDON: LiveStage.FINAL,
}

MIN_PLAYERS = 2


def play_stage(season: Season) -> str:
    """The stage play starts with."""
    return LiveStage.REBUYS if season.kind == SeasonKind.TOUR else LiveStage.CASH


def advance_stage(
    game_pk: int, key: uuid.UUID, user, from_stage: str, to_stage: str | None = None
) -> Outcome:
    """Move a game to another stage. ``from_stage`` is what the organizer saw, so a second phone
    pressing the same button does not skip a stage. Without ``to_stage``, the forward step
    (from the collection: play)."""

    def operation(game: Game) -> Change:
        if game.live_stage != from_stage:
            raise RuleError("Этап уже сменился.")
        target = to_stage
        if target is None:
            target = (
                play_stage(game.season)
                if from_stage == LiveStage.COLLECT
                else NEXT_STAGE.get(from_stage)
            )
        summary = STAGE_CHANGES.get((from_stage, target))
        if summary is None or target not in LIVE_STAGES_BY_KIND[game.season.kind]:
            raise RuleError("Такой смены этапа нет.")
        if target == LiveStage.COLLECT and game.results.count() < MIN_PLAYERS:
            raise RuleError(f"Для игры нужно хотя бы {MIN_PLAYERS} игрока.")
        before = {"live_stage": from_stage}
        after = {"live_stage": target}
        if from_stage == LiveStage.COLLECT and target == play_stage(game.season):
            # The game clock ("идёт 1:17") counts play, not the lineup and the collection.
            before["started_at"] = game.started_at.isoformat()
            after["started_at"] = timezone.now().isoformat()
        Game.objects.filter(pk=game.pk).update(**after)
        return Change(ActionKind.STAGE, summary, before=before, after=after)

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
    """Delete a live game started by mistake: before play starts (the lineup and the collection,
    whoever is seated), or later while nobody is seated.

    The game's action log goes with it, so the deletion is recorded like an admin deletion: a
    LogEntry with the game and the user. Idempotent by nature: a repeated request finds no game
    and raises GameGone.
    """
    with transaction.atomic():
        game = _lock(game_pk)
        if game.live_stage not in PRE_GAME and game.results.exists():
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

LEVEL_FIELDS = ("small_blind", "big_blind", "ante", "minutes", "label", "addon_break")
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


def _append(
    game_pk: int,
    key: uuid.UUID,
    user,
    fields: dict,
    summary: Callable[[clock.Clock], str],
    check: Callable[[clock.Clock], None] = lambda current: None,
) -> Outcome:
    """A row after the last one. If the clock had run out, the new row starts now.

    ``check`` and ``summary`` get the clock before the change.
    """

    def operation(game: Game) -> Change:
        timer = _timer(game)
        now = timezone.now()
        current = _clock(timer, now)
        if not 1 <= fields["minutes"] <= MAX_MINUTES:
            raise RuleError(f"Минуты: от 1 до {MAX_MINUTES}.")
        check(current)
        if current.finished:
            position, started_at = clock.settled(current, timer.paused_at, now)
            BlindTimer.objects.filter(pk=timer.pk).update(position=position, started_at=started_at)
        row = TimerLevel.objects.create(
            timer=timer, position=current.rows[-1].position + 1, **fields
        )
        return Change(ActionKind.LEVEL_ADD, summary(current), after=_level_snapshot(row))

    return perform(game_pk, key, user, operation)


def add_level(
    game_pk: int, key: uuid.UUID, user, *, small: int, big: int, ante: int, minutes: int
) -> Outcome:
    """A level after the last row."""
    _check_blinds(small, big, ante)
    fields = {"small_blind": small, "big_blind": big, "ante": ante, "minutes": minutes}
    return _append(
        game_pk,
        key,
        user,
        fields,
        lambda current: f"Таймер: добавлен уровень {current.level_count + 1} · {small} / {big}",
    )


def add_break(
    game_pk: int, key: uuid.UUID, user, *, label: str, minutes: int, addon: bool = False
) -> Outcome:
    """A break after the last row; ``addon`` makes it the add-on break (one per game)."""
    label = label.strip()
    if not label:
        raise RuleError("Укажите название перерыва, например «Перерыв · аддон».")
    if len(label) > TimerLevel._meta.get_field("label").max_length:
        raise RuleError("Название перерыва слишком длинное.")

    def check(current: clock.Clock) -> None:
        if addon and current.addon_row is not None:
            raise RuleError(f"Перерыв на аддон уже есть: «{current.addon_row.label}».")

    fields = {"label": label, "minutes": minutes, "addon_break": addon}
    return _append(
        game_pk,
        key,
        user,
        fields,
        lambda current: f"Таймер: добавлен перерыв «{label}», {minutes} мин",
        check,
    )


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


def _reseat(game: Game, before: dict) -> int:
    """Put an unseated row back with its pk and values, and relink its actions."""
    fields = {field: before[field] for field in SNAPSHOT_FIELDS if field in before}
    try:
        with transaction.atomic():
            Result.objects.create(
                pk=before["id"], game=game, player_id=before["player_id"], **fields
            )
    except IntegrityError:
        return 0  # the player was seated again meanwhile
    LiveAction.objects.filter(pk__in=before["action_ids"]).update(result_id=before["id"])
    return 1


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
    elif action.kind == ActionKind.UNSEAT:
        changed = _reseat(game, action.before)
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
