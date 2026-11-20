import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import Q

from club.models import LevelFields


class ActionKind(models.TextChoices):
    SEAT = "seat", "за стол"
    UNSEAT = "unseat", "не садился"
    PAID_ON = "paid_on", "вход принят"
    PAID_OFF = "paid_off", "вход: отметка снята"
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


# Undo restores a result row (deletes a seated one, recreates an unseated one) or the game's
# stage; the rest is final.
UNDOABLE = {
    ActionKind.SEAT,
    ActionKind.UNSEAT,
    ActionKind.PAID_ON,
    ActionKind.PAID_OFF,
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
    # A bust only: the place the player got when it was pressed (the players still in at that
    # moment). Later seats and restores change the count, so the display reads it from here.
    place = models.PositiveSmallIntegerField("место при выбывании", null=True, blank=True)

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


# Theme nights (Тематика): what the display (табло) shows when something happens at the table.
# Only one theme is active; without one the display uses live.themes.DEFAULTS.


class EventKind(models.TextChoices):
    BUST = "bust", "Выбыл"
    BUST_ITM = "bust_itm", "Выбыл в призах"
    BUBBLE_BURST = "bubble_burst", "Бабл лопнул"
    REBUY = "rebuy", "Ребай"
    WINNER = "winner", "Победитель"
    BREAK = "break", "Перерыв"


class OverlayStyle(models.TextChoices):
    """The overlay presets: the ``tablo-overlay-<value>`` classes in frontend/source.css."""

    STAMP = "stamp", "Штамп"
    BAND = "band", "Полоса"
    HEADLINE = "headline", "Заголовок"
    LOWER_THIRD = "lower-third", "Плашка снизу"
    FULLSCREEN = "fullscreen", "Весь экран"


class SoundType(models.TextChoices):
    MP3 = "audio/mpeg", "mp3"
    OGG = "audio/ogg", "ogg"
    WAV = "audio/wav", "wav"


class CoverType(models.TextChoices):
    JPEG = "image/jpeg", "jpg"
    PNG = "image/png", "png"
    WEBP = "image/webp", "webp"


# The accent is drawn on ink (the break screen): large text and markers, so 3:1 (WCAG AA large).
ACCENT_MIN_CONTRAST = 3.0
HEX_COLOR = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Цвет в виде #RRGGBB.")


class Theme(models.Model):
    name = models.CharField("название", max_length=100, unique=True)
    is_active = models.BooleanField(
        "включена",
        default=False,
        help_text="Включённая тематика одна: эта выключит остальные. "
        "Без тематики табло объявляет по-клубному.",
    )
    tips = models.TextField(
        "советы на перерыв",
        blank=True,
        default="",
        help_text="По одному в строке; показываются по кругу во время перерыва, "
        "если у тематики включено событие «Перерыв».",
    )
    accent = models.CharField(
        "акцент",
        max_length=7,
        blank=True,
        default="",
        validators=[HEX_COLOR],
        help_text="Цвет названия, разделителя и метки совета на экране перерыва. "
        "Пусто: как обычно.",
    )
    updated_at = models.DateTimeField("изменена", auto_now=True)

    class Meta:
        verbose_name = "тематика"
        verbose_name_plural = "тематики"
        ordering = ["-is_active", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"], condition=Q(is_active=True), name="theme_one_active"
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def tip_list(self) -> list[str]:
        return [line.strip() for line in self.tips.splitlines() if line.strip()]

    def clean(self):
        from live import themes

        super().clean()
        self.accent = self.accent.strip().lower()
        if self.accent and HEX_COLOR.regex.match(self.accent):
            ratio = themes.contrast_on_ink(self.accent)
            if ratio < ACCENT_MIN_CONTRAST:
                raise ValidationError(
                    {
                        "accent": f"Слишком тёмный для экрана перерыва: контраст "
                        f"{ratio:.1f}:1, нужно не меньше {ACCENT_MIN_CONTRAST:.0f}:1."
                    }
                )

    def validate_constraints(self, exclude=None):
        # Activating a theme switches the active one off in save(), so the one-active rule is no
        # form error.
        super().validate_constraints(exclude={*(exclude or ()), "is_active"})

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.is_active:
                Theme.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
            super().save(*args, **kwargs)


class ThemeSound(models.Model):
    """An uploaded sound, kept in the database (the project has no media storage).

    Served by its hash (``live.views.tablo_sound``), so the URL changes with the content and the
    browser may cache it for good.
    """

    name = models.CharField("название", max_length=100)
    content_type = models.CharField("формат", max_length=20, choices=SoundType)
    size = models.PositiveIntegerField("размер, байт")
    sha256 = models.CharField("хеш", max_length=64, unique=True, editable=False)
    data = models.BinaryField("данные")
    created_at = models.DateTimeField("загружен", auto_now_add=True)

    class Meta:
        verbose_name = "звук"
        verbose_name_plural = "звуки"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(content_type__in=SoundType.values), name="themesound_type_valid"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ThemeCover(models.Model):
    """A theme's break screen image (Обложка), kept in the database like the sounds.

    A separate row, so loading a theme (every display sync) never reads the bytes. Served by its
    hash (``live.views.tablo_cover``); two themes may share an image, so the hash is no key.
    """

    theme = models.OneToOneField(
        Theme, on_delete=models.CASCADE, related_name="cover", verbose_name="тематика"
    )
    content_type = models.CharField("формат", max_length=20, choices=CoverType)
    size = models.PositiveIntegerField("размер, байт")
    sha256 = models.CharField("хеш", max_length=64, db_index=True, editable=False)
    data = models.BinaryField("данные")
    created_at = models.DateTimeField("загружена", auto_now_add=True)

    class Meta:
        verbose_name = "обложка"
        verbose_name_plural = "обложки"
        constraints = [
            models.CheckConstraint(
                condition=Q(content_type__in=CoverType.values), name="themecover_type_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.theme}: обложка"


class ThemeEvent(models.Model):
    """How one kind of event looks in a theme. A kind without a row uses the default."""

    theme = models.ForeignKey(
        Theme, on_delete=models.CASCADE, related_name="events", verbose_name="тематика"
    )
    kind = models.CharField("событие", max_length=20, choices=EventKind)
    enabled = models.BooleanField("показывать", default=True)
    title = models.CharField(
        "заголовок", max_length=60, blank=True, default="", help_text="Крупная строка."
    )
    caption = models.CharField(
        "подпись", max_length=120, blank=True, default="", help_text="Строка под заголовком."
    )
    style = models.CharField(
        "стиль", max_length=20, choices=OverlayStyle, default=OverlayStyle.STAMP
    )
    seconds = models.PositiveSmallIntegerField(
        "секунд",
        default=5,
        help_text="Сколько держать на экране. Победитель: 0 значит до конца игры. "
        "Перерыв держится весь перерыв.",
    )
    sound = models.ForeignKey(
        ThemeSound,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name="звук",
    )

    class Meta:
        verbose_name = "событие тематики"
        verbose_name_plural = "события тематики"
        ordering = ["theme", "kind"]
        constraints = [
            models.UniqueConstraint(fields=["theme", "kind"], name="themeevent_kind_unique"),
            models.CheckConstraint(
                condition=Q(kind__in=EventKind.values), name="themeevent_kind_valid"
            ),
            models.CheckConstraint(
                condition=Q(style__in=OverlayStyle.values), name="themeevent_style_valid"
            ),
            models.CheckConstraint(condition=Q(seconds__lte=60), name="themeevent_seconds_max"),
        ]

    def __str__(self) -> str:
        return f"{self.theme}: {self.get_kind_display()}"

    def clean(self):
        from live import themes

        super().clean()
        errors = {}
        for field in ("title", "caption"):
            message = themes.template_error(self.kind, getattr(self, field))
            if message:
                errors[field] = message
        if self.kind == EventKind.BREAK and self.style != OverlayStyle.FULLSCREEN:
            errors["style"] = "Перерыв показывается только на весь экран."
        if self.seconds == 0 and self.kind not in (EventKind.WINNER, EventKind.BREAK):
            errors["seconds"] = "0 секунд можно только победителю: он стоит до конца игры."
        if errors:
            raise ValidationError(errors)
