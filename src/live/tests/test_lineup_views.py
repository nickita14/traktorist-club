"""The lineup and collection screens, the sheets of the board (start with unpaid entries, a
player's row, bust confirmation) and late entries on the seating page."""

import re
import uuid

import pytest
from django.urls import reverse

from club.models import Game, Result, SeasonKind
from club.tests.factories import make_player
from live import actions, views
from live.tests.conftest import key, lineup_game, live_game, seat
from live.views import bust_lines

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "board"}


@pytest.fixture
def org(client, organizer):
    client.force_login(organizer)
    return client


def act(client, game, name, *, result=None, **extra):
    data = {"key": str(uuid.uuid4())} | extra
    if result is not None:
        data["result"] = result.pk
    return client.post(reverse("live:act", args=[game.pk, name]), data, **HTMX)


def board(client, game, **params) -> str:
    return client.get(reverse("live:board", args=[game.pk]), params).text


def stage(game) -> str:
    game.refresh_from_db()
    return game.live_stage


def text(html: str) -> str:
    """The visible words of a fragment, tags dropped and whitespace squeezed."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


@pytest.fixture
def lineup():
    game = lineup_game(SeasonKind.TOUR, entry_price=100)
    return game, seat(game, "Альфа", "Браво", "Чарли")


@pytest.fixture
def collect(lineup):
    game, results = lineup
    actions.advance_stage(game.pk, key(), None, "lineup")
    return game, results


class TestLineupScreen:
    def test_heading_steps_and_list(self, org, lineup):
        game, _ = lineup
        game.location = "Гараж"
        game.save()

        page = board(org, game)

        words = text(page)
        assert "Посадка" in words and "Идёт игра" not in words
        assert "Турниры 2026 · Пн, 28.09.2026 · Гараж" in words
        assert "Турнир № 1" in words
        assert re.search(
            r'<li aria-current="step">\s*<span><span><span class="font-num">1</span> · Состав', page
        )
        assert "За столом · 3" in words and "Вход 100 лей" in words
        assert page.count('aria-label="Убрать: ') == 3
        assert "без следа" in words
        assert "Состав верен · к сбору" in words
        assert 'href="' + reverse("live:cancel", args=[game.pk]) + '"' in page

    def test_numbered_in_seating_order(self, org, lineup):
        game, _ = lineup
        seat(game, "Аврора")
        words = text(board(org, game))
        assert words.index("1 Альфа") < words.index("2 Браво") < words.index("4 Аврора")

    def test_quick_add_recent_players(self, org, lineup):
        game, _ = lineup
        make_player("Дельта")
        page = board(org, game)
        assert 'aria-label="Посадить Дельта"' in page
        assert 'aria-label="Посадить Альфа"' not in page  # already seated

    def test_search_filters_and_seating_keeps_it(self, org, lineup):
        game, _ = lineup
        make_player("Дельта")
        echo = make_player("Эхо")
        page = board(org, game, q="дел")
        assert 'aria-label="Посадить Дельта"' in page and "Эхо" not in page

        response = org.post(
            reverse("live:seat", args=[game.pk]),
            {"key": str(uuid.uuid4()), "player": echo.pk, "q": "дел"},
            **HTMX,
        )

        assert game.results.filter(player=echo).exists()
        assert 'aria-label="Посадить Дельта"' in response.text
        assert 'aria-label="Посадить Эхо"' not in response.text

    def test_list_rows_show_name_and_nickname(self, org, lineup):
        game, _ = lineup
        make_player("Дмитрий", nickname="Трактор")
        make_player("Ион")
        page = board(org, game)
        assert (
            '<span class="live-name">Дмитрий</span> <span class="live-nick">Трактор</span>' in page
        )
        assert 'aria-label="Посадить Дмитрий (Трактор)"' in page
        # No nickname, no empty muted span.
        assert re.search(r'<span class="live-name">Ион</span></span>', page)
        # The new player is a button of its own, under the list.
        assert page.index("Посадить Ион") < page.index("+ Новый игрок")
        assert "Никого не нашли" not in page

    @pytest.mark.parametrize(
        ("query", "found", "missed"),
        [
            ("ио", "Ion Ceban", "Ivan Rusu"),  # Cyrillic query, Latin name
            ("ivan", "Иван Петров", "Ion Ceban"),  # Latin query, Cyrillic name
            ("stefan", "Ștefan Ţurcanu", "Ion Ceban"),  # diacritics ignored
            ("цуркану", "Ștefan Ţurcanu", "Ion Ceban"),
            ("чобан", "Vasile Ciobanu", "Ion Ceban"),  # the Romanian reading
            ("трак", "Дмитрий", "Ion Ceban"),  # the nickname
        ],
    )
    def test_search_in_any_alphabet(self, org, lineup, query, found, missed):
        game, _ = lineup
        for name in ("Ion Ceban", "Ivan Rusu", "Иван Петров", "Ștefan Ţurcanu", "Vasile Ciobanu"):
            make_player(name)
        make_player("Дмитрий", nickname="Трактор")
        page = board(org, game, q=query)
        assert f'<span class="live-name">{found}</span>' in page
        assert f'<span class="live-name">{missed}</span>' not in page

    def test_search_skips_seated_players(self, org, lineup):
        game, _ = lineup
        make_player("Alfa")
        page = board(org, game, q="альф")
        assert 'aria-label="Посадить Alfa"' in page
        assert 'aria-label="Посадить Альфа"' not in page  # already at the table

    def test_nothing_found(self, org, lineup):
        game, _ = lineup
        page = board(org, game, q="Зорро")
        assert '<p class="mt-3 text-muted italic">Никого не нашли.</p>' in page
        assert "Посадить " not in page
        # "+ Новый игрок" takes the search along and starts the name with it.
        new = re.search(r"<button[^>]*>\+ Новый игрок</button>", page).group()
        assert 'hx-include="#search"' in new
        assert reverse("live:seat_new", args=[game.pk]) in new
        sheet = org.get(reverse("live:seat_new", args=[game.pk]), {"q": " Зорро "}).text
        assert 'name="name" maxlength="100" value="Зорро"' in sheet

    def test_new_player_sheet_without_search(self, org, lineup):
        game, _ = lineup
        sheet = org.get(reverse("live:seat_new", args=[game.pk])).text
        assert 'name="name" maxlength="100" value=""' in sheet

    def test_search_input(self, org, lineup):
        game, _ = lineup
        field = re.search(r'<input id="search"[^>]*>', board(org, game, q="ио")).group()
        for attribute in (
            'placeholder="имя или ник"',
            'value="ио"',
            'autocomplete="off"',
            'autocapitalize="off"',
            'spellcheck="false"',
            'enterkeyhint="search"',
            'hx-preserve="true"',
        ):
            assert attribute in field

    def test_start_is_disabled_with_one_player(self, org):
        game = lineup_game(SeasonKind.CASH)
        seat(game, "Альфа")
        page = board(org, game)
        assert re.search(r"disabled>Состав верен · к сбору<", page)

    def test_unseat_from_the_list(self, org, lineup):
        game, (alpha, *_) = lineup
        response = act(org, game, "unseat", result=alpha)
        assert response.status_code == 200
        assert not Result.objects.filter(pk=alpha.pk).exists()
        assert "Убран из состава: Альфа" in response.text
        assert "За столом · 2" in text(response.text)

    def test_to_the_collection(self, org, lineup):
        game, _ = lineup
        response = act(org, game, "stage", **{"from": "lineup", "to": "collect"})
        assert stage(game) == Game.Stage.COLLECT
        assert "К сбору, лей" in response.text

    def test_query_count(self, org, django_assert_num_queries):
        def check(players):
            game = lineup_game(SeasonKind.TOUR)
            seat(game, *(f"Игрок {players} {i}" for i in range(players)))
            # session, user, organizer check; game, results, totals, number, candidates
            with django_assert_num_queries(8):
                assert org.get(reverse("live:board", args=[game.pk])).status_code == 200
            game.delete()
            game.season.delete()

        check(3)
        check(12)

    @pytest.mark.parametrize("query", ["", "ио", "Зорро"])
    def test_query_count_with_a_search(self, org, lineup, django_assert_num_queries, query):
        game, _ = lineup
        for name in ("Ion Ceban", "Ivan Rusu", "Иван Петров", "Ștefan Ţurcanu"):
            make_player(name)
        # The same 8 as above: the search runs in Python over the one candidates query.
        with django_assert_num_queries(8):
            assert org.get(reverse("live:board", args=[game.pk]), {"q": query}).status_code == 200

    def test_search_keeps_the_limits(self, org, lineup):
        game, _ = lineup
        for i in range(40):
            make_player(f"Ion {i:02}")
        assert board(org, game).count('aria-label="Посадить ') == views.QUICK_ADD
        page = board(org, game, q="ио")
        assert page.count('aria-label="Посадить ') == views.SEAT_CANDIDATES


class TestCollectScreen:
    def test_totals(self, org, collect):
        game, (alpha, *_) = collect
        actions.set_paid(game.pk, key(), None, alpha.pk, True)

        page = board(org, game)

        strip = text(re.search(r'<dl class="stat-strip.*?</dl>', page, re.S).group())
        assert strip == "К сбору, лей 300 Собрано 100 Не сдали 2"
        assert '<dd class="stat-value text-accent">2</dd>' in page

    def test_nobody_unpaid(self, org, collect):
        game, results = collect
        for result in results:
            actions.set_paid(game.pk, key(), None, result.pk, True)
        page = board(org, game)
        assert "Собрано 300 Не сдали ·" in text(page)
        assert 'class="stat-value text-accent"' not in page
        # Everyone paid: the button starts the game at once.
        assert f'name="to" value="{Game.Stage.REBUYS}"' in page
        assert reverse("live:begin", args=[game.pk]) not in page

    def test_toggles(self, org, collect):
        game, (alpha, *_) = collect
        page = board(org, game)
        assert page.count('aria-pressed="false"') == 3
        assert "Принять" in page

        response = act(org, game, "paid-on", result=alpha)

        alpha.refresh_from_db()
        assert alpha.entry_paid and alpha.buyin == 100
        assert response.text.count('aria-pressed="true"') == 1
        assert "Сдал" in response.text
        act(org, game, "paid-off", result=alpha)
        alpha.refresh_from_db()
        assert not alpha.entry_paid

    def test_back_to_the_lineup(self, org, collect):
        game, _ = collect
        page = board(org, game)
        assert "← Состав" in page
        act(org, game, "stage", **{"from": "collect", "to": "lineup"})
        assert stage(game) == Game.Stage.LINEUP

    def test_start_opens_the_sheet_while_someone_is_unpaid(self, org, collect):
        game, _ = collect
        assert reverse("live:begin", args=[game.pk]) in board(org, game)

    def test_query_count(self, org, django_assert_num_queries):
        def check(players):
            game = lineup_game(SeasonKind.CASH)
            seat(game, *(f"Игрок {players} {i}" for i in range(players)))
            actions.advance_stage(game.pk, key(), None, "lineup")
            # session, user, organizer check; game, results, totals, number
            with django_assert_num_queries(7):
                assert org.get(reverse("live:board", args=[game.pk])).status_code == 200
            game.delete()
            game.season.delete()

        check(3)
        check(12)


class TestBeginSheet:
    def test_lists_exactly_the_unpaid(self, org, collect):
        game, (alpha, bravo, charlie) = collect
        actions.set_paid(game.pk, key(), None, bravo.pk, True)

        sheet = org.get(reverse("live:begin", args=[game.pk])).text

        words = text(sheet)
        assert "Начать игру" in words and "2 не сдали" in words
        assert "Альфа" in words and "Чарли" in words and "Браво" not in words
        assert "Закупка всё равно засчитана" in words
        assert "К сбору" in words and "Начать всё равно" in words
        assert f'name="to" value="{Game.Stage.REBUYS}"' in sheet

    def test_one_unpaid_is_singular(self, org, collect):
        game, (alpha, bravo, charlie) = collect
        for result in (alpha, bravo):
            actions.set_paid(game.pk, key(), None, result.pk, True)
        assert "1 не сдал" in text(org.get(reverse("live:begin", args=[game.pk])).text)

    def test_start_anyway(self, org, collect):
        game, _ = collect
        act(org, game, "stage", **{"from": "collect", "to": "rebuys"})
        assert stage(game) == Game.Stage.REBUYS
        assert game.results.filter(entry_paid=False).count() == 3


class TestAfterTheStart:
    @pytest.fixture
    def playing(self, collect):
        game, results = collect
        actions.set_paid(game.pk, key(), None, results[0].pk, True)
        actions.advance_stage(game.pk, key(), None, "collect")
        return game, results

    def test_unpaid_line_on_the_board(self, org, playing):
        game, _ = playing
        words = text(board(org, game))
        assert words.count("не сдал 100 · принять") == 2

    def test_unpaid_line_stays_after_a_bust(self, org, playing):
        game, (_, bravo, _) = playing
        actions.eliminate(game.pk, key(), None, bravo.pk)
        page = board(org, game)
        assert text(page).count("не сдал 100 · принять") == 2

    def test_player_sheet_accepts_the_money(self, org, playing):
        game, (_, bravo, _) = playing
        sheet = org.get(reverse("live:player", args=[game.pk, bravo.pk])).text
        assert "Принять 100 лей" in text(sheet)
        assert "paid-on" in sheet

        act(org, game, "paid-on", result=bravo)

        bravo.refresh_from_db()
        assert bravo.entry_paid
        assert text(board(org, game)).count("не сдал 100") == 1

    def test_never_seated_is_offered_only_while_allowed(self, org, playing):
        game, (alpha, bravo, _) = playing
        url = reverse("live:player", args=[game.pk, bravo.pk])
        sheet = org.get(url).text
        assert "Не садился · убрать" in sheet and "hx-confirm" in sheet

        actions.rebuy(game.pk, key(), None, bravo.pk)

        assert "Не садился" not in org.get(url).text
        response = act(org, game, "unseat", result=bravo)
        assert "уже в игре: убрать нельзя" in response.text
        assert Result.objects.filter(pk=bravo.pk).exists()

    def test_never_seated_removes_the_row(self, org, playing):
        game, (alpha, *_) = playing
        act(org, game, "unseat", result=alpha)
        assert not Result.objects.filter(pk=alpha.pk).exists()

    def test_finished_games_have_no_sheets(self, org, playing):
        game, (alpha, *_) = playing
        Game.objects.filter(pk=game.pk).update(live_stage="")
        assert org.get(reverse("live:player", args=[game.pk, alpha.pk])).status_code == 404


class TestBustConfirmation:
    def tournament(self, players, **season):
        game = live_game(SeasonKind.TOUR, **season)
        return game, seat(game, *(f"Игрок {i}" for i in range(1, players + 1)))

    @pytest.mark.parametrize(("paid_places", "first_sheet"), [(3, 4), (2, 3)])
    def test_sheet_only_near_the_money(self, org, paid_places, first_sheet):
        weights = ",".join(["1"] * paid_places)
        game, results = self.tournament(6, paid_places=paid_places, payout_weights=weights)
        for result in results[: 6 - first_sheet - 1]:
            actions.eliminate(game.pk, key(), None, result.pk)
        # One above the threshold: the button acts at once and carries the place it shows.
        page = board(org, game)
        assert reverse("live:bust", args=[game.pk, results[-1].pk]) not in page
        assert f'name="place" value="{first_sheet + 1}"' in page

        actions.eliminate(game.pk, key(), None, results[6 - first_sheet - 1].pk)

        page = board(org, game)
        assert reverse("live:bust", args=[game.pk, results[-1].pk]) in page

    def test_first_place_without_payout(self, org):
        game, results = self.tournament(4)
        sheet = org.get(reverse("live:bust", args=[game.pk, results[0].pk])).text
        words = text(sheet)
        assert "Подтвердите выбывание" in words
        assert "Игрок 1" in words
        assert '<span class="bust-place-number">4</span>' in sheet
        assert "место, первое без выплаты." in words
        assert "В игре останутся трое, все в призах." in words
        assert 'Выбыл · <span class="font-num">4</span>-е место' in sheet
        assert 'name="place" value="4"' in sheet

    def test_prize_place(self, org):
        game, results = self.tournament(4)
        actions.eliminate(game.pk, key(), None, results[0].pk)
        sheet = org.get(reverse("live:bust", args=[game.pk, results[1].pk])).text
        assert "место, в призах." in text(sheet)
        assert 'Выбыл · <span class="font-num">3</span>-е место' in sheet

    def test_other_paid_places(self, org):
        game, results = self.tournament(4, paid_places=2, payout_weights="3,2")
        actions.eliminate(game.pk, key(), None, results[0].pk)
        words = text(org.get(reverse("live:bust", args=[game.pk, results[1].pk])).text)
        assert "место, первое без выплаты." in words
        assert "В игре останутся двое, все в призах." in words

    def test_confirm_records_the_bust(self, org):
        game, results = self.tournament(4)
        act(org, game, "eliminate", result=results[0], place="4")
        results[0].refresh_from_db()
        assert results[0].out_order == 1

    def test_stale_confirmation_refused(self, org):
        game, results = self.tournament(4)
        actions.eliminate(game.pk, key(), None, results[1].pk)
        response = act(org, game, "eliminate", result=results[0], place="4")
        assert "выбыл кто-то ещё" in response.text
        results[0].refresh_from_db()
        assert results[0].out_order is None


@pytest.mark.parametrize(
    ("place", "paid_places", "expected"),
    [
        (4, 3, ("место, первое без выплаты.", "В игре останутся трое, все в призах.")),
        (3, 2, ("место, первое без выплаты.", "В игре останутся двое, все в призах.")),
        (2, 1, ("место, первое без выплаты.", "В игре останется 1 игрок, в призах.")),
        (12, 11, ("место, первое без выплаты.", "В игре останутся 11 игроков, все в призах.")),
        (3, 3, ("место, в призах.", "")),
        (2, 3, ("место, в призах.", "")),
    ],
)
def test_bust_lines(place, paid_places, expected):
    assert bust_lines(place, paid_places) == expected


class TestSeatingAfterTheStart:
    def test_candidate_opens_the_late_sheet(self, org):
        game = live_game(SeasonKind.TOUR)
        delta = make_player("Дельта")
        page = org.get(reverse("live:seating", args=[game.pk])).text
        assert reverse("live:late", args=[game.pk, delta.pk]) in page

        sheet = text(org.get(reverse("live:late", args=[game.pk, delta.pk])).text)

        assert "Посадить · внёс 50" in sheet and "Посадить · не сдал" in sheet

    @pytest.mark.parametrize("paid", ["1", "0"])
    def test_seat_paid_or_not(self, org, paid):
        game = live_game(SeasonKind.CASH)
        delta = make_player("Дельта")
        response = org.post(
            reverse("live:seat", args=[game.pk]),
            {"key": str(uuid.uuid4()), "player": delta.pk, "paid": paid},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="player-list",
        )
        assert response.status_code == 200
        assert game.results.get(player=delta).entry_paid is (paid == "1")
        assert 'id="sheet" hx-swap-oob="true"' in response.text

    def test_new_player_form_has_both_buttons(self, org):
        game = live_game(SeasonKind.TOUR)
        page = text(org.get(reverse("live:seating", args=[game.pk])).text)
        assert "Посадить · внёс 50" in page and "Посадить · не сдал" in page

    def test_new_player_paid(self, org):
        game = live_game(SeasonKind.TOUR)
        org.post(
            reverse("live:seat_new", args=[game.pk]),
            {"key": str(uuid.uuid4()), "name": "Дельта", "nickname": "", "paid": "1"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="player-list",
        )
        assert game.results.get(player__name="Дельта").entry_paid

    def test_closed_during_the_collection(self, org, collect):
        game, _ = collect
        page = org.get(reverse("live:seating", args=[game.pk])).text
        assert "вернитесь к составу" in page


class TestNewPlayerInTheLineup:
    def test_sheet_and_seat(self, org, lineup):
        game, _ = lineup
        sheet = org.get(reverse("live:seat_new", args=[game.pk])).text
        assert 'hx-target="#board"' in sheet and "Создать и посадить" in sheet
        form_key = re.search(r'name="key" value="([^"]+)"', sheet).group(1)

        response = org.post(
            reverse("live:seat_new", args=[game.pk]),
            {"key": form_key, "name": "Дельта", "nickname": "Дед"},
            **HTMX,
        )

        result = game.results.get(player__name="Дельта")
        assert result.entry_paid is False
        assert '<section id="board"' in response.text
        assert 'id="new-player" hx-swap-oob' not in response.text

    def test_errors_stay_in_the_sheet(self, org, lineup):
        game, (alpha, *_) = lineup
        response = org.post(
            reverse("live:seat_new", args=[game.pk]),
            {"key": str(uuid.uuid4()), "name": alpha.player.name, "nickname": ""},
            **HTMX,
        )
        assert response["HX-Retarget"] == "#new-player"
        assert 'hx-target="#board"' in response.text
        assert '<h2 class="live-heading">Новый игрок</h2>' not in response.text


class TestCancelBeforePlay:
    def test_page_allows_it_with_players(self, org, lineup):
        game, _ = lineup
        words = text(org.get(reverse("live:cancel", args=[game.pk])).text)
        assert "Игра удалится вместе с составом" in words
        assert "отменить нельзя" not in words

    def test_cancel(self, org, lineup):
        game, _ = lineup
        response = act(org, game, "cancel")
        assert response["HX-Redirect"] == reverse("live:index")
        assert not Game.objects.filter(pk=game.pk).exists()


def test_results_screen_marks_unpaid(org):
    game = live_game(SeasonKind.TOUR)
    alpha, bravo = seat(game, "Альфа", "Браво")
    actions.set_paid(game.pk, key(), None, alpha.pk, True)
    actions.advance_stage(game.pk, key(), None, "rebuys")
    actions.advance_stage(game.pk, key(), None, "addon")
    words = text(org.get(reverse("live:results", args=[game.pk])).text)
    assert words.count("не сдал 50") == 1


class TestCollectHasNoUnseat:
    def test_no_control_and_refused(self, org, collect):
        game, (alpha, *_) = collect
        page = board(org, game)
        assert "unseat" not in page and "Убрать:" not in page and "Не садился" not in page
        assert "Не садился" not in org.get(reverse("live:player", args=[game.pk, alpha.pk])).text

        response = act(org, game, "unseat", result=alpha)

        assert "Состав утверждён: вернитесь к составу" in response.text
        assert Result.objects.filter(pk=alpha.pk).exists()


class TestStaleLineupControls:
    def test_lineup_forms_post_their_stage(self, org, lineup):
        game, _ = lineup
        page = board(org, game)
        # Every "+" seat and every "убрать" carries it.
        seat_forms = page.count(reverse("live:seat", args=[game.pk]))
        unseat_forms = page.count('aria-label="Убрать: ')
        assert page.count('name="stage" value="lineup"') == seat_forms + unseat_forms
        sheet = org.get(reverse("live:seat_new", args=[game.pk])).text
        assert 'name="stage" value="lineup"' in sheet

    @pytest.fixture(params=["collect", "play"])
    def moved(self, request, lineup):
        game, results = lineup
        actions.advance_stage(game.pk, key(), None, "lineup")
        if request.param == "play":
            actions.advance_stage(game.pk, key(), None, "collect")
        return game, results

    def assert_refused(self, response, game):
        assert response.status_code == 200
        assert "toast-warn" in response.text
        assert "Пока вы смотрели, этап сменился." in response.text
        # The board comes back in the stage the game is in now.
        assert '<section id="board"' in response.text
        assert 'name="stage" value="lineup"' not in response.text

    def test_seat(self, org, moved):
        game, _ = moved
        response = org.post(
            reverse("live:seat", args=[game.pk]),
            {"key": str(uuid.uuid4()), "player": make_player("Дельта").pk, "stage": "lineup"},
            **HTMX,
        )
        self.assert_refused(response, game)
        assert game.results.count() == 3

    def test_seat_new(self, org, moved):
        game, _ = moved
        response = org.post(
            reverse("live:seat_new", args=[game.pk]),
            {"key": str(uuid.uuid4()), "name": "Дельта", "nickname": "", "stage": "lineup"},
            **HTMX,
        )
        self.assert_refused(response, game)
        assert not game.results.filter(player__name="Дельта").exists()

    def test_unseat(self, org, moved):
        game, (alpha, *_) = moved
        response = act(org, game, "unseat", result=alpha, stage="lineup")
        self.assert_refused(response, game)
        assert Result.objects.filter(pk=alpha.pk).exists()


class TestLateEntryViews:
    def test_without_paid_refused(self, org):
        game = live_game(SeasonKind.TOUR)
        response = org.post(
            reverse("live:seat", args=[game.pk]),
            {"key": str(uuid.uuid4()), "player": make_player("Дельта").pk, "stage": "rebuys"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="player-list",
        )
        assert "Укажите, внёс ли игрок деньги за вход." in response.text
        assert not game.results.exists()

    def test_sheets_post_their_stage(self, org):
        game = live_game(SeasonKind.TOUR)
        alpha, *_ = seat(game, "Альфа", "Браво")
        late = org.get(reverse("live:late", args=[game.pk, make_player("Дельта").pk])).text
        assert late.count('name="stage" value="rebuys"') == 2
        sheet = org.get(reverse("live:player", args=[game.pk, alpha.pk])).text
        assert 'name="stage" value="rebuys"' in sheet


class TestReturnTheEntry:
    def test_only_for_a_paid_player(self, org):
        game = live_game(SeasonKind.TOUR)
        alpha, bravo = seat(game, "Альфа", "Браво")
        actions.set_paid(game.pk, key(), None, alpha.pk, True)

        paid = org.get(reverse("live:player", args=[game.pk, alpha.pk])).text
        unpaid = org.get(reverse("live:player", args=[game.pk, bravo.pk])).text

        assert "Вход уже сдан. Верните" in text(paid)
        assert "Верните 50 лей." in text(paid)
        assert re.search(r'hx-confirm="[^"]*Верните 50 лей\."', paid)
        assert "Верните" not in unpaid
