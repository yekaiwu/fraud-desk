"""Canonical values from printed text (ingest-and-extraction.md 6.2)."""

from __future__ import annotations

import pytest

from app.extract.normalise import canonical, looks_like_date, make_cell, parse_date


@pytest.mark.parametrize(
    ("raw", "iso"),
    [
        ("05 Mar 2026", "2026-03-05"),
        ("05-Mar-2026", "2026-03-05"),
        ("5 March 2026", "2026-03-05"),
        ("Mar 05, 2026", "2026-03-05"),
        ("05/03/2026", "2026-03-05"),  # day first
        ("05-03-2026", "2026-03-05"),
        ("05.03.2026", "2026-03-05"),
        ("2026-03-05", "2026-03-05"),
        ("05 Mar 26", "2026-03-05"),  # two-digit year only with a month name
        ("05 Mar 70", "1970-03-05"),
        ("29 Feb 2028", "2028-02-29"),
    ],
)
def test_dates_accepted_forms(raw: str, iso: str) -> None:
    assert canonical("date", raw) == (iso, None)


@pytest.mark.parametrize(
    "raw", ["31 Feb 2026", "29 Feb 2027", "05/13/2026", "tomorrow", "05/03/26"]
)
def test_impossible_or_unknown_dates_are_never_guessed(raw: str) -> None:
    assert canonical("date", raw) == (None, "unparsed")


def test_impossible_date_keeps_raw_in_cell() -> None:
    cell, warn = make_cell("31 Feb 2026", "date", source="text_layer", box=None, verified=True)
    assert (cell.value, cell.raw, warn) == (None, "31 Feb 2026", "unparsed")
    assert looks_like_date("31 Feb 2026")  # still shaped like a date, so it starts a table row
    assert parse_date("31 Feb 2026") is None


@pytest.mark.parametrize(
    ("raw", "value"),
    [
        ("1,234.56", "1234.56"),
        ("S$1,234.56", "1234.56"),
        ("SGD 1,234.56", "1234.56"),
        ("$ 1234.5", "1234.50"),
        ("(1,234.56)", "-1234.56"),
        ("1,234.56 DR", "-1234.56"),
        ("1,234.56 CR", "1234.56"),
        ("0.00", "0.00"),
    ],
)
def test_money_signed(raw: str, value: str) -> None:
    assert canonical("money", raw) == (value, None)


@pytest.mark.parametrize("raw", ["S8222222A", "T08LL1234D", "12A", "abc"])
def test_an_id_is_never_an_amount(raw: str) -> None:
    assert canonical("money", raw) == (None, "unparsed")


def test_money_magnitude_columns() -> None:
    assert canonical("money", "1,234.56 CR", magnitude=True) == ("1234.56", None)
    assert canonical("money", "1,234.56 DR", magnitude=True) == (
        "1234.56",
        None,
    )  # suffix is silent
    assert canonical("money", "(1,234.56)", magnitude=True) == (
        "1234.56",
        "negative_in_amount_column",
    )
    assert canonical("money", "-50.00", magnitude=True) == ("50.00", "negative_in_amount_column")


@pytest.mark.parametrize("raw", ["", "  ", "-", "--", "N/A", None])
def test_empty_markers_are_an_empty_cell(raw: str | None) -> None:
    assert canonical("money", raw) == (None, None)
    cell, warn = make_cell(raw, "money", source="text_layer", box=None, verified=True)
    assert (cell.value, cell.raw, cell.verified, cell.box, warn) == (None, None, None, None, None)


def test_percent_decimal_int_id_bool_text() -> None:
    assert canonical("percent", "33.33%") == ("33.33", None)
    assert canonical("percent", "9%") == ("9.00", None)
    assert canonical("percent", "9.5") == ("9.50", None)
    assert canonical("decimal", "1,000.5") == ("1000.5", None)
    assert canonical("decimal", "ten") == (None, "unparsed")
    assert canonical("int", "300,000") == ("300000", None)
    assert canonical("int", "30.5") == (None, "unparsed")
    assert canonical("id", " inv-2026 001 ") == ("INV-2026001", None)
    assert canonical("id", "s1234567d") == ("S1234567D", None)
    assert canonical("bool", "True") == ("true", None)
    assert canonical("text", "  a   b \n c ") == ("a b c", None)


def test_enums_and_currency() -> None:
    assert canonical("enum", "S$") == ("SGD", None)
    assert canonical("enum", "$") == ("SGD", None)
    assert canonical("enum", "US$") == ("USD", None)
    assert canonical("enum", "eur") == ("EUR", None)
    assert canonical("enum", "XYZ") == (None, "unparsed")
    status = ["paid", "unpaid", "partial"]
    assert canonical("enum", "PAID", values=status) == ("paid", None)
    assert canonical("enum", "PARTIALLY PAID", values=status) == ("partial", None)
    basis = ["audited", "unaudited", "unknown"]
    assert canonical("enum", "Management Accounts", values=basis) == ("unaudited", None)
    assert canonical("enum", "Audited", values=basis) == ("audited", None)
    scale = ["1", "1000", "1000000"]
    assert canonical("enum", "in thousands", values=scale) == ("1000", None)
    assert canonical("enum", "S$'000", values=scale) == ("1000", None)
    assert canonical("enum", "in millions", values=scale) == ("1000000", None)
    assert canonical("enum", "purple", values=status) == (None, "unparsed")


def test_cell_raw_is_capped_at_500_characters() -> None:
    cell, _ = make_cell("x" * 900, "text", source="text_layer", box=None, verified=True)
    assert cell.raw is not None
    assert len(cell.raw) == 500
    assert cell.raw.endswith("...")
    assert cell.value is not None
    assert len(cell.value) == 900  # the value is the cleaned text; only raw is capped


def test_zero_width_characters_are_removed() -> None:
    assert canonical("text", "ab​cd") == ("abcd", None)
