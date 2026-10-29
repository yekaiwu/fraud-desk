"""PDF helpers for ingest (docs/ingest-and-extraction.md 4.3, 4.6)."""

from __future__ import annotations

import io
import unicodedata
from collections.abc import Sequence

import pikepdf
import pymupdf

from app.core.models import PageInfo, Word
from app.ingest.words import build_words

MIN_TEXT_WORDS = 10
MAX_GARBAGE_SHARE = 0.20
SCAN_IMAGE_COVERAGE = 0.85
META_VALUE_MAX = 500
_FITZ_META = {
    "producer": "Producer",
    "creator": "Creator",
    "creationDate": "CreationDate",
    "modDate": "ModDate",
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
}


class FileProblem(Exception):
    """A whole-file problem that becomes one placeholder PageInfo (``code: message``)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def short(message: object, limit: int = 120) -> str:
    return " ".join(str(message).split())[:limit]


def open_pdf(data: bytes) -> pymupdf.Document:
    """Open from memory. Owner-password-only files open; a user password gives ``encrypted_pdf``."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - any parser failure is a local problem
        raise FileProblem("corrupt_pdf", short(exc)) from exc
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise FileProblem("encrypted_pdf", "password required")
    if doc.page_count < 1:
        doc.close()
        raise FileProblem("corrupt_pdf", "the file has no pages")
    return doc


def read_pdf_meta(data: bytes, doc: pymupdf.Document) -> dict[str, str]:
    """The raw Info dictionary (keys without the slash, values truncated). pikepdf first, then the
    PyMuPDF metadata mapped to the same names."""
    meta: dict[str, str] = {}
    try:
        with pikepdf.open(io.BytesIO(data)) as pdf:
            for key, value in pdf.docinfo.items():
                name = str(key).lstrip("/")
                try:
                    meta[name] = str(value)[:META_VALUE_MAX]
                except Exception:  # noqa: BLE001, S112 - an undecodable value is skipped
                    continue
        return meta
    except Exception:  # noqa: BLE001 - fall back to PyMuPDF
        meta = {}
    try:
        for key, value in (doc.metadata or {}).items():
            if value and key in _FITZ_META:
                meta[_FITZ_META[key]] = str(value)[:META_VALUE_MAX]
    except Exception:  # noqa: BLE001
        return {}
    return meta


def load_page(doc: pymupdf.Document, index: int) -> pymupdf.Page:
    """Seam for tests: loading is where a damaged page raises."""
    return doc.load_page(index)


def page_size(doc: pymupdf.Document, index: int) -> tuple[float, float]:
    """(width_pt, height_pt) after rotation; (0, 0) when the page cannot be loaded."""
    try:
        rect = doc.load_page(index).rect
        return float(rect.width), float(rect.height)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def _garbage_share(words: Sequence[Word]) -> float:
    total = bad = 0
    for w in words:
        for ch in w.text:
            total += 1
            if ch == "�" or unicodedata.category(ch) == "Co":
                bad += 1
    return bad / total if total else 0.0


def has_text_layer(words: Sequence[Word]) -> bool:
    """At least 10 words and fewer than 20 % U+FFFD / private-use characters."""
    return len(words) >= MIN_TEXT_WORDS and _garbage_share(words) < MAX_GARBAGE_SHARE


def covers_page(page: pymupdf.Page) -> bool:
    """True when one image covers at least 85 % of the page area."""
    try:
        page_area = abs(page.rect.width * page.rect.height)
        if page_area <= 0:
            return False
        unrotated = page.rect * page.derotation_matrix
        unrotated.normalize()
        for info in page.get_image_info():
            box = pymupdf.Rect(info["bbox"]) & unrotated
            if not box.is_empty and abs(box.width * box.height) / page_area >= SCAN_IMAGE_COVERAGE:
                return True
    except Exception:  # noqa: BLE001 - treat as no big image
        return False
    return False


def analyse_page(doc: pymupdf.Document, index: int) -> tuple[PageInfo, list[Word]]:
    """Dimensions, words and the text-layer / scan flags of page ``index`` (0-based)."""
    page_no = index + 1
    page = load_page(doc, index)
    words = build_words(page, page_no)
    text_layer = has_text_layer(words)
    is_scan = (not text_layer) and covers_page(page)
    info = PageInfo(
        page=page_no,
        width_pt=float(page.rect.width),
        height_pt=float(page.rect.height),
        rotation=int(page.rotation),
        has_text_layer=text_layer,
        is_scan=is_scan,
        word_count=len(words),
    )
    return info, words
