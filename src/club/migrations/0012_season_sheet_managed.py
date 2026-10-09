from django.db import migrations, models


def mark_existing_seasons(apps, schema_editor):
    # Every season so far came from the club spreadsheet, so the importer keeps updating them.
    # Seasons created later (admin, live screens) start unmanaged.
    apps.get_model("club", "Season").objects.update(sheet_managed=True)


class Migration(migrations.Migration):
    dependencies = [
        ("club", "0011_seed_achievement_rules"),
    ]

    operations = [
        migrations.AddField(
            model_name="season",
            name="sheet_managed",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Импорт из таблицы обновляет игры этого сезона. "
                    "Игры с живого экрана он не трогает."
                ),
                verbose_name="ведётся в таблице",
            ),
        ),
        migrations.RunPython(mark_existing_seasons, migrations.RunPython.noop),
    ]
