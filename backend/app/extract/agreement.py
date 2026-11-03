"""Do two independent OCR readers read this printed value the same way? (docs/ingest-and-extraction.md 4.12)

A phone photo is read badly by any one engine, and each engine misreads *different* characters. A
number that was edited by a forger is printed the same way whoever reads it, so both readers give the
same digits; a number one engine misread is read differently by the other. That is the whole signal:
**agreement is never used to clear anything, only disagreement is used to doubt**.

``agreement(raw, kind, box, second_words)`` compares one cell (the value the model returned, with the
box of the first reader's words that carry it) with the second reader's words on the same line of the
page and returns one of

* ``agree``: the second reader prints the same token(s) on that line;
* ``differ``: the second reader prints, where this value stands, something that is within a couple of
  characters of it but not the same (``other`` holds it): a misreading by one of the two;
* ``unread``: the second reader found nothing near the value, or only other figures (a reader that left
  an amount out says nothing about it).

Only money, dates and identifiers are compared; words are compared as the readers wrote them
(case-folded, enclosing punctuation removed), never rewritten to make them match.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal, NamedTuple

from app.core.models import CellKind, Region, Word
from app.extract.verify import locate, normalise_token

__all__ = ["READER_KINDS", "Agreement", "agreement", "squash"]

READER_KINDS = frozenset({"money", "decimal", "percent", "date", "id", "int"})
Agree = Literal["agree", "differ", "unread"]

LINE_BAND = 0.75  # a word is on the value's line when its centre is within this many heights
X_REACH = (
    0.22  # a word further than this (page-width fraction) from the value is not "where it stands"
)
_EDGE = "([{\"'$<"
_TAIL = ")]}\"',.;:!>"


class Agreement(NamedTuple):
    state: Agree
    other: str | None  # what the second reader prints there ("differ"), else None


_FIGURE = re.compile(r"^\d[\d.,]*\d$")
_FIGURE_DECIMALS = re.compile(r"^(\d[\d.,]*)[.,](\d{2})$")
_STRAY = re.compile(r"[^0-9a-z<]+$")


def squash(token: str) -> str:
    """The comparison form of a printed token: NFKC, case-folded, enclosing punctuation, a currency sign
    and stray marks after the last letter or digit (``ł`` read off a table border) removed. In a figure
    every separator but the last two-decimal one is a thousands separator whichever of ``,`` and ``.``
    the reader wrote (Vision often writes ``66.576.50``). Digits and letters are never changed."""
    t = normalise_token(token).strip()
    t = t.lstrip(_EDGE).rstrip(_TAIL)
    if t.startswith("s$"):
        t = t[2:]
    t = _STRAY.sub("", t)
    if _FIGURE.match(t):
        m = _FIGURE_DECIMALS.match(t)
        t = re.sub(r"[.,]", "", m.group(1)) + "." + m.group(2) if m else re.sub(r"[.,]", "", t)
    return t


def _squashed(words: Sequence[Word]) -> list[Word]:
    """Comparison copies of the words; ``block`` remembers the index of the word as printed."""
    out = []
    for i, w in enumerate(words):
        s = squash(w.text)
        if s:
            out.append(w.model_copy(update={"text": s, "block": i}))
    return out


def _has_digit(text: str) -> bool:
    return any(c.isdigit() for c in text)


def _distance(a: str, b: str) -> int:
    """Levenshtein distance (edits needed to turn ``a`` into ``b``)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _is_a_reading_of(printed: str, mine: str) -> bool:
    """Whether ``printed`` (the second reader's text) is a misreading of ``mine`` rather than some other
    figure on the line: the two differ by no more than two edits (one for short figures). A neighbouring
    amount is not a reading of this one, and a reader that left this figure out says nothing about it."""
    limit = 1 if len(mine) <= 5 else 2
    return _distance(printed.replace(" ", ""), mine.replace(" ", "")) <= limit


def agreement(
    raw: str, kind: CellKind, box: Region | None, second: Sequence[Word], *, forced: bool = False
) -> Agreement | None:
    """Compare a read value with the second reader. ``None`` when the comparison does not apply (a
    kind not in :data:`READER_KINDS`, a value with no digit, no box, or no second reading of the page).
    ``forced`` compares a text value anyway (the machine-readable zone of a passport)."""
    if not second or box is None or not (forced or (kind in READER_KINDS and _has_digit(raw))):
        return None
    yc = box.y + box.h / 2
    xc = box.x + box.w / 2
    band = [
        w
        for w in second
        if w.page == box.page
        and abs((w.y + w.h / 2) - yc) <= LINE_BAND * max(box.h, w.h)
        and abs((w.x + w.w / 2) - xc) <= X_REACH + box.w / 2
    ]
    if not band:
        return Agreement("unread", None)
    shown = sorted(band, key=lambda w: w.x)
    band = _squashed(shown)
    tokens = [t for t in (squash(t) for t in raw.split()) if t]
    if not tokens:
        return None
    if locate(" ".join(tokens), band, kind=kind) is not None:
        return Agreement("agree", None)
    near = sorted((w for w in band if _has_digit(w.text)), key=lambda w: abs(w.x + w.w / 2 - xc))
    if not near:
        return Agreement("unread", None)
    picked = sorted(near[: len(tokens)], key=lambda w: w.x)
    left, right = picked[0].x, picked[-1].x  # a month name between two digit words belongs to it
    inside = [w for w in band if left <= w.x <= right]
    folded = " ".join(w.text for w in inside)
    if not _is_a_reading_of(folded, " ".join(tokens)):
        return Agreement("unread", None)
    if kind in ("money", "decimal", "percent") and not _FIGURE_DECIMALS.match(folded):
        return Agreement("unread", None)  # not a well-formed figure: no reading to compare
    return Agreement("differ", " ".join(shown[w.block].text for w in inside))
