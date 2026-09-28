import datetime
import uuid

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from club.models import Game, Result, Season, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season
from live import actions
from live.models import LiveAction
from live.tests.conftest import key, live_game, seat

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "board"}


@pytest.fixture
def org(client, organizer):
    client.force_login(organizer)
    return client


def act(client, game, name, *, result=None, same_key=None, **extra):
    data = {"key": str(same_key or uuid.uuid4())} | extra
    if result is not None:
        data["result"] = result.pk
    return client.post(reverse("live:act", args=[game.pk, name]), data, **HTMX)


def fresh(result) -> Result:
    result.refresh_from_db()
    return result


@pytest.fixture
def tour(db):
    game = live_game(SeasonKind.TOUR)
    return game, seat(game, "Альфа", "Браво", "Чарли")


@pytest.fixture
def cash(db):
    game = live_game(SeasonKind.CASH)
    return game, seat(game, "Альфа", "Браво")


def page_urls(game, result):
    return [
        reverse("live:index"),
        reverse("live:start"),
        reverse("live:board", args=[game.pk]),
        reverse("live:seating", args=[game.pk]),
        reverse("live:cancel", args=[game.pk]),
        reverse("live:exit", args=[game.pk, result.pk]),
    ]


class TestAccess:
    def test_anonymous_goes_to_the_admin_login(self, client, tour):
        game, (alpha, *_) = tour
        for url in page_urls(game, alpha):
            response = client.get(url)
            assert response.status_code == 302
            assert response["Location"].startswith(reverse("admin:login"))

    def test_anonymous_htmx_request_redirects_the_whole_page(self, client, tour):
        game, (alpha, *_) = tour
        response = act(client, game, "rebuy", result=alpha)
        assert response.status_code == 204
        assert response["HX-Redirect"].startswith(reverse("admin:login"))
        assert fresh(alpha).buyin == 100

    def test_non_organizer_is_forbidden(self, client, tour):
        game, (alpha, *_) = tour
        client.force_login(User.objects.create_user("test-guest", is_staff=True))
        for url in page_urls(game, alpha):
            assert client.get(url).status_code == 403
        assert act(client, game, "rebuy", result=alpha).status_code == 403
        assert fresh(alpha).buyin == 100

    def test_organizer_and_superuser_are_let_in(self, client, org, admin_user, tour):
        game, (alpha, *_) = tour
        for url in page_urls(game, alpha):
            assert org.get(url).status_code == 200
        client.force_login(admin_user)
        assert client.get(reverse("live:board", args=[game.pk])).status_code == 200

    def test_unverified_session_is_refused_with_2fa_on(self, org, settings, tour):
        game, _ = tour
        settings.ADMIN_REQUIRE_2FA = True
        response = org.get(reverse("live:board", args=[game.pk]))
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("admin:login"))

    def test_strict_csp_under_the_admin_prefix(self, client, org, tour):
        game, (alpha, *_) = tour
        for url in page_urls(game, alpha):
            policy = org.get(url)["Content-Security-Policy"]
            assert "unsafe-eval" not in policy and "unsafe-inline" not in policy
        client.logout()
        forbidden = client.get(reverse("live:board", args=[game.pk]))
        assert "unsafe-eval" not in forbidden["Content-Security-Policy"]

    def test_pages_are_noindex(self, org, tour):
        game, _ = tour
        assert '<meta name="robots" content="noindex' in org.get(reverse("live:index")).text

    def test_mutations_need_post(self, org, tour):
        game, _ = tour
        assert org.get(reverse("live:act", args=[game.pk, "rebuy"])).status_code == 405


class TestBoard:
    def test_tour_stage_one(self, org, tour):
        game, (alpha, bravo, charlie) = tour
        actions.rebuy(game.pk, key(), None, alpha.pk)
        actions.rebuy(game.pk, key(), None, alpha.pk)
        actions.eliminate(game.pk, key(), None, bravo.pk)

        page = org.get(reverse("live:board", args=[game.pk])).text

        assert "Этап 1 · Ребаи открыты" in page
        assert '<span class="font-num">2</span> ребая' in page
        assert "+50" in page and "Выбыл" in page
        assert "Выбыли" in page and "вернуть" in page
        assert "Перерыв на аддон" in page and "+ Игрок" in page

    def test_addon_break(self, org, tour):
        game, (alpha, bravo, _) = tour
        actions.eliminate(game.pk, key(), None, bravo.pk)
        actions.advance_stage(game.pk, key(), None, "rebuys")
        actions.set_addon(game.pk, key(), None, alpha.pk, True)

        page = org.get(reverse("live:board", args=[game.pk])).text

        assert "Перерыв · Аддон" in page
        assert "✓ Аддон" in page and "+ Аддон" in page
        assert page.count('aria-pressed="true"') == 1
        assert "Выбыл</button>" not in page
        assert "Начать финальный этап" in page

    def test_final_stage(self, org, tour):
        game, _ = tour
        game.live_stage = Game.Stage.FINAL
        game.save()
        page = org.get(reverse("live:board", args=[game.pk])).text
        assert "Финальный этап" in page
        assert "+50</button>" not in page and "Выбыл</button>" in page
        assert "Итоги турнира" in page

    def test_cash(self, org, cash):
        game, (alpha, bravo) = cash
        actions.cash_exit(game.pk, key(), None, bravo.pk, 4570, 45)

        page = org.get(reverse("live:board", args=[game.pk])).text

        assert "За столом" in page and "Вышли" in page
        assert "Выход</button>" in page and "Вернулся +50" in page
        assert "0,70" in page  # in the pot: 45,70 - 45

    def test_finished_game_redirects_to_its_public_sheet(self, org):
        game = make_game(make_season(2026))
        response = org.get(reverse("live:board", args=[game.pk]))
        assert response["Location"] == reverse("game_detail", args=[game.pk])

    def test_poll_returns_the_fragment(self, org, tour):
        game, _ = tour
        response = org.get(reverse("live:board", args=[game.pk]), **HTMX)
        assert response.text.lstrip().startswith('<section id="board"')
        assert "<html" not in response.text

    def test_empty_game_offers_cancel(self, org):
        game = live_game(SeasonKind.CASH)
        page = org.get(reverse("live:board", args=[game.pk])).text
        assert "Отменить игру" in page

    @pytest.mark.parametrize("kind", [SeasonKind.TOUR, SeasonKind.CASH])
    def test_query_count_does_not_grow_with_players(self, org, kind):
        def count(players):
            game = live_game(kind)
            seat(game, *(f"Игрок {kind} {players} {i}" for i in range(players)))
            with CaptureQueriesContext(connection) as queries:
                assert org.get(reverse("live:board", args=[game.pk])).status_code == 200
            Game.objects.filter(pk=game.pk).delete()
            Season.objects.all().delete()
            return len(queries)

        assert count(3) == count(12)


class TestActions:
    def test_rebuy_answers_with_board_and_toast(self, org, tour):
        game, (alpha, *_) = tour

        response = act(org, game, "rebuy", result=alpha)

        assert response.status_code == 200
        assert fresh(alpha).buyin == 150
        assert '<section id="board"' in response.text
        assert 'id="toast" hx-swap-oob="true"' in response.text
        assert "Ребай: Альфа +50" in response.text
        assert "отменить" in response.text
        assert 'id="sheet" hx-swap-oob="true"' in response.text

    def test_same_request_twice_counts_once(self, org, tour):
        game, (alpha, *_) = tour
        same = uuid.uuid4()

        first = act(org, game, "rebuy", result=alpha, same_key=same)
        second = act(org, game, "rebuy", result=alpha, same_key=same)

        assert (first.status_code, second.status_code) == (200, 200)
        assert "Уже записано: Ребай: Альфа +50" in second.text
        assert fresh(alpha).buyin == 150

    def test_undo(self, org, tour):
        game, (alpha, *_) = tour
        act(org, game, "rebuy", result=alpha)
        action = LiveAction.objects.get(kind="rebuy")

        response = org.post(reverse("live:undo", args=[game.pk, action.pk]), **HTMX)

        assert response.status_code == 200
        assert "Отменено: Ребай: Альфа +50" in response.text
        assert fresh(alpha).buyin == 100
        again = org.post(reverse("live:undo", args=[game.pk, action.pk]), **HTMX)
        assert again.status_code == 200
        assert fresh(alpha).buyin == 100

    def test_undo_of_another_games_action_is_404(self, org, tour):
        game, (alpha, *_) = tour
        other = live_game(SeasonKind.CASH)
        act(org, game, "rebuy", result=alpha)
        action = LiveAction.objects.get(kind="rebuy")
        response = org.post(reverse("live:undo", args=[other.pk, action.pk]), **HTMX)
        assert response.status_code == 404

    def test_refused_action_is_a_warning_toast(self, org, tour):
        game, (alpha, *_) = tour
        actions.advance_stage(game.pk, key(), None, "rebuys")

        response = act(org, game, "rebuy", result=alpha)

        assert response.status_code == 200
        assert "toast-warn" in response.text and "Ребаи закрыты." in response.text
        assert fresh(alpha).buyin == 100

    @pytest.mark.parametrize(
        ("setup", "name", "check"),
        [
            (None, "eliminate", lambda r: r.out_order == 1),
            ("eliminate", "restore", lambda r: r.out_order is None),
            ("stage", "addon-on", lambda r: (r.addon, r.buyin) == (True, 150)),
        ],
    )
    def test_tour_buttons(self, org, tour, setup, name, check):
        game, (alpha, *_) = tour
        if setup == "eliminate":
            actions.eliminate(game.pk, key(), None, alpha.pk)
        elif setup == "stage":
            actions.advance_stage(game.pk, key(), None, "rebuys")
        assert act(org, game, name, result=alpha).status_code == 200
        assert check(fresh(alpha))

    def test_stage_button(self, org, tour):
        game, _ = tour
        act(org, game, "stage", **{"from": "rebuys"})
        game.refresh_from_db()
        assert game.live_stage == Game.Stage.ADDON

    def test_cash_buttons(self, org, cash):
        game, (alpha, _) = cash
        act(org, game, "topup", result=alpha)
        assert fresh(alpha).buyin == 100
        actions.cash_exit(game.pk, key(), None, alpha.pk, 100, 1)
        act(org, game, "return", result=alpha)
        assert (fresh(alpha).buyin, fresh(alpha).out_order) == (150, None)

    def test_bad_requests(self, org, tour):
        game, (alpha, *_) = tour
        assert act(org, game, "explode", result=alpha).status_code == 400
        url = reverse("live:act", args=[game.pk, "rebuy"])
        assert org.post(url, {"key": "nope", "result": alpha.pk}, **HTMX).status_code == 400

    def test_action_on_a_finished_game_redirects(self, org, tour):
        game, (alpha, *_) = tour
        Game.objects.filter(pk=game.pk).update(live_stage="")
        response = act(org, game, "rebuy", result=alpha)
        assert response["HX-Redirect"] == reverse("game_detail", args=[game.pk])

    def test_query_count_of_an_action_does_not_grow_with_players(self, org):
        def count(players):
            game = live_game(SeasonKind.TOUR)
            first, *_ = seat(game, *(f"Игрок {players} {i}" for i in range(players)))
            with CaptureQueriesContext(connection) as queries:
                assert act(org, game, "rebuy", result=first).status_code == 200
            LiveAction.objects.all().delete()
            Game.objects.all().delete()
            Season.objects.all().delete()
            return len(queries)

        assert count(3) == count(12)


class TestStart:
    def post(self, client, **fields):
        data = {"key": str(uuid.uuid4()), "date": "2026-09-28", "location": "Гараж"} | fields
        return client.post(reverse("live:start"), data)

    def test_defaults(self, org):
        make_season(2026, SeasonKind.TOUR)
        make_game(make_season(2025, SeasonKind.CASH), location="Гараж № 5")
        form = org.get(reverse("live:start")).context["form"]
        assert form.initial["date"] == timezone.localdate()
        assert form.initial["location"] == "Гараж № 5"

    def test_start_in_an_existing_season(self, org):
        season = make_season(2026, SeasonKind.TOUR)
        response = self.post(org, season=str(season.pk))
        game = Game.objects.get()
        assert response["Location"] == reverse("live:seating", args=[game.pk])
        assert (game.season, game.live_stage, game.location) == (season, "rebuys", "Гараж")

    def test_start_in_a_new_season(self, org, settings):
        make_season(2025, SeasonKind.CASH, chips_per_lei=10, cash_step=20)
        year = timezone.localdate().year
        response = self.post(org, season="new-cash", date=f"{year}-01-03")
        assert response.status_code == 302
        season = Season.objects.get(year=year, kind=SeasonKind.CASH)
        assert (season.chips_per_lei, season.cash_step) == (10, 20)

    def test_new_season_is_offered_only_when_missing(self, org):
        year = timezone.localdate().year
        make_season(year, SeasonKind.TOUR)
        choices = dict(org.get(reverse("live:start")).context["form"].fields["season"].choices)
        assert "new-cash" in choices and "new-tour" not in choices

    def test_wrong_year(self, org):
        season = make_season(2025, SeasonKind.TOUR)
        response = self.post(org, season=str(season.pk))
        assert response.status_code == 200
        assert "Дата должна быть в 2025 году." in response.text
        assert not Game.objects.exists()

    def test_twice_with_the_same_key(self, org):
        season = make_season(2026, SeasonKind.TOUR)
        same = str(uuid.uuid4())
        self.post(org, season=str(season.pk), key=same)
        self.post(org, season=str(season.pk), key=same)
        assert Game.objects.count() == 1

    def test_finished_game_on_that_day(self, org):
        season = make_season(2026, SeasonKind.TOUR)
        make_game(season, day=28, month=9)
        response = self.post(org, season=str(season.pk))
        assert "уже записана" in response.text


class TestSeating:
    def test_recent_players_first_and_seated_ones_hidden(self, org, tour):
        game, (alpha, *_) = tour
        old = make_player("Аист")
        make_result(make_game(make_season(2020)), old)
        recent = make_player("Юрий")
        make_result(make_game(make_season(2025)), recent)
        never = make_player("Борис")

        candidates = list(org.get(reverse("live:seating", args=[game.pk])).context["candidates"])

        assert candidates == [recent, old, never]

    def test_search_by_name_or_nickname(self, org, tour):
        game, _ = tour
        make_player("Дмитрий", nickname="Трактор")
        make_player("Трофим")
        url = reverse("live:seating", args=[game.pk])
        response = org.get(url, {"q": "трак"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="player-list")
        assert response.text.lstrip().startswith('<div id="player-list">')
        assert [p.name for p in response.context["candidates"]] == ["Дмитрий"]

    def test_seat_from_the_list(self, org, tour):
        game, _ = tour
        player = make_player("Дельта")
        response = org.post(
            reverse("live:seat", args=[game.pk]),
            {"key": str(uuid.uuid4()), "player": player.pk, "q": ""},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="player-list",
        )
        assert response.status_code == 200
        assert game.results.filter(player=player, buyin=100).exists()
        assert "За стол: Дельта, 100" in response.text
        assert 'hx-target="#player-list"' in response.text  # the toast's undo

    def test_seat_new_player(self, org, cash):
        game, _ = cash
        response = org.post(
            reverse("live:seat_new", args=[game.pk]),
            {"key": str(uuid.uuid4()), "name": "Евгений", "nickname": "Женя"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="player-list",
        )
        assert response.status_code == 200
        assert game.results.filter(player__name="Евгений", buyin=50).exists()
        assert 'id="new-player" class="live-form" hx-swap-oob="true"' in response.text

    def test_seat_new_player_errors_stay_in_the_form(self, org, cash):
        game, (alpha, _) = cash
        response = org.post(
            reverse("live:seat_new", args=[game.pk]),
            {"key": str(uuid.uuid4()), "name": alpha.player.name, "nickname": ""},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="player-list",
        )
        assert response["HX-Retarget"] == "#new-player"
        assert "field-error" in response.text
        assert game.results.count() == 2

    def test_closed_for_a_tournament_after_stage_one(self, org, tour):
        game, _ = tour
        actions.advance_stage(game.pk, key(), None, "rebuys")
        page = org.get(reverse("live:seating", args=[game.pk])).text
        assert "Ребаи закрыты" in page


class TestExitSheet:
    def test_sheet(self, org, cash):
        game, (alpha, _) = cash
        response = org.get(reverse("live:exit", args=[game.pk, alpha.pk]))
        assert "Выход: Альфа" in response.text and "Записать выход" in response.text

    def test_preview(self, org, cash):
        game, (alpha, _) = cash
        url = reverse("live:exit_preview", args=[game.pk, alpha.pk])

        response = org.get(url, {"chips": "11230", "paid": "110"})

        calc = response.context["calc"]
        assert calc.quick == [112, 110, 100]
        assert "11 230</span> = <span" in response.text
        assert "112,30" in response.text
        assert "2,30" in response.text  # to the pot
        assert "+60" in response.text  # 110 - 50

    def test_preview_quick_amounts_are_unique(self, org, cash):
        game, (alpha, _) = cash
        url = reverse("live:exit_preview", args=[game.pk, alpha.pk])
        assert org.get(url, {"chips": "10000"}).context["calc"].quick == [100]
        assert org.get(url, {"chips": "4570"}).context["calc"].quick == [45, 40, 0]

    def test_preview_warns_when_paying_more_than_the_chips(self, org, cash):
        game, (alpha, _) = cash
        url = reverse("live:exit_preview", args=[game.pk, alpha.pk])
        assert "больше, чем стоят фишки" in org.get(url, {"chips": "100", "paid": "5"}).text

    def test_record(self, org, cash):
        game, (alpha, _) = cash
        response = org.post(
            reverse("live:exit", args=[game.pk, alpha.pk]),
            {"key": str(uuid.uuid4()), "chips": "11230", "paid": "112"},
            **HTMX,
        )
        assert response.status_code == 200
        alpha = fresh(alpha)
        assert (alpha.chips_out, alpha.payout, alpha.out_order) == (11230, 112, 1)
        assert 'id="sheet" hx-swap-oob="true"></div>' in response.text

    def test_invalid_input_stays_in_the_sheet(self, org, cash):
        game, (alpha, _) = cash
        response = org.post(
            reverse("live:exit", args=[game.pk, alpha.pk]),
            {"key": str(uuid.uuid4()), "chips": "-5", "paid": ""},
            **HTMX,
        )
        assert (response["HX-Retarget"], response["HX-Reswap"]) == ("#sheet", "innerHTML")
        assert fresh(alpha).out_order is None


class TestResults:
    @pytest.fixture
    def final(self, tour):
        game, (alpha, bravo, charlie) = tour
        actions.eliminate(game.pk, key(), None, bravo.pk)
        actions.eliminate(game.pk, key(), None, alpha.pk)
        Game.objects.filter(pk=game.pk).update(live_stage=Game.Stage.FINAL)
        return game, (alpha, bravo, charlie)

    def test_places_prefilled_from_the_elimination_order(self, org, final):
        game, (alpha, bravo, charlie) = final
        rows = org.get(reverse("live:results", args=[game.pk])).context["rows"]
        assert [(row.result, row.place) for row in rows] == [
            (charlie, "1"),
            (alpha, "2"),
            (bravo, "3"),
        ]

    def test_prizes_prefilled_by_the_split(self, org, final):
        # Браво went out first, then Альфа; Чарли won. Bank 300, weights 3,2,1: 150, 100, 50.
        game, (alpha, bravo, charlie) = final
        response = org.get(reverse("live:results", args=[game.pk]))

        payouts = {row.result: row.payout for row in response.context["rows"]}
        assert payouts == {charlie: "150", alpha: "100", bravo: "50"}
        assert "Баланс сверен" in response.text
        assert not response.context["note"]

    def test_prefill_keeps_payouts_already_entered(self, org, final):
        game, (alpha, bravo, charlie) = final
        Result.objects.filter(pk=charlie.pk).update(payout=170)
        rows = org.get(reverse("live:results", args=[game.pk])).context["rows"]
        assert {row.result: row.payout for row in rows}[charlie] == "170"

    def test_redistribute_after_changing_places(self, org, final):
        game, (alpha, bravo, charlie) = final
        data = {
            "key": str(uuid.uuid4()),
            "redistribute": "1",
            f"place-{charlie.pk}": "2",
            f"payout-{charlie.pk}": "150",
            f"place-{alpha.pk}": "1",
            f"payout-{alpha.pk}": "100",
            f"place-{bravo.pk}": "",
            f"payout-{bravo.pk}": "30",
        }
        response = org.post(reverse("live:results", args=[game.pk]), data, **HTMX)

        payouts = {row.result: row.payout for row in response.context["rows"]}
        # Only 1st and 2nd filled in: the bank goes 3:2 (180, 120), rounded to 150, 100 with the
        # 50 left over to 1st. Браво, with no place, is cleared: the bank is paid out exactly.
        assert payouts == {alpha: "200", charlie: "100", bravo: ""}
        assert response.context["balance"]["paid"] == 300
        assert "Баланс сверен" in response.text
        assert "Призовых мест заполнено 2 из 3: банк поделён между ними." in response.text
        assert response["HX-Retarget"] == "#results-form"
        game.refresh_from_db()
        assert game.live_stage == Game.Stage.FINAL  # nothing saved

    def test_redistribute_clears_places_outside_the_prizes(self, org, final):
        game, (alpha, bravo, charlie) = final
        Season.objects.filter(pk=game.season_id).update(paid_places=2, payout_weights="3,2")
        data = {
            "key": str(uuid.uuid4()),
            "redistribute": "1",
            f"place-{charlie.pk}": "1",
            f"payout-{charlie.pk}": "10",
            f"place-{alpha.pk}": "2",
            f"payout-{alpha.pk}": "10",
            f"place-{bravo.pk}": "3",
            f"payout-{bravo.pk}": "50",
        }
        response = org.post(reverse("live:results", args=[game.pk]), data, **HTMX)

        payouts = {row.result: row.payout for row in response.context["rows"]}
        assert payouts == {charlie: "200", alpha: "100", bravo: ""}
        assert "Баланс сверен" in response.text

    def test_opening_keeps_non_prize_payouts(self, org, final):
        # Only the button clears; opening the screen never touches what is already stored.
        game, (alpha, bravo, charlie) = final
        Result.objects.filter(pk=bravo.pk).update(payout=20)
        rows = org.get(reverse("live:results", args=[game.pk])).context["rows"]
        assert {row.result: row.payout for row in rows}[bravo] == "20"

    def test_split_follows_the_season(self, org, final):
        game, _ = final
        Season.objects.filter(pk=game.season_id).update(payout_weights="1,1,1", payout_round=100)
        rows = org.get(reverse("live:results", args=[game.pk])).context["rows"]
        assert [row.payout for row in rows] == ["100", "100", "100"]

    def test_only_from_the_final_stage(self, org, tour):
        game, _ = tour
        response = org.get(reverse("live:results", args=[game.pk]))
        assert response["Location"] == reverse("live:board", args=[game.pk])

    def test_balance_stamp(self, org, final):
        game, (alpha, bravo, charlie) = final
        url = reverse("live:results_preview", args=[game.pk])
        paid = {f"payout-{charlie.pk}": "200", f"payout-{alpha.pk}": "100"}
        assert "Баланс сверен" in org.get(url, paid).text
        response = org.get(url, paid | {f"payout-{alpha.pk}": "50"})
        assert "Не сходится" in response.text and "Разница" in response.text

    def test_save(self, org, final):
        game, (alpha, bravo, charlie) = final
        data = {
            "key": str(uuid.uuid4()),
            f"place-{charlie.pk}": "1",
            f"payout-{charlie.pk}": "250",
            f"place-{alpha.pk}": "2",
            f"payout-{alpha.pk}": "",
            f"place-{bravo.pk}": "3",
            f"payout-{bravo.pk}": "0",
        }
        response = org.post(reverse("live:results", args=[game.pk]), data, **HTMX)

        assert response["HX-Redirect"] == reverse("game_detail", args=[game.pk])
        assert (fresh(charlie).place, fresh(charlie).payout) == (1, 250)
        assert fresh(alpha).payout == 0
        game.refresh_from_db()
        assert game.live_stage == ""

    def test_invalid_values(self, org, final):
        game, (alpha, bravo, charlie) = final
        data = {"key": str(uuid.uuid4()), f"place-{alpha.pk}": "0", f"payout-{bravo.pk}": "x"}
        response = org.post(reverse("live:results", args=[game.pk]), data, **HTMX)
        assert response["HX-Retarget"] == "#results-form"
        assert "Место: целое число от 1." in response.text
        assert "Выплата: целое число от 0." in response.text
        game.refresh_from_db()
        assert game.live_stage == Game.Stage.FINAL


class TestCloseAndCancel:
    def test_close_cash(self, org, cash):
        game, (alpha, bravo) = cash
        actions.cash_exit(game.pk, key(), None, alpha.pk, 5230, 52)
        page = org.get(reverse("live:close", args=[game.pk])).text
        assert "За столом ещё" in page
        actions.cash_exit(game.pk, key(), None, bravo.pk, 4770, 47)

        page = org.get(reverse("live:close", args=[game.pk])).text
        assert "Баланс сверен" in page

        response = act(org, game, "close")
        assert response["HX-Redirect"] == reverse("game_detail", args=[game.pk])
        game.refresh_from_db()
        assert game.live_stage == ""

    def test_close_stamp_when_paying_out_too_much(self, org, cash):
        game, (alpha, bravo) = cash
        actions.cash_exit(game.pk, key(), None, alpha.pk, 20000, 200)
        actions.cash_exit(game.pk, key(), None, bravo.pk, 0, 0)
        assert (
            '<p class="stamp">Не сходится</p>'
            in org.get(reverse("live:close", args=[game.pk])).text
        )

    def test_cancel(self, org, organizer):
        game = live_game(SeasonKind.TOUR)
        response = act(org, game, "cancel")
        assert response["HX-Redirect"] == reverse("live:index")
        assert not Game.objects.filter(pk=game.pk).exists()
        assert LogEntry.objects.get().user == organizer

    def test_cancel_twice_goes_to_the_list(self, org):
        game = live_game(SeasonKind.TOUR)
        act(org, game, "cancel")
        assert act(org, game, "cancel")["HX-Redirect"] == reverse("live:index")
        assert LogEntry.objects.count() == 1

    def test_cancel_refused_with_players(self, org, tour):
        game, _ = tour
        assert "отменить нельзя" in org.get(reverse("live:cancel", args=[game.pk])).text
        response = act(org, game, "cancel")
        assert response["HX-Redirect"] == reverse("live:board", args=[game.pk])
        assert Game.objects.filter(pk=game.pk).exists()


class TestTimeZone:
    """Clock and countdown in Europe/Chisinau (UTC+3 in September), whatever the server runs."""

    @pytest.fixture
    def frozen(self, monkeypatch):
        def freeze(utc):
            monkeypatch.setattr(timezone, "now", lambda: utc)

        return freeze

    def test_clock_and_countdown_across_midnight(self, org, frozen):
        season = make_season(2026, SeasonKind.TOUR)
        start = datetime.datetime(2026, 9, 27, 19, 40, tzinfo=datetime.UTC)  # 22:40 local
        game = make_game(season, day=27, month=9, live_stage="rebuys", started_at=start)
        frozen(datetime.datetime(2026, 9, 27, 20, 50, tzinfo=datetime.UTC))  # 23:50 local

        page = org.get(reverse("live:board", args=[game.pk])).text

        assert '<time class="font-num">00:40</time>' in page
        assert 'ещё <span class="font-num">0:50</span>' in page
        assert ">23:50</time>" in page

    def test_time_over(self, org, frozen):
        season = make_season(2026, SeasonKind.TOUR)
        start = datetime.datetime(2026, 9, 27, 19, 40, tzinfo=datetime.UTC)
        game = make_game(season, day=27, month=9, live_stage="rebuys", started_at=start)
        frozen(datetime.datetime(2026, 9, 27, 21, 50, tzinfo=datetime.UTC))  # 00:50 local

        page = org.get(reverse("live:board", args=[game.pk])).text

        assert "время вышло" in page
        assert ">00:50</time>" in page

    def test_default_date_is_the_local_one(self, org, frozen):
        frozen(datetime.datetime(2026, 9, 27, 21, 50, tzinfo=datetime.UTC))
        form = org.get(reverse("live:start")).context["form"]
        assert form.initial["date"] == datetime.date(2026, 9, 28)
