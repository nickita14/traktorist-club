import pytest

from importer.aliases import (
    AliasError,
    AmbiguousName,
    CanonicalPlayer,
    build_alias_map,
    load_aliases,
    normalize,
)
from importer.tests.sheet_builder import fixture_aliases

IVAN = CanonicalPlayer("Иван Тестов", "Трактор")


def test_normalize_ignores_case_spaces_and_yo():
    assert normalize("  Пётр   (СЕЯЛКА) ") == normalize("петр (сеялка)")


class TestResolve:
    @pytest.fixture
    def alias_map(self):
        return build_alias_map(fixture_aliases())

    @pytest.mark.parametrize("raw", ["Иван - Трактор", "Иван (Трактор)", "иван  -  трактор"])
    def test_aliases(self, alias_map, raw):
        assert alias_map.resolve(raw) == IVAN

    def test_name_matches_itself(self, alias_map):
        assert alias_map.resolve("Иван Тестов") == IVAN

    def test_yo_and_case(self, alias_map):
        assert alias_map.resolve("Пётр - Сеялка").name == "Пётр Примеров"
        assert alias_map.resolve("Олег (Плуг)") == CanonicalPlayer("Олег Пробный", "", "oleg")

    def test_unknown(self, alias_map):
        assert alias_map.resolve("Незнакомец") is None


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
    return build_alias_map({"players": players})


class TestSharedNames:
    """Several players may share a name; a bare name match is never guessed."""

    def test_file_with_shared_names_is_valid(self):
        assert len(dimas().players) == 3

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
        assert alias_map.resolve("Дима") == CanonicalPlayer("Дима", "Малый")

    @pytest.mark.parametrize("alias_first", [True, False])
    def test_explicit_alias_of_another_name_wins(self, alias_first):
        chef = {"name": "Дмитрий", "nickname": "Шеф", "aliases": ["Дима"]}
        small = {"name": "Дима", "nickname": "Малый"}
        players = [chef, small] if alias_first else [small, chef]

        alias_map = build_alias_map({"players": players})

        assert alias_map.resolve("Дима") == CanonicalPlayer("Дмитрий", "Шеф")

    def test_nickname_aliases_still_tell_them_apart(self):
        big = {"name": "Дима", "nickname": "Большой", "aliases": ["Дима - Большой"]}
        alias_map = build_alias_map({"players": [big, {"name": "Дима", "nickname": "Малый"}]})
        assert alias_map.resolve("дима - большой") == CanonicalPlayer("Дима", "Большой")
