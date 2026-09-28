import uuid

import pytest
from django.contrib.auth.models import Group, User
from django.utils import timezone

from club.models import Game, SeasonKind
from club.tests.factories import make_game, make_player, make_season
from live import actions


@pytest.fixture
def organizer(db):
    user = User.objects.create_user("test-organizer", is_staff=True)
    user.groups.add(Group.objects.get(name="Organizer"))
    return user


def key() -> uuid.UUID:
    return uuid.uuid4()


def live_game(kind: str, **season_fields) -> Game:
    stage = Game.Stage.REBUYS if kind == SeasonKind.TOUR else Game.Stage.CASH
    season = make_season(2026, kind, **season_fields)
    return make_game(season, day=28, month=9, live_stage=stage, started_at=timezone.now())


def seat(game, *names):
    """Seat invented players through the real action; return their results in order."""
    results = []
    for name in names:
        player = make_player(name)
        outcome = actions.seat(game.pk, key(), None, player.pk)
        results.append(outcome.action.result)
    return results


@pytest.fixture
def tour(db):
    """A live tournament in stage 1 with Альфа, Браво, Чарли seated.

    Entry 100 (not the default 50): the split tests are worked out on a bank of 300.
    """
    game = live_game(SeasonKind.TOUR, entry_price=100)
    return game, seat(game, "Альфа", "Браво", "Чарли")


@pytest.fixture
def cash(db):
    """A live cash game (step 50, 100 chips per lei) with Альфа and Браво seated."""
    game = live_game(SeasonKind.CASH)
    return game, seat(game, "Альфа", "Браво")
