import re

import pytest
from django.utils import timezone

from club.models import AchievementSettings, RankLadder, RankStep, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season

PAST_YEAR = timezone.localdate().year - 1

# How the ``figures`` filter marks a number inside a text (club.templatetags.ledger).
NUM = '<span class="font-num num-run not-italic">'


def plain_text(html: str) -> str:
    """The text of a piece of HTML without its tags, to check wording apart from markup."""
    return re.sub(r"<[^>]+>", "", html)


@pytest.fixture
def club():
    """Two finished years of invented games, worked out by hand. Y = PAST_YEAR.

    Tour Y-1:  t0 10.11: Альфа 50->100 (1st), Браво 50->0
    Tour Y:    t1 01.03: Альфа 50->150 (1st), Браво 50->0, Чарли 50->0
               t2 08.03: Альфа 100->150 (1st), Браво 50->0             (same date as c1)
               t3 15.03: Альфа 50->60 (1st), Браво 50->0               unbalanced: 40 left over
    Cash Y:    c1 08.03: Альфа 100->50 (5 230 chips, pot 2,30), Браво 50->40 (4 770 chips,
                         pot 7,70), Чарли 50->100 (chips unknown)      pot 10
               c2 22.03: Альфа 50->80, Браво 50->30, Дельта 50->60     unbalanced: pot −20
    Эхо has no games. Дельта has exactly one.

    Альфа: tour +210 in 4 games (ITM 4, four 1st places), cash −20 in 2, all-time +190 in 6
    (buy-ins 400, payouts 590).
    """
    tour_old = make_season(PAST_YEAR - 1, SeasonKind.TOUR)
    tour = make_season(PAST_YEAR, SeasonKind.TOUR)
    cash = make_season(PAST_YEAR, SeasonKind.CASH, chips_per_lei=100)
    a = make_player("Альфа", nickname="Трактор")
    b, c, d = make_player("Браво"), make_player("Чарли"), make_player("Дельта")
    e = make_player("Эхо", nickname="Новичок")
    t0 = make_game(tour_old, day=10, month=11)
    t1, t2, t3 = (make_game(tour, day=day, month=3) for day in (1, 8, 15))
    c1 = make_game(cash, day=8, month=3, location="Гараж № 3")
    c2 = make_game(cash, day=22, month=3)
    for game, player, buyin, payout, place in [
        (t0, a, 50, 100, 1),
        (t0, b, 50, 0, None),
        (t1, a, 50, 150, 1),
        (t1, b, 50, 0, None),
        (t1, c, 50, 0, None),
        (t2, a, 100, 150, 1),
        (t2, b, 50, 0, None),
        (t3, a, 50, 60, 1),
        (t3, b, 50, 0, None),
    ]:
        make_result(game, player, buyin=buyin, payout=payout, place=place)
    for game, player, buyin, payout, chips_out in [
        (c1, a, 100, 50, 5230),
        (c1, b, 50, 40, 4770),
        (c1, c, 50, 100, None),
        (c2, a, 50, 80, None),
        (c2, b, 50, 30, None),
        (c2, d, 50, 60, None),
    ]:
        make_result(game, player, buyin=buyin, payout=payout, chips_out=chips_out)
    return {
        "seasons": {"tour_old": tour_old, "tour": tour, "cash": cash},
        "games": {"t0": t0, "t1": t1, "t2": t2, "t3": t3, "c1": c1, "c2": c2},
        "players": {"A": a, "B": b, "C": c, "D": d, "E": e},
    }


@pytest.fixture
def award_rules():
    """Short rank ladders and default parameters. The migration seed cannot be relied on (the
    migration test flushes it), so view tests that show awards create their own rules."""
    RankLadder.objects.all().delete()
    AchievementSettings.objects.all().delete()
    ladders = {
        "veteran": ("Ветеран", [(1, "Новобранец"), (3, "Бывалый"), (5, "Старожил")]),
        "feeder": ("Кормилец клуба", [(100, "Пайщик"), (1000, "Меценат")]),
        "addon": ("Мистер Аддон", [(1, "Первый аддон")]),
    }
    for code, (title, steps) in ladders.items():
        ladder = RankLadder.objects.create(code=code, title=title)
        for threshold, step in steps:
            RankStep.objects.create(ladder=ladder, threshold=threshold, title=step)
    return AchievementSettings.objects.create()
