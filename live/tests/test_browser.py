"""The live screens in a real browser on a phone-sized screen: taps, undo, the exit sheet, polling,
a lost connection, touch target sizes and the CSP."""

import pytest
from django.urls import reverse

from club.models import Result, SeasonKind
from conftest import COLLECT_CSP_VIOLATIONS
from live import actions
from live.tests.conftest import key, live_game, seat

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
MIN_TARGET = 44

# Size of every visible control, as "label: width x height".
TARGET_SIZES = """() => [...document.querySelectorAll('button, a, input, select')]
  .filter(el => el.offsetParent !== null)
  .map(el => {
    const box = el.getBoundingClientRect();
    return {label: el.getAttribute('aria-label') || el.textContent.trim() || el.name,
            width: box.width, height: box.height};
  })"""


@pytest.fixture
def phone(browser, live_server, admin_user, client):
    """Open a live page as a signed-in superuser on a phone-sized screen."""
    client.force_login(admin_user)
    session = client.cookies["sessionid"].value
    contexts = []

    def open_page(path):
        context = browser.new_context(**PHONE)
        contexts.append(context)
        context.add_cookies([{"name": "sessionid", "value": session, "url": live_server.url}])
        context.add_init_script(COLLECT_CSP_VIOLATIONS)
        page = context.new_page()
        page.goto(live_server.url + path)
        page.evaluate("document.fonts.ready")
        return page

    yield open_page
    for context in contexts:
        context.close()


def buyin(result) -> int:
    result.refresh_from_db()
    return result.buyin


def assert_clean(page):
    assert page.evaluate("window.cspViolations") == []
    # htmx must not inject its indicator styles (they would need 'unsafe-inline').
    assert page.evaluate("document.querySelectorAll('style').length") == 0


def assert_touch_targets(page):
    small = [
        target
        for target in page.evaluate(TARGET_SIZES)
        if target["width"] < MIN_TARGET or target["height"] < MIN_TARGET
    ]
    assert small == []


def test_rebuy_toast_and_undo(phone):
    game = live_game(SeasonKind.TOUR)
    alpha, *_ = seat(game, "Альфа", "Браво", "Чарли")
    page = phone(reverse("live:board", args=[game.pk]))

    page.get_by_label("Ребай: Альфа").click()

    expect(page.locator(".toast")).to_contain_text("Ребай: Альфа +50")
    assert buyin(alpha) == 150
    expect(page.locator(".stat-strip")).to_contain_text("350")

    page.locator(".toast").get_by_text("отменить").click()

    expect(page.locator(".toast")).to_contain_text("Отменено: Ребай: Альфа +50")
    assert buyin(alpha) == 100
    assert_clean(page)


def test_board_touch_targets(phone):
    game = live_game(SeasonKind.TOUR)
    alpha, *_ = seat(game, "Альфа", "Браво", "Чарли")
    actions.eliminate(game.pk, key(), None, alpha.pk)
    page = phone(reverse("live:board", args=[game.pk]))

    assert_touch_targets(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= PHONE["viewport"]["width"]
    assert_clean(page)


def test_addon_toggle(phone):
    game = live_game(SeasonKind.TOUR)
    alpha, *_ = seat(game, "Альфа", "Браво")
    actions.advance_stage(game.pk, key(), None, "rebuys")
    page = phone(reverse("live:board", args=[game.pk]))

    page.get_by_label("Аддон: Альфа").click()

    expect(page.get_by_label("Аддон: Альфа")).to_have_attribute("aria-pressed", "true")
    assert buyin(alpha) == 150
    assert_clean(page)


def test_cash_exit_sheet(phone):
    game = live_game(SeasonKind.CASH)
    alpha, _ = seat(game, "Альфа", "Браво")
    actions.top_up(game.pk, key(), None, alpha.pk)
    page = phone(reverse("live:board", args=[game.pk]))

    page.get_by_label("Выход: Альфа").click()
    # htmx focuses the autofocus field once the sheet is processed; typing before that is lost.
    expect(page.locator("#exit-chips")).to_be_focused()
    page.locator("#exit-chips").fill("11230")
    expect(page.locator("#exit-calc")).to_contain_text("112,30")
    page.locator("#exit-calc").get_by_role("button", name="110").click()

    expect(page.locator("#exit-paid")).to_have_value("110")
    expect(page.locator("#exit-calc")).to_contain_text("2,30")
    assert_touch_targets(page)

    page.get_by_role("button", name="Записать выход").click()

    expect(page.locator(".sheet-panel")).to_have_count(0)
    expect(page.locator(".toast")).to_contain_text("Выход: Альфа, выдано 110")
    alpha.refresh_from_db()
    assert (alpha.chips_out, alpha.payout, alpha.out_order) == (11230, 110, 1)
    assert_clean(page)


def test_polling_shows_other_phones_but_not_while_typing(phone, settings):
    settings.LIVE_POLL_SECONDS = 1
    game = live_game(SeasonKind.CASH)
    alpha, _ = seat(game, "Альфа", "Браво")
    page = phone(reverse("live:board", args=[game.pk]))

    # Another phone tops up: this board catches up on its own.
    actions.top_up(game.pk, key(), None, alpha.pk)
    expect(page.locator(".stat-strip")).to_contain_text("150", timeout=5000)

    page.get_by_label("Выход: Альфа").click()
    expect(page.locator("#exit-chips")).to_be_focused()
    page.locator("#exit-chips").fill("123")
    actions.top_up(game.pk, key(), None, alpha.pk)
    page.wait_for_timeout(2500)

    expect(page.locator("#exit-chips")).to_have_value("123")
    expect(page.locator("#exit-chips")).to_be_focused()
    expect(page.locator(".stat-strip")).to_contain_text("150")  # the refresh waited
    assert_clean(page)


def test_lost_connection_shows_an_error_and_retries_once(phone):
    game = live_game(SeasonKind.TOUR)
    alpha, *_ = seat(game, "Альфа", "Браво")
    page = phone(reverse("live:board", args=[game.pk]))

    page.context.set_offline(True)
    page.get_by_label("Ребай: Альфа").click()

    expect(page.locator("#conn-error")).to_be_visible()
    expect(page.locator("#conn-error")).to_contain_text("Нет связи")
    assert buyin(alpha) == 100

    page.context.set_offline(False)
    page.get_by_role("button", name="Повторить").click()

    expect(page.locator(".toast")).to_contain_text("Ребай: Альфа +50")
    expect(page.locator("#conn-error")).to_be_hidden()
    assert buyin(alpha) == 150
    page.locator("[data-retry]").evaluate("el => el.click()")  # a stray retry
    page.wait_for_timeout(500)
    assert buyin(alpha) == 150
    assert_clean(page)


def test_seating_search_and_new_player(phone):
    game = live_game(SeasonKind.TOUR)
    seat(game, "Альфа")
    page = phone(reverse("live:seating", args=[game.pk]))

    page.locator("#search").fill("нет такого")
    expect(page.locator("#player-list")).to_contain_text("Никого не нашли")
    page.get_by_label("Имя", exact=True).fill("Дельта")
    page.get_by_label("Ник", exact=True).fill("Дед")
    page.get_by_role("button", name="Создать и посадить").click()

    expect(page.locator(".toast")).to_contain_text("За стол: Дед, 100")
    expect(page.get_by_label("Имя", exact=True)).to_have_value("")
    assert Result.objects.filter(game=game, player__name="Дельта").exists()
    assert_touch_targets(page)
    assert_clean(page)


@pytest.mark.parametrize("name", ["live:index", "live:start"])
def test_other_pages_are_clean(phone, name):
    page = phone(reverse(name))
    assert_touch_targets(page)
    assert_clean(page)


def test_results_and_close_pages_are_clean(phone):
    tour = live_game(SeasonKind.TOUR)
    alpha, bravo = seat(tour, "Альфа", "Браво")
    actions.advance_stage(tour.pk, key(), None, "rebuys")
    actions.advance_stage(tour.pk, key(), None, "addon")
    actions.eliminate(tour.pk, key(), None, bravo.pk)
    page = phone(reverse("live:results", args=[tour.pk]))

    page.get_by_label("Выплата: Альфа").fill("150")
    expect(page.locator("#balance")).to_contain_text("Не сходится")
    page.get_by_label("Выплата: Альфа").fill("200")
    expect(page.locator("#balance")).to_contain_text("Баланс сверен")
    assert_touch_targets(page)
    assert_clean(page)

    page.get_by_role("button", name="Сохранить итоги").click()
    page.wait_for_url(f"**{reverse('game_detail', args=[tour.pk])}")
    alpha.refresh_from_db()
    assert (alpha.place, alpha.payout) == (1, 200)
