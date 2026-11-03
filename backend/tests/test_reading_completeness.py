"""Completeness guard (extract/completeness.py) and what the invoice rules do with it.

A reading that lacks printed item lines must never become a high flag; a tampered amount on a
completely read invoice must still be one.
"""

from __future__ import annotations

import pytest

from app.checks.invoice import reading_incomplete
from app.core.models import Cell
from app.extract.completeness import canon_amount, extracted_amounts, missed_in_text
from tests.checks_fin_factory import ctx_of, make_invoice, run_rule

RECEIPT_TEXT = """\
        MASTORIS DINER
   1 HAM&EGG BURRITO              9,99
   1 SS PANCAKES                  5.99
     ADD STRWBER                  3.00
   2 HOT TEA                      4.98
     subtotal                    23.96
     Tax                          1.65
     Payment Due                 25.61
"""


@pytest.mark.parametrize(
    ("text", "canon"),
    [
        ("2 053,73", "205373"),
        ("$ 2 259,10", "225910"),
        ("1.234,56", "123456"),
        ("1,234.56", "123456"),
        ("2977 .00", "297700"),
        ("2.05-", "205"),
        ("985-73-8194", None),  # a tax id
        ("12,3", None),  # no two-digit decimals
        ("S8222222A", None),
        (None, None),
    ],
)
def test_canon_amount(text: str | None, canon: str | None) -> None:
    assert canon_amount(text) == canon


def _known(*raw: str) -> set[str]:
    return extracted_amounts(Cell(value=None, raw=r) for r in raw)


def test_every_printed_row_read_means_nothing_missed() -> None:
    assert (
        missed_in_text(RECEIPT_TEXT, _known("9,99", "5.99", "3.00", "4.98", "23.96", "1.65")) == []
    )


def test_a_skipped_row_is_reported() -> None:
    missed = missed_in_text(RECEIPT_TEXT, _known("9,99", "5.99", "4.98", "23.96", "1.65"))
    assert [m.amount for m in missed] == ["300"]
    assert "ADD STRWBER" in missed[0].text


def test_totals_and_tax_lines_are_not_items() -> None:
    # nothing read at all: only the four item rows are "missing", never subtotal, tax or the total
    missed = missed_in_text(RECEIPT_TEXT, set())
    assert [m.amount for m in missed] == ["999", "599", "300", "498"]


def test_a_discount_line_must_be_read_too() -> None:
    text = "  1 Caprese        10.00\n  Open $ Disc     2..05-\n  subtotal       7.95\n"
    missed = missed_in_text(text, _known("10.00", "7.95"))
    assert [(m.kind, m.amount) for m in missed] == [("adjustment", "205")]
    assert missed_in_text(text, _known("10.00", "7.95", "2.05-")) == []


def test_a_table_row_counts_as_read_when_any_of_its_amounts_was_read() -> None:
    row = "1. Marble Table   2,00  each  444,60  889,20   10%   978,12"
    assert missed_in_text(row, _known("889,20")) == []  # net worth taken, gross not
    assert [m.amount for m in missed_in_text(row, _known("7,13"))] == ["97812"]


def test_one_wrong_digit_from_the_image_reading_still_matches() -> None:
    assert missed_in_text("1 HOT TEA      14.98", _known("14.93")) == []


def test_amounts_inside_a_line_of_prose_or_ids_are_not_rows() -> None:
    text = "Tax Id: 985-73-8194\nPhone 206-441-9785\nCard Num XXXX4737\nTRAN#: 2387827\n"
    assert missed_in_text(text, set()) == []


# ---- what the rules do with an incomplete reading


def _incomplete(doc):  # noqa: ANN001, ANN202
    doc.warnings.append("reading_incomplete: page 1: 2 printed lines not read ('ADD STRWBER')")
    return doc


def test_incomplete_reading_is_low_and_never_a_mismatch() -> None:
    doc = _incomplete(make_invoice(fields={"subtotal": "9999.00", "total": "12345.00"}))
    assert reading_incomplete(doc) is not None
    for rule in ("inv_lines", "inv_gst", "inv_total"):
        flags = run_rule(rule, ctx_of(doc))
        assert [f.severity for f in flags] == ["low"], rule
        assert "incomplete" in flags[0].explanation.lower()


def test_a_complete_reading_with_a_changed_total_still_flags_high() -> None:
    doc = make_invoice(fields={"total": "12345.00"})
    flags = run_rule("inv_total", ctx_of(doc))
    assert [f.severity for f in flags] == ["high"]


def test_a_complete_reading_with_a_changed_line_amount_still_flags_high() -> None:
    lines = [
        {
            "description": "Freight Mar",
            "quantity": "4",
            "unit_price": "2000.00",
            "amount": "8500.00",
        },
        {"description": "Handling", "quantity": "20", "unit_price": "100.00", "amount": "2000.00"},
    ]
    flags = run_rule("inv_lines", ctx_of(make_invoice(lines=lines)))
    assert flags and all(f.severity == "high" for f in flags)


def test_a_gross_amount_beside_a_net_unit_price_is_not_a_tamper() -> None:
    lines = [
        {"description": "Table", "quantity": "2.00", "unit_price": "444.60", "amount": "978.12"}
    ]
    doc = make_invoice(
        fields={
            "subtotal": "889.20",
            "gst_rate": "10.00",
            "gst_amount": "88.92",
            "total": "978.12",
        },
        lines=lines,
    )
    assert run_rule("inv_lines", ctx_of(doc)) == []
    lines[0]["amount"] = "988.12"  # changed by ten
    doc = make_invoice(
        fields={
            "subtotal": "889.20",
            "gst_rate": "10.00",
            "gst_amount": "88.92",
            "total": "978.12",
        },
        lines=lines,
    )
    assert run_rule("inv_lines", ctx_of(doc))


# ---- through the cascade: a scanned receipt whose reading skipped a printed row

_TILL_LINES = [
    ("1 HAM&EGG BURRITO", "9.99"),
    ("1 SS PANCAKES", "5.99"),
    ("ADD STRWBER", "3.00"),
    ("2 HOT TEA", "4.98"),
    ("Open $ Disc", "2.00-"),
    ("subtotal", "21.96"),
    ("Tax", "1.54"),
    ("Total", "23.50"),
]


def _till_pdf() -> bytes:
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 11)
    y = 780
    c.drawString(60, y, "MASTORIS DINER")
    for text, amount in _TILL_LINES:
        y -= 22
        c.drawString(60, y, text)
        c.drawRightString(400, y, amount)
    c.save()
    return buf.getvalue()


def _till_json(skip: set[str]) -> dict:
    rows = [
        ("HAM&EGG BURRITO", None, None, "9.99"),
        ("SS PANCAKES", None, None, "5.99"),
        ("ADD STRWBER", None, None, "3.00"),
        ("HOT TEA", None, None, "4.98"),
        ("Open $ Disc", None, None, "2.00-"),
    ]
    from app.core.schemas import DOC_SCHEMAS

    fields = dict.fromkeys(DOC_SCHEMAS["receipt"].fields)
    fields.update(
        merchant_name="MASTORIS DINER", subtotal="21.96", tax_amount="1.54", total="23.50"
    )
    return {
        "fields": fields,
        "tables": {
            "lines": [
                {
                    "description": d,
                    "quantity": q,
                    "unit_price": p,
                    "gst_amount": None,
                    "amount": a,
                }
                for d, q, p, a in rows
                if d not in skip
            ]
        },
    }


def _scan_settings(tmp_path):  # noqa: ANN001, ANN202
    from app.core.config import Settings

    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        db_path=":memory:",
        llm_backend="openai",
        llm_api_key="k",
        llm_model_cheap="m",
        llm_model_strong="m",
        llm_supports_vision=False,
        llm_vision_fallback=True,
        extractor_mode="cascade",
    )


def _extract_till(tmp_path, cheap_skips, strong_skips):  # noqa: ANN001, ANN202
    from app.core.llm import FakeLLMClient
    from app.extract.cascade import extract_document
    from app.extract.registry import discover
    from tests.extract_helpers import make_case

    discover()
    llm = FakeLLMClient(
        {
            "extract:receipt:cheap": lambda req: _till_json(cheap_skips),
            "extract:receipt:strong": lambda req: _till_json(strong_skips),
        }
    )
    ctx, doc, _ = make_case(
        _till_pdf(), "receipt", llm=llm, is_scan=True, settings=_scan_settings(tmp_path)
    )
    result, meta = extract_document(doc, ctx, render=lambda *a, **k: b"\x89PNG")
    return result, meta, llm


def test_a_skipped_discount_row_is_reread_from_the_image(tmp_path) -> None:  # noqa: ANN001
    result, meta, llm = _extract_till(tmp_path, {"Open $ Disc", "ADD STRWBER"}, set())
    assert [c.model_role for c in llm.calls] == ["cheap", "strong"]
    assert len(result.tables["lines"].rows) == 5
    assert not any(w.startswith("reading_incomplete") for w in result.warnings)
    assert meta.status == "ok"


def test_a_row_both_readers_skip_marks_the_reading_incomplete(tmp_path) -> None:  # noqa: ANN001
    result, meta, _ = _extract_till(tmp_path, {"Open $ Disc"}, {"Open $ Disc"})
    assert any(w.startswith("reading_incomplete: page 1") for w in result.warnings)
    assert meta.status == "partial"
    # the amounts the arithmetic would lean on are marked not verified
    assert result.fields["total"].verified is False
    assert all(r.cells["amount"].verified is False for r in result.tables["lines"].rows)


def test_a_complete_reading_is_left_alone(tmp_path) -> None:  # noqa: ANN001
    result, meta, llm = _extract_till(tmp_path, set(), set())
    assert [c.model_role for c in llm.calls] == ["cheap"]
    assert meta.status == "ok" and result.fields["total"].verified is not False


# ---- tamper: one changed amount on a completely read, scanned invoice still flags (both formats)


def _invoice_pdf(rows, sub, gst, total) -> bytes:  # noqa: ANN001
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 10)
    c.drawString(60, 800, "Invoice no: 97159829")
    c.drawString(60, 780, "Date of issue: 18/09/2015")
    c.drawString(60, 760, "Seller: Bradley-Andrade")
    c.drawString(60, 740, "Client: Castro PLC")
    y = 700
    c.drawString(60, y, "Description")
    c.drawRightString(300, y, "Qty")
    c.drawRightString(380, y, "Net price")
    c.drawRightString(470, y, "Amount")
    for d, q, p, a in rows:
        y -= 20
        c.drawString(60, y, d)
        c.drawRightString(300, y, q)
        c.drawRightString(380, y, p)
        c.drawRightString(470, y, a)
    for label, v in (("Subtotal", sub), ("VAT 10%", gst), ("Total", total)):
        y -= 20
        c.drawString(300, y, label)
        c.drawRightString(470, y, v)
    c.save()
    return buf.getvalue()


def _invoice_json(rows, sub, gst, total) -> dict:  # noqa: ANN001
    from app.core.schemas import DOC_SCHEMAS

    fields = dict.fromkeys(DOC_SCHEMAS["invoice"].fields)
    fields.update(
        invoice_number="97159829",
        supplier_name="Bradley-Andrade",
        buyer_name="Castro PLC",
        subtotal=sub,
        gst_rate="10%",
        gst_amount=gst,
        total=total,
    )
    lines = [
        {"description": d, "quantity": q, "unit_price": p, "gst_amount": None, "amount": a}
        for d, q, p, a in rows
    ]
    return {"fields": fields, "tables": {"lines": lines}}


def _run_invoice(tmp_path, rows, sub, gst, total):  # noqa: ANN001, ANN202
    from app.core.llm import FakeLLMClient
    from app.core.models import Document
    from app.extract.cascade import extract_document
    from app.extract.registry import discover
    from tests.extract_helpers import make_case

    discover()
    data = _invoice_json(rows, sub, gst, total)
    llm = FakeLLMClient({"extract:invoice": lambda req: data})
    ctx, doc, _ = make_case(
        _invoice_pdf(rows, sub, gst, total),
        "invoice",
        llm=llm,
        is_scan=True,
        settings=_scan_settings(tmp_path),
    )
    result, meta = extract_document(doc, ctx, render=lambda *a, **k: b"\x89PNG")
    out = Document(
        **{
            **doc.model_dump(),
            "fields": result.fields,
            "tables": result.tables,
            "warnings": result.warnings,
            "status": meta.status,
        }
    )
    return out, result


@pytest.mark.parametrize("comma", [False, True])
def test_a_changed_amount_on_a_completely_read_scanned_invoice_still_flags_high(
    tmp_path,
    comma: bool,  # noqa: ANN001
) -> None:
    def fmt(v: str) -> str:
        return v.replace(".", ",") if comma else v

    clean = [
        ("Desk lamp", fmt("2.00"), fmt("100.00"), fmt("200.00")),
        ("Chair", fmt("3.00"), fmt("150.00"), fmt("450.00")),
    ]
    sub, gst, total = fmt("650.00"), fmt("65.00"), fmt("715.00")
    doc, _ = _run_invoice(tmp_path, clean, sub, gst, total)
    assert not any(w.startswith("reading_incomplete") for w in doc.warnings)
    for rule in ("inv_lines", "inv_total", "inv_gst"):
        assert run_rule(rule, ctx_of(doc)) == [], rule  # the untouched invoice is silent

    # a forger changes one line amount (200 -> 2000) and the printed subtotal is left alone
    edited = [clean[0][:3] + (fmt("2000.00"),), clean[1]]
    doc, _ = _run_invoice(tmp_path, edited, sub, gst, total)
    assert not any(w.startswith("reading_incomplete") for w in doc.warnings)
    flags = run_rule("inv_lines", ctx_of(doc))
    assert flags and all(f.severity == "high" for f in flags)

    # a forger changes only the printed total
    doc, _ = _run_invoice(tmp_path, clean, sub, gst, fmt("7150.00"))
    flags = run_rule("inv_total", ctx_of(doc))
    assert [f.severity for f in flags] == ["high"]
