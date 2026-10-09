import datetime

import pytest
from django.core.management import CommandError

from club.models import Game, Player, Result, Season, SeasonKind
from club.stats import annotate_game_totals
from club.tests.factories import make_game
from importer import sync
from importer.tests.sheet_builder import (
    build_workbook,
    fixture_aliases,
    fixture_sheets,
    sheet_named,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def run_import(run_command, workbook_path, aliases_path):
    def run(path=None, **options):
        options.setdefault("aliases", str(aliases_path))
        return run_command("import_sheet", path or workbook_path, **options)

    return run


def places(date, kind=SeasonKind.TOUR):
    results = Result.objects.filter(game__date=date, game__season__kind=kind)
    return {r.player.name: r.place for r in results.select_related("player")}


def db_counts():
    return tuple(m.objects.count() for m in (Player, Season, Game, Result))


class TestFirstImport:
    def test_creates_everything(self, run_import):
        run_import()

        assert db_counts() == (4, 3, 8, 22)
        assert sorted(Season.objects.values_list("year", "kind")) == [
            (2025, "tour"),
            (2026, "cash"),
            (2026, "tour"),
        ]

    def test_fixed_dates_are_stored(self, run_import):
        run_import()
        tour_2026 = Game.objects.filter(season__year=2026, season__kind=SeasonKind.TOUR)
        assert sorted(tour_2026.values_list("date", flat=True)) == [
            datetime.date(2026, 1, 18),
            datetime.date(2026, 2, 1),
            datetime.date(2026, 3, 14),
        ]

    def test_players_and_slugs(self, run_import):
        run_import()
        assert dict(Player.objects.values_list("name", "slug")) == {
            "Иван Тестов": "traktor",  # generated through full_clean
            "Пётр Примеров": "seyalka",
            "Мария Образцова": "kombayn",
            "Олег Пробный": "oleg",  # from the YAML
        }

    def test_tie_places(self, run_import):
        run_import()
        assert places(datetime.date(2026, 3, 14)) == {
            "Иван Тестов": 1,
            "Пётр Примеров": 1,
            "Мария Образцова": 3,
            "Олег Пробный": None,
        }

    def test_cash_games(self, run_import):
        run_import()
        cash = annotate_game_totals(Game.objects.filter(season__kind=SeasonKind.CASH))
        assert sorted(g.leftover for g in cash) == [5, 60]
        assert not Result.objects.filter(game__season__kind=SeasonKind.CASH, place__isnull=False)

    def test_live_only_fields_stay_unknown(self, run_import):
        # Rebuys, add-ons and the elimination order are not in the sheet: unknown, never 0.
        run_import()
        assert not Result.objects.filter(rebuys__isnull=False).exists()
        assert not Result.objects.filter(addon__isnull=False).exists()
        assert not Result.objects.filter(out_order__isnull=False).exists()

    def test_only_paid_places_are_known(self, run_import):
        run_import()
        tour = Result.objects.filter(game__season__kind=SeasonKind.TOUR)
        assert not tour.filter(place__gt=3).exists()
        assert not tour.filter(payout=0, place__isnull=False).exists()
        assert not tour.filter(payout__gt=0, place__isnull=True).exists()

    def test_output(self, run_import):
        out = run_import()
        assert "ТУР2026_new!K1: 01.02.2025 -> 01.02.2026 (wrong year)" in out
        assert "ТУР2026_new!O (21.03.2026)" in out
        assert "ТУР2026_old version: skipped" in out
        assert "games created 3, results created 10, seasons created 1" in out


class TestRerun:
    def test_second_run_changes_nothing(self, run_import):
        run_import()
        before = db_counts()

        out = run_import()

        assert db_counts() == before
        changes = out[out.index("Changes in") : out.index("Verification")]
        assert "created" not in changes and "updated" not in changes and "deleted" not in changes
        assert "games unchanged 3, results unchanged 10" in out

    def test_sheet_wins_after_edit(self, run_import, tmp_path):
        run_import()
        sheets = fixture_sheets()
        tour = sheet_named(sheets, "ТУР2026_new")
        tour.rows[1].results[0] = (100, 250)  # Пётр now wins 18.01
        del tour.rows[3].results[0]  # Мария removed from 18.01
        edited = build_workbook(tmp_path / "edited.xlsx", sheets)

        out = run_import(edited)

        assert places(datetime.date(2026, 1, 18)) == {"Пётр Примеров": 1, "Иван Тестов": 2}
        assert "18.01.2026 Пётр Примеров (Сеялка): payout 100 -> 250, place 2 -> 1" in out
        assert "18.01.2026 Иван Тестов (Трактор): place 1 -> 2" in out
        assert "18.01.2026 Мария Образцова (Комбайн): deleted (not in the sheet)" in out

    def test_location_and_chips_out_survive(self, run_import):
        run_import()
        Game.objects.filter(date=datetime.date(2026, 1, 18)).update(location="Гараж")
        Result.objects.filter(game__season__kind=SeasonKind.CASH).update(chips_out=5000)

        run_import()

        assert set(
            Game.objects.filter(date=datetime.date(2026, 1, 18)).values_list("location", flat=True)
        ) == {"Гараж"}
        assert set(
            Result.objects.filter(game__season__kind=SeasonKind.CASH).values_list(
                "chips_out", flat=True
            )
        ) == {5000}

    def test_backfilled_fields_survive(self, run_import):
        run_import()
        oleg = Result.objects.get(
            game__date=datetime.date(2026, 3, 14), player__name="Олег Пробный"
        )
        oleg.place, oleg.rebuys, oleg.addon = 4, 1, True
        oleg.save()

        out = run_import()

        oleg.refresh_from_db()
        assert (oleg.place, oleg.rebuys, oleg.addon) == (4, 1, True)
        assert "Олег Пробный" not in out[out.index("Changes in") : out.index("Verification")]

    def test_sheet_wins_over_a_backfilled_paid_place(self, run_import):
        # A zero payout cannot hold a paid place: the sheet clears it.
        run_import()
        Result.objects.filter(
            game__date=datetime.date(2026, 3, 14), player__name="Олег Пробный"
        ).update(place=3)

        run_import()

        assert places(datetime.date(2026, 3, 14))["Олег Пробный"] is None

    def test_game_only_in_database_is_kept(self, run_import):
        run_import()
        season = Season.objects.get(year=2026, kind=SeasonKind.CASH)
        extra = make_game(season, day=5, month=9)

        out = run_import()

        assert Game.objects.filter(pk=extra.pk).exists()
        assert "warning: game Кэш 2026, 05.09.2026 exists only in the database" in out


class TestOptions:
    def test_dry_run_saves_nothing(self, run_import):
        out = run_import(dry_run=True)

        assert out.startswith("DRY RUN: nothing was saved")
        assert "results created 10" in out
        assert db_counts() == (0, 0, 0, 0)

    def test_selected_sheets(self, run_import):
        run_import(sheets="КЭШ2026, ТУР2025")
        assert sorted(Season.objects.values_list("year", "kind")) == [
            (2025, "tour"),
            (2026, "cash"),
        ]

    def test_aliases_come_from_the_database(self, run_import, run_command, workbook_path):
        run_import()  # loads the aliases file once
        before = db_counts()

        out = run_command("import_sheet", workbook_path)

        assert db_counts() == before
        assert "Aliases file" not in out

    def test_without_any_aliases_names_are_unknown(self, run_command, workbook_path):
        with pytest.raises(CommandError, match="Unknown player names"):
            run_command("import_sheet", workbook_path)

    def test_aliases_file_summary(self, run_import):
        out = run_import()
        assert "aliases created 7, re-pointed 0" in out
        assert "player created: Олег Пробный [oleg]" in out

    def test_create_missing(self, run_command, workbook_path):
        run_command("import_sheet", workbook_path, create_missing=True)
        # 4 ТУР2026 names with games, 3 "Имя (ник)" names from ТУР2025; КЭШ2026 reuses ТУР2026's
        assert Player.objects.count() == 7
        assert Player.objects.filter(name="Олег (Плуг)").exists()


class TestFailures:
    def test_unknown_names_import_nothing(self, run_import, write_aliases):
        aliases = write_aliases(
            {"players": [p for p in fixture_aliases()["players"] if p["name"] != "Олег Пробный"]}
        )
        with pytest.raises(CommandError) as exc:
            run_import(aliases=str(aliases))
        message = str(exc.value)
        assert "ТУР2026_new: Олег - Плуг" in message
        assert "ТУР2025: Олег (Плуг)" in message
        assert db_counts() == (0, 0, 0, 0)

    def test_two_names_for_one_player_in_one_game(self, run_import, write_aliases):
        data = fixture_aliases()["players"]
        data[0]["aliases"].append("Пётр - Сеялка")
        del data[1]["aliases"][0]
        aliases = write_aliases({"players": data})

        with pytest.raises(CommandError) as exc:
            run_import(aliases=str(aliases))
        expected = "ТУР2026_new: rows B2, B4 are all Иван Тестов (Трактор) and played on 18.01.2026"
        assert expected in str(exc.value)

    def test_error_mid_import_rolls_back(self, run_import, monkeypatch):
        original = sync._sync_sheet

        def fail_on_cash(plan, player_by_row):
            if plan.sheet.kind == SeasonKind.CASH:
                raise RuntimeError("boom")
            return original(plan, player_by_row)

        monkeypatch.setattr(sync, "_sync_sheet", fail_on_cash)
        with pytest.raises(RuntimeError):
            run_import()
        assert db_counts() == (0, 0, 0, 0)

    def test_broken_workbook_imports_nothing(self, run_import, tmp_path):
        sheets = fixture_sheets()
        sheet_named(sheets, "ТУР2025").dates[0] = "31.02.2025"
        with pytest.raises(CommandError, match='ТУР2025!H1: "31.02.2025" is not a valid date'):
            run_import(build_workbook(tmp_path / "bad.xlsx", sheets))


class TestSharedNames:
    """A bare "Дима" in the sheet while three players are named Дима."""

    @pytest.fixture
    def dima_workbook(self, tmp_path):
        sheets = fixture_sheets()
        sheet_named(sheets, "ТУР2026_new").rows[4].name = "Дима"  # was "Олег - Плуг"
        return build_workbook(tmp_path / "dima.xlsx", sheets)

    def aliases_with_dimas(self, write_aliases, alias_for=None):
        players = fixture_aliases()["players"] + [
            {"name": "Дима", "nickname": nickname}
            | ({"aliases": ["Дима"]} if nickname == alias_for else {})
            for nickname in ("Большой", "Малый", "Рыжий")
        ]
        return write_aliases({"players": players})

    @pytest.mark.parametrize("create_missing", [False, True])
    def test_ambiguous_name_imports_nothing(
        self, run_import, write_aliases, dima_workbook, create_missing
    ):
        aliases = self.aliases_with_dimas(write_aliases)

        with pytest.raises(CommandError) as exc:
            run_import(dima_workbook, aliases=str(aliases), create_missing=create_missing)

        message = str(exc.value)
        assert "Ambiguous player names" in message
        assert (
            "ТУР2026_new: 'Дима' matches the name of several players: "
            "Дима (Большой), Дима (Малый), Дима (Рыжий)" in message
        )
        assert db_counts() == (0, 0, 0, 0)

    def test_explicit_alias_decides(self, run_import, write_aliases, dima_workbook):
        aliases = self.aliases_with_dimas(write_aliases, alias_for="Рыжий")

        run_import(dima_workbook, aliases=str(aliases))

        results = Result.objects.filter(player__name="Дима")
        assert set(results.values_list("player__nickname", flat=True)) == {"Рыжий"}
        assert results.count() == 2  # the two ТУР2026 games of the renamed row
        # The file's other Димы are loaded as players, without games.
        assert not Result.objects.filter(player__nickname="Большой").exists()
