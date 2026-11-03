"""Was a priced page read completely? (docs/ingest-and-extraction.md, "Completeness guard")

A model that is given OCR text transcribes what the OCR found, and it may skip a row on its own (a
discount line, a modifier, a wrapped line). Arithmetic cannot always see that: a reading that drops
a row still passes when the subtotal was dropped too, and a reading of half a receipt has no
subtotal to disagree with. So the page is read a second way, without a model: every printed line of
the OCR text that looks like an item (words, then an amount at the end of the line) or like a
price adjustment (a discount, a coupon) must have its amount among the values the extraction read.
A line whose amount is not found is a row the extraction missed.

This is a check on the reading, not on the document. It cannot see a row the OCR itself lost (no
amount on the page text means nothing to count), so it narrows the gap; it does not close it.
Pure functions, no model call, deterministic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.core.models import Cell, Word
from app.core.textlayout import aligned_text

__all__ = ["Missed", "canon_amount", "extracted_amounts", "missed_items"]

# an amount printed with two decimals, either mark, optional thousands grouping with a point, a comma
# or a space ("2 053,73", "1.234,56", "1,234.56", "$26.09", "2.05-")
_AMOUNT = re.compile(
    r"(?<![\w.,/:-])(?:[$€£]\s?)?"
    r"(?P<num>(?:\d{1,3}(?:[ ,.]\d{3})+|\d+)[.,]\d{2})"
    r"(?P<tail>-?)(?![\d\w])"
)
# looser: OCR often doubles a point or puts a space in it ("2..05-", "26 . 69")
_AMOUNT_LOOSE = re.compile(r"(?<![\w.,/:-])(\d+)\s?[.,]{1,2}\s?(\d{2})(-?)(?![\d\w])")
# what may follow the last amount on an item line: a tax code letter, a star, a bracket
_TRAILER = re.compile(r"^[\s*)\]]*[A-Za-z]?[\s*)\]]*$")
_NOT_ITEM = re.compile(
    r"\b(?:sub\s*-?\s*total|total|tax|vat|gst|tip|gratuity|change|cash|tender(?:ed)?|visa|master|"
    r"amex|card|balance|bal|due|paid|payment|rounding|round|points|credit|debit|auth|approved|"
    r"surcharge|service|svc|deposit|amount|net|gross|summary|qty|price|worth|eft|pos|ref)\b",
    re.I,
)
_ADJUSTMENT = re.compile(
    r"\b(?:disc(?:ount)?|coupon|promo(?:tion)?|voucher|saving|savings|reduction|rebate|comp|"
    r"void|refund)\b",
    re.I,
)
_WORD = re.compile(r"[A-Za-z]{3,}")


@dataclass(frozen=True)
class Missed:
    page: int
    text: str  # the printed line, whitespace collapsed
    amount: str  # canonical digits of its amount
    kind: str  # "item" | "adjustment"


def canon_amount(text: str | None) -> str | None:
    """Digits of an amount with two decimals, no signs and no separators: ``"2 053,73"`` and
    ``"2,053.73"`` both give ``"205373"``. ``None`` when ``text`` holds no such amount. Independent
    of how the application parses money, so the guard holds for any number format."""
    if not text:
        return None
    m = _AMOUNT.search(text) or _AMOUNT_LOOSE.search(text)
    if m is None:
        return None
    if "num" in m.groupdict():
        digits = re.sub(r"\D", "", m.group("num"))
    else:
        digits = m.group(1) + m.group(2)
    return digits.lstrip("0") or "0"


def extracted_amounts(
    cells: Iterable[Cell | None],
) -> set[str]:
    """Canonical digits of every amount-bearing cell the extraction produced (as printed)."""
    out: set[str] = set()
    for cell in cells:
        if cell is None:
            continue
        for raw in (cell.raw, cell.alt_raw):
            canon = canon_amount(raw)
            if canon is not None:
                out.add(canon)
    return out


def _amounts_on(line: str, loose: bool) -> list[tuple[str, int, int]]:
    """(canonical digits, start, end) of every amount on the line, left to right."""
    found: dict[tuple[int, int], str] = {}
    for m in _AMOUNT.finditer(line):
        found[(m.start(), m.end())] = re.sub(r"\D", "", m.group("num")).lstrip("0") or "0"
    if loose:
        for m in _AMOUNT_LOOSE.finditer(line):
            if not any(a <= m.start() < b for a, b in found):
                found[(m.start(), m.end())] = (m.group(1) + m.group(2)).lstrip("0") or "0"
    return sorted((c, a, b) for (a, b), c in found.items())


def _near(canon: str, known: set[str]) -> bool:
    """Equal, or one digit apart (an OCR digit error or the page image read differently)."""
    if canon in known:
        return True
    if len(canon) < 4:  # "2,00" is not near "1,00": short amounts must match exactly
        return False
    return any(
        len(k) == len(canon) and sum(a != b for a, b in zip(k, canon, strict=True)) <= 1
        for k in known
    )


def missed_in_text(text: str, known: set[str], page: int = 1) -> list[Missed]:
    """``missed_items`` for one page of already-aligned text."""
    out: list[Missed] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if not line:
            continue
        adjustment = _ADJUSTMENT.search(line) is not None
        amounts = sorted(_amounts_on(line, loose=adjustment), key=lambda t: t[2])
        if not amounts:
            continue
        canon, start, end = amounts[-1]
        if not _TRAILER.match(line[end:]):
            continue  # the amount is not the end of the line: a column table, not an item
        if not _WORD.search(line[:start]):
            continue
        if adjustment:
            kind = "adjustment"
        elif _NOT_ITEM.search(line):
            continue
        else:
            kind = "item"
        # a line is read when any of its amounts is (a table prints net, tax and gross side by side)
        if any(_near(c, known) for c, _, _ in amounts):
            continue
        out.append(Missed(page=page, text=line[:80], amount=canon, kind=kind))
    return out


def missed_items(words: Sequence[Word], known: set[str]) -> list[Missed]:
    """Printed item or adjustment lines whose amount the extraction did not read."""
    out: list[Missed] = []
    for page in sorted({w.page for w in words}):
        out += missed_in_text(aligned_text([w for w in words if w.page == page]), known, page)
    return out
