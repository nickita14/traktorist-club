"""The year report (форма № 7): one page for the club and one per player, computed for a
calendar year (the season year) from finished games.

load() reads every finished result through achievements.load() (its rows and awards), the
year's seasons and their standings (stats.season_standings), and nothing else: the methods and
properties of what it returns run no queries. The definitions are in PLAN.md Stage 10 and
CLAUDE.md "Year report": an evening is a date with at least one finished game, ties share
every honor (no tiebreaks unless a definition says so), unknown places are never ITM.

``today`` is the computation date: the real local date for a preliminary year, 1 January of the
next year for a closed one (Release.computation_date). The release (preliminary or closed, by
hand or by the 1 January fallback) is decided by release(), close() and reopen() at the end of
this module. Every "today" is the local date (timezone.localdate()), never a UTC date.
"""

import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from django.contrib.admin.models import ADDITION, DELETION, LogEntry
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Min
from django.utils import timezone

from club import achievements, stats
from club.achievements import ClubAwards, GameRef, Kind, ResultRow
from club.formatting import (
    DECIMAL_SEPARATOR,
    EVENINGS_DATIVE,
    format_money,
    format_net,
    ru_plural,
)
from club.models import Game, LadderCode, Player, Season, SeasonKind, YearClosure

# achievements.load(), the year's seasons, at most two season standings.
QUERY_COUNT = achievements.QUERY_COUNT + 3

# Month names in the nominative, lower case, for the missed-months line: "Прогулян только июль".
MONTH_NAMES = [name.lower() for name in achievements.MONTHS]
# One letter per month, under the bars and the player's month grid.
MONTH_LETTERS = [name[0] for name in achievements.MONTHS]

HONORS = {
    "udarnik": "Ударник года",
    "opora": "Опора кассы",
    "patience": "Железное терпение",
    "comeback": "Камбэк года",
    "bubble": "Бабл-гёрл года",
}
# "Бабл-гёрл года" is shown only when its leader has at least this many bubbles.
BUBBLE_MINIMUM = 2
BUBBLE_NOTE = "среди турниров с известными местами"

# Shared ranks name at most this many of the others who share the place.
SHARED_NAMED = 2
# How a shared rank introduces the others. Player.label is a name or a nickname that cannot be
# declined, so the others stay in the nominative: "2–3 место · наравне: Марина и Олег".
SHARED_PREFIX = "наравне"

MONTH_PLAYED = "played"
MONTH_MISSED = "missed"
MONTH_NONE = "none"


def _evenings_text(count: int) -> str:
    return f"{format_money(count)} {ru_plural(count, ('вечер', 'вечера', 'вечеров'))}"


def _percent(part: int, whole: int) -> int:
    """Rounded half up: 38 of 44 -> 86."""
    if not whole:
        return 0
    return int((Decimal(part) * 100 / Decimal(whole)).quantize(Decimal(1), ROUND_HALF_UP))


def format_decimal(value: Fraction) -> str:
    """One decimal with a comma, a whole value without it: 37/5 -> '7,4', 7 -> '7'."""
    rounded = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
        Decimal("0.1"), ROUND_HALF_UP
    )
    whole, tenth = divmod(rounded, 1)
    text = format_money(int(whole))
    return f"{text}{DECIMAL_SEPARATOR}{int(tenth * 10)}" if tenth else text


def _by_label(players) -> list[Player]:
    return sorted(players, key=lambda player: (player.label.casefold(), player.pk))


def _best[K](scores: dict[K, int]) -> tuple[list[K], int | None]:
    """Everyone with the highest score above zero (ties share it), and that score."""
    best = max(scores.values(), default=0)
    if best <= 0:
        return [], None
    return [key for key, score in scores.items() if score == best], best


@dataclass(frozen=True)
class MonthBar:
    month: int
    evenings: int

    @property
    def letter(self) -> str:
        return MONTH_LETTERS[self.month - 1]

    @property
    def name(self) -> str:
        return MONTH_NAMES[self.month - 1]


@dataclass(frozen=True)
class Standing:
    """A row of a season's standings: competition rank, net and games played."""

    player: Player
    rank: int
    net: int
    games: int


@dataclass(frozen=True)
class Honor:
    """An honor of the year: every tied holder, one basis (equal for all of them by definition)
    and an optional note."""

    code: str
    holders: tuple[Player, ...]
    basis: str
    note: str = ""

    @property
    def title(self) -> str:
        return HONORS[self.code]


@dataclass(frozen=True)
class CashEvening:
    player: Player
    game: GameRef
    net: int


@dataclass(frozen=True)
class ClubReport:
    year: int
    # The year of the first finished game in the database: "с <first_year> года".
    first_year: int
    today: datetime.date
    evenings: tuple[datetime.date, ...]
    months: tuple[MonthBar, ...]
    tournaments: int
    cash_games: int
    participants: tuple[Player, ...]
    newcomers: tuple[Player, ...]
    average_players: Fraction
    largest_table: int
    largest_table_times: int
    every_month: tuple[Player, ...]
    podium: tuple[Standing, ...]
    cash_leaders: tuple[Standing, ...]
    best_cash_evenings: tuple[CashEvening, ...]
    cashier_leaders: tuple[Player, ...]
    cashier_count: int
    honors: tuple[Honor, ...]
    last_game: datetime.date | None

    @property
    def evening_count(self) -> int:
        return len(self.evenings)

    @property
    def winners(self) -> tuple[Standing, ...]:
        return tuple(row for row in self.podium if row.rank == 1)

    @property
    def podium_tie(self) -> bool:
        """Two or more players share a place on the podium."""
        ranks = [row.rank for row in self.podium]
        return len(ranks) != len(set(ranks))

    @property
    def games_text(self) -> str:
        """'44 турнира и 38 кэш-игр за 44 вечера'; a kind without games is left out."""
        parts = []
        if self.tournaments:
            forms = ("турнир", "турнира", "турниров")
            parts.append(f"{format_money(self.tournaments)} {ru_plural(self.tournaments, forms)}")
        if self.cash_games:
            forms = ("кэш-игра", "кэш-игры", "кэш-игр")
            parts.append(f"{format_money(self.cash_games)} {ru_plural(self.cash_games, forms)}")
        count = self.evening_count
        evenings = ru_plural(count, ("вечер", "вечера", "вечеров"))
        return f"{' и '.join(parts)} за {format_money(count)} {evenings}"

    @property
    def average_text(self) -> str:
        return format_decimal(self.average_players)

    @property
    def max_month(self) -> int:
        return max((bar.evenings for bar in self.months), default=0)


@dataclass(frozen=True)
class SharedRank:
    """A competition rank as the player page prints it. Display only: ``rank`` is the stored
    competition rank, ``tied`` how many share it (the player included)."""

    rank: int
    tied: int
    others: tuple[Player, ...] = ()
    total: int | None = None

    @property
    def place_text(self) -> str:
        """'2 место', '2–3 место', '4–5 место из 23' (en dash)."""
        place = str(self.rank)
        if self.tied > 1:
            place += f"–{self.rank + self.tied - 1}"
        text = f"{place} место"
        return f"{text} из {format_money(self.total)}" if self.total is not None else text

    @property
    def others_text(self) -> str:
        """'' without a tie; 'Марина', 'Марина и Олег', 'Марина, Олег и ещё 3'."""
        labels = [player.label for player in self.others]
        if not labels:
            return ""
        if len(labels) <= SHARED_NAMED:
            return " и ".join(labels)
        return f"{', '.join(labels[:SHARED_NAMED])} и ещё {len(labels) - SHARED_NAMED}"

    @property
    def text(self) -> str:
        """The whole line: '2 место', '2–3 место · наравне: Марина и Олег'."""
        if not self.others:
            return self.place_text
        return f"{self.place_text} · {SHARED_PREFIX}: {self.others_text}"


@dataclass(frozen=True)
class EveningGame:
    game: GameRef
    players: int
    net: int
    place: int | None
    one_buyin: bool


@dataclass(frozen=True)
class Evening:
    """A player's evening: the sum of their nets over the games of that date."""

    date: datetime.date
    net: int
    games: tuple[EveningGame, ...]

    @property
    def one_buyin(self) -> bool:
        return any(game.one_buyin for game in self.games)


@dataclass(frozen=True)
class KindPlace:
    rank: SharedRank
    net: int
    games: int


@dataclass(frozen=True)
class YearAward:
    """A tile of the player's "Награды за год": a name, a mark (a year, a step, '×4') and why."""

    name: str
    mark: str
    basis: str


@dataclass(frozen=True)
class PlayerReport:
    player: Player
    year: int
    first_year: int
    evenings: int
    club_evenings: int
    attendance: SharedRank
    months: tuple[tuple[MonthBar, str], ...]
    net: int
    tour: KindPlace | None
    cash: KindPlace | None
    tournaments: int
    itm: int
    wins: int
    best_evening: Evening
    worst_evening: Evening | None
    itm_streak: int
    co_players: tuple[Player, ...]
    co_games: int
    awards: tuple[YearAward, ...]
    honors: tuple[Honor, ...]

    @property
    def percent(self) -> int:
        return _percent(self.evenings, self.club_evenings)

    @property
    def itm_text(self) -> str:
        """'11 из 25': tournaments in the money of tournaments played."""
        return f"{format_money(self.itm)} из {format_money(self.tournaments)}"

    @property
    def streak_text(self) -> str:
        return f"{format_money(self.itm_streak)} подряд"

    @property
    def co_text(self) -> str:
        """'Браво, Чарли · 14 игр': the most frequent co-players and their shared games."""
        names = ", ".join(player.label for player in self.co_players)
        forms = ("игра", "игры", "игр")
        return f"{names} · {format_money(self.co_games)} {ru_plural(self.co_games, forms)}"

    @property
    def missed_months(self) -> list[str]:
        return [bar.name for bar, state in self.months if state == MONTH_MISSED]

    @property
    def missed_text(self) -> str:
        """Months with a club evening the player missed (a month without any is never missed)."""
        missed = self.missed_months
        if not missed:
            return "Ни одного прогула"
        if len(missed) == 1:
            return f"Прогулян только {missed[0]}"
        if len(missed) == 2:
            return f"Прогуляны {missed[0]} и {missed[1]}"
        count = len(missed)
        return f"Прогуляно {count} {ru_plural(count, ('месяц', 'месяца', 'месяцев'))}"


@dataclass
class YearReport:
    """The club page and, on request, a player's page. Methods run no queries."""

    club: ClubReport
    awards: ClubAwards
    _rows: list[ResultRow] = field(repr=False)
    _tour: list[Standing] = field(repr=False)
    _cash: list[Standing] = field(repr=False)

    @property
    def year(self) -> int:
        return self.club.year

    def player_ids(self) -> set[int]:
        return {player.pk for player in self.club.participants}

    def player(self, player_id: int) -> PlayerReport | None:
        """The player's page, None for a player without finished games that year."""
        rows = [row for row in self._rows if row.player_id == player_id]
        if not rows:
            return None
        return _player_report(self, player_id, rows)


# Computation.


def _honors(
    year_rows: list[ResultRow], awards: ClubAwards, players: dict[int, Player], year: int
) -> list[Honor]:
    honors = []
    evenings = sorted({row.game.date for row in year_rows})
    holders, best = _best(dict(_attended(year_rows)))
    if holders:
        honors.append(
            Honor(
                "udarnik",
                tuple(_by_label(players[pk] for pk in holders)),
                f"{_evenings_text(best)} из {format_money(len(evenings))}",
            )
        )
    buyins: Counter = Counter()
    for row in year_rows:
        buyins[row.player_id] += row.buyin
    holders, best = _best(dict(buyins))
    if holders:
        honors.append(
            Honor(
                "opora",
                tuple(_by_label(players[pk] for pk in holders)),
                f"закупок на {format_money(best)}, больше всех",
            )
        )
    patience = _patience(year_rows)
    holders, best = _best(patience)
    if holders:
        forms = ("турнир", "турнира", "турниров")
        honors.append(
            Honor(
                "patience",
                tuple(_by_label(players[pk] for pk in holders)),
                f"{format_money(best)} {ru_plural(best, forms)} без призовых, затем в деньгах",
            )
        )
    comeback = _comeback(year_rows)
    if comeback:
        buyin, place, ids = comeback
        honors.append(
            Honor(
                "comeback",
                tuple(_by_label(players[pk] for pk in ids)),
                f"{place} место при закупке {format_money(buyin)}",
            )
        )
    bubbles = Counter(
        award.player_id
        for award in awards.awards
        if award.kind == Kind.BADGE and award.code == "bubble" and award.date.year == year
    )
    holders, best = _best(dict(bubbles))
    if holders and best >= BUBBLE_MINIMUM:
        forms = ("раз", "раза", "раз")
        honors.append(
            Honor(
                "bubble",
                tuple(_by_label(players[pk] for pk in holders)),
                f"{format_money(best)} {ru_plural(best, forms)} место сразу за призовыми",
                BUBBLE_NOTE,
            )
        )
    return honors


def _attendance(rows: list[ResultRow]) -> dict[datetime.date, set[int]]:
    attendance: dict[datetime.date, set[int]] = defaultdict(set)
    for row in rows:
        attendance[row.game.date].add(row.player_id)
    return attendance


def _attended(rows: list[ResultRow]) -> Counter:
    """Evenings attended per player."""
    attended: Counter = Counter()
    for ids in _attendance(rows).values():
        attended.update(ids)
    return attended


def _tour_rows(rows: list[ResultRow]) -> dict[int, list[ResultRow]]:
    by_player: dict[int, list[ResultRow]] = defaultdict(list)
    for row in rows:
        if row.game.kind == SeasonKind.TOUR:
            by_player[row.player_id].append(row)
    return by_player


def _patience(year_rows: list[ResultRow]) -> dict[int, int]:
    """Per player, the longest run of their own tournaments of the year without ITM that ends
    with an ITM in the year. A run counts from the year's first tournament at the earliest; one
    still open at the player's last tournament of the year does not count."""
    longest: dict[int, int] = {}
    for player_id, rows in _tour_rows(year_rows).items():
        run = 0
        for row in rows:
            if row.itm:
                if run:
                    longest[player_id] = max(longest.get(player_id, 0), run)
                run = 0
            else:
                run += 1
    return longest


def _comeback(year_rows: list[ResultRow]) -> tuple[int, int, list[int]] | None:
    """The largest buy-in among ITM results of the year; on an equal buy-in the better place;
    every player with that buy-in and place."""
    itm = [row for row in year_rows if row.itm]
    if not itm:
        return None
    best = min(itm, key=lambda row: (-row.buyin, row.place))
    ids = sorted(
        {row.player_id for row in itm if (row.buyin, row.place) == (best.buyin, best.place)}
    )
    return best.buyin, best.place, ids


def _standings(season: Season | None, players: dict[int, Player]) -> list[Standing]:
    if season is None:
        return []
    return [
        Standing(players[row.pk], row.rank, row.net, row.games_played)
        for row in stats.season_standings(season)
    ]


def _shared(
    standings: list[tuple[int, int]],
    player_id: int,
    players: dict[int, Player],
    total: int | None = None,
) -> SharedRank:
    """The player's SharedRank in a list of (player_id, rank) pairs."""
    rank = next(r for pk, r in standings if pk == player_id)
    others = [players[pk] for pk, r in standings if r == rank and pk != player_id]
    return SharedRank(rank, len(others) + 1, tuple(_by_label(others)), total)


def _competition(scores: dict[int, int]) -> list[tuple[int, int]]:
    """(player_id, rank) by score, highest first; equal scores share a rank, the next skips."""
    ordered = sorted(scores.items(), key=lambda item: -item[1])
    ranked, previous, rank = [], None, 0
    for index, (pk, score) in enumerate(ordered, start=1):
        if score != previous:
            rank, previous = index, score
        ranked.append((pk, rank))
    return ranked


def _evening_of(date: datetime.date, rows: list[ResultRow], game_sizes: Counter) -> Evening:
    games = tuple(
        EveningGame(
            row.game,
            game_sizes[row.game.pk],
            row.net,
            row.place,
            row.game.kind == SeasonKind.TOUR and row.place == 1 and row.buyin == row.entry_price,
        )
        for row in rows
    )
    return Evening(date, sum(row.net for row in rows), games)


def _kind_place(standings: list[Standing], player_id: int, players) -> KindPlace | None:
    mine = next((row for row in standings if row.player.pk == player_id), None)
    if mine is None:
        return None
    pairs = [(row.player.pk, row.rank) for row in standings]
    rank = _shared(pairs, player_id, players, total=len(standings))
    return KindPlace(rank, mine.net, mine.games)


def _player_report(report: YearReport, player_id: int, rows: list[ResultRow]) -> PlayerReport:
    club = report.club
    awards = report.awards
    players = awards.players
    year = club.year
    year_rows = report._rows

    # Attendance and the month grid.
    attended = _attended(year_rows)
    mine_dates = sorted({row.game.date for row in rows})
    club_months = {date.month for date in club.evenings}
    my_months = {date.month for date in mine_dates}
    months = tuple(
        (
            bar,
            MONTH_NONE
            if bar.month not in club_months
            else MONTH_PLAYED
            if bar.month in my_months
            else MONTH_MISSED,
        )
        for bar in club.months
    )

    # Evenings: the sum of the player's nets per date.
    game_sizes = Counter(row.game.pk for row in year_rows)
    by_date: dict[datetime.date, list[ResultRow]] = defaultdict(list)
    for row in rows:
        by_date[row.game.date].append(row)
    evenings = [_evening_of(date, by_date[date], game_sizes) for date in mine_dates]
    # Ties: the earliest evening.
    best = max(evenings, key=lambda evening: (evening.net, -evening.date.toordinal()))
    rest = [evening for evening in evenings if evening.date != best.date]
    worst = min(rest, key=lambda evening: (evening.net, evening.date.toordinal())) if rest else None

    # Tournaments.
    tour_rows = [row for row in rows if row.game.kind == SeasonKind.TOUR]
    streak = longest = 0
    for row in tour_rows:
        streak = streak + 1 if row.itm else 0
        longest = max(longest, streak)

    # Co-players: most shared games of the year.
    my_games = {row.game.pk for row in rows}
    shared = Counter(
        row.player_id for row in year_rows if row.game.pk in my_games and row.player_id != player_id
    )
    co_ids, co_games = _best(dict(shared))

    honors = tuple(honor for honor in club.honors if any(p.pk == player_id for p in honor.holders))
    tour = _kind_place(report._tour, player_id, players)
    return PlayerReport(
        player=players[player_id],
        year=year,
        first_year=club.first_year,
        evenings=len(mine_dates),
        club_evenings=club.evening_count,
        attendance=_shared(_competition(dict(attended)), player_id, players),
        months=months,
        net=sum(row.net for row in rows),
        tour=tour,
        cash=_kind_place(report._cash, player_id, players),
        tournaments=len(tour_rows),
        itm=sum(row.itm for row in tour_rows),
        wins=sum(row.place == 1 for row in tour_rows),
        best_evening=best,
        worst_evening=worst,
        itm_streak=longest,
        co_players=tuple(_by_label(players[pk] for pk in co_ids)),
        co_games=co_games or 0,
        awards=tuple(_year_awards(report, player_id, tour)),
        honors=honors,
    )


def _year_awards(report: YearReport, player_id: int, tour: KindPlace | None) -> list[YearAward]:
    club = report.club
    awards = report.awards
    year = club.year
    tiles = []
    if tour is not None and tour.rank.rank == 1:
        tiles.append(
            YearAward("Победитель сезона", str(year), f"турниры, итог {format_net(tour.net)}")
        )
    summer = [
        result
        for result in awards.titles
        if result.code == "udarnik"
        and result.period.year == year
        and result.period.start.month == 6
        and not result.running
        and player_id in result.holders
    ]
    for result in summer:
        club_summer = sum(1 for date in club.evenings if 6 <= date.month <= 8)
        evenings = int(result.holder_values[result.holders.index(player_id)])
        tiles.append(
            YearAward(
                "Ударник лета",
                str(year),
                f"{_evenings_text(evenings)} из {format_money(club_summer)}",
            )
        )
    steps = [
        award
        for award in awards.awards
        if award.player_id == player_id
        and award.kind == Kind.RANK
        and award.code == LadderCode.VETERAN
        and award.date.year == year
        and not award.is_first_step
    ]
    if steps:
        step = steps[-1]
        tiles.append(
            YearAward(
                "Звание года",
                step.title,
                f"{step.basis}, {step.game.document}, {step.date:%d.%m}"
                f" · счёт с {club.first_year} года",
            )
        )
    counts = Counter(
        award.code
        for award in awards.awards
        if award.player_id == player_id and award.kind == Kind.BADGE and award.date.year == year
    )
    rules = {
        "one_buyin": "победа без докупок",
        "cashier": "лучший итог кэш-игры",
        "bubble": BUBBLE_NOTE,
    }
    for code, basis in rules.items():
        if counts[code]:
            tiles.append(YearAward(achievements.BADGES[code], f"×{counts[code]}", basis))
    return tiles


def history_start() -> int | None:
    """The year of the first finished game in the database, for pages without the awards. One
    query."""
    return Game.objects.finished().aggregate(first=Min("season__year"))["first"]


def first_year(awards: ClubAwards) -> int | None:
    """The year of the first finished game in the database."""
    return awards.rows[0].year if awards.rows else None


def load(year: int, today: datetime.date | None = None) -> YearReport | None:
    """The report for ``year`` as of ``today`` (default the local date), or None when there is
    none: no finished game that year, or the first year of history (no baseline for newcomers).
    QUERY_COUNT queries at most."""
    awards = achievements.load(today or timezone.localdate())
    start = first_year(awards)
    rows = [row for row in awards.rows if row.year == year]
    if not rows or start is None or year <= start:
        return None
    players = awards.players
    seasons = {season.kind: season for season in Season.objects.filter(year=year)}
    tour = _standings(
        seasons.get(SeasonKind.TOUR) if _has(rows, SeasonKind.TOUR) else None, players
    )
    cash = _standings(
        seasons.get(SeasonKind.CASH) if _has(rows, SeasonKind.CASH) else None, players
    )

    evenings = tuple(sorted({row.game.date for row in rows}))
    per_month = Counter(date.month for date in evenings)
    games = {row.game.pk: row.game for row in rows}
    sizes = Counter(row.game.pk for row in rows)
    largest = max(sizes.values())
    participants = {row.player_id for row in rows}
    earlier = {row.player_id for row in awards.rows if row.year < year}
    months_with = set(per_month)
    months_of: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        months_of[row.player_id].add(row.game.date.month)

    cash_rows = [row for row in rows if row.game.kind == SeasonKind.CASH]
    best_net = max((row.net for row in cash_rows), default=0)
    best_cash = (
        sorted(
            (
                CashEvening(players[row.player_id], row.game, row.net)
                for row in cash_rows
                if row.net == best_net
            ),
            key=lambda item: (item.game.date, item.player.label.casefold()),
        )
        if best_net > 0
        else []
    )
    cashiers = Counter(
        award.player_id
        for award in awards.awards
        if award.kind == Kind.BADGE and award.code == "cashier" and award.date.year == year
    )
    cashier_ids, cashier_count = _best(dict(cashiers))

    club = ClubReport(
        year=year,
        first_year=start,
        today=awards.today,
        evenings=evenings,
        months=tuple(MonthBar(month, per_month[month]) for month in range(1, 13)),
        tournaments=sum(1 for game in games.values() if game.kind == SeasonKind.TOUR),
        cash_games=sum(1 for game in games.values() if game.kind == SeasonKind.CASH),
        participants=tuple(_by_label(players[pk] for pk in participants)),
        newcomers=tuple(_by_label(players[pk] for pk in participants - earlier)),
        average_players=Fraction(len(rows), len(games)),
        largest_table=largest,
        largest_table_times=sum(1 for size in sizes.values() if size == largest),
        every_month=tuple(
            _by_label(players[pk] for pk in participants if months_of[pk] >= months_with)
        ),
        podium=tuple(row for row in tour if row.rank <= 3),
        cash_leaders=tuple(row for row in cash if row.rank == 1),
        best_cash_evenings=tuple(best_cash),
        cashier_leaders=tuple(_by_label(players[pk] for pk in cashier_ids)),
        cashier_count=cashier_count or 0,
        honors=tuple(_honors(rows, awards, players, year)),
        last_game=evenings[-1],
    )
    return YearReport(club, awards, rows, tour, cash)


def _has(rows: list[ResultRow], kind: str) -> bool:
    return any(row.game.kind == kind for row in rows)


# Release.


class ReleaseError(Exception):
    """A close or reopen the rules refuse; the message is for the organizer."""


def _new_year(year: int) -> datetime.date:
    return datetime.date(year + 1, 1, 1)


@dataclass(frozen=True)
class Release:
    """A year's release state as of ``today`` (a local date)."""

    year: int
    today: datetime.date
    closure: YearClosure | None

    @property
    def closed_by_hand(self) -> bool:
        return self.closure is not None

    @property
    def fallback(self) -> bool:
        """Closed by the calendar: nobody closed it and 1 January of the next year has come."""
        return self.closure is None and self.today >= _new_year(self.year)

    @property
    def closed(self) -> bool:
        return self.closed_by_hand or self.today >= _new_year(self.year)

    @property
    def can_reopen(self) -> bool:
        """Only a year closed by hand, and only before 1 January of the next year."""
        return self.closed_by_hand and self.today < _new_year(self.year)

    @property
    def computation_date(self) -> datetime.date:
        """1 January of the next year for a closed year, whatever the real date; the real date
        for a preliminary one."""
        return _new_year(self.year) if self.closed else self.today

    def stamp_text(self, evenings: int) -> str:
        """The preliminary stamp's line: "на 16.11, по 41 вечеру", "по 44 вечерам"."""
        noun = ru_plural(evenings, EVENINGS_DATIVE)
        return f"на {self.today:%d.%m}, по {format_money(evenings)} {noun}"


def release(year: int, today: datetime.date | None = None) -> Release:
    """One query."""
    today = today or timezone.localdate()
    return Release(year, today, YearClosure.objects.filter(year=year).first())


def is_closed(year: int, today: datetime.date | None = None) -> bool:
    """A year past its 31 December needs no query: the fallback has closed it."""
    today = today or timezone.localdate()
    if today >= _new_year(year):
        return True
    return YearClosure.objects.filter(year=year).exists()


@dataclass(frozen=True)
class YearSummary:
    """A row of the admin's "Годовые отчёты" and the strip of its confirmation page."""

    year: int
    evenings: int
    last_game: datetime.date
    participants: int
    release: Release
    # The first year of history has no report (no baseline for newcomers).
    first: bool

    @property
    def can_close(self) -> bool:
        return not self.first and not self.release.closed and self.year <= self.release.today.year


def summaries(today: datetime.date | None = None) -> list[YearSummary]:
    """Every year with finished games, newest first. Two queries."""
    today = today or timezone.localdate()
    years = list(
        Game.objects.finished()
        .values("season__year")
        .annotate(
            evenings=Count("date", distinct=True),
            last_game=Max("date"),
            participants=Count("results__player", distinct=True),
        )
        .order_by("-season__year")
    )
    closures = {row.year: row for row in YearClosure.objects.select_related("closed_by")}
    first = min((row["season__year"] for row in years), default=None)
    return [
        YearSummary(
            year=row["season__year"],
            evenings=row["evenings"],
            last_game=row["last_game"],
            participants=row["participants"],
            release=Release(row["season__year"], today, closures.get(row["season__year"])),
            first=row["season__year"] == first,
        )
        for row in years
    ]


def summary(year: int, today: datetime.date | None = None) -> YearSummary | None:
    return next((row for row in summaries(today) if row.year == year), None)


def close(year: int, user, today: datetime.date | None = None) -> YearClosure:
    """Close ``year`` by hand: a year with finished games, not the first year of history, not a
    future year, not already closed. Logged as an addition."""
    today = today or timezone.localdate()
    with transaction.atomic():
        found = summary(year, today)
        if found is None:
            raise ReleaseError(f"В {year} году нет сыгранных игр: закрывать нечего.")
        if found.first:
            raise ReleaseError(f"{year}: первый год в базе, отчёта за него нет.")
        if year > today.year:
            raise ReleaseError(f"Сезон {year} ещё не начался.")
        if found.release.closed:
            raise ReleaseError(f"Сезон {year} уже закрыт.")
        try:
            with transaction.atomic():
                closure = YearClosure.objects.create(year=year, closed_by=user)
        except IntegrityError:
            raise ReleaseError(f"Сезон {year} уже закрыт.") from None
        LogEntry.objects.log_actions(
            user.pk,
            [closure],
            ADDITION,
            change_message=f"Сезон {year} закрыт: годовой отчёт открыт всем.",
            single_object=True,
        )
    return closure


def reopen(year: int, user, today: datetime.date | None = None) -> None:
    """Reopen a year closed by hand, before 1 January of the next year. Logged as a deletion."""
    today = today or timezone.localdate()
    with transaction.atomic():
        state = release(year, today)
        closure = state.closure
        if closure is None:
            if state.fallback:
                raise ReleaseError(f"Сезон {year} закрылся 1 января: открыть снова нельзя.")
            raise ReleaseError(f"Сезон {year} не закрыт.")
        if not state.can_reopen:
            raise ReleaseError(f"После 1 января сезон {year} открыть снова нельзя.")
        LogEntry.objects.log_actions(
            user.pk,
            [closure],
            DELETION,
            change_message=f"Сезон {year} открыт снова: отчёт снова предварительный.",
            single_object=True,
        )
        closure.delete()
