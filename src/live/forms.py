import datetime

from django import forms
from django.utils import timezone

from club.models import BlindStructure, Player, Season, SeasonKind
from live.actions import SEASON_BLINDS


class StartForm(forms.Form):
    """The season is not chosen: start_game resolves it from the kind and the date's year."""

    key = forms.UUIDField(widget=forms.HiddenInput)
    kind = forms.ChoiceField(label="Игра", choices=SeasonKind.choices)
    date = forms.DateField(label="Дата", widget=forms.DateInput(attrs={"type": "date"}))
    location = forms.CharField(label="Место", max_length=100, required=False)
    # Tournaments only: a structure pk, the season's default or "" for no timer.
    blinds = forms.ChoiceField(label="Таймер блайндов", required=False)
    # A date in another year than today's is started only once confirmed: the confirmation it
    # was asked for ("2027:cash", see confirmation()) comes back with the second submit.
    confirm = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["blinds"].choices = [
            (SEASON_BLINDS, "Как в сезоне"),
            ("", "Без таймера"),
            *((str(pk), name) for pk, name in BlindStructure.objects.values_list("pk", "name")),
        ]

    def clean_blinds(self) -> int | str | None:
        value = self.cleaned_data["blinds"]
        return int(value) if value.isdigit() else (value or None)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("kind") == SeasonKind.CASH:
            # A cash game has no timer, whatever was posted (even a value that is no choice).
            self.errors.pop("blinds", None)
            cleaned["blinds"] = None
        return cleaned

    def confirmation(self, today: datetime.date) -> tuple[str, str] | None:
        """For a valid form: None when the game may start, else the confirmation to ask for (its
        token, its question). A date in today's year never asks; another year asks until the
        form brings back the token of this year and kind, so a date or kind changed after the
        question asks again, and a stale token with today's year is simply ignored."""
        kind, year = self.cleaned_data["kind"], self.cleaned_data["date"].year
        token = f"{year}:{kind}"
        if year == today.year or self.cleaned_data["confirm"] == token:
            return None
        if Season.objects.filter(year=year, kind=kind).exists():
            return token, f"Игра попадёт в сезон {year}. Начать?"
        return token, f"Будет создан сезон {SeasonKind(kind).label} {year}. Начать?"


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
        label="Выдано",
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
