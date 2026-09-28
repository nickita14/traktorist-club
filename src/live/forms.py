import datetime

from django import forms
from django.utils import timezone

from club.models import Player, Season, SeasonKind

# Start form choice for a season of the current year that does not exist yet: "new-tour".
NEW_SEASON_PREFIX = "new-"


def season_choices(year: int) -> list[tuple[str, str]]:
    """This year's tour and cash seasons first (tour is the default), offered as new when they do
    not exist yet; then older seasons, newest first."""
    seasons = {(s.year, s.kind): s for s in Season.objects.all()}
    choices = []
    for kind, label in SeasonKind.choices:
        season = seasons.get((year, kind))
        if season is None:
            choices.append((f"{NEW_SEASON_PREFIX}{kind}", f"{label} {year} · новый сезон"))
        else:
            choices.append((str(season.pk), str(season)))
    older = sorted(
        (s for (y, _), s in seasons.items() if y != year),
        key=lambda s: (-s.year, s.kind != SeasonKind.TOUR),
    )
    return choices + [(str(s.pk), str(s)) for s in older]


class StartForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput)
    season = forms.ChoiceField(label="Сезон")
    date = forms.DateField(label="Дата", widget=forms.DateInput(attrs={"type": "date"}))
    location = forms.CharField(label="Место", max_length=100, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["season"].choices = season_choices(timezone.localdate().year)

    def clean(self):
        cleaned = super().clean()
        choice, date = cleaned.get("season"), cleaned.get("date")
        if choice is None or date is None:
            return cleaned
        if choice.startswith(NEW_SEASON_PREFIX):
            cleaned["kind"] = choice.removeprefix(NEW_SEASON_PREFIX)
            year = timezone.localdate().year
            cleaned["season_obj"] = None
        else:
            season = Season.objects.get(pk=choice)
            cleaned["season_obj"], cleaned["kind"], year = season, season.kind, season.year
        if date.year != year:
            self.add_error("date", f"Дата должна быть в {year} году.")
        return cleaned


class NewPlayerForm(forms.ModelForm):
    key = forms.UUIDField(widget=forms.HiddenInput)

    class Meta:
        model = Player
        fields = ["name", "nickname"]


class ExitForm(forms.Form):
    """A cash player's exit: final stack and cash paid, whole numbers."""

    key = forms.UUIDField(widget=forms.HiddenInput)
    chips = forms.IntegerField(
        label="Фишек на выходе",
        min_value=0,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )
    paid = forms.IntegerField(
        label="Выдано, лей",
        min_value=0,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "autocomplete": "off"}),
    )


def parse_int(value: str | None, *, minimum: int = 0) -> int | None:
    """A whole number from a form field, or None for blank or invalid input."""
    try:
        number = int((value or "").strip())
    except ValueError:
        return None
    return number if number >= minimum else None


def today() -> datetime.date:
    return timezone.localdate()
