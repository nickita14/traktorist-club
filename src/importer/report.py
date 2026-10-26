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

# Counters that mean the import writes something (ImportReport.has_changes).
CHANGE_COUNTS = (
    "seasons created",
    "games created",
    "results created",
    "results updated",
    "results deleted",
)
# Fields kept for display only: the digest is built without them, so it fingerprints the same
# changes whatever way the page shows the player.
DISPLAY_FIELDS = ("name", "nickname")


@dataclass
class ResultChange:
    """A result created, deleted, or with a new buy-in or payout. Places are PlaceChange."""

    date: datetime.date
    player: str
    action: str
    diff: dict[str, tuple[int, int]] = field(default_factory=dict)  # field -> (old, new)
    name: str = ""  # the player's name and nickname apart, for display
    nickname: str = ""


@dataclass
class PlaceChange:
    """A stored place the sync replaces: a manual fix it would overwrite shows up here."""

    date: datetime.date
    player: str
    old: int | None
    new: int | None
    name: str = ""
    nickname: str = ""


@dataclass
class SheetReport:
    title: str
    kind: str
    year: int
    start_letter: str
    games: int  # date columns with results
    created_games: list[datetime.date] = field(default_factory=list)  # "games created" dates
    date_fixes: list[DateFix] = field(default_factory=list)
    empty_columns: list[str] = field(default_factory=list)
    # The season exists and is not kept in the sheet: nothing of this sheet was imported.
    refused: bool = False
    skipped_games: dict[datetime.date, str] = field(default_factory=dict)  # date -> LIVE_*
    # A date selection was given (run_import(dates=...)): sheet dates left out of it, and
    # whether the sheet had no selected date at all (then nothing of it was read or written).
    not_selected: list[datetime.date] = field(default_factory=list)
    nothing_selected: bool = False
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
class Duplicate:
    """Several rows of a sheet resolve to one player who played the same game."""

    sheet: str
    cells: list[str]
    player: str
    date: datetime.date


@dataclass
class NameProblems:
    """Names the import cannot place; with any of them nothing is imported."""

    unknown: dict[str, list[str]] = field(default_factory=dict)  # sheet -> raw names
    # sheet -> raw name -> the players it may mean
    ambiguous: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    duplicates: list[Duplicate] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # all of it, as the command prints it


@dataclass
class ImportReport:
    dry_run: bool
    sheets: list[SheetReport] = field(default_factory=list)
    skipped_sheets: list[str] = field(default_factory=list)  # not import sheets
    upsert: AliasUpsert | None = None
    players_created: list[str] = field(default_factory=list)  # --create-missing
    problems: NameProblems | None = None
    dates: list[datetime.date] | None = None  # the selection; None: every date
    # Game dates of the parsed sheets -> their titles (the admin's date list).
    found_dates: dict[datetime.date, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.problems is None

    @property
    def partial(self) -> bool:
        return self.dates is not None

    @property
    def imported(self) -> list[SheetReport]:
        return [sheet for sheet in self.sheets if not sheet.refused and not sheet.nothing_selected]

    @property
    def refused(self) -> list[SheetReport]:
        return [sheet for sheet in self.sheets if sheet.refused]

    def totals(self) -> Counter:
        return sum((sheet.counts for sheet in self.sheets), Counter())

    @property
    def has_changes(self) -> bool:
        """Whether the import writes anything: an empty one is not applied (sheet sync)."""
        totals = self.totals()
        return (
            any(totals[key] for key in CHANGE_COUNTS)
            or any(sheet.place_changes for sheet in self.sheets)
            or bool(self.players_created)
            or bool(self.upsert and self.upsert.players_created)
        )

    def digest(self) -> str:
        """Fingerprint of every change; equal digests mean the same import (sheet sync apply)."""
        data = {
            "problems": asdict(self.problems) if self.problems else None,
            "upsert": asdict(self.upsert) if self.upsert else None,
            "players_created": self.players_created,
            "dates": self.dates,
            "sheets": [
                {
                    "title": sheet.title,
                    "refused": sheet.refused,
                    "skipped": sorted(sheet.skipped_games.items()),
                    "counts": sorted(sheet.counts.items()),
                    "changes": [_fingerprint(change) for change in sheet.changes],
                    "places": [_fingerprint(change) for change in sheet.place_changes],
                    "db_only": sheet.db_only_games,
                    "not_selected": sheet.not_selected,
                    "nothing_selected": sheet.nothing_selected,
                }
                for sheet in self.sheets
            ],
        }
        encoded = json.dumps(data, default=str, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode()).hexdigest()


def _fingerprint(change: ResultChange | PlaceChange) -> dict:
    return {key: value for key, value in asdict(change).items() if key not in DISPLAY_FIELDS}
