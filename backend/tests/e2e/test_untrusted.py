"""T-integration-loan-11: determinism and untrusted input end to end (plan 7.2, principle 5).

Documents are written by the person being checked. A company name like ``"); DROP TABLE cases; --``
or ``<script>`` must flow through extraction, the database, the API and the case-pack HTML as inert
data: no behaviour change, no executed text, escaped output.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.pipeline import run_pipeline
from scripts import make_bundle
from scripts._gen.schema import BundleSpec, Mutation
from tests.e2e._support import Run, build_bundles, run_bundle, temp_root

HOSTILE = '"); DROP TABLE cases; -- <script>alert(1)</script> Pte. Ltd.'


@pytest.fixture(scope="module")
def hostile() -> Run:
    root = temp_root("gldb-e2e-hostile-")
    base = build_bundles(root, variants=("clean",))["clean"]
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    uen = manifest["applicant"]["uen"]
    roles = {
        "bank_statement": "account_holder",
        "invoice": "supplier",
        "financial_statement": "company",
    }
    muts = [
        Mutation(
            op="swap_party",
            target=d["key"],
            args={"role": roles[d["doc_type"]], "name": HOSTILE, "uen": uen},
        )
        for d in manifest["documents"]
        if d["doc_type"] in roles
    ]
    bdir = root / "bundles" / "hostile"
    make_bundle.build_bundle(BundleSpec(scenario="loan", variant="clean", mutations=muts), bdir)
    return run_bundle(bdir, tmp=root / "run")


def test_hostile_names_go_through_every_stage_without_behaviour_change(hostile: Run) -> None:
    case = hostile.case
    assert case.status == "open" and all(s.status == "ok" for s in case.stages)
    assert not [r for r in case.check_runs if r.status == "error"]
    assert case.applicant.name == HOSTILE  # kept byte for byte, as data
    # the only difference from the clean control is the honest one: names disagree for one UEN
    assert {f.rule for f in case.flags} == {"bs_benford", "doc_unclassified", "x_entity"}
    assert hostile.llm.calls == []


def test_database_is_intact_and_parameterised(hostile: Run) -> None:
    with hostile.db.conn() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"cases", "case_versions", "jobs", "fingerprints", "audit_log"} <= tables
        n = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        assert n == 1
        row = conn.execute(
            "SELECT applicant_name FROM cases WHERE id = ?", (hostile.case.id,)
        ).fetchone()
    assert row["applicant_name"] == HOSTILE
    assert hostile.db.load_case(hostile.case.id).applicant.name == HOSTILE


def test_api_returns_the_text_as_json_data_and_filters_on_it(hostile: Run) -> None:
    application = main.create_app(
        hostile.settings, db=hostile.db, llm=hostile.llm, registry=hostile.registry
    )
    client = TestClient(application, raise_server_exceptions=False)
    view = client.get(f"/cases/{hostile.case.id}")
    assert view.status_code == 200 and view.headers["content-type"].startswith("application/json")
    assert view.json()["applicant"]["name"] == HOSTILE
    queue = client.get("/cases", params={"q": "DROP TABLE"}).json()
    assert [i["applicant_name"] for i in queue["items"]] == [HOSTILE]
    nothing = client.get("/cases", params={"q": "'; DELETE FROM cases; --"}).json()
    assert nothing["total"] == 0
    assert client.get("/cases").json()["total"] == 1  # nothing was dropped


def test_case_pack_html_escapes_everything_extracted(hostile: Run) -> None:
    application = main.create_app(
        hostile.settings, db=hostile.db, llm=hostile.llm, registry=hostile.registry
    )
    client = TestClient(application, raise_server_exceptions=False)
    html = client.get(f"/cases/{hostile.case.id}/pack", params={"format": "html"})
    assert html.status_code == 200
    text = html.text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert len(re.findall(r"<script", text, flags=re.IGNORECASE)) == 0
    pack = client.get(f"/cases/{hostile.case.id}/pack").json()
    assert HOSTILE in json.dumps(pack)  # JSON keeps the data; the browser never parses it as HTML


def test_hostile_applicant_field_on_upload_is_data_too(
    loan_bundles: dict[str, Path], tmp_path: Path
) -> None:
    from app.core.llm import FakeLLMClient
    from scripts.run_case import bundle_files
    from tests.e2e._support import make_settings, new_db

    settings = make_settings(tmp_path)
    db = new_db(settings)
    application = main.create_app(settings, db=db, llm=FakeLLMClient())
    client = TestClient(application, raise_server_exceptions=False)
    files = [
        ("files", (p.name, p.read_bytes(), "application/pdf"))
        for p in bundle_files(loan_bundles["clean"])[:4]
    ]
    name = "<img src=x onerror=alert(1)> '; DROP TABLE cases;--"
    r = client.post("/cases", data={"use_case": "loan", "applicant_name": name}, files=files)
    assert r.status_code == 202
    job = application.state.runner.wait(r.json()["job_id"], timeout=120)
    assert job.state == "succeeded"
    pack = client.get(f"/cases/{r.json()['case_id']}/pack", params={"format": "html"}).text
    assert "<img src=x" not in pack and "&lt;img src=x" in pack
    assert client.get("/cases").json()["total"] == 1


def test_fresh_databases_give_identical_digests_flag_ids_and_findings(
    loan_runs: dict[str, Run], loan_bundles: dict[str, Path], tmp_path: Path
) -> None:
    again = run_bundle(loan_bundles["fraud"], tmp=tmp_path)
    first = loan_runs["fraud"].case
    assert again.case.id != first.id  # different case, different database, different paths
    assert again.case.provenance.flags_digest == first.provenance.flags_digest
    assert [f.id for f in again.case.flags] == [f.id for f in first.flags]
    assert [f.id for f in again.case.findings] == [f.id for f in first.findings]
    assert again.case.score == first.score and again.case.top_finding == first.top_finding
    assert again.case.provenance.ruleset_version == first.provenance.ruleset_version


def test_rerun_gives_identical_digest_and_flag_ids(
    loan_bundles: dict[str, Path], tmp_path: Path
) -> None:
    run = run_bundle(loan_bundles["forger"], tmp=tmp_path)
    again = run_pipeline(
        run.case.id, db=run.db, settings=run.settings, llm=run.llm, registry=run.registry
    )
    assert again.version == 2
    assert again.provenance.flags_digest == run.case.provenance.flags_digest
    assert [f.id for f in again.flags] == [f.id for f in run.case.flags]
    assert again.today == run.case.today  # the rerun reuses the stored date
