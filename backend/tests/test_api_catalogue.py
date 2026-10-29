"""Check catalogue overrides: store, change log and HTTP contract (docs/api.md section 9a)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.catalogue import DESCRIPTION_MAX, NAME_MAX, NOTE_MAX, REASON_MAX, REVIEWER_MAX
from app.checks.registry import all_checks
from app.core.config import Settings
from app.core.db import Database
from tests.test_api_support import FIXED_NOW, Env, FakeClock, make_env

RULE = "bs_running_balance"  # high, bank_statement
ITEM_KEYS = {
    "rule",
    "stage",
    "severity",
    "applies_to",
    "requires",
    "use_cases",
    "description",
    "tamper",
    "version",
    "default_severity",
    "effective_severity",
    "enabled",
    "override",
}
OVERRIDE_KEYS = {
    "enabled",
    "severity",
    "name",
    "description",
    "note",
    "updated_by",
    "updated_at",
    "reason",
    "revision",
}
CHANGE_KEYS = {"id", "rule", "at", "reviewer", "reason", "action", "before", "after"}
WHO = {"reviewer": "Ann Officer", "reason": "Calibrated on last quarter"}


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db, FakeClock())


def put(env: Env, rule: str = RULE, **body: Any):  # noqa: ANN201
    return env.client.put(f"/rules/{rule}/override", json={**WHO, **body})


def error(resp, status: int, code: str) -> dict[str, Any]:  # noqa: ANN001
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    return body["error"]


def item(env: Env, rule: str = RULE) -> dict[str, Any]:
    return next(r for r in env.client.get("/rules").json()["rules"] if r["rule"] == rule)


# ------------------------------------------------------------------------------------ store


def test_tables_exist_and_init_schema_is_idempotent(db: Database) -> None:
    db.init_schema()
    with db.conn() as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        cols = {r["name"] for r in c.execute("PRAGMA table_info(rule_overrides)")}
        change_cols = {r["name"] for r in c.execute("PRAGMA table_info(rule_changes)")}
    assert {"rule_overrides", "rule_changes", "rule_changes_no_update"} <= names
    assert cols == {"rule", "enabled", "severity", "name", "description", "note"} | {
        "updated_by",
        "updated_at",
        "reason",
        "revision",
    }
    assert change_cols == CHANGE_KEYS


def test_rule_changes_is_append_only(env: Env) -> None:
    assert put(env, severity="low").status_code == 200
    with env.db.conn() as c, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("UPDATE rule_changes SET reason = 'edited later'")
    with env.db.conn() as c, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("DELETE FROM rule_changes")
    with env.db.conn() as c, pytest.raises(sqlite3.IntegrityError):  # the action is constrained
        c.execute(
            'INSERT INTO rule_changes (rule, at, reviewer, reason, action, "before", "after") '
            "VALUES ('r', 't', 'who', 'why', 'delete', 'null', 'null')"
        )
    assert len(env.client.get("/rules/changes").json()["changes"]) == 1


def test_override_store_round_trip_and_revision_survives_a_reset(db: Database) -> None:
    now = FIXED_NOW
    first = db.write_rule_override(
        RULE,
        {"enabled": False, "severity": "low", "name": None, "description": None, "note": None},
        reviewer="Ann",
        reason="switching it off",
        at=now,
    )
    assert first is not None and first.revision == 1 and first.enabled is False
    assert db.rule_overrides() == {RULE: first}
    assert db.write_rule_override(RULE, None, reviewer="Bob", reason="back", at=now) is None
    assert db.rule_overrides() == {} and db.get_rule_override(RULE) is None
    again = db.write_rule_override(
        RULE,
        {"enabled": True, "severity": "medium", "name": None, "description": None, "note": None},
        reviewer="Ann",
        reason="medium now",
        at=now,
    )
    assert again is not None and again.revision == 3  # counts every logged change of the rule
    log = db.rule_changes(RULE)
    assert [c["action"] for c in log] == ["update", "reset", "update"]
    assert [c["id"] for c in log] == sorted((c["id"] for c in log), reverse=True)


def test_delete_case_and_retention_never_touch_the_catalogue(env: Env) -> None:
    from app.audit import AuditLog
    from app.retention import purge

    case = env.seed()
    assert put(env, severity="low", note="kept").status_code == 200
    assert env.client.delete(f"/cases/{case.id}", params=WHO).status_code == 204
    old = env.seed(status="closed")
    env.clock.now = FIXED_NOW + timedelta(days=4000)
    audit = AuditLog(env.db, env.settings, clock=env.clock)
    result = purge(env.db, audit, older_than_days=30, dry_run=False, now=env.clock.now)
    assert old.id in result["case_ids"]
    assert item(env)["override"]["note"] == "kept"
    assert len(env.client.get("/rules/changes").json()["changes"]) == 1


# ------------------------------------------------------------------------------- GET /rules


def test_get_rules_without_overrides_gains_exactly_the_new_fields(env: Env) -> None:
    rules = env.client.get("/rules").json()["rules"]
    assert [r["rule"] for r in rules] == [m.rule for m in all_checks()]
    for r in rules:
        assert set(r) == ITEM_KEYS
        assert r["override"] is None and r["enabled"] is True
        assert r["default_severity"] == r["severity"] == r["effective_severity"]


def test_get_rules_after_an_override(env: Env) -> None:
    before = item(env)
    put(env, enabled=False, severity="low", name="Balance chain", note="watch this one")
    after = item(env)
    assert set(after) == ITEM_KEYS
    # the code definition is untouched; `severity` stays the code default
    for key in ITEM_KEYS - {"effective_severity", "enabled", "override"}:
        assert after[key] == before[key]
    assert after["severity"] == after["default_severity"] == "high"
    assert after["effective_severity"] == "low" and after["enabled"] is False
    assert set(after["override"]) == OVERRIDE_KEYS
    assert after["override"] == {
        "enabled": False,
        "severity": "low",
        "name": "Balance chain",
        "description": None,
        "note": "watch this one",
        "updated_by": "Ann Officer",
        "updated_at": "2026-10-27T10:00:00.000000Z",
        "reason": "Calibrated on last quarter",
        "revision": 1,
    }
    # other rules unaffected
    assert all(
        r["override"] is None for r in env.client.get("/rules").json()["rules"] if r["rule"] != RULE
    )


def test_name_only_override_keeps_effective_severity_and_enabled(env: Env) -> None:
    put(env, name="Just a better name")
    got = item(env)
    assert got["effective_severity"] == "high" and got["enabled"] is True
    assert got["override"]["severity"] is None


# ----------------------------------------------------------------------------- PUT override


def test_put_returns_the_updated_item_and_logs_before_and_after(env: Env) -> None:
    r = put(env, severity="medium")
    assert r.status_code == 200
    assert r.json() == item(env)
    env.clock.advance(minutes=5)
    r2 = put(env, description="Plain words.", reviewer="Bob", reason="clearer wording")
    assert r2.json()["override"]["revision"] == 2
    assert r2.json()["override"]["severity"] == "medium"  # omitted keeps its value
    log = env.client.get("/rules/changes", params={"rule": RULE}).json()["changes"]
    assert [c["id"] for c in log] == [2, 1] and set(log[0]) == CHANGE_KEYS
    assert log[1]["action"] == "update" and log[1]["before"] is None
    assert log[1]["after"]["severity"] == "medium"
    assert (
        log[0]["before"]["description"] is None and log[0]["after"]["description"] == "Plain words."
    )
    assert log[0]["reviewer"] == "Bob" and log[0]["reason"] == "clearer wording"
    assert log[0]["at"] == "2026-10-27T10:05:00.000000Z"
    assert log[0]["before"] == log[1]["after"]


def test_partial_update_keeps_omitted_fields_and_null_or_empty_clears(env: Env) -> None:
    put(env, severity="low", name="A name", description="A description", note="A note")
    put(env, enabled=False)
    o = item(env)["override"]
    assert (o["enabled"], o["severity"], o["name"], o["description"], o["note"]) == (
        False,
        "low",
        "A name",
        "A description",
        "A note",
    )
    put(env, severity=None, name="", description="   ")  # null and blank both clear
    o = item(env)["override"]
    assert o["severity"] is None and o["name"] is None and o["description"] is None
    assert o["note"] == "A note" and o["enabled"] is False
    assert item(env)["effective_severity"] == "high"
    put(env, note=None)
    assert item(env)["override"]["enabled"] is False  # still off, so the row stays


def test_clearing_everything_back_to_defaults_deletes_the_row_and_logs_a_reset(env: Env) -> None:
    put(env, severity="low", note="n")
    r = put(env, severity=None, note="", reviewer="Bob", reason="back to the defaults")
    assert r.status_code == 200 and r.json()["override"] is None
    assert item(env)["effective_severity"] == "high"
    with env.db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM rule_overrides").fetchone()[0] == 0
    last = env.client.get("/rules/changes").json()["changes"][0]
    assert last["action"] == "reset" and last["after"] is None
    assert last["before"]["severity"] == "low" and last["reviewer"] == "Bob"
    put(env, severity="medium")  # a new override after the reset keeps counting revisions
    assert item(env)["override"]["revision"] == 3


def test_enabling_a_switched_off_check_with_nothing_else_resets(env: Env) -> None:
    put(env, enabled=False)
    r = put(env, enabled=True, reason="switch it back on")
    assert r.json()["override"] is None and r.json()["enabled"] is True
    assert env.client.get("/rules/changes").json()["changes"][0]["action"] == "reset"


def test_explicit_severity_equal_to_the_default_is_kept_as_an_override(env: Env) -> None:
    put(env, severity="high")  # flattens flags of a rule that emits mixed severities
    assert item(env)["override"]["severity"] == "high"


def test_unknown_rule_is_404(env: Env) -> None:
    err = error(put(env, rule="no_such_rule", severity="low"), 404, "unknown_rule")
    assert err["details"] == {"rule": "no_such_rule"}
    error(env.client.delete("/rules/no_such_rule/override", params=WHO), 404, "unknown_rule")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"severity": "critical"}, "validation_error"),
        ({"severity": "HIGH"}, "validation_error"),
        ({"enabled": "yes"}, "validation_error"),
        ({"enabled": None}, "validation_error"),
        ({"name": 12}, "validation_error"),
        ({"surprise": 1}, "validation_error"),  # extra fields are forbidden
        ({"severity": "low", "reason": "no"}, "reason_required"),
        ({"severity": "low", "reason": "   .  "}, "reason_required"),
        ({"severity": "low", "reviewer": ""}, "reviewer_required"),
        ({"severity": "low", "reviewer": "A"}, "reviewer_required"),
        ({"severity": "low", "reviewer": "x" * (REVIEWER_MAX + 1)}, "reviewer_required"),
        ({"severity": "low", "reason": "r" * (REASON_MAX + 1)}, "validation_error"),
        ({"name": "n" * (NAME_MAX + 1)}, "validation_error"),
        ({"description": "d" * (DESCRIPTION_MAX + 1)}, "validation_error"),
        ({"note": "n" * (NOTE_MAX + 1)}, "validation_error"),
        ({}, "no_change"),
        ({"enabled": True}, "no_change"),  # already the default
        ({"severity": None, "name": "", "note": None}, "no_change"),
    ],
)
def test_put_validation_errors(env: Env, body: dict[str, Any], code: str) -> None:
    payload = {**WHO, **body}
    err = error(env.client.put(f"/rules/{RULE}/override", json=payload), 422, code)
    assert isinstance(err["message"], str) and isinstance(err["details"], dict)
    with env.db.conn() as c:  # a rejected request writes nothing
        assert c.execute("SELECT COUNT(*) FROM rule_changes").fetchone()[0] == 0


def test_put_requires_reviewer_and_reason_keys(env: Env) -> None:
    error(
        env.client.put(f"/rules/{RULE}/override", json={"severity": "low"}),
        422,
        "reviewer_required",
    )
    error(
        env.client.put(f"/rules/{RULE}/override", json={"severity": "low", "reviewer": "Ann"}),
        422,
        "reason_required",
    )


def test_repeating_the_same_state_is_no_change_and_logs_nothing(env: Env) -> None:
    put(env, severity="low", name="N")
    error(put(env, severity="low", name="N", reason="again, same values"), 422, "no_change")
    assert len(env.client.get("/rules/changes").json()["changes"]) == 1


def test_malformed_json_is_400(env: Env) -> None:
    r = env.client.put(
        f"/rules/{RULE}/override", content=b"{nope", headers={"content-type": "application/json"}
    )
    error(r, 400, "bad_request")


def test_text_is_plain_text_cleaned_and_limits_are_inclusive(env: Env) -> None:
    dirty = "Line one\r\nline\ttwo ‮evil‬​  <b>bold</b>\x00"
    put(env, name=dirty, reviewer="  Ann‮ Officer  ", reason="Because\nof\tthis   reason")
    o = item(env)["override"]
    assert o["name"] == "Line one line two evil <b>bold</b>"
    assert o["updated_by"] == "Ann Officer"
    assert o["reason"] == "Because of this reason"
    for ch in o["name"]:
        assert ch.isprintable()
    # exactly at the limits is accepted
    r = put(
        env,
        name="n" * NAME_MAX,
        description="d" * DESCRIPTION_MAX,
        note="t" * NOTE_MAX,
        reviewer="r" * REVIEWER_MAX,
        reason="w" * REASON_MAX,
    )
    assert r.status_code == 200
    # the limit counts the cleaned text, not the raw input
    assert put(env, note=("t " * 10 + "\n" * 600).strip() + "z").status_code == 200


# ---------------------------------------------------------------------------- DELETE override


def test_delete_resets_with_body_and_returns_the_default_item(env: Env) -> None:
    put(env, severity="low")
    r = env.client.request(
        "DELETE", f"/rules/{RULE}/override", json={"reviewer": "Bob", "reason": "no longer needed"}
    )
    assert r.status_code == 200
    assert r.json() == item(env) and r.json()["override"] is None
    last = env.client.get("/rules/changes").json()["changes"][0]
    assert (
        last["action"] == "reset"
        and last["reviewer"] == "Bob"
        and last["before"]["severity"] == "low"
    )


def test_delete_accepts_query_parameters_and_the_body_wins(env: Env) -> None:
    put(env, severity="low")
    r = env.client.delete(
        f"/rules/{RULE}/override", params={"reviewer": "Query Person", "reason": "query reason"}
    )
    assert r.status_code == 200 and r.json()["override"] is None
    put(env, severity="low")
    r = env.client.request(
        "DELETE",
        f"/rules/{RULE}/override",
        params={"reviewer": "Query Person", "reason": "query reason"},
        json={"reviewer": "Body Person"},
    )
    assert r.status_code == 200
    last = env.client.get("/rules/changes").json()["changes"][0]
    assert last["reviewer"] == "Body Person" and last["reason"] == "query reason"


def test_delete_errors(env: Env) -> None:
    error(env.client.delete(f"/rules/{RULE}/override", params=WHO), 404, "no_override")
    put(env, severity="low")
    error(env.client.delete(f"/rules/{RULE}/override"), 422, "reviewer_required")
    error(
        env.client.delete(f"/rules/{RULE}/override", params={"reviewer": "Ann"}),
        422,
        "reason_required",
    )
    error(
        env.client.request("DELETE", f"/rules/{RULE}/override", json={**WHO, "extra": 1}),
        422,
        "validation_error",
    )
    assert item(env)["override"] is not None  # nothing was reset by the rejected calls


# ----------------------------------------------------------------------------- GET /changes


def test_changes_newest_first_filter_and_limit(env: Env) -> None:
    for n in range(4):
        env.clock.advance(seconds=1)
        put(env, rule="inv_dates", note=f"note {n}")
    put(env, severity="low")
    everything = env.client.get("/rules/changes").json()["changes"]
    assert [c["id"] for c in everything] == [5, 4, 3, 2, 1] and everything[0]["rule"] == RULE
    only = env.client.get("/rules/changes", params={"rule": "inv_dates"}).json()["changes"]
    assert [c["id"] for c in only] == [4, 3, 2, 1] and {c["rule"] for c in only} == {"inv_dates"}
    two = env.client.get("/rules/changes", params={"limit": 2}).json()["changes"]
    assert [c["id"] for c in two] == [5, 4]
    assert env.client.get("/rules/changes", params={"rule": "nothing_here"}).json() == {
        "changes": []
    }


def test_changes_default_limit_is_50_and_max_is_200(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLDB_RATE_REVIEWS_PER_MIN", "0")  # 55 writes in a row
    env = make_env(settings, db, FakeClock())
    for n in range(55):
        put(env, note=f"note {n}")
    assert len(env.client.get("/rules/changes").json()["changes"]) == 50
    assert len(env.client.get("/rules/changes", params={"limit": 200}).json()["changes"]) == 55
    for bad in (0, 201, -1):
        error(env.client.get("/rules/changes", params={"limit": bad}), 422, "validation_error")


# --------------------------------------------------------------- proxy secret and rate limit

SECRET = "s3cret-s3cret-s3cret-s3cret-0123456789"
H = {"X-Gldb-Proxy-Secret": SECRET}
REMOTE = ("203.0.113.9", 50000)


def secured(settings: Settings, monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    for key, value in env.items():
        monkeypatch.setenv(f"GLDB_{key.upper()}", value)
    return TestClient(main.create_app(settings), client=REMOTE)


def test_catalogue_routes_sit_behind_the_proxy_secret(settings, monkeypatch) -> None:  # noqa: ANN001
    c = secured(settings, monkeypatch)
    for method, path in [
        ("GET", "/rules"),
        ("GET", "/rules/changes"),
        ("PUT", f"/rules/{RULE}/override"),
        ("DELETE", f"/rules/{RULE}/override"),
    ]:
        r = c.request(method, path, json={**WHO, "severity": "low"} if method == "PUT" else None)
        assert r.status_code == 401, (method, path)
        assert r.json()["error"]["code"] == "unauthorized"
    assert (
        c.put(f"/rules/{RULE}/override", headers=H, json={**WHO, "severity": "low"}).status_code
        == 200
    )
    assert c.get("/rules/changes", headers=H).status_code == 200


def test_override_writes_share_the_review_rate_limit(settings, monkeypatch) -> None:  # noqa: ANN001
    c = secured(settings, monkeypatch, rate_reviews_per_min="3")
    notes = iter(f"note {n}" for n in range(10))
    for _ in range(2):
        assert (
            c.put(
                f"/rules/{RULE}/override", headers=H, json={**WHO, "note": next(notes)}
            ).status_code
            == 200
        )
    # a DELETE counts in the same bucket (and is 200 here)
    assert c.delete(f"/rules/{RULE}/override", headers=H, params=WHO).status_code == 200
    r = c.put(f"/rules/{RULE}/override", headers=H, json={**WHO, "note": next(notes)})
    assert r.status_code == 429 and r.headers["retry-after"]
    err = r.json()["error"]
    assert err["code"] == "rate_limited" and err["details"]["scope"] == "reviews"
    assert c.delete(f"/rules/{RULE}/override", headers=H, params=WHO).status_code == 429
    # reads are never limited, and a review POST by the same client hits the same bucket
    assert c.get("/rules", headers=H).status_code == 200
    assert c.get("/rules/changes", headers=H).status_code == 200
    assert c.post("/cases/c_x/review", headers=H, json={}).status_code == 429
    # the rejected writes changed nothing
    assert len(c.get("/rules/changes", headers=H).json()["changes"]) == 3


def test_rate_limit_zero_disables_it_and_other_clients_have_their_own_bucket(
    settings, monkeypatch
) -> None:  # noqa: ANN001
    c = secured(settings, monkeypatch, rate_reviews_per_min="1")
    assert (
        c.put(f"/rules/{RULE}/override", headers=H, json={**WHO, "note": "one"}).status_code == 200
    )
    assert (
        c.put(f"/rules/{RULE}/override", headers=H, json={**WHO, "note": "two"}).status_code == 429
    )
    other = TestClient(c.app, client=("198.51.100.7", 1))
    assert (
        other.put(f"/rules/{RULE}/override", headers=H, json={**WHO, "note": "three"}).status_code
        == 200
    )
    # an unlimited deployment
    monkeypatch.setenv("GLDB_RATE_REVIEWS_PER_MIN", "0")
    free = TestClient(main.create_app(settings), client=REMOTE)
    for n in range(5):
        assert (
            free.put(
                f"/rules/{RULE}/override", headers=H, json={**WHO, "note": f"n{n}"}
            ).status_code
            == 200
        )


# ------------------------------------------------------------------------------ docs agree


def test_api_md_documents_the_catalogue_routes_and_error_codes() -> None:
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "docs" / "api.md").read_text(encoding="utf-8")
    for row in (
        "| PUT | `/rules/{rule}/override` |",
        "| DELETE | `/rules/{rule}/override` |",
        "| GET | `/rules/changes` |",
    ):
        assert row in text, row
    for code in ("unknown_rule", "no_override", "no_change", "switched_off"):
        assert f"`{code}`" in text, code
    for name in (
        "RuleOverride",
        "RuleChange",
        "OverrideRequest",
        "default_severity",
        "effective_severity",
    ):
        assert name in text, name
    assert json.loads(
        json.dumps(sorted(ITEM_KEYS))
    )  # the shape asserted above is the documented one
