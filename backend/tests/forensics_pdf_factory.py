"""Fixture builders for the PDF forensics tests (not collected: no ``test_`` prefix).

Everything is built from scratch with reportlab, PyMuPDF and pikepdf (and hand-written PDF syntax
for signatures), independent of the mock-bundle generator. All data is fictitious.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pikepdf
import pymupdf
import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.core.models import Classification, Document, FileRef, PageInfo
from app.core.testing import FIXED_TODAY, make_ctx, make_doc

VERA_TTF = str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf")
_FONT_DIR = Path(reportlab.__file__).parent / "fonts"
# Extra families for font_mix tests: a TrueType (subset prefix) and an embedded Type 1 font.
pdfmetrics.registerFont(TTFont("Vera", VERA_TTF))
pdfmetrics.registerTypeFace(
    pdfmetrics.EmbeddedType1Face(
        str(_FONT_DIR / "DarkGardenMK.afm"), str(_FONT_DIR / "DarkGardenMK.pfb")
    )
)
pdfmetrics.registerFont(pdfmetrics.Font("DarkGarden", "DarkGardenMK", "WinAnsiEncoding"))
GOOD_PRODUCER = "Mock Composer 9.1"
CREATED = "D:20261001090000+08'00'"

# Right edges of the amount columns of the generated statement (A4 is 595 pt wide).
COL_WITHDRAWAL, COL_DEPOSIT, COL_BALANCE = 385.0, 465.0, 555.0


# --------------------------------------------------------------------------------------
# Clean statement
# --------------------------------------------------------------------------------------


def _money(v: float) -> str:
    return f"{v:,.2f}"


def statement_pdf(
    *,
    pages: int = 3,
    rows_per_page: int = 18,
    bold_first_row: bool = True,
    bold_total_row: bool = True,
    extra_lines: Sequence[tuple[int, float, float, str, str, float]] = (),
    hidden_lines: Sequence[tuple[int, float, float, str]] = (),
    id_text: str | None = None,
    producer: str | None = GOOD_PRODUCER,
    creator: str | None = "Mock Composer",
    created: str | None = CREATED,
    modified: str | None = CREATED,
) -> bytes:
    """A clean multi-page statement: Helvetica 9 body, Helvetica-Bold header, right-aligned
    amounts, a grey header band drawn BEFORE its text, a bold first row ("brought forward") on the
    first page of each statement section and a bold total row on the last page.

    ``extra_lines``: ``(page, x, y, text, fontname, size)`` appended after the table.
    ``hidden_lines``: ``(page, x, y, text)`` in text render mode 3 (invisible).
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    height = A4[1]
    balance = 10_000.00
    for p in range(1, pages + 1):
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, height - 40, f"Statement of Account (fictitious) page {p}")
        # header band first, then its (bold) text: an honest quirk, not a covered word
        c.setFillGray(0.85)
        c.rect(36, height - 82, 523, 14, stroke=0, fill=1)
        c.setFillGray(0)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(40, height - 78, "Date")
        c.drawString(100, height - 78, "Description")
        c.drawRightString(COL_WITHDRAWAL, height - 78, "Withdrawal")
        c.drawRightString(COL_DEPOSIT, height - 78, "Deposit")
        c.drawRightString(COL_BALANCE, height - 78, "Balance")
        y = height - 100
        for r in range(rows_per_page):
            first = r == 0 and bold_first_row
            last = p == pages and r == rows_per_page - 1 and bold_total_row
            c.setFont("Helvetica-Bold" if first or last else "Helvetica", 9)
            c.drawString(40, y, f"{1 + (r * 3) % 28:02d} Oct")
            desc = "Brought forward" if first else "Total" if last else f"Payment ref {p}{r:02d}"
            c.drawString(100, y, desc)
            amount = 100.0 + 37.5 * ((p * 31 + r * 17) % 40)
            if r % 3 == 0:
                balance += amount
                c.drawRightString(COL_DEPOSIT, y, _money(amount))
            else:
                balance -= amount
                c.drawRightString(COL_WITHDRAWAL, y, _money(amount))
            c.drawRightString(COL_BALANCE, y, _money(balance))
            y -= 16
        if id_text:
            c.setFont("Helvetica", 9)
            c.drawString(40, y - 10, f"Customer ID {id_text}")
        for page_no, x, ey, text, font, size in extra_lines:
            if page_no == p:
                c.setFont(font, size)
                c.drawString(x, ey, text)
        for page_no, x, hy, text in hidden_lines:
            if page_no == p:
                obj = c.beginText(x, hy)
                obj.setFont("Helvetica", 9)
                obj.setTextRenderMode(3)
                obj.textOut(text)
                c.drawText(obj)
        c.showPage()
    c.save()
    return set_meta(
        buf.getvalue(), Producer=producer, Creator=creator, CreationDate=created, ModDate=modified
    )


def set_meta(data: bytes, *, xmp: dict[str, str] | None = None, **info: str | None) -> bytes:
    """Rewrite the Info dictionary (``None`` removes a key) and optionally XMP fields."""
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for key in ("Producer", "Creator", "CreationDate", "ModDate", "Title", "Author"):
            if "/" + key in pdf.docinfo:
                del pdf.docinfo["/" + key]
        for key, value in info.items():
            if value is not None:
                pdf.docinfo["/" + key] = value
        if xmp:
            with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
                for key, value in xmp.items():
                    meta[key] = value
        out = io.BytesIO()
        pdf.save(out)
    return out.getvalue()


def pdf_meta_of(data: bytes) -> dict[str, str]:
    """What ingest would put in ``FileRef.pdf_meta`` (Info keys without the slash)."""
    with pikepdf.open(io.BytesIO(data)) as pdf:
        return {str(k).lstrip("/"): str(v) for k, v in pdf.docinfo.items()}


def page_infos(data: bytes, *, rotation_of: dict[int, int] | None = None) -> list[PageInfo]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        out = []
        for i, page in enumerate(doc, start=1):
            text = page.get_text().strip()
            out.append(
                PageInfo(
                    page=i,
                    width_pt=float(page.rect.width),
                    height_pt=float(page.rect.height),
                    rotation=(rotation_of or {}).get(i, 0),
                    has_text_layer=bool(text),
                    is_scan=False,
                    word_count=len(text.split()),
                )
            )
        return out
    finally:
        doc.close()


def file_ref(
    fid: str, data: bytes, *, name: str | None = None, pages: list[PageInfo] | None = None
) -> FileRef:
    return FileRef(
        id=fid,
        name=name or f"{fid}.pdf",
        sha256="0" * 64,  # make_ctx replaces it with the real hash
        size_bytes=len(data),
        mime="application/pdf",
        pages=pages if pages is not None else page_infos(data),
        pdf_meta=pdf_meta_of(data),
    )


def issuer(label: str = "dbs", p: float = 0.93) -> Classification:
    return Classification(label=label, probability=p, classifier="rules", version="1")


def statement_doc(
    doc_id: str = "d1",
    *,
    file_id: str = "f1",
    pages: int = 3,
    issuer_label: str | None = "dbs",
    issuer_p: float = 0.93,
) -> Document:
    return make_doc(
        "bank_statement",
        id=doc_id,
        file_id=file_id,
        page_start=1,
        page_end=pages,
        issuer=issuer(issuer_label, issuer_p) if issuer_label else None,
    )


def ctx_for(
    files: Sequence[tuple[str, bytes]],
    documents: Sequence[Document] | None = None,
    *,
    today: date = FIXED_TODAY,
    **kw,
):
    """A CaseContext whose files are on disk (``make_ctx(files=...)``)."""
    refs = [(file_ref(fid, data), data) for fid, data in files]
    if documents is not None:
        docs = list(documents)
    else:
        docs = [
            statement_doc(f"d{i}", file_id=fid, pages=len(ref.pages))
            for i, ((fid, _), (ref, _)) in enumerate(zip(files, refs, strict=True), start=1)
        ]
    return make_ctx(docs, files=refs, today=today, **kw)


# --------------------------------------------------------------------------------------
# Tampering (PyMuPDF, full rewrite so the result is a plain unsigned PDF)
# --------------------------------------------------------------------------------------


def _find_trace(page: pymupdf.Page, text: str):
    for t in page.get_texttrace():
        if "".join(chr(c[0]) for c in t["chars"]) == text:
            return t
    raise LookupError(text)


def white_box_retype(
    data: bytes,
    page_no: int,
    old: str,
    new: str,
    *,
    fontname: str = "helv",
    fontsize: float = 9.0,
    redact_instead: bool = False,
) -> bytes:
    """Cover ``old`` with an opaque white rectangle (or remove it with a redaction) and type
    ``new`` right-aligned on the same baseline."""
    doc = pymupdf.open(stream=data, filetype="pdf")
    page = doc[page_no - 1]
    trace = _find_trace(page, old)
    x1 = trace["bbox"][2]
    baseline = trace["chars"][0][2][1]
    rect = pymupdf.Rect(trace["bbox"]) + (-1.0, -1.0, 1.0, 1.0)
    if redact_instead:
        page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
    else:
        page.draw_rect(rect, color=None, fill=(1, 1, 1))
    width = pymupdf.get_text_length(new, fontname=fontname, fontsize=fontsize)
    page.insert_text((x1 - width, baseline), new, fontname=fontname, fontsize=fontsize)
    out = doc.tobytes()
    doc.close()
    return keep_meta(data, out)


def add_text(
    data: bytes,
    page_no: int,
    point: tuple[float, float],
    text: str,
    *,
    fontname: str = "helv",
    fontsize: float = 9.0,
    color: tuple[float, float, float] = (0, 0, 0),
    render_mode: int = 0,
    fontfile: str | None = None,
) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    doc[page_no - 1].insert_text(
        point,
        text,
        fontname=fontname,
        fontfile=fontfile,
        fontsize=fontsize,
        color=color,
        render_mode=render_mode,
    )
    out = doc.tobytes()
    doc.close()
    return keep_meta(data, out)


def keep_meta(original: bytes, rewritten: bytes) -> bytes:
    """PyMuPDF rewrites Producer/dates; put the original Info values back."""
    meta = pdf_meta_of(original)
    return set_meta(
        rewritten, **{k: meta.get(k) for k in ("Producer", "Creator", "CreationDate", "ModDate")}
    )


# --------------------------------------------------------------------------------------
# Signed PDFs written by hand (placeholder /Contents, correct /ByteRange)
# --------------------------------------------------------------------------------------

HEX_LEN = 128
SENTINEL = b"/ByteRange [0 0000000000 0000000000 0000000000]"
_STARTXREF = re.compile(rb"startxref\s+(\d+)\s+%%EOF\s*$")


def _obj(num: int, body: bytes) -> bytes:
    return f"{num} 0 obj\n".encode() + body + b"\nendobj\n"


def _stream(content: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"


def _xref(offsets: dict[int, int], *, with_free: bool = False) -> bytes:
    entries = dict(offsets)
    out = b"xref\n"
    nums = sorted(entries)
    if with_free:
        out += f"0 {len(nums) + 1}\n".encode() + b"0000000000 65535 f \n"
        for n in nums:
            out += f"{entries[n]:010d} 00000 n \n".encode()
        return out
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out += f"{nums[i]} {j - i + 1}\n".encode()
        for n in nums[i : j + 1]:
            out += f"{entries[n]:010d} 00000 n \n".encode()
        i = j + 1
    return out


def page_content(text: str) -> bytes:
    return _stream(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode())


def build_unsigned(text: str = "Balance 1,000.00") -> bytes:
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    bodies = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        4: page_content(text),
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    offsets = {}
    for num, body in bodies.items():
        offsets[num] = len(out)
        out += _obj(num, body)
    xref_pos = len(out)
    out += _xref(offsets, with_free=True)
    out += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def last_startxref(data: bytes) -> int:
    m = _STARTXREF.search(data)
    if not m:
        raise ValueError("no startxref at the end")
    return int(m.group(1))


def append_revision(base: bytes, objs: dict[int, bytes], *, size: int) -> bytes:
    """Append an incremental update: new/changed objects, xref, trailer with /Prev, startxref."""
    prev = last_startxref(base)
    out = bytearray(base)
    offsets = {}
    for num, body in sorted(objs.items()):
        offsets[num] = len(out)
        out += _obj(num, body)
    xref_pos = len(out)
    out += _xref(offsets)
    out += f"trailer\n<< /Size {size} /Root 1 0 R /Prev {prev} >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)


def add_signature(base: bytes, k: int, *, text_page_objs: bytes | None = None) -> bytes:
    """Append revision ``k`` (1-based) that adds a signature field + signature dictionary and
    patch its /ByteRange so it covers the whole file (including earlier signatures)."""
    widget, sig = 4 + 2 * k, 5 + 2 * k
    fields = " ".join(f"{4 + 2 * i} 0 R" for i in range(1, k + 1))
    objs = {
        1: (
            f"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [{fields}] /SigFlags 3 >> >>"
        ).encode(),
        3: (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            f"/Resources << /Font << /F1 5 0 R >> >> /Annots [{fields}] >>"
        ).encode(),
        widget: (
            f"<< /Type /Annot /Subtype /Widget /FT /Sig /T (Sig{k}) /Rect [0 0 0 0] /F 132 "
            f"/V {sig} 0 R /P 3 0 R >>"
        ).encode(),
        sig: (
            b"<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
            + SENTINEL
            + b" /Contents <"
            + b"0" * HEX_LEN
            + b"> /M (D:20261027090000Z) >>"
        ),
    }
    data = append_revision(base, objs, size=6 + 2 * k)
    return patch_byterange(data)


def patch_byterange(data: bytes) -> bytes:
    """Fill the LAST sentinel ByteRange so that it covers everything except the hex string."""
    at = data.rindex(SENTINEL)
    lt = data.index(b"<", data.index(b"/Contents", at))
    gt = data.index(b">", lt)
    b, c = lt, gt + 1
    d = len(data) - c
    patched = f"/ByteRange [0 {b:010d} {c:010d} {d:010d}]".encode()
    assert len(patched) == len(SENTINEL)
    return data[:at] + patched + data[at + len(SENTINEL) :]


def build_signed(signatures: int = 1, text: str = "Balance 1,000.00") -> bytes:
    data = build_unsigned(text)
    for k in range(1, signatures + 1):
        data = add_signature(data, k)
    return data


def tamper_after_signature(signed: bytes, new_text: str = "Balance 9,000.00") -> bytes:
    """Incremental update after the signature that changes the visible balance."""
    return append_revision(
        signed, {4: page_content(new_text)}, size=6 + 2 * len(re.findall(rb"/Type /Sig\b", signed))
    )


def set_byterange(data: bytes, which: int, values: tuple[int, int, int, int]) -> bytes:
    """Overwrite the ``which``-th (0-based, file order) /ByteRange with fixed-width numbers."""
    spans = [m.span() for m in re.finditer(rb"/ByteRange \[[0-9 ]+\]", data)]
    a, b = spans[which]
    new = f"/ByteRange [{values[0]} {values[1]:010d} {values[2]:010d} {values[3]:010d}]".encode()
    assert len(new) == b - a, (len(new), b - a)
    return data[:a] + new + data[b:]


def byteranges(data: bytes) -> list[tuple[int, int, int, int]]:
    return [
        tuple(int(g) for g in m.groups())  # type: ignore[misc]
        for m in re.finditer(rb"/ByteRange \[(\d+) (\d+) (\d+) (\d+)\]", data)
    ]
