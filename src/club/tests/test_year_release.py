"""The year report's release: preliminary, closed by hand, closed by the 1 January fallback;
close and reopen with their rules, log entries and the admin page "Годовые отчёты"."""

import datetime
import zoneinfo

import pytest
from django.contrib.admin.models import ADDITION, DELETION, LogEntry
from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils import timezone

from club import year_report
from club.models import SeasonKind, YearClosure
from club.tests.factories import make_game, make_player, make_result, make_season
from club.year_report import ReleaseError

pytestmark = pytest.mark.django_db

YEAR = 2026
LOCAL = zoneinfo.ZoneInfo("Europe/Chisinau")
NOVEMBER = datetime.date(YEAR, 11, 16)
NEW_YEAR = datetime.date(YEAR + 1, 1, 1)


@pytest.fixture
def games():
    """A baseline year and two evenings in YEAR."""
    a, b = make_player("Альфа"), make_player("Браво")
    old = make_season(YEAR - 1, SeasonKind.TOUR)
    season = make_season(YEAR, SeasonKind.TOUR)
    for game, rows in [
        (make_game(old, month=1, day=5), [(a, 100), (b, 0)]),
        (make_game(season, month=1, day=3), [(a, 100), (b, 0)]),
        (make_game(season, month=1, day=10), [(b, 100), (a, 0)]),
    ]:
        for player, payout in rows:
            make_result(game, player, payout=payout)
    return season


@pytest.fixture
def organizer():
    user = User.objects.create_user("test-organizer", is_staff=True)
    user.groups.add(Group.objects.get(name="Organizer"))
    return user


def at_local(monkeypatch, *args):
    """Pin timezone.now() to a local wall-clock time in Europe/Chisinau."""
    moment = datetime.datetime(*args, tzinfo=LOCAL)
    monkeypatch.setattr(timezone, "now", lambda: moment)


class TestRelease:
    def test_preliminary(self, games):
        state = year_report.release(YEAR, NOVEMBER)
        assert not state.closed
        assert not state.closed_by_hand
        assert not state.fallback
        assert state.computation_date == NOVEMBER

    def test_closed_by_hand_is_computed_as_of_1_january(self, games, organizer):
        year_report.close(YEAR, organizer, NOVEMBER)
        state = year_report.release(YEAR, NOVEMBER)
        assert state.closed and state.closed_by_hand and state.can_reopen
        assert state.computation_date == NEW_YEAR
        assert year_report.is_closed(YEAR, NOVEMBER)

    def test_fallback_on_1_january(self, games):
        state = year_report.release(YEAR, NEW_YEAR)
        assert state.closed and state.fallback and not state.can_reopen
        assert state.computation_date == NEW_YEAR
        assert year_report.release(YEAR, datetime.date(YEAR + 3, 6, 1)).computation_date == NEW_YEAR

    def test_local_date_boundary(self, games, monkeypatch):
        # 23:30 on 31 December in Chisinau: still the old year.
        at_local(monkeypatch, YEAR, 12, 31, 23, 30)
        assert timezone.localdate() == datetime.date(YEAR, 12, 31)
        assert not year_report.is_closed(YEAR)
        # 00:30 on 1 January in Chisinau is still 31 December in UTC: the local date decides.
        at_local(monkeypatch, YEAR + 1, 1, 1, 0, 30)
        assert timezone.now().astimezone(datetime.UTC).date() == datetime.date(YEAR, 12, 31)
        assert year_report.is_closed(YEAR)
        assert year_report.release(YEAR).fallback

    def test_reopen_boundary_uses_the_local_date(self, games, organizer, monkeypatch):
        year_report.close(YEAR, organizer, NOVEMBER)
        at_local(monkeypatch, YEAR + 1, 1, 1, 0, 30)
        with pytest.raises(ReleaseError, match="После 1 января"):
            year_report.reopen(YEAR, organizer)
        at_local(monkeypatch, YEAR, 12, 31, 23, 30)
        year_report.reopen(YEAR, organizer)
        assert not YearClosure.objects.exists()

    @pytest.mark.parametrize(
        ("evenings", "text"),
        [(1, "по 1 вечеру"), (2, "по 2 вечерам"), (41, "по 41 вечеру"), (44, "по 44 вечерам")],
    )
    def test_stamp_text(self, evenings, text):
        state = year_report.Release(YEAR, NOVEMBER, None)
        assert state.stamp_text(evenings) == f"на 16.11, {text}"


class TestCloseAndReopen:
    def test_close_logs_one_addition(self, games, organizer):
        closure = year_report.close(YEAR, organizer, NOVEMBER)
        assert closure.closed_by == organizer
        entries = LogEntry.objects.all()
        assert [(e.action_flag, e.object_id, e.user_id) for e in entries] == [
            (ADDITION, str(closure.pk), organizer.pk)
        ]
        assert entries[0].change_message == f"Сезон {YEAR} закрыт: годовой отчёт открыт всем."

    def test_reopen_logs_one_deletion(self, games, organizer):
        year_report.close(YEAR, organizer, NOVEMBER)
        year_report.reopen(YEAR, organizer, datetime.date(YEAR, 12, 31))
        assert not YearClosure.objects.exists()
        flags = list(LogEntry.objects.order_by("pk").values_list("action_flag", flat=True))
        assert flags == [ADDITION, DELETION]
        assert not year_report.is_closed(YEAR, datetime.date(YEAR, 12, 31))

    @pytest.mark.parametrize(
        ("year", "message"),
        [
            (YEAR - 2, "нет сыгранных игр"),
            (YEAR - 1, "первый год в базе"),
        ],
    )
    def test_close_refused(self, games, organizer, year, message):
        with pytest.raises(ReleaseError, match=message):
            year_report.close(year, organizer, NOVEMBER)
        assert not LogEntry.objects.exists()

    def test_close_refused_for_a_future_year(self, games, organizer):
        make_result(
            make_game(make_season(YEAR + 1, SeasonKind.TOUR), month=2, day=1), make_player()
        )
        with pytest.raises(ReleaseError, match="ещё не начался"):
            year_report.close(YEAR + 1, organizer, NOVEMBER)

    def test_close_refused_when_closed(self, games, organizer):
        year_report.close(YEAR, organizer, NOVEMBER)
        with pytest.raises(ReleaseError, match="уже закрыт"):
            year_report.close(YEAR, organizer, NOVEMBER)
        YearClosure.objects.all().delete()
        with pytest.raises(ReleaseError, match="уже закрыт"):  # the fallback
            year_report.close(YEAR, organizer, NEW_YEAR)
        assert LogEntry.objects.count() == 1

    def test_reopen_refused(self, games, organizer):
        with pytest.raises(ReleaseError, match="не закрыт"):
            year_report.reopen(YEAR, organizer, NOVEMBER)
        with pytest.raises(ReleaseError, match="закрылся 1 января"):
            year_report.reopen(YEAR, organizer, NEW_YEAR)
        year_report.close(YEAR, organizer, NOVEMBER)
        with pytest.raises(ReleaseError, match="После 1 января"):
            year_report.reopen(YEAR, organizer, NEW_YEAR)
        assert YearClosure.objects.count() == 1

    def test_deleting_the_user_keeps_the_year_closed(self, games, organizer):
        year_report.close(YEAR, organizer, NOVEMBER)
        organizer.delete()
        closure = YearClosure.objects.get()
        assert closure.closed_by is None
        assert year_report.is_closed(YEAR, NOVEMBER)

    def test_a_game_added_to_a_closed_year_counts(self, games, organizer):
        year_report.close(YEAR, organizer, NOVEMBER)
        date = year_report.release(YEAR, NOVEMBER).computation_date
        assert year_report.load(YEAR, date).club.evening_count == 2
        make_result(make_game(games, month=12, day=28), make_player())
        assert year_report.load(YEAR, date).club.evening_count == 3

    def test_summaries(self, games, organizer):
        year_report.close(YEAR, organizer, NOVEMBER)
        rows = year_report.summaries(NOVEMBER)
        assert [(r.year, r.evenings, r.participants, r.first, r.can_close) for r in rows] == [
            (YEAR, 2, 2, False, False),
            (YEAR - 1, 1, 2, True, False),
        ]
        assert rows[0].last_game == datetime.date(YEAR, 1, 10)


class TestAdmin:
    @pytest.fixture(autouse=True)
    def november(self, monkeypatch):
        """The views use the real local date: pin it inside the preliminary period."""
        at_local(monkeypatch, YEAR, 11, 16, 12, 0)

    @pytest.fixture
    def urls(self):
        return {
            "list": reverse("admin:club_yearclosure_changelist"),
            "close": reverse("admin:club_yearclosure_close", args=[YEAR]),
            "reopen": reverse("admin:club_yearclosure_reopen", args=[YEAR]),
        }

    def test_anonymous_goes_to_login(self, client, games, urls):
        for url in urls.values():
            response = client.get(url)
            assert response.status_code == 302
            assert reverse("admin:login") in response.url
        client.post(urls["close"])
        assert not YearClosure.objects.exists()

    def test_staff_without_the_group_is_refused(self, client, games, urls):
        client.force_login(User.objects.create_user("test-staff", is_staff=True))
        for url in urls.values():
            assert client.get(url).status_code == 403
        assert client.post(urls["close"]).status_code == 403
        assert not YearClosure.objects.exists()

    def test_list_states(self, client, games, organizer, urls):
        client.force_login(organizer)
        html = client.get(urls["list"]).content.decode()
        assert "Годовые отчёты" in html
        assert "Предварительный" in html
        assert "Нет отчёта" in html
        assert urls["close"] in html
        year_report.close(YEAR, organizer)
        html = client.get(urls["list"]).content.decode()
        assert "Закрыт вручную" in html
        assert "test-organizer" in html

    def test_get_never_changes_state(self, client, games, organizer, urls):
        client.force_login(organizer)
        html = client.get(urls["close"]).content.decode()
        assert f"Закрыть сезон {YEAR}?" in html
        assert f"Да, закрыть сезон {YEAR}" in html
        assert not YearClosure.objects.exists()
        year_report.close(YEAR, organizer)
        client.get(urls["reopen"])
        assert YearClosure.objects.exists()

    def test_organizer_closes_and_reopens(self, client, games, organizer, urls):
        client.force_login(organizer)
        response = client.post(urls["close"], follow=True)
        assert response.redirect_chain[-1][0] == urls["list"]
        assert f"Сезон {YEAR} закрыт" in response.content.decode()
        assert YearClosure.objects.get().closed_by == organizer
        client.post(urls["reopen"])
        assert not YearClosure.objects.exists()
        assert LogEntry.objects.count() == 2

    def test_refusal_is_a_message(self, client, games, organizer):
        client.force_login(organizer)
        url = reverse("admin:club_yearclosure_close", args=[YEAR - 1])
        response = client.post(url, follow=True)
        assert "первый год в базе" in response.content.decode()
        assert not YearClosure.objects.exists()

    def test_unknown_year_is_404(self, client, games, organizer):
        client.force_login(organizer)
        url = reverse("admin:club_yearclosure_close", args=[YEAR - 5])
        assert client.get(url).status_code == 404

    def test_superuser(self, admin_client, games, urls):
        assert admin_client.get(urls["list"]).status_code == 200
        assert admin_client.get(urls["close"]).status_code == 200

    def test_no_plain_add_change_or_delete(self, client, games, organizer):
        client.force_login(organizer)
        closure = year_report.close(YEAR, organizer)
        add = client.get(reverse("admin:club_yearclosure_add"))
        assert add.status_code == 302
        delete = client.post(reverse("admin:club_yearclosure_delete", args=[closure.pk]))
        assert delete.status_code == 403
        assert YearClosure.objects.exists()
