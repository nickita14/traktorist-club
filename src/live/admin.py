import hashlib

from django import forms
from django.contrib import admin
from django.db.models import Count
from django.shortcuts import redirect
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import ChoicesDropdownFilter
from unfold.decorators import action, display
from unfold.widgets import (
    UnfoldAdminSelectWidget,
    UnfoldAdminTextInputWidget,
    UnfoldBooleanSwitchWidget,
)

from club.admin import number, number_cell
from live import themes
from live.models import LiveAction, Theme, ThemeCover, ThemeEvent, ThemeSound


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
COVER_HELP = "jpg, png или webp, до 3 МБ. Без обложки экран перерыва как обычно."


def listen(sound: ThemeSound | None) -> str:
    if sound is None:
        return ""
    return format_html(
        '<audio controls preload="none" src="{}" aria-label="Прослушать: {}"></audio>',
        themes.sound_url(sound),
        sound.name,
    )


def file_info(content_type_label: str, size: int) -> str:
    return format_html(
        '{} · <span class="admin-num">{}</span>', content_type_label, themes.size_label(size)
    )


class SoundSelect(UnfoldAdminSelectWidget):
    """The event row's sound select: each option carries its sound's URL, so
    assets/js/admin-theme-sounds.js can play the selected sound before the row is saved."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        sound = getattr(value, "instance", None)
        if sound is not None:
            option["attrs"]["data-sound-url"] = themes.sound_url(sound)
        return option


class ThemeEventForm(forms.ModelForm):
    class Meta:
        model = ThemeEvent
        fields = ["kind", "enabled", "title", "caption", "style", "seconds", "sound"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["caption"].help_text = themes.PLACEHOLDER_HELP
        self.fields["sound"].help_text = "Звуки загружаются на странице «Звуки»."


class ThemeEventInline(TabularInline):
    model = ThemeEvent
    form = ThemeEventForm
    fields = ["kind", "enabled", "title", "caption", "style", "seconds", "sound", "heard"]
    readonly_fields = ["heard"]
    extra = 0
    max_num = len(ThemeEvent._meta.get_field("kind").choices)
    show_count = True

    class Media:
        js = ["js/admin-theme-sounds.js"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sound").defer("sound__data")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "sound":
            kwargs["queryset"] = ThemeSound.objects.defer("data")
            kwargs["widget"] = SoundSelect
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @display(description="прослушать")
    def heard(self, obj):
        # Always rendered (hidden without a sound): the script points it at the selected sound.
        sound = obj.sound if obj and obj.sound_id else None
        if sound is None:
            return mark_safe(
                '<audio controls preload="none" data-sound-player aria-label="Прослушать" hidden>'
                "</audio>"
            )
        return format_html(
            '<audio controls preload="none" data-sound-player src="{}" '
            'aria-label="Прослушать: {}"></audio>',
            themes.sound_url(sound),
            sound.name,
        )


class ThemeForm(forms.ModelForm):
    cover = forms.FileField(
        label="Обложка",
        required=False,
        help_text=COVER_HELP,
        widget=forms.FileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp"}),
    )
    remove_cover = forms.BooleanField(
        label="Убрать обложку", required=False, widget=UnfoldBooleanSwitchWidget
    )

    class Meta:
        model = Theme
        fields = ["name", "is_active", "accent", "tips"]
        widgets = {
            "accent": UnfoldAdminTextInputWidget(attrs={"placeholder": "#F2B630", "size": 8})
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cover = None

    def clean_cover(self):
        upload = self.cleaned_data.get("cover")
        if upload:
            self._cover = themes.read_cover(upload)
        return upload

    def clean(self):
        cleaned = super().clean()
        if self._cover is not None and cleaned.get("remove_cover"):
            self.add_error("remove_cover", "Либо новая обложка, либо убрать.")
        return cleaned

    def save_cover(self, theme: Theme) -> None:
        """Called after the theme is saved: the cover row needs its pk."""
        if self._cover is not None:
            themes.store_cover(theme, *self._cover)
        elif self.cleaned_data.get("remove_cover"):
            ThemeCover.objects.filter(theme=theme).delete()


@admin.register(Theme)
class ThemeAdmin(ModelAdmin):
    """A theme night: captions, presets, sounds and the break screen for the display (табло)."""

    form = ThemeForm
    list_display = ["name", "is_active", "events_count", "updated_at"]
    search_fields = ["name"]
    search_help_text = "Название тематики"
    readonly_fields = ["accent_swatch", "cover_shown"]
    inlines = [ThemeEventInline]
    actions_detail = ["preview"]

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("cover")
            .defer("cover__data")
            .annotate(events_count=Count("events"))
        )

    def get_fields(self, request, obj=None):
        fields = ["name", "is_active", "tips", "accent"]
        if obj is not None and obj.accent:
            fields.append("accent_swatch")
        if themes.theme_cover(obj) is not None:
            return [*fields, "cover_shown", "cover", "remove_cover"]
        return [*fields, "cover"]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        form.save_cover(obj)

    @display(description="событий", ordering="events_count", **number)
    def events_count(self, obj):
        return number_cell(obj.events_count)

    @display(description="цвет")
    def accent_swatch(self, obj):
        # A plain SVG (no style attribute: the admin CSP has no inline styles for us to rely on).
        return format_html(
            '<svg class="admin-swatch" width="48" height="24" aria-hidden="true">'
            '<rect width="48" height="24" fill="{}"/></svg>',
            obj.accent,
        )

    @display(description="текущая обложка")
    def cover_shown(self, obj):
        cover = themes.theme_cover(obj)
        if cover is None:
            return ""
        return format_html(
            '<a href="{url}" target="_blank" rel="noopener"><img class="admin-cover" src="{url}" '
            'alt="Обложка тематики"></a><p>{info}</p>',
            url=themes.cover_url(cover),
            info=file_info(cover.get_content_type_display(), cover.size),
        )

    @action(description="Проверить на табло", url_path="preview", attrs={"target": "_blank"})
    def preview(self, request, object_id):
        return redirect("live:theme_preview", object_id)


class ThemeSoundForm(forms.ModelForm):
    upload = forms.FileField(
        label="Файл",
        required=False,
        help_text=SOUND_HELP,
        widget=forms.FileInput(attrs={"accept": ".mp3,.ogg,.wav"}),
    )

    class Meta:
        model = ThemeSound
        fields = ["name", "upload"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # A new file replaces the sound everywhere it is used; its URL (the hash) changes, so
            # displays fetch it on their next sync.
            self.fields["upload"].help_text = f"{SOUND_HELP} Новый файл заменит звук в событиях."
        else:
            self.fields["upload"].required = True
        self._sound = None

    def clean_upload(self):
        upload = self.cleaned_data.get("upload")
        if upload:
            data, content_type = themes.read_sound(upload)
            digest = hashlib.sha256(data).hexdigest()
            same = (
                ThemeSound.objects.filter(sha256=digest)
                .exclude(pk=self.instance.pk)
                .only("name")
                .first()
            )
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
    readonly_fields = ["heard", "file_shown"]

    def get_queryset(self, request):
        return super().get_queryset(request).defer("data").annotate(uses=Count("events"))

    def get_fields(self, request, obj=None):
        return ["name", "heard", "file_shown", "upload"] if obj else ["name", "upload"]

    @display(description="размер, КБ", ordering="size", **number)
    def size_kb(self, obj):
        return number_cell(max(round(obj.size / 1024), 1))

    @display(description="в событиях", ordering="uses", **number)
    def uses(self, obj):
        return number_cell(obj.uses)

    @display(description="прослушать")
    def heard(self, obj):
        return listen(obj)

    @display(description="формат и размер")
    def file_shown(self, obj):
        return file_info(obj.get_content_type_display(), obj.size)
