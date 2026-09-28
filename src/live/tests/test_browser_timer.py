"""The blind timer in a real browser: the display counting on its own, offline and back, edits
from the phone reaching it, its controls, and the phone screens. Every page is checked for CSP
violations."""

import datetime
import json
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from club.models import SeasonKind
from club.tests.factories import make_structure
from conftest import COLLECT_CSP_VIOLATIONS
from live import actions
from live.models import BlindTimer
from live.tests.conftest import key, live_game, seat
from live.tests.test_browser import assert_clean, assert_touch_targets, phone  # noqa: F401

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]

LAPTOP = {"viewport": {"width": 1280, "height": 800}}
PHONE_HEIGHT = 844
CASES = json.loads((Path(__file__).parent / "clock_cases.json").read_text())

# Short levels, so the clock changes level within a test: 1-2 one minute, a break, level 3.
SHORT = [(25, 50, 1), (50, 100, 1, 10), ("Перерыв · аддон", 1, "addon"), (100, 200, 1)]


@pytest.fixture
def fast_sync(settings):
    settings.TIMER_SYNC_SECONDS = 1


@pytest.fixture
def laptop(browser, live_server, admin_user, client):
    """Open a page on a laptop-sized screen, signed in as a superuser or not at all."""
    contexts = []

    def open_page(path, *, signed_in=True):
        context = browser.new_context(**LAPTOP)
        contexts.append(context)
        if signed_in:
            client.force_login(admin_user)
            session = client.cookies["sessionid"].value
            context.add_cookies([{"name": "sessionid", "value": session, "url": live_server.url}])
        context.add_init_script(COLLECT_CSP_VIOLATIONS)
        page = context.new_page()
        page.goto(live_server.url + path)
        page.evaluate("document.fonts.ready")
        return page

    yield open_page
    for context in contexts:
        context.close()


def timed_game(rows=SHORT):
    game = live_game(SeasonKind.TOUR)
    seat(game, "Альфа", "Браво")
    actions.start_timer(game.pk, key(), None, make_structure(rows=rows).pk)
    return game


def running(game, *, left_seconds, position=1):
    """Run the clock at ``position`` with ``left_seconds`` to go (levels are 1 minute)."""
    started = timezone.now() - datetime.timedelta(seconds=60 - left_seconds)
    BlindTimer.objects.filter(game=game).update(
        position=position, started_at=started, paused_at=None
    )


def public_url(game):
    return reverse("tablo_public", args=[BlindTimer.objects.get(game=game).display_token])


def test_js_clock_matches_the_python_cases(laptop):
    game = timed_game()
    page = laptop(public_url(game), signed_in=False)

    results = page.evaluate(
        """({rows, cases}) => cases.map(({position, started, paused, expect}) => {
            const now = 1_790_000_000_000;
            const levels = rows.map(([kind, minutes], i) => ({
              position: i + 1, minutes, label: kind === "B" ? "Перерыв" : "",
              small: kind === "L" ? 25 : null, big: kind === "L" ? 50 : null,
              ante: 0, addon: i === 3,
            }));
            const at = (s) => s === null ? null : now + s * 1000;
            const c = BlindClock.clockAt(levels, position, at(started), at(paused), now);
            return {position: c.current.position, remaining: c.remaining / 1000,
                    finished: c.finished,
                    until_break: c.untilBreak === null ? null : c.untilBreak / 1000};
        })""",
        CASES,
    )

    assert results == [case["expect"] for case in CASES["cases"]]
    assert page.evaluate("BlindClock.format(877000)") == "14:37"
    assert page.evaluate("BlindClock.money(12500)") == "12 500"


def test_keeps_counting_offline_and_resyncs(laptop, fast_sync):
    game = timed_game()
    running(game, left_seconds=3)
    page = laptop(public_url(game), signed_in=False)
    expect(page.locator("[data-level-line]")).to_contain_text("Уровень 1")

    page.context.set_offline(True)

    # The next level comes from the local clock, with the blinds it already had.
    expect(page.locator("[data-level-line]")).to_have_text("Уровень 2 · из 3 · 1 мин", timeout=6000)
    expect(page.locator("[data-blinds-now]")).to_have_text("50 / 100")
    expect(page.locator("[data-offline]")).to_be_visible()
    expect(page.locator("[data-clock]")).not_to_have_text("00:00")

    # Meanwhile the organizer pauses on the phone; the display learns it once back online.
    actions.set_paused(game.pk, key(), None, True)
    page.context.set_offline(False)

    expect(page.locator("[data-offline]")).to_be_hidden(timeout=5000)
    expect(page.locator("[data-paused-stamp]")).to_be_visible()
    frozen = page.locator("[data-clock]").text_content()
    page.wait_for_timeout(1500)
    assert page.locator("[data-clock]").text_content() == frozen
    assert_clean(page)


def test_phone_edits_reach_the_display(laptop, fast_sync):
    game = timed_game()
    running(game, left_seconds=50)
    page = laptop(public_url(game), signed_in=False)
    expect(page.locator("[data-until-break]")).to_have_text("01:50")
    expect(page.locator("[data-blinds-next]")).to_have_text("50 / 100")
    expect(page.locator("[data-ante-next]")).to_contain_text("10")

    actions.edit_level(game.pk, key(), None, 2, minutes=5, small=75, big=150, ante=0)
    actions.add_level(game.pk, key(), None, small=200, big=400, ante=0, minutes=1)

    expect(page.locator("[data-until-break]")).to_contain_text("05:", timeout=4000)
    expect(page.locator("[data-blinds-next]")).to_have_text("75 / 150")
    expect(page.locator("[data-ante-next]")).to_be_hidden()
    expect(page.locator("[data-level-line]")).to_contain_text("из 4")
    expect(page.locator("[data-until-break-note]")).to_have_text("после 2-го уровня · аддон")
    assert_clean(page)


def test_break_and_no_break_left(laptop, fast_sync):
    game = timed_game()
    running(game, left_seconds=30, position=3)
    page = laptop(public_url(game), signed_in=False)

    expect(page.locator("[data-break]")).to_be_visible()
    expect(page.locator("[data-blinds]")).to_be_hidden()
    expect(page.locator("[data-break-label]")).to_have_text("Перерыв · аддон")
    expect(page.locator("[data-break-next-value]")).to_have_text("100 / 200")

    running(game, left_seconds=30, position=4)
    expect(page.locator("[data-until-break-block]")).to_be_hidden(timeout=4000)
    expect(page.locator("[data-blinds-now]")).to_have_text("100 / 200")

    running(game, left_seconds=1, position=4)
    expect(page.locator("[data-finished]")).to_be_visible(timeout=5000)
    expect(page.locator("[data-clock]")).to_have_text("00:00")
    assert_clean(page)


def test_revoked_link_stops_the_display(laptop, fast_sync):
    game = timed_game()
    page = laptop(public_url(game), signed_in=False)
    assert page.locator("[data-act]").count() == 0

    actions.reset_link(game.pk, key(), None)

    expect(page.locator("[data-level-line]")).to_contain_text("Табло отключено", timeout=4000)


def test_organizer_controls(laptop):
    game = timed_game(rows=[(25, 50, 20), (50, 100, 20), (100, 200, 20)])
    page = laptop(reverse("live:tablo", args=[game.pk]))
    toggle = page.locator('[data-act="toggle"]')
    expect(toggle).to_have_text("Продолжить")
    expect(page.locator("[data-paused-stamp]")).to_be_visible()

    toggle.click()
    expect(toggle).to_have_text("Пауза")
    expect(page.locator("[data-paused-stamp]")).to_be_hidden()
    assert BlindTimer.objects.get(game=game).paused_at is None

    page.keyboard.press("Space")
    expect(toggle).to_have_text("Продолжить")
    assert BlindTimer.objects.get(game=game).paused_at is not None

    page.get_by_role("button", name="+1 мин").click()
    expect(page.locator("[data-clock]")).to_have_text("21:00")
    page.get_by_label("Следующий уровень").click()
    expect(page.locator("[data-level-line]")).to_have_text("Уровень 2 · из 3 · 20 мин")
    page.get_by_label("Предыдущий уровень").click()
    expect(page.locator("[data-level-line]")).to_contain_text("Уровень 1")
    page.get_by_label("Предыдущий уровень").click()
    expect(page.locator("[data-message]")).to_have_text("Это первый уровень.")

    expect(page.locator("[data-sound-prompt]")).to_be_hidden()  # the first click enabled it
    clock_size = page.locator("[data-clock]").evaluate("el => getComputedStyle(el).fontSize")
    assert 200 <= float(clock_size.removesuffix("px")) <= 240
    assert page.evaluate("document.documentElement.scrollWidth") <= LAPTOP["viewport"]["width"]
    assert_clean(page)


def test_phone_strip_ticks_and_opens_the_editor(phone):  # noqa: F811
    game = timed_game(rows=[(25, 50, 20), ("Перерыв · аддон", 15, "addon"), (50, 100, 20)])
    running(game, left_seconds=1190)
    page = phone(reverse("live:board", args=[game.pk]))

    clock = page.locator("[data-timer] [data-clock]")
    first = clock.text_content()
    expect(clock).not_to_have_text(first, timeout=3000)
    assert page.locator("[data-timer]").get_by_role("button", name="Пауза").is_visible()
    assert_touch_targets(page)
    assert_clean(page)

    page.get_by_role("link", name="Структура").click()
    page.wait_for_url(f"**{reverse('live:blinds', args=[game.pk])}")
    page.get_by_label("Минут: уровень 2").fill("25")
    page.get_by_label("Минут: уровень 2").press("Tab")

    expect(page.locator(".toast")).to_contain_text("Таймер: уровень 2 · 50 / 100, 25 мин")
    assert list(BlindTimer.objects.get(game=game).levels.values_list("minutes", flat=True)) == [
        20,
        15,
        25,
    ]
    assert_touch_targets(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert_clean(page)


def test_phone_adds_a_break_with_the_addon(phone):  # noqa: F811
    game = timed_game(rows=[(25, 50, 20), (50, 100, 20)])
    page = phone(reverse("live:blinds", args=[game.pk]))
    assert_touch_targets(page)

    page.get_by_label("Название перерыва").fill("Перерыв · аддон")
    page.get_by_label("Минут перерыва").fill("15")
    page.get_by_label("перерыв на аддон").check()
    page.get_by_role("button", name="+ Перерыв").click()

    expect(page.locator(".toast")).to_contain_text("добавлен перерыв «Перерыв · аддон», 15 мин")
    expect(page.get_by_label("перерыв на аддон")).to_have_count(0)
    level = BlindTimer.objects.get(game=game).levels.get(position=3)
    assert (level.label, level.minutes, level.addon_break) == ("Перерыв · аддон", 15, True)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert_clean(page)


SHARE_STUB = """Object.defineProperty(navigator, 'share', {
  configurable: true, value: async data => { window.sharedWith = data; },
});
Object.defineProperty(navigator, 'canShare', {configurable: true, value: () => true});"""
NO_SHARE = """Object.defineProperty(navigator, 'share', {configurable: true, value: undefined});"""


def blinds_page(phone, game, init_script):  # noqa: F811
    page = phone(reverse("live:blinds", args=[game.pk]))
    page.add_init_script(init_script)
    page.reload()
    return page


def test_share_opens_the_share_sheet(phone):  # noqa: F811
    game = timed_game()
    link = public_url(game)
    page = blinds_page(phone, game, SHARE_STUB)

    page.get_by_role("button", name="Поделиться табло").click()

    shared = page.wait_for_function("window.sharedWith").json_value()
    assert shared["url"].endswith(link)
    assert shared["title"] == "Табло · Тестовая структура"
    expect(page.locator(".toast")).to_have_count(0)
    assert_clean(page)


def test_share_falls_back_to_copying(phone):  # noqa: F811
    game = timed_game()
    page = blinds_page(phone, game, NO_SHARE)
    origin = page.url.split("/", 3)[:3]
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin="/".join(origin))

    page.get_by_role("button", name="Поделиться табло").click()

    expect(page.locator(".toast")).to_have_text("Ссылка на табло скопирована.")
    assert page.evaluate("navigator.clipboard.readText()").endswith(public_url(game))
    assert_clean(page)


def test_new_link_asks_first(phone):  # noqa: F811
    game = timed_game()
    page = phone(reverse("live:blinds", args=[game.pk]))
    old = BlindTimer.objects.get(game=game).display_token
    messages = []

    page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))
    page.get_by_role("button", name="Новая ссылка").click()
    page.wait_for_timeout(300)
    assert BlindTimer.objects.get(game=game).display_token == old
    assert "Старая перестанет работать на всех открытых табло" in messages[0]

    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Новая ссылка").click()
    expect(page.locator(".toast")).to_contain_text("Табло: новая ссылка")
    new = BlindTimer.objects.get(game=game).display_token
    assert new != old
    assert page.locator("[data-share]").get_attribute("data-share").endswith(f"/tablo/{new}/")


def test_bottom_bar_goes_back_to_the_board(phone):  # noqa: F811
    game = timed_game()
    page = phone(reverse("live:blinds", args=[game.pk]))
    bar = page.locator(".bottom-bar")
    assert bar.evaluate("el => getComputedStyle(el).position") == "sticky"
    # Sticky at the bottom of the screen even before scrolling to the end.
    box = bar.bounding_box()
    assert box["y"] + box["height"] <= PHONE_HEIGHT + 1
    assert_touch_targets(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390

    bar.get_by_role("link", name="← К игре").click()
    page.wait_for_url(f"**{reverse('live:board', args=[game.pk])}")
