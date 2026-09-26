from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    DropdownFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from club import stats
from club.models import Game, Player, Result, Season, SeasonKind

# Auth models re-registered with Unfold styling (https://unfoldadmin.com/docs/installation/auth/).
admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


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
    fields = ["name", "nickname", "slug"]

    def get_queryset(self, request):
        return stats.annotate_player_totals(super().get_queryset(request))

    @display(description="игр", ordering="games_played")
    def games_played(self, obj):
        return obj.games_played

    @display(description="итог, лей", ordering="net")
    def net(self, obj):
        return obj.net


@admin.register(Season)
class SeasonAdmin(ModelAdmin):
    list_display = [
        "__str__",
        "year",
        "kind",
        "paid_places",
        "chips_per_lei",
        "games_count",
        "players_count",
    ]
    list_filter = [("kind", ChoicesDropdownFilter), year_filter("year")]
    list_filter_submit = True

    def get_queryset(self, request):
        return stats.annotate_season_totals(super().get_queryset(request))

    @display(description="игр", ordering="games_count")
    def games_count(self, obj):
        return obj.games_count

    @display(description="игроков", ordering="players_count")
    def players_count(self, obj):
        return obj.players_count


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
        return getattr(obj, "net", None)


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

    @display(description="игроков", ordering="players_count")
    def players_count(self, obj):
        return obj.players_count

    @display(description="закупки, лей", ordering="buyin_total")
    def buyin_total(self, obj):
        return obj.buyin_total

    @display(description="выплаты, лей", ordering="payout_total")
    def payout_total(self, obj):
        return obj.payout_total

    @display(description="остаток, лей", ordering="leftover")
    def leftover(self, obj):
        return obj.leftover

    @display(description="Остаток не сходится", boolean=True, ordering="leftover_mismatch")
    def leftover_mismatch(self, obj):
        return obj.leftover_mismatch

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
