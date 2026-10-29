"""Invoice (layout A: GST on net total, layout B: per-line GST) and purchase order renderers."""

from __future__ import annotations

from typing import Any

from reportlab.pdfbase.pdfmetrics import stringWidth

from .render_common import (
    A4_W,
    DocCanvas,
    DocTruth,
    PageLabel,
    RenderedDoc,
    finalize_pdf,
    fmt_amount,
    fmt_date,
    money_str,
    union_box,
)
from .world_types import InvLine, Invoice, Party, PurchaseOrder

LEFT, RIGHT = 36.0, A4_W - 36.0


def _fit(text: str, width: float, size: float = 8.5) -> float:
    while size > 5.5 and stringWidth(text, "Helvetica", size) > width:
        size -= 0.25
    return size


def _party_block(
    cv: DocCanvas, x: float, y: float, p: Party, *, gst: bool, head: str | None = None
) -> dict[str, dict[str, Any] | None]:
    boxes: dict[str, dict[str, Any] | None] = {}
    if head:
        cv.text(x, y, head, size=8, font="Helvetica-Bold", color=(0.35, 0.35, 0.35))
        y += 13
    boxes["name"] = cv.text(x, y, p.name, size=11, font="Helvetica-Bold")
    cv.text(x, y + 15, p.address, size=8)
    boxes["uen"] = cv.text(x, y + 28, f"UEN: {p.uen}", size=8.5)
    boxes["gst"] = None
    if gst and p.gst_no:
        boxes["gst"] = cv.text(x, y + 41, f"GST Reg No: {p.gst_no}", size=8.5)
    return boxes


def _lines_table(
    cv: DocCanvas, truth: DocTruth, lines: list[InvLine], y: float, per_line_gst: bool
) -> float:
    truth.table("lines", ["description", "quantity", "unit_price", "gst_amount", "amount"])
    cv.line(LEFT, y + 14, RIGHT, y + 14, 0.8)
    heads = [("Description", LEFT, "l"), ("Qty", 330, "r"), ("Unit price (S$)", 405, "r")]
    if per_line_gst:
        heads += [("GST (S$)", 478, "r"), ("Amount (S$)", RIGHT, "r")]
    else:
        heads += [("Amount (S$)", RIGHT, "r")]
    for h, x, a in heads:
        cv.text(x, y, h, size=8, font="Helvetica-Bold", align=a)
    y += 20
    for ln in lines:
        size = _fit(ln.description, 250)
        bd = cv.text(LEFT, y, ln.description, size=size)
        qty = (
            f"{ln.quantity:f}".rstrip("0").rstrip(".")
            if "." in f"{ln.quantity:f}"
            else f"{ln.quantity:f}"
        )
        bq = cv.text(330, y, qty, size=8.5, align="r")
        bu = cv.text(405, y, fmt_amount(ln.unit_price), size=8.5, align="r")
        bg = None
        if per_line_gst:
            bg = cv.text(478, y, fmt_amount(ln.gst or 0), size=8.5, align="r")
        ba = cv.text(RIGHT, y, fmt_amount(ln.amount), size=8.5, align="r")
        cells = {
            "description": (ln.description, ln.description, bd),
            "quantity": (qty, qty, bq),
            "unit_price": (money_str(ln.unit_price), fmt_amount(ln.unit_price), bu),
            "gst_amount": (money_str(ln.gst), fmt_amount(ln.gst), bg)
            if (per_line_gst and ln.gst is not None)
            else (None, None, None),
            "amount": (money_str(ln.amount), fmt_amount(ln.amount), ba),
        }
        truth.add_row("lines", cells, 1, union_box([c[2] for c in cells.values() if c[2]]))
        y += 18
    cv.line(LEFT, y + 2, RIGHT, y + 2, 0.5)
    return y + 12


def render_invoice(inv: Invoice, overrides: dict[str, Any] | None = None) -> RenderedDoc:
    key = inv.key
    truth = DocTruth(key=key, doc_type="invoice")
    f = truth.set_field
    cv = DocCanvas()
    b = _party_block(cv, LEFT, 48, inv.supplier, gst=True)
    f("supplier_name", inv.supplier.name, inv.supplier.name, 1, b["name"])
    f("supplier_uen", inv.supplier.uen, inv.supplier.uen, 1, b["uen"])
    f("supplier_gst_no", inv.supplier.gst_no, inv.supplier.gst_no, 1, b["gst"])
    cv.text(RIGHT, 44, "TAX INVOICE", size=17, font="Helvetica-Bold", align="r")
    y = 70.0
    bx = cv.text(RIGHT, y, f"Invoice No: {inv.number}", size=9.5, align="r", font="Helvetica-Bold")
    f("invoice_number", inv.number, inv.number, 1, bx)
    ds = "dmy_mon"
    bx = cv.text(RIGHT, y + 15, f"Invoice Date: {fmt_date(inv.date, ds)}", size=9, align="r")
    f("invoice_date", inv.date.isoformat(), fmt_date(inv.date, ds), 1, bx)
    bx = cv.text(RIGHT, y + 29, f"Due Date: {fmt_date(inv.due, ds)}", size=9, align="r")
    f("due_date", inv.due.isoformat(), fmt_date(inv.due, ds), 1, bx)
    yy = y + 43
    if inv.po_ref:
        bx = cv.text(RIGHT, yy, f"PO Ref: {inv.po_ref}", size=9, align="r")
        f("po_reference", inv.po_ref, inv.po_ref, 1, bx)
        yy += 14
    else:
        f("po_reference", None, None, 1, None)
    if inv.contract_ref:
        bx = cv.text(RIGHT, yy, f"Contract Ref: {inv.contract_ref}", size=9, align="r")
        f("contract_reference", inv.contract_ref, inv.contract_ref, 1, bx)
    else:
        f("contract_reference", None, None, 1, None)
    b = _party_block(cv, LEFT, 150, inv.buyer, gst=False, head="BILL TO")
    f("buyer_name", inv.buyer.name, inv.buyer.name, 1, b["name"])
    f("buyer_uen", inv.buyer.uen, inv.buyer.uen, 1, b["uen"])
    bx = cv.text(RIGHT, 160, "Currency: SGD", size=9, align="r")
    f("currency", "SGD", "SGD", 1, bx)
    per_line = inv.layout == "B"
    y = _lines_table(cv, truth, inv.lines, 250, per_line)
    cv.text(405, y, "Subtotal", size=9, align="r")
    bs = cv.text(RIGHT, y, fmt_amount(inv.subtotal), size=9, align="r")
    f("subtotal", money_str(inv.subtotal), fmt_amount(inv.subtotal), 1, bs)
    rate_txt = f"GST ({int(inv.gst_rate)}%)"
    cv.text(405, y + 16, rate_txt, size=9, align="r")
    bg = cv.text(RIGHT, y + 16, fmt_amount(inv.gst_amount), size=9, align="r")
    f("gst_amount", money_str(inv.gst_amount), fmt_amount(inv.gst_amount), 1, bg)
    f("gst_rate", format(inv.gst_rate, "f"), f"{int(inv.gst_rate)}%", 1, None)
    cv.text(405, y + 34, "Total payable", size=10, align="r", font="Helvetica-Bold")
    bt = cv.text(RIGHT, y + 34, fmt_amount(inv.total), size=10, align="r", font="Helvetica-Bold")
    f("total", money_str(inv.total), fmt_amount(inv.total), 1, bt)
    cv.text(
        LEFT,
        y + 60,
        "All amounts in Singapore dollars. GST charged at the prevailing rate.",
        size=8,
        color=(0.3, 0.3, 0.3),
    )
    if inv.status == "paid" and inv.paid_date:
        cv.rect(LEFT, y + 80, 190, 30, stroke=True, width=1.2)
        pt = f"PAID  {fmt_date(inv.paid_date, ds)}"
        bp = cv.text(
            LEFT + 95, y + 88, pt, size=12, font="Helvetica-Bold", align="c", color=(0.7, 0.1, 0.1)
        )
        f("payment_status", "paid", "PAID", 1, bp)
        f("paid_date", inv.paid_date.isoformat(), fmt_date(inv.paid_date, ds), 1, bp)
    else:
        f("payment_status", inv.status, None, 1, None)
        f("paid_date", inv.paid_date.isoformat() if inv.paid_date else None, None, 1, None)
    cv.footer("Page 1 of 1" if not inv.blank_page else "Page 1 of 1")
    labels = [PageLabel("invoice", key)]
    if inv.blank_page:
        cv.new_page()
        cv.footer(None)
        labels.append(PageLabel("boilerplate", None))
    raw = cv.finish()
    ov = overrides or {}
    pdf = finalize_pdf(
        raw,
        producer=str(ov.get("producer", "Mock Ledger 5.0")),
        creator=str(ov.get("creator", "Mock Ledger Desktop")),
        title=f"Invoice {inv.number}",
        doc_date=inv.date,
    )
    return RenderedDoc(
        key=key,
        doc_type="invoice",
        stem=f"invoice_{inv.number}",
        pdf=pdf,
        page_labels=labels,
        truth=truth,
        doc_date=inv.date,
        page_start=1,
        page_end=1,
    )


def render_po(po: PurchaseOrder, overrides: dict[str, Any] | None = None) -> RenderedDoc:
    key = po.key
    truth = DocTruth(key=key, doc_type="purchase_order")
    f = truth.set_field
    cv = DocCanvas()
    b = _party_block(cv, LEFT, 48, po.buyer, gst=False)
    f("buyer_name", po.buyer.name, po.buyer.name, 1, b["name"])
    f("buyer_uen", po.buyer.uen, po.buyer.uen, 1, b["uen"])
    cv.text(RIGHT, 44, "PURCHASE ORDER", size=17, font="Helvetica-Bold", align="r")
    ds = "dmy_mon"
    bx = cv.text(RIGHT, 70, f"PO No: {po.number}", size=9.5, align="r", font="Helvetica-Bold")
    f("po_number", po.number, po.number, 1, bx)
    bx = cv.text(RIGHT, 85, f"PO Date: {fmt_date(po.date, ds)}", size=9, align="r")
    f("po_date", po.date.isoformat(), fmt_date(po.date, ds), 1, bx)
    bx = cv.text(RIGHT, 99, f"Delivery by: {fmt_date(po.delivery, ds)}", size=9, align="r")
    f("delivery_date", po.delivery.isoformat(), fmt_date(po.delivery, ds), 1, bx)
    if po.contract_ref:
        bx = cv.text(RIGHT, 113, f"Contract Ref: {po.contract_ref}", size=9, align="r")
        f("contract_reference", po.contract_ref, po.contract_ref, 1, bx)
    else:
        f("contract_reference", None, None, 1, None)
    b = _party_block(cv, LEFT, 150, po.supplier, gst=True, head="SUPPLIER")
    f("supplier_name", po.supplier.name, po.supplier.name, 1, b["name"])
    f("supplier_uen", po.supplier.uen, po.supplier.uen, 1, b["uen"])
    bx = cv.text(RIGHT, 160, "Currency: SGD", size=9, align="r")
    f("currency", "SGD", "SGD", 1, bx)
    y = _lines_table(cv, truth, po.lines, 250, False)
    cv.text(405, y, "Subtotal", size=9, align="r")
    bs = cv.text(RIGHT, y, fmt_amount(po.subtotal), size=9, align="r")
    f("subtotal", money_str(po.subtotal), fmt_amount(po.subtotal), 1, bs)
    cv.text(405, y + 16, "GST (9%)", size=9, align="r")
    bg = cv.text(RIGHT, y + 16, fmt_amount(po.gst_amount), size=9, align="r")
    f("gst_amount", money_str(po.gst_amount), fmt_amount(po.gst_amount), 1, bg)
    cv.text(405, y + 34, "Order total", size=10, align="r", font="Helvetica-Bold")
    bt = cv.text(RIGHT, y + 34, fmt_amount(po.total), size=10, align="r", font="Helvetica-Bold")
    f("total", money_str(po.total), fmt_amount(po.total), 1, bt)
    cv.footer("Page 1 of 1")
    raw = cv.finish()
    ov = overrides or {}
    pdf = finalize_pdf(
        raw,
        producer=str(ov.get("producer", "Mock Ledger 5.0")),
        creator=str(ov.get("creator", "Mock Ledger Desktop")),
        title=f"Purchase Order {po.number}",
        doc_date=po.date,
    )
    return RenderedDoc(
        key=key,
        doc_type="purchase_order",
        stem=f"purchase_order_{po.n}",
        pdf=pdf,
        page_labels=[PageLabel("purchase_order", key)],
        truth=truth,
        doc_date=po.date,
        page_start=1,
        page_end=1,
    )
