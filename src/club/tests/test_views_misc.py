import pytest
from django.urls import reverse

from club.tests.conftest import PAST_YEAR

pytestmark = pytest.mark.django_db

MINUS = "−"


def get_all_time(client, **params):
    return client.get(reverse("all_time"), params)


class TestAllTime:
    def test_everything_by_default(self, client, club):
        response = get_all_time(client)
        html = response.content.decode()

        assert response.status_code == 200
        rows = [(p.rank, p.name, p.net) for p in response.context["standings"]]
        assert rows == [(1, "Альфа", 190), (2, "Дельта", 10), (3, "Чарли", 0), (4, "Браво", -230)]
        assert "Форма № 4 · Сводная за всё время" in html
        assert ">Ведомость за всё время</h1>" in html
        assert ">ITM</a></th>" in html
        assert '<a href="/all-time/" aria-current="page">Все</a>' in html

    def test_meta_line_totals(self, client, club):
        response = get_all_time(client)

        assert response.context["totals"] == {
            "games_count": 6,
            "players_count": 4,
            "buyin_total": 850,
        }

    def test_tour_filter(self, client, club):
        response = get_all_time(client, kind="tour")

        rows = [(p.name, p.games_played, p.itm) for p in response.context["standings"]]
        assert rows == [("Альфа", 4, 4), ("Чарли", 1, 0), ("Браво", 4, 0)]
        assert 'href="/all-time/?kind=tour" aria-current="page">Турниры</a>' in (
            response.content.decode()
        )

    def test_cash_filter_hides_places(self, client, club):
        response = get_all_time(client, kind="cash")
        html = response.content.decode()

        assert [p.name for p in response.context["standings"]] == [
            "Чарли",
            "Дельта",
            "Альфа",
            "Браво",
        ]
        assert ">ITM</a></th>" not in html
        assert "Первых мест" not in html
        assert ">Закупки</a></th>" in html

    def test_zero_itm_is_a_faint_dot(self, client, club):
        html = get_all_time(client, kind="tour").content.decode()
        body = html.split("<tbody>")[1].split("</tbody>")[0]
        # Чарли and Браво never finished in the money.
        rows = body.split("<tr>")[2:]

        for row in rows:
            assert row.split("</td>")[3].endswith('<span class="sr-only">0</span></span>')
        assert '<span class="val-zero">0</span>' not in body

    def test_unknown_kind_is_404(self, client, club):
        assert get_all_time(client, kind="poker").status_code == 404

    def test_empty_site(self, client):
        html = get_all_time(client).content.decode()

        assert "Игр пока не было." in html

    def test_nav_marks_all_time(self, client, club):
        html = get_all_time(client).content.decode()

        assert 'href="/all-time/" aria-current="true">За всё время</a>' in html

    def test_query_count(self, client, club, django_assert_num_queries):
        # Standings, totals, nav years.
        with django_assert_num_queries(3):
            get_all_time(client)


class TestNav:
    def test_every_section_is_a_link(self, client, club):
        html = client.get(reverse("player_list")).content.decode()

        assert f'<a href="/{PAST_YEAR}/tour/">Турниры</a>' in html
        assert f'<a href="/{PAST_YEAR}/cash/">Кэш</a>' in html
        assert '<a href="/all-time/">За всё время</a>' in html

    def test_missing_kind_stays_a_placeholder(self, client):
        html = client.get(reverse("player_list")).content.decode()

        assert "<span>Турниры</span>" in html
        assert "<span>Кэш</span>" in html


class TestIndexing:
    def test_noindex_and_robots_by_default(self, client, club):
        page = client.get(reverse("player_list")).content.decode()
        robots = client.get("/robots.txt")

        assert '<meta name="robots" content="noindex, nofollow">' in page
        assert robots.status_code == 200
        assert robots["Content-Type"] == "text/plain"
        assert robots.content.decode() == "User-agent: *\nDisallow: /\n"

    def test_setting_enables_indexing(self, client, club, settings):
        settings.SITE_INDEXING = True

        page = client.get(reverse("player_list")).content.decode()
        robots = client.get("/robots.txt").content.decode()

        assert 'name="robots"' not in page
        assert robots == "User-agent: *\nAllow: /\nDisallow: /tablo/\n"
