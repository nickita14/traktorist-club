"""Design tokens: poster-red and gold belong to the celebration surfaces (CLAUDE.md "Celebration
pages")."""

import re

from django.conf import settings

POSTER_RED = re.compile(r"poster-red|#c8281e", re.IGNORECASE)

# The year report (форма № 7) and its includes: the only templates allowed to use poster-red.
CELEBRATION = re.compile(r"^club/(year_report[^/]*\.html|includes/year_report/.+\.html)$")


def templates() -> dict[str, str]:
    root = settings.SRC_DIR / "templates"
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in root.rglob("*.html")
    }


def test_poster_red_is_a_token():
    tokens = (settings.BASE_DIR / "assets/css/tokens.css").read_text(encoding="utf-8")
    source = (settings.BASE_DIR / "frontend/source.css").read_text(encoding="utf-8")

    assert "--poster-red: #c8281e;" in tokens
    assert "--color-poster-red: var(--poster-red);" in source


def test_poster_red_only_in_celebration_templates():
    found = [
        name
        for name, text in templates().items()
        if POSTER_RED.search(text) and not CELEBRATION.match(name)
    ]

    assert found == []


def test_celebration_pattern():
    assert CELEBRATION.match("club/year_report.html")
    assert CELEBRATION.match("club/year_report_player.html")
    assert CELEBRATION.match("club/includes/year_report/sheet.html")
    assert not CELEBRATION.match("club/honors.html")
    assert not CELEBRATION.match("live/year_report.html")


# The display's theme overlays (табло) are the only other celebration surface: their colors live
# in frontend/source.css, so no template names gold either.
GOLD = re.compile(r"\bgold\b|#f2b630", re.IGNORECASE)


def test_gold_is_a_token():
    tokens = (settings.BASE_DIR / "assets/css/tokens.css").read_text(encoding="utf-8")
    source = (settings.BASE_DIR / "frontend/source.css").read_text(encoding="utf-8")

    assert "--gold: #f2b630;" in tokens
    assert "--color-gold: var(--gold);" in source


def test_gold_only_in_celebration_templates():
    found = [
        name
        for name, text in templates().items()
        if GOLD.search(text) and not CELEBRATION.match(name)
    ]

    assert found == []


def test_russo_one_only_in_the_display_overlays():
    source = (settings.BASE_DIR / "frontend/source.css").read_text(encoding="utf-8")
    uses = [line for line in source.splitlines() if "--font-stack-poster" in line]
    # The band and headline title rule, and the split break screen's theme name and title.
    assert len(uses) == 2
    assert all("Russo One" not in text for text in templates().values())
