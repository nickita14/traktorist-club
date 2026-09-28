"""Render the home screen icons (assets/icons/live-*.png) from the design tokens.

A stamp-like mark: a double ring and "ККТ" in stamp red on paper, tilted like the hand-drawn
circle of the season leader. Uses Playwright's Chromium (a dev dependency) and the self-hosted
PT Sans Narrow, so the PNGs are committed and production never runs this.

    uv run python manage.py render_icons
"""

import base64

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from live.manifest import APPLE_ICON, ICONS, tokens

FONT = settings.BASE_DIR / "assets/fonts/pt-sans-narrow/pt-sans-narrow-700-cyrillic.woff2"
OUTPUT = settings.BASE_DIR / "assets"

# Ring radius in a 100-unit square: "any" icons fill the square, "maskable" ones stay inside the
# safe zone (a circle of radius 40) that launchers never crop.
RADIUS = {"any": 42, "maskable": 31}


def svg(radius: float, colors: dict[str, str]) -> str:
    inner = radius - 5
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100%">
  <rect width="100" height="100" fill="{colors["paper"]}"/>
  <g transform="rotate(-8 50 50)" fill="none" stroke="{colors["accent"]}">
    <circle cx="50" cy="50" r="{radius}" stroke-width="3"/>
    <circle cx="50" cy="50" r="{inner}" stroke-width="1.2"/>
    <text x="50" y="50" dy="0.35em" text-anchor="middle" fill="{colors["accent"]}" stroke="none"
          font-family="Stamp" font-weight="700" font-size="{radius * 0.62}"
          letter-spacing="{radius * 0.02}">ККТ</text>
  </g>
</svg>"""


def page_html(radius: float, colors: dict[str, str]) -> str:
    font = base64.b64encode(FONT.read_bytes()).decode()
    return f"""<!doctype html><meta charset="utf-8">
<style>
  @font-face {{ font-family: Stamp; font-weight: 700; src: url(data:font/woff2;base64,{font}); }}
  html, body {{ margin: 0; }}
  #icon {{ width: 100vw; height: 100vh; }}
</style>
<div id="icon">{svg(radius, colors)}</div>"""


class Command(BaseCommand):
    help = "Render the home screen icons of the live screens from the design tokens."

    def handle(self, *args, **options):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise CommandError("Needs Playwright: uv sync (dev dependencies).") from error
        colors = tokens()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for path, size, purpose in [*ICONS, APPLE_ICON]:
                page = browser.new_page(viewport={"width": size, "height": size})
                page.set_content(page_html(RADIUS[purpose], colors))
                page.evaluate("document.fonts.ready")
                target = OUTPUT / path
                target.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(target))
                page.close()
                self.stdout.write(f"{target.relative_to(settings.BASE_DIR)} ({size}px, {purpose})")
            browser.close()
