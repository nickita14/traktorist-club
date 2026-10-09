"""Run an import end to end in one transaction: aliases file, guards, sync, verification.

``import_sheet`` and the sheet sync admin page both call ``run_import`` and show its report.
"""

from pathlib import Path

from django.db import transaction

from importer.aliases import AliasMap, upsert_aliases
from importer.report import ImportReport, SheetReport
from importer.sync import ImportProblem, import_workbook
from importer.verify import verify
from importer.workbook import parse_workbook


class ReportChanged(Exception):
    """The import would differ from the previewed one; it was rolled back."""

    def __init__(self, report: ImportReport):
        self.report = report
        super().__init__("the import differs from the preview")


def run_import(
    source: str | Path | bytes,
    *,
    sheets: list[str] | None = None,
    dry_run: bool = False,
    create_missing: bool = False,
    alias_map: AliasMap | None = None,
    expected_digest: str | None = None,
) -> ImportReport:
    """Import ``source`` (a path or the workbook's bytes); a dry run rolls everything back.

    Unreadable workbooks raise SheetError. Unplaceable names leave ``report.problems`` set and
    nothing imported. With ``expected_digest``, an import whose changes differ from it is rolled
    back and raises ReportChanged.
    """
    parsed = parse_workbook(source, sheets)
    report = ImportReport(dry_run=dry_run, skipped_sheets=list(parsed.skipped))
    with transaction.atomic():
        if alias_map is not None:
            report.upsert = upsert_aliases(alias_map)
        try:
            result = import_workbook(parsed, create_missing=create_missing)
        except ImportProblem as exc:
            transaction.set_rollback(True)
            report.problems = exc.problems
            report.sheets = [SheetReport.of(sheet) for sheet in parsed.sheets]
            return report

        verification = {season.title: season for season in verify(parsed, result)}
        for sheet in result.sheets:
            sheet.verification = verification.get(sheet.title)
        report.sheets = result.sheets
        report.players_created = result.players_created
        if expected_digest is not None and report.digest() != expected_digest:
            raise ReportChanged(report)  # leaving the block rolls the import back
        if dry_run:
            transaction.set_rollback(True)
    return report
