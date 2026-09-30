"""The admin in a real browser: light mode holds and text stays readable.

Unfold keeps its theme in localStorage ("adminTheme"), which outranks UNFOLD["THEME"]; a stored
"dark" (or "auto" on a dark OS) used to render dark text on a dark background. These tests seed
that state and measure the rendered colors.

The ``browser`` fixture (headless Chromium, skipped when it is missing) is in the root conftest.py.
"""

import pytest
from django.urls import reverse

from club.models import BlindStructure, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season, make_structure
from conftest import COLLECT_CSP_VIOLATIONS

pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.django_db(transaction=True)

# WCAG AA for body text.
MIN_CONTRAST = 4.5

# Contrast of each element's text against the first opaque background behind it. Colors are
# normalized through a canvas, so oklab() and color-mix() values compare like hex ones.
MEASURE = """selectors => {
  const canvas = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  const rgb = color => {
    canvas.clearRect(0, 0, 1, 1);
    canvas.fillStyle = color;
    canvas.fillRect(0, 0, 1, 1);
    return [...canvas.getImageData(0, 0, 1, 1).data].slice(0, 3);
  };
  const channel = c => {
    c /= 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const luminance = color => {
    const [r, g, b] = rgb(color).map(channel);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const background = el => {
    for (; el; el = el.parentElement) {
      const color = getComputedStyle(el).backgroundColor;
      if (color !== 'rgba(0, 0, 0, 0)' && color !== 'transparent') return color;
    }
    return 'rgb(255, 255, 255)';
  };
  const result = {htmlClass: document.documentElement.className, contrast: {}};
  for (const selector of selectors) {
    const el = document.querySelector(selector);
    if (!el) { result.contrast[selector] = null; continue; }
    const [hi, lo] = [luminance(getComputedStyle(el).color), luminance(background(el))]
      .sort((a, b) => b - a);
    result.contrast[selector] = Math.round((hi + 0.05) / (lo + 0.05) * 100) / 100;
  }
  return result;
}"""

CHANGELIST_TEXT = ["body", "h1", "#result_list tbody td", "#result_list thead th", "#searchbar"]
CHANGE_FORM_TEXT = ["body", "h1", "input[name=location]", "label", ".tabular td"]


@pytest.fixture
def game():
    game = make_game(make_season(2025, SeasonKind.TOUR), day=7, month=6, location="Гараж")
    make_result(game, make_player("Альфа"), buyin=50, payout=100, place=1)
    make_result(game, make_player("Браво"), buyin=50, payout=0)
    return game


@pytest.fixture
def admin_page(browser, live_server, admin_user, client):
    """Open an admin URL in a fresh context with an OS scheme and an optional stored theme."""
    client.force_login(admin_user)
    session = client.cookies["sessionid"].value
    contexts = []

    def open_page(path, *, color_scheme="light", stored_theme=None):
        context = browser.new_context(color_scheme=color_scheme)
        contexts.append(context)
        context.add_cookies([{"name": "sessionid", "value": session, "url": live_server.url}])
        context.add_init_script(COLLECT_CSP_VIOLATIONS)
        if stored_theme:
            # What Unfold's theme switcher left behind before THEME was set.
            context.add_init_script(
                f"localStorage.setItem('adminTheme', JSON.stringify('{stored_theme}'))"
            )
        page = context.new_page()
        page.goto(live_server.url + path)
        page.evaluate("document.fonts.ready")
        return page

    yield open_page
    for context in contexts:
        context.close()


SCENARIOS = [
    pytest.param("light", None, id="os-light"),
    pytest.param("dark", None, id="os-dark"),
    pytest.param("light", "dark", id="stored-dark"),
    pytest.param("dark", "dark", id="stored-dark-os-dark"),
    pytest.param("dark", "auto", id="stored-auto-os-dark"),
]


@pytest.mark.parametrize(("color_scheme", "stored_theme"), SCENARIOS)
def test_changelist_stays_light_and_readable(admin_page, game, color_scheme, stored_theme):
    page = admin_page(
        reverse("admin:club_player_changelist"),
        color_scheme=color_scheme,
        stored_theme=stored_theme,
    )

    measured = page.evaluate(MEASURE, CHANGELIST_TEXT)

    assert "dark" not in measured["htmlClass"].split()
    for selector, ratio in measured["contrast"].items():
        assert ratio is not None, f"{selector} not found"
        assert ratio >= MIN_CONTRAST, f"{selector}: contrast {ratio}"


@pytest.mark.parametrize(("color_scheme", "stored_theme"), SCENARIOS)
def test_change_form_stays_light_and_readable(admin_page, game, color_scheme, stored_theme):
    page = admin_page(
        reverse("admin:club_game_change", args=[game.pk]),
        color_scheme=color_scheme,
        stored_theme=stored_theme,
    )

    measured = page.evaluate(MEASURE, CHANGE_FORM_TEXT)

    assert "dark" not in measured["htmlClass"].split()
    for selector, ratio in measured["contrast"].items():
        assert ratio is not None, f"{selector} not found"
        assert ratio >= MIN_CONTRAST, f"{selector}: contrast {ratio}"


def test_keyboard_toggle_does_not_switch_to_dark(admin_page, game):
    page = admin_page(reverse("admin:club_player_changelist"))

    page.keyboard.press("Control+e")

    assert "dark" not in page.evaluate("document.documentElement.className").split()


def test_primary_is_ink_and_danger_is_stamp_red(admin_page, game):
    page = admin_page(reverse("admin:club_player_changelist"))

    colors = page.evaluate("""() => {
      const probe = document.createElement('div');
      document.body.append(probe);
      const resolve = value => { probe.style.color = value; return getComputedStyle(probe).color; };
      return {
        primary: resolve('var(--color-primary-600)'), ink: resolve('var(--ink)'),
        danger: resolve('var(--color-red-600)'), accent: resolve('var(--accent)'),
        warning: resolve('var(--color-orange-600)'),
      };
    }""")

    assert colors["primary"] == colors["ink"]
    assert colors["danger"] == colors["accent"] == colors["warning"]


@pytest.mark.parametrize(
    "path",
    [
        pytest.param(lambda game: reverse("admin:club_season_changelist"), id="changelist"),
        pytest.param(lambda game: reverse("admin:club_game_change", args=[game.pk]), id="form"),
        pytest.param(lambda game: reverse("game_detail", args=[game.pk]), id="public"),
        pytest.param(lambda game: reverse("admin:club_rankladder_changelist"), id="ladders"),
        pytest.param(
            lambda game: reverse("admin:club_achievementsettings_changelist"), id="settings"
        ),
    ],
)
def test_pages_run_without_csp_violations(admin_page, game, path):
    page = admin_page(path(game))
    page.wait_for_load_state("networkidle")

    assert page.evaluate("window.cspViolations") == []


def test_blind_rows_switch_type_and_renumber(admin_page):
    structure = make_structure(rows=[(25, 50, 20), (50, 100, 20), ("Перерыв", 10), (100, 200, 20)])
    page = admin_page(reverse("admin:club_blindstructure_change", args=[structure.pk]))
    numbers = page.locator("[data-level-number]")
    kinds = page.locator("select[data-row-kind]")
    second = page.locator("tbody.form-group").nth(1)

    assert numbers.all_inner_texts()[:4] == ["1", "2", "перерыв", "3"]
    # A level: blinds usable, the break fields hidden and off.
    assert second.locator("input[name$=-label]").is_disabled()
    assert not second.locator("input[name$=-small_blind]").is_disabled()
    handle = page.locator("[x-sort\\:handle]").first
    assert handle.is_visible()

    kinds.nth(1).select_option("break")

    assert numbers.all_inner_texts()[:4] == ["1", "перерыв", "перерыв", "2"]
    assert second.locator("input[name$=-small_blind]").is_disabled()
    assert not second.locator("input[name$=-small_blind]").is_visible()
    assert not second.locator("input[name$=-label]").is_disabled()
    assert second.locator("input[name$=-label]").is_visible()

    second.locator("input[name$=-label]").fill("Перерыв · аддон")
    second.locator("td.field-addon_break label, td.field-addon_break input").first.click()
    page.get_by_role("button", name="Сохранить", exact=True).click()
    page.wait_for_url("**/blindstructure/")

    rows = BlindStructure.objects.get(pk=structure.pk).levels.values_list(
        "big_blind", "label", "addon_break"
    )
    assert list(rows) == [
        (50, "", False),
        (None, "Перерыв · аддон", True),
        (None, "Перерыв", False),
        (200, "", False),
    ]
    assert page.evaluate("window.cspViolations") == []
