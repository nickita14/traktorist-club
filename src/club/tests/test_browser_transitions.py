"""Page transitions (View Transitions API) in Chromium: declared in CSS, used by same-origin
navigations and by the board's button swaps, off with reduced motion, and CSP-clean."""

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse

from club.models import SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season
from conftest import COLLECT_CSP_VIOLATIONS
from live.tests.conftest import live_game, seat

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.browser

# Whether the page was revealed with a view transition (read on the new page).
RECORD_REVEAL = """window.addEventListener('pagereveal', event => {
  window.revealedWithTransition = Boolean(event.viewTransition);
});"""
# Counts document.startViewTransition calls (htmx uses it for "transition:true" swaps).
COUNT_TRANSITIONS = """window.transitions = 0;
if (document.startViewTransition) {
  const start = document.startViewTransition.bind(document);
  document.startViewTransition = callback => { window.transitions += 1; return start(callback); };
}"""

VIEW_TRANSITION_RULES = """() => {
  const found = [];
  const walk = (rules, media) => {
    for (const rule of rules) {
      if (rule instanceof CSSMediaRule) walk(rule.cssRules, rule.conditionText);
      else if (rule instanceof CSSLayerBlockRule) walk(rule.cssRules, media);
      else if (rule.constructor.name === 'CSSViewTransitionRule')
        found.push({media, navigation: rule.navigation, types: [...(rule.types || [])]});
    }
  };
  for (const sheet of document.styleSheets) walk(sheet.cssRules, null);
  return found;
}"""


def test_css_declares_the_transitions(browser):
    css = open(finders.find(settings.TAILWIND_CLI_DIST_CSS)).read()
    page = browser.new_page()
    page.set_content(f"<style>{css}</style>")

    rules = page.evaluate(VIEW_TRANSITION_RULES)
    page.close()

    assert {"media": None, "navigation": "auto", "types": ["page"]} in rules
    reduced = [rule for rule in rules if rule["media"]]
    assert reduced == [
        {"media": "(prefers-reduced-motion: reduce)", "navigation": "none", "types": []}
    ]


@pytest.fixture
def season(db):
    season = make_season(2025, SeasonKind.TOUR)
    game = make_game(season, day=7, month=6)
    make_result(game, make_player("Альфа"), buyin=50, payout=100, place=1)
    make_result(game, make_player("Браво"), buyin=50, payout=0)
    return season


@pytest.fixture
def open_page(browser, live_server):
    contexts = []

    def open_at(path, *, reduced_motion="no-preference", session=None):
        context = browser.new_context(
            viewport={"width": 390, "height": 844}, reduced_motion=reduced_motion
        )
        contexts.append(context)
        if session:
            context.add_cookies([{"name": "sessionid", "value": session, "url": live_server.url}])
        context.add_init_script(COLLECT_CSP_VIOLATIONS)
        context.add_init_script(RECORD_REVEAL)
        context.add_init_script(COUNT_TRANSITIONS)
        page = context.new_page()
        page.goto(live_server.url + path)
        return page

    yield open_at
    for context in contexts:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_navigation_between_pages_transitions(open_page, season):
    page = open_page(reverse("season", args=[2025, "tour"]))

    page.get_by_role("link", name="Игроки").click()
    page.wait_for_url(f"**{reverse('player_list')}")

    assert page.evaluate("window.revealedWithTransition") is True
    assert page.evaluate("window.cspViolations") == []


@pytest.mark.django_db(transaction=True)
def test_reduced_motion_navigates_without_a_transition(open_page, season):
    page = open_page(reverse("season", args=[2025, "tour"]), reduced_motion="reduce")

    page.get_by_role("link", name="Игроки").click()
    page.wait_for_url(f"**{reverse('player_list')}")

    assert page.evaluate("window.revealedWithTransition") is False
    assert page.evaluate("window.cspViolations") == []


@pytest.mark.django_db(transaction=True)
def test_board_buttons_fade_but_polling_does_not(open_page, admin_user, client, settings):
    settings.LIVE_POLL_SECONDS = 1
    game = live_game(SeasonKind.TOUR)
    seat(game, "Альфа", "Браво")
    client.force_login(admin_user)
    page = open_page(
        reverse("live:board", args=[game.pk]), session=client.cookies["sessionid"].value
    )

    page.wait_for_timeout(2500)  # at least one poll
    assert page.evaluate("window.transitions") == 0

    page.get_by_label("Ребай: Альфа").click()
    expect(page.locator(".toast")).to_contain_text("Ребай: Альфа")

    assert page.evaluate("window.transitions") == 1
    assert page.evaluate("window.cspViolations") == []
    assert page.evaluate("document.querySelectorAll('style').length") == 0
