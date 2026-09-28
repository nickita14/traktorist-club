"""Tiny builders for invented test data. No real club names anywhere."""

import datetime
from itertools import count

from club.models import BlindLevel, BlindStructure, Game, Player, Result, Season, SeasonKind

_player_numbers = count(1)


def make_player(name: str | None = None, nickname: str = "", **kwargs) -> Player:
    return Player.objects.create(
        name=name or f"Тестов Игрок {next(_player_numbers)}", nickname=nickname, **kwargs
    )


def make_season(year: int = 2025, kind: str = SeasonKind.TOUR, **kwargs) -> Season:
    return Season.objects.create(year=year, kind=kind, **kwargs)


def make_game(season: Season, day: int = 1, month: int = 1, **kwargs) -> Game:
    return Game.objects.create(season=season, date=datetime.date(season.year, month, day), **kwargs)


def make_result(game: Game, player: Player, buyin: int = 50, payout: int = 0, **kwargs) -> Result:
    return Result.objects.create(game=game, player=player, buyin=buyin, payout=payout, **kwargs)


# A short structure: three levels, the add-on break, one more level, a plain break, a last level.
STRUCTURE = [
    (25, 50, 20),
    (50, 100, 20),
    (100, 200, 20, 25),
    ("Перерыв · аддон", 15, "addon"),
    (150, 300, 20, 25),
    ("Перерыв", 10),
    (200, 400, 20, 50),
]


def make_structure(name: str = "Тестовая структура", rows=STRUCTURE) -> BlindStructure:
    """Rows: (small, big, minutes[, ante]) for a level, (label, minutes[, "addon"]) for a break."""
    structure = BlindStructure.objects.create(name=name)
    for position, row in enumerate(rows, start=1):
        if isinstance(row[0], str):
            label, minutes, *flag = row
            fields = {"label": label, "minutes": minutes, "addon_break": flag == ["addon"]}
        else:
            small, big, minutes, *ante = row
            fields = {"small_blind": small, "big_blind": big, "minutes": minutes}
            fields["ante"] = ante[0] if ante else 0
        BlindLevel.objects.create(structure=structure, position=position, **fields)
    return structure
