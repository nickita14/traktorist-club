"""Theme nights (Тематика) on the display (табло): announcements built from the table log.

The display syncs its state every few seconds (``live.views.display_state``) and passes the last
action it has seen (the cursor). ``table_events`` answers with what happened since, as public
facts only (``Player.label``, the place, a rebuy's amount), and ``theme_payload`` with how to
show each kind: the active theme's rows, else ``DEFAULTS``.

Kinds of a bust, by the place the player got (``LiveAction.place``, stored when it was pressed)
and the season's paid places: ``bubble_burst`` (place paid + 1: everyone left is in the money),
else ``bust_itm`` (a prize place), else ``bust``. The bust at place 2 is followed by ``winner``,
the one player still in. A bust or rebuy that was undone (or a bust reversed by "вернуть") before
the display saw it is never sent; one already shown stays shown. ``break`` is no event: the
display sees the break on its own clock.
"""

import datetime
import hashlib
import string
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Max, Prefetch
from django.urls import reverse

from club import stats
from club.models import Game, Season
from live.models import (
    ActionKind,
    EventKind,
    LiveAction,
    OverlayStyle,
    SoundType,
    Theme,
    ThemeEvent,
    ThemeSound,
)

# What a caption or title of each kind may use, in the order the admin lists them.
PLACEHOLDER_ORDER = ("name", "place", "amount", "count")
PLACEHOLDERS = {
    EventKind.BUST: {"name", "place"},
    EventKind.BUST_ITM: {"name", "place"},
    EventKind.BUBBLE_BURST: {"name", "place"},
    EventKind.REBUY: {"name", "amount", "count"},
    EventKind.WINNER: {"name"},
    EventKind.BREAK: set(),
}
PLACEHOLDER_HELP = "{name} имя, {place} место (выбывание), {amount} сумма и {count} номер ребая."
# No prize amount on a bust or the winner: payouts are known only on the results screen
# ("Итоги"), where the bank is split among the places actually filled in and can be edited.


def _event(enabled, title, caption, style, seconds) -> dict:
    return {
        "enabled": enabled,
        "title": title,
        "caption": caption,
        "style": style,
        "seconds": seconds,
        "sound": None,
    }


# Without an active theme (or for a kind the theme has no row for): the club's own announcement.
DEFAULTS = {
    EventKind.BUST: _event(True, "Выбыл", "{name} · {place} место", OverlayStyle.STAMP, 5),
    EventKind.BUST_ITM: _event(
        True, "Выбыл в призах", "{name} · {place} место", OverlayStyle.STAMP, 6
    ),
    EventKind.BUBBLE_BURST: _event(
        True, "Все в призах", "{name} · {place} место", OverlayStyle.STAMP, 6
    ),
    EventKind.WINNER: _event(True, "Победитель", "{name}", OverlayStyle.STAMP, 0),
    EventKind.REBUY: _event(False, "Ребай", "{name}", OverlayStyle.LOWER_THIRD, 4),
    EventKind.BREAK: _event(False, "Перерыв", "", OverlayStyle.FULLSCREEN, 0),
}

# Bust and rebuy presses: the only actions the display announces.
EVENT_ACTIONS = (ActionKind.ELIMINATE, ActionKind.REBUY)
# Events per sync at most; the rest come with the next one.
MAX_EVENTS = 20


def template_error(kind: str, text: str) -> str | None:
    """Why ``text`` is no valid title or caption for ``kind`` (None when it is).

    Only bare ``{name}``-style fields of the kind are allowed: no format specs, conversions,
    attributes or indexes. The server never formats a caption; the display substitutes the
    values itself.
    """
    allowed = PLACEHOLDERS.get(kind, set())
    try:
        parsed = list(string.Formatter().parse(text))
    except ValueError:
        return "Непарная фигурная скобка: саму скобку пишите как {{ или }}."
    for _literal, field, spec, conversion in parsed:
        if field is None:
            continue
        if field in allowed and not spec and conversion is None:
            continue
        names = [f"{{{name}}}" for name in PLACEHOLDER_ORDER if name in allowed]
        can = f"можно {', '.join(names)}" if names else "подстановок нет"
        shown = (
            "{" + field + (f"!{conversion}" if conversion else "") + (f":{spec}" if spec else "")
        )
        shown += "}"
        return f"Неизвестная подстановка {shown}: здесь {can}."
    return None


# Sounds.

EXTENSIONS = {".mp3": SoundType.MP3, ".ogg": SoundType.OGG, ".wav": SoundType.WAV}


def sniff(head: bytes) -> str | None:
    """The sound type the file's first bytes say, or None."""
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return SoundType.MP3
    if head.startswith(b"OggS"):
        return SoundType.OGG
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return SoundType.WAV
    return None


def read_sound(upload) -> tuple[bytes, str]:
    """The bytes and type of an uploaded sound; ValidationError for a wrong one."""
    limit = settings.THEME_SOUND_MAX_BYTES
    name = upload.name or ""
    extension = name[name.rfind(".") :].lower() if "." in name else ""
    expected = EXTENSIONS.get(extension)
    if expected is None:
        raise ValidationError("Звук: только mp3, ogg или wav.")
    if upload.size is not None and upload.size > limit:
        raise ValidationError(f"Звук больше {limit // 1024} КБ.")
    data = b"".join(upload.chunks())
    if len(data) > limit:
        raise ValidationError(f"Звук больше {limit // 1024} КБ.")
    if not data:
        raise ValidationError("Файл пустой.")
    if sniff(data[:16]) != expected:
        raise ValidationError(f"Это не {expected.label}: файл не похож на звук такого формата.")
    return data, expected


def store_sound(name: str, data: bytes, content_type: str) -> ThemeSound:
    """The sound with these bytes: a new row, or the one already stored."""
    digest = hashlib.sha256(data).hexdigest()
    sound, _ = ThemeSound.objects.get_or_create(
        sha256=digest,
        defaults={
            "name": name[:100],
            "content_type": content_type,
            "size": len(data),
            "data": data,
        },
    )
    return sound


def sound_url(sound: ThemeSound | None) -> str | None:
    return None if sound is None else reverse("tablo_sound", args=[sound.sha256])


# The theme.


def active_theme() -> Theme | None:
    """The active theme with its events and their sounds (not the sound bytes). Two queries,
    one when no theme is active."""
    events = ThemeEvent.objects.select_related("sound").defer("sound__data")
    return (
        Theme.objects.filter(is_active=True)
        .prefetch_related(Prefetch("events", queryset=events))
        .first()
    )


def theme_with_events(pk: int) -> Theme | None:
    events = ThemeEvent.objects.select_related("sound").defer("sound__data")
    return Theme.objects.prefetch_related(Prefetch("events", queryset=events)).filter(pk=pk).first()


def theme_payload(theme: Theme | None) -> dict:
    """How the display shows each kind, every kind present."""
    rows = {row.kind: row for row in theme.events.all()} if theme else {}
    events = {}
    for kind in EventKind.values:
        row = rows.get(kind)
        if row is None:
            events[kind] = dict(DEFAULTS[kind])
        else:
            events[kind] = {
                "enabled": row.enabled,
                "title": row.title,
                "caption": row.caption,
                "style": row.style,
                "seconds": row.seconds,
                "sound": sound_url(row.sound),
            }
    return {
        "name": theme.name if theme else "",
        "tips": theme.tip_list if theme else [],
        "events": events,
    }


# Events.


@dataclass(frozen=True)
class Event:
    id: str
    kind: str
    name: str
    place: int | None = None
    amount: int | None = None
    count: int | None = None
    note: str = ""

    def as_json(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "place": self.place,
            "amount": self.amount,
            "count": self.count,
            "note": self.note,
        }


def latest_cursor(game: Game) -> int:
    """The game's latest action: a display that opens now starts after it. One query."""
    return game.live_actions.aggregate(top=Max("pk"))["top"] or 0


def parse_cursor(value: str | None) -> int | None:
    try:
        cursor = int(value or "")
    except ValueError:
        return None
    return cursor if cursor >= 0 else None


def bust_kind(place: int, paid_places: int) -> str:
    if place == paid_places + 1:
        return EventKind.BUBBLE_BURST
    if place <= paid_places:
        return EventKind.BUST_ITM
    return EventKind.BUST


def in_game_note(left: int, total: int) -> str:
    return f"в игре {left} из {total}"


def table_events(
    game: Game, cursor: int, totals: stats.LiveTotals, now: datetime.datetime
) -> tuple[list[Event], int]:
    """What happened after ``cursor`` and the new cursor. One query, two after the last bust.

    ``game.season`` must be loaded. Presses younger than THEME_EVENT_HOLD_SECONDS wait for the
    next sync; older than THEME_EVENT_MAX_AGE_SECONDS are skipped (the winner is not).
    """
    hold = datetime.timedelta(seconds=settings.THEME_EVENT_HOLD_SECONDS)
    max_age = datetime.timedelta(seconds=settings.THEME_EVENT_MAX_AGE_SECONDS)
    actions = list(
        LiveAction.objects.filter(
            game=game, pk__gt=cursor, kind__in=EVENT_ACTIONS, created_at__lte=now - hold
        )
        .select_related("result__player")
        .order_by("pk")[:MAX_EVENTS]
    )
    if not actions:
        return [], cursor
    season = game.season
    events = []
    for action in actions:
        result = action.result
        if action.undone_at is not None or result is None:
            continue
        fresh = now - action.created_at <= max_age
        label = result.player.label
        if action.kind == ActionKind.REBUY:
            if fresh:
                before, after = action.before or {}, action.after or {}
                events.append(
                    Event(
                        id=str(action.pk),
                        kind=EventKind.REBUY,
                        name=label,
                        amount=after.get("buyin", 0) - before.get("buyin", 0),
                        count=after.get("rebuys"),
                        note=f"ребай № {after.get('rebuys')}",
                    )
                )
            continue
        # A bust still stands while the player's out_order is the one it gave.
        place = action.place
        if place is None or result.out_order != (action.after or {}).get("out_order"):
            continue
        if fresh:
            kind = bust_kind(place, season.paid_places)
            events.append(
                Event(
                    id=str(action.pk),
                    kind=kind,
                    name=label,
                    place=place,
                    note=in_game_note(place - 1, totals.players),
                )
            )
        if place == 2:
            still_in = list(
                game.results.filter(out_order__isnull=True).select_related("player")[:2]
            )
            if len(still_in) == 1:
                events.append(
                    Event(
                        id=f"{action.pk}-winner",
                        kind=EventKind.WINNER,
                        name=still_in[0].player.label,
                        place=1,
                        note=f"1 место из {totals.players}",
                    )
                )
    return events, actions[-1].pk


# The preview page ("Проверить" on a theme): invented values, no game.

SAMPLE_NAME = "Игрок"
SAMPLE_PLAYERS = 9
SAMPLE_BANK = 900


def sample_events() -> dict[str, dict]:
    """One invented event per kind, worked out with the default season rules."""
    season = Season()
    paid = season.paid_places
    bust_place = paid + 4
    return {
        EventKind.BUST: Event(
            "sample-bust",
            EventKind.BUST,
            SAMPLE_NAME,
            place=bust_place,
            note=in_game_note(bust_place - 1, SAMPLE_PLAYERS),
        ).as_json(),
        EventKind.BUBBLE_BURST: Event(
            "sample-bubble",
            EventKind.BUBBLE_BURST,
            SAMPLE_NAME,
            place=paid + 1,
            note=in_game_note(paid, SAMPLE_PLAYERS),
        ).as_json(),
        EventKind.BUST_ITM: Event(
            "sample-itm",
            EventKind.BUST_ITM,
            SAMPLE_NAME,
            place=paid,
            note=in_game_note(paid - 1, SAMPLE_PLAYERS),
        ).as_json(),
        EventKind.WINNER: Event(
            "sample-winner",
            EventKind.WINNER,
            SAMPLE_NAME,
            place=1,
            note=f"1 место из {SAMPLE_PLAYERS}",
        ).as_json(),
        EventKind.REBUY: Event(
            "sample-rebuy",
            EventKind.REBUY,
            SAMPLE_NAME,
            amount=season.rebuy_price,
            count=2,
            note="ребай № 2",
        ).as_json(),
    }
