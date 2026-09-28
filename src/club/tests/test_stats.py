import datetime
from fractions import Fraction

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

    def test_rank_follows_net(self, tour):
        rows = stats.season_standings(tour["season"])

        assert [(p.name, p.net, p.rank) for p in rows] == [
            ("Браво", 50, 1),
            ("Чарли", 30, 2),
            ("Альфа", 20, 3),
            ("Дельта", -100, 4),
        ]

    def test_equal_nets_share_a_rank_and_the_next_skips(self):
        season = make_season(2025, SeasonKind.TOUR)
        game = make_game(season)
        for name, payout in [("Альфа", 100), ("Браво", 100), ("Чарли", 0), ("Дельта", 0)]:
            make_result(game, make_player(name), buyin=50, payout=payout)

        rows = stats.season_standings(season)

        assert [(p.name, p.rank) for p in rows] == [
            ("Альфа", 1),
            ("Браво", 1),
            ("Дельта", 3),  # ties keep name order
            ("Чарли", 3),
        ]


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

        # Tour before cash within a year, like the nav.
        assert [(str(s), s.games_played, s.net, s.itm) for s in rows] == [
            ("Турнир 2025", 3, 20, 2),
            ("Кэш 2025", 1, 30, 0),
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

    def test_buyin_total_and_last_game_date(self, tour):
        make_season(2026, SeasonKind.TOUR)
        rows = {str(s): s for s in stats.annotate_season_totals(Season.objects.all())}

        # g1 250 (with D's rebuy), g2 200, g3 200.
        assert rows["Турнир 2025"].buyin_total == 650
        assert rows["Турнир 2025"].last_game_date == datetime.date(2025, 1, 3)
        assert rows["Турнир 2026"].buyin_total == 0
        assert rows["Турнир 2026"].last_game_date is None


class TestRecentGames:
    def test_newest_first_with_season_numbers_and_totals(self, tour):
        games = list(stats.recent_games(tour["season"], limit=2))

        assert [(g.date.day, g.number, g.players_count, g.buyin_total) for g in games] == [
            (3, 3, 4, 200),
            (2, 2, 4, 200),
        ]

    def test_winners(self, tour):
        games = {g.date.day: g for g in stats.recent_games(tour["season"], limit=10)}

        assert [r.player.name for r in games[3].winners] == ["Дельта"]
        assert [r.player.name for r in games[1].winners] == ["Альфа"]

    def test_tie_for_first_gives_every_winner(self):
        game = make_game(make_season(2025, SeasonKind.TOUR))
        make_result(game, make_player("Браво"), payout=50, place=1)
        make_result(game, make_player("Альфа"), payout=50, place=1)

        (row,) = stats.recent_games(game.season, limit=5)

        assert [r.player.name for r in row.winners] == ["Альфа", "Браво"]

    def test_cash_game_has_no_winners(self):
        game = make_game(make_season(2025, SeasonKind.CASH))
        make_result(game, make_player(), buyin=100, payout=150)

        (row,) = stats.recent_games(game.season, limit=5)

        assert row.winners == []

    def test_other_seasons_do_not_leak(self, tour):
        make_game(make_season(2026, SeasonKind.TOUR))

        assert len(stats.recent_games(tour["season"], limit=10)) == 3

    def test_is_two_queries(self, tour, django_assert_num_queries):
        with django_assert_num_queries(2):
            games = list(stats.recent_games(tour["season"], limit=10))
            [r.player.name for g in games for r in g.winners]


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


class TestGameNumber:
    def test_numbers_follow_the_date_within_each_season(self, club):
        games = stats.annotate_game_number(Game.objects.all())

        numbers = {(g.season.kind, g.date.day): g.number for g in games.select_related("season")}
        assert numbers == {
            ("tour", 10): 1,
            ("tour", 1): 1,
            ("tour", 8): 2,
            ("tour", 15): 3,
            ("cash", 8): 1,
            ("cash", 22): 2,
        }

    def test_recent_games_use_the_same_numbers(self, club):
        recent = stats.recent_games(club["seasons"]["tour"], limit=2)

        assert [g.number for g in recent] == [3, 2]


class TestGamePosition:
    def test_middle_game(self, club):
        g = club["games"]

        position = stats.game_position(g["t2"])

        assert position.number == 2
        assert (position.previous.pk, position.previous.number) == (g["t1"].pk, 1)
        assert (position.next.pk, position.next.number, position.next.date) == (
            g["t3"].pk,
            3,
            g["t3"].date,
        )

    def test_edges(self, club):
        assert stats.game_position(club["games"]["t1"]).previous is None
        assert stats.game_position(club["games"]["t3"]).next is None
        only = stats.game_position(club["games"]["t0"])
        assert (only.number, only.previous, only.next) == (1, None, None)

    def test_is_one_query(self, club, django_assert_num_queries):
        game = Game.objects.select_related("season").get(pk=club["games"]["t2"].pk)

        with django_assert_num_queries(1):
            stats.game_position(game)


class TestSameEvening:
    def test_finds_the_other_kind_with_its_number(self, club):
        other = stats.same_evening(club["games"]["t2"])

        assert (other.pk, other.number) == (club["games"]["c1"].pk, 1)
        assert stats.same_evening(club["games"]["c1"]).pk == club["games"]["t2"].pk

    def test_none_without_a_game_of_the_other_kind(self, club):
        assert stats.same_evening(club["games"]["t1"]) is None


class TestGameResults:
    def test_pot_is_exact_chip_value_minus_payout(self, club):
        rows = {r.player.name: r.pot for r in stats.game_results(club["games"]["c1"])}

        assert rows == {"Альфа": Fraction(23, 10), "Браво": Fraction(77, 10), "Чарли": None}

    def test_tour_in_place_order_players_without_a_place_last(self, club):
        game = club["games"]["t1"]
        game.results.filter(player=club["players"]["C"]).update(place=2)
        names = [r.player.name for r in stats.game_results(game)]
        assert names == ["Альфа", "Чарли", "Браво"]

    def test_cash_best_net_first(self, club):
        names = [r.player.name for r in stats.game_results(club["games"]["c1"])]
        assert names == ["Чарли", "Браво", "Альфа"]  # +50, -10, -50

    def test_tour_results_have_no_pot(self, club):
        assert {r.pot for r in stats.game_results(club["games"]["t1"])} == {None}

    def test_is_one_query(self, club, django_assert_num_queries):
        game = Game.objects.select_related("season").get(pk=club["games"]["c1"].pk)

        with django_assert_num_queries(1):
            [r.player.name for r in stats.game_results(game)]

    def test_chips_value(self):
        assert stats.chips_value(5230, 100) == Fraction(523, 10)
        assert stats.chips_value(5000, 100) == 50


class TestGameAndSeasonTotals:
    def test_net_total_is_the_negative_leftover(self, club):
        game = stats.annotate_game_totals(Game.objects.filter(pk=club["games"]["t3"].pk)).get()

        assert (game.leftover, game.net_total) == (40, -40)

    def test_season_pot(self, club):
        season = stats.annotate_season_totals(Season.objects.filter(pk=club["seasons"]["cash"].pk))

        row = season.get()
        assert (row.buyin_total, row.payout_total, row.leftover) == (350, 360, -10)


class TestPlayerCard:
    def test_all_time_tour_and_cash_on_one_row(self, club):
        card = stats.annotate_player_card(Player.objects.filter(pk=club["players"]["A"].pk)).get()

        assert (card.games_played, card.net, card.itm, card.first_places) == (6, 190, 4, 4)
        assert (card.tour_games_played, card.tour_net, card.tour_itm) == (4, 210, 4)
        assert (card.cash_games_played, card.cash_net, card.cash_itm) == (2, -20, 0)
        assert (card.first_game_date.year, card.last_game_date.day) == (
            club["seasons"]["tour_old"].year,
            22,
        )

    def test_player_without_games(self, club):
        card = stats.annotate_player_card(Player.objects.filter(pk=club["players"]["E"].pk)).get()

        assert (card.games_played, card.net, card.tour_net, card.first_game_date) == (0, 0, 0, None)


class TestPlayerTimeline:
    def test_cumulative_net_with_tour_first_on_a_shared_date(self, club):
        points = stats.player_net_timeline(club["players"]["A"])

        assert [p.net for p in points] == [50, 150, 200, 150, 160, 190]
        assert points[2].date == points[3].date

    def test_no_games(self, club):
        assert stats.player_net_timeline(club["players"]["E"]) == []


class TestPlayerResults:
    def test_newest_first_with_net_and_number(self, club):
        rows = [
            (r.game.season.kind, r.game_number, r.net)
            for r in stats.player_results(club["players"]["B"])
        ]

        assert rows == [
            ("cash", 2, -20),
            ("tour", 3, -50),
            ("cash", 1, -10),
            ("tour", 2, -50),
            ("tour", 1, -50),
            ("tour", 1, -50),
        ]


class TestAllTimeRankAndClubTotals:
    def test_rank(self, club):
        assert [(p.rank, p.name) for p in stats.all_time_standings(kind=SeasonKind.CASH)] == [
            (1, "Чарли"),
            (2, "Дельта"),
            (3, "Альфа"),
            (4, "Браво"),
        ]

    def test_club_totals_by_kind(self, club):
        assert stats.club_totals(SeasonKind.CASH) == {
            "games_count": 2,
            "players_count": 4,
            "buyin_total": 350,
        }
        assert stats.club_totals()["games_count"] == 6

    def test_player_index_skips_players_without_games(self, club):
        assert [p.name for p in stats.player_index()] == ["Альфа", "Браво", "Дельта", "Чарли"]
