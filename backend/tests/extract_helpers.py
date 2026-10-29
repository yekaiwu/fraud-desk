"""Helpers for the extract tests (not collected). No dependency on the generator or on app.ingest.

* ``pdf_words`` reads a PDF with PyMuPDF the way ingest will (rawdict, page fractions, top-left origin);
* ``render_statement`` draws the four statement layouts of test-data-and-eval.md 4.1 with reportlab;
* ``render_invoice`` / ``render_fs`` / ``render_contract`` / ``render_po`` draw labelled forms;
* ``make_case`` builds a ``CaseContext`` with the PDF's words, a FileRef and one Document.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.core.config import Settings
from app.core.models import Classification, Document, FileRef, PageInfo, Word
from app.core.testing import make_ctx

PAGE_W, PAGE_H = A4
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# --------------------------------------------------------------------------------------
# Reading words like ingest does
# --------------------------------------------------------------------------------------


def _flush(
    cur: list[dict], span: dict, bi: int, li: int, pno: int, rect, words: list[Word]
) -> None:  # noqa: ANN001
    if not cur:
        return
    text = "".join(c["c"] for c in cur)
    x0 = min(c["bbox"][0] for c in cur)
    y0 = min(c["bbox"][1] for c in cur)
    x1 = max(c["bbox"][2] for c in cur)
    y1 = max(c["bbox"][3] for c in cur)
    words.append(
        Word(
            page=pno,
            text=text,
            x=max(0.0, x0 / rect.width),
            y=max(0.0, y0 / rect.height),
            w=min(1.0, (x1 - x0) / rect.width),
            h=min(1.0, (y1 - y0) / rect.height),
            font=span["font"],
            size=round(span["size"], 1),
            line=li,
            block=bi,
        )
    )
    cur.clear()


def pdf_words(pdf: bytes) -> dict[int, list[Word]]:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    out: dict[int, list[Word]] = {}
    for pno, page in enumerate(doc, start=1):
        rect = page.rect
        words: list[Word] = []
        raw = page.get_text("rawdict")
        for bi, block in enumerate(raw["blocks"]):
            if block.get("type") != 0:
                continue
            for li, line in enumerate(block["lines"]):
                for span in line["spans"]:
                    cur: list[dict] = []
                    for ch in span["chars"]:
                        if ch["c"].isspace():
                            _flush(cur, span, bi, li, pno, rect, words)
                        else:
                            cur.append(ch)
                    _flush(cur, span, bi, li, pno, rect, words)
        out[pno] = words
    return out


def make_case(
    pdf: bytes,
    doc_type: str,
    *,
    settings: Settings | None = None,
    llm=None,  # noqa: ANN001
    is_scan: bool = False,
    keep_words: bool = True,
    **ctx_kw,  # noqa: ANN003
):
    """A CaseContext holding the PDF as file f1 and one Document d1 spanning all its pages.

    ``is_scan=True`` marks every page as a scan; ``keep_words`` then decides whether the page words
    are present (they stand for OCR words, an independent reader) or absent (OCR failed).
    """
    ctx = make_multi_case(
        [(pdf, doc_type, is_scan, keep_words)], settings=settings, llm=llm, **ctx_kw
    )
    doc = ctx.case.documents[0]
    return ctx, doc, ctx.case.files[0].sha256


def make_multi_case(items, *, settings=None, llm=None, **ctx_kw):  # noqa: ANN001, ANN003, ANN201
    """items: ``(pdf bytes, doc_type, is_scan, keep_words)``; file f<i>, document d<i>."""
    files, docs, all_words = [], [], {}
    for i, (pdf, doc_type, is_scan, keep_words) in enumerate(items, start=1):
        words = pdf_words(pdf)
        pages = [
            PageInfo(
                page=p,
                width_pt=PAGE_W,
                height_pt=PAGE_H,
                has_text_layer=bool(ws) and not is_scan,
                is_scan=is_scan,
                word_count=len(ws),
            )
            for p, ws in words.items()
        ]
        ref = FileRef(
            id=f"f{i}",
            name=f"x{i}.pdf",
            sha256="0" * 64,
            size_bytes=len(pdf),
            mime="application/pdf",
            pages=pages,
        )
        files.append((ref, pdf))
        docs.append(
            Document(
                id=f"d{i}",
                file_id=f"f{i}",
                page_start=1,
                page_end=len(pages),
                doc_type=doc_type,
                classification=Classification(
                    label=doc_type, probability=1.0, classifier="test", version="1"
                ),
                words=[w for ws in words.values() for w in ws]
                if keep_words and not is_scan
                else [],
            )
        )
        if keep_words:
            for p, ws in words.items():
                all_words[(f"f{i}", p)] = ws
    kw = dict(ctx_kw)
    if settings is not None:
        kw["settings"] = settings
    if llm is not None:
        kw["llm"] = llm
    ctx = make_ctx(docs, files=files, **kw)
    ctx.page_words.update(all_words)
    return ctx


# --------------------------------------------------------------------------------------
# Bank statements
# --------------------------------------------------------------------------------------


@dataclass
class Txn:
    day: date
    desc: str
    debit: Decimal | None = None
    credit: Decimal | None = None


@dataclass
class StatementSpec:
    layout: str = "dbs"  # dbs | ocbc | uob | software
    holder: str = "Meridian Fastener Supplies Pte. Ltd."
    account: str = "123-456-789-0"
    uen: str = "201912345R"
    start: date = date(2026, 3, 1)
    end: date = date(2026, 3, 31)
    opening: Decimal = Decimal("10000.00")
    txns: list[Txn] = field(default_factory=list)
    rows_per_page: int = 22
    wrap_at: int = 46
    wrap_by_layout: bool = True
    balance_edits: dict[int, str] = field(default_factory=dict)  # row index -> printed balance
    amount_edits: dict[int, str] = field(
        default_factory=dict
    )  # row index -> printed amount (debit/credit)
    drop_balance_column: bool = False
    date_edits: dict[int, str] = field(default_factory=dict)  # row index -> printed date
    currency: str = "SGD"
    show_totals: bool = True
    footer_specimen: bool = True
    header_labels: dict[str, str] = field(default_factory=dict)  # printed header text per column


def default_txns(n: int = 40) -> list[Txn]:
    out: list[Txn] = []
    d = date(2026, 3, 2)
    for i in range(n):
        if i % 3 == 1:
            out.append(
                Txn(
                    d,
                    f"FAST PAYMENT FROM ACME TRADING PTE LTD REF INV-{100 + i}",
                    credit=Decimal(f"{1200 + i * 37}.50"),
                )
            )
        elif i % 7 == 0:
            out.append(Txn(d, "SERVICE CHARGE", debit=Decimal("12.00")))
        else:
            out.append(
                Txn(d, f"GIRO PAYMENT TO SUPPLIER {i} PTE LTD", debit=Decimal(f"{80 + i * 9}.25"))
            )
        d += timedelta(days=1)
        if d.weekday() >= 5:
            d += timedelta(days=2)
    return out


def money(d: Decimal) -> str:
    return f"{d:,.2f}"


def fmt_date(layout: str, d: date) -> str:
    if layout == "dbs":
        return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}"
    if layout == "ocbc":
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    if layout == "uob":
        return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"
    return d.isoformat()


def _wrap(text: str, width: int) -> list[str]:
    if len(text) <= width:
        return [text]
    cut = text.rfind(" ", 0, width)
    cut = cut if cut > 0 else width
    return [text[:cut], text[cut:].strip()]


def statement_truth(spec: StatementSpec) -> list[dict]:
    """Expected rows: date iso, description, debit, credit, balance (canonical strings)."""
    bal = spec.opening
    rows = []
    for t in spec.txns:
        bal = bal + (t.credit or 0) - (t.debit or 0)
        rows.append(
            {
                "date": t.day.isoformat(),
                "description": t.desc,
                "debit": f"{t.debit:.2f}" if t.debit is not None else None,
                "credit": f"{t.credit:.2f}" if t.credit is not None else None,
                "balance": f"{bal:.2f}",
            }
        )
    return rows


def render_statement(spec: StatementSpec) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    lay = spec.layout
    pages = [
        spec.txns[i : i + spec.rows_per_page] for i in range(0, len(spec.txns), spec.rows_per_page)
    ] or [[]]
    n_pages = len(pages)

    def neg(b: Decimal) -> str:
        return f"({money(-b)})" if b < 0 else money(b)

    # columns: (key, x, align)
    if lay == "dbs":
        cols = [
            ("Date", 40, "l"),
            ("Description", 120, "l"),
            ("Withdrawals", 400, "r"),
            ("Deposits", 470, "r"),
            ("Balance", 550, "r"),
        ]
    elif lay == "ocbc":
        cols = [
            ("Date", 40, "l"),
            ("Value Date", 105, "l"),
            ("Description", 170, "l"),
            ("Cheque", 360, "l"),
            ("Withdrawal", 430, "r"),
            ("Deposit", 495, "r"),
            ("Balance", 555, "r"),
        ]
    elif lay == "uob":
        cols = [
            ("Date", 40, "l"),
            ("Transaction", 120, "l"),
            ("Amount", 440, "r"),
            ("Balance", 550, "r"),
        ]
    else:
        cols = [
            ("Date", 40, "l"),
            ("Description", 120, "l"),
            ("Debit", 400, "r"),
            ("Credit", 470, "r"),
            ("Balance", 550, "r"),
        ]
    if spec.drop_balance_column:
        cols = [col for col in cols if col[0] != "Balance"]
    xs = {k: (x, a) for k, x, a in cols}

    def put(key: str, text: str, y: float, font: str = "Helvetica", size: int = 9) -> None:
        x, a = xs[key]
        c.setFont(font, size)
        if a == "r":
            c.drawRightString(x, y, text)
        else:
            c.drawString(x, y, text)

    bal = spec.opening
    idx = 0
    total_d = Decimal(0)
    total_c = Decimal(0)
    for pno, chunk in enumerate(pages, start=1):
        y = PAGE_H - 50
        if lay == "software":
            c.setFont("Helvetica-Bold", 16)
            c.drawString(40, y, spec.holder + " - Ledger Report")
        else:
            bank = {"dbs": "DBS Bank Ltd", "ocbc": "OCBC Bank", "uob": "United Overseas Bank Ltd"}[
                lay
            ]
            c.setFont("Helvetica-Bold", 18)
            c.drawString(40, y, bank)
        y -= 20
        c.setFont("Helvetica", 10)
        c.drawString(40, y, "Statement of Account")
        y -= 16
        c.drawString(40, y, f"Account Number: {spec.account}")
        if pno == 1:
            y -= 14
            c.drawString(40, y, f"Account Name: {spec.holder}")
            y -= 14
            c.drawString(40, y, f"UEN: {spec.uen}")
            y -= 14
            c.drawString(
                40, y, f"Statement Period: {fmt_date(lay, spec.start)} to {fmt_date(lay, spec.end)}"
            )
            y -= 14
            c.drawString(40, y, f"Currency: {spec.currency}")
            y -= 14
            if lay != "dbs":
                c.drawString(40, y, f"Opening Balance: {money(spec.opening)}")
                y -= 14
        y -= 18
        for k, _x, _a in cols:
            put(k, spec.header_labels.get(k, k), y, "Helvetica-Bold")
        y -= 16
        if pno == 1 and lay == "dbs":
            put("Description", "Balance Brought Forward", y)
            if not spec.drop_balance_column:
                put("Balance", money(spec.opening), y)
            y -= 14
        for t in chunk:
            i = idx
            idx += 1
            bal = bal + (t.credit or 0) - (t.debit or 0)
            total_d += t.debit or 0
            total_c += t.credit or 0
            dbal = spec.balance_edits.get(i, neg(bal) if lay == "dbs" else money(bal))
            put("Date", spec.date_edits.get(i, fmt_date(lay, t.day)), y)
            if lay == "ocbc":
                put("Value Date", fmt_date(lay, t.day), y)
            lines = _wrap(t.desc, 30 if (lay == "ocbc" and spec.wrap_by_layout) else spec.wrap_at)
            dkey = "Transaction" if lay == "uob" else "Description"
            put(dkey, lines[0], y)
            amt = money(t.debit if t.debit is not None else t.credit)  # type: ignore[arg-type]
            amt = spec.amount_edits.get(i, amt)
            if lay == "uob":
                put("Amount", f"{amt} {'DR' if t.debit is not None else 'CR'}", y)
            elif lay == "dbs":
                put("Withdrawals" if t.debit is not None else "Deposits", amt, y)
            elif lay == "ocbc":
                put("Withdrawal" if t.debit is not None else "Deposit", amt, y)
            else:
                put("Debit" if t.debit is not None else "Credit", amt, y)
            if not spec.drop_balance_column:
                put("Balance", dbal, y)
            for extra in lines[1:]:
                y -= 10
                put(dkey, extra, y)
            y -= 14
        if pno == n_pages and spec.show_totals:
            if lay == "dbs":
                put("Description", "Total", y, "Helvetica-Bold")
                put("Withdrawals", money(total_d), y, "Helvetica-Bold")
                put("Deposits", money(total_c), y, "Helvetica-Bold")
                y -= 14
            if lay == "uob":
                c.setFont("Helvetica", 10)
                c.drawString(40, y - 10, f"Total Debits: {money(total_d)}")
                c.drawString(300, y - 10, f"Total Credits: {money(total_c)}")
                y -= 14
        if pno == n_pages:
            c.setFont("Helvetica", 10)
            c.drawString(40, y - 14, f"Closing Balance: {money(bal)}")
        # footer
        c.setFont("Helvetica", 8)
        marker = {
            "dbs": f"Page {pno} of {n_pages}",
            "ocbc": f"Page {pno}/{n_pages}",
            "uob": f"{n_pages} pages, this is page {pno}",
            "software": f"{pno}/{n_pages}",
        }[lay]
        c.drawString(40, 40, marker)
        if spec.footer_specimen:
            c.drawString(200, 28, "SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT")
        c.showPage()
    if lay == "ocbc":  # appended T&C boilerplate page
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, PAGE_H - 60, "Terms and Conditions")
        c.setFont("Helvetica", 9)
        for i in range(12):
            c.drawString(
                40,
                PAGE_H - 90 - 14 * i,
                f"{i + 1}. The Bank may amend these terms upon notice to the customer.",
            )
        c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------------------
# Labelled forms: invoice, purchase order, contract, financial statement
# --------------------------------------------------------------------------------------


@dataclass
class InvLine:
    desc: str
    qty: str
    price: str
    amount: str
    gst: str | None = None


def render_invoice(
    *,
    per_line_gst: bool = False,
    number: str = "INV-2026-0101",
    gst_label: str = "GST (9%)",
    with_gst_line: bool = True,
    lines: list[InvLine] | None = None,
    subtotal: str = "1,500.00",
    gst: str = "135.00",
    total: str = "1,635.00",
    status: str | None = "PAID",
    po: str | None = "PO-7788",
) -> bytes:
    lines = lines or [
        InvLine("Hex bolts M8 (box of 100)", "10", "50.00", "500.00", "45.00"),
        InvLine("Anchor sleeves M10", "20", "50.00", "1,000.00", "90.00"),
    ]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    y = PAGE_H - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "Meridian Fastener Supplies Pte. Ltd.")
    c.setFont("Helvetica", 10)
    y -= 16
    c.drawString(40, y, "UEN: 201912345R")
    y -= 14
    c.drawString(40, y, "GST Reg No: M21234567K")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(400, PAGE_H - 50, "TAX INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(400, PAGE_H - 66, "Currency: SGD")
    c.setFont("Helvetica", 10)
    y -= 26
    c.drawString(40, y, f"Invoice No: {number}")
    c.drawString(300, y, "Invoice Date: 05 Mar 2026")
    y -= 14
    c.drawString(300, y, "Due Date: 04 Apr 2026")
    if po:
        c.drawString(40, y, f"PO Number: {po}")
    y -= 24
    c.drawString(40, y, "Bill To")
    y -= 14
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "Orchid Bay Holdings Pte. Ltd.")
    c.setFont("Helvetica", 10)
    y -= 14
    c.drawString(40, y, "UEN: 202012345H")
    y -= 30
    hdr = [("Description", 40, "l"), ("Qty", 330, "r"), ("Unit Price", 410, "r")]
    if per_line_gst:
        hdr.append(("GST", 480, "r"))
    hdr.append(("Amount", 550, "r"))
    c.setFont("Helvetica-Bold", 10)
    for t, x, a in hdr:
        (c.drawRightString if a == "r" else c.drawString)(x, y, t)
    c.setFont("Helvetica", 10)
    y -= 16
    for ln in lines:
        c.drawString(40, y, ln.desc)
        c.drawRightString(330, y, ln.qty)
        c.drawRightString(410, y, ln.price)
        if per_line_gst:
            c.drawRightString(480, y, ln.gst or "")
        c.drawRightString(550, y, ln.amount)
        y -= 14
    y -= 14
    c.drawString(380, y, "Subtotal")
    c.drawRightString(550, y, subtotal)
    y -= 14
    if with_gst_line:
        c.drawString(380, y, gst_label)
        c.drawRightString(550, y, gst)
        y -= 14
    c.setFont("Helvetica-Bold", 10)
    c.drawString(380, y, "Total")
    c.drawRightString(550, y, total)
    c.setFont("Helvetica", 10)
    if status:
        y -= 30
        c.drawString(40, y, status)
        if status == "PAID":
            c.drawString(120, y, "Paid on 20 Mar 2026")
    c.setFont("Helvetica", 8)
    c.drawString(200, 28, "SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT")
    c.showPage()
    c.save()
    return buf.getvalue()


def render_po() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    y = PAGE_H - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "PURCHASE ORDER")
    c.setFont("Helvetica", 10)
    y -= 24
    for label, value in [
        ("PO Number:", "PO-7788"),
        ("Order Date:", "01 Mar 2026"),
        ("Delivery Date:", "15 Mar 2026"),
        ("Buyer:", "Orchid Bay Holdings Pte. Ltd."),
        ("Buyer UEN:", "202012345H"),
        ("Supplier:", "Meridian Fastener Supplies Pte. Ltd."),
        ("Supplier UEN:", "201912345R"),
        ("Contract Ref:", "CT-2026-01"),
        ("Currency:", "SGD"),
    ]:
        c.drawString(40, y, label)
        c.drawString(140, y, value)
        y -= 14
    y -= 16
    c.setFont("Helvetica-Bold", 10)
    for t, x, a in [
        ("Description", 40, "l"),
        ("Quantity", 330, "r"),
        ("Unit Price", 420, "r"),
        ("Amount", 550, "r"),
    ]:
        (c.drawRightString if a == "r" else c.drawString)(x, y, t)
    c.setFont("Helvetica", 10)
    y -= 16
    c.drawString(40, y, "Hex bolts M8 (box of 100)")
    c.drawRightString(330, y, "30")
    c.drawRightString(420, y, "50.00")
    c.drawRightString(550, y, "1,500.00")
    y -= 28
    c.drawString(380, y, "Subtotal")
    c.drawRightString(550, y, "1,500.00")
    y -= 14
    c.drawString(380, y, "GST (9%)")
    c.drawRightString(550, y, "135.00")
    y -= 14
    c.drawString(380, y, "Total")
    c.drawRightString(550, y, "1,635.00")
    c.showPage()
    c.save()
    return buf.getvalue()


def render_contract(*, labelled: bool = True) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    y = PAGE_H - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "SUPPLY AGREEMENT")
    c.setFont("Helvetica", 10)
    y -= 26
    if labelled:
        rows = [
            ("Contract No:", "CT-2026-01"),
            ("Agreement Date:", "01 Feb 2026"),
            ("Effective Date:", "01 Mar 2026"),
            ("Expiry Date:", "28 Feb 2027"),
            ("Seller:", "Meridian Fastener Supplies Pte. Ltd."),
            ("Seller UEN:", "201912345R"),
            ("Buyer:", "Orchid Bay Holdings Pte. Ltd."),
            ("Buyer UEN:", "202012345H"),
            ("Contract Value:", "S$ 240,000.00"),
            ("Payment Terms:", "Net 30 days from invoice date"),
        ]
        for label, value in rows:
            c.drawString(40, y, label)
            c.drawString(150, y, value)
            y -= 14
        y -= 20
        c.drawString(40, y, "Signed for and on behalf of Meridian Fastener Supplies Pte. Ltd.")
        y -= 14
        c.drawString(40, y, "Lim Hui Min")
        y -= 24
        c.drawString(40, y, "Signed for and on behalf of Orchid Bay Holdings Pte. Ltd.")
        y -= 14
        c.drawString(40, y, "Chen Wei Jie")
    else:
        for _ in range(6):
            c.drawString(
                40,
                y,
                "This Agreement is made between the parties on the date first written above and",
            )
            y -= 14
            c.drawString(
                40, y, "the Seller agrees to supply goods to the Buyer under the terms herein."
            )
            y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()


@dataclass
class FsRow:
    label: str
    cur: str | None = None
    prior: str | None = None
    heading: bool = False
    bold: bool = False


def render_fs(
    *,
    statements: dict[str, list[FsRow]],
    years: tuple[int, int] = (2025, 2024),
    scale_note: str | None = None,
    audited: bool = True,
    company: str = "Meridian Fastener Supplies Pte. Ltd.",
    uen: str = "201912345R",
    note_column: bool = False,
) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for title, rows in statements.items():
        y = PAGE_H - 50
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y, company)
        c.setFont("Helvetica", 10)
        y -= 14
        c.drawString(40, y, f"UEN: {uen}")
        y -= 20
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, title)
        c.setFont("Helvetica", 10)
        y -= 14
        c.drawString(40, y, f"For the financial year ended 31 December {years[0]}")
        y -= 14
        c.drawString(40, y, "Currency: SGD")
        y -= 14
        if scale_note:
            c.drawString(40, y, scale_note)
            y -= 14
        if not audited:
            c.drawString(40, y, "Management Accounts - Unaudited")
            y -= 14
        else:
            c.drawString(40, y, "Audited by Fictitious Assurance LLP")
            y -= 14
        y -= 10
        c.setFont("Helvetica-Bold", 10)
        if note_column:
            c.drawRightString(380, y, "Note")
        c.drawRightString(470, y, str(years[0]))
        c.drawRightString(550, y, str(years[1]))
        c.setFont("Helvetica", 10)
        y -= 16
        for r in rows:
            if r.heading:
                c.setFont("Helvetica-Bold", 10)
                c.drawString(40, y, r.label)
                c.setFont("Helvetica", 10)
            else:
                c.setFont("Helvetica-Bold" if r.bold else "Helvetica", 10)
                c.drawString(50, y, r.label)
                if note_column:
                    c.drawRightString(380, y, "5")
                if r.cur is not None:
                    c.drawRightString(470, y, r.cur)
                if r.prior is not None:
                    c.drawRightString(550, y, r.prior)
            y -= 14
        c.setFont("Helvetica", 8)
        c.drawString(200, 28, "SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT")
        c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------------------
# Model-facing fixtures
# --------------------------------------------------------------------------------------


def empty_page(doc_type: str) -> dict:
    """The model JSON for a page with nothing on it (every key present and null)."""
    from app.core.schemas import DOC_SCHEMAS

    schema = DOC_SCHEMAS[doc_type]
    out: dict = {
        "fields": {k: None for k in schema.fields},
        "tables": {t: [] for t in schema.tables},
    }
    if doc_type == "bank_statement":
        out["page_marker"] = None
    return out


def printed_rows(spec: StatementSpec) -> list[dict]:
    """Strings exactly as the renderer prints them for each transaction."""
    bal = spec.opening
    out = []
    for i, t in enumerate(spec.txns):
        bal = bal + (t.credit or 0) - (t.debit or 0)
        amt = spec.amount_edits.get(i, money(t.debit if t.debit is not None else t.credit))
        if spec.layout == "dbs":
            dbal = f"({money(-bal)})" if bal < 0 else money(bal)
        else:
            dbal = money(bal)
        dbal = spec.balance_edits.get(i, dbal)
        row = {
            "date": spec.date_edits.get(i, fmt_date(spec.layout, t.day)),
            "description": t.desc,
            "debit": None,
            "credit": None,
            "balance": None if spec.drop_balance_column else dbal,
        }
        if spec.layout == "uob":
            suffix = "DR" if t.debit is not None else "CR"
            row["debit" if t.debit is not None else "credit"] = f"{amt} {suffix}"
        else:
            row["debit" if t.debit is not None else "credit"] = amt
        out.append(row)
    return out


def bank_page_json(
    spec: StatementSpec, page: int, *, fields: bool = True, edits: dict | None = None
) -> dict:
    """What a perfect model would return for one page of the statement."""
    out = empty_page("bank_statement")
    rows = printed_rows(spec)
    per = spec.rows_per_page
    chunk = rows[(page - 1) * per : page * per]
    n_pages = max(1, -(-len(rows) // per))
    for j, r in enumerate(chunk):
        r = dict(r)
        for k, v in (edits or {}).get((page - 1) * per + j, {}).items():
            r[k] = v
        out["tables"]["transactions"].append(r)
    out["page_marker"] = {
        "dbs": f"Page {page} of {n_pages}",
        "ocbc": f"Page {page}/{n_pages}",
        "uob": f"{n_pages} pages, this is page {page}",
        "software": f"{page}/{n_pages}",
    }[spec.layout]
    out["fields"]["account_number"] = spec.account
    if page == 1 and fields:
        out["fields"].update(
            {
                "bank_name": {
                    "dbs": "DBS Bank Ltd",
                    "ocbc": "OCBC Bank",
                    "uob": "United Overseas Bank Ltd",
                    "software": f"{spec.holder} - Ledger Report",
                }[spec.layout],
                "account_holder": spec.holder,
                "uen": spec.uen,
                "currency": spec.currency,
                "period_start": fmt_date(spec.layout, spec.start),
                "period_end": fmt_date(spec.layout, spec.end),
                "opening_balance": money(spec.opening),
            }
        )
    if page == n_pages and fields:
        final = spec.opening + sum((t.credit or 0) - (t.debit or 0) for t in spec.txns)
        out["fields"]["closing_balance"] = money(final)
    return out


def page_of(req) -> int:  # noqa: ANN001
    """Page number from an LLMRequest.cache_extra ("<sha>:<page>:<mode>")."""
    return int(req.cache_extra.split(":")[1])


# --------------------------------------------------------------------------------------
# Free-form pages (generator-style layouts drawn line by line)
# --------------------------------------------------------------------------------------


def draw_pages(pages: list[list[tuple]]) -> bytes:
    """Each page is a list of ``(x_pt, y_from_top_pt, text[, align[, size[, bold]]])``."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for items in pages:
        for item in items:
            x, y, text = item[:3]
            align = item[3] if len(item) > 3 else "l"
            size = item[4] if len(item) > 4 else 9
            bold = item[5] if len(item) > 5 else False
            c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
            ypos = PAGE_H - y
            if align == "r":
                c.drawRightString(x, ypos, text)
            else:
                c.drawString(x, ypos, text)
        c.showPage()
    c.save()
    return buf.getvalue()
