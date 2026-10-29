"""The cheap-then-strong cascade (ingest-and-extraction.md 6.6, tests 15, 16, 19 to 23)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.cache import ResponseCache
from app.core.config import Settings
from app.core.db import Database
from app.core.llm import FakeLLMClient
from app.extract.bank_statement import validate_bank_statement
from app.extract.cascade import cap_warnings, extract_document
from app.extract.registry import discover
from tests.extract_helpers import (
    FsRow,
    StatementSpec,
    bank_page_json,
    default_txns,
    empty_page,
    make_case,
    page_of,
    render_fs,
    render_statement,
    statement_truth,
)

discover()
RENDER = lambda *a, **k: b"\x89PNG"  # noqa: E731


def S(tmp_path: Path, mode: str = "cascade", **kw) -> Settings:  # noqa: ANN003
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        db_path=":memory:",
        llm_backend=kw.pop("llm_backend", "fake"),
        extractor_mode=mode,
        **kw,
    )


def per_page(spec: StatementSpec, edits: dict | None = None):  # noqa: ANN201
    return lambda req: bank_page_json(spec, page_of(req), edits=edits)


def cell(result, row: int, col: str):  # noqa: ANN001, ANN201
    return result.tables["transactions"].rows[row].cells[col]


# ----------------------------------------------------------------------------- text layer first


def test_clean_digital_statement_needs_zero_model_calls(tmp_path: Path) -> None:
    spec = StatementSpec(layout="dbs", txns=default_txns(40))
    llm = FakeLLMClient()
    ctx, doc, _ = make_case(render_statement(spec), "bank_statement", llm=llm, settings=S(tmp_path))
    result, meta = extract_document(doc, ctx)
    assert llm.calls == []
    assert meta.extractor == "text_layer@1"
    assert meta.status == "ok"
    assert meta.sources_by_page == {1: "text_layer", 2: "text_layer"}
    assert meta.pages_escalated == []
    rows = result.tables["transactions"].rows
    assert [r.cells["balance"].value for r in rows] == [t["balance"] for t in statement_truth(spec)]
    assert all(
        c.verified is True and c.box is not None
        for r in rows
        for k, c in r.cells.items()
        if c.value is not None
    )
    assert validate_bank_statement(result, ctx, doc).failing == {}
    assert result.warnings == []


# ----------------------------------------------------------------------------- test 15


def tampered_spec() -> StatementSpec:
    return StatementSpec(layout="dbs", txns=default_txns(30), balance_edits={5: "58,210.75"})


def test_tampered_digital_page_costs_one_cheap_call_and_the_chain_break_survives(
    tmp_path: Path,
) -> None:
    spec = tampered_spec()
    llm = FakeLLMClient(
        {"extract:bank_statement": per_page(spec)}
    )  # the model reads the same numbers
    ctx, doc, _ = make_case(render_statement(spec), "bank_statement", llm=llm, settings=S(tmp_path))
    result, meta = extract_document(doc, ctx)
    assert len(llm.calls) == 1
    assert llm.calls[0].model_role == "cheap"  # readers agree, so the strong model is skipped
    assert meta.extractor == "cascade(text_layer>llm_cheap)"
    assert meta.pages_escalated == [1]
    assert meta.sources_by_page[1] == "text_layer"
    assert (
        "escalated: page=1 reason=balance_chain result=confirmed_by_text_layer" in result.warnings
    )
    edited = cell(result, 5, "balance")
    assert (edited.value, edited.source, edited.alt_raw) == ("58210.75", "text_layer", None)
    assert validate_bank_statement(result, ctx, doc).failing == {
        1: "balance_chain"
    }  # the check will flag it
    assert meta.status == "ok"


def test_tampered_page_without_fixtures_keeps_the_parse_and_stays_ok(tmp_path: Path) -> None:
    spec = tampered_spec()
    llm = FakeLLMClient()
    ctx, doc, _ = make_case(render_statement(spec), "bank_statement", llm=llm, settings=S(tmp_path))
    result, meta = extract_document(doc, ctx)
    assert "escalation_failed: page 1: no_fixture" in result.warnings
    assert meta.status == "ok"
    assert meta.extractor == "text_layer@1"
    assert cell(result, 5, "balance").value == "58210.75"
    assert not any(w.startswith("escalated:") for w in result.warnings)


def test_a_model_that_misreads_a_digit_never_replaces_the_text_layer(tmp_path: Path) -> None:
    spec = tampered_spec()
    misread = {5: {"balance": "58,210.76"}}
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec, misread)})
    ctx, doc, _ = make_case(render_statement(spec), "bank_statement", llm=llm, settings=S(tmp_path))
    result, meta = extract_document(doc, ctx)
    assert [c.model_role for c in llm.calls] == [
        "cheap",
        "strong",
    ]  # they differ, so strong is asked
    assert meta.extractor == "cascade(text_layer>llm_cheap>llm_strong)"
    balance = cell(result, 5, "balance")
    assert (balance.value, balance.raw, balance.source) == ("58210.75", "58,210.75", "text_layer")
    assert balance.alt_raw == "58,210.76"
    assert 'reread: transactions[5].balance cheap="58,210.76" strong="58,210.76"' in result.warnings
    assert "escalated: page=1 reason=balance_chain result=text_layer_kept" in result.warnings


def test_strong_model_failing_keeps_the_parse(tmp_path: Path) -> None:
    spec = tampered_spec()
    llm = FakeLLMClient(
        {
            "extract:bank_statement:cheap": per_page(spec, {5: {"balance": "58,210.76"}}),
            "extract:bank_statement:strong": {"fields": {"nope": 1}},  # schema-invalid
        }
    )
    ctx, doc, _ = make_case(render_statement(spec), "bank_statement", llm=llm, settings=S(tmp_path))
    result, meta = extract_document(doc, ctx)
    assert "escalation_failed: page 1: schema" in result.warnings
    assert cell(result, 5, "balance").alt_raw == "58,210.76"  # the cheap reading is still recorded
    assert meta.status == "ok"


# ----------------------------------------------------------------------------- test 16


def unknown_layout() -> StatementSpec:
    return StatementSpec(
        layout="software",
        txns=default_txns(10),
        header_labels={
            "Date": "Posted",
            "Description": "Narrative",
            "Debit": "Out",
            "Credit": "In",
            "Balance": "Bal",
        },
    )


def test_unknown_layout_falls_to_the_cheap_model_with_verified_cells(tmp_path: Path) -> None:
    spec = unknown_layout()
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    ctx, doc, _ = make_case(render_statement(spec), "bank_statement", llm=llm, settings=S(tmp_path))
    result, meta = extract_document(doc, ctx)
    assert any(w.startswith("layout_not_recognised:") for w in result.warnings)
    assert meta.extractor == "llm:fake"
    assert [c.purpose for c in llm.calls] == ["extract:bank_statement"]
    assert meta.sources_by_page == {1: "llm_cheap"}
    rows = result.tables["transactions"].rows
    assert len(rows) == 10
    assert all(
        c.verified is True
        for r in rows
        for k, c in r.cells.items()
        if c.value is not None and k != "description"
    )
    assert meta.status == "ok"


def test_text_layer_mode_never_calls_a_model(tmp_path: Path) -> None:
    llm = FakeLLMClient(default=empty_page("bank_statement"))
    ctx, doc, _ = make_case(
        render_statement(unknown_layout()),
        "bank_statement",
        llm=llm,
        settings=S(tmp_path, "text_layer"),
    )
    result, meta = extract_document(doc, ctx)
    assert llm.calls == []
    assert meta.status == "failed"  # nothing could be parsed, and it says so
    assert any(w.startswith("layout_not_recognised:") for w in result.warnings)
    assert meta.extractor == ""


def test_llm_mode_skips_the_text_layer(tmp_path: Path) -> None:
    spec = StatementSpec(layout="dbs", txns=default_txns(10))
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    ctx, doc, _ = make_case(
        render_statement(spec), "bank_statement", llm=llm, settings=S(tmp_path, "llm")
    )
    _, meta = extract_document(doc, ctx)
    assert meta.extractor == "llm:fake"
    assert len(llm.calls) == 1


# ----------------------------------------------------------------------------- scans: tests 19 to 21


def scan_spec(**kw) -> StatementSpec:  # noqa: ANN003
    return StatementSpec(layout="dbs", txns=default_txns(12), rows_per_page=12, **kw)


def test_cheap_misread_on_a_scan_is_resolved_by_the_strong_model(tmp_path: Path) -> None:
    spec = scan_spec()
    misread = {4: {"balance": "11,225.51"}}  # a digit wrong; not what the OCR words say
    llm = FakeLLMClient(
        {
            "extract:bank_statement:cheap": per_page(spec, misread),
            "extract:bank_statement:strong": per_page(spec),
        }
    )
    ctx, doc, _ = make_case(
        render_statement(spec), "bank_statement", llm=llm, is_scan=True, settings=S(tmp_path)
    )
    result, meta = extract_document(doc, ctx, render=RENDER)
    assert [c.model_role for c in llm.calls] == ["cheap", "strong"]
    truth = statement_truth(spec)
    fixed = cell(result, 4, "balance")
    assert fixed.value == truth[4]["balance"]
    assert (fixed.source, fixed.verified) == ("llm_strong", True)
    assert fixed.alt_raw == "11,225.51"  # the cheap reading is kept
    assert any(
        w.startswith('reread: transactions[4].balance cheap="11,225.51"') for w in result.warnings
    )
    assert "escalated: page=1 reason=balance_chain result=resolved" in result.warnings
    assert meta.extractor == "cascade(llm_cheap>llm_strong)"
    assert meta.sources_by_page == {1: "llm_strong"}
    assert validate_bank_statement(result, ctx, doc).failing == {}
    assert sum(1 for c in llm.calls if c.model_role == "strong") == 1


def test_strong_agreeing_with_cheap_on_a_genuinely_edited_scan_row_stands(tmp_path: Path) -> None:
    spec = scan_spec(balance_edits={4: "58,210.75"})  # the page itself shows the edited balance
    llm = FakeLLMClient(
        {"extract:bank_statement": per_page(spec)}
    )  # both models read it faithfully
    ctx, doc, _ = make_case(
        render_statement(spec), "bank_statement", llm=llm, is_scan=True, settings=S(tmp_path)
    )
    result, meta = extract_document(doc, ctx, render=RENDER)
    balance = cell(result, 4, "balance")
    assert balance.value == "58210.75"
    assert balance.alt_raw is None
    assert balance.verified is True
    assert "escalated: page=1 reason=balance_chain result=confirmed_by_strong" in result.warnings
    assert not any(w.startswith("reread:") for w in result.warnings)
    assert validate_bank_statement(result, ctx, doc).failing == {
        1: "balance_chain"
    }  # still flaggable
    assert meta.status == "ok"


def test_strong_model_is_skipped_when_both_model_names_are_equal(tmp_path: Path) -> None:
    spec = scan_spec()
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec, {4: {"balance": "11,225.51"}})})
    settings = S(
        tmp_path, llm_backend="openai", llm_api_key="k", llm_model_cheap="m", llm_model_strong="m"
    )
    ctx, doc, _ = make_case(
        render_statement(spec), "bank_statement", llm=llm, is_scan=True, settings=settings
    )
    result, _ = extract_document(doc, ctx, render=RENDER)
    assert [c.model_role for c in llm.calls] == ["cheap"]
    assert not any(w.startswith("escalated:") for w in result.warnings)
    assert cell(result, 4, "balance").verified is False  # the unverified reading is visible


def test_scan_without_an_ocr_reader_is_never_verified_and_says_so(tmp_path: Path) -> None:
    spec = scan_spec()
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    ctx, doc, _ = make_case(
        render_statement(spec),
        "bank_statement",
        llm=llm,
        is_scan=True,
        keep_words=False,
        settings=S(tmp_path),
    )
    result, meta = extract_document(doc, ctx, render=RENDER)
    assert result.warnings.count("unverified_no_reader") == 1
    cells = [
        c
        for r in result.tables["transactions"].rows
        for c in r.cells.values()
        if c.value is not None
    ]
    assert cells
    assert all(c.verified is None for c in cells)
    assert [c.model_role for c in llm.calls] == ["cheap"]  # verification cannot trigger escalation
    assert meta.status == "ok"


def test_scan_without_fixture_is_unreadable_with_a_stated_reason(tmp_path: Path) -> None:
    spec = scan_spec()
    ctx, doc, _ = make_case(
        render_statement(spec),
        "bank_statement",
        llm=FakeLLMClient(),
        is_scan=True,
        keep_words=False,
        settings=S(tmp_path),
    )
    result, meta = extract_document(doc, ctx, render=RENDER)
    assert "unreadable: page 1: no_fixture" in result.warnings
    assert meta.status == "failed"  # never silently skipped, never faked
    assert result.tables["transactions"].rows == []


def test_a_single_unreadable_page_makes_the_document_partial(tmp_path: Path) -> None:
    spec = StatementSpec(layout="dbs", txns=default_txns(30))

    def fixture(req):  # noqa: ANN001, ANN202
        if page_of(req) == 2:
            return {"fields": {}}
        return bank_page_json(spec, 1)

    llm = FakeLLMClient({"extract:bank_statement": fixture})
    ctx, doc, _ = make_case(
        render_statement(spec), "bank_statement", llm=llm, is_scan=True, settings=S(tmp_path, "llm")
    )
    result, meta = extract_document(doc, ctx, render=RENDER)
    assert "unreadable: page 2: schema" in result.warnings
    assert meta.status == "partial"
    assert len(result.tables["transactions"].rows) == 22


# ----------------------------------------------------------------------------- budget and cache


def test_budget_limits_which_pages_are_read_deterministically(tmp_path: Path) -> None:
    spec = StatementSpec(layout="dbs", txns=default_txns(12), rows_per_page=3)  # four pages
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    settings = S(tmp_path, "llm", max_llm_calls_per_case=2, max_page_workers=4)
    ctx, doc, _ = make_case(
        render_statement(spec), "bank_statement", llm=llm, is_scan=True, settings=settings
    )
    result, meta = extract_document(doc, ctx, render=RENDER)
    assert len(llm.calls) == 2  # exactly the budget
    assert "unreadable: page 3: budget_exceeded" in result.warnings
    assert "unreadable: page 4: budget_exceeded" in result.warnings
    assert meta.status == "partial"
    assert meta.sources_by_page == {1: "llm_cheap", 2: "llm_cheap"}
    assert len(result.tables["transactions"].rows) == 6
    assert ctx.costs.breach() is not None


def test_second_run_is_served_from_the_response_cache(tmp_path: Path) -> None:
    spec = StatementSpec(layout="dbs", txns=default_txns(10))
    db = Database(":memory:")
    db.init_schema()
    cache = ResponseCache(db)
    pdf = render_statement(spec)

    def run():  # noqa: ANN202
        llm = FakeLLMClient({"extract:bank_statement": per_page(spec)}, cache=cache)
        ctx, doc, _ = make_case(pdf, "bank_statement", llm=llm, settings=S(tmp_path, "llm"))
        with ctx.costs.scope(stage="extract"):
            result, _ = extract_document(doc, ctx)
        return result, llm, ctx

    first, llm1, ctx1 = run()
    second, llm2, ctx2 = run()
    assert len(llm1.calls) == 1
    assert llm2.calls == []  # the cache answered before any call
    hits = [c for c in ctx2.costs.costs() if c.cache_hit]
    assert len(hits) == 1
    assert hits[0].usd == 0
    assert ctx2.costs.calls_used == 0  # a hit consumes no call budget
    assert first.model_dump_json() == second.model_dump_json()


# ----------------------------------------------------------------------------- financial statements


def test_fs_through_the_llm_path_is_scaled_once_with_sign_rules(tmp_path: Path) -> None:
    pdf = render_fs(
        statements={
            "Income Statement": [
                FsRow("Revenue", "10,000", "8,000"),
                FsRow("Income tax", "(272)", "(173)"),
            ]
        },
        scale_note="S$'000",
    )
    page = empty_page("financial_statement")
    page["fields"].update(
        {
            "company_name": "Meridian Fastener Supplies Pte. Ltd.",
            "uen": "201912345R",
            "scale_printed": "S$'000",
            "basis": "Audited",
            "currency": "S$",
        }
    )
    page["tables"]["income_statement"] = [
        {
            "line": "revenue",
            "section": "pnl",
            "label": "Revenue",
            "current": "10,000",
            "prior": "8,000",
        },
        {
            "line": "income_tax",
            "section": "pnl",
            "label": "Income tax",
            "current": "(272)",
            "prior": "(173)",
        },
    ]
    llm = FakeLLMClient({"extract:financial_statement": page})
    ctx, doc, _ = make_case(pdf, "financial_statement", llm=llm, settings=S(tmp_path, "llm"))
    result, _ = extract_document(doc, ctx)
    rows = result.tables["income_statement"].rows
    assert [(r.cells["current"].value, r.cells["current"].raw) for r in rows] == [
        ("10000000.00", "10,000"),
        ("272000.00", "(272)"),
    ]
    assert [r.cells["is_subtotal"].value for r in rows] == ["false", "false"]
    assert result.fields["scale_printed"].value == "1000"


# ----------------------------------------------------------------------------- helpers


def test_warnings_are_capped_at_fifty_plus_one_summary_entry() -> None:
    many = [f"unverified: x{i}" for i in range(80)]
    capped = cap_warnings([*many, many[0]])
    assert len(capped) == 51
    assert capped[-1] == "+30 more"
    assert cap_warnings(["a", "a", "b"]) == ["a", "b"]


@pytest.mark.parametrize("workers", [1, 4])
def test_results_do_not_depend_on_page_worker_count(tmp_path: Path, workers: int) -> None:
    spec = StatementSpec(layout="dbs", txns=default_txns(30))
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    ctx, doc, _ = make_case(
        render_statement(spec),
        "bank_statement",
        llm=llm,
        is_scan=True,
        settings=S(tmp_path, "llm", max_page_workers=workers),
    )
    result, _ = extract_document(doc, ctx, render=RENDER)
    assert result.model_dump_json() == _baseline(tmp_path, spec)


def _baseline(tmp_path: Path, spec: StatementSpec) -> str:
    llm = FakeLLMClient({"extract:bank_statement": per_page(spec)})
    ctx, doc, _ = make_case(
        render_statement(spec),
        "bank_statement",
        llm=llm,
        is_scan=True,
        settings=S(tmp_path, "llm", max_page_workers=1),
    )
    return extract_document(doc, ctx, render=RENDER)[0].model_dump_json()
