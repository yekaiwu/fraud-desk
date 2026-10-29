"""Reading-order text from words and page markers (ingest-and-extraction.md 3.2, classification.md 4.4).

Shared by ingest, classify and extract, so it lives in core (no wave-1 agent imports another).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from statistics import median

from app.core.models import Word

WIDE_GAP_FACTOR = 1.0  # a gap wider than this many line heights becomes three spaces


def _line_groups(words: Sequence[Word]) -> list[list[Word]]:
    """Cluster words (one page) into visual lines by vertical centre."""
    items = [w for w in words if w.text.strip()]
    if not items:
        return []
    typical = median(w.h for w in items) or 0.01
    items.sort(key=lambda w: (w.y + w.h / 2, w.x))
    lines: list[list[Word]] = []
    centre = 0.0
    for w in items:
        c = w.y + w.h / 2
        if lines and abs(c - centre) <= 0.6 * typical:
            lines[-1].append(w)
            centre = sum(x.y + x.h / 2 for x in lines[-1]) / len(lines[-1])
        else:
            lines.append([w])
            centre = c
    return lines


def layout_text(words: Sequence[Word]) -> str:
    """Reading-order text: one line per visual line, wide gaps (columns) as three spaces.

    Words may span several pages; pages are separated by a blank line.
    """
    pages: dict[int, list[Word]] = {}
    for w in words:
        pages.setdefault(w.page, []).append(w)
    out_pages: list[str] = []
    for page in sorted(pages):
        out_lines: list[str] = []
        for line in _line_groups(pages[page]):
            line.sort(key=lambda w: w.x)
            height = median(w.h for w in line) or 0.01
            parts = [line[0].text.strip()]
            for prev, cur in zip(line, line[1:], strict=False):
                gap = cur.x - (prev.x + prev.w)
                parts.append("   " if gap > WIDE_GAP_FACTOR * height else " ")
                parts.append(cur.text.strip())
            out_lines.append("".join(parts))
        out_pages.append("\n".join(out_lines))
    return "\n\n".join(p for p in out_pages if p)


_OF = re.compile(r"\bpage\s+(\d{1,3})\s*(?:of|/)\s*(\d{1,3})\b")
_THIS_IS = re.compile(r"\b(\d{1,3})\s+pages?\b[^\n]{0,20}?\bthis is page\s+(\d{1,3})\b")
_BARE = re.compile(r"^\s*(\d{1,3})\s*/\s*(\d{1,3})\s*$")


def page_marker(text: str) -> tuple[int, int] | None:
    """``(n, m)`` meaning "page n of m", or ``None``.

    Works on the casefolded text, last match wins. Forms: ``page n of m`` / ``page n/m``;
    ``m pages, this is page n``; one of the last three non-empty lines being only ``n/m``.
    Dates such as ``05/03/2026`` never match. ``None`` when ``n == 0``, ``m == 0`` or ``n > m``.
    """
    t = text.casefold()
    best: tuple[int, int, int] | None = None  # (position, n, m)

    def consider(pos: int, n: int, m: int) -> None:
        nonlocal best
        if best is None or pos >= best[0]:
            best = (pos, n, m)

    for mt in _OF.finditer(t):
        consider(mt.start(), int(mt.group(1)), int(mt.group(2)))
    for mt in _THIS_IS.finditer(t):
        consider(mt.start(), int(mt.group(2)), int(mt.group(1)))
    offset = 0
    lines: list[tuple[int, str]] = []
    for raw in t.split("\n"):
        if raw.strip():
            lines.append((offset, raw))
        offset += len(raw) + 1
    for pos, line in lines[-3:]:
        mt = _BARE.match(line)
        if mt:
            consider(pos, int(mt.group(1)), int(mt.group(2)))
    if best is None:
        return None
    _, n, m = best
    if n == 0 or m == 0 or n > m:
        return None
    return n, m
