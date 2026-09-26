"""Map raw spreadsheet names to canonical players (``aliases.yaml``, see aliases.example.yaml).

Resolution order: an explicit alias always wins; otherwise a raw name may match a player's own
``name``. A raw name that matches the name of several players (three "Дима" with different
nicknames) is never guessed: it must be listed as an explicit alias of one of them.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
    def __init__(self, raw_name: str, candidates: list[CanonicalPlayer]):
        self.raw_name, self.candidates = raw_name, candidates
        super().__init__(
            f"{raw_name!r} matches the name of several players: "
            f"{', '.join(map(str, candidates))}; list it under 'aliases' of one of them"
        )


@dataclass
class AliasMap:
    players: list[CanonicalPlayer] = field(default_factory=list)
    by_alias: dict[str, CanonicalPlayer] = field(default_factory=dict)
    by_name: dict[str, list[CanonicalPlayer]] = field(default_factory=dict)

    def candidates(self, raw_name: str) -> list[CanonicalPlayer]:
        """Players the raw name may mean: an explicit alias wins, else every name match."""
        key = normalize(raw_name)
        if key in self.by_alias:
            return [self.by_alias[key]]
        return self.by_name.get(key, [])

    def resolve(self, raw_name: str) -> CanonicalPlayer | None:
        """The one player for ``raw_name``, None if unknown; AmbiguousName, never a guess."""
        candidates = self.candidates(raw_name)
        if len(candidates) > 1:
            raise AmbiguousName(raw_name, candidates)
        return candidates[0] if candidates else None


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
        alias_map.by_name.setdefault(normalize(player.name), []).append(player)
        for alias in dict.fromkeys(normalize(a) for a in aliases):
            owner = alias_map.by_alias.get(alias)
            if owner is not None and owner != player:
                errors.append(f"{where}: alias {alias!r} already belongs to {owner}")
                continue
            alias_map.by_alias[alias] = player

    if errors:
        raise AliasError(errors)
    return alias_map
