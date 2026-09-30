"""The public site as an organizer sees it, in a real browser on a phone-sized screen."""

import pytest
from django.urls import reverse

from club.models import SeasonKind
from live.tests.conftest import live_game, seat
from live.tests.test_browser import (  # noqa: F401 - phone is a fixture
    PHONE,
    assert_clean,
    assert_touch_targets,
    phone,
)

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.django_db(transaction=True)


def test_header_links_and_live_strip(phone):  # noqa: F811
    game = live_game(SeasonKind.TOUR)
    seat(game, "Альфа", "Браво")
    page = phone(reverse("all_time"))

    expect(page.get_by_role("link", name="Живая игра")).to_be_visible()
    expect(page.get_by_role("link", name="Админка")).to_be_visible()
    strip = page.locator(".live-strip")
    expect(strip).to_contain_text("Идёт турнир № 1")
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["viewport"]["width"]
    header_and_strip = page.locator("header a, .live-strip")
    for index in range(header_and_strip.count()):
        box = header_and_strip.nth(index).bounding_box()
        assert box["height"] >= 44, header_and_strip.nth(index).inner_text()
    manifest = page.evaluate(
        "fetch(document.querySelector('link[rel=manifest]').href).then(r => r.json())"
    )
    assert manifest["start_url"] == reverse("live:index")
    assert_clean(page)

    strip.click()
    page.wait_for_url(f"**{reverse('live:board', args=[game.pk])}")
    assert_touch_targets(page)
