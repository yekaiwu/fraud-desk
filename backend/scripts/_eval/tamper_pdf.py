"""PDF-edit tampers: a rendered digital PDF is edited in place with PyMuPDF / pikepdf and the
forensic traces stay (T30a white box and retyped figure, T30b other font and +1 pt, T31 bytes
appended after a signature placeholder, T35 extra fonts).

The Info dictionary is carried over unchanged, so the metadata checks see an honest producer and
only the content-level traces (text under a white box, a font outlier, appended bytes) remain.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf

from scripts._eval.tamper_core import (
    Ctx,
    Plan,
    TamperNotApplicable,
    TmpBundle,
    cell,
    credit_rows,
    row_target,
    table_cell,
    to_box,
    tx_rows,
)
from scripts._gen.schema import Target

D = Decimal


def normalise_pdf(data: bytes) -> bytes:
    """Rewrite with pikepdf and a deterministic trailer ``/ID`` so equal arguments give equal bytes
    (PyMuPDF writes a random ``/ID``). The Info dictionary and the page content are unchanged."""
    import io

    import pikepdf

    with pikepdf.open(io.BytesIO(data)) as pdf:
        out = io.BytesIO()
        pdf.save(out, deterministic_id=True, object_stream_mode=pikepdf.ObjectStreamMode.disable)
    return out.getvalue()


def pin_trailer_id(data: bytes) -> bytes:
    """Replace the ``/ID`` array of the final trailer with a value derived from the bytes before it
    (the bytes before the trailer, including every signed byte range, are untouched)."""
    import hashlib
    import re

    matches = list(re.finditer(rb"/ID\s*\[", data))
    if not matches:
        return data
    at = matches[-1].start()
    i = matches[-1].end()
    for _ in range(2):  # two strings: <hex> or (literal with escapes and nested parentheses)
        while data[i : i + 1].isspace():
            i += 1
        if data[i : i + 1] == b"<":
            i = data.index(b">", i) + 1
        elif data[i : i + 1] == b"(":
            depth = 0
            while True:
                ch = data[i : i + 1]
                if ch == b"\\":
                    i += 2
                    continue
                if ch == b"(":
                    depth += 1
                elif ch == b")":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
        else:
            return data
    while data[i : i + 1].isspace():
        i += 1
    if data[i : i + 1] != b"]":
        return data
    digest = hashlib.sha256(data[:at]).hexdigest()[:32].upper().encode()
    return data[:at] + b"/ID [<" + digest + b"><" + digest + b">]" + data[i + 1 :]


def _amount_cell(ctx: Ctx) -> tuple[dict[str, Any], dict[str, Any], str]:
    st = ctx.pick(ctx.docs("bank_statement"), "statement")
    rows = [
        r
        for r in credit_rows(st)
        if D(cell(r, "credit")) >= D("500") and r["cells"]["credit"].get("box")
    ]
    r = ctx.pick(rows, "credit row")
    return st, r, "credit"


def retype(
    pdf: bytes, box: dict[str, Any], new_text: str, *, font: str | None, size_delta: float
) -> tuple[bytes, str, float]:
    """White box over ``box`` and ``new_text`` typed over it (the old figure stays in the text
    layer under the box). Returns ``(pdf, font used, size used)``."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    meta = dict(doc.metadata or {})
    page = doc[box["page"] - 1]
    w, h = page.rect.width, page.rect.height
    rect = pymupdf.Rect(
        box["x"] * w, box["y"] * h, (box["x"] + box["w"]) * w, (box["y"] + box["h"]) * h
    )
    spans = [
        s
        for b in page.get_text("dict", clip=rect).get("blocks", [])
        for ln in b.get("lines", [])
        for s in ln.get("spans", [])
    ]
    if not spans:
        raise TamperNotApplicable("no text under the target box")
    sp = spans[0]
    size = float(sp["size"]) + size_delta
    base_font = "hebo" if "bold" in str(sp["font"]).lower() else "helv"
    fontname = font or base_font
    page.draw_rect(
        pymupdf.Rect(rect.x0 - 1.5, rect.y0 - 1, rect.x1 + 1.5, rect.y1 + 1),
        color=None,
        fill=(1, 1, 1),
        overlay=True,
    )
    width = pymupdf.get_text_length(new_text, fontname=fontname, fontsize=size)
    page.insert_text(
        (rect.x1 - width, sp["origin"][1]),
        new_text,
        fontname=fontname,
        fontsize=size,
        color=(0, 0, 0),
    )
    doc.set_metadata(meta)
    out = pin_trailer_id(normalise_pdf(doc.tobytes(deflate=True)))
    doc.close()
    return out, fontname, size


def _t30(ctx: Ctx, *, other_font: bool) -> Plan:
    st, r, col = _amount_cell(ctx)
    key, idx = st["key"], r["index"]
    old = D(cell(r, col))
    new = (old * D("1.10")).quantize(D("0.01"))
    box = r["cells"][col]["box"]
    path = next(d.digital_path for d in ctx.base.documents if d.key == key)
    raw_new = f"{new:,.2f}"

    def edit(tb: TmpBundle) -> None:
        data = (tb.root / path).read_bytes()
        out, _font, _size = retype(
            data,
            box,
            raw_new,
            font="tiro" if other_font else None,
            size_delta=1.0 if other_font else 0.0,
        )
        tb.replace_file(path, out)

    def locate(b: dict[str, Any], n: dict[str, Any]):  # noqa: ANN202
        t = row_target(n, key, idx)
        t = t.model_copy(update={"box": to_box(box)})
        return (
            t,
            {col: table_cell(b, key, "transactions", idx, col)},
            {col: f"{new} (printed {raw_new})"},
        )

    how = "Times and +1 pt" if other_font else "the same font and size"
    return Plan(
        edit=f"{key} row {idx}: white box over the {col} {old}, retyped as {new} in {how}",
        file_edit=edit,
        locate=locate,
    )


def t30a(ctx: Ctx) -> Plan:
    return _t30(ctx, other_font=False)


def t30b(ctx: Ctx) -> Plan:
    return _t30(ctx, other_font=True)


def _statement_path(ctx: Ctx) -> tuple[str, str, Any]:
    st = ctx.pick(ctx.docs("bank_statement"), "statement")
    key = st["key"]
    path = next(d.digital_path for d in ctx.base.documents if d.key == key)
    return key, path, st


def t31(ctx: Ctx) -> Plan:
    from scripts._gen.render_common import add_placeholder_signature, signature_byte_range

    key, path, _st = _statement_path(ctx)

    def edit(tb: TmpBundle) -> None:
        data = (tb.root / path).read_bytes()
        if signature_byte_range(data) is None:
            tz = timezone(timedelta(hours=8))
            signed = datetime(ctx.today.year, ctx.today.month, 1, 9, 0, 0, tzinfo=tz)
            data = add_placeholder_signature(
                data, signer="MOCK BANK SIGNING SERVICE", signed=signed
            )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "doc.pdf"
            p.write_bytes(data)
            doc = pymupdf.open(str(p))
            page = doc[0]
            page.insert_text(
                (60, page.rect.height - 60),
                "Amended after signing: see revised figures",
                fontsize=8,
                fontname="helv",
                color=(0, 0, 0),
            )
            doc.saveIncr()
            doc.close()
            out = pin_trailer_id(p.read_bytes())
        rng = signature_byte_range(out)
        if rng is None or rng[2] + rng[3] >= len(out):
            raise TamperNotApplicable("no bytes after the signature range")
        tb.replace_file(path, out)

    return Plan(
        edit=f"{key}: placeholder signature (ByteRange covers the file), then one overlay appended by an incremental update",
        file_edit=edit,
        locate=lambda b, n: (
            Target(doc_key=key),
            {"bytes_after_signature": "0"},
            {"bytes_after_signature": "> 0"},
        ),
    )


def t35(ctx: Ctx) -> Plan:
    key, path, _st = _statement_path(ctx)

    def edit(tb: TmpBundle) -> None:
        doc = pymupdf.open(stream=(tb.root / path).read_bytes(), filetype="pdf")
        meta = dict(doc.metadata or {})
        page = doc[0]
        fonts = ["cour", "tiro", "cobo", "tibo", "heit"]
        h = page.rect.height
        for i, fnt in enumerate(fonts):
            page.insert_text(
                (300 + 40 * (i % 2), h - 90 + 7 * i),
                f"Ref {1000 + i}",
                fontsize=6.5,
                fontname=fnt,
                color=(0.3, 0.3, 0.3),
            )
        doc.set_metadata(meta)
        tb.replace_file(path, pin_trailer_id(normalise_pdf(doc.tobytes(deflate=True))))
        doc.close()

    return Plan(
        edit=f"{key} page 1: five text spans added in five other fonts",
        file_edit=edit,
        locate=lambda b, n: (Target(doc_key=key), {"fonts_added": "0"}, {"fonts_added": "5"}),
    )


PDF_BUILDERS = {"T30a": t30a, "T30b": t30b, "T31": t31, "T35": t35}
_ = tx_rows
