"""Deterministic text-layer parsing toolkit (docs/ingest-and-extraction.md 6.3).

Free, no model call. It reads the PyMuPDF/OCR *words with boxes* of a document and parses the
generator's layouts with layout-aware regexes: label/value fields and header-anchored tables.
Real bank layouts are the LLM path's job (honest limit, doc section 11).

Building blocks (used by ``bank_statement.py``, ``invoice.py`` ... and by extract-kyc):

* ``build_view(doc, ctx)`` -> ``DocView``: words grouped into ``Line`` objects and ``Seg`` runs
  (words separated by a wide gap), with page dimensions;
* ``DocView.find_labelled(patterns)`` -> ``LabelHit`` (value inline, to the right, or below);
* ``DocView.cell(words, kind, where)`` -> a ``Cell`` whose box is the union of its words and
  ``verified=True`` (the text layer *is* what is printed);
* ``find_header`` / ``parse_table``: header-anchored column bands, rows that start on a
  predicate, wrapped description lines appended to the previous row;
* ``TextLayerExtractor``: base class. A subclass implements ``parse(view, out)``.

Hidden or covered text is part of ``words`` (forensics needs it), so this parser may read it; that
is a documented limit.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import ClassVar, Literal

from app.core.context import CaseContext
from app.core.models import Cell, CellKind, Document, Region, Row, Table, Word
from app.core.schemas import DOC_SCHEMAS
from app.extract.base import ExtractionResult, LayoutNotRecognised
from app.extract.normalise import format_warning, make_cell
from app.extract.verify import union_box

__all__ = [
    "Band",
    "ColDef",
    "DocView",
    "FlatText",
    "Header",
    "LabelHit",
    "Line",
    "PageView",
    "ParsedRow",
    "ParsedTable",
    "Seg",
    "TextLayerExtractor",
    "build_view",
    "empty_cell",
    "find_header",
    "flat_text",
    "materialise",
    "parse_table",
    "words_text",
]

A4 = (595.3, 841.9)
FLOW_GAP_PT = 8.0  # words closer than this continue the same text cell across column bands
GAP_FACTOR = 2.5  # a gap wider than this many word heights splits a line into segments
MIN_GAP_PT = 8.0
_AMOUNT = re.compile(r"^\(?[-+]?(?:S\$|US\$|SGD|\$)?\d[\d,]*(?:\.\d+)?\)?$", re.I)
_SUFFIX_WORD = re.compile(r"^(?:CR|DR)$", re.I)
_DASH = re.compile(r"^[-–—]{1,2}$")
_SEPARATORS = frozenset({"|", "\u2022", "\u00b7"})
_CURRENCY_TOKEN = re.compile(r"^\(?(?:S\$|SGD|USD|US\$|\$)\)?$", re.I)


def words_text(words: Sequence[Word]) -> str:
    return " ".join(w.text.strip() for w in words if w.text.strip())


def is_amount_text(text: str) -> bool:
    return bool(_AMOUNT.match(text.strip()))


# --------------------------------------------------------------------------------------
# Lines and segments
# --------------------------------------------------------------------------------------


@dataclass
class Seg:
    """Words of one line that are not separated by a wide gap."""

    words: list[Word]

    @property
    def text(self) -> str:
        return words_text(self.words)

    @property
    def x0(self) -> float:
        return min(w.x for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x + w.w for w in self.words)


@dataclass
class Line:
    page: int
    index: int  # index within its page
    words: list[Word]
    segs: list[Seg] = field(default_factory=list)

    @property
    def text(self) -> str:
        return words_text(self.words)

    @property
    def cy(self) -> float:
        return median(w.y + w.h / 2 for w in self.words)

    @property
    def height(self) -> float:
        return median(w.h for w in self.words)

    @property
    def x0(self) -> float:
        return min(w.x for w in self.words)

    @property
    def top(self) -> float:
        return min(w.y for w in self.words)

    @property
    def max_size(self) -> float:
        sizes = [w.size for w in self.words if w.size]
        return max(sizes) if sizes else self.height * 100


@dataclass
class PageView:
    page: int
    lines: list[Line]
    words: list[Word]
    wpt: float
    hpt: float


def group_lines(words: Sequence[Word], page: int, wpt: float, hpt: float) -> list[Line]:
    """Words of one page into visual lines (vertical centres within half the smaller height)."""
    items = sorted(
        (w for w in words if w.text.strip() and w.w > 0 and w.h > 0),
        key=lambda w: (w.y + w.h / 2, w.x),
    )
    groups: list[list[Word]] = []
    centre = 0.0
    min_h = 0.0
    for w in items:
        c = w.y + w.h / 2
        if groups and abs(c - centre) < 0.5 * min(w.h, min_h):
            groups[-1].append(w)
            centre = sum(x.y + x.h / 2 for x in groups[-1]) / len(groups[-1])
            min_h = min(min_h, w.h)
        else:
            groups.append([w])
            centre, min_h = c, w.h
    lines: list[Line] = []
    for g in groups:
        g.sort(key=lambda w: w.x)
        line = Line(page=page, index=len(lines), words=g)
        line.segs = _segments(g, wpt, hpt)
        lines.append(line)
    return lines


def _segments(words: list[Word], wpt: float, hpt: float) -> list[Seg]:
    h_pt = median(w.h for w in words) * hpt
    threshold = max(GAP_FACTOR * h_pt, MIN_GAP_PT)
    segs: list[list[Word]] = [[words[0]]]
    for prev, cur in zip(words, words[1:], strict=False):
        gap_pt = (cur.x - (prev.x + prev.w)) * wpt
        if gap_pt > threshold:
            segs.append([cur])
        else:
            segs[-1].append(cur)
    return [Seg(s) for s in segs]


# --------------------------------------------------------------------------------------
# Document view
# --------------------------------------------------------------------------------------


@dataclass
class LabelHit:
    line: Line
    label_words: list[Word]
    value_words: list[Word]
    how: Literal["inline", "right", "below", "none"]
    label_text: str = ""


class DocView:
    """Everything a parser needs: lines per page, a warning sink, claimed table lines."""

    def __init__(self, doc: Document, ctx: CaseContext, pages: list[PageView]):
        self.doc = doc
        self.ctx = ctx
        self.file_id = doc.file_id
        self.pages = pages
        self.warnings: list[str] = []
        self.claimed: set[tuple[int, int]] = set()  # (page, line index) consumed by tables
        self.row_pages: dict[str, list[int]] = {}  # table -> file page per row (set by parsers)
        self.known_labels: re.Pattern[str] | None = None
        self.tolerance = ctx.settings.money_tolerance

    # ------------------------------------------------------------------ basics
    @property
    def all_lines(self) -> list[Line]:
        return [ln for p in self.pages for ln in p.lines]

    @property
    def first_page(self) -> PageView:
        return self.pages[0]

    def page(self, page: int) -> PageView:
        for p in self.pages:
            if p.page == page:
                return p
        raise KeyError(page)

    def text(self, page: int | None = None) -> str:
        pages = self.pages if page is None else [self.page(page)]
        return "\n".join(ln.text for p in pages for ln in p.lines)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def set_known_labels(self, patterns: Iterable[str]) -> None:
        pats = [p for p in patterns if p]
        self.known_labels = re.compile("|".join(f"(?:{p})" for p in pats), re.I) if pats else None

    # ------------------------------------------------------------------ cells
    def region(self, words: Sequence[Word]) -> Region | None:
        ws = [w for w in words if w.text.strip()]
        if not ws:
            return None
        by_page: dict[int, list[Word]] = {}
        for w in ws:
            by_page.setdefault(w.page, []).append(w)
        first = min(by_page)
        return union_box(by_page[first], self.file_id)

    def cell(
        self,
        words: Sequence[Word],
        kind: CellKind,
        where: str,
        *,
        values: list[str] | None = None,
        magnitude: bool = False,
        raw_override: str | None = None,
    ) -> Cell:
        """A text-layer cell: raw as printed, box = union of its words, ``verified=True``."""
        ws = [w for w in words if w.text.strip()]
        if not ws:
            return empty_cell(kind)
        raw = raw_override if raw_override is not None else words_text(ws)
        cell, warn = make_cell(
            raw,
            kind,
            source="text_layer",
            box=self.region(ws),
            verified=True,
            values=values,
            magnitude=magnitude,
        )
        if warn:
            self.warn(format_warning(warn, where, raw))
        return cell

    # ------------------------------------------------------------------ labels
    def find_labelled(
        self,
        patterns: Sequence[str],
        *,
        lines: Sequence[Line] | None = None,
        below: bool = True,
    ) -> list[LabelHit]:
        """Every label occurrence (reading order) with its value words.

        Patterns are case-insensitive regexes applied to each segment's text at word boundaries;
        a leading ``^`` anchors at the start of a segment. Value = words after the label in the
        same segment; else the next segment on the line; else (``below``) the segment under the
        label's left edge on the next line. Lines claimed by a table are skipped.
        """
        compiled = [re.compile(p + r"\s*:?\s*", re.I) for p in patterns]
        hits: list[LabelHit] = []
        pool = lines if lines is not None else self.all_lines
        by_page: dict[int, list[Line]] = {}
        for ln in self.all_lines:
            by_page.setdefault(ln.page, []).append(ln)
        for ln in pool:
            if (ln.page, ln.index) in self.claimed:
                continue
            for si, seg in enumerate(ln.segs):
                text, starts = _seg_offsets(seg)
                start_set = set(starts)
                for rx in compiled:
                    for m in rx.finditer(text):
                        if m.start() not in start_set:
                            continue
                        if m.end() != len(text) and m.end() not in start_set:
                            continue
                        k0 = next(i for i, s0 in enumerate(starts) if s0 >= m.start())
                        k1 = next((i for i, s0 in enumerate(starts) if s0 >= m.end()), len(starts))
                        label_words = seg.words[k0:k1]
                        if not label_words:
                            continue
                        value = _strip_punct(self._cut(seg, text, starts, k1))
                        how: Literal["inline", "right", "below", "none"] = "inline"
                        if not value:
                            how = "right"
                            if si + 1 < len(ln.segs):
                                nseg = ln.segs[si + 1]
                                ntext, nstarts = _seg_offsets(nseg)
                                value = _strip_punct(self._cut(nseg, ntext, nstarts, 0))
                            if not value and below:
                                how = "below"
                                value = self._below(ln, label_words, by_page.get(ln.page, []))
                            if not value:
                                how = "none"
                        hits.append(
                            LabelHit(
                                ln,
                                label_words,
                                value,
                                how,
                                label_text=text[m.start() : m.end()].strip(),
                            )
                        )
        hits.sort(key=lambda h: (_page_pos(self, h.line), min(w.x for w in h.label_words)))
        return _dedupe(hits)

    def _cut(self, seg: Seg, text: str, starts: list[int], first: int) -> list[Word]:
        """Words of ``seg`` from index ``first``, cut where another known label starts."""
        words = seg.words[first:]
        for k, w in enumerate(words):  # a "|" or bullet separates two facts printed on one line
            if k > 0 and w.text.strip() in _SEPARATORS:
                words = words[:k]
                break
        if self.known_labels is None:
            return list(words)
        out: list[Word] = []
        for k, w in enumerate(words):
            if k > 0 and self.known_labels.match(text, starts[first + k]):
                break
            out.append(w)
        return out

    def _below(self, ln: Line, label_words: list[Word], page_lines: list[Line]) -> list[Word]:
        x0 = min(w.x for w in label_words)
        nxt = next((c for c in page_lines if c.index == ln.index + 1), None)
        if nxt is None or (nxt.page, nxt.index) in self.claimed:
            return []
        # must be close below (at most ~3 line heights), not a far-away block
        if (nxt.cy - ln.cy) > 3.2 * max(ln.height, nxt.height):
            return []
        for seg in nxt.segs:
            if abs(seg.x0 - x0) <= 0.04:
                if self.known_labels is not None and self.known_labels.match(seg.text):
                    return []
                return list(seg.words)
        return []

    def uen_near(self, party: LabelHit, *, max_lines: int = 4) -> LabelHit | None:
        """The first ``UEN`` label (with a value) within a few lines after a party label, same page."""
        hits = [
            h
            for h in self.find_labelled([r"UEN", r"Co\.?\s*Reg(?:\.|istration)?\s*No\.?"])
            if h.value_words
            and h.line.page == party.line.page
            and 0 <= h.line.index - party.line.index <= max_lines
            and h.label_words[0] is not party.label_words[0]
        ]
        return hits[0] if hits else None

    def first(
        self, patterns: Sequence[str], *, lines: Sequence[Line] | None = None
    ) -> LabelHit | None:
        hits = [h for h in self.find_labelled(patterns, lines=lines) if h.value_words]
        return hits[0] if hits else None

    def field(
        self,
        out: dict[str, Cell],
        key: str,
        patterns: Sequence[str],
        *,
        lines: Sequence[Line] | None = None,
        nth: int = 0,
        trim: re.Pattern[str] | None = None,
    ) -> LabelHit | None:
        """Fill ``out[key]`` from the ``nth`` labelled occurrence that has a value. ``trim`` drops
        value words that match it (for example a trailing ``(continued)``)."""
        spec = DOC_SCHEMAS[self.doc.doc_type].fields[key]
        hits = [h for h in self.find_labelled(patterns, lines=lines) if h.value_words]
        if trim is not None:
            for h in hits:
                h.value_words = [w for w in h.value_words if not trim.match(w.text.strip())]
            hits = [h for h in hits if h.value_words]
        if len(hits) <= nth:
            return None
        hit = hits[nth]
        cell = self.cell(hit.value_words, spec.kind, f"fields.{key}", values=spec.values)
        out[key] = cell
        if nth == 0:
            norm0 = " ".join(words_text(hit.value_words).casefold().split())
            for other in hits[1:]:
                if " ".join(words_text(other.value_words).casefold().split()) != norm0:
                    self.warn(f"label_repeated: {key}")
                    break
        return hit


def _seg_offsets(seg: Seg) -> tuple[str, list[int]]:
    text = ""
    starts: list[int] = []
    for i, w in enumerate(seg.words):
        if i:
            text += " "
        starts.append(len(text))
        text += w.text.strip()
    return text, starts


def _strip_punct(words: list[Word]) -> list[Word]:
    out = list(words)
    while out and out[0].text.strip() in (":", "-", "–", "—", "="):
        out.pop(0)
    if out and out[0].text.startswith(":") and len(out[0].text) > 1:
        w = out[0]
        out[0] = w.model_copy(update={"text": w.text.lstrip(": ")})
    return out


def _page_pos(view: DocView, line: Line) -> tuple[int, int]:
    return (line.page, line.index)


def _dedupe(hits: list[LabelHit]) -> list[LabelHit]:
    seen: set[tuple[int, int, float]] = set()
    out: list[LabelHit] = []
    for h in hits:
        key = (h.line.page, h.line.index, round(min(w.x for w in h.label_words), 4))
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


@dataclass
class FlatText:
    """All words of some pages as one string (single spaces, reading order) with word offsets, for
    sentences that wrap across lines."""

    text: str
    spans: list[tuple[int, int, Word]]

    def words_in(self, start: int, end: int) -> list[Word]:
        return [w for a, b, w in self.spans if a < end and b > start]


def flat_text(view: DocView, page: int | None = None) -> FlatText:
    parts: list[str] = []
    spans: list[tuple[int, int, Word]] = []
    pos = 0
    for pv in view.pages:
        if page is not None and pv.page != page:
            continue
        for ln in pv.lines:
            for w in ln.words:
                t = w.text.strip()
                if not t:
                    continue
                if parts:
                    pos += 1
                parts.append(t)
                spans.append((pos, pos + len(t), w))
                pos += len(t)
    return FlatText(" ".join(parts), spans)


def empty_cell(kind: CellKind = "text") -> Cell:
    return Cell(value=None, kind=kind, source="text_layer", verified=None)


# --------------------------------------------------------------------------------------
# Building the view
# --------------------------------------------------------------------------------------


def doc_pages(doc: Document) -> range:
    return range(doc.page_start, doc.page_end + 1)


def page_dims(ctx: CaseContext, file_id: str, page: int) -> tuple[float, float]:
    try:
        for info in ctx.file(file_id).pages:
            if info.page == page and info.width_pt > 0 and info.height_pt > 0:
                return info.width_pt, info.height_pt
    except KeyError:
        pass
    return A4


def page_words_for(doc: Document, ctx: CaseContext, page: int) -> list[Word]:
    words = ctx.page_words.get((doc.file_id, page))
    if words:
        return list(words)
    return [w for w in doc.words if w.page == page]


def build_view(doc: Document, ctx: CaseContext, pages: Iterable[int] | None = None) -> DocView:
    pv: list[PageView] = []
    for page in pages if pages is not None else doc_pages(doc):
        words = page_words_for(doc, ctx, page)
        if not words:
            continue
        wpt, hpt = page_dims(ctx, doc.file_id, page)
        pv.append(PageView(page, group_lines(words, page, wpt, hpt), words, wpt, hpt))
    return DocView(doc, ctx, pv)


# --------------------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ColDef:
    key: str
    pattern: str  # regex that must fully match the header text (1 to 3 words)
    align: Literal["left", "right"] = "left"
    ignore: bool = False


@dataclass
class Band:
    key: str
    align: Literal["left", "right"]
    ignore: bool
    hx0: float
    hx1: float
    lo: float = 0.0
    hi: float = 1.0


@dataclass
class Header:
    page: int
    line: Line
    bands: list[Band]
    currency_raw: str | None = None

    def keys(self) -> set[str]:
        return {b.key for b in self.bands if not b.ignore}


def find_header(
    page: PageView,
    coldefs: Sequence[ColDef],
    accept: Callable[[set[str]], bool],
    *,
    start_line: int = 0,
) -> Header | None:
    """The first line of the page whose words map onto ``coldefs`` and satisfy ``accept``."""
    compiled = [(c, re.compile(c.pattern, re.I)) for c in coldefs]
    for line in page.lines:
        if line.index < start_line:
            continue
        words = [w for w in line.words if w.text.strip()]
        currency: str | None = None
        kept: list[Word] = []
        cur_tokens: list[tuple[int, Word]] = []  # (index into kept of the word before it, token)
        for w in words:
            if _CURRENCY_TOKEN.match(w.text.strip()):
                currency = currency or w.text.strip("() ")
                cur_tokens.append((len(kept) - 1, w))
                continue
            kept.append(w)
        bands: list[Band] = []
        i = 0
        while i < len(kept):
            matched = None
            for n in (3, 2, 1):
                if i + n > len(kept):
                    continue
                joined = " ".join(w.text.strip() for w in kept[i : i + n])
                joined = re.sub(r"\s*\(.*?\)\s*$", "", joined).strip()
                for cd, rx in compiled:
                    if rx.fullmatch(joined):
                        matched = (cd, n)
                        break
                if matched:
                    break
            if matched:
                cd, n = matched
                span = kept[i : i + n]
                bands.append(
                    Band(
                        cd.key,
                        cd.align,
                        cd.ignore,
                        min(w.x for w in span),
                        max(w.x + w.w for w in span),
                    )
                )
                i += n
            else:
                w = kept[i]
                bands.append(Band("_unknown", "left", True, w.x, w.x + w.w))
                i += 1
        for idx, tok in cur_tokens:  # "Amount (S$)": the figures align with the end of "(S$)"
            if idx < 0 or idx >= len(kept):
                continue
            prev = kept[idx]
            for b in bands:
                if abs(b.hx1 - (prev.x + prev.w)) < 1e-9:
                    b.hx1 = max(b.hx1, tok.x + tok.w)
        named = [b.key for b in bands if not b.ignore]
        if len(set(named)) == len(named) and accept(set(named)):
            return Header(page.page, line, bands, currency)
    return None


@dataclass
class Tok:
    words: list[Word]

    @property
    def text(self) -> str:
        return words_text(self.words)

    @property
    def x0(self) -> float:
        return min(w.x for w in self.words)

    @property
    def x1(self) -> float:
        return max(w.x + w.w for w in self.words)


def _tokens(line: Line, wpt: float) -> list[Tok]:
    """Words of a line; a CR/DR suffix is merged into the amount before it."""
    toks: list[Tok] = []
    for w in line.words:
        if not w.text.strip():
            continue
        if (
            toks
            and _SUFFIX_WORD.match(w.text.strip())
            and is_amount_text(toks[-1].words[-1].text)
            and (w.x - toks[-1].x1) * wpt < 14
        ):
            toks[-1].words.append(w)
        else:
            toks.append(Tok([w]))
    return toks


def _amountish(tok: Tok) -> bool:
    first = tok.words[0].text.strip()
    if len(tok.words) > 1:
        return is_amount_text(first)
    return is_amount_text(first) or bool(_DASH.match(first))


def _fit_bands(header: Header, body: list[Line], wpt: float) -> list[Band]:
    """Content bounds per band from the header and from the amounts aligned under it."""
    bands = sorted((b for b in header.bands), key=lambda b: b.hx0)
    tolr = max(0.012, 12 / wpt)
    rights = [b for b in bands if b.align == "right"]
    for b in rights:
        xs = []
        for ln in body:
            for t in _tokens(ln, wpt):
                if _amountish(t) and abs(t.x1 - b.hx1) <= tolr:
                    xs.append(t.x0)
        b.lo = min(xs + [b.hx0]) - 0.004
        b.hi = b.hx1 + tolr
    for i, b in enumerate(bands):
        if b.align == "left":
            b.lo = b.hx0 - tolr
            nxt = bands[i + 1] if i + 1 < len(bands) else None
            if nxt is None:
                b.hi = 1.0
            elif nxt.align == "right":
                b.hi = nxt.lo
            else:
                b.hi = nxt.hx0 - tolr
    return bands


def assign(
    line: Line, bands: list[Band], wpt: float, flow_key: str | None = None
) -> dict[str, list[Word]]:
    """Words of a body line per column key (ignored columns are dropped).

    Amount-looking tokens go to the right-aligned column whose right edge they sit on. Text flows:
    a word that follows a ``flow_key`` (description) word with only a space between them stays in
    that column even if it runs into the band of an ignored column such as ``Cheque``."""
    tolr = max(0.012, 12 / wpt)
    out: dict[str, list[Word]] = {}
    prev: tuple[str, Tok] | None = None
    for tok in _tokens(line, wpt):
        band: Band | None = None
        if _amountish(tok):
            cands = [b for b in bands if b.align == "right" and abs(tok.x1 - b.hx1) <= tolr]
            if cands:
                band = min(cands, key=lambda b: abs(tok.x1 - b.hx1))
        if (
            band is None
            and flow_key is not None
            and prev is not None
            and prev[0] == flow_key
            and (tok.x0 - prev[1].x1) * wpt <= FLOW_GAP_PT
        ):
            out.setdefault(flow_key, []).extend(tok.words)
            prev = (flow_key, tok)
            continue
        if band is None:
            lefts = [b for b in bands if b.align == "left" and b.lo <= tok.x0 < b.hi]
            if lefts:
                band = max(lefts, key=lambda b: b.lo)
        if band is None:
            rights = [b for b in bands if b.align == "right" and b.lo <= tok.x1 <= b.hi]
            if rights:
                band = min(rights, key=lambda b: abs(tok.x1 - b.hx1))
        if band is None or band.ignore:
            prev = None
            continue
        out.setdefault(band.key, []).extend(tok.words)
        prev = (band.key, tok)
    for words in out.values():
        words.sort(key=lambda w: w.x)
    return out


@dataclass
class ParsedRow:
    page: int
    cols: dict[str, list[Word]]
    lines: list[Line]


@dataclass
class ParsedTable:
    rows: list[ParsedRow] = field(default_factory=list)
    specials: list[tuple[str, int, dict[str, list[Word]], Line]] = field(default_factory=list)
    header_pages: list[int] = field(default_factory=list)
    currency_raw: str | None = None
    stop_at: tuple[int, int] | None = None  # (page, line index) of the first stop line


def parse_table(
    view: DocView,
    coldefs: Sequence[ColDef],
    *,
    accept: Callable[[set[str]], bool],
    is_row_start: Callable[[dict[str, list[Word]]], bool],
    special: Callable[[Line, dict[str, list[Word]]], str | None] | None = None,
    stop: Callable[[Line, dict[str, list[Word]]], bool] | None = None,
    wrap_key: str = "description",
    footer: re.Pattern[str] | None = None,
    pages: Sequence[PageView] | None = None,
) -> ParsedTable:
    """Parse one logical table across the pages of the view (header repeated or not)."""
    result = ParsedTable()
    bands: list[Band] | None = None
    for page in pages if pages is not None else view.pages:
        header = find_header(page, coldefs, accept)
        body_start = 0
        if header is not None:
            result.header_pages.append(page.page)
            result.currency_raw = result.currency_raw or header.currency_raw
            body = [ln for ln in page.lines if ln.index > header.line.index]
            bands = _fit_bands(header, body, page.wpt)
            view.claimed.add((page.page, header.line.index))
            body_start = header.line.index + 1
        elif bands is None:
            continue
        else:  # continuation page without a repeated header: re-fit on this page's amounts
            hdr = Header(page.page, page.lines[0], [Band(**b.__dict__) for b in bands])
            bands = _fit_bands(hdr, list(page.lines), page.wpt)
        lines = [ln for ln in page.lines if ln.index >= body_start]
        current: ParsedRow | None = None
        pitches: list[float] = []
        prev_cy: float | None = None
        for ln in lines:
            cols = assign(ln, bands, page.wpt, wrap_key)
            if footer is not None and footer.search(ln.text):
                current = None
                continue
            if stop is not None and stop(ln, cols):
                result.stop_at = result.stop_at or (page.page, ln.index)
                break
            tag = special(ln, cols) if special is not None else None
            if tag is not None:
                result.specials.append((tag, page.page, cols, ln))
                view.claimed.add((page.page, ln.index))
                current = None
                prev_cy = ln.cy
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
                limit = 1.9 * ln.height
                if pitches:
                    limit = max(limit, 1.05 * median(pitches))
                if (ln.cy - prev_cy if prev_cy is not None else 1.0) <= limit:
                    current.cols.setdefault(wrap_key, []).extend(cols[wrap_key])
                    current.lines.append(ln)
                    view.claimed.add((page.page, ln.index))
                    prev_cy = ln.cy
                    continue
            current = None if not cols else current
            prev_cy = ln.cy
    return result


# --------------------------------------------------------------------------------------
# Base class
# --------------------------------------------------------------------------------------


def materialise(doc_type: str) -> tuple[dict[str, Cell], dict[str, Table]]:
    """Every schema key as an empty ``Cell`` and every table with its columns and no rows."""
    schema = DOC_SCHEMAS[doc_type]
    fields = {
        k: Cell(value=None, kind=spec.kind, source="text_layer")
        for k, spec in schema.fields.items()
    }
    tables = {
        name: Table(name=name, columns=list(spec.columns)) for name, spec in schema.tables.items()
    }
    return fields, tables


def blank_row(doc_type: str, table: str, index: int) -> Row:
    spec = DOC_SCHEMAS[doc_type].tables[table]
    return Row(
        index=index,
        cells={
            c: Cell(value=None, kind=s.kind, source="text_layer") for c, s in spec.columns.items()
        },
    )


def row_box(cells: Iterable[Cell]) -> Region | None:
    boxes = [c.box for c in cells if c.box is not None]
    if not boxes:
        return None
    page = boxes[0].page
    same = [b for b in boxes if b.page == page]
    x0 = min(b.x for b in same)
    y0 = min(b.y for b in same)
    x1 = min(1.0, max(b.x + b.w for b in same))
    y1 = min(1.0, max(b.y + b.h for b in same))
    return Region(
        file_id=same[0].file_id, page=page, x=x0, y=y0, w=max(x1 - x0, 1e-4), h=max(y1 - y0, 1e-4)
    )


class TextLayerExtractor:
    """Base class of every deterministic extractor.

    Subclasses set ``doc_types``, ``name``/``version`` and implement ``parse(view, fields, tables)``
    (fill the pre-materialised dicts; add warnings with ``view.warn``). Raise
    ``LayoutNotRecognised`` when the structure cannot be found.
    """

    doc_types: ClassVar[frozenset[str]] = frozenset()
    backend: ClassVar[Literal["text_layer", "llm"]] = "text_layer"
    name: ClassVar[str] = "text_layer"
    version: ClassVar[str] = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        raise NotImplementedError

    def parse_document(self, doc: Document, ctx: CaseContext) -> ExtractionResult:
        """Raw parse (no post-processing): values exactly as the text layer prints them."""
        view = build_view(doc, ctx)
        if not view.pages:
            raise LayoutNotRecognised("no text layer words on the document's pages")
        fields, tables = materialise(doc.doc_type)
        self.parse(view, fields, tables)
        for name, table in tables.items():  # a parser that did not say gets the row box page
            view.row_pages.setdefault(
                name, [r.box.page if r.box else doc.page_start for r in table.rows]
            )
        return ExtractionResult(
            fields=fields,
            tables=tables,
            warnings=list(view.warnings),
            row_pages=view.row_pages,
            field_pages={k: c.box.page for k, c in fields.items() if c.box is not None},
        )

    def extract(self, doc: Document, ctx: CaseContext) -> ExtractionResult:
        from app.extract.registry import get_postprocessor

        result = self.parse_document(doc, ctx)
        post = get_postprocessor(doc.doc_type)
        if post is not None:
            post(result, ctx, doc)
        return result
