"""Test and script helpers (docs/data-model.md section 4.2). Never uses the wall clock for ``today``."""

from __future__ import annotations

import shutil
import tempfile
import weakref
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.core.cache import ResponseCache
from app.core.config import Settings
from app.core.context import CaseContext
from app.core.costs import BudgetLimits, CostMeter, PriceTable
from app.core.db import Database
from app.core.fingerprints import FingerprintStore, MemoryFingerprintStore
from app.core.llm import FakeLLMClient, LLMClient
from app.core.models import (
    Annotation,
    Applicant,
    Case,
    Cell,
    CellKind,
    Classification,
    DocType,
    Document,
    FileRef,
    Region,
    Row,
    Table,
    UseCase,
)
from app.core.registry import MockRegistry
from app.core.schemas import DOC_SCHEMAS, FieldSpec, TableSpec
from app.core.storage import FileStore

FIXED_TODAY = date(2026, 10, 27)
FIXED_NOW = datetime(2026, 10, 27, 9, 0, 0, tzinfo=UTC)
TEST_UENS = ("201912345R", "202012345H", "201934567C", "53012345B")  # valid per stdnum.sg.uen


def make_cell(
    value: str | None,
    *,
    kind: CellKind = "text",
    raw: str | None = None,
    box: Region | None = None,
    **kw: Any,
) -> Cell:
    return Cell(value=value, kind=kind, raw=raw, box=box, **kw)


def _spec_for(table: str, keys: set[str], doc_type: str | None) -> TableSpec:
    candidates: list[TableSpec] = []
    for dt, schema in DOC_SCHEMAS.items():
        if doc_type is not None and dt != doc_type:
            continue
        if table in schema.tables:
            candidates.append(schema.tables[table])
    if not candidates:
        raise KeyError(f"no table named {table!r} in DOC_SCHEMAS")
    for spec in candidates:
        if keys <= set(spec.columns):
            return spec
    return candidates[0]


def make_table(
    name: str,
    rows: list[dict[str, str | None]],
    *,
    annotations: list[dict[str, Annotation]] | None = None,
    doc_type: str | None = None,
) -> Table:
    """A table whose columns come from DOC_SCHEMAS; every row carries every column."""
    keys = {k for r in rows for k in r}
    spec = _spec_for(name, keys, doc_type)
    columns = list(spec.columns)
    unknown = keys - set(columns)
    if unknown:
        raise KeyError(f"unknown column(s) for table {name!r}: {sorted(unknown)}")
    out_rows: list[Row] = []
    for i, values in enumerate(rows):
        cells = {
            col: make_cell(values.get(col), kind=spec.columns[col].kind, raw=None)
            for col in columns
        }
        ann = annotations[i] if annotations and i < len(annotations) else {}
        out_rows.append(Row(index=i, cells=cells, annotations=ann))
    return Table(name=name, columns=columns, rows=out_rows, pages=[1])


def make_doc(
    doc_type: DocType,
    *,
    id: str = "d1",  # noqa: A002 - mirrors the Document field name
    fields: dict[str, str | None] | None = None,
    tables: dict[str, list[dict[str, str | None]]] | None = None,
    file_id: str = "f1",
    **kw: Any,
) -> Document:
    """A Document with every schema key present (value None when not given)."""
    schema = DOC_SCHEMAS[doc_type]
    fields = fields or {}
    unknown = set(fields) - set(schema.fields)
    if unknown:
        raise KeyError(f"unknown field(s) for {doc_type}: {sorted(unknown)}")
    cells = {
        key: make_cell(fields.get(key), kind=_kind(spec)) for key, spec in schema.fields.items()
    }
    tables = tables or {}
    unknown_tables = set(tables) - set(schema.tables)
    if unknown_tables:
        raise KeyError(f"unknown table(s) for {doc_type}: {sorted(unknown_tables)}")
    built = {
        name: make_table(name, tables.get(name, []), doc_type=doc_type) for name in schema.tables
    }
    params: dict[str, Any] = {
        "id": id,
        "file_id": file_id,
        "page_start": 1,
        "page_end": 1,
        "doc_type": doc_type,
        "classification": Classification(
            label=doc_type, probability=1.0, classifier="test", version="1"
        ),
        "fields": cells,
        "tables": built,
    }
    params.update(kw)
    return Document(**params)


def _kind(spec: FieldSpec) -> CellKind:
    return spec.kind


_CTX_KEYS = {"settings", "costs", "cache", "page_words"}


def make_ctx(
    documents: Sequence[Document] = (),
    *,
    use_case: UseCase = "loan",
    today: date = FIXED_TODAY,
    registry: MockRegistry | None = None,
    llm: LLMClient | None = None,
    applicant: Applicant | None = None,
    files: Sequence[tuple[FileRef, bytes]] = (),
    fingerprints: FingerprintStore | None = None,
    **kw: Any,
) -> CaseContext:
    """A ready CaseContext: in-memory database and cache, Fake LLM, empty fingerprint store.

    ``files``: each ``(FileRef, bytes)`` is written to a temporary content-addressed folder and
    appended to ``ctx.case.files`` (its ``sha256``/``size_bytes`` are set from the bytes), so
    ``ctx.file_path`` resolves it. Extra keyword arguments name either a context field
    (``settings``, ``costs``, ``cache``, ``page_words``) or a ``Case`` field (``flags``,
    ``check_runs``, ``status``, ``partial``, ``reference`` ...).
    """
    tmp = Path(tempfile.mkdtemp(prefix="gldb-test-"))
    settings = kw.pop("settings", None) or Settings(
        _env_file=None, data_dir=tmp, db_path=":memory:", llm_backend="fake"
    )
    db = Database(":memory:", data_dir=settings.data_dir)
    db.init_schema()
    store = FileStore(settings.data_dir)
    refs: list[FileRef] = []
    for ref, data in files:
        sha = store.put(data)
        refs.append(ref.model_copy(update={"sha256": sha, "size_bytes": len(data)}))

    case_kw = {k: v for k, v in kw.items() if k not in _CTX_KEYS}
    case = Case(
        id=case_kw.pop("id", "c_test00000000"),
        use_case=use_case,
        today=today,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        applicant=applicant or Applicant(),
        files=[*case_kw.pop("files_extra", []), *refs],
        documents=list(documents),
        **case_kw,
    )
    ctx = CaseContext(
        case=case,
        today=today,
        settings=settings,
        llm=llm if llm is not None else FakeLLMClient(),
        registry=registry,
        fingerprints=fingerprints if fingerprints is not None else MemoryFingerprintStore(),
        costs=kw.get("costs") or CostMeter(BudgetLimits.from_settings(settings), PriceTable()),
        cache=kw.get("cache") or ResponseCache(db, enabled=settings.cache_enabled),
        page_words=kw.get("page_words") or {},
    )
    weakref.finalize(ctx, shutil.rmtree, tmp, True)
    return ctx
