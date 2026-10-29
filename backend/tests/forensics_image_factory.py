"""Fixture builders for the image-forensics tests (not collected: no ``test_`` prefix).

Everything is synthesised with Pillow, numpy, OpenCV, reportlab and PyMuPDF, independent of the
test-data generator. All data is fictitious.

The raster fixtures are tuned so that the three pixel checks have something to find:
* ``dense_photo``: many sharp shapes, so ORB finds keypoints inside a cloned patch;
* ``scan_like``: paper grey plus gaussian sensor noise, so ELA and residual-noise statistics exist;
* ``tamper_fresh_patch``: a heavily compressed page (JPEG q60) with a *fresh* patch pasted in and a
  final high-quality save (q95): the classic ELA situation (the patch keeps detail the page lost).
"""

from __future__ import annotations

import io
import random
from collections.abc import Sequence
from typing import Any

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.core.models import Classification, FileRef, PageInfo

SHA_PLACEHOLDER = "0" * 64  # make_ctx replaces it with the real digest

# --------------------------------------------------------------------------------------
# Raster content
# --------------------------------------------------------------------------------------


def statement_pdf_bytes(
    seed: int = 1, rows: int = 45, producer: str = "Mock Composer 9.1"
) -> bytes:
    """One A4 text page that looks like a bank statement (reportlab)."""
    rnd = random.Random(seed)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setProducer(producer)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 800, "Mock Bank Statement")
    c.setFont("Helvetica", 9)
    y, balance = 760, 10000.0
    for _ in range(rows):
        amount = rnd.randint(100, 9000) + rnd.randint(0, 99) / 100
        balance += amount
        c.drawString(50, y, f"{rnd.randint(1, 28):02d} Mar 2026")
        c.drawString(
            130,
            y,
            rnd.choice(["PAYMENT ACME", "TRANSFER IN", "GIRO SALARY", "FAST PAYMENT", "ATM"])
            + f" REF{rnd.randint(1000, 9999)}",
        )
        c.drawRightString(450, y, f"{amount:,.2f}")
        c.drawRightString(540, y, f"{balance:,.2f}")
        y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()


def render_pdf_page(pdf: bytes, dpi: int = 150, page: int = 0) -> Image.Image:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    pix = doc[page].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples).convert("RGB")


def dense_photo(
    w: int, h: int, seed: int, n: int = 300, lo: int = 8, hi: int = 40, blur: float = 0.0
) -> Image.Image:
    """Grey canvas covered with sharp random shapes: plenty of ORB corners everywhere."""
    rng = np.random.default_rng(seed)
    im = Image.new("RGB", (w, h), (150, 150, 150))
    draw = ImageDraw.Draw(im)
    for _ in range(n):
        x0, y0 = int(rng.integers(0, w - 20)), int(rng.integers(0, h - 20))
        x1, y1 = x0 + int(rng.integers(lo, hi)), y0 + int(rng.integers(lo, hi))
        g = int(rng.choice([20, 60, 100, 200, 235]))
        box = (x0, y0, x1, y1)
        if rng.random() < 0.5:
            draw.rectangle(box, fill=(g, g, g))
        else:
            draw.ellipse(box, fill=(g, g, g))
    return im.filter(ImageFilter.GaussianBlur(blur)) if blur else im


def scan_like(im: Image.Image, sigma: float = 5.0, seed: int = 3) -> Image.Image:
    """Paper grey instead of pure white (no clipping) plus gaussian sensor noise."""
    arr = np.asarray(im.convert("RGB")).astype(np.float32) * 0.85 + 25
    rng = np.random.default_rng(seed)
    arr = arr + rng.normal(0, sigma, arr.shape[:2])[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def jpeg_roundtrip(im: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def jpeg_bytes(im: Image.Image, quality: int = 85, **save_kw: Any) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality, **save_kw)
    return buf.getvalue()


def png_bytes(im: Image.Image, **save_kw: Any) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", **save_kw)
    return buf.getvalue()


def photo_page(sigma: float = 5.0) -> Image.Image:
    """1200 x 800 scan-like page of dense shapes (the clean original)."""
    return scan_like(dense_photo(1200, 800, 11, n=300), sigma=sigma)


CLONE_SRC = (60, 250, 400, 520)  # x0, y0, x1, y1 of the source patch in ``photo_page``
CLONE_DST = (460, 250)  # where the clone lands: 400 px to the right of the source


def tamper_fresh_patch(original: Image.Image, *, clone: bool = True) -> Image.Image:
    """Page compressed at q60, then a *fresh* (never compressed) patch pasted in, final save q95.

    With ``clone=True`` the patch is a copy of another part of the same page (copy-move); the
    fresh detail also lights up ELA and the residual-noise check on the clone.
    """
    page = jpeg_roundtrip(original, 60)
    patch = original.crop(CLONE_SRC) if clone else original.crop((700, 500, 1040, 770))
    page.paste(patch, CLONE_DST if clone else (60, 500))
    return jpeg_roundtrip(page, 95)


def clone_only(original: Image.Image) -> Image.Image:
    """Same patch cloned, no other trace (one save at q85): copy_move only."""
    page = original.copy()
    page.paste(original.crop(CLONE_SRC), CLONE_DST)
    return jpeg_roundtrip(page, 85)


def ela_patch_page() -> Image.Image:
    """Scan at q60 with a fresh 120 x 40 patch pasted in, final save q95 (ELA, test 20 shape)."""
    original = photo_page(sigma=7.0)
    page = jpeg_roundtrip(original, 60)
    page.paste(original.crop((300, 300, 420, 340)), (703, 603))
    return jpeg_roundtrip(page, 95)


# --------------------------------------------------------------------------------------
# Metadata fixtures
# --------------------------------------------------------------------------------------

XMP_TEMPLATE = (
    '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" {attrs}/>'
    '</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
)


def xmp_packet(**attrs: str) -> bytes:
    """``xmp_packet(**{"xmp:CreatorTool": "Canva"})`` -> bytes of a minimal XMP packet."""
    text = " ".join(f'{k}="{v}"' for k, v in attrs.items())
    return XMP_TEMPLATE.format(attrs=text).encode()


def small_page(size: tuple[int, int] = (400, 300)) -> Image.Image:
    return dense_photo(size[0], size[1], seed=2, n=40)


def jpeg_with_meta(
    im: Image.Image | None = None,
    *,
    software: str | None = None,
    description: str | None = None,
    user_comment: bytes | None = None,
    datetime_: str | None = None,
    original: str | None = None,
    digitized: str | None = None,
    xmp: bytes | None = None,
    trailing: bytes = b"",
) -> bytes:
    """A JPEG with the given EXIF tags / XMP packet (``trailing`` bytes follow the EOI marker)."""
    exif = Image.Exif()
    if software is not None:
        exif[305] = software
    if description is not None:
        exif[270] = description
    if datetime_ is not None:
        exif[306] = datetime_
    ifd = exif.get_ifd(0x8769)
    if original is not None:
        ifd[36867] = original
    if digitized is not None:
        ifd[36868] = digitized
    if user_comment is not None:
        ifd[37510] = b"ASCII\x00\x00\x00" + user_comment
    kw: dict[str, Any] = {"exif": exif}
    if xmp is not None:
        kw["xmp"] = xmp
    return jpeg_bytes(im or small_page(), 90, **kw) + trailing


def png_with_text(
    im: Image.Image | None = None, text: dict[str, str] | None = None, xmp: bytes | None = None
) -> bytes:
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    for key, value in (text or {}).items():
        info.add_text(key, value)
    if xmp is not None:
        info.add_itxt("XML:com.adobe.xmp", xmp.decode())
    return png_bytes(im or small_page(), pnginfo=info)


# --------------------------------------------------------------------------------------
# PDFs with pictures
# --------------------------------------------------------------------------------------


def image_pdf_bytes(images: Sequence[bytes], *, page_size: tuple[int, int] = (595, 842)) -> bytes:
    """One page per image, each filling the page. JPEG bytes stay a DCT stream (PyMuPDF keeps
    the original stream), PNG bytes become a lossless image."""
    doc = pymupdf.open()
    for data in images:
        page = doc.new_page(width=page_size[0], height=page_size[1])
        page.insert_image(page.rect, stream=data)
    out = doc.tobytes()
    doc.close()
    return out


def signature_png(seed: int = 1, size: tuple[int, int] = (160, 80)) -> bytes:
    """A fictitious scribble on a transparent background (an 'image of a signature')."""
    rnd = random.Random(seed)
    im = Image.new("RGBA", size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(im)
    x, y = 10, size[1] // 2
    pts = [(x, y)]
    for _ in range(14):
        x += rnd.randint(6, 14)
        y = max(8, min(size[1] - 8, y + rnd.randint(-22, 22)))
        pts.append((min(x, size[0] - 6), y))
    draw.line(pts, fill=(15, 15, 90, 255), width=3)
    return png_bytes(im)


def pdf_with_pictures(
    pictures: Sequence[tuple[bytes, tuple[float, float, float, float]]] = (),
    *,
    text: str = "Fictitious document",
    pages: int = 1,
    picture_page: int = 1,
) -> bytes:
    """A text PDF with images at ``(x, y, w, h)`` points on page ``picture_page`` (1-based)."""
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 80), f"{text} page {number}", fontsize=11)
        if number == picture_page:
            for data, (x, y, w, h) in pictures:
                page.insert_image(
                    pymupdf.Rect(x, y, x + w, y + h), stream=data, keep_proportion=False
                )
    out = doc.tobytes()
    doc.close()
    return out


LOWER_RIGHT = (380.0, 640.0, 120.0, 60.0)  # signature block, lower part of the page
HEADER_LOGO = (60.0, 30.0, 120.0, 60.0)  # letterhead logo, top of the page


# --------------------------------------------------------------------------------------
# File references and classification helpers
# --------------------------------------------------------------------------------------


def page_info(
    page: int = 1, *, scan: bool = True, text: bool = False, rotation: int = 0
) -> PageInfo:
    return PageInfo(
        page=page,
        width_pt=595,
        height_pt=842,
        rotation=rotation,
        has_text_layer=text,
        is_scan=scan,
    )


def file_ref(
    file_id: str,
    mime: str,
    *,
    pages: int = 1,
    scan: bool = True,
    name: str | None = None,
    pdf_meta: dict[str, str] | None = None,
) -> FileRef:
    ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}[mime]
    return FileRef(
        id=file_id,
        name=name or f"{file_id}.{ext}",
        sha256=SHA_PLACEHOLDER,
        size_bytes=0,
        mime=mime,  # type: ignore[arg-type]
        pages=[page_info(p, scan=scan) for p in range(1, pages + 1)],
        pdf_meta=pdf_meta or {},
    )


def classification(label: str, p: float = 0.93) -> Classification:
    return Classification(label=label, probability=p, classifier="test", version="1")


def noise_patch_png() -> bytes:
    """Lossless page whose patch has other sensor noise (sigma 9) than the rest (sigma 2):
    only ``noise_mismatch`` has anything to find (no JPEG, no cloned content)."""
    base = dense_photo(1200, 800, 11, n=300)
    page = scan_like(base, sigma=2.0, seed=3)
    noisy = scan_like(base, sigma=9.0, seed=4)
    page.paste(noisy.crop((500, 300, 820, 520)), (500, 300))
    return png_bytes(page)


# --------------------------------------------------------------------------------------
# Contexts
# --------------------------------------------------------------------------------------


def upload_ctx(
    data: bytes,
    mime: str = "image/jpeg",
    *,
    doc_type: str = "national_id",
    file_id: str = "f1",
    **kw: Any,
):  # noqa: ANN201
    """A case with one uploaded image and one document of ``doc_type`` on it."""
    from app.core.testing import make_ctx, make_doc

    ref = file_ref(file_id, mime)
    doc = make_doc(doc_type, id="d1", file_id=file_id)  # type: ignore[arg-type]
    return make_ctx([doc], files=[(ref, data)], **kw)


def scan_pdf_ctx(
    page_images: Sequence[bytes],
    *,
    doc_type: str = "bank_statement",
    scan: bool = True,
    page_size: tuple[int, int] = (595, 842),
    **kw: Any,
):  # noqa: ANN201
    """A case with one PDF that has one full-page image per page, covered by one document."""
    from app.core.testing import make_ctx, make_doc

    n = len(page_images)
    ref = file_ref("f1", "application/pdf", pages=n, scan=scan)
    doc = make_doc(doc_type, id="d1", file_id="f1", page_end=n)  # type: ignore[arg-type]
    pdf = image_pdf_bytes(page_images, page_size=page_size)
    return make_ctx([doc], files=[(ref, pdf)], **kw)
