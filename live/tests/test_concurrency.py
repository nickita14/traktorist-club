"""Two organizers on two phones: parallel requests on one game, each on its own connection.

Every action locks the game row first, so these run one after another in the database even
though the threads start together.
"""

import threading

import pytest
from django.db import connection

from club.models import Result, SeasonKind
from live import actions
from live.models import LiveAction
from live.tests.conftest import key, live_game, seat

pytestmark = pytest.mark.django_db(transaction=True)


def run_together(*calls):
    """Run the calls in parallel threads, released at the same moment; re-raise any error."""
    barrier = threading.Barrier(len(calls))
    errors = []

    def worker(call):
        try:
            barrier.wait()
            call()
        except Exception as error:  # reported to the test below
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_parallel_rebuys_all_count():
    game = live_game(SeasonKind.TOUR)
    (alpha,) = seat(game, "Альфа")
    calls = [lambda: actions.rebuy(game.pk, key(), None, alpha.pk) for _ in range(8)]

    assert run_together(*calls) == []

    alpha.refresh_from_db()
    assert (alpha.buyin, alpha.rebuys) == (100 + 8 * 50, 8)


def test_same_key_from_two_phones_applies_once():
    game = live_game(SeasonKind.TOUR)
    (alpha,) = seat(game, "Альфа")
    same = key()

    assert run_together(*[lambda: actions.rebuy(game.pk, same, None, alpha.pk)] * 4) == []

    alpha.refresh_from_db()
    assert alpha.buyin == 150
    assert LiveAction.objects.filter(key=same).count() == 1


def test_parallel_eliminations_get_distinct_orders():
    game = live_game(SeasonKind.TOUR)
    results = seat(game, "Альфа", "Браво", "Чарли", "Дельта", "Эхо")
    calls = [lambda r=r: actions.eliminate(game.pk, key(), None, r.pk) for r in results[:4]]

    assert run_together(*calls) == []

    orders = sorted(
        Result.objects.filter(game=game).values_list("out_order", flat=True),
        key=lambda order: order or 0,
    )
    assert orders == [None, 1, 2, 3, 4]


def test_everyone_eliminated_at_once_leaves_a_winner():
    game = live_game(SeasonKind.TOUR)
    results = seat(game, "Альфа", "Браво", "Чарли")
    calls = [lambda r=r: actions.eliminate(game.pk, key(), None, r.pk) for r in results]

    errors = run_together(*calls)

    assert [type(error) for error in errors] == [actions.RuleError]
    assert Result.objects.filter(game=game, out_order__isnull=True).count() == 1


def test_rebuy_racing_the_stage_change():
    # Either the rebuy lands before rebuys close or it is refused; never after the change.
    game = live_game(SeasonKind.TOUR)
    (alpha, _) = seat(game, "Альфа", "Браво")

    errors = run_together(
        lambda: actions.advance_stage(game.pk, key(), None, "rebuys"),
        lambda: actions.rebuy(game.pk, key(), None, alpha.pk),
    )

    alpha.refresh_from_db()
    assert (alpha.buyin, len(errors)) in {(150, 0), (100, 1)}


def test_cash_top_ups_from_two_phones():
    game = live_game(SeasonKind.CASH)
    (alpha, bravo) = seat(game, "Альфа", "Браво")
    calls = [lambda r=r: actions.top_up(game.pk, key(), None, r.pk) for r in (alpha, bravo) * 3]

    assert run_together(*calls) == []

    assert sorted(Result.objects.filter(game=game).values_list("buyin", flat=True)) == [200, 200]
