from django.contrib.auth.management import create_permissions
from django.db import migrations

ORGANIZER_GROUP = "Organizer"

# Organizers prepare theme nights (Тематика): themes, their events and the sounds they upload.
PERMISSIONS = [
    f"{action}_{model}"
    for model in ("theme", "themeevent", "themesound")
    for action in ("view", "add", "change", "delete")
]


def grant(apps, schema_editor):
    # post_migrate has not created the new models' permissions yet on this run.
    app_config = apps.get_app_config("live")
    app_config.models_module = True
    create_permissions(app_config, apps=apps, verbosity=0)
    app_config.models_module = None

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permissions = Permission.objects.filter(
        content_type__app_label="live", codename__in=PERMISSIONS
    )
    missing = set(PERMISSIONS) - set(permissions.values_list("codename", flat=True))
    if missing:
        raise RuntimeError(f"Missing permissions: {sorted(missing)}")
    group, _ = Group.objects.get_or_create(name=ORGANIZER_GROUP)
    group.permissions.add(*permissions)


def revoke(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    group = Group.objects.filter(name=ORGANIZER_GROUP).first()
    if group is not None:
        group.permissions.remove(
            *Permission.objects.filter(content_type__app_label="live", codename__in=PERMISSIONS)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("live", "0005_theme_nights"),
        ("club", "0002_organizer_group"),
        ("auth", "__latest__"),
        ("contenttypes", "__latest__"),
    ]

    operations = [migrations.RunPython(grant, revoke)]
