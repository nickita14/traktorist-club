from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import ChoicesDropdownFilter

from live.models import LiveAction


@admin.register(LiveAction)
class LiveActionAdmin(ModelAdmin):
    """The table log, read-only: who pressed what and when. Organizers have no permission for it,
    so only superusers see it."""

    list_display = ["created_at", "summary", "kind", "game", "user", "undone_at"]
    list_filter = [("kind", ChoicesDropdownFilter)]
    list_filter_submit = True
    list_select_related = ["game__season", "user"]
    search_fields = ["summary"]
    search_help_text = "Текст действия или имя игрока"
    date_hierarchy = "created_at"
    fields = [
        "created_at",
        "summary",
        "kind",
        "game",
        "result",
        "user",
        "before",
        "after",
        "undone_at",
        "key",
    ]
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
