"""Sortable tables from the query string: ``?sort=<key>&dir=asc``.

Pure parsing and URL building; the ordering itself is in club.stats. Each table (a Table) names
its columns, its default column and which columns start from A (text) instead of the highest
value. Anything unknown falls back to the table's default order, never an error.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode


@dataclass(frozen=True)
class Table:
    # URL key -> the annotation or field club.stats orders by.
    fields: Mapping[str, str]
    default: str
    # Text columns: their first click (and the default) is A to Я, not the highest first.
    ascending_first: frozenset[str] = frozenset()
    # The row above the table on narrow screens: keys in order.
    mobile: tuple[str, ...] = ()

    def start(self, key: str) -> "Sort":
        """The order a column starts with."""
        return Sort(key, key not in self.ascending_first, self)

    @property
    def default_sort(self) -> "Sort":
        return self.start(self.default)


# Annotations of club.stats._player_totals.
_STANDINGS_FIELDS = {
    "net": "net",
    "games": "games_played",
    "buyin": "buyin_total",
    "payout": "payout_total",
    "itm": "itm",
    "first": "first_places",
    "second": "second_places",
    "third": "third_places",
}


def _standings(keys: tuple[str, ...], mobile: tuple[str, ...]) -> Table:
    return Table({key: _STANDINGS_FIELDS[key] for key in keys}, "net", mobile=mobile)


# A tournament (or all kinds) standings table and a cash one.
TOUR_STANDINGS = _standings(
    ("games", "itm", "first", "second", "third", "net"), mobile=("net", "games", "itm")
)
CASH_STANDINGS = _standings(
    ("games", "buyin", "payout", "net"), mobile=("net", "games", "buyin", "payout")
)
# /players/: by name by default.
PLAYERS = Table(
    {"name": "name", "nick": "nickname", "games": "games_played", "net": "net"},
    "name",
    ascending_first=frozenset({"name", "nick"}),
    mobile=("name", "nick", "games", "net"),
)

MOBILE_LABELS = {
    "net": "итог",
    "games": "игры",
    "itm": "ITM",
    "buyin": "закупки",
    "payout": "выплаты",
    "name": "имя",
    "nick": "ник",
}


@dataclass(frozen=True)
class Sort:
    key: str
    descending: bool
    table: Table

    @property
    def field(self) -> str:
        return self.table.fields[self.key]

    @property
    def is_default(self) -> bool:
        """The table's default order (in the standings: ИТОГ, highest first, the only order in
        which the season leader is marked)."""
        return self == self.table.default_sort

    @property
    def params(self) -> dict[str, str]:
        """Query parameters of this order; a column's starting direction needs no ``dir``."""
        params = {}
        if self.key != self.table.default:
            params["sort"] = self.key
        if self.descending != self.table.start(self.key).descending:
            params["dir"] = "desc" if self.descending else "asc"
        return params

    @property
    def query(self) -> str:
        """'sort=games&dir=asc', or '' for the default, to append to other links."""
        return urlencode(self.params)

    def toggled(self, key: str) -> "Sort":
        """The order a header link of ``key`` leads to: flip the active column, else start the
        column in its own direction."""
        if key == self.key:
            return Sort(key, not self.descending, self.table)
        return self.table.start(key)


def parse_sort(params: Mapping[str, str], table: Table) -> Sort:
    key = params.get("sort", table.default)
    if key not in table.fields:
        return table.default_sort
    start = table.start(key)
    direction = params.get("dir")
    if direction in ("asc", "desc"):
        return Sort(key, direction == "desc", table)
    return start


def sort_url(path: str, params: Mapping[str, str], sort: Sort) -> str:
    """``path`` with every other parameter of ``params`` (the kind filter) and ``sort``."""
    kept = {name: value for name, value in params.items() if name not in ("sort", "dir")}
    query = urlencode(kept | sort.params)
    return f"{path}?{query}" if query else path
