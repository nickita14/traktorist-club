"""Render the icons (assets/icons/) from the design tokens.

A stamp-like mark: a double ring and "ККТ" in stamp red on paper, tilted like the hand-drawn
circle of the season leader. Uses Playwright's Chromium (a dev dependency) and the self-hosted
PT Sans Narrow, so the files are committed and production never runs this.

- live-*.png: the home screen icons of the live screens (manifest, apple-touch-icon).
- favicon.svg, favicon-32.png, favicon.ico: the browser tab icon on every page. The favicon
  draws its letters as strokes (К and Т are straight lines), so the SVG needs no font and stays
  sharp at 16 px; the PNG and the ICO (16 and 32 px PNG frames) are rendered from it.

    uv run python manage.py render_icons
"""

import base64
import struct

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


# The favicon's letters in a 100-unit square before the tilt: cap height 38..62, 11 units wide,
# 6 apart. Each letter is a list of strokes (x1, y1, x2, y2) from its left edge.
LETTER_K = [(2.25, 38, 2.25, 62), (2.25, 51, 11, 38), (4.5, 48, 11.5, 62)]
LETTER_T = [(0, 40.25, 11, 40.25), (5.5, 38, 5.5, 62)]
FAVICON_SIZES = (16, 32)


def favicon_svg(colors: dict[str, str]) -> str:
    strokes = []
    for left, letter in zip((28.5, 44.5, 60.5), (LETTER_K, LETTER_K, LETTER_T), strict=True):
        for x1, y1, x2, y2 in letter:
            strokes.append(f'<line x1="{left + x1}" y1="{y1}" x2="{left + x2}" y2="{y2}"/>')
    lines = "\n    ".join(strokes)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <rect width="100" height="100" fill="{colors["paper"]}"/>
  <g transform="rotate(-8 50 50)" fill="none" stroke="{colors["accent"]}">
    <circle cx="50" cy="50" r="44" stroke-width="6"/>
    <circle cx="50" cy="50" r="35.5" stroke-width="2.5"/>
    <g stroke-width="4.5">
    {lines}
    </g>
  </g>
</svg>
"""


def ico(pngs: list[tuple[int, bytes]]) -> bytes:
    """A .ico file whose frames are PNG images (supported by every current browser)."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = len(header) + 16 * len(pngs)
    entries, data = b"", b""
    for size, png in pngs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        data += png
    return header + entries + data


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
            self.render_favicons(browser, colors)
            browser.close()

    def render_favicons(self, browser, colors: dict[str, str]) -> None:
        icons = OUTPUT / "icons"
        source = favicon_svg(colors)
        (icons / "favicon.svg").write_text(source)
        pngs = []
        for size in FAVICON_SIZES:
            page = browser.new_page(viewport={"width": size, "height": size})
            page.set_content(
                "<!doctype html><style>html, body { margin: 0; } svg { display: block; "
                f"width: 100vw; height: 100vh; }}</style>{source}"
            )
            pngs.append((size, page.screenshot()))
            page.close()
        (icons / "favicon-32.png").write_bytes(dict(pngs)[32])
        (icons / "favicon.ico").write_bytes(ico(pngs))
        for name in ("favicon.svg", "favicon-32.png", "favicon.ico"):
            self.stdout.write(f"assets/icons/{name}")
