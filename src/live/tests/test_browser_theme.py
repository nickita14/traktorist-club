"""Theme nights on the display (табло) in Chromium: announcements from the phone's presses, one
after another and within a second on both display routes, the lower third over a visible clock,
the break screen's tips, with and without the theme's cover and accent, no replay after a reload,
the winner through the end of the game and gone after an undo, reduced motion, sound only after the
tap, each sound as long as its overlay, the break music looping while the break runs, the admin's
preview page and the theme admin (the sound player follows the select, the file button). Every
page is checked for CSP violations."""

import io
import re
import struct
import wave
import zlib

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

# Records every theme sound (Web Audio buffer source) that started: whether it loops, when it was
# told to stop (seconds after the call: the fade) and whether it ended.
RECORD_SOUNDS = """
window.sounds = [];
const start = AudioBufferSourceNode.prototype.start;
AudioBufferSourceNode.prototype.start = function (...args) {
  const entry = { loop: this.loop, stop: null, ended: false };
  window.sounds.push(entry);
  this.recorded = entry;
  this.addEventListener("ended", () => { entry.ended = true; });
  return start.apply(this, args);
};
const stop = AudioBufferSourceNode.prototype.stop;
AudioBufferSourceNode.prototype.stop = function (when = 0) {
  if (this.recorded) {
    this.recorded.stop = when ? when - this.context.currentTime : 0;
  }
  return stop.call(this, when);
};
"""

# Whether the "нет связи" mark was ever shown since the page loaded.
WATCH_OFFLINE = """
window.offlineShown = false;
document.addEventListener("DOMContentLoaded", () => {
  const mark = document.querySelector("[data-offline]");
  new MutationObserver(() => {
    if (!mark.hidden) {
      window.offlineShown = true;
    }
  }).observe(mark, { attributes: true, attributeFilter: ["hidden"] });
});
"""


# Six more players: the first two busts are plain ones (places 8 and 7, 3 paid).
MORE = ("Чарли", "Дельта", "Эхо", "Фокстрот", "Гольф", "Хотел")


def wav(seconds: float = 0.1) -> bytes:
    """Silence of that length: a sound Chromium can decode."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(8000)
        file.writeframes(bytes(2 * round(8000 * seconds)))
    return buffer.getvalue()


def png(width: int = 64, height: int = 40) -> bytes:
    """A real image Chromium can decode (stripes of two greys), without an imaging library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(
        b"\x00" + bytes([90 if (x // 8) % 2 else 160 for x in range(width)]) for _ in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit greyscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def sound_page(browser, live_server, path):
    """A display with the sound recorder, sound already switched on (the tap)."""
    context = browser.new_context(**LAPTOP)
    context.add_init_script(COLLECT_CSP_VIOLATIONS)
    context.add_init_script(RECORD_SOUNDS)
    page = context.new_page()
    page.goto(live_server.url + path)
    page.mouse.click(5, 5)  # any tap switches sound on
    until(page, "window.sounds !== undefined")
    return page


def until(page, expression, timeout=5000):
    """Poll ``expression`` until it is true. Not wait_for_function: under the strict CSP its
    polling evaluates a string, which 'unsafe-eval' would have to allow."""
    for _ in range(timeout // 100):
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"still false after {timeout} ms: {expression}")


def sounds(page):
    return page.evaluate("window.sounds")


def faded(entry) -> bool:
    """Told to stop with the fade (about 300 ms), not cut."""
    return entry["stop"] is not None and 0.2 <= entry["stop"] <= 0.35


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
    # A running clock: started with 50 s left, a few seconds gone by the time the page shows it.
    expect(page.locator("[data-break-screen-clock]")).to_have_text(re.compile(r"^00:[45]\d$"))
    expect(page.locator("[data-tip]")).to_have_text("первый")
    expect(page.locator("[data-tip-count]")).to_have_text("1 / 2")
    expect(page.locator("[data-tip]")).to_have_text("второй", timeout=3000)
    expect(page.locator("[data-tip]")).to_have_text("первый", timeout=3000)
    assert_clean(page)


BREAK_ROW = {"title": "Перекур", "style": OverlayStyle.FULLSCREEN, "seconds": 0}

# The break screen's geometry and colours, as computed: what the mockup fixes on 1280x800.
BREAK_LAYOUT = """() => {
  const box = (selector) => document.querySelector(selector).getBoundingClientRect();
  const style = (selector) => getComputedStyle(document.querySelector(selector));
  const cover = document.querySelector("[data-break-cover]");
  const column = document.querySelector(".tablo-break-screen-body");
  return {
    split: document.querySelector("[data-break-screen]").classList.contains(
      "tablo-break-screen-split"
    ),
    coverShown: !cover.hidden && box("[data-break-cover]").width > 0,
    coverLoaded: cover.complete && cover.naturalWidth > 0,
    coverLeft: box("[data-break-cover]").left,
    coverFit: style("[data-break-cover]").objectFit,
    coverPosition: style("[data-break-cover]").objectPosition,
    columnWidth: box(".tablo-break-screen-body").width,
    columnOverflows: column.scrollWidth > column.clientWidth + 1
      || column.scrollHeight > column.clientHeight + 1,
    stripe: style(".tablo-break-screen-body").borderRightColor,
    stripeWidth: style(".tablo-break-screen-body").borderRightWidth,
    themeColor: style("[data-break-screen-theme]").color,
    themeFont: style("[data-break-screen-theme]").fontFamily,
    titleFont: style("[data-break-screen-title]").fontFamily,
    titleSize: style("[data-break-screen-title]").fontSize,
    clockFont: style("[data-break-screen-clock]").fontFamily,
    clockFits: (() => {
      const clock = document.querySelector("[data-break-screen-clock]");
      return clock.scrollWidth <= clock.clientWidth;
    })(),
    tipBorder: style("[data-tip-box]").borderLeftColor,
    tipTop: box("[data-tip-box]").top,
    nextTop: box("[data-break-screen-next]").top,
    nextBottom: box("[data-break-screen-next]").bottom,
    blindsFont: style("[data-break-screen-next-blinds]").fontFamily,
    pageScrolls: document.documentElement.scrollWidth > window.innerWidth,
  };
}"""

PAPER_RGB = "rgb(242, 237, 227)"
GOLD_RGB = "rgb(242, 182, 48)"


def test_break_screen_with_a_cover(laptop, fast_sync):  # noqa: F811
    created = theme(**{EventKind.BREAK: BREAK_ROW})
    created.accent = "#f2ede3"
    created.save()
    themes.store_cover(created, png(), "image/png")
    game, _ = table()
    running(game, left_seconds=50, position=3)  # the add-on break
    page = laptop(tablo_url(game), signed_in=False)

    expect(page.locator("[data-break-screen]")).to_be_visible()
    expect(page.locator("[data-break-cover]")).to_be_visible()
    until(page, "document.querySelector('[data-break-cover]').naturalWidth > 0")
    page.evaluate("document.fonts.ready")
    expect(page.locator("[data-break-screen-addon]")).to_have_text("Перерыв на аддон")
    expect(page.locator("[data-break-screen-next-label]")).to_have_text("Дальше: уровень 3 · ")
    expect(page.locator("[data-break-screen-next-blinds]")).to_have_text("100 / 200")
    layout = page.evaluate(BREAK_LAYOUT)

    assert layout["split"] and layout["coverShown"] and layout["coverLoaded"]
    assert layout["columnWidth"] == pytest.approx(580, abs=1)
    assert layout["coverLeft"] == pytest.approx(580, abs=1)
    assert (layout["coverFit"], layout["coverPosition"]) == ("cover", "50% 30%")
    assert layout["stripe"] == layout["themeColor"] == layout["tipBorder"] == PAPER_RGB
    assert layout["stripeWidth"] == "6px"
    assert layout["themeFont"].startswith('"Russo One"')
    assert layout["titleFont"].startswith('"Russo One"')
    assert layout["titleSize"] == "96px"
    assert layout["clockFont"].startswith('"PT Mono"') and layout["clockFits"]
    assert layout["blindsFont"].startswith('"PT Mono"')
    # The tip and the next level sit at the bottom, in that order; nothing spills out.
    assert layout["tipTop"] < layout["nextTop"]
    assert layout["nextBottom"] == pytest.approx(800 - 40, abs=2)
    assert not layout["columnOverflows"] and not layout["pageScrolls"]
    assert_clean(page)


def test_break_screen_without_a_cover_stays_plain(laptop, fast_sync):  # noqa: F811
    theme(**{EventKind.BREAK: BREAK_ROW})
    game, _ = table()
    running(game, left_seconds=50, position=3)
    page = laptop(tablo_url(game), signed_in=False)

    expect(page.locator("[data-break-screen]")).to_be_visible()
    expect(page.locator("[data-break-screen-addon]")).to_be_visible()
    expect(page.locator("[data-break-screen-next-blinds]")).to_have_text("100 / 200")
    layout = page.evaluate(BREAK_LAYOUT)

    assert not layout["split"]
    assert page.locator("[data-break-cover]").is_hidden()
    assert page.locator("[data-break-cover]").get_attribute("src") is None
    assert layout["themeColor"] == layout["tipBorder"] == GOLD_RGB  # no accent: as before
    assert not layout["blindsFont"].startswith('"PT Mono"')  # the plain line is unchanged
    assert not layout["pageScrolls"]
    assert_clean(page)


def test_cover_that_fails_to_load_keeps_the_plain_layout(laptop, fast_sync):  # noqa: F811
    created = theme(**{EventKind.BREAK: BREAK_ROW})
    themes.store_cover(created, b"\x89PNG\r\n\x1a\n" + bytes(32), "image/png")  # broken
    game, _ = table()
    running(game, left_seconds=50, position=3)
    page = laptop(tablo_url(game), signed_in=False)

    expect(page.locator("[data-break-screen]")).to_be_visible()
    expect(page.locator("[data-break-cover]")).to_be_hidden()
    assert not page.evaluate(BREAK_LAYOUT)["split"]


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
    context.add_init_script(RECORD_SOUNDS)
    page = context.new_page()
    try:
        page.goto(live_server.url + tablo_url(game))
        bust(game, results[0])
        expect(page.locator("[data-overlay-caption]")).to_have_text("Альфа")
        page.wait_for_timeout(500)
        assert page.evaluate("window.sounds.length") == 0

        page.locator("[data-sound-prompt]").click()
        bust(game, results[1])
        expect(page.locator("[data-overlay-caption]")).to_have_text("Браво", timeout=4000)
        until(page, "window.sounds.length === 1")
        assert_clean(page)
    finally:
        context.close()


def test_sound_ends_with_its_overlay(browser, live_server):
    """A 5 s file on 2 s overlays: each fades out when its overlay ends, the next one's start
    included."""
    sound = themes.store_sound("long.wav", wav(5), "audio/wav")
    theme(bust={"title": "Мимо", "caption": "{name}", "seconds": 2, "sound": sound})
    game, results = table(*MORE)
    page = sound_page(browser, live_server, tablo_url(game))
    try:
        bust(game, results[0])
        bust(game, results[1])
        caption = page.locator("[data-overlay-caption]")
        expect(caption).to_have_text("Альфа")
        until(page, "window.sounds.length === 1")
        assert sounds(page)[0]["stop"] is None  # plays while its overlay is up

        expect(caption).to_have_text("Браво", timeout=4000)
        until(page, "window.sounds.length === 2")
        first, second = sounds(page)
        assert faded(first)
        assert second["stop"] is None

        expect(overlay(page)).to_be_hidden(timeout=4000)
        assert faded(sounds(page)[1])
        assert not any(entry["loop"] for entry in sounds(page))
        assert_clean(page)
    finally:
        page.context.close()


def test_winner_sound_plays_once_to_the_end(browser, live_server):
    sound = themes.store_sound("fanfare.wav", wav(0.5), "audio/wav")
    theme(
        bust_itm={"title": "Призёр", "caption": "{name}", "seconds": 1},
        winner={"title": "Победитель", "caption": "{name}", "seconds": 0, "sound": sound},
    )
    game, results = table()  # Альфа and Браво: the first bust leaves the winner
    page = sound_page(browser, live_server, tablo_url(game))
    try:
        bust(game, results[0])
        expect(page.locator("[data-overlay-title]")).to_have_text("Победитель", timeout=4000)
        until(page, "window.sounds.some((entry) => entry.ended)", timeout=4000)
        (entry,) = [entry for entry in sounds(page) if entry["ended"]]
        assert entry["stop"] is None and not entry["loop"]
        expect(overlay(page)).to_be_visible()
        assert_clean(page)
    finally:
        page.context.close()


def test_break_music_loops_while_the_break_runs(browser, live_server):
    sound = themes.store_sound("break.wav", wav(1), "audio/wav")
    theme(
        **{
            EventKind.BREAK: {
                "title": "Перекур",
                "style": OverlayStyle.FULLSCREEN,
                "seconds": 0,
                "sound": sound,
            }
        }
    )
    game, _ = table()
    running(game, left_seconds=50, position=3)  # the add-on break
    page = sound_page(browser, live_server, tablo_url(game))
    try:
        expect(page.locator("[data-break-screen]")).to_be_visible()
        until(page, "window.sounds.length === 1")
        page.wait_for_timeout(1500)  # longer than the file: still the one looping source
        (music,) = sounds(page)
        assert music["loop"] and music["stop"] is None

        actions.set_paused(game.pk, key(), None, True)
        until(page, "window.sounds[0].stop !== null", timeout=3000)
        assert faded(sounds(page)[0])
        assert len(sounds(page)) == 1  # nothing while paused

        actions.set_paused(game.pk, key(), None, False)
        until(page, "window.sounds.length === 2", timeout=3000)
        assert sounds(page)[1]["loop"]

        running(game, left_seconds=1, position=3)  # the break ends in a second
        expect(page.locator("[data-break-screen]")).to_be_hidden(timeout=4000)
        until(page, "window.sounds[1].stop !== null")
        assert faded(sounds(page)[1])
        assert len(sounds(page)) == 2
        assert_clean(page)
    finally:
        page.context.close()


def test_break_music_stops_when_the_theme_is_switched_off(browser, live_server):
    sound = themes.store_sound("break.wav", wav(1), "audio/wav")
    created = theme(
        **{EventKind.BREAK: {"style": OverlayStyle.FULLSCREEN, "seconds": 0, "sound": sound}}
    )
    game, _ = table()
    running(game, left_seconds=50, position=3)
    page = sound_page(browser, live_server, tablo_url(game))
    try:
        until(page, "window.sounds.length === 1")
        Theme.objects.filter(pk=created.pk).update(is_active=False)
        until(page, "window.sounds[0].stop !== null", timeout=3000)
        assert faded(sounds(page)[0])
        expect(page.locator("[data-break-screen]")).to_be_hidden()
        assert_clean(page)
    finally:
        page.context.close()


@pytest.mark.parametrize("route", ["public", "organizer"])
def test_overlay_within_the_sync_interval(laptop, route):  # noqa: F811
    """With the default interval (no fast_sync): a bust shows on both display routes in about a
    second."""
    game, results = table("Чарли", "Дельта", "Эхо")
    if route == "public":
        page = laptop(tablo_url(game), signed_in=False)
    else:
        page = laptop(reverse("live:tablo", args=[game.pk]))
    expect(page.locator("[data-clock]")).not_to_have_text("--:--")

    bust(game, results[0])
    # One interval (1 s) plus the request and a frame.
    expect(overlay(page)).to_be_visible(timeout=1600)
    assert_clean(page)


def test_one_lost_sync_does_not_show_no_connection(browser, live_server):
    game, _ = table()
    context = browser.new_context(**LAPTOP)
    context.add_init_script(COLLECT_CSP_VIOLATIONS)
    context.add_init_script(WATCH_OFFLINE)
    page = context.new_page()
    try:
        page.goto(live_server.url + tablo_url(game))
        expect(page.locator("[data-clock]")).not_to_have_text("--:--")
        lost = []

        def lose_once(route):
            if not lost:
                lost.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/tablo/*/state/**", lose_once)
        page.wait_for_timeout(3000)
        assert lost
        assert not page.evaluate("window.offlineShown")

        page.route("**/tablo/*/state/**", lambda route: route.abort())
        expect(page.locator("[data-offline]")).to_be_visible(timeout=6000)
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


def test_preview_shows_the_cover_and_accent(laptop):  # noqa: F811
    created = Theme.objects.create(name="Обложечная", accent="#f2ede3")  # not active
    ThemeEvent.objects.create(theme=created, kind=EventKind.BREAK, **BREAK_ROW)
    themes.store_cover(created, png(), "image/png")
    page = laptop(reverse("live:theme_preview", args=[created.pk]))

    page.locator('[data-preview-kind="break"]').click()
    expect(page.locator("[data-break-cover]")).to_be_visible()
    until(page, "document.querySelector('[data-break-cover]').naturalWidth > 0")
    layout = page.evaluate(BREAK_LAYOUT)
    assert layout["split"] and layout["coverLoaded"]
    assert layout["stripe"] == layout["themeColor"] == PAPER_RGB
    expect(page.locator("[data-break-screen-theme]")).to_have_text("Обложечная")
    assert_clean(page)


def test_theme_admin_plays_the_selected_sound(laptop):  # noqa: F811
    gong = themes.store_sound("gong.wav", wav(), "audio/wav")
    bell = themes.store_sound("bell.wav", wav(0.2), "audio/wav")
    created = Theme.objects.create(name="Вечер")
    ThemeEvent.objects.create(theme=created, kind=EventKind.BUST, sound=gong)
    ThemeEvent.objects.create(theme=created, kind=EventKind.WINNER)
    page = laptop(reverse("admin:live_theme_change", args=[created.pk]))
    page.wait_for_load_state("networkidle")
    players = page.locator("audio[data-sound-player]")
    selects = page.locator("select[name$='-sound']")

    assert players.nth(0).get_attribute("src") == themes.sound_url(gong)
    expect(players.nth(1)).to_be_hidden()

    selects.nth(1).select_option(str(bell.pk))
    expect(players.nth(1)).to_be_visible()
    assert players.nth(1).get_attribute("src") == themes.sound_url(bell)
    selects.nth(0).select_option("")
    expect(players.nth(0)).to_be_hidden()
    assert players.nth(0).get_attribute("src") is None
    assert page.locator("input[name$='-upload']").count() == 0

    # The cover input: the browser's button drawn like Unfold's secondary button.
    button = page.locator("input[name=cover]").evaluate(
        """el => {
          const style = getComputedStyle(el, "::file-selector-button");
          return {
            border: style.borderTopWidth, radius: style.borderTopLeftRadius,
            height: parseFloat(style.height), cursor: style.cursor,
          };
        }"""
    )
    assert button["border"] == "1px" and button["radius"] != "0px"
    assert button["cursor"] == "pointer"
    assert page.evaluate("window.cspViolations") == []


def test_sound_page_shows_the_file(laptop):  # noqa: F811
    sound = themes.store_sound("gong.wav", wav(), "audio/wav")
    page = laptop(reverse("admin:live_themesound_change", args=[sound.pk]))
    page.wait_for_load_state("networkidle")

    expect(page.locator("audio")).to_have_attribute("src", themes.sound_url(sound))
    expect(page.locator("body")).to_contain_text("wav · 2 КБ")
    expect(page.locator("input[type=file][name=upload]")).to_be_visible()
    assert page.evaluate("window.cspViolations") == []


def test_preview_sounds(browser, live_server, admin_user, client):
    """The preview plays the same way: an event's sound ends with its overlay, the break music
    loops until the break is switched off."""
    hit = themes.store_sound("hit.wav", wav(5), "audio/wav")
    music = themes.store_sound("break.wav", wav(1), "audio/wav")
    created = theme(
        bust={"title": "Мимо", "caption": "{name}", "seconds": 1, "sound": hit},
        **{
            EventKind.BREAK: {
                "title": "Перекур",
                "style": OverlayStyle.FULLSCREEN,
                "seconds": 0,
                "sound": music,
            }
        },
    )
    client.force_login(admin_user)
    context = browser.new_context(**LAPTOP)
    context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    context.add_init_script(COLLECT_CSP_VIOLATIONS)
    context.add_init_script(RECORD_SOUNDS)
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("live:theme_preview", args=[created.pk]))
        page.mouse.click(5, 5)  # sound on
        until(page, "window.sounds !== undefined")

        page.locator('[data-preview-kind="bust"]').click()
        until(page, "window.sounds.length === 1")
        expect(overlay(page)).to_be_hidden(timeout=3000)
        until(page, "window.sounds[0].stop !== null")
        assert faded(sounds(page)[0])

        page.locator('[data-preview-kind="break"]').click()
        until(page, "window.sounds.length === 2", timeout=3000)
        assert sounds(page)[1]["loop"]
        page.locator('[data-preview-kind="break"]').click()  # the break off again
        until(page, "window.sounds[1].stop !== null", timeout=3000)
        assert faded(sounds(page)[1])
        assert_clean(page)
    finally:
        context.close()
