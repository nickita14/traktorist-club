"""Organizer shortcuts on the public site, and the home screen manifest.

The admin path must never reach anyone who is not an organizer.
"""

import json
import struct

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from club.models import Game, Season, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season
from live.manifest import APPLE_ICON, ICONS
from live.tests.conftest import live_game

pytestmark = pytest.mark.django_db

ADMIN_PATH = f"/{settings.ADMIN_URL}"


@pytest.fixture
def season():
    """Tour 2026 with two finished games and a player; plus a finished cash game."""
    tour = make_season(2026, SeasonKind.TOUR)
    player = make_player("Альфа", nickname="Трактор")
    for day in (1, 8):
        make_result(make_game(tour, day=day, month=3), player, payout=50, place=1)
    cash = make_season(2026, SeasonKind.CASH)
    make_result(make_game(cash, day=8, month=3), player, payout=50)
    return tour


def public_urls(season):
    game = season.games.order_by("date").first()
    player = game.results.get().player
    return [
        reverse("season", args=[2026, "tour"]),
        reverse("season_games", args=[2026, "tour"]),
        reverse("season", args=[2026, "cash"]),
        reverse("game_detail", args=[game.pk]),
        reverse("player_list"),
        reverse("player_detail", args=[player.slug]),
        reverse("player_games", args=[player.slug]),
        reverse("all_time"),
        reverse("robots_txt"),
    ]


def start_live(season, day, kind=SeasonKind.TOUR, minutes_ago=0):
    target = season if kind == SeasonKind.TOUR else Season.objects.get(year=2026, kind=kind)
    stage = Game.Stage.REBUYS if kind == SeasonKind.TOUR else Game.Stage.CASH
    started = timezone.now() - timezone.timedelta(minutes=minutes_ago)
    return make_game(target, day=day, month=9, live_stage=stage, started_at=started)


def organizer_client(client, organizer):
    client.force_login(organizer)
    return client


class TestEveryoneElse:
    @pytest.mark.parametrize("who", ["anonymous", "guest"])
    def test_no_admin_path_and_no_links(self, client, season, who):
        start_live(season, day=28)
        if who == "guest":
            client.force_login(User.objects.create_user("test-guest"))
        for url in public_urls(season):
            text = client.get(url).text
            assert ADMIN_PATH not in text, url
            assert "Живая игра" not in text and "Админка" not in text
            assert "Идёт турнир" not in text
            assert 'rel="manifest"' not in text

    def test_manifest_is_404(self, client, organizer):
        url = reverse("live:manifest")
        assert client.get(url).status_code == 404
        client.force_login(User.objects.create_user("test-guest", is_staff=True))
        assert client.get(url).status_code == 404


class TestOrganizers:
    @pytest.mark.parametrize("who", ["organizer", "superuser"])
    def test_links_after_the_nav(self, client, season, organizer, admin_user, who):
        client.force_login(organizer if who == "organizer" else admin_user)
        text = client.get(reverse("season", args=[2026, "tour"])).text
        assert f'<a href="{reverse("live:index")}">Живая игра</a>' in text
        assert f'<a href="{reverse("admin:index")}">Админка</a>' in text
        assert text.index("Игроки</a>") < text.index("Живая игра")
        assert f'<link rel="manifest" href="{reverse("live:manifest")}">' in text

    def test_no_strip_without_live_games(self, client, season, organizer):
        text = organizer_client(client, organizer).get(reverse("all_time")).text
        assert "live-strip" not in text

    def test_one_strip_per_live_game(self, client, season, organizer):
        tour = start_live(season, day=28, minutes_ago=30)
        cash = start_live(season, day=28, kind=SeasonKind.CASH, minutes_ago=10)

        text = organizer_client(client, organizer).get(reverse("all_time")).text

        assert text.count('class="live-strip"') == 2
        tour_strip = f'href="{reverse("live:board", args=[tour.pk])}"'
        cash_strip = f'href="{reverse("live:board", args=[cash.pk])}"'
        assert tour_strip in text and cash_strip in text
        # The tour has two finished games before it, so it becomes game 3; oldest start first.
        assert 'Идёт турнир №&nbsp;<span class="font-num">3</span>' in text
        assert 'Идёт кэш-вечер №&nbsp;<span class="font-num">2</span>' in text
        assert text.index(tour_strip) < text.index(cash_strip)

    def test_manifest(self, client, organizer):
        response = organizer_client(client, organizer).get(reverse("live:manifest"))

        assert response["Content-Type"] == "application/manifest+json"
        data = json.loads(response.content)
        assert data["start_url"] == reverse("live:index")
        assert (data["display"], data["lang"], data["scope"]) == ("standalone", "ru", "/")
        assert data["theme_color"] == data["background_color"] == "#f2ede3"  # the paper token
        assert {icon["purpose"] for icon in data["icons"]} == {"any", "maskable"}

    def test_live_pages_link_the_manifest(self, client, organizer):
        game = live_game(SeasonKind.TOUR)
        text = organizer_client(client, organizer).get(reverse("live:board", args=[game.pk])).text
        assert 'rel="manifest"' in text and 'rel="apple-touch-icon"' in text


def png_size(path) -> tuple[int, int]:
    with open(path, "rb") as file:
        header = file.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", header[16:24])


@pytest.mark.parametrize(("path", "size", "purpose"), [*ICONS, APPLE_ICON])
def test_icons_exist_at_their_size(path, size, purpose):
    assert png_size(settings.BASE_DIR / "assets" / path) == (size, size)


class TestQueryCounts:
    def count(self, client, url):
        with CaptureQueriesContext(connection) as queries:
            assert client.get(url).status_code == 200
        return len(queries)

    def test_anonymous_pays_nothing(self, client, season):
        url = reverse("season", args=[2026, "tour"])
        before = self.count(client, url)
        start_live(season, day=28)
        start_live(season, day=28, kind=SeasonKind.CASH)
        assert self.count(client, url) == before

    def test_organizer_pays_one_query_whatever_the_live_games(self, client, season, organizer):
        url = reverse("season", args=[2026, "tour"])
        anonymous = self.count(client, url)
        organizer_client(client, organizer)
        # Knowing who is signed in costs the session and the user; the shortcuts one more.
        assert self.count(client, url) == anonymous + 2 + 1
        start_live(season, day=28)
        start_live(season, day=28, kind=SeasonKind.CASH)
        assert self.count(client, url) == anonymous + 2 + 1
