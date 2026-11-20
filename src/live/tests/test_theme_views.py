"""Theme nights on the display's state, the sound and cover views, the preview page and the
theme admin."""

import hashlib

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from club.models import SeasonKind
from club.tests.factories import make_structure
from live import actions, themes
from live.models import (
    BlindTimer,
    EventKind,
    LiveAction,
    Theme,
    ThemeCover,
    ThemeEvent,
    ThemeSound,
)
from live.tests.conftest import key, live_game, seat
from live.tests.test_themes import JPEG, MP3, OGG, PNG, SVG, WAV

pytestmark = pytest.mark.django_db

ADMIN_PATH = f"/{settings.ADMIN_URL}"
EVENT_KEYS = {"id", "kind", "name", "place", "amount", "count", "note"}


@pytest.fixture
def org(client, organizer):
    client.force_login(organizer)
    return client


@pytest.fixture
def timed(db):
    game = live_game(SeasonKind.TOUR)
    results = seat(game, "Альфа", "Браво", "Чарли")
    actions.start_timer(game.pk, key(), None, make_structure().pk)
    return game, results


def public(game, name="tablo_public_state"):
    return reverse(name, args=[BlindTimer.objects.get(game=game).display_token])


def latest(game) -> int:
    return LiveAction.objects.filter(game=game).latest("pk").pk


class TestState:
    def test_page_starts_after_the_latest_action(self, client, timed):
        game, _ = timed
        page = client.get(public(game, "tablo_public")).text
        assert f'data-event-cursor="{latest(game)}"' in page
        assert "data-overlay hidden" in page
        assert "data-break-screen" in page

    def test_events_since_the_cursor(self, client, timed):
        game, results = timed
        cursor = latest(game)
        actions.eliminate(game.pk, key(), None, results[0].pk)

        data = client.get(public(game), {"after": cursor}).json()

        assert data["cursor"] == latest(game)
        assert [(e["kind"], e["name"], e["place"]) for e in data["events"]] == [
            ("bust_itm", "Альфа", 3)
        ]
        assert data["theme"]["events"]["bust"]["style"] == "stamp"

    def test_only_public_fields(self, client, timed):
        game, results = timed
        cursor = latest(game)
        for result in results[:2]:
            actions.eliminate(game.pk, key(), None, result.pk)

        data = client.get(public(game), {"after": cursor}).json()

        assert [event["kind"] for event in data["events"]] == ["bust_itm", "bust_itm", "winner"]
        assert all(set(event) == EVENT_KEYS for event in data["events"])
        text = str(data)
        for result in results:
            assert result.player.slug not in text
        assert ADMIN_PATH not in text

    def test_garbage_cursor_gets_the_latest(self, client, timed):
        game, results = timed
        actions.eliminate(game.pk, key(), None, results[0].pk)

        for value in ("", "abc", "-1"):
            data = client.get(public(game), {"after": value}).json()
            assert data["events"] == []
            assert data["cursor"] == latest(game)

    def test_no_events_without_a_cursor(self, client, timed):
        game, _ = timed
        data = client.get(public(game)).json()
        assert {"events", "cursor", "theme"}.isdisjoint(data)

    def test_organizer_state_has_events_too(self, org, timed):
        game, results = timed
        cursor = latest(game)
        actions.rebuy(game.pk, key(), None, results[0].pk)
        url = reverse("live:tablo_state", args=[game.pk])
        data = org.get(url, {"after": cursor}).json()
        assert [event["kind"] for event in data["events"]] == ["rebuy"]

    def test_query_count(self, client, timed, django_assert_num_queries):
        game, results = timed
        cursor = latest(game)
        Theme.objects.create(name="Вечер", is_active=True)
        for result in results[:2]:
            actions.eliminate(game.pk, key(), None, result.pk)
        url = public(game)
        # Timer with game and season, levels, totals; actions, the winner; theme and its events.
        with django_assert_num_queries(7):
            client.get(url, {"after": cursor})
        cursor = latest(game)
        with django_assert_num_queries(6):
            client.get(url, {"after": cursor})

    def test_cover_costs_no_query_and_no_bytes(self, client, timed, django_assert_num_queries):
        game, results = timed
        cursor = latest(game)
        theme = Theme.objects.create(name="Вечер", is_active=True, accent="#f2ede3")
        cover = themes.store_cover(theme, JPEG, "image/jpeg")
        for result in results[:2]:
            actions.eliminate(game.pk, key(), None, result.pk)
        url = public(game)
        with django_assert_num_queries(7) as queries:
            data = client.get(url, {"after": cursor}).json()

        assert data["theme"]["cover"] == f"/tablo/cover/{cover.sha256}/"
        assert data["theme"]["accent"] == "#f2ede3"
        sql = " ".join(query["sql"] for query in queries.captured_queries)
        assert '"live_themecover"."data"' not in sql

    def test_replaced_sound_reaches_the_display(self, client, org, timed):
        game, _ = timed
        theme = Theme.objects.create(name="Вечер", is_active=True)
        sound = themes.store_sound("a.ogg", OGG, "audio/ogg")
        ThemeEvent.objects.create(theme=theme, kind=EventKind.BUST, sound=sound)
        before = client.get(public(game), {"after": 0}).json()["theme"]["events"]["bust"]
        assert before["sound"] == f"/tablo/sound/{sound.sha256}/"

        response = org.post(
            reverse("admin:live_themesound_change", args=[sound.pk]),
            {"name": "Гонг", "upload": SimpleUploadedFile("b.wav", WAV)},
        )
        assert response.status_code == 302, response.text

        sound.refresh_from_db()
        after = client.get(public(game), {"after": 0}).json()["theme"]["events"]["bust"]
        assert after["sound"] == f"/tablo/sound/{sound.sha256}/" != before["sound"]
        assert client.get(after["sound"]).content == WAV


class TestSound:
    def sound(self):
        return themes.store_sound("a.ogg", OGG, "audio/ogg")

    def test_served_by_its_hash(self, client, db):
        sound = self.sound()
        response = client.get(reverse("tablo_sound", args=[sound.sha256]))

        assert response.status_code == 200
        assert response.content == OGG
        assert response["Content-Type"] == "audio/ogg"
        assert response["ETag"] == f'"{sound.sha256}"'
        assert response["Cache-Control"] == "public, max-age=31536000, immutable"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_not_modified(self, client, db, django_assert_num_queries):
        sound = self.sound()
        url = reverse("tablo_sound", args=[sound.sha256])
        with django_assert_num_queries(0):
            response = client.get(url, headers={"If-None-Match": f'"{sound.sha256}"'})
        assert response.status_code == 304

    def test_unknown(self, client, db):
        assert client.get(reverse("tablo_sound", args=["0" * 64])).status_code == 404
        assert client.get("/tablo/sound/xyz/").status_code == 404

    def test_policy_stays_strict(self, client, db):
        policy = client.get(reverse("tablo_sound", args=[self.sound().sha256]))[
            "Content-Security-Policy"
        ]
        assert "unsafe" not in policy
        assert "media-src" not in policy  # default-src 'self' covers it


class TestCover:
    def cover(self, name="Вечер", data=JPEG, content_type="image/jpeg"):
        return themes.store_cover(Theme.objects.create(name=name), data, content_type)

    def test_served_by_its_hash(self, client, db):
        cover = self.cover()
        response = client.get(reverse("tablo_cover", args=[cover.sha256]))

        assert response.status_code == 200
        assert response.content == JPEG
        assert response["Content-Type"] == "image/jpeg"
        assert response["ETag"] == f'"{cover.sha256}"'
        assert response["Cache-Control"] == "public, max-age=31536000, immutable"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["X-Robots-Tag"] == "noindex, nofollow"
        assert "unsafe" not in response["Content-Security-Policy"]

    def test_not_modified(self, client, db, django_assert_num_queries):
        cover = self.cover()
        url = reverse("tablo_cover", args=[cover.sha256])
        with django_assert_num_queries(0):
            response = client.get(url, headers={"If-None-Match": f'"{cover.sha256}"'})
        assert response.status_code == 304
        assert response["ETag"] == f'"{cover.sha256}"'
        assert response["Cache-Control"] == "public, max-age=31536000, immutable"

    def test_two_themes_share_an_image(self, client, db):
        first = self.cover("Первая")
        second = self.cover("Вторая")
        assert first.sha256 == second.sha256 and first.pk != second.pk

        response = client.get(reverse("tablo_cover", args=[first.sha256]))
        assert response.status_code == 200
        assert response.content == JPEG

    def test_unknown(self, client, db):
        assert client.get(reverse("tablo_cover", args=["0" * 64])).status_code == 404
        assert client.get("/tablo/cover/xyz/").status_code == 404


class TestPreview:
    def url(self, theme, name="live:theme_preview"):
        return reverse(name, args=[theme.pk])

    def test_organizer_sees_the_buttons(self, org, db):
        theme = Theme.objects.create(name="Вечер")
        response = org.get(self.url(theme))
        page = response.text

        assert response.status_code == 200
        assert "data-preview" in page
        for kind in EventKind.values:
            assert f'data-preview-kind="{kind}"' in page
        assert "Проверка · Вечер" in page
        assert "unsafe" not in response["Content-Security-Policy"]

    def test_state(self, org, db):
        theme = Theme.objects.create(name="Вечер", tips="раз")
        data = org.get(self.url(theme, "live:theme_preview_state")).json()
        assert data["theme"]["tips"] == ["раз"]
        assert set(data["samples"]) == set(EventKind.values) - {"break"}
        assert data["position"] == 1

        data = org.get(self.url(theme, "live:theme_preview_state"), {"scene": "break"}).json()
        assert data["levels"][data["position"] - 1]["big"] is None

    def test_state_carries_accent_and_cover(self, org, db):
        theme = Theme.objects.create(name="Вечер", accent="#f2ede3")
        cover = themes.store_cover(theme, PNG, "image/png")
        data = org.get(self.url(theme, "live:theme_preview_state"), {"scene": "break"}).json()
        assert data["theme"]["accent"] == "#f2ede3"
        assert data["theme"]["cover"] == f"/tablo/cover/{cover.sha256}/"

    def test_writes_nothing(self, org, db):
        theme = Theme.objects.create(name="Вечер")
        org.get(self.url(theme))
        org.get(self.url(theme, "live:theme_preview_state"))
        assert not LiveAction.objects.exists()

    def test_access(self, client, db):
        theme = Theme.objects.create(name="Вечер")
        response = client.get(self.url(theme))
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("admin:login"))

        client.force_login(User.objects.create_user("test-viewer", is_staff=True))
        assert client.get(self.url(theme)).status_code == 403
        assert client.get(self.url(theme, "live:theme_preview_state")).status_code == 403

    def test_unknown_theme(self, org, db):
        assert org.get(reverse("live:theme_preview", args=[999])).status_code == 404


class TestAdmin:
    def form(self, **fields):
        data = {
            "name": "Вечер",
            "is_active": "on",
            "tips": "",
            "events-TOTAL_FORMS": "1",
            "events-INITIAL_FORMS": "0",
            "events-MIN_NUM_FORMS": "0",
            "events-MAX_NUM_FORMS": "6",
            "events-0-kind": "bust",
            "events-0-enabled": "on",
            "events-0-title": "Мимо кассы",
            "events-0-caption": "{name} · {place} место",
            "events-0-style": "band",
            "events-0-seconds": "4",
            "events-0-sound": "",
        }
        return data | fields

    def test_organizer_adds_a_theme_with_a_sound(self, org):
        sound = themes.store_sound("hit.mp3", MP3, "audio/mpeg")
        response = org.post(
            reverse("admin:live_theme_add"), self.form(**{"events-0-sound": str(sound.pk)})
        )

        assert response.status_code == 302, response.text
        event = ThemeEvent.objects.get()
        assert (event.theme.name, event.theme.is_active, event.style) == ("Вечер", True, "band")
        assert event.sound == sound

    def test_rows_have_no_upload(self, org):
        sound = themes.store_sound("hit.mp3", MP3, "audio/mpeg")
        theme = Theme.objects.create(name="Вечер")
        ThemeEvent.objects.create(theme=theme, kind=EventKind.BUST, sound=sound)
        page = org.get(reverse("admin:live_theme_change", args=[theme.pk])).text

        assert "events-0-upload" not in page
        assert 'type="file"' in page  # only the cover
        assert f'data-sound-url="/tablo/sound/{sound.sha256}/"' in page
        assert "data-sound-player" in page
        assert "js/admin-theme-sounds" in page
        # The row's upload field is gone: a posted file is ignored, nothing is stored.
        data = self.form(**{"events-0-upload": SimpleUploadedFile("x.mp3", MP3), "name": "Б"})
        assert org.post(reverse("admin:live_theme_add"), data).status_code == 302
        assert ThemeSound.objects.count() == 1

    def test_bad_caption_is_an_error(self, org):
        data = self.form(**{"events-0-caption": "{name} {who}"})
        response = org.post(reverse("admin:live_theme_add"), data)

        assert response.status_code == 200
        assert "Неизвестная подстановка {who}" in response.text
        assert not Theme.objects.exists()

    def test_sound_admin(self, org):
        url = reverse("admin:live_themesound_add")
        response = org.post(url, {"name": "Гонг", "upload": SimpleUploadedFile("g.ogg", OGG)})
        assert response.status_code == 302, response.text
        sound = ThemeSound.objects.get()
        assert (sound.content_type, sound.size) == ("audio/ogg", len(OGG))

        again = org.post(url, {"name": "Ещё", "upload": SimpleUploadedFile("h.ogg", OGG)})
        assert "Этот звук уже загружен: «Гонг»." in again.text

        page = org.get(reverse("admin:live_themesound_change", args=[sound.pk])).text
        assert f'src="/tablo/sound/{sound.sha256}/"' in page
        assert "ogg · " in page and "1 КБ" in page
        assert 'name="upload"' in page

    def test_sound_file_replaced(self, org):
        sound = themes.store_sound("a.ogg", OGG, "audio/ogg")
        url = reverse("admin:live_themesound_change", args=[sound.pk])

        # Renaming keeps the file.
        assert org.post(url, {"name": "Гонг"}).status_code == 302
        sound.refresh_from_db()
        assert (sound.name, bytes(sound.data)) == ("Гонг", OGG)

        # The same checks as a new sound.
        wrong = org.post(url, {"name": "Гонг", "upload": SimpleUploadedFile("b.mp3", OGG)})
        assert "Это не mp3" in wrong.text
        other = themes.store_sound("c.mp3", MP3, "audio/mpeg")
        taken = org.post(url, {"name": "Гонг", "upload": SimpleUploadedFile("d.mp3", MP3)})
        assert f"Этот звук уже загружен: «{other.name}»." in taken.text

        # The same file again is no duplicate of itself.
        same = org.post(url, {"name": "Гонг", "upload": SimpleUploadedFile("a.ogg", OGG)})
        assert same.status_code == 302

        response = org.post(url, {"name": "Гонг", "upload": SimpleUploadedFile("e.wav", WAV)})
        assert response.status_code == 302, response.text
        sound.refresh_from_db()
        assert (sound.content_type, sound.size, bytes(sound.data)) == (
            "audio/wav",
            len(WAV),
            WAV,
        )
        assert sound.sha256 == hashlib.sha256(WAV).hexdigest()

    def test_sound_size_limit(self, org, settings):
        settings.THEME_SOUND_MAX_BYTES = 32
        sound = themes.store_sound("a.ogg", OGG, "audio/ogg")
        url = reverse("admin:live_themesound_change", args=[sound.pk])
        response = org.post(url, {"name": "Гонг", "upload": SimpleUploadedFile("e.wav", WAV)})
        assert "Звук больше" in response.text

    def test_cover_and_accent(self, org):
        response = org.post(
            reverse("admin:live_theme_add"),
            self.form(accent="#F2EDE3", cover=SimpleUploadedFile("c.png", PNG)),
        )
        assert response.status_code == 302, response.text
        theme = Theme.objects.get()
        assert theme.accent == "#f2ede3"
        assert (theme.cover.content_type, bytes(theme.cover.data)) == ("image/png", PNG)

        url = reverse("admin:live_theme_change", args=[theme.pk])
        page = org.get(url).text
        assert f'src="/tablo/cover/{theme.cover.sha256}/"' in page
        assert "png · " in page
        assert 'fill="#f2ede3"' in page
        assert 'name="remove_cover"' in page

        change = self.form(**{"events-TOTAL_FORMS": "0"})
        response = org.post(url, change | {"cover": SimpleUploadedFile("c.jpg", JPEG)})
        assert response.status_code == 302, response.text
        assert bytes(ThemeCover.objects.get().data) == JPEG

        both = change | {"cover": SimpleUploadedFile("c.png", PNG), "remove_cover": "on"}
        assert "Либо новая обложка, либо убрать." in org.post(url, both).text

        assert org.post(url, change | {"remove_cover": "on"}).status_code == 302
        assert not ThemeCover.objects.exists()

    @pytest.mark.parametrize(
        ("name", "data", "message"),
        [
            ("c.svg", SVG, "только jpg, png или webp"),
            ("c.png", SVG, "Это не png"),
        ],
    )
    def test_cover_refused(self, org, name, data, message):
        response = org.post(
            reverse("admin:live_theme_add"),
            self.form(cover=SimpleUploadedFile(name, data)),
        )
        assert response.status_code == 200
        assert message in response.text
        assert not Theme.objects.exists()

    def test_cover_size_limit(self, org, settings):
        settings.THEME_COVER_MAX_BYTES = 32
        response = org.post(
            reverse("admin:live_theme_add"),
            self.form(cover=SimpleUploadedFile("c.jpg", JPEG)),
        )
        assert "Обложка больше" in response.text
        assert not ThemeCover.objects.exists()

    def test_dark_accent_refused(self, org):
        response = org.post(reverse("admin:live_theme_add"), self.form(accent="#b3261e"))
        assert response.status_code == 200
        assert "Слишком тёмный для экрана перерыва" in response.text

    def test_change_page_and_preview_action(self, org):
        theme = Theme.objects.create(name="Вечер")
        ThemeEvent.objects.create(theme=theme, kind=EventKind.BUST)
        page = org.get(reverse("admin:live_theme_change", args=[theme.pk])).text
        assert "Проверить на табло" in page
        assert themes.PLACEHOLDER_HELP in page
        assert "приз" not in themes.PLACEHOLDER_HELP

        response = org.get(reverse("admin:live_theme_preview", args=[theme.pk]))
        assert response.status_code == 302
        assert response["Location"] == reverse("live:theme_preview", args=[theme.pk])

    def test_lists(self, org):
        Theme.objects.create(name="Вечер")
        themes.store_sound("a.ogg", OGG, "audio/ogg")
        assert org.get(reverse("admin:live_theme_changelist")).status_code == 200
        assert org.get(reverse("admin:live_themesound_changelist")).status_code == 200
