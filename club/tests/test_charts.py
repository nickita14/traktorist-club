import datetime

from club.charts import BOTTOM, HEIGHT, TOP, X_PAD, net_chart
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
