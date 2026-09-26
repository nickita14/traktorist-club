from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    DropdownFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from club import stats
from club.formatting import format_money, format_net
from club.models import Game, Player, Result, Season, SeasonKind

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
    ]
    list_filter = [("kind", ChoicesDropdownFilter), year_filter("year")]
    list_filter_submit = True

    def get_queryset(self, request):
        return stats.annotate_season_totals(super().get_queryset(request))

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


class ResultInline(TabularInline):
    model = Result
    fields = ["player", "buyin", "payout", "place", "chips_out", "net"]
    readonly_fields = ["net"]
    autocomplete_fields = ["player"]
    extra = 0
    show_count = True

    # Fields that only make sense for the other season kind. The add view has no season yet, so it
    # shows both (Unfold's conditional_fields cover the main form only, not inlines).
    HIDDEN_BY_KIND = {SeasonKind.TOUR: "chips_out", SeasonKind.CASH: "place"}

    def get_fields(self, request, obj=None):
        # obj is the parent Game here.
        fields = super().get_fields(request, obj)
        if obj is None:
            return fields
        hidden = self.HIDDEN_BY_KIND[obj.season.kind]
        return [field for field in fields if field != hidden]

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
    fields = ["season", "date", "location"]
    readonly_fields = ["players_count", "buyin_total", "payout_total", "leftover"]
    inlines = [ResultInline]
    actions = ["fill_places"]

    def get_queryset(self, request):
        return stats.annotate_leftover_check(super().get_queryset(request))

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
        warning = stats.leftover_warning(game)
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
