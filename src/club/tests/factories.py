"""Tiny builders for invented test data. No real club names anywhere."""

import datetime
from itertools import count

from club.models import Game, Player, Result, Season, SeasonKind

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
