"""The sheet sync page in Chromium: the whole flow runs under ADMIN_CSP without a violation.

The live server runs in this process, so the patched ``fetch_sheet`` answers its requests too.
"""

import pytest
from django.urls import reverse

from club.models import Result
from conftest import COLLECT_CSP_VIOLATIONS
from importer import sources
from importer.aliases import build_alias_map, upsert_aliases
from importer.tests.sheet_builder import build_workbook, fixture_aliases

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def page(browser, live_server, admin_user, client, settings, monkeypatch, tmp_path):
    settings.GOOGLE_SHEET_ID = "fake-sheet-id-0123456789abcdefghij"
    content = build_workbook(tmp_path / "club.xlsx").read_bytes()
    monkeypatch.setattr(sources, "fetch_sheet", lambda: content)
    upsert_aliases(build_alias_map(fixture_aliases()))

    client.force_login(admin_user)
    context = browser.new_context()
    context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    context.add_init_script(COLLECT_CSP_VIOLATIONS)
    page = context.new_page()
    page.goto(live_server.url + reverse("admin:importer_sheetsnapshot_sync"))
    yield page
    context.close()


def test_fetch_preview_apply_without_csp_violations(page):
    assert page.evaluate("window.cspViolations") == []

    page.get_by_role("button", name="Подтянуть из таблицы").click()
    page.get_by_role("button", name="Пересчитать").wait_for()
    assert page.get_by_role("checkbox").count() == 3
    assert page.evaluate("window.cspViolations") == []
    assert not Result.objects.exists()

    page.get_by_role("button", name="Применить").first.click()
    page.get_by_role("heading", name="Применено").wait_for()

    assert Result.objects.count() == 22
    assert page.evaluate("window.cspViolations") == []


def test_one_date_without_csp_violations(page):
    page.get_by_role("button", name="Подтянуть из таблицы").click()
    page.get_by_text("Все даты выбранных листов.").wait_for()

    page.get_by_text("Даты: все").click()
    page.get_by_role("checkbox", name="01.02.2026").check()
    page.get_by_role("button", name="Пересчитать").click()
    page.get_by_text("Частичный импорт: только 01.02.2026.").wait_for()
    page.get_by_role("button", name="Применить").first.click()
    page.get_by_role("heading", name="Применено").wait_for()

    assert Result.objects.count() == 3
    assert page.evaluate("window.cspViolations") == []


def fetch_report(page):
    page.get_by_role("button", name="Подтянуть из таблицы").click()
    page.get_by_role("button", name="Пересчитать").wait_for()


@pytest.mark.parametrize("width", [390, 1280])
def test_page_never_scrolls_sideways(page, width):
    page.set_viewport_size({"width": width, "height": 800})
    fetch_report(page)
    page.get_by_text("Даты: все").click()  # the date list open too
    page.get_by_role("checkbox", name="01.02.2026").wait_for()

    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0


def test_checkboxes_in_ink(page):
    fetch_report(page)

    accent, ink = page.evaluate(
        """() => [
            getComputedStyle(document.querySelector('input[name="sheets"]')).accentColor,
            getComputedStyle(document.documentElement).getPropertyValue("--ink").trim(),
        ]"""
    )
    red, green, blue = (int(ink[i : i + 2], 16) for i in (1, 3, 5))
    assert accent == f"rgb({red}, {green}, {blue})"


def test_apply_in_the_first_viewport(page):
    page.set_viewport_size({"width": 1280, "height": 800})
    fetch_report(page)

    box = page.get_by_role("button", name="Применить").first.bounding_box()
    assert box["y"] + box["height"] <= 800
