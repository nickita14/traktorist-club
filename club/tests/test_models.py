import datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

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
        assert (season.entry_price, season.rebuy_price, season.addon_price) == (100, 50, 50)
        assert (season.rebuy_minutes, season.cash_step) == (120, 50)
        assert (season.payout_weights, season.prize_weights, season.payout_round) == (
            "3,2,1",
            [3, 2, 1],
            50,
        )

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
            ({"entry_price": 0}, "season_live_prices_positive"),
            ({"rebuy_price": 0}, "season_live_prices_positive"),
            ({"addon_price": 0}, "season_live_prices_positive"),
            ({"rebuy_minutes": 0}, "season_live_prices_positive"),
            ({"cash_step": 0}, "season_live_prices_positive"),
            ({"payout_round": 0}, "season_payout_round_positive"),
            ({"payout_weights": "3,,1"}, "season_payout_weights_format"),
            ({"payout_weights": "3,0,1"}, "season_payout_weights_format"),
            ({"payout_weights": "3, 2, 1"}, "season_payout_weights_format"),
        ],
    )
    def test_check_constraints(self, fields, constraint):
        values = {"year": 2025, "kind": SeasonKind.TOUR} | fields
        assert_integrity_error(constraint, lambda: Season.objects.create(**values))

    def test_weights_are_normalized(self):
        season = Season(year=2026, kind=SeasonKind.TOUR, payout_weights=" 5 , 3,2 ")
        season.full_clean()
        assert season.payout_weights == "5,3,2"

    @pytest.mark.parametrize(
        ("weights", "paid_places", "message"),
        [
            ("3,2", 3, "Нужно 3 доли: по одной на призовое место, а указано 2."),
            ("3,2,1", 1, "Нужно 1 доля: по одной на призовое место, а указано 3."),
            ("3,x,1", 3, "Доли: целые числа больше нуля"),
            ("3,0,1", 3, "Доли: целые числа больше нуля"),
        ],
    )
    def test_weights_must_fit_paid_places(self, weights, paid_places, message):
        season = Season(
            year=2026, kind=SeasonKind.TOUR, payout_weights=weights, paid_places=paid_places
        )
        with pytest.raises(ValidationError) as exc:
            season.full_clean()
        assert message in exc.value.message_dict["payout_weights"][0]

    def test_migration_gives_other_seasons_matching_weights(self):
        from importlib import import_module

        from django.apps import apps

        migration = import_module("club.migrations.0004_payout_split")
        three, four = make_season(2024, paid_places=3), make_season(2025, paid_places=4)
        Season.objects.update(payout_weights="3,2,1")

        migration.weights_for_paid_places(apps, None)

        three.refresh_from_db()
        four.refresh_from_db()
        assert (three.payout_weights, four.payout_weights) == ("3,2,1", "4,3,2,1")

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

    def test_not_live_by_default(self):
        game = make_game(make_season())
        assert (game.live_stage, game.started_at, game.is_live) == ("", None, False)
        assert list(Game.objects.finished()) == [game]
        assert list(Game.objects.live()) == []

    def test_live_game_is_not_finished(self):
        game = make_game(make_season(), live_stage=Game.Stage.REBUYS, started_at=timezone.now())
        assert game.is_live
        assert list(Game.objects.finished()) == []
        assert list(Game.objects.live()) == [game]

    def test_unknown_stage_rejected_by_db(self):
        season = make_season()
        assert_integrity_error(
            "game_live_stage_valid",
            lambda: make_game(season, live_stage="poker", started_at=timezone.now()),
        )

    def test_live_game_needs_start_time(self):
        season = make_season()
        assert_integrity_error(
            "game_live_has_start", lambda: make_game(season, live_stage=Game.Stage.REBUYS)
        )

    @pytest.mark.parametrize(
        ("kind", "stage"),
        [(SeasonKind.TOUR, Game.Stage.CASH), (SeasonKind.CASH, Game.Stage.FINAL)],
    )
    def test_stage_must_match_season_kind(self, kind, stage):
        game = Game(
            season=make_season(2026, kind),
            date=datetime.date(2026, 3, 14),
            live_stage=stage,
            started_at=timezone.now(),
        )
        with pytest.raises(ValidationError) as exc:
            game.full_clean()
        assert "live_stage" in exc.value.message_dict

    def test_clean_asks_for_start_time(self):
        game = Game(season=make_season(2026), date=datetime.date(2026, 3, 14), live_stage="rebuys")
        with pytest.raises(ValidationError) as exc:
            game.full_clean()
        assert "started_at" in exc.value.message_dict


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
            ({"out_order": 0}, "result_out_order_positive"),
        ],
    )
    def test_check_constraints(self, game, fields, constraint):
        values = {"game": game, "player": make_player(), "buyin": 50} | fields
        assert_integrity_error(constraint, lambda: Result.objects.create(**values))

    def test_out_order_unique_per_game(self, game):
        make_result(game, make_player(), out_order=1)
        make_result(game, make_player())  # several players still in: nulls do not clash
        make_result(game, make_player())
        make_result(make_game(make_season(kind=SeasonKind.CASH)), make_player(), out_order=1)
        assert_integrity_error(
            "result_game_out_order_unique",
            lambda: make_result(game, make_player(), out_order=1),
        )

    def test_live_fields_empty_by_default(self, game):
        result = make_result(game, make_player())
        assert (result.rebuys, result.addon, result.out_order) == (None, None, None)


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

    @pytest.mark.parametrize("field", ["rebuys", "addon"])
    def test_tour_live_fields_in_cash_rejected(self, field):
        value = {"rebuys": 2, "addon": True}[field]
        with pytest.raises(ValidationError) as exc:
            self.build(SeasonKind.CASH, **{field: value}).full_clean()
        assert field in exc.value.message_dict

    def test_valid_tour_result(self):
        self.build(SeasonKind.TOUR, place=2, payout=100).full_clean()

    def test_valid_live_tour_result(self):
        self.build(SeasonKind.TOUR, rebuys=2, addon=True, out_order=3).full_clean()

    def test_valid_live_cash_result(self):
        self.build(SeasonKind.CASH, chips_out=7500, payout=75, out_order=1).full_clean()

    def test_valid_cash_result(self):
        self.build(SeasonKind.CASH, chips_out=7500, payout=75).full_clean()

    def test_without_game_skips_kind_rule(self):
        Result(player=make_player(), buyin=50, place=1).clean()
