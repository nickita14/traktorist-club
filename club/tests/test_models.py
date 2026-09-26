import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from club.models import Game, Player, Result, Season, SeasonKind, transliterate
from club.tests.factories import make_game, make_player, make_result, make_season

pytestmark = pytest.mark.django_db


def assert_integrity_error(constraint_name, create):
    with pytest.raises(IntegrityError, match=constraint_name), transaction.atomic():
        create()


class TestSeason:
    def test_defaults(self):
        season = make_season()
        season.refresh_from_db()
        assert season.chips_per_lei == 100
        assert season.paid_places == 3

    def test_str(self):
        assert str(make_season(2025, SeasonKind.CASH)) == "Кэш 2025"

    def test_year_kind_unique(self):
        make_season(2025, SeasonKind.TOUR)
        make_season(2025, SeasonKind.CASH)
        assert_integrity_error("season_year_kind_unique", lambda: make_season(2025))

    @pytest.mark.parametrize(
        ("fields", "constraint"),
        [
            ({"kind": "pokr"}, "season_kind_valid"),
            ({"year": 1999}, "season_year_range"),
            ({"year": 2101}, "season_year_range"),
            ({"paid_places": 0}, "season_paid_places_positive"),
            ({"chips_per_lei": 0}, "season_chips_per_lei_positive"),
        ],
    )
    def test_check_constraints(self, fields, constraint):
        values = {"year": 2025, "kind": SeasonKind.TOUR} | fields
        assert_integrity_error(constraint, lambda: Season.objects.create(**values))

    def test_kind_change_rejected_when_results_have_places(self):
        season = make_season(kind=SeasonKind.TOUR)
        make_result(make_game(season), make_player(), place=1)
        season.kind = SeasonKind.CASH
        with pytest.raises(ValidationError) as exc:
            season.full_clean()
        assert "kind" in exc.value.message_dict

    def test_kind_change_rejected_when_results_have_chips_out(self):
        season = make_season(kind=SeasonKind.CASH)
        make_result(make_game(season), make_player(), chips_out=5000)
        season.kind = SeasonKind.TOUR
        with pytest.raises(ValidationError):
            season.full_clean()

    def test_kind_change_allowed_without_kind_specific_data(self):
        season = make_season(kind=SeasonKind.TOUR)
        make_result(make_game(season), make_player())
        season.kind = SeasonKind.CASH
        season.full_clean()


class TestPlayerSlug:
    def test_transliterate(self):
        assert transliterate("Щука Ёжиков-Эхо") == "shchuka ezhikov-ekho"

    def test_from_nickname(self):
        assert make_player("Тестов Иван", nickname="Трактор").slug == "traktor"

    def test_from_name_when_no_nickname(self):
        assert make_player("Жуков Юрий").slug == "zhukov-yuriy"

    def test_latin_diacritics(self):
        assert make_player("Ștefan Țurcanu").slug == "stefan-turcanu"

    def test_fallback_when_nothing_sluggable(self):
        assert make_player("!!!").slug == "player"

    def test_collision_gets_suffix(self):
        make_player("Первый", nickname="Ваня")
        assert make_player("Второй", nickname="Ваня").slug == "vanya-2"
        assert make_player("Третий", nickname="Ваня").slug == "vanya-3"

    def test_full_clean_fills_slug_before_constraint_checks(self):
        player = Player(name="Тестов Иван", nickname="Ваня")
        player.full_clean()
        assert player.slug == "vanya"

    def test_explicit_slug_kept(self):
        assert make_player("Тестов Иван", slug="custom-slug").slug == "custom-slug"

    def test_slug_stable_after_rename(self):
        player = make_player("Тестов Иван", nickname="Ваня")
        player.name, player.nickname = "Другое Имя", "Другой"
        player.save()
        player.refresh_from_db()
        assert player.slug == "vanya"

    def test_str(self):
        assert str(make_player("Тестов Иван", nickname="Ваня")) == "Тестов Иван (Ваня)"
        assert str(make_player("Петров Пётр")) == "Петров Пётр"


class TestPlayerConstraints:
    def test_name_nickname_unique(self):
        make_player("Тестов Иван", nickname="Ваня")
        assert_integrity_error(
            "player_name_nickname_unique",
            lambda: Player.objects.create(name="Тестов Иван", nickname="Ваня", slug="other"),
        )

    def test_slug_unique(self):
        make_player("Первый", slug="same")
        assert_integrity_error(
            "player_slug_unique", lambda: Player.objects.create(name="Второй", slug="same")
        )

    def test_name_not_empty(self):
        assert_integrity_error(
            "player_name_not_empty", lambda: Player.objects.create(name="", slug="x")
        )

    def test_slug_not_empty_at_db_level(self):
        player = make_player("Тестов Иван")
        assert_integrity_error(
            "player_slug_not_empty", lambda: Player.objects.filter(pk=player.pk).update(slug="")
        )


class TestGame:
    def test_season_date_unique(self):
        season = make_season()
        make_game(season, day=5)
        assert_integrity_error("game_season_date_unique", lambda: make_game(season, day=5))

    def test_tour_and_cash_can_share_a_date(self):
        make_game(make_season(kind=SeasonKind.TOUR), day=5)
        make_game(make_season(kind=SeasonKind.CASH), day=5)
        assert Game.objects.count() == 2

    def test_date_must_be_in_season_year(self):
        game = Game(season=make_season(2026), date=datetime.date(2025, 3, 14))
        with pytest.raises(ValidationError) as exc:
            game.full_clean()
        assert "date" in exc.value.message_dict

    def test_location_optional(self):
        game = Game(season=make_season(2026), date=datetime.date(2026, 3, 14))
        game.full_clean()
        game.save()
        assert game.location == ""


class TestResultConstraints:
    @pytest.fixture
    def game(self):
        return make_game(make_season())

    def test_game_player_unique(self, game):
        player = make_player()
        make_result(game, player)
        assert_integrity_error("result_game_player_unique", lambda: make_result(game, player))

    @pytest.mark.parametrize(
        ("fields", "constraint"),
        [
            ({"buyin": 0}, "result_buyin_positive"),
            ({"place": 0}, "result_place_positive"),
            ({"place": 1, "chips_out": 5000}, "result_place_xor_chips_out"),
        ],
    )
    def test_check_constraints(self, game, fields, constraint):
        values = {"game": game, "player": make_player(), "buyin": 50} | fields
        assert_integrity_error(constraint, lambda: Result.objects.create(**values))


class TestResultClean:
    """The tour/cash rule needs game.season.kind, so clean() enforces it, not the DB."""

    def build(self, kind, **fields):
        game = make_game(make_season(kind=kind))
        return Result(game=game, player=make_player(), buyin=50, **fields)

    def test_place_in_cash_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self.build(SeasonKind.CASH, place=1).full_clean()
        assert "place" in exc.value.message_dict

    def test_chips_out_in_tour_rejected(self):
        with pytest.raises(ValidationError) as exc:
            self.build(SeasonKind.TOUR, chips_out=5000).full_clean()
        assert "chips_out" in exc.value.message_dict

    def test_valid_tour_result(self):
        self.build(SeasonKind.TOUR, place=2, payout=100).full_clean()

    def test_valid_cash_result(self):
        self.build(SeasonKind.CASH, chips_out=7500, payout=75).full_clean()

    def test_without_game_skips_kind_rule(self):
        Result(player=make_player(), buyin=50, place=1).clean()
