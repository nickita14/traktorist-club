"""The toast on a phone: "×", a sideways swipe and Escape remove it; a drag that starts on it
never clicks "отменить" or what lies under it, and leaves later taps alone; once gone (or faded
out) it no longer catches taps. Every page is checked for CSP violations."""

import pytest
from django.urls import reverse

from club.models import SeasonKind
from live.tests.conftest import live_game, seat
from live.tests.test_browser import assert_clean, assert_touch_targets, buyin, phone  # noqa: F401
from live.tests.test_browser_timer import NO_SHARE, blinds_page, timed_game

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.django_db(transaction=True)

NAMES = ("Альфа", "Браво", "Чарли", "Дельта", "Эхо", "Фокстрот", "Гольф", "Хотел", "Индия")

# Pointer cancels and clicks, as the page saw them (capture phase, before live.js).
RECORD = """
window.pointerCancels = 0;
addEventListener("pointercancel", () => { window.pointerCancels += 1; }, true);
"""

# Scroll the first rebuy button below height ``y`` up to it; return its label and centre.
SCROLL_TO = """(y) => {
  const button = [...document.querySelectorAll('[aria-label^="Ребай: "]')]
    .find(el => el.getBoundingClientRect().top > y);
  const box = button.getBoundingClientRect();
  window.scrollBy(0, box.top + box.height / 2 - y);
  const moved = button.getBoundingClientRect();
  const label = button.getAttribute("aria-label");
  return [label, moved.left + moved.width / 2, moved.top + moved.height / 2];
}"""

# The aria-label of the button at a point, or of the toast when the point is on it.
HIT = """([x, y]) => {
  const el = document.elementFromPoint(x, y);
  if (!el) return null;
  if (el.closest(".toast")) return "toast";
  return el.closest("button")?.getAttribute("aria-label") ?? el.tagName;
}"""


def board(phone, players=NAMES[:3]):  # noqa: F811
    game = live_game(SeasonKind.TOUR)
    results = seat(game, *players)
    page = phone(reverse("live:board", args=[game.pk]))
    page.add_init_script(RECORD)
    page.reload()
    page.evaluate("document.fonts.ready")
    return page, results


# True once the toast is what a tap at its centre reaches: the board swap's view transition
# covers the page until it ends (Playwright's clicks wait for that, raw pointer moves do not).
TOAST_ON_TOP = """() => {
  const box = document.querySelector(".toast")?.getBoundingClientRect();
  return Boolean(box) && Boolean(
    document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.closest(".toast")
  );
}"""


def rebuy(page, label="Альфа"):
    page.get_by_label(f"Ребай: {label}").click()
    expect(page.locator(".toast")).to_contain_text(f"Ребай: {label} +50")
    page.wait_for_function(TOAST_ON_TOP)


def centre(locator):
    box = locator.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def mouse_drag(page, start, dx, dy, steps=10):
    x, y = start
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=steps)
    page.mouse.up()


def touch_drag(page, start, dx, dy, steps=10):
    """A finger drag through the DevTools protocol: touch events, so the browser may pan."""
    cdp = page.context.new_cdp_session(page)
    x, y = start
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    for step in range(1, steps + 1):
        point = {"x": x + dx * step / steps, "y": y + dy * step / steps}
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [point]})
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()


def under_toast(page):
    """Scroll a rebuy button under the toast; return its label and a point on both."""
    _, toast_y = centre(page.locator(".toast"))
    label, x, y = page.evaluate(SCROLL_TO, toast_y)
    assert page.evaluate(HIT, [x, y]) == "toast"
    return label, (x, y)


def result_of(results, label):
    return next(r for r in results if label == f"Ребай: {r.player.label}")


def test_close_button_removes_the_toast(phone):  # noqa: F811
    page, (alpha, *_) = board(phone)
    rebuy(page)

    close = page.locator(".toast").get_by_role("button", name="Закрыть")
    box = close.bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    assert_touch_targets(page)
    close.click()

    expect(page.locator(".toast")).to_have_count(0)
    assert buyin(alpha) == 100  # not undone
    # The next toast from the server appears as before.
    rebuy(page)
    assert buyin(alpha) == 150
    assert_clean(page)


def test_swipe_removes_the_toast(phone):  # noqa: F811
    page, (alpha, *_) = board(phone)
    rebuy(page)

    mouse_drag(page, centre(page.locator(".toast-text")), -200, 0)

    expect(page.locator(".toast")).to_have_count(0)
    assert buyin(alpha) == 100
    # A finger swipe the other way, starting on "отменить": removed, not undone.
    rebuy(page)
    touch_drag(page, centre(page.locator(".toast").get_by_text("отменить")), 200, 0)
    expect(page.locator(".toast")).to_have_count(0)
    assert buyin(alpha) == 150
    assert_clean(page)


def test_swipe_under_reduced_motion_removes_at_once(phone):  # noqa: F811
    page, _ = board(phone)
    page.emulate_media(reduced_motion="reduce")
    rebuy(page)

    mouse_drag(page, centre(page.locator(".toast-text")), 200, 0)

    assert page.locator(".toast").count() == 0  # no slide to wait for
    assert_clean(page)


def test_undo_still_works(phone):  # noqa: F811
    page, (alpha, *_) = board(phone)
    rebuy(page)

    page.locator(".toast").get_by_text("отменить").click()

    expect(page.locator(".toast")).to_contain_text("Отменено: Ребай: Альфа +50")
    assert buyin(alpha) == 50
    assert_clean(page)


def test_short_drag_keeps_the_toast_and_clicks_nothing(phone):  # noqa: F811
    page, (alpha, bravo, _) = board(phone)
    rebuy(page)

    # 30px sideways from "отменить": under the swipe threshold, past the tap slop.
    mouse_drag(page, centre(page.locator(".toast").get_by_text("отменить")), 30, 0)
    page.wait_for_timeout(300)

    expect(page.locator(".toast")).to_contain_text("Ребай: Альфа +50")
    expect(page.locator(".toast")).not_to_contain_text("Отменено")
    assert buyin(alpha) == 100
    # A vertical drag does not dismiss either, nor click "отменить" when it starts and ends on
    # it (no pointer capture for a vertical move: the click would land on the button).
    mouse_drag(page, centre(page.locator(".toast-text")), 0, -60)
    mouse_drag(page, centre(page.locator(".toast").get_by_text("отменить")), 0, 15)
    page.wait_for_timeout(300)
    expect(page.locator(".toast")).to_contain_text("Ребай: Альфа +50")
    expect(page.locator(".toast")).not_to_contain_text("Отменено")
    assert buyin(alpha) == 100

    # The drag does not swallow the next tap: a row button, then "отменить", both work.
    page.get_by_label("Ребай: Браво").click()
    expect(page.locator(".toast")).to_contain_text("Ребай: Браво +50")
    assert buyin(bravo) == 100
    page.locator(".toast").get_by_text("отменить").click()
    expect(page.locator(".toast")).to_contain_text("Отменено: Ребай: Браво +50")
    assert buyin(bravo) == 50
    assert_clean(page)


def test_vertical_finger_drag_is_cancelled_and_the_next_tap_works(phone):  # noqa: F811
    page, results = board(phone, NAMES)
    rebuy(page)

    # The page scrolls (touch-action: pan-y), so the browser cancels the pointer: no click.
    touch_drag(page, centre(page.locator(".toast-text")), 0, -150)

    page.wait_for_function("window.pointerCancels > 0")
    expect(page.locator(".toast")).to_contain_text("Ребай: Альфа +50")
    assert buyin(results[0]) == 100
    # The next tap on a row button goes through.
    button = page.get_by_label("Ребай: Браво")
    button.scroll_into_view_if_needed()
    page.touchscreen.tap(*centre(button))
    expect(page.locator(".toast")).to_contain_text("Ребай: Браво +50")
    assert buyin(results[1]) == 100
    assert_clean(page)


def test_dismissed_toast_frees_the_button_under_it(phone):  # noqa: F811
    page, results = board(phone, NAMES)
    rebuy(page)
    label, (x, y) = under_toast(page)

    page.locator(".toast").get_by_role("button", name="Закрыть").click()

    assert page.evaluate(HIT, [x, y]) == label
    page.mouse.click(x, y)
    expect(page.locator(".toast")).to_contain_text(f"{label} +50")
    assert buyin(result_of(results, label)) == 100
    assert_clean(page)


def test_faded_toast_catches_no_taps(phone):  # noqa: F811
    page, results = board(phone, NAMES)
    rebuy(page)
    label, (x, y) = under_toast(page)

    # At the end of its fade, before live.js removes it, CSS alone lets taps through.
    faded = page.evaluate(
        """([x, y]) => {
          const box = document.querySelector(".toast");
          for (const animation of box.getAnimations()) animation.currentTime = 8000;
          return [getComputedStyle(box).visibility, box.contains(document.elementFromPoint(x, y))];
        }""",
        [x, y],
    )
    assert faded == ["hidden", False]
    # Then the element itself goes.
    expect(page.locator(".toast")).to_have_count(0)
    page.mouse.click(x, y)
    expect(page.locator(".toast")).to_contain_text(f"{label} +50")
    assert buyin(result_of(results, label)) == 100
    assert_clean(page)


def test_escape_closes_the_toast_only_from_inside(phone):  # noqa: F811
    page, _ = board(phone)
    rebuy(page)

    # Focus in the bust sheet: Escape closes the sheet, the toast stays.
    page.get_by_label("Выбыл: Браво").click()
    sheet = page.locator(".sheet-panel")
    expect(sheet.get_by_role("button", name="Отмена")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator(".sheet-panel")).to_have_count(0)
    expect(page.locator(".toast")).to_contain_text("Ребай: Альфа +50")

    # Focus in the toast: Escape removes it.
    page.locator(".toast").get_by_text("отменить").focus()
    page.keyboard.press("Escape")
    expect(page.locator(".toast")).to_have_count(0)
    assert_clean(page)


def test_share_toast_closes(phone):  # noqa: F811
    game = timed_game()
    page = blinds_page(phone, game, NO_SHARE)
    origin = page.url.split("/", 3)[:3]
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin="/".join(origin))

    page.get_by_role("button", name="Поделиться табло").click()
    expect(page.locator(".toast-text")).to_have_text("Ссылка на табло скопирована.")
    page.locator(".toast").get_by_role("button", name="Закрыть").focus()
    page.keyboard.press("Escape")
    expect(page.locator(".toast")).to_have_count(0)

    # The next one appears as before, and "×" removes it.
    page.get_by_role("button", name="Поделиться табло").click()
    expect(page.locator(".toast-text")).to_have_text("Ссылка на табло скопирована.")
    page.locator(".toast").get_by_role("button", name="Закрыть").click()
    expect(page.locator(".toast")).to_have_count(0)
    assert_clean(page)
