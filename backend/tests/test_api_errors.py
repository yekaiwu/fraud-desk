"""The error map of api.md section 1: one body, one code per situation, X-Request-Id, no traces
(review-workflow test 33, tasks T-api-backend-11)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.audit import AuditLog
from app.core.config import Settings
from app.core.db import Database
from tests.test_api_support import (
    Env,
    make_env,
    mk_flag,
    review_body,
    tiny_pdf,
    write_fixture,
)

HIGH = mk_flag("bs_running_balance", "high")
LOW = mk_flag("bs_dates", "low", doc="d1", locator="transactions:5")


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def post_pdf(env: Env, files: list[tuple[str, bytes]], **form: str):  # noqa: ANN201
    return env.client.post(
        "/cases",
        data={"use_case": "loan", **form},
        files=[("files", (n, c, "application/pdf")) for n, c in files],
    )


def scenario_calls(env: Env) -> dict[str, tuple[Callable[[], object], int, str]]:
    """name -> (call, expected status, expected code)"""
    open_case = env.seed(flags=[HIGH, LOW])
    closed = env.seed(flags=[], status="closed", decision="approve")
    processing = env.seed(flags=[], status="processing")
    env.review(open_case.id, **review_body("flag_decision", "dismiss", flag_ids=[HIGH.id]))
    cid = open_case.id
    review = lambda **kw: env.client.post(  # noqa: E731
        f"/cases/{cid}/review", json=review_body(**kw) if "kind" in kw else kw
    )
    return {
        "malformed_json": (
            lambda: env.client.post(
                f"/cases/{cid}/review",
                content=b"{not json",
                headers={"content-type": "application/json"},
            ),
            400,
            "bad_request",
        ),
        "malformed_multipart": (
            lambda: env.client.post(
                "/cases",
                content=b"garbage",
                headers={"content-type": "multipart/form-data; boundary=zz"},
            ),
            400,
            "bad_request",
        ),
        "not_found_case": (lambda: env.client.get("/cases/c_missing"), 404, "not_found"),
        "not_found_job": (lambda: env.client.get("/jobs/j_missing"), 404, "not_found"),
        "not_found_file": (lambda: env.client.get(f"/cases/{cid}/files/f9"), 404, "not_found"),
        "not_found_document": (
            lambda: env.client.get(f"/cases/{cid}/documents/d9/words"),
            404,
            "not_found",
        ),
        "not_found_flag": (
            lambda: review(kind="flag_decision", decision="confirm", flag_ids=["nope.d1.-"]),
            404,
            "not_found",
        ),
        "case_processing": (
            lambda: env.client.post(
                f"/cases/{processing.id}/review", json=review_body("verdict", "approve")
            ),
            409,
            "case_processing",
        ),
        "case_not_open": (
            lambda: env.client.post(
                f"/cases/{closed.id}/review", json=review_body("verdict", "approve")
            ),
            409,
            "case_not_open",
        ),
        "high_flags_open": (
            lambda: review(kind="verdict", decision="approve", reviewer="c.ng"),
            409,
            "high_flags_open",
        ),
        "second_approver_must_differ": (
            lambda: review(kind="second_approval", decision="approve", target_entry_id="r1"),
            409,
            "second_approver_must_differ",
        ),
        "stale_version": (
            lambda: review(kind="verdict", decision="request_documents", case_version=5),
            409,
            "stale_version",
        ),
        "flag_decision_pending": (
            lambda: review(kind="flag_decision", decision="confirm", flag_ids=[HIGH.id]),
            409,
            "flag_decision_pending",
        ),
        "entry_not_pending": (
            lambda: (
                review(
                    kind="second_approval",
                    decision="approve",
                    target_entry_id="r1",
                    reviewer="b.lim",
                )
                and review(
                    kind="second_approval",
                    decision="approve",
                    target_entry_id="r1",
                    reviewer="c.ng",
                )
            ),
            409,
            "entry_not_pending",
        ),
        "file_too_large": (lambda: None, 413, "file_too_large"),  # covered with a small limit below
        "unsupported_media_type": (
            lambda: post_pdf(env, [("a.pdf", b"text")]),
            415,
            "unsupported_media_type",
        ),
        "validation_error": (
            lambda: env.client.post(f"/cases/{cid}/review", json={"kind": "nope"}),
            422,
            "validation_error",
        ),
        "reason_required": (
            lambda: review(kind="verdict", decision="approve", reason="abc"),
            422,
            "reason_required",
        ),
        "reviewer_required": (
            lambda: review(kind="verdict", decision="approve", reviewer="x"),
            422,
            "reviewer_required",
        ),
        "verdict_not_allowed": (
            lambda: review(kind="verdict", decision="refer_edd"),
            422,
            "verdict_not_allowed",
        ),
        "no_files": (lambda: env.client.post("/cases", data={"use_case": "loan"}), 422, "no_files"),
        "unknown_route": (lambda: env.client.get("/nowhere"), 404, "not_found"),
        "wrong_method": (lambda: env.client.put("/health"), 405, "method_not_allowed"),
    }


def test_every_error_has_a_stable_code_one_body_and_a_request_id(env: Env) -> None:
    calls = scenario_calls(env)
    seen = set()
    for name, (call, status, code) in calls.items():
        if name == "file_too_large":
            continue
        resp = call()
        body = resp.json()
        assert resp.status_code == status, (name, resp.status_code, resp.text)
        assert set(body) == {"error"}, name
        assert set(body["error"]) == {"code", "message", "details"}, name
        assert body["error"]["code"] == code, (name, body)
        assert isinstance(body["error"]["message"], str) and body["error"]["message"], name
        assert resp.headers["x-request-id"], name
        assert "Traceback" not in resp.text and 'File "' not in resp.text, name
        seen.add(code)
    assert seen >= {
        "bad_request",
        "not_found",
        "case_processing",
        "case_not_open",
        "high_flags_open",
        "second_approver_must_differ",
        "entry_not_pending",
        "stale_version",
        "flag_decision_pending",
        "unsupported_media_type",
        "validation_error",
        "reason_required",
        "reviewer_required",
        "verdict_not_allowed",
        "no_files",
    }


def test_oversize_and_too_many_files_codes(settings: Settings, db: Database) -> None:
    env = make_env(settings.model_copy(update={"max_upload_mb": 1, "max_files_per_case": 1}), db)
    big = post_pdf(env, [("big.pdf", b"%PDF-" + b"0" * (1024 * 1024 + 1))])
    assert big.status_code == 413 and big.json()["error"]["code"] == "file_too_large"
    many = post_pdf(env, [("a.pdf", tiny_pdf()), ("b.pdf", tiny_pdf("b"))])
    assert many.status_code == 422 and many.json()["error"]["code"] == "too_many_files"
    assert big.headers["x-request-id"] and many.headers["x-request-id"]


def test_request_id_is_echoed_on_errors_and_internal_errors_hide_details(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = env.client.get("/cases/c_missing", headers={"X-Request-Id": "trace-me-42"})
    assert r.status_code == 404 and r.headers["x-request-id"] == "trace-me-42"

    def boom(self, case_id):  # noqa: ANN001, ANN202
        raise RuntimeError("secret internal detail /etc/passwd")

    case = env.seed(flags=[])
    monkeypatch.setattr(AuditLog, "for_case", boom)
    r = env.client.get(f"/cases/{case.id}/audit", headers={"X-Request-Id": "trace-500"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal_error"
    assert r.json()["error"]["details"] == {"request_id": "trace-500"}
    assert "secret" not in r.text and "Traceback" not in r.text and "passwd" not in r.text


def test_no_route_answers_501_any_more(env: Env) -> None:
    case = env.seed(flags=[])
    routes = [
        ("GET", "/cases"),
        ("GET", f"/cases/{case.id}"),
        ("GET", f"/cases/{case.id}/versions"),
        ("GET", f"/cases/{case.id}/review"),
        ("GET", f"/cases/{case.id}/audit"),
        ("GET", f"/cases/{case.id}/pack"),
        ("GET", "/rules/effectiveness"),
        ("GET", "/rules"),
        ("GET", "/jobs/j_x"),
        ("POST", "/admin/retention/purge"),
    ]
    for method, path in routes:
        assert env.client.request(method, path).status_code != 501, (method, path)


def test_review_response_fixture_for_the_frontend_contract(env: Env) -> None:
    case = env.seed(flags=[LOW])
    r = env.review(case.id, **review_body("flag_decision", "dismiss", flag_ids=[LOW.id]))
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"entry", "case"}
    assert set(body["entry"]) == {
        "id",
        "kind",
        "reviewer",
        "decision",
        "reason",
        "flag_ids",
        "finding_id",
        "target_entry_id",
        "status",
        "case_version",
        "created_at",
    }
    assert set(body["case"]) == {
        "id",
        "status",
        "decision",
        "escalated_at",
        "version",
        "pending_second_approvals",
    }
    assert body["entry"]["created_at"].endswith("Z")
    write_fixture("review_response", body)
    bad = env.review(case.id, **review_body("verdict", "approve", reason="no"))
    write_fixture("error_response", bad.json())
