"""``import_sheet --dates``: only the selected dates are synced, after the guards."""

import datetime

import pytest
from django.core.management import CommandError

from club.models import Game, Player, Result, Season, SeasonKind
from club.tests.factories import make_season
from importer.aliases import build_alias_map, upsert_aliases
from importer.management.commands import import_sheet
from importer.service import UnknownDates, run_import
from importer.tests.sheet_builder import fixture_aliases
from importer.tests.test_guards import edited_workbook, record_live, section, tour_game

pytestmark = pytest.mark.django_db

JAN_18 = datetime.date(2026, 1, 18)
FEB_1 = datetime.date(2026, 2, 1)
MAR_14 = datetime.date(2026, 3, 14)


@pytest.fixture
def run(run_command, workbook_path, aliases_path):
    def run(path=None, **options):
        options.setdefault("aliases", str(aliases_path))
        return run_command("import_sheet", path or workbook_path, **options)

    return run


def db_counts():
    return tuple(m.objects.count() for m in (Player, Season, Game, Result))


def game_dates(kind=None):
    games = Game.objects.all() if kind is None else Game.objects.filter(season__kind=kind)
    return sorted(games.values_list("date", flat=True))


def results_of(date, kind=SeasonKind.TOUR):
    results = Result.objects.filter(game__date=date, game__season__kind=kind)
    return {r.player.name: (r.buyin, r.payout) for r in results.select_related("player")}


class TestSelection:
    def test_one_date_out_of_several(self, run):
        out = run(dates="2026-02-01", sheets="ТУР2026_new")

        assert game_dates() == [FEB_1]
        assert set(results_of(FEB_1)) == {"Пётр Примеров", "Мария Образцова", "Олег Пробный"}
        changes = section(out, "Changes in ТУР2026_new")
        assert "games created 1, results created 3, seasons created 1" in changes
        assert "not selected, left as is: 18.01.2026, 14.03.2026" in changes
        assert "selected dates: 01.02.2026" in out

    def test_unselected_dates_are_left_as_is(self, run, tmp_path):
        run()
        jan_18 = tour_game(JAN_18)
        jan_18.results.filter(player__name="Иван Тестов").update(buyin=150)
        jan_18.results.filter(player__name="Мария Образцова").delete()
        before = results_of(JAN_18)

        out = run(edited_workbook(tmp_path), dates="2026-02-01")

        assert results_of(FEB_1)["Пётр Примеров"] == (100, 300)
        assert results_of(JAN_18) == before  # neither the edit nor the deletion is undone
        assert results_of(JAN_18, SeasonKind.CASH)  # the cash game of the date is untouched
        changes = section(out, "Changes in ТУР2026_new", "Changes in ТУР2025")
        assert "01.02.2026 Пётр Примеров (Сеялка): payout 200 -> 300" in changes
        assert "not selected, left as is: 18.01.2026, 14.03.2026" in changes
        assert "exists only in the database" not in out
        assert "deleted" not in changes

    def test_the_date_on_every_chosen_sheet(self, run):
        out = run(dates="2026-01-18")

        assert game_dates(SeasonKind.TOUR) == [JAN_18]
        assert game_dates(SeasonKind.CASH) == [JAN_18]
        assert not Season.objects.filter(year=2025).exists()
        assert "no selected date on this sheet, left as is" in section(
            out, "Changes in ТУР2025", "Changes in КЭШ2026"
        )
        assert "ТУР2025: players checked" not in out

    def test_with_sheets(self, run):
        run(dates="2026-01-18", sheets="КЭШ2026")

        assert game_dates(SeasonKind.CASH) == [JAN_18]
        assert game_dates(SeasonKind.TOUR) == []

    def test_dry_run_writes_nothing(self, run):
        out = run(dates="2026-01-18,2026-02-01", dry_run=True)

        assert out.startswith("DRY RUN: nothing was saved")
        assert "games created 2" in section(out, "Changes in ТУР2026_new")
        assert db_counts() == (0, 0, 0, 0)

    def test_verification_says_partial(self, run):
        partial = "Verification against the sheet (partial import: only the selected dates"
        assert partial in run(dates="2026-01-18", dry_run=True)
        assert partial not in run(dry_run=True)

    def test_unknown_name_on_an_unselected_date_does_not_block(self, run, write_aliases):
        players = [p for p in fixture_aliases()["players"] if p["name"] != "Олег Пробный"]

        aliases = str(write_aliases({"players": players}))

        run(aliases=aliases, dates="2026-01-18", sheets="ТУР2026_new")  # Олег played 01.02, 14.03

        assert game_dates() == [JAN_18]
        assert not Player.objects.filter(name="Олег Пробный").exists()

    def test_from_google(self, run_command, workbook_path, aliases_path, monkeypatch):
        fetched = workbook_path.read_bytes()
        monkeypatch.setattr(import_sheet, "fetch_sheet", lambda: fetched)

        run_command("import_sheet", from_google=True, aliases=str(aliases_path), dates="2026-03-14")

        assert game_dates() == [MAR_14]


class TestGuardsFirst:
    def test_live_date_is_skipped_even_when_selected(self, run, tmp_path):
        run()
        record_live(tour_game(FEB_1))

        out = run(edited_workbook(tmp_path), dates="2026-01-18,2026-02-01")

        assert results_of(FEB_1)["Пётр Примеров"] == (100, 200)  # the live game is untouched
        assert results_of(JAN_18)["Пётр Примеров"] == (100, 250)  # the other selected date
        changes = section(out, "Changes in ТУР2026_new", "Changes in ТУР2025")
        assert "skipped: 01.02.2026 recorded on the live screens, left as is" in changes
        assert "not selected, left as is: 14.03.2026" in changes

    def test_unmanaged_season_is_still_refused(self, run):
        season = make_season(2026, SeasonKind.TOUR)  # created in the admin

        out = run(dates="2026-01-18")

        assert not Game.objects.filter(season=season).exists()
        assert game_dates(SeasonKind.CASH) == [JAN_18]
        assert "refused: the season is not kept in the sheet" in section(
            out, "Changes in ТУР2026_new"
        )


class TestUnknownDates:
    @pytest.mark.parametrize(
        ("dates", "sheets", "expected"),
        [
            ("2026-02-02", None, "02.02.2026: no game on this date in the selected sheets "),
            ("2026-03-21", None, "21.03.2026: no game"),  # a date column without results
            (
                "2026-01-18,2026-02-01",
                "КЭШ2026",
                "01.02.2026: no game on this date in the selected sheets (КЭШ2026)",
            ),
        ],
    )
    def test_is_an_error_and_imports_nothing(self, run, dates, sheets, expected):
        with pytest.raises(CommandError) as exc:
            run(dates=dates, sheets=sheets)

        assert str(exc.value).startswith("Nothing imported:")
        assert expected in str(exc.value)
        assert "18.01.2026" not in str(exc.value)
        assert db_counts() == (0, 0, 0, 0)  # not even the aliases file

    def test_every_chosen_sheet_is_named(self, run):
        with pytest.raises(CommandError, match=r"\(ТУР2026_new, ТУР2025, КЭШ2026\)"):
            run(dates="2026-02-02")

    @pytest.mark.parametrize("value", ["2026-13-01", "18.01.2026", "yesterday"])
    def test_malformed_value(self, run, value):
        with pytest.raises(CommandError, match=f"--dates: {value} is not a date"):
            run(dates=value)


class TestService:
    @pytest.fixture(autouse=True)
    def aliases(self):
        upsert_aliases(build_alias_map(fixture_aliases()))

    def test_digest_covers_the_selection(self, workbook_path):
        def digest(dates):
            return run_import(workbook_path, dates=dates, dry_run=True).digest()

        assert digest(None) == digest([])  # empty: every date
        assert digest([JAN_18]) != digest(None)
        assert digest([JAN_18]) != digest([FEB_1])
        assert digest([JAN_18, FEB_1]) == digest([FEB_1, JAN_18, FEB_1])

    def test_report_lists_the_found_dates(self, workbook_path):
        report = run_import(workbook_path, sheets=["ТУР2026_new", "КЭШ2026"], dry_run=True)

        assert not report.partial
        assert report.found_dates == {
            JAN_18: ["ТУР2026_new", "КЭШ2026"],
            datetime.date(2026, 1, 25): ["КЭШ2026"],
            FEB_1: ["ТУР2026_new"],
            MAR_14: ["ТУР2026_new"],
        }

    def test_unknown_dates_carry_the_data(self, workbook_path):
        with pytest.raises(UnknownDates) as exc:
            run_import(workbook_path, sheets=["КЭШ2026"], dates=[FEB_1, JAN_18])

        assert (exc.value.dates, exc.value.sheets) == ([FEB_1], ["КЭШ2026"])
