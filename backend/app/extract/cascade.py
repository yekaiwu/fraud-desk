"""Cheap-then-strong extraction cascade (docs/ingest-and-extraction.md 6.6, cost lever 5).

Order of work for one document, mode ``GLDB_EXTRACTOR_MODE`` (``text_layer`` | ``llm`` | ``cascade``):

1. **Text layer** (free). Eligible when a text-layer extractor is registered and no page is a scan.
   Its result is validated (structure and arithmetic, ``register_validator``). A structural failure
   or ``LayoutNotRecognised`` sends the whole document to step 2; broken arithmetic on some pages
   sends only those pages to a second reader.
2. **Cheap model** over the pages that need it (all pages when there is no text-layer parse).
3. **Strong model**, per failing page, *before any flag is raised*: skipped when both model names are
   equal, or (page with a text-layer parse) when the cheap reading equals the text layer.
4. **Resolve**. A page with a text-layer parse keeps the text-layer cells (the text layer is what is
   printed); a differing model reading is kept in ``Cell.alt_raw``. Without a parse, the two model
   readings are compared and the verified one (else the strong one) wins, the other goes to
   ``alt_raw``. A reading is never chosen because it makes the arithmetic work. If the strong model
   agrees with the cheap one the mismatch stands and the check layer flags it with both readings.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.core.context import CaseContext
from app.core.models import Cell, DocStatus, Document, Row
from app.extract.base import ExtractionMeta, ExtractionResult, LayoutNotRecognised, Validation
from app.extract.llm_extractor import (
    LLMExtractor,
    PageReadError,
    PageReading,
    assemble,
    page_state,
)
from app.extract.registry import get_extractors, get_postprocessor, get_validator
from app.extract.verify import normalise_token

log = logging.getLogger("gldb.extract")

MAX_WARNINGS = 50
UNSTABLE_FRACTION = 0.10
_VALUE_KINDS = {"money", "decimal", "percent", "int", "date"}
_DERIVED_COLUMNS = {
    ("financial_statement", "line"),
    ("financial_statement", "section"),
    ("financial_statement", "is_subtotal"),
}


def cap_warnings(warnings: list[str]) -> list[str]:
    """De-duplicate, keep order, cap at 50 with one ``+N more`` entry."""
    unique = list(dict.fromkeys(warnings))
    if len(unique) <= MAX_WARNINGS:
        return unique
    return [*unique[:MAX_WARNINGS], f"+{len(unique) - MAX_WARNINGS} more"]


def _norm(raw: str | None) -> str:
    return normalise_token(" ".join((raw or "").split()))


def _same(a: Cell, b: Cell) -> bool:
    if _norm(a.raw) == _norm(b.raw):
        return True
    return a.kind in _VALUE_KINDS and a.value is not None and a.value == b.value


def _short(raw: str | None) -> str:
    return (raw or "")[:40]


@dataclass
class _Diff:
    where: str  # "transactions[17].balance", "fields.closing_balance" or "transactions[rows]"
    table: str | None  # None for a field
    row: int | None  # global row index in the text-layer result
    col: str  # column, or field key
    local: int  # row index inside the model's page reading (-1 for fields)
    n_rows: int | None = None  # set for a row-count mismatch

    def model_raw(self, reading: PageReading | None) -> str | None:
        if reading is None:
            return None
        if self.n_rows is not None:
            return f"{self.n_rows} rows"
        if self.table is None:
            cell = reading.fields.get(self.col)
        else:
            rows = reading.rows.get(self.table, [])
            cell = rows[self.local].cells.get(self.col) if 0 <= self.local < len(rows) else None
        return cell.raw if cell else None


class _Run:
    def __init__(self, doc: Document, ctx: CaseContext, render: Callable | None):
        self.doc = doc
        self.ctx = ctx
        self.render = render
        self.settings = ctx.settings
        # tolerate the short spellings used in the brief ("auto", "text")
        self.mode = {"auto": "cascade", "text": "text_layer"}.get(
            self.settings.extractor_mode, self.settings.extractor_mode
        )
        self.pages = list(range(doc.page_start, doc.page_end + 1))
        self.states = {p: page_state(doc, ctx, p) for p in self.pages}
        by_backend = get_extractors(doc.doc_type)
        self.tl = by_backend.get("text_layer")
        self.llm: LLMExtractor | None = by_backend.get("llm")
        self.validator = get_validator(doc.doc_type)
        self.warnings: list[str] = []
        self.sources: dict[int, str] = {}
        self.escalated: list[int] = []
        self.used: list[str] = []  # distinct sources in order
        self.models: dict[str, str] = {}
        self.partial = False
        self.cheap_model = getattr(ctx.llm, "model_for", lambda r: "")("cheap")
        self.strong_model = getattr(ctx.llm, "model_for", lambda r: "")("strong")

    # ------------------------------------------------------------------ helpers
    def use(self, source: str, model: str = "") -> None:
        if source not in self.used:
            self.used.append(source)
        if model:
            self.models.setdefault(source, model)

    def validate(self, result: ExtractionResult) -> Validation:
        if self.validator is None:
            return Validation()
        return self.validator(result, self.ctx, self.doc)

    def strong_skipped_same_model(self) -> bool:
        s = self.settings
        return (
            s.effective_llm_backend() in ("openai", "anthropic")
            and s.llm_model_cheap == s.llm_model_strong
        )

    def vision_fallback(self) -> bool:
        """True when a local OCR feeds the model text and a failing scanned page may be re-read
        from its image (``GLDB_LLM_VISION_FALLBACK``)."""
        return self.settings.llm_vision_fallback

    def _wants_strong_pass(self) -> bool:
        return self.vision_fallback() or not self.strong_skipped_same_model()

    def run_pages(
        self, pages: list[int], fn: Callable[[int], Any], calls_per_page: int = 2
    ) -> dict[int, Any]:
        """Run ``fn(page)`` for each page; exceptions of type PageReadError are returned as values.
        Parallel up to ``max_page_workers`` unless the call budget is tight (then sequential, so
        which pages get the last calls never depends on thread timing)."""

        def guarded(p: int) -> Any:
            try:
                return fn(p)
            except PageReadError as exc:
                return exc

        costs = self.ctx.costs
        remaining = costs.limits.max_calls - costs.calls_used
        workers = self.settings.max_page_workers
        if workers <= 1 or len(pages) <= 1 or remaining < len(pages) * calls_per_page:
            return {p: guarded(p) for p in pages}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {p: pool.submit(costs.wrap(guarded), p) for p in pages}
            return {p: futures[p].result() for p in pages}

    # ------------------------------------------------------------------ main
    def run(self) -> tuple[ExtractionResult, ExtractionMeta]:
        for p in self.pages:
            st = self.states[p]
            if st.error and not st.error.startswith("ocr_"):
                self.warnings.append(f"unreadable: page {p}: {st.error.split(':', 1)[0]}")
                self.partial = True
        tl_result: ExtractionResult | None = None
        if (
            self.mode in ("text_layer", "cascade")
            and self.tl is not None
            and not any(s.is_scan for s in self.states.values())
        ):
            tl_result = self._text_layer()
        if tl_result is not None:
            result = self._with_text_layer(tl_result)
        elif self.mode == "text_layer" or self.llm is None:
            result = self._empty()
            if self.mode != "text_layer":
                self.warnings.append(f"no_extractor: {self.doc.doc_type}")
        else:
            result = self._llm_path()
        return self._finalize(result)

    # ------------------------------------------------------------------ text layer
    def _text_layer(self) -> ExtractionResult | None:
        assert self.tl is not None
        try:
            parse = getattr(self.tl, "parse_document", None) or self.tl.extract
            result = parse(self.doc, self.ctx)
        except LayoutNotRecognised as exc:
            self.warnings.append(f"layout_not_recognised: {exc}")
            return None
        validation = self.validate(result)
        if validation.structural:
            self.warnings.append(f"layout_not_recognised: {'; '.join(validation.structural)}")
            return None
        self.use("text_layer")
        for p in self.pages:
            self.sources[p] = "text_layer"
        return result

    def _with_text_layer(self, result: ExtractionResult) -> ExtractionResult:
        validation = self.validate(result)
        self.warnings += result.warnings
        if validation.failing and self.mode == "cascade" and self.llm is not None:
            self._second_reader(result, validation)
        return result

    def _second_reader(self, result: ExtractionResult, validation: Validation) -> None:
        """Failing pages of a text-layer parse get a cheap (then strong) second reading.

        The text-layer cells stay the final cells. A model reading that differs is stored in
        ``Cell.alt_raw`` and listed in a ``reread:`` warning; the arithmetic mismatch stands."""
        assert self.llm is not None
        llm = self.llm
        failing = sorted(validation.failing)
        cheap = self.run_pages(
            failing,
            lambda p: llm.read_page(self.doc, self.ctx, p, role="cheap", render=self.render),
        )
        cheap_ok: dict[int, PageReading] = {}
        want_strong: list[int] = []
        for p in failing:
            got = cheap[p]
            if isinstance(got, PageReadError):
                self.warnings.append(f"escalation_failed: page {p}: {got.kind}")
                continue
            self.use("llm_cheap", got.model)
            cheap_ok[p] = got
            # two independent readers already agree -> the strong model adds nothing
            if self._diff(result, got, p) and not self.strong_skipped_same_model():
                want_strong.append(p)
        strong = self.run_pages(
            want_strong,
            lambda p: llm.read_page(self.doc, self.ctx, p, role="strong", render=self.render),
        )
        for p in sorted(cheap_ok):
            cheap_reading = cheap_ok[p]
            strong_reading: PageReading | None = None
            if p in strong:
                got = strong[p]
                if isinstance(got, PageReadError):
                    self.warnings.append(f"escalation_failed: page {p}: {got.kind}")
                else:
                    strong_reading = got
                    self.use("llm_strong", got.model)
            diffs = self._diff(result, strong_reading or cheap_reading, p)
            for d in diffs:
                cheap_raw = d.model_raw(cheap_reading)
                strong_raw = d.model_raw(strong_reading) if strong_reading else None
                alt = strong_raw if strong_raw is not None else cheap_raw
                if d.table is not None and d.row is not None:
                    cells = result.tables[d.table].rows[d.row].cells
                    cells[d.col] = cells[d.col].model_copy(update={"alt_raw": alt})
                elif d.table is None:
                    result.fields[d.col] = result.fields[d.col].model_copy(update={"alt_raw": alt})
                self.warnings.append(
                    f'reread: {d.where} cheap="{_short(cheap_raw)}" strong="{_short(strong_raw)}"'
                )
            tag = "text_layer_kept" if diffs else "confirmed_by_text_layer"
            self.warnings.append(f"escalated: page={p} reason={validation.failing[p]} result={tag}")
            self.escalated.append(p)

    # -- comparing a model page against the text-layer parse
    @staticmethod
    def _page_rows(result: ExtractionResult, table: str, page: int) -> list[tuple[int, Row]]:
        pages = result.row_pages.get(table, [])
        rows = result.tables[table].rows
        return [(i, rows[i]) for i in range(len(rows)) if i < len(pages) and pages[i] == page]

    def _diff(self, result: ExtractionResult, reading: PageReading, page: int) -> list[_Diff]:
        """Cells where the model reading of ``page`` differs from the text-layer cell. A model that
        returns null for a field is not a contradicting reading and is ignored."""
        diffs: list[_Diff] = []
        for tname, model_rows in reading.rows.items():
            tl_rows = self._page_rows(result, tname, page)
            if len(tl_rows) != len(model_rows):
                diffs.append(_Diff(f"{tname}[rows]", tname, None, "", -1, len(model_rows)))
                continue
            for local, ((gi, trow), mrow) in enumerate(zip(tl_rows, model_rows, strict=True)):
                for col, tcell in trow.cells.items():
                    if (self.doc.doc_type, col) in _DERIVED_COLUMNS:
                        continue
                    mcell = mrow.cells.get(col)
                    if mcell is None or (tcell.raw is None and mcell.raw is None):
                        continue
                    if not _same(tcell, mcell):
                        diffs.append(_Diff(f"{tname}[{gi}].{col}", tname, gi, col, local))
        for key, tcell in result.fields.items():
            if result.field_pages.get(key) != page or tcell.source != "text_layer":
                continue
            mcell = reading.fields.get(key)
            if mcell is None or mcell.raw is None or (self.doc.doc_type, key) in _DERIVED_COLUMNS:
                continue
            if not _same(tcell, mcell):
                diffs.append(_Diff(f"fields.{key}", None, None, key, -1))
        return diffs

    # ------------------------------------------------------------------ LLM path
    def _llm_path(self) -> ExtractionResult:
        assert self.llm is not None
        llm = self.llm
        readable = [
            p
            for p in self.pages
            if not (self.states[p].error and not self.states[p].error.startswith("ocr_"))
        ]
        got = self.run_pages(
            readable,
            lambda p: llm.read_page(self.doc, self.ctx, p, role="cheap", render=self.render),
        )
        readings: dict[int, PageReading] = {}
        for p in readable:
            r = got[p]
            if isinstance(r, PageReadError):
                self.warnings.append(f"unreadable: page {p}: {r.kind}")
                self.partial = True
                continue
            readings[p] = r
            self.use("llm_cheap", r.model)
            self.sources[p] = "llm_cheap"
        if any(not r.has_reader for r in readings.values()):
            self.warnings.append("unverified_no_reader")
        if self.mode == "cascade" and readings and self._wants_strong_pass():
            self._strong_pass(readings)
        result = assemble(self.doc, readings)
        self.warnings += result.warnings
        return result

    def _strong_pass(self, readings: dict[int, PageReading]) -> None:
        assert self.llm is not None
        llm = self.llm
        failing = self.validate(assemble(self.doc, readings)).failing
        pages = sorted(p for p in failing if p in readings)
        # With one model for both roles the second reading only helps when it sees the image: the
        # vision fallback re-reads the failing pages that are scans, from their pixels.
        image_pass = self.vision_fallback()
        if image_pass:
            pages = [p for p in pages if self.states[p].is_scan]
        if not pages:
            return
        got = self.run_pages(
            pages,
            lambda p: llm.read_page(
                self.doc,
                self.ctx,
                p,
                role="strong",
                render=self.render,
                allow_images=image_pass,
            ),
        )
        replaced: dict[int, str] = {}
        for p in pages:
            s = got[p]
            if isinstance(s, PageReadError):
                self.warnings.append(f"escalation_failed: page {p}: {s.kind}")
                continue
            self.use("llm_strong", s.model)
            merged, tag, changed, total = merge_cheap_strong(
                readings[p], s, prefer_strong=image_pass
            )
            readings[p] = merged
            self.sources[p] = "llm_strong"
            replaced[p] = tag
            self.escalated.append(p)
            if total and changed / total > UNSTABLE_FRACTION:
                self.warnings.append("page_unstable")
                self.partial = True
        after = self.validate(assemble(self.doc, readings)).failing
        for p in pages:
            if p not in replaced:
                continue
            tag = replaced[p]
            if tag != "row_count_changed":
                tag = "confirmed_by_strong" if p in after else "resolved"
            self.warnings.append(f"escalated: page={p} reason={failing[p]} result={tag}")

    # ------------------------------------------------------------------ finishing
    def _empty(self) -> ExtractionResult:
        return assemble(self.doc, {})

    def _finalize(self, result: ExtractionResult) -> tuple[ExtractionResult, ExtractionMeta]:
        post = get_postprocessor(self.doc.doc_type)
        if post is not None:
            post(result, self.ctx, self.doc)
        validation = self.validate(result)
        for key in validation.missing_required:
            self.warnings.append(f"missing: fields.{key}")
        result.warnings = cap_warnings([*self.warnings, *result.warnings])
        nothing = not any(
            c.value is not None or c.raw is not None for c in result.fields.values()
        ) and not any(t.rows for t in result.tables.values())
        status: DocStatus
        if nothing:
            status = "failed"
        elif self.partial or validation.missing_required:
            status = "partial"
        else:
            status = "ok"
        meta = ExtractionMeta(
            extractor=self._extractor_name(),
            status=status,
            sources_by_page=dict(self.sources),
            pages_escalated=sorted(set(self.escalated)),
        )
        return result, meta

    def _extractor_name(self) -> str:
        if not self.used:
            return ""

        def single(source: str) -> str:
            if source == "text_layer":
                return f"text_layer@{getattr(self.tl, 'version', '1')}"
            return f"llm:{self.models.get(source) or self.cheap_model or 'unknown'}"

        if len(self.used) == 1:
            return single(self.used[0])
        order = ("text_layer", "llm_cheap", "llm_strong")
        return "cascade(" + ">".join(s for s in order if s in self.used) + ")"


# --------------------------------------------------------------------------------------
# cheap vs strong
# --------------------------------------------------------------------------------------


def merge_cheap_strong(
    cheap: PageReading, strong: PageReading, *, prefer_strong: bool = False
) -> tuple[PageReading, str, int, int]:
    """Compare two model readings of one page.

    Returns ``(merged, tag, changed_cells, total_cells)``. Row counts differ in any table: the strong
    page is taken whole (``row_count_changed``, no per-cell ``alt_raw``). Otherwise per cell: same
    raw keeps the strong cell; different raw picks the verified one (else the strong one) and stores
    the other raw in ``alt_raw`` with a ``reread`` note. ``prefer_strong``: the strong reading looked
    at the image while the cheap one read OCR text, so on a difference the strong cell wins even
    though the OCR words cannot verify it (the cheap value is kept in ``alt_raw``)."""
    if any(
        len(cheap.rows.get(t, [])) != len(strong.rows.get(t, []))
        for t in {*cheap.rows, *strong.rows}
    ):
        return strong, "row_count_changed", 0, max(cheap.cell_count(), strong.cell_count())
    merged = PageReading(
        page=strong.page,
        role="strong",
        model=strong.model,
        mode=strong.mode,
        fields={},
        rows={},
        has_reader=strong.has_reader,
        page_marker=strong.page_marker or cheap.page_marker,
        marker_box=strong.marker_box or cheap.marker_box,
        notes=list(dict.fromkeys([*cheap.notes, *strong.notes])),
    )
    changed = 0
    total = 0

    def choose(c: Cell, s: Cell) -> tuple[Cell, bool]:
        nonlocal total
        if c.raw is None and s.raw is None:
            return s, False
        total += 1
        if _same(c, s):
            return s, False
        pick_cheap = not prefer_strong and c.verified is True and s.verified is not True
        chosen, other = (c, s) if pick_cheap else (s, c)
        return chosen.model_copy(update={"alt_raw": other.raw}), True

    def warns(reading: PageReading, table: str | None, row: int | None, key: str):  # noqa: ANN202
        return [w for w in reading.warnings if w.table == table and w.row == row and w.key == key]

    for key, scell in strong.fields.items():
        ccell = cheap.fields[key]
        cell, diff = choose(ccell, scell)
        merged.fields[key] = cell
        src = cheap if cell.source == "llm_cheap" and diff and cell.raw == ccell.raw else strong
        merged.warnings += warns(src, None, None, key)
        if diff:
            changed += 1
            merged.rereads.append((None, 0, key, ccell.raw, scell.raw))
    for tname, srows in strong.rows.items():
        out_rows: list[Row] = []
        for i, srow in enumerate(srows):
            crow = cheap.rows[tname][i]
            cells = {}
            for col, scell in srow.cells.items():
                ccell = crow.cells[col]
                cell, diff = choose(ccell, scell)
                cells[col] = cell
                src = cheap if diff and cell.raw == ccell.raw else strong
                merged.warnings += warns(src, tname, i, col)
                if diff:
                    changed += 1
                    merged.rereads.append((tname, i, col, ccell.raw, scell.raw))
            out_rows.append(srow.model_copy(update={"cells": cells}))
        merged.rows[tname] = out_rows
    tag = "resolved" if changed else "confirmed_by_strong"
    return merged, tag, changed, total


def extract_document(
    doc: Document, ctx: CaseContext, *, render: Callable | None = None
) -> tuple[ExtractionResult, ExtractionMeta]:
    """Extract one document with the configured cascade. Never raises for a page or document problem."""
    return _Run(doc, ctx, render).run()
