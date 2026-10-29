"""Builders for ingest and classify tests (not collected). Tiny PDFs and images, no generator."""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pikepdf
from PIL import Image
from reportlab.lib.colors import white
from reportlab.pdfgen import canvas

from app.core.cases import sniff_mime
from app.core.config import Settings
from app.core.context import CaseContext
from app.core.models import Applicant, FileRef, PageInfo, Word
from app.core.testing import make_ctx

PAGE_W, PAGE_H = 595.0, 842.0  # A4 points

# A page is a list of lines; a line is a plain string or a dict(text=, x=, y=, size=, color=).
Line = str | dict[str, Any]


def lines_page(lines: Sequence[str], *, top: float = 800.0, step: float = 16.0) -> list[Line]:
    """Plain lines placed top-down at 11 pt."""
    return [{"text": t, "x": 50.0, "y": top - i * step, "size": 11.0} for i, t in enumerate(lines)]


def make_pdf(
    pages: Sequence[Sequence[Line]],
    *,
    producer: str | None = None,
    creator: str | None = None,
    rotate: dict[int, int] | None = None,
    covers: dict[int, tuple[float, float, float, float]] | None = None,
) -> bytes:
    """A PDF with text pages. ``rotate``: page index -> /Rotate. ``covers``: page index -> white
    rectangle (x, y, w, h) drawn over the text (covered text stays in the text layer)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    if producer is not None:
        c.setProducer(producer)
    if creator is not None:
        c.setCreator(creator)
    for index, page in enumerate(pages):
        for line in page:
            spec = {"text": line} if isinstance(line, str) else dict(line)
            c.setFont("Helvetica", spec.get("size", 11.0))
            c.setFillColor(spec.get("color", None) or (0, 0, 0))
            c.drawString(spec.get("x", 50.0), spec.get("y", 800.0), spec["text"])
        if covers and index in covers:
            x, y, w, h = covers[index]
            c.setFillColor(white)
            c.rect(x, y, w, h, stroke=0, fill=1)
        c.showPage()
    c.save()
    data = buf.getvalue()
    if rotate:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            for index, angle in rotate.items():
                pdf.pages[index].Rotate = angle
            out = io.BytesIO()
            pdf.save(out)
            data = out.getvalue()
    return data


def png_bytes(width: int = 200, height: int = 300, *, fill: int = 255, dark: bool = True) -> bytes:
    img = Image.new("RGB", (width, height), (fill, fill, fill))
    if dark:
        for x in range(20, width - 20):
            for y in range(40, 50):
                img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def jpeg_with_orientation(orientation: int, width: int = 120, height: int = 80) -> bytes:
    img = Image.new("RGB", (width, height), (240, 240, 240))
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def image_pdf(width: int = 400, height: int = 560, *, pages: int = 1) -> bytes:
    """A PDF whose pages are one full-page picture and no text (a scan)."""
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    reader = ImageReader(io.BytesIO(png_bytes(width, height)))
    for _ in range(pages):
        c.drawImage(reader, 0, 0, width=PAGE_W, height=PAGE_H)
        c.showPage()
    c.save()
    return buf.getvalue()


def searchable_scan_pdf(words: int = 15) -> bytes:
    """A full-page picture with an invisible text layer on top (a 'searchable scan')."""
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.drawImage(ImageReader(io.BytesIO(png_bytes(400, 560))), 0, 0, width=PAGE_W, height=PAGE_H)
    text = c.beginText(50, 700)
    text.setTextRenderMode(3)  # invisible
    text.textLine(" ".join(f"word{i}" for i in range(words)))
    c.drawText(text)
    c.showPage()
    c.save()
    return buf.getvalue()


def make_settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(
        _env_file=None, data_dir=tmp_path / "data", db_path=":memory:", llm_backend="fake", **kw
    )


def file_ref(file_id: str, data: bytes, name: str = "upload") -> FileRef:
    mime = sniff_mime(data) or "application/pdf"
    return FileRef(id=file_id, name=name, sha256="0" * 64, size_bytes=len(data), mime=mime)  # type: ignore[arg-type]


def ctx_with_files(
    tmp_path: Path, blobs: Sequence[bytes], *, llm: Any = None, **settings: Any
) -> CaseContext:
    """A CaseContext whose case holds the blobs as ``f1``, ``f2`` ... (stored content-addressed)."""
    refs = [(file_ref(f"f{i + 1}", data), data) for i, data in enumerate(blobs)]
    kw: dict[str, Any] = {"settings": make_settings(tmp_path, **settings), "files": refs}
    if llm is not None:
        kw["llm"] = llm
    return make_ctx(**kw)


# --------------------------------------------------------------------------- classify exemplars
# One hand-made page text per C1 label. Fictitious. Layout words follow test-data-and-eval.md 4.1.

STATEMENT_DBS = (
    "DBS Bank Ltd\nStatement of Account\nAccount Number 123-456-789\n"
    "Statement Period 01 Mar 2026 to 31 Mar 2026\n"
    "Date   Description   Withdrawals   Deposits   Balance\n"
    "Balance Brought Forward   4,900.00\n"
    "05 Mar 2026   FAST PAYMENT FROM ACME TRADING PTE LTD   500.00   5,400.00\n"
    "Total Withdrawals 0.00   Total Deposits 500.00\nClosing Balance 5,400.00\nPage 1 of 3"
)
STATEMENT_OCBC = (
    "OCBC Bank\nAccount Statement\nAccount No 501-123456-001\n"
    "Date   Value Date   Description   Cheque   Withdrawal   Deposit   Balance\n"
    "05/03/2026   05/03/2026   GIRO PAYMENT   120.00   4,780.00\nPage 1/3"
)
STATEMENT_UOB = (
    "United Overseas Bank\nStatement of Account\nAccount Number 301-998877-3\n"
    "Date   Transaction   Amount   Balance\n05-Mar-2026   SALARY   1,200.00 CR   6,100.00\n"
    "Closing Balance 6,100.00\n3 pages, this is page 1"
)
STATEMENT_SOFTWARE = (
    "Example Logistics Pte Ltd\nBank Statement\nAccount Number 123-456-789\n"
    "Date   Description   Debit   Credit   Balance\n2026-03-05   FAST PAYMENT   100.00   4,900.00\n"
    "Opening Balance 5,000.00\nClosing Balance 4,900.00\n1/3"
)
TABLE_ONLY_PAGE = (
    "12 Mar 2026   FAST PAYMENT TO ABC LOGISTICS PTE LTD   200.00   5,200.00\n"
    "15 Mar 2026   GIRO PAYMENT   50.00   5,150.00\nPage 2 of 3"
)

EXEMPLARS: dict[str, str] = {
    "bank_statement": STATEMENT_DBS,
    "invoice": (
        "TAX INVOICE\nInvoice Number INV-2026-0101\nInvoice Date 05 Mar 2026\n"
        "Bill To Orchid Bay Holdings Pte Ltd\nGST Reg No M21234567K\n"
        "Description   Qty   Unit Price   Amount\nFreight services 1 1,000.00 1,000.00\n"
        "Subtotal 1,000.00\nGST 90.00\nAmount Due 1,090.00"
    ),
    "financial_statement": (
        "Example Logistics Pte Ltd\nIncome Statement for the year ended 31 December 2025\n"
        "Revenue 1,000,000\nProfit before tax 100,000\nNet profit 80,000\n"
        "Balance Sheet\nTotal assets 500,000\nTotal liabilities 200,000\nTotal equity 300,000\n"
        "Statement of Cash Flows\nIndependent Auditor's Report"
    ),
    "contract": (
        "SERVICE AGREEMENT\nThis agreement is made between the Seller and the Buyer.\n"
        "Contract Number C-2026-001\nContract Value S$ 120,000.00\nGoverning law: Singapore\n"
        "Effective Date 01 Jan 2026\nSigned by the parties"
    ),
    "purchase_order": (
        "PURCHASE ORDER\nPO Number PO-2026-014\nOrder Date 01 Mar 2026\nShip To Warehouse 1\n"
        "Delivery Date 15 Mar 2026\nPlease deliver the items below"
    ),
    "company_profile": (
        "Business Profile\nBizFile\nEntity Type Local Company\nStatus Live\n"
        "Registered Address 1 Fictitious Road\nDate of Incorporation 12 Apr 2019\n"
        "Principal Activity Freight forwarding\nSSIC 52291\nOfficers\nShareholders"
    ),
    "certificate_of_incorporation": (
        "CERTIFICATE OF INCORPORATION\nI hereby certify that EXAMPLE LOGISTICS PTE LTD "
        "is this day incorporated under the Companies Act\nCertificate Number 12345\nUEN 201912345R"
    ),
    "ownership_chart": (
        "Ownership Structure\nExample Logistics Pte Ltd\nTan Wei Ming 60%\nLim Ah Beng 25%\n"
        "Kestrel Holdings Pte Ltd 15%\nUBO"
    ),
    "shareholder_register": (
        "REGISTER OF MEMBERS\nExample Logistics Pte Ltd\n"
        "Holder   No. of Shares   Share Class   Date Acquired\nTan Wei Ming 300,000 Ordinary shares\n"
        "Total shares 500,000"
    ),
    "source_of_funds_declaration": (
        "SOURCE OF FUNDS DECLARATION\nI hereby declare that the funds originate from sale proceeds.\n"
        "Declared amount S$ 50,000.00"
    ),
    "national_id": (
        "REPUBLIC OF SINGAPORE\nIDENTITY CARD\nS1234567D\nName TAN WEI MING\nRace CHINESE\n"
        "Date of birth 01-01-1980\nSex M"
    ),
    "passport": (
        "PASSPORT\nType P   Country code SGP\nSurname TAN\nGiven names WEI MING\n"
        "Date of expiry 01 JAN 2030\nPlace of birth SINGAPORE\n"
        "P<SGPTAN<<WEI<MING<<<<<<<<<<<<<<<<<<<<<<<<<<<\n"
        "K1234567<8SGP8001014M3001012<<<<<<<<<<<<<<04"
    ),
    "boilerplate": "Important Notice\nThis page is intentionally left blank\nPrivacy Policy",
    "other": "Minutes of the board meeting held on 3 March 2026 at the registered office.",
}


# --------------------------------------------------------------------------- classify contexts

PageSpec = str | tuple[str, str]  # text | ("error", "<code>: ...") | ("scan", text)


def words_for(text: str, page: int) -> list[Word]:
    """Words on a grid, one row per text line (enough for layout_text and page_marker)."""
    out: list[Word] = []
    for i, line in enumerate(text.split("\n")):
        for j, token in enumerate(line.split()):
            out.append(
                Word(
                    page=page,
                    text=token,
                    x=min(0.9, 0.05 + 0.03 * j),
                    y=min(0.95, 0.03 + 0.02 * i),
                    w=0.02,
                    h=0.012,
                    line=i,
                    block=0,
                )
            )
    return out


def text_file(file_id: str, pages: Sequence[PageSpec], pdf_meta: dict[str, str] | None = None):  # noqa: ANN201
    """(FileRef with PageInfo entries, {(file_id, page): words}) built without any PDF bytes."""
    infos: list[PageInfo] = []
    words: dict[tuple[str, int], list[Word]] = {}
    for number, spec in enumerate(pages, start=1):
        error = None
        scan = False
        text = spec if isinstance(spec, str) else spec[1]
        if isinstance(spec, tuple):
            if spec[0] == "error":
                error, text = spec[1], ""
            elif spec[0] == "scan":
                scan = True
        page_words = words_for(text, number) if text.strip() else []
        if page_words:
            words[(file_id, number)] = page_words
        infos.append(
            PageInfo(
                page=number,
                width_pt=595.0,
                height_pt=842.0,
                has_text_layer=len(page_words) >= 10,
                is_scan=scan,
                word_count=len(page_words),
                error=error,
            )
        )
    ref = FileRef(
        id=file_id,
        name=f"{file_id}.pdf",
        sha256="0" * 64,
        size_bytes=1,
        mime="application/pdf",
        pages=infos,
        pdf_meta=pdf_meta or {},
    )
    return ref, words


def text_ctx(
    tmp_path: Path,
    files: Sequence[Sequence[PageSpec]],
    *,
    llm: Any = None,
    applicant_name: str | None = None,
    pdf_meta: dict[str, str] | None = None,
    **settings: Any,
) -> CaseContext:
    """A CaseContext that already holds ingest output (pages and words) for ``files``."""
    refs: list[FileRef] = []
    page_words: dict[tuple[str, int], list[Word]] = {}
    for index, pages in enumerate(files, start=1):
        ref, words = text_file(f"f{index}", pages, pdf_meta)
        refs.append(ref)
        page_words.update(words)
    kw: dict[str, Any] = {
        "settings": make_settings(tmp_path, **settings),
        "files_extra": refs,
        "page_words": page_words,
    }
    if llm is not None:
        kw["llm"] = llm
    if applicant_name:
        kw["applicant"] = Applicant(name=applicant_name, source="upload")
    return make_ctx(**kw)
