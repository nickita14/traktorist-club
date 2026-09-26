import pytest

from club import stats
from club.models import Game, Player, Result, Season, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season

pytestmark = pytest.mark.django_db

TOTAL_FIELDS = [
    "games_played",
    "buyin_total",
    "payout_total",
    "net",
    "itm",
    "first_places",
    "second_places",
    "third_places",
]


def totals(obj):
    return {field: getattr(obj, field) for field in TOTAL_FIELDS}


@pytest.fixture
def tour():
    """Tour 2025, paid_places=3, three games; expected totals are worked out by hand.

    g1: A 50->120 (1st), B 50->80 (2nd), C 50->50 (3rd), D 100->0 (rebuy, no place)
    g2: B 50->120 (1st), C 50->80 (2nd), A 50->0, D 50->0
    g3: D 50->100 (1st), A 50->50 (2nd), C 50->50 (2nd, tie), B 50->0 (4th)
    """
    season = make_season(2025, SeasonKind.TOUR)
    a, b, c, d = (make_player(name) for name in ["Альфа", "Браво", "Чарли", "Дельта"])
    g1, g2, g3 = (make_game(season, day=day) for day in (1, 2, 3))
    for game, player, buyin, payout, place in [
        (g1, a, 50, 120, 1),
        (g1, b, 50, 80, 2),
        (g1, c, 50, 50, 3),
        (g1, d, 100, 0, None),
        (g2, b, 50, 120, 1),
        (g2, c, 50, 80, 2),
        (g2, a, 50, 0, None),
        (g2, d, 50, 0, None),
        (g3, d, 50, 100, 1),
        (g3, a, 50, 50, 2),
        (g3, c, 50, 50, 2),
        (g3, b, 50, 0, 4),
    ]:
        make_result(game, player, buyin=buyin, payout=payout, place=place)
    return {"season": season, "players": {"A": a, "B": b, "C": c, "D": d}}


class TestSeasonStandings:
    def test_totals_and_order(self, tour):
        rows = list(stats.season_standings(tour["season"]))

        assert [p.name for p in rows] == ["Браво", "Чарли", "Альфа", "Дельта"]
        by_name = {p.name: totals(p) for p in rows}
        assert by_name["Альфа"] == {
            "games_played": 3,
            "buyin_total": 150,
            "payout_total": 170,
            "net": 20,
            "itm": 2,
            "first_places": 1,
            "second_places": 1,
            "third_places": 0,
        }
        assert by_name["Браво"] == {
            "games_played": 3,
            "buyin_total": 150,
            "payout_total": 200,
            "net": 50,
            "itm": 2,  # 4th place is outside paid_places=3
            "first_places": 1,
            "second_places": 1,
            "third_places": 0,
        }
        assert by_name["Чарли"] == {
            "games_played": 3,
            "buyin_total": 150,
            "payout_total": 180,
            "net": 30,
            "itm": 3,
            "first_places": 0,
            "second_places": 2,  # includes the tie in g3
            "third_places": 1,
        }
        assert by_name["Дельта"] == {
            "games_played": 3,
            "buyin_total": 200,
            "payout_total": 100,
            "net": -100,
            "itm": 1,
            "first_places": 1,
            "second_places": 0,
            "third_places": 0,
        }

    def test_tied_place_counts_for_both_players(self):
        season = make_season()
        game = make_game(season)
        make_result(game, make_player("Первый"), payout=100, place=1)
        make_result(game, make_player("Ничья А"), payout=25, place=2)
        make_result(game, make_player("Ничья Б"), payout=25, place=2)

        rows = {p.name: p for p in stats.season_standings(season)}

        for name in ("Ничья А", "Ничья Б"):
            assert (rows[name].second_places, rows[name].itm) == (1, 1)
        assert rows["Первый"].second_places == 0

    def test_zero_payout_player(self):
        season = make_season()
        game = make_game(season)
        make_result(game, make_player("Победитель"), buyin=50, payout=50, place=1)
        make_result(game, make_player("Ноль"), buyin=50, payout=0)

        row = stats.season_standings(season).get(name="Ноль")

        assert totals(row) == {
            "games_played": 1,
            "buyin_total": 50,
            "payout_total": 0,
            "net": -50,
            "itm": 0,
            "first_places": 0,
            "second_places": 0,
            "third_places": 0,
        }

    def test_player_with_no_games(self, tour):
        idle = make_player("Без Игр")

        assert idle not in stats.season_standings(tour["season"])
        row = stats.annotate_player_totals(Player.objects.all()).get(pk=idle.pk)
        assert set(totals(row).values()) == {0}

    def test_empty_season(self, tour):
        empty = make_season(2026, SeasonKind.TOUR)

        assert list(stats.season_standings(empty)) == []
        row = stats.annotate_season_totals(Season.objects.filter(pk=empty.pk)).get()
        assert (row.games_count, row.players_count) == (0, 0)

    def test_other_seasons_do_not_leak(self, tour):
        a = tour["players"]["A"]
        cash = make_season(2025, SeasonKind.CASH)
        make_result(make_game(cash), a, buyin=100, payout=500, chips_out=50000)

        row = stats.season_standings(tour["season"]).get(pk=a.pk)
        assert (row.games_played, row.net) == (3, 20)

    def test_is_one_query(self, tour, django_assert_num_queries):
        with django_assert_num_queries(1):
            list(stats.season_standings(tour["season"]))


class TestAllTime:
    def test_itm_uses_each_seasons_paid_places(self):
        player = make_player()
        two_paid = make_season(2024, SeasonKind.TOUR, paid_places=2)
        three_paid = make_season(2025, SeasonKind.TOUR, paid_places=3)
        make_result(make_game(two_paid), player, payout=0, place=3)  # not ITM in 2024
        make_result(make_game(three_paid), player, payout=40, place=3)  # ITM in 2025

        row = stats.all_time_standings().get(pk=player.pk)

        assert (row.games_played, row.itm, row.third_places) == (2, 1, 2)

    def test_cash_is_never_itm(self):
        player = make_player()
        make_result(make_game(make_season(kind=SeasonKind.CASH)), player, payout=200)

        assert stats.all_time_standings().get(pk=player.pk).itm == 0

    def test_kind_filter_and_totals(self, tour):
        a = tour["players"]["A"]
        cash = make_season(2025, SeasonKind.CASH)
        make_result(make_game(cash), a, buyin=100, payout=130, chips_out=13000)

        everything = stats.all_time_standings().get(pk=a.pk)
        cash_only = stats.all_time_standings(kind=SeasonKind.CASH)

        assert (everything.games_played, everything.net) == (4, 50)
        assert [(p.name, p.net) for p in cash_only] == [("Альфа", 30)]

    def test_is_one_query(self, tour, django_assert_num_queries):
        with django_assert_num_queries(1):
            list(stats.all_time_standings())


class TestPlayerSeasonBreakdown:
    def test_per_season_rows(self, tour):
        a = tour["players"]["A"]
        cash = make_season(2025, SeasonKind.CASH)
        make_result(make_game(cash), a, buyin=100, payout=130, chips_out=13000)
        make_season(2026, SeasonKind.TOUR)  # A did not play: no row

        rows = list(stats.player_season_breakdown(a))

        assert [(str(s), s.games_played, s.net, s.itm) for s in rows] == [
            ("Кэш 2025", 1, 30, 0),
            ("Турнир 2025", 3, 20, 2),
        ]

    def test_other_players_do_not_leak(self, tour):
        rows = list(stats.player_season_breakdown(tour["players"]["D"]))
        assert [(s.games_played, s.buyin_total, s.net) for s in rows] == [(3, 200, -100)]

    def test_is_one_query(self, tour, django_assert_num_queries):
        with django_assert_num_queries(1):
            list(stats.player_season_breakdown(tour["players"]["A"]))


class TestGameTotals:
    def test_cash_game_leftover(self):
        game = make_game(make_season(kind=SeasonKind.CASH))
        make_result(game, make_player(), buyin=100, payout=120, chips_out=12000)
        make_result(game, make_player(), buyin=100, payout=75, chips_out=7550)
        make_result(game, make_player(), buyin=50, payout=0, chips_out=0)

        row = stats.annotate_game_totals(Game.objects.filter(pk=game.pk)).get()

        assert (row.players_count, row.buyin_total, row.payout_total, row.leftover) == (
            3,
            250,
            195,
            55,
        )

    def test_tour_games_with_full_payouts_have_no_leftover(self, tour):
        rows = stats.annotate_game_totals(tour["season"].games.all())
        assert [row.leftover for row in rows] == [0, 0, 0]

    def test_game_without_results(self):
        game = make_game(make_season())
        row = stats.annotate_game_totals(Game.objects.filter(pk=game.pk)).get()
        assert (row.players_count, row.buyin_total, row.payout_total, row.leftover) == (
            0,
            0,
            0,
            0,
        )

    def test_is_one_query_for_many_games(self, tour, django_assert_num_queries):
        with django_assert_num_queries(1):
            rows = list(stats.annotate_game_totals(Game.objects.all()))
        assert len(rows) == 3


class TestSeasonTotals:
    def test_counts_distinct_players(self, tour):
        make_season(2026, SeasonKind.TOUR)
        rows = {str(s): s for s in stats.annotate_season_totals(Season.objects.all())}

        assert (rows["Турнир 2025"].games_count, rows["Турнир 2025"].players_count) == (3, 4)
        assert (rows["Турнир 2026"].games_count, rows["Турнир 2026"].players_count) == (0, 0)


class TestResultNet:
    def test_net_per_result(self, tour):
        rows = stats.annotate_result_net(Result.objects.filter(game__date__day=1))
        nets = {r.player.name: r.net for r in rows.select_related("player")}
        assert nets == {"Альфа": 70, "Браво": 30, "Чарли": 0, "Дельта": -100}


class TestLeftoverRule:
    def game_with(self, kind, buyins, payouts):
        game = make_game(make_season(kind=kind))
        for buyin, payout in zip(buyins, payouts, strict=True):
            make_result(game, make_player(), buyin=buyin, payout=payout)
        return stats.annotate_leftover_check(Game.objects.select_related("season")).get(pk=game.pk)

    @pytest.mark.parametrize(
        ("kind", "buyins", "payouts", "mismatch"),
        [
            (SeasonKind.TOUR, [50, 50], [100, 0], False),  # leftover 0
            (SeasonKind.TOUR, [50, 50], [95, 0], True),  # leftover +5
            (SeasonKind.TOUR, [50, 50], [105, 0], True),  # leftover -5
            (SeasonKind.CASH, [50, 50], [95, 0], False),  # +5 stays in the pot
            (SeasonKind.CASH, [50, 50], [100, 0], False),  # 0
            (SeasonKind.CASH, [50, 50], [105, 0], True),  # -5: paid out more than bought in
            (SeasonKind.TOUR, [], [], False),  # empty game
            (SeasonKind.CASH, [], [], False),
        ],
    )
    def test_rule(self, kind, buyins, payouts, mismatch):
        game = self.game_with(kind, buyins, payouts)

        assert game.leftover_mismatch is mismatch
        assert (stats.leftover_warning(game) is not None) is mismatch

    def test_warning_text_names_the_numbers(self):
        tour = self.game_with(SeasonKind.TOUR, [50, 50], [95, 0])
        cash = self.game_with(SeasonKind.CASH, [50, 50], [105, 0])

        assert "разница 5" in stats.leftover_warning(tour)
        assert "выплачено (105)" in stats.leftover_warning(cash)

    def test_is_one_query(self, tour, django_assert_num_queries):
        with django_assert_num_queries(1):
            rows = list(stats.annotate_leftover_check(Game.objects.all()))
        assert [row.leftover_mismatch for row in rows] == [False, False, False]
