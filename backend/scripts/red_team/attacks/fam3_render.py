"""Family 3: the human sees one document, the text layer holds another (or nothing is left over)."""

from __future__ import annotations

import io
import random

import fitz
import pikepdf
from pikepdf import Dictionary, Name

from ..core import Bundle, Outcome, attack
from ..pdfkit import PdfEdit, fmt_money
from ._render import (
    annot_overlay,
    box,
    changed_items,
    finish,
    overlay_xobject,
    text_png,
)
from ._util import invoice, restore_docinfo, stmts

F = "F3 text layer versus what is seen"
INV = "invoice_INV-2025-0107.pdf"  # unpaid, no PO or contract reference


def _forged_invoice(b: Bundle, mult: float = 1.6):
    inv = invoice(b, INV)
    for k in range(len(inv.lines)):
        inv.set_qty(k, int(int(inv.lines[k][1].text) * mult))
    sub, gst, total = inv.recompute()
    genuine = PdfEdit(b.files[INV])
    return genuine, inv.edit, total


def _hybrid_xobject(b: Bundle, tag: str, *, invisible_text: bool, white: bool = True):
    genuine, forged, total = _forged_invoice(b)
    ch = changed_items(genuine, forged)
    if invisible_text:
        genuine.wrap(
            [g for g, _ in ch], [([3], pikepdf.Operator("Tr"))], [([0], pikepdf.Operator("Tr"))]
        )
    data = genuine.dump()
    pdf = pikepdf.open(io.BytesIO(data))
    rects, pngs = [], []
    for g, f in ch:
        rects.append(box(g, f.text))
        bold = "Bold" in g.base_font
        pngs.append(text_png(f.text, g.size, bold)[0])
    if not white:
        # no cover: the genuine text is invisible, the image alone is seen
        pass
    overlay_xobject(pdf, 0, rects, pngs, tag)
    out = finish(pdf, b.files[INV])
    return out, total, ch


@attack(
    "R20",
    F,
    "Forged amounts as opaque FreeText annotations over the genuine text (text layer untouched)",
)
def r20(b: Bundle, rng: random.Random) -> Outcome:
    genuine, forged, total = _forged_invoice(b)
    out = annot_overlay(b.files[INV], changed_items(genuine, forged))
    b.replace(INV, out)
    return Outcome(
        "An invoice 60% larger is what a reader sees; the stored text is still the original figures.",
        [INV],
        [],
        [(INV, fmt_money(total))],
    )


@attack(
    "R21",
    F,
    "Forged amounts as images in a Form XObject (white box + rendered digits) painted after the page",
)
def r21(b: Bundle, rng: random.Random) -> Outcome:
    out, total, _ = _hybrid_xobject(b, "A", invisible_text=False)
    b.replace(INV, out)
    return Outcome(
        "The visible invoice is 60% larger; digits are pictures inside an XObject, the genuine text stays underneath.",
        [INV],
        [],
        [(INV, fmt_money(total))],
    )


@attack(
    "R22",
    F,
    "Genuine amounts set to invisible text (render mode 3) and replaced by pictures of the forged amounts",
)
def r22(b: Bundle, rng: random.Random) -> Outcome:
    out, total, _ = _hybrid_xobject(b, "B", invisible_text=True, white=False)
    b.replace(INV, out)
    return Outcome(
        "As R21 but the genuine digits are not painted at all (Tr 3), so there is nothing underneath to detect by covering.",
        [INV],
        [],
        [(INV, fmt_money(total))],
    )


def _tounicode_font(pdf: pikepdf.Pdf, page, base_name: str, mapping: dict[int, str]):
    lines = [
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
        "/CMapName /Adobe-Identity-UCS def /CMapType 2 def",
        "1 begincodespacerange <00> <FF> endcodespacerange",
        f"{len(mapping)} beginbfchar",
    ]
    for code, ch in sorted(mapping.items()):
        lines.append(f"<{code:02X}> <{ord(ch):04X}>")
    lines += ["endbfchar", "endcmap CMapName currentdict /CMap defineresource pop end end"]
    tu = pdf.make_stream("\n".join(lines).encode())
    font = Dictionary(
        Type=Name.Font,
        Subtype=Name.Type1,
        BaseFont=Name("/" + base_name),
        Encoding=Name.WinAnsiEncoding,
        ToUnicode=tu,
    )
    return pdf.make_indirect(font)


@attack(
    "R23",
    F,
    "ToUnicode lie: December's closing balance shows 9x,xxx.xx, the text layer says 5x,xxx.xx",
)
def r23(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    s = ss.stmts[5]
    targets = [s.closing, s.rows[-1].bal]
    genuine_text = targets[0].text  # 55,704.74
    shown = ["9" + it.text[1:] for it in targets]
    mapping = {ord(c): c for c in "0123456789,."}
    mapping[ord("9")] = genuine_text[0]  # shown '9' is extracted as the genuine first digit
    pdf = s.edit.pdf
    for it, new in zip(targets, shown, strict=False):
        s.edit.set_text(it, new)
    font = _tounicode_font(pdf, None, "Helvetica", mapping)
    for p in {it.page for it in targets}:
        pdf.pages[p].Resources.Font[Name("/F9")] = font
    for it in sorted(targets, key=lambda i: (i.page, -i.op_index)):
        s.edit.wrap(
            [it],
            [([Name("/F9"), it.size], pikepdf.Operator("Tf"))],
            [([Name("/F1"), it.size], pikepdf.Operator("Tf"))],
        )
    b.replace(s.name, restore_docinfo(s.edit.dump(), b.files[s.name]))
    # what a reader sees: 95,704.74 ; what extraction gives:
    txt = "".join(p.get_text() for p in fitz.open(stream=b.files[s.name], filetype="pdf"))
    note = "extraction shows %s" % ("lie" if "95,704.74" not in txt else "forged text")
    return Outcome(
        "Glyphs say 95,704.74 but the font's ToUnicode map makes every extractor read 55,704.74.",
        [s.name],
        [],
        [(s.name, shown[0])],
        notes=note,
    )


@attack(
    "R24",
    F,
    "White box in the page stream plus retyped figure in the same font and size (calibration, known T30a)",
    sanity=True,
)
def r24(b: Bundle, rng: random.Random) -> Outcome:
    genuine, forged, total = _forged_invoice(b)
    ch = changed_items(genuine, forged)
    doc = fitz.open(stream=b.files[INV], filetype="pdf")
    page = doc[0]
    H = page.rect.height
    for g, f in ch:
        x0, y0, x1, y1 = box(g, f.text)
        page.draw_rect(fitz.Rect(x0, H - y1, x1, H - y0), color=None, fill=(1, 1, 1))
        page.insert_text(
            (x1 - 1 - fitz.get_text_length(f.text, "helv", g.size), H - g.y),
            f.text,
            fontname="helv",
            fontsize=g.size,
        )
    b.replace(INV, restore_docinfo(doc.tobytes(), b.files[INV]))
    return Outcome("The classic overlay, here only to show the harness registers a catch.", [INV])


@attack(
    "R25",
    F,
    "True redaction: genuine digits deleted from the file, forged digits inserted in Helvetica of the same size",
    consistent=True,
)
def r25(b: Bundle, rng: random.Random) -> Outcome:
    genuine, forged, total = _forged_invoice(b)
    ch = changed_items(genuine, forged)
    doc = fitz.open(stream=b.files[INV], filetype="pdf")
    page = doc[0]
    H = page.rect.height
    for g, _f in ch:
        x0, y0, x1, y1 = box(g, None, pad=0.2)
        page.add_redact_annot(fitz.Rect(x0, H - y1, x1, H - y0), fill=(1, 1, 1))
    page.apply_redactions()
    for g, f in ch:
        bold = "Bold" in g.base_font
        page.insert_text(
            (g.right - fitz.get_text_length(f.text, "hebo" if bold else "helv", g.size), H - g.y),
            f.text,
            fontname="hebo" if bold else "helv",
            fontsize=g.size,
        )
    b.replace(INV, restore_docinfo(doc.tobytes(garbage=3, deflate=True), b.files[INV]))
    return Outcome(
        "Delete the old digits for real (redaction), type new ones in the same base-14 font, restore the info dictionary.",
        [INV],
        [fmt_money(total)],
    )
