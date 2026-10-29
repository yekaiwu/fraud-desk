"""Versions, add documents and rerun (review-workflow tests 15, 17, 30, 31; api.md section 6)."""

from __future__ import annotations

import threading
from typing import Any

import pytest

import app.pipeline as pipeline
from app.core.config import Settings
from app.core.db import Database
from app.core.models import StageOutcome
from tests.test_api_support import Env, make_env, mk_flag, review_body, tiny_pdf

LOW = mk_flag("bs_dates", "low", doc="d1", locator="transactions:5")
HIGH = mk_flag("bs_running_balance", "high")


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def err(resp) -> dict:  # noqa: ANN001
    assert set(resp.json()) == {"error"}
    return resp.json()["error"]


def wait(env: Env, accepted: dict[str, Any]) -> dict[str, Any]:
    job = env.app.state.runner.wait(accepted["job_id"], timeout=120)
    assert job.state == "succeeded", job.error
    return env.client.get(f"/cases/{accepted['case_id']}").json()


def add_files(env: Env, case_id: str, *contents: bytes):  # noqa: ANN201
    return env.client.post(
        f"/cases/{case_id}/files",
        files=[("files", (f"extra{i}.pdf", c, "application/pdf")) for i, c in enumerate(contents)],
    )


@pytest.fixture
def blocked_ingest(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Hold the ingest stage so a case stays 'processing' for as long as the test needs."""
    release = threading.Event()

    def slow(ctx):  # noqa: ANN001, ANN202
        release.wait(timeout=60)
        return StageOutcome()

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "ingest", slow)
    yield release
    release.set()


# 15. request documents, then add a file -----------------------------------------------------------


def test_request_documents_then_adding_files_makes_version_two(env: Env, blocked_ingest) -> None:  # noqa: ANN001
    case = env.seed(flags=[LOW, HIGH])
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOW.id]))
    r = env.review(
        case.id, **review_body("verdict", "request_documents", reason="Need the March statement")
    )
    assert r.json()["case"]["status"] == "awaiting_documents"

    r = add_files(env, case.id, tiny_pdf("March"))
    assert r.status_code == 202, r.text
    accepted = r.json()
    assert (
        set(accepted) == {"case_id", "job_id", "status_url", "version"} and accepted["version"] == 2
    )
    assert r.headers["location"] == accepted["status_url"]

    # while the job is held the case is processing and everything refuses
    during = env.client.get(f"/cases/{case.id}").json()
    assert during["status"] == "processing" and during["version"] == 2
    assert during["files"][0]["added_in_version"] == 2
    assert (
        env.review(case.id, **review_body("verdict", "approve", case_version=2)).status_code == 409
    )
    assert env.client.post(f"/cases/{case.id}/rerun").json()["error"]["code"] == "case_processing"
    assert add_files(env, case.id, tiny_pdf()).json()["error"]["code"] == "case_processing"
    assert env.client.get(f"/cases/{case.id}/pack").json()["error"]["code"] == "case_processing"
    assert (
        env.client.delete(
            f"/cases/{case.id}", params={"reviewer": "a.tan", "reason": "cleanup"}
        ).json()["error"]["code"]
        == "case_processing"
    )
    blocked_ingest.set()

    v2 = wait(env, accepted)
    assert v2["status"] == "open" and v2["version"] == 2
    assert all(f["disposition"] == "open" for f in v2["flags"])
    assert v2["review"] == []  # entries carry their case_version
    all_entries = env.client.get(f"/cases/{case.id}/review").json()["entries"]
    assert [(e["id"], e["case_version"]) for e in all_entries] == [("r1", 1), ("r2", 1)]
    assert env.client.get(f"/cases/{case.id}/review", params={"version": 2}).json() == {
        "entries": []
    }

    versions = env.client.get(f"/cases/{case.id}/versions").json()["versions"]
    assert [(v["version"], v["reason"], v["current"]) for v in versions] == [
        (1, "created", False),
        (2, "documents_added", True),
    ]
    v1 = env.client.get(f"/cases/{case.id}", params={"version": 1}).json()
    assert v1["version"] == 1 and v1["allowed_verdicts"] == []
    assert {f["id"]: f["disposition"] for f in v1["flags"]} == {
        LOW.id: "dismissed",
        HIGH.id: "open",
    }
    assert [e["id"] for e in v1["review"]] == ["r1", "r2"]
    # the new version can be reviewed again from scratch
    r = env.review(case.id, **review_body("verdict", "request_documents", case_version=2))
    assert r.status_code == 201 and r.json()["entry"]["id"] == "r3"
    assert r.json()["entry"]["case_version"] == 2
    actions = [e["action"] for e in env.audit(case.id)]
    assert "case.documents_added" in actions and actions.count("case.analysis_finished") == 1
    added = next(e for e in env.audit(case.id) if e["action"] == "case.documents_added")
    assert added["detail"]["version_from"] == 1 and added["detail"]["version_to"] == 2
    assert len(added["detail"]["file_sha256s"]) == 1


# 17. guards ------------------------------------------------------------------------------------------


def test_closed_cases_take_neither_files_nor_reruns(env: Env) -> None:
    case = env.seed(flags=[], status="closed", decision="approve")
    for call in (
        lambda: env.client.post(f"/cases/{case.id}/rerun"),
        lambda: add_files(env, case.id, tiny_pdf()),
    ):
        r = call()
        assert r.status_code == 409 and err(r)["code"] == "case_not_open"
        assert err(r)["details"] == {"status": "closed"}
    assert env.client.get(f"/cases/{case.id}").json()["version"] == 1


def test_unknown_case_files_rerun_and_versions_are_404(env: Env) -> None:
    assert env.client.post("/cases/c_x/rerun").status_code == 404
    assert add_files(env, "c_x", tiny_pdf()).status_code == 404
    assert env.client.get("/cases/c_x/versions").status_code == 404


def test_invalid_files_add_nothing_and_do_not_bump_the_version(env: Env) -> None:
    case = env.seed(flags=[LOW])
    bad = add_files(env, case.id, tiny_pdf(), b"not a pdf")
    assert bad.status_code == 415 and err(bad)["code"] == "unsupported_media_type"
    none = env.client.post(f"/cases/{case.id}/files")
    assert none.status_code == 422 and err(none)["code"] == "no_files"
    detail = env.client.get(f"/cases/{case.id}").json()
    assert detail["version"] == 1 and detail["status"] == "open" and detail["files"] == []
    assert env.client.get(f"/cases/{case.id}/versions").json()["versions"][0]["reason"] == "created"


# 30. version history -----------------------------------------------------------------------------------


def test_versions_created_rerun_documents_added_and_per_version_review(env: Env) -> None:
    case = env.seed(flags=[LOW, HIGH])
    env.review(case.id, **review_body("flag_decision", "confirm", flag_ids=[HIGH.id]))

    rr = env.client.post(f"/cases/{case.id}/rerun")
    assert rr.status_code == 202 and rr.json()["version"] == 2
    wait(env, rr.json())
    added = add_files(env, case.id, tiny_pdf("third"))
    assert added.status_code == 202 and added.json()["version"] == 3
    v3 = wait(env, added.json())

    versions = env.client.get(f"/cases/{case.id}/versions").json()["versions"]
    assert [(v["version"], v["reason"], v["current"]) for v in versions] == [
        (1, "created", False),
        (2, "rerun", False),
        (3, "documents_added", True),
    ]
    assert versions[0]["score"] == 35 and versions[0]["band"] == "medium"
    assert all(v["created_at"] for v in versions)
    v1 = env.client.get(f"/cases/{case.id}", params={"version": 1}).json()
    assert [(e["id"], e["case_version"]) for e in v1["review"]] == [("r1", 1)]
    assert {f["id"]: f["disposition"] for f in v1["flags"]}[HIGH.id] == "confirmed"
    assert env.client.get(f"/cases/{case.id}", params={"version": 2}).json()["review"] == []
    assert v3["review"] == [] and v3["version"] == 3
    actions = [e["action"] for e in env.audit(case.id)]
    assert actions[-4:] == [
        "case.rerun",
        "case.analysis_finished",
        "case.documents_added",
        "case.analysis_finished",
    ]


def test_awaiting_documents_case_can_be_rerun_back_to_open(env: Env) -> None:
    case = env.seed(flags=[LOW])
    env.review(case.id, **review_body("verdict", "request_documents"))
    rr = env.client.post(f"/cases/{case.id}/rerun")
    assert rr.status_code == 202
    again = wait(env, rr.json())
    assert again["status"] == "open" and again["version"] == 2 and again["allowed_verdicts"]


# 31. reproducibility ------------------------------------------------------------------------------------


def test_rerun_with_identical_inputs_gives_the_same_flags_digest(env: Env) -> None:
    created = env.client.post(
        "/cases",
        data={"use_case": "loan", "applicant_name": "Example Trading Pte. Ltd."},
        files=[("files", ("statement.pdf", tiny_pdf("Statement"), "application/pdf"))],
    ).json()
    v1 = wait(env, created)
    rr = env.client.post(f"/cases/{created['case_id']}/rerun")
    v2 = wait(env, rr.json())
    assert v2["version"] == 2 and v1["version"] == 1
    old = env.client.get(f"/cases/{created['case_id']}", params={"version": 1}).json()
    assert old["provenance"]["flags_digest"] == v2["provenance"]["flags_digest"]
    assert old["today"] == v2["today"]  # the injected date is kept, so results reproduce
    assert [f["id"] for f in old["flags"]] == [f["id"] for f in v2["flags"]]
    assert old["score"]["value"] == v2["score"]["value"]
    assert old["provenance"]["input_hashes"] == v2["provenance"]["input_hashes"]
