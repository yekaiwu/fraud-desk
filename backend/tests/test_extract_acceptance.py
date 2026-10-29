"""Acceptance run on hand-built fixtures (T-extract-fin-11, ingest-and-extraction.md section 10)."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.core.llm import FakeLLMClient, prompt_registry
from app.extract.bank_statement import validate_bank_statement
from app.extract.registry import discover
from app.extract.stage import run_stage
from tests.extract_helpers import (
    FsRow,
    StatementSpec,
    default_txns,
    make_multi_case,
    render_contract,
    render_fs,
    render_invoice,
    render_po,
    render_statement,
)

FS = {
    "Income Statement": [
        FsRow("Revenue", "10,000", "8,000"),
        FsRow("Cost of sales", "(6,000)", "(5,000)"),
        FsRow("Gross profit", "4,000", "3,000"),
    ],
    "Balance Sheet": [
        FsRow("Current assets", heading=True),
        FsRow("Cash and cash equivalents", "3,000", "2,500"),
        FsRow("Total current assets", "3,000", "2,500"),
        FsRow("Total assets", "3,000", "2,500"),
        FsRow("Total liabilities and equity", "3,000", "2,500"),
    ],
    "Statement of Cash Flows": [FsRow("Net increase in cash", "500", "400")],
}


def no_classifier(ctx):  # noqa: ANN001, ANN202
    class C:
        def classify(self, question, items):  # noqa: ANN001, ANN202
            raise RuntimeError("n/a")

    return C()


def test_clean_digital_bundle_runs_extract_with_zero_model_calls(tmp_path: Path) -> None:
    items = [
        (
            render_statement(StatementSpec(layout=lay, txns=default_txns(40))),
            "bank_statement",
            False,
            True,
        )
        for lay in ("dbs", "ocbc", "uob", "software")
    ]
    items += [
        (render_invoice(per_line_gst=False), "invoice", False, True),
        (render_invoice(per_line_gst=True, number="INV-2026-0102"), "invoice", False, True),
        (render_fs(statements=FS), "financial_statement", False, True),
        (render_contract(), "contract", False, True),
        (render_po(), "purchase_order", False, True),
    ]
    llm = FakeLLMClient()
    settings = Settings(_env_file=None, data_dir=tmp_path, db_path=":memory:", llm_backend="fake")
    ctx = make_multi_case(items, llm=llm, settings=settings)
    outcome = run_stage(ctx, classifier_factory=no_classifier)

    assert llm.calls == []  # the acceptance criterion: a clean digital bundle costs nothing
    assert outcome.detail["text_layer_docs"] == 9
    assert outcome.detail["unverified_cells"] == 0
    for doc in ctx.case.documents:
        assert doc.extractor == "text_layer@1", doc.id
        cells = [c for c in doc.fields.values() if c.value is not None and c.source == "text_layer"]
        for table in doc.tables.values():
            cells += [
                c
                for r in table.rows
                for c in r.cells.values()
                if c.value is not None and c.source == "text_layer"
            ]
        assert cells
        assert all(c.verified is True for c in cells), doc.id
        boxed = [c for c in cells if c.box is not None]
        assert (
            len(boxed) >= len(cells) - 6
        )  # only derived values (page numbers, defaults) lack a box
        if doc.doc_type == "bank_statement":
            assert validate_bank_statement(_as_result(doc), ctx, doc).failing == {}
    statuses = {d.id: d.status for d in ctx.case.documents}
    assert set(statuses.values()) <= {"ok"}, statuses


def _as_result(doc):  # noqa: ANN001, ANN202
    from app.extract.base import ExtractionResult

    rows = {
        n: [r.box.page if r.box else doc.page_start for r in t.rows] for n, t in doc.tables.items()
    }
    return ExtractionResult(fields=doc.fields, tables=doc.tables, row_pages=rows)


def test_scan_without_fixture_is_unreadable_with_a_reason_never_silent(tmp_path: Path) -> None:
    items = [(render_statement(StatementSpec(txns=default_txns(5))), "bank_statement", True, False)]
    settings = Settings(_env_file=None, data_dir=tmp_path, db_path=":memory:", llm_backend="fake")
    ctx = make_multi_case(items, llm=FakeLLMClient(), settings=settings)
    outcome = run_stage(ctx, render=lambda *a, **k: b"png", classifier_factory=no_classifier)
    doc = ctx.case.documents[0]
    assert doc.status == "failed"
    assert "unreadable: page 1: no_fixture" in doc.warnings
    assert outcome.partial is True
    assert outcome.warnings  # shown on the case page


def test_prompts_are_registered_for_provenance() -> None:
    discover()
    registered = prompt_registry()
    for doc_type in (
        "bank_statement",
        "invoice",
        "financial_statement",
        "contract",
        "purchase_order",
    ):
        assert f"extract:{doc_type}" in registered
