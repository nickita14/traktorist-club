from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from importer.management.commands.import_sheet import sheet_list
from importer.workbook import SheetError, parse_workbook


class Command(BaseCommand):
    help = "Print every distinct raw player name per sheet, to write aliases.yaml by hand."

    def add_arguments(self, parser):
        parser.add_argument("path", help="the .xlsx file (keep it in data/)")
        parser.add_argument("--sheets", help="comma-separated sheet names (default: all)")

    def handle(self, *args, **options):
        try:
            parsed = parse_workbook(options["path"], sheet_list(options["sheets"]))
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        except SheetError as exc:
            raise CommandError("\n".join(["Cannot read the workbook:", *exc.errors])) from exc

        for sheet in parsed.sheets:
            games = Counter()
            for row in sheet.players:
                games[row.raw_name] += len(row.entries)
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"{sheet.title} ({sheet.kind} {sheet.year}, data from column "
                    f"{sheet.start_letter}, {len(games)} names)"
                )
            )
            for name in sorted(games, key=str.casefold):
                count = f"games: {games[name]}" if games[name] else "(no games)"
                self.stdout.write(f"  {name}  {count}")
        for title in parsed.skipped:
            self.stdout.write(f"{title}: skipped (not an import sheet)")
