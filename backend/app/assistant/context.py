"""The documents an Ask question may quote, built from cells already stored on the case.

Nothing is re-extracted. Each block is untrusted text with stable ids (file, page, field, row) so a
citation can be checked against the cell it names. A whole-case question is trimmed to a character
budget: pages with an open flag first, then the page the officer is looking at and its neighbours.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.llm import wrap_untrusted
from app.core.models import Case, Cell, Document, Flag, Row
from app.semantic.render import shown

# About six thousand tokens. A question stays inside the per-case dollar cap.
CHAR_BUDGET = 24_000
MAX_ROWS = 150


def _page_of(cell: Cell, fallback: int) -> int:
    return cell.box.page if cell.box is not None else fallback


def _row_page(row: Row, fallback: int) -> int:
    if row.box is not None:
        return row.box.page
    for cell in row.cells.values():
        if cell.box is not None:
            return cell.box.page
    return fallback


def render_document(doc: Document) -> str:
    """One document as citable lines. Empty cells are left out. Ids are the ones the verifier uses."""
    lines = [
        f"document_id {doc.id}",
        f"file_id {doc.file_id}",
        f"pages {doc.page_start}-{doc.page_end}",
        f"type {doc.doc_type}",
    ]
    for name, cell in doc.fields.items():
        text = shown(cell)
        if text:
            lines.append(f'field {name} page {_page_of(cell, doc.page_start)}: "{text}"')
    for tname, table in doc.tables.items():
        for row in table.rows[:MAX_ROWS]:
            cells = [f'{col}="{shown(cell)}"' for col, cell in row.cells.items() if shown(cell)]
            if not cells:
                continue
            page = _row_page(row, doc.page_start)
            lines.append(f"table {tname} row {row.index} page {page}: " + " | ".join(cells))
        if len(table.rows) > MAX_ROWS:
            lines.append(f"table {tname}: {len(table.rows) - MAX_ROWS} more rows not shown")
    return "\n".join(lines)


def _open_flag_docs(flags: Sequence[Flag]) -> set[str]:
    return {d for f in flags if f.disposition == "open" for d in f.document_ids}


def _rank(
    doc: Document, flagged: set[str], file_id: str | None, page: int | None
) -> tuple[int, int]:
    """Higher is kept first: an open flag, then the viewed page or a neighbour."""
    neighbour = 0
    if (
        file_id
        and page
        and doc.file_id == file_id
        and doc.page_start - 1 <= page <= doc.page_end + 1
    ):
        neighbour = 1
    return (1 if doc.id in flagged else 0, neighbour)


def select_documents(
    case: Case,
    *,
    scope: str,
    file_id: str | None,
    page: int | None,
) -> list[Document]:
    """Documents the model may see, best first.

    Scope ``document`` is every document in that file. Scope ``case`` is cut once the character
    budget is full: an open flag first, then the page on screen and its neighbours.
    """
    docs = list(case.documents)
    if scope == "document":
        return [d for d in docs if d.file_id == file_id]
    flagged = _open_flag_docs(case.flags)
    ranked = sorted(
        docs,
        key=lambda d: _rank(d, flagged, file_id, page),
        reverse=True,
    )
    kept: list[Document] = []
    used = 0
    for doc in ranked:
        text = render_document(doc)
        if kept and used + len(text) > CHAR_BUDGET:
            continue
        if not kept and len(text) > CHAR_BUDGET:
            kept.append(doc)  # one oversized document is still shown, truncated by wrap_untrusted
            break
        kept.append(doc)
        used += len(text)
        if used >= CHAR_BUDGET:
            break
    return kept


def blocks_for(docs: Sequence[Document]) -> str:
    """One untrusted block per document. Raises ValueError if a block forges its own end marker."""
    parts: list[str] = []
    for doc in docs:
        label = f"{doc.file_id}.p{doc.page_start}-{doc.page_end}.{doc.id}"
        parts.append(wrap_untrusted(render_document(doc), label=label))
    return "\n\n".join(parts)
