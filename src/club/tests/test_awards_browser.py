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
    assert len(cells) == 6
    assert cells[0][1] == cells[1][1] and cells[0][0] < cells[1][0]  # two per row
    assert cells[2][1] > cells[0][1] and cells[2][0] == cells[0][0]
    assert len({top for _, top in cells}) == 3  # rows aligned: three of them
    cells = open_page(path, 800).evaluate(CELLS)
    assert len({top for _, top in cells}) == 2  # three per row from md
    cells = open_page(path, 1280).evaluate(CELLS)
    assert len({top for _, top in cells}) == 1  # one row of six from lg


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


# Where the text of each header of the badge matrix ends (a Range: the text, not the box).
HEADER_TEXT_BOTTOMS = """() => [...document.querySelectorAll(
  '[aria-labelledby="badges-title"] thead th')].map(th => {
  const range = document.createRange();
  range.selectNodeContents(th.querySelector('.sort-lines') || th);
  return Math.round(range.getBoundingClientRect().bottom);
})"""

# The height of each body row of the badge matrix.
MATRIX_ROWS = """() => [...document.querySelectorAll(
  '[aria-labelledby="badges-title"] tbody tr')].map(row => row.getBoundingClientRect().height)"""


def test_badge_matrix_rows_are_one_line_on_desktop(open_page, club, award_rules):
    # A longer invented name and nickname, as long as the club's longest. Longer ones still
    # stay on one line; the table then scrolls inside its container.
    alpha = club["players"]["A"]
    type(alpha).objects.filter(pk=alpha.pk).update(name="Вениамин Кочетков", nickname="Веялка")
    page = open_page(reverse("honors"), 1280)
    scroller = page.locator('[aria-labelledby="badges-title"] .ledger-scroll')
    assert scroller.evaluate("el => el.scrollWidth <= el.clientWidth")  # no sideways scroll
    heights = page.evaluate(MATRIX_ROWS)
    assert len(heights) == 3
    assert all(height <= 45 for height in heights)  # the ledger's 44px row: one line
    # Every header's text ends on the same line, two-line ones and the plain "№" alike.
    bottoms = page.evaluate(HEADER_TEXT_BOTTOMS)
    assert max(bottoms) - min(bottoms) <= 1  # "№" has a taller line box: a pixel of rounding
    head = page.locator('[aria-labelledby="badges-title"] thead')
    # And they keep sorting.
    head.get_by_role("link", name="Ни одного прогула").click()
    active = page.locator('[aria-labelledby="badges-title"] thead a[aria-current]')
    expect(active).to_have_class("sort-link sort-link-lines sort-desc")
    assert page.url.endswith("/honors/?sort=no_skip")
    assert page.evaluate("window.cspViolations") == []


def test_badge_matrix_scrolls_inside_on_phones(open_page, club, award_rules):
    page = open_page(reverse("honors"), PHONE)
    scroller = page.locator('[aria-labelledby="badges-title"] .ledger-scroll')
    assert scroller.evaluate("el => el.scrollWidth > el.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE
    assert page.evaluate("window.cspViolations") == []
