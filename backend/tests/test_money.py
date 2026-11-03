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


# ---- decimal marks: a comma before one or two final digits is a decimal mark, three digits is
# a thousands separator (independent invoices print "$ 978,12")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("978,12", "978.12"),
        ("$ 889,20", "889.20"),
        ("$9,35", "9.35"),
        ("9,5", "9.50"),
        ("0,05", "0.05"),
        ("1.234,56", "1234.56"),
        ("SGD 1.000,00", "1000.00"),
        ("$ 2 259,10", "2259.10"),  # a space between thousands groups
        ("1 319,97", "1319.97"),
        ("1 319.97", "1319.97"),
        ("1,234", "1234.00"),  # three digits: thousands
        ("1,234.56", "1234.56"),
        ("12,345,678", "12345678.00"),
        ("(1,50)", "-1.50"),
        ("10,00 DR", "-10.00"),
        ("2.05-", "-2.05"),  # trailing minus on a discount line
    ],
)
def test_decimal_comma_and_european_thousands(text: str, expected: str) -> None:
    assert parse_money(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text",
    [
        "1,2345",  # four digits after a comma
        "1,234,56",  # grouped, then a decimal comma: not a number
        "12,34,56",  # neither convention
        "1.234.567",  # points only: not an amount
        "1 234",  # space grouping without decimals could be two numbers
        "£5 99",  # a space where the decimal point was
        "21053;73)",  # OCR debris
        "2O.¢¢",
        "985-73-8194",  # a US tax id
        "206-441-9785",  # a phone number
        "09/18/2015",  # a date
        "GB81LZWO32519172531418",  # an IBAN
        "12-",  # a minus after an integer is not a discount amount
        "1,23.4",
    ],
)
def test_garbage_and_ids_are_still_not_amounts(text: str) -> None:
    assert parse_money(text) is None


def test_parse_number_shapes() -> None:
    from app.core.money import parse_number

    assert parse_number("1.234,5") == Decimal("1234.5")
    assert parse_number("1,000") == Decimal("1000")
    assert parse_number("0.005") == Decimal("0.005")
    assert parse_number("1e5") is None
    assert parse_number("") is None
