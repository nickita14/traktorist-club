"""Sortable tables: parsing, ordering, ranks, links and the leader circle of the standings, and
the players list."""

import pytest
from django.urls import reverse
from django.utils.http import urlencode

from club import stats
from club.models import SeasonKind
from club.sorting import CASH_STANDINGS, PLAYERS, TOUR_STANDINGS, Sort, parse_sort, sort_url
from club.tests.conftest import PAST_YEAR
from club.tests.factories import make_game, make_player, make_result

LEADER = '<span class="rank-leader">1</span>'

TOUR_KEYS = tuple(TOUR_STANDINGS.fields)
CASH_KEYS = tuple(CASH_STANDINGS.fields)
FIELDS = TOUR_STANDINGS.fields | CASH_STANDINGS.fields
DEFAULT = TOUR_STANDINGS.default_sort


def tour(key: str, descending: bool = True) -> Sort:
    return Sort(key, descending, TOUR_STANDINGS)


def cash(key: str, descending: bool = True) -> Sort:
    return Sort(key, descending, CASH_STANDINGS)


def table(is_cash: bool):
    return CASH_STANDINGS if is_cash else TOUR_STANDINGS


class TestParse:
    @pytest.mark.parametrize("key", TOUR_KEYS)
    def test_tour_keys(self, key):
        assert parse_sort({"sort": key}, TOUR_STANDINGS) == tour(key)
        assert parse_sort({"sort": key, "dir": "asc"}, TOUR_STANDINGS) == tour(key, False)

    @pytest.mark.parametrize("key", CASH_KEYS)
    def test_cash_keys(self, key):
        assert parse_sort({"sort": key, "dir": "asc"}, CASH_STANDINGS) == cash(key, False)

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
        assert parse_sort(params, table(cash)) == table(cash).default_sort
        assert table(cash).default_sort.key == "net"

    def test_any_other_direction_is_the_starting_one(self):
        assert parse_sort({"sort": "games", "dir": "sideways"}, TOUR_STANDINGS) == tour("games")
        assert parse_sort({"sort": "games", "dir": "desc"}, TOUR_STANDINGS) == tour("games")

    def test_toggle(self):
        assert DEFAULT.toggled("net") == tour("net", False)
        assert tour("net", False).toggled("net") == DEFAULT
        assert tour("net", False).toggled("games") == tour("games", True)

    def test_default_needs_no_parameters(self):
        assert (DEFAULT.params, DEFAULT.query) == ({}, "")
        assert tour("net", False).query == "dir=asc"
        assert tour("games", False).query == "sort=games&dir=asc"

    def test_url_keeps_other_parameters(self):
        params = {"kind": "tour", "sort": "games", "dir": "asc"}
        assert sort_url("/all-time/", params, tour("itm")) == "/all-time/?kind=tour&sort=itm"
        assert sort_url("/all-time/", {"sort": "games"}, DEFAULT) == "/all-time/"


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
        assert response.context["sort"] == tour(key, direction == "desc")
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
        sort = season_page(client, SeasonKind.CASH, sort="itm").context["sort"]
        assert sort == CASH_STANDINGS.default_sort

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


class TestPlayersParse:
    def test_default_is_name_from_a(self):
        assert parse_sort({}, PLAYERS) == Sort("name", False, PLAYERS)
        assert PLAYERS.default_sort.params == {}

    def test_text_columns_start_from_a_numbers_from_the_highest(self):
        assert parse_sort({"sort": "nick"}, PLAYERS) == Sort("nick", False, PLAYERS)
        assert parse_sort({"sort": "games"}, PLAYERS) == Sort("games", True, PLAYERS)
        assert parse_sort({"sort": "net", "dir": "asc"}, PLAYERS) == Sort("net", False, PLAYERS)

    def test_reverse_of_a_text_column_says_desc(self):
        reverse_names = Sort("name", True, PLAYERS)
        assert reverse_names.query == "dir=desc"
        assert parse_sort({"dir": "desc"}, PLAYERS) == reverse_names
        assert Sort("nick", True, PLAYERS).query == "sort=nick&dir=desc"

    @pytest.mark.parametrize(
        "params",
        [{"sort": "bogus"}, {"sort": "itm"}, {"sort": "NAME"}, {"sort": ""}, {"dir": "up"}],
    )
    def test_bad_values_fall_back_to_names(self, params):
        assert parse_sort(params, PLAYERS) == PLAYERS.default_sort


def players_page(client, **params):
    return client.get(reverse("player_list") + (f"?{urlencode(params)}" if params else ""))


@pytest.mark.django_db
class TestPlayersPage:
    """All-time: Альфа (Трактор) 6 games +190, Браво 6 −230, Дельта 1 +10, Чарли 2 0."""

    @pytest.mark.parametrize(
        ("params", "names", "label"),
        [
            ({}, ["Альфа", "Браво", "Дельта", "Чарли"], "по алфавиту"),
            (
                {"dir": "desc"},
                ["Чарли", "Дельта", "Браво", "Альфа"],
                "по алфавиту, в обратном порядке",
            ),
            # Without a nickname: last in both directions, by name.
            ({"sort": "nick"}, ["Альфа", "Браво", "Дельта", "Чарли"], "по нику"),
            ({"sort": "nick", "dir": "desc"}, ["Альфа", "Браво", "Дельта", "Чарли"], None),
            # Equal games: by name.
            ({"sort": "games"}, ["Альфа", "Браво", "Чарли", "Дельта"], "по числу игр"),
            ({"sort": "games", "dir": "asc"}, ["Дельта", "Чарли", "Альфа", "Браво"], None),
            ({"sort": "net"}, ["Альфа", "Дельта", "Чарли", "Браво"], "по итогу"),
            ({"sort": "net", "dir": "asc"}, ["Браво", "Чарли", "Дельта", "Альфа"], None),
        ],
    )
    def test_orders(self, client, club, params, names, label):
        response = players_page(client, **params)
        assert response.status_code == 200
        assert [player.name for player in response.context["players"]] == names
        if label:
            assert f"· {label}\n" in response.text

    def test_default_headers(self, client, club):
        html = players_page(client).text
        base = reverse("player_list")
        assert '<th scope="col" aria-sort="ascending"><a class="sort-link sort-asc"' in html
        assert html.count("aria-sort=") == 1
        assert header_link(html, "Имя") == f"{base}?dir=desc"  # the active column flips
        assert header_link(html, "Ник") == f"{base}?sort=nick"
        assert header_link(html, "Игр") == f"{base}?sort=games"
        assert header_link(html, "Итог, лей") == f"{base}?sort=net"

    def test_active_numeric_header(self, client, club):
        html = players_page(client, sort="games").text
        base = reverse("player_list")
        assert '<th scope="col" class="num" aria-sort="descending">' in html
        assert 'class="sort-link sort-desc"' in html and 'aria-current="true">Игр</a>' in html
        assert header_link(html, "Игр") == f"{base}?sort=games&dir=asc"
        assert header_link(html, "Имя") == base

    def test_mobile_links(self, client, club):
        html = players_page(client, sort="net").text
        row = html[html.index('class="sort-links') : html.index('class="ledger-scroll')]
        assert row.startswith('class="sort-links md:hidden">Сортировать: ')
        for label in ("имя", "ник", "игры"):
            assert f">{label}</a>" in row
        assert 'aria-current="true">итог</a>' in row

    @pytest.mark.parametrize(
        "params", [{"sort": "bogus"}, {"sort": "net", "dir": "up"}, {"sort": "<script>"}]
    )
    def test_bad_values_are_not_an_error(self, client, club, params):
        response = players_page(client, **params)
        assert response.status_code == 200
        assert "<script>" not in response.text

    def test_query_count_does_not_depend_on_the_order(
        self, client, club, django_assert_num_queries
    ):
        for params in [{}, {"sort": "nick", "dir": "desc"}, {"sort": "net"}, {"sort": "x"}]:
            with django_assert_num_queries(2):
                players_page(client, **params)
