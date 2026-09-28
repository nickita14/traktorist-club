"""The tab icon: links on every kind of page, for everyone, and /favicon.ico."""

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import reverse

from club.tests.factories import make_structure
from live import actions
from live.models import BlindTimer
from live.tests.conftest import key, live_game, organizer  # noqa: F401 - a fixture

pytestmark = pytest.mark.django_db

ICON_FILES = [
    "icons/favicon.svg",
    "icons/favicon-32.png",
    "icons/favicon.ico",
    "icons/live-180.png",
]
LINKS = [
    f'<link rel="icon" href="{static("icons/favicon.svg")}" type="image/svg+xml" sizes="any">',
    f'<link rel="icon" href="{static("icons/favicon-32.png")}" type="image/png" sizes="32x32">',
    f'<link rel="apple-touch-icon" href="{static("icons/live-180.png")}">',
]
ADMIN_PATH = f"/{settings.ADMIN_URL}"


def head(html: str) -> str:
    return html.split("</head>")[0]


@pytest.mark.parametrize("path", ICON_FILES)
def test_icon_files_exist(path):
    assert finders.find(path)


def test_svg_needs_no_font():
    svg = open(finders.find("icons/favicon.svg")).read()
    assert "<text" not in svg and "font" not in svg


def test_ico_holds_16_and_32_pixel_frames():
    data = open(finders.find("icons/favicon.ico"), "rb").read()
    assert data[:4] == b"\x00\x00\x01\x00"
    assert int.from_bytes(data[4:6], "little") == 2
    assert (data[6], data[22]) == (16, 32)


def test_favicon_ico_redirects_to_the_static_file(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 302
    assert response["Location"] == static("icons/favicon.ico")
    assert "max-age=86400" in response["Cache-Control"]
    assert client.post("/favicon.ico").status_code == 405


@pytest.mark.parametrize("name", ["player_list", "all_time"])
def test_anonymous_public_pages_have_the_icons(client, name):
    page = head(client.get(reverse(name)).text)
    for link in LINKS:
        assert link in page
    assert 'rel="manifest"' not in page  # still organizers only
    assert ADMIN_PATH not in page


def test_organizers_get_the_manifest_too(client, organizer):  # noqa: F811
    client.force_login(organizer)
    page = head(client.get(reverse("player_list")).text)
    assert LINKS[0] in page and 'rel="manifest"' in page


def test_live_screens_have_the_icons(client, organizer):  # noqa: F811
    client.force_login(organizer)
    page = head(client.get(reverse("live:index")).text)
    for link in LINKS:
        assert link in page


def test_secret_display_link_has_the_icons_and_no_admin_path(client):
    game = live_game("tour")
    actions.start_timer(game.pk, key(), None, make_structure().pk)
    token = BlindTimer.objects.get(game=game).display_token
    page = client.get(reverse("tablo_public", args=[token])).text
    for link in LINKS:
        assert link in head(page)
    assert ADMIN_PATH not in page


def test_admin_login_has_the_icons(client):
    page = head(client.get(reverse("admin:login")).text)
    assert f'href="{static("icons/favicon.svg")}"' in page
    assert 'type="image/svg+xml"' in page
    assert f'href="{static("icons/favicon-32.png")}"' in page
