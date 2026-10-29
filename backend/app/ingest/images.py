"""Shared Pillow helpers for image uploads (docs/ingest-and-extraction.md 4.4).

``Image.MAX_IMAGE_PIXELS`` is set at import. Pillow only warns between 1x and 2x the limit and
raises above 2x, so :func:`open_image` also enforces the limit itself.
"""

from __future__ import annotations

import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps

MAX_IMAGE_PIXELS = 50_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
DEFAULT_DPI = 150.0


class ImageProblem(Exception):
    """A whole-file image problem; ``code`` is a PageInfo.error code (``image_too_large`` ...)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def open_image(source: bytes | Path) -> Image.Image:
    """Open lazily (header only), enforce the pixel cap, raise :class:`ImageProblem` on failure."""
    limit = Image.MAX_IMAGE_PIXELS or MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(source) if isinstance(source, bytes) else source)
            width, height = img.size
            if width * height > limit:
                raise ImageProblem(
                    "image_too_large", f"{width}x{height} pixels exceeds the {limit} pixel limit"
                )
            img.load()
    except ImageProblem:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageProblem("image_too_large", str(exc)[:120]) from exc
    except Exception as exc:  # noqa: BLE001 - any decode failure is a local, per-file problem
        raise ImageProblem("image_decode_failed", str(exc).replace("\n", " ")[:120]) from exc
    return img


def upright(img: Image.Image) -> Image.Image:
    """Apply the EXIF orientation (the stored file is untouched)."""
    return ImageOps.exif_transpose(img) or img


def declared_dpi(img: Image.Image) -> float:
    """The image's declared dpi if it is in 36..1200, else 150."""
    raw = img.info.get("dpi")
    try:
        value = float(raw[0]) if isinstance(raw, tuple | list) else float(raw)
    except (TypeError, ValueError, IndexError):
        return DEFAULT_DPI
    return value if 36 <= value <= 1200 else DEFAULT_DPI


def frame_count(img: Image.Image) -> int:
    return int(getattr(img, "n_frames", 1) or 1)
