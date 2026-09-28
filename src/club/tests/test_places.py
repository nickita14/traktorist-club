import pytest

from club.models import SeasonKind
from club.stats import (
    apply_suggested_places,
    split_prizes,
    suggest_places,
    suggest_places_for_game,
)
from club.tests.factories import make_game, make_player, make_result, make_season


def places(payouts, paid_places=3):
    """Suggest places for payouts given in order; return the places in the same order."""
    result = suggest_places(dict(enumerate(payouts)), paid_places)
    return [result[i] for i in range(len(payouts))]


class TestSuggestPlaces:
    def test_simple(self):
        assert places([500, 300, 200]) == [1, 2, 3]

    def test_input_order_does_not_matter(self):
        assert places([200, 500, 300]) == [3, 1, 2]

    def test_tie_in_the_middle_skips_next_place(self):
        # Competition ranking: 1, 2, 2, 4; the 4th is outside paid_places=3.
        assert places([300, 200, 200, 100]) == [1, 2, 2, None]
        assert places([300, 200, 200, 100], paid_places=4) == [1, 2, 2, 4]

    def test_tie_at_the_top(self):
        assert places([300, 300, 100]) == [1, 1, 3]

    def test_tie_at_the_cutoff_keeps_everyone_in_it(self):
        assert places([400, 300, 200, 200]) == [1, 2, 3, 3]

    def test_zero_payouts_get_no_place(self):
        assert places([150, 0, 100, 0]) == [1, None, 2, None]

    def test_all_zero(self):
        assert places([0, 0, 0]) == [None, None, None]

    def test_fewer_payers_than_paid_places(self):
        assert places([250, 0]) == [1, None]

    def test_positive_payout_beyond_paid_places(self):
        assert places([400, 300, 200, 100]) == [1, 2, 3, None]

    def test_paid_places_respected(self):
        assert places([400, 300, 200], paid_places=2) == [1, 2, None]

    def test_empty(self):
        assert suggest_places({}, 3) == {}


@pytest.mark.django_db
class TestGamePlaces:
    @pytest.fixture
    def game(self):
        game = make_game(make_season(kind=SeasonKind.TOUR))
        self.first = make_result(game, make_player("Первый"), payout=150)
        self.second = make_result(game, make_player("Второй"), payout=100, place=5)
        self.third = make_result(game, make_player("Третий"), payout=0)
        return game

    def test_suggest_for_game(self, game):
        assert suggest_places_for_game(game) == {
            self.first.pk: 1,
            self.second.pk: 2,
            self.third.pk: None,
        }

    def test_apply_fills_only_empty_places(self, game):
        assert apply_suggested_places(game) == 1
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        assert (self.first.place, self.second.place) == (1, 5)  # manual 5 kept

    def test_apply_with_overwrite(self, game):
        assert apply_suggested_places(game, overwrite=True) == 2
        self.second.refresh_from_db()
        assert self.second.place == 2

    def test_apply_is_idempotent(self, game):
        apply_suggested_places(game)
        assert apply_suggested_places(game) == 0

    def test_cash_games_untouched(self):
        game = make_game(make_season(kind=SeasonKind.CASH))
        result = make_result(game, make_player(), payout=150)

        assert apply_suggested_places(game, overwrite=True) == 0
        result.refresh_from_db()
        assert result.place is None


def split(bank, places=(1, 2, 3), weights=(3, 2, 1), step=50):
    """Payouts for players given by their places, in the same order (None: no prize)."""
    result = split_prizes(bank, weights, dict(enumerate(places)), step)
    return [result.get(i) for i in range(len(places))]


class TestSplitPrizes:
    def test_even_split(self):
        assert split(1200) == [600, 400, 200]

    def test_rounded_down_with_the_remainder_to_first(self):
        # 525, 350, 175 round down to 500, 350, 150; the 50 left over goes to 1st place.
        assert split(1050) == [550, 350, 150]

    def test_total_always_equals_the_bank(self):
        for bank in range(0, 3001, 10):
            assert sum(split(bank)) == bank

    def test_other_rounding_step(self):
        assert split(1050, step=10) == [530, 350, 170]
        assert split(1051, step=1) == [526, 350, 175]

    def test_bank_smaller_than_the_step(self):
        assert split(40) == [40, 0, 0]

    def test_input_order_does_not_matter(self):
        assert split(1200, places=(3, 1, 2)) == [200, 600, 400]

    def test_players_outside_the_prizes_get_nothing(self):
        assert split(1200, places=(1, 2, 3, 4, None)) == [600, 400, 200, None, None]

    def test_blank_places_drop_out_of_the_split(self):
        # A deal: only 1st and 2nd are filled in, so the bank goes 3:2 between them.
        assert split(1000, places=(1, 2, None)) == [600, 400, None]

    def test_missing_first_place_gives_the_remainder_to_the_best_one(self):
        assert split(1050, places=(None, 2, 3)) == [None, 700, 350]

    def test_tie_takes_the_weight_of_the_shared_place(self):
        # Weights 3:2:2 give 428, 285, 285: 400, 250, 250, and the 100 left over to 1st.
        assert split(1000, places=(1, 2, 2)) == [500, 250, 250]

    def test_nobody_in_a_prize_place(self):
        assert split(1000, places=(None, 4)) == [None, None]

    def test_empty(self):
        assert split_prizes(1000, [3, 2, 1], {}, 50) == {}
