"""Club achievements: ranks, badges, diplomas and titles, computed from finished games.

No award is stored. The rules are (RankLadder and RankStep, AchievementSettings); everything
else follows from the results. load() reads the rules, every finished result and the players in
QUERY_COUNT queries whatever the club's size, then works out streaks and titles in Python, so
views and the admin only call the methods of the returned ClubAwards.

A game recorded at the table counts nowhere until its results are saved (Game.live_stage empty
again), as in club.stats. Unknown values never count: a NULL add-on is not an add-on, a NULL
place is not ITM (and not a bubble), a NULL chips_out leaves the result out of the pot.
"""

import calendar
import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction

from django.utils import timezone

from club.formatting import format_amount, format_money, format_net, ru_plural
from club.models import AchievementSettings, LadderCode, Player, RankStep, Result, SeasonKind
from club.stats import chips_value, game_number

# settings row, ladder steps, finished results, players.
QUERY_COUNT = 4


class Kind(StrEnum):
    RANK = "rank"
    BADGE = "badge"
    DIPLOMA = "diploma"
    TITLE = "title"


KIND_LABELS = {
    Kind.RANK: "Звание",
    Kind.BADGE: "Знак",
    Kind.DIPLOMA: "Грамота",
    Kind.TITLE: "Титул",
}

# Per ladder code: the counter's name, its unit (one, few, many) and what a rank's basis adds
# after the threshold ("5 000 лей закупок").
LADDER_COUNTERS = {
    LadderCode.VETERAN: ("Игры", ("игра", "игры", "игр"), ""),
    LadderCode.FEEDER: ("Закупки", ("лей", "лея", "лей"), " закупок"),
    LadderCode.ADDON: ("Аддоны", ("аддон", "аддона", "аддонов"), ""),
}


# Repeatable, each with the game that earned it. The order is the display order.
BADGES = {
    "bubble": "Бабл-гёрл",
    "cashier": "Снял кассу",
    "one_buyin": "С одной закупки",
    "comeback": "Камбэк",
    "hat_trick": "Хет-трик",
    "no_skip": "Ни одного прогула",
}

FIRST_WIN = "first_win"
FIRST_WIN_TITLE = "Первая победа"

TITLES = {
    "udarnik": "Ударник сезона",
    "always_itm": "Всегда в деньгах",
    "patron": "Меценат",
}

MONTHS = [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]


def itm_series_title(length: int) -> str:
    return f"Серия: {length} {ru_plural(length, ('призовой', 'призовых', 'призовых'))} подряд"


def evening_series_title(length: int) -> str:
    return f"Серия: {length} {ru_plural(length, ('вечер', 'вечера', 'вечеров'))} подряд"


def _count(value: int, forms: tuple[str, str, str]) -> str:
    return f"{format_money(value)} {ru_plural(value, forms)}"


def _lei(value: int | Fraction) -> str:
    """'10 лей', '2 лея', '12,30 лея' (a fractional amount takes the genitive singular)."""
    if isinstance(value, Fraction) and value.denominator != 1:
        return f"{format_amount(value)} лея"
    return _count(int(value), ("лей", "лея", "лей"))


def _since(date: datetime.date) -> str:
    return f"подряд с {date:%d.%m.%Y}"


@dataclass(frozen=True)
class GameRef:
    pk: int
    date: datetime.date
    kind: str
    # The game's number in its season, as on the rest of the site (stats.game_number).
    number: int = 0

    @property
    def document(self) -> str:
        """How award lists name the game: "Турнир № 34", "Вечер № 12"."""
        noun = "Турнир" if self.kind == SeasonKind.TOUR else "Вечер"
        return f"{noun} №\u00a0{self.number}"


@dataclass(frozen=True)
class Step:
    threshold: int
    title: str


@dataclass(frozen=True)
class Ladder:
    code: str
    title: str
    steps: tuple[Step, ...]

    @property
    def counter_label(self) -> str:
        return LADDER_COUNTERS[self.code][0]

    @property
    def unit_forms(self) -> str:
        """For the ``plural`` template filter: "игра,игры,игр"."""
        return ",".join(LADDER_COUNTERS[self.code][1])

    def counter_text(self, value: int) -> str:
        """'86 игр', '7 400 лей', '11 аддонов'."""
        return _count(value, LADDER_COUNTERS[self.code][1])


@dataclass(frozen=True)
class Period:
    """A title's period. ``year`` is the year it is filed under (a winter goes under the year
    it ends); ``season_kind`` is set for the tour and cash seasons, None for calendar periods."""

    code: str
    label: str
    year: int
    start: datetime.date
    end: datetime.date
    season_kind: str | None = None
    # A meteorological season in the genitive, for the running-title line: "осени 2026".
    genitive: str = ""


@dataclass(frozen=True)
class Award:
    """One award to one player. ``game`` is the game that earned it (None for a title, which is
    dated on its period's last day); ``ladder`` is set for a rank step."""

    kind: Kind
    code: str
    title: str
    player_id: int
    date: datetime.date
    game: GameRef | None = None
    period: Period | None = None
    ladder: Ladder | None = None
    # The rank step reached (rank awards only).
    step: Step | None = None
    # Why it was given, in a few words: "50 игр", "подряд с 01.03.2025", "13 вечеров".
    basis: str = ""
    player: Player | None = field(default=None, compare=False, repr=False)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def is_first_step(self) -> bool:
        """The lowest step of a ladder: reached by almost everyone at once, so it is shown in the
        rank table and cells but is not an event (recent awards, the record, a game's list)."""
        return self.kind == Kind.RANK and self.step == self.ladder.steps[0]

    @property
    def document(self) -> str:
        """The game that earned it, or the title's period."""
        if self.game is not None:
            return self.game.document
        return self.period.label if self.period else ""


@dataclass(frozen=True)
class RankProgress:
    """A player's place on one ladder: the current step (None below the first threshold), the
    next one (None at the top) and every step reached, oldest first."""

    ladder: Ladder
    value: int
    step: Step | None
    next_step: Step | None
    reached: tuple[Award, ...]

    @property
    def remaining(self) -> int | None:
        return None if self.next_step is None else self.next_step.threshold - self.value

    @property
    def progress_value(self) -> int:
        """Progress from the current step to the next, for <progress value max>."""
        base = self.step.threshold if self.step else 0
        return self.value - base

    @property
    def progress_max(self) -> int:
        base = self.step.threshold if self.step else 0
        return (self.next_step.threshold if self.next_step else self.value) - base

    @property
    def step_number(self) -> int:
        """1 for the first step, 0 below it."""
        return self.ladder.steps.index(self.step) + 1 if self.step else 0

    @property
    def step_count(self) -> int:
        return len(self.ladder.steps)

    @property
    def step_states(self) -> list[tuple[Step, str]]:
        """Every step of the ladder: "passed", "current" or "future"."""
        return [
            (
                step,
                "current"
                if step == self.step
                else "passed"
                if index < self.step_number
                else "future",
            )
            for index, step in enumerate(self.ladder.steps, start=1)
        ]

    @property
    def value_text(self) -> str:
        return self.ladder.counter_text(self.value)


@dataclass(frozen=True)
class BadgeCount:
    code: str
    title: str
    count: int
    last: Award | None
    rule: str = ""


@dataclass(frozen=True)
class TitleResult:
    """A title over one period.

    ``holders``: the winners once the period has ended, the current leaders while it runs
    (``running``); ties share it. Empty holders mean "претендентов нет", unless ``no_data``:
    nothing in the period can decide it (a cash season without a single known chip stack).
    ``value`` is the winning figure (evenings, tournaments, pot in lei as a Fraction);
    ``holder_values`` is each holder's own figure, in the order of ``holders`` (they differ only
    for "Всегда в деньгах", where holders may have played a different number of tournaments).
    """

    code: str
    title: str
    period: Period
    running: bool
    holders: tuple[int, ...] = ()
    value: int | Fraction | None = None
    no_data: bool = False
    holder_values: tuple[int | Fraction, ...] = ()

    def metric_of(self, value: int | Fraction) -> str:
        """The figure as the honors board prints it: "13 вечеров", "4 из 4", "12,30 лея"."""
        if self.code == "udarnik":
            return _count(int(value), ("вечер", "вечера", "вечеров"))
        if self.code == "patron":
            return _lei(value)
        return f"{format_money(int(value))} из {format_money(int(value))}"

    @property
    def contest(self) -> bool:
        """A title with one winner by definition (most evenings, largest pot): several holders
        are a tie. The others are a bar several players can clear, so no tie."""
        return self.code in ("udarnik", "patron")

    @property
    def running_note(self) -> str:
        """The honors board's note after the holders of a running period."""
        return "лидирует" if self.contest else "пока без промахов"

    def holder_metric(self, player_id: int) -> str:
        return self.metric_of(self.holder_values[self.holders.index(player_id)])

    @property
    def shared_metric(self) -> str | None:
        """The figure when every holder has the same one; None without holders or when they
        differ (each holder's own figure is shown instead)."""
        if not self.holder_values or len(set(self.holder_values)) > 1:
            return None
        return self.metric_of(self.holder_values[0])

    def lead_text(self, player_id: int) -> str:
        """The player card's line for a running title the player leads."""
        metric = self.holder_metric(player_id)
        if self.code == "udarnik":
            return f"Лидирует в «Ударнике {self.period.genitive}»: {metric}."
        if self.code == "always_itm":
            return f"Всегда в деньгах в турнирах {self.period.year}: пока {metric}."
        return f"Лидирует в «Меценате» кэша {self.period.year}: {metric}."


@dataclass(frozen=True)
class PlayerAwards:
    ranks: list[RankProgress]
    badges: list[BadgeCount]
    diplomas: list[Award]
    titles: list[Award]
    leading: list[TitleResult]
    # Every badge in display order, earned or not (count 0, last None), with its rule.
    badge_slots: list[BadgeCount] = field(default_factory=list)
    # Diplomas, ended titles and every rank step reached, newest first.
    record: list[Award] = field(default_factory=list)
    # One line per running title the player leads (TitleResult.lead_text).
    lead_lines: list[str] = field(default_factory=list)

    @property
    def badge_total(self) -> int:
        return sum(badge.count for badge in self.badges)

    @property
    def veteran(self) -> RankProgress | None:
        """The current step on the veteran ladder, for the card's stamp; None below it."""
        return next((r for r in self.ranks if r.ladder.code == LadderCode.VETERAN and r.step), None)


@dataclass(frozen=True)
class GameAwards:
    badges: dict[int, list[Award]]
    reached: list[Award]


@dataclass(frozen=True)
class HonorTitle:
    """A row of the honors board's title table: the result with its holders as players, each
    with their own figure."""

    result: TitleResult
    holders: list[tuple[Player, str]]

    @property
    def tie(self) -> bool:
        return self.result.contest and not self.result.running and len(self.holders) > 1


@dataclass(frozen=True)
class BadgeRow:
    """A row of the badge matrix: counts in the order of BADGES."""

    player: Player
    counts: list[int]

    @property
    def total(self) -> int:
        return sum(self.counts)


@dataclass(frozen=True)
class HonorsBoard:
    """Titles filed under one year (plus every running title on the default page); the badge
    matrix and the rank table are all-time, and so are the totals."""

    year: int
    titles: list[TitleResult]
    badge_codes: list[str]
    badge_matrix: list[tuple[Player, dict[str, int]]]
    rank_table: list[tuple[Player, list[RankProgress]]]
    title_rows: list[HonorTitle] = field(default_factory=list)
    badge_rows: list[BadgeRow] = field(default_factory=list)
    participants: int = 0
    badge_total: int = 0
    diploma_title_total: int = 0


@dataclass(frozen=True)
class Completeness:
    tournaments: int
    tournaments_all_places: int
    tour_results: int
    rebuys_known: int
    addon_known: int


@dataclass(frozen=True)
class _Row:
    player_id: int
    game: GameRef
    year: int
    paid_places: int
    entry_price: int
    rebuy_price: int
    chips_per_lei: int
    buyin: int
    payout: int
    place: int | None
    chips_out: int | None
    rebuys: int | None
    addon: bool | None

    @property
    def itm(self) -> bool:
        return self.place is not None and self.place <= self.paid_places

    @property
    def net(self) -> int:
        return self.payout - self.buyin


def _season_period(date: datetime.date) -> Period:
    """The meteorological season of ``date``; winter is filed under the year it ends."""
    year, month = date.year, date.month
    if month in (3, 4, 5):
        start, end, label, genitive = (year, 3), (year, 5), f"Весна {year}", f"весны {year}"
    elif month in (6, 7, 8):
        start, end, label, genitive = (year, 6), (year, 8), f"Лето {year}", f"лета {year}"
    elif month in (9, 10, 11):
        start, end, label, genitive = (year, 9), (year, 11), f"Осень {year}", f"осени {year}"
    else:
        ends = year + 1 if month == 12 else year
        years = f"{ends - 1}/{ends % 100:02d}"
        start, end, label, genitive = (ends - 1, 12), (ends, 2), f"Зима {years}", f"зимы {years}"
    return Period(
        code="udarnik",
        label=label,
        year=end[0],
        start=datetime.date(*start, 1),
        end=_month_end(*end),
        genitive=genitive,
    )


def _month_end(year: int, month: int) -> datetime.date:
    return datetime.date(year, month, calendar.monthrange(year, month)[1])


def _month_period(date: datetime.date) -> Period:
    return Period(
        code="no_skip",
        label=f"{MONTHS[date.month - 1]} {date.year}",
        year=date.year,
        start=date.replace(day=1),
        end=_month_end(date.year, date.month),
    )


def _year_period(code: str, year: int, kind: str) -> Period:
    label = f"Турниры {year}" if kind == SeasonKind.TOUR else f"Кэш {year}"
    return Period(
        code=code,
        label=label,
        year=year,
        start=datetime.date(year, 1, 1),
        end=datetime.date(year, 12, 31),
        season_kind=kind,
    )


def _leaders[K](scores: dict[K, int | Fraction]) -> tuple[list[K], int | Fraction | None]:
    """Everyone with the best score, if it is above zero (ties share it), and that score."""
    best = max(scores.values(), default=0)
    if best <= 0:
        return [], None
    return [key for key, score in scores.items() if score == best], best


class ClubAwards:
    """Every award of the club, computed once by load(). Methods run no queries."""

    def __init__(
        self,
        *,
        rows: list[_Row],
        players: dict[int, Player],
        ladders: list[Ladder],
        settings: AchievementSettings,
        today: datetime.date,
    ):
        self.players = players
        self.ladders = ladders
        self.settings = settings
        self.today = today
        self._rows = rows
        self.awards: list[Award] = []
        self._order: dict[int, int] = {}  # game pk -> chronological index
        self._counters: dict[str, dict[int, int]] = {code: {} for code in LadderCode.values}
        self._reached: dict[tuple[int, str], list[Award]] = defaultdict(list)
        for row in rows:
            self._order.setdefault(row.game.pk, len(self._order))
        # Club evenings: dates with at least one finished game, oldest first.
        self.evenings = sorted({row.game.date for row in rows})
        self._badges()
        self._streaks()
        self._evenings()
        self._no_skip()
        self._ranks()
        self.titles = self._titles()
        for result in self.titles:
            if not result.running:
                self.awards += [
                    Award(
                        Kind.TITLE,
                        result.code,
                        result.title,
                        pk,
                        result.period.end,
                        None,
                        result.period,
                        basis=result.holder_metric(pk),
                        player=self.players.get(pk),
                    )
                    for pk in result.holders
                ]
        self.awards.sort(key=self._sort_key)

    def _sort_key(self, award: Award) -> tuple:
        # Oldest first; on one day games in their order, then titles.
        order = self._order[award.game.pk] if award.game else len(self._order)
        return (award.date, order, list(Kind).index(award.kind), award.player_id)

    # Computation.

    def _games(self) -> list[tuple[GameRef, list[_Row]]]:
        games: dict[int, tuple[GameRef, list[_Row]]] = {}
        for row in self._rows:
            games.setdefault(row.game.pk, (row.game, []))[1].append(row)
        return list(games.values())

    def _award(self, kind: Kind, code: str, title: str, row: _Row, basis: str, **extra) -> None:
        self.awards.append(
            Award(
                kind,
                code,
                title,
                row.player_id,
                row.game.date,
                row.game,
                basis=basis,
                player=self.players.get(row.player_id),
                **extra,
            )
        )

    def _badges(self) -> None:
        extra_buys = self.settings.comeback_min_rebuys
        for game, rows in self._games():
            if game.kind == SeasonKind.TOUR:
                for row in rows:
                    if row.place == row.paid_places + 1:
                        paid = ru_plural(row.paid_places, ("призовом", "призовых", "призовых"))
                        basis = f"{row.place} место при {row.paid_places} {paid}"
                        self._award(Kind.BADGE, "bubble", BADGES["bubble"], row, basis)
                    if row.place == 1 and row.buyin == row.entry_price:
                        basis = "1 место без докупок"
                        self._award(Kind.BADGE, "one_buyin", BADGES["one_buyin"], row, basis)
                    if row.itm and row.buyin >= row.entry_price + extra_buys * row.rebuy_price:
                        basis = f"{row.place} место, закупка {_lei(row.buyin)}"
                        self._award(Kind.BADGE, "comeback", BADGES["comeback"], row, basis)
            else:
                winners, _ = _leaders({row: row.net for row in rows})
                for row in winners:
                    basis = f"лучший итог вечера, {format_net(row.net)}"
                    self._award(Kind.BADGE, "cashier", BADGES["cashier"], row, basis)

    def _streaks(self) -> None:
        """Hat-tricks, ITM series and the first win, over each player's own tournaments: a
        tournament they skipped does not break a streak, one outside the money does."""
        length = self.settings.hat_trick_length
        series = self.settings.itm_series
        by_player: dict[int, list[_Row]] = defaultdict(list)
        for row in self._rows:
            if row.game.kind == SeasonKind.TOUR:
                by_player[row.player_id].append(row)
        for rows in by_player.values():
            streak, won, done, start = 0, False, set(), None
            for row in rows:
                if row.place == 1 and not won:
                    won = True
                    self._award(Kind.DIPLOMA, FIRST_WIN, FIRST_WIN_TITLE, row, "1 место в турнире")
                streak = streak + 1 if row.itm else 0
                if streak == 1:
                    start = row.game.date
                if streak == length:
                    basis = f"в деньгах {_since(start)}"
                    self._award(Kind.BADGE, "hat_trick", BADGES["hat_trick"], row, basis)
                if streak in series and streak not in done:
                    done.add(streak)
                    code, title = f"itm_series_{streak}", itm_series_title(streak)
                    self._award(Kind.DIPLOMA, code, title, row, f"в деньгах {_since(start)}")

    def _evenings(self) -> None:
        """Evening series: club evenings in a row; a missed one breaks it. The earning game is
        the player's first game that evening (the tournament when there were both)."""
        series = self.settings.evening_series
        first_game: dict[tuple[int, datetime.date], _Row] = {}
        for row in self._rows:
            first_game.setdefault((row.player_id, row.game.date), row)
        for player_id in {row.player_id for row in self._rows}:
            streak, done, start = 0, set(), None
            for date in self.evenings:
                row = first_game.get((player_id, date))
                if row is None:
                    streak = 0
                    continue
                streak += 1
                if streak == 1:
                    start = date
                if streak in series and streak not in done:
                    done.add(streak)
                    code, title = f"evening_series_{streak}", evening_series_title(streak)
                    self._award(Kind.DIPLOMA, code, title, row, f"на вечерах {_since(start)}")

    def _ranks(self) -> None:
        increments = {
            LadderCode.VETERAN: lambda row: 1,
            LadderCode.FEEDER: lambda row: row.buyin,
            LadderCode.ADDON: lambda row: 1 if row.addon is True else 0,
        }
        ladders = {ladder.code: ladder for ladder in self.ladders}
        for row in self._rows:
            for code, increment in increments.items():
                counters = self._counters[code]
                before = counters.get(row.player_id, 0)
                after = counters[row.player_id] = before + increment(row)
                ladder = ladders.get(code)
                if ladder is None:
                    continue
                for step in ladder.steps:
                    if before < step.threshold <= after:
                        basis = ladder.counter_text(step.threshold) + LADDER_COUNTERS[code][2]
                        self._award(
                            Kind.RANK, code, step.title, row, basis, ladder=ladder, step=step
                        )
                        self._reached[(row.player_id, code)].append(self.awards[-1])

    def _titles(self) -> list[TitleResult]:
        results = []
        results += self._udarnik()
        results += self._always_itm()
        results += self._patron()
        return sorted(results, key=lambda r: (r.period.start, list(TITLES).index(r.code)))

    def _running(self, period: Period) -> bool:
        return self.today <= period.end

    def _attendance(self) -> dict[datetime.date, set[int]]:
        players: dict[datetime.date, set[int]] = defaultdict(set)
        for row in self._rows:
            players[row.game.date].add(row.player_id)
        return players

    def _udarnik(self) -> list[TitleResult]:
        attendance = self._attendance()
        counts: dict[Period, Counter] = defaultdict(Counter)
        for date in self.evenings:
            counts[_season_period(date)].update(attendance[date])
        results = []
        for period, count in counts.items():
            holders, best = _leaders(dict(count))
            results.append(
                TitleResult(
                    "udarnik",
                    TITLES["udarnik"],
                    period,
                    self._running(period),
                    tuple(sorted(holders)),
                    best,
                    holder_values=(best,) * len(holders),
                )
            )
        return results

    def _no_skip(self) -> None:
        """The badge «Ни одного прогула»: every club evening of an ended month with at least the
        minimum of them. Dated on the month's last day, linked to the player's last game that
        month."""
        attendance = self._attendance()
        months: dict[Period, list[datetime.date]] = defaultdict(list)
        for date in self.evenings:
            months[_month_period(date)].append(date)
        last_game: dict[tuple[int, Period], _Row] = {}
        for row in self._rows:  # oldest first: the last one seen is the month's last game
            last_game[(row.player_id, _month_period(row.game.date))] = row
        for period, dates in months.items():
            if self._running(period) or len(dates) < self.settings.no_skip_min_evenings:
                continue
            count = len(dates)
            evenings = ru_plural(count, ("вечера", "вечеров", "вечеров"))
            month = period.label[0].lower() + period.label[1:]
            basis = f"{count} из {count} {evenings}, {month}"
            for player_id in sorted(set.intersection(*(attendance[date] for date in dates))):
                row = last_game[(player_id, period)]
                self.awards.append(
                    Award(
                        Kind.BADGE,
                        "no_skip",
                        BADGES["no_skip"],
                        player_id,
                        period.end,
                        row.game,
                        basis=basis,
                        player=self.players.get(player_id),
                    )
                )

    def _by_season(self, kind: str) -> dict[int, list[_Row]]:
        seasons: dict[int, list[_Row]] = defaultdict(list)
        for row in self._rows:
            if row.game.kind == kind:
                seasons[row.year].append(row)
        return seasons

    def _always_itm(self) -> list[TitleResult]:
        """ITM in every tournament played, at least the minimum of them (while the season runs
        too: a contender needs as many tournaments as a holder)."""
        results = []
        for year, rows in self._by_season(SeasonKind.TOUR).items():
            period = _year_period("always_itm", year, SeasonKind.TOUR)
            running = self._running(period)
            played, itm = Counter(), Counter()
            for row in rows:
                played[row.player_id] += 1
                itm[row.player_id] += row.itm
            minimum = self.settings.always_itm_min_tournaments
            holders = sorted(
                pk for pk, count in played.items() if itm[pk] == count and count >= minimum
            )
            value = max((played[pk] for pk in holders), default=None)
            results.append(
                TitleResult(
                    "always_itm",
                    TITLES["always_itm"],
                    period,
                    running,
                    tuple(holders),
                    value,
                    holder_values=tuple(played[pk] for pk in holders),
                )
            )
        return results

    def _patron(self) -> list[TitleResult]:
        """The largest pot (chip value minus payout, exact) over results with a known stack."""
        results = []
        for year, rows in self._by_season(SeasonKind.CASH).items():
            period = _year_period("patron", year, SeasonKind.CASH)
            running = self._running(period)
            pots: dict[int, Fraction] = defaultdict(Fraction)
            for row in rows:
                if row.chips_out is not None:
                    pots[row.player_id] += (
                        chips_value(row.chips_out, row.chips_per_lei) - row.payout
                    )
            holders, best = _leaders(pots)
            results.append(
                TitleResult(
                    "patron",
                    TITLES["patron"],
                    period,
                    running,
                    tuple(sorted(holders)),
                    best,
                    no_data=not pots,
                    holder_values=(best,) * len(holders),
                )
            )
        return results

    # What views and the admin call.

    def counters(self) -> dict[str, dict[int, int]]:
        """Lifetime counter per ladder code and player (players without games are absent)."""
        return {code: dict(values) for code, values in self._counters.items()}

    def rank_progress(self, player_id: int) -> list[RankProgress]:
        progress = []
        for ladder in self.ladders:
            value = self._counters[ladder.code].get(player_id, 0)
            reached = [step for step in ladder.steps if step.threshold <= value]
            upcoming = [step for step in ladder.steps if step.threshold > value]
            progress.append(
                RankProgress(
                    ladder=ladder,
                    value=value,
                    step=reached[-1] if reached else None,
                    next_step=upcoming[0] if upcoming else None,
                    reached=tuple(self._reached.get((player_id, ladder.code), ())),
                )
            )
        return progress

    def badge_rules(self) -> dict[str, str]:
        """One short rule per badge, with the numbers of the current settings."""
        extra_buys = self.settings.comeback_min_rebuys
        length = self.settings.hat_trick_length
        return {
            "bubble": "первое место сразу за призовыми",
            "cashier": "лучший итог кэш-вечера, в плюсе",
            "one_buyin": "победа в турнире без докупок",
            "comeback": (
                f"в деньгах после {extra_buys} "
                f"{ru_plural(extra_buys, ('докупки', 'докупок', 'докупок'))} и более"
            ),
            # Over the player's own tournaments: one they skipped does not break the streak.
            "hat_trick": (
                f"в деньгах в {length} "
                f"{ru_plural(length, ('своём турнире', 'своих турнирах', 'своих турнирах'))} подряд"
            ),
            "no_skip": (
                "на всех клубных вечерах месяца, если их "
                f"{self.settings.no_skip_min_evenings} и больше"
            ),
        }

    def player(self, player_id: int) -> PlayerAwards:
        mine = [award for award in self.awards if award.player_id == player_id]
        rules = self.badge_rules()
        slots = []
        for code, title in BADGES.items():
            earned = [award for award in mine if award.kind == Kind.BADGE and award.code == code]
            slots.append(
                BadgeCount(code, title, len(earned), earned[-1] if earned else None, rules[code])
            )
        leading = [r for r in self.titles if r.running and player_id in r.holders]
        return PlayerAwards(
            ranks=self.rank_progress(player_id),
            badges=[slot for slot in slots if slot.count],
            diplomas=[award for award in mine if award.kind == Kind.DIPLOMA],
            titles=[award for award in mine if award.kind == Kind.TITLE],
            leading=leading,
            badge_slots=slots,
            record=[
                award
                for award in reversed(mine)
                if award.kind != Kind.BADGE and not award.is_first_step
            ],
            lead_lines=[result.lead_text(player_id) for result in leading],
        )

    def game(self, game_pk: int) -> GameAwards:
        badges: dict[int, list[Award]] = defaultdict(list)
        reached = []
        for award in self.awards:
            if award.game is None or award.game.pk != game_pk:
                continue
            if award.kind == Kind.BADGE:
                badges[award.player_id].append(award)
            elif not award.is_first_step:
                reached.append(award)
        return GameAwards(dict(badges), reached)

    def title_years(self) -> list[int]:
        """Years with titles, newest first."""
        return sorted({result.period.year for result in self.titles}, reverse=True)

    def honor_years(self) -> list[int]:
        """The honors board's year switcher, newest first: years with finished games or ended
        titles. A year with running titles only (a winter that began in December) is left out:
        the default page shows every running title anyway."""
        years = {date.year for date in self.evenings}
        years |= {result.period.year for result in self.titles if not result.running}
        return sorted(years, reverse=True)

    def latest_game_year(self) -> int | None:
        return self.evenings[-1].year if self.evenings else None

    def honors(self, year: int, *, running: bool = False) -> HonorsBoard:
        """The board for ``year``; with ``running`` (the default page) every running title is
        listed too, whatever year it is filed under."""
        counts: dict[int, Counter] = defaultdict(Counter)
        for award in self.awards:
            if award.kind == Kind.BADGE:
                counts[award.player_id][award.code] += 1
        matrix = sorted(
            ((self.players[pk], dict(count)) for pk, count in counts.items()),
            key=lambda item: (-sum(item[1].values()), item[0].name, item[0].nickname),
        )
        veteran = self._counters[LadderCode.VETERAN]
        ranked = sorted(
            (pk for pk in self.players if veteran.get(pk)),
            key=lambda pk: (-veteran[pk], self.players[pk].name, self.players[pk].nickname),
        )
        titles = [
            result
            for result in self.titles
            if result.period.year == year or (running and result.running)
        ]
        # Running periods first, then the newest.
        ordered = sorted(
            titles,
            key=lambda r: (not r.running, -r.period.end.toordinal(), list(TITLES).index(r.code)),
        )
        kinds = Counter(award.kind for award in self.awards)
        return HonorsBoard(
            year=year,
            titles=titles,
            badge_codes=list(BADGES),
            badge_matrix=matrix,
            rank_table=[(self.players[pk], self.rank_progress(pk)) for pk in ranked],
            title_rows=[
                HonorTitle(r, [(self.players[pk], r.holder_metric(pk)) for pk in r.holders])
                for r in ordered
            ],
            badge_rows=[
                BadgeRow(player, [count.get(code, 0) for code in BADGES])
                for player, count in matrix
            ],
            participants=len({row.player_id for row in self._rows}),
            badge_total=kinds[Kind.BADGE],
            diploma_title_total=kinds[Kind.DIPLOMA] + kinds[Kind.TITLE],
        )

    def recent(self, limit: int) -> list[Award]:
        """The newest ``limit`` awards club-wide (titles once their period has ended), first rank
        steps left out."""
        events = [award for award in reversed(self.awards) if not award.is_first_step]
        return events[:limit] if limit > 0 else []

    def completeness(self) -> Completeness:
        tour = [row for row in self._rows if row.game.kind == SeasonKind.TOUR]
        games: dict[int, bool] = {}
        for row in tour:
            games[row.game.pk] = games.get(row.game.pk, True) and row.place is not None
        return Completeness(
            tournaments=len(games),
            tournaments_all_places=sum(games.values()),
            tour_results=len(tour),
            rebuys_known=sum(row.rebuys is not None for row in tour),
            addon_known=sum(row.addon is not None for row in tour),
        )


def sort_badge_rows(rows: list[BadgeRow], key: str, descending: bool) -> list[BadgeRow]:
    """The badge matrix by ``key``: "name", "total" or a badge code. Ties stay by name."""
    by_name = sorted(rows, key=lambda row: (row.player.name, row.player.nickname))
    if key == "name":
        return by_name[::-1] if descending else by_name
    if key == "total":
        return sorted(by_name, key=lambda row: row.total, reverse=descending)
    index = list(BADGES).index(key)
    return sorted(by_name, key=lambda row: row.counts[index], reverse=descending)


def _load_ladders() -> list[Ladder]:
    steps: dict[int, list[RankStep]] = defaultdict(list)
    ladders = {}
    for step in RankStep.objects.select_related("ladder").order_by("ladder_id", "threshold"):
        ladders[step.ladder_id] = step.ladder
        steps[step.ladder_id].append(step)
    return [
        Ladder(ladder.code, ladder.title, tuple(Step(s.threshold, s.title) for s in steps[pk]))
        for pk, ladder in ladders.items()
    ]


def _load_rows() -> list[_Row]:
    # Oldest first; on a date with both kinds the tournament comes first ("tour" > "cash").
    fields = (
        "player_id",
        "game_id",
        "game__date",
        "game__season__kind",
        "game__season__year",
        "game__season__paid_places",
        "game__season__entry_price",
        "game__season__rebuy_price",
        "game__season__chips_per_lei",
        "buyin",
        "payout",
        "place",
        "chips_out",
        "rebuys",
        "addon",
    )
    values = (
        Result.objects.filter(game__live_stage="")
        .annotate(game_number=game_number("game__season", "game__date"))
        .order_by("game__date", "-game__season__kind", "game_id", "player_id")
        .values_list(*fields, "game_number")
    )
    games: dict[int, GameRef] = {}
    rows = []
    for player_id, game_id, date, kind, *rest, number in values:
        game = games.setdefault(game_id, GameRef(game_id, date, kind, number))
        rows.append(_Row(player_id, game, *rest))
    return rows


def load(today: datetime.date | None = None) -> ClubAwards:
    """Every award of the club in QUERY_COUNT queries. Never writes: without the settings row
    the field defaults apply, without ladders there are no ranks."""
    settings = AchievementSettings.objects.filter(pk=1).first() or AchievementSettings()
    return ClubAwards(
        rows=_load_rows(),
        players={player.pk: player for player in Player.objects.all()},
        ladders=_load_ladders(),
        settings=settings,
        today=today or timezone.localdate(),
    )
