import secrets
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from club.models import LevelFields


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
    TIMER = "timer", "таймер подключён"
    PAUSE = "pause", "таймер: пауза"
    RESUME = "resume", "таймер: пуск"
    LEVEL_NEXT = "level_next", "таймер: следующий уровень"
    LEVEL_PREV = "level_prev", "таймер: предыдущий уровень"
    PLUS_MINUTE = "plus_min", "таймер: +1 минута"
    LEVEL_EDIT = "level_edit", "уровень изменён"
    LEVEL_ADD = "level_add", "уровень добавлен"
    BULK_MINUTES = "bulk_min", "минуты уровней"
    LINK = "link", "новая ссылка на табло"


# Actions on the blind timer. They never touch results or the stage.
TIMER_KINDS = {
    ActionKind.TIMER,
    ActionKind.PAUSE,
    ActionKind.RESUME,
    ActionKind.LEVEL_NEXT,
    ActionKind.LEVEL_PREV,
    ActionKind.PLUS_MINUTE,
    ActionKind.LEVEL_EDIT,
    ActionKind.LEVEL_ADD,
    ActionKind.BULK_MINUTES,
    ActionKind.LINK,
}


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
    # Undo puts the timer's three fields back (level edits are changed back in the editor).
    ActionKind.PAUSE,
    ActionKind.RESUME,
    ActionKind.LEVEL_NEXT,
    ActionKind.LEVEL_PREV,
    ActionKind.PLUS_MINUTE,
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


def display_token() -> str:
    return secrets.token_urlsafe(32)


class BlindTimer(models.Model):
    """A tournament's blind clock, stored as the few fields the remaining time follows from.

    The clock counts from level ``position``, which started at ``started_at``; while paused,
    ``paused_at`` holds when. Elapsed time is ``(paused_at or now) - started_at``, and the levels
    after ``position`` follow by their minutes (live.clock), so running through levels writes
    nothing. Resuming and "+1 минута" move ``started_at`` forward.

    ``display_token`` opens the read-only display without a login (the secret link); a new token
    revokes the old link.
    """

    game = models.OneToOneField(
        "club.Game", on_delete=models.CASCADE, related_name="blind_timer", verbose_name="игра"
    )
    structure_name = models.CharField("структура", max_length=100)
    position = models.PositiveSmallIntegerField("уровень отсчёта")
    started_at = models.DateTimeField("начало уровня отсчёта")
    paused_at = models.DateTimeField("пауза с", null=True, blank=True)
    display_token = models.CharField(
        "ключ ссылки на табло", max_length=64, unique=True, default=display_token
    )

    class Meta:
        verbose_name = "таймер блайндов"
        verbose_name_plural = "таймеры блайндов"
        constraints = [
            models.CheckConstraint(
                condition=Q(position__gte=1), name="blindtimer_position_positive"
            ),
            models.CheckConstraint(
                condition=~Q(display_token=""), name="blindtimer_token_not_empty"
            ),
        ]

    def __str__(self) -> str:
        return f"Таймер: {self.game}"


class TimerLevel(LevelFields):
    """One row of a game's own copy of a blind structure."""

    timer = models.ForeignKey(
        BlindTimer, on_delete=models.CASCADE, related_name="levels", verbose_name="таймер"
    )
    position = models.PositiveSmallIntegerField("порядок")

    class Meta(LevelFields.Meta):
        verbose_name = "уровень игры"
        verbose_name_plural = "уровни игры"
        ordering = ["position"]
        constraints = [
            *LevelFields.Meta.constraints,
            models.UniqueConstraint(
                fields=["timer", "position"], name="timerlevel_position_unique"
            ),
            models.UniqueConstraint(
                fields=["timer"], condition=Q(addon_break=True), name="timerlevel_one_addon_break"
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1), name="timerlevel_position_positive"
            ),
        ]

    def __str__(self) -> str:
        return self.label if self.is_break else f"{self.small_blind} / {self.big_blind}"
