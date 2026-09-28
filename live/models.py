import uuid

from django.conf import settings
from django.db import models


class ActionKind(models.TextChoices):
    SEAT = "seat", "за стол"
    REBUY = "rebuy", "ребай"
    ADDON_ON = "addon_on", "аддон"
    ADDON_OFF = "addon_off", "аддон снят"
    ELIMINATE = "eliminate", "выбыл"
    RESTORE = "restore", "возвращён в игру"
    TOPUP = "topup", "докупка"
    EXIT = "exit", "выход"
    RETURN = "return", "вернулся"
    STAGE = "stage", "смена этапа"
    START = "start", "игра начата"
    FINISH = "finish", "игра завершена"


# Undo restores a result row (or deletes a seated one) or the game's stage; the rest is final.
UNDOABLE = {
    ActionKind.SEAT,
    ActionKind.REBUY,
    ActionKind.ADDON_ON,
    ActionKind.ADDON_OFF,
    ActionKind.ELIMINATE,
    ActionKind.RESTORE,
    ActionKind.TOPUP,
    ActionKind.EXIT,
    ActionKind.RETURN,
    ActionKind.STAGE,
}


class LiveAction(models.Model):
    """One button press on the live game screens.

    ``key`` makes the request idempotent: each rendered button carries a fresh key, and a request
    whose key is already here (a double tap, a retry after a lost response) changes nothing.
    ``before`` and ``after`` hold the touched fields, so undo restores exact values.
    The log belongs to its game and is deleted with it; a deletion itself is in Django's LogEntry
    (the admin writes one, and so does cancelling a game on the live screen).
    """

    Kind = ActionKind

    key = models.UUIDField("ключ запроса", unique=True, default=uuid.uuid4, editable=False)
    game = models.ForeignKey(
        "club.Game",
        on_delete=models.CASCADE,
        related_name="live_actions",
        verbose_name="игра",
    )
    result = models.ForeignKey(
        "club.Result",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="результат",
    )
    kind = models.CharField("действие", max_length=10, choices=ActionKind)
    summary = models.CharField("описание", max_length=200)
    before = models.JSONField("было", null=True, blank=True)
    after = models.JSONField("стало", null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="кто",
    )
    created_at = models.DateTimeField("когда", auto_now_add=True)
    undone_at = models.DateTimeField("отменено", null=True, blank=True)

    class Meta:
        verbose_name = "действие за столом"
        verbose_name_plural = "действия за столом"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=ActionKind.values), name="liveaction_kind_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.summary

    @property
    def undoable(self) -> bool:
        return self.kind in UNDOABLE and self.undone_at is None
