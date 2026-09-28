from dataclasses import dataclass, field

from django.contrib.auth.models import Group, User
from django.contrib.postgres.expressions import ArraySubquery
from django.db.models import Exists, OuterRef
from django.db.models.functions import JSONObject
from django.utils.functional import SimpleLazyObject

from club import stats
from club.models import Game
from live.access import ORGANIZER_GROUP


@dataclass(frozen=True)
class LiveLink:
    pk: int
    kind: str
    number: int


@dataclass(frozen=True)
class Shortcuts:
    is_organizer: bool = False
    live_games: list[LiveLink] = field(default_factory=list)


NOBODY = Shortcuts()


def shortcuts(user) -> Shortcuts:
    """Organizer links and the games in progress, for the public header.

    Anonymous visitors cost no query. A signed-in user costs one: the Organizer membership and the
    live games come back together (the live games as an array subquery on the user's row).
    """
    if not user.is_authenticated:
        return NOBODY
    live = stats.annotate_live_number(Game.objects.live()).order_by("started_at", "pk")
    row = (
        User.objects.filter(pk=user.pk)
        .annotate(
            member=Exists(Group.objects.filter(user=OuterRef("pk"), name=ORGANIZER_GROUP)),
            live=ArraySubquery(
                live.values(json=JSONObject(pk="pk", kind="season__kind", number="number"))
            ),
        )
        .values("member", "live")
        .first()
    )
    if row is None or not (user.is_superuser or row["member"]):
        return NOBODY
    return Shortcuts(True, [LiveLink(**game) for game in row["live"]])


def organizer(request):
    """``organizer``: lazy Shortcuts, evaluated only when a template reads it."""
    return {"organizer": SimpleLazyObject(lambda: shortcuts(request.user))}
