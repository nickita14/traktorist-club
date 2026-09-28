import datetime
import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

# live 0002 needs club 0004, so step back before both.
BEFORE = [("live", "0001_initial"), ("club", "0003_live_game")]
AFTER = [("live", "0002_game_cascade")]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def test_orphaned_actions_are_removed():
    apps = migrate(BEFORE)
    Season = apps.get_model("club", "Season")
    Game = apps.get_model("club", "Game")
    LiveAction = apps.get_model("live", "LiveAction")
    season = Season.objects.create(year=2026, kind="tour")
    game = Game.objects.create(season=season, date=datetime.date(2026, 9, 28))
    kept = LiveAction.objects.create(key=uuid.uuid4(), game=game, kind="start", summary="начата")
    LiveAction.objects.create(key=uuid.uuid4(), game=None, kind="cancel", summary="отменена")

    apps = migrate(AFTER)

    LiveAction = apps.get_model("live", "LiveAction")
    assert list(LiveAction.objects.values_list("pk", flat=True)) == [kept.pk]
    migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
