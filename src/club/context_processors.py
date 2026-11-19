from django.conf import settings
from django.db.models import Exists, Max, OuterRef, Q
from django.utils.functional import SimpleLazyObject

from club.models import Game, Season, SeasonKind


def _latest_years() -> dict[str, int | None]:
    """Per kind, the latest season with a finished game (a season created ahead, or by a game
    still being played, is not where the nav leads); else the latest season. One query."""
    played = Q(Exists(Game.objects.finished().filter(season=OuterRef("pk"))))
    tour, cash = Q(kind=SeasonKind.TOUR), Q(kind=SeasonKind.CASH)
    years = Season.objects.aggregate(
        tour_played=Max("year", filter=tour & played),
        tour=Max("year", filter=tour),
        cash_played=Max("year", filter=cash & played),
        cash=Max("year", filter=cash),
    )
    return {
        kind: years[f"{kind}_played"] if years[f"{kind}_played"] is not None else years[kind]
        for kind in ("tour", "cash")
    }


def site(request):
    """Header nav targets and the indexing flag for every template.

    ``latest_years`` is lazy: the query runs only when a template reads it (the admin never does).
    """
    return {
        "latest_years": SimpleLazyObject(_latest_years),
        "site_indexing": settings.SITE_INDEXING,
    }
