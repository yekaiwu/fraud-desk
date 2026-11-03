"""Family 7: invoice games - duplicates, splitting, round-tripping, fabrication, double use of a
deposit, invoices built to match an existing credit."""

from __future__ import annotations

import contextlib
import io
import random
from decimal import Decimal

import pikepdf

from ..core import Bundle, Outcome, attack
from ..models import Invoice, round2
from ..pdfkit import fmt_money
from ._util import invoice, valid_uen

F = "F7 invoice games"


def retext(inv: Invoice, repl: dict[str, str]) -> None:
    """Replace whole text items (exact match) or items starting with a key ending in ':'."""
    for it in inv.edit.items(0):
        for old, new in repl.items():
            if it.text == old or (old.endswith(" ") and it.text.startswith(old)):
                inv.edit.set_text(it, new, align="left" if it.x < 300 else "right")


def clone(b: Bundle, src: str, number: str, **kw) -> tuple[str, Invoice]:
    inv = invoice(b, src)
    old_no = src.split("_")[1].removesuffix(".pdf")
    repl = {f"Invoice No: {old_no}": f"Invoice No: {number}"}
    for k, v in kw.items():
        repl[k] = v
    retext(inv, repl)
    name = f"invoice_{number}.pdf"
    return name, inv


def _finish(b: Bundle, name: str, inv: Invoice, number: str, src: str) -> None:
    data = inv.dump()
    pdf = pikepdf.open(io.BytesIO(data))
    pdf.docinfo["/Title"] = f"Invoice {number}"
    out = io.BytesIO()
    pdf.save(out)
    b.add(name, out.getvalue())


@attack(
    "R70",
    F,
    "Paid invoice cloned under a new number (same date, supplier, total) and also marked paid by the same deposit",
)
def r70(b: Bundle, rng: random.Random) -> Outcome:
    src = "invoice_INV-2025-0109.pdf"
    name, inv = clone(b, src, "INV-2025-0115")
    _finish(b, name, inv, "INV-2025-0115", src)
    return Outcome(
        "The same sale presented twice; one genuine deposit stands behind both invoices.", [name]
    )


@attack(
    "R71", F, "Duplicate with the same number but invoice date moved one day (fingerprint changes)"
)
def r71(b: Bundle, rng: random.Random) -> Outcome:
    src = "invoice_INV-2025-0109.pdf"
    inv = invoice(b, src)
    retext(
        inv,
        {
            "Invoice Date: 22 Sep 2025": "Invoice Date: 23 Sep 2025",
            "Due Date: 06 Oct 2025": "Due Date: 07 Oct 2025",
        },
    )
    data = inv.dump()
    b.add("invoice_INV-2025-0109_copy.pdf", data)
    return Outcome(
        "Same invoice number reused with a different date.", ["invoice_INV-2025-0109_copy.pdf"]
    )


@attack(
    "R72",
    F,
    "Duplicate with the same number and date and the total raised by about S$0.50 (under the S$1.00 tolerance)",
)
def r72(b: Bundle, rng: random.Random) -> Outcome:
    src = "invoice_INV-2025-0109.pdf"
    inv = invoice(b, src)
    k = min(range(len(inv.lines)), key=lambda i: int(inv.lines[i][1].text))
    qty = int(inv.lines[k][1].text)
    unit = Decimal(inv.lines[k][2].text.replace(",", ""))
    inv.edit.set_text(inv.lines[k][2], fmt_money(unit + Decimal("0.01")))
    sub, gst, total = inv.recompute()
    b.add("invoice_INV-2025-0109_resubmitted.pdf", inv.dump())
    return Outcome(
        f"Unit price raised by S$0.01 on a quantity of {qty}: the total differs by a few cents from the original.",
        ["invoice_INV-2025-0109_resubmitted.pdf"],
    )


@attack(
    "R73",
    F,
    "One invoice split into two consecutive invoices that add up to the original",
    consistent=True,
)
def r73(b: Bundle, rng: random.Random) -> Outcome:
    src = "invoice_INV-2025-0107.pdf"
    a = invoice(b, src)
    c = invoice(b, src)
    # a keeps line 1; c keeps lines 2-3
    for it in a.lines[1:]:
        for x in (it[0], it[1], it[2], it[3], it[4]):
            if x is not None:
                a.edit.remove(x)
    a.lines = a.lines[:1]
    a.recompute()
    for it in c.lines[:1]:
        for x in (it[0], it[1], it[2], it[3], it[4]):
            if x is not None:
                c.edit.remove(x)
    c.lines = c.lines[1:]
    c.recompute()
    retext(c, {"Invoice No: INV-2025-0109": "Invoice No: INV-2025-0115"})
    b.replace(src, a.dump())
    _finish(b, "invoice_INV-2025-0115.pdf", c, "INV-2025-0115", src)
    return Outcome(
        "Two invoices for one sale, e.g. to stay under a per-invoice limit downstream.",
        [src, "invoice_INV-2025-0115.pdf"],
    )


@attack(
    "R74",
    F,
    "Round trip: a mirror invoice from the customer back to the applicant for the same amount",
)
def r74(b: Bundle, rng: random.Random) -> Outcome:
    src = "invoice_INV-2025-0107.pdf"
    inv = invoice(b, src)
    retext(
        inv,
        {
            "Meridian Fastener Supplies Pte. Ltd.": "Acme Trading Pte. Ltd.",
            "20 Kaki Bukit Road #01-48, Singapore 239198": "84 Pandan Crescent #07-58, Singapore 484926",
            "UEN: 201873387K": "UEN: 200414209D",
            "GST Reg No: M38619861E": "GST Reg No: M20041420D",
        },
    )
    items = inv.edit.items(0)
    # the BILL TO block holds the old buyer's name/address/UEN: set them to the applicant
    [i for i, it in enumerate(items) if it.text == "BILL TO"][0]
    return _mirror(b, inv, src)


def _mirror(b: Bundle, inv: Invoice, src: str) -> Outcome:
    # rewrite the BILL TO block explicitly after the seller block was rewritten
    {it.y: it for it in inv.edit.items(0)}
    bill = [it for it in inv.edit.items(0) if it.text == "BILL TO"][0]
    below = sorted(
        (it for it in inv.edit.items(0) if it.y < bill.y and it.x < 100), key=lambda i: -i.y
    )
    texts = [it for it in below if it.text and not it.text.startswith(("Currency", "Description"))][
        :3
    ]
    vals = [
        "Meridian Fastener Supplies Pte. Ltd.",
        "20 Kaki Bukit Road #01-48, Singapore 239198",
        "UEN: 201873387K",
    ]
    for it, v in zip(texts, vals, strict=False):
        inv.edit.set_text(it, v, align="left")
    name = "invoice_ACM-2025-0031.pdf"
    retext(inv, {"Invoice No: INV-2025-0107": "Invoice No: ACM-2025-0031"})
    _finish(b, name, inv, "ACM-2025-0031", src)
    return Outcome(
        "Applicant and customer invoice each other for the same goods and amount (nets to zero, looks like two sales).",
        [name],
    )


@attack(
    "R75",
    F,
    "Invoice with the contract reference removed and quantities x3 (cannot be compared with any contract)",
)
def r75(b: Bundle, rng: random.Random) -> Outcome:
    src = "invoice_INV-2025-0110.pdf"
    inv = invoice(b, src)
    for k in range(len(inv.lines)):
        inv.set_qty(k, int(int(inv.lines[k][1].text) * 3))
    inv.recompute()
    with contextlib.suppress(KeyError):
        inv.edit.remove(inv.item("Contract Ref"))
    with contextlib.suppress(KeyError):
        inv.edit.remove(inv.item("PO Ref"))
    b.replace(src, inv.dump())
    return Outcome(
        "Triple an invoice that was paid, delete its references. The paid claim now has no deposit of that size.",
        [src],
    )


@attack(
    "R76",
    F,
    "Invoice built to match an existing deposit (S$5,000.00 retainer from a non-customer), marked paid",
)
def r76(b: Bundle, rng: random.Random) -> Outcome:
    src = "invoice_INV-2025-0109.pdf"
    inv = invoice(b, src)
    # one line only: 4,587.16 + 9% GST = 5,000.04 -> choose 4,587.15 -> 5,000.0335 ~ 5,000.03 (within the S$1 tolerance)
    for it in inv.lines[1:]:
        for x in (it[0], it[1], it[2], it[3], it[4]):
            if x is not None:
                inv.edit.remove(x)
    inv.lines = inv.lines[:1]
    inv.edit.set_text(inv.lines[0][1], "1")
    inv.edit.set_text(inv.lines[0][2], "4,587.16")
    inv.recompute()
    # buyer: the depositor on the statement
    bill = [it for it in inv.edit.items(0) if it.text == "BILL TO"][0]
    below = sorted(
        (it for it in inv.edit.items(0) if it.y < bill.y and it.x < 100), key=lambda i: -i.y
    )
    texts = [it for it in below if not it.text.startswith(("Currency", "Description"))][:3]
    for it, v in zip(
        texts,
        [
            "Elmstead Trading Pte. Ltd.",
            "9 Changi South Lane #03-11, Singapore 486015",
            f"UEN: {valid_uen(rng, 2016)}",
        ],
        strict=False,
    ):
        inv.edit.set_text(it, v, align="left")
    retext(
        inv,
        {
            "Invoice No: INV-2025-0109": "Invoice No: INV-2025-0115",
            "Invoice Date: 22 Sep 2025": "Invoice Date: 28 Aug 2025",
            "Due Date: 06 Oct 2025": "Due Date: 12 Sep 2025",
            "PAID  10 Oct 2025": "PAID  04 Sep 2025",
        },
    )
    _finish(b, "invoice_INV-2025-0115.pdf", inv, "INV-2025-0115", src)
    return Outcome(
        "A deposit that is not trade income (a retainer) is presented as the payment of a new invoice.",
        ["invoice_INV-2025-0115.pdf"],
    )


@attack(
    "R77",
    F,
    "Fabricated supplier invoice from scratch: new company, checksum-valid UEN, correct arithmetic, unpaid",
    consistent=True,
)
def r77(b: Bundle, rng: random.Random) -> Outcome:
    from reportlab.pdfgen import canvas

    uen = valid_uen(rng)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(595.27, 841.89))
    c.setFont("Helvetica-Bold", 15)
    c.drawString(36, 790, "Larchfield Industrial Components Pte. Ltd.")
    c.setFont("Helvetica", 8.5)
    y = 770
    for t in [
        "11 Tuas South Street 5 #02-14, Singapore 637801",
        f"UEN: {uen}",
        "GST Reg No: M91234567X",
    ]:
        c.drawString(36, y, t)
        y -= 14
    c.setFont("Helvetica-Bold", 15)
    c.drawString(450, 790, "TAX INVOICE")
    c.setFont("Helvetica", 8.5)
    for i, t in enumerate(
        ["Invoice No: LIC-2025-0442", "Invoice Date: 02 Oct 2025", "Due Date: 01 Nov 2025"]
    ):
        c.drawString(430, 770 - i * 14, t)
    c.drawString(36, 700, "BILL TO")
    for i, t in enumerate(
        [
            "Meridian Fastener Supplies Pte. Ltd.",
            "20 Kaki Bukit Road #01-48, Singapore 239198",
            "UEN: 201873387K",
        ]
    ):
        c.drawString(36, 686 - i * 14, t)
    sub = Decimal("38400.00")
    gst = round2(sub * Decimal("0.09"))
    y = 620
    c.drawString(36, y, "Description")
    c.drawString(300, y, "Qty")
    c.drawString(350, y, "Unit price (S$)")
    c.drawString(480, y, "Amount (S$)")
    y -= 16
    c.drawString(36, y, "Hot-rolled steel bar 20mm (tonne)")
    c.drawString(305, y, "32")
    c.drawString(390, y, "1,200.00")
    c.drawRightString(560, y, fmt_money(sub))
    y -= 40
    for lab, v in (("Subtotal", sub), ("GST (9%)", gst), ("Total payable", sub + gst)):
        c.drawString(380, y, lab)
        c.drawRightString(560, y, fmt_money(v))
        y -= 14
    c.setFont("Helvetica", 7)
    c.drawString(36, 22, "Page 1 of 1")
    c.save()
    pdf = pikepdf.open(io.BytesIO(buf.getvalue()))
    pdf.docinfo["/Producer"] = "Mock Ledger 5.0"
    pdf.docinfo["/Creator"] = "Mock Ledger Desktop"
    pdf.docinfo["/Title"] = "Invoice LIC-2025-0442"
    pdf.docinfo["/CreationDate"] = "D:20251002090000+08'00'"
    pdf.docinfo["/ModDate"] = "D:20251002090000+08'00'"
    out = io.BytesIO()
    pdf.save(out)
    b.add("invoice_LIC-2025-0442.pdf", out.getvalue())
    return Outcome(
        "A supplier invoice for a company that does not exist, with a checksum-valid UEN, billing the applicant (expense padding / sham trade).",
        ["invoice_LIC-2025-0442.pdf"],
        [fmt_money(sub + gst)],
    )
