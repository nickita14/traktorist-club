"""No currency word on the public site and the live screens (the admin keeps units in labels).

Every public page and every live screen is rendered on invented data, and every template under
``templates/club/`` and ``templates/live/`` must have been rendered by at least one of them, so
a new template cannot slip past the check.
"""

import re

import pytest
from django.conf import settings
from django.contrib.auth.models import Group, User
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from club.models import Game, Season, SeasonKind
from club.tests.conftest import PAST_YEAR
from club.tests.factories import make_game, make_player, make_result, make_structure
from live import actions
from live.models import BlindTimer
from live.tests.conftest import key, lineup_game, live_game, seat

pytestmark = pytest.mark.django_db

# The word in any case, as a whole word: "ролей" or "полей" do not match.
CURRENCY = re.compile(r"(?<![а-яё])ле(?:й|я|ях)(?![а-яё])", re.IGNORECASE)

TEMPLATE_DIRS = ("club", "live")

# Templates that only a POST response renders; they are checked by rendering them directly.
POST_ONLY = {"live/_toast.html"}


def all_templates() -> set[str]:
    root = settings.SRC_DIR / "templates"
    return {
        path.relative_to(root).as_posix()
        for folder in TEMPLATE_DIRS
        for path in (root / folder).rglob("*.html")
    }


class Pages:
    """Fetches pages, fails on the word, and remembers which templates were rendered."""

    def __init__(self, client):
        self.client = client
        self.rendered: set[str] = set()

    def get(self, url: str, data: dict | None = None) -> str:
        response = self.client.get(url, data or {}, follow=url == reverse("home"))
        assert response.status_code == 200, (url, response.status_code)
        self.rendered |= {t.name for t in response.templates if t.name}
        html = response.content.decode()
        found = CURRENCY.search(html)
        assert found is None, (url, html[max(found.start() - 80, 0) : found.end() + 20])
        return html


@pytest.fixture
def organizer_client(client, db):
    user = User.objects.create_user("test-organizer", is_staff=True)
    user.groups.add(Group.objects.get(name="Organizer"))
    client.force_login(user)
    return client


def public_urls(club) -> list[str]:
    urls = [reverse("home"), reverse("player_list"), reverse("all_time"), reverse("honors")]
    for season in club["seasons"].values():
        urls += [
            reverse("season", args=[season.year, season.kind]),
            reverse("season_games", args=[season.year, season.kind]),
        ]
    urls += [reverse("game_detail", args=[game.pk]) for game in club["games"].values()]
    # The year report of PAST_YEAR (closed by the 1 January fallback) and its players' pages.
    urls.append(reverse("year_report", args=[PAST_YEAR]))
    for name in "ABCD":
        urls.append(reverse("year_report_player", args=[PAST_YEAR, club["players"][name].slug]))
    for player in club["players"].values():
        for suffix in ("", "?year=all", f"?year={PAST_YEAR}"):
            urls.append(reverse("player_detail", args=[player.slug]) + suffix)
            urls.append(reverse("player_games", args=[player.slug]) + suffix)
    return urls


def test_public_pages(client, club, award_rules):
    pages = Pages(client)
    for url in public_urls(club):
        pages.get(url)
    pages.get(reverse("login"))


def test_public_and_live_pages(organizer_client, client, club, award_rules):
    """The public pages again (now with the organizer shortcuts), then every live screen."""
    pages = Pages(organizer_client)
    for url in public_urls(club):
        pages.get(url)
    live = lambda name, *args: reverse(f"live:{name}", args=args)  # noqa: E731

    # Lineup: seated players and the search.
    lineup = lineup_game(SeasonKind.TOUR)
    seat(lineup, "Альфа-1", "Браво-1")
    make_player("Чарли-1")
    pages.get(live("board", lineup.pk))
    pages.get(live("board", lineup.pk), {"q": "чарли"})
    pages.get(live("seat_new", lineup.pk), {"q": "Новенький"})
    pages.get(live("cancel", lineup.pk))

    # Collection: unpaid entries, the begin sheet and a player's sheet.
    Game.objects.filter(pk=lineup.pk).update(live_stage=Game.Stage.COLLECT)
    pages.get(live("board", lineup.pk))
    pages.get(live("begin", lineup.pk))

    # A tournament in play with a blind timer, a paid player and an unpaid late entry.
    tour = make_game(
        lineup.season, day=29, month=9, live_stage=Game.Stage.REBUYS, started_at=timezone.now()
    )
    paid = seat(tour, "Дельта-2", "Эхо-2", "Фокстрот-2")
    actions.set_paid(tour.pk, key(), None, paid[0].pk, True)
    actions.start_timer(tour.pk, key(), None, make_structure().pk)
    late = make_player("Гольф-2")
    pages.get(live("late", tour.pk, late.pk))
    (unpaid,) = seat(tour, "Хотел-2")
    pages.get(live("board", tour.pk))
    pages.get(live("player", tour.pk, unpaid.pk))
    pages.get(live("player", tour.pk, paid[0].pk))
    pages.get(live("bust", tour.pk, paid[1].pk))
    pages.get(live("seating", tour.pk))
    pages.get(live("blinds", tour.pk))
    pages.get(live("tablo", tour.pk))
    token = BlindTimer.objects.get(game=tour).display_token
    pages.get(reverse("tablo_public", args=[token]))
    for stage in (Game.Stage.ADDON, Game.Stage.FINAL):
        Game.objects.filter(pk=tour.pk).update(live_stage=stage)
        pages.get(live("board", tour.pk))
    pages.get(live("results", tour.pk))

    # Cash: the exit sheet and a chip stack worth a fractional amount (11 230 chips = 112,30).
    cash = live_game(SeasonKind.CASH)
    alpha, _ = seat(cash, "Индия-3", "Джульетта-3")
    pages.get(live("board", cash.pk))
    pages.get(live("exit", cash.pk, alpha.pk))
    preview = pages.get(live("exit_preview", cash.pk, alpha.pk), {"chips": "11230", "paid": "110"})
    assert "112,30" in preview
    pages.get(live("close", cash.pk))

    # This year's report is preliminary: organizers only, with its stamp and the close link.
    this_year = timezone.localdate().year
    season, _ = Season.objects.get_or_create(year=this_year, kind=SeasonKind.CASH)
    evening = make_game(season, day=1, month=1)
    make_result(evening, club["players"]["A"], buyin=50, payout=60)
    pages.get(reverse("year_report", args=[this_year]))
    pages.get(reverse("year_report_player", args=[this_year, club["players"]["A"].slug]))

    pages.get(live("index"))
    pages.get(live("start"))

    # Templates only a POST renders, rendered directly.
    for name in POST_ONLY:
        html = render_to_string(name, {"toast": "Пока вы смотрели, этап сменился."})
        assert CURRENCY.search(html) is None, name
        pages.rendered.add(name)

    client.logout()
    pages.client = client
    pages.get(reverse("login"))

    missing = all_templates() - pages.rendered
    assert not missing, sorted(missing)


@pytest.mark.parametrize("text", ["лей", "Итог, ЛЕЙ", "50 лея", "в леях", "(лей)"])
def test_pattern_finds_the_word(text):
    assert CURRENCY.search(text)


@pytest.mark.parametrize(
    "text", ["ролей", "полей", "долей", "клей", "лейка", "Лейла", "chips_per_lei"]
)
def test_pattern_ignores_other_words(text):
    assert CURRENCY.search(text) is None
