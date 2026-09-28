"""Layout of the live screens in a real browser: stat strips without empty cells and number
inputs that open the phone keypad without spin buttons, on a phone and on a wide screen."""

import pytest
from django.urls import reverse

from club.models import SeasonKind
from live import actions
from live.tests.conftest import key, live_game, seat
from live.tests.test_browser import PHONE, phone  # noqa: F401 - the fixture

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

# Stat strips whose last row is not full (an empty cell with the rule background shows).
UNEVEN_STRIPS = """() => [...document.querySelectorAll('.stat-strip')].filter(strip => {
  const columns = getComputedStyle(strip).gridTemplateColumns.split(' ').length;
  return strip.children.length % columns !== 0;
}).length"""

# Number inputs that would not open the phone keypad or that keep native spin buttons.
BAD_NUMBER_INPUTS = """() => [...document.querySelectorAll('input[type=number]')].filter(input =>
  input.inputMode !== 'numeric' || getComputedStyle(input).appearance !== 'textfield'
).length"""


def assert_layout(page):
    """Full stat strips and keypad-friendly number inputs, on a phone and on a wide screen."""
    for width in (PHONE["viewport"]["width"], 1280):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate(UNEVEN_STRIPS) == 0, f"uneven stat strip at {width}px"
    page.set_viewport_size(PHONE["viewport"])
    assert page.evaluate(BAD_NUMBER_INPUTS) == 0


def test_board_and_close_layout(phone):  # noqa: F811
    tour = live_game(SeasonKind.TOUR)
    seat(tour, "Альфа", "Браво")
    assert_layout(phone(reverse("live:board", args=[tour.pk])))
    cash = live_game(SeasonKind.CASH)
    seat(cash, "Чарли", "Дельта")
    page = phone(reverse("live:board", args=[cash.pk]))
    assert_layout(page)
    page.get_by_label("Выход: Чарли").click()
    expect(page.locator("#exit-chips")).to_be_focused()
    assert_layout(page)
    assert_layout(phone(reverse("live:close", args=[cash.pk])))


def test_results_layout(phone):  # noqa: F811
    tour = live_game(SeasonKind.TOUR)
    _, bravo = seat(tour, "Альфа", "Браво")
    actions.advance_stage(tour.pk, key(), None, "rebuys")
    actions.advance_stage(tour.pk, key(), None, "addon")
    actions.eliminate(tour.pk, key(), None, bravo.pk)
    assert_layout(phone(reverse("live:results", args=[tour.pk])))
