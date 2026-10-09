"""Map raw spreadsheet names to players.

The aliases live in the database (``PlayerAlias``, edited on the Player admin page);
``aliases.yaml`` (see aliases.example.yaml) is only a way to load many of them at once
(``upsert_aliases``, run by ``import_sheet --aliases``).

Resolution order: an explicit alias always wins; otherwise a raw name may match a player's own
``name``. A raw name that matches the name of several players (three "Дима" with different
nicknames) is never guessed: it must be an explicit alias of one of them.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from club.models import Player
from importer.models import PlayerAlias


class AliasError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def normalize(name: str) -> str:
    """Matching key: case, repeated spaces and ё/е do not matter."""
    return " ".join(name.split()).casefold().replace("ё", "е")


@dataclass(frozen=True)
class CanonicalPlayer:
    name: str
    nickname: str = ""
    slug: str = ""

    def __str__(self) -> str:
        return f"{self.name} ({self.nickname})" if self.nickname else self.name


class AmbiguousName(Exception):
    def __init__(self, raw_name: str, candidates: list):
        self.raw_name, self.candidates = raw_name, candidates
        super().__init__(
            f"{raw_name!r} matches the name of several players: "
            f"{', '.join(map(str, candidates))}; make it an alias of one of them"
        )


@dataclass
class AliasMap:
    """A validated aliases file. Only loaded into the database (upsert_aliases), never resolved
    against directly: DbAliasMap applies the same rules to the tables."""

    players: list[CanonicalPlayer] = field(default_factory=list)
    by_alias: dict[str, CanonicalPlayer] = field(default_factory=dict)
    # Aliases as written in the file (spaces collapsed), per player: what upsert_aliases stores.
    raw_aliases: dict[CanonicalPlayer, list[str]] = field(default_factory=dict)


def load_aliases(path: str | Path) -> AliasMap:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AliasError([f"{path}: {exc}"]) from exc
    return build_alias_map(data)


def build_alias_map(data) -> AliasMap:
    """Validate the parsed YAML and build the lookup; raise AliasError listing every problem."""
    if not isinstance(data, dict) or not isinstance(data.get("players"), list):
        raise AliasError(["top level must be a mapping with a 'players' list"])

    errors: list[str] = []
    alias_map = AliasMap()
    for index, entry in enumerate(data["players"], start=1):
        where = f"players[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        name, nickname, slug = entry.get("name"), entry.get("nickname", ""), entry.get("slug", "")
        aliases = entry.get("aliases", [])
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{where}: 'name' is required")
            continue
        if not isinstance(nickname, str) or not isinstance(slug, str):
            errors.append(f"{where}: 'nickname' and 'slug' must be text")
            continue
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            errors.append(f"{where}: 'aliases' must be a list of names")
            continue

        player = CanonicalPlayer(" ".join(name.split()), " ".join(nickname.split()), slug.strip())
        if any((p.name, p.nickname) == (player.name, player.nickname) for p in alias_map.players):
            errors.append(f"{where}: duplicate player {player}")
            continue
        alias_map.players.append(player)
        # Several players may share a name (checked when a raw name is resolved), but an
        # explicit alias must point to exactly one player.
        written = {normalize(a): " ".join(a.split()) for a in reversed(aliases)}
        for alias in dict.fromkeys(normalize(a) for a in aliases):
            owner = alias_map.by_alias.get(alias)
            if owner is not None and owner != player:
                errors.append(f"{where}: alias {alias!r} already belongs to {owner}")
                continue
            alias_map.by_alias[alias] = player
            alias_map.raw_aliases.setdefault(player, []).append(written[alias])

    if errors:
        raise AliasError(errors)
    return alias_map


class DbAliasMap:
    """The same lookup over ``PlayerAlias`` and ``Player`` (two queries), resolving to players."""

    def __init__(self):
        players = {player.pk: player for player in Player.objects.all()}
        self.by_name: dict[str, list[Player]] = defaultdict(list)
        for player in players.values():
            self.by_name[normalize(player.name)].append(player)
        # A list, not one player: two aliases that differ only in ё/е could still point to two
        # players (the admin checks it, the database cannot), and that must not be a guess.
        self.by_alias: dict[str, list[Player]] = defaultdict(list)
        for raw_name, player_id in PlayerAlias.objects.values_list("raw_name", "player_id"):
            owners = self.by_alias[normalize(raw_name)]
            if players[player_id] not in owners:
                owners.append(players[player_id])

    def resolve(self, raw_name: str) -> Player | None:
        """The one player for ``raw_name``, None if unknown; AmbiguousName, never a guess."""
        key = normalize(raw_name)
        candidates = self.by_alias.get(key) or self.by_name.get(key, [])
        if len(candidates) > 1:
            raise AmbiguousName(raw_name, candidates)
        return candidates[0] if candidates else None


@dataclass
class AliasUpsert:
    players_created: list[str] = field(default_factory=list)
    aliases_created: list[str] = field(default_factory=list)
    aliases_moved: list[str] = field(default_factory=list)  # "raw: old player -> new player"


def upsert_aliases(alias_map: AliasMap) -> AliasUpsert:
    """Load a validated aliases file into the database; the file wins, nothing is deleted.

    A file player is found by slug when the file gives one (the player may have been renamed in
    the admin since), else by (name, nickname), else created. Call inside a transaction.
    """
    upsert = AliasUpsert()
    existing = {normalize(alias.raw_name): alias for alias in PlayerAlias.objects.all()}
    for canonical in alias_map.players:
        player = find_player(canonical)
        if player is None:
            player = Player(name=canonical.name, nickname=canonical.nickname, slug=canonical.slug)
            player.full_clean()  # generates the slug when the file has none
            player.save()
            upsert.players_created.append(f"{player} [{player.slug}]")
        for raw_name in alias_map.raw_aliases.get(canonical, []):
            alias = existing.get(normalize(raw_name))
            if alias is None:
                existing[normalize(raw_name)] = PlayerAlias.objects.create(
                    raw_name=raw_name, player=player
                )
                upsert.aliases_created.append(f"{raw_name}: {player}")
            elif alias.player_id != player.pk:
                upsert.aliases_moved.append(f"{alias.raw_name}: {alias.player} -> {player}")
                alias.player = player
                alias.save(update_fields=["player"])
    return upsert


def find_player(canonical: CanonicalPlayer) -> Player | None:
    if canonical.slug:
        player = Player.objects.filter(slug=canonical.slug).first()
        if player is not None:
            return player
    return Player.objects.filter(name=canonical.name, nickname=canonical.nickname).first()
