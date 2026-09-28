from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import InvalidPage, Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from club import charts, stats
from club.models import Game, Player, Season, SeasonKind
from club.sorting import CASH_KEYS, TOUR_KEYS, parse_sort

RECENT_GAMES = 8
PLAYER_RECENT_GAMES = 10
PLAYER_GAMES_PER_PAGE = 50
# The cash note shows the chip rate for a round sum, like on the club's paper sheets.
RATE_EXAMPLE_LEI = 50

NAV_SECTION = {SeasonKind.TOUR: "tour", SeasonKind.CASH: "cash"}


def home(request):
    season = Season.objects.filter(kind=SeasonKind.TOUR).order_by("-year").first()
    return redirect(season if season is not None else "all_time")


def _season(year: int, kind: str) -> Season:
    seasons = stats.annotate_season_totals(Season.objects.filter(kind=kind))
    return get_object_or_404(seasons, year=year)


def season_standings(request, year: int, kind: str):
    season = _season(year, kind)
    years = list(Season.objects.filter(kind=kind).order_by("year").values_list("year", flat=True))
    sort = parse_sort(request.GET, cash=kind == SeasonKind.CASH)
    return render(
        request,
        "club/season_standings.html",
        {
            "season": season,
            "standings": stats.season_standings(
                season, order_by=sort.field, descending=sort.descending
            ),
            "sort": sort,
            # The year switcher keeps the order.
            "sort_suffix": f"?{sort.query}" if sort.query else "",
            "recent_games": stats.recent_games(season, RECENT_GAMES),
            "years": years,
            # No stored "closed" flag: the season of the current year is the running one.
            "is_ongoing": season.year == timezone.localdate().year,
            "cash": kind == SeasonKind.CASH,
            "nav_section": NAV_SECTION[kind],
        },
    )


def season_games(request, year: int, kind: str):
    season = _season(year, kind)
    return render(
        request,
        "club/season_games.html",
        {
            "season": season,
            "games": stats.season_games(season),
            "nav_section": NAV_SECTION[kind],
        },
    )


def game_detail(request, pk: int):
    # A game still being played has no public sheet until its results are saved.
    game = get_object_or_404(
        stats.annotate_leftover_check(Game.objects.finished().select_related("season")), pk=pk
    )
    chips_per_lei = game.season.chips_per_lei
    results = stats.game_results(game)
    return render(
        request,
        "club/game_detail.html",
        {
            "game": game,
            "season": game.season,
            "position": stats.game_position(game),
            "same_evening": stats.same_evening(game),
            "results": results,
            # Chip and pot columns only when someone's final stack was written down.
            "show_chips": any(result.chips_out is not None for result in results),
            "rate_example": {"chips": RATE_EXAMPLE_LEI * chips_per_lei, "lei": RATE_EXAMPLE_LEI},
            "nav_section": NAV_SECTION[game.season.kind],
        },
    )


def player_detail(request, slug: str):
    player = get_object_or_404(stats.annotate_player_card(Player.objects.all()), slug=slug)
    context = {"player": player, "nav_section": "players"}
    # A player created ahead of their first game gets the heading only.
    if player.games_played:
        timeline = stats.player_net_timeline(player)
        context |= {
            "seasons": stats.player_season_breakdown(player),
            "chart": charts.net_chart(timeline),
            "recent_results": stats.player_results(player)[:PLAYER_RECENT_GAMES],
            "has_more_games": player.games_played > PLAYER_RECENT_GAMES,
        }
    return render(request, "club/player_detail.html", context)


def player_games(request, slug: str):
    player = get_object_or_404(Player, slug=slug)
    paginator = Paginator(stats.player_results(player), PLAYER_GAMES_PER_PAGE)
    try:
        page = paginator.page(request.GET.get("page", 1))
    except InvalidPage as error:
        raise Http404("Нет такой страницы.") from error
    return render(
        request,
        "club/player_games.html",
        {"player": player, "page": page, "nav_section": "players"},
    )


def player_list(request):
    return render(
        request,
        "club/player_list.html",
        {"players": stats.player_index(), "nav_section": "players"},
    )


# Filter values of /all-time/: query value -> season kind (None is everything).
ALL_TIME_KINDS = {None: None, "tour": SeasonKind.TOUR, "cash": SeasonKind.CASH}


def all_time(request):
    filter_value = request.GET.get("kind")
    if filter_value not in ALL_TIME_KINDS:
        raise Http404("Нет такого формата.")
    kind = ALL_TIME_KINDS[filter_value]
    sort = parse_sort(request.GET, cash=kind == SeasonKind.CASH)
    return render(
        request,
        "club/all_time.html",
        {
            "kind": kind,
            "cash": kind == SeasonKind.CASH,
            "standings": stats.all_time_standings(
                kind, order_by=sort.field, descending=sort.descending
            ),
            "sort": sort,
            "kind_links": _kind_links(kind, sort),
            "totals": stats.club_totals(kind),
            "nav_section": "all-time",
        },
    )


def _kind_links(current: str | None, sort) -> list[dict]:
    """The format filter of /all-time/, keeping the order where the target table has that
    column (ITM does not exist on the cash table)."""
    links = []
    for value, label in [(None, "Все"), ("tour", "Турниры"), ("cash", "Кэш")]:
        params = {"kind": value} if value else {}
        keys = CASH_KEYS if value == SeasonKind.CASH else TOUR_KEYS
        if sort.key in keys:
            params |= sort.params
        query = urlencode(params)
        links.append(
            {
                "label": label,
                "url": reverse("all_time") + (f"?{query}" if query else ""),
                "current": ALL_TIME_KINDS[value] == current,
            }
        )
    return links


@require_GET
def robots_txt(request):
    # The blind timer display (/tablo/) stays out even when the rest may be indexed.
    rules = "Allow: /\nDisallow: /tablo/" if settings.SITE_INDEXING else "Disallow: /"
    return HttpResponse(f"User-agent: *\n{rules}\n", content_type="text/plain")
