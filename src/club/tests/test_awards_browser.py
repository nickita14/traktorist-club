"""Awards in a real browser at phone width: the five-item nav, no sideways page scroll, the badge
grid, the marks on a game sheet, the rank bar. Data: the club fixture with ``award_rules``."""

import pytest
from django.urls import reverse

from conftest import COLLECT_CSP_VIOLATIONS

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

PHONE = 390


@pytest.fixture
def open_page(browser, live_server):
    contexts = []

    def open_at(path, width):
        context = browser.new_context(viewport={"width": width, "height": 900})
        contexts.append(context)
        context.add_init_script(COLLECT_CSP_VIOLATIONS)
        page = context.new_page()
        page.goto(live_server.url + path)
        page.evaluate("document.fonts.ready")
        return page

    yield open_at
    for context in contexts:
        context.close()


def pages(club):
    return {
        "home": reverse("home"),
        "honors": reverse("honors"),
        "card": reverse("player_detail", args=[club["players"]["A"].slug]),
        "sheet": reverse("game_detail", args=[club["games"]["t2"].pk]),
    }


def test_nav_fits_and_pages_never_scroll_sideways(open_page, club, award_rules):
    for name, path in pages(club).items():
        page = open_page(path, PHONE)
        links = page.locator(".site-nav a")
        assert links.count() == 5, name
        tops = set()
        for index in range(links.count()):
            box = links.nth(index).bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= PHONE, name
            assert box["height"] >= 44, name
            tops.add(round(box["y"]))
        assert len(tops) == 1, name  # one line
        expect(page.locator(".site-nav .nav-short")).to_be_visible()
        expect(page.locator(".site-nav .nav-long")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth") <= PHONE, name
        assert page.evaluate("window.cspViolations") == [], name


def test_desktop_nav_has_the_long_label(open_page, club, award_rules):
    page = open_page(reverse("honors"), 1280)
    link = page.get_by_role("link", name="Доска почёта")
    expect(link).to_have_attribute("aria-current", "true")
    expect(page.locator(".site-nav .nav-short")).to_be_hidden()
    assert page.evaluate("window.cspViolations") == []


# The grid cells (li), not the tilted stamps: a rotated box reports a shifted rectangle.
CELLS = """() => [...document.querySelectorAll('.award-grid > li')].map(cell => {
  const box = cell.getBoundingClientRect();
  return [Math.round(box.left), Math.round(box.top)];
})"""


def test_badge_grid_columns(open_page, club, award_rules):
    path = pages(club)["card"]
    cells = open_page(path, PHONE).evaluate(CELLS)
    assert len(cells) == 5
    assert cells[0][1] == cells[1][1] and cells[0][0] < cells[1][0]  # two per row
    assert cells[2][1] > cells[0][1] and cells[2][0] == cells[0][0]
    cells = open_page(path, 1280).evaluate(CELLS)
    assert len({top for _, top in cells}) == 1  # one row of five


def test_rank_bar_and_cells(open_page, club, award_rules):
    page = open_page(pages(club)["card"], PHONE)
    bars = page.locator(".rank-progress")
    assert bars.count() == 3
    assert round(bars.first.bounding_box()["height"]) == 8
    # Stacked on phones.
    lefts = {round(cell.bounding_box()["x"]) for cell in page.locator(".rank-cells > div").all()}
    assert len(lefts) == 1
    assert page.evaluate("window.cspViolations") == []


def test_mark_keeps_the_row_height(open_page, club, award_rules):
    page = open_page(pages(club)["sheet"], PHONE)
    rows = page.locator('[aria-labelledby="results-title"] .ledger tbody tr')
    marked = rows.filter(has=page.locator(".award-mark"))
    plain = rows.filter(has_not=page.locator(".award-mark"))
    expect(marked).to_have_count(1)  # Альфа's hat-trick
    assert marked.bounding_box()["height"] == plain.first.bounding_box()["height"]
    mark = marked.locator(".award-mark").bounding_box()
    assert mark["x"] + mark["width"] <= PHONE
    assert page.evaluate("window.cspViolations") == []
