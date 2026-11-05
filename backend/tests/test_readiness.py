"""Readiness for MAS Guidelines 5.23 (a) to (c): named owner, approved thresholds, alerts that reach a place."""

from __future__ import annotations

import itertools
import json
from datetime import timedelta
from typing import Any

import pytest

from app.alerts import AlertService
from app.core.config import Settings
from app.core.db import Database
from app.readiness import readiness, thresholds_hash
from tests.test_alerts import AlwaysFails, FakeSink, mk_settings
from tests.test_api_support import FIXED_NOW, Env, FakeClock, make_env

OWNER = "J. Tan, Head of Fraud Analytics"
APPROVAL = {
    "approver": "M. Lim",
    "role": "Chief Risk Officer",
    "note": "Reviewed against last quarter",
}


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    e = make_env(settings, db, FakeClock(FIXED_NOW))
    e.app.state.alert_sleep = lambda _s: None
    return e


def items(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in body["items"]}


def test_nothing_is_prefilled_so_a_fresh_install_is_not_ready(env: Env) -> None:
    body = env.client.get("/monitoring/readiness").json()
    assert body["ready"] is False
    assert [i["id"] for i in body["items"]] == ["5.23(a)", "5.23(b)", "5.23(c)"]
    assert all(i["status"] == "not_met" and i["missing"] for i in body["items"])
    assert env.settings.ai_owner == ""
    assert [r for r in env.audit_rows() if r["action"] == "monitoring.thresholds_approved"] == []


def test_the_response_says_these_are_the_banks_decisions(env: Env) -> None:
    text = env.client.get("/monitoring/readiness").json()["statement"]
    assert "decisions for the bank" in text and "only records them and checks" in text


def test_a_named_owner_meets_item_a(settings: Settings, db: Database) -> None:
    s = settings.model_copy(update={"ai_owner": OWNER})
    a = items(readiness(db, s, [], now=FIXED_NOW))["5.23(a)"]
    assert a["status"] == "met" and a["missing"] == []
    blank = settings.model_copy(update={"ai_owner": "   "})
    assert items(readiness(db, blank, [], now=FIXED_NOW))["5.23(a)"]["status"] == "not_met"


def test_approving_thresholds_stores_a_hash_in_the_audit_log(env: Env) -> None:
    r = env.client.post("/monitoring/thresholds/approve", json={**APPROVAL})
    assert r.status_code == 201, r.text
    assert r.json()["thresholds_sha256"] == thresholds_hash(env.settings)
    row = [x for x in env.audit_rows() if x["action"] == "monitoring.thresholds_approved"][0]
    detail = json.loads(row["detail"])
    assert detail["thresholds_sha256"] == thresholds_hash(env.settings)
    assert detail["approver"] == "M. Lim" and detail["role"] == "Chief Risk Officer"
    assert detail["thresholds"]["monitor_partial_rate_max"] == 0.3
    b = items(env.client.get("/monitoring/readiness").json())["5.23(b)"]
    assert (
        b["status"] == "met"
        and b["approved_by"] == "M. Lim"
        and b["approver_role"] == "Chief Risk Officer"
    )


@pytest.mark.parametrize("missing", ["approver", "role", "note"])
def test_approval_needs_approver_role_and_note(env: Env, missing: str) -> None:
    body = {k: v for k, v in APPROVAL.items() if k != missing}
    assert env.client.post("/monitoring/thresholds/approve", json=body).status_code == 422
    assert (
        env.client.post(
            "/monitoring/thresholds/approve", json={**APPROVAL, missing: "x"}
        ).status_code
        == 422
    )
    assert [r for r in env.audit_rows() if r["action"] == "monitoring.thresholds_approved"] == []


def test_changing_a_threshold_after_approval_withdraws_it(settings: Settings, db: Database) -> None:
    first = make_env(settings, db, FakeClock(FIXED_NOW))
    assert first.client.post("/monitoring/thresholds/approve", json=APPROVAL).status_code == 201
    changed = settings.model_copy(update={"monitor_partial_rate_max": 0.5})
    second = make_env(changed, db, FakeClock(FIXED_NOW))
    b = items(second.client.get("/monitoring/readiness").json())["5.23(b)"]
    assert b["status"] == "not_met" and "changed since approval" in b["detail"]
    assert b["approved_by"] == "M. Lim"  # who approved what, so the gap is clear
    # approving the new values makes it current again
    assert second.client.post("/monitoring/thresholds/approve", json=APPROVAL).status_code == 201
    assert items(second.client.get("/monitoring/readiness").json())["5.23(b)"]["status"] == "met"
    # going back to the first values is a change again: only the latest approval counts
    third = make_env(settings, db, FakeClock(FIXED_NOW))
    assert items(third.client.get("/monitoring/readiness").json())["5.23(b)"]["status"] == "not_met"


@pytest.mark.parametrize(
    "field,value",
    [
        ("monitor_window_days", 14),
        ("monitor_min_cases", 20),
        ("monitor_check_error_rate_max", 0.1),
        ("monitor_agent_failure_rate_max", 0.2),
        ("monitor_daily_spend_ratio_max", 0.9),
        ("monitor_rule_drift_max", 0.5),
    ],
)
def test_every_threshold_is_part_of_the_hash(settings: Settings, field: str, value: float) -> None:
    assert thresholds_hash(settings) != thresholds_hash(settings.model_copy(update={field: value}))


def test_the_hash_ignores_how_a_number_is_written_and_unrelated_settings(tmp_path: Any) -> None:
    a = mk_settings(tmp_path, monitor_partial_rate_max="0.30", ai_owner="x")
    b = mk_settings(tmp_path, monitor_partial_rate_max=0.3, monitor_interval_minutes=15)
    assert thresholds_hash(a) == thresholds_hash(b)


# ---- (c)


def test_no_sink_means_item_c_is_not_met(settings: Settings, db: Database) -> None:
    c = items(readiness(db, settings, [], now=FIXED_NOW))["5.23(c)"]
    assert c["status"] == "not_met" and "No alert sink" in c["detail"]


def test_a_sink_without_a_test_is_not_enough(settings: Settings, db: Database) -> None:
    c = items(readiness(db, settings, [FakeSink()], now=FIXED_NOW))["5.23(c)"]
    assert c["status"] == "not_met" and "No test alert" in c["detail"]


def test_a_delivered_test_meets_item_c(env: Env) -> None:
    env.app.state.alert_sinks = [FakeSink("webhook")]
    env.client.post("/monitoring/alerts/test")
    c = items(env.client.get("/monitoring/readiness").json())["5.23(c)"]
    assert c["status"] == "met" and c["delivered_by"] == ["webhook"]


def test_a_failed_test_does_not_meet_item_c(env: Env) -> None:
    env.app.state.alert_sinks = [AlwaysFails("webhook")]
    env.client.post("/monitoring/alerts/test")
    c = items(env.client.get("/monitoring/readiness").json())["5.23(c)"]
    assert c["status"] == "not_met" and "not delivered" in c["detail"]


def test_a_test_older_than_the_max_age_no_longer_counts(env: Env) -> None:
    env.app.state.alert_sinks = [FakeSink("webhook")]
    env.client.post("/monitoring/alerts/test")
    env.clock.advance(days=29)
    assert items(env.client.get("/monitoring/readiness").json())["5.23(c)"]["status"] == "met"
    env.clock.advance(days=2)
    c = items(env.client.get("/monitoring/readiness").json())["5.23(c)"]
    assert c["status"] == "not_met" and "older than 30 days" in c["detail"]


def test_the_max_age_is_a_setting(settings: Settings, db: Database) -> None:
    s = settings.model_copy(update={"alert_test_max_age_days": 2})
    e = make_env(s, db, FakeClock(FIXED_NOW))
    e.app.state.alert_sinks = [FakeSink("webhook")]
    e.client.post("/monitoring/alerts/test")
    e.clock.advance(days=3)
    assert items(e.client.get("/monitoring/readiness").json())["5.23(c)"]["status"] == "not_met"


def test_changing_the_sink_after_the_test_withdraws_it(env: Env) -> None:
    sink = FakeSink("webhook")
    env.app.state.alert_sinks = [sink]
    env.client.post("/monitoring/alerts/test")
    sink.fp = "a-different-url"
    c = items(env.client.get("/monitoring/readiness").json())["5.23(c)"]
    assert c["status"] == "not_met" and "configuration changed" in c["detail"]


def test_removing_the_sink_after_the_test_withdraws_it(env: Env) -> None:
    env.app.state.alert_sinks = [FakeSink("webhook")]
    env.client.post("/monitoring/alerts/test")
    env.app.state.alert_sinks = []
    assert items(env.client.get("/monitoring/readiness").json())["5.23(c)"]["status"] == "not_met"


def test_one_working_sink_is_enough_and_the_failing_one_is_shown(env: Env) -> None:
    env.app.state.alert_sinks = [AlwaysFails("telegram"), FakeSink("webhook")]
    env.client.post("/monitoring/alerts/test")
    c = items(env.client.get("/monitoring/readiness").json())["5.23(c)"]
    assert c["status"] == "met" and c["delivered_by"] == ["webhook"]
    assert any(not r["ok"] for r in c["last_test_results"])


def test_only_the_file_sink_meets_c_but_says_someone_must_watch_it(env: Env) -> None:
    env.app.state.alert_sinks = [FakeSink("file")]
    env.client.post("/monitoring/alerts/test")
    c = items(env.client.get("/monitoring/readiness").json())["5.23(c)"]
    assert c["status"] == "met" and "someone or something watches" in c["detail"]


# ---- overall: every combination of the three


@pytest.mark.parametrize("a,b,c", list(itertools.product([False, True], repeat=3)))
def test_ready_only_when_all_three_hold(
    settings: Settings, db: Database, a: bool, b: bool, c: bool
) -> None:
    s = settings.model_copy(update={"ai_owner": OWNER if a else ""})
    e = make_env(s, db, FakeClock(FIXED_NOW))
    e.app.state.alert_sleep = lambda _s: None
    if b:
        e.client.post("/monitoring/thresholds/approve", json=APPROVAL)
    if c:
        e.app.state.alert_sinks = [FakeSink("webhook")]
        e.client.post("/monitoring/alerts/test")
    body = e.client.get("/monitoring/readiness").json()
    got = items(body)
    assert [got[k]["status"] == "met" for k in ("5.23(a)", "5.23(b)", "5.23(c)")] == [a, b, c]
    assert body["ready"] is (a and b and c)
    summary = e.client.get("/monitoring").json()["readiness"]
    assert summary["ready"] is (a and b and c)
    assert summary["accountable_person"] == ("met" if a else "not_met")


def test_each_missing_item_says_what_to_do(env: Env) -> None:
    for item in env.client.get("/monitoring/readiness").json()["items"]:
        assert item["missing"] and all(isinstance(m, str) and m for m in item["missing"])


def test_with_personal_accounts_a_typed_approver_alone_cannot_approve(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from tests.test_alerts import PROXY, SECRET

    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    e = make_env(settings.model_copy(update={"personal_accounts": True}), db, FakeClock(FIXED_NOW))
    client = TestClient(e.app, client=("127.0.0.1", 50000), headers=PROXY)
    assert client.post("/monitoring/thresholds/approve", json=APPROVAL).status_code == 403
    ok = client.post(
        "/monitoring/thresholds/approve",
        json=APPROVAL,
        headers={**PROXY, "X-Gldb-Reviewer": "mlim"},
    )
    assert ok.status_code == 201 and ok.json()["recorded_by"] == "mlim"
    row = [x for x in e.audit_rows() if x["action"] == "monitoring.thresholds_approved"][0]
    # the signed-in account is the approver; the name typed in the form is kept as approver_name
    assert row["actor"] == "mlim" and json.loads(row["detail"])["approver"] == "mlim"
    assert ok.json()["approver"] == "mlim"
    # ... and is kept beside it as approver_name
    assert json.loads(row["detail"])["approver_name"] == "M. Lim"
    assert ok.json()["approver_name"] == "M. Lim"


def test_the_audit_approval_row_cannot_be_edited(env: Env) -> None:
    env.client.post("/monitoring/thresholds/approve", json=APPROVAL)
    with pytest.raises(Exception, match="append-only"), env.db.conn() as c:
        c.execute(
            "UPDATE audit_log SET detail = '{}' WHERE action = 'monitoring.thresholds_approved'"
        )


def test_readiness_uses_the_alert_service_test_record(settings: Settings, db: Database) -> None:
    clock = FakeClock(FIXED_NOW)
    sink = FakeSink("webhook")
    AlertService(db, settings, clock=clock, sinks=[sink], sleep=lambda _s: None).send_test(who="op")
    assert (
        items(readiness(db, settings, [sink], now=FIXED_NOW + timedelta(days=1)))["5.23(c)"][
            "status"
        ]
        == "met"
    )
