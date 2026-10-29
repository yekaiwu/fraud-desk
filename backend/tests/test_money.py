from decimal import Decimal

import pytest

from app.core.money import approx_eq, fmt_money, parse_money, quantize, to_decimal


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234.56", "1234.56"),
        ("1234.5", "1234.50"),
        ("S$1,234.56", "1234.56"),
        ("SGD 1,234.56", "1234.56"),
        ("SGD1,234.56", "1234.56"),
        ("$ 1234.5", "1234.50"),
        ("(1,234.56)", "-1234.56"),
        ("1,234.56 DR", "-1234.56"),
        ("1,234.56 dr", "-1234.56"),
        ("1,234.56 CR", "1234.56"),
        ("-120.50", "-120.50"),
        ("S$-12.5", "-12.50"),
        (".50", "0.50"),
        ("0.00", "0.00"),
        ("-0.00", "0.00"),
        ("12", "12.00"),
    ],
)
def test_parses_amounts(text: str, expected: str) -> None:
    assert parse_money(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text",
    ["S8222222A", "T08LL1234D", "12A", "A12", "12 A", "abc", "1,23.4", "", "-", "N/A", "n/a", None],
)
def test_not_an_amount(text: str | None) -> None:
    # a letter touching a digit is NOT an amount (plan 8.5): the NRIC S8222222A
    assert parse_money(text) is None


def test_nric_is_not_money_even_with_currency_prefix_lookalike() -> None:
    assert parse_money("S8222222A") is None
    assert parse_money("SGD S8222222A") is None


def test_float_input_raises() -> None:
    with pytest.raises(TypeError):
        parse_money(12.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        to_decimal(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        approx_eq(1.0, Decimal("1"))  # type: ignore[arg-type]


def test_approx_eq_is_inclusive() -> None:
    assert approx_eq(Decimal("100.00"), Decimal("101.00"))  # exactly 1.00 passes
    assert not approx_eq(Decimal("100.00"), Decimal("101.01"))
    assert approx_eq(Decimal("5"), Decimal("5.5"), tol=Decimal("0.5"))


def test_to_decimal_and_format() -> None:
    assert to_decimal("12.50") == Decimal("12.50")
    assert to_decimal(None) is None
    assert to_decimal("") is None
    assert to_decimal("abc") is None
    assert fmt_money(Decimal("1.005")) == "1.01"  # half up
    assert fmt_money(Decimal("-0.001")) == "0.00"  # never negative zero
    assert fmt_money(Decimal("12345.6")) == "12345.60"
    assert quantize(Decimal("1.23456"), 4) == Decimal("1.2346")
