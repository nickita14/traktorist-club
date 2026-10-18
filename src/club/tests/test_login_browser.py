"""The organizers' login in a real browser: the header link ("Проходная") on phones and wider
screens, the form, the logout button, touch targets and the CSP."""

from pathlib import Path

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


def open_anonymous(browser, live_server, width, path=None, **options):
    context = browser.new_context(viewport={"width": width, "height": 900}, **options)
    context.add_init_script(COLLECT_CSP_VIOLATIONS)
    page = context.new_page()
    page.goto(live_server.url + (path or reverse("player_list")))
    page.evaluate("document.fonts.ready")
    return context, page


# Header screenshots for review, beside the failure artifacts (test-results/ is gitignored).
SCREENSHOTS = Path(settings.BASE_DIR) / "test-results" / "header"

# Line boxes of an element's text: one entry per line.
TEXT_LINES = """el => {
  const range = document.createRange();
  range.selectNodeContents(el);
  return new Set([...range.getClientRects()].map(rect => Math.round(rect.top))).size;
}"""

RULE = """el => {
  const rule = getComputedStyle(el, '::before');
  return {content: rule.content, height: rule.height, color: rule.backgroundColor,
          token: getComputedStyle(document.documentElement).getPropertyValue('--rule').trim()};
}"""


def rgb(hex_color):
    value = hex_color.lstrip("#")
    return "rgb({}, {}, {})".format(*(int(value[i : i + 2], 16) for i in (0, 2, 4)))


def header_parts(page):
    header = page.locator("header")
    return (
        header.get_by_role("link", name="Кочующий Клуб Тракториста"),
        page.locator(".site-nav"),
        header.get_by_role("link", name="Проходная"),
    )


def assert_header_fits(page, width):
    name, _, link = header_parts(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert name.evaluate(TEXT_LINES) == 1
    box = link.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert_target(link)


def settle(page):
    """After a navigation: the DOM is queryable before its stylesheet applies, so wait for both."""
    page.wait_for_load_state("load")
    page.evaluate("document.fonts.ready")


def assert_target(locator):
    box = locator.bounding_box()
    assert box["width"] >= MIN_TARGET and box["height"] >= MIN_TARGET, box


def test_login_and_logout(anonymous_phone, organizer):
    page = anonymous_phone(reverse("player_list"))
    expect(page.locator("footer").get_by_role("link")).to_have_count(0)
    pass_link = page.locator("header").get_by_role("link", name="Проходная")
    assert_target(pass_link)

    pass_link.click()
    page.wait_for_url("**/prokhodnaya/?next=/players/")
    settle(page)
    expect(page.get_by_role("heading", level=1)).to_have_text("Проходная")
    expect(page.get_by_role("link", name="Проходная")).to_have_count(0)
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
    expect(page.get_by_role("link", name="Проходная")).to_have_count(0)
    expect(page.locator("footer").get_by_role("link")).to_have_count(0)
    # No pass beside it: organizers keep the full-size club name.
    assert header_parts(page)[0].evaluate("el => getComputedStyle(el).fontSize") == "19px"
    assert_target(logout)
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["viewport"]["width"]
    assert_clean(page)

    logout.click()
    expect(page.locator("header").get_by_role("link", name="Проходная")).to_be_visible()
    assert page.url.endswith(reverse("player_list"))
    expect(page.get_by_role("link", name="Живая игра")).to_have_count(0)
    assert_clean(page)


def test_header_line_on_a_phone(anonymous_phone, organizer):
    page = anonymous_phone(reverse("player_list"))
    settle(page)
    assert_header_fits(page, 390)
    name, nav, link = header_parts(page)
    assert name.evaluate("el => getComputedStyle(el).fontSize") == "15px"
    name_box, nav_box, link_box = name.bounding_box(), nav.bounding_box(), link.bounding_box()
    # One line: the name on the left, the pass at the right gutter, the nav row below.
    assert (
        abs((name_box["y"] + name_box["height"] / 2) - (link_box["y"] + link_box["height"] / 2)) < 2
    )
    assert abs(link_box["x"] + link_box["width"] - (390 - 16)) < 1
    assert nav_box["y"] >= link_box["y"] + link_box["height"] - 1
    assert link.evaluate(RULE)["content"] == "none"
    assert_clean(page)
    page.screenshot(path=SCREENSHOTS / "header-390.png")


def test_header_at_360(browser, live_server, organizer):
    context, page = open_anonymous(browser, live_server, 360, is_mobile=True, has_touch=True)
    settle(page)
    assert_header_fits(page, 360)
    name, nav, link = header_parts(page)
    name_box, nav_box, link_box = name.bounding_box(), nav.bounding_box(), link.bounding_box()
    # The accepted fallback: the pass on a line of its own, at the right gutter, no rule.
    assert link_box["y"] >= name_box["y"] + name_box["height"] - 1
    assert nav_box["y"] >= link_box["y"] + link_box["height"] - 1
    assert abs(link_box["x"] + link_box["width"] - (360 - 16)) < 1
    assert link.evaluate(RULE)["content"] == "none"
    assert_clean(page)
    page.screenshot(path=SCREENSHOTS / "header-360.png")
    context.close()


@pytest.mark.parametrize("width", [768, 1024, 1280])
def test_header_link_after_the_sections(browser, live_server, organizer, width):
    context, page = open_anonymous(browser, live_server, width)
    settle(page)
    assert_header_fits(page, width)
    name, nav, link = header_parts(page)
    items = [
        box
        for box in nav.locator("li > a, li > span").evaluate_all(
            "els => els.map(el => el.getBoundingClientRect().toJSON())"
        )
    ]
    link_box, name_box = link.bounding_box(), name.bounding_box()
    # The sections and the pass on one row, the pass last.
    assert {round(box["y"]) for box in items} == {round(link_box["y"])}
    assert link_box["x"] >= max(box["right"] for box in items)
    rule = link.evaluate(RULE)
    assert rule["height"] == "22px" and rule["color"] == rgb(rule["token"])
    if width >= 1280:
        assert (
            abs(name_box["y"] + name_box["height"] / 2 - (link_box["y"] + link_box["height"] / 2))
            < 2
        )
    else:
        assert link_box["y"] >= name_box["y"] + name_box["height"] - 1
    assert_clean(page)
    page.screenshot(path=SCREENSHOTS / f"header-{width}.png")
    context.close()
