"""Geometry for the server-side net chart on the player card. Pure functions, no database.

The SVG is drawn in two layers so it can stretch to any width without distorting text or dots:
the polyline lives in an inner <svg viewBox="0 0 100 HEIGHT" preserveAspectRatio="none">, where
x runs from 0 to 100; everything else sits in the outer <svg> with x in percent and y in pixels.
The outer <svg> has a fixed pixel height of HEIGHT, so y means the same in both layers.
The all-time chart's x axis is the game index, not the calendar: gaps between seasons would
flatten the line. The chart of one year runs along the calendar from 1 January instead, as a
step line: the total holds between game nights and moves on the night itself.
"""

import datetime
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from club.stats import TimelinePoint

logger = logging.getLogger(__name__)

HEIGHT = 180
TOP = 28  # room for the year labels
BOTTOM = HEIGHT - 12
X_PAD = 1.5  # percent of the width kept free on each side, so the end dot is not clipped
# The chart of one year: no labels on top, the month labels under the line.
YEAR_TOP = 12
YEAR_BOTTOM = HEIGHT - 24
MONTH_LABEL_Y = HEIGHT - 6
MONTHS = ("ЯНВ", "ФЕВ", "МАР", "АПР", "МАЙ", "ИЮН", "ИЮЛ", "АВГ", "СЕН", "ОКТ", "НОЯ", "ДЕК")


@dataclass(frozen=True)
class YearMark:
    x: float  # percent
    year: int
    line: bool  # the first year is only labelled, the following ones get a vertical line


@dataclass(frozen=True)
class MonthMark:
    start: float  # percent: where the month begins (its vertical line)
    x: float  # percent: the middle of the month, where the label goes
    label: str
    line: bool  # January starts at the left edge and gets no line


@dataclass(frozen=True)
class NetChart:
    height: int
    points: str  # polyline points in the inner viewBox
    zero_y: float
    last_x: float
    last_y: float
    years: list[YearMark] = field(default_factory=list)
    months: list[MonthMark] = field(default_factory=list)
    label_y: int = 12  # baseline of the year or month labels


def _round(value: float) -> float:
    return round(value, 2)


def net_chart(timeline: Sequence[TimelinePoint]) -> NetChart | None:
    """Chart geometry for a cumulative net timeline, or None when there are no games.

    The line starts at zero before the first game, so a single game still draws a segment.
    """
    if not timeline:
        return None
    values = [0, *(point.net for point in timeline)]
    low, high = min(values), max(values)
    if low == high:  # every game broke even: a flat line through the middle
        low, high = low - 1, high + 1
    span = high - low
    steps = len(values) - 1

    def x(index: int) -> float:
        return _round(X_PAD + index / steps * (100 - 2 * X_PAD))

    def y(value: int) -> float:
        return _round(TOP + (high - value) / span * (BOTTOM - TOP))

    points = " ".join(f"{x(i)},{y(v)}" for i, v in enumerate(values))

    # timeline[i] is values[i + 1]; a new year's line goes halfway between its first game and
    # the game before it.
    years = [YearMark(x(0), timeline[0].date.year, line=False)]
    for i in range(1, len(timeline)):
        if timeline[i].date.year != timeline[i - 1].date.year:
            years.append(YearMark(_round((x(i) + x(i + 1)) / 2), timeline[i].date.year, line=True))

    return NetChart(
        height=HEIGHT,
        points=points,
        zero_y=y(0),
        last_x=x(steps),
        last_y=y(values[-1]),
        years=years,
    )


def _month_start(year: int, month: int) -> datetime.date:
    """The first day of ``month`` of ``year``; month 13 is the next January."""
    return datetime.date(year + month // 13, (month - 1) % 12 + 1, 1)


def year_chart(timeline: Sequence[TimelinePoint], year: int) -> NetChart | None:
    """Chart geometry for one year's cumulative net (counted from zero on 1 January), or None
    when there are no games that year.

    The axis runs from 1 January to the end of the month of the last game. Points outside
    ``year`` cannot be placed on it: they are dropped (and logged), never drawn off the axis.
    """
    inside = [point for point in timeline if point.date.year == year]
    if len(inside) != len(timeline):
        logger.warning(
            "year_chart: %d point(s) outside %d dropped", len(timeline) - len(inside), year
        )
    if not inside:
        return None
    values = [0, *(point.net for point in inside)]
    low, high = min(values), max(values)
    if low == high:  # every game broke even: a flat line through the middle
        low, high = low - 1, high + 1
    span = high - low
    start = _month_start(year, 1)
    last_month = inside[-1].date.month
    days = (_month_start(year, last_month + 1) - start).days

    def x(date: datetime.date) -> float:
        return _round(X_PAD + (date - start).days / days * (100 - 2 * X_PAD))

    def y(value: int) -> float:
        return _round(YEAR_TOP + (high - value) / span * (YEAR_BOTTOM - YEAR_TOP))

    # A step line: along at the old total up to the game's date, then up or down to the new one.
    coordinates = [(x(start), y(0))]
    for point in inside:
        coordinates += [(x(point.date), coordinates[-1][1]), (x(point.date), y(point.net))]
    points = " ".join(f"{px},{py}" for px, py in coordinates)

    months = []
    for month in range(1, last_month + 1):
        begin, end = x(_month_start(year, month)), x(_month_start(year, month + 1))
        months.append(MonthMark(begin, _round((begin + end) / 2), MONTHS[month - 1], month > 1))

    return NetChart(
        height=HEIGHT,
        points=points,
        zero_y=y(0),
        last_x=x(inside[-1].date),
        last_y=y(values[-1]),
        months=months,
        label_y=MONTH_LABEL_Y,
    )
