from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower


class PlayerAlias(models.Model):
    """A raw name from the club spreadsheet that means this player (importer.aliases)."""

    raw_name = models.CharField(
        "имя в таблице",
        max_length=100,
        unique=True,
        help_text="Как в таблице; регистр, пробелы и ё/е не важны.",
    )
    player = models.ForeignKey(
        "club.Player",
        on_delete=models.CASCADE,
        related_name="aliases",
        verbose_name="игрок",
    )

    class Meta:
        verbose_name = "имя в таблице"
        verbose_name_plural = "имена в таблице"
        ordering = ["raw_name"]
        constraints = [
            # A backstop for the case; clean() also catches spaces and ё/е.
            models.UniqueConstraint(Lower("raw_name"), name="playeralias_raw_name_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.raw_name

    def clean(self):
        from importer.aliases import normalize  # importer.aliases imports this module

        super().clean()
        self.raw_name = " ".join(self.raw_name.split())
        key = normalize(self.raw_name)
        others = PlayerAlias.objects.exclude(pk=self.pk).select_related("player")
        clash = next((alias for alias in others if normalize(alias.raw_name) == key), None)
        if clash is not None:
            raise ValidationError(
                {"raw_name": f"Это имя уже записано за игроком {clash.player}: «{clash}»."}
            )
