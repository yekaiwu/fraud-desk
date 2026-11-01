"""Monitoring against thresholds, the AI incident log and the scheduled purge."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.config import Settings
from app.core.db import Database
from app.core.models import StageRecord, utcnow
from app.monitoring import snapshot
from app.retention import start_auto_purge
from tests.test_api_support import Env, make_env, mk_flag, seed_case

HIGH = mk_flag("bs_running_balance", "high", doc="d1", locator="transactions:3")


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return make_env(settings, db)


def with_settings(settings: Settings, **kw: object) -> Settings:
    return settings.model_copy(update=kw)


def seed_many(env: Env, n: int, *, partial: int = 0, flags: list | None = None) -> None:
    for i in range(n):
        case = seed_case(env.db, env.settings, flags=flags if flags is not None else [])
        if i < partial:
            case.partial = True
            case.stages = [
                StageRecord(stage="rules", status="ok", warnings=["check_error:x: boom"])
            ]
            env.db.save_case(case)


def test_too_few_cases_gives_no_verdict_rather_than_a_false_all_clear(env: Env) -> None:
    seed_many(env, 3, partial=3)
    snap = snapshot(env.db, env.settings, now=utcnow() + timedelta(seconds=5))
    assert snap["enough_data"] is False
    assert all(
        a["status"] == "unknown" for a in snap["alerts"] if a["metric"] != "daily_spend_ratio"
    )
    assert snap["alerting"] == []


def test_a_high_partial_rate_and_check_errors_raise_alerts_with_what_to_do(env: Env) -> None:
    seed_many(env, 12, partial=6)
    snap = snapshot(env.db, env.settings, now=utcnow() + timedelta(seconds=5))
    assert snap["enough_data"] and snap["cases_in_window"] == 12
    by = {a["metric"]: a for a in snap["alerts"]}
    assert by["partial_rate"]["status"] == "alert" and by["partial_rate"]["value"] == 0.5
    assert by["check_error_rate"]["status"] == "alert"
    assert "partial_rate" in snap["alerting"]
    assert by["partial_rate"]["action"]


def test_a_quiet_window_is_ok_and_the_missing_owner_is_reported(env: Env) -> None:
    seed_many(env, 12)
    snap = snapshot(env.db, env.settings, now=utcnow() + timedelta(seconds=5))
    assert snap["alerting"] == []
    assert snap["owner_named"] is False and snap["accountable_owner"] is None
    named = snapshot(
        env.db,
        with_settings(env.settings, ai_owner="J. Tan, Head of Fraud Analytics"),
        now=utcnow(),
    )
    assert named["owner_named"] is True


def test_monitoring_endpoint_returns_the_snapshot_and_hides_nothing_secret(env: Env) -> None:
    seed_many(env, 11)
    r = env.client.get("/monitoring")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"alerts", "alerting", "disk_encryption", "spend", "owner_named"} <= set(body)
    assert body["disk_encryption"]["status"] in ("on", "off", "unknown")


def test_an_incident_is_recorded_in_the_append_only_audit_log_and_listed(env: Env) -> None:
    r = env.client.post(
        "/monitoring/incidents",
        json={
            "severity": "high",
            "summary": "The challenger lowered a flag it should not have.",
            "reviewer": "ops.lead",
        },
    )
    assert r.status_code == 201, r.text
    listed = env.client.get("/monitoring/incidents").json()["incidents"]
    assert listed[0]["severity"] == "high" and listed[0]["reported_by"] == "ops.lead"
    assert listed[0]["status"] == "open"
    short = env.client.post(
        "/monitoring/incidents", json={"severity": "low", "summary": "x", "reviewer": "ops.lead"}
    )
    assert short.status_code == 422


def test_scheduled_purge_deletes_expired_closed_cases_and_never_the_open_ones(
    settings: Settings, db: Database
) -> None:
    settings = with_settings(settings, retention_days=30, retention_interval_hours=1)
    old = seed_case(db, settings, flags=[])
    old.status = "closed"
    old.updated_at = utcnow() - timedelta(days=90)
    db.save_case(old, touch=False)
    fresh = seed_case(db, settings, flags=[])
    runs = iter([False, True])  # one interval elapses, then stop is requested
    stop = start_auto_purge(db, settings, now=utcnow, wait=lambda _s: next(runs))
    for _ in range(100):  # the daemon thread runs the one purge
        if not db.case_exists(old.id):
            break
        import time

        time.sleep(0.02)
    assert not db.case_exists(old.id)
    assert db.case_exists(fresh.id)
    assert stop is not None


def test_an_incident_is_resolved_by_a_later_report_that_names_it(env: Env) -> None:
    first = env.client.post(
        "/monitoring/incidents",
        json={
            "severity": "medium",
            "summary": "A flag was wrong in a way that mattered.",
            "reviewer": "ops.lead",
        },
    ).json()["id"]
    env.client.post(
        "/monitoring/incidents",
        json={
            "severity": "low",
            "summary": "Fixed the rule and added a test.",
            "reviewer": "ops.lead",
            "resolves": first,
        },
    )
    by_id = {i["id"]: i for i in env.client.get("/monitoring/incidents").json()["incidents"]}
    assert by_id[first]["status"] == "resolved"
