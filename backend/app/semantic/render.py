"""Documents as citable, untrusted-wrapped prompt text (docs/semantic-signals.md, invariant 4).

Every cell a model may quote is shown with the name the verifier looks up: ``field <name>`` and
``table <name> row <n>``. All of it is document text, so it goes into the ``user`` message between
``wrap_untrusted`` delimiters whose nonce is a hash of the enclosed text; the fixed instructions live
in ``system`` and never contain document text.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.core.llm import wrap_untrusted
from app.core.models import Cell, Document, Row, clean_text

CELL_MAX_CHARS = 200
DEFAULT_MAX_ROWS = 150


def shown(cell: Cell) -> str:
    """The text a model sees for a cell: the printed form when there is one, else the value."""
    text = cell.raw if cell.raw is not None else cell.value
    return clean_text(text or "", CELL_MAX_CHARS)


def render_document(
    doc: Document,
    *,
    fields: Iterable[str] | None = None,
    tables: Iterable[str] | None = None,
    columns: dict[str, Sequence[str]] | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> str:
    """Plain text of the selected fields and tables (all of them when not given), one cell group per
    line. ``columns`` narrows a table to some columns. Empty cells are left out."""
    wanted_fields = None if fields is None else set(fields)
    wanted_tables = None if tables is None else set(tables)
    lines = [f"document {doc.id} type={doc.doc_type}"]
    for name, cell in doc.fields.items():
        if wanted_fields is not None and name not in wanted_fields:
            continue
        text = shown(cell)
        if text:
            lines.append(f'field {name}: "{text}"')
    for tname, table in doc.tables.items():
        if wanted_tables is not None and tname not in wanted_tables:
            continue
        cols = list((columns or {}).get(tname) or table.columns)
        rows: list[Row] = table.rows[:max_rows]
        for row in rows:
            cells = [
                f'{c}="{shown(row.cells[c])}"'
                for c in cols
                if c in row.cells and shown(row.cells[c])
            ]
            if cells:
                lines.append(f"table {tname} row {row.index}: " + " | ".join(cells))
        if len(table.rows) > max_rows:
            lines.append(f"table {tname}: {len(table.rows) - max_rows} more rows not shown")
    return "\n".join(lines)


def wrap_documents(rendered: Iterable[tuple[str, str]]) -> str:
    """``(document id, rendered text)`` pairs -> one untrusted block per document.

    ``wrap_untrusted`` raises ``ValueError`` if a text carries its own end marker (it cannot, short
    of a hash collision); ``call.run_claims`` turns that into "not run"."""
    return "\n\n".join(wrap_untrusted(text, label=doc_id) for doc_id, text in rendered)
