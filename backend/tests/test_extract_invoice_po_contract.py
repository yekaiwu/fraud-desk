"""Invoice, purchase order and contract text-layer extraction (labelled forms)."""

from __future__ import annotations

import pytest

from app.extract.base import LayoutNotRecognised
from app.extract.invoice import validate_invoice
from app.extract.registry import discover, get_extractors
from tests.extract_helpers import (
    InvLine,
    make_case,
    render_contract,
    render_invoice,
    render_po,
)

discover()


def extract(pdf: bytes, doc_type: str):
    ctx, doc, _ = make_case(pdf, doc_type)
    return get_extractors(doc_type)["text_layer"].extract(doc, ctx), ctx, doc


def values(cells) -> dict:
    return {k: c.value for k, c in cells.items()}


@pytest.mark.parametrize("per_line", [False, True])
def test_invoice_both_gst_layouts_fill_the_same_fields(per_line: bool) -> None:
    result, ctx, doc = extract(render_invoice(per_line_gst=per_line), "invoice")
    f = values(result.fields)
    assert f["invoice_number"] == "INV-2026-0101"
    assert f["invoice_date"] == "2026-03-05"
    assert f["due_date"] == "2026-04-04"
    assert f["supplier_name"] == "Meridian Fastener Supplies Pte. Ltd."
    assert f["supplier_uen"] == "201912345R"
    assert f["supplier_gst_no"] == "M21234567K"
    assert f["buyer_name"] == "Orchid Bay Holdings Pte. Ltd."
    assert f["buyer_uen"] == "202012345H"
    assert (f["subtotal"], f["gst_amount"], f["total"]) == ("1500.00", "135.00", "1635.00")
    assert f["currency"] == "SGD"
    assert f["gst_rate"] == "9.00"
    assert f["po_reference"] == "PO-7788"
    assert f["payment_status"] == "paid"
    assert f["paid_date"] == "2026-03-20"
    lines = result.tables["lines"].rows
    assert [r.cells["amount"].value for r in lines] == ["500.00", "1000.00"]
    assert [r.cells["quantity"].value for r in lines] == ["10", "20"]
    gst = [r.cells["gst_amount"].value for r in lines]
    assert gst == (
        ["45.00", "90.00"] if per_line else [None, None]
    )  # per-line GST only when printed
    assert all(r.cells["amount"].box is not None and r.cells["amount"].verified for r in lines)
    assert validate_invoice(result, ctx, doc).failing == {}


def test_invoice_gst_rate_defaults_to_nine_only_when_a_gst_line_exists() -> None:
    printed_without_rate = render_invoice(gst_label="GST")
    result, *_ = extract(printed_without_rate, "invoice")
    rate = result.fields["gst_rate"]
    assert rate.value == "9.00"
    assert rate.source == "derived"  # defaulted, not read
    assert "gst_rate_defaulted" in result.warnings

    none_printed = render_invoice(with_gst_line=False, gst="0.00", total="1,500.00")
    result, *_ = extract(none_printed, "invoice")
    assert result.fields["gst_rate"].value is None
    assert result.fields["gst_amount"].value is None
    assert result.fields["total"].value == "1500.00"


def test_invoice_with_an_eight_percent_rate_keeps_the_printed_rate() -> None:
    pdf = render_invoice(gst_label="GST (8%)", gst="120.00", total="1,620.00")
    result, *_ = extract(pdf, "invoice")
    assert result.fields["gst_rate"].value == "8.00"
    assert result.fields["gst_rate"].raw == "8%"


def test_invoice_tampered_total_fails_the_identity_on_its_page() -> None:
    pdf = render_invoice(total="1,735.00")  # subtotal + GST is 1,635.00
    result, ctx, doc = extract(pdf, "invoice")
    assert result.fields["total"].value == "1735.00"  # reported as printed
    assert validate_invoice(result, ctx, doc).failing == {1: "total"}


def test_invoice_line_arithmetic_is_validated() -> None:
    lines = [InvLine("Hex bolts", "10", "50.00", "600.00")]
    pdf = render_invoice(lines=lines, subtotal="600.00", gst="54.00", total="654.00")
    result, ctx, doc = extract(pdf, "invoice")
    assert validate_invoice(result, ctx, doc).failing == {1: "total"}  # 10 x 50.00 is not 600.00


def test_invoice_unlabelled_text_is_not_an_invoice_layout() -> None:
    ctx, doc, _ = make_case(render_contract(labelled=False), "invoice")
    with pytest.raises(LayoutNotRecognised):
        get_extractors("invoice")["text_layer"].extract(doc, ctx)


def test_purchase_order_labelled_form() -> None:
    result, ctx, doc = extract(render_po(), "purchase_order")
    f = values(result.fields)
    assert f["po_number"] == "PO-7788"
    assert f["po_date"] == "2026-03-01"
    assert f["delivery_date"] == "2026-03-15"
    assert f["buyer_name"] == "Orchid Bay Holdings Pte. Ltd."
    assert f["buyer_uen"] == "202012345H"
    assert f["supplier_name"] == "Meridian Fastener Supplies Pte. Ltd."
    assert f["supplier_uen"] == "201912345R"
    assert f["contract_reference"] == "CT-2026-01"
    assert (f["subtotal"], f["gst_amount"], f["total"]) == ("1500.00", "135.00", "1635.00")
    assert [r.cells["amount"].value for r in result.tables["lines"].rows] == ["1500.00"]


def test_contract_labelled_form() -> None:
    result, *_ = extract(render_contract(), "contract")
    f = values(result.fields)
    assert f["contract_number"] == "CT-2026-01"
    assert f["title"] == "SUPPLY AGREEMENT"
    assert f["contract_date"] == "2026-02-01"
    assert f["effective_date"] == "2026-03-01"
    assert f["end_date"] == "2027-02-28"
    assert f["seller_name"] == "Meridian Fastener Supplies Pte. Ltd."
    assert f["seller_uen"] == "201912345R"
    assert f["buyer_name"] == "Orchid Bay Holdings Pte. Ltd."
    assert f["buyer_uen"] == "202012345H"
    assert f["contract_value"] == "240000.00"
    assert f["currency"] == "SGD"
    assert f["payment_terms"] == "Net 30 days from invoice date"
    assert f["signatory_seller"] == "Lim Hui Min"
    assert f["signatory_buyer"] == "Chen Wei Jie"
    assert result.fields["contract_value"].box is not None


def test_unlabelled_contract_raises_layout_not_recognised() -> None:
    ctx, doc, _ = make_case(render_contract(labelled=False), "contract")
    with pytest.raises(LayoutNotRecognised, match="labelled"):
        get_extractors("contract")["text_layer"].extract(doc, ctx)
