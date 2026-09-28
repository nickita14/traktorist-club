"""The blind timer on the live screens: the board strip, the structure editor, the display and
its secret link."""

import datetime
import uuid

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from club.models import Game, SeasonKind
from club.tests.factories import make_game, make_season, make_structure
from live import actions
from live.models import ActionKind, BlindTimer, LiveAction
from live.tests.conftest import key, live_game, seat
from live.tests.test_timer import FrozenClock

pytestmark = pytest.mark.django_db

ADMIN_PATH = f"/{settings.ADMIN_URL}"


@pytest.fixture
def org(client, organizer):
    client.force_login(organizer)
    return client


@pytest.fixture
def frozen(monkeypatch):
    frozen = FrozenClock()
    monkeypatch.setattr(timezone, "now", lambda: frozen.now)
    return frozen


@pytest.fixture
def timed(frozen):
    """A live tournament, three players, the test structure's timer paused at level 1."""
    game = live_game(SeasonKind.TOUR)
    seat(game, "Альфа", "Браво", "Чарли")
    actions.start_timer(game.pk, key(), None, make_structure().pk)
    return game


def timer(game) -> BlindTimer:
    return BlindTimer.objects.get(game=game)


def act(client, game, name, *, target="board", **data):
    return client.post(
        reverse("live:act", args=[game.pk, name]),
        {"key": str(uuid.uuid4())} | data,
        headers={"HX-Request": "true", "HX-Target": target},
    )


def board(client, game) -> str:
    return client.get(reverse("live:board", args=[game.pk])).text


class TestBoardStrip:
    def test_timer_replaces_the_stage_time(self, org, timed):
        page = board(org, timed)

        assert 'Уровень <span class="font-num">1</span>' in page
        assert '<span class="font-num" data-clock>20:00</span>' in page
        assert '<span class="font-num num-run">25 / 50</span>' in page
        assert 'data-remaining="1200" data-stopped' in page
        assert "Ребаи открыты до конца" in page
        assert "идёт <span" not in page  # no stage 1 elapsed line
        assert ">Пуск</button>" in page
        assert reverse("live:blinds", args=[timed.pk]) in page
        assert reverse("live:tablo", args=[timed.pk]) in page

    def test_running_with_ante(self, org, timed, frozen):
        actions.set_paused(timed.pk, key(), None, False)
        frozen.advance(minutes=45, seconds=23)

        page = board(org, timed)

        assert '<span class="font-num" data-clock>14:37</span>' in page
        assert (
            '<span class="phase-quiet">анте</span> <span class="font-num num-run">25</span>' in page
        )
        assert 'data-remaining="877">' in page
        assert ">Пауза</button>" in page
        assert '<input type="hidden" name="from" value="3">' in page

    def test_addon_break_is_suggested_but_not_applied(self, org, timed, frozen):
        actions.set_paused(timed.pk, key(), None, False)
        frozen.advance(minutes=62)

        page = board(org, timed)

        assert "По таймеру перерыв на аддон" in page
        assert "Перерыв на аддон</button>" in page
        timed.refresh_from_db()
        assert timed.live_stage == Game.Stage.REBUYS

    def test_levels_ran_out(self, org, timed, frozen):
        actions.set_paused(timed.pk, key(), None, False)
        frozen.advance(minutes=300)

        page = board(org, timed)

        assert "уровни закончились · добавьте уровень" in page
        assert f'href="{reverse("live:blinds", args=[timed.pk])}#add-level">+ Уровень</a>' in page
        assert "data-stopped" in page

    def test_without_a_timer_the_board_offers_one(self, org):
        game = live_game(SeasonKind.TOUR)
        page = board(org, game)
        assert "Подключить таймер блайндов" in page
        assert "data-timer" not in page

    def test_cash_board_has_no_timer(self, org):
        page = board(org, live_game(SeasonKind.CASH))
        assert "таймер" not in page.lower()


class TestBoardActions:
    def test_run_pause_next(self, org, timed, frozen):
        response = act(org, timed, "timer-run")
        assert "Таймер запущен" in response.text
        frozen.advance(minutes=3)

        response = act(org, timed, "timer-next", **{"from": 1})
        assert "Таймер: уровень 2 · 50 / 100" in response.text
        assert 'data-remaining="1200">' in response.text

        response = act(org, timed, "timer-pause")
        assert "Таймер: пауза, уровень 2" in response.text
        assert "отменить" in response.text

    def test_stale_next_is_a_warning(self, org, timed):
        act(org, timed, "timer-next", **{"from": 1})
        response = act(org, timed, "timer-next", **{"from": 1})
        assert "toast-warn" in response.text
        assert "Уровень уже сменился." in response.text

    def test_same_key_twice_counts_once(self, org, timed):
        same = str(uuid.uuid4())
        for _ in range(2):
            org.post(
                reverse("live:act", args=[timed.pk, "timer-next"]),
                {"key": same, "from": 1},
                headers={"HX-Request": "true", "HX-Target": "board"},
            )
        assert timer(timed).position == 2
        assert LiveAction.objects.filter(kind=ActionKind.LEVEL_NEXT).count() == 1

    def test_bad_numbers(self, org, timed):
        response = act(org, timed, "timer-next", **{"from": "x"})
        assert "Нужны целые числа." in response.text

    def test_undo_from_the_toast(self, org, timed, frozen):
        act(org, timed, "timer-run")
        frozen.advance(minutes=5)
        act(org, timed, "timer-plus")
        action = LiveAction.objects.get(kind=ActionKind.PLUS_MINUTE)

        response = org.post(
            reverse("live:undo", args=[timed.pk, action.pk]),
            headers={"HX-Request": "true", "HX-Target": "board"},
        )

        assert "Отменено: Таймер: +1 минута" in response.text
        assert '<span class="font-num" data-clock>15:00</span>' in response.text


class TestStructureEditor:
    def url(self, game):
        return reverse("live:blinds", args=[game.pk])

    def test_rows(self, org, timed, frozen):
        actions.set_paused(timed.pk, key(), None, False)
        frozen.advance(minutes=25)  # level 2, 5 minutes in

        page = org.get(self.url(timed)).text

        played = page.split('class="blinds-row blinds-row-played"')[1].split("</li>")[0]
        current = page.split('aria-current="step"')[1].split("</li>")[0]
        assert "<input" not in played
        assert 'aria-current="step"' in page
        # The current level is past its first minute: minutes only, blinds locked.
        assert 'name="minutes"' in current and 'name="small"' not in current
        assert 'id="small-3"' in page  # a later level stays open
        assert '<option value="1">' not in page.split('name="from"')[1].split("</select>")[0]
        for minutes in (10, 15, 20, 30):
            assert f'value="{minutes}" aria-label="По {minutes} минут"' in page
        assert 'value="200" aria-label="Малый блайнд нового уровня"' in page
        assert f'value="http://testserver/tablo/{timer(timed).display_token}/"' in page

    def test_current_blinds_open_while_paused(self, org, timed):
        current = org.get(self.url(timed)).text.split('aria-current="step"')[1].split("</li>")[0]
        assert 'name="small"' in current

    def test_edit_answers_with_the_editor(self, org, timed):
        response = act(
            org, timed, "level-edit", target="blinds", position=5, minutes=25, small=200,
            big=400, ante=50,
        )  # fmt: skip

        assert '<section id="blinds"' in response.text
        assert "Таймер: уровень 4 · 200 / 400, анте 50, 25 мин" in response.text
        assert 'id="minutes-5" class="live-input live-input-xs" type="number"' in response.text

    def test_break_minutes_only(self, org, timed):
        act(org, timed, "level-edit", target="blinds", position=4, minutes=20)
        assert timer(timed).levels.get(position=4).minutes == 20

    def test_refused_edit_is_a_warning(self, org, timed, frozen):
        actions.set_paused(timed.pk, key(), None, False)
        frozen.advance(minutes=25)
        response = act(org, timed, "level-edit", target="blinds", position=1, minutes=30)
        assert "toast-warn" in response.text
        assert "уже сыгран" in response.text

    def test_bulk_and_add(self, org, timed):
        act(org, timed, "bulk-minutes", target="blinds", **{"from": 2, "minutes": 15})
        act(org, timed, "level-add", target="blinds", small=300, big=600, ante=50, minutes=15)

        minutes = list(timer(timed).levels.values_list("minutes", flat=True))
        assert minutes == [20, 15, 15, 15, 15, 10, 15, 15]

    def test_attach_a_timer(self, org, frozen):
        structure = make_structure(name="Обычный турнир")
        season = make_season(2026, SeasonKind.TOUR, default_blinds=structure)
        game = actions.start_game(
            key(), None, season=season, date=datetime.date(2026, 9, 28)
        ).action.game
        page = org.get(self.url(game)).text
        assert f'<option value="{structure.pk}" selected>Обычный турнир</option>' in page

        response = act(org, game, "timer-start", target="blinds", structure=structure.pk)

        assert "Таймер: Обычный турнир" in response.text
        assert BlindTimer.objects.filter(game=game).exists()

    def test_cash_game_has_no_editor(self, org):
        game = live_game(SeasonKind.CASH)
        assert org.get(self.url(game)).url == reverse("live:board", args=[game.pk])

    def test_reset_link(self, org, timed):
        old = timer(timed).display_token
        response = act(org, timed, "link-reset", target="blinds")
        assert "Табло: новая ссылка" in response.text
        assert timer(timed).display_token != old
        assert old not in response.text


class TestStartForm:
    def test_season_default_preselected(self, org):
        page = org.get(reverse("live:start")).text
        assert '<option value="season" selected>Как в сезоне</option>' in page
        assert '<option value="">Без таймера</option>' in page

    def test_start_with_a_structure(self, org, frozen):
        structure = make_structure()
        season = make_season(2026, SeasonKind.TOUR)
        org.post(
            reverse("live:start"),
            {"key": str(uuid.uuid4()), "season": season.pk, "date": "2026-09-28",
             "blinds": structure.pk},
        )  # fmt: skip
        assert BlindTimer.objects.filter(game__season=season).exists()

    def test_start_without_a_timer(self, org, frozen):
        season = make_season(2026, SeasonKind.TOUR, default_blinds=make_structure())
        org.post(
            reverse("live:start"),
            {"key": str(uuid.uuid4()), "season": season.pk, "date": "2026-09-28", "blinds": ""},
        )
        assert Game.objects.filter(season=season).exists()
        assert not BlindTimer.objects.exists()


STATE_KEYS = {"now", "position", "started", "paused", "levels", "stage", "players", "total", "bank"}


class TestDisplay:
    def test_organizer_page_has_controls(self, org, timed):
        response = org.get(reverse("live:tablo", args=[timed.pk]))
        page = response.text

        assert response.status_code == 200
        assert response["X-Robots-Tag"] == "noindex, nofollow"
        assert 'data-act="toggle"' in page and ">Продолжить</button>" in page
        assert f'data-act-url="{reverse("live:tablo_act", args=[timed.pk, "NAME"])}"' in page
        assert 'data-state-url="' + reverse("live:tablo_state", args=[timed.pk]) in page
        assert 'Турнир № <span class="font-num">1</span>' in page
        assert (
            'Уровень <span class="font-num">1</span> · из <span class="font-num">5</span>' in page
        )

    def test_first_frame(self, org, timed, frozen):
        actions.set_paused(timed.pk, key(), None, False)
        frozen.advance(minutes=6)
        page = org.get(reverse("live:tablo", args=[timed.pk])).text

        assert 'data-clock role="timer" aria-live="off">14:00</p>' in page
        assert "data-until-break>54:00</dd>" in page
        assert 'после <span class="font-num">3</span>-го уровня · аддон' in page
        assert 'до конца <span class="font-num">3</span>-го уровня' in page
        assert "data-players>3 / 3</dd>" in page
        assert "data-bank>150</dd>" in page
        assert "data-paused-stamp hidden" in page

    def test_no_break_left_hides_the_block(self, org, timed, frozen):
        actions.set_paused(timed.pk, key(), None, False)
        frozen.advance(minutes=106)  # the last level
        page = org.get(reverse("live:tablo", args=[timed.pk])).text
        assert "data-until-break-block hidden" in page

    def test_break_replaces_the_blinds(self, org, timed, frozen):
        actions.set_paused(timed.pk, key(), None, False)
        frozen.advance(minutes=61)
        page = org.get(reverse("live:tablo", args=[timed.pk])).text
        assert "tablo-is-break" in page
        assert "data-blinds hidden" in page
        assert "data-break-label>Перерыв · аддон</span>" in page

    def test_state(self, org, timed, frozen):
        data = org.get(reverse("live:tablo_state", args=[timed.pk])).json()

        assert set(data) == STATE_KEYS
        assert data["now"] == round(frozen.now.timestamp() * 1000)
        assert data["paused"] == data["started"]
        assert (data["position"], data["stage"], data["players"], data["bank"]) == (
            1, "rebuys", 3, 150,
        )  # fmt: skip
        assert data["levels"][3] == {
            "position": 4, "small": None, "big": None, "ante": 0, "minutes": 15,
            "label": "Перерыв · аддон", "addon": True,
        }  # fmt: skip

    def test_act_answers_with_the_state(self, org, timed):
        url = reverse("live:tablo_act", args=[timed.pk, "timer-run"])
        data = org.post(url, {"key": str(uuid.uuid4()), "from": 1}).json()
        assert data["paused"] is None
        assert data["error"] is None

        data = org.post(url, {"key": str(uuid.uuid4()), "from": 1}).json()
        assert data["error"] == "Таймер уже идёт."

    def test_act_is_idempotent(self, org, timed):
        url = reverse("live:tablo_act", args=[timed.pk, "timer-next"])
        same = str(uuid.uuid4())
        for _ in range(3):
            org.post(url, {"key": same, "from": 1})
        assert timer(timed).position == 2

    def test_act_only_timer_actions(self, org, timed):
        url = reverse("live:tablo_act", args=[timed.pk, "rebuy"])
        assert org.post(url, {"key": str(uuid.uuid4())}).status_code == 400

    def test_no_timer_or_finished_game(self, org, timed):
        game = make_game(
            timed.season, day=29, month=9, live_stage="rebuys", started_at=timezone.now()
        )
        assert org.get(reverse("live:tablo", args=[game.pk])).status_code == 404
        Game.objects.filter(pk=timed.pk).update(live_stage="")
        assert org.get(reverse("live:tablo_state", args=[timed.pk])).status_code == 404


class TestSecretLink:
    def url(self, game, name="tablo_public"):
        return reverse(name, args=[timer(game).display_token])

    def test_read_only_without_login(self, client, timed):
        response = client.get(self.url(timed))
        page = response.text

        assert response.status_code == 200
        assert response["X-Robots-Tag"] == "noindex, nofollow"
        assert '<meta name="robots" content="noindex, nofollow">' in page
        assert "data-act" not in page
        assert "csrf" not in page.lower()
        assert ADMIN_PATH not in page
        assert "/live/" not in page
        assert f'data-state-url="{self.url(timed, "tablo_public_state")}"' in page

    def test_no_admin_path_even_for_an_organizer(self, org, timed):
        page = org.get(self.url(timed)).text
        assert ADMIN_PATH not in page
        assert "data-act" not in page
        assert "manifest" not in page

    def test_state_without_login(self, client, timed):
        data = client.get(self.url(timed, "tablo_public_state")).json()
        assert set(data) == STATE_KEYS

    def test_strict_csp(self, client, timed):
        policy = client.get(self.url(timed))["Content-Security-Policy"]
        assert "script-src 'self'" in policy
        assert "unsafe" not in policy

    def test_revoked(self, client, timed):
        old = self.url(timed)
        old_state = self.url(timed, "tablo_public_state")
        actions.reset_link(timed.pk, key(), None)

        assert client.get(old).status_code == 404
        assert client.get(old_state).status_code == 404
        assert client.get(self.url(timed)).status_code == 200

    def test_finished_game(self, client, timed):
        url = self.url(timed)
        Game.objects.filter(pk=timed.pk).update(live_stage="")
        assert client.get(url).status_code == 404

    def test_unknown_token(self, client, db):
        assert client.get(reverse("tablo_public", args=["nope"])).status_code == 404

    def test_robots(self, client, settings):
        settings.SITE_INDEXING = True
        assert "Disallow: /tablo/" in client.get("/robots.txt").text


class TestPermissions:
    def urls(self, game):
        return [
            reverse("live:blinds", args=[game.pk]),
            reverse("live:tablo", args=[game.pk]),
            reverse("live:tablo_state", args=[game.pk]),
        ]

    def test_anonymous_goes_to_login(self, client, timed):
        for url in self.urls(timed):
            response = client.get(url)
            assert response.status_code == 302
            assert response.url.startswith(reverse("admin:login"))
        response = client.post(
            reverse("live:tablo_act", args=[timed.pk, "timer-run"]), {"key": str(uuid.uuid4())}
        )
        assert response.status_code == 302
        assert timer(timed).paused_at is not None

    def test_non_organizer_forbidden(self, client, timed):
        client.force_login(User.objects.create_user("test-visitor"))
        for url in self.urls(timed):
            assert client.get(url).status_code == 403
        url = reverse("live:tablo_act", args=[timed.pk, "timer-run"])
        assert client.post(url, {"key": str(uuid.uuid4())}).status_code == 403


class TestQueries:
    def test_public_state(self, client, timed, django_assert_num_queries):
        url = reverse("tablo_public_state", args=[timer(timed).display_token])
        # Timer with game and season, levels, totals.
        with django_assert_num_queries(3):
            client.get(url)

    def test_public_page(self, client, timed, django_assert_num_queries):
        url = reverse("tablo_public", args=[timer(timed).display_token])
        # The state's three plus the game's number.
        with django_assert_num_queries(4):
            client.get(url)

    def test_organizer_state(self, org, timed, django_assert_num_queries):
        url = reverse("live:tablo_state", args=[timed.pk])
        # Session, user, organizer check, game, timer, levels, totals.
        with django_assert_num_queries(7):
            org.get(url)

    def test_board_with_a_timer_costs_two_more(self, org, timed, django_assert_max_num_queries):
        with django_assert_max_num_queries(10):
            org.get(reverse("live:board", args=[timed.pk]))
