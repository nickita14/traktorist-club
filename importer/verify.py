"""Compare imported data with the sheet's own cached totals. Informational: never fails.

Every difference gets one label:
- "expected: tie": the sheet counts places with LARGE(payouts, k), so tied payouts count in
  several places at once (300, 300, 100 gives both a 1st and a 2nd); we store 1, 1, 3.
- "expected: sheet formula gap": explained by the sheet's own formulas, either a net formula in
  a game the player did not play, or a played game without a net formula.
- "MISMATCH": anything else.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from club.models import Game, Player, SeasonKind
from club.stats import annotate_leftover_check, leftover_warning, season_standings
from importer.sync import ImportResult
from importer.workbook import ParsedSheet, ParsedWorkbook, PlayerRow

TIE = "expected: tie"
GAP = "expected: sheet formula gap"
MISMATCH = "MISMATCH"
LABELS = (TIE, GAP, MISMATCH)

# (summary key, sheet header, standings attribute, tour only)
CHECKS = [
    ("net", "Итого", "net", False),
    ("games", "Кол. игр", "games_played", False),
    ("itm", "ITM", "itm", True),
    ("first", "Кол. 1", "first_places", True),
    ("second", "Кол. 2", "second_places", True),
    ("third", "Кол. 3", "third_places", True),
]
PLACE_OF = {"first": 1, "second": 2, "third": 3}


@dataclass
class Finding:
    player: str
    header: str
    ours: int
    sheet: int
    label: str

    def __str__(self) -> str:
        return f"{self.player}: {self.header} ours {self.ours}, sheet {self.sheet}"


@dataclass
class SeasonReport:
    title: str
    checked: int = 0
    ok: int = 0
    findings: list[Finding] = field(default_factory=list)
    leftover_games: list[str] = field(default_factory=list)

    def count(self, label: str) -> int:
        return sum(1 for finding in self.findings if finding.label == label)

    def summary(self) -> str:
        return (
            f"{self.title}: players checked {self.checked}, ok {self.ok}, "
            f"expected (tie) {self.count(TIE)}, expected (formula gap) {self.count(GAP)}, "
            f"MISMATCH {self.count(MISMATCH)}, "
            f"games with leftover problems {len(self.leftover_games)}"
        )


def verify(parsed: ParsedWorkbook, result: ImportResult) -> list[SeasonReport]:
    return [_verify_sheet(sheet, result) for sheet in parsed.sheets]


def _verify_sheet(sheet: ParsedSheet, result: ImportResult) -> SeasonReport:
    season = result.season_by_sheet[sheet.title]
    report = SeasonReport(sheet.title)
    standings = {p.pk: p for p in season_standings(season)}

    rows_by_player: dict[Player, list[PlayerRow]] = defaultdict(list)
    for row in sheet.players:
        player = result.player_by_row.get((sheet.title, row.row))
        if player is not None:
            rows_by_player[player].append(row)
    sheet_places, tied = _sheet_style_places(sheet, result, season.paid_places)

    for player, rows in rows_by_player.items():
        report.checked += 1
        ours_row = standings.get(player.pk)
        findings = []
        for key, header, attribute, tour_only in CHECKS:
            if tour_only and sheet.kind != SeasonKind.TOUR:
                continue
            values = [row.cached.get(key) for row in rows if row.cached.get(key) is not None]
            if not values:
                continue  # column absent on this sheet
            cached, ours = sum(values), getattr(ours_row, attribute, 0) if ours_row else 0
            if cached == ours:
                continue
            label = _label(key, ours, cached, rows, sheet_places[player.pk], player.pk in tied)
            findings.append(Finding(str(player), header, ours, cached, label))
        report.findings += findings
        report.ok += not findings

    games = annotate_leftover_check(Game.objects.filter(season=season).select_related("season"))
    for game in games.filter(leftover_mismatch=True).order_by("date"):
        report.leftover_games.append(f"{game.date:%d.%m.%Y}: {leftover_warning(game)}")
    return report


def _sheet_style_places(sheet: ParsedSheet, result: ImportResult, paid_places: int):
    """Place counts as the sheet computes them (LARGE per k), and the players who ever tied."""
    counts: dict[int, Counter] = defaultdict(Counter)
    tied: set[int] = set()
    payouts_by_date: dict = defaultdict(dict)
    for row in sheet.players:
        player = result.player_by_row.get((sheet.title, row.row))
        for date, entry in row.entries.items():
            payouts_by_date[date][player.pk] = entry.payout
    for payouts in payouts_by_date.values():
        positive = sorted((p for p in payouts.values() if p > 0), reverse=True)
        for k, value in enumerate(positive[:paid_places], start=1):
            for pk, payout in payouts.items():
                if payout == value:
                    counts[pk][k] += 1
        repeated = {p for p, n in Counter(positive).items() if n > 1}
        tied |= {pk for pk, payout in payouts.items() if payout in repeated}
    return counts, tied


def _label(key, ours, cached, rows, sheet_places: Counter, tied: bool) -> str:
    if key in PLACE_OF or key == "itm":
        sheet_value = (
            sheet_places[PLACE_OF[key]]
            if key in PLACE_OF
            else sum(sheet_places[k] for k in PLACE_OF.values())
        )
        return TIE if tied and sheet_value == cached else MISMATCH

    orphans = [value for row in rows for value in row.orphan_net.values()]
    missing = [row.entries[date] for row in rows for date in row.missing_net]
    if not orphans and not missing:
        return MISMATCH
    if key == "games":
        explained = ours - len(missing) + len(orphans)
    else:
        explained = ours - sum(e.payout - e.buyin for e in missing) + sum(orphans)
    return GAP if explained == cached else MISMATCH
