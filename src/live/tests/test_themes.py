"""Theme nights (live.themes): caption rules, sounds, the theme payload and the table events the
display announces, derived from the action log."""

import hashlib

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from club import stats
from club.models import SeasonKind
from live import actions, themes
from live.models import (
    ACCENT_MIN_CONTRAST,
    EventKind,
    LiveAction,
    OverlayStyle,
    Theme,
    ThemeCover,
    ThemeEvent,
    ThemeSound,
)
from live.tests.conftest import key, live_game, seat
from live.tests.test_timer import FrozenClock

pytestmark = pytest.mark.django_db

MP3 = b"ID3\x04\x00" + bytes(64)
MP3_FRAME = b"\xff\xfb\x90\x00" + bytes(64)
OGG = b"OggS\x00\x02" + bytes(64)
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(64)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(64)
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(64)
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + bytes(64)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.fixture
def frozen(monkeypatch):
    frozen = FrozenClock()
    monkeypatch.setattr(timezone, "now", lambda: frozen.now)
    return frozen


@pytest.fixture
def five(frozen):
    """A live tournament (3 paid places, entry 100) with five players: places 5 to 1."""
    game = live_game(SeasonKind.TOUR, entry_price=100)
    return game, seat(game, "Альфа", "Браво", "Чарли", "Дельта", "Эхо")


def bust(game, result):
    return actions.eliminate(game.pk, key(), None, result.pk).action


def events(game, cursor=0):
    game.refresh_from_db()
    return themes.table_events(game, cursor, stats.live_totals(game), timezone.now())


def upload(name, data):
    return SimpleUploadedFile(name, data)


class TestTemplates:
    @pytest.mark.parametrize(
        ("kind", "text"),
        [
            (EventKind.BUST, "{name} · {place} место"),
            (EventKind.BUST_ITM, "{name} · {place} место"),
            (EventKind.REBUY, "{name}: ребай № {count}, {amount}"),
            (EventKind.WINNER, "{name} забирает банк"),
            (EventKind.BREAK, "Перерыв"),
            (EventKind.BUST, "{{скобки}} и {name}"),
            (EventKind.BUST, ""),
        ],
    )
    def test_valid(self, kind, text):
        assert themes.template_error(kind, text) is None

    @pytest.mark.parametrize(
        ("kind", "text", "message"),
        [
            (EventKind.BUST, "{who}", "подстановка {who}: здесь можно {name}, {place}."),
            (EventKind.BUST, "{amount}", "Неизвестная подстановка {amount}"),
            # No prize amounts: payouts are known only on the results screen.
            (EventKind.BUST_ITM, "{amount}", "здесь можно {name}, {place}."),
            (EventKind.WINNER, "{amount}", "здесь можно {name}."),
            (EventKind.BUBBLE_BURST, "{amount}", "Неизвестная подстановка {amount}"),
            (EventKind.WINNER, "{place}", "Неизвестная подстановка {place}"),
            (EventKind.BREAK, "{name}", "здесь подстановок нет."),
            (EventKind.BUST, "{}", "Неизвестная подстановка {}"),
            (EventKind.BUST, "{0}", "Неизвестная подстановка {0}"),
            (EventKind.BUST, "{name.__class__}", "{name.__class__}"),
            (EventKind.BUST, "{name[0]}", "{name[0]}"),
            (EventKind.BUST, "{name!r}", "{name!r}"),
            (EventKind.BUST, "{place:>5}", "{place:>5}"),
            (EventKind.BUST, "{name", "Непарная фигурная скобка"),
            (EventKind.BUST, "name}", "Непарная фигурная скобка"),
        ],
    )
    def test_refused(self, kind, text, message):
        assert message in themes.template_error(kind, text)

    def test_event_clean(self, db):
        theme = Theme.objects.create(name="Вечер")
        event = ThemeEvent(theme=theme, kind=EventKind.BUST, title="{who}", caption="{amount}")
        with pytest.raises(ValidationError) as error:
            event.full_clean()
        assert set(error.value.message_dict) == {"title", "caption"}

    def test_seconds_zero_only_for_the_winner(self, db):
        theme = Theme.objects.create(name="Вечер")
        with pytest.raises(ValidationError) as error:
            ThemeEvent(theme=theme, kind=EventKind.BUST, seconds=0).full_clean()
        assert "seconds" in error.value.message_dict
        ThemeEvent(theme=theme, kind=EventKind.WINNER, seconds=0).full_clean()

    def test_break_is_fullscreen(self, db):
        theme = Theme.objects.create(name="Вечер")
        with pytest.raises(ValidationError) as error:
            ThemeEvent(theme=theme, kind=EventKind.BREAK, style=OverlayStyle.STAMP).full_clean()
        assert "style" in error.value.message_dict
        ThemeEvent(theme=theme, kind=EventKind.BREAK, style=OverlayStyle.FULLSCREEN).full_clean()


class TestActiveTheme:
    def test_activating_one_switches_the_other_off(self, db):
        first = Theme.objects.create(name="Первая", is_active=True)
        second = Theme(name="Вторая", is_active=True)
        second.full_clean()  # the one-active rule is no form error
        second.save()

        first.refresh_from_db()
        assert not first.is_active
        assert list(Theme.objects.filter(is_active=True)) == [second]

    def test_tips(self, db):
        theme = Theme(name="Вечер", tips="  первый  \n\n второй\n")
        assert theme.tip_list == ["первый", "второй"]


class TestSounds:
    @pytest.mark.parametrize(
        ("name", "data", "kind"),
        [
            ("a.mp3", MP3, "audio/mpeg"),
            ("b.MP3", MP3_FRAME, "audio/mpeg"),
            ("c.ogg", OGG, "audio/ogg"),
            ("d.wav", WAV, "audio/wav"),
        ],
    )
    def test_accepted(self, name, data, kind):
        assert themes.read_sound(upload(name, data)) == (data, kind)

    @pytest.mark.parametrize(
        ("name", "data", "message"),
        [
            ("a.flac", MP3, "только mp3, ogg или wav"),
            ("noext", MP3, "только mp3, ogg или wav"),
            ("a.mp3", OGG, "Это не mp3"),
            ("a.ogg", b"<html>" + bytes(32), "Это не ogg"),
            ("a.wav", b"RIFF\x00\x00\x00\x00AVI " + bytes(32), "Это не wav"),
            ("a.mp3", b"", "Файл пустой."),
        ],
    )
    def test_refused(self, name, data, message):
        with pytest.raises(ValidationError, match=message):
            themes.read_sound(upload(name, data))

    def test_size_limit(self, settings):
        settings.THEME_SOUND_MAX_BYTES = 32
        with pytest.raises(ValidationError, match="больше"):
            themes.read_sound(upload("a.mp3", MP3))

    def test_stored_once(self, db):
        first = themes.store_sound("a.mp3", MP3, "audio/mpeg")
        again = themes.store_sound("другое.mp3", MP3, "audio/mpeg")

        assert first == again
        assert ThemeSound.objects.count() == 1
        assert (first.size, len(first.sha256)) == (len(MP3), 64)


class TestCovers:
    @pytest.mark.parametrize(
        ("name", "data", "kind"),
        [
            ("a.jpg", JPEG, "image/jpeg"),
            ("b.JPEG", JPEG, "image/jpeg"),
            ("c.png", PNG, "image/png"),
            ("d.webp", WEBP, "image/webp"),
        ],
    )
    def test_accepted(self, name, data, kind):
        assert themes.read_cover(upload(name, data)) == (data, kind)

    @pytest.mark.parametrize(
        ("name", "data", "message"),
        [
            ("a.svg", SVG, "только jpg, png или webp"),
            ("a.gif", b"GIF89a" + bytes(32), "только jpg, png или webp"),
            ("noext", JPEG, "только jpg, png или webp"),
            # A script dressed as a picture: the bytes decide, not the name.
            ("a.png", SVG, "Это не png"),
            ("a.jpg", b"<html>" + bytes(32), "Это не jpg"),
            ("a.jpg", PNG, "Это не jpg"),
            ("a.webp", WAV, "Это не webp"),
            ("a.png", b"", "Файл пустой."),
        ],
    )
    def test_refused(self, name, data, message):
        with pytest.raises(ValidationError, match=message):
            themes.read_cover(upload(name, data))

    def test_size_limit(self, settings):
        assert settings.THEME_COVER_MAX_BYTES == 3 * 1024 * 1024
        data = JPEG + bytes(settings.THEME_COVER_MAX_BYTES)
        with pytest.raises(ValidationError, match="Обложка больше 3 МБ."):
            themes.read_cover(upload("a.jpg", data))
        just = JPEG + bytes(settings.THEME_COVER_MAX_BYTES - len(JPEG))
        assert themes.read_cover(upload("a.jpg", just))[1] == "image/jpeg"

    def test_store_replaces(self, db):
        theme = Theme.objects.create(name="Вечер")
        first = themes.store_cover(theme, JPEG, "image/jpeg")
        second = themes.store_cover(theme, PNG, "image/png")

        assert first.pk == second.pk
        cover = ThemeCover.objects.get()
        assert (cover.content_type, cover.size, bytes(cover.data)) == ("image/png", len(PNG), PNG)
        assert cover.sha256 == hashlib.sha256(PNG).hexdigest()


class TestAccent:
    def test_contrast(self):
        assert themes.contrast("#000000", "#ffffff") == pytest.approx(21)
        assert themes.contrast("#777777", "#777777") == pytest.approx(1)

    @pytest.mark.parametrize("color", ["#F2B630", "#f2ede3", "#c8281e", "#ffffff"])
    def test_light_enough_on_ink(self, color):
        theme = Theme(name="Вечер", accent=color)
        theme.full_clean()
        assert theme.accent == color.lower()
        assert themes.contrast_on_ink(theme.accent) >= ACCENT_MIN_CONTRAST

    @pytest.mark.parametrize("color", ["#b3261e", "#1d1b18", "#333333"])
    def test_too_dark_on_ink(self, color):
        with pytest.raises(ValidationError, match="Слишком тёмный") as error:
            Theme(name="Вечер", accent=color).full_clean()
        assert "accent" in error.value.message_dict

    @pytest.mark.parametrize("color", ["red", "#fff", "#gggggg", "f2b630", "#f2b6300"])
    def test_hex_only(self, color):
        with pytest.raises(ValidationError) as error:
            Theme(name="Вечер", accent=color).full_clean()
        assert "accent" in error.value.message_dict

    def test_empty_is_the_plain_look(self):
        Theme(name="Вечер", accent="").full_clean()


class TestPayload:
    def test_defaults_without_a_theme(self, db, django_assert_num_queries):
        with django_assert_num_queries(1):
            payload = themes.theme_payload(themes.active_theme())

        assert payload["name"] == "" and payload["tips"] == []
        assert payload["accent"] == "" and payload["cover"] is None
        assert set(payload["events"]) == set(EventKind.values)
        assert payload["events"]["bust"] == {
            "enabled": True,
            "title": "Выбыл",
            "caption": "{name} · {place} место",
            "style": "stamp",
            "seconds": 5,
            "sound": None,
        }
        assert payload["events"]["winner"]["seconds"] == 0
        assert not payload["events"]["rebuy"]["enabled"]
        assert not payload["events"]["break"]["enabled"]

    def test_theme_rows_override_their_kind(self, db, django_assert_num_queries):
        theme = Theme.objects.create(name="Вечер", is_active=True, tips="раз\nдва")
        sound = themes.store_sound("a.ogg", OGG, "audio/ogg")
        ThemeEvent.objects.create(
            theme=theme,
            kind=EventKind.REBUY,
            title="Снова в строю",
            caption="{name}",
            style=OverlayStyle.LOWER_THIRD,
            seconds=3,
            sound=sound,
        )
        with django_assert_num_queries(2):
            payload = themes.theme_payload(themes.active_theme())

        assert payload["name"] == "Вечер" and payload["tips"] == ["раз", "два"]
        assert payload["events"]["rebuy"] == {
            "enabled": True,
            "title": "Снова в строю",
            "caption": "{name}",
            "style": "lower-third",
            "seconds": 3,
            "sound": f"/tablo/sound/{sound.sha256}/",
        }
        assert payload["events"]["bust"] == themes.DEFAULTS["bust"]

    def test_accent_and_cover(self, db, django_assert_num_queries):
        theme = Theme.objects.create(name="Вечер", is_active=True, accent="#f2ede3")
        cover = themes.store_cover(theme, JPEG, "image/jpeg")
        with django_assert_num_queries(2):
            payload = themes.theme_payload(themes.active_theme())
        assert payload["accent"] == "#f2ede3"
        assert payload["cover"] == f"/tablo/cover/{cover.sha256}/"

        cover.delete()
        assert themes.theme_payload(themes.active_theme())["cover"] is None

    def test_cover_bytes_are_never_loaded(self, db):
        theme = Theme.objects.create(name="Вечер", is_active=True)
        themes.store_cover(theme, JPEG, "image/jpeg")
        with CaptureQueriesContext(connection) as queries:
            themes.theme_payload(themes.active_theme())
        sql = " ".join(query["sql"] for query in queries.captured_queries)
        assert '"live_themecover"."sha256"' in sql
        assert '"live_themecover"."data"' not in sql

    def test_inactive_theme_is_ignored(self, db):
        Theme.objects.create(name="Вечер")
        assert themes.active_theme() is None


class TestKinds:
    @pytest.mark.parametrize(
        ("place", "kind"),
        [(9, "bust"), (5, "bust"), (4, "bubble_burst"), (3, "bust_itm"), (2, "bust_itm")],
    )
    def test_bust_kind(self, place, kind):
        assert themes.bust_kind(place, 3) == kind


class TestEvents:
    def test_bust_stores_its_place(self, five):
        game, results = five
        assert bust(game, results[0]).place == 5
        assert bust(game, results[1]).place == 4

    def test_a_whole_tournament(self, five):
        game, (a, b, c, d, e) = five
        for result in (a, b, c, d):
            bust(game, result)

        found, cursor = events(game)

        assert [(event.kind, event.name, event.place) for event in found] == [
            ("bust", "Альфа", 5),
            ("bubble_burst", "Браво", 4),
            ("bust_itm", "Чарли", 3),
            ("bust_itm", "Дельта", 2),
            ("winner", "Эхо", 1),
        ]
        # No prize amounts: payouts are known only on the results screen.
        assert [event.amount for event in found] == [None] * 5
        assert [event.note for event in found] == [
            "в игре 4 из 5",
            "в игре 3 из 5",
            "в игре 2 из 5",
            "в игре 1 из 5",
            "1 место из 5",
        ]
        assert cursor == LiveAction.objects.filter(game=game).latest("pk").pk
        assert found[-1].id == f"{found[-2].id}-winner"

    def test_nothing_twice(self, five):
        game, results = five
        bust(game, results[0])
        _, cursor = events(game)

        assert events(game, cursor) == ([], cursor)
        bust(game, results[1])
        found, _ = events(game, cursor)
        assert [event.name for event in found] == ["Браво"]

    def test_cursor_skips_other_actions(self, five):
        game, results = five
        actions.set_paid(game.pk, key(), None, results[0].pk, True)
        assert events(game) == ([], 0)

    def test_undone_bust_is_not_sent(self, five):
        game, results = five
        action = bust(game, results[0])
        actions.undo(action.pk, None)

        found, cursor = events(game)
        assert found == []
        assert cursor == action.pk

    def test_restored_bust_is_not_sent(self, five):
        game, results = five
        bust(game, results[0])
        actions.restore(game.pk, key(), None, results[0].pk)
        assert events(game)[0] == []

    def test_undone_last_bust_has_no_winner(self, five):
        game, results = five
        for result in results[:3]:
            bust(game, result)
        _, cursor = events(game)
        last = bust(game, results[3])
        actions.undo(last.pk, None)

        assert events(game, cursor)[0] == []

    def test_rebuy(self, five):
        game, results = five
        actions.rebuy(game.pk, key(), None, results[0].pk)
        actions.rebuy(game.pk, key(), None, results[0].pk)

        found, _ = events(game)
        assert [(e.kind, e.name, e.amount, e.count, e.note) for e in found] == [
            ("rebuy", "Альфа", 50, 1, "ребай № 1"),
            ("rebuy", "Альфа", 50, 2, "ребай № 2"),
        ]

    def test_hold(self, five, frozen, settings):
        settings.THEME_EVENT_HOLD_SECONDS = 10
        game, results = five
        bust(game, results[0])
        assert events(game) == ([], 0)

        frozen.advance(seconds=10)
        assert [event.name for event in events(game)[0]] == ["Альфа"]

    def test_old_events_are_dropped_but_not_the_winner(self, five, frozen):
        game, results = five
        for result in results[:4]:
            bust(game, result)
        frozen.advance(minutes=5)

        found, _ = events(game)
        assert [event.kind for event in found] == ["winner"]

    def test_query_count(self, five, django_assert_num_queries):
        game, results = five
        for result in results[:3]:
            bust(game, result)
        game.refresh_from_db()
        totals = stats.live_totals(game)
        with django_assert_num_queries(1):
            themes.table_events(game, 0, totals, timezone.now())

        bust(game, results[3])
        with django_assert_num_queries(2):
            found, _ = themes.table_events(game, 0, totals, timezone.now())
        assert found[-1].kind == "winner"

    def test_latest_cursor(self, five):
        game, _ = five
        assert themes.latest_cursor(game) == LiveAction.objects.filter(game=game).latest("pk").pk

    @pytest.mark.parametrize(
        ("value", "cursor"), [("12", 12), ("0", 0), ("", None), ("x", None), ("-3", None)]
    )
    def test_parse_cursor(self, value, cursor):
        assert themes.parse_cursor(value) == cursor


def test_samples_cover_every_announced_kind():
    samples = themes.sample_events()
    assert set(samples) == set(EventKind.values) - {EventKind.BREAK}
    assert all(sample["name"] == themes.SAMPLE_NAME for sample in samples.values())
    assert [kind for kind, sample in samples.items() if sample["amount"] is not None] == ["rebuy"]
