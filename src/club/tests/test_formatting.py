from fractions import Fraction

import pytest
from django.template import Context, Template

from club.formatting import format_amount, format_money, format_net, format_roman, ru_plural

NBSP = "\u00a0"
MINUS = "−"
GAME_FORMS = ("игра", "игры", "игр")


class TestFormatMoney:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0"),
            (5, "5"),
            (999, "999"),
            (1000, f"1{NBSP}000"),
            (12345, f"12{NBSP}345"),
            (1234567, f"1{NBSP}234{NBSP}567"),
            (-300, f"{MINUS}300"),
            (-12500, f"{MINUS}12{NBSP}500"),
        ],
    )
    def test_format(self, value, expected):
        assert format_money(value) == expected

    def test_never_uses_hyphen_or_plain_space(self):
        assert "-" not in format_money(-1000)
        assert " " not in format_money(1000000)


class TestFormatAmount:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (10, "10"),
            (Fraction(10), "10"),
            (Fraction(0), "0"),
            (Fraction(23, 10), "2,30"),
            (Fraction(1, 2), "0,50"),
            (Fraction(-1, 2), f"{MINUS}0,50"),
            (Fraction(123405, 100), f"1{NBSP}234,05"),
            (Fraction(1, 3), "0,33"),
            (Fraction(1, 200), "0,01"),  # half a cent rounds up
            (Fraction(1999, 1000), "2"),  # rounds to a whole value, printed as one
            (Fraction(-1, 1000), "0"),  # rounds to zero: no minus sign
        ],
    )
    def test_format(self, value, expected):
        assert format_amount(value) == expected


class TestFormatNet:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0"),
            (1, "+1"),
            (1250, f"+1{NBSP}250"),
            (-1, f"{MINUS}1"),
            (-300, f"{MINUS}300"),
            (-1500, f"{MINUS}1{NBSP}500"),
        ],
    )
    def test_format(self, value, expected):
        assert format_net(value) == expected


class TestFormatRoman:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0, ""), (1, "I"), (3, "III"), (4, "IV"), (5, "V"), (6, "VI"), (9, "IX"), (14, "XIV")],
    )
    def test_format(self, value, expected):
        assert format_roman(value) == expected


class TestRuPlural:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "игр"),
            (1, "игра"),
            (2, "игры"),
            (4, "игры"),
            (5, "игр"),
            (11, "игр"),
            (12, "игр"),
            (14, "игр"),
            (15, "игр"),
            (20, "игр"),
            (21, "игра"),
            (22, "игры"),
            (25, "игр"),
            (101, "игра"),
            (111, "игр"),
            (112, "игр"),
            (122, "игры"),
            (1000, "игр"),
            (-1, "игра"),
            (-3, "игры"),
        ],
    )
    def test_forms(self, value, expected):
        assert ru_plural(value, GAME_FORMS) == expected


def render(source: str, **context) -> str:
    return Template("{% load ledger %}" + source).render(Context(context))


class TestTemplateTags:
    def test_money_and_net_filters(self):
        assert render("{{ v|money }}", v=12500) == f"12{NBSP}500"
        assert render("{{ v|net }}", v=-40) == f"{MINUS}40"

    def test_amount_filter(self):
        assert render("{{ v|amount }}", v=Fraction(11230, 100)) == "112,30"
        assert render("{{ v|amount }}", v=Fraction(1000, 100)) == "10"

    def test_roman_filter(self):
        assert render("{{ n|roman }} из {{ m|roman }}", n=5, m=6) == "V из VI"

    def test_plural_filter(self):
        assert render('{{ n }} {{ n|plural:"игра,игры,игр" }}', n=23) == "23 игры"

    def test_net_value_marks_negative_with_minus_and_class(self):
        assert render("{% net_value v %}", v=-1500) == (
            f'<span class="val-neg">{MINUS}1{NBSP}500</span>'
        )

    def test_net_value_positive_is_plain_with_plus(self):
        assert render("{% net_value v %}", v=1250) == f"+1{NBSP}250"

    def test_net_value_zero_is_faint(self):
        assert render("{% net_value v %}", v=0) == '<span class="val-zero">0</span>'

    def test_count_value_zero_is_a_faint_dot_read_as_zero(self):
        html = render("{% count_value v %}", v=0)
        assert 'class="val-zero"' in html
        assert '<span aria-hidden="true">·</span>' in html
        assert '<span class="sr-only">0</span>' in html
        assert render("{% count_value v %}", v=3) == "3"
        assert render("{% count_value v %}", v=1250) == f"1{NBSP}250"

    def test_pot_value(self):
        assert render("{% pot_value v %}", v=Fraction(23, 10)) == "2,30"
        assert render("{% pot_value v %}", v=-20) == f'<span class="val-neg">{MINUS}20</span>'
        assert render("{% pot_value v %}", v=0) == '<span class="val-zero">0</span>'
        assert 'aria-hidden="true">·</span>' in render("{% pot_value v %}", v=None)

    def test_optional_count(self):
        assert render("{% optional_count v %}", v=5230) == f"5{NBSP}230"
        assert '<span class="sr-only">нет</span>' in render("{% optional_count v %}", v=None)
