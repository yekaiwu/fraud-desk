"""Alert sinks, the alert state machine, acknowledgement, the silence check, the schedule and the CLI.

No real network: webhooks and Telegram go through ``httpx.MockTransport``, e-mail through a fake SMTP
factory. Clocks are injected, so nothing sleeps.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import smtplib
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import app.alerts as alerts_mod
from app.alerts import (
    STALE_KEY,
    AlertMessage,
    AlertService,
    EmailSink,
    FileSink,
    TelegramSink,
    WebhookSink,
    build_message,
    build_sinks,
    get_json_meta,
    list_alert_state,
    recent_deliveries,
    scheduler_status,
    start_monitor_scheduler,
)
from app.core.config import Settings
from app.core.db import Database, ts
from tests.test_api_support import FIXED_NOW, Env, FakeClock, make_env

WEBHOOK_URL = "https://hooks.example.test/services/T0/B0/TOKEN-WEBHOOK-SECRET"
HMAC_SECRET = "hmac-secret-value-123"
BOT_TOKEN = "123456:BOT-TOKEN-SECRET"
SMTP_PASSWORD = "smtp-password-SECRET"
SECRETS = (WEBHOOK_URL, "TOKEN-WEBHOOK-SECRET", HMAC_SECRET, BOT_TOKEN, SMTP_PASSWORD)


def mk_settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=str(tmp_path / "data" / "gldb.sqlite3"),
        llm_backend="fake",
        **kw,
    )


class FakeSink:
    def __init__(self, name: str = "fake", fail: list[BaseException] | None = None):
        self.name = name
        self.sent: list[AlertMessage] = []
        self.fail = list(fail or [])
        self.fp = "fp1"

    def fingerprint(self) -> str:
        return self.fp

    def send(self, msg: AlertMessage) -> None:
        if self.fail:
            raise self.fail.pop(0)
        self.sent.append(msg)


class AlwaysFails(FakeSink):
    def send(self, msg: AlertMessage) -> None:
        raise ConnectionError(f"cannot reach {WEBHOOK_URL}")


def fake_snapshot(
    monkeypatch: pytest.MonkeyPatch, rows: dict[str, tuple[str, float | None]]
) -> None:
    """Make ``snapshot`` return the given {metric: (status, value)} so the state machine is tested alone."""

    def snap(db: Database, settings: Settings, *, now: datetime) -> dict[str, Any]:
        return {
            "alerts": [
                {
                    "metric": m,
                    "value": v,
                    "threshold": 0.3,
                    "status": st,
                    "meaning": f"meaning of {m}",
                    "action": f"action for {m}",
                }
                for m, (st, v) in rows.items()
            ],
            "cases_in_window": 20,
            "enough_data": True,
        }

    monkeypatch.setattr(alerts_mod, "snapshot", snap)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(FIXED_NOW)


def svc(
    db: Database,
    settings: Settings,
    clock: FakeClock,
    sinks: list[Any] | None = None,
) -> AlertService:
    return AlertService(db, settings, clock=clock, sinks=sinks, sleep=lambda _s: None)


# ---------------------------------------------------------------------------------- messages


def test_message_names_metric_value_threshold_meaning_action_and_where_to_read(
    settings: Settings,
) -> None:
    s = settings.model_copy(update={"alert_base_url": "https://fraud.bank.test/api"})
    msg = build_message(
        "alert",
        "partial_rate",
        s,
        now=FIXED_NOW,
        value=0.5,
        threshold=0.3,
        meaning="Share of partial cases.",
        action="Open the partial cases.",
    )
    for needle in ("partial_rate", "0.5", "0.3", "Share of partial cases.", "Open the partial cases.",
                   "GET /monitoring", "https://fraud.bank.test/api/monitoring",
                   "POST /monitoring/alerts/partial_rate/acknowledge"):  # fmt: skip
        assert needle in msg.text, needle


def test_test_message_is_clearly_labelled(settings: Settings) -> None:
    msg = build_message("test", "test", settings, now=FIXED_NOW)
    assert "TEST ALERT" in msg.title and "not a real alert" in msg.text


# ---------------------------------------------------------------------------------- sinks


def test_no_sink_is_built_by_default(settings: Settings) -> None:
    assert build_sinks(settings) == []


def test_all_four_sinks_are_built_when_configured(tmp_path: Path) -> None:
    s = mk_settings(
        tmp_path,
        alert_webhook_url=WEBHOOK_URL,
        alert_smtp_host="smtp.example.test",
        alert_email_from="fraud@bank.test",
        alert_email_to="a@bank.test, b@bank.test",
        alert_telegram_bot_token=BOT_TOKEN,
        alert_telegram_chat_id="42",
        alert_file_path=tmp_path / "alerts.jsonl",
    )
    assert [x.name for x in build_sinks(s)] == ["webhook", "email", "telegram", "file"]
    half = mk_settings(tmp_path, alert_smtp_host="smtp.example.test")  # no from/to: not configured
    assert build_sinks(half) == []


def test_webhook_posts_json_with_text_and_a_verifiable_signature(settings: Settings) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200)

    sink = WebhookSink(WEBHOOK_URL, HMAC_SECRET, transport=httpx.MockTransport(handler))
    msg = build_message("alert", "partial_rate", settings, now=FIXED_NOW, value=0.5, threshold=0.3)
    sink.send(msg)
    req = seen[0]
    body = json.loads(req.content)
    assert (
        body["text"] == msg.text
        and body["event"] == "alert"
        and body["alert"]["metric"] == "partial_rate"
    )
    expected = hmac.new(HMAC_SECRET.encode(), req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-Gldb-Signature"] == f"sha256={expected}"
    assert str(req.url) == WEBHOOK_URL


def test_webhook_without_a_secret_sends_no_signature(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    sink = WebhookSink(
        WEBHOOK_URL,
        transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(204))[1]),
    )
    sink.send(build_message("test", "test", settings, now=FIXED_NOW))
    assert "X-Gldb-Signature" not in seen[0].headers


def test_telegram_sends_to_the_chat_with_the_message_text(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    sink = TelegramSink(
        BOT_TOKEN,
        "42",
        transport=httpx.MockTransport(
            lambda r: (seen.append(r), httpx.Response(200, json={"ok": True}))[1]
        ),
    )
    msg = build_message("test", "test", settings, now=FIXED_NOW)
    sink.send(msg)
    assert seen[0].url.path == f"/bot{BOT_TOKEN}/sendMessage"
    body = json.loads(seen[0].content)
    assert body["chat_id"] == "42" and body["text"] == msg.text


class FakeSMTP:
    instances: list[FakeSMTP] = []

    def __init__(self, fail_login: bool = False):
        self.calls: list[str] = []
        self.sent: list[Any] = []
        self.fail_login = fail_login
        FakeSMTP.instances.append(self)

    def starttls(self, context: Any = None) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials " + password.encode())

    def send_message(self, mail: Any) -> None:
        self.calls.append("send")
        self.sent.append(mail)

    def quit(self) -> None:
        self.calls.append("quit")


def test_email_uses_starttls_login_and_sends_to_all_recipients(settings: Settings) -> None:
    FakeSMTP.instances.clear()
    sink = EmailSink(
        "smtp.example.test", 587, "fraud@bank.test", ["a@bank.test", "b@bank.test"],
        user="u", password=SMTP_PASSWORD, smtp_factory=lambda h, p, t: FakeSMTP(),
    )  # fmt: skip
    sink.send(
        build_message("alert", "partial_rate", settings, now=FIXED_NOW, value=0.5, threshold=0.3)
    )
    smtp = FakeSMTP.instances[0]
    assert smtp.calls == ["starttls", "login:u", "send", "quit"]
    mail = smtp.sent[0]
    assert mail["To"] == "a@bank.test, b@bank.test" and "partial_rate" in mail["Subject"]


def test_file_sink_appends_json_lines(tmp_path: Path, settings: Settings) -> None:
    path = tmp_path / "sub" / "alerts.jsonl"
    sink = FileSink(path)
    sink.send(build_message("alert", "a", settings, now=FIXED_NOW, value=1.0, threshold=0.3))
    sink.send(build_message("resolved", "a", settings, now=FIXED_NOW, value=0.1, threshold=0.3))
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["event"] for x in lines] == ["alert", "resolved"]


def test_http_logs_are_quiet_so_a_telegram_token_is_not_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    build_sinks(
        mk_settings(tmp_path, alert_telegram_bot_token=BOT_TOKEN, alert_telegram_chat_id="1")
    )
    assert logging.getLogger("httpx").level >= logging.WARNING
    caplog.set_level(logging.DEBUG)
    sink = TelegramSink(
        BOT_TOKEN, "1", transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    sink.send(AlertMessage("test", "t", "t", "t"))
    assert BOT_TOKEN not in caplog.text


# ---------------------------------------------------------------------------------- delivery


def test_a_failing_sink_is_recorded_and_does_not_block_the_others(
    db: Database, settings: Settings, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    good, bad = FakeSink("file"), AlwaysFails("webhook")
    s = svc(db, settings, clock, [bad, good])
    results = s.deliver(build_message("test", "test", settings, now=FIXED_NOW))
    assert [(r.sink, r.ok) for r in results] == [("webhook", False), ("file", True)]
    assert results[0].error == "ConnectionError" and results[0].attempts == settings.alert_retries
    assert len(good.sent) == 1
    rows = recent_deliveries(db)
    assert {(r["sink"], r["ok"]) for r in rows} == {("webhook", False), ("file", True)}
    assert WEBHOOK_URL not in caplog.text and "TOKEN-WEBHOOK-SECRET" not in caplog.text
    assert "TOKEN-WEBHOOK-SECRET" not in json.dumps(rows)


def test_retries_back_off_and_then_succeed(
    db: Database, settings: Settings, clock: FakeClock
) -> None:
    sleeps: list[float] = []
    sink = FakeSink("webhook", fail=[TimeoutError(), TimeoutError()])
    s = AlertService(db, settings, clock=clock, sinks=[sink], sleep=sleeps.append)
    [r] = s.deliver(build_message("test", "test", settings, now=FIXED_NOW))
    assert r.ok and r.attempts == 3
    assert sleeps == [settings.alert_retry_backoff_s, settings.alert_retry_backoff_s * 2]


def test_a_permanent_http_error_is_not_retried(
    db: Database, settings: Settings, clock: FakeClock
) -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    sink = WebhookSink(WEBHOOK_URL, transport=httpx.MockTransport(handler))
    [r] = svc(db, settings, clock, [sink]).deliver(
        build_message("test", "test", settings, now=FIXED_NOW)
    )
    assert not r.ok and r.error == "HTTP_404" and len(calls) == 1


def test_a_5xx_is_retried_up_to_the_bound(
    db: Database, settings: Settings, clock: FakeClock
) -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    sink = WebhookSink(WEBHOOK_URL, transport=httpx.MockTransport(handler))
    [r] = svc(db, settings, clock, [sink]).deliver(
        build_message("test", "test", settings, now=FIXED_NOW)
    )
    assert r.error == "HTTP_503" and len(calls) == settings.alert_retries == r.attempts


def test_smtp_authentication_failure_is_permanent_and_never_shows_the_password(
    db: Database, settings: Settings, clock: FakeClock
) -> None:
    sink = EmailSink(
        "h", 587, "f@x.test", ["t@x.test"], user="u", password=SMTP_PASSWORD,
        smtp_factory=lambda h, p, t: FakeSMTP(fail_login=True),
    )  # fmt: skip
    [r] = svc(db, settings, clock, [sink]).deliver(
        build_message("test", "test", settings, now=FIXED_NOW)
    )
    assert not r.ok and r.attempts == 1 and r.error == "SMTPAuthenticationError"
    assert SMTP_PASSWORD not in json.dumps(recent_deliveries(db))


# ---------------------------------------------------------------------------------- state machine


def state(db: Database, key: str) -> dict[str, Any]:
    return next(a for a in list_alert_state(db) if a["key"] == key)


def test_alert_is_announced_once_not_every_run(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_snapshot(monkeypatch, {"partial_rate": ("alert", 0.5)})
    sink = FakeSink()
    s = svc(db, settings, clock, [sink])
    first = s.evaluate()
    assert first["opened"] == ["partial_rate"] and len(sink.sent) == 1
    assert sink.sent[0].kind == "alert"
    for _ in range(3):
        clock.advance(minutes=15)
        s.evaluate()
    assert len(sink.sent) == 1
    row = state(db, "partial_rate")
    assert (
        row["status"] == "open"
        and row["notify_count"] == 1
        and row["first_seen"] != row["last_seen"]
    )


def test_an_unacknowledged_alert_is_announced_again_after_the_renotify_hours(
    db: Database, tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = mk_settings(tmp_path, alert_renotify_hours=6)
    fake_snapshot(monkeypatch, {"partial_rate": ("alert", 0.5)})
    sink = FakeSink()
    s = svc(db, settings, clock, [sink])
    s.evaluate()
    clock.advance(hours=5)
    s.evaluate()
    assert len(sink.sent) == 1
    clock.advance(hours=1, minutes=1)
    s.evaluate()
    assert [m.kind for m in sink.sent] == ["alert", "reminder"]
    assert state(db, "partial_rate")["notify_count"] == 2


def test_an_acknowledged_alert_stays_quiet_even_after_the_renotify_hours(
    db: Database, tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = mk_settings(tmp_path, alert_renotify_hours=1)
    fake_snapshot(monkeypatch, {"partial_rate": ("alert", 0.5)})
    sink = FakeSink()
    s = svc(db, settings, clock, [sink])
    s.evaluate()
    s.acknowledge("partial_rate", who="alice", reason="Looking into the model service")
    clock.advance(hours=30)
    s.evaluate()
    assert len(sink.sent) == 1
    assert state(db, "partial_rate")["status"] == "acknowledged"


def test_resolution_is_announced_once_and_then_nothing(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = FakeSink()
    s = svc(db, settings, clock, [sink])
    fake_snapshot(monkeypatch, {"partial_rate": ("alert", 0.5)})
    s.evaluate()
    fake_snapshot(monkeypatch, {"partial_rate": ("ok", 0.1)})
    out = s.evaluate()
    assert out["resolved"] == ["partial_rate"]
    assert [m.kind for m in sink.sent] == ["alert", "resolved"]
    s.evaluate()
    s.evaluate()
    assert len(sink.sent) == 2
    assert state(db, "partial_rate")["status"] == "resolved"


def test_an_acknowledged_alert_resolves_and_a_new_breach_starts_a_fresh_unacknowledged_alert(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = FakeSink()
    s = svc(db, settings, clock, [sink])
    fake_snapshot(monkeypatch, {"m": ("alert", 0.5)})
    s.evaluate()
    s.acknowledge("m", who="alice", reason="Looking into it now")
    fake_snapshot(monkeypatch, {"m": ("ok", 0.1)})
    s.evaluate()
    fake_snapshot(monkeypatch, {"m": ("alert", 0.6)})
    s.evaluate()
    row = state(db, "m")
    assert row["status"] == "open" and row["acknowledged_by"] is None and row["notify_count"] == 1
    assert [m.kind for m in sink.sent] == ["alert", "resolved", "alert"]


def test_unknown_never_announces_and_never_clears_an_open_alert(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink = FakeSink()
    s = svc(db, settings, clock, [sink])
    fake_snapshot(monkeypatch, {"partial_rate": ("unknown", None)})
    s.evaluate()
    assert sink.sent == [] and list_alert_state(db) == []
    fake_snapshot(monkeypatch, {"partial_rate": ("alert", 0.5)})
    s.evaluate()
    fake_snapshot(monkeypatch, {"partial_rate": ("unknown", None)})
    for _ in range(3):
        clock.advance(hours=1)
        s.evaluate()
    row = state(db, "partial_rate")
    assert row["status"] == "open"  # too few cases is not an all-clear
    assert [m.kind for m in sink.sent] == ["alert"]


def test_a_failed_delivery_is_retried_on_the_next_run_and_only_counted_when_it_arrives(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_snapshot(monkeypatch, {"m": ("alert", 0.5)})
    bad = AlwaysFails("webhook")
    s = svc(db, settings, clock, [bad])
    out = s.evaluate()
    assert out["delivery_failed"] is True
    assert state(db, "m")["last_notified"] is None and state(db, "m")["notified"] is False
    good = FakeSink("file")
    clock.advance(minutes=15)
    out = svc(db, settings, clock, [bad, good]).evaluate()
    assert [m.kind for m in good.sent] == ["alert"] and out["delivery_failed"] is True
    assert state(db, "m")["notified"] is True  # one sink arrived
    clock.advance(minutes=15)
    svc(db, settings, clock, [bad, good]).evaluate()
    assert len(good.sent) == 1


def test_without_a_sink_alerts_are_tracked_but_unannounced_until_one_is_configured(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_snapshot(monkeypatch, {"m": ("alert", 0.5)})
    out = svc(db, settings, clock, []).evaluate()
    assert out["opened"] == ["m"] and out["notified"] == [] and out["sinks"] == []
    assert state(db, "m")["notified"] is False
    sink = FakeSink()
    svc(db, settings, clock, [sink]).evaluate()
    assert [m.kind for m in sink.sent] == ["alert"]


def test_no_resolution_message_for_an_alert_nobody_was_ever_told_about(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_snapshot(monkeypatch, {"m": ("alert", 0.5)})
    svc(db, settings, clock, []).evaluate()
    fake_snapshot(monkeypatch, {"m": ("ok", 0.1)})
    sink = FakeSink()
    svc(db, settings, clock, [sink]).evaluate()
    assert sink.sent == []


def test_opening_and_resolving_are_written_to_the_audit_log(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = svc(db, settings, clock, [FakeSink()])
    fake_snapshot(monkeypatch, {"m": ("alert", 0.5)})
    s.evaluate()
    fake_snapshot(monkeypatch, {"m": ("ok", 0.1)})
    s.evaluate()
    with db.conn() as c:
        actions = [r["action"] for r in c.execute("SELECT action FROM audit_log ORDER BY id")]
    assert actions == ["monitoring.alert_opened", "monitoring.alert_resolved"]


def test_a_real_snapshot_drives_the_whole_loop(env_with_cases: Env) -> None:
    env = env_with_cases
    sink = FakeSink()
    s = AlertService(env.db, env.settings, clock=env.clock, sinks=[sink], sleep=lambda _s: None)
    out = s.evaluate()
    assert "partial_rate" in out["opened"]
    assert any("partial_rate" in m.text for m in sink.sent)


@pytest.fixture
def env_with_cases(settings: Settings, db: Database) -> Env:
    from tests.test_monitoring import seed_many

    env = make_env(settings, db, FakeClock(FIXED_NOW))
    seed_many(env, 12, partial=6)
    env.clock.now = datetime.now(tz=FIXED_NOW.tzinfo) + timedelta(seconds=5)
    return env


# ---------------------------------------------------------------------------------- silence


def test_scheduler_off_is_reported_plainly(db: Database, settings: Settings) -> None:
    st = scheduler_status(db, settings, now=FIXED_NOW)
    assert st["scheduler"] == "off" and st["monitor_stale"] is False
    assert "Off" in st["scheduler_note"] and "monitor_once.py" in st["scheduler_note"]


def test_stale_is_reported_when_the_last_evaluation_is_older_than_two_intervals(
    db: Database, tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = mk_settings(tmp_path, monitor_interval_minutes=10)
    fake_snapshot(monkeypatch, {})
    svc(db, settings, clock, []).evaluate()
    assert (
        scheduler_status(db, settings, now=clock.now + timedelta(minutes=19))["monitor_stale"]
        is False
    )
    late = scheduler_status(db, settings, now=clock.now + timedelta(minutes=21))
    assert late["monitor_stale"] is True and late["scheduler"] == "in_process"
    assert "Stale" in late["scheduler_note"]


def test_never_evaluated_counts_from_the_moment_the_schedule_was_switched_on(
    db: Database, tmp_path: Path
) -> None:
    settings = mk_settings(tmp_path, monitor_interval_minutes=10)
    assert scheduler_status(db, settings, now=FIXED_NOW)["monitor_stale"] is False
    assert (
        scheduler_status(db, settings, now=FIXED_NOW + timedelta(minutes=25))["monitor_stale"]
        is True
    )


def test_external_scheduler_mode_is_reported_and_watched_the_same_way(
    db: Database, tmp_path: Path
) -> None:
    settings = mk_settings(tmp_path, monitor_interval_minutes=60, monitor_in_process=False)
    st = scheduler_status(db, settings, now=FIXED_NOW)
    assert st["scheduler"] == "external" and st["interval_minutes"] == 60


def test_the_next_run_after_a_silence_raises_a_stale_alert_through_the_sinks_and_it_clears(
    db: Database, tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = mk_settings(tmp_path, monitor_interval_minutes=10)
    fake_snapshot(monkeypatch, {})
    sink = FakeSink()
    s = svc(db, settings, clock, [sink])
    s.evaluate()
    clock.advance(minutes=15)  # within two intervals: fine
    assert s.evaluate()["monitor_stale"] is False and sink.sent == []
    clock.advance(minutes=45)  # silent for 45 minutes
    out = s.evaluate()
    assert out["monitor_stale"] is True and STALE_KEY in out["opened"]
    assert sink.sent[0].key == STALE_KEY and "silent" in sink.sent[0].text
    clock.advance(minutes=10)
    out = s.evaluate()
    assert STALE_KEY in out["resolved"]
    assert [m.kind for m in sink.sent] == ["alert", "resolved"]


def test_no_stale_check_when_the_schedule_is_off(
    db: Database, settings: Settings, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_snapshot(monkeypatch, {})
    s = svc(db, settings, clock, [])
    s.evaluate()
    clock.advance(days=3)
    assert s.evaluate()["monitor_stale"] is False
    assert list_alert_state(db) == []


# ---------------------------------------------------------------------------------- scheduler thread


def run_scheduler(
    db: Database, settings: Settings, clock: FakeClock, runs: int, sink: FakeSink
) -> threading_event:  # noqa: F821
    import threading

    done = threading.Event()
    calls = iter(range(runs - 1))

    def wait(_seconds: float) -> bool:
        try:
            next(calls)
            clock.advance(minutes=settings.monitor_interval_minutes)
            return False
        except StopIteration:
            done.set()
            return True

    start_monitor_scheduler(
        db, settings, now=clock, wait=wait, sinks=lambda: [sink], sleep=lambda _s: None
    )
    assert done.wait(10)
    return done


def test_the_schedule_runs_at_once_and_after_each_interval(
    db: Database, tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    caplog.set_level(logging.INFO)
    settings = mk_settings(tmp_path, monitor_interval_minutes=5, alert_webhook_url=WEBHOOK_URL)
    fake_snapshot(monkeypatch, {"m": ("alert", 0.5)})
    sink = FakeSink()
    run_scheduler(db, settings, clock, 3, sink)
    assert len(sink.sent) == 1  # announced once across three runs
    assert get_json_meta(db, "last_test") is None
    assert parse(db) == ts(clock.now)  # the stamp is the third run's time
    assert "scheduled monitor run" in caplog.text
    assert not any(x in caplog.text for x in SECRETS)


def parse(db: Database) -> str | None:
    return alerts_mod.get_meta(db, "last_evaluation")


def test_a_failing_run_is_logged_and_does_not_kill_the_schedule(
    db: Database, tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    caplog.set_level(logging.INFO)
    settings = mk_settings(tmp_path, monitor_interval_minutes=5)
    boom = {"n": 0}

    def snap(db: Database, settings: Settings, *, now: datetime) -> dict[str, Any]:
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError(f"database exploded {WEBHOOK_URL}")
        return {"alerts": [], "cases_in_window": 0, "enough_data": False}

    monkeypatch.setattr(alerts_mod, "snapshot", snap)
    run_scheduler(db, settings, clock, 2, FakeSink())
    assert boom["n"] == 2
    assert "scheduled monitor run failed: RuntimeError" in caplog.text
    assert WEBHOOK_URL not in caplog.text
    assert parse(db) is not None  # the second run succeeded and stamped


def test_a_failed_evaluation_leaves_the_stamp_old_so_silence_is_visible(
    db: Database, tmp_path: Path, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = mk_settings(tmp_path, monitor_interval_minutes=10)
    fake_snapshot(monkeypatch, {})
    svc(db, settings, clock, []).evaluate()
    stamp = parse(db)

    def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(alerts_mod, "snapshot", broken)
    clock.advance(minutes=30)
    with pytest.raises(RuntimeError):
        svc(db, settings, clock, []).evaluate()
    assert parse(db) == stamp
    assert scheduler_status(db, settings, now=clock.now)["monitor_stale"] is True


def test_create_app_starts_no_schedule_when_off_and_one_when_on(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[int] = []
    monkeypatch.setattr(
        alerts_mod,
        "start_monitor_scheduler",
        lambda db, s, **k: (
            started.append(s.monitor_interval_minutes),
            __import__("threading").Event(),
        )[1],
    )
    make_env(settings, db)
    assert started == []
    make_env(settings.model_copy(update={"monitor_interval_minutes": 15}), db)
    assert started == [15]
    make_env(
        settings.model_copy(update={"monitor_interval_minutes": 15, "monitor_in_process": False}),
        db,
    )
    assert started == [15]  # external: this process starts no thread


# ---------------------------------------------------------------------------------- config / migration


def test_schema_is_safe_to_apply_twice(db: Database) -> None:
    db.init_schema()
    db.init_schema()
    with db.conn() as c:
        names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"alert_state", "alert_deliveries", "monitor_meta"} <= names


def test_defaults_are_safe_and_off(settings: Settings) -> None:
    assert settings.monitor_interval_minutes == 0
    assert settings.ai_owner == ""
    assert settings.alert_renotify_hours == 24 and settings.alert_test_max_age_days == 30
    assert settings.personal_accounts is False
    assert build_sinks(settings) == []


def test_bad_alert_settings_fail_at_startup(settings: Settings) -> None:
    from app.core.config import SettingsError

    with pytest.raises(SettingsError):
        settings.model_copy(update={"monitor_interval_minutes": -1}).validate_startup()
    with pytest.raises(SettingsError):
        settings.model_copy(update={"alert_renotify_hours": 0}).validate_startup()


# ---------------------------------------------------------------------------------- API


SECRET = "s3cret-s3cret-s3cret-s3cret-0123456789"
PROXY = {"X-Gldb-Proxy-Secret": SECRET}


@pytest.fixture
def api(settings: Settings, db: Database) -> Env:
    env = make_env(settings, db, FakeClock(FIXED_NOW))
    env.app.state.alert_sleep = lambda _s: None
    return env


def use_sinks(env: Env, *sinks: Any) -> None:
    env.app.state.alert_sinks = list(sinks)


def open_alert(env: Env, monkeypatch: pytest.MonkeyPatch, sink: FakeSink | None = None) -> None:
    fake_snapshot(monkeypatch, {"partial_rate": ("alert", 0.5)})
    AlertService(
        env.db, env.settings, clock=env.clock, sinks=[sink or FakeSink()], sleep=lambda _s: None
    ).evaluate()


def test_get_alerts_lists_state_deliveries_and_scheduler(
    api: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_alert(api, monkeypatch)
    body = api.client.get("/monitoring/alerts").json()
    assert body["alerts"][0]["key"] == "partial_rate" and body["alerts"][0]["status"] == "open"
    assert body["deliveries"][0]["ok"] is True
    assert body["scheduler"] == "off" and body["last_test"] is None


def test_acknowledge_records_who_why_and_audits_it(
    api: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_alert(api, monkeypatch)
    r = api.client.post(
        "/monitoring/alerts/partial_rate/acknowledge",
        json={"reason": "Model service restart under way", "reviewer": "ops.lead"},
    )
    assert r.status_code == 200, r.text
    a = r.json()["alert"]
    assert a["status"] == "acknowledged" and a["acknowledged_by"] == "ops.lead"
    assert a["note"] == "Model service restart under way" and a["acknowledged_at"]
    rows = [x for x in api.audit_rows() if x["action"] == "monitoring.alert_acknowledged"]
    assert len(rows) == 1 and rows[0]["actor"] == "ops.lead"
    assert json.loads(rows[0]["detail"])["key"] == "partial_rate"
    # a second acknowledgement of the same alert is refused, it is no longer open
    again = api.client.post(
        "/monitoring/alerts/partial_rate/acknowledge", json={"reason": "also me", "reviewer": "x.y"}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "alert_not_open"


def test_acknowledge_needs_a_reason_and_a_name_and_a_known_alert(
    api: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_alert(api, monkeypatch)
    url = "/monitoring/alerts/partial_rate/acknowledge"
    no_reason = api.client.post(url, json={"reviewer": "ops.lead"})
    assert no_reason.status_code == 422 and no_reason.json()["error"]["code"] == "reason_required"
    no_name = api.client.post(url, json={"reason": "seen and handled"})
    assert no_name.status_code == 422 and no_name.json()["error"]["code"] == "reviewer_required"
    missing = api.client.post(
        "/monitoring/alerts/nope/acknowledge",
        json={"reason": "seen and handled", "reviewer": "ops.lead"},
    )
    assert missing.status_code == 404
    assert [x for x in api.audit_rows() if x["action"] == "monitoring.alert_acknowledged"] == []


@pytest.fixture
def gated(settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)
    s = settings.model_copy(update={"personal_accounts": True})
    env = make_env(s, db, FakeClock(FIXED_NOW))
    env.client = TestClient(env.app, client=("127.0.0.1", 50000), headers=PROXY)
    env.app.state.alert_sleep = lambda _s: None
    return env


def test_with_personal_accounts_a_typed_name_cannot_acknowledge(
    gated: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_alert(gated, monkeypatch)
    url = "/monitoring/alerts/partial_rate/acknowledge"
    body = {"reason": "seen and handled", "reviewer": "typed.name"}
    refused = gated.client.post(url, json=body)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "account_required"
    ok = gated.client.post(url, json=body, headers={**PROXY, "X-Gldb-Reviewer": "alice.tan"})
    assert ok.status_code == 200
    assert ok.json()["alert"]["acknowledged_by"] == "alice.tan"  # the account, not the typed name
    row = [x for x in gated.audit_rows() if x["action"] == "monitoring.alert_acknowledged"][0]
    assert row["actor"] == "alice.tan"


def test_test_alert_goes_through_every_sink_and_records_each_result(api: Env) -> None:
    good, bad = FakeSink("file"), AlwaysFails("webhook")
    use_sinks(api, bad, good)
    r = api.client.post("/monitoring/alerts/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delivered"] is True and body["sinks_configured"] == ["webhook", "file"]
    by = {x["sink"]: x for x in body["results"]}
    assert (
        by["file"]["ok"] and not by["webhook"]["ok"] and by["webhook"]["error"] == "ConnectionError"
    )
    assert "TEST ALERT" in good.sent[0].title
    last = api.client.get("/monitoring/alerts").json()["last_test"]
    assert last["delivered_to"] == ["file"] and last["at"]
    assert any(x["action"] == "monitoring.test_alert" for x in api.audit_rows())
    assert "TOKEN-WEBHOOK-SECRET" not in r.text


def test_test_alert_with_no_sink_says_so(api: Env) -> None:
    body = api.client.post("/monitoring/alerts/test").json()
    assert (
        body["delivered"] is False
        and body["sinks_configured"] == []
        and "No sink" in body["message"]
    )


def test_test_alerts_are_rate_limited(api: Env) -> None:
    use_sinks(api, FakeSink())
    assert api.client.post("/monitoring/alerts/test").status_code == 200
    assert api.client.post("/monitoring/alerts/test").status_code == 429
    api.clock.advance(seconds=31)
    assert api.client.post("/monitoring/alerts/test").status_code == 200


def test_monitoring_response_carries_scheduler_stale_sinks_and_readiness(api: Env) -> None:
    use_sinks(api, FakeSink("file"))
    body = api.client.get("/monitoring").json()
    assert body["scheduler"] == "off" and body["monitor_stale"] is False
    assert body["alert_sinks"] == ["file"] and body["open_alerts"] == []
    assert (
        body["readiness"]["ready"] is False and body["readiness"]["accountable_person"] == "not_met"
    )
    assert {"alerts", "alerting", "owner_named", "disk_encryption"} <= set(body)  # unchanged keys


def test_monitoring_reports_stale_when_the_schedule_has_gone_quiet(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = settings.model_copy(update={"monitor_interval_minutes": 10, "monitor_in_process": False})
    env = make_env(s, db, FakeClock(FIXED_NOW))
    assert env.client.get("/monitoring").json()["monitor_stale"] is False
    env.clock.advance(minutes=25)
    body = env.client.get("/monitoring").json()
    assert body["monitor_stale"] is True and body["scheduler"] == "external"


# ---------------------------------------------------------------------------------- incidents


def post_incident(env: Env, severity: str, **extra: Any) -> dict[str, Any]:
    r = env.client.post(
        "/monitoring/incidents",
        json={"severity": severity, "summary": "A flag was wrong in a way that mattered.",
              "reviewer": "ops.lead", **extra},
    )  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def test_medium_and_high_incidents_are_announced_low_ones_are_not(api: Env) -> None:
    sink = FakeSink()
    use_sinks(api, sink)
    post_incident(api, "low")
    assert sink.sent == []
    med = post_incident(api, "medium")
    high = post_incident(api, "high")
    assert [m.kind for m in sink.sent] == ["incident", "incident"]
    assert f"#{med['id']}" in sink.sent[0].title and "severity high" in sink.sent[1].text
    # the free-text summary stays in the audit log; it is not sent to an outside channel
    assert all("A flag was wrong" not in m.text for m in sink.sent)
    assert high["id"] != med["id"]


def test_a_failing_sink_never_fails_the_incident_report(api: Env) -> None:
    use_sinks(api, AlwaysFails("webhook"))
    assert post_incident(api, "high")["severity"] == "high"
    assert api.client.get("/monitoring/incidents").json()["incidents"][0]["status"] == "open"
    assert recent_deliveries(api.db)[0]["kind"] == "incident"


def test_resolving_report_does_not_announce_again(api: Env) -> None:
    sink = FakeSink()
    use_sinks(api, sink)
    first = post_incident(api, "high")["id"]
    post_incident(api, "high", resolves=first)
    assert len(sink.sent) == 1


def test_incident_acknowledgement_and_assignment_do_not_change_open_or_resolved(api: Env) -> None:
    first = post_incident(api, "medium")["id"]
    r = api.client.post(
        f"/monitoring/incidents/{first}/acknowledge",
        json={"note": "Taking this one", "assigned_to": "priya.n", "reviewer": "ops.lead"},
    )
    assert r.status_code == 201, r.text
    row = {i["id"]: i for i in api.client.get("/monitoring/incidents").json()["incidents"]}[first]
    assert row["status"] == "open"
    assert row["acknowledged_by"] == "ops.lead" and row["assigned_to"] == "priya.n"
    assert row["ack_note"] == "Taking this one"
    post_incident(api, "low", resolves=first)
    row = {i["id"]: i for i in api.client.get("/monitoring/incidents").json()["incidents"]}[first]
    assert row["status"] == "resolved" and row["assigned_to"] == "priya.n"
    again = api.client.post(
        f"/monitoring/incidents/{first}/acknowledge",
        json={"note": "late note", "reviewer": "ops.lead"},
    )
    assert again.status_code == 404  # only an open incident takes a note
    assert api.client.post(
        "/monitoring/incidents/9999/acknowledge", json={"note": "no such one", "reviewer": "a.b"}
    ).status_code == 404  # fmt: skip


def test_incident_acknowledgement_needs_the_account_when_accounts_are_configured(
    gated: Env,
) -> None:
    rid = post_incident_gated(gated)
    body = {"note": "Taking this one", "reviewer": "typed.name"}
    assert (
        gated.client.post(f"/monitoring/incidents/{rid}/acknowledge", json=body).status_code == 403
    )
    ok = gated.client.post(
        f"/monitoring/incidents/{rid}/acknowledge", json=body,
        headers={**PROXY, "X-Gldb-Reviewer": "alice.tan"},
    )  # fmt: skip
    assert ok.status_code == 201 and ok.json()["acknowledged_by"] == "alice.tan"


def post_incident_gated(env: Env) -> int:
    r = env.client.post(
        "/monitoring/incidents",
        json={"severity": "low", "summary": "A flag was wrong in a way that mattered."},
        headers={**PROXY, "X-Gldb-Reviewer": "alice.tan"},
    )
    return r.json()["id"]


# ---------------------------------------------------------------------------------- secrets


def secret_settings(tmp_path: Path, **kw: Any) -> Settings:
    return mk_settings(
        tmp_path,
        alert_webhook_url=WEBHOOK_URL,
        alert_webhook_secret=HMAC_SECRET,
        alert_smtp_host="smtp.example.test",
        alert_smtp_user="mailer",
        alert_smtp_password=SMTP_PASSWORD,
        alert_email_from="fraud@bank.test",
        alert_email_to="a@bank.test",
        alert_telegram_bot_token=BOT_TOKEN,
        alert_telegram_chat_id="42",
        ai_owner="J. Tan, Head of Fraud Analytics",
        **kw,
    )


def test_secrets_never_reach_logs_responses_the_audit_log_or_the_delivery_table(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    s = secret_settings(tmp_path)
    db = Database(s.database_path, data_dir=s.data_dir)
    db.init_schema()
    env = make_env(s, db, FakeClock(FIXED_NOW))

    def leaky(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            f"connection to {req.url} failed"
        )  # the message holds URL and token

    def smtp_boom(host: str, port: int, timeout: float) -> Any:
        raise OSError(f"cannot connect with {SMTP_PASSWORD}")

    env.app.state.alert_sinks = build_sinks(
        s, transport=httpx.MockTransport(leaky), smtp_factory=smtp_boom
    )
    env.app.state.alert_sleep = lambda _s: None
    texts = [
        env.client.post("/monitoring/alerts/test").text,
        env.client.get("/monitoring").text,
        env.client.get("/monitoring/alerts").text,
        env.client.get("/monitoring/readiness").text,
        env.client.post(
            "/monitoring/incidents",
            json={
                "severity": "high",
                "summary": "something went wrong here",
                "reviewer": "ops.lead",
            },
        ).text,
    ]
    AlertService(
        db, s, clock=env.clock, sinks=env.app.state.alert_sinks, sleep=lambda _s: None
    ).evaluate()
    with db.conn() as c:
        texts.append(json.dumps([dict(r) for r in c.execute("SELECT * FROM audit_log")]))
        texts.append(json.dumps([dict(r) for r in c.execute("SELECT * FROM alert_deliveries")]))
        texts.append(json.dumps([dict(r) for r in c.execute("SELECT * FROM monitor_meta")]))
        texts.append(json.dumps([dict(r) for r in c.execute("SELECT * FROM alert_state")]))
    texts.append(caplog.text)
    assert "alert delivery failed" in caplog.text  # the failure was logged, class only
    for text in texts:
        for secret in SECRETS:
            assert secret not in text, secret


# ---------------------------------------------------------------------------------- CLI


def test_monitor_once_evaluates_once_and_writes_the_file_sink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import monitor_once

    out_file = tmp_path / "alerts.jsonl"
    monkeypatch.setenv("GLDB_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("GLDB_DB_PATH", str(tmp_path / "d" / "x.sqlite3"))
    monkeypatch.setenv("GLDB_LLM_BACKEND", "fake")
    monkeypatch.setenv("GLDB_ALERT_FILE_PATH", str(out_file))
    monkeypatch.setenv("GLDB_ALERT_WEBHOOK_URL", WEBHOOK_URL)
    fake_snapshot(monkeypatch, {"partial_rate": ("alert", 0.5)})
    # the webhook is unreachable on purpose (tests have no network): exit code 2, nothing leaks
    monkeypatch.setattr(
        alerts_mod, "build_sinks",
        lambda s, **k: [WebhookSink(WEBHOOK_URL, transport=httpx.MockTransport(_refuse)), FileSink(out_file)],
    )  # fmt: skip
    monkeypatch.setattr(alerts_mod.time, "sleep", lambda _s: None)
    code = monitor_once.main(["--json"])
    printed = capsys.readouterr().out
    assert code == 2
    result = json.loads(printed)
    assert result["opened"] == ["partial_rate"] and result["source"] == "cli"
    assert WEBHOOK_URL not in printed and "TOKEN-WEBHOOK-SECRET" not in printed
    lines = [json.loads(x) for x in out_file.read_text().splitlines()]
    assert [x["event"] for x in lines] == ["alert"]
    # a second run changes nothing: announced once
    monitor_once.main([])
    assert len(out_file.read_text().splitlines()) == 1


def _refuse(req: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"no route to {req.url}")


def test_monitor_once_test_flag_and_exit_codes(
    tmp_path: Path, db: Database, settings: Settings
) -> None:
    from scripts import monitor_once

    sink = FakeSink()
    code, result = monitor_once.run(settings, db=db, test=True, sinks=[sink])
    assert code == 0 and result["delivered"] is True
    with db.conn() as c:  # the test endpoint is rate limited; start the next one clean
        c.execute("DELETE FROM monitor_meta WHERE name = 'last_test'")
    code, result = monitor_once.run(settings, db=db, test=True, sinks=[])
    assert code == 2 and result["delivered"] is False
    code, result = monitor_once.run(settings, db=db, sinks=[sink])
    assert code == 0 and result["source"] == "cli"
    assert get_json_meta(db, "last_test") is not None


def test_monitor_once_reports_an_evaluation_failure_by_class_only(
    db: Database, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import monitor_once

    def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError(f"token {BOT_TOKEN}")

    monkeypatch.setattr(alerts_mod, "snapshot", broken)
    code, result = monitor_once.run(settings, db=db, sinks=[])
    assert code == 1 and result == {"error": "RuntimeError"}


_ = Callable  # keep the import used by type checkers
