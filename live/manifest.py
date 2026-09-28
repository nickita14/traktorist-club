"""The web app manifest and icons that put the live screens on an organizer's home screen.

Colors come from assets/css/tokens.css, the one place with raw color values.
"""

import re
from functools import cache

from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse

TOKENS_CSS = settings.BASE_DIR / "assets" / "css" / "tokens.css"
TOKEN = re.compile(r"--([a-z]+):\s*(#[0-9a-f]{6})\s*;", re.IGNORECASE)

# (published file, size, purpose). "maskable" keeps the mark inside the safe zone.
ICONS = [
    ("icons/live-192.png", 192, "any"),
    ("icons/live-512.png", 512, "any"),
    ("icons/live-maskable-512.png", 512, "maskable"),
]
APPLE_ICON = ("icons/live-180.png", 180, "any")


@cache
def tokens() -> dict[str, str]:
    """Color tokens by name: {"paper": "#f2ede3", ...}."""
    return {name: value.lower() for name, value in TOKEN.findall(TOKENS_CSS.read_text())}


def manifest() -> dict:
    colors = tokens()
    return {
        "name": "Клуб Тракториста · Живая игра",
        "short_name": "Живая игра",
        "lang": "ru",
        "start_url": reverse("live:index"),
        # The whole site, so the public game sheet after saving results stays in the app.
        "scope": "/",
        "display": "standalone",
        "background_color": colors["paper"],
        "theme_color": colors["paper"],
        "icons": [
            {
                "src": static(path),
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": purpose,
            }
            for path, size, purpose in ICONS
        ],
    }
