"""Shared helpers of the KYC and AML text-layer extractors (extract-kyc).

This module registers nothing (``discover()`` imports it harmlessly). It sits on top of the toolkit
in ``text_layer.py`` and adds the few things the labelled KYC forms need:

* ``Form``: label lookups that fix two toolkit corner cases (a value that is really the *next label*
  on the same line, as on a card that prints ``Date of issue`` and ``Address`` side by side, falls
  back to the value printed below the label) and that can continue a value over wrapped lines;
* ``run_table``: one header-anchored table across pages. Each body line is split into cells by
  ``assign_line``, a small dynamic programme that fits runs of words to the header's columns by left
  or right edge (whichever fits), so right-aligned multi-word dates, tight neighbouring columns and
  empty cells are read correctly without knowing each column's alignment in advance;
* ``fill_rows``, ``currency_cell``, ``make_validator``.

Everything here reads the text layer only; nothing is guessed. A cell the page does not print stays
empty and a value that is printed wrongly (a malformed ID number, a 43-character MRZ line) is kept
as printed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from statistics import median

from app.core.context import CaseContext
from app.core.models import Cell, Document, Table, Word
from app.core.schemas import DOC_SCHEMAS
from app.extract.base import ExtractionResult, Validation, missing_required, unverified_failures
from app.extract.registry import register_validator
from app.extract.text_layer import (
    ColDef,
    DocView,
    FlatText,
    LabelHit,
    ParsedRow,
    ParsedTable,
    blank_row,
    find_header,
    row_box,
    words_text,
)

__all__ = [
    "FOOTER",
    "SECTION_STOP",
    "Form",
    "L",
    "currency_cell",
    "flat_of",
    "fill_rows",
    "make_validator",
    "run_table",
]

# SPECIMEN footer of the generator ("SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT") and page markers
FOOTER = re.compile(r"SPECIMEN|FICTITIOUS|\bPage\s+\d+\s*(?:of|/)\s*\d+", re.I)
# a heading that starts another section: ends the table above it
SECTION_STOP = re.compile(
    r"^(?:Shareholders?|Share\s*holdings?|Members?|Share\s+Capital|Directors?|Officers?|"
    r"Charges|Auditors?|Company\s+Secretar\w+)\s*$",
    re.I,
)
_CUR_PREFIX = re.compile(r"^(S\$|US\$|SGD|USD)(?=\d|$)", re.I)
_LABEL_END = r"(?![A-Za-z0-9])"


def L(patterns: Sequence[str]) -> list[str]:
    """Label patterns with a word boundary at the end (``Name`` must not match ``Namex``)."""
    return [p + _LABEL_END for p in patterns]


def flat_of(words: Sequence[Word]) -> FlatText:
    """``FlatText`` over the given words (kept in the order given)."""
    parts: list[str] = []
    spans: list[tuple[int, int, Word]] = []
    pos = 0
    for w in words:
        t = w.text.strip()
        if not t:
            continue
        if parts:
            pos += 1
        parts.append(t)
        spans.append((pos, pos + len(t), w))
        pos += len(t)
    return FlatText(" ".join(parts), spans)


class Form:
    """Label/value lookups for one document view.

    ``labels`` maps a field key to anchored label regexes (``^`` = start of a segment). The union of
    all patterns is installed as the view's known labels so a value is never read past the next label.
    """

    def __init__(
        self,
        view: DocView,
        doc_type: str,
        labels: dict[str, list[str]],
        *,
        extra_known: Sequence[str] = (),
    ):
        self.view = view
        self.doc_type = doc_type
        self.labels = {k: L(v) for k, v in labels.items()}
        known = [p for pats in self.labels.values() for p in pats] + L(extra_known)
        view.set_known_labels(known)

    # ------------------------------------------------------------------ lookups
    def _is_label(self, words: Sequence[Word]) -> bool:
        kl = self.view.known_labels
        return kl is not None and bool(kl.match(words_text(words)))

    def hits(self, key_or_patterns: str | Sequence[str]) -> list[LabelHit]:
        """Label occurrences that have a value, reading order. A value that is itself a label (two
        label/value pairs on one line, values printed under their labels) is replaced by the value
        printed directly below the label, or dropped."""
        patterns = (
            self.labels[key_or_patterns]
            if isinstance(key_or_patterns, str)
            else L(list(key_or_patterns))
        )
        view = self.view
        out: list[LabelHit] = []
        for h in view.find_labelled(patterns):
            if not h.value_words:
                continue
            if h.how == "right" and self._is_label(h.value_words):
                page_lines = [ln for ln in view.all_lines if ln.page == h.line.page]
                below = view._below(h.line, h.label_words, page_lines)
                if not below:
                    continue
                h = replace(h, value_words=below, how="below")
            out.append(h)
        return out

    def first(self, key_or_patterns: str | Sequence[str]) -> LabelHit | None:
        found = self.hits(key_or_patterns)
        return found[0] if found else None

    def extend(self, hit: LabelHit, max_extra: int = 2) -> list[Word]:
        """The hit's value words plus up to ``max_extra`` wrapped lines directly below that start at
        the value's left edge and are not labels (an address that runs onto a second line)."""
        words = list(hit.value_words)
        if not words:
            return words
        x0 = min(w.x for w in words)
        page_lines = {ln.index: ln for ln in self.view.all_lines if ln.page == hit.line.page}
        idx = hit.line.index + (1 if hit.how == "below" else 0)
        cur = page_lines.get(idx)
        for _ in range(max_extra):
            nxt = page_lines.get(idx + 1)
            if nxt is None or cur is None or (nxt.page, nxt.index) in self.view.claimed:
                break
            if nxt.cy - cur.cy > 1.9 * max(nxt.height, cur.height):
                break
            if len(nxt.segs) != 1 or abs(nxt.segs[0].x0 - x0) > 0.02:
                break
            if self._is_label(nxt.segs[0].words) or FOOTER.search(nxt.text):
                break
            words.extend(nxt.segs[0].words)
            idx += 1
            cur = nxt
        return words

    # ------------------------------------------------------------------ filling
    def put(
        self,
        fields: dict[str, Cell],
        key: str,
        *,
        nth: int = 0,
        wrap: bool = False,
        patterns: Sequence[str] | None = None,
    ) -> LabelHit | None:
        """Fill ``fields[key]`` from the ``nth`` labelled occurrence (an already filled field is kept)."""
        if fields[key].raw is not None or fields[key].value is not None:
            return None
        found = self.hits(key) if patterns is None else self.hits(patterns)
        if len(found) <= nth:
            return None
        hit = found[nth]
        spec = DOC_SCHEMAS[self.doc_type].fields[key]
        words = self.extend(hit) if wrap else hit.value_words
        fields[key] = self.view.cell(words, spec.kind, f"fields.{key}", values=spec.values)
        if nth == 0:
            norm0 = " ".join(words_text(hit.value_words).casefold().split())
            for other in found[1:]:
                if " ".join(words_text(other.value_words).casefold().split()) != norm0:
                    self.view.warn(f"label_repeated: {key}")
                    break
        return hit

    def put_all(
        self, fields: dict[str, Cell], keys: Sequence[str], *, wrap: Sequence[str] = ()
    ) -> None:
        for key in keys:
            self.put(fields, key, wrap=key in wrap)


def currency_cell(
    view: DocView,
    fields: dict[str, Cell],
    words: Sequence[Word],
    *,
    key: str = "currency",
) -> bool:
    """Fill ``fields[key]`` from a currency symbol or ISO code printed with an amount (``S$``,
    ``S$1,000.00``, ``1,000.00 SGD``). Returns True when one was found."""
    if fields[key].value is not None or not words:
        return False
    cands = [words[0], *words[1:2], words[-1]]
    for w in cands:
        t = w.text.strip()
        m = _CUR_PREFIX.match(t)
        if m:
            fields[key] = view.cell([w], "enum", f"fields.{key}", raw_override=m.group(1))
            return True
        if re.fullmatch(r"(?:SGD|USD)", t, re.I):
            fields[key] = view.cell([w], "enum", f"fields.{key}")
            return True
    return False


# ----------------------------------------------------------------------------------------- tables


EMPTY_CELL_PT = 20.0  # cost of leaving a column empty on a row (points)
GAP_FREE_PT = 12.0  # a gap inside one cell up to this wide is free; wider costs
GAP_COST = 1.5


def _cell_cost(words: Sequence[Word], band, wpt: float) -> float:  # noqa: ANN001
    """How well a run of words fits a column: distance of the run's left edge to the header's left
    edge, or of its right edge to the header's right edge (whichever is closer: a column is aligned
    left or right, and which one is not assumed), plus a charge for wide gaps inside the run."""
    x0 = words[0].x * wpt
    x1 = (words[-1].x + words[-1].w) * wpt
    cost = min(abs(x0 - band.hx0 * wpt), abs(x1 - band.hx1 * wpt))
    for a, b in zip(words, words[1:], strict=False):
        gap = (b.x - (a.x + a.w)) * wpt
        cost += GAP_COST * max(0.0, gap - GAP_FREE_PT)
    return cost


def assign_line(ln, bands, wpt: float) -> dict[str, list[Word]]:  # noqa: ANN001
    """Split one body line into column cells: the segmentation of its words (left to right) over the
    header's columns with the lowest total fit cost. Alignment-agnostic, so a right-aligned date
    column of several words, or two tight columns, are read correctly. Ignored columns are dropped."""
    words = [w for w in ln.words if w.text.strip()]
    n, m = len(words), len(bands)
    if n == 0 or m == 0:
        return {}
    inf = float("inf")
    best = [[inf] * (n + 1) for _ in range(m + 1)]
    back = [[0] * (n + 1) for _ in range(m + 1)]
    best[0][0] = 0.0
    for j in range(1, m + 1):
        band = bands[j - 1]
        for i in range(n + 1):
            skip = best[j - 1][i] + EMPTY_CELL_PT
            if skip < best[j][i]:
                best[j][i], back[j][i] = skip, i
            for k in range(i):
                if best[j - 1][k] == inf:
                    continue
                cost = best[j - 1][k] + _cell_cost(words[k:i], band, wpt)
                if cost < best[j][i]:
                    best[j][i], back[j][i] = cost, k
    out: dict[str, list[Word]] = {}
    i = n
    for j in range(m, 0, -1):
        k = back[j][i]
        if k < i and not bands[j - 1].ignore:
            out[bands[j - 1].key] = words[k:i]
        i = k
    return out


def run_table(
    view: DocView,
    coldefs: Sequence[ColDef],
    *,
    accept: Callable[[set[str]], bool],
    is_row_start: Callable[[dict[str, list[Word]]], bool],
    wrap_key: str,
    special: Callable | None = None,
    stop: Callable | None = None,
) -> ParsedTable:
    """One logical table across the pages of the view: the header is found by ``coldefs`` (and repeated
    headers on later pages are recognised), each body line is split into cells by ``assign_line``,
    a row starts where ``is_row_start`` says so, and a following line that only has text in the
    ``wrap_key`` column (a wrapped name) is appended to the row above. The toolkit's ``ParsedTable``
    is returned so ``fill_rows`` and the cascade see the usual shape."""
    result = ParsedTable()
    bands: list | None = None
    for page in view.pages:
        header = find_header(page, coldefs, accept)
        body_start = 0
        if header is not None:
            bands = sorted(header.bands, key=lambda b: b.hx0)
            result.header_pages.append(page.page)
            result.currency_raw = result.currency_raw or header.currency_raw
            view.claimed.add((page.page, header.line.index))
            body_start = header.line.index + 1
        elif bands is None:
            continue
        current: ParsedRow | None = None
        prev_cy: float | None = None
        pitches: list[float] = []
        for ln in page.lines:
            if ln.index < body_start or (page.page, ln.index) in view.claimed:
                continue
            if FOOTER.search(ln.text):
                current = None
                continue
            cols = assign_line(ln, bands, page.wpt)
            if stop is not None and stop(ln, cols):
                result.stop_at = result.stop_at or (page.page, ln.index)
                break
            tag = special(ln, cols) if special is not None else None
            if tag is not None:
                result.specials.append((tag, page.page, cols, ln))
                view.claimed.add((page.page, ln.index))
                current, prev_cy = None, ln.cy
                continue
            if cols and is_row_start(cols):
                if prev_cy is not None and current is not None:
                    pitches.append(ln.cy - prev_cy)
                current = ParsedRow(page.page, {k: list(v) for k, v in cols.items()}, [ln])
                result.rows.append(current)
                view.claimed.add((page.page, ln.index))
                prev_cy = ln.cy
                continue
            if current is not None and cols and set(cols) <= {wrap_key}:
                limit = max(1.9 * ln.height, 1.05 * median(pitches) if pitches else 0.0)
                if prev_cy is not None and ln.cy - prev_cy <= limit:
                    current.cols.setdefault(wrap_key, []).extend(cols[wrap_key])
                    current.lines.append(ln)
                    view.claimed.add((page.page, ln.index))
                    prev_cy = ln.cy
                    continue
            current = None if not cols else current
            prev_cy = ln.cy
    return result


def fill_rows(
    view: DocView, tables: dict[str, Table], doc_type: str, name: str, parsed: ParsedTable
) -> None:
    """Rows of ``parsed`` into ``tables[name]``: every schema column, kind from the schema."""
    spec = DOC_SCHEMAS[doc_type].tables[name]
    table = tables[name]
    pages: list[int] = []
    for i, pr in enumerate(parsed.rows):
        row = blank_row(doc_type, name, i)
        for col, cspec in spec.columns.items():
            row.cells[col] = view.cell(
                pr.cols.get(col, []), cspec.kind, f"{name}[{i}].{col}", values=cspec.values
            )
        row.box = row_box(row.cells.values())
        table.rows.append(row)
        pages.append(pr.page)
    table.pages = sorted(set(pages))
    view.row_pages[name] = pages


def make_validator(
    doc_type: str,
) -> Callable[[ExtractionResult, CaseContext, Document], Validation]:
    """Register the standard validator of a KYC type: unverified money/date/id cells make a page
    failing; schema-required fields without a value are listed. Never edits a value."""

    def validate(result: ExtractionResult, ctx: CaseContext, doc: Document) -> Validation:
        v = Validation()
        for page, reason in unverified_failures(result, doc).items():
            v.failing.setdefault(page, reason)
        v.missing_required = missing_required(result, doc_type)
        return v

    validate.__name__ = f"validate_{doc_type}"
    register_validator(doc_types={doc_type})(validate)
    return validate
