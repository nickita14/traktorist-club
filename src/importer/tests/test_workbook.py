import datetime

import pytest

from importer.tests.conftest import edit_cells
from importer.tests.sheet_builder import FakeSheet, build_workbook, fixture_sheets, sheet_named
from importer.workbook import Entry, SheetError, parse_workbook


def by_title(parsed):
    return {sheet.title: sheet for sheet in parsed.sheets}


def d(day, month, year):
    return datetime.date(year, month, day)


class TestLayout:
    def test_sheets_and_start_columns(self, workbook_path):
        parsed = parse_workbook(workbook_path)

        assert [(s.title, s.kind, s.year, s.start_letter) for s in parsed.sheets] == [
            ("ТУР2026_new", "tour", 2026, "I"),
            ("ТУР2025", "tour", 2025, "H"),
            ("КЭШ2026", "cash", 2026, "E"),
        ]
        assert parsed.skipped == ["ТУР2026_old version"]

    def test_end_marker_in_b_and_d(self, workbook_path):
        sheets = by_title(parse_workbook(workbook_path))

        assert [p.raw_name for p in sheets["ТУР2026_new"].players] == [
            "Иван - Трактор",
            "Пётр - Сеялка",
            "Мария - Комбайн",
            "Олег - Плуг",
            "Нина - Борона",
        ]
        assert len(sheets["ТУР2025"].players) == 3  # marker in D
        assert len(sheets["КЭШ2026"].players) == 3  # fund block below the marker ignored

    def test_blank_slot_skipped_and_player_without_games_kept(self, workbook_path):
        tour = by_title(parse_workbook(workbook_path))["ТУР2026_new"]
        rows = {p.raw_name: p for p in tour.players}

        assert rows["Мария - Комбайн"].row == 8  # after the blank slot at rows 6-7
        assert rows["Нина - Борона"].entries == {}

    def test_entries_are_integers(self, workbook_path):
        tour = by_title(parse_workbook(workbook_path))["ТУР2026_new"]
        ivan = tour.players[0]

        assert ivan.entries == {
            d(18, 1, 2026): Entry(buyin=100, payout=200),
            d(14, 3, 2026): Entry(buyin=200, payout=300),
        }
        assert all(type(v) is int for e in ivan.entries.values() for v in (e.buyin, e.payout))

    def test_cached_summary_values(self, workbook_path):
        tour = by_title(parse_workbook(workbook_path))["ТУР2026_new"]
        assert tour.players[0].cached == {
            "net": 200,
            "games": 2,
            "itm": 3,
            "first": 2,
            "second": 1,
            "third": 0,
        }


class TestDates:
    def test_year_comes_from_sheet_name_and_fixes_are_logged(self, workbook_path):
        parsed = parse_workbook(workbook_path)
        sheets = by_title(parsed)

        assert [str(fix) for s in parsed.sheets for fix in s.date_fixes] == [
            "ТУР2026_new!K1: 01.02.2025 -> 01.02.2026 (wrong year)",
            'ТУР2026_new!M1: "14.03.0202" -> 14.03.2026 (text date)',
            'КЭШ2026!G1: "25.01.0202" -> 25.01.2026 (text date)',
        ]
        assert [g.date for g in sheets["ТУР2026_new"].games] == [
            d(18, 1, 2026),
            d(1, 2, 2026),
            d(14, 3, 2026),
        ]

    def test_date_column_without_results_is_not_a_game(self, workbook_path):
        tour = by_title(parse_workbook(workbook_path))["ТУР2026_new"]
        assert tour.empty_columns == ["O (21.03.2026)"]


class TestFormulaGaps:
    def test_orphan_net_formula_recorded(self, workbook_path):
        old_tour = by_title(parse_workbook(workbook_path))["ТУР2025"]
        oleg = next(p for p in old_tour.players if p.raw_name == "Олег (Плуг)")
        assert oleg.orphan_net == {d(4, 5, 2025): 0}

    def test_missing_net_formula_recorded(self, tmp_path):
        sheets = fixture_sheets()
        sheet_named(sheets, "КЭШ2026").rows[0].missing_net = (0,)
        cash = by_title(parse_workbook(build_workbook(tmp_path / "x.xlsx", sheets)))["КЭШ2026"]
        assert cash.players[0].missing_net == [d(18, 1, 2026)]


class TestOnly:
    def test_selected_sheets(self, workbook_path):
        parsed = parse_workbook(workbook_path, only=["КЭШ2026"])
        assert [s.title for s in parsed.sheets] == ["КЭШ2026"]

    def test_unknown_and_non_import_sheets_are_errors(self, workbook_path):
        with pytest.raises(SheetError) as exc:
            parse_workbook(workbook_path, only=["ТУР1999", "ТУР2026_old version"])
        assert exc.value.errors == [
            "ТУР1999: no such sheet",
            "ТУР2026_old version: not an import sheet (expected ТУРyyyy or КЭШyyyy)",
        ]


class TestErrors:
    def test_all_problems_are_reported_together(self, tmp_path):
        sheets = fixture_sheets()
        tour = sheet_named(sheets, "ТУР2026_new")
        tour.dates[2] = "30.02.0202"  # impossible date
        tour.rows[0].results[0] = (100.5, 200)  # not whole lei
        sheet_named(sheets, "ТУР2025").rows[1].results[0] = (0, 0)  # zero buy-in
        cash = sheet_named(sheets, "КЭШ2026")
        cash.marker_column, cash.below_marker = "F", []  # marker only counts in B or D
        path = build_workbook(tmp_path / "bad.xlsx", sheets)
        edit_cells(path, "ТУР2025", {"L3": 40.0})  # payout in a game Иван did not play

        with pytest.raises(SheetError) as exc:
            parse_workbook(path)

        assert exc.value.errors == [
            'ТУР2026_new!M1: "30.02.0202" is not a valid date',
            "ТУР2026_new!I2: 100.5 is not a whole number of lei",
            "ТУР2025!L3: payout without a buy-in",
            "ТУР2025!H4: buy-in is 0 (leave it empty if they did not play)",
            "КЭШ2026: end marker 'Закупка' not found in B or D",
        ]

    def test_number_in_blank_slot(self, tmp_path):
        sheets = fixture_sheets()
        path = build_workbook(tmp_path / "bad.xlsx", sheets)
        edit_cells(path, "ТУР2026_new", {"I6": 100.0})  # rows 6-7 are the blank slot

        with pytest.raises(SheetError) as exc:
            parse_workbook(path)
        assert exc.value.errors == ["ТУР2026_new!I6: number in a row without a player name"]

    def test_two_sheets_for_one_season(self, tmp_path):
        sheets = fixture_sheets()
        duplicate = FakeSheet(**{**vars(sheet_named(sheets, "КЭШ2026")), "title": "КЭШ2026_new"})
        path = build_workbook(tmp_path / "dup.xlsx", [*sheets, duplicate])

        with pytest.raises(SheetError) as exc:
            parse_workbook(path)
        assert exc.value.errors == ["КЭШ2026_new: same season as КЭШ2026"]

    def test_repeated_date_after_fix(self, tmp_path):
        sheets = fixture_sheets()
        sheet_named(sheets, "КЭШ2026").dates[1] = datetime.datetime(2025, 1, 18)
        with pytest.raises(SheetError) as exc:
            parse_workbook(build_workbook(tmp_path / "x.xlsx", sheets))
        assert exc.value.errors == ["КЭШ2026!G1: date 18.01.2026 repeats КЭШ2026!E1"]


class TestBytes:
    """The sheet sync parses the fetched bytes; no file is ever written."""

    def test_same_as_from_the_file(self, workbook_path):
        from_file = parse_workbook(workbook_path)
        from_bytes = parse_workbook(workbook_path.read_bytes())
        assert from_bytes == from_file

    @pytest.mark.parametrize("content", [b"PK\x03\x04 not really a zip", b"<html></html>"])
    def test_not_a_workbook(self, content):
        with pytest.raises(SheetError, match="not a readable .xlsx workbook"):
            parse_workbook(content)
