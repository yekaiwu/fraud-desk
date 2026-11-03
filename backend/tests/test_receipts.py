"""Receipts: a point-of-sale slip is its own document type (classify, extract, six rules)."""

from __future__ import annotations

from datetime import date

import pytest

from app.checks.registry import all_checks, run_stage_checks
from app.classify.rules import RulesClassifier
from app.core.fingerprints import MemoryFingerprintStore, receipt_fingerprint
from app.core.models import C1_LABELS
from app.core.schemas import DOC_SCHEMAS, EXTRACTABLE_TYPES
from app.core.testing import make_doc
from tests.checks_fin_factory import ctx_of, run_rule, skipped

RECEIPT_RULES = {
    "rcpt_lines": "medium",
    "rcpt_tax": "medium",
    "rcpt_total": "medium",
    "rcpt_dates": "medium",
    "rcpt_merchant": "low",
    "rcpt_duplicate": "medium",
}

LINES = [
    {"description": "HAM&EGG BURRITO", "quantity": "1", "unit_price": "9.99", "amount": "9.99"},
    {"description": "SS PANCAKES", "quantity": "1", "unit_price": "5.99", "amount": "5.99"},
    {"description": "HOT TEA", "quantity": "2", "unit_price": "2.49", "amount": "4.98"},
]


def make_receipt(id: str = "r1", *, fields=None, lines=None):  # noqa: ANN001, ANN201, A002
    base = {
        "merchant_name": "MASTORIS DINER",
        "receipt_number": "1885",
        "receipt_date": "2026-09-04",
        "currency": "USD",
        "subtotal": "20.96",
        "tax_rate": "7.00",
        "tax_amount": "1.47",
        "total": "22.43",
    }
    base.update(fields or {})
    return make_doc(
        "receipt",
        id=id,
        fields=base,
        tables={"lines": [dict(r) for r in (LINES if lines is None else lines)]},
    )


# ---- the type


def test_receipt_is_a_document_type_with_a_schema_and_the_generic_extractor() -> None:
    assert "receipt" in C1_LABELS and "receipt" in EXTRACTABLE_TYPES
    schema = DOC_SCHEMAS["receipt"]
    assert set(schema.tables) == {"lines"}
    # no invoice number, UEN or GST number: nothing for an invoice rule to ask for
    assert not {"invoice_number", "supplier_uen", "supplier_gst_no"} & set(schema.fields)
    assert schema.fields["total"].required


def test_receipt_rules_are_registered_with_honest_severities_and_never_high() -> None:
    by_rule = {m.rule: m for m in all_checks()}
    for rule, severity in RECEIPT_RULES.items():
        meta = by_rule[rule]
        assert meta.severity == severity and meta.applies_to == {"receipt"}, rule
        assert meta.tamper and meta.description


def test_no_invoice_rule_runs_on_a_receipt() -> None:
    ctx = ctx_of(make_receipt())
    ran = {r.rule for r in run_stage_checks(ctx, "rules") if r.status == "ran"}
    assert not {r for r in ran if r.startswith(("inv_", "x_invoice"))}
    assert {"rcpt_lines", "rcpt_tax", "rcpt_total", "rcpt_dates", "rcpt_merchant"} <= ran


# ---- classification

TILL = """\
MASTORIS DINER
144 ROUTE 130 BORDENTOWN
Table 12/1  Chk 1441
Server: Tyler
1 HAM&EGG BURRITO     9.99
subtotal              23.96
Tax                    1.65
Payment Due           25.61
Thank you for dining with us
"""
INVOICE = """\
TAX INVOICE
Invoice No: INV-0042
Bill To: Acme Trading Pte Ltd
Description  Qty  Unit Price  Amount
Freight  4  2000.00  8000.00
Subtotal 10000.00
GST 9% 900.00
Total 10900.00
"""


def _top(text: str) -> tuple[str, float]:
    dist = RulesClassifier().classify(text, list(C1_LABELS), question="C1")
    label = max(dist, key=lambda k: dist[k])
    return label, dist[label]


def test_a_till_slip_is_a_receipt_and_an_invoice_is_still_an_invoice() -> None:
    label, p = _top(TILL)
    assert label == "receipt" and p >= 0.7
    assert _top(INVOICE)[0] == "invoice"


def test_item_words_in_a_description_are_not_receipt_cues() -> None:
    text = INVOICE.replace("Freight  4", "Marble Coffee Table 4,00 each")
    assert _top(text)[0] == "invoice"


# ---- the rules


def test_a_clean_receipt_is_silent() -> None:
    ctx = ctx_of(make_receipt(), today=date(2026, 10, 27))
    for rule in RECEIPT_RULES:
        if rule == "rcpt_duplicate":
            continue
        assert run_rule(rule, ctx) == [], rule


def test_a_changed_total_is_medium_not_high() -> None:
    flags = run_rule("rcpt_total", ctx_of(make_receipt(fields={"total": "32.43"})))
    assert [f.severity for f in flags] == ["medium"]


def test_a_total_slightly_above_subtotal_plus_tax_reads_as_a_tip() -> None:
    flags = run_rule("rcpt_total", ctx_of(make_receipt(fields={"total": "25.43"})))
    assert [f.severity for f in flags] == ["low"]
    assert "tip" in flags[0].evidence


def test_a_changed_line_amount_is_medium() -> None:
    lines = [dict(r) for r in LINES]
    lines[1]["amount"] = "15.99"
    flags = run_rule("rcpt_lines", ctx_of(make_receipt(lines=lines)))
    assert [f.severity for f in flags] == ["medium"]


def test_a_changed_tax_is_medium_when_above_the_rate_and_low_when_below() -> None:
    above = run_rule(
        "rcpt_tax", ctx_of(make_receipt(fields={"tax_amount": "3.47", "total": "24.43"}))
    )
    assert [f.severity for f in above] == ["medium"]
    below = run_rule(
        "rcpt_tax", ctx_of(make_receipt(fields={"tax_amount": "0.47", "total": "21.43"}))
    )
    assert [f.severity for f in below] == ["low"]


def test_tax_without_a_printed_rate_is_not_tested() -> None:
    assert skipped("rcpt_tax", ctx_of(make_receipt(fields={"tax_rate": None}))) is not None


def test_future_and_ancient_dates() -> None:
    today = date(2026, 10, 27)
    future = run_rule(
        "rcpt_dates", ctx_of(make_receipt(fields={"receipt_date": "2027-01-02"}), today=today)
    )
    assert [f.severity for f in future] == ["medium"]
    old = run_rule(
        "rcpt_dates", ctx_of(make_receipt(fields={"receipt_date": "2009-01-02"}), today=today)
    )
    assert [f.severity for f in old] == ["low"]


def test_a_missing_merchant_is_low() -> None:
    flags = run_rule("rcpt_merchant", ctx_of(make_receipt(fields={"merchant_name": None})))
    assert [f.severity for f in flags] == ["low"]


def test_the_same_receipt_twice_in_a_case_is_a_duplicate_and_a_different_one_is_not() -> None:
    a, b = make_receipt("r1"), make_receipt("r2")
    flags = run_rule("rcpt_duplicate", ctx_of(a, b))
    assert [f.severity for f in flags] == ["medium"] and "r1" in flags[0].evidence
    other = make_receipt("r3", fields={"total": "30.00"})
    assert run_rule("rcpt_duplicate", ctx_of(a, other)) == []


def test_a_receipt_seen_in_an_earlier_case_is_a_duplicate() -> None:
    store = MemoryFingerprintStore()
    fp = receipt_fingerprint("Mastoris  Diner", "2026-09-04", "22.43", "1885")
    store.record("receipt", fp, case_id="earlier", document_id="d9")
    flags = run_rule("rcpt_duplicate", ctx_of(make_receipt(), fingerprints=store))
    assert len(flags) == 1 and "earlier" in flags[0].evidence


def test_an_incompletely_read_receipt_is_low_and_never_a_mismatch() -> None:
    doc = make_receipt(fields={"subtotal": "99.00", "total": "250.00"})
    doc.warnings.append("reading_incomplete: page 1: 2 printed lines not read ('ADD STRWBER')")
    for rule in ("rcpt_lines", "rcpt_tax", "rcpt_total"):
        flags = run_rule(rule, ctx_of(doc))
        assert [f.severity for f in flags] == ["low"], rule


# ---- parsing a receipt's numbers and dates


def _dated(raw: str):  # noqa: ANN202
    from app.core.models import Cell

    doc = make_receipt(fields={"receipt_date": None})
    doc.fields["receipt_date"] = Cell(value=None, raw=raw, kind="date")
    return doc


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("9/18/2026", "future"), ("5/19/2017", None), ("5/4/2099", None)],
)
def test_a_us_month_first_date_is_read_only_when_it_cannot_be_day_first(
    raw: str, expected: str | None
) -> None:
    # "9/18/2026" cannot be day first (no month 18), so it is 18 Sep 2026 and not in the past
    # relative to 1 Sep 2026; "5/4/2099" is ambiguous and is never guessed (the rule is skipped)
    ctx = ctx_of(_dated(raw), today=date(2026, 9, 1))
    if expected is None and raw == "5/4/2099":
        assert skipped("rcpt_dates", ctx) is not None
    elif expected is None:
        flags = run_rule("rcpt_dates", ctx)  # 2017 is within ten years of 2026
        assert flags == []
    else:
        assert [f.severity for f in run_rule("rcpt_dates", ctx)] == ["medium"]


def test_receipt_fingerprint_ignores_spacing_and_punctuation_of_the_merchant() -> None:
    assert receipt_fingerprint("Mastoris Diner", "2026-09-04", "22.4", "1") == receipt_fingerprint(
        "MASTORIS  DINER.", "2026-09-04", "22.40", "1"
    )


def test_a_mismatch_that_rests_on_an_unconfirmed_reading_is_only_low() -> None:
    # the page text could not confirm the printed tax rate (an image reading): look, do not alarm
    doc = make_receipt(fields={"tax_amount": "3.47", "total": "24.43"})
    doc.fields["tax_rate"] = doc.fields["tax_rate"].model_copy(update={"verified": False})
    assert [f.severity for f in run_rule("rcpt_tax", ctx_of(doc))] == ["low"]
    lines = [dict(r) for r in LINES]
    lines[1]["amount"] = "15.99"
    doc = make_receipt(lines=lines)
    row = doc.tables["lines"].rows[1]
    row.cells["amount"] = row.cells["amount"].model_copy(update={"verified": False})
    assert [f.severity for f in run_rule("rcpt_lines", ctx_of(doc))] == ["low"]
    # the same mismatch on confirmed cells stays medium (see the tests above)
