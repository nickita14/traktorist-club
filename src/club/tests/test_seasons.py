"""club.seasons: a new season copies the rules and the sheet management of the previous one."""

import pytest

from club.models import Season, SeasonKind
from club.seasons import INHERITED, previous_season, season_for
from club.tests.factories import make_season, make_structure

pytestmark = pytest.mark.django_db

RULES_2026 = {
    "chips_per_lei": 50,
    "paid_places": 2,
    "entry_price": 150,
    "rebuy_price": 70,
    "addon_price": 60,
    "rebuy_minutes": 90,
    "payout_weights": "6,4",
    "payout_round": 10,
    "cash_step": 20,
}


def test_copies_every_rule_and_the_sheet_flag():
    structure = make_structure()
    previous = make_season(
        2026, SeasonKind.TOUR, default_blinds=structure, sheet_managed=True, **RULES_2026
    )

    season = season_for(SeasonKind.TOUR, 2027)

    assert (season.year, season.kind) == (2027, SeasonKind.TOUR)
    for field in INHERITED:
        assert getattr(season, field) == getattr(previous, field), field
    assert season.sheet_managed is True
    assert season.default_blinds == structure


def test_an_unmanaged_previous_season_gives_an_unmanaged_one():
    make_season(2026, SeasonKind.CASH, sheet_managed=False, cash_step=20)
    season = season_for(SeasonKind.CASH, 2027)
    assert (season.sheet_managed, season.cash_step) == (False, 20)


def test_nearest_earlier_year_of_the_same_kind():
    make_season(2024, SeasonKind.TOUR, entry_price=200, sheet_managed=False)
    make_season(2025, SeasonKind.TOUR, entry_price=150, sheet_managed=True)
    make_season(2026, SeasonKind.CASH, chips_per_lei=10)  # another kind never counts
    season = season_for(SeasonKind.TOUR, 2026)
    assert (season.entry_price, season.sheet_managed, season.chips_per_lei) == (150, True, 100)


def test_a_later_season_is_never_copied():
    make_season(2026, SeasonKind.TOUR, entry_price=150, sheet_managed=True)
    season = season_for(SeasonKind.TOUR, 2025)  # backfilling an older year
    assert (season.entry_price, season.sheet_managed) == (50, False)
    assert previous_season(SeasonKind.TOUR, 2025) is None


def test_first_season_of_a_kind_gets_the_defaults():
    season = season_for(SeasonKind.CASH, 2026)
    assert (season.chips_per_lei, season.cash_step, season.sheet_managed) == (100, 50, False)


def test_explicit_flag_wins_for_a_new_season():
    make_season(2026, SeasonKind.TOUR, sheet_managed=False, entry_price=150)
    season = season_for(SeasonKind.TOUR, 2027, sheet_managed=True)
    assert (season.sheet_managed, season.entry_price) == (True, 150)


def test_an_existing_season_is_returned_unchanged():
    make_season(2026, SeasonKind.TOUR, sheet_managed=True, entry_price=150)
    existing = make_season(2027, SeasonKind.TOUR, sheet_managed=False, entry_price=80)

    season = season_for(SeasonKind.TOUR, 2027, sheet_managed=True)

    assert season == existing
    season.refresh_from_db()
    assert (season.sheet_managed, season.entry_price) == (False, 80)
    assert Season.objects.count() == 2


def test_sheet_title_is_not_inherited():
    assert "sheet_title" not in INHERITED
