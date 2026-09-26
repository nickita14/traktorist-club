"""All derived numbers live here: views and admin call these helpers, nothing else aggregates.

Totals are conditional aggregates over one many-to-one join path (results -> game -> season),
so each helper is a single query with no fan-out and no N+1. Scopes go into the aggregate's
``filter=`` rather than ``.filter()``, which keeps rows without results (they get zeros).
"""

from collections.abc import Hashable, Mapping

from django.db.models import (
    BooleanField,
    Count,
    ExpressionWrapper,
    F,
    IntegerField,
    Q,
    QuerySet,
    Sum,
)
from django.db.models.functions import Coalesce

from club.models import Game, Player, Result, Season, SeasonKind

RESULT_NET = ExpressionWrapper(F("payout") - F("buyin"), output_field=IntegerField())


def _difference(minuend: str, subtrahend: str) -> ExpressionWrapper:
    return ExpressionWrapper(F(minuend) - F(subtrahend), output_field=IntegerField())


def _player_totals(path: str, scope: Q) -> dict:
    """Annotations for a model that reaches Result through ``path``, limited to ``scope``."""

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
    return {
        "games_played": Count(path, filter=only()),
        "buyin_total": Coalesce(Sum(f"{path}__buyin", filter=only()), 0),
        "payout_total": Coalesce(Sum(f"{path}__payout", filter=only()), 0),
        "net": _difference("payout_total", "buyin_total"),
        "itm": Count(path, filter=only(itm)),
        "first_places": places(1),
        "second_places": places(2),
        "third_places": places(3),
    }


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


def season_standings(season: Season) -> QuerySet[Player]:
    """Players who played in ``season``, best net first."""
    return (
        annotate_player_totals(Player.objects.all(), season=season)
        .filter(games_played__gt=0)
        .order_by("-net", "name", "nickname")
    )


def all_time_standings(kind: str | None = None) -> QuerySet[Player]:
    """Players across all seasons (optionally one kind), best net first."""
    return (
        annotate_player_totals(Player.objects.all(), kind=kind)
        .filter(games_played__gt=0)
        .order_by("-net", "name", "nickname")
    )


def player_season_breakdown(player: Player) -> QuerySet[Season]:
    """Seasons ``player`` played in, each with that player's totals (same fields as above)."""
    return (
        Season.objects.annotate(
            **_player_totals("games__results", Q(games__results__player=player))
        )
        .filter(games_played__gt=0)
        .order_by("-year", "kind")
    )


def annotate_game_totals(games: QuerySet[Game]) -> QuerySet[Game]:
    """Add players_count, buyin_total, payout_total and leftover (buy-ins minus payouts)."""
    return games.annotate(
        players_count=Count("results"),
        buyin_total=Coalesce(Sum("results__buyin"), 0),
        payout_total=Coalesce(Sum("results__payout"), 0),
        leftover=_difference("buyin_total", "payout_total"),
    )


# The one leftover rule (over annotate_game_totals fields). Tour buy-ins must be paid out in full;
# a cash game may keep rounding change in the pot but can never pay out more than was bought in.
LEFTOVER_MISMATCH = Q(season__kind=SeasonKind.TOUR) & ~Q(leftover=0) | Q(
    season__kind=SeasonKind.CASH, leftover__lt=0
)


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


def annotate_season_totals(seasons: QuerySet[Season]) -> QuerySet[Season]:
    """Add games_count and players_count (distinct players who played at least once)."""
    return seasons.annotate(
        games_count=Count("games", distinct=True),
        players_count=Count("games__results__player", distinct=True),
    )


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
