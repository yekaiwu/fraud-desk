"""Shared rendering machinery: a recording PDF canvas, truth records, PDF finalisation,
the Q09 placeholder signature and printed-line extraction."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pikepdf
import pymupdf
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

A4_W, A4_H = 595.2756, 841.8898
FOOTER_TEXT = "SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT"
SGT = timezone(timedelta(hours=8))
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTHS_LONG = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip
CENT = Decimal("0.01")


def q2(d: Decimal | int | str) -> Decimal:
    return Decimal(d).quantize(CENT, rounding=ROUND_HALF_UP)


def money_str(d: Decimal | None) -> str | None:
    """Canonical money string (data-model section 2)."""
    return None if d is None else format(q2(d), "f")


def fmt_amount(d: Decimal, parens: bool = False) -> str:
    """Printed amount ``12,345.67``; negatives ``-12,345.67`` (or ``(12,345.67)``)."""
    s = f"{abs(q2(d)):,.2f}"
    if d < 0:
        return f"({s})" if parens else f"-{s}"
    return s


def fmt_date(d: date, style: str) -> str:
    if style == "dmy_mon":  # 05 Mar 2026
        return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}"
    if style == "dmy_slash":  # 05/03/2026
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    if style == "dmy_dash":  # 05-Mar-2026
        return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"
    if style == "iso":
        return d.isoformat()
    if style == "long":  # 5 March 2026
        return f"{d.day} {MONTHS_LONG[d.month - 1]} {d.year}"
    raise ValueError(style)


def creation_date_for(doc_date: date) -> datetime:
    """Honest PDFs: document date + 1 day 09:00:00 +08:00."""
    nxt = doc_date + timedelta(days=1)
    return datetime(nxt.year, nxt.month, nxt.day, 9, 0, 0, tzinfo=SGT)


def pdf_date(dt: datetime) -> str:
    off = dt.utcoffset() or timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    mins = abs(mins)
    return f"D:{dt:%Y%m%d%H%M%S}{sign}{mins // 60:02d}'{mins % 60:02d}'"


# ------------------------------------------------------------------------------- truth records


def frac_box(x: float, y: float, w: float, h: float, page: int) -> dict[str, Any]:
    return {
        "page": page,
        "x": round(max(0.0, x / A4_W), 5),
        "y": round(max(0.0, y / A4_H), 5),
        "w": round(min(w / A4_W, 1.0), 5),
        "h": round(min(h / A4_H, 1.0), 5),
    }


@dataclass
class DocTruth:
    """What is printed on one document (ground_truth.documents[*])."""

    key: str
    doc_type: str
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)  # e.g. signature boxes (not schema fields)

    def set_field(
        self,
        name: str,
        value: str | None,
        raw: str | None = None,
        page: int = 1,
        box: dict[str, Any] | None = None,
    ) -> None:
        self.fields[name] = {"value": value, "raw": raw, "page": page, "box": box}

    def table(self, name: str, columns: list[str]) -> dict[str, Any]:
        return self.tables.setdefault(name, {"columns": columns, "rows": []})

    def add_row(
        self,
        table: str,
        cells: dict[str, tuple[str | None, str | None, dict[str, Any] | None]],
        page: int,
        box: dict[str, Any] | None = None,
    ) -> int:
        t = self.tables[table]
        idx = len(t["rows"])
        t["rows"].append(
            {
                "index": idx,
                "page": page,
                "box": box,
                "cells": {
                    col: {"value": v, "raw": r, "box": b} for col, (v, r, b) in cells.items()
                },
            }
        )
        return idx


@dataclass
class PageLabel:
    c1: str
    doc_key: str | None
    layout: str | None = None


@dataclass
class RenderedDoc:
    key: str
    doc_type: str
    stem: str
    pdf: bytes
    page_labels: list[PageLabel]
    truth: DocTruth
    doc_date: date
    layout: str | None = None
    issuer: str | None = None  # C3 label for statements
    row_labels: list[dict[str, Any]] = field(default_factory=list)  # {row,c4,c5,counterparty}
    page_start: int = 1
    page_end: int = 1

    @property
    def page_count(self) -> int:
        return len(self.page_labels)


def union_box(boxes: list[dict[str, Any]]) -> dict[str, Any] | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    page = boxes[0]["page"]
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["w"] for b in boxes)
    y1 = max(b["y"] + b["h"] for b in boxes)
    return {
        "page": page,
        "x": round(x0, 5),
        "y": round(y0, 5),
        "w": round(min(x1 - x0, 1.0), 5),
        "h": round(min(y1 - y0, 1.0), 5),
    }


# ----------------------------------------------------------------------------------- canvas


class DocCanvas:
    """Top-origin reportlab canvas that returns fractional boxes for everything it draws."""

    def __init__(self) -> None:
        self.buf = io.BytesIO()
        self.c = rl_canvas.Canvas(self.buf, pagesize=(A4_W, A4_H), invariant=1, pageCompression=1)
        self.page = 1

    def new_page(self) -> None:
        self.c.showPage()
        self.page += 1

    def text(
        self,
        x: float,
        y: float,
        s: str,
        *,
        size: float = 9,
        font: str = "Helvetica",
        align: str = "l",
        color: tuple[float, float, float] = (0, 0, 0),
    ) -> dict[str, Any]:
        """Draw ``s`` with its top edge at ``y`` (points from the top). ``x`` is the left, right
        or centre per ``align``."""
        w = stringWidth(s, font, size)
        left = x if align == "l" else (x - w if align == "r" else x - w / 2)
        self.c.setFillColorRGB(*color)
        self.c.setFont(font, size)
        self.c.drawString(left, A4_H - y - size * 0.82, s)
        self.c.setFillColorRGB(0, 0, 0)
        return frac_box(left, y, w, size * 1.2, self.page)

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        stroke: bool = True,
        fill: tuple[float, float, float] | None = None,
        width: float = 0.6,
    ) -> None:
        self.c.setLineWidth(width)
        if fill is not None:
            self.c.setFillColorRGB(*fill)
        self.c.rect(x, A4_H - y - h, w, h, stroke=1 if stroke else 0, fill=1 if fill else 0)
        self.c.setFillColorRGB(0, 0, 0)

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5) -> None:
        self.c.setLineWidth(width)
        self.c.line(x1, A4_H - y1, x2, A4_H - y2)

    def image(self, png: bytes, x: float, y: float, w: float, h: float) -> dict[str, Any]:
        self.c.drawImage(
            ImageReader(io.BytesIO(png)), x, A4_H - y - h, width=w, height=h, mask="auto"
        )
        return frac_box(x, y, w, h, self.page)

    def footer(
        self, marker: str | None = None, marker_x: float = A4_W - 40
    ) -> dict[str, Any] | None:
        self.text(A4_W / 2, A4_H - 28, FOOTER_TEXT, size=7, align="c", color=(0.35, 0.35, 0.35))
        if marker:
            return self.text(marker_x, A4_H - 44, marker, size=8, align="r")
        return None

    def finish(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


# ------------------------------------------------------------------------------ finalisation


def finalize_pdf(
    raw: bytes,
    *,
    producer: str,
    creator: str,
    title: str,
    doc_date: date,
    extra_info: dict[str, str] | None = None,
    creation: datetime | None = None,
    mod: datetime | None = None,
) -> bytes:
    """Rewrite the Info dict (Title, Creator, Producer, CreationDate, ModDate) and save with a
    deterministic /ID and a classic xref table so incremental updates can follow."""
    created = creation or creation_date_for(doc_date)
    modified = mod or created
    with pikepdf.open(io.BytesIO(raw)) as pdf:
        info = pikepdf.Dictionary()
        info["/Title"] = pikepdf.String(title)
        info["/Creator"] = pikepdf.String(creator)
        info["/Producer"] = pikepdf.String(producer)
        info["/CreationDate"] = pikepdf.String(pdf_date(created))
        info["/ModDate"] = pikepdf.String(pdf_date(modified))
        for k, v in (extra_info or {}).items():
            info["/" + k] = pikepdf.String(v)
        pdf.trailer["/Info"] = pdf.make_indirect(info)
        # drop any XMP the library may have produced
        if "/Metadata" in pdf.Root:
            del pdf.Root["/Metadata"]
        if "/ID" in pdf.trailer:  # a random /ID from another writer would defeat deterministic_id
            del pdf.trailer["/ID"]
        out = io.BytesIO()
        pdf.save(
            out,
            deterministic_id=True,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
            compress_streams=True,
        )
    return out.getvalue()


def _info_text(info: pikepdf.Dictionary) -> bytes:
    parts = []
    for k in sorted(info.keys()):
        v = str(info[k]).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        parts.append(f"{k} ({v})")
    return ("<< " + " ".join(parts) + " >>").encode("latin-1")


def _append_update(
    base: bytes, objs: dict[int, bytes], size: int, root: int, info: int | None
) -> bytes:
    """Append an incremental update (classic xref section) to ``base``."""
    import re

    m = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", base)
    if not m:
        raise ValueError("base PDF has no startxref")
    prev = int(m.group(1))
    out = bytearray(base)
    if not out.endswith(b"\n"):
        out += b"\n"
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n"
    nums = sorted(offsets)
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out += f"{nums[i]} {j - i + 1}\n".encode()
        for n in nums[i : j + 1]:
            out += f"{offsets[n]:010d} 00000 n \n".encode()
        i = j + 1
    trailer = f"trailer\n<< /Size {size} /Root {root} 0 R"
    if info is not None:
        trailer += f" /Info {info} 0 R"
    trailer += f" /Prev {prev} >>\nstartxref\n{xref_at}\n%%EOF\n"
    out += trailer.encode()
    return bytes(out)


def add_signature_field(pdf_bytes: bytes) -> tuple[bytes, int]:
    """Add an (empty) AcroForm signature field on page 1 with pikepdf; returns the new bytes
    and the field's object number."""
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        info_pdf = pdf.trailer.get("/Info")
        field_obj = pdf.make_indirect(
            pikepdf.Dictionary(
                FT=pikepdf.Name.Sig,
                T=pikepdf.String("Signature1"),
                Type=pikepdf.Name.Annot,
                Subtype=pikepdf.Name.Widget,
                Rect=pikepdf.Array([36, 24, 190, 52]),
                F=132,
            )
        )
        page = pdf.pages[0]
        field_obj["/P"] = page.obj
        annots = page.obj.get("/Annots")
        if annots is None:
            page.obj["/Annots"] = pikepdf.Array([field_obj])
        else:
            annots.append(field_obj)
        pdf.Root["/AcroForm"] = pikepdf.Dictionary(Fields=pikepdf.Array([field_obj]), SigFlags=3)
        out = io.BytesIO()
        pdf.save(
            out,
            deterministic_id=True,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
        )
        del info_pdf
        return out.getvalue(), field_obj.objgen[0]


def add_placeholder_signature(
    pdf_bytes: bytes, *, signer: str, signed: datetime, contents_hex_len: int = 1024
) -> bytes:
    """Q09: an honest incremental update (Info /Subject) followed by a signing update whose
    /ByteRange covers the whole file up to EOF. The /Contents is a zero placeholder; there is no
    cryptographic signature (docs gap 8.7). Result has three ``%%EOF`` markers."""
    base, field_num = add_signature_field(pdf_bytes)
    with pikepdf.open(io.BytesIO(base)) as pdf:
        size = int(pdf.trailer["/Size"])
        root = pdf.trailer["/Root"].objgen[0]
        info_ref = pdf.trailer["/Info"]
        info_num = info_ref.objgen[0]
        info = pikepdf.Dictionary({k: v for k, v in info_ref.items()})
        field_page = pdf.pages[0].obj.objgen[0]
    # update 1: rewrite Info with a /Subject (an ordinary incremental edit before signing)
    info["/Subject"] = pikepdf.String("Statement of account")
    upd1 = _append_update(base, {info_num: _info_text(info)}, size, root, info_num)
    # update 2: signature dict + field /V
    sig_num = size
    pad = "0" * contents_hex_len
    sig_body_tmpl = (
        "<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
        "/ByteRange [0 {a:010d} {b:010d} {c:010d}] /Contents <{pad}> "
        "/M ({m}) /Name ({name}) /Reason (Statement approved) >>"
    )
    field_body = (
        f"<< /FT /Sig /T (Signature1) /Type /Annot /Subtype /Widget /Rect [36 24 190 52] "
        f"/F 132 /P {field_page} 0 R /V {sig_num} 0 R >>"
    ).encode()
    m_text = pdf_date(signed)

    def build(a: int, b: int, c: int) -> bytes:
        body = sig_body_tmpl.format(a=a, b=b, c=c, pad=pad, m=m_text, name=signer).encode()
        return _append_update(
            upd1, {field_num: field_body, sig_num: body}, size + 1, root, info_num
        )

    first = build(0, 0, 0)
    marker = f"{sig_num} 0 obj\n".encode()
    sig_at = first.index(marker, len(upd1))
    contents_at = first.index(b"/Contents <", sig_at) + len(b"/Contents ")
    contents_end = contents_at + 1 + contents_hex_len + 1
    total = len(first)
    return build(contents_at, contents_end, total - contents_end)


def signature_byte_range(pdf_bytes: bytes) -> tuple[int, int, int, int] | None:
    import re

    m = re.search(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]", pdf_bytes)
    return tuple(int(g) for g in m.groups()) if m else None  # type: ignore[return-value]


# ---------------------------------------------------------------------------------- readback


def printed_lines(pdf_bytes: bytes) -> list[dict[str, Any]]:
    """Every printed text line of every page, with fractional boxes, read with PyMuPDF."""
    out: list[dict[str, Any]] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for pno, page in enumerate(doc, start=1):
            w, h = page.rect.width, page.rect.height
            lines = []
            for block in page.get_text("dict").get("blocks", []):
                for ln in block.get("lines", []):
                    text = "".join(sp["text"] for sp in ln["spans"]).strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = ln["bbox"]
                    lines.append(
                        {
                            "text": text,
                            "x": round(max(0.0, x0 / w), 4),
                            "y": round(max(0.0, y0 / h), 4),
                            "w": round(min((x1 - x0) / w, 1.0), 4),
                            "h": round(min((y1 - y0) / h, 1.0), 4),
                        }
                    )
            lines.sort(key=lambda d: (d["y"], d["x"]))
            out.append({"page": pno, "lines": lines})
    return out
