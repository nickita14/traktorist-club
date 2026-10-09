"""What an import did (or would do): one structured object for every way of showing it.

``import_sheet`` prints it in English, the sheet sync admin page renders it in Russian. Both read
the same fields, so neither formats anything the other cannot see.
"""

import datetime
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from importer.aliases import AliasUpsert
from importer.workbook import DateFix, ParsedSheet

if TYPE_CHECKING:  # importer.verify imports importer.sync, which imports this module
    from importer.verify import SeasonReport

# Why a sheet date was left alone (SheetReport.skipped_games).
LIVE_RECORDED = "live recorded"
LIVE_IN_PROGRESS = "live in progress"

# Result changes (ResultChange.action).
CREATED, UPDATED, DELETED = "created", "updated", "deleted"


@dataclass
class ResultChange:
    """A result created, deleted, or with a new buy-in or payout. Places are PlaceChange."""

    date: datetime.date
    player: str
    action: str
    diff: dict[str, tuple[int, int]] = field(default_factory=dict)  # field -> (old, new)


@dataclass
class PlaceChange:
    """A stored place the sync replaces: a manual fix it would overwrite shows up here."""

    date: datetime.date
    player: str
    old: int | None
    new: int | None


@dataclass
class SheetReport:
    title: str
    kind: str
    year: int
    start_letter: str
    games: int  # date columns with results
    date_fixes: list[DateFix] = field(default_factory=list)
    empty_columns: list[str] = field(default_factory=list)
    # The season exists and is not kept in the sheet: nothing of this sheet was imported.
    refused: bool = False
    skipped_games: dict[datetime.date, str] = field(default_factory=dict)  # date -> LIVE_*
    counts: Counter = field(default_factory=Counter)
    changes: list[ResultChange] = field(default_factory=list)
    place_changes: list[PlaceChange] = field(default_factory=list)
    db_only_games: list[datetime.date] = field(default_factory=list)
    verification: "SeasonReport | None" = None

    @classmethod
    def of(cls, sheet: ParsedSheet) -> "SheetReport":
        return cls(
            title=sheet.title,
            kind=sheet.kind,
            year=sheet.year,
            start_letter=sheet.start_letter,
            games=len(sheet.games),
            date_fixes=list(sheet.date_fixes),
            empty_columns=list(sheet.empty_columns),
        )


@dataclass
class NameProblems:
    """Names the import cannot place; with any of them nothing is imported."""

    unknown: dict[str, list[str]] = field(default_factory=dict)  # sheet -> raw names
    ambiguous: dict[str, list[str]] = field(default_factory=dict)  # sheet -> messages
    duplicates: list[str] = field(default_factory=list)  # two rows, one player, one game
    lines: list[str] = field(default_factory=list)  # the same, as the command prints it


@dataclass
class ImportReport:
    dry_run: bool
    sheets: list[SheetReport] = field(default_factory=list)
    skipped_sheets: list[str] = field(default_factory=list)  # not import sheets
    upsert: AliasUpsert | None = None
    players_created: list[str] = field(default_factory=list)  # --create-missing
    problems: NameProblems | None = None

    @property
    def ok(self) -> bool:
        return self.problems is None

    @property
    def imported(self) -> list[SheetReport]:
        return [sheet for sheet in self.sheets if not sheet.refused]

    @property
    def refused(self) -> list[SheetReport]:
        return [sheet for sheet in self.sheets if sheet.refused]

    def totals(self) -> Counter:
        return sum((sheet.counts for sheet in self.sheets), Counter())

    def digest(self) -> str:
        """Fingerprint of every change; equal digests mean the same import (sheet sync apply)."""
        data = {
            "problems": asdict(self.problems) if self.problems else None,
            "upsert": asdict(self.upsert) if self.upsert else None,
            "players_created": self.players_created,
            "sheets": [
                {
                    "title": sheet.title,
                    "refused": sheet.refused,
                    "skipped": sorted(sheet.skipped_games.items()),
                    "counts": sorted(sheet.counts.items()),
                    "changes": [asdict(change) for change in sheet.changes],
                    "places": [asdict(change) for change in sheet.place_changes],
                    "db_only": sheet.db_only_games,
                }
                for sheet in self.sheets
            ],
        }
        encoded = json.dumps(data, default=str, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode()).hexdigest()
