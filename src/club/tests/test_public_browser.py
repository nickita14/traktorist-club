"""Sortable standings in a real browser: header links, the drawn arrow, the mobile sort row."""

import re

import pytest
from django.urls import reverse

from club.models import SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season
from conftest import COLLECT_CSP_VIOLATIONS

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


def test_phone_sort_row(open_page, season):
    page = open_page(reverse("season", args=[2025, "tour"]), 390)

    row = page.locator(".sort-links")
    expect(row).to_be_visible()
    for index in range(row.locator("a").count()):
        assert row.locator("a").nth(index).bounding_box()["height"] >= 44
    row.get_by_role("link", name="игры").click()
    expect(page).to_have_url(re.compile(r"\?sort=games$"))
    expect(page.locator(".sort-links a[aria-current]")).to_have_text("игры")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert page.evaluate("window.cspViolations") == []


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
