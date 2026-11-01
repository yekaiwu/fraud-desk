"""The signed-in account is the reviewer (app/access.py, api/review.py): a typed name cannot stand in."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.db import Database
from tests.test_api_support import Env, make_env, mk_flag, review_body

SECRET = "s3cret-s3cret-s3cret-s3cret-0123456789"
PROXY = {"X-Gldb-Proxy-Secret": SECRET}
LOOPBACK = ("127.0.0.1", 50000)
HIGH = mk_flag("bs_running_balance", "high", doc="d1", locator="transactions:3")


@pytest.fixture
def env(settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    e = make_env(settings, db)
    e.client = TestClient(e.app, client=LOOPBACK, headers=PROXY)
    return e


def post(env: Env, case_id: str, who: str | None, typed: str = "typed.name", **extra: str):  # noqa: ANN201
    headers = dict(PROXY)
    if who:
        headers["X-Gldb-Reviewer"] = who
    body = review_body("verdict", "approve", reviewer=typed, **extra)
    return env.client.post(f"/cases/{case_id}/review", json=body, headers=headers)


def test_me_reports_the_signed_in_account(env: Env) -> None:
    assert env.client.get("/me", headers={**PROXY, "X-Gldb-Reviewer": "Alice"}).json() == {
        "reviewer": "alice"
    }
    assert env.client.get("/me", headers=PROXY).json() == {"reviewer": None}


def test_the_account_replaces_a_typed_name_in_the_review_and_the_audit_log(env: Env) -> None:
    case = env.seed(flags=[])
    r = post(env, case.id, "alice.tan")
    assert r.status_code == 201, r.text
    assert r.json()["entry"]["reviewer"] == "alice.tan"
    assert {e["actor"] for e in env.audit(case.id)} == {"alice.tan"}


def test_without_an_account_the_typed_name_is_used_as_before(env: Env) -> None:
    case = env.seed(flags=[])
    r = post(env, case.id, None)
    assert r.status_code == 201, r.text
    assert r.json()["entry"]["reviewer"] == "typed.name"


def test_the_header_is_ignored_without_the_proxy_secret(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GLDB_PROXY_SECRET", raising=False)
    env = make_env(settings, db)
    case = env.seed(flags=[])
    r = env.client.post(
        f"/cases/{case.id}/review",
        json=review_body("verdict", "approve", reviewer="typed.name"),
        headers={"X-Gldb-Reviewer": "mallory"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["entry"]["reviewer"] == "typed.name"


def test_a_second_approver_must_be_a_different_account_even_if_the_typed_names_differ(
    env: Env,
) -> None:
    case = env.seed(flags=[HIGH])
    flag_ids = [HIGH.id]
    first = env.client.post(
        f"/cases/{case.id}/review",
        json=review_body("flag_decision", "dismiss", reviewer="typed.one", flag_ids=flag_ids),
        headers={**PROXY, "X-Gldb-Reviewer": "alice.tan"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["entry"]["status"] == "pending_second_approval"
    same = env.client.post(
        f"/cases/{case.id}/review",
        json=review_body("second_approval", "approve", reviewer="typed.two", target_entry_id="r1"),
        headers={**PROXY, "X-Gldb-Reviewer": "alice.tan"},
    )
    assert same.status_code in (403, 409, 422), (
        same.text
    )  # the same account cannot countersign itself
    other = env.client.post(
        f"/cases/{case.id}/review",
        json=review_body("second_approval", "approve", reviewer="typed.one", target_entry_id="r1"),
        headers={**PROXY, "X-Gldb-Reviewer": "bob.lim"},
    )
    assert other.status_code == 201, other.text
