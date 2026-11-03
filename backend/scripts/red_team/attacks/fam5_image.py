"""Family 5: raster forgeries. Flattening to an image, screenshots, local edits with inpainting and
re-noising, matched-quality JPEG re-saves, grid-aligned pastes."""

from __future__ import annotations

import io
import random

import numpy as np
from PIL import Image

from ..core import Bundle, Outcome, attack
from ..pdfkit import PdfEdit, fmt_money
from . import _img
from ._render import box, changed_items
from ._util import invoice

F = "F5 raster and flattening"
INV = "invoice_INV-2025-0107.pdf"
DPI = 150


def _pair(b: Bundle):
    inv = invoice(b, INV)
    for k in range(len(inv.lines)):
        inv.set_qty(k, int(int(inv.lines[k][1].text) * 1.6))
    total = inv.recompute()[2]
    return PdfEdit(b.files[INV]), inv.edit, inv.dump(), total


def _swap(b: Bundle, newname: str, data: bytes) -> None:
    b.remove(INV)
    b.add(newname, data)


def _px(it, new_text, H_pt):
    x0, y0, x1, y1 = box(it, new_text, pad=1.0)
    s = DPI / 72
    return (int(x0 * s), int((H_pt - y1) * s), int(x1 * s) + 1, int((H_pt - y0) * s) + 1)


def _edit_raster(b: Bundle, *, q: int, renoise: bool, seed: int, grid_paste: bool = False):
    genuine, forged, forged_bytes, total = _pair(b)
    base = _img.render(b.files[INV], 0, DPI)
    H = float(genuine.pdf.pages[0].MediaBox[3])
    im = Image.open(io.BytesIO(_img.jpeg(base, q)))  # the 'original scan'
    for g, f in changed_items(genuine, forged):
        px = _px(g, f.text, H)
        im = _img.inpaint_retype(
            im,
            px,
            f.text,
            int(g.size * DPI / 72 * 1.0),
            bold="Bold" in g.base_font,
            renoise=renoise,
            seed=seed,
        )
    return _img.jpeg(im, q), total


def _control_jpeg(q: int):
    def c(b: Bundle, rng: random.Random) -> None:
        im = _img.render(b.files[INV], 0, DPI)
        _swap(b, "invoice_INV-2025-0107.jpg", _img.jpeg(im, q))

    c.__name__ = f"control_jpeg_q{q}"
    return c


def _control_flat(b: Bundle, rng: random.Random) -> None:
    im = _img.render(b.files[INV], 0, DPI)
    b.replace(INV, _img.image_pdf([_img.jpeg(im, 85)], DPI))


def _control_png(b: Bundle, rng: random.Random) -> None:
    buf = io.BytesIO()
    _img.render(b.files[INV], 0, DPI).save(buf, "PNG")
    _swap(b, "invoice_INV-2025-0107.png", buf.getvalue())


@attack(
    "R40",
    F,
    "Forged invoice flattened to an image-only PDF (no text layer, producer empty)",
    control=_control_flat,
    forensic_only=True,
)
def r40(b: Bundle, rng: random.Random) -> Outcome:
    _, _, forged_bytes, total = _pair(b)
    im = _img.render(forged_bytes, 0, DPI)
    b.replace(INV, _img.image_pdf([_img.jpeg(im, 85)], DPI))
    return Outcome(
        "Print/flatten the forged invoice to pictures: metadata, fonts and text layer all gone.",
        [INV],
        [fmt_money(total)],
    )


@attack(
    "R41",
    F,
    "Forged invoice as a screenshot-style PNG upload",
    control=_control_png,
    forensic_only=True,
)
def r41(b: Bundle, rng: random.Random) -> Outcome:
    _, _, forged_bytes, total = _pair(b)
    buf = io.BytesIO()
    _img.render(forged_bytes, 0, DPI).save(buf, "PNG")
    _swap(b, "invoice_INV-2025-0107.png", buf.getvalue())
    return Outcome(
        "A PNG screenshot of the forged invoice.", ["invoice_INV-2025-0107.png"], [fmt_money(total)]
    )


@attack(
    "R42",
    F,
    "JPEG scan edited with OpenCV inpainting + retyped digits + noise re-matched, re-saved at the same quality",
    control=_control_jpeg(90),
    forensic_only=True,
)
def r42(b: Bundle, rng: random.Random) -> Outcome:
    data, total = _edit_raster(b, q=90, renoise=True, seed=rng.randrange(1 << 30))
    _swap(b, "invoice_INV-2025-0107.jpg", data)
    return Outcome(
        "Erase the amounts, type new ones, add matching Gaussian noise, save at q90 like the original.",
        ["invoice_INV-2025-0107.jpg"],
        [fmt_money(total)],
    )


@attack(
    "R43",
    F,
    "As R42 without re-noising (the pixel checks' easiest case)",
    control=_control_jpeg(90),
    forensic_only=True,
)
def r43(b: Bundle, rng: random.Random) -> Outcome:
    data, total = _edit_raster(b, q=90, renoise=False, seed=1)
    _swap(b, "invoice_INV-2025-0107.jpg", data)
    return Outcome(
        "Same edit, no noise matching.", ["invoice_INV-2025-0107.jpg"], [fmt_money(total)]
    )


@attack(
    "R44",
    F,
    "JPEG scan edited then the whole page degraded again (blur 0.6, 0.97 rescale, q75): every block recompressed alike",
    control=_control_jpeg(75),
    forensic_only=True,
)
def r44(b: Bundle, rng: random.Random) -> Outcome:
    genuine, forged, forged_bytes, total = _pair(b)
    data, _ = _edit_raster(b, q=92, renoise=True, seed=rng.randrange(1 << 30))
    im = Image.open(io.BytesIO(data))
    import cv2

    arr = cv2.GaussianBlur(np.array(im), (0, 0), 0.6)
    h, w = arr.shape[:2]
    arr = cv2.resize(arr, (int(w * 0.97), int(h * 0.97)), interpolation=cv2.INTER_AREA)
    out = _img.jpeg(Image.fromarray(arr), 75)
    _swap(b, "invoice_INV-2025-0107.jpg", out)
    return Outcome(
        "Finish with a global blur, rescale and lower-quality save so no region keeps its own compression history.",
        ["invoice_INV-2025-0107.jpg"],
        [fmt_money(total)],
    )


@attack(
    "R45",
    F,
    "Statement page photographed-style: forged December statement rendered, rotated 0.8 degrees, JPEG q80, wrapped as a scanned PDF",
    forensic_only=True,
)
def r45(b: Bundle, rng: random.Random) -> Outcome:
    from . import fam4_meta

    touched = fam4_meta._forge_statements_neutral(b)
    n = touched[-1]
    import cv2

    pages = []
    import fitz

    for p in range(len(fitz.open(stream=b.files[n], filetype="pdf"))):
        arr = np.array(_img.render(b.files[n], p, DPI))
        h, w = arr.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), 0.8, 1.0)
        arr = cv2.warpAffine(arr, M, (w, h), borderValue=(255, 255, 255))
        pages.append(_img.jpeg(Image.fromarray(arr), 80))
    b.replace(n, _img.image_pdf(pages, DPI))
    return Outcome(
        "Only the final statement is replaced by a slightly rotated scan of the forged one.", [n]
    )
