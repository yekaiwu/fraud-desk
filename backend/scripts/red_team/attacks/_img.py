"""Raster helpers: render, JPEG, inpainting, re-noising, image-only PDFs."""

from __future__ import annotations

import io

import cv2
import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ._render import _FONT_PATHS


def render(pdf: bytes, page: int = 0, dpi: int = 150) -> Image.Image:
    doc = fitz.open(stream=pdf, filetype="pdf")
    pix = doc[page].get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def jpeg(im: Image.Image, q: int = 85, exif: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    kw = {"quality": q, "subsampling": 2}
    if exif:
        kw["exif"] = exif
    im.save(buf, "JPEG", **kw)
    return buf.getvalue()


def image_pdf(images: list[bytes], dpi: int = 150, producer: str | None = None) -> bytes:
    """An image-only PDF with one JPEG per page (no text layer)."""
    doc = fitz.open()
    for data in images:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        page = doc.new_page(width=w * 72 / dpi, height=h * 72 / dpi)
        page.insert_image(page.rect, stream=data)
    doc.set_metadata({"producer": producer or "", "creator": ""})
    return doc.tobytes()


def font_for(size_px: int, bold: bool = False) -> ImageFont.ImageFont:
    for fp in _FONT_PATHS:
        try:
            return ImageFont.truetype(fp, size_px, index=1 if (bold and fp.endswith(".ttc")) else 0)
        except OSError:
            continue
    return ImageFont.load_default()


def noise_sigma(gray: np.ndarray, box: tuple[int, int, int, int]) -> float:
    """High-pass residual noise level of the page outside ``box`` (a forger measuring the page)."""
    blur = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.2)
    res = gray.astype(np.float32) - blur
    m = np.ones(gray.shape, bool)
    x0, y0, x1, y1 = box
    m[y0:y1, x0:x1] = False
    paper = m & (gray > 235)
    return float(res[paper].std()) if paper.any() else 1.0


def inpaint_retype(
    im: Image.Image,
    box: tuple[int, int, int, int],
    text: str,
    size_px: int,
    bold: bool = False,
    renoise: bool = True,
    seed: int = 0,
) -> Image.Image:
    """Erase the glyphs in ``box`` with OpenCV inpainting, set ``text`` right-aligned in the same
    face, and add Gaussian noise whose sigma matches the rest of the paper."""
    arr = np.array(im)
    x0, y0, x1, y1 = box
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    mask = np.zeros(gray.shape, np.uint8)
    mask[y0:y1, x0:x1] = (gray[y0:y1, x0:x1] < 200).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    arr = cv2.inpaint(arr, mask, 3, cv2.INPAINT_TELEA)
    pil = Image.fromarray(arr)
    font = font_for(size_px, bold)
    d = ImageDraw.Draw(pil)
    w = d.textlength(text, font=font)
    d.text((x1 - w, y0 + (y1 - y0 - size_px) // 2 - 1), text, fill=(0, 0, 0), font=font)
    out = np.array(pil).astype(np.float32)
    if renoise:
        rng = np.random.default_rng(seed)
        sigma = noise_sigma(gray, box)
        patch = out[y0 - 2 : y1 + 2, x0 - 2 : x1 + 2]
        patch += rng.normal(0, sigma, patch.shape)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
