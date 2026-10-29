"""The extract stage: routing, idempotence, determinism, budget, status (6.1, tests 22, 28)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.llm import FakeLLMClient
from app.core.models import Cell, Table
from app.extract.stage import run_stage
from tests.extract_helpers import (
    StatementSpec,
    bank_page_json,
    default_txns,
    make_multi_case,
    page_of,
    render_contract,
    render_invoice,
    render_statement,
)

DETAIL_KEYS = {
    "documents",
    "text_layer_docs",
    "llm_cheap_pages",
    "llm_strong_pages",
    "cache_hits",
    "received_not_checked",
    "unverified_cells",
    "rows_annotated",
}


@pytest.fixture(autouse=True)
def _passport_has_no_text_layer_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests need one recognised type that nobody extracts. Passport was that type until
    extract-kyc (wave 2) registered ``kyc_ids``; hide that owner here (integration-loan)."""
    from app.extract import registry as _registry

    _registry.discover()
    llm_only = {"llm": _registry._EXTRACTORS["passport"]["llm"]}
    monkeypatch.setitem(_registry._EXTRACTORS, "passport", llm_only)
    monkeypatch.setitem(_registry._HINTS, "passport", [])


class NoClassifier:
    def classify(self, question, items):  # noqa: ANN001, ANN201
        raise RuntimeError("no classifier in this test")


def stub_factory(ctx):  # noqa: ANN001, ANN201
    return NoClassifier()


def S(tmp_path: Path, **kw) -> Settings:  # noqa: ANN003
    return Settings(_env_file=None, data_dir=tmp_path, db_path=":memory:", llm_backend="fake", **kw)


def mixed_case(tmp_path: Path, llm: FakeLLMClient | None = None, **kw):  # noqa: ANN003, ANN201
    items = [
        (
            render_statement(StatementSpec(layout="dbs", txns=default_txns(30))),
            "bank_statement",
            False,
            True,
        ),
        (render_invoice(), "invoice", False, True),
        (render_contract(), "boilerplate", False, True),
        (render_contract(), "passport", False, True),
        (render_contract(), "other", False, True),
    ]
    ctx = make_multi_case(items, llm=llm or FakeLLMClient(), settings=S(tmp_path, **kw))
    ctx.case.documents[4].status = "received_not_checked"
    ctx.case.documents[4].warnings = ["classify: low_confidence"]
    return ctx


def test_routing_statuses_detail_and_zero_model_calls(tmp_path: Path) -> None:
    llm = FakeLLMClient()
    ctx = mixed_case(tmp_path, llm)
    outcome = run_stage(ctx, classifier_factory=stub_factory)
    docs = {d.id: d for d in ctx.case.documents}
    assert docs["d1"].status == "ok"
    assert docs["d1"].extractor == "text_layer@1"
    assert len(docs["d1"].tables["transactions"].rows) == 30
    assert docs["d2"].status == "ok"
    assert docs["d2"].fields["invoice_number"].value == "INV-2026-0101"
    assert docs["d3"].status == "received_not_checked"  # boilerplate: listed, never extracted
    assert docs["d4"].status == "received_not_checked"  # passport: no extractor yet
    assert "no_extractor: passport" in docs["d4"].warnings
    assert docs["d4"].extractor is None
    assert set(docs["d4"].fields) >= {
        "mrz_line1",
        "passport_number",
    }  # every schema key present, empty
    assert all(c.value is None for c in docs["d4"].fields.values())
    assert docs["d5"].warnings == ["classify: low_confidence"]  # left alone
    assert llm.calls == []
    assert set(outcome.detail) == DETAIL_KEYS
    assert outcome.detail["documents"] == 2
    assert outcome.detail["text_layer_docs"] == 2
    assert outcome.detail["received_not_checked"] == 3
    assert outcome.detail["llm_cheap_pages"] == 0
    assert outcome.detail["rows_annotated"] == 30
    assert "c4_c5_unavailable" in docs["d1"].warnings  # classifier stub failed: honest, not a crash
    assert all(isinstance(v, int | float | str) for v in outcome.detail.values())
    assert outcome.partial is False


def test_every_cell_of_a_clean_digital_set_is_verified_with_a_box(tmp_path: Path) -> None:
    ctx = mixed_case(tmp_path)
    outcome = run_stage(ctx, classifier_factory=stub_factory)
    assert outcome.detail["unverified_cells"] == 0
    for doc in ctx.case.documents[:2]:
        cells = [c for c in doc.fields.values() if c.value is not None]
        for t in doc.tables.values():
            cells += [
                c
                for r in t.rows
                for c in r.cells.values()
                if c.value is not None and c.source == "text_layer"
            ]
        assert cells
        assert all(
            c.verified is True for c in cells if c.source == "text_layer" and c.box is not None
        )


def test_stage_is_idempotent_and_replaces_previous_output(tmp_path: Path) -> None:
    ctx = mixed_case(tmp_path)
    ctx.case.documents[0].warnings = ["ingest: note", "stale warning"]
    ctx.case.documents[0].fields["bank_name"] = Cell(value="STALE")
    ctx.case.documents[0].tables["transactions"] = Table(name="transactions", columns=["date"])
    run_stage(ctx, classifier_factory=stub_factory)
    first = [d.model_dump_json() for d in ctx.case.documents]
    assert ctx.case.documents[0].fields["bank_name"].value == "DBS Bank Ltd"
    assert "stale warning" not in ctx.case.documents[0].warnings
    assert "ingest: note" in ctx.case.documents[0].warnings  # ingest and classify warnings survive
    run_stage(ctx, classifier_factory=stub_factory)
    assert [d.model_dump_json() for d in ctx.case.documents] == first


def test_two_independent_runs_give_byte_identical_documents(tmp_path: Path) -> None:
    a = mixed_case(tmp_path / "a")
    b = mixed_case(tmp_path / "b")
    run_stage(a, classifier_factory=stub_factory)
    run_stage(b, classifier_factory=stub_factory)
    assert [d.model_dump_json() for d in a.case.documents] == [
        d.model_dump_json() for d in b.case.documents
    ]


def scan_case(tmp_path: Path, workers: int, **kw):  # noqa: ANN003, ANN201
    spec = StatementSpec(layout="dbs", txns=default_txns(12), rows_per_page=3)
    llm = FakeLLMClient({"extract:bank_statement": lambda req: bank_page_json(spec, page_of(req))})
    items = [
        (render_statement(spec), "bank_statement", True, True),
        (render_invoice(), "invoice", False, True),
    ]
    return make_multi_case(
        items, llm=llm, settings=S(tmp_path, max_page_workers=workers, **kw)
    ), llm


def test_page_worker_count_does_not_change_the_output(tmp_path: Path) -> None:
    outputs = []
    for workers in (1, 4):
        ctx, _ = scan_case(tmp_path / str(workers), workers)
        run_stage(ctx, render=lambda *a, **k: b"png", classifier_factory=stub_factory)
        outputs.append([d.model_dump_json() for d in ctx.case.documents])
    assert outputs[0] == outputs[1]


def test_budget_trips_partial_with_a_stated_reason_and_text_layer_docs_still_finish(
    tmp_path: Path,
) -> None:
    ctx, llm = scan_case(tmp_path, 4, max_llm_calls_per_case=2)
    outcome = run_stage(ctx, render=lambda *a, **k: b"png", classifier_factory=stub_factory)
    d1, d2 = ctx.case.documents
    assert len(llm.calls) == 2  # exactly the budget
    assert d1.status == "partial"
    assert "unreadable: page 3: budget_exceeded" in d1.warnings
    assert "unreadable: page 4: budget_exceeded" in d1.warnings
    assert len(d1.tables["transactions"].rows) == 6
    assert d2.status == "ok"  # the digital invoice needs no model
    assert d2.extractor == "text_layer@1"
    assert outcome.partial is True
    assert outcome.partial_reason is not None
    assert outcome.partial_reason.startswith("budget_exceeded: max_calls")
    assert outcome.partial_reason.endswith("2 pages not extracted")
    assert any("d1 partial" in w for w in outcome.warnings)


def test_scan_without_fixture_is_failed_with_a_reason_and_partial_outcome(tmp_path: Path) -> None:
    items = [(render_statement(StatementSpec(txns=default_txns(5))), "bank_statement", True, False)]
    ctx = make_multi_case(items, llm=FakeLLMClient(), settings=S(tmp_path))
    outcome = run_stage(ctx, render=lambda *a, **k: b"png", classifier_factory=stub_factory)
    doc = ctx.case.documents[0]
    assert doc.status == "failed"
    assert doc.error is not None
    assert doc.error.startswith("unreadable: page 1: no_fixture")
    assert outcome.partial is True
    assert outcome.detail["documents"] == 1


def test_failed_documents_from_ingest_or_classify_are_skipped(tmp_path: Path) -> None:
    ctx = mixed_case(tmp_path)
    ctx.case.documents[0].status = "failed"
    ctx.case.documents[0].error = "corrupt_pdf: x"
    run_stage(ctx, classifier_factory=stub_factory)
    assert ctx.case.documents[0].status == "failed"
    assert not any(t.rows for t in ctx.case.documents[0].tables.values())
    assert ctx.case.documents[1].status == "ok"


def test_empty_case_is_a_clean_noop(tmp_path: Path) -> None:
    from app.core.testing import make_ctx

    outcome = run_stage(make_ctx(), classifier_factory=stub_factory)
    assert outcome.partial is False
    assert outcome.detail["documents"] == 0


def test_extract_package_never_reads_the_wall_clock() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "extract"
    pattern = re.compile(r"date\.today|datetime\.now|datetime\.utcnow|time\.time\(|\.today\(\)")
    offenders = [
        f"{p.name}:{n}"
        for p in sorted(root.rglob("*.py"))
        for n, line in enumerate(p.read_text().splitlines(), start=1)
        if pattern.search(line) and not line.lstrip().startswith("#")
    ]
    assert offenders == []


@pytest.mark.parametrize("mode", ["text_layer", "llm", "cascade"])
def test_all_extractor_modes_run_without_raising(tmp_path: Path, mode: str) -> None:
    ctx = mixed_case(tmp_path, extractor_mode=mode)
    outcome = run_stage(ctx, classifier_factory=stub_factory)
    assert set(outcome.detail) == DETAIL_KEYS
