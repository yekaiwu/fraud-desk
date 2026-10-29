"""T-integration-loan-09: API and UI contract flow with real generated bundles.

Upload loan-fraud and loan-clean through ``POST /cases``, poll the jobs, check the queue ranks the
fraud case first, exercise dismiss + second approver, the case pack in JSON and HTML, then write the
API fixtures the frontend contract test (`npm test`) validates against ``types.ts``.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.llm import FakeLLMClient
from app.core.testing import FIXED_TODAY
from scripts.run_case import bundle_files, bundle_registry
from tests.e2e._support import make_settings, new_db, temp_root
from tests.test_api_support import write_fixture


@pytest.fixture(scope="module")
def flow(loan_bundles: dict[str, Path]):
    root = temp_root("gldb-e2e-api-")
    settings = make_settings(root, today=FIXED_TODAY)
    db = new_db(settings)
    application = main.create_app(
        settings, db=db, llm=FakeLLMClient(), registry=bundle_registry(loan_bundles["fraud"])
    )
    client = TestClient(application, raise_server_exceptions=False)

    def upload(variant: str, reference: str) -> dict:
        files = [
            ("files", (p.name, p.read_bytes(), "application/pdf"))
            for p in bundle_files(loan_bundles[variant])
        ]
        r = client.post("/cases", data={"use_case": "loan", "reference": reference}, files=files)
        assert r.status_code == 202, r.text
        return r.json()

    clean = upload("clean", "LOAN-CLEAN-001")  # uploaded first: age must not beat risk
    fraud = upload("fraud", "LOAN-FRAUD-001")
    jobs = {}
    for name, created in (("clean", clean), ("fraud", fraud)):
        deadline = time.monotonic() + 120
        while True:
            job = client.get(created["status_url"]).json()
            if job["state"] in ("succeeded", "failed") or time.monotonic() > deadline:
                break
            time.sleep(0.2)
        jobs[name] = job
    yield {"client": client, "app": application, "clean": clean, "fraud": fraud, "jobs": jobs}


def test_jobs_succeed_with_all_seven_stages(flow: dict) -> None:
    for name in ("clean", "fraud"):
        job = flow["jobs"][name]
        assert job["state"] == "succeeded", job
        assert [s["stage"] for s in job["stages"]] == [
            "ingest",
            "classify",
            "extract",
            "forensics",
            "rules",
            "crossdoc",
            "score",
        ]
        assert all(s["status"] == "ok" for s in job["stages"])
        assert job["progress"]["stage_index"] == 7 and job["error"] is None


def test_queue_ranks_the_fraud_case_above_the_clean_one(flow: dict) -> None:
    client: TestClient = flow["client"]
    queue = client.get("/cases").json()
    assert queue["total"] == 2
    first, second = queue["items"]
    assert first["case_id"] == flow["fraud"]["case_id"]
    assert second["case_id"] == flow["clean"]["case_id"]
    assert first["score"] > second["score"] == 0
    assert first["band"] in {"medium", "high"} and second["band"] == "low"
    assert first["lane"] == "loan" and first["high_count"] >= 1 and second["findings_count"] == 0
    assert first["top_finding"] and first["file_count"] == 32 and first["partial"] is False
    # the same ordering under the explicit sort, and filters agree
    by_score = client.get("/cases", params={"sort": "score"}).json()["items"]
    assert [i["case_id"] for i in by_score] == [first["case_id"], second["case_id"]]
    high = client.get("/cases", params={"band": "high,medium"}).json()
    assert [i["case_id"] for i in high["items"]] == [first["case_id"]]
    write_fixture("queue_page", queue)


def test_case_view_for_a_real_bundle(flow: dict) -> None:
    client: TestClient = flow["client"]
    view = client.get(f"/cases/{flow['fraud']['case_id']}").json()
    assert view["status"] == "open" and view["use_case"] == "loan"
    assert view["applicant"]["uen"] and view["applicant"]["source"] == "derived"
    assert len(view["files"]) == 32 and all(f["pdf_meta"] for f in view["files"])
    assert {"x_revenue_vs_credits", "x_invoice_vs_receipts", "x_invoice_vs_contract"} <= {
        f["rule"] for f in view["flags"]
    }
    for fl in view["flags"]:
        for r in [fl["region"], *fl["extra_regions"]]:
            if r:
                assert 0 <= r["x"] <= 1 and 0 <= r["y"] <= 1 and r["x"] + r["w"] <= 1.0001
    assert all("words" not in d for d in view["documents"])
    assert view["cost_summary"]["calls"] >= 0 and set(view["cost_summary"]["seconds_by_stage"]) >= {
        "ingest",
        "score",
    }
    # page image of the first file renders
    fid = view["files"][0]["id"]
    img = client.get(f"/cases/{view['id']}/pages/1", params={"file_id": fid})
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    write_fixture("case_detail", view)
    write_fixture("job", flow["jobs"]["fraud"])


def test_dismiss_a_high_flag_needs_a_second_approver_then_decide(flow: dict) -> None:
    client: TestClient = flow["client"]
    case_id = flow["fraud"]["case_id"]
    view = client.get(f"/cases/{case_id}").json()
    high = [f for f in view["flags"] if f["severity"] == "high"]
    assert high
    target = high[0]["id"]

    def post(body: dict):  # noqa: ANN202
        return client.post(f"/cases/{case_id}/review", json={"case_version": 1, **body})

    r = post(
        {
            "kind": "flag_decision",
            "decision": "dismiss",
            "flag_ids": [target],
            "reviewer": "a.tan",
            "reason": "Reviewer believes the printed subtotal is a typo.",
        }
    )
    assert r.status_code == 201, r.text
    assert r.json()["entry"]["status"] == "pending_second_approval"
    entry_id = r.json()["entry"]["id"]
    # the same reviewer cannot approve their own dismissal
    same = post(
        {
            "kind": "second_approval",
            "decision": "approve",
            "target_entry_id": entry_id,
            "reviewer": "a.tan",
            "reason": "Approving my own dismissal.",
        }
    )
    assert same.status_code == 409 and same.json()["error"]["code"] == "second_approver_must_differ"
    # the approve verdict is blocked while a high flag is only pending
    blocked = post(
        {
            "kind": "verdict",
            "decision": "approve",
            "reviewer": "a.tan",
            "reason": "Looks fine to me.",
        }
    )
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "high_flags_open"
    ok = post(
        {
            "kind": "second_approval",
            "decision": "approve",
            "target_entry_id": entry_id,
            "reviewer": "b.lim",
            "reason": "Agree, confirmed with the auditor.",
        }
    )
    assert ok.status_code == 201, ok.text
    after = client.get(f"/cases/{case_id}").json()
    assert next(f for f in after["flags"] if f["id"] == target)["disposition"] == "dismissed"
    assert after["pending_second_approvals"] == []
    # analysis is immutable: the score did not change
    assert after["score"] == view["score"]
    write_fixture("review_response", ok.json())
    # the scripted analyst closes the case
    verdict = post(
        {
            "kind": "verdict",
            "decision": "reject_or_escalate",
            "reviewer": "a.tan",
            "reason": "Revenue not supported by bank credits.",
        }
    )
    assert verdict.status_code == 201 and verdict.json()["case"]["status"] == "closed"
    assert client.get(f"/cases/{case_id}").json()["decision"] == "reject_or_escalate"


def test_case_pack_json_and_html(flow: dict) -> None:
    client: TestClient = flow["client"]
    case_id = flow["fraud"]["case_id"]
    pack = client.get(f"/cases/{case_id}/pack")
    assert pack.status_code == 200 and pack.headers["content-type"].startswith("application/json")
    body = pack.json()
    assert body["disclaimer"] and "Decision support" in body["disclaimer"]
    assert body["case"]["id"] == case_id if "case" in body else True
    html = client.get(f"/cases/{case_id}/pack", params={"format": "html"})
    assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
    text = html.text
    assert "<script" not in text.lower()
    assert not re.search(r"""(?:src|href)=["']https?://""", text)  # self-contained, no requests
    assert "Decision support" in text
    write_fixture("pack", body)


def test_effectiveness_and_error_fixtures_for_the_contract_test(flow: dict) -> None:
    client: TestClient = flow["client"]
    eff = client.get("/rules/effectiveness")
    assert eff.status_code == 200
    body = eff.json()
    assert body["cases_total"] == 2 and body["cases_decided"] == 1
    fired = {r["rule"]: r for r in body["rules"] if r["times_fired"]}
    assert "x_revenue_vs_credits" in fired
    bad = client.get("/cases/c_doesnotexist")
    assert bad.status_code == 404 and set(bad.json()) == {"error"}
    write_fixture("effectiveness", body)
    write_fixture("error_response", bad.json())
