from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


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


class SheetSnapshot(models.Model):
    """The club spreadsheet as fetched for one preview, so that "Применить" imports exactly the
    previewed bytes, never a new fetch. Short-lived: deleted on apply, on the next fetch, and once
    older than TTL (importer.admin)."""

    TTL = timedelta(hours=1)

    content = models.BinaryField("файл")
    sha256 = models.CharField("SHA-256", max_length=64)
    fetched_at = models.DateTimeField("загружено", default=timezone.now)
    fetched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="кто загрузил",
    )
    sheets = models.JSONField("листы", default=list)  # the previewed selection

    class Meta:
        verbose_name = "таблица клуба"
        verbose_name_plural = "таблица клуба"

    def __str__(self) -> str:
        return f"Таблица от {timezone.localtime(self.fetched_at):%d.%m.%Y %H:%M}"

    @property
    def expired(self) -> bool:
        return timezone.now() - self.fetched_at > self.TTL

    @classmethod
    def purge_expired(cls) -> None:
        cls.objects.filter(fetched_at__lt=timezone.now() - cls.TTL).delete()
