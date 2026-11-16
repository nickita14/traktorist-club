"""The year report in Chromium at phone width: no sideways scroll, touch targets, CSP, the roster
in two columns, and motion: entrances once, nothing moves under reduced motion."""

import datetime
import os
import zoneinfo

import pytest
from django.urls import reverse
from django.utils import timezone

from club.tests.conftest import REPORT_YEAR as YEAR
from conftest import COLLECT_CSP_VIOLATIONS
from live.tests.test_browser import assert_touch_targets

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

PHONE = 390
# Screenshots for a look by eye: YEAR_REPORT_SHOTS=<dir> uv run pytest -n 0 <this file>.
SHOTS = os.environ.get("YEAR_REPORT_SHOTS")


@pytest.fixture(autouse=True)
def closed(monkeypatch):
    """The 1 January fallback: the report is public. The live server runs in this process."""
    moment = datetime.datetime(YEAR + 1, 1, 2, 12, tzinfo=zoneinfo.ZoneInfo("Europe/Chisinau"))
    monkeypatch.setattr(timezone, "now", lambda: moment)


@pytest.fixture
def open_page(browser, live_server):
    contexts = []

    def open_at(path, *, reduced_motion="no-preference"):
        context = browser.new_context(
            viewport={"width": PHONE, "height": 844}, reduced_motion=reduced_motion
        )
        contexts.append(context)
        context.add_init_script(COLLECT_CSP_VIOLATIONS)
        page = context.new_page()
        page.goto(live_server.url + path)
        page.evaluate("document.fonts.ready")
        return page

    yield open_at
    for context in contexts:
        context.close()


def pages(report_year):
    return {
        "club": reverse("year_report", args=[YEAR]),
        "player": reverse("year_report_player", args=[YEAR, report_year["players"]["A"].slug]),
    }


def test_phone_layout(open_page, report_year):
    for name, path in pages(report_year).items():
        page = open_page(path)
        assert page.evaluate("document.documentElement.scrollWidth") <= PHONE, name
        assert_touch_targets(page)
        assert page.evaluate("window.cspViolations") == [], name
        if SHOTS:  # every sheet at rest: unseen sheets keep their plaques hidden otherwise
            still = open_page(path, reduced_motion="reduce")
            sheets = still.locator(".poster-sheet")
            for index in range(sheets.count()):
                sheets.nth(index).screenshot(path=f"{SHOTS}/{name}-{index + 1}.png")


def test_roster_two_columns_of_44px_rows(open_page, report_year):
    page = open_page(pages(report_year)["club"])
    links = page.locator(".poster-roster a")
    assert links.count() == 5
    lefts = {round(links.nth(i).bounding_box()["x"]) for i in range(links.count())}
    assert len(lefts) == 2
    for index in range(links.count()):
        assert links.nth(index).bounding_box()["height"] >= 44


def test_entrance_plays_once_when_a_sheet_is_seen(open_page, report_year):
    page = open_page(pages(report_year)["club"])
    assert page.evaluate("document.documentElement.classList.contains('poster-motion')")
    stamp = page.locator(".poster-stamp")
    stamp.scroll_into_view_if_needed()
    expect(page.locator(".poster-sheet:has(.poster-stamp)")).to_have_class(
        "poster-sheet poster-sheet-ink poster-sheet-center is-seen"
    )
    # wait_for_function evaluates a string, which the strict CSP refuses: poll instead.
    finished = "el => el.getAnimations().every(a => a.playState === 'finished')"
    for _ in range(40):
        if stamp.evaluate(finished):
            break
        page.wait_for_timeout(50)
    assert stamp.evaluate(finished)
    assert stamp.evaluate("el => getComputedStyle(el).opacity") == "1"


def test_reduced_motion_moves_nothing(open_page, report_year):
    page = open_page(pages(report_year)["club"], reduced_motion="reduce")
    assert not page.evaluate("document.documentElement.classList.contains('poster-motion')")
    assert page.evaluate("document.getAnimations().length") == 0
    stamp = page.locator(".poster-stamp")
    stamp.scroll_into_view_if_needed()
    assert stamp.evaluate("el => getComputedStyle(el).opacity") == "1"
    assert page.evaluate("document.getAnimations().length") == 0
