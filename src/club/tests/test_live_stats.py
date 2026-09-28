from fractions import Fraction

import pytest
from django.utils import timezone

from club.models import Game, SeasonKind
from club.stats import elimination_places, live_totals
from club.tests.factories import make_game, make_player, make_result, make_season


class TestEliminationPlaces:
    def test_first_out_gets_the_last_place(self):
        assert elimination_places({"a": 2, "b": 1, "c": None}) == {"a": 2, "b": 3, "c": 1}

    def test_several_still_in_have_no_place(self):
        assert elimination_places({"a": None, "b": 1, "c": None, "d": 2}) == {
            "a": None,
            "b": 4,
            "c": None,
            "d": 3,
        }

    def test_gaps_in_the_order_do_not_matter(self):
        # Out orders 1 and 3: the player who was 2nd out came back.
        assert elimination_places({"a": 3, "b": 1, "c": None, "d": None}) == {
            "a": 3,
            "b": 4,
            "c": None,
            "d": None,
        }

    def test_late_seating_shifts_places(self):
        before = elimination_places({"a": 1, "b": None, "c": None})
        after = elimination_places({"a": 1, "b": None, "c": None, "late": None})
        assert (before["a"], after["a"]) == (3, 4)

    def test_nobody_out_yet(self):
        assert elimination_places({"a": None, "b": None}) == {"a": None, "b": None}

    def test_empty(self):
        assert elimination_places({}) == {}


@pytest.mark.django_db
class TestLiveTotals:
    def test_tour(self):
        game = make_game(
            make_season(kind=SeasonKind.TOUR),
            live_stage=Game.Stage.FINAL,
            started_at=timezone.now(),
        )
        make_result(game, make_player(), buyin=250, rebuys=2, addon=True)
        make_result(game, make_player(), buyin=150, rebuys=1, addon=False, out_order=1)
        make_result(game, make_player(), buyin=100, rebuys=0, addon=True)

        totals = live_totals(game)

        assert (totals.players, totals.in_game, totals.bank) == (3, 2, 500)
        assert (totals.rebuys, totals.addons, totals.paid_out) == (3, 2, 0)
        assert totals.pot == 0

    def test_cash_pot_counts_players_who_left(self):
        game = make_game(
            make_season(kind=SeasonKind.CASH, chips_per_lei=100),
            live_stage=Game.Stage.CASH,
            started_at=timezone.now(),
        )
        make_result(game, make_player(), buyin=100, payout=112, chips_out=11230, out_order=1)
        make_result(game, make_player(), buyin=50, payout=45, chips_out=4570, out_order=2)
        make_result(game, make_player(), buyin=150)  # still playing

        totals = live_totals(game)

        assert (totals.bank, totals.paid_out, totals.in_game) == (300, 157, 1)
        assert totals.pot == Fraction(30, 100) + Fraction(70, 100)  # 112,30 - 112 and 45,70 - 45

    def test_empty_game(self):
        game = make_game(
            make_season(kind=SeasonKind.CASH), live_stage=Game.Stage.CASH, started_at=timezone.now()
        )
        totals = live_totals(game)
        assert (totals.players, totals.bank, totals.pot) == (0, 0, 0)
