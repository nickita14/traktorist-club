"""Template filters and tags for ledger numbers: `{% load ledger %}`.

Filters return plain text; the ``*_value`` tags wrap it in a span with the CSS class that marks
negative (``val-neg``) or zero (``val-zero``) values, defined in frontend/source.css.
"""

from django import template
from django.utils.html import format_html

from club.formatting import format_amount, format_money, format_net, ru_plural

register = template.Library()

PLACE_ZERO = "·"


def _faint_dot(spoken: str) -> str:
    return format_html(
        '<span class="val-zero"><span aria-hidden="true">{}</span>'
        '<span class="sr-only">{}</span></span>',
        PLACE_ZERO,
        spoken,
    )


@register.filter
def money(value: int) -> str:
    return format_money(value)


@register.filter
def net(value: int) -> str:
    return format_net(value)


@register.filter
def plural(value: int, forms: str) -> str:
    """``{{ n|plural:"игра,игры,игр" }}`` gives the word only, the number is printed apart."""
    return ru_plural(value, forms.split(","))


@register.simple_tag
def net_value(value: int) -> str:
    """Signed net; negatives in accent (the minus sign stays too), zero faint."""
    if value < 0:
        return format_html('<span class="val-neg">{}</span>', format_net(value))
    if value == 0:
        return format_html('<span class="val-zero">{}</span>', format_net(value))
    return format_net(value)


@register.simple_tag
def count_value(value: int) -> str:
    """A count (ITM, 1st/2nd/3rd places); zero is a faint middle dot (read out as 0)."""
    if value == 0:
        return _faint_dot("0")
    return format_money(value)


@register.simple_tag
def no_value() -> str:
    """A cell that has no value for this row (a cash place, an unknown stack): a faint dot."""
    return _faint_dot("нет")


@register.simple_tag
def optional_count(value: int | None) -> str:
    """A place or a chip count that may be missing: the number, or a faint dot."""
    if value is None:
        return no_value()
    return format_money(value)


@register.simple_tag
def pot_value(value) -> str:
    """What a cash result left in the pot (maybe fractional lei); faint dot when unknown."""
    if value is None:
        return no_value()
    if value < 0:
        return format_html('<span class="val-neg">{}</span>', format_amount(value))
    if value == 0:
        return format_html('<span class="val-zero">{}</span>', format_amount(value))
    return format_amount(value)
