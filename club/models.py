from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.text import slugify

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
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.year}"

    def clean(self):
        super().clean()
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


class Game(models.Model):
    season = models.ForeignKey(
        Season, on_delete=models.PROTECT, related_name="games", verbose_name="сезон"
    )
    date = models.DateField("дата")
    location = models.CharField("место проведения", max_length=100, blank=True, default="")

    class Meta:
        verbose_name = "игра"
        verbose_name_plural = "игры"
        ordering = ["-date", "season__kind"]
        constraints = [
            models.UniqueConstraint(fields=["season", "date"], name="game_season_date_unique"),
        ]
        indexes = [models.Index(fields=["date"], name="game_date_idx")]

    def __str__(self) -> str:
        return f"{self.season}, {self.date:%d.%m.%Y}"

    def clean(self):
        super().clean()
        if self.season_id is None or self.date is None:
            return
        if self.date.year != self.season.year:
            raise ValidationError({"date": f"Дата должна быть в {self.season.year} году."})


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
        if errors:
            raise ValidationError(errors)
