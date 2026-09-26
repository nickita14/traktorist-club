"""Template filters and tags for ledger numbers: `{% load ledger %}`.

Filters return plain text; the ``*_value`` tags wrap it in a span with the CSS class that marks
negative (``val-neg``) or zero (``val-zero``) values, defined in frontend/source.css.
"""

from django import template
from django.utils.html import format_html

from club.formatting import format_money, format_net, ru_plural

register = template.Library()

PLACE_ZERO = "·"


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
    """A count such as ITM; zero is faint."""
    if value == 0:
        return format_html('<span class="val-zero">{}</span>', 0)
    return format_money(value)


@register.simple_tag
def place_count(value: int) -> str:
    """Number of 1st/2nd/3rd places; zero is a faint middle dot (read out as 0)."""
    if value == 0:
        return format_html(
            '<span class="val-zero"><span aria-hidden="true">{}</span>'
            '<span class="sr-only">0</span></span>',
            PLACE_ZERO,
        )
    return format_money(value)
