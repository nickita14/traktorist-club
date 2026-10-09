"""The sheet sync admin page: access, the snapshot lifecycle and applying what was previewed.

``importer.sources.fetch_sheet`` is replaced in every test: nothing touches the network.
"""

import datetime

import pytest
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils import timezone

from club.models import Game, Result
from club.tests.factories import make_game, make_season
from importer import sources
from importer.aliases import build_alias_map, upsert_aliases
from importer.models import SheetSnapshot
from importer.tests.sheet_builder import build_workbook, fixture_aliases

pytestmark = pytest.mark.django_db

SHEET_ID = "fake-sheet-id-0123456789abcdefghij"
URL_NAME = "admin:importer_sheetsnapshot_sync"


@pytest.fixture
def url():
    return reverse(URL_NAME)


@pytest.fixture
def workbook_bytes(tmp_path):
    return build_workbook(tmp_path / "club.xlsx").read_bytes()


@pytest.fixture
def google(settings, monkeypatch, workbook_bytes):
    """The sheet is configured and Google answers with the fixture workbook."""
    settings.GOOGLE_SHEET_ID = SHEET_ID
    calls = []

    def fetch():
        calls.append(1)
        return workbook_bytes

    monkeypatch.setattr(sources, "fetch_sheet", fetch)
    return calls


@pytest.fixture
def aliases():
    upsert_aliases(build_alias_map(fixture_aliases()))


@pytest.fixture
def superuser_client(client, admin_user):
    client.force_login(admin_user)
    return client


@pytest.fixture
def organizer_client(client):
    user = User.objects.create_user("test-organizer", is_staff=True)
    group, _ = Group.objects.get_or_create(name="Organizer")
    user.groups.add(group)
    client.force_login(user)
    return client


def db_counts():
    return tuple(m.objects.count() for m in (Game, Result))


def fetch(client, url):
    """Press "Подтянуть из таблицы" and open the report it redirects to."""
    response = client.post(url, {"action": "fetch"})
    assert response.status_code == 302
    return client.get(response.url)


def apply(client, url, page):
    return client.post(
        url,
        {
            "action": "apply",
            "snapshot": page.context["snapshot"].pk,
            "digest": page.context["digest"],
        },
        follow=True,
    )


def messages_of(response) -> list[str]:
    return [str(message) for message in response.context["messages"]]


class TestAccess:
    def test_anonymous_goes_to_the_login(self, client, url):
        response = client.get(url)
        assert response.status_code == 302
        assert response.url.startswith(reverse("admin:login"))

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_organizer_is_refused(self, organizer_client, url, google, method):
        response = getattr(organizer_client, method)(url, {"action": "fetch"})
        assert response.status_code == 403
        assert google == []  # nothing fetched

    def test_organizer_does_not_see_the_section(self, organizer_client):
        response = organizer_client.get(reverse("admin:index"))
        assert "Импорт" not in response.text
        assert "importer/sheetsnapshot" not in response.text

    def test_superuser_sees_the_section(self, superuser_client):
        response = superuser_client.get(reverse("admin:index"))
        assert reverse("admin:importer_sheetsnapshot_changelist") in response.text

    def test_changelist_redirects_to_the_page(self, superuser_client, url):
        response = superuser_client.get(reverse("admin:importer_sheetsnapshot_changelist"))
        assert response.status_code == 302
        assert response.url == url


class TestNotConfigured:
    def test_page_says_so_and_offers_nothing(self, superuser_client, url, settings):
        settings.GOOGLE_SHEET_ID = ""
        response = superuser_client.get(url)

        assert response.status_code == 200
        assert "Синхронизация выключена: не задан GOOGLE_SHEET_ID." in response.text
        assert 'value="fetch"' not in response.text

    def test_post_does_nothing(self, superuser_client, url, settings, monkeypatch):
        settings.GOOGLE_SHEET_ID = ""
        monkeypatch.setattr(sources, "fetch_sheet", lambda: pytest.fail("fetched"))
        superuser_client.post(url, {"action": "fetch"})
        assert not SheetSnapshot.objects.exists()


class TestPreview:
    def test_fetch_stores_a_snapshot_and_shows_a_dry_run(
        self, superuser_client, url, google, aliases
    ):
        page = fetch(superuser_client, url)

        snapshot = SheetSnapshot.objects.get()
        assert snapshot.sheets == ["ТУР2026_new", "ТУР2025", "КЭШ2026"]
        assert snapshot.fetched_by.is_superuser
        assert len(snapshot.sha256) == 64
        assert db_counts() == (0, 0)  # nothing written yet
        assert page.context["can_apply"]
        assert "результатов создано" in page.text

    def test_sheet_id_never_on_the_page(self, superuser_client, url, google, aliases):
        assert SHEET_ID not in superuser_client.get(url).text
        assert SHEET_ID not in fetch(superuser_client, url).text

    def test_checkboxes_list_only_import_sheets(self, superuser_client, url, google, aliases):
        page = fetch(superuser_client, url)

        assert page.context["titles"] == [
            ("ТУР2026_new", True),
            ("ТУР2025", True),
            ("КЭШ2026", True),
        ]
        assert "ТУР2026_old version" not in page.text

    def test_recount_for_chosen_sheets(self, superuser_client, url, google, aliases):
        snapshot_pk = fetch(superuser_client, url).context["snapshot"].pk

        response = superuser_client.post(
            url, {"action": "recount", "snapshot": snapshot_pk, "sheets": ["КЭШ2026"]}, follow=True
        )

        assert SheetSnapshot.objects.get().sheets == ["КЭШ2026"]
        assert [item["sheet"].title for item in response.context["sheets"]] == ["КЭШ2026"]
        assert google == [1]  # recounting never fetches

    def test_recount_needs_a_sheet(self, superuser_client, url, google, aliases):
        snapshot_pk = fetch(superuser_client, url).context["snapshot"].pk
        response = superuser_client.post(
            url, {"action": "recount", "snapshot": snapshot_pk}, follow=True
        )
        assert "Выберите хотя бы один лист." in messages_of(response)

    def test_unknown_names_listed_and_apply_disabled(self, superuser_client, url, google):
        page = fetch(superuser_client, url)  # no aliases loaded: every name is unknown

        assert not page.context["can_apply"]
        assert "Иван - Трактор" in page.text
        assert 'value="apply" class="sync-button sync-button-primary" disabled' in page.text

        response = apply(superuser_client, url, page)

        assert db_counts() == (0, 0)
        assert "Этот отчёт нельзя применить: исправьте ошибки в нём." in messages_of(response)

    def test_fetch_error_is_shown(self, superuser_client, url, settings, monkeypatch):
        settings.GOOGLE_SHEET_ID = SHEET_ID

        def fail():
            raise sources.SheetFetchError(sources.NOT_FOUND)

        monkeypatch.setattr(sources, "fetch_sheet", fail)
        response = superuser_client.post(url, {"action": "fetch"}, follow=True)

        assert sources.NOT_FOUND in messages_of(response)
        assert not SheetSnapshot.objects.exists()

    def test_not_a_workbook(self, superuser_client, url, settings, monkeypatch):
        settings.GOOGLE_SHEET_ID = SHEET_ID
        monkeypatch.setattr(sources, "fetch_sheet", lambda: b"PK\x03\x04 broken")
        response = superuser_client.post(url, {"action": "fetch"}, follow=True)
        assert "не читается как таблица" in messages_of(response)[0]


class TestApply:
    def test_applies_the_preview(self, superuser_client, url, google, aliases, admin_user):
        page = fetch(superuser_client, url)

        response = apply(superuser_client, url, page)

        assert response.status_code == 200
        assert response.context["applied"]
        assert db_counts() == (8, 22)
        assert not SheetSnapshot.objects.exists()
        entry = LogEntry.objects.get()
        assert (entry.user, entry.action_flag) == (admin_user, CHANGE)
        assert entry.change_message.startswith(
            "Импорт из таблицы: ТУР2026_new, ТУР2025, КЭШ2026; игр создано 8; "
            "результатов создано 22, изменено 0, удалено 0"
        )
        assert messages_of(response)[0].startswith("Применено. Импорт из таблицы")

    def test_uses_the_stored_bytes_never_a_new_fetch(
        self, superuser_client, url, google, aliases, monkeypatch
    ):
        page = fetch(superuser_client, url)
        monkeypatch.setattr(sources, "fetch_sheet", lambda: pytest.fail("fetched again"))

        apply(superuser_client, url, page)

        assert db_counts() == (8, 22)

    def test_only_the_chosen_sheets(self, superuser_client, url, google, aliases):
        snapshot_pk = fetch(superuser_client, url).context["snapshot"].pk
        page = superuser_client.post(
            url, {"action": "recount", "snapshot": snapshot_pk, "sheets": ["ТУР2025"]}, follow=True
        )

        apply(superuser_client, url, page)

        assert set(Game.objects.values_list("season__year", flat=True)) == {2025}

    def test_second_apply_is_refused(self, superuser_client, url, google, aliases):
        page = fetch(superuser_client, url)
        apply(superuser_client, url, page)
        Result.objects.update(payout=0)  # would show if the import ran again

        response = apply(superuser_client, url, page)

        assert "Этот снимок таблицы уже применён или устарел." in messages_of(response)[0]
        assert not Result.objects.exclude(payout=0).exists()
        assert LogEntry.objects.count() == 1

    def test_expired_snapshot_is_refused(self, superuser_client, url, google, aliases):
        page = fetch(superuser_client, url)
        old = timezone.now() - SheetSnapshot.TTL - datetime.timedelta(minutes=1)
        SheetSnapshot.objects.update(fetched_at=old)

        response = apply(superuser_client, url, page)

        assert db_counts() == (0, 0)
        assert not SheetSnapshot.objects.exists()
        assert "уже применён или устарел" in messages_of(response)[0]

    def test_database_changed_since_the_preview(self, superuser_client, url, google, aliases):
        page = fetch(superuser_client, url)
        # Meanwhile the 2025 tour season appears with a game the sheet does not have: the import
        # would now report a different set of changes than the page showed.
        make_game(make_season(2025, sheet_managed=True), day=1, month=9)

        response = apply(superuser_client, url, page)

        assert "Данные в базе изменились после просмотра" in messages_of(response)[0]
        assert db_counts() == (1, 0)  # only the game created above
        assert SheetSnapshot.objects.exists()  # kept, to preview again
        assert not LogEntry.objects.exists()


class TestCleanup:
    def test_new_fetch_replaces_the_snapshot(self, superuser_client, url, google, aliases):
        first = fetch(superuser_client, url).context["snapshot"].pk
        second = fetch(superuser_client, url).context["snapshot"].pk

        assert list(SheetSnapshot.objects.values_list("pk", flat=True)) == [second]
        assert first != second

    def test_old_snapshots_are_purged_on_any_visit(self, superuser_client, url, google):
        SheetSnapshot.objects.create(
            content=b"PK\x03\x04",
            sha256="0" * 64,
            fetched_at=timezone.now() - SheetSnapshot.TTL - datetime.timedelta(seconds=1),
        )

        superuser_client.get(url)

        assert not SheetSnapshot.objects.exists()

    def test_expired_snapshot_is_not_previewed(self, superuser_client, url, google, aliases):
        snapshot_pk = fetch(superuser_client, url).context["snapshot"].pk
        SheetSnapshot.objects.update(fetched_at=timezone.now() - datetime.timedelta(hours=2))

        page = superuser_client.get(f"{url}?snapshot={snapshot_pk}")

        assert "report" not in page.context
