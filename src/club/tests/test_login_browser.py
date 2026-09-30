"""The organizers' login in a real browser on a phone: the footer link, the form, the logout
button, touch targets and the CSP."""

import pytest
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.urls import reverse

from club.models import SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season
from conftest import COLLECT_CSP_VIOLATIONS
from live.access import ORGANIZER_GROUP
from live.tests.test_browser import MIN_TARGET, PHONE, assert_clean, assert_touch_targets

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.django_db(transaction=True)

PASSWORD = "a-long-test-password-for-the-login"


@pytest.fixture
def organizer():
    make_result(
        make_game(make_season(2026, SeasonKind.TOUR), day=7, month=6),
        make_player("Альфа"),
        payout=50,
        place=1,
    )
    user = User.objects.create_user("test-organizer", password=PASSWORD)
    # Transactional tests flush the group the data migration made.
    user.groups.add(Group.objects.get_or_create(name=ORGANIZER_GROUP)[0])
    return user


@pytest.fixture
def anonymous_phone(browser, live_server):
    context = browser.new_context(**PHONE)
    context.add_init_script(COLLECT_CSP_VIOLATIONS)
    page = context.new_page()

    def open_page(path):
        page.goto(live_server.url + path)
        page.evaluate("document.fonts.ready")
        return page

    yield open_page
    context.close()


def settle(page):
    """After a navigation: the DOM is queryable before its stylesheet applies, so wait for both."""
    page.wait_for_load_state("load")
    page.evaluate("document.fonts.ready")


def assert_target(locator):
    box = locator.bounding_box()
    assert box["width"] >= MIN_TARGET and box["height"] >= MIN_TARGET, box


def test_login_and_logout(anonymous_phone, organizer):
    page = anonymous_phone(reverse("player_list"))
    assert_target(page.locator("footer").get_by_role("link", name="Войти"))

    page.locator("footer").get_by_role("link", name="Войти").click()
    page.wait_for_url("**/prokhodnaya/?next=/players/")
    settle(page)
    expect(page.get_by_role("heading", level=1)).to_have_text("Проходная")
    assert f"/{settings.ADMIN_URL}" not in page.content()
    assert_touch_targets(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["viewport"]["width"]
    assert_clean(page)

    page.get_by_label("Имя пользователя").fill("test-organizer")
    page.get_by_label("Пароль").fill("wrong")
    page.get_by_role("button", name="Войти").click()
    expect(page.get_by_role("alert")).to_contain_text("Вход только для организаторов")
    settle(page)
    assert_touch_targets(page)
    assert_clean(page)

    page.get_by_label("Пароль").fill(PASSWORD)
    page.get_by_role("button", name="Войти").click()
    page.wait_for_url("**/players/")
    settle(page)
    logout = page.locator(".site-nav").get_by_role("button", name="Выйти")
    expect(logout).to_be_visible()
    expect(page.get_by_role("link", name="Живая игра")).to_be_visible()
    expect(page.locator("footer").get_by_role("link", name="Войти")).to_have_count(0)
    assert_target(logout)
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["viewport"]["width"]
    assert_clean(page)

    logout.click()
    expect(page.locator("footer").get_by_role("link", name="Войти")).to_be_visible()
    assert page.url.endswith(reverse("player_list"))
    expect(page.get_by_role("link", name="Живая игра")).to_have_count(0)
    assert_clean(page)
