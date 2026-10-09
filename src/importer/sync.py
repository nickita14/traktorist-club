"""Write a parsed workbook to the database. Idempotent: the sheet wins for sheet-derived fields.

Natural keys: Season (year, kind), Player (name, nickname), Game (season, date),
Result (game, player). Buy-in and payout come from the sheet, tour places are recomputed from
payouts (a place beyond the paid ones, backfilled in the admin, is kept while the sheet pays that
player nothing), and results missing from the sheet are deleted. Game.location, Result.chips_out,
rebuys and addon are never touched; games that exist only in the database are kept and reported.

Guards (no override): a season whose ``sheet_managed`` is off is refused as a whole, and inside a
managed season a game recorded on the live screens (it has LiveAction rows) or still in progress
(non-empty ``live_stage``) is left as is, whatever the sheet says about that date.
"""

import dataclasses
import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db.models import Exists, OuterRef

from club.models import Game, Player, Result, Season, SeasonKind
from club.stats import suggest_places
from importer.aliases import AmbiguousName, CanonicalPlayer, DbAliasMap
from importer.workbook import ParsedSheet, ParsedWorkbook, clean_name
from live.models import LiveAction

RowKey = tuple[str, int]  # (sheet title, name row)
ADD_ALIAS = "add an alias on the player's admin page or in aliases.yaml"

# Why a sheet date was left alone (SheetChanges.skipped_games).
LIVE_RECORDED = "live recorded"
LIVE_IN_PROGRESS = "live in progress"


class ImportProblem(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass
class SheetChanges:
    title: str
    counts: Counter = field(default_factory=Counter)
    details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The season exists and is not kept in the sheet: nothing of this sheet was imported.
    refused: bool = False
    skipped_games: dict[datetime.date, str] = field(default_factory=dict)  # date -> LIVE_*


@dataclass
class ImportResult:
    sheets: list[SheetChanges]
    players_created: list[str]
    player_by_row: dict[RowKey, Player]
    season_by_sheet: dict[str, Season]
    # What was imported per sheet title: the parsed sheet without its skipped dates.
    # Refused sheets are missing.
    synced_sheets: dict[str, ParsedSheet] = field(default_factory=dict)


@dataclass
class SheetPlan:
    sheet: ParsedSheet  # without the skipped dates
    season: Season | None
    refused: bool = False
    skipped: dict[datetime.date, str] = field(default_factory=dict)


def plan_sheet(sheet: ParsedSheet) -> SheetPlan:
    """Apply the guards to one sheet before anything is resolved or written."""
    season = Season.objects.filter(year=sheet.year, kind=sheet.kind).first()
    if season is None:
        return SheetPlan(sheet, None)
    if not season.sheet_managed:
        return SheetPlan(sheet, season, refused=True)
    sheet_dates = {column.date for column in sheet.games}
    games = Game.objects.filter(season=season, date__in=sheet_dates).annotate(
        recorded=Exists(LiveAction.objects.filter(game=OuterRef("pk")))
    )
    skipped = {}
    for game in games:
        if game.live_stage:
            skipped[game.date] = LIVE_IN_PROGRESS
        elif game.recorded:
            skipped[game.date] = LIVE_RECORDED
    return SheetPlan(_without_dates(sheet, set(skipped)), season, skipped=skipped)


def _without_dates(sheet: ParsedSheet, dates: set[datetime.date]) -> ParsedSheet:
    if not dates:
        return sheet
    players = [
        dataclasses.replace(
            row,
            entries={d: e for d, e in row.entries.items() if d not in dates},
            orphan_net={d: v for d, v in row.orphan_net.items() if d not in dates},
            missing_net=[d for d in row.missing_net if d not in dates],
        )
        for row in sheet.players
    ]
    games = [column for column in sheet.games if column.date not in dates]
    return dataclasses.replace(sheet, games=games, players=players)


def resolve_names(
    sheets: list[ParsedSheet], alias_map: DbAliasMap, *, create_missing: bool
) -> dict[RowKey, Player | CanonicalPlayer]:
    """Player per sheet row (a CanonicalPlayer: to be created). Rows without games may stay
    unresolved.

    Ambiguous names (several players share the name, no explicit alias) are always an error,
    also with ``create_missing``: creating yet another player would be a guess too.
    """
    resolved: dict[RowKey, Player | CanonicalPlayer] = {}
    unknown: dict[str, set[str]] = defaultdict(set)
    ambiguous: dict[str, dict[str, str]] = defaultdict(dict)
    for sheet in sheets:
        for row in sheet.players:
            try:
                canonical = alias_map.resolve(row.raw_name)
            except AmbiguousName as exc:
                if row.entries:
                    ambiguous[sheet.title][row.raw_name] = str(exc)
                continue
            if canonical is None and row.entries:
                if not create_missing:
                    unknown[sheet.title].add(row.raw_name)
                    continue
                canonical = CanonicalPlayer(name=clean_name(row.raw_name))
            if canonical is not None:
                resolved[(sheet.title, row.row)] = canonical
    lines = []
    if ambiguous:
        lines.append(f"Ambiguous player names ({ADD_ALIAS}):")
        for title, messages in ambiguous.items():
            lines += [f"  {title}: {messages[name]}" for name in sorted(messages)]
    if unknown:
        lines.append(f"Unknown player names ({ADD_ALIAS}, or pass --create-missing):")
        for title, names in unknown.items():
            lines += [f"  {title}: {name}" for name in sorted(names)]
    if lines:
        raise ImportProblem(lines)

    # Two raw names for one player in the same game would break result_game_player_unique.
    errors = []
    for sheet in sheets:
        rows_by_game: dict[tuple, list[int]] = defaultdict(list)
        for row in sheet.players:
            for date in row.entries:
                rows_by_game[(resolved[(sheet.title, row.row)], date)].append(row.row)
        for (player, date), rows in sorted(rows_by_game.items(), key=lambda item: item[0][1]):
            if len(rows) > 1:
                errors.append(
                    f"{sheet.title}: rows {', '.join(f'B{r}' for r in rows)} are all {player} "
                    f"and played on {date:%d.%m.%Y}"
                )
    if errors:
        raise ImportProblem(errors)
    return resolved


def import_workbook(parsed: ParsedWorkbook, *, create_missing: bool = False) -> ImportResult:
    """Sync every parsed sheet the guards allow; call inside a transaction (the command does).

    Names resolve through the aliases in the database (importer.aliases.DbAliasMap).
    """
    plans = {sheet.title: plan_sheet(sheet) for sheet in parsed.sheets}
    synced = [plan.sheet for plan in plans.values() if not plan.refused]
    resolved = resolve_names(synced, DbAliasMap(), create_missing=create_missing)

    players: dict[Player | CanonicalPlayer, Player] = {}
    created_players = []
    for found in dict.fromkeys(resolved.values()):
        if isinstance(found, Player):
            players[found] = found
            continue
        player = Player(name=found.name)  # --create-missing: a raw name nobody knows
        player.full_clean()  # generates the slug
        player.save()
        created_players.append(f"{player} [{player.slug}]")
        players[found] = player

    result = ImportResult(
        sheets=[],
        players_created=created_players,
        player_by_row={key: players[canonical] for key, canonical in resolved.items()},
        season_by_sheet={},
    )
    for plan in plans.values():
        if plan.refused:
            result.sheets.append(SheetChanges(plan.sheet.title, refused=True))
            continue
        changes, season = _sync_sheet(plan, result.player_by_row)
        result.sheets.append(changes)
        result.season_by_sheet[plan.sheet.title] = season
        result.synced_sheets[plan.sheet.title] = plan.sheet
    return result


def _sync_sheet(plan: SheetPlan, player_by_row: dict[RowKey, Player]):
    sheet, season = plan.sheet, plan.season
    changes = SheetChanges(sheet.title, skipped_games=plan.skipped)
    if season is None:
        season = Season(year=sheet.year, kind=sheet.kind, sheet_managed=True)
        season.full_clean()
        season.save()
        changes.counts["seasons created"] += 1

    entries_by_date = defaultdict(dict)
    for row in sheet.players:
        for date, entry in row.entries.items():
            entries_by_date[date][player_by_row[(sheet.title, row.row)]] = entry

    for column in sheet.games:
        game = Game.objects.filter(season=season, date=column.date).first()
        if game is None:
            game = Game(season=season, date=column.date)
            game.full_clean()
            game.save()
            changes.counts["games created"] += 1
        else:
            changes.counts["games unchanged"] += 1
        _sync_results(game, season, entries_by_date[column.date], changes)

    sheet_dates = [column.date for column in sheet.games] + list(plan.skipped)
    for game in Game.objects.filter(season=season).exclude(date__in=sheet_dates):
        changes.warnings.append(f"game {game} exists only in the database; left as is")
    return changes, season


def _sync_results(game: Game, season: Season, entries: dict, changes: SheetChanges) -> None:
    if season.kind == SeasonKind.TOUR:
        places = suggest_places({p.pk: e.payout for p, e in entries.items()}, season.paid_places)
    else:
        places = dict.fromkeys((p.pk for p in entries), None)
    existing = {r.player_id: r for r in game.results.select_related("player")}
    where = f"{game.date:%d.%m.%Y}"

    for player, entry in entries.items():
        wanted = {"buyin": entry.buyin, "payout": entry.payout, "place": places[player.pk]}
        result = existing.pop(player.pk, None)
        if result is not None and _backfilled_place(result, wanted["place"], season):
            del wanted["place"]
        if result is None:
            result = Result(game=game, player=player, **wanted)
            result.full_clean()
            result.save()
            changes.counts["results created"] += 1
            continue
        diff = {
            name: (getattr(result, name), value)
            for name, value in wanted.items()
            if getattr(result, name) != value
        }
        if not diff:
            changes.counts["results unchanged"] += 1
            continue
        for name, (_, value) in diff.items():
            setattr(result, name, value)
        result.full_clean()
        result.save()
        changes.counts["results updated"] += 1
        changes.details.append(
            f"{where} {player}: "
            + ", ".join(f"{name} {old} -> {new}" for name, (old, new) in diff.items())
        )

    for result in existing.values():
        changes.details.append(f"{where} {result.player}: deleted (not in the sheet)")
        result.delete()
        changes.counts["results deleted"] += 1


def _backfilled_place(result: Result, suggested: int | None, season: Season) -> bool:
    """A place beyond the paid ones that the sheet cannot know (it only has payouts)."""
    return (
        season.kind == SeasonKind.TOUR
        and suggested is None
        and result.place is not None
        and result.place > season.paid_places
    )
