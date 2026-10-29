"""Page rendering (docs/ingest-and-extraction.md 3.2, 4.8).

``render_page_png`` is the cached UI render (called lazily by the API). ``render_for_model`` is the
small grayscale image sent to a vision model (cost lever 6); same input gives the same bytes so the
response cache key is stable.
"""

from __future__ import annotations

import io
import os
import re
import uuid
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image
from pydantic import ConfigDict

from app.core.models import Strict
from app.ingest.images import declared_dpi, open_image, upright

MAX_MODEL_SIDE_PX = 2200
DARK_THRESHOLD = 245
CROP_PAD = 0.015
CROP_MAX_AREA = 0.90
UI_MIN_WIDTH, UI_MAX_WIDTH = 200, 2000
_SHA = re.compile(r"^[0-9a-f]{64}$")


class RenderedImage(Strict):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    png: bytes
    width_px: int
    height_px: int
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # (x, y, w, h) page fractions

    def to_page(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        """Image fractions -> page fractions (undoes the margin crop)."""
        cx, cy, cw, ch = self.crop
        return cx + x * cw, cy + y * ch, w * cw, h * ch


def _open_pdf(path: Path) -> pymupdf.Document:
    doc = pymupdf.open(path)
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise ValueError("encrypted_pdf: password required")
    return doc


def _check_page(page: int) -> None:
    if page < 1:
        raise ValueError("page numbers start at 1")


# ----------------------------------------------------------------------------- model render


def _find_crop(gray: Image.Image, crop_margins: bool) -> tuple[Image.Image, tuple[float, ...]]:
    full = (0.0, 0.0, 1.0, 1.0)
    if not crop_margins:
        return gray, full
    arr = np.asarray(gray)
    dark = arr < DARK_THRESHOLD
    if not dark.any():
        return gray, full
    rows = np.flatnonzero(dark.any(axis=1))
    cols = np.flatnonzero(dark.any(axis=0))
    height, width = arr.shape
    pad_x, pad_y = round(CROP_PAD * width), round(CROP_PAD * height)
    x0, x1 = max(0, int(cols[0]) - pad_x), min(width, int(cols[-1]) + 1 + pad_x)
    y0, y1 = max(0, int(rows[0]) - pad_y), min(height, int(rows[-1]) + 1 + pad_y)
    if (x1 - x0) * (y1 - y0) > CROP_MAX_AREA * width * height:
        return gray, full
    return gray.crop((x0, y0, x1, y1)), (
        x0 / width,
        y0 / height,
        (x1 - x0) / width,
        (y1 - y0) / height,
    )


def render_for_model(
    path: Path, mime: str, page: int, *, dpi: int, crop_margins: bool = True
) -> RenderedImage:
    """Grayscale PNG at ``dpi``, longest side at most 2200 px (never upscaled), blank margins
    cropped when that removes at least 10 % of the page."""
    _check_page(page)
    if mime == "application/pdf":
        doc = _open_pdf(path)
        try:
            pg = doc.load_page(page - 1)
            rect = pg.rect
            zoom = min(dpi / 72.0, MAX_MODEL_SIDE_PX / max(rect.width, rect.height))
            pix = pg.get_pixmap(
                matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False
            )
            gray = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        finally:
            doc.close()
    else:
        if page != 1:
            raise ValueError("an image has exactly one page")
        img = upright(open_image(path))
        # many scanners and phones write a bogus 72 dpi density: never trust a declared value
        # that would make the page larger than a sheet of A4 (11.7 in on its long side)
        native = max(declared_dpi(img), min(1200.0, max(img.size) / 11.7))
        scale = min(1.0, dpi / native, MAX_MODEL_SIDE_PX / max(img.size))
        gray = img.convert("L")
        if scale < 1.0:
            gray = gray.resize(
                (max(1, round(gray.width * scale)), max(1, round(gray.height * scale))),
                Image.Resampling.LANCZOS,
            )
    cropped, crop = _find_crop(gray, crop_margins)
    buf = io.BytesIO()
    cropped.save(buf, format="PNG", optimize=True)
    return RenderedImage(
        png=buf.getvalue(),
        width_px=cropped.width,
        height_px=cropped.height,
        crop=(crop[0], crop[1], crop[2], crop[3]),
    )


# ----------------------------------------------------------------------------- UI render


def _atomic_write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def render_page_png(
    path: Path,
    sha256: str,
    mime: str,
    page: int,
    *,
    width_px: int | None = None,
    renders_dir: Path,
    render_dpi: int | None = None,
) -> Path:
    """UI render cached at ``<renders_dir>/<sha256>/<page>_<width>.png`` (atomic write).

    ``width_px`` is clamped to 200..2000; ``None`` means ``GLDB_RENDER_DPI`` applied to the page's
    physical width. ``ValueError`` for a bad hash or page number (the API turns it into a 404/422).
    """
    if not _SHA.match(sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    _check_page(page)
    dpi = render_dpi
    if dpi is None:
        from app.core.config import get_settings  # noqa: PLC0415 - only when the caller gave none

        dpi = get_settings().render_dpi
    if mime == "application/pdf":
        doc = _open_pdf(path)
        try:
            pg = doc.load_page(page - 1)
            width_pt = float(pg.rect.width)
            width = _ui_width(width_px, width_pt, dpi)
            target = renders_dir / sha256 / f"{page}_{width}.png"
            if target.is_file():
                return target
            zoom = width / width_pt
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            data = pix.tobytes("png")
        finally:
            doc.close()
    else:
        if page != 1:
            raise ValueError("an image has exactly one page")
        img = upright(open_image(path))
        width_pt = img.width * 72.0 / declared_dpi(img)
        width = _ui_width(width_px, width_pt, dpi)
        target = renders_dir / sha256 / f"{page}_{width}.png"
        if target.is_file():
            return target
        height = max(1, round(img.height * width / img.width))
        buf = io.BytesIO()
        img.convert("RGB").resize((width, height), Image.Resampling.LANCZOS).save(buf, "PNG")
        data = buf.getvalue()
    _atomic_write(target, data)
    return target


def _ui_width(width_px: int | None, width_pt: float, dpi: int) -> int:
    if width_px is None:
        width_px = round(width_pt / 72.0 * dpi)
    return max(UI_MIN_WIDTH, min(UI_MAX_WIDTH, int(width_px)))
