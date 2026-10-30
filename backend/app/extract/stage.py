"""Extract stage (docs/ingest-and-extraction.md 6.1, architecture.md section 3).

Per document in id order: materialise every schema key, route to the cascade, write ``fields``,
``tables``, ``extractor``, ``status`` and ``warnings``; then the annotation pass. Writes only what the
architecture table lists for this stage. Idempotent (a rerun replaces the previous output; the
warnings that classify and ingest wrote are kept) and deterministic for deterministic backends.

No per-page or per-document problem raises: it is a ``Document.warnings`` entry, a ``status`` and
``StageOutcome.partial``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.assemble import assemble_statements
from app.core.context import CaseContext, natural_key
from app.core.models import Cell, Document, StageOutcome, Table
from app.core.schemas import DOC_SCHEMAS, EXTRACTABLE_TYPES
from app.extract.annotate import ClassifierFactory, annotate_rows
from app.extract.cascade import cap_warnings, extract_document
from app.extract.registry import discover, has_extractor

log = logging.getLogger("gldb.extract")

_KEPT_PREFIXES = ("classify:", "ingest:")
_CHECKED_KINDS = {"money", "date", "id", "decimal", "percent", "int"}


def _materialise(doc: Document) -> None:
    schema = DOC_SCHEMAS[doc.doc_type]
    doc.fields = {k: Cell(value=None, kind=s.kind) for k, s in schema.fields.items()}
    doc.tables = {n: Table(name=n, columns=list(s.columns)) for n, s in schema.tables.items()}


def run_stage(
    ctx: CaseContext,
    *,
    render: Callable | None = None,
    classifier_factory: ClassifierFactory | None = None,
) -> StageOutcome:
    discover()
    detail = {
        "documents": 0,
        "text_layer_docs": 0,
        "llm_cheap_pages": 0,
        "llm_strong_pages": 0,
        "cache_hits": 0,
        "received_not_checked": 0,
        "unverified_cells": 0,
        "rows_annotated": 0,
    }
    warnings: list[str] = []
    partial = False
    budget_pages = 0
    skipped_escalations = 0
    start_costs = len(ctx.costs.costs())

    for doc in sorted(ctx.case.documents, key=lambda d: natural_key(d.id)):
        if doc.doc_type not in EXTRACTABLE_TYPES:  # boilerplate / other: listed, never extracted
            if doc.status != "failed":
                doc.status = "received_not_checked"
                detail["received_not_checked"] += 1
            continue
        if doc.status == "failed":
            continue
        kept = [w for w in doc.warnings if w.startswith(_KEPT_PREFIXES)]
        if doc.status == "received_not_checked":  # classify already listed it: left alone
            detail["received_not_checked"] += 1
            continue
        _materialise(doc)
        doc.extractor = None
        doc.error = None
        if not has_extractor(doc.doc_type):
            doc.status = "received_not_checked"
            doc.warnings = cap_warnings([*kept, f"no_extractor: {doc.doc_type}"])
            detail["received_not_checked"] += 1
            continue
        detail["documents"] += 1
        with ctx.costs.scope(stage="extract", document_id=doc.id):
            result, meta = extract_document(doc, ctx, render=render)
        doc.fields = result.fields
        doc.tables = result.tables
        doc.extractor = meta.extractor or None
        doc.status = meta.status
        doc.warnings = cap_warnings([*kept, *result.warnings])
        if meta.status == "failed":
            doc.error = next(
                (w for w in doc.warnings if w.startswith(("unreadable:", "layout_not_recognised"))),
                "extraction produced no data",
            )
        if meta.extractor.startswith("text_layer@"):
            detail["text_layer_docs"] += 1
        detail["llm_cheap_pages"] += sum(
            1 for s in meta.sources_by_page.values() if s == "llm_cheap"
        )
        detail["llm_strong_pages"] += sum(
            1 for s in meta.sources_by_page.values() if s == "llm_strong"
        )
        detail["unverified_cells"] += _count_unverified(doc)
        budget_pages += sum(
            1 for w in doc.warnings if w.startswith("unreadable:") and w.endswith("budget_exceeded")
        )
        skipped_escalations += sum(
            1
            for w in doc.warnings
            if w.startswith("escalation_failed:") and w.endswith("budget_exceeded")
        )
        if doc.status in ("partial", "failed"):
            partial = True
            first = next((w for w in doc.warnings if not w.startswith(_KEPT_PREFIXES)), "")
            warnings.append(f"extract: {doc.id} {doc.status}: {first}".rstrip(": "))

    try:
        detail["rows_annotated"] = annotate_rows(ctx, classifier_factory=classifier_factory)
    except Exception as exc:  # the pass is best effort by design
        log.warning("annotation pass failed: %s", type(exc).__name__)
        warnings.append("extract: annotation pass failed")
        partial = True

    try:
        pieces = assemble_statements(ctx)
    except Exception as exc:  # best effort: without it the pieces are judged one by one
        log.warning("statement assembly failed: %s", type(exc).__name__)
        warnings.append("extract: statement assembly failed")
        partial = True
        pieces = {}
    for key, count in pieces.items():  # only present for split scans: digital detail is unchanged
        detail[f"statements_{key}" if key != "pages" else "statement_pages_unreviewed"] = count

    detail["cache_hits"] = sum(
        1 for c in ctx.costs.costs()[start_costs:] if c.cache_hit and c.stage == "extract"
    )

    reason: str | None = None
    breach = ctx.costs.breach()
    if breach is not None and breach.limit in ("max_calls", "max_usd"):
        tail = (
            f"{budget_pages} pages not extracted"
            if budget_pages
            else f"{skipped_escalations} second readings skipped"
        )
        reason = f"{breach}; {tail}"
        partial = True
    return StageOutcome(
        detail=detail,
        warnings=warnings,
        partial=partial,
        partial_reason=reason or ("some documents were only partly extracted" if partial else None),
    )


def _count_unverified(doc: Document) -> int:
    n = sum(1 for c in doc.fields.values() if c.verified is False and c.kind in _CHECKED_KINDS)
    for t in doc.tables.values():
        n += sum(
            1
            for r in t.rows
            for c in r.cells.values()
            if c.verified is False and c.kind in _CHECKED_KINDS
        )
    return n
