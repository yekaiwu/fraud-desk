"""The code-built readback (app/custom_rules/readback.py): exact sentences, built from the spec only."""

from __future__ import annotations

import pytest

from app.custom_rules import readback as rb
from app.custom_rules.spec import UnsupportedPart, addressable_fields, fs_lines
from tests.custom_rules_helpers import DEBT, EQUITY, spec_of, t1, t2, t3, t4


@pytest.mark.parametrize(
    ("condition", "severity", "expected"),
    [
        (
            t1("invoice.total", "gt", "100000"),
            "low",
            "Flags low when an invoice's total is greater than 100,000.00.",
        ),
        (
            t1("invoice.total", "lte", "99.5"),
            "info",
            "Flags info when an invoice's total is at most 99.50.",
        ),
        (
            t1("invoice.gst_rate", "eq", "9"),
            "low",
            "Flags low when an invoice's GST rate is 9%.",
        ),
        (
            t1("invoice.invoice_date", "lt", "2025-02-01"),
            "low",
            "Flags low when an invoice's date is before 1 February 2025.",
        ),
        (
            t1("invoice.invoice_date", "gte", "2025-02-01"),
            "low",
            "Flags low when an invoice's date is on or after 1 February 2025.",
        ),
        (
            t1("shareholder_register.total_shares", "gt", "1500000"),
            "low",
            "Flags low when a shareholder register's total shares is greater than 1,500,000.",
        ),
        (
            t1("invoice.payment_status", "in", None, values=["unpaid", "partial"]),
            "medium",
            "Flags medium when an invoice's payment status is one of unpaid, partial.",
        ),
        (
            t1("invoice.payment_status", "ne", "paid"),
            "low",
            "Flags low when an invoice's payment status is not paid.",
        ),
        (
            t1("invoice.currency", "ne", "SGD"),
            "low",
            "Flags low when an invoice's currency is not SGD.",
        ),
        (
            t2("passport.date_of_expiry", "older_than", 0),
            "low",
            "Flags low when a passport's date of expiry is before the case date.",
        ),
        (
            t2("invoice.invoice_date", "older_than", 90),
            "low",
            "Flags low when an invoice's date is more than 90 days before the case date.",
        ),
        (
            t2("invoice.invoice_date", "older_than", 1),
            "low",
            "Flags low when an invoice's date is more than 1 day before the case date.",
        ),
        (
            t2("invoice.invoice_date", "newer_than", 30),
            "low",
            "Flags low when an invoice's date is less than 30 days before the case date, or later.",
        ),
        (
            t2("invoice.invoice_date", "newer_than", 0),
            "low",
            "Flags low when an invoice's date is after the case date.",
        ),
        (
            t2("invoice.invoice_date", "in_future"),
            "low",
            "Flags low when an invoice's date is after the case date.",
        ),
        (
            t3("receipt.receipt_number"),
            "info",
            "Flags info when a receipt in the case has no receipt number.",
        ),
        (
            t3("invoice.buyer_uen"),
            "low",
            "Flags low when an invoice in the case has no invoice buyer UEN.",
        ),
        (
            t4(DEBT, EQUITY),
            "medium",
            "Flags medium when, on a financial statement's current-year column, borrowings (current) "
            "+ borrowings (non-current) is greater than 2 times total equity.",
        ),
        (
            t4(["balance_sheet.total_liabilities"], EQUITY, "lte", "1", "prior"),
            "low",
            "Flags low when, on a financial statement's prior-year column, total liabilities is at "
            "most total equity.",
        ),
    ],
)
def test_readback_sentences(condition: dict, severity: str, expected: str) -> None:
    assert rb.readback(spec_of(condition, severity)) == expected


def test_money_percent_date_and_number_formats() -> None:
    assert rb.format_value("invoice.total", "100000") == "100,000.00"
    assert rb.format_value("invoice.total", "-5.5") == "-5.50"
    assert rb.format_value("invoice.gst_rate", "9.0") == "9%"
    assert rb.format_value("invoice.invoice_date", "2025-02-01") == "1 February 2025"
    assert rb.format_value("shareholder_register.total_shares", "1000000") == "1,000,000"
    assert rb.format_value("invoice.currency", "SGD") == "SGD"
    assert rb.format_value("invoice.total", "abc") == "abc"  # never raises on bad input
    assert rb.format_value("nowhere.nothing", "x") == "x"


def test_flag_explanation_is_code_text_and_short() -> None:
    spec = spec_of(t1("invoice.total", "gt", "100000"))
    assert rb.flag_explanation(spec) == (
        "Custom rule (unreviewed): an invoice's total is greater than 100,000.00."
    )
    assert len(rb.flag_explanation(spec_of(t4(DEBT, EQUITY)))) <= rb.EXPLANATION_MAX


def test_will_check_lists_the_readback_and_what_the_engine_does() -> None:
    spec = spec_of(t1("invoice.total", "gt", "100000"))
    lines = rb.will_check(spec)
    assert lines[0] == rb.readback(spec)
    assert lines[1] == "Only invoices in the case are read."
    assert any("skipped, not flagged" in line for line in lines)
    assert any("lowers the flag to low" in line for line in lines)
    t3_lines = rb.will_check(spec_of(t3("receipt.receipt_number")))
    assert t3_lines[1] == "Only receipts in the case are read."
    assert any("read only in part" in line for line in t3_lines)
    assert (
        rb.will_check(spec_of(t4(DEBT, EQUITY)))[1]
        == "Only financial statements in the case are read."
    )


def test_will_not_check_names_each_phrase_with_its_reason() -> None:
    parts = [
        UnsupportedPart(phrase="from different places", reason="field_not_extracted"),
        UnsupportedPart(phrase="looks odd", reason="needs_judgement", detail="ignored"),
    ]
    lines = rb.will_not_check(parts, spec_of(t1("invoice.total", "gt", "1")))
    assert lines == [
        '"from different places": the system does not read this from the documents',
        '"looks odd": it needs a judgement that cannot be written as a fixed condition',
    ]


def test_will_not_check_adds_the_fs_ratio_limit() -> None:
    lines = rb.will_not_check([], spec_of(t4(DEBT, EQUITY)))
    assert lines == [
        "A line that is not printed counts as 0; a statement without total equity is skipped."
    ]
    assert rb.will_not_check([], None) == []


def test_will_not_check_cleans_the_officers_phrase() -> None:
    part = UnsupportedPart(phrase="a‮b\nc" + "x" * 100, reason="needs_image")
    line = rb.will_not_check([part], None)[0]
    assert "‮" not in line and "\n" not in line
    assert line.startswith('"a b c')


def test_every_addressable_field_and_line_reads_back() -> None:
    """No path makes the readback fail, whatever the schema holds."""
    for path, info in addressable_fields().items():
        spec = spec_of(t3(path))
        assert rb.readback(spec).startswith("Flags low when")
        if info.operators and info.kind != "enum":
            value = "2025-01-01" if info.kind == "date" else "5"
            assert rb.readback(spec_of(t1(path, "gt", value)))
    for path in fs_lines():
        right = "balance_sheet.total_assets" if path.endswith("total_equity") else EQUITY
        assert rb.readback(spec_of(t4([path], right))).startswith("Flags low when, on a")


def test_readback_never_contains_markup_or_unicode_controls() -> None:
    spec = spec_of(t1("invoice.payment_status", "in", None, values=["paid", "unpaid"]))
    text = rb.readback(spec)
    assert "<" not in text and "\n" not in text
