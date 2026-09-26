from django.conf import settings
from django.db.models import Max, Q
from django.utils.functional import SimpleLazyObject

from club.models import Season, SeasonKind


def _latest_years() -> dict[str, int | None]:
    return Season.objects.aggregate(
        tour=Max("year", filter=Q(kind=SeasonKind.TOUR)),
        cash=Max("year", filter=Q(kind=SeasonKind.CASH)),
    )


def site(request):
    """Header nav targets and the indexing flag for every template.

    ``latest_years`` is lazy: the query runs only when a template reads it (the admin never does).
    """
    return {
        "latest_years": SimpleLazyObject(_latest_years),
        "site_indexing": settings.SITE_INDEXING,
    }
