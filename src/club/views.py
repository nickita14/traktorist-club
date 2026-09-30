from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import InvalidPage, Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from club import achievements, charts, stats
from club.models import Game, Player, Season, SeasonKind
from club.sorting import BADGES, CASH_STANDINGS, PLAYERS, TOUR_STANDINGS, parse_sort

RECENT_GAMES = 8
RECENT_AWARDS = 10
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
    sort = parse_sort(request.GET, CASH_STANDINGS if kind == SeasonKind.CASH else TOUR_STANDINGS)
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
    awards = achievements.load().game(game.pk)
    for result in results:
        result.badges = awards.badges.get(result.player_id, [])
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
            # Ranks and diplomas this game earned (badges are marked in the table).
            "reached": awards.reached,
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
            "awards": achievements.load().player(player.pk),
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


# The meta line of /players/: what the order is, then ", в обратном порядке" if flipped.
PLAYER_ORDER_LABELS = {
    "name": "по алфавиту",
    "nick": "по нику",
    "games": "по числу игр",
    "net": "по итогу",
}


def player_list(request):
    sort = parse_sort(request.GET, PLAYERS)
    label = PLAYER_ORDER_LABELS[sort.key]
    if sort.descending != PLAYERS.start(sort.key).descending:
        label += ", в обратном порядке"
    return render(
        request,
        "club/player_list.html",
        {
            "players": stats.player_index(sort.field, descending=sort.descending),
            "sort": sort,
            "order_label": label,
            "nav_section": "players",
        },
    )


# Filter values of /all-time/: query value -> season kind (None is everything).
ALL_TIME_KINDS = {None: None, "tour": SeasonKind.TOUR, "cash": SeasonKind.CASH}


def all_time(request):
    filter_value = request.GET.get("kind")
    if filter_value not in ALL_TIME_KINDS:
        raise Http404("Нет такого формата.")
    kind = ALL_TIME_KINDS[filter_value]
    sort = parse_sort(request.GET, CASH_STANDINGS if kind == SeasonKind.CASH else TOUR_STANDINGS)
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
        table = CASH_STANDINGS if value == SeasonKind.CASH else TOUR_STANDINGS
        if sort.key in table.fields:
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


def honors(request):
    """Доска почёта. Without ``?year=``: the latest year with games plus every running title;
    a year the switcher does not offer is a 404."""
    awards = achievements.load()
    years = awards.honor_years()
    default_year = awards.latest_game_year()
    requested = request.GET.get("year")
    if requested is None:
        year = default_year
    else:
        try:
            year = int(requested)
        except ValueError:
            raise Http404("Нет такого года.") from None
        if year not in years:
            raise Http404("Нет такого года.")
    sort = parse_sort(request.GET, BADGES)
    board = awards.honors(year, running=requested is None) if year is not None else None
    rules = awards.badge_rules()
    return render(
        request,
        "club/honors.html",
        {
            "board": board,
            "badge_rows": (
                achievements.sort_badge_rows(board.badge_rows, sort.key, sort.descending)
                if board
                else []
            ),
            "badge_columns": [
                (code, title, rules[code]) for code, title in achievements.BADGES.items()
            ],
            "ladders": awards.ladders,
            "year_links": _year_links(years, year, default_year, sort),
            "sort": sort,
            "recent_awards": awards.recent(RECENT_AWARDS),
            "nav_section": "honors",
        },
    )


def _year_links(years: list[int], current: int | None, default: int | None, sort) -> list[dict]:
    """The year switcher of /honors/, oldest first like the season pages. The default year
    links to the page without ``?year=`` (which also lists the running titles); the order of
    the badge table is kept."""
    links = []
    for year in sorted(years):
        params = ({} if year == default else {"year": year}) | sort.params
        query = urlencode(params)
        links.append(
            {
                "year": year,
                "url": reverse("honors") + (f"?{query}" if query else ""),
                "current": year == current,
            }
        )
    return links


@require_GET
def favicon(request):
    """Browsers ask for /favicon.ico on their own; the file itself is a (hashed) static file."""
    response = redirect(static("icons/favicon.ico"))
    response["Cache-Control"] = "public, max-age=86400"
    return response


@require_GET
def robots_txt(request):
    # The blind timer display (/tablo/) and the login stay out even when the rest may be indexed.
    if settings.SITE_INDEXING:
        rules = f"Allow: /\nDisallow: /tablo/\nDisallow: {reverse('login')}"
    else:
        rules = "Disallow: /"
    return HttpResponse(f"User-agent: *\n{rules}\n", content_type="text/plain")
