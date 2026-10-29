"""T-integration-loan-07: fingerprint hooks, cost wiring and rerun semantics in ``pipeline.py``."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.pipeline as pipeline
from app.core.llm import FakeLLMClient
from app.core.models import StageOutcome
from app.pipeline import run_pipeline
from scripts.run_case import analyse_files, bundle_files
from tests.e2e._support import (
    PricedFake,
    make_settings,
    new_db,
    run_bundle,
    tampered_loan_bundle,
    temp_root,
)


def _fp_count(run, kind: str, case_id: str | None = None) -> int:  # noqa: ANN001
    with run.db.conn() as conn:
        sql = "SELECT COUNT(*) FROM fingerprints WHERE kind = ?"
        args: list = [kind]
        if case_id:
            sql += " AND case_id = ?"
            args.append(case_id)
        return int(conn.execute(sql, args).fetchone()[0])


def test_second_submission_of_the_same_bundle_is_flagged_on_the_second_case_only(
    loan_bundles: dict[str, Path], tmp_path: Path
) -> None:
    settings = make_settings(tmp_path)
    db = new_db(settings)
    first = run_bundle(loan_bundles["clean"], settings=settings, db=db)
    second = run_bundle(loan_bundles["clean"], settings=settings, db=db)
    assert first.case.id != second.case.id
    # first case: nothing to match against
    assert not first.flags("inv_duplicate")
    assert not [f for f in first.flags("file_reuse")]
    # second case: every invoice and the files are recognised from the first case
    dup = second.flags("inv_duplicate")
    assert len(dup) == 14 and all(f.severity == "high" for f in dup)
    assert first.case.id in dup[0].evidence or "earlier case" in dup[0].evidence.lower()
    reused = second.flags("file_reuse")
    assert reused and all(first.case.id in f.evidence for f in reused)
    assert {f.severity for f in reused} <= {"info", "low", "medium"}
    # a re-submission of the same applicant's own files is not a "different applicant" alarm
    assert all(f.severity == "info" for f in reused)
    # the first case's stored analysis is untouched by the second submission
    assert db.load_case(first.case.id).provenance.flags_digest == first.case.provenance.flags_digest


def test_fingerprints_are_recorded_for_files_invoices_and_signature_crops(
    loan_runs,  # noqa: ANN001
) -> None:
    run = loan_runs["clean"]
    case = run.case
    assert _fp_count(run, "file_sha256", case.id) == len(case.files) == 32
    # one full + one key fingerprint per invoice
    assert _fp_count(run, "invoice", case.id) == 28
    crops = _fp_count(run, "crop_phash", case.id)
    assert crops >= 1  # contracts carry signature images (CR-27)
    with run.db.conn() as conn:
        row = conn.execute(
            "SELECT meta, document_id FROM fingerprints WHERE kind = 'crop_phash' AND case_id = ?",
            (case.id,),
        ).fetchone()
    assert "applicant_uen" in row["meta"] and row["document_id"].startswith("d")


def test_rerun_never_matches_itself_and_keeps_the_same_digest(
    loan_bundles: dict[str, Path], tmp_path: Path
) -> None:
    run = run_bundle(loan_bundles["clean"], tmp=tmp_path)
    again = run_pipeline(
        run.case.id,
        db=run.db,
        settings=run.settings,
        llm=run.llm,
        registry=run.registry,
    )
    assert again.version == 2
    assert not [f for f in again.flags if f.rule in {"inv_duplicate", "file_reuse"}]
    assert again.provenance.flags_digest == run.case.provenance.flags_digest
    assert [f.id for f in again.flags] == [f.id for f in run.case.flags]
    # the rerun replaced, not appended, this case's fingerprints
    assert _fp_count(run, "file_sha256", run.case.id) == 32
    assert _fp_count(run, "invoice", run.case.id) == 28


def test_rerun_snapshots_the_old_version_and_keeps_review_entries(
    loan_bundles: dict[str, Path], tmp_path: Path
) -> None:
    run = run_bundle(loan_bundles["fraud"], tmp=tmp_path)
    application = main.create_app(run.settings, db=run.db, llm=run.llm, registry=run.registry)
    client = TestClient(application, raise_server_exceptions=False)
    case_id = run.case.id
    flag_id = next(f.id for f in run.case.flags if f.severity == "medium")
    r = client.post(
        f"/cases/{case_id}/review",
        json={
            "kind": "flag_decision",
            "decision": "confirm",
            "flag_ids": [flag_id],
            "reviewer": "a.tan",
            "reason": "Checked against the bank statement.",
            "case_version": 1,
        },
    )
    assert r.status_code == 201, r.text
    rr = client.post(f"/cases/{case_id}/rerun")
    assert rr.status_code == 202, rr.text
    job = application.state.runner.wait(rr.json()["job_id"], timeout=120)
    assert job.state == "succeeded", job.error
    view = client.get(f"/cases/{case_id}").json()
    assert view["version"] == 2 and view["status"] == "open"
    versions = client.get(f"/cases/{case_id}/versions").json()["versions"]
    assert [v["version"] for v in versions][:2] in ([2, 1], [1, 2])
    log = client.get(f"/cases/{case_id}/review").json()["entries"]
    assert len(log) == 1 and log[0]["case_version"] == 1  # kept, tied to the version it saw
    old = client.get(f"/cases/{case_id}", params={"version": 1}).json()
    assert old["provenance"]["flags_digest"] == view["provenance"]["flags_digest"]
    assert any(f["id"] == flag_id and f["disposition"] == "confirmed" for f in old["flags"])


def test_cost_meter_is_fresh_per_run_and_scoped_per_stage(tmp_path: Path) -> None:
    """A rerun starts a new budget; costs are attributed to the stage that made the call."""
    bdir = tampered_loan_bundle(tmp_path / "src", "one", ("2025-09",))
    run = run_bundle(bdir, tmp=tmp_path / "run", llm=PricedFake(usd="0.25"))
    priced = [c for c in run.case.costs if c.usd > 0]
    assert priced and all(c.usd == Decimal("0.25") for c in priced)
    assert {c.stage for c in run.case.costs} == {"extract"}
    n_first = len(run.case.costs)
    again = run_pipeline(
        run.case.id, db=run.db, settings=run.settings, llm=run.llm, registry=run.registry
    )
    assert len(again.costs) == n_first  # a fresh meter: nothing carried over from version 1
    assert {c.stage for c in again.costs} == {"extract"}
    extract = next(s for s in again.stages if s.stage == "extract")
    assert extract.status == "ok" and extract.cost_usd == Decimal("0.25") * len(priced)
    assert all(s.cost_usd == 0 for s in again.stages if s.stage != "extract")


def test_partial_reasons_are_deduplicated_and_warnings_drained_per_stage(
    loan_bundles: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def partial_stage(ctx) -> StageOutcome:  # noqa: ANN001
        ctx.costs.add_warning("price_unknown: acme-model")
        return StageOutcome(partial=True, partial_reason="same reason", warnings=["w1"])

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "forensics", partial_stage)
    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "crossdoc", partial_stage)
    run = run_bundle(loan_bundles["clean"], tmp=tmp_path)
    assert run.case.partial_reasons.count("same reason") == 1
    assert run.case.score is not None and run.case.score.partial
    forensics = next(s for s in run.case.stages if s.stage == "forensics")
    crossdoc = next(s for s in run.case.stages if s.stage == "crossdoc")
    assert forensics.warnings == ["w1", "price_unknown: acme-model"]
    assert crossdoc.warnings == ["w1"]  # the cost warning is queued once per run, not repeated


def test_a_failed_stage_is_recorded_and_the_rest_still_run(
    loan_bundles: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(ctx) -> StageOutcome:  # noqa: ANN001
        raise RuntimeError("synthetic failure")

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "forensics", boom)
    run = run_bundle(loan_bundles["fraud"], tmp=tmp_path)
    by_stage = {s.stage: s for s in run.case.stages}
    assert (
        by_stage["forensics"].status == "failed"
        and "synthetic failure" in by_stage["forensics"].error
    )
    assert by_stage["rules"].status == "ok" and by_stage["score"].status == "ok"
    assert "stage_failed:forensics" in run.case.partial_reasons
    assert run.case.score.partial and run.case.status == "open"
    assert run.rules(min_severity="medium") >= {"x_revenue_vs_credits"}


def test_analyse_files_twice_in_one_db_uses_the_same_storage(
    loan_bundles: dict[str, Path],
) -> None:
    root = temp_root("gldb-e2e-fp-")
    settings = make_settings(root)
    db = new_db(settings)
    paths = bundle_files(loan_bundles["clean"])[:3]
    a = analyse_files(
        paths, use_case="loan", today=run_today(), db=db, settings=settings, llm=FakeLLMClient()
    )
    b = analyse_files(
        paths, use_case="loan", today=run_today(), db=db, settings=settings, llm=FakeLLMClient()
    )
    assert {f.sha256 for f in a.files} == {f.sha256 for f in b.files}
    assert len(list(db.files.files_dir.rglob("*"))) > 0


def run_today():  # noqa: ANN201
    from app.core.testing import FIXED_TODAY

    return FIXED_TODAY
