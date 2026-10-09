from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db.models import Case, Count, F, IntegerField, Min, Q, Sum, When, Window
from django.shortcuts import redirect
from django.utils.html import format_html
from django_otp.plugins.otp_totp.models import TOTPDevice
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    DropdownFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display
from unfold.forms import (
    AdminPasswordChangeForm,
    PaginationInlineFormSet,
    UserChangeForm,
    UserCreationForm,
)
from unfold.widgets import UnfoldAdminSelectWidget

from club import stats
from club.formatting import format_money, format_net
from club.models import (
    ADDON_ON_LEVEL,
    AchievementSettings,
    BlindLevel,
    BlindStructure,
    Game,
    Player,
    RankLadder,
    RankStep,
    Result,
    Season,
    SeasonKind,
)
from live.models import LiveAction

# Unfold 0.108 ships no translations, so its default search placeholder ("Type to search") stays
# English; a ModelAdmin's search_help_text replaces it.

# Numeric columns: right-aligned header and cells (Unfold's "price" formatting) in PT Mono.
number = {"formatting": "price"}


def number_cell(value: int | None) -> str:
    """A count or amount like on the public pages: thousands separator, real minus in accent."""
    if value is None:
        return ""
    css = "admin-num val-neg" if value < 0 else "admin-num"
    return format_html('<span class="{}">{}</span>', css, format_money(value))


def net_cell(value: int | None) -> str:
    """A net result: "+" for a gain, a real minus in accent for a loss, like the public net."""
    if value is None:
        return ""
    css = "admin-num val-neg" if value < 0 else "admin-num"
    return format_html('<span class="{}">{}</span>', css, format_net(value))


# Auth models re-registered with Unfold styling (https://unfoldadmin.com/docs/installation/auth/).
admin.site.unregister(User)
admin.site.unregister(Group)
# django-otp's device admin is a plain Django ModelAdmin. Devices are managed over SSH with
# `manage.py totp_enroll` instead.
admin.site.unregister(TOTPDevice)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    search_help_text = "Логин, имя или email"


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    search_help_text = "Название группы"


def year_filter(field_path: str) -> type[DropdownFilter]:
    """Dropdown of existing season years, filtering on ``field_path``."""

    class YearFilter(DropdownFilter):
        title = "год"
        parameter_name = "year"

        def lookups(self, request, model_admin):
            years = Season.objects.order_by("-year").values_list("year", flat=True).distinct()
            return [(str(year), str(year)) for year in years]

        def queryset(self, request, queryset):
            value = self.value()
            if value and value.isdigit():
                return queryset.filter(**{field_path: int(value)})
            return queryset

    return YearFilter


@admin.register(Player)
class PlayerAdmin(ModelAdmin):
    list_display = ["name", "nickname", "slug", "games_played", "net"]
    search_fields = ["name", "nickname", "slug"]
    search_help_text = "Имя, ник или slug"
    fields = ["name", "nickname", "slug"]

    def get_queryset(self, request):
        return stats.annotate_player_totals(super().get_queryset(request))

    @display(description="игр", ordering="games_played", **number)
    def games_played(self, obj):
        return number_cell(obj.games_played)

    @display(description="итог, лей", ordering="net", **number)
    def net(self, obj):
        return net_cell(obj.net)


@admin.register(Season)
class SeasonAdmin(ModelAdmin):
    list_display = [
        "__str__",
        "year_number",
        "kind",
        "paid_places_number",
        "chips_per_lei_number",
        "games_count",
        "players_count",
        "sheet_managed",
    ]
    list_filter = [("kind", ChoicesDropdownFilter), year_filter("year")]
    list_filter_submit = True
    fields = [
        "year",
        "kind",
        "chips_per_lei",
        "paid_places",
        "entry_price",
        "rebuy_price",
        "addon_price",
        "rebuy_minutes",
        "payout_weights",
        "payout_round",
        "default_blinds",
        "cash_step",
        "sheet_managed",
    ]
    # Prices of the live game screens: only the ones of the chosen kind (Alpine expressions).
    conditional_fields = {
        "entry_price": "kind == 'tour'",
        "rebuy_price": "kind == 'tour'",
        "addon_price": "kind == 'tour'",
        "rebuy_minutes": "kind == 'tour'",
        "payout_weights": "kind == 'tour'",
        "payout_round": "kind == 'tour'",
        "default_blinds": "kind == 'tour'",
        "cash_step": "kind == 'cash'",
    }

    def get_queryset(self, request):
        return stats.annotate_season_totals(super().get_queryset(request))

    def get_readonly_fields(self, request, obj=None):
        # Whether the importer may write to a season is the superusers' call (the sheet sync is
        # theirs too), not the organizers'.
        readonly = super().get_readonly_fields(request, obj)
        return readonly if request.user.is_superuser else [*readonly, "sheet_managed"]

    @display(description="год", ordering="year", **number)
    def year_number(self, obj):
        # A year, not an amount: no thousands separator.
        return format_html('<span class="admin-num">{}</span>', obj.year)

    @display(description="призовых мест", ordering="paid_places", **number)
    def paid_places_number(self, obj):
        return number_cell(obj.paid_places)

    @display(description="фишек за 1 лей", ordering="chips_per_lei", **number)
    def chips_per_lei_number(self, obj):
        return number_cell(obj.chips_per_lei)

    @display(description="игр", ordering="games_count", **number)
    def games_count(self, obj):
        return number_cell(obj.games_count)

    @display(description="игроков", ordering="players_count", **number)
    def players_count(self, obj):
        return number_cell(obj.players_count)


class BlindLevelFormSet(PaginationInlineFormSet):
    """A structure needs at least one level with blinds and at most one add-on break."""

    def clean(self):
        super().clean()
        kept = [
            form.cleaned_data
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        if not any(data.get("big_blind") for data in kept):
            raise ValidationError("В структуре нужен хотя бы один уровень с блайндами.")
        if sum(1 for data in kept if data.get("addon_break")) > 1:
            raise ValidationError("Перерыв на аддон в структуре может быть только один.")


class RowKind:
    LEVEL = "level"
    BREAK = "break"
    CHOICES = [(LEVEL, "Уровень"), (BREAK, "Перерыв")]


class BlindLevelForm(forms.ModelForm):
    """One row with an explicit type: a level has blinds (and maybe an ante), a break has a label
    (and maybe the add-on). The model tells them apart by the empty blinds of a break.

    assets/js/admin-blinds.js hides and disables the other type's inputs; without it, the type
    still decides what is stored.
    """

    kind = forms.ChoiceField(
        label="Тип",
        choices=RowKind.CHOICES,
        initial=RowKind.LEVEL,
        widget=UnfoldAdminSelectWidget(attrs={"data-row-kind": ""}),
    )

    class Meta:
        model = BlindLevel
        fields = ["small_blind", "big_blind", "ante", "minutes", "label", "addon_break"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.is_break:
            self.initial["kind"] = RowKind.BREAK
        # A break posts no ante (its input is disabled): blank means 0.
        self.fields["ante"].required = False

    def clean(self):
        data = super().clean()
        if data.get("kind") == RowKind.BREAK:
            data |= {"small_blind": None, "big_blind": None, "ante": 0}
            if not (data.get("label") or "").strip():
                self.add_error("label", "Укажите название перерыва, например «Перерыв · аддон».")
        else:
            if data.get("addon_break"):
                self.add_error("addon_break", ADDON_ON_LEVEL)
            data["label"] = ""
            data["ante"] = data.get("ante") or 0
            for field in ("small_blind", "big_blind"):
                if data.get(field) is None and field not in self.errors:
                    self.add_error(field, "Укажите малый и большой блайнд.")
        return data


class BlindLevelInline(TabularInline):
    model = BlindLevel
    form = BlindLevelForm
    formset = BlindLevelFormSet
    # "Ур." comes first: Unfold draws the drag handle in the first column.
    fields = [
        "level_number",
        "kind",
        "small_blind",
        "big_blind",
        "ante",
        "minutes",
        "label",
        "addon_break",
        "position",
    ]
    readonly_fields = ["level_number"]
    # Drag to reorder (Unfold); new rows go to the end and can be moved after saving.
    ordering_field = "position"
    hide_ordering_field = True
    show_title = False
    extra = 0
    show_count = True

    def get_queryset(self, request):
        # Level numbers skip breaks: a running count of the rows with blinds.
        is_level = Case(
            When(big_blind__isnull=False, then=1), default=0, output_field=IntegerField()
        )
        return (
            super()
            .get_queryset(request)
            .annotate(
                level_number=Window(
                    Sum(is_level),
                    partition_by=[F("structure")],
                    order_by=[F("position").asc(nulls_last=True), F("pk").asc()],
                )
            )
        )

    @display(description="Ур.")
    def level_number(self, obj):
        # admin-blinds.js keeps the numbers right while rows are dragged or change type.
        if obj is None or obj.pk is None:
            number = ""
        elif obj.is_break:
            number = "перерыв"
        else:
            number = obj.level_number
        return format_html('<span class="admin-num" data-level-number>{}</span>', number)

    class Media:
        js = ["js/admin-blinds.js"]


@admin.register(BlindStructure)
class BlindStructureAdmin(ModelAdmin):
    list_display = ["name", "levels_count", "breaks_count"]
    search_fields = ["name"]
    search_help_text = "Название структуры"
    fields = ["name"]
    inlines = [BlindLevelInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                levels_count=Count("levels", filter=Q(levels__big_blind__isnull=False)),
                breaks_count=Count("levels", filter=Q(levels__big_blind__isnull=True)),
            )
        )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        form.instance.renumber()

    @display(description="уровней", ordering="levels_count", **number)
    def levels_count(self, obj):
        return number_cell(obj.levels_count)

    @display(description="перерывов", ordering="breaks_count", **number)
    def breaks_count(self, obj):
        return number_cell(obj.breaks_count)


class ResultInline(TabularInline):
    model = Result
    fields = [
        "player",
        "buyin",
        "payout",
        "place",
        "chips_out",
        "rebuys",
        "addon",
        "out_order",
        "net",
    ]
    readonly_fields = ["net"]
    autocomplete_fields = ["player"]
    extra = 0
    show_count = True

    # Fields that only make sense for the other season kind. The add view has no season yet, so it
    # shows both (Unfold's conditional_fields cover the main form only, not inlines).
    HIDDEN_BY_KIND = {
        SeasonKind.TOUR: {"chips_out"},
        SeasonKind.CASH: {"place", "rebuys", "addon"},
    }

    def get_fields(self, request, obj=None):
        # obj is the parent Game here.
        fields = super().get_fields(request, obj)
        if obj is None:
            return fields
        hidden = self.HIDDEN_BY_KIND[obj.season.kind]
        return [field for field in fields if field not in hidden]

    def get_queryset(self, request):
        return stats.annotate_result_net(super().get_queryset(request)).select_related("player")

    @display(description="итог, лей")
    def net(self, obj):
        # New unsaved rows have no annotation yet.
        return net_cell(getattr(obj, "net", None))


LEFTOVER_MISMATCH_LABEL = "не сходится"


class LeftoverMismatchFilter(DropdownFilter):
    title = "остаток не сходится"
    parameter_name = "leftover_mismatch"

    def lookups(self, request, model_admin):
        return [("yes", "Да"), ("no", "Нет")]

    def queryset(self, request, queryset):
        # The queryset is already annotated by stats.annotate_leftover_check.
        if self.value() == "yes":
            return queryset.filter(leftover_mismatch=True)
        if self.value() == "no":
            return queryset.filter(leftover_mismatch=False)
        return queryset


@admin.register(Game)
class GameAdmin(ModelAdmin):
    list_display = [
        "date",
        "season",
        "location",
        "players_count",
        "buyin_total",
        "payout_total",
        "leftover",
        "leftover_mismatch",
    ]
    list_filter = [
        LeftoverMismatchFilter,
        year_filter("season__year"),
        ("season__kind", ChoicesDropdownFilter),
        ("season", RelatedDropdownFilter),
    ]
    list_filter_submit = True
    list_select_related = ["season"]
    search_fields = ["location", "results__player__name", "results__player__nickname"]
    search_help_text = "Место или игрок"
    date_hierarchy = "date"
    fields = ["season", "date", "location", "live_stage", "started_at"]
    readonly_fields = ["players_count", "buyin_total", "payout_total", "leftover"]
    inlines = [ResultInline]
    actions = ["fill_places"]

    def get_queryset(self, request):
        return stats.annotate_leftover_check(super().get_queryset(request))

    def get_deleted_objects(self, objs, request):
        # A game's live action log is read-only on its own (nobody may delete a row of it) but
        # goes with its game, so it must not block deleting the game.
        deleted, counts, perms_needed, protected = super().get_deleted_objects(objs, request)
        perms_needed.discard(LiveAction._meta.verbose_name)
        return deleted, counts, perms_needed, protected

    def get_fields(self, request, obj=None):
        # Totals only make sense for a saved game.
        return self.fields + (self.readonly_fields if obj else [])

    @display(description="игроков", ordering="players_count", **number)
    def players_count(self, obj):
        return number_cell(obj.players_count)

    @display(description="закупки, лей", ordering="buyin_total", **number)
    def buyin_total(self, obj):
        return number_cell(obj.buyin_total)

    @display(description="выплаты, лей", ordering="payout_total", **number)
    def payout_total(self, obj):
        return number_cell(obj.payout_total)

    @display(description="остаток, лей", ordering="leftover", **number)
    def leftover(self, obj):
        return number_cell(obj.leftover)

    # A danger label only on the games that break the rule; the rest show Unfold's "-".
    @display(
        description="проверка остатка",
        ordering="leftover_mismatch",
        label={LEFTOVER_MISMATCH_LABEL: "danger"},
    )
    def leftover_mismatch(self, obj):
        return LEFTOVER_MISMATCH_LABEL if obj.leftover_mismatch else ""

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        # Non-blocking: games are filled in gradually, so only warn once the results are saved.
        game = stats.annotate_leftover_check(Game.objects.select_related("season")).get(
            pk=form.instance.pk
        )
        for warning in (stats.leftover_warning(game), stats.buyin_warning(game)):
            if warning:
                self.message_user(request, warning, messages.WARNING)

    @action(description="Проставить места по выплатам", permissions=["change"])
    def fill_places(self, request, queryset):
        games = list(queryset.select_related("season"))
        filled = sum(stats.apply_suggested_places(game) for game in games)
        skipped = sum(1 for game in games if game.season.kind != SeasonKind.TOUR)
        message = f"Проставлено мест: {filled}."
        if skipped:
            message += f" Кэш-игры пропущены (в них нет мест): {skipped}."
        self.message_user(request, message)


class RankStepInline(TabularInline):
    model = RankStep
    fields = ["threshold", "title"]
    ordering = ["threshold"]
    extra = 0
    show_count = True


@admin.register(RankLadder)
class RankLadderAdmin(ModelAdmin):
    """The three ladders are fixed by their codes (club.achievements reads them by code): titles
    and steps are edited here, ladders are never added or deleted."""

    list_display = ["title", "code", "steps_count", "first_threshold"]
    search_fields = ["title", "steps__title"]
    search_help_text = "Лестница или звание"
    fields = ["title", "code"]
    readonly_fields = ["code"]
    inlines = [RankStepInline]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(steps_count=Count("steps"), first_threshold=Min("steps__threshold"))
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description="ступеней", ordering="steps_count", **number)
    def steps_count(self, obj):
        return number_cell(obj.steps_count)

    @display(description="первый порог", ordering="first_threshold", **number)
    def first_threshold(self, obj):
        return number_cell(obj.first_threshold)


@admin.register(AchievementSettings)
class AchievementSettingsAdmin(ModelAdmin):
    """One row of parameters: the list goes straight to it."""

    fieldsets = [
        ("Титулы", {"fields": ["always_itm_min_tournaments"]}),
        (
            "Знаки отличия",
            {"fields": ["hat_trick_length", "comeback_min_rebuys", "no_skip_min_evenings"]},
        ),
        ("Грамоты", {"fields": ["itm_series_steps", "evening_series_steps"]}),
    ]

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not AchievementSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        row = AchievementSettings.objects.first()
        if row is not None:
            return redirect("admin:club_achievementsettings_change", row.pk)
        return super().changelist_view(request, extra_context)
