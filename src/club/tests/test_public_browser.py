"""Public pages in a real browser: the section nav on phones, sortable headers (links, the drawn
arrow, touch targets, the mobile sort row), the year switcher and chart."""

import re

import pytest
from django.urls import reverse

from club.models import SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season
from conftest import COLLECT_CSP_VIOLATIONS, RECORD_SETTLED, settle_navigation
from live.tests.test_browser import assert_touch_targets

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def season():
    season = make_season(2025, SeasonKind.TOUR)
    game = make_game(season, day=7, month=6)
    make_result(game, make_player("Альфа"), buyin=50, payout=100, place=1)
    make_result(game, make_player("Браво"), buyin=50, payout=0)
    return season


@pytest.fixture
def open_page(browser, live_server):
    contexts = []

    def open_at(path, width):
        context = browser.new_context(viewport={"width": width, "height": 900})
        contexts.append(context)
        context.add_init_script(COLLECT_CSP_VIOLATIONS)
        context.add_init_script(RECORD_SETTLED)
        page = context.new_page()
        page.goto(live_server.url + path)
        page.evaluate("document.fonts.ready")
        return page

    yield open_at
    for context in contexts:
        context.close()


ARROW = """() => {
  const link = document.querySelector('.ledger th .sort-link[aria-current]');
  const after = getComputedStyle(link, '::after');
  return {top: after.borderTopWidth, bottom: after.borderBottomWidth, content: after.content};
}"""


def test_desktop_headers(open_page, season):
    page = open_page(reverse("season", args=[2025, "tour"]) + "?sort=games&dir=asc", 1280)

    expect(page.locator(".sort-links")).to_be_hidden()
    arrow = page.evaluate(ARROW)
    assert arrow["content"] == '""' and arrow["bottom"] != "0px" and arrow["top"] == "0px"
    head = page.locator("#standings-title + .sort-links + .ledger-scroll thead tr").bounding_box()
    assert head["height"] <= 48  # the links fill the cells, the row does not grow
    page.get_by_role("link", name="Игр", exact=True).click()
    expect(page).to_have_url(re.compile(r"/2025/tour/\?sort=games$"))
    assert page.evaluate("window.cspViolations") == []


@pytest.mark.parametrize("width", [360, 390])
def test_phone_sort_row(open_page, season, width):
    page = open_page(reverse("season", args=[2025, "tour"]), width)

    row = page.locator(".sort-links")
    expect(row).to_be_visible()
    assert row.locator("a").count() == 3
    assert_touch_targets(page, ".sort-links a")
    row.get_by_role("link", name="игры").click()
    expect(page).to_have_url(re.compile(r"\?sort=games$"))
    # Until its stylesheet applies, the new page's table shows every column (500px wide).
    settle_navigation(page)
    expect(page.locator(".sort-links a[aria-current]")).to_have_text("игры")
    assert_touch_targets(page, ".sort-links a")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.evaluate("window.cspViolations") == []


@pytest.mark.parametrize("width", [360, 390])
def test_phone_nav_on_one_line(open_page, season, width):
    # A cash game too, so "Кэш" (the shortest label) is a link.
    cash = make_game(make_season(2025, SeasonKind.CASH), day=8, month=6)
    make_result(cash, make_player("Чарли"), buyin=50, payout=50)
    page = open_page(reverse("season", args=[2025, "tour"]), width)

    items = page.locator(".site-nav li > a")
    assert items.count() == 5
    tops = {round(items.nth(index).bounding_box()["y"]) for index in range(5)}
    assert len(tops) == 1
    assert page.locator(".site-nav").bounding_box()["height"] <= 45
    expect(page.locator(".site-nav .nav-short")).to_be_visible()
    assert_touch_targets(page, ".site-nav a")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.evaluate("window.cspViolations") == []


# The header rows (thead) of every sortable table, in px.
HEADER_ROWS = "[...document.querySelectorAll('.ledger thead tr')].map(row => row.offsetHeight)"


@pytest.mark.parametrize(
    ("name", "args", "width"),
    [
        ("player_list", [], 360),
        ("player_list", [], 390),
        ("season", [2025, "tour"], 360),
        ("season", [2025, "tour"], 390),
        ("season", [2025, "tour"], 1280),  # the "1", "2", "3" columns
    ],
)
def test_sort_link_targets(open_page, season, name, args, width):
    page = open_page(reverse(name, args=args), width)

    assert page.locator(".sort-link:visible").count() >= 3
    assert_touch_targets(page, ".sort-link")
    # The links fill their cells: the header row keeps its height.
    assert all(height <= 48 for height in page.evaluate(HEADER_ROWS))
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.evaluate("window.cspViolations") == []


def test_honors_sort_link_targets(open_page, club, award_rules):
    page = open_page(reverse("honors"), 390)

    assert page.locator(".ledger .sort-link-lines").count() >= 3
    assert_touch_targets(page, ".sort-link")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert page.evaluate("window.cspViolations") == []


def test_header_link_hover_underline(open_page, season):
    page = open_page(reverse("season", args=[2025, "tour"]), 1280)

    link = page.locator(".ledger th .sort-link:not([aria-current])").first
    assert link.evaluate("el => getComputedStyle(el).textDecorationLine") == "none"
    link.hover()
    assert link.evaluate("el => getComputedStyle(el).textDecorationLine") == "underline"


def test_favicons_load_without_csp_violations(open_page, season):
    page = open_page(reverse("season", args=[2025, "tour"]), 1280)

    loaded = page.evaluate("""async () => {
      const links = [...document.querySelectorAll('link[rel$="icon"]')];
      const answers = await Promise.all(links.map(link => fetch(link.href)));
      const ico = await fetch('/favicon.ico');
      return {
        icons: answers.map(answer => [answer.status, answer.headers.get('Content-Type')]),
        ico: [ico.status, ico.redirected, ico.headers.get('Content-Type')],
      };
    }""")

    assert loaded["icons"] == [[200, "image/svg+xml"], [200, "image/png"], [200, "image/png"]]
    assert loaded["ico"][:2] == [200, True]
    assert "icon" in loaded["ico"][2]
    assert page.evaluate("window.cspViolations") == []


@pytest.fixture
def three_years():
    """A player with games in three years, each with a cash game too."""
    player = make_player("Альфа", nickname="Трактор")
    other = make_player("Браво")
    for year in (2023, 2024, 2025):
        for kind in (SeasonKind.TOUR, SeasonKind.CASH):
            game = make_game(make_season(year, kind), day=7, month=6)
            if kind == SeasonKind.TOUR:
                make_result(game, player, buyin=50, payout=100, place=1)
                make_result(game, other, buyin=50, payout=0, place=2)
            else:
                make_result(game, player, buyin=100, payout=70)
                make_result(game, other, buyin=100, payout=130)
    return player


@pytest.mark.parametrize("path", ["player_detail", "player_games"])
def test_phone_year_switcher(open_page, three_years, path):
    page = open_page(reverse(path, args=[three_years.slug]), 390)

    title = page.locator("#doc-title").bounding_box()
    switcher = page.locator(".doc-head nav[aria-label]")
    # Under the name on phones, like the season pages.
    assert switcher.bounding_box()["y"] >= title["y"] + title["height"]
    links = switcher.locator("a")
    assert links.count() == 4
    for index in range(links.count()):
        assert links.nth(index).bounding_box()["height"] >= 44
    expect(switcher.locator("a[aria-current]")).to_have_text("2025")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert page.evaluate("window.cspViolations") == []


def test_desktop_year_switcher_sits_right_of_the_name(open_page, three_years):
    page = open_page(reverse("player_detail", args=[three_years.slug]), 1280)

    title = page.locator("#doc-title").bounding_box()
    switcher = page.locator(".doc-head nav[aria-label]").bounding_box()
    assert switcher["x"] > title["x"] + title["width"]
    assert switcher["y"] < title["y"] + title["height"]
    page.get_by_role("link", name="Все годы").click()
    expect(page).to_have_url(re.compile(r"\?year=all$"))
    expect(page.locator(".net-chart .chart-year")).to_have_count(2)
    assert page.evaluate("window.cspViolations") == []


def test_phone_year_chart_fits(open_page, three_years):
    page = open_page(reverse("player_detail", args=[three_years.slug]), 390)

    months = page.locator(".net-chart .chart-month")
    expect(months).to_have_count(6)
    boxes = [months.nth(index).bounding_box() for index in range(6)]
    # Month labels do not run into each other.
    assert all(a["x"] + a["width"] <= b["x"] for a, b in zip(boxes, boxes[1:], strict=False))
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
