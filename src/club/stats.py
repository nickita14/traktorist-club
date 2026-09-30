"""All derived numbers live here: views and admin call these helpers, nothing else aggregates.

Totals are conditional aggregates over one many-to-one join path (results -> game -> season),
so each helper is a single query with no fan-out and no N+1. Scopes go into the aggregate's
``filter=`` rather than ``.filter()``, which keeps rows without results (they get zeros).

A game recorded at the table counts nowhere until its results are saved (``Game.live_stage`` is
empty again): every per-player and per-season helper here sees finished games only. Per-game
totals (annotate_game_totals) still work on a live game.
"""

import datetime
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from django.db.models import (
    BooleanField,
    Case,
    Count,
    ExpressionWrapper,
    F,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
    Window,
)
from django.db.models.functions import Coalesce, Rank

from club.models import Game, Player, Result, Season, SeasonKind

RESULT_NET = ExpressionWrapper(F("payout") - F("buyin"), output_field=IntegerField())

# Season order within a year, as in the nav: tournaments first, then cash.
SEASON_KIND_ORDER = Case(
    When(kind=SeasonKind.TOUR, then=Value(0)), default=Value(1), output_field=IntegerField()
)


def _difference(minuend: str, subtrahend: str) -> ExpressionWrapper:
    return ExpressionWrapper(F(minuend) - F(subtrahend), output_field=IntegerField())


def _player_totals(path: str, scope: Q, prefix: str = "") -> dict:
    """Annotations for a model that reaches Result through ``path``, limited to ``scope``
    and to finished games.

    ``prefix`` goes in front of every annotation name, so several scopes fit on one row.
    """
    scope = scope & Q(**{f"{path}__game__live_stage": ""})

    def only(extra: Q | None = None) -> Q | None:
        condition = scope & extra if extra is not None else scope
        return condition or None

    def places(place: int) -> Count:
        return Count(path, filter=only(Q(**{f"{path}__place": place})))

    itm = Q(
        **{
            f"{path}__game__season__kind": SeasonKind.TOUR,
            f"{path}__place__lte": F(f"{path}__game__season__paid_places"),
        }
    )
    totals = {
        "games_played": Count(path, filter=only()),
        "buyin_total": Coalesce(Sum(f"{path}__buyin", filter=only()), 0),
        "payout_total": Coalesce(Sum(f"{path}__payout", filter=only()), 0),
        "net": _difference(f"{prefix}payout_total", f"{prefix}buyin_total"),
        "itm": Count(path, filter=only(itm)),
        "first_places": places(1),
        "second_places": places(2),
        "third_places": places(3),
    }
    return {f"{prefix}{name}": value for name, value in totals.items()}


def annotate_result_net(results: QuerySet[Result]) -> QuerySet[Result]:
    """Add ``net`` (payout - buyin) to each result."""
    return results.annotate(net=RESULT_NET)


def annotate_player_totals(
    players: QuerySet[Player], *, season: Season | None = None, kind: str | None = None
) -> QuerySet[Player]:
    """Add games_played, buyin_total, payout_total, net, itm, first/second/third_places.

    Without ``season`` or ``kind`` the totals are all-time. Players without results get zeros.
    """
    scope = Q()
    if season is not None:
        scope &= Q(results__game__season=season)
    if kind is not None:
        scope &= Q(results__game__season__kind=kind)
    return players.annotate(**_player_totals("results", scope))


def _ranked(players: QuerySet[Player], order_by: str, descending: bool) -> QuerySet[Player]:
    """Order by ``order_by`` (an annotation) with ``rank``; ties by net, highest first, then name.

    ``rank`` is competition ranking by ``order_by`` alone: equal values share a rank and the next
    one skips.
    """
    key = F(order_by).desc() if descending else F(order_by).asc()
    order = [key] if order_by == "net" else [key, F("net").desc()]
    return players.annotate(rank=Window(Rank(), order_by=key)).order_by(*order, "name", "nickname")


def season_standings(
    season: Season, *, order_by: str = "net", descending: bool = True
) -> QuerySet[Player]:
    """Players who played in ``season``, best net first (or by ``order_by``), with ``rank``."""
    players = annotate_player_totals(Player.objects.all(), season=season).filter(games_played__gt=0)
    return _ranked(players, order_by, descending)


def all_time_standings(
    kind: str | None = None, *, order_by: str = "net", descending: bool = True
) -> QuerySet[Player]:
    """Players across all seasons (optionally one kind), best net first (or by ``order_by``),
    with ``rank``."""
    players = annotate_player_totals(Player.objects.all(), kind=kind).filter(games_played__gt=0)
    return _ranked(players, order_by, descending)


def club_totals(kind: str | None = None) -> dict:
    """games_count, players_count (distinct) and buyin_total over all seasons, or one kind."""
    games = Game.objects.finished()
    if kind is not None:
        games = games.filter(season__kind=kind)
    return games.aggregate(
        games_count=Count("pk", distinct=True),
        players_count=Count("results__player", distinct=True),
        buyin_total=Coalesce(Sum("results__buyin"), 0),
    )


def player_index(order_by: str = "name", descending: bool = False) -> QuerySet[Player]:
    """Players with at least one game, all-time totals, by name or by ``order_by`` (``nickname``
    or an annotation of _player_totals). Players without a nickname come last when ordered by it,
    whichever the direction; ties go by name."""
    key = F(order_by).desc() if descending else F(order_by).asc()
    order = [key, "name", "nickname"]
    if order_by == "nickname":
        order.insert(0, Case(When(nickname="", then=1), default=0))
    return annotate_player_totals(Player.objects.all()).filter(games_played__gt=0).order_by(*order)


# Prefixes of annotate_player_card: all-time, tour only, cash only.
PLAYER_CARD_SCOPES = {
    "": Q(),
    "tour_": Q(results__game__season__kind=SeasonKind.TOUR),
    "cash_": Q(results__game__season__kind=SeasonKind.CASH),
}


def annotate_player_card(players: QuerySet[Player]) -> QuerySet[Player]:
    """The record card totals in one query.

    All-time fields as in annotate_player_totals, the same with ``tour_`` and ``cash_`` prefixes,
    and first_game_date / last_game_date (None for a player without games).
    """
    annotations = {}
    for prefix, scope in PLAYER_CARD_SCOPES.items():
        annotations |= _player_totals("results", scope, prefix)
    finished = Q(results__game__live_stage="")
    return players.annotate(
        **annotations,
        first_game_date=Min("results__game__date", filter=finished),
        last_game_date=Max("results__game__date", filter=finished),
    )


def player_season_breakdown(player: Player) -> QuerySet[Season]:
    """Seasons ``player`` played in, newest year first and tour before cash within a year, each
    with that player's totals (same fields as above)."""
    return (
        Season.objects.annotate(
            **_player_totals("games__results", Q(games__results__player=player))
        )
        .filter(games_played__gt=0)
        .order_by("-year", SEASON_KIND_ORDER)
    )


def annotate_game_totals(games: QuerySet[Game]) -> QuerySet[Game]:
    """Add players_count, buyin_total, payout_total, leftover (buy-ins minus payouts) and
    net_total (payouts minus buy-ins, the sum of the players' nets)."""
    return games.annotate(
        players_count=Count("results"),
        buyin_total=Coalesce(Sum("results__buyin"), 0),
        payout_total=Coalesce(Sum("results__payout"), 0),
        leftover=_difference("buyin_total", "payout_total"),
        net_total=_difference("payout_total", "buyin_total"),
    )


def _game_number(season: str, date: str) -> Subquery:
    """Number of a game within its season: 1 for the earliest date.

    Counts the season's games up to this date; dates are unique per season, so this is the
    date order. ``season`` and ``date`` are paths from the outer model to the game's fields.
    """
    earlier = (
        Game.objects.finished()
        .filter(season=OuterRef(season), date__lte=OuterRef(date))
        .order_by()
        .values("season")
        .annotate(count=Count("pk"))
        .values("count")
    )
    return Subquery(earlier, output_field=IntegerField())


def annotate_game_number(games: QuerySet[Game]) -> QuerySet[Game]:
    """Add ``number``, the game's position in its season (see _game_number)."""
    return games.annotate(number=_game_number("season", "date"))


def annotate_live_number(games: QuerySet[Game]) -> QuerySet[Game]:
    """Add ``number`` to live games: the number the game will get in its season once finished
    (finished games up to its date, plus itself)."""
    return games.annotate(number=Coalesce(_game_number("season", "date"), 0) + 1)


# The one leftover rule (over annotate_game_totals fields). Tour buy-ins must be paid out in full;
# a cash game may keep rounding change in the pot but can never pay out more than was bought in.
# A live game is not checked until its results are saved.
LEFTOVER_MISMATCH = (
    Q(season__kind=SeasonKind.TOUR) & ~Q(leftover=0)
    | Q(season__kind=SeasonKind.CASH, leftover__lt=0)
) & Q(live_stage="")


def leftover_mismatch(kind: str, buyin_total: int, payout_total: int) -> bool:
    """LEFTOVER_MISMATCH for totals that are not saved yet (the live results screen)."""
    leftover = buyin_total - payout_total
    if kind == SeasonKind.TOUR:
        return leftover != 0
    return leftover < 0


def annotate_leftover_check(games: QuerySet[Game]) -> QuerySet[Game]:
    """annotate_game_totals plus ``leftover_mismatch`` (bool) from LEFTOVER_MISMATCH."""
    return annotate_game_totals(games).annotate(
        leftover_mismatch=ExpressionWrapper(LEFTOVER_MISMATCH, output_field=BooleanField())
    )


def leftover_warning(game: Game) -> str | None:
    """Warning text for a game annotated by annotate_leftover_check, or None if it is fine."""
    if not game.leftover_mismatch:
        return None
    if game.season.kind == SeasonKind.TOUR:
        return (
            f"Остаток не сходится: в турнире закупки ({game.buyin_total}) и выплаты "
            f"({game.payout_total}) должны совпадать, разница {game.leftover}."
        )
    return (
        f"Остаток не сходится: в кэш-игре выплачено ({game.payout_total}) больше, "
        f"чем закуплено ({game.buyin_total}), разница {game.leftover}."
    )


def expected_buyin(season: Season, rebuys: int | None, addon: bool | None) -> int | None:
    """What a tournament buy-in should be at ``season``'s prices: entry, rebuys and the add-on.

    None when rebuys or the add-on is unknown (an imported game): nothing to compare with.
    """
    if rebuys is None or addon is None:
        return None
    return season.entry_price + rebuys * season.rebuy_price + (season.addon_price if addon else 0)


def buyin_warning(game: Game) -> str | None:
    """Warning text when a finished tournament's buy-ins differ from expected_buyin, else None.

    Only a warning: historical prices may differ from the season's current ones. One query
    (``game.season`` must be loaded).
    """
    season = game.season
    if season.kind != SeasonKind.TOUR or game.is_live:
        return None
    mismatches = []
    rows = game.results.order_by("player__name", "player__nickname").values_list(
        "player__name", "player__nickname", "buyin", "rebuys", "addon"
    )
    for name, nickname, buyin, rebuys, addon in rows:
        expected = expected_buyin(season, rebuys, addon)
        if expected is not None and buyin != expected:
            mismatches.append(f"{nickname or name} {buyin} вместо {expected}")
    if not mismatches:
        return None
    return (
        f"Закупка не сходится с ребаями и аддоном по ценам сезона (вход {season.entry_price}, "
        f"ребай {season.rebuy_price}, аддон {season.addon_price}): {', '.join(mismatches)}. "
        "Если цены тогда были другими, всё в порядке."
    )


def annotate_season_totals(seasons: QuerySet[Season]) -> QuerySet[Season]:
    """Add games_count, players_count, buyin_total, payout_total, leftover and last_game_date.

    players_count counts distinct players who played at least once; leftover is the season's pot
    (buy-ins minus payouts over all games); last_game_date is None for a season without games.
    """
    finished = Q(games__live_stage="")
    return seasons.annotate(
        games_count=Count("games", distinct=True, filter=finished),
        players_count=Count("games__results__player", distinct=True, filter=finished),
        buyin_total=Coalesce(Sum("games__results__buyin", filter=finished), 0),
        payout_total=Coalesce(Sum("games__results__payout", filter=finished), 0),
        leftover=_difference("buyin_total", "payout_total"),
        last_game_date=Max("games__date", filter=finished),
    )


def season_games(season: Season) -> QuerySet[Game]:
    """All games of ``season``, newest first. Two queries.

    Each game has the annotate_game_totals fields, ``number`` (see annotate_game_number) and
    ``winners``: results with place 1, player loaded. A tie for first gives several winners, a
    cash game none.
    """
    winners = Result.objects.filter(place=1).select_related("player").order_by("player__name")
    return (
        annotate_game_number(annotate_game_totals(season.games.finished()))
        .prefetch_related(Prefetch("results", queryset=winners, to_attr="winners"))
        .order_by("-date")
    )


def recent_games(season: Season, limit: int) -> QuerySet[Game]:
    """The latest ``limit`` games of season_games(``season``)."""
    return season_games(season)[:limit]


@dataclass(frozen=True)
class GameLink:
    pk: int
    date: datetime.date
    number: int


@dataclass(frozen=True)
class GamePosition:
    number: int
    previous: GameLink | None
    next: GameLink | None


def game_position(game: Game) -> GamePosition:
    """The game's number in its season and its neighbours by date. One query."""
    games = list(game.season.games.finished().order_by("date").values_list("pk", "date"))
    index = next(i for i, (pk, _) in enumerate(games) if pk == game.pk)

    def link(i: int) -> GameLink | None:
        if not 0 <= i < len(games):
            return None
        pk, date = games[i]
        return GameLink(pk, date, i + 1)

    return GamePosition(index + 1, link(index - 1), link(index + 1))


def same_evening(game: Game) -> Game | None:
    """The game of the other kind played on the same date, with ``number``, or None."""
    others = Game.objects.finished().filter(date=game.date).exclude(season__kind=game.season.kind)
    return annotate_game_number(others.select_related("season")).first()


def chips_value(chips: int, chips_per_lei: int) -> Fraction:
    """Exact value of a chip stack in lei; real stacks need not be whole lei."""
    return Fraction(chips, chips_per_lei)


@dataclass(frozen=True)
class LiveTotals:
    players: int
    in_game: int
    bank: int
    paid_out: int
    rebuys: int
    addons: int
    # Cash only: what the players who left put in the pot so far (chip value minus cash paid).
    pot: Fraction


def live_totals(game: Game) -> LiveTotals:
    """Running totals for the live game screens. One query (``game.season`` must be loaded).

    Players still in the game have an empty ``out_order``.
    """
    left = Q(chips_out__isnull=False)
    row = game.results.aggregate(
        players=Count("pk"),
        in_game=Count("pk", filter=Q(out_order__isnull=True)),
        bank=Coalesce(Sum("buyin"), 0),
        paid_out=Coalesce(Sum("payout"), 0),
        rebuys=Coalesce(Sum("rebuys"), 0),
        addons=Count("pk", filter=Q(addon=True)),
        left_chips=Coalesce(Sum("chips_out", filter=left), 0),
        left_payout=Coalesce(Sum("payout", filter=left), 0),
    )
    pot = chips_value(row.pop("left_chips"), game.season.chips_per_lei) - row.pop("left_payout")
    return LiveTotals(**row, pot=pot)


def elimination_places[K: Hashable](out_orders: Mapping[K, int | None]) -> dict[K, int | None]:
    """Places from the order players left a tournament: the first one out gets the last place.

    ``out_orders`` has every player of the game; None means still in. Gaps in the order (a player
    brought back) do not matter, only the sequence. The last player standing gets 1st place;
    while several are still in, they have no place yet.
    """
    count = len(out_orders)
    out = sorted(
        (key for key, order in out_orders.items() if order is not None),
        key=lambda key: out_orders[key],
    )
    places: dict[K, int | None] = dict.fromkeys(out_orders)
    for index, key in enumerate(out):
        places[key] = count - index
    still_in = [key for key, order in out_orders.items() if order is None]
    if len(still_in) == 1:
        places[still_in[0]] = 1
    return places


def game_results(game: Game) -> list[Result]:
    """Results of ``game``, player loaded. One query.

    A tournament is in place order (1, 2, 3, ...), players without a place after them, best net
    first; a cash game is best net first. Each result has ``net`` and ``pot``: what it left in
    the pot, chip value minus payout (a Fraction), or None when chips_out is unknown or the game
    is a tournament.
    """
    order = ["-net", "player__name", "player__nickname"]
    if game.season.kind == SeasonKind.TOUR:
        order.insert(0, F("place").asc(nulls_last=True))
    results = list(annotate_result_net(game.results.select_related("player")).order_by(*order))
    for result in results:
        result.pot = (
            chips_value(result.chips_out, game.season.chips_per_lei) - result.payout
            if result.chips_out is not None
            else None
        )
    return results


def player_results(player: Player) -> QuerySet[Result]:
    """The player's results, newest first, with ``net``, ``game_number`` and the game's season.

    On a date with both kinds, cash comes before tour (the reverse of player_net_timeline).
    """
    return (
        annotate_result_net(
            player.results.filter(game__live_stage="").select_related("game__season")
        )
        .annotate(game_number=_game_number("game__season", "game__date"))
        .order_by("-game__date", "game__season__kind")
    )


@dataclass(frozen=True)
class TimelinePoint:
    date: datetime.date
    net: int


def player_net_timeline(player: Player) -> list[TimelinePoint]:
    """Cumulative net after each of the player's games, oldest first. One query.

    On a date with both kinds the tournament comes first ("tour" sorts after "cash").
    """
    rows = (
        player.results.filter(game__live_stage="")
        .order_by("game__date", "-game__season__kind")
        .values_list("game__date", "buyin", "payout")
    )
    timeline, running = [], 0
    for date, buyin, payout in rows:
        running += payout - buyin
        timeline.append(TimelinePoint(date, running))
    return timeline


def suggest_places[K: Hashable](payouts: Mapping[K, int], paid_places: int) -> dict[K, int | None]:
    """Suggest places from payouts using competition ranking.

    Only positive payouts are ranked. Equal payouts share a place and the next place skips
    (300, 200, 200, 100 -> 1, 2, 2, 4). Ranks above ``paid_places`` become None, so a tie at
    the cutoff keeps everyone in it (paid_places=3: 400, 300, 200, 200 -> 1, 2, 3, 3).
    """
    ranked = sorted((payout for payout in payouts.values() if payout > 0), reverse=True)
    places: dict[K, int | None] = {}
    for key, payout in payouts.items():
        # In a descending list, the first index of a value is the number of larger payouts.
        rank = ranked.index(payout) + 1 if payout > 0 else None
        places[key] = rank if rank is not None and rank <= paid_places else None
    return places


def split_prizes[K: Hashable](
    bank: int, weights: Sequence[int], places: Mapping[K, int | None], step: int
) -> dict[K, int]:
    """Suggested payouts for the prize places: the bank split by ``weights`` (one per paid place).

    Only players whose place is a prize place (1..len(weights)) take part, each with the weight
    of their place, so blank places (a deal) or missing ones just drop out of the split. Each
    share is rounded down to ``step`` and the remainder goes to the best place, so the payouts
    always add up to the bank. Bank 1050, weights 3,2,1, step 50 -> 550, 350, 150.
    Nobody in a prize place: nothing to split, an empty dict.
    """
    prized = {
        key: weights[place - 1]
        for key, place in places.items()
        if place is not None and 1 <= place <= len(weights)
    }
    if not prized:
        return {}
    total = sum(prized.values())
    payouts = {key: bank * weight // total // step * step for key, weight in prized.items()}
    best = min(prized, key=lambda key: places[key])
    payouts[best] += bank - sum(payouts.values())
    return payouts


def suggest_places_for_game(game: Game) -> dict[int, int | None]:
    """Suggested place per Result.pk for ``game``."""
    payouts = dict(Result.objects.filter(game=game).values_list("pk", "payout"))
    return suggest_places(payouts, game.season.paid_places)


def apply_suggested_places(game: Game, *, overwrite: bool = False) -> int:
    """Store suggested places for a tour game and return how many rows changed.

    Only empty places are filled unless ``overwrite`` is set: the stored place is the source of
    truth. Cash games are left alone.
    """
    if game.season.kind != SeasonKind.TOUR:
        return 0
    results = list(Result.objects.filter(game=game))
    suggested = suggest_places({r.pk: r.payout for r in results}, game.season.paid_places)
    changed = []
    for result in results:
        if (overwrite or result.place is None) and result.place != suggested[result.pk]:
            result.place = suggested[result.pk]
            changed.append(result)
    Result.objects.bulk_update(changed, ["place"])
    return len(changed)
