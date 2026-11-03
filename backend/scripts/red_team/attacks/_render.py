"""Building blocks for 'what the human sees differs from the text layer' forgeries."""

from __future__ import annotations

import io
import zlib

import fitz
import pikepdf
from pikepdf import Dictionary, Name
from PIL import Image, ImageDraw, ImageFont

from ..pdfkit import Item, PdfEdit
from ._util import restore_docinfo

_FONT_PATHS = (
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def text_png(
    text: str, size_pt: float, bold: bool = False, scale: int = 8
) -> tuple[bytes, int, int]:
    """Render ``text`` in a Helvetica look-alike on white, return (png, w_px, h_px)."""
    font = None
    for fp in _FONT_PATHS:
        try:
            font = ImageFont.truetype(
                fp, int(size_pt * scale), index=1 if (bold and fp.endswith(".ttc")) else 0
            )
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    w = int(font.getlength(text)) + 2
    h = int(size_pt * scale * 1.25)
    im = Image.new("RGB", (w, h), "white")
    ImageDraw.Draw(im).text((0, int(size_pt * scale * 0.1)), text, fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), w, h


def box(
    it: Item, new_text: str | None = None, pad: float = 1.0
) -> tuple[float, float, float, float]:
    """PDF-space rectangle (x0, y0, x1, y1) covering the item's glyphs."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    w = (
        it.width
        if new_text is None
        else max(it.width, stringWidth(new_text, it.base_font, it.size))
    )
    x1 = it.right + pad
    x0 = x1 - w - 2 * pad
    return (x0, it.y - 0.24 * it.size - pad, x1, it.y + 0.80 * it.size + pad)


def changed_items(genuine: PdfEdit, forged: PdfEdit) -> list[tuple[Item, Item]]:
    out = []
    for p in range(genuine.n_pages):
        for g, f in zip(genuine.items(p), forged.items(p), strict=False):
            if g.text != f.text:
                out.append((g, f))
    return out


def _png_xobject(pdf: pikepdf.Pdf, png: bytes):
    im = Image.open(io.BytesIO(png)).convert("RGB")
    raw = zlib.compress(im.tobytes())
    st = pdf.make_stream(raw)
    st.Type, st.Subtype = Name.XObject, Name.Image
    st.Width, st.Height = im.width, im.height
    st.ColorSpace, st.BitsPerComponent = Name.DeviceRGB, 8
    st.Filter = Name.FlateDecode
    return st


def overlay_xobject(
    pdf: pikepdf.Pdf, page_no: int, rects: list[tuple], pngs: list[bytes], tag: str
) -> None:
    """Append, to the page, one Form XObject that paints white boxes and the forged text images."""
    page = pdf.pages[page_no]
    ops = []
    xres = Dictionary()
    for k, ((x0, y0, x1, y1), png) in enumerate(zip(rects, pngs, strict=False)):
        img = _png_xobject(pdf, png)
        nm = f"Im{tag}{k}"
        xres[Name("/" + nm)] = img
        ops.append(f"1 1 1 rg {x0:.3f} {y0:.3f} {x1 - x0:.3f} {y1 - y0:.3f} re f")
        ops.append(f"q {x1 - x0:.3f} 0 0 {y1 - y0:.3f} {x0:.3f} {y0:.3f} cm /{nm} Do Q")
    form = pdf.make_stream("\n".join(ops).encode("latin-1"))
    form.Type, form.Subtype = Name.XObject, Name.Form
    form.BBox = [0, 0, float(page.MediaBox[2]), float(page.MediaBox[3])]
    form.Resources = Dictionary(XObject=xres)
    res = page.Resources
    if "/XObject" not in res:
        res.XObject = Dictionary()
    res.XObject[Name("/OV" + tag)] = form
    new = page.Contents.read_bytes() + f"\nq /OV{tag} Do Q\n".encode()
    page.Contents = pdf.make_stream(new)


def finish(pdf: pikepdf.Pdf, original: bytes) -> bytes:
    out = io.BytesIO()
    pdf.save(out)
    return restore_docinfo(out.getvalue(), original)


def annot_overlay(data: bytes, changed: list[tuple[Item, Item]]) -> bytes:
    """FreeText annotations (opaque white, no border) over each changed amount."""
    doc = fitz.open(stream=data, filetype="pdf")
    for g, f in changed:
        page = doc[g.page]
        x0, y0, x1, y1 = box(g, f.text, pad=0.8)
        H = page.rect.height
        rect = fitz.Rect(x0, H - y1, x1, H - y0)
        a = page.add_freetext_annot(
            rect,
            f.text,
            fontsize=g.size,
            fontname="helv",
            text_color=(0, 0, 0),
            fill_color=(1, 1, 1),
            border_width=0,
            align=2,
        )
        a.update(fill_color=(1, 1, 1), text_color=(0, 0, 0), border_color=None)
    out = doc.tobytes(garbage=0, deflate=True)
    return restore_docinfo(out, data)
