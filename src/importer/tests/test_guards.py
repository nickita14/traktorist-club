"""The importer never writes to an unmanaged season or to a game from the live screens."""

import datetime
import uuid

import pytest
from django.utils import timezone

from club.models import Game, Result, Season, SeasonKind
from club.tests.factories import make_season
from importer.tests.sheet_builder import (
    build_workbook,
    fixture_aliases,
    fixture_sheets,
    sheet_named,
)
from live.models import LiveAction

pytestmark = pytest.mark.django_db

JAN_18 = datetime.date(2026, 1, 18)
FEB_1 = datetime.date(2026, 2, 1)
MAR_14 = datetime.date(2026, 3, 14)


@pytest.fixture
def run_import(run_command, workbook_path, aliases_path):
    def run(path=None, **options):
        options.setdefault("aliases", str(aliases_path))
        return run_command("import_sheet", path or workbook_path, **options)

    return run


def tour_game(date):
    return Game.objects.get(season__year=2026, season__kind=SeasonKind.TOUR, date=date)


def section(out, start, end="Verification"):
    return out[out.index(start) : out.index(end)]


def edited_workbook(tmp_path):
    """The fixture with a new payout on two dates: Пётр wins 18.01 and 01.02."""
    sheets = fixture_sheets()
    tour = sheet_named(sheets, "ТУР2026_new")
    tour.rows[1].results[0] = (100, 250)
    tour.rows[1].results[1] = (100, 300)
    return build_workbook(tmp_path / "edited.xlsx", sheets)


def record_live(game):
    LiveAction.objects.create(key=uuid.uuid4(), game=game, kind="start", summary="начата")


class TestUnmanagedSeason:
    def test_refused_as_a_whole_others_import(self, run_import):
        season = make_season(2026, SeasonKind.TOUR)  # created in the admin: not kept in the sheet

        out = run_import()

        assert not Game.objects.filter(season=season).exists()
        assert Game.objects.filter(season__year=2025).count() == 3
        assert Game.objects.filter(season__kind=SeasonKind.CASH).count() == 2
        assert "refused: the season is not kept in the sheet" in section(
            out, "Changes in ТУР2026_new"
        )
        verification = out[out.index("Verification") :]
        assert "ТУР2026_new" not in verification
        assert "ТУР2025: players checked" in verification
        assert "КЭШ2026: players checked" in verification

    def test_unknown_names_of_a_refused_sheet_do_not_block(self, run_import, write_aliases):
        make_season(2025, SeasonKind.TOUR)
        players = fixture_aliases()["players"]
        players[3]["aliases"] = ["Олег - Плуг"]  # "Олег (Плуг)" of ТУР2025 is now unknown

        run_import(aliases=str(write_aliases({"players": players})))

        assert Game.objects.filter(season__year=2026).count() == 5
        assert not Game.objects.filter(season__year=2025).exists()

    def test_imported_seasons_are_managed(self, run_import):
        run_import()
        assert set(Season.objects.values_list("sheet_managed", flat=True)) == {True}


class TestLiveGames:
    def test_live_recorded_date_is_left_as_is(self, run_import, tmp_path):
        run_import()
        game = tour_game(JAN_18)
        record_live(game)
        game.results.filter(player__name="Иван Тестов").update(buyin=150)
        game.results.filter(player__name="Мария Образцова").delete()

        out = run_import(edited_workbook(tmp_path))

        live = {r.player.name: (r.buyin, r.payout) for r in game.results.select_related("player")}
        assert live == {"Иван Тестов": (150, 200), "Пётр Примеров": (100, 100)}
        petr = Result.objects.get(game=tour_game(FEB_1), player__name="Пётр Примеров")
        assert petr.payout == 300  # the other dates of the season import
        changes = section(out, "Changes in ТУР2026_new", "Changes in ТУР2025")
        assert "skipped: 18.01.2026 recorded on the live screens, left as is" in changes
        assert "01.02.2026 Пётр Примеров (Сеялка): payout 200 -> 300" in changes
        assert "18.01.2026 " not in changes.replace("skipped: 18.01.2026", "")
        assert "exists only in the database" not in changes

    def test_game_in_progress_is_left_as_is(self, run_import, tmp_path):
        run_import()
        game = tour_game(JAN_18)
        Game.objects.filter(pk=game.pk).update(live_stage="rebuys", started_at=timezone.now())

        out = run_import(edited_workbook(tmp_path))

        assert game.results.get(player__name="Пётр Примеров").payout == 100
        assert Result.objects.get(game=tour_game(FEB_1), player__name="Пётр Примеров").payout == 300
        assert "skipped: 18.01.2026 live game in progress, left as is" in out

    def test_live_game_on_a_new_sheet_date(self, run_import):
        # The game started on the live screens before the sheet got its column: never merged.
        season = make_season(2026, SeasonKind.TOUR, sheet_managed=True)
        game = Game.objects.create(
            season=season, date=MAR_14, live_stage="final", started_at=timezone.now()
        )

        out = run_import()

        assert not game.results.exists()
        assert Game.objects.filter(season=season).count() == 3
        assert "skipped: 14.03.2026 live game in progress" in out

    def test_dry_run_reports_the_same(self, run_import, tmp_path):
        run_import()
        record_live(tour_game(JAN_18))

        out = run_import(edited_workbook(tmp_path), dry_run=True)

        assert "skipped: 18.01.2026 recorded on the live screens" in out
        assert tour_game(FEB_1).results.get(player__name="Пётр Примеров").payout == 200
