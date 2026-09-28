"""The blind clock: where a timer is at a given moment, computed from its stored fields.

A timer stores only the level it counts from (``position``), when that level started
(``started_at``) and, while paused, when it was paused (``paused_at``). Everything else follows:
the clock walks forward through the levels' minutes, so a timer runs through levels with no
writes, and an edit of the minutes of the current or a later level shows at once.

``assets/js/blind-clock.js`` mirrors ``clock_at`` for the display, which counts seconds in the
browser; ``live/tests/clock_cases.json`` runs the same cases through both.
"""

import datetime
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# The current level's blinds can be fixed while paused or this long after it started.
BLINDS_FIX_WINDOW = datetime.timedelta(minutes=1)


@dataclass(frozen=True)
class Row:
    """One level or break. ``number`` counts levels only (breaks have None)."""

    position: int
    small_blind: int | None
    big_blind: int | None
    ante: int
    minutes: int
    label: str
    addon_break: bool
    number: int | None

    @property
    def is_break(self) -> bool:
        return self.big_blind is None

    @property
    def duration(self) -> datetime.timedelta:
        return datetime.timedelta(minutes=self.minutes)

    def as_json(self) -> dict:
        return {
            "position": self.position,
            "small": self.small_blind,
            "big": self.big_blind,
            "ante": self.ante,
            "minutes": self.minutes,
            "label": self.label,
            "addon": self.addon_break,
        }


def rows_of(levels: Iterable) -> list[Row]:
    """Rows from TimerLevel (or BlindLevel) objects in their order, levels numbered 1, 2, ..."""
    rows, number = [], 0
    for level in levels:
        is_level = level.big_blind is not None
        number += is_level
        rows.append(
            Row(
                position=level.position,
                small_blind=level.small_blind,
                big_blind=level.big_blind,
                ante=level.ante,
                minutes=level.minutes,
                label=level.label,
                addon_break=level.addon_break,
                number=number if is_level else None,
            )
        )
    return rows


def format_clock(delta: datetime.timedelta) -> str:
    """'14:37', or '1:04:37' past an hour; whole seconds rounded up, so 0:00 only at the end."""
    seconds = max(math.ceil(delta.total_seconds()), 0)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class Clock:
    rows: Sequence[Row]
    index: int  # the current row
    elapsed: datetime.timedelta  # into the current row; negative after "+1 минута" at its start
    paused: bool
    finished: bool  # the last row has run out: the clock stays at 0:00 on it

    @property
    def current(self) -> Row:
        return self.rows[self.index]

    @property
    def remaining(self) -> datetime.timedelta:
        return max(self.current.duration - self.elapsed, datetime.timedelta(0))

    @property
    def remaining_text(self) -> str:
        return format_clock(self.remaining)

    @property
    def remaining_seconds(self) -> int:
        return max(math.ceil(self.remaining.total_seconds()), 0)

    @property
    def duration_seconds(self) -> int:
        return self.current.minutes * 60

    @property
    def progress_seconds(self) -> int:
        """Seconds into the level for its progress bar, within 0..duration."""
        return self.duration_seconds - min(self.remaining_seconds, self.duration_seconds)

    @property
    def fix_blinds_open(self) -> bool:
        """The current level's blinds can still be fixed (see live.actions.edit_level)."""
        return self.paused or self.elapsed < BLINDS_FIX_WINDOW

    @property
    def level_count(self) -> int:
        return sum(1 for row in self.rows if not row.is_break)

    def played(self, row: Row) -> bool:
        return row.position < self.current.position

    @property
    def next_level(self) -> Row | None:
        return next((row for row in self.rows[self.index + 1 :] if not row.is_break), None)

    @property
    def next_break(self) -> Row | None:
        return next((row for row in self.rows[self.index + 1 :] if row.is_break), None)

    @property
    def until_break(self) -> datetime.timedelta | None:
        """Time until the next break starts, or None when no break is left."""
        pause = self.next_break
        if pause is None:
            return None
        between = self.rows[self.index + 1 : self.rows.index(pause)]
        return self.remaining + sum((row.duration for row in between), datetime.timedelta(0))

    def level_before(self, pause: Row) -> Row | None:
        """The level a break comes after ("после 6-го уровня")."""
        earlier = self.rows[: self.rows.index(pause)]
        return next((row for row in reversed(earlier) if not row.is_break), None)

    @property
    def addon_row(self) -> Row | None:
        return next((row for row in self.rows if row.addon_break), None)

    @property
    def rebuys_until(self) -> Row | None:
        """The last level before the add-on break: rebuys are open until it ends."""
        addon = self.addon_row
        return self.level_before(addon) if addon else None

    @property
    def at_addon_break(self) -> bool:
        return self.current.addon_break and not self.finished


def clock_at(
    rows: Sequence[Row],
    position: int,
    started_at: datetime.datetime,
    paused_at: datetime.datetime | None,
    now: datetime.datetime,
) -> Clock:
    """Where the clock is at ``now``: walk from ``position`` through the rows' minutes."""
    index = next((i for i, row in enumerate(rows) if row.position == position), 0)
    elapsed = (paused_at or now) - started_at
    while index < len(rows) - 1 and elapsed >= rows[index].duration:
        elapsed -= rows[index].duration
        index += 1
    finished = elapsed >= rows[index].duration
    return Clock(rows, index, elapsed, paused_at is not None, finished)


def settled(
    clock: Clock, paused_at: datetime.datetime | None, now: datetime.datetime
) -> tuple[int, datetime.datetime]:
    """``position`` and ``started_at`` that count from the current row and give the same clock.

    A finished clock is held at the end of its last row, so a level added later starts fresh
    instead of already being part-way through.
    """
    elapsed = min(clock.elapsed, clock.current.duration)
    return clock.current.position, (paused_at or now) - elapsed
