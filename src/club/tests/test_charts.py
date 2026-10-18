import datetime
import logging

from club.charts import (
    BOTTOM,
    HEIGHT,
    MONTH_LABEL_Y,
    TOP,
    X_PAD,
    YEAR_BOTTOM,
    YEAR_TOP,
    net_chart,
    year_chart,
)
from club.stats import TimelinePoint


def timeline(*points):
    return [TimelinePoint(datetime.date(*date), net) for date, net in points]


def parse(points: str) -> list[tuple[float, float]]:
    return [tuple(map(float, pair.split(","))) for pair in points.split()]


class TestNetChart:
    def test_no_games_no_chart(self):
        assert net_chart([]) is None

    def test_single_game_is_a_segment_from_zero(self):
        chart = net_chart(timeline(((2025, 3, 1), 100)))

        assert parse(chart.points) == [(X_PAD, BOTTOM), (100 - X_PAD, TOP)]
        assert chart.zero_y == BOTTOM
        assert (chart.last_x, chart.last_y) == (100 - X_PAD, TOP)
        assert chart.height == HEIGHT

    def test_losses_go_below_the_zero_line(self):
        chart = net_chart(timeline(((2025, 3, 1), 100), ((2025, 3, 8), -300)))

        (_, start), (_, high), (_, low) = parse(chart.points)
        # Range −300..100: zero sits a quarter of the way down.
        assert high == TOP and low == BOTTOM
        assert start == chart.zero_y == TOP + (BOTTOM - TOP) / 4

    def test_flat_line_runs_through_the_middle(self):
        chart = net_chart(timeline(((2025, 3, 1), 0)))

        assert chart.zero_y == chart.last_y == (TOP + BOTTOM) / 2

    def test_points_are_evenly_spaced_by_game(self):
        chart = net_chart(
            timeline(
                ((2025, 1, 1), 10), ((2025, 1, 2), 20), ((2025, 12, 31), 30), ((2026, 1, 1), 40)
            )
        )

        xs = [x for x, _ in parse(chart.points)]
        steps = {round(b - a, 2) for a, b in zip(xs, xs[1:], strict=False)}
        assert len(steps) == 1

    def test_year_marks(self):
        chart = net_chart(
            timeline(
                ((2024, 11, 1), 10), ((2025, 3, 1), 20), ((2025, 4, 1), 30), ((2026, 1, 5), 40)
            )
        )

        xs = [x for x, _ in parse(chart.points)]
        assert [(m.year, m.line) for m in chart.years] == [
            (2024, False),
            (2025, True),
            (2026, True),
        ]
        assert chart.years[0].x == X_PAD
        # Each new year's line sits between its first game and the game before it.
        assert xs[1] < chart.years[1].x < xs[2]
        assert xs[3] < chart.years[2].x < xs[4]


class TestYearChart:
    def test_no_games_no_chart(self):
        assert year_chart([], 2025) is None

    def test_starts_at_zero_on_the_first_of_january(self):
        chart = year_chart(timeline(((2025, 3, 10), 100)), 2025)

        assert parse(chart.points)[0] == (X_PAD, chart.zero_y)
        assert chart.zero_y == YEAR_BOTTOM
        assert (chart.height, chart.label_y, chart.years) == (HEIGHT, MONTH_LABEL_Y, [])

    def test_a_single_game_is_one_step(self):
        chart = year_chart(timeline(((2025, 3, 10), 100)), 2025)

        (x0, y0), (x1, y1), (x2, y2) = parse(chart.points)
        # Along the zero line to the game's date, then straight up to the total.
        assert y1 == y0 == YEAR_BOTTOM
        assert x1 == x2 == chart.last_x
        assert y2 == chart.last_y == YEAR_TOP
        assert x0 < x1 < 100 - X_PAD

    def test_steps_follow_the_calendar(self):
        chart = year_chart(timeline(((2025, 1, 11), 10), ((2025, 1, 21), 20)), 2025)

        xs = [x for x, _ in parse(chart.points)]
        # Ten days apart twice: 1 -> 11 -> 21 January in a 31-day axis.
        assert round(xs[1] - xs[0], 1) == round(xs[3] - xs[1], 1)

    def test_games_on_one_date_share_their_x(self):
        chart = year_chart(timeline(((2025, 3, 1), 50), ((2025, 3, 1), 20)), 2025)

        coordinates = parse(chart.points)
        assert len({x for x, _ in coordinates[1:]}) == 1
        assert [y for _, y in coordinates[3:]] == [coordinates[2][1], chart.last_y]

    def test_losses_go_below_the_zero_line(self):
        chart = year_chart(timeline(((2025, 2, 1), 100), ((2025, 2, 8), -300)), 2025)

        assert chart.zero_y == YEAR_TOP + (YEAR_BOTTOM - YEAR_TOP) / 4
        assert chart.last_y == YEAR_BOTTOM

    def test_months_up_to_the_last_game(self):
        chart = year_chart(timeline(((2025, 2, 3), 10), ((2025, 4, 30), 20)), 2025)

        assert [(m.label, m.line) for m in chart.months] == [
            ("ЯНВ", False),
            ("ФЕВ", True),
            ("МАР", True),
            ("АПР", True),
        ]
        assert chart.months[0].start == X_PAD
        # The axis ends with the last game's month; each label sits in the middle of its month.
        assert chart.last_x < 100 - X_PAD
        for month in chart.months:
            assert month.start < month.x

    def test_a_december_game_ends_the_axis_with_december(self):
        chart = year_chart(timeline(((2025, 12, 31), 10)), 2025)

        assert len(chart.months) == 12
        assert chart.months[-1].label == "ДЕК"
        assert chart.last_x < 100 - X_PAD

    def test_flat_line_runs_through_the_middle(self):
        chart = year_chart(timeline(((2025, 5, 1), 0)), 2025)

        assert chart.zero_y == chart.last_y == (YEAR_TOP + YEAR_BOTTOM) / 2

    def test_points_outside_the_year_are_dropped_and_logged_once(self, caplog):
        points = timeline(((2024, 12, 31), 99), ((2025, 3, 1), 10), ((2026, 1, 1), 99))

        with caplog.at_level(logging.WARNING, logger="club.charts"):
            chart = year_chart(points, 2025)

        assert len(parse(chart.points)) == 3  # the start and one step
        assert [m.label for m in chart.months] == ["ЯНВ", "ФЕВ", "МАР"]
        assert [r.getMessage() for r in caplog.records] == [
            "year_chart: 2 point(s) outside 2025 dropped"
        ]

    def test_only_points_outside_the_year_give_no_chart(self, caplog):
        with caplog.at_level(logging.WARNING, logger="club.charts"):
            assert year_chart(timeline(((2024, 6, 1), 10)), 2025) is None
        assert len(caplog.records) == 1
