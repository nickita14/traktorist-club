"""Write a parsed workbook to the database. Idempotent: the sheet wins for sheet-derived fields.

Natural keys: Season (year, kind), Player (name, nickname), Game (season, date),
Result (game, player). Buy-in and payout come from the sheet, tour places are recomputed from
payouts (a place beyond the paid ones, backfilled in the admin, is kept while the sheet pays that
player nothing), and results missing from the sheet are deleted. Game.location, Result.chips_out,
rebuys and addon are never touched; games that exist only in the database are kept and reported.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from club.models import Game, Player, Result, Season, SeasonKind
from club.stats import suggest_places
from importer.aliases import AliasMap, AmbiguousName, CanonicalPlayer
from importer.workbook import ParsedSheet, ParsedWorkbook, clean_name

RowKey = tuple[str, int]  # (sheet title, name row)


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


@dataclass
class ImportResult:
    sheets: list[SheetChanges]
    players_created: list[str]
    player_by_row: dict[RowKey, Player]
    season_by_sheet: dict[str, Season]


def resolve_names(
    parsed: ParsedWorkbook, alias_map: AliasMap, *, create_missing: bool
) -> dict[RowKey, CanonicalPlayer]:
    """Canonical player per sheet row. Rows without games may stay unresolved.

    Ambiguous names (several players share the name, no explicit alias) are always an error,
    also with ``create_missing``: creating yet another player would be a guess too.
    """
    resolved: dict[RowKey, CanonicalPlayer] = {}
    unknown: dict[str, set[str]] = defaultdict(set)
    ambiguous: dict[str, dict[str, str]] = defaultdict(dict)
    for sheet in parsed.sheets:
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
        lines.append("Ambiguous player names (add an explicit alias in aliases.yaml):")
        for title, messages in ambiguous.items():
            lines += [f"  {title}: {messages[name]}" for name in sorted(messages)]
    if unknown:
        lines.append("Unknown player names (add them to aliases.yaml or pass --create-missing):")
        for title, names in unknown.items():
            lines += [f"  {title}: {name}" for name in sorted(names)]
    if lines:
        raise ImportProblem(lines)

    # Two raw names for one player in the same game would break result_game_player_unique.
    errors = []
    for sheet in parsed.sheets:
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


def import_workbook(
    parsed: ParsedWorkbook, alias_map: AliasMap, *, create_missing: bool = False
) -> ImportResult:
    """Sync every parsed sheet; call inside a transaction (the command does)."""
    resolved = resolve_names(parsed, alias_map, create_missing=create_missing)

    players: dict[CanonicalPlayer, Player] = {}
    created_players = []
    for canonical in dict.fromkeys(resolved.values()):
        player = Player.objects.filter(name=canonical.name, nickname=canonical.nickname).first()
        if player is None:
            player = Player(name=canonical.name, nickname=canonical.nickname, slug=canonical.slug)
            player.full_clean()  # generates the slug when the YAML has none
            player.save()
            created_players.append(f"{player} [{player.slug}]")
        players[canonical] = player

    result = ImportResult(
        sheets=[],
        players_created=created_players,
        player_by_row={key: players[canonical] for key, canonical in resolved.items()},
        season_by_sheet={},
    )
    for sheet in parsed.sheets:
        changes, season = _sync_sheet(sheet, result.player_by_row)
        result.sheets.append(changes)
        result.season_by_sheet[sheet.title] = season
    return result


def _sync_sheet(sheet: ParsedSheet, player_by_row: dict[RowKey, Player]):
    changes = SheetChanges(sheet.title)
    season = Season.objects.filter(year=sheet.year, kind=sheet.kind).first()
    if season is None:
        season = Season(year=sheet.year, kind=sheet.kind)
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

    sheet_dates = [column.date for column in sheet.games]
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
