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
TESSERACT_DOUBT_CONF = 50.0  # a word below this confidence makes its text line doubtful
TESSERACT_DOUBT_MIN_HEIGHT = 8  # px; specks of noise are not worth a second look
TESSERACT_MAX_BANDS = 10  # second looks per page
TESSERACT_RECHECK_SCALE = 3
# sparse text: reads a header block set beside another block, which psm 4 clips
TESSERACT_SPARSE_PSM = 11
TESSERACT_SPARSE_MIN_CONF = 70.0
TESSERACT_NOISE_CONF = 30.0  # short letter runs below this confidence are dropped as noise
# the second reading of a doubtful band must beat the first by this much average confidence
TESSERACT_RECHECK_GAIN = 5.0
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
# the check letter of an ID number read as a digit: S3129044I -> "$31290441", M43070742O -> "M430707420"
_ID_CHECK = re.compile(r"^([STFGM$§])(\d{7}|\d{8})([\d!|])([,.;:)]*)$")
_ID_CHECK_MASKED = re.compile(r"^([STFGMS$§])(\*{3,}\d{3})([\d!|])([,.;:)]*)$")
_ID_LOWER_CHECK = re.compile(r"^([STFGM])(\d{7}|\d{8})([a-z])([,.;:)]*)$")  # "M32552516l"
_UEN_LOWER = re.compile(r"^(\(?\d{9})([a-z])(\)?[,.;:]*)$")  # "201741243k),"
_UEN_TEN_DIGITS = re.compile(r"^(\d{9})(\d)([,.;:]*)$")
_UEN_LABELS = {"UEN", "ID/UEN", "UEN/ID", "UEN/REG", "REG"}
# the I of an "SI-2026-0112" invoice number read as a digit, a bar or a t
_INVOICE_NO_I = re.compile(r"^S[1!|lt](-\d{4}-\d{3,5})([,.;:]?)$")
# thousands separators read as full stops: "167.289.02"
_AMOUNT_DOTS = re.compile(r"^(\d{1,3})((?:\.\d{3})+)\.(\d{2})([,.;:]?)$")
# a digit standing where a check letter belongs: only the shapes digits and letters really swap into
_DIGIT_AS_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "C", "8": "B", "!": "I", "|": "I"}
_SUFFIXES = ("PTE", "LTD")
_REAL_WORDS = {
    "PIE",
    "PET",
    "PEE",
    "ATE",
    "UTE",
    "LID",
}  # one letter from a suffix but words in their own right


def _core(token: str) -> tuple[str, str, str]:
    """(leading punctuation, the word, trailing punctuation)."""
    m = re.match(r"^([(\[\"']*)(.*?)([)\]\"',.;:!]*)$", token)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", token, "")


def _one_off(word: str, target: str) -> bool:
    return (
        len(word) == len(target)
        and sum(a != b for a, b in zip(word.upper(), target, strict=True)) == 1
    )


def _in_style(original: str, word: str) -> str:
    return word if original.isupper() or not original.isalpha() else word.title()


def repair_line_tokens(tokens: list[str]) -> list[str]:
    """Context repairs on the words of one line, for slips a single word cannot show.

    * the company suffix ``PTE LTD`` with one letter misread (``PTE LID``, ``ETE LTD``, ``PTELTD``);
    * a ten-digit number right after a UEN label (a UEN is nine digits and a letter).
    Words are only changed when exactly one character is off, and only next to the other half of the
    suffix pair or a UEN label, so ordinary words and account numbers are left alone."""
    out = list(tokens)
    for i in range(len(out)):
        pre, word, post = _core(out[i])
        if re.fullmatch(r"(?i)pte\.?ltd", word) and word.upper() not in ("PTE", "LTD"):
            joined = re.sub(r"(?i)^(pte)\.?(ltd)$", lambda m: f"{m.group(1)} {m.group(2)}", word)
            out[i] = pre + (joined.upper() if word.isupper() else joined) + post
        elif i + 1 < len(out):
            pre2, word2, post2 = _core(out[i + 1])
            a_ok, a_off = word.upper() == _SUFFIXES[0], _one_off(word, _SUFFIXES[0])
            b_ok, b_off = word2.upper() == _SUFFIXES[1], _one_off(word2, _SUFFIXES[1])
            if a_off and word.upper() in _REAL_WORDS and not b_off:
                a_off = (
                    False  # "BLUE PIE LTD": leave a real word alone unless the LTD is damaged too
                )
            if b_off and word2.upper() in _REAL_WORDS and not a_off and not a_ok:
                b_off = False
            if (a_ok or a_off) and (b_ok or b_off) and (a_off or b_off):
                out[i] = pre + _in_style(word, _SUFFIXES[0]) + post
                out[i + 1] = pre2 + _in_style(word2, _SUFFIXES[1]) + post2
        if i and out[i - 1].strip(":").upper() in _UEN_LABELS:
            m = _UEN_TEN_DIGITS.match(out[i])
            if m and m.group(2) in _DIGIT_AS_LETTER:
                out[i] = m.group(1) + _DIGIT_AS_LETTER[m.group(2)] + m.group(3)
    return out


_RULE_MARKS = re.compile(r"^[\u2014\u2013\-_=~|.\u00b7*]{3,}$")


def _is_noise(text: str, conf: float) -> bool:
    """A row of rule marks, or a short run of letters Tesseract itself hardly believes (``ee``, ``Ee``,
    ``ete`` read from a smudge or a table border). Digits and anything it is fairly sure of stay."""
    return bool(_RULE_MARKS.match(text)) or (
        conf < TESSERACT_NOISE_CONF and len(text) <= 3 and text.isalpha()
    )


def clean_tesseract_token(text: str) -> str:
    """Repair habitual Tesseract slips one word at a time: a stray punctuation speck after an amount, a
    CR/DR mark glued to the amount, thousands separators read as full stops, and ID numbers (the S read
    as ``$`` or ``§``, the check letter read as a digit or in lower case). A digit inside an amount is
    never changed."""
    if _ID_SYMBOL.match(text):
        return "S" + text[1:]
    for pattern in (_ID_CHECK, _ID_CHECK_MASKED):
        m = pattern.match(text)
        if m and m.group(3) in _DIGIT_AS_LETTER:
            head = "S" if m.group(1) in "$§" else m.group(1)
            return head + m.group(2) + _DIGIT_AS_LETTER[m.group(3)] + m.group(4)
    m = _INVOICE_NO_I.match(text)
    if m:
        return "SI" + m.group(1) + m.group(2)
    m = _ID_LOWER_CHECK.match(text)
    if m:
        return (
            m.group(1)
            + m.group(2)
            + ("I" if m.group(3) == "l" else m.group(3).upper())
            + m.group(4)
        )
    m = _UEN_LOWER.match(text)
    if m:
        return m.group(1) + m.group(2).upper() + m.group(3)
    m = _AMOUNT_DOTS.match(text)
    if m:
        return m.group(1) + m.group(2).replace(".", ",") + "." + m.group(3) + m.group(4)
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
        if not text or conf < 0 or w <= 0 or h <= 0 or _is_noise(text, conf):
            continue
        lines.setdefault((cols[2], cols[3], cols[4]), []).append((left, top, w, h, text))
    out: list[OcrBox] = []
    for words in lines.values():
        words.sort(key=lambda w: w[0])
        fixed = repair_line_tokens([w[4] for w in words])
        words = [(*w[:4], t) for w, t in zip(words, fixed, strict=True)]
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

    def _tsv(self, png: bytes, psm: int) -> str:
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, the image goes in on stdin
                [self._path, "stdin", "stdout", "-l", self.language, "--psm", str(psm), "tsv"],
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
        return done.stdout.decode("utf-8", errors="replace")

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
            tsv = self._tsv(png, self.psm)
            tsv = self._complete_truncated_words(tsv, self._tsv(png, TESSERACT_SPARSE_PSM))
            tsv = self._reread_doubtful_bands(tsv, image)
        return tesseract_tsv_to_ocr(tsv, width, height)

    @staticmethod
    def _word_rows(tsv: str) -> list[tuple[int, int, int, int, float, list[str]]]:
        out = []
        for cols in (r.split("\t") for r in tsv.splitlines()[1:]):
            if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
                continue
            try:
                left, top, w, h = (int(cols[i]) for i in (6, 7, 8, 9))
                conf = float(cols[10])
            except ValueError:
                continue
            if conf >= 0 and w > 0 and h > 0:
                out.append((left, top, w, h, conf, cols))
        return out

    def _complete_truncated_words(self, tsv: str, sparse: str) -> str:
        """Table mode clips some words set in a header block (``INV-2025-0107`` read as ``-2025-010``,
        with high confidence, so the confidence check cannot see it). Sparse-text mode reads them whole.
        A page-pass word is replaced by the sparse-pass word it overlaps only when its text is a
        proper part of that word, or when it is a low-confidence read of a confident one, so a word
        the page pass read correctly is never swapped for a different reading."""
        main = self._word_rows(tsv)
        other = self._word_rows(sparse)
        if not main or not other:
            return tsv
        have = {}
        for w in main:
            have[w[5][11].strip()] = have.get(w[5][11].strip(), 0) + 1
        swapped: dict[int, list[str]] = {}
        dropped: set[int] = set()
        for o in other:
            text = o[5][11].strip()
            if have.get(text) or len(text) < 3 or o[4] < TESSERACT_SPARSE_MIN_CONF:
                continue
            margin = 0.3 * o[3]
            hits = [
                w
                for w in main
                if w[0] < o[0] + o[2] + margin
                and w[0] + w[2] > o[0] - margin
                and abs((w[1] + w[3] / 2) - (o[1] + o[3] / 2)) < 0.6 * max(w[3], o[3])
            ]
            if not hits:
                continue
            # low-confidence specks beside the word do not count; they go with the replacement
            strong = sorted(
                (h for h in hits if h[4] >= TESSERACT_DOUBT_CONF + 10), key=lambda h: h[0]
            )
            joined = "".join(h[5][11].strip() for h in strong)
            if strong and not (joined in text and joined != text):
                continue
            first = min(hits, key=lambda h: h[0])
            cols = list(first[5])
            cols[6:10] = [str(o[0]), str(o[1]), str(o[2]), str(o[3])]
            cols[10], cols[11] = str(o[4]), text
            swapped[id(first[5])] = cols
            dropped.update(id(h[5]) for h in hits if h is not first)
        if not swapped and not dropped:
            return tsv
        rows = ["\t".join(swapped.get(id(w[5]), w[5])) for w in main if id(w[5]) not in dropped]
        return "\n".join([tsv.splitlines()[0], *rows])

    def _reread_doubtful_bands(self, tsv: str, image: Any) -> str:
        """Second look at the places Tesseract itself doubts.

        Words with a low confidence (a number set in a different face, a line cut in two by the layout
        analysis) are read badly in the page pass. For each horizontal band holding such a word, the
        band is cropped, enlarged and read again as a block of text; the new words replace the band
        only when their average confidence is clearly higher. At most ``TESSERACT_MAX_BANDS`` bands
        per page, so a noisy page costs a bounded extra time."""
        from PIL import Image

        words = self._word_rows(tsv)
        doubtful = [
            w for w in words if w[4] < TESSERACT_DOUBT_CONF and w[3] >= TESSERACT_DOUBT_MIN_HEIGHT
        ]
        if not doubtful:
            return tsv
        bands: list[list[tuple[int, int, int, int, float, list[str]]]] = []
        used: set[int] = set()
        for d in sorted(doubtful, key=lambda w: w[1]):
            if id(d[5]) in used:
                continue
            mid = d[1] + d[3] / 2
            band = [w for w in words if w[1] - 0.25 * w[3] <= mid <= w[1] + 1.25 * w[3]]
            used.update(id(w[5]) for w in band)
            bands.append(band)
            if len(bands) >= TESSERACT_MAX_BANDS:
                break
        replaced: set[int] = set()
        extra: list[str] = []
        for n, band in enumerate(bands):
            x0 = max(0, min(w[0] for w in band) - 12)
            y0 = max(0, min(w[1] for w in band) - 12)
            x1 = min(image.width, max(w[0] + w[2] for w in band) + 12)
            y1 = min(image.height, max(w[1] + w[3] for w in band) + 12)
            crop = image.crop((x0, y0, x1, y1))
            crop = crop.resize(
                (crop.width * TESSERACT_RECHECK_SCALE, crop.height * TESSERACT_RECHECK_SCALE),
                Image.Resampling.LANCZOS,
            )
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            new_rows = self._word_rows(self._tsv(buf.getvalue(), 6))
            if not new_rows:
                continue
            old_mean = sum(w[4] for w in band) / len(band)
            new_mean = sum(w[4] for w in new_rows) / len(new_rows)
            if new_mean < old_mean + TESSERACT_RECHECK_GAIN:
                continue
            replaced.update(id(w[5]) for w in band)
            k = TESSERACT_RECHECK_SCALE
            for left, top, w, h, conf, cols in new_rows:
                extra.append(
                    "\t".join(
                        ["5", "1", f"r{n}-{cols[2]}", cols[3], cols[4], cols[5]]
                        + [
                            str(x0 + left // k),
                            str(y0 + top // k),
                            str(max(1, w // k)),
                            str(max(1, h // k)),
                        ]
                        + [str(conf), cols[11]]
                    )
                )
        if not replaced:
            return tsv
        kept = ["\t".join(w[5]) for w in words if id(w[5]) not in replaced]
        return "\n".join([tsv.splitlines()[0], *kept, *extra])


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
