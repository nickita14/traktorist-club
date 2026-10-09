import pytest

from importer.aliases import build_alias_map, upsert_aliases
from importer.sync import import_workbook
from importer.tests.sheet_builder import (
    build_workbook,
    fixture_aliases,
    fixture_sheets,
    sheet_named,
)
from importer.verify import GAP, MISMATCH, TIE, verify
from importer.workbook import parse_workbook

pytestmark = pytest.mark.django_db


def run_verify(path):
    parsed = parse_workbook(path)
    upsert_aliases(build_alias_map(fixture_aliases()))
    result = import_workbook(parsed)
    return {report.title: report for report in verify(parsed, result)}


def findings(report):
    return {(f.player, f.header, f.ours, f.sheet, f.label) for f in report.findings}


class TestLabels:
    def test_tie_is_expected(self, workbook_path):
        report = run_verify(workbook_path)["ТУР2026_new"]

        assert findings(report) == {
            ("Иван Тестов (Трактор)", "Кол. 2", 0, 1, TIE),
            ("Иван Тестов (Трактор)", "ITM", 2, 3, TIE),
            ("Пётр Примеров (Сеялка)", "Кол. 2", 1, 2, TIE),
            ("Пётр Примеров (Сеялка)", "ITM", 3, 4, TIE),
        }
        assert (report.checked, report.ok) == (4, 2)

    def test_formula_gap_and_real_mismatch(self, workbook_path):
        report = run_verify(workbook_path)["ТУР2025"]
        assert findings(report) == {
            ("Олег Пробный", "Кол. игр", 2, 3, GAP),
            ("Пётр Примеров (Сеялка)", "Итого", 0, 20, MISMATCH),
        }

    def test_missing_net_formula_is_a_gap(self, tmp_path):
        sheets = fixture_sheets()
        cash = sheet_named(sheets, "КЭШ2026")
        cash.rows[0].missing_net = (0,)
        cash.rows[0].cached["Итого"] = 0  # the sheet does not see the +50 of that game
        report = run_verify(build_workbook(tmp_path / "x.xlsx", sheets))["КЭШ2026"]
        assert findings(report) == {("Иван Тестов (Трактор)", "Итого", 50, 0, GAP)}

    def test_place_difference_without_tie_is_a_mismatch(self, tmp_path):
        sheets = fixture_sheets()
        sheet_named(sheets, "ТУР2025").rows[0].cached["Кол. 1"] = 1
        report = run_verify(build_workbook(tmp_path / "x.xlsx", sheets))["ТУР2025"]
        assert ("Иван Тестов (Трактор)", "Кол. 1", 2, 1, MISMATCH) in findings(report)

    def test_clean_sheet(self, workbook_path):
        report = run_verify(workbook_path)["КЭШ2026"]
        assert (report.checked, report.ok, report.findings) == (3, 3, [])


class TestLeftoverRule:
    def test_unbalanced_tour_game_listed_cash_leftover_not(self, workbook_path):
        reports = run_verify(workbook_path)

        assert reports["ТУР2025"].leftover_games == [
            "11.05.2025: Остаток не сходится: в турнире закупки (150) и выплаты (140) "
            "должны совпадать, разница 10."
        ]
        assert reports["КЭШ2026"].leftover_games == []  # positive cash leftover is fine
        assert reports["ТУР2026_new"].leftover_games == []


def test_command_reports_and_succeeds(run_command, workbook_path, aliases_path):
    out = run_command("import_sheet", workbook_path, aliases=str(aliases_path))

    assert (
        "ТУР2025: players checked 3, ok 1, expected (tie) 0, expected (formula gap) 1, "
        "MISMATCH 1, games with leftover problems 1" in out
    )
    assert "Пётр Примеров (Сеялка): Итого ours 0, sheet 20" in out
    assert "11.05.2025: Остаток не сходится" in out


def test_list_sheet_names(run_command, workbook_path):
    out = run_command("list_sheet_names", workbook_path)

    assert "ТУР2026_new (tour 2026, data from column I, 5 names)" in out
    assert "  Мария - Комбайн  games: 3" in out
    assert "  Нина - Борона  (no games)" in out
    assert "  Иван (Трактор)  games: 2" in out
    assert "ТУР2026_old version: skipped (not an import sheet)" in out
