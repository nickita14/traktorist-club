"""Season creation: every code path that creates a season goes through ``season_for``."""

from club.models import Season

# What a new season takes from the nearest earlier season of its kind. Not ``sheet_title``:
# a tab name belongs to one season.
INHERITED = (
    "chips_per_lei",
    "paid_places",
    "entry_price",
    "rebuy_price",
    "addon_price",
    "rebuy_minutes",
    "payout_weights",
    "payout_round",
    "cash_step",
    "default_blinds",
    "sheet_managed",
)


def previous_season(kind: str, year: int) -> Season | None:
    """The nearest earlier season of ``kind`` (a later one never counts)."""
    return Season.objects.filter(kind=kind, year__lt=year).order_by("-year").first()


def inherited_rules(kind: str, year: int) -> dict:
    """The fields a new season of ``kind`` for ``year`` copies; empty without an earlier one
    (the model defaults apply, and the season is not sheet-managed)."""
    previous = previous_season(kind, year)
    return {field: getattr(previous, field) for field in INHERITED} if previous else {}


def season_for(kind: str, year: int, *, sheet_managed: bool | None = None) -> Season:
    """The season of ``kind`` for ``year``; a missing one is created with the rules and the
    sheet management of the nearest earlier season of that kind. ``sheet_managed`` overrides
    the inherited flag of a new season and never changes an existing one."""
    season = Season.objects.filter(kind=kind, year=year).first()
    if season is not None:
        return season
    defaults = inherited_rules(kind, year)
    if sheet_managed is not None:
        defaults["sheet_managed"] = sheet_managed
    season = Season(kind=kind, year=year, **defaults)
    season.full_clean(validate_unique=False, validate_constraints=False)
    season, _ = Season.objects.get_or_create(kind=kind, year=year, defaults=defaults)
    return season
