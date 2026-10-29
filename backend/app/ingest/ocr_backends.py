"""Local OCR backends for scanned pages (docs/local-llm.md section 4).

A text-only model (the local Qwen server) cannot read an image, so scanned and photographed pages
need an OCR that runs on this machine and produces the same ``(text, box)`` lines the vision-model
OCR produces. ``ocr.py`` turns those lines into :class:`Word` objects, so everything downstream
(classification, extraction in text mode, verification against the page words, region highlights)
is unchanged.

* ``OcrBackend`` is the interface: ``recognise(png) -> list[OcrBox]`` with boxes as fractions of the
  image, origin top-left (the data-model convention).
* ``MacVisionOcr`` uses Apple's Vision framework through the optional ``ocrmac`` package
  (``uv sync --extra ocr``, macOS only). It is imported lazily: the base install never needs it.
* ``select_backend`` implements ``GLDB_OCR_BACKEND`` = ``auto | vision | none``.

"OCR unavailable" is a value (``OcrUnavailable`` with a short reason), never a crash: the ingest
stage records ``ocr_unavailable: <reason>`` on the page, which becomes an info flag and reduced
coverage like any other unreadable page.
"""

from __future__ import annotations

import importlib
import io
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.core.config import Settings

MIN_LOCAL_OCR_DPI = 200  # Vision reads small print better above the 150 dpi default
INSTALL_HINT = "install the 'ocr' extra on macOS: uv sync --extra ocr"


class OcrUnavailable(Exception):  # noqa: N818 - a state, not an error class
    """No OCR can read this page here. ``str(exc)`` is the short reason shown on the page."""


@dataclass(frozen=True)
class OcrBox:
    """One recognised text line; x, y = top-left corner, w, h = size, all fractions of the image."""

    text: str
    x: float
    y: float
    w: float
    h: float


class OcrBackend(Protocol):
    name: str

    def recognise(self, png: bytes) -> list[OcrBox]: ...


# ----------------------------------------------------------------------------- macOS Vision


def _import_ocrmac() -> Any:
    """The ``ocrmac.ocrmac`` module, or raise ``ImportError``. A seam for tests."""
    return importlib.import_module("ocrmac.ocrmac")


def vision_import_problem() -> str | None:
    """``None`` when macOS Vision OCR can run here, else the reason it cannot."""
    if sys.platform != "darwin":
        return f"macOS Vision OCR needs macOS (this is {sys.platform})"
    try:
        _import_ocrmac()
    except ImportError:
        return f"ocrmac is not installed ({INSTALL_HINT})"
    except Exception as exc:  # noqa: BLE001 - a broken pyobjc install must not crash ingest
        return f"ocrmac failed to load ({type(exc).__name__})"
    return None


def vision_boxes_to_ocr(observations: list[Any]) -> list[OcrBox]:
    """Convert ocrmac output ``[(text, confidence, (x, y, w, h)), ...]`` to :class:`OcrBox`.

    Vision reports boxes in a bottom-left origin; the data model uses top-left, so
    ``y_top = 1 - (y + h)``."""
    out: list[OcrBox] = []
    for item in observations:
        try:
            text, _confidence, (x, y, w, h) = item
        except (TypeError, ValueError):
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        out.append(
            OcrBox(text=text, x=float(x), y=1.0 - (float(y) + float(h)), w=float(w), h=float(h))
        )
    return out


class MacVisionOcr:
    name = "macos-vision"
    _lock = threading.Lock()  # one Vision request at a time: it already uses the whole ANE/GPU

    def __init__(self, recognition_level: str = "accurate"):
        problem = vision_import_problem()
        if problem is not None:
            raise OcrUnavailable(problem)
        self._ocrmac = _import_ocrmac()
        self.recognition_level = recognition_level

    def recognise(self, png: bytes) -> list[OcrBox]:
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            image.load()
            with self._lock:
                observations = self._ocrmac.OCR(
                    image, recognition_level=self.recognition_level
                ).recognize()
        return vision_boxes_to_ocr(list(observations))


# ----------------------------------------------------------------------------- selection


def select_backend(settings: Settings) -> OcrBackend | None:
    """The local OCR backend for ``GLDB_OCR_BACKEND``, or ``None`` when there is none.

    * ``vision``: macOS Vision; ``OcrUnavailable`` if it cannot run (an explicit request).
    * ``auto``: macOS Vision when ``ocrmac`` imports on darwin, else ``None``.
    * ``none``: ``None``.
    """
    choice = settings.ocr_backend
    if choice == "none":
        return None
    if choice == "vision":
        return MacVisionOcr()  # raises OcrUnavailable with the reason
    if vision_import_problem() is None:
        return MacVisionOcr()
    return None


def describe_unavailable(settings: Settings) -> str:
    """Why a text-only deployment has no OCR (shown on the page and in the docs)."""
    if settings.ocr_backend == "none":
        return "GLDB_OCR_BACKEND=none and the model is text-only"
    return f"the model is text-only and no local OCR is available ({vision_import_problem()})"
