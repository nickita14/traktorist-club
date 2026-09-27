"""A game being recorded at the table counts nowhere until its results are saved."""

import pytest
from django.urls import reverse
from django.utils import timezone

from club import stats
from club.models import Game, Player, Season, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season

pytestmark = pytest.mark.django_db


@pytest.fixture
def live(club):
    """A live tour game after t3 in club's tour season: Альфа and Эхо bought in 100, no payouts.

    Эхо has no other games, so everything about Эхо comes from this game alone.
    """
    game = make_game(
        club["seasons"]["tour"],
        day=29,
        month=3,
        live_stage=Game.Stage.FINAL,
        started_at=timezone.now(),
    )
    make_result(game, club["players"]["A"], buyin=100, rebuys=0, addon=False)
    make_result(game, club["players"]["E"], buyin=100, rebuys=0, addon=False, out_order=1)
    return game


def finish(game):
    Game.objects.filter(pk=game.pk).update(live_stage="")


def alpha_tour_net(club):
    players = stats.season_standings(club["seasons"]["tour"])
    return players.get(pk=club["players"]["A"].pk).net


class TestHidden:
    def test_season_standings(self, club, live):
        standings = stats.season_standings(club["seasons"]["tour"])
        assert alpha_tour_net(club) == 160  # t1..t3 only: +100, +50, +10
        assert club["players"]["E"] not in standings

    def test_all_time_and_club_totals(self, club, live):
        assert stats.all_time_standings().get(pk=club["players"]["A"].pk).games_played == 6
        assert stats.club_totals()["games_count"] == 6
        assert stats.club_totals(SeasonKind.TOUR)["buyin_total"] == 500

    def test_player_index_and_card(self, club, live):
        assert club["players"]["E"] not in stats.player_index()
        card = stats.annotate_player_card(Player.objects.all()).get(pk=club["players"]["A"].pk)
        assert (card.games_played, card.tour_games_played) == (6, 4)
        assert card.last_game_date == club["games"]["c2"].date

    def test_player_season_breakdown(self, club, live):
        seasons = stats.player_season_breakdown(club["players"]["E"])
        assert list(seasons) == []

    def test_season_totals_and_games(self, club, live):
        season = stats.annotate_season_totals(Season.objects.all()).get(
            pk=club["seasons"]["tour"].pk
        )
        assert (season.games_count, season.buyin_total) == (3, 400)
        assert season.last_game_date == club["games"]["t3"].date
        assert live not in stats.season_games(club["seasons"]["tour"])

    def test_player_results_and_timeline(self, club, live):
        alpha = club["players"]["A"]
        assert live.pk not in {r.game_id for r in stats.player_results(alpha)}
        assert len(stats.player_net_timeline(alpha)) == 6

    def test_game_position_and_same_evening(self, club, live):
        position = stats.game_position(club["games"]["t3"])
        assert position.next is None
        cash = make_game(
            club["seasons"]["cash"], day=29, month=3, live_stage="cash", started_at=timezone.now()
        )
        assert stats.same_evening(cash) is None

    def test_game_numbers_skip_live_games(self, club, live):
        make_game(
            club["seasons"]["tour"],
            day=5,
            month=3,
            live_stage=Game.Stage.REBUYS,
            started_at=timezone.now(),
        )
        numbers = {g.pk: g.number for g in stats.season_games(club["seasons"]["tour"])}
        assert numbers[club["games"]["t3"].pk] == 3

    def test_public_sheet_is_404(self, client, live):
        assert client.get(reverse("game_detail", args=[live.pk])).status_code == 404

    def test_public_pages_leave_it_out(self, client, club, live):
        response = client.get(reverse("season", args=[club["seasons"]["tour"].year, "tour"]))
        assert response.status_code == 200
        assert "Новичок" not in response.text  # Эхо's nickname
        response = client.get(reverse("player_detail", args=[club["players"]["E"].slug]))
        assert response.status_code == 200
        assert "player" in response.context and not response.context["player"].games_played


class TestCountsOnceFinished:
    def test_everything_sees_it(self, client, club, live):
        finish(live)

        assert alpha_tour_net(club) == 60  # 160 - 100
        assert club["players"]["E"] in stats.player_index()
        assert stats.club_totals()["games_count"] == 7
        assert live in stats.season_games(club["seasons"]["tour"])
        assert client.get(reverse("game_detail", args=[live.pk])).status_code == 200


class TestLeftoverCheck:
    def test_live_game_is_not_flagged(self, live):
        game = stats.annotate_leftover_check(Game.objects.all()).get(pk=live.pk)
        assert game.leftover == 200  # totals of the game itself still work
        assert game.leftover_mismatch is False
        assert stats.leftover_warning(game) is None

    def test_flagged_once_finished(self, live):
        finish(live)
        game = stats.annotate_leftover_check(Game.objects.all()).get(pk=live.pk)
        assert game.leftover_mismatch is True

    def test_admin_filter_and_column(self, admin_client, live):
        url = reverse("admin:club_game_changelist")
        flagged = admin_client.get(url, {"leftover_mismatch": "yes"}).context["cl"].result_list
        assert live not in flagged  # t3 of the club fixture is the flagged one
        assert len(flagged) == 2

        finish(live)
        flagged = admin_client.get(url, {"leftover_mismatch": "yes"}).context["cl"].result_list
        assert live in flagged


CASES = [
    (SeasonKind.TOUR, 100, 100, False),
    (SeasonKind.TOUR, 100, 90, True),
    (SeasonKind.TOUR, 100, 110, True),
    (SeasonKind.CASH, 100, 90, False),
    (SeasonKind.CASH, 100, 100, False),
    (SeasonKind.CASH, 100, 110, True),
]


@pytest.mark.parametrize(("kind", "buyin", "payout", "expected"), CASES)
def test_python_rule_agrees_with_the_query(kind, buyin, payout, expected):
    game = make_game(make_season(kind=kind))
    make_result(game, make_player(), buyin=buyin, payout=payout)

    annotated = stats.annotate_leftover_check(Game.objects.all()).get()

    assert stats.leftover_mismatch(kind, buyin, payout) is expected
    assert annotated.leftover_mismatch is expected
