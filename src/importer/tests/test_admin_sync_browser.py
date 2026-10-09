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

    page.get_by_role("button", name="Применить").click()
    page.get_by_role("heading", name="Применено").wait_for()

    assert Result.objects.count() == 22
    assert page.evaluate("window.cspViolations") == []


def test_page_never_scrolls_sideways_on_a_phone(page):
    page.set_viewport_size({"width": 390, "height": 800})
    page.get_by_role("button", name="Подтянуть из таблицы").click()
    page.get_by_role("button", name="Пересчитать").wait_for()

    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0
