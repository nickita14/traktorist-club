"""Live game screens for organizers at the table. Thin views over live.actions.

Pages render in full on a normal request. With htmx, a mutating request answers with the
fragment its form targets (``HX-Target``: the board, or the list on the seating page) plus an
out-of-band toast, so every phone shows the state the server holds.
"""

import datetime
import math
import uuid
from dataclasses import dataclass
from fractions import Fraction

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import F, Max, Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from club import stats
from club.models import Game, Player, Result, SeasonKind
from live import actions
from live.access import organizer_required
from live.actions import GameGone, Outcome, RuleError
from live.forms import ExitForm, NewPlayerForm, StartForm, parse_int
from live.models import LiveAction

SEAT_CANDIDATES = 30


def _game(pk: int) -> Game:
    return get_object_or_404(Game.objects.select_related("season"), pk=pk)


def _key(request) -> uuid.UUID | None:
    try:
        return uuid.UUID(request.POST.get("key", ""))
    except ValueError:
        return None


def _hx_redirect(url: str) -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


# Board.


@dataclass(frozen=True)
class Countdown:
    deadline: datetime.datetime
    remaining: datetime.timedelta
    elapsed: int  # seconds, for the progress bar
    total: int

    @property
    def over(self) -> bool:
        return self.remaining <= datetime.timedelta(0)

    @property
    def remaining_text(self) -> str:
        """'1:43' (hours:minutes), rounded up so it reaches 0:00 only at the deadline."""
        minutes = math.ceil(self.remaining.total_seconds() / 60)
        return f"{minutes // 60}:{minutes % 60:02d}"


def countdown(game: Game, now: datetime.datetime) -> Countdown:
    """Time left in stage 1: from the start, ``rebuy_minutes`` long. Display only."""
    total = datetime.timedelta(minutes=game.season.rebuy_minutes)
    start = timezone.localtime(game.started_at)
    deadline = start + total
    elapsed = min(max(now - start, datetime.timedelta(0)), total)
    return Countdown(deadline, deadline - now, int(elapsed.total_seconds()), int(total.seconds))


def board_context(game: Game) -> dict:
    results = list(
        stats.annotate_result_net(game.results.select_related("player")).order_by(
            "player__name", "player__nickname"
        )
    )
    places = stats.elimination_places({result.pk: result.out_order for result in results})
    for result in results:
        result.live_place = places[result.pk]
    now = timezone.localtime()
    return {
        "game": game,
        "season": game.season,
        "cash": game.season.kind == SeasonKind.CASH,
        "totals": stats.live_totals(game),
        "playing": [result for result in results if result.out_order is None],
        # Latest out first: that is the one a "вернуть" is most likely for.
        "out": sorted(
            (result for result in results if result.out_order is not None),
            key=lambda result: -result.out_order,
        ),
        "now": now,
        "countdown": countdown(game, now) if game.live_stage == Game.Stage.REBUYS else None,
        "poll_seconds": settings.LIVE_POLL_SECONDS,
        # The cash rate for one step, like the paper sheets: "5 000 фишек = 50 лей".
        "step_chips": game.season.cash_step * game.season.chips_per_lei,
    }


def _toast(outcome: Outcome | None, message: str | None, warning: bool) -> dict:
    if outcome is None:
        return {"toast_message": message, "toast_warning": warning}
    action = outcome.action
    text = action.summary if not outcome.replayed else f"Уже записано: {action.summary}"
    return {"toast_message": text, "toast_action": action if action.undoable else None}


def refresh(
    request,
    game_pk: int,
    *,
    outcome: Outcome | None = None,
    message: str | None = None,
    warning: bool = True,
):
    """The answer to a mutating htmx request: the targeted fragment plus a toast (the action
    with "отменить", or a plain message; a warning is a refused action)."""
    game = Game.objects.select_related("season").filter(pk=game_pk).first()
    if game is None:
        return _hx_redirect(reverse("live:index"))
    if not game.is_live:
        return _hx_redirect(reverse("game_detail", args=[game.pk]))
    if request.headers.get("HX-Target") == "player-list":
        context = seating_context(game, request.POST.get("q", ""))
        fragment = "live/_seating_list.html"
    else:
        context = board_context(game)
        fragment = "live/_board.html"
    context |= _toast(outcome, message, warning) | {"oob": True}
    return render(request, fragment, context)


def gone(request, game_pk: int):
    """The game was finished or cancelled on another phone."""
    game = Game.objects.filter(pk=game_pk).first()
    url = reverse("game_detail", args=[game.pk]) if game else reverse("live:index")
    return _hx_redirect(url) if _is_htmx(request) else redirect(url)


@organizer_required
@require_GET
def index(request):
    games = stats.annotate_game_totals(Game.objects.live().select_related("season")).order_by(
        "-started_at"
    )
    return render(request, "live/index.html", {"games": games})


@organizer_required
@require_http_methods(["GET", "POST"])
def start(request):
    if request.method == "POST":
        form = StartForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                outcome = actions.start_game(
                    data["key"],
                    request.user,
                    season=data["season_obj"],
                    kind=data["kind"],
                    date=data["date"],
                    location=data["location"],
                )
            except RuleError as error:
                form.add_error(None, str(error))
            else:
                game = outcome.action.game
                url = reverse("live:seating", args=[game.pk])
                return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    else:
        last = Game.objects.exclude(location="").order_by("-date").first()
        form = StartForm(
            initial={
                "key": uuid.uuid4(),
                "date": timezone.localdate(),
                "location": last.location if last else "",
            }
        )
    return render(request, "live/start.html", {"form": form})


@organizer_required
@require_GET
def board(request, pk: int):
    game = _game(pk)
    if not game.is_live:
        return gone(request, pk)
    context = board_context(game)
    if _is_htmx(request) and request.headers.get("HX-Target") == "board":
        return render(request, "live/_board.html", context)
    return render(request, "live/board.html", context)


def _tour_action(name: str):
    def call(pk, key, user, post):
        result = parse_int(post.get("result"), minimum=1)
        return getattr(actions, name)(pk, key, user, result)

    return call


ACTIONS = {
    "rebuy": _tour_action("rebuy"),
    "eliminate": _tour_action("eliminate"),
    "restore": _tour_action("restore"),
    "topup": _tour_action("top_up"),
    "return": _tour_action("cash_return"),
    "addon-on": lambda pk, key, user, post: actions.set_addon(
        pk, key, user, parse_int(post.get("result"), minimum=1), True
    ),
    "addon-off": lambda pk, key, user, post: actions.set_addon(
        pk, key, user, parse_int(post.get("result"), minimum=1), False
    ),
    "stage": lambda pk, key, user, post: actions.advance_stage(pk, key, user, post.get("from")),
    "close": lambda pk, key, user, post: actions.close_cash(pk, key, user),
    "cancel": lambda pk, key, user, post: actions.cancel_game(pk, key, user),
}


@organizer_required
@require_POST
def act(request, pk: int, name: str):
    """One button on the board. The form carries ``key`` and, for a row, ``result``."""
    operation = ACTIONS.get(name)
    key = _key(request)
    if operation is None or key is None:
        return HttpResponseBadRequest("Неизвестное действие.")
    try:
        outcome = operation(pk, key, request.user, request.POST)
    except RuleError as error:
        if name in ("close", "cancel"):
            # Their own pages already say why; the board shows the current state.
            return _hx_redirect(reverse("live:board", args=[pk]))
        return refresh(request, pk, message=str(error))
    except GameGone:
        return gone(request, pk)
    if name == "cancel":
        url = reverse("live:index")
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return refresh(request, pk, outcome=outcome)


@organizer_required
@require_POST
def undo(request, pk: int, action_pk: int):
    get_object_or_404(LiveAction, pk=action_pk, game_id=pk)
    try:
        outcome = actions.undo(action_pk, request.user)
    except RuleError as error:
        return refresh(request, pk, message=str(error))
    except GameGone:
        return gone(request, pk)
    return refresh(request, pk, message=f"Отменено: {outcome.action.summary}", warning=False)


# Seating.


def seat_candidates(game: Game, query: str):
    """Players not at this table yet, whoever played most recently first."""
    players = (
        Player.objects.exclude(pk__in=game.results.values("player"))
        .annotate(last_played=Max("results__game__date"))
        .order_by(F("last_played").desc(nulls_last=True), "name", "nickname")
    )
    query = query.strip()
    if query:
        players = players.filter(Q(name__icontains=query) | Q(nickname__icontains=query))
    return players[:SEAT_CANDIDATES]


def seating_context(game: Game, query: str) -> dict:
    seated = list(
        game.results.select_related("player").order_by("player__name", "player__nickname")
    )
    return {
        "game": game,
        "season": game.season,
        "query": query,
        "candidates": seat_candidates(game, query),
        "seated": seated,
        "can_seat": game.season.kind == SeasonKind.CASH or game.live_stage == Game.Stage.REBUYS,
    }


@organizer_required
@require_GET
def seating(request, pk: int):
    game = _game(pk)
    if not game.is_live:
        return gone(request, pk)
    context = seating_context(game, request.GET.get("q", ""))
    if _is_htmx(request) and request.headers.get("HX-Target") == "player-list":
        return render(request, "live/_seating_list.html", context)
    return render(
        request, "live/seating.html", context | {"new_player": NewPlayerForm(initial=_fresh())}
    )


def _fresh() -> dict:
    return {"key": uuid.uuid4()}


@organizer_required
@require_POST
def seat(request, pk: int):
    key, player = _key(request), parse_int(request.POST.get("player"), minimum=1)
    if key is None or player is None:
        return HttpResponseBadRequest("Нет игрока.")
    try:
        outcome = actions.seat(pk, key, request.user, player)
    except RuleError as error:
        return refresh(request, pk, message=str(error))
    except GameGone:
        return gone(request, pk)
    return refresh(request, pk, outcome=outcome)


@organizer_required
@require_POST
def seat_new(request, pk: int):
    """Create a player and seat them. Errors re-render the form in place."""
    form = NewPlayerForm(request.POST)
    if form.is_valid():
        data = form.cleaned_data
        try:
            outcome = actions.seat_new(
                pk, data["key"], request.user, data["name"], data["nickname"]
            )
        except ValidationError as error:
            form.add_error(None, error)
        except RuleError as error:
            return refresh(request, pk, message=str(error))
        except GameGone:
            return gone(request, pk)
        else:
            response = refresh(request, pk, outcome=outcome)
            # A clean form for the next new player.
            response.content += render_to_string(
                "live/_new_player.html",
                {"game": _game(pk), "new_player": NewPlayerForm(initial=_fresh()), "oob": True},
                request,
            ).encode()
            return response
    response = render(request, "live/_new_player.html", {"game": _game(pk), "new_player": form})
    response["HX-Retarget"] = "#new-player"
    response["HX-Reswap"] = "outerHTML"
    return response


# Cash exit.


@dataclass(frozen=True)
class ExitCalc:
    chips: int | None
    paid: int | None
    value: Fraction | None
    quick: list[int]
    pot: Fraction | None
    net: int | None


def exit_calc(result: Result, chips: int | None, paid: int | None, chips_per_lei: int) -> ExitCalc:
    """What the exit sheet shows: the stack in lei, quick cash amounts (floor, down to 10, down
    to 50), this exit's share of the pot and the player's net after it."""
    value = stats.chips_value(chips, chips_per_lei) if chips is not None else None
    quick = []
    if value is not None:
        whole = math.floor(value)
        for amount in (whole, whole // 10 * 10, whole // 50 * 50):
            if amount not in quick:
                quick.append(amount)
    pot = value - paid if value is not None and paid is not None else None
    net = result.payout + paid - result.buyin if paid is not None else None
    return ExitCalc(chips, paid, value, quick, pot, net)


def _cash_row(game: Game, result_pk: int) -> Result:
    return get_object_or_404(Result.objects.select_related("player"), pk=result_pk, game=game)


@organizer_required
@require_http_methods(["GET", "POST"])
def exit_sheet(request, pk: int, result_pk: int):
    """The bottom sheet for a cash player leaving; POST records the exit."""
    game = _game(pk)
    if not game.is_live:
        return gone(request, pk)
    result = _cash_row(game, result_pk)
    if request.method == "POST":
        form = ExitForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                outcome = actions.cash_exit(
                    pk, data["key"], request.user, result.pk, data["chips"], data["paid"]
                )
            except RuleError as error:
                return refresh(request, pk, message=str(error))
            except GameGone:
                return gone(request, pk)
            return refresh(request, pk, outcome=outcome)
    else:
        form = ExitForm(initial=_fresh())
    chips, paid = (
        (parse_int(request.POST.get("chips")), parse_int(request.POST.get("paid")))
        if request.method == "POST"
        else (None, None)
    )
    response = render(
        request,
        "live/_exit_sheet.html",
        {
            "game": game,
            "result": result,
            "form": form,
            "calc": exit_calc(result, chips, paid, game.season.chips_per_lei),
        },
    )
    if request.method == "POST":
        response["HX-Retarget"] = "#sheet"
        response["HX-Reswap"] = "innerHTML"
    return response


@organizer_required
@require_GET
def exit_preview(request, pk: int, result_pk: int):
    game = _game(pk)
    result = _cash_row(game, result_pk)
    calc = exit_calc(
        result,
        parse_int(request.GET.get("chips")),
        parse_int(request.GET.get("paid")),
        game.season.chips_per_lei,
    )
    return render(request, "live/_exit_calc.html", {"game": game, "result": result, "calc": calc})


@organizer_required
@require_GET
def close(request, pk: int):
    """Closing a cash game once everyone has left: totals and the balance stamp."""
    game = _game(pk)
    if not game.is_live:
        return gone(request, pk)
    totals = stats.live_totals(game)
    return render(
        request,
        "live/close.html",
        {
            "game": game,
            "season": game.season,
            "totals": totals,
            "leftover": totals.bank - totals.paid_out,
            "mismatch": stats.leftover_mismatch(SeasonKind.CASH, totals.bank, totals.paid_out),
            "key": uuid.uuid4(),
        },
    )


@organizer_required
@require_GET
def cancel(request, pk: int):
    """Confirmation before deleting a game started by mistake."""
    game = _game(pk)
    if not game.is_live:
        return gone(request, pk)
    return render(
        request,
        "live/cancel.html",
        {"game": game, "season": game.season, "has_players": game.results.exists()},
    )


# Tournament results.


@dataclass
class ResultRow:
    result: Result
    place: str
    payout: str
    error: str = ""


def _result_rows(game: Game, data=None) -> list[ResultRow]:
    results = list(game.results.select_related("player").order_by("pk"))
    places = stats.elimination_places({result.pk: result.out_order for result in results})
    rows = []
    for result in results:
        if data is None:
            place = places[result.pk]
            row = ResultRow(result, "" if place is None else str(place), str(result.payout or ""))
        else:
            row = ResultRow(
                result,
                data.get(f"place-{result.pk}", "").strip(),
                data.get(f"payout-{result.pk}", "").strip(),
            )
        rows.append(row)
    rows.sort(key=lambda row: (parse_int(row.place, minimum=1) or math.inf, row.result.player.name))
    return rows


def _split(game: Game, rows: list[ResultRow], *, overwrite: bool) -> str:
    """Fill the prize rows' payouts with the season's split of the bank (club.stats.split_prizes).

    Only rows in a prize place are touched; without ``overwrite`` only the empty ones. Returns a
    note for the organizer when fewer prize places are filled in than the season pays.
    """
    season = game.season
    bank = sum(row.result.buyin for row in rows)
    places = {index: parse_int(row.place, minimum=1) for index, row in enumerate(rows)}
    payouts = stats.split_prizes(bank, season.prize_weights, places, season.payout_round)
    for index, amount in payouts.items():
        if overwrite or not rows[index].payout:
            rows[index].payout = str(amount)
    if 0 < len(payouts) < season.paid_places:
        return (
            f"Призовых мест заполнено {len(payouts)} из {season.paid_places}: "
            "банк поделён между ними."
        )
    return ""


def _balance(game: Game, rows: list[ResultRow]) -> dict:
    bank = sum(row.result.buyin for row in rows)
    paid = sum(parse_int(row.payout) or 0 for row in rows)
    return {
        "bank": bank,
        "paid": paid,
        "difference": bank - paid,
        "mismatch": stats.leftover_mismatch(SeasonKind.TOUR, bank, paid),
    }


def _validate(rows: list[ResultRow]) -> dict[int, tuple[int | None, int]] | None:
    cleaned, valid = {}, True
    for row in rows:
        place = parse_int(row.place, minimum=1) if row.place else None
        payout = parse_int(row.payout) if row.payout else 0
        if row.place and place is None:
            row.error, valid = "Место: целое число от 1.", False
        elif payout is None:
            row.error, valid = "Выплата: целое число от 0.", False
        cleaned[row.result.pk] = (place, payout)
    return cleaned if valid else None


@organizer_required
@require_http_methods(["GET", "POST"])
def results(request, pk: int):
    game = _game(pk)
    if not game.is_live:
        return gone(request, pk)
    if game.season.kind != SeasonKind.TOUR or game.live_stage != Game.Stage.FINAL:
        return redirect("live:board", pk)
    data = request.POST if request.method == "POST" else None
    rows = _result_rows(game, data)
    error = note = ""
    if request.method == "GET":
        note = _split(game, rows, overwrite=False)
    elif "redistribute" in request.POST:
        # "Распределить заново": new prize payouts for the places as they are now; no saving.
        note = _split(game, rows, overwrite=True)
    else:
        cleaned = _validate(rows)
        key = _key(request)
        if cleaned is not None and key is not None:
            try:
                actions.save_results(pk, key, request.user, cleaned)
            except RuleError as rule:
                error = str(rule)
            except GameGone:
                return gone(request, pk)
            else:
                url = reverse("game_detail", args=[pk])
                return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    context = {
        "game": game,
        "season": game.season,
        "rows": rows,
        "balance": _balance(game, rows),
        "error": error,
        "note": note,
        "key": _key(request) if request.method == "POST" else uuid.uuid4(),
    }
    if _is_htmx(request):
        response = render(request, "live/_results_form.html", context)
        response["HX-Retarget"] = "#results-form"
        response["HX-Reswap"] = "outerHTML"
        return response
    return render(request, "live/results.html", context)


@organizer_required
@require_GET
def results_preview(request, pk: int):
    game = _game(pk)
    rows = _result_rows(game, request.GET)
    return render(request, "live/_balance.html", {"balance": _balance(game, rows)})
