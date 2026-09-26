import pytest
from django.urls import reverse
from django.utils import timezone

from club.models import SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season

pytestmark = pytest.mark.django_db

NBSP = "\u00a0"
MINUS = "−"
PAST_YEAR = timezone.localdate().year - 1


@pytest.fixture
def season():
    """A finished tour season with three games; every tour buy-in is paid out in full.

    g1 (01.03): Альфа 50->150 (1st), Браво 50->0, Чарли 50->0
    g2 (08.03): Браво 50->100 (1st), Чарли 50->50 (2nd), Альфа 50->0
    g3 (15.03): Альфа 1000->2500 (1st), Браво 1500->0
    Nets: Альфа +1550, Браво -1500, Чарли -50. Buy-ins 2800.
    """
    season = make_season(PAST_YEAR, SeasonKind.TOUR)
    a = make_player("Альфа", nickname="Трактор")
    b, c = make_player("Браво"), make_player("Чарли")
    g1, g2, g3 = (make_game(season, day=day, month=3) for day in (1, 8, 15))
    for game, player, buyin, payout, place in [
        (g1, a, 50, 150, 1),
        (g1, b, 50, 0, None),
        (g1, c, 50, 0, None),
        (g2, b, 50, 100, 1),
        (g2, c, 50, 50, 2),
        (g2, a, 50, 0, None),
        (g3, a, 1000, 2500, 1),
        (g3, b, 1500, 0, None),
    ]:
        make_result(game, player, buyin=buyin, payout=payout, place=place)
    return season


def get_page(client, year):
    return client.get(reverse("tour_standings", args=[year]))


class TestTourStandings:
    def test_renders_standings_in_order(self, client, season):
        response = get_page(client, season.year)

        assert response.status_code == 200
        names = [p.name for p in response.context["standings"]]
        assert names == ["Альфа", "Чарли", "Браво"]

    def test_numbers_are_formatted(self, client, season):
        html = get_page(client, season.year).content.decode()

        assert f"+1{NBSP}550" in html
        assert f'<span class="val-neg">{MINUS}1{NBSP}500</span>' in html
        assert f'<span class="val-neg">{MINUS}50</span>' in html
        # The no-break space is narrowed by CSS on number-only elements (num-run, td.num).
        assert f'закупки на <span class="font-num num-run not-italic">2{NBSP}800</span> лей' in html
        assert "\u202f" not in html

    def test_title_and_meta_line(self, client, season):
        html = get_page(client, season.year).content.decode()

        assert ">Ведомость турниров</h1>" in html
        assert f'Сезон <span class="font-num not-italic">{season.year}</span>' in html

    def test_meta_line_uses_russian_plurals(self, client, season):
        html = get_page(client, season.year).content.decode()

        assert "3</span> игры" in html
        assert "3</span> участника" in html
        # Mobile line under the name.
        assert "3 игры · ITM 2 · 2/0/0" in html
        assert "2 игры · ITM 1 · 0/1/0" in html

    def test_leader_rank_is_circled(self, client, season):
        html = get_page(client, season.year).content.decode()

        assert html.count('<span class="rank-leader">1</span>') == 1

    def test_nickname_is_shown(self, client, season):
        assert "Трактор" in get_page(client, season.year).content.decode()

    def test_past_season_is_closed_and_dated_by_last_game(self, client, season):
        html = get_page(client, season.year).content.decode()

        assert "Сезон закрыт" in html
        assert f">15.03.{season.year}</time>" in html

    def test_current_year_season_is_ongoing(self, client):
        current = make_season(timezone.localdate().year, SeasonKind.TOUR)

        html = get_page(client, current.year).content.decode()

        assert "Сезон идёт" in html
        assert "В этом сезоне ещё не было игр." in html
        assert "По состоянию на" not in html

    def test_year_switcher_marks_current_year(self, client, season):
        make_season(PAST_YEAR - 1, SeasonKind.TOUR)

        html = get_page(client, season.year).content.decode()

        assert f'href="/{PAST_YEAR - 1}/tour/">{PAST_YEAR - 1}</a>' in html
        assert f'href="/{PAST_YEAR}/tour/" aria-current="page">{PAST_YEAR}</a>' in html

    def test_nav_points_to_latest_tour_season(self, client, season):
        make_season(PAST_YEAR - 1, SeasonKind.TOUR)

        response = get_page(client, PAST_YEAR - 1)

        assert response.context["latest_tour_year"] == PAST_YEAR
        assert f'href="/{PAST_YEAR}/tour/" aria-current="true">Турниры</a>' in (
            response.content.decode()
        )

    def test_recent_games_sidebar(self, client, season):
        response = get_page(client, season.year)

        games = list(response.context["recent_games"])
        assert [(g.number, g.players_count, g.buyin_total) for g in games] == [
            (3, 2, 2500),
            (2, 3, 150),
            (1, 3, 150),
        ]
        assert f"2{NBSP}500" in response.content.decode()

    def test_recent_winner_is_nickname_or_name(self, client, season):
        html = get_page(client, season.year).content.decode()
        winners = [
            cell.split("</td>")[0] for cell in html.split('id="recent-title"')[1].split("<td>")[1:]
        ]

        # Альфа goes by "Трактор" (games 3 and 1), Браво has no nickname (game 2).
        assert winners == ["Трактор", "Браво", "Трактор"]

    def test_page_is_noindex(self, client, season):
        assert '<meta name="robots" content="noindex, nofollow">' in (
            get_page(client, season.year).content.decode()
        )

    def test_unknown_year_is_404(self, client, season):
        assert get_page(client, PAST_YEAR - 5).status_code == 404

    def test_cash_season_is_not_served_here(self, client):
        make_season(PAST_YEAR, SeasonKind.CASH)

        assert get_page(client, PAST_YEAR).status_code == 404

    def test_query_count_does_not_grow_with_rows(self, client, season, django_assert_num_queries):
        # Season, year list, standings, recent games and their winners.
        with django_assert_num_queries(5):
            get_page(client, season.year)
