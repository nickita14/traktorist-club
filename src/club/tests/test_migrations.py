import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from club.models import AchievementSettings, RankLadder

pytestmark = pytest.mark.django_db


def test_organizer_group_has_exact_permissions():
    group = Group.objects.get(name="Organizer")

    codenames = set(group.permissions.values_list("codename", flat=True))

    assert codenames == {
        "view_player",
        "add_player",
        "change_player",
        "view_season",
        "add_season",
        "change_season",
        "view_game",
        "add_game",
        "change_game",
        "view_result",
        "add_result",
        "change_result",
        "delete_result",
        "view_blindstructure",
        "add_blindstructure",
        "change_blindstructure",
        "view_blindlevel",
        "add_blindlevel",
        "change_blindlevel",
        "delete_blindlevel",
        "view_rankladder",
        "change_rankladder",
        "view_rankstep",
        "add_rankstep",
        "change_rankstep",
        "delete_rankstep",
        "view_achievementsettings",
        "change_achievementsettings",
    }
    assert set(group.permissions.values_list("content_type__app_label", flat=True)) == {"club"}


def test_seeded_achievement_rules():
    ladders = {
        ladder.code: (ladder.title, list(ladder.steps.values_list("threshold", "title")))
        for ladder in RankLadder.objects.all()
    }
    assert ladders["veteran"] == (
        "Ветеран",
        [
            (1, "Новичок"),
            (6, "Подмастерье"),
            (16, "Мастер"),
            (31, "Бригадир"),
            (61, "Ветеран"),
            (101, "Почётный тракторист"),
        ],
    )
    assert ladders["feeder"][1][0] == (1, "Пайщик")
    assert ladders["feeder"][1][-1] == (10000, "Кормилец клуба")
    assert ladders["addon"][1] == [
        (1, "Первый аддон"),
        (5, "Любитель"),
        (10, "Ценитель"),
        (20, "Знаток"),
        (40, "Мистер Аддон"),
    ]
    settings = AchievementSettings.objects.get()
    assert (settings.pk, settings.itm_series, settings.evening_series) == (1, [5, 10], [3, 5, 10])
    assert (
        settings.no_skip_min_evenings,
        settings.always_itm_min_tournaments,
        settings.hat_trick_length,
        settings.comeback_min_rebuys,
    ) == (2, 5, 3, 2)


# Rolling a migration back needs real transactions, so the database is flushed afterwards and the
# seeded rows (the Organizer group, the rules) are gone. pytest-django runs transactional tests
# after all the others, so no test that reads the seed comes later; the achievement tests create
# their own rules. (serialized_rollback would clash with the content types that post_migrate
# recreates after another transactional test's flush.)
@pytest.mark.django_db(transaction=True)
def test_achievement_rules_migration_forward_and_backward():
    seeded = ("club", "0011_seed_achievement_rules")
    schema = ("club", "0010_achievement_rules")

    def migrate(target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        executor.loader.build_graph()
        return executor.loader.project_state([target]).apps

    try:
        apps = migrate(schema)
        assert apps.get_model("club", "RankLadder").objects.count() == 0
        assert apps.get_model("club", "AchievementSettings").objects.count() == 0
        # An earlier transactional test may have flushed the group away.
        organizer, _ = apps.get_model("auth", "Group").objects.get_or_create(name="Organizer")
        assert not organizer.permissions.filter(codename="change_rankstep").exists()

        apps = migrate(seeded)
        steps = apps.get_model("club", "RankStep").objects
        assert apps.get_model("club", "RankLadder").objects.count() == 3
        assert steps.count() == 16
        assert apps.get_model("club", "AchievementSettings").objects.count() == 1
        organizer = apps.get_model("auth", "Group").objects.get(name="Organizer")
        assert organizer.permissions.filter(codename="change_rankstep").exists()
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_existing_seasons_become_sheet_managed():
    before = ("club", "0011_seed_achievement_rules")
    after = ("club", "0012_season_sheet_managed")

    def migrate(target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        executor.loader.build_graph()
        return executor.loader.project_state([target]).apps

    try:
        apps = migrate(before)
        apps.get_model("club", "Season").objects.create(year=2025, kind="tour")

        apps = migrate(after)
        assert list(apps.get_model("club", "Season").objects.values_list("sheet_managed")) == [
            (True,)
        ]
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
