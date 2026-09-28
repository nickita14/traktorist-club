from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models import F, Q
from django.urls import reverse
from django.utils.text import slugify

from club.formatting import ru_plural

# Russian-to-Latin table for slugs; everything else is left to slugify (NFKD strips diacritics).
TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def transliterate(value: str) -> str:
    return "".join(TRANSLIT.get(char, char) for char in value.lower())


class Player(models.Model):
    name = models.CharField("имя", max_length=100)
    nickname = models.CharField("ник", max_length=100, blank=True, default="")
    slug = models.SlugField(
        "slug",
        max_length=120,
        blank=True,
        db_index=False,  # player_slug_unique already indexes it
        help_text="Оставьте пустым, чтобы сгенерировать из ника или имени.",
    )

    class Meta:
        verbose_name = "игрок"
        verbose_name_plural = "игроки"
        ordering = ["name", "nickname"]
        constraints = [
            models.UniqueConstraint(fields=["slug"], name="player_slug_unique"),
            models.UniqueConstraint(
                fields=["name", "nickname"], name="player_name_nickname_unique"
            ),
            models.CheckConstraint(condition=~Q(name=""), name="player_name_not_empty"),
            models.CheckConstraint(condition=~Q(slug=""), name="player_slug_not_empty"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.nickname})" if self.nickname else self.name

    def get_absolute_url(self) -> str:
        return reverse("player_detail", args=[self.slug])

    @property
    def label(self) -> str:
        """How the club calls the player where only one word fits: nickname, else name."""
        return self.nickname or self.name

    def clean(self):
        # Forms validate constraints before save(), so the slug must exist by now.
        super().clean()
        self.ensure_slug()

    def save(self, *args, **kwargs):
        # ORM paths that skip full_clean() still get a slug.
        self.ensure_slug()
        super().save(*args, **kwargs)

    def ensure_slug(self) -> None:
        """Fill an empty slug from nickname or name; an existing slug is never changed."""
        if not self.slug:
            self.slug = self._unique_slug()

    def _unique_slug(self) -> str:
        max_length = self._meta.get_field("slug").max_length
        base = slugify(transliterate(self.nickname or self.name))[: max_length - 4] or "player"
        others = Player.objects.exclude(pk=self.pk)
        slug, suffix = base, 2
        while others.filter(slug=slug).exists():
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug


class SeasonKind(models.TextChoices):
    TOUR = "tour", "Турнир"
    CASH = "cash", "Кэш"


class Season(models.Model):
    Kind = SeasonKind

    year = models.PositiveSmallIntegerField("год")
    kind = models.CharField("тип", max_length=4, choices=Kind)
    chips_per_lei = models.PositiveIntegerField("фишек за 1 лей", default=100)
    paid_places = models.PositiveSmallIntegerField("призовых мест", default=3)
    # Prices used by the live game screens; Result.buyin stays the source of truth.
    entry_price = models.PositiveIntegerField("вход в турнир, лей", default=50)
    rebuy_price = models.PositiveIntegerField("ребай, лей", default=50)
    addon_price = models.PositiveIntegerField("аддон, лей", default=50)
    rebuy_minutes = models.PositiveSmallIntegerField(
        "ориентир для ребаев, минут",
        default=120,
        help_text=(
            "Примерно столько обычно идёт этап 1. Только подсказка на экране игры: "
            "ребаи закрывает организатор."
        ),
    )
    cash_step = models.PositiveIntegerField("шаг закупки в кэше, лей", default=50)
    # How the live results screen suggests prizes (see club.stats.split_prizes).
    payout_weights = models.CharField(
        "доли призовых мест",
        max_length=100,
        default="3,2,1",
        help_text="По одной доле на призовое место, через запятую: 3,2,1 делит банк 3:2:1.",
    )
    payout_round = models.PositiveIntegerField(
        "округление выплат, лей",
        default=50,
        help_text="Выплаты округляются вниз до этого шага, остаток получает первое место.",
    )
    default_blinds = models.ForeignKey(
        "BlindStructure",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="структура блайндов",
        help_text="Предлагается при старте турнира; у игры своя копия.",
    )

    class Meta:
        verbose_name = "сезон"
        verbose_name_plural = "сезоны"
        ordering = ["-year", "kind"]
        constraints = [
            models.UniqueConstraint(fields=["year", "kind"], name="season_year_kind_unique"),
            models.CheckConstraint(
                condition=Q(kind__in=SeasonKind.values), name="season_kind_valid"
            ),
            models.CheckConstraint(
                condition=Q(chips_per_lei__gte=1), name="season_chips_per_lei_positive"
            ),
            models.CheckConstraint(
                condition=Q(paid_places__gte=1), name="season_paid_places_positive"
            ),
            models.CheckConstraint(
                condition=Q(year__gte=2000, year__lte=2100), name="season_year_range"
            ),
            models.CheckConstraint(
                condition=Q(
                    entry_price__gte=1,
                    rebuy_price__gte=1,
                    addon_price__gte=1,
                    rebuy_minutes__gte=1,
                    cash_step__gte=1,
                ),
                name="season_live_prices_positive",
            ),
            models.CheckConstraint(
                condition=Q(payout_round__gte=1), name="season_payout_round_positive"
            ),
            models.CheckConstraint(
                condition=Q(payout_weights__regex=r"^[1-9][0-9]*(,[1-9][0-9]*)*$"),
                name="season_payout_weights_format",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.year}"

    def _clean_payout_weights(self) -> None:
        """Normalize "3, 2, 1" to "3,2,1" and require one positive weight per paid place."""
        parts = [part.strip() for part in self.payout_weights.split(",")]
        if not all(part.isdigit() and int(part) > 0 for part in parts):
            raise ValidationError(
                {"payout_weights": "Доли: целые числа больше нуля через запятую, например 3,2,1."}
            )
        self.payout_weights = ",".join(str(int(part)) for part in parts)
        if self.paid_places is not None and len(parts) != self.paid_places:
            raise ValidationError(
                {
                    "payout_weights": (
                        f"Нужно {self.paid_places} "
                        f"{ru_plural(self.paid_places, ('доля', 'доли', 'долей'))}: "
                        f"по одной на призовое место, а указано {len(parts)}."
                    )
                }
            )

    def get_absolute_url(self) -> str:
        return reverse("season", args=[self.year, self.kind])

    @property
    def prize_weights(self) -> list[int]:
        return [int(weight) for weight in self.payout_weights.split(",")]

    def clean(self):
        super().clean()
        self._clean_payout_weights()
        if self.pk is None:
            return
        results = Result.objects.filter(game__season=self)
        if self.kind == self.Kind.CASH and results.filter(place__isnull=False).exists():
            raise ValidationError(
                {"kind": "В сезоне есть результаты с местами: кэш-сезоном он быть не может."}
            )
        if self.kind == self.Kind.TOUR and results.filter(chips_out__isnull=False).exists():
            raise ValidationError(
                {"kind": "В сезоне есть результаты с фишками на выходе: турниром он быть не может."}
            )


# What to do when the add-on flag is set on a level instead of a break row.
ADDON_ON_LEVEL = (
    "Аддон ставится на строку-перерыв: добавьте строку с типом «Перерыв» после нужного уровня."
)


class LevelFields(models.Model):
    """One row of a blind structure: a level (blinds set) or a break (no blinds, a label).

    Shared by the templates (BlindLevel) and a game's own copy (live.TimerLevel).
    """

    small_blind = models.PositiveIntegerField("малый блайнд", null=True, blank=True)
    big_blind = models.PositiveIntegerField("большой блайнд", null=True, blank=True)
    ante = models.PositiveIntegerField("анте", default=0, help_text="0: без анте.")
    minutes = models.PositiveSmallIntegerField("минут")
    label = models.CharField(
        "название перерыва",
        max_length=60,
        blank=True,
        default="",
        help_text="Только для перерыва, например «Перерыв · аддон».",
    )
    addon_break = models.BooleanField("перерыв на аддон", default=False)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=Q(minutes__gte=1), name="%(app_label)s_%(class)s_minutes_positive"
            ),
            # A level: 1 <= small <= big (NULLs spelled out: a CHECK passes on NULL). A break: no
            # blinds, no ante, a label.
            models.CheckConstraint(
                condition=(
                    Q(
                        small_blind__isnull=False,
                        big_blind__isnull=False,
                        small_blind__gte=1,
                        big_blind__gte=F("small_blind"),
                    )
                    | Q(small_blind__isnull=True, big_blind__isnull=True, ante=0) & ~Q(label="")
                ),
                name="%(app_label)s_%(class)s_level_or_break",
            ),
            models.CheckConstraint(
                condition=Q(addon_break=False) | Q(big_blind__isnull=True),
                name="%(app_label)s_%(class)s_addon_is_a_break",
            ),
        ]

    @property
    def is_break(self) -> bool:
        return self.big_blind is None

    def clean(self):
        super().clean()
        errors = {}
        has_blinds = self.small_blind is not None or self.big_blind is not None
        if has_blinds:
            if self.small_blind is None or self.big_blind is None:
                errors["big_blind"] = "Укажите оба блайнда или ни одного (перерыв)."
            elif not 1 <= self.small_blind <= self.big_blind:
                errors["big_blind"] = "Большой блайнд не меньше малого, малый от 1."
            if self.addon_break:
                errors["addon_break"] = ADDON_ON_LEVEL
        else:
            if not self.label.strip():
                errors["label"] = "Перерыву нужно название, уровню нужны блайнды."
            if self.ante:
                errors["ante"] = "В перерыве нет анте."
        if errors:
            raise ValidationError(errors)


class BlindStructure(models.Model):
    """A blind structure template. A tournament gets its own copy at the start (live.BlindTimer),
    so editing or deleting a template never changes a game."""

    name = models.CharField("название", max_length=100, unique=True)

    class Meta:
        verbose_name = "структура блайндов"
        verbose_name_plural = "структуры блайндов"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(condition=~Q(name=""), name="blindstructure_name_not_empty"),
        ]

    def __str__(self) -> str:
        return self.name

    def renumber(self) -> None:
        """Positions 1..n in the current order; rows without one (new in the admin) go last."""
        levels = list(self.levels.order_by(F("position").asc(nulls_last=True), "pk"))
        for number, level in enumerate(levels, start=1):
            level.position = number
        BlindLevel.objects.bulk_update(levels, ["position"])


class BlindLevel(LevelFields):
    structure = models.ForeignKey(
        BlindStructure,
        on_delete=models.CASCADE,
        related_name="levels",
        verbose_name="структура",
    )
    # Sortable in the admin (Unfold fills it); renumbered 1..n on save, so no unique constraint.
    position = models.PositiveIntegerField("порядок", null=True, blank=True, db_index=True)

    class Meta(LevelFields.Meta):
        verbose_name = "уровень или перерыв"
        verbose_name_plural = "уровни и перерывы"
        ordering = [F("position").asc(nulls_last=True), "pk"]
        constraints = [
            *LevelFields.Meta.constraints,
            models.UniqueConstraint(
                fields=["structure"],
                condition=Q(addon_break=True),
                name="blindlevel_one_addon_break",
                violation_error_message="Перерыв на аддон в структуре может быть только один.",
            ),
        ]

    def __str__(self) -> str:
        if self.is_break:
            return self.label
        return f"{self.small_blind} / {self.big_blind}"


class LiveStage(models.TextChoices):
    """Where a game recorded at the table is; empty for a finished or historical game."""

    REBUYS = "rebuys", "Этап 1: ребаи"
    ADDON = "addon", "Перерыв на аддон"
    FINAL = "final", "Финальный этап"
    CASH = "cash", "Кэш-игра идёт"


# Which live stages a season kind can have.
LIVE_STAGES_BY_KIND = {
    SeasonKind.TOUR: {LiveStage.REBUYS, LiveStage.ADDON, LiveStage.FINAL},
    SeasonKind.CASH: {LiveStage.CASH},
}


class GameQuerySet(models.QuerySet):
    def finished(self):
        """Games that count: historical ones and live ones whose results are saved."""
        return self.filter(live_stage="")

    def live(self):
        return self.exclude(live_stage="")


class Game(models.Model):
    Stage = LiveStage

    season = models.ForeignKey(
        Season, on_delete=models.PROTECT, related_name="games", verbose_name="сезон"
    )
    date = models.DateField("дата")
    location = models.CharField("место проведения", max_length=100, blank=True, default="")
    live_stage = models.CharField(
        "идёт сейчас",
        max_length=6,
        choices=LiveStage,
        blank=True,
        default="",
        help_text="Пусто: игра завершена. Пока игра идёт, она не входит в статистику.",
    )
    started_at = models.DateTimeField("начало", null=True, blank=True)

    objects = GameQuerySet.as_manager()

    class Meta:
        verbose_name = "игра"
        verbose_name_plural = "игры"
        ordering = ["-date", "season__kind"]
        constraints = [
            models.UniqueConstraint(fields=["season", "date"], name="game_season_date_unique"),
            models.CheckConstraint(
                condition=Q(live_stage__in=["", *LiveStage.values]), name="game_live_stage_valid"
            ),
            models.CheckConstraint(
                condition=Q(live_stage="") | Q(started_at__isnull=False),
                name="game_live_has_start",
            ),
        ]
        indexes = [models.Index(fields=["date"], name="game_date_idx")]

    def __str__(self) -> str:
        return f"{self.season}, {self.date:%d.%m.%Y}"

    def get_absolute_url(self) -> str:
        return reverse("game_detail", args=[self.pk])

    @property
    def is_live(self) -> bool:
        return self.live_stage != ""

    def clean(self):
        super().clean()
        if self.season_id is None:
            return
        errors = {}
        if self.date is not None and self.date.year != self.season.year:
            errors["date"] = f"Дата должна быть в {self.season.year} году."
        if self.live_stage and self.live_stage not in LIVE_STAGES_BY_KIND[self.season.kind]:
            errors["live_stage"] = "Этот этап не подходит к типу сезона."
        if self.live_stage and self.started_at is None:
            errors["started_at"] = "У идущей игры должно быть время начала."
        if errors:
            raise ValidationError(errors)


class Result(models.Model):
    game = models.ForeignKey(
        Game, on_delete=models.CASCADE, related_name="results", verbose_name="игра"
    )
    player = models.ForeignKey(
        Player, on_delete=models.PROTECT, related_name="results", verbose_name="игрок"
    )
    buyin = models.PositiveIntegerField("закупка")
    payout = models.PositiveIntegerField("выплата", default=0)
    place = models.PositiveSmallIntegerField("место", null=True, blank=True)
    chips_out = models.PositiveIntegerField("фишек на выходе", null=True, blank=True)
    # Filled by the live game screens; empty for games entered afterwards.
    rebuys = models.PositiveSmallIntegerField("ребаев", null=True, blank=True)
    addon = models.BooleanField("аддон", null=True, blank=True)
    out_order = models.PositiveSmallIntegerField(
        "порядок выхода",
        null=True,
        blank=True,
        help_text="Кто когда вышел из-за стола: 1 = первым. Пусто: ещё играет.",
    )

    class Meta:
        verbose_name = "результат"
        verbose_name_plural = "результаты"
        ordering = ["game", F("place").asc(nulls_last=True), "-payout"]
        constraints = [
            models.UniqueConstraint(fields=["game", "player"], name="result_game_player_unique"),
            models.CheckConstraint(condition=Q(buyin__gte=1), name="result_buyin_positive"),
            models.CheckConstraint(
                condition=Q(place__isnull=True) | Q(place__gte=1), name="result_place_positive"
            ),
            models.CheckConstraint(
                condition=Q(place__isnull=True) | Q(chips_out__isnull=True),
                name="result_place_xor_chips_out",
            ),
            models.CheckConstraint(
                condition=Q(out_order__isnull=True) | Q(out_order__gte=1),
                name="result_out_order_positive",
            ),
            models.UniqueConstraint(
                fields=["game", "out_order"], name="result_game_out_order_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.player} в игре {self.game}"

    def clean(self):
        # A check constraint cannot see game.season.kind, so the tour/cash rule lives here.
        super().clean()
        try:
            kind = self.game.season.kind
        except ObjectDoesNotExist:
            # Parent game or season not attached yet: that form reports its own errors.
            return
        errors = {}
        if self.place is not None and kind != Season.Kind.TOUR:
            errors["place"] = "Место указывается только в турнирах."
        if self.chips_out is not None and kind != Season.Kind.CASH:
            errors["chips_out"] = "Фишки на выходе указываются только в кэш-играх."
        if self.rebuys is not None and kind != Season.Kind.TOUR:
            errors["rebuys"] = "Ребаи бывают только в турнирах."
        if self.addon is not None and kind != Season.Kind.TOUR:
            errors["addon"] = "Аддон бывает только в турнирах."
        if errors:
            raise ValidationError(errors)
