"""Text-layer words with boxes (docs/ingest-and-extraction.md 4.5).

Boxes are fractions of the (rotated) page, origin top-left, clamped to [0, 1]. Hidden and covered
text is kept on purpose: forensics needs it to find ``pdf_text_vs_render``.
"""

from __future__ import annotations

from typing import Any

import pymupdf

from app.core.models import Word

MAX_WORDS_PER_PAGE = 20_000  # resource limit: a page with more than this is cut (reading order)


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _flush(
    chars: list[dict[str, Any]],
    *,
    page_no: int,
    block: int,
    line: int,
    font: str | None,
    size: float | None,
    matrix: pymupdf.Matrix,
    width: float,
    height: float,
    out: list[tuple[tuple[int, int, float], Word]],
) -> None:
    if not chars:
        return
    rect = pymupdf.Rect(chars[0]["bbox"])
    for ch in chars[1:]:
        rect |= pymupdf.Rect(ch["bbox"])
    rect = rect * matrix
    rect.normalize()
    x0, y0 = _clamp(rect.x0 / width), _clamp(rect.y0 / height)
    x1, y1 = _clamp(rect.x1 / width), _clamp(rect.y1 / height)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return  # zero area
    text = "".join(c["c"] for c in chars).replace("\x00", "")
    if not text.strip():
        return
    word = Word(
        page=page_no,
        text=text,
        x=x0,
        y=y0,
        w=x1 - x0,
        h=y1 - y0,
        font=font,
        size=size,
        line=line,
        block=block,
    )
    out.append(((block, line, x0), word))


def build_words(page: pymupdf.Page, page_no: int) -> list[Word]:
    """Words of one PDF page in reading order ``(block, line, x)``."""
    rect = page.rect
    width, height = float(rect.width), float(rect.height)
    if width <= 0 or height <= 0:
        return []
    matrix = page.rotation_matrix
    raw = page.get_text("rawdict")
    found: list[tuple[tuple[int, int, float], Word]] = []
    for b_index, block in enumerate(raw.get("blocks", [])):
        if block.get("type", 0) != 0:
            continue  # image block
        for l_index, line in enumerate(block.get("lines", [])):
            for span in line.get("spans", []):
                font = span.get("font") or None
                size = round(float(span["size"]), 1) if span.get("size") is not None else None
                current: list[dict[str, Any]] = []
                for ch in span.get("chars", []):
                    if ch["c"].isspace():
                        _flush(
                            current,
                            page_no=page_no,
                            block=b_index,
                            line=l_index,
                            font=font,
                            size=size,
                            matrix=matrix,
                            width=width,
                            height=height,
                            out=found,
                        )
                        current = []
                    else:
                        current.append(ch)
                _flush(
                    current,
                    page_no=page_no,
                    block=b_index,
                    line=l_index,
                    font=font,
                    size=size,
                    matrix=matrix,
                    width=width,
                    height=height,
                    out=found,
                )
    found.sort(key=lambda item: item[0])
    return [w for _, w in found[:MAX_WORDS_PER_PAGE]]
