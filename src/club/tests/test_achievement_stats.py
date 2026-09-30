from io import StringIO

import pytest
from django.core.management import call_command

from club.models import AchievementSettings, SeasonKind
from club.tests.factories import make_game, make_player, make_result, make_season

pytestmark = pytest.mark.django_db


def run():
    out = StringIO()
    call_command("achievement_stats", stdout=out)
    return out.getvalue()


def test_counters_rungs_and_completeness():
    """With the seeded rules: veteran steps 1, 6, 16, ...; feeder 1, 1000, ...; add-on 1, 5, ..."""
    season = make_season(2025, SeasonKind.TOUR)
    a = make_player("Альфа", nickname="Трактор")
    b, c = make_player("Браво"), make_player("Чарли")
    for day in range(1, 7):
        game = make_game(season, day=day, month=3)
        make_result(game, a, buyin=200, place=1, rebuys=2, addon=True)
        if day <= 2:
            make_result(game, b, buyin=50, place=2)
    make_result(make_game(season, day=7, month=3), c, buyin=50)  # no place known
    settings_before = list(AchievementSettings.objects.values())

    out = run()

    veteran = out[out.index("Ветеран [veteran]") : out.index("Кормилец клуба [feeder]")]
    lines = [line.split() for line in veteran.splitlines()[1:4]]
    assert lines == [["6", "Трактор"], ["2", "Браво"], ["1", "Чарли"]]
    assert "quartiles: Q1 1.5, median 2, Q3 4" in veteran
    assert " Новичок (1)" in veteran and "Подмастерье (6)" in veteran
    rung_lines = veteran[veteran.index("players per rung") :].splitlines()[1:]
    rungs = {
        line.rsplit(maxsplit=1)[0].strip(): int(line.rsplit(maxsplit=1)[1])
        for line in rung_lines
        if line.strip()
    }
    assert rungs["below the first step"] == 0
    assert rungs["Новичок (1)"] == 2
    assert rungs["Подмастерье (6)"] == 1
    assert "1200  Трактор" in out  # feeder
    addon = out[out.index("Мистер Аддон [addon]") : out.index("Completeness")]
    assert "0  Браво" in addon and "Ценитель (10)" in addon
    assert "tournaments with every place known: 6 of 7" in out
    assert "tour results with rebuys known: 6 of 9" in out
    assert "tour results with add-on known: 6 of 9" in out
    assert list(AchievementSettings.objects.values()) == settings_before  # writes nothing


def test_empty_club():
    out = run()
    assert "no players" in out
    assert "tournaments with every place known: 0 of 0" in out
