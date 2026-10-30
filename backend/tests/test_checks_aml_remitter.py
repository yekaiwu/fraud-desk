"""x_remitter_vs_buyer (checks-kyc-aml.md 4.9; test 17)."""

from __future__ import annotations

import pytest

from app.checks.aml import x_remitter_vs_buyer
from app.checks.registry import CheckSkipped
from app.core.models import Cell, Document
from app.names import entity_key
from tests.test_checks_kyc_fixtures import (
    APPLICANT_NAME,
    box,
    ctx_for,
    invoice_doc,
    statement_doc,
)


def run(*docs: Document):  # noqa: ANN201
    return x_remitter_vs_buyer(ctx_for(docs, use_case="aml", registry=False))


def credit(date: str, amount: str, cp: str, **kw) -> dict:  # noqa: ANN003
    return {"date": date, "credit": amount, "cp": cp, **kw}


def test_credit_from_another_company_is_high() -> None:
    doc = statement_doc(
        "d3",
        [
            credit("2026-02-20", "999.00", "SOMEONE ELSE PTE LTD"),
            credit("2026-03-04", "12500.00", "BETA HOLDINGS PTE LTD"),
        ],
    )
    flags = run(invoice_doc("d1"), doc)
    assert len(flags) == 1
    f = flags[0]
    assert f.severity == "high" and f.table == "transactions" and f.row == 1
    assert f.document_ids == ["d1", "d3"]
    assert f.evidence == (
        "invoice INV-0042 to Alpha Trading Pte Ltd, 12500.00 due 2026-03-01; credit 2026-03-04 of "
        "12500.00 from 'BETA HOLDINGS PTE LTD' (row 2, d3)"
    )
    assert f.id == f"x_remitter_vs_buyer.d1+d3.transactions:1~{entity_key('BETA HOLDINGS PTE LTD')}"
    assert f.explanation.startswith(
        "Invoice INV-0042 to Alpha Trading Pte Ltd was paid by BETA HOLDINGS"
    )


def test_flag_regions_point_at_the_description_and_the_buyer_cell() -> None:
    doc = statement_doc("d3", [credit("2026-03-04", "12500.00", "BETA HOLDINGS PTE LTD")])
    doc.tables["transactions"].rows[0].cells["description"] = Cell(
        value="FAST FROM BETA", box=box(y=0.5)
    )
    inv = invoice_doc("d1")
    inv.fields["buyer_name"] = Cell(value="Alpha Trading Pte Ltd", box=box(y=0.2, page=1))
    f = run(inv, doc)[0]
    assert f.region is not None and f.region.y == 0.5 and f.region.label == "remitter"
    assert [r.y for r in f.extra_regions] == [0.2]


def test_truncated_remitter_name_is_the_buyer() -> None:
    doc = statement_doc("d3", [credit("2026-03-04", "12500.00", "ALPHA TRADING PTE L")])
    assert run(invoice_doc("d1"), doc) == []


def test_description_fallback_when_there_is_no_annotation() -> None:
    doc = statement_doc(
        "d3",
        [
            {
                "date": "2026-03-04",
                "credit": "12500.00",
                "desc": "FAST PAYMENT FROM ALPHA TRADING PTE LTD",
            }
        ],
    )
    assert run(invoice_doc("d1"), doc) == []


def test_paid_by_the_applicants_own_account() -> None:
    doc = statement_doc("d3", [credit("2026-03-04", "12500.00", "EXAMPLE LOGISTICS PTE LTD")])
    f = run(invoice_doc("d1"), doc)[0]
    assert f.severity == "high" and f.evidence.endswith("; paid by the applicant's own account")
    assert "(the applicant's own account)" in f.explanation


def test_instalment_set_from_three_other_companies() -> None:
    inv = invoice_doc(
        "d1", number="INV-2026-0117", total="1000000.00", date_="2026-02-01", due="2026-03-01"
    )
    doc = statement_doc(
        "d3",
        [
            credit("2026-03-02", "333333.33", "GAMMA PTE LTD"),
            credit("2026-03-03", "333333.33", "DELTA PTE LTD"),
            credit("2026-03-04", "333333.34", "EPSILON PTE LTD"),
            credit("2026-03-05", "12.00", "NOISE PTE LTD"),
        ],
    )
    flags = run(inv, doc)
    assert len(flags) == 1
    f = flags[0]
    assert f.severity == "high" and f.row == 0
    assert "paid by 3 credits" in f.evidence
    for name in ("GAMMA PTE LTD", "DELTA PTE LTD", "EPSILON PTE LTD"):
        assert f"from '{name}'" in f.evidence
    assert "NOISE" not in f.evidence
    assert len(f.extra_regions) <= 6


def test_instalments_from_the_buyer_are_silent() -> None:
    inv = invoice_doc("d1", total="10900.00")
    doc = statement_doc(
        "d3",
        [
            credit("2026-03-02", "5450.00", "ALPHA TRADING PTE LTD"),
            credit("2026-03-20", "5450.00", "ALPHA TRADING PTE LTD"),
        ],
    )
    assert run(inv, doc) == []


def test_set_with_a_small_part_from_another_payer_is_silent() -> None:
    inv = invoice_doc("d1", total="10000.00")
    doc = statement_doc(
        "d3",
        [
            credit("2026-03-02", "8500.00", "ALPHA TRADING PTE LTD"),
            credit("2026-03-03", "1500.00", "GROUP TREASURY"),  # 15% of the invoice
        ],
    )
    assert run(inv, doc) == []


def test_set_with_a_real_share_from_another_payer_is_medium() -> None:
    inv = invoice_doc("d1", total="10000.00")
    doc = statement_doc(
        "d3",
        [
            credit("2026-03-02", "6000.00", "ALPHA TRADING PTE LTD"),
            credit("2026-03-03", "4000.00", "GROUP TREASURY"),
        ],
    )
    (flag,) = run(inv, doc)
    assert flag.severity == "medium"
    assert (
        "GROUP TREASURY" in flag.evidence and "other parts came from the customer" in flag.evidence
    )
    assert (
        flag.explanation.startswith("Invoice")
        and "partly paid by GROUP TREASURY" in flag.explanation
    )


def test_first_instalment_before_the_paid_window_is_found_when_the_set_is_mixed() -> None:
    # paid 2026-03-20: the narrow window (+-5 days) misses the 2026-03-03 instalment, the wider one
    # (invoice date to paid + 5) finds the pair, and the second part is from a stranger
    inv = invoice_doc("d1", total="10000.00", date_="2026-02-01", paid="2026-03-20")
    doc = statement_doc(
        "d3",
        [
            credit("2026-03-03", "6000.00", "ALPHA TRADING PTE LTD"),
            credit("2026-03-20", "4000.00", "GROUP TREASURY"),
        ],
    )
    (flag,) = run(inv, doc)
    assert flag.severity == "medium" and "GROUP TREASURY" in flag.evidence


def test_wide_window_set_of_unrelated_credits_is_not_a_finding() -> None:
    # a lucky subset sum between strangers is not enough: no part came from the customer
    inv = invoice_doc("d1", total="10000.00", date_="2026-02-01", paid="2026-03-20")
    doc = statement_doc(
        "d3",
        [
            credit("2026-03-03", "6000.00", "GAMMA PTE LTD"),
            credit("2026-03-20", "4000.00", "GROUP TREASURY"),
        ],
    )
    assert run(inv, doc) == []


def test_wide_window_set_from_the_customer_only_is_silent() -> None:
    inv = invoice_doc("d1", total="10000.00", date_="2026-02-01", paid="2026-03-20")
    doc = statement_doc(
        "d3",
        [
            credit("2026-03-03", "6000.00", "ALPHA TRADING PTE LTD"),
            credit("2026-03-20", "4000.00", "ALPHA TRADING PTE LTD"),
        ],
    )
    assert run(inv, doc) == []


def test_no_candidate_credit_means_no_flag_here() -> None:
    doc = statement_doc("d3", [credit("2026-03-04", "999.00", "BETA HOLDINGS PTE LTD")])
    assert run(invoice_doc("d1"), doc) == []


def test_amount_tolerance_and_window() -> None:
    near = statement_doc("d3", [credit("2026-03-04", "12499.50", "BETA HOLDINGS PTE LTD")])
    assert len(run(invoice_doc("d1"), near)) == 1  # within 1.00
    far = statement_doc("d3", [credit("2026-03-04", "12498.00", "BETA HOLDINGS PTE LTD")])
    assert run(invoice_doc("d1"), far) == []
    # [invoice_date, due + 30d]: 2026-03-31 is the last day for a 2026-03-01 due date
    last = statement_doc("d3", [credit("2026-03-31", "12500.00", "BETA HOLDINGS PTE LTD")])
    assert len(run(invoice_doc("d1"), last)) == 1
    late = statement_doc("d3", [credit("2026-04-02", "12500.00", "BETA HOLDINGS PTE LTD")])
    assert run(invoice_doc("d1"), late) == []
    before = statement_doc("d3", [credit("2026-01-31", "12500.00", "BETA HOLDINGS PTE LTD")])
    assert run(invoice_doc("d1"), before) == []


def test_paid_date_narrows_the_window_to_plus_minus_five_days() -> None:
    inv = invoice_doc("d1", paid="2026-03-10")
    inside = statement_doc("d3", [credit("2026-03-15", "12500.00", "BETA HOLDINGS PTE LTD")])
    assert len(run(inv, inside)) == 1
    outside = statement_doc("d3", [credit("2026-03-16", "12500.00", "BETA HOLDINGS PTE LTD")])
    assert run(inv, outside) == []


def test_buyer_uen_in_the_description_decides() -> None:
    inv = invoice_doc("d1", buyer_uen="201512345K")
    same_uen = statement_doc(
        "d3",
        [
            {
                "date": "2026-03-04",
                "credit": "12500.00",
                "cp": "PARENT HOLDING",
                "desc": "FAST PAYMENT FROM PARENT HOLDING 201512345K",
            }
        ],
    )
    assert run(inv, same_uen) == []
    other_uen = statement_doc(
        "d3",
        [
            {
                "date": "2026-03-04",
                "credit": "12500.00",
                "cp": "ALPHA TRADING PTE LTD",
                "desc": "FAST PAYMENT FROM ALPHA TRADING PTE LTD 202012345H",
            }
        ],
    )
    assert len(run(inv, other_uen)) == 1


def test_each_credit_pays_one_invoice() -> None:
    a = invoice_doc("d1", number="INV-1", buyer="Alpha Trading Pte Ltd", date_="2026-02-01")
    b = invoice_doc("d2", number="INV-2", buyer="Beta Holdings Pte Ltd", date_="2026-02-02")
    stmt = statement_doc(
        "d3",
        [
            credit("2026-03-01", "12500.00", "ALPHA TRADING PTE LTD"),
            credit("2026-03-02", "12500.00", "BETA HOLDINGS PTE LTD"),
        ],
    )
    assert run(a, b, stmt) == []
    only_one = statement_doc("d3", [credit("2026-03-01", "12500.00", "ALPHA TRADING PTE LTD")])
    assert (
        run(a, b, only_one) == []
    )  # the single credit pays INV-1; INV-2 has none left (not this rule)


def test_payables_are_out_of_scope() -> None:
    payable = invoice_doc(
        "d1", supplier="Other Supplier Pte Ltd", supplier_uen="202012345H", buyer=APPLICANT_NAME
    )
    with pytest.raises(CheckSkipped, match="no invoice issued by the applicant"):
        run(payable, statement_doc("d3", [credit("2026-03-04", "12500.00", "X PTE LTD")]))


def test_supplier_uen_alone_identifies_the_applicant() -> None:
    inv = invoice_doc("d1", supplier="Quite A Different Name Pte Ltd")
    doc = statement_doc("d3", [credit("2026-03-04", "12500.00", "BETA HOLDINGS PTE LTD")])
    assert len(run(inv, doc)) == 1


def test_currency_mismatch_and_unknown_applicant() -> None:
    usd = statement_doc(
        "d3", [credit("2026-03-04", "12500.00", "BETA HOLDINGS PTE LTD")], currency="USD"
    )
    with pytest.raises(CheckSkipped, match="currency mismatch"):
        run(invoice_doc("d1"), usd)
    from app.core.models import Applicant

    ctx = ctx_for([invoice_doc("d1"), usd], use_case="aml", registry=False, applicant=Applicant())
    with pytest.raises(CheckSkipped, match="applicant unknown"):
        x_remitter_vs_buyer(ctx)


def test_row_without_a_counterparty_is_an_extraction_gap_not_a_finding() -> None:
    doc = statement_doc("d3", [{"date": "2026-03-04", "credit": "12500.00", "desc": "  "}])
    assert run(invoice_doc("d1"), doc) == []
