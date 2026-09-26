"""Geometry for the server-side net chart on the player card. Pure functions, no database.

The SVG is drawn in two layers so it can stretch to any width without distorting text or dots:
the polyline lives in an inner <svg viewBox="0 0 100 HEIGHT" preserveAspectRatio="none">, where
x runs from 0 to 100; everything else sits in the outer <svg> with x in percent and y in pixels.
The outer <svg> has a fixed pixel height of HEIGHT, so y means the same in both layers.
The x axis is the game index, not the calendar: gaps between seasons would flatten the line.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from club.stats import TimelinePoint

HEIGHT = 180
TOP = 28  # room for the year labels
BOTTOM = HEIGHT - 12
X_PAD = 1.5  # percent of the width kept free on each side, so the end dot is not clipped


@dataclass(frozen=True)
class YearMark:
    x: float  # percent
    year: int
    line: bool  # the first year is only labelled, the following ones get a vertical line


@dataclass(frozen=True)
class NetChart:
    height: int
    points: str  # polyline points in the inner viewBox
    zero_y: float
    last_x: float
    last_y: float
    years: list[YearMark]


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
