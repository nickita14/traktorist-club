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


THEME_PERMISSIONS = {
    f"{action}_{model}"
    for model in ("theme", "themeevent", "themesound")
    for action in ("view", "add", "change", "delete")
}


def organizer_theme_permissions(apps) -> set[str]:
    Group = apps.get_model("auth", "Group")
    group = Group.objects.filter(name="Organizer").first()
    if group is None:
        return set()
    return set(
        group.permissions.filter(content_type__app_label="live").values_list("codename", flat=True)
    )


def test_theme_permissions_forward_and_back():
    apps = migrate([("live", "0005_theme_nights")])
    assert organizer_theme_permissions(apps) == set()

    apps = migrate([("live", "0006_organizer_theme_permissions")])
    assert organizer_theme_permissions(apps) == THEME_PERMISSIONS

    apps = migrate([("live", "0005_theme_nights")])
    assert organizer_theme_permissions(apps) == set()
    migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
