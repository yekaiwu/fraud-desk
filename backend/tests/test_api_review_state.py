"""Review state machine, concurrency, atomicity and honest quirks (review-workflow tests 17, 20 to 22, 34)."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.audit import AuditLog
from app.core.config import Settings
from app.core.db import Database
from app.core.models import CheckRun
from tests.test_api_support import Env, make_env, mk_flag, review_body

HIGH = mk_flag("bs_running_balance", "high", doc="d1", locator="transactions:3")
LOW = mk_flag("bs_dates", "low", doc="d1", locator="transactions:5")


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def test_second_approver_setting_off_makes_a_high_dismissal_effective_at_once(
    settings: Settings, db: Database
) -> None:
    env = make_env(settings.model_copy(update={"require_second_approver": False}), db)
    case = env.seed(flags=[HIGH, LOW])
    r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    assert r.status_code == 201 and r.json()["entry"]["status"] == "effective"
    flags = {f["id"]: f["disposition"] for f in env.client.get(f"/cases/{case.id}").json()["flags"]}
    assert flags[HIGH.id] == "dismissed"
    again = env.review(
        case.id, **review_body("second_approval", "approve", target_entry_id="r1", reviewer="b.lim")
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "entry_not_pending"
    assert env.client.get("/health").json()["settings"]["require_second_approver"] is False


def test_two_threads_countersigning_the_same_entry_give_one_201_and_one_409(
    settings: Settings, db: Database
) -> None:
    env = make_env(settings, db)
    case = env.seed(flags=[HIGH])
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    results: list[int] = []
    codes: list[str | None] = []
    barrier = threading.Barrier(2)

    def countersign(name: str) -> None:
        client = TestClient(env.app, raise_server_exceptions=False)
        barrier.wait()
        r = client.post(
            f"/cases/{case.id}/review",
            json=review_body("second_approval", "approve", target_entry_id="r1", reviewer=name),
        )
        results.append(r.status_code)
        codes.append(r.json().get("error", {}).get("code"))

    threads = [threading.Thread(target=countersign, args=(n,)) for n in ("b.lim", "c.ng")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(results) == [201, 409], (results, codes)
    assert "entry_not_pending" in codes
    entries = env.client.get(f"/cases/{case.id}/review").json()["entries"]
    assert [e["kind"] for e in entries].count("second_approval") == 1
    assert entries[0]["status"] == "effective"


def test_a_failing_audit_insert_rolls_back_the_entry_and_the_status_change(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = env.seed(flags=[])
    real = AuditLog.write

    def boom(self, **kw):  # noqa: ANN001, ANN003, ANN202
        if kw.get("action") == "review.verdict":
            raise RuntimeError("audit store unavailable")
        return real(self, **kw)

    monkeypatch.setattr(AuditLog, "write", boom)
    r = env.review(case.id, **review_body("verdict", "approve"))
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert "audit store" not in r.text and "Traceback" not in r.text
    monkeypatch.undo()
    assert env.client.get(f"/cases/{case.id}/review").json() == {"entries": []}
    detail = env.client.get(f"/cases/{case.id}").json()
    assert detail["status"] == "open" and detail["decision"] is None
    assert all(e["action"] != "review.verdict" for e in env.audit(case.id))
    # the case still takes a review afterwards
    assert env.review(case.id, **review_body("verdict", "approve")).status_code == 201


def test_a_failing_audit_insert_also_rolls_back_a_flag_decision(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = env.seed(flags=[LOW])

    def boom(self, **kw):  # noqa: ANN001, ANN003, ANN202
        raise RuntimeError("down")

    monkeypatch.setattr(AuditLog, "write", boom)
    r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOW.id]))
    assert r.status_code == 500
    monkeypatch.undo()
    assert env.client.get(f"/cases/{case.id}/review").json() == {"entries": []}


def test_entry_ids_are_per_case_and_never_reused_across_cases(env: Env) -> None:
    a, b = env.seed(flags=[LOW]), env.seed(flags=[LOW])
    for case in (a, b):
        r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOW.id]))
        assert r.json()["entry"]["id"] == "r1"
    r = env.review(a.id, **review_body("flag_decision", "confirm", flag_ids=[LOW.id]))
    assert r.json()["entry"]["id"] == "r2"
    with env.db.conn() as c:
        ids = {row["id"] for row in c.execute("SELECT id FROM review_entries")}
    assert ids == {f"{a.id}/r1", f"{a.id}/r2", f"{b.id}/r1"}


def test_review_entries_only_change_through_a_second_approval(env: Env) -> None:
    case = env.seed(flags=[HIGH])
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    with env.db.conn() as c:
        before = c.execute("SELECT body FROM review_entries").fetchone()["body"]
    env.review(case.id, **review_body("verdict", "request_documents"))
    with env.db.conn() as c:
        rows = c.execute("SELECT body FROM review_entries ORDER BY rowid").fetchall()
    assert rows[0]["body"] == before  # a later verdict never rewrites an earlier entry
    assert len(rows) == 2


# 34. honest quirks -----------------------------------------------------------------------------------


def test_quirk_a_meaningless_five_character_reason_is_accepted(env: Env) -> None:
    case = env.seed(flags=[])
    assert (
        env.review(case.id, **review_body("verdict", "approve", reason="xxxxx")).status_code == 201
    )


def test_quirk_the_same_person_typing_two_spellings_is_still_caught_but_two_names_are_not(
    env: Env,
) -> None:
    case = env.seed(flags=[HIGH])
    env.review(
        case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id], reviewer="b.lim")
    )
    same = env.review(
        case.id, **review_body("second_approval", "approve", target_entry_id="r1", reviewer="B.Lim")
    )
    assert same.status_code == 409 and same.json()["error"]["code"] == "second_approver_must_differ"
    # no authentication: one human typing a different name passes (documented limit)
    other = env.review(
        case.id,
        **review_body("second_approval", "approve", target_entry_id="r1", reviewer="b.lim2"),
    )
    assert other.status_code == 201


def test_quirk_a_partial_case_can_be_approved_and_the_audit_says_partial(env: Env) -> None:
    case = env.seed(
        flags=[],
        partial=True,
        check_runs=[
            CheckRun(rule="bs_running_balance", stage="rules", status="ran"),
            CheckRun(rule="inv_dates", stage="rules", status="error", reason="boom"),
        ],
    )
    detail = env.client.get(f"/cases/{case.id}").json()
    assert detail["partial"] is True and "approve" in detail["allowed_verdicts"]
    r = env.review(
        case.id, **review_body("verdict", "approve", reason="Documents verified by phone")
    )
    assert r.status_code == 201
    row = next(e for e in env.audit(case.id) if e["action"] == "review.verdict")
    assert row["detail"]["partial"] is True
    assert row["detail"]["coverage_ratio"] == 0.5
    assert row["detail"]["score"] == 0 and row["detail"]["band"] == "low"
    assert row["detail"]["status_before"] == "open" and row["detail"]["status_after"] == "closed"


def test_every_rejected_rule_attempt_is_audited(env: Env) -> None:
    case = env.seed(flags=[HIGH])
    env.review(case.id, **review_body("verdict", "approve"))  # high_flags_open
    env.review(case.id, **review_body("verdict", "refer_edd"))  # verdict_not_allowed
    env.review(case.id, **review_body("verdict", "request_documents", case_version=9))  # stale
    env.review(case.id, **review_body("verdict", "approve", reason="no"))  # reason_required
    env.review(case.id, **review_body("verdict", "approve", reviewer="x"))  # reviewer_required
    codes = [e["detail"]["code"] for e in env.audit(case.id) if e["action"] == "review.rejected"]
    assert codes == [
        "high_flags_open",
        "verdict_not_allowed",
        "stale_version",
        "reason_required",
        "reviewer_required",
    ]
    assert env.client.get(f"/cases/{case.id}/review").json() == {"entries": []}
    detail = next(e for e in env.audit(case.id) if e["detail"].get("code") == "reason_required")[
        "detail"
    ]
    assert detail["reason_len"] == 2 and "reason" not in detail


def test_the_database_transaction_alone_serialises_racing_service_calls(
    settings: Settings, db: Database
) -> None:
    """Without the router lock (a second process, say) BEGIN IMMEDIATE still lets only one win."""
    from app.audit import AuditLog
    from app.core.errors import ApiError
    from app.review import ReviewService, parse_review_request

    env = make_env(settings, db)
    case = env.seed(flags=[HIGH])
    env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def countersign(name: str) -> None:
        service = ReviewService(db, AuditLog(db, settings), settings)
        req = parse_review_request(
            review_body("second_approval", "approve", target_entry_id="r1", reviewer=name)
        )
        barrier.wait()
        try:
            service.submit(case.id, req)
            outcomes.append("ok")
        except ApiError as exc:
            outcomes.append(exc.code)

    threads = [threading.Thread(target=countersign, args=(n,)) for n in ("b.lim", "c.ng")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(outcomes) == ["entry_not_pending", "ok"]
