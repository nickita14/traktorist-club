from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("club", "0015_organizer_year_closure_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="season",
            name="sheet_title",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Точное название листа, если оно не вида ТУР2027 или КЭШ2027. "
                    "Пусто: лист ищется по такому названию."
                ),
                max_length=100,
                verbose_name="лист в таблице",
            ),
        ),
        migrations.AddConstraint(
            model_name="season",
            constraint=models.UniqueConstraint(
                condition=models.Q(("sheet_title", ""), _negated=True),
                fields=("sheet_title",),
                name="season_sheet_title_unique",
                violation_error_message="Этот лист уже указан у другого сезона.",
            ),
        ),
    ]
