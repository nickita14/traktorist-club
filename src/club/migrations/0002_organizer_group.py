from django.contrib.auth.management import create_permissions
from django.db import migrations

ORGANIZER_GROUP = "Organizer"

# Organizers edit everything but may delete only results (to fix a wrong row in a game).
ORGANIZER_PERMISSIONS = [
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
]


def create_organizer_group(apps, schema_editor):
    # Permissions are normally created by post_migrate, which has not run yet on a fresh DB.
    app_config = apps.get_app_config("club")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permissions = Permission.objects.filter(
        content_type__app_label="club", codename__in=ORGANIZER_PERMISSIONS
    )
    missing = set(ORGANIZER_PERMISSIONS) - set(permissions.values_list("codename", flat=True))
    if missing:
        raise RuntimeError(f"Missing permissions: {sorted(missing)}")

    group, _ = Group.objects.get_or_create(name=ORGANIZER_GROUP)
    group.permissions.set(permissions)


def delete_organizer_group(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name=ORGANIZER_GROUP).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("club", "0001_initial"),
        ("auth", "__latest__"),
        ("contenttypes", "__latest__"),
    ]

    operations = [migrations.RunPython(create_organizer_group, delete_organizer_group)]
