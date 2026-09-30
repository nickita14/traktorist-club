"""Text formatting for numbers and Russian words. Pure functions; templates use club.templatetags.

Money is integer lei (see CLAUDE.md). The one exception is the value of a chip stack, which is
derived and may fall between whole lei: format_amount prints it with two decimals when needed.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

THOUSANDS_SEPARATOR = "\u00a0"  # no-break space; CSS narrows it (see tokens.css)
MINUS = "−"
DECIMAL_SEPARATOR = ","
CENTS = Decimal("0.01")


def format_money(value: int) -> str:
    """12500 -> '12 500' with a no-break space; negatives get a real minus sign."""
    grouped = f"{abs(value):,}".replace(",", THOUSANDS_SEPARATOR)
    return f"{MINUS}{grouped}" if value < 0 else grouped


def format_amount(value: int | Fraction) -> str:
    """Lei that may be fractional: '10', '2,30', '−0,50', '1 234,05' (rounded half up to cents).

    Whole values stay integers, others always get two decimals.
    """
    cents = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(CENTS, ROUND_HALF_UP)
    whole, fraction = divmod(abs(cents), 1)
    text = format_money(int(whole))
    if fraction:
        text += f"{DECIMAL_SEPARATOR}{int(fraction * 100):02d}"
    return f"{MINUS}{text}" if cents < 0 else text


def format_net(value: int) -> str:
    """Signed result: '+1 250', '−300' (U+2212), and a bare '0'."""
    if value > 0:
        return f"+{format_money(value)}"
    return format_money(value)


ROMAN = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def format_roman(value: int) -> str:
    """A rank step number: 1 -> 'I', 6 -> 'VI', 14 -> 'XIV'; 0 (no step) -> ''."""
    text = ""
    for number, letters in ROMAN:
        count, value = divmod(value, number)
        text += letters * count
    return text


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
