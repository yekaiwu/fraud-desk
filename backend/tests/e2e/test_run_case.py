"""T-integration-loan-01: ``run_case.py`` and ``analyse_files`` use the same path as ``POST /cases``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.llm import FakeLLMClient
from app.core.testing import FIXED_TODAY
from scripts import run_case
from tests.e2e._support import make_settings, new_db, run_bundle, temp_root


def test_analyse_files_equals_post_cases_for_the_same_files(
    loan_bundles: dict[str, Path], tmp_path: Path
) -> None:
    bdir = loan_bundles["fraud"]
    # path A: the helper the eval imports
    a = run_bundle(bdir, tmp=tmp_path / "a", today=FIXED_TODAY)
    # path B: the real API, real job runner
    settings = make_settings(tmp_path / "b", today=FIXED_TODAY)
    db = new_db(settings)
    application = main.create_app(
        settings, db=db, llm=FakeLLMClient(), registry=run_case.bundle_registry(bdir)
    )
    client = TestClient(application, raise_server_exceptions=False)
    files = [
        ("files", (p.name, p.read_bytes(), "application/pdf")) for p in run_case.bundle_files(bdir)
    ]
    created = client.post("/cases", data={"use_case": "loan"}, files=files)
    assert created.status_code == 202, created.text
    job = application.state.runner.wait(created.json()["job_id"], timeout=120)
    assert job.state == "succeeded", job.error
    b = db.load_case(created.json()["case_id"])

    assert a.case.provenance and b.provenance
    assert a.case.provenance.flags_digest == b.provenance.flags_digest
    assert [f.id for f in a.case.flags] == [f.id for f in b.flags]
    assert (a.case.score.value, a.case.score.band) == (b.score.value, b.score.band)
    assert (a.case.lane, a.case.top_finding) == (b.lane, b.top_finding)
    assert a.case.coverage.model_dump() == b.coverage.model_dump()
    assert [f.sha256 for f in a.case.files] == [f.sha256 for f in b.files]
    assert a.case.applicant == b.applicant and a.case.today == b.today == FIXED_TODAY


def test_cli_prints_stages_score_lane_and_top_finding(
    loan_bundles: dict[str, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = run_case.main(
        ["--bundle", str(loan_bundles["clean"]), "--timings", "--data-dir", str(tmp_path / "d")]
    )
    out = capsys.readouterr().out
    assert code == 0
    for stage in ("ingest", "classify", "extract", "forensics", "rules", "crossdoc", "score"):
        assert stage in out
    assert "score 0 band=low lane=" in out and "top finding:" in out and "seconds" in out
    assert "checks not run" in out


def test_cli_json_is_the_full_case(
    loan_bundles: dict[str, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = run_case.main(
        ["--bundle", str(loan_bundles["forger"]), "--json", "--data-dir", str(tmp_path)]
    )
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["status"] == "open" and data["use_case"] == "loan"
    assert data["today"] == "2026-10-27" and data["score"]["band"] in {"medium", "high"}
    assert {f["rule"] for f in data["flags"]} >= {"x_revenue_vs_credits"}


def test_cli_scenario_variant_reads_the_samples_folder(
    loan_root: Path, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = run_case.main(
        [
            "--scenario",
            "loan",
            "--variant",
            "clean",
            "--samples",
            str(loan_root),
            "--data-dir",
            str(tmp_path),
        ]
    )
    assert code == 0 and "case c_" in capsys.readouterr().out


def test_cli_missing_bundle_tells_how_to_build_it(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        run_case.main(["--scenario", "loan", "--variant", "clean", "--samples", str(tmp_path)])
    assert "make_bundle.py" in str(exc.value)


def test_analyse_files_never_reads_the_wall_clock(loan_bundles: dict[str, Path]) -> None:
    run = run_bundle(loan_bundles["clean"], tmp=temp_root(), today=FIXED_TODAY)
    assert run.case.today == FIXED_TODAY
