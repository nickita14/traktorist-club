"""Sortable standings: parsing, ordering, ranks, links and the leader circle."""

import pytest
from django.urls import reverse
from django.utils.http import urlencode

from club import stats
from club.models import SeasonKind
from club.sorting import CASH_KEYS, FIELDS, TOUR_KEYS, Sort, parse_sort, sort_url
from club.tests.conftest import PAST_YEAR
from club.tests.factories import make_game, make_player, make_result

LEADER = '<span class="rank-leader">1</span>'


class TestParse:
    @pytest.mark.parametrize("key", TOUR_KEYS)
    def test_tour_keys(self, key):
        assert parse_sort({"sort": key}, cash=False) == Sort(key, True)
        assert parse_sort({"sort": key, "dir": "asc"}, cash=False) == Sort(key, False)

    @pytest.mark.parametrize("key", CASH_KEYS)
    def test_cash_keys(self, key):
        assert parse_sort({"sort": key, "dir": "asc"}, cash=True) == Sort(key, False)

    @pytest.mark.parametrize(
        ("params", "cash"),
        [
            ({}, False),
            ({"sort": "bogus"}, False),
            ({"sort": ""}, False),
            ({"sort": "itm"}, True),  # not a column of the cash table
            ({"sort": "buyin"}, False),  # nor this of the tour one
            ({"sort": "NET"}, False),
            ({"dir": "asc", "sort": "nope"}, False),
        ],
    )
    def test_unknown_values_fall_back_to_the_default(self, params, cash):
        assert parse_sort(params, cash=cash) == Sort()

    def test_any_other_direction_is_descending(self):
        assert parse_sort({"sort": "games", "dir": "sideways"}, cash=False) == Sort("games")

    def test_toggle(self):
        assert Sort().toggled("net") == Sort("net", False)
        assert Sort("net", False).toggled("net") == Sort()
        assert Sort("net", False).toggled("games") == Sort("games", True)

    def test_default_needs_no_parameters(self):
        assert (Sort().params, Sort().query) == ({}, "")
        assert Sort("net", False).query == "dir=asc"
        assert Sort("games", False).query == "sort=games&dir=asc"

    def test_url_keeps_other_parameters(self):
        params = {"kind": "tour", "sort": "games", "dir": "asc"}
        assert sort_url("/all-time/", params, Sort("itm")) == "/all-time/?kind=tour&sort=itm"
        assert sort_url("/all-time/", {"sort": "games"}, Sort()) == "/all-time/"


def expected_order(players, key, descending):
    """Sort the rows by hand: the column, then net highest first, then name."""
    field = FIELDS[key]

    def order(player):
        value = getattr(player, field)
        return (-value if descending else value, -player.net, player.name, player.nickname)

    return [player.pk for player in sorted(players, key=order)]


@pytest.mark.django_db
class TestOrdering:
    @pytest.mark.parametrize("descending", [True, False])
    @pytest.mark.parametrize("key", TOUR_KEYS)
    def test_all_time_every_tour_key(self, club, key, descending):
        rows = list(stats.all_time_standings(order_by=FIELDS[key], descending=descending))
        assert [row.pk for row in rows] == expected_order(rows, key, descending)

    @pytest.mark.parametrize("descending", [True, False])
    @pytest.mark.parametrize("key", CASH_KEYS)
    def test_cash_season_every_cash_key(self, club, key, descending):
        season = club["seasons"]["cash"]
        rows = list(stats.season_standings(season, order_by=FIELDS[key], descending=descending))
        assert [row.pk for row in rows] == expected_order(rows, key, descending)

    def test_ties_share_a_rank_and_fall_back_to_net_then_name(self, club):
        # Tour season: Альфа 3 games (+160), Браво 3 games (−150), Чарли 1 game (−50).
        rows = stats.season_standings(club["seasons"]["tour"], order_by="games_played")
        assert [(row.name, row.rank) for row in rows] == [
            ("Альфа", 1),
            ("Браво", 1),
            ("Чарли", 3),
        ]

    def test_equal_values_ascending_share_a_rank_too(self, club):
        rows = stats.season_standings(
            club["seasons"]["tour"], order_by="first_places", descending=False
        )
        assert [(row.name, row.rank) for row in rows] == [
            ("Чарли", 1),
            ("Браво", 1),
            ("Альфа", 3),
        ]


def season_page(client, kind=SeasonKind.TOUR, **params):
    url = reverse("season", args=[PAST_YEAR, kind])
    return client.get(url + (f"?{urlencode(params)}" if params else ""))


def header_link(html, label):
    """The href of the sortable header whose text is ``label``."""
    end = html.index(f">{label}</a></th>")
    start = html.rindex('href="', 0, end) + len('href="')
    return html[start : html.index('"', start)].replace("&amp;", "&")


@pytest.mark.django_db
class TestSeasonPage:
    @pytest.mark.parametrize("direction", ["desc", "asc"])
    @pytest.mark.parametrize("key", TOUR_KEYS)
    def test_every_tour_key(self, client, club, key, direction):
        response = season_page(client, sort=key, dir=direction)
        assert response.status_code == 200
        assert response.context["sort"] == Sort(key, direction == "desc")
        rows = list(response.context["standings"])
        assert [row.pk for row in rows] == expected_order(rows, key, direction == "desc")

    @pytest.mark.parametrize("direction", ["desc", "asc"])
    @pytest.mark.parametrize("key", CASH_KEYS)
    def test_every_cash_key(self, client, club, key, direction):
        response = season_page(client, SeasonKind.CASH, sort=key, dir=direction)
        assert response.status_code == 200
        rows = list(response.context["standings"])
        assert [row.pk for row in rows] == expected_order(rows, key, direction == "desc")

    @pytest.mark.parametrize(
        "params", [{"sort": "bogus"}, {"sort": "games", "dir": "up"}, {"sort": "<script>"}]
    )
    def test_bad_values_are_not_an_error(self, client, club, params):
        response = season_page(client, **params)
        assert response.status_code == 200
        assert "<script>" not in response.text

    def test_cash_table_ignores_a_tour_key(self, client, club):
        assert season_page(client, SeasonKind.CASH, sort="itm").context["sort"] == Sort()

    def test_header_links(self, client, club):
        html = season_page(client).text
        base = reverse("season", args=[PAST_YEAR, "tour"])
        assert header_link(html, "Игр") == f"{base}?sort=games"
        assert header_link(html, "Итог, лей") == f"{base}?dir=asc"  # the active column flips
        html = season_page(client, sort="games").text
        assert header_link(html, "Игр") == f"{base}?sort=games&dir=asc"
        assert header_link(html, "Итог, лей") == base

    def test_active_header(self, client, club):
        html = season_page(client, sort="itm", dir="asc").text
        assert '<th scope="col" class="num max-md:hidden" aria-sort="ascending">' in html
        assert 'class="sort-link sort-asc"' in html and 'aria-current="true">ITM</a>' in html
        assert html.count("aria-sort=") == 1

    def test_default_marks_net(self, client, club):
        html = season_page(client).text
        assert '<th scope="col" class="num" aria-sort="descending">' in html

    def test_year_switcher_keeps_the_order(self, client, club):
        html = season_page(client, sort="games", dir="asc").text
        old_url = reverse("season", args=[PAST_YEAR - 1, "tour"])
        assert f'href="{old_url}?sort=games&amp;dir=asc"' in html

    def test_mobile_links(self, client, club):
        html = season_page(client).text
        row = html[html.index('class="sort-links') : html.index('class="ledger-scroll')]
        assert ">итог</a>" in row and ">игры</a>" in row and ">ITM</a>" in row
        cash = season_page(client, SeasonKind.CASH).text
        row = cash[cash.index('class="sort-links') : cash.index('class="ledger-scroll')]
        assert ">закупки</a>" in row and ">выплаты</a>" in row and "ITM" not in row


@pytest.mark.django_db
class TestLeaderCircle:
    def test_only_in_the_default_order(self, client, club):
        assert season_page(client).text.count(LEADER) == 1
        assert LEADER not in season_page(client, sort="games").text
        assert LEADER not in season_page(client, dir="asc").text

    def test_not_for_the_first_row_of_another_order(self, client, club):
        # By buy-ins the first row is not the season leader, and nothing circles it.
        html = season_page(client, SeasonKind.CASH, sort="buyin").text
        assert LEADER not in html


@pytest.mark.django_db
class TestAllTimePage:
    def get(self, client, **params):
        return client.get(reverse("all_time") + (f"?{urlencode(params)}" if params else ""))

    def test_sorting_keeps_the_kind_filter(self, client, club):
        html = self.get(client, kind="tour").text
        assert header_link(html, "Игр") == f"{reverse('all_time')}?kind=tour&sort=games"

    def test_kind_filter_keeps_the_sort(self, client, club):
        html = self.get(client, sort="games", dir="asc").text
        base = reverse("all_time")
        assert f'href="{base}?kind=tour&amp;sort=games&amp;dir=asc"' in html
        assert f'href="{base}?kind=cash&amp;sort=games&amp;dir=asc"' in html
        assert f'href="{base}?sort=games&amp;dir=asc" aria-current="page"' in html

    def test_kind_filter_drops_a_column_the_target_lacks(self, client, club):
        html = self.get(client, kind="tour", sort="itm").text
        assert f'href="{reverse("all_time")}?kind=cash"' in html
        assert f'href="{reverse("all_time")}?sort=itm"' in html

    @pytest.mark.parametrize("key", TOUR_KEYS)
    def test_every_key_with_the_kind(self, client, club, key):
        response = self.get(client, kind="tour", sort=key, dir="asc")
        rows = list(response.context["standings"])
        assert [row.pk for row in rows] == expected_order(rows, key, False)

    def test_unknown_kind_is_still_a_404(self, client, club):
        assert self.get(client, kind="poker", sort="games").status_code == 404


@pytest.mark.django_db
class TestQueryCounts:
    """Sorting changes the ORDER BY and the window, not the number of queries."""

    def test_season(self, client, club, django_assert_num_queries):
        for params in [{}, {"sort": "games", "dir": "asc"}, {"sort": "bogus"}]:
            with django_assert_num_queries(6):
                season_page(client, **params)

    def test_all_time(self, client, club, django_assert_num_queries):
        for params in [{}, {"kind": "cash", "sort": "payout"}, {"sort": "third", "dir": "asc"}]:
            with django_assert_num_queries(3):
                client.get(reverse("all_time") + f"?{urlencode(params)}")

    def test_more_players_same_count(self, client, club, django_assert_num_queries):
        season = club["seasons"]["tour"]
        game = make_game(season, day=29, month=3)
        for i in range(10):
            make_result(game, make_player(f"Лишний {i}"), buyin=50)
        with django_assert_num_queries(6):
            season_page(client, sort="itm")
