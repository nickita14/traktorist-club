from django.core.management.base import BaseCommand, CommandError

from club.models import SeasonKind
from importer.aliases import AliasError, load_aliases
from importer.report import DELETED, LIVE_IN_PROGRESS, LIVE_RECORDED, UPDATED, ImportReport
from importer.service import run_import
from importer.verify import LABELS
from importer.workbook import SheetError

REFUSED = "refused: the season is not kept in the sheet (Season.sheet_managed is off)"
SKIPPED = {
    LIVE_RECORDED: "recorded on the live screens, left as is",
    LIVE_IN_PROGRESS: "live game in progress, left as is",
}


def sheet_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [name.strip() for name in value.split(",") if name.strip()]


class Command(BaseCommand):
    help = (
        "Import the club spreadsheet in one transaction, then compare the result with the "
        "sheet's cached totals. The sheet wins: rerunning updates rows and deletes results "
        "missing from the sheet."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", help="the .xlsx file (keep it in data/)")
        parser.add_argument(
            "--aliases",
            help="aliases.yaml to load into the database first (players and their raw names)",
        )
        parser.add_argument(
            "--sheets", help="comma-separated sheet names to import (default: all import sheets)"
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="show what would change, save nothing"
        )
        parser.add_argument(
            "--create-missing",
            action="store_true",
            help="create players for names not in the aliases file instead of failing",
        )

    def handle(self, *args, **options):
        try:
            alias_map = load_aliases(options["aliases"]) if options["aliases"] else None
            if options["dry_run"]:
                self.stdout.write(self.style.WARNING("DRY RUN: nothing was saved"))
            report = run_import(
                options["path"],
                sheets=sheet_list(options["sheets"]),
                dry_run=options["dry_run"],
                create_missing=options["create_missing"],
                alias_map=alias_map,
            )
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        except (SheetError, AliasError) as exc:
            raise CommandError("\n".join(["Nothing imported:", *exc.errors])) from exc

        self._write_parse_notes(report)
        if report.problems is not None:
            raise CommandError("\n".join(["Nothing imported:", *report.problems.lines]))
        self._write_upsert(report.upsert)
        self._write_changes(report)
        self._write_verification(report)

    def _section(self, title: str, lines: list[str]) -> None:
        if lines:
            self.stdout.write(self.style.MIGRATE_HEADING(title))
            for line in lines:
                self.stdout.write(f"  {line}")

    def _write_parse_notes(self, report: ImportReport) -> None:
        self._section(
            "Sheets",
            [
                f"{s.title}: {s.kind} {s.year}, data from column {s.start_letter}, {s.games} games"
                for s in report.sheets
            ]
            + [f"{title}: skipped" for title in report.skipped_sheets],
        )
        self._section("Date fixes", [str(fix) for s in report.sheets for fix in s.date_fixes])
        self._section(
            "Date columns without results (no game created)",
            [f"{s.title}!{column}" for s in report.sheets for column in s.empty_columns],
        )

    def _write_upsert(self, upsert) -> None:
        if upsert is None:
            return
        self._section(
            "Aliases file",
            [
                f"aliases created {len(upsert.aliases_created)}, "
                f"re-pointed {len(upsert.aliases_moved)}"
            ]
            + [f"player created: {line}" for line in upsert.players_created]
            + [f"alias re-pointed: {line}" for line in upsert.aliases_moved],
        )

    def _write_changes(self, report: ImportReport) -> None:
        self._section("Players created", report.players_created)
        for sheet in report.sheets:
            if sheet.refused:
                self._section(f"Changes in {sheet.title}", [REFUSED])
                continue
            counts = ", ".join(f"{name} {n}" for name, n in sorted(sheet.counts.items()))
            self._section(f"Changes in {sheet.title}", [counts or "nothing"])
            for change in sheet.changes:
                if change.action == UPDATED:
                    diff = ", ".join(f"{k} {old} -> {new}" for k, (old, new) in change.diff.items())
                    self.stdout.write(f"    {change.date:%d.%m.%Y} {change.player}: {diff}")
                elif change.action == DELETED:
                    self.stdout.write(
                        f"    {change.date:%d.%m.%Y} {change.player}: deleted (not in the sheet)"
                    )
            if sheet.place_changes:
                self.stdout.write("    place changes:")
                for change in sheet.place_changes:
                    self.stdout.write(
                        f"      {change.date:%d.%m.%Y} {change.player}: "
                        f"place {change.old} -> {change.new}"
                    )
            for date, reason in sorted(sheet.skipped_games.items()):
                self.stdout.write(
                    self.style.WARNING(f"    skipped: {date:%d.%m.%Y} {SKIPPED[reason]}")
                )
            season = f"{SeasonKind(sheet.kind).label} {sheet.year}"
            for date in sheet.db_only_games:
                self.stdout.write(
                    self.style.WARNING(
                        f"    warning: game {season}, {date:%d.%m.%Y} exists only in the "
                        "database; left as is"
                    )
                )

    def _write_verification(self, report: ImportReport) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("Verification against the sheet"))
        for sheet in report.imported:
            verification = sheet.verification
            self.stdout.write(f"  {verification.summary()}")
            for label in LABELS:
                findings = [f for f in verification.findings if f.label == label]
                if findings:
                    self.stdout.write(f"    {label}:")
                    for finding in findings:
                        self.stdout.write(f"      {finding}")
            if verification.leftover_games:
                self.stdout.write("    games where the leftover does not add up:")
                for line in verification.leftover_games:
                    self.stdout.write(f"      {line}")
