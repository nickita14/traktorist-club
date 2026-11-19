"""Season.sheet_title: a tab with any name holds a season once a superuser names it."""

import datetime

import pytest
from django.core.exceptions import ValidationError

from club.models import Game, SeasonKind
from club.tests.factories import make_season
from importer.sync import sheet_titles
from importer.tests.sheet_builder import build_workbook, fixture_sheets, sheet_named
from importer.workbook import SheetError, import_sheet_titles, parse_workbook, season_of

TOUR_2026 = (2026, SeasonKind.TOUR)


def renamed_workbook(tmp_path, title="Турниры 26", *, keep_old=False):
    """The fixture with ТУР2026_new renamed (or copied, with ``keep_old``) to ``title``."""
    sheets = fixture_sheets()
    tour = sheet_named(sheets, "ТУР2026_new")
    if keep_old:
        copy = fixture_sheets()
        renamed = sheet_named(copy, "ТУР2026_new")
        renamed.title = title
        sheets.append(renamed)
    else:
        tour.title = title
    return build_workbook(tmp_path / "renamed.xlsx", sheets)


class TestSeasonOf:
    def test_regular_names_need_nothing(self):
        assert season_of("ТУР2027") == (2027, SeasonKind.TOUR)
        assert season_of("КЭШ2027_new") == (2027, SeasonKind.CASH)
        assert season_of("Турниры 2027") is None

    def test_explicit_title_wins(self):
        titles = {"Турниры 2027": (2027, SeasonKind.TOUR)}
        assert season_of("Турниры 2027", titles) == (2027, SeasonKind.TOUR)

    def test_regular_tab_of_an_explicitly_named_season_is_skipped(self):
        titles = {"Турниры 2027": (2027, SeasonKind.TOUR)}
        assert season_of("ТУР2027", titles) is None
        assert season_of("КЭШ2027", titles) == (2027, SeasonKind.CASH)  # another season

    def test_exact_match_only(self):
        titles = {"Турниры 2027": (2027, SeasonKind.TOUR)}
        assert season_of("турниры 2027", titles) is None
        assert season_of("Турниры 2027 ", titles) is None


class TestParse:
    def test_unknown_name_is_skipped_without_a_title(self, tmp_path):
        parsed = parse_workbook(renamed_workbook(tmp_path))
        assert "Турниры 26" in parsed.skipped
        assert all(sheet.kind == SeasonKind.CASH or sheet.year != 2026 for sheet in parsed.sheets)

    def test_named_tab_is_parsed_with_the_season_year(self, tmp_path):
        parsed = parse_workbook(renamed_workbook(tmp_path), titles={"Турниры 26": TOUR_2026})
        sheet = next(sheet for sheet in parsed.sheets if sheet.title == "Турниры 26")
        assert (sheet.year, sheet.kind) == TOUR_2026
        # The wrong-year and text dates are fixed with the season's year, as from a ТУР name.
        assert datetime.date(2026, 2, 1) in [column.date for column in sheet.games]

    def test_old_regular_tab_is_left_out(self, tmp_path):
        path = renamed_workbook(tmp_path, keep_old=True)
        assert "Турниры 26" in parse_workbook(path).skipped  # without a title: the old tab
        parsed = parse_workbook(path, titles={"Турниры 26": TOUR_2026})
        assert "ТУР2026_new" in parsed.skipped
        assert [s.title for s in parsed.sheets if s.year == 2026 and s.kind == "tour"] == [
            "Турниры 26"
        ]

    def test_selection_accepts_a_named_tab(self, tmp_path):
        path = renamed_workbook(tmp_path)
        with pytest.raises(SheetError, match="not an import sheet"):
            parse_workbook(path, ["Турниры 26"])
        parsed = parse_workbook(path, ["Турниры 26"], {"Турниры 26": TOUR_2026})
        assert [sheet.title for sheet in parsed.sheets] == ["Турниры 26"]

    def test_import_sheet_titles(self, tmp_path):
        path = renamed_workbook(tmp_path, keep_old=True)
        titles = import_sheet_titles(path, {"Турниры 26": TOUR_2026})
        assert "Турниры 26" in titles
        assert "ТУР2026_new" not in titles


@pytest.mark.django_db
class TestFromTheDatabase:
    def test_sheet_titles(self):
        make_season(2026, SeasonKind.TOUR, sheet_title="Турниры 26")
        make_season(2026, SeasonKind.CASH)
        assert sheet_titles() == {"Турниры 26": TOUR_2026}

    def test_import_reads_the_named_tab(self, tmp_path, run_command, aliases_path):
        season = make_season(2026, SeasonKind.TOUR, sheet_managed=True, sheet_title="Турниры 26")

        out = run_command("import_sheet", renamed_workbook(tmp_path), aliases=str(aliases_path))

        assert "Changes in Турниры 26" in out
        assert Game.objects.filter(season=season).count() == 3

    def test_title_is_unique_and_trimmed(self):
        first = make_season(2026, SeasonKind.TOUR)
        first.sheet_title = "  Турниры 26 "
        first.full_clean()
        first.save()
        assert first.sheet_title == "Турниры 26"

        other = make_season(2026, SeasonKind.CASH)
        other.sheet_title = "Турниры 26"
        with pytest.raises(ValidationError, match="уже указан у другого сезона"):
            other.full_clean()

    def test_many_seasons_may_leave_it_blank(self):
        make_season(2026, SeasonKind.TOUR)
        make_season(2026, SeasonKind.CASH)
        make_season(2025, SeasonKind.TOUR)
        assert sheet_titles() == {}
