"""The blind clock computation (live.clock), over the cases the display's JS runs too."""

import datetime
import json
from pathlib import Path

import pytest

from live import clock

CASES = json.loads((Path(__file__).parent / "clock_cases.json").read_text())
NOW = datetime.datetime(2026, 9, 28, 19, 0, tzinfo=datetime.UTC)


def case_rows(spec=CASES["rows"]) -> list[clock.Row]:
    rows, number = [], 0
    for position, (kind, minutes) in enumerate(spec, start=1):
        level = kind == "L"
        number += level
        rows.append(
            clock.Row(
                position=position,
                small_blind=25 * position if level else None,
                big_blind=50 * position if level else None,
                ante=0,
                minutes=minutes,
                label="" if level else "Перерыв",
                addon_break=position == 4,
                number=number if level else None,
            )
        )
    return rows


def at(seconds):
    return None if seconds is None else NOW + datetime.timedelta(seconds=seconds)


@pytest.mark.parametrize("case", CASES["cases"], ids=[case["name"] for case in CASES["cases"]])
def test_shared_cases(case):
    state = clock.clock_at(
        case_rows(), case["position"], at(case["started"]), at(case["paused"]), NOW
    )
    until = state.until_break

    assert {
        "position": state.current.position,
        "remaining": state.remaining.total_seconds(),
        "finished": state.finished,
        "until_break": None if until is None else until.total_seconds(),
    } == case["expect"]


def test_level_numbers_skip_breaks():
    state = clock.clock_at(case_rows(), 5, NOW, None, NOW)
    assert [row.number for row in state.rows] == [1, 2, 3, None, 4, None, 5]
    assert (state.current.number, state.level_count) == (4, 5)
    assert state.next_level.position == 7
    assert state.next_break.position == 6
    assert state.level_before(state.next_break).number == 4


def test_rebuys_until_the_level_before_the_addon_break():
    state = clock.clock_at(case_rows(), 1, NOW, None, NOW)
    assert state.addon_row.position == 4
    assert state.rebuys_until.number == 3
    assert not state.at_addon_break
    assert clock.clock_at(case_rows(), 4, NOW, None, NOW).at_addon_break


def test_played_rows():
    state = clock.clock_at(case_rows(), 3, NOW, None, NOW)
    assert [state.played(row) for row in state.rows[:4]] == [True, True, False, False]


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "00:00"), (0.2, "00:01"), (59, "00:59"), (877, "14:37"), (3877, "1:04:37"), (-5, "00:00")],
)
def test_format(seconds, text):
    assert clock.format_clock(datetime.timedelta(seconds=seconds)) == text


def test_settled_keeps_the_clock_and_holds_a_finished_one():
    rows = case_rows()
    running = clock.clock_at(rows, 1, at(-1500), None, NOW)
    assert clock.settled(running, None, NOW) == (2, at(-300))

    done = clock.clock_at(rows, 7, at(-5000), None, NOW)
    assert clock.settled(done, None, NOW) == (7, at(-1200))

    paused = clock.clock_at(rows, 1, at(-1500), at(-600), NOW)
    assert clock.settled(paused, at(-600), NOW) == (1, at(-1500))
