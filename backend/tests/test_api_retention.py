"""Delete-case and retention purge (review-workflow tests 25 to 27, section 4.11)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.audit import AuditLog, case_ref_hash
from app.core.config import Settings
from app.core.db import Database
from app.core.fingerprints import SqliteFingerprintStore
from app.retention import expired_case_ids, purge
from tests.test_api_support import (
    FIXED_NOW,
    Env,
    FakeClock,
    make_env,
    mk_flag,
    review_body,
    tiny_pdf,
)

NAME = "Tan Wei Ming"
NRIC = "S1234567D"


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db, FakeClock())


def delete(env: Env, case_id: str, **params: str):  # noqa: ANN201
    return env.client.delete(f"/cases/{case_id}", params=params)


def dump_database(env: Env) -> str:
    with env.db.conn() as c:
        return "\n".join(c.iterdump())


def seed_with_data(env: Env):  # noqa: ANN202
    pdf = tiny_pdf(f"Statement for {NAME}")
    case = env.seed(
        flags=[mk_flag("x_directors", "high", doc="d9", locator="-", disc="tan_wei_ming")],
        applicant_name=NAME,
        applicant_uen="201912345R",
        reference="ACME-1",
        files=[("statement.pdf", pdf)],
    )
    sha = case.files[0].sha256
    SqliteFingerprintStore(env.db).record(
        "file_sha256", sha, case_id=case.id, document_id="d1", meta={"applicant_uen": "201912345R"}
    )
    with env.db.conn() as c:
        c.execute(
            "INSERT INTO llm_cache (key, value, created_at, model, purpose) VALUES (?,?,?,?,?)",
            (
                "k1",
                json.dumps({"file_sha256": sha, "data": {}}),
                "2026-10-27T00:00:00Z",
                "fake",
                "x",
            ),
        )
        c.execute(
            "INSERT INTO llm_cache (key, value, created_at, model, purpose) VALUES (?,?,?,?,?)",
            ("k2", json.dumps({"file_sha256": "f" * 64}), "2026-10-27T00:00:00Z", "fake", "x"),
        )
    return case, sha


# 25. delete ---------------------------------------------------------------------------------------


def test_delete_removes_everything_and_leaves_a_hashed_audit_trail(env: Env) -> None:
    case, sha = seed_with_data(env)
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[case.flags[0].id]))
    env.client.get(f"/cases/{case.id}/pack")
    assert env.db.files.exists(sha)

    r = delete(env, case.id, reviewer="a.tan", reason=f"duplicate upload {NRIC}")
    assert r.status_code == 204 and r.content == b""
    assert env.client.get(f"/cases/{case.id}").status_code == 404
    again = delete(env, case.id, reviewer="a.tan", reason="second attempt")
    assert again.status_code == 404 and again.json()["error"]["details"]["kind"] == "case"

    # nothing about the applicant remains in the database or on disk
    dump = dump_database(env)
    for forbidden in (NAME, "Tan Wei", NRIC, "201912345R", "ACME-1", "tan_wei_ming", case.id):
        assert forbidden not in dump, forbidden
    assert not env.db.files.exists(sha)
    assert not (env.db.files.renders_dir / sha).exists()
    with env.db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM review_entries").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM fingerprints").fetchone()[0] == 0
        keys = [r["key"] for r in c.execute("SELECT key FROM llm_cache")]
    assert keys == ["k2"]  # the cache rows of the freed file are pruned, others stay

    rows = env.audit_rows()
    assert rows, "the audit trail is kept"
    expected = case_ref_hash(case.id)
    assert {r["case_ref"] for r in rows} == {expected}
    deleted = next(r for r in rows if r["action"] == "case.deleted")
    detail = json.loads(deleted["detail"])
    assert detail["reason"] == "duplicate upload [id]"
    assert detail["trigger"] == "manual" and detail["files"] == 1 and detail["versions"] == 1
    assert detail["had_escalation"] is False and detail["decision"] is None
    assert deleted["actor"] == "a.tan"
    assert NAME not in json.dumps(rows) and NRIC not in json.dumps(rows)
    # the trail can still be read by someone who knows the hash path (for_case matches both forms)
    assert [e["action"] for e in AuditLog(env.db, env.settings).for_case(case.id)][
        -1
    ] == "case.deleted"


def test_other_cases_sharing_a_file_keep_it(env: Env) -> None:
    pdf = tiny_pdf("shared")
    a = env.seed(flags=[], files=[("a.pdf", pdf)])
    b = env.seed(flags=[], files=[("b.pdf", pdf)])
    assert delete(env, a.id, reviewer="a.tan", reason="not needed").status_code == 204
    assert env.db.files.exists(b.files[0].sha256)
    assert env.client.get(f"/cases/{b.id}").status_code == 200


# 26. guards -------------------------------------------------------------------------------------------


def test_delete_refuses_a_processing_case_and_bad_parameters(env: Env) -> None:
    processing = env.seed(flags=[], status="processing")
    r = delete(env, processing.id, reviewer="a.tan", reason="cleanup please")
    assert r.status_code == 409 and r.json()["error"]["code"] == "case_processing"
    case = env.seed(flags=[])
    assert delete(env, case.id, reason="long enough").json()["error"]["code"] == "reviewer_required"
    assert delete(env, case.id, reviewer="a", reason="long enough").status_code == 422
    assert delete(env, case.id, reviewer="a.tan").json()["error"]["code"] == "reason_required"
    assert delete(env, case.id, reviewer="a.tan", reason="abcd").status_code == 422
    assert delete(env, case.id, reviewer="a.tan", reason="   ").status_code == 422
    assert delete(env, "c_nope", reviewer="a.tan", reason="long enough").status_code == 404
    assert env.client.get(f"/cases/{case.id}").status_code == 200
    assert delete(env, case.id, reviewer="a.tan", reason="abcde").status_code == 204


def test_a_failing_delete_is_audited_and_the_case_stays(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = env.seed(flags=[])

    def boom(self, case_id):  # noqa: ANN001, ANN202
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(Database, "delete_case", boom)
    r = delete(env, case.id, reviewer="a.tan", reason="clean up")
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "disk on fire" not in r.text
    monkeypatch.undo()
    assert env.client.get(f"/cases/{case.id}").status_code == 200
    actions = [row["action"] for row in env.audit_rows()]
    assert actions == ["case.deleted", "case.delete_failed"]


# 27. retention ----------------------------------------------------------------------------------------


def seed_retention(env: Env):  # noqa: ANN202
    old = FIXED_NOW - timedelta(days=2000)
    recent = FIXED_NOW - timedelta(days=100)
    return {
        "old_closed": env.seed(
            flags=[], status="closed", decision="approve", updated_at=old, created_at=old
        ),
        "recent_closed": env.seed(
            flags=[], status="closed", decision="approve", updated_at=recent, created_at=recent
        ),
        "old_open": env.seed(flags=[], status="open", updated_at=old, created_at=old),
        "old_escalated": env.seed(
            flags=[],
            status="closed",
            decision="escalate_aml",
            updated_at=old,
            created_at=old,
            escalated_at=old,
        ),
        "old_error": env.seed(flags=[], status="error", updated_at=old, created_at=old),
        "old_awaiting": env.seed(
            flags=[], status="awaiting_documents", updated_at=old, created_at=old
        ),
    }


def test_retention_dry_run_lists_only_closed_or_errored_unescalated_old_cases(env: Env) -> None:
    cases = seed_retention(env)
    r = env.client.post("/admin/retention/purge")  # no body: defaults, dry run on
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert set(body["case_ids"]) == {cases["old_closed"].id, cases["old_error"].id}
    assert body["count"] == 2
    for case in cases.values():
        assert env.client.get(f"/cases/{case.id}").status_code == 200  # nothing deleted
    assert [r["action"] for r in env.audit_rows()] == []


def test_retention_purge_deletes_and_audits_with_the_system_actor(env: Env) -> None:
    cases = seed_retention(env)
    r = env.client.post("/admin/retention/purge", json={"dry_run": False})
    assert r.status_code == 200 and r.json()["dry_run"] is False and r.json()["count"] == 2
    gone = {cases["old_closed"].id, cases["old_error"].id}
    assert set(r.json()["case_ids"]) == gone
    for name, case in cases.items():
        expected = 404 if case.id in gone else 200
        assert env.client.get(f"/cases/{case.id}").status_code == expected, name
    rows = env.audit_rows()
    deleted = [x for x in rows if x["action"] == "case.deleted"]
    assert len(deleted) == 2 and {x["actor"] for x in deleted} == {"system"}
    assert {json.loads(x["detail"])["trigger"] for x in deleted} == {"retention"}
    assert {json.loads(x["detail"])["reason"] for x in deleted} == {"retention"}
    purge_row = next(x for x in rows if x["action"] == "retention.purge")
    detail = json.loads(purge_row["detail"])
    assert detail["count"] == 2 and detail["older_than_days"] == 1825
    assert sorted(detail["case_ref_hashes"]) == sorted(case_ref_hash(i) for i in gone)
    assert purge_row["case_ref"] is None
    again = env.client.post("/admin/retention/purge", json={"dry_run": False})
    assert again.json()["count"] == 0


def test_retention_window_can_be_overridden_and_is_validated(env: Env) -> None:
    cases = seed_retention(env)
    short = env.client.post("/admin/retention/purge", json={"older_than_days": 50}).json()
    assert cases["recent_closed"].id in short["case_ids"] and short["count"] == 3
    assert (
        env.client.post("/admin/retention/purge", json={"older_than_days": -1}).status_code == 422
    )
    assert env.client.post("/admin/retention/purge", json={"dry_run": "maybe"}).status_code == 422
    assert env.client.post("/admin/retention/purge", json={"surprise": 1}).status_code == 422


def test_retention_days_setting_is_the_default(settings: Settings, db: Database) -> None:
    env = make_env(settings.model_copy(update={"retention_days": 30}), db, FakeClock())
    cases = seed_retention(env)
    r = env.client.post("/admin/retention/purge").json()
    assert cases["recent_closed"].id in r["case_ids"]


def test_expired_case_ids_and_purge_functions_directly(env: Env) -> None:
    cases = seed_retention(env)
    ids = expired_case_ids(env.db, older_than_days=1825, now=FIXED_NOW)
    assert set(ids) == {cases["old_closed"].id, cases["old_error"].id}
    assert expired_case_ids(env.db, older_than_days=1825, now=FIXED_NOW - timedelta(days=400)) == []
    log = AuditLog(env.db, env.settings, clock=env.clock)
    dry = purge(env.db, log, older_than_days=1825, dry_run=True, now=FIXED_NOW)
    assert dry["count"] == 2 and dry["dry_run"] is True
    assert env.client.get(f"/cases/{cases['old_closed'].id}").status_code == 200
