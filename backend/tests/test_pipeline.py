"""run_pipeline, job runner, applicant resolution and fingerprint recording."""

from __future__ import annotations

from decimal import Decimal

import pytest

import app.pipeline as pipeline
from app.core.cases import add_file, new_case
from app.core.config import Settings
from app.core.costs import LLMBudgetExceeded
from app.core.db import Database
from app.core.fingerprints import (
    SqliteFingerprintStore,
    invoice_fingerprint,
    invoice_key_fingerprint,
)
from app.core.llm import FakeLLMClient, LLMRequest
from app.core.models import Applicant, ReviewEntry, StageOutcome
from app.core.testing import FIXED_NOW, FIXED_TODAY, TEST_UENS, make_ctx, make_doc
from app.pipeline import (
    STAGES,
    JobRunner,
    prepare_rerun,
    record_fingerprints,
    resolve_applicant,
    run_pipeline,
)

PDF = b"%PDF-1.4 pipeline test file"


@pytest.fixture
def env(db: Database, settings: Settings):  # noqa: ANN201
    case = new_case(settings, "loan", reference="T-1", today=FIXED_TODAY)
    db.create_case(case)
    return db, settings, case


def statement(rows: list[dict], id: str = "d1"):  # noqa: ANN201, A002
    return make_doc(
        "bank_statement",
        id=id,
        fields={"opening_balance": "100.00", "account_holder": "Acme Pte Ltd"},
        tables={"transactions": rows},
        extractor="text_layer@1",
    )


EDITED = [
    {"date": "2026-03-01", "credit": "50.00", "balance": "150.00"},
    {"date": "2026-03-02", "credit": "10.00", "balance": "200.00"},  # should be 160.00
]
CLEAN = [
    {"date": "2026-03-01", "credit": "50.00", "balance": "150.00"},
    {"date": "2026-03-02", "credit": "10.00", "balance": "160.00"},
]


def with_documents(monkeypatch: pytest.MonkeyPatch, *docs):  # noqa: ANN001, ANN201
    def extract(ctx):  # noqa: ANN001, ANN202
        ctx.case.documents = [d.model_copy(deep=True) for d in docs]
        return StageOutcome(detail={"documents": len(docs)})

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "extract", extract)


def test_zero_file_case_finishes_open_with_a_partial_score(env) -> None:  # noqa: ANN001
    db, settings, case = env
    out = run_pipeline(case.id, db=db, settings=settings)
    assert out.status == "open" and out.version == 1
    assert out.score is not None and out.score.partial is True and out.score.value == 0
    assert any(r.startswith("no_documents") for r in out.score.partial_reasons)
    assert [s.stage for s in out.stages] == list(STAGES)
    assert all(s.status == "ok" for s in out.stages)
    assert out.coverage is not None and out.coverage.ratio == 0.0  # silence is not safety
    assert out.provenance is not None and out.provenance.today == FIXED_TODAY
    loaded = db.load_case(case.id)
    assert loaded.status == "open" and loaded.score == out.score
    with db.conn() as c:
        row = c.execute("SELECT partial, score, status FROM cases").fetchone()
    assert (row["partial"], row["score"], row["status"]) == (1, 0, "open")


def test_a_failing_stage_is_recorded_and_the_rest_still_run(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env
    seen: list[str] = []

    def boom(ctx):  # noqa: ANN001, ANN202
        raise RuntimeError("kaboom with /secret/path")

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "extract", boom)
    out = run_pipeline(
        case.id, db=db, settings=settings, on_stage=lambda r: seen.append(f"{r.stage}:{r.status}")
    )
    by_stage = {s.stage: s for s in out.stages}
    assert by_stage["extract"].status == "failed"
    assert by_stage["extract"].error == "RuntimeError: kaboom with /secret/path"
    assert "Traceback" not in (by_stage["extract"].error or "")
    assert all(by_stage[s].status == "ok" for s in STAGES if s != "extract")
    assert out.status == "open" and out.partial
    assert "stage_failed:extract" in out.partial_reasons
    assert (
        out.score is not None
        and out.score.partial
        and "stage_failed:extract" in out.score.partial_reasons
    )
    assert seen[:4] == ["ingest:running", "ingest:ok", "classify:running", "classify:ok"]
    assert "extract:failed" in seen and seen[-1] == "score:ok"


def test_budget_exceeded_is_ok_but_partial_with_the_message(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env

    def extract(ctx):  # noqa: ANN001, ANN202
        raise LLMBudgetExceeded("max_calls", "2", "2")

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "extract", extract)
    out = run_pipeline(case.id, db=db, settings=settings)
    rec = {s.stage: s for s in out.stages}["extract"]
    assert rec.status == "ok" and rec.error is None
    assert "budget_exceeded: max_calls (limit 2, used 2)" in out.partial_reasons
    assert "budget_exceeded: max_calls (limit 2, used 2)" in (
        out.score.partial_reasons if out.score else []
    )
    assert {s.stage: s.status for s in out.stages}[
        "rules"
    ] == "ok"  # deterministic stages still run


def test_stage_reported_partial_reason_is_deduplicated(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env
    msg = "budget_exceeded: max_pages (limit 3, used 5); 2 pages not processed"
    monkeypatch.setitem(
        pipeline.STAGE_RUNNERS, "ingest", lambda ctx: StageOutcome(partial=True, partial_reason=msg)
    )
    monkeypatch.setitem(
        pipeline.STAGE_RUNNERS,
        "classify",
        lambda ctx: StageOutcome(partial=True, partial_reason=msg),
    )
    out = run_pipeline(case.id, db=db, settings=settings)
    assert out.partial_reasons.count(msg) == 1


def test_breach_not_reported_by_a_stage_is_added_by_the_pipeline(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env

    def ingest(ctx):  # noqa: ANN001, ANN202
        ctx.costs.claim_pages(ctx.settings.max_pages_per_case + 5)
        return StageOutcome()

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "ingest", ingest)
    out = run_pipeline(case.id, db=db, settings=settings)
    assert any(r.startswith("budget_exceeded: max_pages") for r in out.partial_reasons)


def test_costs_scope_warnings_and_fresh_meter_per_run(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env

    def extract(ctx):  # noqa: ANN001, ANN202
        req = LLMRequest(
            purpose="extract:invoice",
            system="s",
            user="u",
            schema_name="X",
            json_schema={"type": "object"},
            cache_extra="a" * 64 + ":1:text",
        )
        with ctx.costs.scope(document_id="d1", page=1):
            ctx.llm.complete_json(req, ctx_costs=ctx.costs)
        ctx.costs.record(
            purpose="x", model="mystery-model", prompt_tokens=1, completion_tokens=1, seconds=0
        )
        return StageOutcome(detail={"calls": 1})

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "extract", extract)
    fake = FakeLLMClient(default={})
    out = run_pipeline(case.id, db=db, settings=settings, llm=fake)
    ext = {s.stage: s for s in out.stages}["extract"]
    assert len(out.costs) == 2 and all(c.stage == "extract" for c in out.costs)
    assert (out.costs[0].document_id, out.costs[0].page, out.costs[0].model) == ("d1", 1, "fake")
    assert ext.cost_usd == Decimal("0") and ext.detail == {"calls": 1}
    assert "price_unknown: mystery-model" in ext.warnings
    second = run_pipeline(case.id, db=db, settings=settings, llm=fake)
    assert second.version == 2 and len(second.costs) == 2  # new meter: costs not accumulated


def test_walking_skeleton_flags_an_edited_balance_and_scores_it(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env
    with_documents(monkeypatch, statement(EDITED))
    out = run_pipeline(case.id, db=db, settings=settings)
    (flag,) = [f for f in out.flags if f.rule == "bs_running_balance"]
    assert flag.id == "bs_running_balance.d1.transactions:1" and flag.stage == "rules"
    assert out.score is not None and out.score.value == 30 and out.score.band == "medium"
    assert out.findings[0].weight == 30 and out.findings[0].flag_ids == [flag.id]
    assert out.top_finding and "row 2" in out.top_finding
    assert out.coverage is not None and out.coverage.applicable == len(out.check_runs)
    assert {s["rule"] for s in out.coverage.skipped} >= {"doc_unreadable"} or out.coverage.ran >= 1
    rules_stage = {s.stage: s for s in out.stages}["rules"]
    assert rules_stage.detail["flags"] >= 1 and rules_stage.detail["ran"] >= 1


def test_clean_statement_gives_zero_and_rerun_digest_is_identical(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env
    with_documents(monkeypatch, statement(CLEAN))
    first = run_pipeline(case.id, db=db, settings=settings)
    assert first.score is not None and first.score.value == 0 and not first.flags
    again = run_pipeline(case.id, db=db, settings=settings)
    assert again.version == 2
    assert first.provenance and again.provenance
    assert first.provenance.flags_digest == again.provenance.flags_digest
    assert first.provenance.ruleset_version == again.provenance.ruleset_version
    assert first.provenance.settings_hash == again.provenance.settings_hash

    with_documents(monkeypatch, statement(EDITED))
    third = run_pipeline(case.id, db=db, settings=settings)
    fourth = run_pipeline(case.id, db=db, settings=settings)
    assert third.provenance and fourth.provenance
    assert (
        third.provenance.flags_digest
        == fourth.provenance.flags_digest
        != first.provenance.flags_digest
    )


def test_stages_are_idempotent_on_one_context(env, monkeypatch) -> None:  # noqa: ANN001
    from app.checks.registry import run_stage_checks
    from app.score import run_stage

    ctx = make_ctx([statement(EDITED)])
    run_stage_checks(ctx, "rules")
    run_stage(ctx)

    def snap() -> dict:
        case = ctx.case.model_dump(mode="json")
        for r in case["check_runs"]:
            r["seconds"] = 0  # timing is the only thing allowed to differ
        return case

    before = snap()
    run_stage_checks(ctx, "rules")
    run_stage(ctx)
    assert snap() == before
    assert len(ctx.case.flags) == len({f.id for f in ctx.case.flags})


def test_rerun_snapshots_old_version_and_keeps_review_entries(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env
    with_documents(monkeypatch, statement(EDITED))
    first = run_pipeline(case.id, db=db, settings=settings)
    entry = ReviewEntry(
        id="r1",
        kind="verdict",
        reviewer="a.tan",
        decision="request_documents",
        reason="please send more",
        case_version=1,
        created_at=FIXED_NOW,
    )
    with db.conn() as c:
        c.execute(
            "INSERT INTO review_entries (id, case_id, case_version, created_at, body) VALUES (?,?,?,?,?)",
            (f"{case.id}/r1", case.id, 1, "2026-10-27T09:00:00.000000Z", entry.model_dump_json()),
        )
    second = run_pipeline(case.id, db=db, settings=settings)
    assert second.version == 2 and second.status == "open" and second.review == []
    old = db.load_case(case.id, version=1)
    assert old.version == 1 and [r.id for r in old.review] == ["r1"]
    assert (
        old.provenance
        and first.provenance
        and old.provenance.flags_digest == first.provenance.flags_digest
    )
    assert db.load_case(case.id).today == FIXED_TODAY  # reruns reuse the stored today


def test_prepare_rerun_is_not_bumped_twice(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env
    with_documents(monkeypatch, statement(CLEAN))
    run_pipeline(case.id, db=db, settings=settings)
    prepared = prepare_rerun(db, case.id)  # what an API does before queueing a job
    assert prepared.version == 2 and prepared.status == "processing" and prepared.stages == []
    assert prepared.flags == [] and prepared.score is None
    out = run_pipeline(case.id, db=db, settings=settings)
    assert out.version == 2  # run_pipeline saw a prepared case and did not bump again


def test_a_case_in_error_can_be_rerun(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env
    case = db.load_case(case.id)
    case.status = "error"
    db.save_case(case)
    out = run_pipeline(case.id, db=db, settings=settings)
    assert out.status == "open" and out.version == 2


def test_upload_applicant_survives_a_rerun(env) -> None:  # noqa: ANN001
    db, settings, _ = env
    case = new_case(
        settings,
        "kyc",
        applicant_name="Typed Name Pte Ltd",
        applicant_uen="201912345R",
        today=FIXED_TODAY,
    )
    db.create_case(case)
    run_pipeline(case.id, db=db, settings=settings)
    out = run_pipeline(case.id, db=db, settings=settings)
    assert out.applicant == Applicant(name="Typed Name Pte Ltd", uen="201912345R", source="upload")


# ---------------------------------------------------------------- applicant resolution


def profile(id: str, name: str, uen: str, doc_type: str = "company_profile"):  # noqa: ANN201, A002
    return make_doc(doc_type, id=id, fields={"company_name": name, "uen": uen})


def test_applicant_most_frequent_pair_wins() -> None:
    docs = [
        profile("d1", "Foreign Corp Pte Ltd", "201934567C", "financial_statement"),
        profile("d2", "Example Logistics Pte. Ltd.", " 2019 12345r "),
        profile("d3", "EXAMPLE LOGISTICS PTE LTD", "201912345R", "certificate_of_incorporation"),
    ]
    a = resolve_applicant(make_ctx(docs))
    assert a == Applicant(name="Example Logistics Pte. Ltd.", uen="201912345R", source="derived")


def test_applicant_falls_back_to_earliest_statement_holder_then_none() -> None:
    s2 = make_doc("bank_statement", id="d2", fields={"account_holder": "Later Pte Ltd"})
    s1 = make_doc(
        "bank_statement", id="d1", fields={"account_holder": "First Pte Ltd", "uen": TEST_UENS[1]}
    )
    a = resolve_applicant(make_ctx([s2, s1]))
    assert (a.name, a.uen, a.source) == ("First Pte Ltd", TEST_UENS[1], "derived")
    assert resolve_applicant(make_ctx([make_doc("invoice", id="d1")])) == Applicant()


def test_upload_values_are_authoritative_and_only_fill_gaps() -> None:
    typed = Applicant(name="Typed Pte Ltd", uen=None, source="upload")
    a = resolve_applicant(
        make_ctx([profile("d1", "Printed Pte Ltd", "201912345R")], applicant=typed)
    )
    assert (a.name, a.uen, a.source) == ("Typed Pte Ltd", "201912345R", "upload")


# ---------------------------------------------------------------- fingerprints


def invoice(id: str, uen: str, number: str, date: str, total: str):  # noqa: ANN201, A002
    return make_doc(
        "invoice",
        id=id,
        fields={
            "supplier_uen": uen,
            "invoice_number": number,
            "invoice_date": date,
            "total": total,
        },
    )


def test_record_fingerprints_files_and_invoices(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env
    case = db.load_case(case.id)
    add_file(case, db.files, name="a.pdf", data=PDF)
    db.save_case(case)
    docs = [
        invoice("d1", "201912345R", "INV-001", "2026-03-05", "1090.00").model_copy(
            update={"file_id": "f1"}
        ),
        invoice("d2", "201912345R", "INV-002", "2026-03-06", ""),  # no total: key only
    ]
    with_documents(monkeypatch, *docs)
    out = run_pipeline(case.id, db=db, settings=settings)
    store = SqliteFingerprintStore(db)
    (file_hit,) = store.lookup("file_sha256", out.files[0].sha256)
    assert (file_hit.case_id, file_hit.document_id) == (case.id, "d1")
    assert file_hit.meta == {"applicant_uen": None, "doc_type": "invoice"}
    full = invoice_fingerprint("201912345R", "INV-001", "2026-03-05", "1090.00")
    (full_hit,) = store.lookup("invoice", full)
    assert full_hit.meta["role"] == "full" and full_hit.document_id == "d1"
    (key_hit,) = store.lookup("invoice", invoice_key_fingerprint("201912345R", "INV-001"))
    assert key_hit.meta["role"] == "key"
    assert store.lookup("invoice", invoice_key_fingerprint("201912345R", "INV-002"))
    assert (
        store.lookup("invoice", invoice_fingerprint("201912345R", "INV-002", "2026-03-06", ""))
        == []
    )
    # a rerun never matches itself, but another case does
    assert store.lookup("invoice", full, exclude_case_id=case.id) == []
    run_pipeline(case.id, db=db, settings=settings)
    assert len(store.lookup("invoice", full)) == 1  # re-recorded, not duplicated


def test_record_fingerprints_unit() -> None:
    ctx = make_ctx([invoice("d1", "U1", "N1", "2026-01-01", "5.00")], applicant=Applicant(uen="U9"))
    n = record_fingerprints(ctx)
    assert n == 2  # full + key, no files in the case
    assert (
        ctx.fingerprints.lookup("invoice", invoice_key_fingerprint("U1", "N1"))[0].meta[
            "applicant_uen"
        ]
        == "U9"
    )


# ---------------------------------------------------------------- job runner


def test_job_runner_runs_a_case_to_success(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env
    with_documents(monkeypatch, statement(CLEAN))
    runner = JobRunner(db, settings)
    try:
        job = runner.submit(case.id)
        assert job.state == "queued"
        done = runner.wait(job.id, timeout=30)
    finally:
        runner.shutdown()
    assert done.state == "succeeded" and done.error is None and done.finished_at and done.started_at
    assert [s.stage for s in done.stages] == list(STAGES) and all(
        s.status == "ok" for s in done.stages
    )
    assert db.load_case(case.id).status == "open"


def test_job_runner_marks_a_crashed_run_failed(env, monkeypatch) -> None:  # noqa: ANN001
    db, settings, case = env

    def crash(ctx):  # noqa: ANN001, ANN202
        raise OSError("disk on fire")

    monkeypatch.setattr(pipeline, "build_provenance", crash)  # outside any stage: a runner crash
    runner = JobRunner(db, settings)
    try:
        job = runner.submit(case.id)
        done = runner.wait(job.id, timeout=30)
    finally:
        runner.shutdown()
    assert done.state == "failed" and done.error == "OSError: disk on fire"
    assert db.load_case(case.id).status == "error"  # recoverable with a rerun
    monkeypatch.undo()
    assert run_pipeline(case.id, db=db, settings=settings).status == "open"


def test_job_runner_is_bounded(env) -> None:  # noqa: ANN001
    db, settings, _ = env
    runner = JobRunner(db, Settings(_env_file=None, job_workers=1))
    try:
        assert runner._pool._max_workers == 1
    finally:
        runner.shutdown()


def test_malformed_registry_degrades_instead_of_crashing(env, tmp_path) -> None:  # noqa: ANN001
    db, settings, case = env
    bad = tmp_path / "registry.json"
    bad.write_text("{nope")
    s = settings.model_copy(update={"registry_path": bad})
    out = run_pipeline(case.id, db=db, settings=s)
    assert out.status == "open" and any(
        r.startswith("registry_unreadable") for r in out.partial_reasons
    )
