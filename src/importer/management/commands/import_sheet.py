from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from importer.aliases import AliasError, load_aliases, upsert_aliases
from importer.sync import LIVE_IN_PROGRESS, LIVE_RECORDED, ImportProblem, import_workbook
from importer.verify import LABELS, verify
from importer.workbook import SheetError, parse_workbook

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
            parsed = parse_workbook(options["path"], sheet_list(options["sheets"]))
            alias_map = load_aliases(options["aliases"]) if options["aliases"] else None
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        except (SheetError, AliasError) as exc:
            raise CommandError("\n".join(["Nothing imported:", *exc.errors])) from exc

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN: nothing was saved"))
        self._write_parse_notes(parsed)

        with transaction.atomic():
            if alias_map is not None:
                self._write_upsert(upsert_aliases(alias_map))
            try:
                result = import_workbook(parsed, create_missing=options["create_missing"])
            except ImportProblem as exc:
                raise CommandError("\n".join(["Nothing imported:", *exc.errors])) from exc
            reports = verify(parsed, result)
            self._write_changes(result)
            self._write_reports(reports)
            if options["dry_run"]:
                transaction.set_rollback(True)

    def _section(self, title: str, lines: list[str]) -> None:
        if lines:
            self.stdout.write(self.style.MIGRATE_HEADING(title))
            for line in lines:
                self.stdout.write(f"  {line}")

    def _write_parse_notes(self, parsed) -> None:
        self._section(
            "Sheets",
            [
                f"{s.title}: {s.kind} {s.year}, data from column {s.start_letter}, "
                f"{len(s.games)} games"
                for s in parsed.sheets
            ]
            + [f"{title}: skipped" for title in parsed.skipped],
        )
        self._section("Date fixes", [str(fix) for s in parsed.sheets for fix in s.date_fixes])
        self._section(
            "Date columns without results (no game created)",
            [f"{s.title}!{column}" for s in parsed.sheets for column in s.empty_columns],
        )

    def _write_upsert(self, upsert) -> None:
        self._section(
            "Aliases file",
            [
                f"aliases created {len(upsert.aliases_created)}, "
                f"re-pointed {len(upsert.aliases_moved)}"
            ]
            + [f"player created: {line}" for line in upsert.players_created]
            + [f"alias re-pointed: {line}" for line in upsert.aliases_moved],
        )

    def _write_changes(self, result) -> None:
        self._section("Players created", result.players_created)
        for changes in result.sheets:
            if changes.refused:
                self._section(f"Changes in {changes.title}", [REFUSED])
                continue
            counts = ", ".join(f"{name} {n}" for name, n in sorted(changes.counts.items()))
            self._section(f"Changes in {changes.title}", [counts or "nothing"])
            for line in changes.details:
                self.stdout.write(f"    {line}")
            for date, reason in sorted(changes.skipped_games.items()):
                self.stdout.write(
                    self.style.WARNING(f"    skipped: {date:%d.%m.%Y} {SKIPPED[reason]}")
                )
            for line in changes.warnings:
                self.stdout.write(self.style.WARNING(f"    warning: {line}"))

    def _write_reports(self, reports) -> None:
        self.stdout.write(self.style.MIGRATE_HEADING("Verification against the sheet"))
        for report in reports:
            self.stdout.write(f"  {report.summary()}")
            for label in LABELS:
                findings = [f for f in report.findings if f.label == label]
                if findings:
                    self.stdout.write(f"    {label}:")
                    for finding in findings:
                        self.stdout.write(f"      {finding}")
            if report.leftover_games:
                self.stdout.write("    games where the leftover does not add up:")
                for line in report.leftover_games:
                    self.stdout.write(f"      {line}")
