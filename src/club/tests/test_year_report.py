"""club.year_report: every figure of the club and player pages, worked out by hand."""

import datetime
from fractions import Fraction

import pytest

from club import year_report
from club.models import Player, SeasonKind
from club.tests.conftest import REPORT_YEAR as YEAR
from club.tests.conftest import cash_game, tour_game
from club.tests.factories import make_game, make_player, make_result, make_season
from club.year_report import MONTH_MISSED, MONTH_NONE, MONTH_PLAYED, SharedRank

pytestmark = pytest.mark.django_db

CLOSED = datetime.date(YEAR + 1, 1, 1)  # a closed year is computed as of 1 January


def baseline(player):
    """A game in the year before, so YEAR is not the first year of history."""
    tour_game(make_season(YEAR - 1, SeasonKind.TOUR), 1, 1, [(player, 50, 0, 1)])


def labels(players) -> list[str]:
    return [player.label for player in players]


class TestAvailability:
    def test_no_report_without_games_or_for_the_first_year(self, report_year):
        assert year_report.load(YEAR - 1, CLOSED) is None  # first year of history
        assert year_report.load(YEAR - 2, CLOSED) is None
        assert year_report.load(YEAR + 2, CLOSED) is None
        assert year_report.load(YEAR, CLOSED) is not None

    def test_no_games_at_all(self):
        assert year_report.load(YEAR, CLOSED) is None

    def test_player_without_games_that_year(self, report_year):
        report = year_report.load(YEAR, CLOSED)
        assert report.player(Player.objects.get(name="Фокстрот").pk) is None
        assert report.player(report_year["players"]["E"].pk) is not None

    def test_live_games_count_nowhere(self, report_year):
        game = make_game(
            report_year["games"]["t1"].season,
            day=1,
            month=11,
            live_stage="rebuys",
            started_at=datetime.datetime(YEAR, 11, 1, 19, tzinfo=datetime.UTC),
        )
        make_result(game, report_year["players"]["A"])
        club = year_report.load(YEAR, CLOSED).club
        assert club.evening_count == 5
        assert club.months[10].evenings == 0


class TestClub:
    @pytest.fixture
    def club(self, report_year):
        return year_report.load(YEAR, CLOSED).club

    def test_evenings_and_games(self, club):
        assert club.evenings == tuple(
            datetime.date(YEAR, m, d) for m, d in [(3, 1), (3, 8), (6, 7), (7, 12), (12, 20)]
        )
        assert [bar.evenings for bar in club.months] == [0, 0, 2, 0, 0, 1, 1, 0, 0, 0, 0, 1]
        assert club.max_month == 2
        assert (club.tournaments, club.cash_games) == (4, 2)
        assert club.games_text == "4 турнира и 2 кэш-игры за 5 вечеров"
        assert club.last_game == datetime.date(YEAR, 12, 20)
        assert club.first_year == YEAR - 1
        assert club.today == CLOSED

    def test_players(self, club):
        assert labels(club.participants) == ["Браво", "Дельта", "Сеялка", "Трактор", "Чарли"]
        assert labels(club.newcomers) == ["Дельта", "Сеялка", "Чарли"]
        assert club.average_players == Fraction(18, 6)
        assert club.average_text == "3"
        assert (club.largest_table, club.largest_table_times) == (4, 1)
        assert labels(club.every_month) == ["Трактор"]

    def test_podium_stops_at_third_rank_with_ties(self, club):
        assert [(row.player.name, row.rank, row.net, row.games) for row in club.podium] == [
            ("Альфа", 1, 50, 4),
            ("Браво", 2, -50, 4),
            ("Чарли", 2, -50, 3),
        ]
        assert club.podium_tie
        assert labels(row.player for row in club.winners) == ["Трактор"]

    def test_cash(self, club, report_year):
        assert [(row.player.name, row.net, row.games) for row in club.cash_leaders] == [
            ("Браво", 100, 1)
        ]
        assert [(e.player.name, e.game.pk, e.net) for e in club.best_cash_evenings] == [
            ("Браво", report_year["games"]["c1"].pk, 100)
        ]
        # One "Снял кассу" each: a tie.
        assert labels(club.cashier_leaders) == ["Браво", "Трактор"]
        assert club.cashier_count == 1

    def test_honors(self, club):
        honors = {honor.code: honor for honor in club.honors}
        assert list(honors) == ["udarnik", "opora", "patience", "comeback", "bubble"]
        assert labels(honors["udarnik"].holders) == ["Трактор"]
        assert honors["udarnik"].basis == "5 вечеров из 5"
        assert labels(honors["opora"].holders) == ["Браво", "Трактор"]  # 350 each
        assert honors["opora"].basis == "закупок на 350, больше всех"
        # C: two tournaments out of the money, then the win. A's run at the end stays open.
        assert labels(honors["patience"].holders) == ["Чарли"]
        assert honors["patience"].basis == "2 турнира без призовых, затем в деньгах"
        assert labels(honors["comeback"].holders) == ["Дельта"]
        assert honors["comeback"].basis == "1 место при закупке 200"
        assert labels(honors["bubble"].holders) == ["Чарли"]
        assert honors["bubble"].basis == "2 раза место сразу за призовыми"
        assert honors["bubble"].note == "среди турниров с известными местами"
        assert honors["bubble"].title == "Бабл-гёрл года"


class TestHonorRules:
    @pytest.fixture
    def season(self, award_rules):
        baseline(make_player("Старожил"))
        return make_season(YEAR, SeasonKind.TOUR, paid_places=1)

    def test_bubble_hidden_below_two(self, season):
        a, b = make_player("Альфа"), make_player("Браво")
        tour_game(season, 2, 1, [(a, 50, 100, 1), (b, 50, 0, 2)])
        codes = [h.code for h in year_report.load(YEAR, CLOSED).club.honors]
        assert "bubble" not in codes

    def test_comeback_tie_break_by_place_then_shared(self, season):
        a, b, c = make_player("Альфа"), make_player("Браво"), make_player("Чарли")
        tour_game(season, 2, 1, [(a, 150, 300, 1), (b, 50, 0, 2)])
        tour_game(season, 2, 8, [(b, 150, 300, 1), (c, 50, 0, 2)])
        tour_game(season, 2, 15, [(c, 100, 300, 1), (a, 50, 0, 2)])
        honors = {h.code: h for h in year_report.load(YEAR, CLOSED).club.honors}
        assert labels(honors["comeback"].holders) == ["Альфа", "Браво"]
        assert honors["comeback"].basis == "1 место при закупке 150"

    def test_comeback_better_place_wins_on_equal_buyin(self, award_rules):
        season = make_season(YEAR, SeasonKind.TOUR, paid_places=2)
        a, b, c = make_player("Альфа"), make_player("Браво"), make_player("Чарли")
        baseline(c)
        tour_game(season, 2, 1, [(c, 50, 100, 1), (a, 150, 50, 2), (b, 50, 0, 3)])
        tour_game(season, 2, 8, [(b, 150, 100, 1), (a, 50, 50, 2), (c, 50, 0, 3)])
        honors = {h.code: h for h in year_report.load(YEAR, CLOSED).club.honors}
        assert labels(honors["comeback"].holders) == ["Браво"]

    def test_patience_tie_and_open_run(self, season):
        a, b, c = make_player("Альфа"), make_player("Браво"), make_player("Чарли")
        tour_game(season, 2, 1, [(c, 50, 100, 1), (a, 50, 0, 2), (b, 50, 0, 3)])
        tour_game(season, 2, 8, [(a, 50, 100, 1), (b, 50, 0, 2), (c, 50, 0, 3)])
        tour_game(season, 2, 15, [(b, 50, 100, 1), (c, 50, 0, 2)])
        # A: 1 miss then ITM; B: 2 misses then ITM; C: an open run of 2 (not counted).
        tour_game(season, 2, 22, [(a, 50, 0, 2), (b, 50, 100, 1)])
        tour_game(season, 3, 1, [(a, 50, 100, 1), (b, 50, 0, 2)])
        honors = {h.code: h for h in year_report.load(YEAR, CLOSED).club.honors}
        assert labels(honors["patience"].holders) == ["Браво"]
        tour_game(season, 3, 8, [(a, 50, 0, 2), (b, 50, 100, 1)])
        tour_game(season, 3, 15, [(a, 50, 0, 2), (b, 50, 100, 1)])
        tour_game(season, 3, 22, [(a, 50, 100, 1), (b, 50, 0, 2)])
        # A: 2 misses then ITM, as B: a tie.
        honors = {h.code: h for h in year_report.load(YEAR, CLOSED).club.honors}
        assert labels(honors["patience"].holders) == ["Альфа", "Браво"]
        assert honors["patience"].basis == "2 турнира без призовых, затем в деньгах"
        for day in (5, 12, 19):
            tour_game(season, 4, day, [(a, 50, 0, 2), (b, 50, 100, 1)])
        tour_game(season, 4, 26, [(a, 50, 100, 1), (b, 50, 0, 2)])
        honors = {h.code: h for h in year_report.load(YEAR, CLOSED).club.honors}
        assert labels(honors["patience"].holders) == ["Альфа"]  # 3 misses in a row
        assert honors["patience"].basis == "3 турнира без призовых, затем в деньгах"

    def test_no_itm_no_patience_no_comeback(self, award_rules):
        a = make_player("Альфа")
        baseline(a)
        cash_game(make_season(YEAR, SeasonKind.CASH), 2, 1, [(a, 50, 0), (make_player(), 50, 100)])
        club = year_report.load(YEAR, CLOSED).club
        assert [h.code for h in club.honors] == ["udarnik", "opora"]
        assert club.podium == ()
        assert club.tournaments == 0
        assert club.games_text == "1 кэш-игра за 1 вечер"


class TestPlayer:
    @pytest.fixture
    def report(self, report_year):
        return year_report.load(YEAR, CLOSED)

    def test_attendance_and_months(self, report, report_year):
        alpha = report.player(report_year["players"]["A"].pk)
        assert (alpha.evenings, alpha.club_evenings, alpha.percent) == (5, 5, 100)
        assert alpha.attendance.text == "1 место"
        states = [state for _, state in alpha.months]
        assert states == [
            MONTH_NONE,
            MONTH_NONE,
            MONTH_PLAYED,
            MONTH_NONE,
            MONTH_NONE,
            MONTH_PLAYED,
            MONTH_PLAYED,
            MONTH_NONE,
            MONTH_NONE,
            MONTH_NONE,
            MONTH_NONE,
            MONTH_PLAYED,
        ]
        assert alpha.missed_text == "Ни одного прогула"

    def test_shared_attendance_rank_names_the_others(self, report, report_year):
        bravo = report.player(report_year["players"]["B"].pk)
        assert bravo.attendance.text == "2–3 место · наравне: Чарли"
        assert bravo.percent == 80
        assert report.player(report_year["players"]["D"].pk).attendance.text == "4 место"

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("A", "Ни одного прогула"),
            ("B", "Прогулян только июль"),
            ("D", "Прогуляны июнь и июль"),
            ("E", "Прогуляно 3 месяца"),
        ],
    )
    def test_missed_months(self, report, report_year, key, expected):
        assert report.player(report_year["players"][key].pk).missed_text == expected

    def test_kinds_and_net(self, report, report_year):
        alpha = report.player(report_year["players"]["A"].pk)
        assert alpha.net == 70
        assert (alpha.tour.rank.text, alpha.tour.net, alpha.tour.games) == ("1 место из 4", 50, 4)
        assert (alpha.cash.rank.text, alpha.cash.net, alpha.cash.games) == ("2 место из 4", 20, 2)
        assert (alpha.tournaments, alpha.itm, alpha.wins, alpha.itm_streak) == (4, 3, 1, 3)
        bravo = report.player(report_year["players"]["B"].pk)
        assert bravo.tour.rank.text == "2–3 место из 4 · наравне: Чарли"
        echo = report.player(report_year["players"]["E"].pk)  # cash only
        assert echo.tour is None
        assert (echo.tournaments, echo.itm, echo.itm_streak) == (0, 0, 0)
        assert report.player(report_year["players"]["D"].pk).cash is None

    def test_best_and_worst_evening(self, report, report_year):
        alpha = report.player(report_year["players"]["A"].pk)
        best = alpha.best_evening
        assert (best.date, best.net, best.one_buyin) == (datetime.date(YEAR, 3, 1), 100, True)
        assert [(g.game.pk, g.players, g.place, g.net) for g in best.games] == [
            (report_year["games"]["t1"].pk, 4, 1, 100)
        ]
        # −50 on 08.03 (0 + −50) and on 20.12: the earliest.
        worst = alpha.worst_evening
        assert (worst.date, worst.net) == (datetime.date(YEAR, 3, 8), -50)
        assert [g.game.kind for g in worst.games] == [SeasonKind.TOUR, SeasonKind.CASH]
        assert not worst.one_buyin

    def test_one_evening_has_no_worst(self, report, report_year):
        echo = report.player(report_year["players"]["E"].pk)
        assert echo.best_evening.net == -50
        assert echo.worst_evening is None

    def test_co_players_with_ties(self, report, report_year):
        alpha = report.player(report_year["players"]["A"].pk)
        assert (labels(alpha.co_players), alpha.co_games) == (["Браво"], 5)
        echo = report.player(report_year["players"]["E"].pk)
        assert (labels(echo.co_players), echo.co_games) == (["Браво", "Трактор"], 1)

    def test_awards_of_the_year(self, report, report_year):
        alpha = report.player(report_year["players"]["A"].pk)
        assert [(t.name, t.mark, t.basis) for t in alpha.awards] == [
            ("Победитель сезона", "2026", "турниры, итог +50"),
            ("Ударник лета", "2026", "2 вечера из 2"),
            ("Звание года", "Старожил", "5 игр, Турнир № 3, 07.06 · счёт с 2025 года"),
            ("С одной закупки", "×1", "победа без докупок"),
            ("Снял кассу", "×1", "лучший итог кэш-игры"),
            ("Бабл-гёрл", "×1", "среди турниров с известными местами"),
        ]
        assert [h.code for h in alpha.honors] == ["udarnik", "opora"]

    def test_no_veteran_step_in_the_year(self, report, report_year):
        # Echo's only game is their first: the lowest step is not an event.
        echo = report.player(report_year["players"]["E"].pk)
        assert [t.name for t in echo.awards] == []

    def test_running_summer_title_is_not_an_award(self, report_year):
        report = year_report.load(YEAR, datetime.date(YEAR, 7, 20))
        alpha = report.player(report_year["players"]["A"].pk)
        assert "Ударник лета" not in [t.name for t in alpha.awards]

    def test_only_months_the_club_barely_played(self, award_rules):
        old = make_season(YEAR - 1, SeasonKind.CASH)
        season = make_season(YEAR, SeasonKind.CASH)
        a, b = make_player("Альфа"), make_player("Браво")
        cash_game(old, 5, 1, [(a, 50, 50), (b, 50, 50)])
        cash_game(season, 2, 14, [(a, 50, 60), (b, 50, 40)])
        cash_game(season, 11, 7, [(a, 50, 60), (b, 50, 40)])
        alpha = year_report.load(YEAR, CLOSED).player(a.pk)
        assert alpha.missed_text == "Ни одного прогула"
        played = [bar.month for bar, state in alpha.months if state == MONTH_PLAYED]
        assert played == [2, 11]
        assert all(state != MONTH_MISSED for _, state in alpha.months)


class TestCalendarBoundary:
    def test_december_counts_in_its_year_not_the_winter_title(self, report_year):
        report = year_report.load(YEAR, CLOSED)
        # 20.12 belongs to the winter 2026/27 title (filed under 2027), yet counts here.
        assert report.club.evenings[-1] == datetime.date(YEAR, 12, 20)
        delta = report.player(report_year["players"]["D"].pk)
        assert delta.evenings == 2
        # The January 2027 game is not part of 2026.
        assert report.player(report_year["players"]["C"].pk).evenings == 4

    def test_next_year_has_its_own_report(self, report_year):
        report = year_report.load(YEAR + 1, CLOSED)
        assert report.club.evening_count == 1
        assert labels(report.club.newcomers) == []


class TestQueries:
    def test_fixed_number_of_queries(self, report_year, django_assert_max_num_queries):
        with django_assert_max_num_queries(year_report.QUERY_COUNT):
            report = year_report.load(YEAR, CLOSED)
            for player in report.club.participants:
                page = report.player(player.pk)
                page.missed_text, page.attendance.text  # noqa: B018


class TestSharedRank:
    def players(self, *names):
        return tuple(Player(name=name) for name in names)

    def test_texts(self):
        assert SharedRank(2, 1).text == "2 место"
        assert SharedRank(2, 1, total=23).text == "2 место из 23"
        assert SharedRank(2, 2, self.players("Марина")).text == "2–3 место · наравне: Марина"
        assert (
            SharedRank(4, 3, self.players("Марина", "Олег"), total=23).text
            == "4–6 место из 23 · наравне: Марина и Олег"
        )
        assert (
            SharedRank(1, 6, self.players("Марина", "Олег", "А", "Б", "В")).others_text
            == "Марина, Олег и ещё 3"
        )


class TestFormatting:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Fraction(37, 5), "7,4"),
            (Fraction(7), "7"),
            (Fraction(149, 20), "7,5"),
            (Fraction(2, 3), "0,7"),
        ],
    )
    def test_format_decimal(self, value, expected):
        assert year_report.format_decimal(value) == expected
