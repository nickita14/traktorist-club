import datetime
from fractions import Fraction

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from club import achievements, stats
from club.achievements import Kind
from club.models import AchievementSettings, Player, RankLadder, RankStep, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season

pytestmark = pytest.mark.django_db

# Pinned "today" for every test: 2025 has ended, 2026 runs until the end of summer.
TODAY = datetime.date(2026, 7, 15)


@pytest.fixture(autouse=True)
def rules():
    """Own rules instead of the seeded ones: short ladders that a few games can climb."""
    RankLadder.objects.all().delete()
    AchievementSettings.objects.all().delete()
    ladders = {
        "veteran": ("Ветеран", [(1, "Новичок"), (3, "Мастер")]),
        "feeder": ("Кормилец клуба", [(100, "Пайщик"), (200, "Вкладчик"), (250, "Спонсор")]),
        "addon": ("Мистер Аддон", [(1, "Первый аддон"), (2, "Любитель")]),
    }
    for code, (title, steps) in ladders.items():
        ladder = RankLadder.objects.create(code=code, title=title)
        for threshold, step in steps:
            RankStep.objects.create(ladder=ladder, threshold=threshold, title=step)
    return AchievementSettings.objects.create()


@pytest.fixture
def tour25():
    return make_season(2025, SeasonKind.TOUR)  # paid 3, entry 50, rebuy 50


@pytest.fixture
def cash25():
    return make_season(2025, SeasonKind.CASH)  # 100 chips per lei


@pytest.fixture
def players():
    return [make_player(name) for name in ("Альфа", "Браво", "Чарли", "Дельта", "Эхо")]


def tour(season, month, day, rows, **game):
    """``rows``: (player, place) or (player, place, buyin[, payout[, extra fields]])."""
    played = make_game(season, day=day, month=month, **game)
    for player, place, *rest in rows:
        buyin, payout, extra = (rest + [50, 0, {}][len(rest) :])[:3]
        make_result(played, player, buyin=buyin, payout=payout, place=place, **extra)
    return played


def cash(season, month, day, rows, **game):
    """``rows``: (player, buyin, payout[, chips_out])."""
    played = make_game(season, day=day, month=month, **game)
    for player, buyin, payout, *chips in rows:
        make_result(played, player, buyin=buyin, payout=payout, chips_out=(chips or [None])[0])
    return played


def awards(kind=None, code=None, today=TODAY):
    return [
        award
        for award in achievements.load(today).awards
        if (kind is None or award.kind == kind) and (code is None or award.code == code)
    ]


def who(found):
    return sorted(Player.objects.get(pk=award.player_id).name for award in found)


def title(code, label, today=TODAY):
    loaded = achievements.load(today)
    return next(r for r in loaded.titles if r.code == code and r.period.label == label)


def names(pks):
    return sorted(Player.objects.get(pk=pk).name for pk in pks)


class TestBubble:
    def test_first_place_outside_the_money(self, tour25, players):
        a, b, c, d, e = players
        tour(tour25, 3, 1, [(a, 1), (b, 2), (c, 3), (d, 4), (e, 5)])
        assert who(awards(code="bubble")) == ["Дельта"]

    def test_two_sharing_third_leave_no_bubble(self, tour25, players):
        a, b, c, d, e = players
        tour(tour25, 3, 1, [(a, 1), (b, 2), (c, 3), (d, 3), (e, 5)])
        assert awards(code="bubble") == []

    def test_players_tied_on_the_bubble_share_it(self, tour25, players):
        a, b, c, d, e = players
        tour(tour25, 3, 1, [(a, 1), (b, 2), (c, 3), (d, 4), (e, 4)])
        assert who(awards(code="bubble")) == ["Дельта", "Эхо"]

    def test_unknown_places_give_no_bubble(self, tour25, players):
        a, b, c, d, e = players
        tour(tour25, 3, 1, [(a, 1), (b, 2), (c, 3), (d, None), (e, None)])
        assert awards(code="bubble") == []

    def test_follows_the_season_paid_places(self, players):
        season = make_season(2025, SeasonKind.TOUR, paid_places=2, payout_weights="2,1")
        a, b, c, *_ = players
        tour(season, 3, 1, [(a, 1), (b, 2), (c, 3)])
        assert who(awards(code="bubble")) == ["Чарли"]


class TestCashier:
    def test_best_net_of_the_game(self, cash25, players):
        a, b, c, *_ = players
        cash(cash25, 3, 1, [(a, 50, 120), (b, 50, 60), (c, 100, 20)])
        found = awards(code="cashier")
        assert who(found) == ["Альфа"]
        assert found[0].game.kind == SeasonKind.CASH

    def test_tie_shares_it(self, cash25, players):
        a, b, c, *_ = players
        cash(cash25, 3, 1, [(a, 50, 90), (b, 100, 140), (c, 100, 20)])
        assert who(awards(code="cashier")) == ["Альфа", "Браво"]

    def test_nobody_up_means_nobody(self, cash25, players):
        a, b, *_ = players
        cash(cash25, 3, 1, [(a, 50, 50), (b, 50, 30)])
        assert awards(code="cashier") == []


class TestOneBuyinAndComeback:
    def test_one_buyin_needs_the_bare_entry(self, tour25, players):
        a, b, c, *_ = players
        tour(tour25, 3, 1, [(a, 1, 50), (b, 2), (c, 3)])
        tour(tour25, 3, 8, [(a, 2), (b, 1, 100), (c, 3)])
        assert [(who([x]), x.game.date.day) for x in awards(code="one_buyin")] == [(["Альфа"], 1)]

    def test_comeback_at_the_boundary(self, tour25, players):
        a, b, c, d, _ = players
        # Entry 50 + 2 rebuys of 50 = 150 is the least that counts.
        tour(tour25, 3, 1, [(a, 1, 150), (b, 2, 149), (c, 3, 200), (d, 4, 300)])
        assert who(awards(code="comeback")) == ["Альфа", "Чарли"]  # Дельта is out of the money

    def test_comeback_follows_the_setting_and_prices(self, rules, players):
        rules.comeback_min_rebuys = 1
        rules.save()
        season = make_season(2025, SeasonKind.TOUR, entry_price=40, rebuy_price=30)
        a, b, *_ = players
        tour(season, 3, 1, [(a, 1, 70), (b, 2, 69)])
        assert who(awards(code="comeback")) == ["Альфа"]


class TestStreaks:
    def test_skipped_tournament_does_not_break_a_hat_trick(self, tour25, players):
        a, b, c, d, _ = players
        tour(tour25, 3, 1, [(a, 1), (b, 2), (c, 3), (d, 4)])
        tour(tour25, 3, 8, [(b, 1), (c, 2), (d, 3)])  # Альфа skips it
        third = tour(tour25, 3, 15, [(a, 3), (b, 4), (c, 1)])
        fourth = tour(tour25, 3, 22, [(a, 2), (b, 5), (c, 1)])
        found = awards(code="hat_trick")
        assert [(who([x]), x.game.pk) for x in found] == [
            (["Чарли"], third.pk),
            (["Альфа"], fourth.pk),
        ]

    def test_a_game_outside_the_money_breaks_it(self, tour25, players):
        a, b, c, d, _ = players
        tour(tour25, 3, 1, [(a, 1), (b, 2), (c, 3), (d, 4)])
        tour(tour25, 3, 8, [(a, 2), (b, 4), (c, 3), (d, 1)])
        tour(tour25, 3, 15, [(a, 4), (b, 1), (c, 2), (d, 3)])
        tour(tour25, 3, 22, [(a, 1), (b, 3), (c, 2), (d, 4)])
        assert who(awards(code="hat_trick")) == ["Чарли"]

    def test_a_streak_of_five_gives_one(self, tour25, players):
        a, b, c, d, _ = players
        for day in range(1, 6):
            tour(tour25, 4, day, [(a, 1), (b, 2), (c, 3), (d, 4)])
        found = awards(code="hat_trick")
        assert len(found) == 3  # Альфа, Браво, Чарли: one each, not three each
        assert {x.game.date for x in found} == {datetime.date(2025, 4, 3)}

    def test_two_streaks_give_two(self, tour25, players):
        a, b, c, d, _ = players
        for day in (1, 2, 3):
            tour(tour25, 4, day, [(a, 1), (b, 2), (c, 3), (d, 4)])
        tour(tour25, 4, 4, [(a, 4), (b, 2), (c, 3), (d, 1)])
        for day in (5, 6, 7):
            tour(tour25, 4, day, [(a, 1), (b, 2), (c, 3), (d, 4)])
        assert who(awards(code="hat_trick")) == ["Альфа", "Альфа", "Браво", "Чарли"]

    def test_streaks_run_across_seasons(self, tour25, players):
        a, b, c, d, _ = players
        tour(tour25, 11, 1, [(a, 1), (b, 4)])
        tour(tour25, 12, 1, [(a, 2), (b, 4)])
        tour(make_season(2026, SeasonKind.TOUR), 1, 10, [(a, 3), (b, 4)])
        assert who(awards(code="hat_trick")) == ["Альфа"]

    def test_itm_series_diplomas_once_each(self, rules, tour25, players):
        rules.itm_series_steps = "2,4"
        rules.save()
        a, b, *_ = players
        days = []
        for day in (1, 2, 3, 4):
            days.append(tour(tour25, 5, day, [(a, 1), (b, 4)]))
        tour(tour25, 5, 5, [(a, 4), (b, 1)])
        for day in (6, 7, 8, 9):
            tour(tour25, 5, day, [(a, 1), (b, 4)])
        found = awards(Kind.DIPLOMA)
        series = [(x.code, x.title, x.game.pk) for x in found if x.code.startswith("itm_series")]
        assert series == [
            ("itm_series_2", "Серия: 2 призовых подряд", days[1].pk),
            ("itm_series_4", "Серия: 4 призовых подряд", days[3].pk),
        ]

    def test_first_win_once(self, tour25, players):
        a, b, *_ = players
        tour(tour25, 3, 1, [(a, 2), (b, 1)])
        second = tour(tour25, 3, 8, [(a, 1), (b, 2)])
        tour(tour25, 3, 15, [(a, 1), (b, 2)])
        found = [x for x in awards(code="first_win") if x.player_id == a.pk]
        assert [(x.title, x.game.pk) for x in found] == [("Первая победа", second.pk)]


class TestEveningSeries:
    def test_missed_evening_breaks_it(self, rules, tour25, cash25, players):
        rules.evening_series_steps = "3"
        rules.save()
        a, b, *_ = players
        cash(cash25, 1, 5, [(a, 50, 50), (b, 50, 50)])
        tour(tour25, 1, 12, [(a, 1), (b, 2)])
        cash(cash25, 1, 19, [(b, 50, 50)])  # Альфа misses an evening
        tour(tour25, 1, 26, [(a, 1), (b, 2)])
        tour(tour25, 2, 2, [(a, 1), (b, 2)])  # Альфа: two in a row since the miss
        found = awards(code="evening_series_3")
        assert who(found) == ["Браво"]
        assert found[0].title == "Серия: 3 вечера подряд"
        assert found[0].game.date == datetime.date(2025, 1, 19)

    def test_both_kinds_on_one_evening_count_once(self, rules, tour25, cash25, players):
        rules.evening_series_steps = "2"
        rules.save()
        a, b, *_ = players
        tour(tour25, 1, 5, [(a, 1), (b, 2)])
        cash(cash25, 1, 5, [(a, 50, 50)])
        evening_tour = tour(tour25, 1, 12, [(a, 1), (b, 2)])
        cash(cash25, 1, 12, [(a, 50, 50)])
        found = [x for x in awards(code="evening_series_2") if x.player_id == a.pk]
        # One diploma, earned on the second evening, linked to its tournament.
        assert [x.game.pk for x in found] == [evening_tour.pk]


class TestRanks:
    def test_step_dates_and_progress(self, tour25, cash25, players):
        a, *_ = players
        first = tour(tour25, 3, 1, [(a, 1, 50)])
        second = cash(cash25, 3, 1, [(a, 60, 0)])  # feeder 50 -> 110
        third = tour(tour25, 3, 8, [(a, 1, 150)])  # feeder 110 -> 260: two steps at once

        loaded = achievements.load(TODAY)
        ranks = {r.ladder.code: r for r in loaded.player(a.pk).ranks}

        veteran = ranks["veteran"]
        assert (veteran.value, veteran.step.title, veteran.next_step) == (3, "Мастер", None)
        assert [(x.title, x.game.pk) for x in veteran.reached] == [
            ("Новичок", first.pk),
            ("Мастер", third.pk),
        ]
        feeder = ranks["feeder"]
        assert feeder.value == 260
        assert [(x.title, x.game.pk) for x in feeder.reached] == [
            ("Пайщик", second.pk),
            ("Вкладчик", third.pk),
            ("Спонсор", third.pk),
        ]
        reached = loaded.game(third.pk).reached
        assert [(x.ladder.code, x.title) for x in reached] == [
            ("veteran", "Мастер"),
            ("feeder", "Вкладчик"),
            ("feeder", "Спонсор"),
        ]

    def test_below_the_first_step(self, tour25, players):
        a, *_ = players
        tour(tour25, 3, 1, [(a, 1, 60)])
        feeder = next(
            r for r in achievements.load(TODAY).player(a.pk).ranks if r.ladder.code == "feeder"
        )
        assert (feeder.step, feeder.next_step.title, feeder.remaining) == (None, "Пайщик", 40)
        assert (feeder.progress_value, feeder.progress_max) == (60, 100)

    def test_progress_between_steps(self, tour25, players):
        a, *_ = players
        tour(tour25, 3, 1, [(a, 1, 150)])
        feeder = next(
            r for r in achievements.load(TODAY).player(a.pk).ranks if r.ladder.code == "feeder"
        )
        assert (feeder.step.title, feeder.next_step.title, feeder.remaining) == (
            "Пайщик",
            "Вкладчик",
            50,
        )
        assert (feeder.progress_value, feeder.progress_max) == (50, 100)

    def test_unknown_addons_do_not_count(self, tour25, players):
        a, b, *_ = players
        tour(tour25, 3, 1, [(a, 1, 100, 0, {"addon": True, "rebuys": 0}), (b, 2, 50, 0, {})])
        tour(tour25, 3, 8, [(a, 1, 50, 0, {"addon": False, "rebuys": 0}), (b, 2, 50, 0, {})])
        tour(tour25, 3, 15, [(a, 1, 50, 0, {}), (b, 2, 50, 0, {})])  # imported: unknown
        counters = achievements.load(TODAY).counters()["addon"]
        assert counters == {a.pk: 1, b.pk: 0}
        assert who(awards(Kind.RANK, "addon")) == ["Альфа"]

    def test_counters_match_the_stats_totals(self, tour25, cash25, players):
        a, b, c, *_ = players
        tour(tour25, 3, 1, [(a, 1, 150), (b, 2), (c, 3, 100)])
        cash(cash25, 3, 1, [(a, 50, 80), (c, 150, 20)])
        tour(tour25, 3, 8, [(a, 2), (b, 1)])
        counters = achievements.load(TODAY).counters()
        for player in stats.annotate_player_totals(
            Player.objects.filter(results__isnull=False).distinct()
        ):
            assert counters["veteran"][player.pk] == player.games_played
            assert counters["feeder"][player.pk] == player.buyin_total


class TestUdarnik:
    def test_tie_shares_the_title(self, tour25, cash25, players):
        a, b, c, *_ = players
        tour(tour25, 3, 1, [(a, 1), (b, 2), (c, 3)])
        cash(cash25, 3, 1, [(c, 50, 50)])  # the same evening: still one
        tour(tour25, 4, 5, [(a, 1), (b, 2)])
        result = title("udarnik", "Весна 2025")
        assert (names(result.holders), result.value, result.running) == (
            ["Альфа", "Браво"],
            2,
            False,
        )
        assert result.period.end == datetime.date(2025, 5, 31)

    def test_winter_across_new_year(self, tour25, players):
        a, b, *_ = players
        tour(tour25, 12, 20, [(a, 1), (b, 2)])
        tour(make_season(2026, SeasonKind.TOUR), 1, 10, [(a, 1)])
        result = title("udarnik", "Зима 2025/26")
        assert (result.period.year, result.period.start, result.period.end) == (
            2026,
            datetime.date(2025, 12, 1),
            datetime.date(2026, 2, 28),
        )
        assert names(result.holders) == ["Альфа"]
        board = achievements.load(TODAY).honors(2026)
        assert [r.period.label for r in board.titles if r.code == "udarnik"] == ["Зима 2025/26"]
        assert achievements.load(TODAY).honors(2025).titles[0].period.label != "Зима 2025/26"

    def test_running_period_shows_leaders(self, players):
        a, b, *_ = players
        season = make_season(2026, SeasonKind.TOUR)
        tour(season, 6, 5, [(a, 1), (b, 2)])
        tour(season, 7, 3, [(a, 1)])
        running = title("udarnik", "Лето 2026")
        assert (running.running, names(running.holders)) == (True, ["Альфа"])
        loaded = achievements.load(TODAY)
        # By period start: the tour season since January, summer since June, July.
        assert [r.code for r in loaded.player(a.pk).leading] == ["always_itm", "udarnik", "no_skip"]
        assert not any(x.kind == Kind.TITLE and x.code == "udarnik" for x in loaded.awards)

        ended = title("udarnik", "Лето 2026", today=datetime.date(2026, 9, 1))
        assert ended.running is False
        found = awards(Kind.TITLE, "udarnik", today=datetime.date(2026, 9, 1))
        assert [(who([x]), x.date) for x in found] == [(["Альфа"], datetime.date(2026, 8, 31))]

    def test_last_day_is_still_running(self, players):
        a, *_ = players
        tour(make_season(2026, SeasonKind.TOUR), 6, 5, [(a, 1)])
        assert title("udarnik", "Лето 2026", today=datetime.date(2026, 8, 31)).running


class TestNoSkip:
    def test_every_evening_of_the_month(self, tour25, cash25, players):
        a, b, *_ = players
        tour(tour25, 3, 1, [(a, 1), (b, 2)])
        cash(cash25, 3, 15, [(a, 50, 50)])
        result = title("no_skip", "Март 2025")
        assert (names(result.holders), result.value) == (["Альфа"], 2)

    def test_month_under_the_minimum_does_not_count(self, tour25, players):
        a, *_ = players
        tour(tour25, 4, 1, [(a, 1)])
        loaded = achievements.load(TODAY)
        assert [r for r in loaded.titles if r.code == "no_skip"] == []

    def test_nobody_at_every_evening(self, tour25, players):
        a, b, *_ = players
        tour(tour25, 3, 1, [(a, 1)])
        tour(tour25, 3, 8, [(b, 1)])
        assert title("no_skip", "Март 2025").holders == ()

    def test_running_month_counts_so_far(self, players):
        a, b, *_ = players
        tour(make_season(2026, SeasonKind.TOUR), 7, 2, [(a, 1), (b, 2)])
        result = title("no_skip", "Июль 2026")
        assert (result.running, names(result.holders)) == (True, ["Альфа", "Браво"])


class TestAlwaysItm:
    def test_needs_the_minimum(self, rules, tour25, players):
        rules.always_itm_min_tournaments = 3
        rules.save()
        a, b, c, *_ = players
        for day in (1, 2, 3):
            tour(tour25, 3, day, [(a, 1), (b, 2), (c, 4)])
        tour(tour25, 3, 4, [(a, 1), (c, 4)])
        result = title("always_itm", "Турниры 2025")
        assert (names(result.holders), result.value, result.running) == (
            ["Альфа", "Браво"],
            4,
            False,
        )
        assert result.period.season_kind == SeasonKind.TOUR

        rules.always_itm_min_tournaments = 4
        rules.save()
        assert names(title("always_itm", "Турниры 2025").holders) == ["Альфа"]

    def test_running_contenders(self, players):
        a, b, *_ = players
        season = make_season(2026, SeasonKind.TOUR)
        tour(season, 3, 1, [(a, 1), (b, 4)])
        result = title("always_itm", "Турниры 2026")
        assert (result.running, names(result.holders)) == (True, ["Альфа"])

    def test_running_without_contenders(self, players):
        a, b, *_ = players
        season = make_season(2026, SeasonKind.TOUR)
        tour(season, 3, 1, [(a, 1), (b, 4)])
        tour(season, 3, 8, [(a, 4), (b, 1)])
        result = title("always_itm", "Турниры 2026")
        assert (result.running, result.holders, result.no_data) == (True, (), False)


class TestPatron:
    def test_exact_sums(self, cash25, players):
        a, b, c, *_ = players
        # Альфа: 0,10 + 0,20; Браво: 0,30. Floats would say 0.30000000000000004 != 0.3.
        cash(cash25, 3, 1, [(a, 50, 50, 5010), (b, 50, 50, 5030), (c, 50, 50)])
        cash(cash25, 3, 8, [(a, 50, 50, 5020), (c, 50, 40, 4000)])
        result = title("patron", "Кэш 2025")
        assert (names(result.holders), result.value) == (["Альфа", "Браво"], Fraction(3, 10))
        assert result.value == sum(
            r.pot for r in stats.game_results(cash25.games.get(date__day=1)) if r.player == b
        )

    def test_unknown_stacks_are_ignored(self, cash25, players):
        a, b, *_ = players
        cash(cash25, 3, 1, [(a, 50, 0), (b, 50, 45, 4600)])  # Альфа left 50 but chips unknown
        assert names(title("patron", "Кэш 2025").holders) == ["Браво"]

    def test_no_data_when_no_stack_is_known(self, cash25, players):
        a, b, *_ = players
        cash(cash25, 3, 1, [(a, 50, 60), (b, 50, 40)])
        result = title("patron", "Кэш 2025")
        assert (result.no_data, result.holders) == (True, ())

    def test_no_contenders_without_a_positive_pot(self, cash25, players):
        a, b, *_ = players
        cash(cash25, 3, 1, [(a, 50, 60, 6000), (b, 50, 40, 4000)])
        result = title("patron", "Кэш 2025")
        assert (result.no_data, result.holders) == (False, ())


class TestLiveGames:
    def test_live_games_count_nowhere(self, tour25, players):
        a, b, c, d, _ = players
        tour(
            tour25,
            3,
            1,
            [(a, 1), (b, 2), (c, 3), (d, 4)],
            live_stage="final",
            started_at=timezone.now(),
        )
        loaded = achievements.load(TODAY)
        assert loaded.awards == []
        assert loaded.titles == []
        assert loaded.evenings == []
        assert loaded.counters()["veteran"] == {}


class TestQueries:
    def build(self, players, games):
        season = make_season(2025, SeasonKind.TOUR)
        cash_season = make_season(2025, SeasonKind.CASH)
        for day in range(1, games + 1):
            rows = [(p, i + 1, 50 + 50 * (i % 3)) for i, p in enumerate(players)]
            tour(season, 1 + day // 28, 1 + day % 28, rows)
            cash(cash_season, 1 + day // 28, 1 + day % 28, [(p, 50, 40, 4500) for p in players])

    def use_everything(self, loaded, players):
        for player in players:
            loaded.player(player.pk)
        for award in loaded.awards[:5]:
            if award.game:
                loaded.game(award.game.pk)
        loaded.honors(2025)
        loaded.recent(10)
        loaded.completeness()

    @pytest.mark.parametrize("size", [(2, 2), (5, 20)])
    def test_fixed_number_of_queries(self, django_assert_num_queries, size):
        players = [make_player() for _ in range(size[0])]
        self.build(players, size[1])
        with django_assert_num_queries(achievements.QUERY_COUNT):
            self.use_everything(achievements.load(TODAY), players)

    def test_missing_rules_add_no_query_and_no_write(self, django_assert_num_queries, players):
        self.build(players[:3], 3)
        RankLadder.objects.all().delete()
        AchievementSettings.objects.all().delete()
        with CaptureQueriesContext(connection) as queries:
            with django_assert_num_queries(achievements.QUERY_COUNT):
                loaded = achievements.load(TODAY)
                self.use_everything(loaded, players)
        assert all(q["sql"].lstrip().upper().startswith("SELECT") for q in queries)
        assert loaded.settings.hat_trick_length == 3  # the field default
        assert loaded.player(players[0].pk).ranks == []
        assert awards(Kind.RANK) == []
        assert awards(code="hat_trick")  # everything else still works
        assert not AchievementSettings.objects.exists()

    def test_today_defaults_to_the_local_date(self):
        assert achievements.load().today == timezone.localdate()


class TestViews:
    """What 9b reads: a game's awards, the honors board, recent awards, completeness."""

    def test_game_awards(self, tour25, players):
        a, b, c, d, _ = players
        game = tour(tour25, 3, 1, [(a, 1), (b, 2), (c, 3), (d, 4)])
        found = achievements.load(TODAY).game(game.pk)
        assert {pk: [x.code for x in badges] for pk, badges in found.badges.items()} == {
            a.pk: ["one_buyin"],
            d.pk: ["bubble"],
        }
        assert sorted((who([x])[0], x.code) for x in found.reached if x.kind == Kind.DIPLOMA) == [
            ("Альфа", "first_win")
        ]
        assert {x.title for x in found.reached if x.kind == Kind.RANK} == {"Новичок"}

    def test_badges_on_the_player_card(self, tour25, players):
        a, b, *_ = players
        tour(tour25, 3, 1, [(a, 1), (b, 2)])
        last = tour(tour25, 3, 8, [(a, 1), (b, 2)])
        card = achievements.load(TODAY).player(a.pk)
        assert [(x.code, x.count, x.last.game.pk) for x in card.badges] == [
            ("one_buyin", 2, last.pk)
        ]
        assert [x.code for x in card.diplomas] == ["first_win"]

    def test_honors_board(self, tour25, players):
        a, b, c, d, _ = players
        tour(tour25, 3, 1, [(a, 1), (b, 2), (c, 3), (d, 4)])
        tour(make_season(2026, SeasonKind.TOUR), 3, 1, [(a, 1), (d, 4)])
        loaded = achievements.load(TODAY)
        assert loaded.title_years() == [2026, 2025]
        board = loaded.honors(2025)
        assert {r.period.year for r in board.titles} == {2025}
        # The badge matrix is all-time: both years.
        matrix = {player.name: counts for player, counts in board.badge_matrix}
        assert matrix == {"Альфа": {"one_buyin": 2}, "Дельта": {"bubble": 2}}
        assert board.badge_codes == list(achievements.BADGES)
        assert [(p.name, ranks[0].value) for p, ranks in board.rank_table][:2] == [
            ("Альфа", 2),
            ("Дельта", 2),
        ]

    def test_recent_awards_newest_first(self, tour25, players):
        a, b, *_ = players
        tour(tour25, 3, 1, [(a, 1), (b, 2)])
        tour(tour25, 3, 8, [(a, 2), (b, 1)])
        recent = achievements.load(TODAY).recent(3)
        assert len(recent) == 3
        assert recent[0].date >= recent[-1].date
        # Titles are dated on the period's last day: spring 2025 ended on 31.05.
        assert (recent[0].code, recent[0].date) == ("udarnik", datetime.date(2025, 5, 31))
        assert achievements.load(TODAY).recent(0) == []

    def test_completeness(self, tour25, players):
        a, b, c, *_ = players
        tour(tour25, 3, 1, [(a, 1, 50, 0, {"rebuys": 0, "addon": False}), (b, 2), (c, None)])
        tour(tour25, 3, 8, [(a, 1), (b, 2)])
        assert achievements.load(TODAY).completeness() == achievements.Completeness(
            tournaments=2,
            tournaments_all_places=1,
            tour_results=5,
            rebuys_known=1,
            addon_known=1,
        )
