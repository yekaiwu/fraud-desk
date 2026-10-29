"""Extractor contracts (docs/architecture.md section 4, ingest-and-extraction.md 3.3)."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel

from app.core.context import CaseContext
from app.core.models import Cell, DocStatus, Document, Strict, Table
from app.core.schemas import response_model_for

__all__ = [
    "ExtractionMeta",
    "ExtractionResult",
    "Extractor",
    "LayoutNotRecognised",
    "PageExtraction",
    "Validation",
    "make_page_model",
]


class LayoutNotRecognised(Exception):
    """The text layer extractor cannot parse this layout; the cascade falls through (never an error)."""


class ExtractionResult(Strict):
    fields: dict[str, Cell]
    tables: dict[str, Table]
    warnings: list[str] = []
    # file page of every table row (table name -> page per row index) and of every field; extractors
    # fill them so the cascade can attribute arithmetic failures to a page even when a cell has no box
    row_pages: dict[str, list[int]] = {}
    field_pages: dict[str, int] = {}


class ExtractionMeta(Strict):
    """Returned by the cascade next to the ``ExtractionResult``."""

    extractor: str
    status: DocStatus
    sources_by_page: dict[int, str] = {}
    pages_escalated: list[int] = []


class Extractor(Protocol):
    doc_types: ClassVar[frozenset[str]]
    backend: ClassVar[Literal["text_layer", "llm"]]
    name: ClassVar[str]
    version: ClassVar[str]

    def extract(self, doc: Document, ctx: CaseContext) -> ExtractionResult: ...


class Validation(Strict):
    """Outcome of a document-type validator.

    ``structural`` lists problems that mean the layout was not understood (the document goes to the
    next backend); ``failing`` maps a file page to the reason (``balance_chain``, ``total``,
    ``identity``) for arithmetic that does not hold on that page. Validators never edit values.
    """

    structural: list[str] = []
    failing: dict[int, str] = {}
    missing_required: list[str] = []


PageExtraction = response_model_for  # name used by ingest-and-extraction.md 3.3
make_page_model: Callable[[str], type[BaseModel]] = response_model_for


CHECKED_KINDS = frozenset({"money", "date", "id", "decimal", "percent", "int"})


def unverified_failures(result: ExtractionResult, doc: Document) -> dict[int, str]:
    """Pages holding a money, date or id cell the second reader could not find
    (``verified is False``). Free-text cells (OCR noise in a description) do not escalate."""
    failing: dict[int, str] = {}
    for name, table in result.tables.items():
        pages = result.row_pages.get(name) or [doc.page_start] * len(table.rows)
        for row, page in zip(table.rows, pages, strict=False):
            if any(c.verified is False and c.kind in CHECKED_KINDS for c in row.cells.values()):
                failing.setdefault(page, "unverified")
    for key, cell in result.fields.items():
        if cell.verified is False and cell.kind in CHECKED_KINDS:
            failing.setdefault(result.field_pages.get(key, doc.page_start), "unverified")
    return failing


def missing_required(
    result: ExtractionResult, doc_type: str, *, ignore: Collection[str] = ()
) -> list[str]:
    """Schema-required fields with no value (a warning and ``partial``, never an error)."""
    from app.core.schemas import DOC_SCHEMAS

    spec = DOC_SCHEMAS[doc_type].fields
    return [
        k
        for k, c in result.fields.items()
        if c.value is None and spec[k].required and k not in ignore
    ]
