"""LLM backend: one class registered for every document type that has a schema (6.4).

* Unit of work is the **page**. A page with words goes to the model as text only (cost lever 4); a
  scan goes as a grayscale image, unless the model is text-only (``GLDB_LLM_SUPPORTS_VISION=false``):
  then a scan is read from its local-OCR words and no image part is ever built. The system prompt is fixed per document type and holds no
  document text; the page text is wrapped by ``wrap_untrusted``.
* The model returns raw strings only (``response_model_for``); every value is canonicalised here and
  then **verified** against the page's independent reading (PyMuPDF words or OCR words). A value the
  reader cannot find is kept as read with ``verified=False``; it is never replaced.
* Failures are values, not exceptions: ``read_page`` raises ``PageReadError`` with a short kind
  (``no_fixture``, ``schema``, ``transport``, ``budget_exceeded``, ``render_unavailable``,
  ``page_error``) that the cascade turns into ``unreadable:`` / ``escalation_failed:`` warnings.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.core.context import CaseContext
from app.core.costs import LLMBudgetExceeded
from app.core.llm import (
    UNTRUSTED_MAX_CHARS,
    LLMNoFixture,
    LLMRequest,
    LLMSchemaError,
    LLMTransportError,
    register_prompt,
    wrap_untrusted,
)
from app.core.models import Cell, CellKind, Document, Row, Table, Word
from app.core.schemas import (
    DOC_SCHEMAS,
    EXTRACTABLE_TYPES,
    response_json_schema,
    response_model_for,
)
from app.core.textlayout import aligned_text, layout_text, page_marker
from app.extract.base import ExtractionResult
from app.extract.normalise import format_warning, make_cell
from app.extract.prompts.common import build_system_prompt
from app.extract.registry import (
    get_postprocessor,
    has_extractor,
    register_extractor,
)
from app.extract.text_layer import blank_row, page_words_for, row_box
from app.extract.verify import PageVerifier

log = logging.getLogger("gldb.extract")

Role = Literal["cheap", "strong"]
_SOURCE = {"cheap": "llm_cheap", "strong": "llm_strong"}
_NOT_VERIFIED_KINDS = {"bool"}  # derived by us, never printed
_MAGNITUDE_COLUMNS = {("bank_statement", "transactions"): {"debit", "credit"}}


class PageReadError(Exception):
    def __init__(self, kind: str, message: str = ""):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind
        self.message = message


@dataclass
class PageState:
    page: int
    words: list[Word]
    is_scan: bool
    has_text_layer: bool
    error: str | None


def page_state(doc: Document, ctx: CaseContext, page: int) -> PageState:
    words = page_words_for(doc, ctx, page)
    is_scan, has_text, error = False, bool(words), None
    try:
        for info in ctx.file(doc.file_id).pages:
            if info.page == page:
                is_scan, has_text, error = info.is_scan, info.has_text_layer, info.error
                break
    except KeyError:
        pass
    return PageState(page, words, is_scan, has_text, error)


@dataclass
class CellWarn:
    code: str  # unverified | unparsed | negative_in_amount_column
    table: str | None
    row: int | None  # local row index within the page reading
    key: str
    raw: str | None

    def text(self, row_offset: int = 0) -> str:
        where = (
            f"fields.{self.key}"
            if self.table is None
            else f"{self.table}[{(self.row or 0) + row_offset}].{self.key}"
        )
        return format_warning(self.code, where, self.raw)


@dataclass
class PageReading:
    page: int
    role: Role
    model: str
    mode: str
    fields: dict[str, Cell]
    rows: dict[str, list[Row]]
    warnings: list[CellWarn] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    page_marker: str | None = None
    marker_box: Cell | None = None
    has_reader: bool = True
    # (table or None, local row, key, cheap raw, strong raw): filled when a strong re-read changed a cell
    rereads: list[tuple[str | None, int, str, str | None, str | None]] = field(default_factory=list)

    def cell_count(self) -> int:
        n = sum(1 for c in self.fields.values() if c.raw is not None)
        return n + sum(
            1
            for rows in self.rows.values()
            for r in rows
            for c in r.cells.values()
            if c.raw is not None
        )


# --------------------------------------------------------------------------------------
# Building a reading from the model's JSON
# --------------------------------------------------------------------------------------


def build_reading(
    doc: Document,
    page: int,
    data: dict,
    *,
    role: Role,
    model: str,
    mode: str,
    words: list[Word],
) -> PageReading:
    schema = DOC_SCHEMAS[doc.doc_type]
    source = _SOURCE[role]
    verifier = PageVerifier(words, doc.file_id, page)
    reading = PageReading(
        page=page,
        role=role,
        model=model,
        mode=mode,
        fields={},
        rows={},
        has_reader=verifier.has_reader,
    )

    def cell_for(
        raw: str | None,
        kind: CellKind,
        values: list[str] | None,
        *,
        table: str | None,
        row: int | None,
        key: str,
        cursor: bool,
        verify: bool = True,
        magnitude: bool = False,
    ) -> Cell:
        text = None if raw is None else " ".join(raw.split())
        if not text:
            return Cell(value=None, kind=kind, source=source)  # type: ignore[arg-type]
        verified: bool | None = None
        box = None
        if verify and kind not in _NOT_VERIFIED_KINDS:
            v = verifier.check(text, kind, use_cursor=cursor)
            verified, box = v.verified, v.box
        cell, warn = make_cell(
            text,
            kind,
            source=source,  # type: ignore[arg-type]
            box=box,
            verified=verified,
            values=values,
            magnitude=magnitude,
        )
        if warn:
            reading.warnings.append(CellWarn(warn, table, row, key, text))
        if verified is False:
            reading.warnings.append(CellWarn("unverified", table, row, key, text))
        return cell

    fdata = data.get("fields", {})
    for key, spec in schema.fields.items():
        reading.fields[key] = cell_for(
            fdata.get(key),
            spec.kind,
            spec.values,
            table=None,
            row=None,
            key=key,
            cursor=False,
            # derived from the page markers by assemble(), never printed as such
            verify=not (doc.doc_type == "bank_statement" and key == "page_x_of_y"),
        )

    magnitude_cols = _MAGNITUDE_COLUMNS
    for tname, tspec in schema.tables.items():
        if doc.doc_type == "bank_statement" and tname == "page_chain":
            continue  # built from page_marker and the account number field by assemble()
        verifier.reset_cursor()
        out: list[Row] = []
        for raw_row in data.get("tables", {}).get(tname, []):
            if all((raw_row.get(c) is None or not str(raw_row.get(c)).strip()) for c in raw_row):
                continue  # padding row: nothing printed
            i = len(out)
            row = blank_row(doc.doc_type, tname, i)
            for col, cspec in tspec.columns.items():
                fs_derived = doc.doc_type == "financial_statement" and col in ("line", "section")
                if doc.doc_type == "financial_statement" and col == "is_subtotal":
                    continue
                row.cells[col] = cell_for(
                    raw_row.get(col),
                    cspec.kind,
                    cspec.values,
                    table=tname,
                    row=i,
                    key=col,
                    cursor=True,
                    verify=not fs_derived,
                    magnitude=col in magnitude_cols.get((doc.doc_type, tname), ()),
                )
            row.box = row_box(row.cells.values())
            out.append(row)
        reading.rows[tname] = out
    if doc.doc_type == "bank_statement":
        marker = data.get("page_marker")
        if isinstance(marker, str) and marker.strip():
            reading.page_marker = " ".join(marker.split())
            v = verifier.check(reading.page_marker, "text", use_cursor=False)
            reading.marker_box = Cell(
                value=reading.page_marker,
                raw=reading.page_marker,
                kind="text",
                box=v.box,
                source=source,  # type: ignore[arg-type]
                verified=v.verified,
            )
    return reading


# --------------------------------------------------------------------------------------
# Merging page readings into one document result
# --------------------------------------------------------------------------------------


def assemble(doc: Document, readings: dict[int, PageReading]) -> ExtractionResult:
    """Fields: first non-null value by page order. Tables: rows concatenated in page order and
    renumbered 0-based; ``Table.pages`` lists the contributing pages."""
    schema = DOC_SCHEMAS[doc.doc_type]
    fields: dict[str, Cell] = {
        k: Cell(value=None, kind=s.kind, source="llm_cheap") for k, s in schema.fields.items()
    }
    tables = {n: Table(name=n, columns=list(s.columns)) for n, s in schema.tables.items()}
    warnings: list[str] = []
    row_pages: dict[str, list[int]] = {n: [] for n in schema.tables}
    field_pages: dict[str, int] = {}
    for page in sorted(readings):
        reading = readings[page]
        for key, cell in reading.fields.items():
            if fields[key].raw is None and cell.raw is not None:
                fields[key] = cell
                field_pages[key] = page
                warnings += [w.text() for w in reading.warnings if w.table is None and w.key == key]
        for tname, rows in reading.rows.items():
            table = tables[tname]
            offset = len(table.rows)
            if rows:
                table.pages.append(page)
            for row in rows:
                table.rows.append(row.model_copy(update={"index": len(table.rows)}))
                row_pages[tname].append(page)
            warnings += [w.text(offset) for w in reading.warnings if w.table == tname]
            warnings += [
                _reread_text(f"{tname}[{r + offset}].{k}", c, st)
                for t, r, k, c, st in reading.rereads
                if t == tname
            ]
        warnings += [
            _reread_text(f"fields.{k}", c, st)
            for t, _r, k, c, st in reading.rereads
            if t is None and fields[k] is reading.fields.get(k)
        ]
        warnings += reading.notes
    if doc.doc_type == "bank_statement":
        _bank_page_chain(doc, readings, fields, tables, warnings, row_pages)
    return ExtractionResult(
        fields=fields,
        tables=tables,
        warnings=list(dict.fromkeys(warnings)),
        row_pages=row_pages,
        field_pages=field_pages,
    )


def _reread_text(where: str, cheap: str | None, strong: str | None) -> str:
    return f'reread: {where} cheap="{(cheap or "")[:40]}" strong="{(strong or "")[:40]}"'


def _bank_page_chain(
    doc: Document,
    readings: dict[int, PageReading],
    fields: dict[str, Cell],
    tables: dict[str, Table],
    warnings: list[str],
    row_pages: dict[str, list[int]],
) -> None:
    table = tables["page_chain"]
    markers: list[str] = []
    for page in sorted(readings):
        reading = readings[page]
        acct = reading.fields.get("account_number")
        marker = page_marker(reading.page_marker) if reading.page_marker else None
        if marker is None and (acct is None or acct.raw is None):
            if reading.page_marker:
                warnings.append(
                    format_warning("unparsed", "page_chain.page_x", reading.page_marker)
                )
            continue
        row = blank_row("bank_statement", "page_chain", len(table.rows))
        row.cells["page"] = Cell(value=str(page), kind="int", source="derived")
        if marker is not None and reading.marker_box is not None:
            mc = reading.marker_box
            for key, num in (("page_x", marker[0]), ("page_y", marker[1])):
                row.cells[key] = Cell(
                    value=str(num),
                    raw=mc.raw,
                    kind="int",
                    box=mc.box,
                    source=mc.source,
                    verified=mc.verified,
                )
            markers.append(f"{marker[0]}/{marker[1]}")
            if mc.verified is False:
                warnings.append(
                    format_warning("unverified", f"page_chain[{row.index}].page_x", mc.raw)
                )
        elif reading.page_marker:
            warnings.append(format_warning("unparsed", "page_chain.page_x", reading.page_marker))
        if acct is not None and acct.raw is not None:
            row.cells["account_number"] = acct
        row.box = row_box(row.cells.values())
        table.rows.append(row)
        table.pages.append(page)
        row_pages["page_chain"].append(page)
    if markers:
        fields["page_x_of_y"] = Cell(value=",".join(markers), kind="text", source="derived")


# --------------------------------------------------------------------------------------
# The extractor
# --------------------------------------------------------------------------------------


def system_prompt_for(doc_type: str) -> str:
    return build_system_prompt(doc_type)


def register_all_prompts() -> None:
    """Register ``extract:<type>`` for every type that has an LLM extractor (provenance hashes them)."""
    for doc_type in EXTRACTABLE_TYPES:
        if has_extractor(doc_type):
            try:
                register_prompt(f"extract:{doc_type}", system_prompt_for(doc_type))
            except ValueError:  # pragma: no cover - hints registered after first use
                log.warning("prompt for extract:%s changed after registration", doc_type)


RenderFn = Callable[..., object]


@register_extractor(doc_types=set(EXTRACTABLE_TYPES), backend="llm")
class LLMExtractor:
    doc_types = frozenset(EXTRACTABLE_TYPES)
    backend = "llm"
    name = "llm"
    version = "1"

    def __init__(self, render: RenderFn | None = None):
        self.render = render

    # ------------------------------------------------------------------ one page
    def read_page(
        self,
        doc: Document,
        ctx: CaseContext,
        page: int,
        *,
        role: Role = "cheap",
        render: RenderFn | None = None,
        allow_images: bool = False,
    ) -> PageReading:
        state = page_state(doc, ctx, page)
        if state.error and not state.error.startswith("ocr_"):
            raise PageReadError("page_error", state.error.split(":", 1)[0])
        # A text-only model (GLDB_LLM_SUPPORTS_VISION=false) never gets an image: a scan is read
        # from its OCR words (ingest/ocr.py), and a scan with no OCR words is unreadable.
        # ``allow_images`` is the vision fallback: a model that reads OCR text may look at the image
        # of one page that failed its arithmetic (settings.llm_vision_fallback, cascade.py)
        text_only_model = not (ctx.settings.llm_supports_vision or allow_images)
        vision = (state.is_scan or not state.words) and not (text_only_model and state.words)
        notes: list[str] = []
        images: list[bytes] = []
        n_pages = doc.page_end - doc.page_start + 1
        label = f"page {page - doc.page_start + 1} of {n_pages}"
        if vision:
            if not state.is_scan:
                raise PageReadError("no_text", "page has neither a text layer nor an image")
            if text_only_model:
                raise PageReadError(
                    "no_text", "scanned page has no OCR text and the model is text-only"
                )
            dpi = ctx.settings.vision_dpi
            images = [self._render(doc, ctx, page, dpi, render or self.render)]
            mode = f"vision:{dpi}"
            body = "(the page is in the attached image)"
        else:
            mode = "ocr_text" if state.is_scan else "text"
            # OCR text keeps column positions: a model with no image cannot otherwise tell a debit
            # column from a credit column
            text = aligned_text(state.words) if state.is_scan else layout_text(state.words)
            if len(text) > UNTRUSTED_MAX_CHARS:
                notes.append(f"text_truncated: page {page}")
            body = text
        sha = ctx.file(doc.file_id).sha256
        try:
            wrapped = wrap_untrusted(body, label=label)
        except ValueError as exc:  # the page text carries its own end marker: never send it
            raise PageReadError("unsafe_text", "page text contains a forged end marker") from exc
        req = LLMRequest(
            purpose=f"extract:{doc.doc_type}",
            model_role=role,
            system=system_prompt_for(doc.doc_type),
            user=wrapped,
            images=images,
            schema_name=f"extract_{doc.doc_type}",
            json_schema=response_json_schema(doc.doc_type),
            cache_extra=f"{sha}:{page}:{mode}",
        )
        try:
            with ctx.costs.scope(stage="extract", document_id=doc.id, page=page):
                result = ctx.llm.complete_json(req, ctx_costs=ctx.costs)
        except LLMBudgetExceeded as exc:
            raise PageReadError("budget_exceeded", str(exc)) from exc
        except LLMNoFixture as exc:
            raise PageReadError("no_fixture", str(exc)) from exc
        except LLMSchemaError as exc:
            raise PageReadError("schema", str(exc)[:200]) from exc
        except LLMTransportError as exc:
            raise PageReadError("transport", str(exc)[:200]) from exc
        try:
            response_model_for(doc.doc_type).model_validate(result.data)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            raise PageReadError("schema", f"{first.get('loc')}: {first.get('msg')}"[:200]) from exc
        reading = build_reading(
            doc,
            page,
            result.data,
            role=role,
            model=result.model,
            mode=mode,
            words=state.words,
        )
        reading.notes += notes
        return reading

    @staticmethod
    def _render(
        doc: Document, ctx: CaseContext, page: int, dpi: int, render: RenderFn | None
    ) -> bytes:
        fn = render
        if fn is None:
            try:
                from app.ingest.render import render_for_model as fn  # type: ignore[no-redef]
            except ImportError as exc:
                raise PageReadError(
                    "render_unavailable", "app.ingest.render is not available"
                ) from exc
        ref = ctx.file(doc.file_id)
        try:
            image = fn(Path(ctx.file_path(doc.file_id)), ref.mime, page, dpi=dpi)  # type: ignore[misc]
        except Exception as exc:  # a render problem is local to the page
            raise PageReadError("render_failed", type(exc).__name__) from exc
        png = getattr(image, "png", image)
        if not isinstance(png, bytes):
            raise PageReadError("render_failed", "renderer returned no PNG bytes")
        return png

    # ------------------------------------------------------------------ protocol
    def extract(self, doc: Document, ctx: CaseContext) -> ExtractionResult:
        """Cheap read of every page, no cascade. Failures become ``unreadable:`` warnings."""
        readings: dict[int, PageReading] = {}
        warnings: list[str] = []
        for page in range(doc.page_start, doc.page_end + 1):
            try:
                readings[page] = self.read_page(doc, ctx, page)
            except PageReadError as exc:
                warnings.append(f"unreadable: page {page}: {exc.kind}")
        result = assemble(doc, readings)
        result.warnings = list(dict.fromkeys([*result.warnings, *warnings]))
        post = get_postprocessor(doc.doc_type)
        if post is not None:
            post(result, ctx, doc)
        return result
