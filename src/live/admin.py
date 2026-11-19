import hashlib

from django import forms
from django.contrib import admin
from django.db.models import Count
from django.shortcuts import redirect
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter
from unfold.decorators import action, display

from club.admin import number, number_cell
from live import themes
from live.models import LiveAction, Theme, ThemeEvent, ThemeSound


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
        "place",
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


# Theme nights (Тематика).

SOUND_HELP = "mp3, ogg или wav, до 1 МБ."


def listen(sound: ThemeSound | None) -> str:
    if sound is None:
        return ""
    return format_html(
        '<audio controls preload="none" src="{}" aria-label="Прослушать: {}"></audio>',
        themes.sound_url(sound),
        sound.name,
    )


class ThemeEventForm(forms.ModelForm):
    upload = forms.FileField(label="загрузить звук", required=False, help_text=SOUND_HELP)

    class Meta:
        model = ThemeEvent
        fields = ["kind", "enabled", "title", "caption", "style", "seconds", "sound", "upload"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["caption"].help_text = themes.PLACEHOLDER_HELP
        self._sound = None

    def clean_upload(self):
        upload = self.cleaned_data.get("upload")
        if upload:
            self._sound = themes.read_sound(upload)
        return upload

    def save(self, commit=True):
        if self._sound is not None:
            data, content_type = self._sound
            self.instance.sound = themes.store_sound(
                self.cleaned_data["upload"].name, data, content_type
            )
        return super().save(commit)


class ThemeEventInline(TabularInline):
    model = ThemeEvent
    form = ThemeEventForm
    fields = ["kind", "enabled", "title", "caption", "style", "seconds", "sound", "upload", "heard"]
    readonly_fields = ["heard"]
    extra = 0
    max_num = len(ThemeEvent._meta.get_field("kind").choices)
    show_count = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sound").defer("sound__data")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "sound":
            kwargs["queryset"] = ThemeSound.objects.defer("data")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @display(description="прослушать")
    def heard(self, obj):
        return listen(obj.sound if obj and obj.sound_id else None)


@admin.register(Theme)
class ThemeAdmin(ModelAdmin):
    """A theme night: captions, presets and sounds for the display (табло)."""

    list_display = ["name", "is_active", "events_count", "updated_at"]
    search_fields = ["name"]
    search_help_text = "Название тематики"
    fields = ["name", "is_active", "tips"]
    inlines = [ThemeEventInline]
    actions_detail = ["preview"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(events_count=Count("events"))

    @display(description="событий", ordering="events_count", **number)
    def events_count(self, obj):
        return number_cell(obj.events_count)

    @action(description="Проверить на табло", url_path="preview", attrs={"target": "_blank"})
    def preview(self, request, object_id):
        return redirect("live:theme_preview", object_id)


class ThemeSoundForm(forms.ModelForm):
    upload = forms.FileField(label="файл", required=False, help_text=SOUND_HELP)

    class Meta:
        model = ThemeSound
        fields = ["name", "upload"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The bytes of a stored sound never change: a new file is a new sound.
        if self.instance.pk:
            del self.fields["upload"]
        else:
            self.fields["upload"].required = True
        self._sound = None

    def clean_upload(self):
        upload = self.cleaned_data.get("upload")
        if upload:
            data, content_type = themes.read_sound(upload)
            digest = hashlib.sha256(data).hexdigest()
            same = ThemeSound.objects.filter(sha256=digest).only("name").first()
            if same is not None:
                raise forms.ValidationError(f"Этот звук уже загружен: «{same.name}».")
            self._sound = data, content_type, digest
        return upload

    def save(self, commit=True):
        if self._sound is not None:
            data, content_type, digest = self._sound
            self.instance.data = data
            self.instance.content_type = content_type
            self.instance.size = len(data)
            self.instance.sha256 = digest
        return super().save(commit)


@admin.register(ThemeSound)
class ThemeSoundAdmin(ModelAdmin):
    form = ThemeSoundForm
    list_display = ["name", "content_type", "size_kb", "uses", "created_at"]
    search_fields = ["name"]
    search_help_text = "Название звука"
    readonly_fields = ["heard"]

    def get_queryset(self, request):
        return super().get_queryset(request).defer("data").annotate(uses=Count("events"))

    def get_fields(self, request, obj=None):
        return ["name", "heard"] if obj else ["name", "upload"]

    @display(description="размер, КБ", ordering="size", **number)
    def size_kb(self, obj):
        return number_cell(max(round(obj.size / 1024), 1))

    @display(description="в событиях", ordering="uses", **number)
    def uses(self, obj):
        return number_cell(obj.uses)

    @display(description="прослушать")
    def heard(self, obj):
        return listen(obj)
