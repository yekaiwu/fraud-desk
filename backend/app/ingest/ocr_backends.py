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
* ``TesseractOcr`` drives the ``tesseract`` command-line program (``brew install tesseract``; it is a
  system program, not a Python package, so nothing is added to ``uv sync``). It reads TSV output.
* ``RapidOcr`` runs PaddleOCR models through ONNX Runtime (``rapidocr-onnxruntime``, in the ``ocr``
  extra: ``uv sync --extra ocr``; the models ship inside the wheel, nothing is downloaded).
* ``select_backend`` implements ``GLDB_OCR_BACKEND`` = ``auto | vision | tesseract | rapidocr | none``.

"OCR unavailable" is a value (``OcrUnavailable`` with a short reason), never a crash: the ingest
stage records ``ocr_unavailable: <reason>`` on the page, which becomes an info flag and reduced
coverage like any other unreadable page.
"""

from __future__ import annotations

import importlib
import io
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.core.config import Settings

MIN_LOCAL_OCR_DPI = 200  # Vision reads small print better above the 150 dpi default
INSTALL_HINT = "install the 'ocr' extra on macOS: uv sync --extra ocr"
INSTALL_HINT_PORTABLE = "install the 'ocr' extra: uv sync --extra ocr"


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


# ----------------------------------------------------------------------------- Tesseract

TESSERACT_INSTALL_HINT = "install the program with: brew install tesseract"
TESSERACT_TIMEOUT_S = 90
TESSERACT_TARGET_SIDE_PX = 2600  # pages are upscaled towards this long side, never down
TESSERACT_MAX_UPSCALE = 2.0
_TESSERACT_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
)  # a launchd job has a short PATH


def find_tesseract() -> str | None:
    """Path of the ``tesseract`` program, or ``None``. A seam for tests."""
    found = shutil.which("tesseract")
    if found:
        return found
    for directory in _TESSERACT_DIRS:
        candidate = os.path.join(directory, "tesseract")
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def tesseract_import_problem() -> str | None:
    """``None`` when the Tesseract program can run here, else the reason it cannot."""
    if find_tesseract() is None:
        return f"tesseract is not installed ({TESSERACT_INSTALL_HINT})"
    return None


_AMOUNT_SPECK = re.compile(
    r"^(\d[\d,]*\.\d{2})[,.;:']$"
)  # "207,282.43," : a speck read after an amount
_AMOUNT_MARK = re.compile(r"^(\d[\d,]*\.\d{2})(CR|DR)$")  # "5.39CR" : the mark glued to the amount


# "$****854E": the S of an ID number read as a dollar or section sign
_ID_SYMBOL = re.compile(r"^[$§](\*{3,}\d{3}[A-Z]|\d{7}[A-Z])$")


def clean_tesseract_token(text: str) -> str:
    """Repair three habitual Tesseract slips: a stray punctuation speck after an amount, a CR/DR mark
    glued to the amount, and the S of a (masked) ID card number read as ``$`` or ``§``. Digits are never
    changed."""
    if _ID_SYMBOL.match(text):
        return "S" + text[1:]
    speck = _AMOUNT_SPECK.match(text)
    if speck:
        return speck.group(1)
    mark = _AMOUNT_MARK.match(text)
    return f"{mark.group(1)} {mark.group(2)}" if mark else text


# a gap wider than this many word heights starts a new cell (a table column)
TESSERACT_CELL_GAP = 1.5


def tesseract_tsv_to_ocr(tsv: str, width: int, height: int) -> list[OcrBox]:
    """Convert Tesseract TSV output to :class:`OcrBox` cells, left to right on each line.

    Rows are ``level page block par line word left top width height conf text``; only word rows
    (level 5) with a non-negative confidence and some text count. Words of one (block, paragraph,
    line) are joined with a space, and a gap wider than ``TESSERACT_CELL_GAP`` word heights starts a
    new box. The split matters: ``ingest/ocr.py`` spreads a box's tokens evenly across the box, so
    one box per table row would erase the column gaps that tell a debit from a credit. Pixel boxes
    become fractions of the image."""
    if width <= 0 or height <= 0:
        return []
    lines: dict[tuple[str, str, str], list[tuple[int, int, int, int, str]]] = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = clean_tesseract_token(cols[11].strip())
        try:
            left, top, w, h = (int(cols[i]) for i in (6, 7, 8, 9))
            conf = float(cols[10])
        except ValueError:
            continue
        if not text or conf < 0 or w <= 0 or h <= 0:
            continue
        lines.setdefault((cols[2], cols[3], cols[4]), []).append((left, top, w, h, text))
    out: list[OcrBox] = []
    for words in lines.values():
        words.sort(key=lambda w: w[0])
        limit = TESSERACT_CELL_GAP * sorted(w[3] for w in words)[len(words) // 2]
        cells: list[list[tuple[int, int, int, int, str]]] = [[words[0]]]
        for prev, cur in zip(words, words[1:], strict=False):
            if cur[0] - (prev[0] + prev[2]) > limit:
                cells.append([])
            cells[-1].append(cur)
        for cell in cells:
            x0 = min(w[0] for w in cell)
            y0 = min(w[1] for w in cell)
            x1 = max(w[0] + w[2] for w in cell)
            y1 = max(w[1] + w[3] for w in cell)
            out.append(
                OcrBox(
                    text=" ".join(w[4] for w in cell),
                    x=x0 / width,
                    y=y0 / height,
                    w=(x1 - x0) / width,
                    h=(y1 - y0) / height,
                )
            )
    out.sort(key=lambda b: (round(b.y, 3), b.x))
    return out


class TesseractOcr:
    name = "tesseract"

    def __init__(self, language: str = "eng", psm: int = 4):
        path = find_tesseract()
        if path is None:
            raise OcrUnavailable(tesseract_import_problem() or "tesseract is not installed")
        self._path = path
        self.language = language
        self.psm = psm  # 4: a single column of text of variable sizes (6 and 4 read alike; 4 was a hair better)

    def recognise(self, png: bytes) -> list[OcrBox]:
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            image.load()
            scale = min(TESSERACT_MAX_UPSCALE, max(1.0, TESSERACT_TARGET_SIDE_PX / max(image.size)))
            if (
                scale > 1.0
            ):  # small print reads better at about 300 dpi (measured: docs/ingest-and-extraction.md)
                image = image.resize(
                    (round(image.width * scale), round(image.height * scale)),
                    Image.Resampling.LANCZOS,
                )
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                png = buf.getvalue()
            width, height = image.size
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, the image goes in on stdin
                [self._path, "stdin", "stdout", "-l", self.language, "--psm", str(self.psm), "tsv"],
                input=png,
                capture_output=True,
                timeout=TESSERACT_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrUnavailable("tesseract timed out on this page") from exc
        except OSError as exc:
            raise OcrUnavailable(f"tesseract could not start ({type(exc).__name__})") from exc
        if done.returncode != 0:
            raise OcrUnavailable(f"tesseract failed (exit {done.returncode})")
        return tesseract_tsv_to_ocr(done.stdout.decode("utf-8", errors="replace"), width, height)


# ----------------------------------------------------------------------------- RapidOCR


def _import_rapidocr() -> Any:
    """The ``rapidocr_onnxruntime`` module, or raise ``ImportError``. A seam for tests."""
    return importlib.import_module("rapidocr_onnxruntime")


def rapidocr_import_problem() -> str | None:
    """``None`` when RapidOCR can run here, else the reason it cannot."""
    try:
        _import_rapidocr()
    except ImportError:
        return f"rapidocr-onnxruntime is not installed ({INSTALL_HINT_PORTABLE})"
    except Exception as exc:  # noqa: BLE001 - a broken onnxruntime install must not crash ingest
        return f"rapidocr failed to load ({type(exc).__name__})"
    return None


def rapidocr_results_to_ocr(results: Any, width: int, height: int) -> list[OcrBox]:
    """Convert RapidOCR output ``[[[x, y] * 4, text, score], ...]`` (pixels) to :class:`OcrBox`."""
    if width <= 0 or height <= 0 or not results:
        return []
    out: list[OcrBox] = []
    for item in results:
        try:
            points, text, _score = item
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
        except (TypeError, ValueError, IndexError):
            continue
        if not isinstance(text, str) or not text.strip() or not xs:
            continue
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        out.append(
            OcrBox(
                text=text.strip(),
                x=max(0.0, x0 / width),
                y=max(0.0, y0 / height),
                w=(x1 - x0) / width,
                h=(y1 - y0) / height,
            )
        )
    out.sort(key=lambda b: (round(b.y, 3), b.x))
    return out


class RapidOcr:
    name = "rapidocr"
    _lock = threading.Lock()  # one engine, shared; ONNX Runtime already uses the cores

    def __init__(self) -> None:
        problem = rapidocr_import_problem()
        if problem is not None:
            raise OcrUnavailable(problem)
        self._engine = _import_rapidocr().RapidOCR()

    def recognise(self, png: bytes) -> list[OcrBox]:
        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            image.load()
            rgb = np.asarray(image.convert("RGB"))[:, :, ::-1]  # RapidOCR expects BGR
        with self._lock:
            results, _elapsed = self._engine(rgb)
        height, width = rgb.shape[:2]
        return rapidocr_results_to_ocr(results, width, height)


# ----------------------------------------------------------------------------- selection


def select_backend(settings: Settings) -> OcrBackend | None:
    """The local OCR backend for ``GLDB_OCR_BACKEND``, or ``None`` when there is none.

    * ``vision``: macOS Vision; ``OcrUnavailable`` if it cannot run (an explicit request).
    * ``tesseract``: the Tesseract program; ``OcrUnavailable`` if it is not installed.
    * ``rapidocr``: RapidOCR; ``OcrUnavailable`` if ``rapidocr-onnxruntime`` is not installed.
    * ``auto``: macOS Vision when ``ocrmac`` imports on darwin, else ``None``.
    * ``none``: ``None``.
    """
    choice = settings.ocr_backend
    if choice == "none":
        return None
    if choice == "vision":
        return MacVisionOcr()  # raises OcrUnavailable with the reason
    if choice == "tesseract":
        return TesseractOcr()  # raises OcrUnavailable with the reason
    if choice == "rapidocr":
        return RapidOcr()  # raises OcrUnavailable with the reason
    if vision_import_problem() is None:
        return MacVisionOcr()
    return None


def describe_unavailable(settings: Settings) -> str:
    """Why a text-only deployment has no OCR (shown on the page and in the docs)."""
    if settings.ocr_backend == "none":
        return "GLDB_OCR_BACKEND=none and the model is text-only"
    if settings.ocr_backend == "tesseract":
        return (
            f"the model is text-only and Tesseract is not available ({tesseract_import_problem()})"
        )
    if settings.ocr_backend == "rapidocr":
        return f"the model is text-only and RapidOCR is not available ({rapidocr_import_problem()})"
    return f"the model is text-only and no local OCR is available ({vision_import_problem()})"
