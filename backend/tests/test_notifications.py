"""The in-app sink and the notification feed (docs/ai-incidents.md): what the Notifications page lists.

No real network. The alert state machine itself is covered in test_alerts.py; here the point is that every
announcement also lands on the page, that the feed pages and filters, that read marks are audited, and that
nothing secret and nothing from a case reaches the table or an answer.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.alerts import STALE_KEY, AlertService, build_message, build_sinks
from app.core.config import Settings
from app.core.db import SCHEMA, Database, ts
from app.notifications import IN_APP, MAX_ROWS, InAppSink, list_notifications, summary
from app.notifications import STALE_KEY as NOTIF_STALE_KEY
from tests.test_alerts import (
    PROXY,
    SECRET,
    SECRETS,
    SMTP_PASSWORD,
    AlwaysFails,
    FakeSink,
    fake_snapshot,
    secret_settings,
)
from tests.test_api_support import (
    FIXED_NOW,
    Env,
    FakeClock,
    make_env,
    seed_case,
    tiny_pdf,
    write_fixture,
)
from tests.test_readiness import items


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(FIXED_NOW)


@pytest.fixture
def api(settings: Settings, db: Database, clock: FakeClock) -> Env:
    env = make_env(settings, db, clock)
    env.app.state.alert_sleep = lambda _s: None
    return env


def page_service(db: Database, settings: Settings, clock: FakeClock, *outside: Any) -> AlertService:
    """What the application builds by default (the page first), plus any outside sinks given."""
    return AlertService(
        db, settings, clock=clock, sinks=[InAppSink(db), *outside], sleep=lambda _s: None
    )


def feed(env: Env, **params: Any) -> dict[str, Any]:
    r = env.client.get("/monitoring/notifications", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def alert_now(
    monkeypatch: pytest.MonkeyPatch, key: str = "partial_rate", value: float = 0.5
) -> None:
    fake_snapshot(monkeypatch, {key: ("alert", value)})


# ---------------------------------------------------------------------------------- the sink


def test_the_table_is_created_if_missing_and_the_schema_applies_twice(db: Database) -> None:
    with db.conn() as c:
        c.executescript(SCHEMA)
        c.executescript(SCHEMA)
        names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "notifications" in names


def test_the_stale_key_matches_the_alert_service() -> None:
    assert NOTIF_STALE_KEY == STALE_KEY


def test_the_default_service_always_has_the_page_and_an_unconfigured_install_has_nothing_else(
    db: Database, settings: Settings, clock: FakeClock
) -> None:
    service = AlertService(db, settings, clock=clock, sleep=lambda _s: None)
    assert [s.name for s in service.sinks] == [IN_APP]


def test_an_alert_appears_on_the_page_with_no_outside_sink(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    alert_now(monkeypatch)
    out = AlertService(db, settings, clock=clock, sleep=lambda _s: None).evaluate()
    assert out["opened"] == ["partial_rate"] and out["sinks"] == [IN_APP]
    rows, nxt = list_notifications(db)
    assert nxt is None and len(rows) == 1
    n = rows[0]
    assert (n["kind"], n["category"], n["key"], n["severity"]) == (
        "alert",
        "alerts",
        "partial_rate",
        "attention",
    )
    assert n["read"] is False and n["ts"] == ts(FIXED_NOW)
    assert n["data"] == {"metric": "partial_rate", "value": 0.5, "threshold": 0.3}
    assert "0.5" in n["text"] and "threshold 0.3" in n["text"]
    assert "meaning of partial_rate" in n["text"] and "action for partial_rate" in n["text"]
    assert "GET /" not in n["text"] and "POST /" not in n["text"]  # nothing meant for an API client
    assert "as of" not in n["text"] and "2026-" not in n["text"]  # the entry carries its own time
    assert "GLDB fraud tool" not in n["text"]


def test_resolution_and_reminder_each_appear_once(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = page_service(db, settings, clock)
    alert_now(monkeypatch)
    svc.evaluate()
    svc.evaluate()  # still open, not due: nothing new
    assert [n["kind"] for n in list_notifications(db)[0]] == ["alert"]
    clock.advance(hours=settings.alert_renotify_hours + 1)
    svc.evaluate()
    svc.evaluate()
    assert [n["kind"] for n in list_notifications(db)[0]] == ["reminder", "alert"]
    fake_snapshot(monkeypatch, {"partial_rate": ("ok", 0.1)})
    clock.advance(minutes=5)
    svc.evaluate()
    svc.evaluate()
    kinds = [n["kind"] for n in list_notifications(db)[0]]
    assert kinds == ["resolved", "reminder", "alert"]
    resolved = list_notifications(db)[0][0]
    assert resolved["severity"] == "info" and "back within the threshold" in resolved["text"]


def test_a_failing_outside_sink_is_retried_without_repeating_the_page_entry(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    alert_now(monkeypatch)
    bad = AlwaysFails("webhook")
    svc = page_service(db, settings, clock, bad)
    first = svc.evaluate()
    assert first["delivery_failed"] is True
    svc.evaluate()  # the page already has it; the outside sink is tried again
    assert len(list_notifications(db)[0]) == 1
    with db.conn() as c:
        state = c.execute("SELECT notify_count FROM alert_state").fetchone()
    assert state["notify_count"] == 0  # the page alone does not stop the retry of the webhook
    good = FakeSink("webhook")
    page_service(db, settings, clock, good).evaluate()
    assert len(good.sent) == 1 and len(list_notifications(db)[0]) == 1


def test_a_page_only_install_counts_the_alert_as_announced(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    alert_now(monkeypatch)
    page_service(db, settings, clock).evaluate()
    with db.conn() as c:
        assert c.execute("SELECT notify_count FROM alert_state").fetchone()["notify_count"] == 1


def test_an_unknown_metric_never_appears(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_snapshot(monkeypatch, {"partial_rate": ("unknown", None)})
    page_service(db, settings, clock).evaluate()
    assert list_notifications(db)[0] == []


def test_the_monitor_going_silent_is_a_system_notification(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = settings.model_copy(update={"monitor_interval_minutes": 10})
    fake_snapshot(monkeypatch, {"partial_rate": ("ok", 0.1)})
    svc = page_service(db, s, clock)
    svc.evaluate()
    clock.advance(minutes=45)  # more than two intervals
    svc.evaluate()
    rows, _ = list_notifications(db)
    assert [(n["category"], n["key"], n["kind"]) for n in rows] == [("system", STALE_KEY, "alert")]
    assert "silent" in rows[0]["text"] and "Minutes since the monitor last ran" in rows[0]["text"]


def test_a_medium_or_high_incident_appears_and_a_low_one_does_not(api: Env) -> None:
    for sev in ("low", "medium", "high"):
        r = api.client.post(
            "/monitoring/incidents",
            json={
                "severity": sev,
                "summary": f"free text of the {sev} report, SECRET-ANALYST-WORDS",
                "reviewer": "ops.lead",
            },
        )
        assert r.status_code == 201
    body = feed(api)
    assert [(n["category"], n["severity"]) for n in body["notifications"]] == [
        ("incidents", "high"),
        ("incidents", "attention"),
    ]
    assert body["notifications"][0]["data"]["severity"] == "high"
    text = json.dumps(body)
    assert "SECRET-ANALYST-WORDS" not in text and "free text" not in text
    assert "reported by ops.lead" in body["notifications"][0]["text"]
    # a resolving report is not announced again
    api.client.post(
        "/monitoring/incidents",
        json={
            "severity": "high",
            "summary": "fixed it properly",
            "resolves": 1,
            "reviewer": "ops.lead",
        },
    )
    assert len(feed(api)["notifications"]) == 2


def test_a_test_alert_shows_up_in_the_feed_and_meets_readiness_with_the_page_caveat(
    api: Env,
) -> None:
    sent = api.client.post("/monitoring/alerts/test").json()
    assert sent["delivered"] is True and sent["sinks_configured"] == [IN_APP]
    body = feed(api)
    n = body["notifications"][0]
    assert (n["kind"], n["category"], n["severity"]) == ("test", "system", "info")
    assert "not a real alert" in n["title"] and "No action is needed" in n["text"]
    c = items(api.client.get("/monitoring/readiness").json())["5.23(c)"]
    assert c["status"] == "met" and c["delivered_by"] == [IN_APP] and c["page_only"] is True
    assert "not the same as someone watching" in c["detail"]
    assert c["watching_confirmed"] is False
    assert "in-app" in api.client.get("/monitoring").json()["alert_sinks"]


def test_without_a_test_the_page_alone_does_not_meet_item_c(api: Env) -> None:
    c = items(api.client.get("/monitoring/readiness").json())["5.23(c)"]
    assert c["status"] == "not_met" and "No test alert" in c["detail"]


def test_the_feed_is_pruned_to_the_newest_rows(db: Database, settings: Settings) -> None:
    sink = InAppSink(db)
    for i in range(MAX_ROWS + 5):
        sink.send(build_message("test", "test", settings, now=FIXED_NOW + timedelta(seconds=i)))
    with db.conn() as c:
        total = c.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    assert total == MAX_ROWS
    newest = list_notifications(db, limit=1)[0][0]
    assert newest["ts"] == ts(FIXED_NOW + timedelta(seconds=MAX_ROWS + 4))


# ---------------------------------------------------------------------------------- listing


def seed_feed(db: Database, n: int) -> None:
    with db.conn() as c:
        for i in range(n):
            c.execute(
                "INSERT INTO notifications (ts, kind, category, key, severity, title, text) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    ts(FIXED_NOW + timedelta(minutes=i)),
                    "alert" if i % 3 == 0 else ("incident" if i % 3 == 1 else "test"),
                    ("alerts", "incidents", "system")[i % 3],
                    f"k{i}",
                    "attention",
                    f"title {i}",
                    f"text {i}",
                ),
            )


def test_an_empty_feed(api: Env) -> None:
    body = feed(api)
    assert body["notifications"] == [] and body["unread"] == 0 and body["next_before"] is None


def test_newest_first_and_paged_by_id(api: Env) -> None:
    seed_feed(api.db, 7)
    p1 = feed(api, limit=3)
    assert [n["title"] for n in p1["notifications"]] == ["title 6", "title 5", "title 4"]
    assert p1["next_before"] == p1["notifications"][-1]["id"] and p1["unread"] == 7
    p2 = feed(api, limit=3, before=p1["next_before"])
    assert [n["title"] for n in p2["notifications"]] == ["title 3", "title 2", "title 1"]
    p3 = feed(api, limit=3, before=p2["next_before"])
    assert [n["title"] for n in p3["notifications"]] == ["title 0"] and p3["next_before"] is None
    exact = feed(api, limit=7)
    assert len(exact["notifications"]) == 7 and exact["next_before"] is None


def test_since_keeps_only_newer_rows_and_accepts_z_and_dates(api: Env) -> None:
    seed_feed(api.db, 5)
    cut = (FIXED_NOW + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert [n["title"] for n in feed(api, since=cut)["notifications"]] == ["title 4", "title 3"]
    assert len(feed(api, since="2020-01-01")["notifications"]) == 5
    assert feed(api, since="2999-01-01")["notifications"] == []
    bad = api.client.get("/monitoring/notifications", params={"since": "yesterday"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"


def test_category_and_unread_filters_and_limits(api: Env) -> None:
    seed_feed(api.db, 6)
    assert {n["category"] for n in feed(api, category="incidents")["notifications"]} == {
        "incidents"
    }
    assert len(feed(api, category="system")["notifications"]) == 2
    assert api.client.get("/monitoring/notifications", params={"category": "x"}).status_code == 422
    for bad in ({"limit": 0}, {"limit": 201}, {"before": 0}):
        assert api.client.get("/monitoring/notifications", params=bad).status_code == 422
    first = feed(api)["notifications"][0]["id"]
    api.client.post("/monitoring/notifications/read", json={"ids": [first], "reviewer": "a.tan"})
    assert len(feed(api, unread=True)["notifications"]) == 5
    assert feed(api, category="system", unread=True)["unread"] == 5  # the count ignores the filter


# ---------------------------------------------------------------------------------- reading


def test_marking_read_by_id_records_who_and_when_and_is_audited(api: Env) -> None:
    seed_feed(api.db, 3)
    ids = [n["id"] for n in feed(api)["notifications"]]
    r = api.client.post(
        "/monitoring/notifications/read", json={"ids": ids[:2], "reviewer": "alice.tan"}
    )
    assert r.status_code == 200 and r.json() == {"marked": 2, "unread": 1}
    rows = {n["id"]: n for n in feed(api)["notifications"]}
    assert rows[ids[0]]["read"] is True and rows[ids[0]]["read_by"] == "alice.tan"
    assert rows[ids[0]]["read_at"] == ts(FIXED_NOW) and rows[ids[2]]["read"] is False
    audit = [x for x in api.audit_rows() if x["action"] == "monitoring.notifications_read"]
    assert len(audit) == 1 and audit[0]["actor"] == "alice.tan"
    detail = json.loads(audit[0]["detail"])
    assert (
        detail["count"] == 2 and detail["all"] is False and sorted(detail["ids"]) == sorted(ids[:2])
    )


def test_mark_all_read_and_repeating_it_changes_nothing(api: Env) -> None:
    seed_feed(api.db, 4)
    assert api.client.post(
        "/monitoring/notifications/read", json={"all": True, "reviewer": "alice.tan"}
    ).json() == {"marked": 4, "unread": 0}
    again = api.client.post(
        "/monitoring/notifications/read", json={"all": True, "reviewer": "alice.tan"}
    )
    assert again.json() == {"marked": 0, "unread": 0}
    assert len([x for x in api.audit_rows() if x["action"] == "monitoring.notifications_read"]) == 1


def test_unknown_ids_change_nothing_and_leave_no_audit_row(api: Env) -> None:
    seed_feed(api.db, 1)
    r = api.client.post(
        "/monitoring/notifications/read", json={"ids": [999], "reviewer": "alice.tan"}
    )
    assert r.json() == {"marked": 0, "unread": 1}
    assert [x for x in api.audit_rows() if x["action"] == "monitoring.notifications_read"] == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"ids": []},
        {"ids": [1], "all": True},
        {"all": False},
        {"ids": [0]},
        {"ids": list(range(1, 202))},
        {"ids": [1], "extra": 1},
    ],
)
def test_the_read_request_needs_ids_or_all_and_nothing_else(api: Env, body: dict[str, Any]) -> None:
    r = api.client.post("/monitoring/notifications/read", json={**body, "reviewer": "alice.tan"})
    assert r.status_code == 422, body


def test_marking_read_needs_a_reviewer_name(api: Env) -> None:
    seed_feed(api.db, 1)
    r = api.client.post("/monitoring/notifications/read", json={"all": True})
    assert r.status_code == 422 and r.json()["error"]["code"] == "reviewer_required"
    assert summary(api.db)["unread"] == 1


def test_with_personal_accounts_only_a_signed_in_account_can_mark_read(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    s = settings.model_copy(update={"personal_accounts": True})
    env = make_env(s, db, FakeClock(FIXED_NOW))
    env.client = TestClient(env.app, client=("127.0.0.1", 50000), headers=PROXY)
    seed_feed(db, 1)
    body = {"all": True, "reviewer": "typed.name"}
    refused = env.client.post("/monitoring/notifications/read", json=body)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "account_required"
    ok = env.client.post(
        "/monitoring/notifications/read",
        json=body,
        headers={**PROXY, "X-Gldb-Reviewer": "alice.tan"},
    )
    assert ok.status_code == 200 and ok.json()["marked"] == 1
    assert (
        feed(env)["notifications"][0]["read_by"] == "alice.tan"
    )  # the account, not the typed name


# ---------------------------------------------------------------------------------- the badge


def test_summary_counts_an_open_alert_once_and_other_unread_notifications_beside_it(
    api: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert api.client.get("/monitoring/notifications/summary").json() == {
        "unread": 0,
        "open_alerts": 0,
        "badge": 0,
    }
    alert_now(monkeypatch)
    page_service(api.db, api.settings, api.clock).evaluate()
    assert api.client.get("/monitoring/notifications/summary").json() == {
        "unread": 1,
        "open_alerts": 1,
        "badge": 1,  # the alert and its own "raised" notification are one event
    }
    api.client.post("/monitoring/alerts/test")
    assert api.client.get("/monitoring/notifications/summary").json()["badge"] == 2


def test_acknowledging_an_alert_marks_its_notifications_read_under_the_acknowledger(
    api: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    alert_now(monkeypatch)
    page_service(api.db, api.settings, api.clock).evaluate()
    r = api.client.post(
        "/monitoring/alerts/partial_rate/acknowledge",
        json={"reason": "seen and being handled", "reviewer": "alice.tan"},
    )
    assert r.status_code == 200
    n = feed(api)["notifications"][0]
    assert n["read"] is True and n["read_by"] == "alice.tan"
    assert api.client.get("/monitoring/notifications/summary").json() == {
        "unread": 0,
        "open_alerts": 0,
        "badge": 0,
    }


def test_acknowledging_an_incident_marks_its_notification_read(api: Env) -> None:
    api.client.post(
        "/monitoring/incidents",
        json={"severity": "high", "summary": "something went wrong here", "reviewer": "ops.lead"},
    )
    iid = api.client.get("/monitoring/incidents").json()["incidents"][0]["id"]
    assert feed(api)["unread"] == 1
    r = api.client.post(
        f"/monitoring/incidents/{iid}/acknowledge",
        json={"note": "looking at it now", "reviewer": "alice.tan"},
    )
    assert r.status_code == 201 and feed(api)["unread"] == 0


def test_a_new_breach_after_a_resolution_is_a_new_unread_entry(
    api: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = page_service(api.db, api.settings, api.clock)
    alert_now(monkeypatch)
    svc.evaluate()
    fake_snapshot(monkeypatch, {"partial_rate": ("ok", 0.1)})
    api.clock.advance(minutes=5)
    svc.evaluate()
    alert_now(monkeypatch, value=0.6)
    api.clock.advance(minutes=5)
    svc.evaluate()
    assert [n["kind"] for n in feed(api)["notifications"]] == ["alert", "resolved", "alert"]


# ---------------------------------------------------------------------------------- what must not be there


def test_no_secret_and_no_case_text_reaches_the_table_or_an_answer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    s = secret_settings(tmp_path, monitor_min_cases=1)
    db = Database(s.database_path, data_dir=s.data_dir)
    db.init_schema()
    env = make_env(s, db, FakeClock(FIXED_NOW))
    env.app.state.alert_sleep = lambda _s: None

    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection to {req.url} failed")

    def smtp_boom(host: str, port: int, timeout: float) -> Any:
        raise OSError(f"cannot connect with {SMTP_PASSWORD}")

    env.app.state.alert_sinks = [
        InAppSink(db),
        *build_sinks(s, transport=httpx.MockTransport(refuse), smtp_factory=smtp_boom),
    ]
    applicant = "Zebulon Quixote Holdings Pte Ltd"
    secret_words = ["KESTREL-EVIDENCE-LINE", "Zebulon", "Quixote", "REF-QUIXOTE-77"]
    for i in range(2):
        seed_case(
            db,
            s,
            applicant_name=applicant,
            reference="REF-QUIXOTE-77",
            partial=True,
            files=[(f"Quixote-statement-{i}.pdf", tiny_pdf("KESTREL-EVIDENCE-LINE"))],
        )
    out = AlertService(
        db, s, clock=env.clock, sinks=env.app.state.alert_sinks, sleep=lambda _s: None
    ).evaluate()
    assert "partial_rate" in out["opened"]
    env.client.post("/monitoring/alerts/test")
    env.client.post(
        "/monitoring/incidents",
        json={
            "severity": "high",
            "summary": f"{applicant} statement was altered KESTREL-EVIDENCE-LINE",
            "case_id": "c_secret",
            "reviewer": "ops.lead",
        },
    )
    texts = [
        env.client.get("/monitoring/notifications").text,
        env.client.get("/monitoring/notifications/summary").text,
    ]
    with db.conn() as c:
        texts.append(json.dumps([dict(r) for r in c.execute("SELECT * FROM notifications")]))
    texts.append(caplog.text)
    assert json.loads(texts[0])["notifications"], "the feed has entries to check"
    for text in texts:
        for secret in [*SECRETS, *secret_words, "c_secret"]:
            assert secret not in text, secret


def test_the_page_works_with_encryption_at_rest_on(tmp_path: Path) -> None:
    import base64

    from app.core.crypto import vault_from_settings

    key = base64.b64encode(bytes([7]) * 32).decode()
    s = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=str(tmp_path / "data" / "gldb.sqlite3"),
        llm_backend="fake",
        encrypt_at_rest="on",
        encryption_keys=f"k1:{key}",
    )
    db = Database(s.database_path, data_dir=s.data_dir, vault=vault_from_settings(s))
    db.init_schema()
    env = make_env(s, db, FakeClock(FIXED_NOW))
    assert env.client.post("/monitoring/alerts/test").status_code == 200
    n = feed(env)["notifications"][0]
    assert n["kind"] == "test"
    with db.conn() as c:  # queue-style data: readable by design, and free of case content
        raw = c.execute("SELECT title, text, data FROM notifications").fetchone()
    assert "Test alert" in raw["title"] and raw["data"] == "{}"


# ---------------------------------------------------------------------------------- the frontend contract


def test_responses_are_written_for_the_frontend_contract_test(
    api: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mock data in frontend/src/lib/mock must have the same keys as these (fixtures.contract.test.ts)."""
    svc = page_service(api.db, api.settings, api.clock)
    fake_snapshot(monkeypatch, {"partial_rate": ("alert", 0.5), "rule_drift": ("alert", 0.4)})
    svc.evaluate()
    api.client.post(
        "/monitoring/alerts/rule_drift/acknowledge",
        json={"reason": "seen and being handled", "reviewer": "alice.tan"},
    )
    api.client.post(
        "/monitoring/incidents",
        json={"severity": "high", "summary": "something went wrong here", "reviewer": "ops.lead"},
    )
    iid = api.client.get("/monitoring/incidents").json()["incidents"][0]["id"]
    api.client.post(
        f"/monitoring/incidents/{iid}/acknowledge",
        json={"note": "looking at it now", "assigned_to": "platform", "reviewer": "alice.tan"},
    )
    test = api.client.post("/monitoring/alerts/test").json()
    first = feed(api)["notifications"][0]["id"]
    read = api.client.post(
        "/monitoring/notifications/read", json={"ids": [first], "reviewer": "alice.tan"}
    ).json()
    write_fixture("monitoring_notifications", feed(api))
    write_fixture(
        "monitoring_notification_summary",
        api.client.get("/monitoring/notifications/summary").json(),
    )
    write_fixture("monitoring_notification_read", read)
    write_fixture("monitoring_alerts", api.client.get("/monitoring/alerts").json())
    write_fixture("monitoring_test_alert", test)
    write_fixture("monitoring_readiness", api.client.get("/monitoring/readiness").json())
    write_fixture("monitoring_incidents", api.client.get("/monitoring/incidents").json())
