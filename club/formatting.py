"""Text formatting for numbers and Russian words. Pure functions; templates use club.templatetags.

Money is integer lei (see CLAUDE.md), so there are no decimals to handle.
"""

from collections.abc import Sequence

THOUSANDS_SEPARATOR = "\u00a0"  # no-break space; CSS narrows it (see tokens.css)
MINUS = "−"


def format_money(value: int) -> str:
    """12500 -> '12 500' with a no-break space; negatives get a real minus sign."""
    grouped = f"{abs(value):,}".replace(",", THOUSANDS_SEPARATOR)
    return f"{MINUS}{grouped}" if value < 0 else grouped


def format_net(value: int) -> str:
    """Signed result: '+1 250', '−300' (U+2212), and a bare '0'."""
    if value > 0:
        return f"+{format_money(value)}"
    return format_money(value)


def ru_plural(value: int, forms: Sequence[str]) -> str:
    """Pick the Russian form for ``value`` from (one, few, many): игра, игры, игр.

    1, 21, 101 -> one; 2-4, 22-24 -> few; 0, 5-20, 25-30, 111-114 -> many.
    """
    one, few, many = forms
    n = abs(value)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
