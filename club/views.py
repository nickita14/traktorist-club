from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from club import stats
from club.models import Season, SeasonKind

RECENT_GAMES = 8


def tour_standings(request, year: int):
    seasons = Season.objects.filter(kind=SeasonKind.TOUR)
    season = get_object_or_404(stats.annotate_season_totals(seasons), year=year)
    years = list(seasons.order_by("year").values_list("year", flat=True))
    return render(
        request,
        "club/season_standings.html",
        {
            "season": season,
            "standings": stats.season_standings(season),
            "recent_games": stats.recent_games(season, RECENT_GAMES),
            "years": years,
            # No stored "closed" flag: the season of the current year is the running one.
            "is_ongoing": season.year == timezone.localdate().year,
            "nav_section": "tour",
            "latest_tour_year": years[-1],
        },
    )
