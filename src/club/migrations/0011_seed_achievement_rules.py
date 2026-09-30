from django.contrib.auth.management import create_permissions
from django.db import migrations

ORGANIZER_GROUP = "Organizer"

# Organizers tune the rules: ladder titles and steps, the parameters row. Ladders themselves are
# fixed by their codes, so nobody adds or deletes one; the parameters are a single row.
PERMISSIONS = [
    "view_rankladder",
    "change_rankladder",
    "view_rankstep",
    "add_rankstep",
    "change_rankstep",
    "delete_rankstep",
    "view_achievementsettings",
    "change_achievementsettings",
]

# (code, title, [(threshold, step title)]). Feeder and add-on steps are provisional:
# `manage.py achievement_stats` shows the real distributions to set them.
LADDERS = [
    (
        "veteran",
        "Ветеран",
        [
            (1, "Новичок"),
            (6, "Подмастерье"),
            (16, "Мастер"),
            (31, "Бригадир"),
            (61, "Ветеран"),
            (101, "Почётный тракторист"),
        ],
    ),
    (
        "feeder",
        "Кормилец клуба",
        [
            (1, "Пайщик"),
            (1000, "Вкладчик"),
            (2500, "Спонсор"),
            (5000, "Благодетель"),
            (10000, "Кормилец клуба"),
        ],
    ),
    (
        "addon",
        "Мистер Аддон",
        [
            (1, "Первый аддон"),
            (5, "Любитель"),
            (10, "Ценитель"),
            (20, "Знаток"),
            (40, "Мистер Аддон"),
        ],
    ),
]


def seed(apps, schema_editor):
    RankLadder = apps.get_model("club", "RankLadder")
    RankStep = apps.get_model("club", "RankStep")
    AchievementSettings = apps.get_model("club", "AchievementSettings")
    for code, title, steps in LADDERS:
        ladder = RankLadder.objects.create(code=code, title=title)
        RankStep.objects.bulk_create(
            RankStep(ladder=ladder, threshold=threshold, title=step) for threshold, step in steps
        )
    AchievementSettings.objects.create(pk=1)

    # post_migrate has not created the new models' permissions yet on this run.
    app_config = apps.get_app_config("club")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permissions = Permission.objects.filter(
        content_type__app_label="club", codename__in=PERMISSIONS
    )
    missing = set(PERMISSIONS) - set(permissions.values_list("codename", flat=True))
    if missing:
        raise RuntimeError(f"Missing permissions: {sorted(missing)}")
    group, _ = Group.objects.get_or_create(name=ORGANIZER_GROUP)
    group.permissions.add(*permissions)


def unseed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    group = Group.objects.filter(name=ORGANIZER_GROUP).first()
    if group is not None:
        group.permissions.remove(
            *Permission.objects.filter(content_type__app_label="club", codename__in=PERMISSIONS)
        )
    apps.get_model("club", "AchievementSettings").objects.all().delete()
    # Steps go with their ladders (CASCADE).
    apps.get_model("club", "RankLadder").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("club", "0010_achievement_rules"),
        ("auth", "__latest__"),
        ("contenttypes", "__latest__"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
