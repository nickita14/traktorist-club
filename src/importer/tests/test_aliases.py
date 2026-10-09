import pytest
from django.core.exceptions import ValidationError

from club.models import Player
from club.tests.factories import make_player
from importer.aliases import (
    AliasError,
    AmbiguousName,
    DbAliasMap,
    build_alias_map,
    load_aliases,
    normalize,
    upsert_aliases,
)
from importer.models import PlayerAlias
from importer.tests.sheet_builder import fixture_aliases


def test_normalize_ignores_case_spaces_and_yo():
    assert normalize("  Пётр   (СЕЯЛКА) ") == normalize("петр (сеялка)")


def load(data):
    """Upsert an aliases file into the database and return the lookup over the tables."""
    upsert_aliases(build_alias_map(data))
    return DbAliasMap()


@pytest.mark.django_db
class TestResolve:
    @pytest.fixture
    def alias_map(self):
        return load(fixture_aliases())

    @pytest.mark.parametrize("raw", ["Иван - Трактор", "Иван (Трактор)", "иван  -  трактор"])
    def test_aliases(self, alias_map, raw):
        assert str(alias_map.resolve(raw)) == "Иван Тестов (Трактор)"

    def test_name_matches_itself(self, alias_map):
        assert str(alias_map.resolve("Иван Тестов")) == "Иван Тестов (Трактор)"

    def test_yo_and_case(self, alias_map):
        assert alias_map.resolve("Пётр - Сеялка").name == "Пётр Примеров"
        assert alias_map.resolve("Олег (Плуг)").slug == "oleg"

    def test_unknown(self, alias_map):
        assert alias_map.resolve("Незнакомец") is None

    def test_alias_added_in_the_admin(self, alias_map):
        oleg = Player.objects.get(slug="oleg")
        PlayerAlias.objects.create(raw_name="Олежка", player=oleg)
        assert DbAliasMap().resolve("ОЛЕЖКА") == oleg

    def test_two_queries(self, django_assert_num_queries):
        load(fixture_aliases())
        with django_assert_num_queries(2):
            DbAliasMap().resolve("Иван Тестов")


class TestValidation:
    def test_alias_owned_by_two_players(self):
        data = {
            "players": [
                {"name": "Первый", "aliases": ["Общий"]},
                {"name": "Второй", "aliases": ["общий"]},
            ]
        }
        with pytest.raises(AliasError) as exc:
            build_alias_map(data)
        assert exc.value.errors == ["players[2]: alias 'общий' already belongs to Первый"]

    def test_every_problem_listed(self):
        data = {
            "players": [
                {"nickname": "Без имени"},
                "просто строка",
                {"name": "Тест", "aliases": "не список"},
                {"name": "Двойник"},
                {"name": "Двойник"},
            ]
        }
        with pytest.raises(AliasError) as exc:
            build_alias_map(data)
        assert exc.value.errors == [
            "players[1]: 'name' is required",
            "players[2]: must be a mapping",
            "players[3]: 'aliases' must be a list of names",
            "players[5]: duplicate player Двойник",
        ]

    @pytest.mark.parametrize("data", [None, [], {"players": "x"}])
    def test_top_level_shape(self, data):
        with pytest.raises(AliasError):
            build_alias_map(data)

    def test_broken_yaml_file(self, tmp_path):
        path = tmp_path / "aliases.yaml"
        path.write_text("players: [unclosed", encoding="utf-8")
        with pytest.raises(AliasError):
            load_aliases(path)


def dimas(alias_for=None):
    """Three players named "Дима"; ``alias_for`` gives one of them the explicit alias "Дима"."""
    players = [
        {"name": "Дима", "nickname": nickname}
        | ({"aliases": ["Дима"]} if nickname == alias_for else {})
        for nickname in ("Большой", "Малый", "Рыжий")
    ]
    return load({"players": players})


@pytest.mark.django_db
class TestSharedNames:
    """Several players may share a name; a bare name match is never guessed."""

    def test_bare_name_matching_several_players_is_an_error(self):
        alias_map = dimas()

        with pytest.raises(AmbiguousName) as exc:
            alias_map.resolve("  дима ")

        assert [str(p) for p in exc.value.candidates] == [
            "Дима (Большой)",
            "Дима (Малый)",
            "Дима (Рыжий)",
        ]
        assert "Дима (Большой), Дима (Малый), Дима (Рыжий)" in str(exc.value)

    def test_explicit_alias_wins_over_name_matches(self):
        alias_map = dimas(alias_for="Малый")
        assert str(alias_map.resolve("Дима")) == "Дима (Малый)"

    @pytest.mark.parametrize("alias_first", [True, False])
    def test_explicit_alias_of_another_name_wins(self, alias_first):
        chef = {"name": "Дмитрий", "nickname": "Шеф", "aliases": ["Дима"]}
        small = {"name": "Дима", "nickname": "Малый"}
        players = [chef, small] if alias_first else [small, chef]

        alias_map = load({"players": players})

        assert str(alias_map.resolve("Дима")) == "Дмитрий (Шеф)"

    def test_nickname_aliases_still_tell_them_apart(self):
        big = {"name": "Дима", "nickname": "Большой", "aliases": ["Дима - Большой"]}
        alias_map = load({"players": [big, {"name": "Дима", "nickname": "Малый"}]})
        assert str(alias_map.resolve("дима - большой")) == "Дима (Большой)"

    def test_aliases_differing_in_yo_for_two_players_are_ambiguous(self):
        # The admin refuses this pair (PlayerAlias.clean), the database alone cannot.
        PlayerAlias.objects.create(raw_name="Пётр", player=make_player("Первый"))
        PlayerAlias.objects.create(raw_name="Петр", player=make_player("Второй"))
        with pytest.raises(AmbiguousName):
            DbAliasMap().resolve("петр")


@pytest.mark.django_db
class TestPlayerAliasModel:
    def test_clean_refuses_the_same_name_written_differently(self):
        PlayerAlias.objects.create(raw_name="Пётр - Сеялка", player=make_player("Первый"))
        alias = PlayerAlias(raw_name="  петр  -  СЕЯЛКА ", player=make_player("Второй"))

        with pytest.raises(ValidationError) as exc:
            alias.full_clean()

        assert "уже записано за игроком Первый" in str(exc.value)

    def test_clean_collapses_spaces(self):
        alias = PlayerAlias(raw_name="  Иван   - Трактор ", player=make_player())
        alias.full_clean()
        assert alias.raw_name == "Иван - Трактор"


@pytest.mark.django_db
class TestUpsert:
    def test_creates_players_and_aliases(self):
        upsert = upsert_aliases(build_alias_map(fixture_aliases()))

        assert Player.objects.count() == 4
        assert len(upsert.players_created) == 4
        assert "Олег Пробный [oleg]" in upsert.players_created
        # "олег   (ПЛУГ)" is stored with collapsed spaces, as written otherwise
        assert set(
            PlayerAlias.objects.filter(player__slug="oleg").values_list("raw_name", flat=True)
        ) == {
            "Олег - Плуг",
            "олег (ПЛУГ)",
        }

    def test_second_run_changes_nothing(self):
        upsert_aliases(build_alias_map(fixture_aliases()))
        before = PlayerAlias.objects.count()

        upsert = upsert_aliases(build_alias_map(fixture_aliases()))

        assert (upsert.players_created, upsert.aliases_created, upsert.aliases_moved) == (
            [],
            [],
            [],
        )
        assert PlayerAlias.objects.count() == before

    def test_file_wins_and_keeps_other_aliases(self):
        other = make_player("Другой")
        PlayerAlias.objects.create(raw_name="Иван - Трактор", player=other)
        PlayerAlias.objects.create(raw_name="Только в базе", player=other)

        upsert = upsert_aliases(build_alias_map(fixture_aliases()))

        assert upsert.aliases_moved == ["Иван - Трактор: Другой -> Иван Тестов (Трактор)"]
        assert PlayerAlias.objects.get(raw_name="Иван - Трактор").player.name == "Иван Тестов"
        assert PlayerAlias.objects.get(raw_name="Только в базе").player == other

    def test_player_renamed_in_the_database_is_matched_by_slug(self):
        upsert_aliases(build_alias_map(fixture_aliases()))
        Player.objects.filter(slug="oleg").update(name="Олег Переименованный")

        upsert = upsert_aliases(build_alias_map(fixture_aliases()))

        assert upsert.players_created == []
        assert Player.objects.filter(slug="oleg").get().name == "Олег Переименованный"
        assert Player.objects.count() == 4

    def test_without_slug_a_renamed_player_is_created_again_and_reported(self):
        # The summary shows it before anything is applied: a dry run makes it obvious.
        upsert_aliases(build_alias_map(fixture_aliases()))
        Player.objects.filter(name="Мария Образцова").update(name="Мария Новая")

        upsert = upsert_aliases(build_alias_map(fixture_aliases()))

        assert upsert.players_created == ["Мария Образцова (Комбайн) [kombayn-2]"]
