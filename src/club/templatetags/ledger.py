"""Template filters and tags for ledger numbers: `{% load ledger %}`.

Filters return plain text; the ``*_value`` tags wrap it in a span with the CSS class that marks
negative (``val-neg``) or zero (``val-zero``) values, defined in frontend/source.css.
"""

import re

from django import template
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe

from club.formatting import (
    FIGURE,
    format_amount,
    format_money,
    format_net,
    format_roman,
    ru_plural,
)

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
def amount(value) -> str:
    """Lei that may be fractional (a chip stack's value): '112,30', '10'."""
    return format_amount(value)


@register.filter
def net(value: int) -> str:
    return format_net(value)


# Tags and entities of the escaped text are kept whole, so "&#x27;" never gets a figure span.
_MARKUP_OR_FIGURE = re.compile(rf"(<[^>]*>|&#?\w+;)|{FIGURE.pattern}")


@register.filter
def figures(value) -> str:
    """A text with figures ("11 вечеров", "2 из 2", "15 020 лей"): each figure in the number
    face with the narrowed thousands space, the words in the text face with normal spacing.
    Plain input is escaped, safe input is not escaped twice."""

    def wrap(match: re.Match) -> str:
        if match.group(1):
            return match.group(1)
        # not-italic: the number face has no italic, and captions and notes are italic.
        return f'<span class="font-num num-run not-italic">{match.group()}</span>'

    return mark_safe(_MARKUP_OR_FIGURE.sub(wrap, conditional_escape(value)))


@register.filter
def roman(value: int) -> str:
    """A rank step number in roman numerals: 'V'."""
    return format_roman(value)


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
