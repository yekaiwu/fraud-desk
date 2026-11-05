"""Findings from the adversarial review: who may purge, who is recorded as the actor or approver,
the incident limiter and the request size caps."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

import app.main as main
from tests.test_access import REMOTE, SECRET, H, assert_envelope, make_client
from tests.test_api_support import make_env, mk_flag, seed_case, tiny_pdf

BOSS = {**H, "X-Gldb-Reviewer": "boss"}
USER = {**H, "X-Gldb-Reviewer": "user1"}


def signed_in_client(settings, monkeypatch, **update) -> TestClient:
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    s = settings.model_copy(update={"admin_users": "boss", "personal_accounts": True, **update})
    return TestClient(main.create_app(s), client=REMOTE)


def rows(c: TestClient, action: str) -> list[dict]:
    with c.app.state.db.conn() as conn:
        found = conn.execute("SELECT * FROM audit_log WHERE action = ?", (action,)).fetchall()
    return [dict(r) for r in found]


# --------------------------------------------------------------------------- purge is admin only


def test_purge_needs_an_administrator_account_when_accounts_are_personal(
    settings, monkeypatch
) -> None:
    c = signed_in_client(settings, monkeypatch)
    for hdr in (H, USER):
        for body in (None, {"dry_run": False, "older_than_days": 0}):
            r = c.post("/admin/retention/purge", headers=hdr, json=body)
            assert r.status_code == 403
            assert_envelope(r.json(), "forbidden")


def test_purge_by_an_administrator_records_that_account(settings, monkeypatch) -> None:
    c = signed_in_client(settings, monkeypatch)
    case = seed_case(c.app.state.db, c.app.state.settings, flags=[], status="closed")
    r = c.post(
        "/admin/retention/purge", headers=BOSS, json={"dry_run": False, "older_than_days": 0}
    )
    assert r.status_code == 200 and r.json()["case_ids"] == [case.id]
    assert {x["actor"] for x in rows(c, "case.deleted")} == {"boss"}
    assert [x["actor"] for x in rows(c, "retention.purge")] == ["boss"]


def test_purge_keeps_working_with_typed_names_and_no_accounts(settings, db) -> None:
    env = make_env(settings, db)
    r = env.client.post("/admin/retention/purge")
    assert r.status_code == 200 and r.json()["dry_run"] is True


def test_case_delete_records_the_signed_in_account_not_a_typed_name(settings, monkeypatch) -> None:
    c = signed_in_client(settings, monkeypatch)
    case = seed_case(c.app.state.db, c.app.state.settings, flags=[], status="closed")
    r = c.delete(
        f"/cases/{case.id}",
        headers=USER,
        params={"reviewer": "someone.else", "reason": "duplicate upload"},
    )
    assert r.status_code == 204
    assert [x["actor"] for x in rows(c, "case.deleted")] == ["user1"]


# --------------------------------------------------------------------------- approver and actor


def test_a_signed_in_account_is_the_approver_whatever_name_was_typed(settings, monkeypatch) -> None:
    c = signed_in_client(settings, monkeypatch)
    body = {"approver": "Director Lim", "role": "Chief Risk Officer", "note": "reviewed today"}
    r = c.post("/monitoring/thresholds/approve", headers=USER, json=body)
    assert r.status_code == 201
    assert r.json()["approver"] == "user1" and r.json()["recorded_by"] == "user1"
    detail = json.loads(rows(c, "monitoring.thresholds_approved")[0]["detail"])
    assert detail["approver"] == "user1"
    # the typed name is kept beside the account, in its own field
    assert r.json()["approver_name"] == "Director Lim" and detail["approver_name"] == "Director Lim"


def test_the_typed_approver_name_is_cleaned_and_capped_and_skipped_when_it_is_the_account(
    settings, monkeypatch
) -> None:
    c = signed_in_client(settings, monkeypatch)
    body = {"approver": "  Director   Lim " + "x" * 200, "role": "CRO", "note": "reviewed today"}
    r = c.post("/monitoring/thresholds/approve", headers=USER, json=body)
    assert r.status_code == 422  # the form limit (80) still applies to what is typed
    body["approver"] = "  Director   Lim  "
    r = c.post("/monitoring/thresholds/approve", headers=USER, json=body)
    assert r.status_code == 201 and r.json()["approver_name"] == "Director Lim"
    same = c.post(
        "/monitoring/thresholds/approve", headers=USER, json={**body, "approver": "user1"}
    )
    assert same.status_code == 201 and "approver_name" not in same.json()


def test_typed_approver_is_kept_without_accounts(settings, db) -> None:
    env = make_env(settings, db)
    body = {"approver": "Director Lim", "role": "Chief Risk Officer", "note": "reviewed today"}
    r = env.client.post("/monitoring/thresholds/approve", json=body)
    assert r.status_code == 201 and r.json()["approver"] == "Director Lim"
    assert (
        "approver_name" not in r.json()
    )  # nobody signed in: the typed name is the approver itself


def test_case_actions_are_audited_under_the_signed_in_account(settings, monkeypatch) -> None:
    c = signed_in_client(settings, monkeypatch, max_active_jobs=0)
    pdf = tiny_pdf()
    files = [("files", ("a.pdf", pdf, "application/pdf"))]
    r = c.post("/cases", headers=USER, data={"use_case": "loan"}, files=files)
    assert r.status_code == 202
    case_id = r.json()["case_id"]
    assert [x["actor"] for x in rows(c, "case.created")] == ["user1"]
    db, cfg = c.app.state.db, c.app.state.settings
    # cases of their own, so the job started above cannot race with these calls
    open_a = seed_case(db, cfg, flags=[], files=[("a.pdf", pdf)])
    open_b = seed_case(db, cfg, flags=[], files=[("a.pdf", pdf)])
    assert c.post(f"/cases/{open_a.id}/files", headers=BOSS, files=files).status_code == 202
    assert c.post(f"/cases/{open_b.id}/rerun", headers=USER).status_code == 202
    assert [x["actor"] for x in rows(c, "case.documents_added")] == ["boss"]
    assert [x["actor"] for x in rows(c, "case.rerun")] == ["user1"]
    quiet = seed_case(db, cfg, flags=[], files=[("a.pdf", pdf)])
    case_id = quiet.id
    fid = quiet.files[0].id
    assert c.get(f"/cases/{case_id}/files/{fid}", headers=BOSS).status_code == 200
    assert c.get(f"/cases/{case_id}/pack", headers=USER).status_code == 200
    assert [x["actor"] for x in rows(c, "file.downloaded")] == ["boss"]
    assert [x["actor"] for x in rows(c, "pack.exported")] == ["user1"]


def test_without_an_account_these_actions_stay_anonymous(settings, db) -> None:
    env = make_env(settings, db)
    case = seed_case(db, settings, flags=[mk_flag()], files=[("a.pdf", tiny_pdf())])
    assert env.client.get(f"/cases/{case.id}/pack").status_code == 200
    assert {r["actor"] for r in env.audit_rows() if r["action"] == "pack.exported"} == {"anonymous"}


# --------------------------------------------------------------------------- incident limiter


def test_incident_reports_share_the_review_limiter(settings, monkeypatch) -> None:
    c = make_client(
        settings, monkeypatch, client=REMOTE, proxy_secret=SECRET, rate_reviews_per_min="2"
    )
    body = {
        "severity": "high",
        "summary": "The model returned unrelated text.",
        "reviewer": "ops.lead",
    }
    assert c.post("/monitoring/incidents", headers=H, json=body).status_code == 201
    assert c.post("/monitoring/incidents", headers=H, json=body).status_code == 201
    r = c.post("/monitoring/incidents", headers=H, json=body)
    assert r.status_code == 429
    assert assert_envelope(r.json(), "rate_limited")["details"]["scope"] == "reviews"
    assert int(r.headers["retry-after"]) >= 1
    # reading the log is not limited, and the same bucket serves reviews
    assert c.get("/monitoring/incidents", headers=H).status_code == 200
    assert c.post("/cases/c_x/review", headers=H, json={}).status_code == 429


# --------------------------------------------------------------------------- body size


def test_an_oversized_upload_is_refused_before_it_is_read(settings, monkeypatch) -> None:
    s = settings.model_copy(update={"max_files_per_case": 2, "max_upload_mb": 1})
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    c = TestClient(main.create_app(s), client=REMOTE)
    cap = 2 * 1024 * 1024 + 1024 * 1024
    for path in ("/cases", "/cases/c_1/files"):
        r = c.post(
            path,
            headers={**H, "Content-Length": str(cap + 1), "Content-Type": "multipart/form-data"},
            content=b"x",
        )
        assert r.status_code == 413, path
        err = assert_envelope(r.json(), "file_too_large")
        assert err["details"]["max_files"] == 2 and err["details"]["max_mb"] == 1
        assert r.headers["x-request-id"]
    # a request within the cap is not stopped by the size check
    files = [("files", ("a.pdf", tiny_pdf(), "application/pdf"))]
    assert c.post("/cases", headers=H, data={"use_case": "loan"}, files=files).status_code == 202


def test_a_form_with_too_many_files_is_a_422_not_a_spool_of_thousands(
    settings, monkeypatch
) -> None:
    s = settings.model_copy(update={"max_files_per_case": 2})
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    c = TestClient(main.create_app(s), client=REMOTE)
    files = [("files", (f"{i}.pdf", b"%PDF-1.4", "application/pdf")) for i in range(50)]
    r = c.post("/cases", headers=H, data={"use_case": "loan"}, files=files)
    assert r.status_code == 422
    assert assert_envelope(r.json(), "too_many_files")["details"]["max"] == 2
    # one over the limit still gets read_uploads' own message
    three = files[:3]
    r = c.post("/cases", headers=H, data={"use_case": "loan"}, files=three)
    assert r.status_code == 422 and r.json()["error"]["code"] == "too_many_files"
    assert r.json()["error"]["details"]["received"] == 3
