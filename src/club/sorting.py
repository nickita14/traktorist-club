"""Sorting of the standings tables from the query string: ``?sort=<key>&dir=asc``.

Pure parsing and URL building; the ordering itself is in club.stats. Anything unknown falls
back to the default (ИТОГ, highest first), never an error.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlencode

# URL key -> annotation of club.stats._player_totals.
FIELDS = {
    "net": "net",
    "games": "games_played",
    "buyin": "buyin_total",
    "payout": "payout_total",
    "itm": "itm",
    "first": "first_places",
    "second": "second_places",
    "third": "third_places",
}
# The sortable columns of each table: a tournament (or all kinds) table and a cash one.
TOUR_KEYS = ("games", "itm", "first", "second", "third", "net")
CASH_KEYS = ("games", "buyin", "payout", "net")
# The row above the table on narrow screens, where the other columns are hidden.
MOBILE_KEYS = {False: ("net", "games", "itm"), True: ("net", "games", "buyin", "payout")}
MOBILE_LABELS = {
    "net": "итог",
    "games": "игры",
    "itm": "ITM",
    "buyin": "закупки",
    "payout": "выплаты",
}

DEFAULT_KEY = "net"


@dataclass(frozen=True)
class Sort:
    key: str = DEFAULT_KEY
    descending: bool = True

    @property
    def field(self) -> str:
        return FIELDS[self.key]

    @property
    def is_default(self) -> bool:
        """ИТОГ, highest first: the only order in which the season leader is marked."""
        return self == Sort()

    @property
    def params(self) -> dict[str, str]:
        """Query parameters of this order; the default needs none."""
        params = {}
        if self.key != DEFAULT_KEY:
            params["sort"] = self.key
        if not self.descending:
            params["dir"] = "asc"
        return params

    @property
    def query(self) -> str:
        """'sort=games&dir=asc', or '' for the default, to append to other links."""
        return urlencode(self.params)

    def toggled(self, key: str) -> "Sort":
        """The order a header link of ``key`` leads to: flip the active column, else start
        with the highest first."""
        if key == self.key:
            return Sort(key, not self.descending)
        return Sort(key)


def parse_sort(params: Mapping[str, str], *, cash: bool) -> Sort:
    keys = CASH_KEYS if cash else TOUR_KEYS
    key = params.get("sort", DEFAULT_KEY)
    if key not in keys:
        return Sort()
    return Sort(key, params.get("dir") != "asc")


def sort_url(path: str, params: Mapping[str, str], sort: Sort) -> str:
    """``path`` with every other parameter of ``params`` (the kind filter) and ``sort``."""
    kept = {name: value for name, value in params.items() if name not in ("sort", "dir")}
    query = urlencode(kept | sort.params)
    return f"{path}?{query}" if query else path
