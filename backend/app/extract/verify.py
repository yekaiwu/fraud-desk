"""Exact copy, then verify (docs/ingest-and-extraction.md 6.5).

A prompt is not a guarantee, so every value a model returns is looked up in an independent
reading of the page (PyMuPDF words for a digital page, OCR words for a scan). A value that is not
found is **kept as read** with ``verified=False``; it is never replaced by a "closer" token, never
rounded, never dropped. Verification proves the printed value exists on the page, not that it sits
in the right row (the row cursor reduces wrong-row matches for repeated amounts).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.models import CellKind, Region, Word

__all__ = ["PageVerifier", "locate", "normalise_token", "union_box"]

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))


def normalise_token(s: str) -> str:
    """NFKC, casefold, drop zero-width characters. Punctuation is kept."""
    return unicodedata.normalize("NFKC", s).translate(_ZERO_WIDTH).casefold()


def _tokens(raw: str) -> list[str]:
    return [t for t in (normalise_token(x) for x in raw.split()) if t]


def _all_matches(raw: str, words: Sequence[Word], kind: CellKind) -> list[tuple[int, int]]:
    """Every ``(start, end)`` (end exclusive) at which ``raw`` is found, in word order."""
    tokens = _tokens(raw)
    if not tokens or not words:
        return []
    norm = [normalise_token(w.text) for w in words]
    n = len(tokens)
    found: list[tuple[int, int]] = []

    # pass 1: contiguous run of equal tokens
    for i in range(len(words) - n + 1):
        if norm[i : i + n] == tokens:
            found.append((i, i + n))
    if found:
        return found

    # pass 2: ignore whitespace between up to n+2 words (a PDF may split "S$" from "1,234.56")
    target = "".join(tokens)
    max_words = n + 2
    for i in range(len(words)):
        acc = ""
        for j in range(i, min(len(words), i + max_words)):
            acc += norm[j]
            if acc == target:
                found.append((i, j + 1))
                break
            if len(acc) >= len(target) or not target.startswith(acc):
                break
    if found or kind != "text":
        return found

    # text only: tokens in order within a window (descriptions that wrap under other columns)
    window = 4 * n + 10
    for i in range(len(words)):
        if norm[i] != tokens[0]:
            continue
        pos = i
        last = i
        ok = True
        for tok in tokens[1:]:
            nxt = next(
                (k for k in range(pos + 1, min(len(words), i + window)) if norm[k] == tok), None
            )
            if nxt is None:
                ok = False
                break
            pos = last = nxt
        if ok:
            found.append((i, last + 1))
    if found:
        return found

    # text only, last resort: every token somewhere on the page, each printed token used at most
    # as many times as it is printed (bag of words)
    positions: dict[str, list[int]] = {}
    for k, tok in enumerate(norm):
        positions.setdefault(tok, []).append(k)
    used: dict[str, int] = {}
    idx: list[int] = []
    for tok in tokens:
        n_used = used.get(tok, 0)
        where = positions.get(tok, [])
        if n_used >= len(where):
            return []
        idx.append(where[n_used])
        used[tok] = n_used + 1
    return [(min(idx), max(idx) + 1)]


def locate(
    raw: str, words: Sequence[Word], *, kind: CellKind, start: int = 0
) -> tuple[int, int] | None:
    """First match of ``raw`` at word index ``>= start`` as ``(start, end)`` (end exclusive)."""
    for a, b in _all_matches(raw, words, kind):
        if a >= start:
            return a, b
    return None


def union_box(words: Sequence[Word], file_id: str) -> Region:
    """Union box of words that sit on one page, clamped to the page."""
    if not words:
        raise ValueError("union_box needs at least one word")
    pages = {w.page for w in words}
    if len(pages) != 1:
        raise ValueError("union_box words must be on one page")
    x0 = max(0.0, min(w.x for w in words))
    y0 = max(0.0, min(w.y for w in words))
    x1 = min(1.0, max(w.x + w.w for w in words))
    y1 = min(1.0, max(w.y + w.h for w in words))
    w = max(x1 - x0, 1e-4)
    h = max(y1 - y0, 1e-4)
    if x0 + w > 1.0:
        x0 = 1.0 - w
    if y0 + h > 1.0:
        y0 = 1.0 - h
    return Region(file_id=file_id, page=next(iter(pages)), x=x0, y=y0, w=w, h=h)


@dataclass
class Verification:
    verified: bool | None
    box: Region | None
    cursor: int


class PageVerifier:
    """Verifies printed values of one page against that page's words, with a row cursor.

    Inside a table, cells of a row are searched from the end of the previous cell's match, rows in
    order, so repeated amounts (``500.00`` five times) bind to the right row. If nothing is found
    after the cursor the whole page is searched and the match nearest the cursor is used.
    """

    def __init__(self, words: Sequence[Word], file_id: str, page: int):
        self.words = [w for w in words if w.page == page] if words else []
        self.file_id = file_id
        self.page = page
        self.cursor = 0

    @property
    def has_reader(self) -> bool:
        return bool(self.words)

    def check(self, raw: str | None, kind: CellKind, *, use_cursor: bool = True) -> Verification:
        if raw is None or not raw.strip():
            return Verification(None, None, self.cursor)
        if not self.words:
            return Verification(None, None, self.cursor)
        start = self.cursor if use_cursor else 0
        hit = locate(raw, self.words, kind=kind, start=start)
        if hit is None and start > 0:
            matches = _all_matches(raw, self.words, kind)
            if matches:
                hit = min(matches, key=lambda m: abs(m[0] - start))
        if hit is None:
            return Verification(False, None, self.cursor)
        box = union_box(self.words[hit[0] : hit[1]], self.file_id)
        if use_cursor:
            self.cursor = max(self.cursor, hit[1]) if hit[0] >= start else self.cursor
        return Verification(True, box, self.cursor)

    def reset_cursor(self) -> None:
        self.cursor = 0
