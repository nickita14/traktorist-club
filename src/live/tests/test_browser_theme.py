"""Theme nights on the display (табло) in Chromium: announcements from the phone's presses, one
after another, the lower third over a visible clock, the break screen's tips, no replay after a
reload, the winner through the end of the game and gone after an undo, reduced motion, sound
only after the tap, and the admin's preview page. Every page is checked for CSP violations."""

import io
import wave

import pytest
from django.urls import reverse

from club.models import Game
from conftest import COLLECT_CSP_VIOLATIONS
from live import actions, themes
from live.models import BlindTimer, EventKind, OverlayStyle, Theme, ThemeEvent
from live.tests.conftest import key, seat
from live.tests.test_browser import assert_clean, assert_touch_targets
from live.tests.test_browser_timer import LAPTOP, fast_sync, laptop, running  # noqa: F401
from live.tests.test_browser_timer import timed_game as timer_game

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

pytestmark = pytest.mark.django_db(transaction=True)

# Counts the theme sounds that started playing (Web Audio buffer sources).
COUNT_SOUNDS = """
window.soundsStarted = 0;
const start = AudioBufferSourceNode.prototype.start;
AudioBufferSourceNode.prototype.start = function (...args) {
  window.soundsStarted += 1;
  return start.apply(this, args);
};
"""


# Six more players: the first two busts are plain ones (places 8 and 7, 3 paid).
MORE = ("Чарли", "Дельта", "Эхо", "Фокстрот", "Гольф", "Хотел")


def wav() -> bytes:
    """A tenth of a second of silence: a sound Chromium can decode."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(8000)
        file.writeframes(bytes(1600))
    return buffer.getvalue()


def tablo_url(game):
    return reverse("tablo_public", args=[BlindTimer.objects.get(game=game).display_token])


def table(*names):
    """A live tournament with a timer and these players seated (the first ones bust first)."""
    game = timer_game()
    results = list(game.results.order_by("pk"))
    return game, results + seat(game, *names)


def theme(**kinds):
    """An active theme; each keyword is a kind with its row's fields."""
    created = Theme.objects.create(name="Проверочная", is_active=True, tips="первый\nвторой")
    for kind, fields in kinds.items():
        ThemeEvent.objects.create(theme=created, kind=kind, **fields)
    return created


def bust(game, result):
    return actions.eliminate(game.pk, key(), None, result.pk).action


def overlay(page):
    return page.locator("[data-overlay]")


def test_bust_shows_the_place_in_the_club_style(laptop, fast_sync):  # noqa: F811
    game, results = table("Чарли", "Дельта", "Эхо")
    page = laptop(tablo_url(game), signed_in=False)

    bust(game, results[0])

    expect(overlay(page)).to_be_visible()
    expect(page.locator("[data-overlay-title]")).to_have_text("Выбыл")
    expect(page.locator("[data-overlay-caption]")).to_have_text("Альфа · 5 место")
    expect(page.locator("[data-overlay-note]")).to_have_text("в игре 4 из 5")
    assert "tablo-overlay-stamp" in page.locator("body").get_attribute("class")
    # The place is a figure in PT Mono.
    expect(page.locator("[data-overlay-caption] .tablo-overlay-num")).to_have_text("5")
    title = page.locator("[data-overlay-title]")
    assert title.evaluate("el => getComputedStyle(el).animationName") == "tablo-stamp-in"
    expect(overlay(page)).to_be_hidden(timeout=8000)
    assert_clean(page)


def test_busts_play_one_after_another(laptop, fast_sync):  # noqa: F811
    theme(bust={"title": "Мимо", "caption": "{name}", "seconds": 1})
    game, results = table(*MORE)
    page = laptop(tablo_url(game), signed_in=False)

    bust(game, results[0])
    bust(game, results[1])

    caption = page.locator("[data-overlay-caption]")
    expect(caption).to_have_text("Альфа")
    assert page.locator("[data-overlay]").count() == 1
    expect(caption).to_have_text("Браво", timeout=4000)
    expect(overlay(page)).to_be_hidden(timeout=4000)
    assert_clean(page)


def test_rebuy_lower_third_keeps_the_clock(laptop, fast_sync):  # noqa: F811
    theme(
        rebuy={
            "title": "Снова в игре",
            "caption": "{name} · ребай {amount}",
            "style": OverlayStyle.LOWER_THIRD,
            "seconds": 3,
        }
    )
    game, results = table()
    page = laptop(tablo_url(game), signed_in=False)

    actions.rebuy(game.pk, key(), None, results[0].pk)

    expect(page.locator("[data-overlay-caption]")).to_have_text("Альфа · ребай 50")
    assert "tablo-overlay-lower-third" in page.locator("body").get_attribute("class")
    clock = page.locator("[data-clock]")
    expect(clock).to_be_visible()
    assert clock.evaluate("el => getComputedStyle(el.closest('.tablo-grid')).opacity") == "1"
    box = page.locator(".tablo-overlay-box").bounding_box()
    assert box["y"] > LAPTOP["viewport"]["height"] / 2  # at the bottom
    assert_clean(page)


def test_rebuy_is_silent_without_a_theme(laptop, fast_sync):  # noqa: F811
    game, results = table()
    page = laptop(tablo_url(game), signed_in=False)
    actions.rebuy(game.pk, key(), None, results[0].pk)
    page.wait_for_timeout(2500)
    expect(overlay(page)).to_be_hidden()


def test_break_screen_rotates_the_tips(laptop, fast_sync, settings):  # noqa: F811
    settings.THEME_TIP_SECONDS = 1
    theme(
        **{
            EventKind.BREAK: {
                "title": "Перекур",
                "style": OverlayStyle.FULLSCREEN,
                "seconds": 0,
            }
        }
    )
    game, _ = table()
    running(game, left_seconds=50, position=3)  # the break
    page = laptop(tablo_url(game), signed_in=False)

    screen = page.locator("[data-break-screen]")
    expect(screen).to_be_visible()
    expect(page.locator("[data-break-screen-title]")).to_have_text("Перекур")
    expect(page.locator("[data-break-screen-theme]")).to_have_text("Проверочная")
    expect(page.locator("[data-break-screen-clock]")).to_have_text("00:50", timeout=3000)
    expect(page.locator("[data-tip]")).to_have_text("первый")
    expect(page.locator("[data-tip-count]")).to_have_text("1 / 2")
    expect(page.locator("[data-tip]")).to_have_text("второй", timeout=3000)
    expect(page.locator("[data-tip]")).to_have_text("первый", timeout=3000)
    assert_clean(page)


def test_no_replay_after_a_reload(laptop, fast_sync):  # noqa: F811
    game, results = table("Чарли", "Дельта", "Эхо")
    page = laptop(tablo_url(game), signed_in=False)
    bust(game, results[0])
    expect(overlay(page)).to_be_visible()

    page.reload()
    page.wait_for_timeout(2500)
    expect(overlay(page)).to_be_hidden()


def test_winner_stays_through_the_end_of_the_game(laptop, fast_sync):  # noqa: F811
    theme(bust_itm={"title": "Призёр", "caption": "{name}", "seconds": 1})
    game, results = table()  # Альфа and Браво
    page = laptop(tablo_url(game), signed_in=False)

    bust(game, results[0])
    expect(page.locator("[data-overlay-title]")).to_have_text("Победитель", timeout=4000)
    expect(page.locator("[data-overlay-caption]")).to_have_text("Браво")

    Game.objects.filter(pk=game.pk).update(live_stage="")
    expect(page.locator("[data-level-line]")).to_contain_text("Табло отключено", timeout=4000)
    expect(page.locator("[data-overlay-title]")).to_have_text("Победитель")
    expect(overlay(page)).to_be_visible()
    assert_clean(page)


def test_winner_goes_away_after_an_undo(laptop, fast_sync):  # noqa: F811
    theme(bust_itm={"title": "Призёр", "caption": "{name}", "seconds": 1})
    game, results = table()
    page = laptop(tablo_url(game), signed_in=False)

    action = bust(game, results[0])
    expect(page.locator("[data-overlay-title]")).to_have_text("Победитель", timeout=4000)

    actions.undo(action.pk, None)
    expect(overlay(page)).to_be_hidden(timeout=4000)
    page.wait_for_timeout(1500)
    expect(overlay(page)).to_be_hidden()
    assert_clean(page)


def test_reduced_motion_shows_the_text_without_animation(browser, live_server, fast_sync):  # noqa: F811
    game, results = table("Чарли")
    context = browser.new_context(reduced_motion="reduce", **LAPTOP)
    context.add_init_script(COLLECT_CSP_VIOLATIONS)
    page = context.new_page()
    try:
        page.goto(live_server.url + tablo_url(game))
        bust(game, results[0])

        expect(page.locator("[data-overlay-caption]")).to_have_text("Альфа · 3 место")
        names = page.evaluate(
            "[...document.querySelectorAll('.tablo-overlay-box, .tablo-overlay-title')]"
            ".map(el => getComputedStyle(el).animationName)"
        )
        assert set(names) == {"none"}
        assert_clean(page)
    finally:
        context.close()


def test_sound_only_after_the_tap(browser, live_server, fast_sync):  # noqa: F811
    sound = themes.store_sound("hit.wav", wav(), "audio/wav")
    theme(bust={"title": "Мимо", "caption": "{name}", "seconds": 1, "sound": sound})
    game, results = table(*MORE)
    context = browser.new_context(**LAPTOP)
    context.add_init_script(COLLECT_CSP_VIOLATIONS)
    context.add_init_script(COUNT_SOUNDS)
    page = context.new_page()
    try:
        page.goto(live_server.url + tablo_url(game))
        bust(game, results[0])
        expect(page.locator("[data-overlay-caption]")).to_have_text("Альфа")
        page.wait_for_timeout(500)
        assert page.evaluate("window.soundsStarted") == 0

        page.locator("[data-sound-prompt]").click()
        bust(game, results[1])
        expect(page.locator("[data-overlay-caption]")).to_have_text("Браво", timeout=4000)
        page.wait_for_function("window.soundsStarted === 1")
        assert_clean(page)
    finally:
        context.close()


def test_preview_page(laptop):  # noqa: F811
    created = theme(
        bust={"title": "Мимо", "caption": "{name} · {place} место", "style": OverlayStyle.BAND},
        **{EventKind.BREAK: {"title": "Перекур", "style": OverlayStyle.FULLSCREEN, "seconds": 0}},
    )
    page = laptop(reverse("live:theme_preview", args=[created.pk]))

    page.locator('[data-preview-kind="bust"]').click()
    expect(page.locator("[data-overlay-title]")).to_have_text("Мимо")
    expect(page.locator("[data-overlay-caption]")).to_have_text(f"{themes.SAMPLE_NAME} · 7 место")
    assert "tablo-overlay-band" in page.locator("body").get_attribute("class")
    assert (
        page.locator(".tablo-overlay-title")
        .evaluate("el => getComputedStyle(el).fontFamily")
        .startswith('"Russo One"')
    )

    page.locator('[data-preview-kind="rebuy"]').click()
    expect(page.locator("[data-preview-note]")).to_contain_text("выключено")

    page.locator('[data-preview-kind="break"]').click()
    expect(page.locator("[data-break-screen]")).to_be_visible()
    expect(page.locator("[data-break-screen-title]")).to_have_text("Перекур")
    assert_touch_targets(page, ".tablo-preview .btn")
    assert_clean(page)
