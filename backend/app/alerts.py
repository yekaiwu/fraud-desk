"""Alert delivery, acknowledgement and the monitor's own silence check (docs/ai-incidents.md).

``monitoring.snapshot`` only reads. This module turns what it finds into something a person is told:

* **Sinks** (all optional, configured by ``GLDB_ALERT_*``): a JSON webhook (Slack, Teams or any receiver, with
  an optional HMAC signature), e-mail over SMTP, a Telegram bot and a local JSON-lines file. A failing sink is
  recorded (sink, time, error class) and never blocks the others. Retries are bounded.
* **State** (``alert_state``): one row per alert key. A metric that goes to ``alert`` opens an alert and is
  announced once; an open alert nobody acknowledged is announced again after ``GLDB_ALERT_RENOTIFY_HOURS``;
  an acknowledged one stays quiet; going back to ``ok`` resolves it and says so. ``unknown`` (too few cases)
  neither announces nor clears anything.
* **Silence** (dead-man switch): every successful evaluation stamps ``last_evaluation``. When the schedule is
  on and the stamp is more than two intervals old, ``GET /monitoring`` says ``monitor_stale`` and the next run
  raises the ``monitor_stale`` alert through the sinks.

Secrets never leave this module in a log line, a response or an audit row: failures are recorded as the
exception class only (an httpx message can contain the URL, which can contain a token).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import smtplib
import sqlite3
import ssl
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.audit import AuditLog, scrub, sha256_text
from app.core.config import Settings
from app.core.db import Database, ts
from app.core.errors import ApiError
from app.core.privacy import host_allowed, parse_allowed, url_allowed
from app.monitoring import snapshot

log = logging.getLogger("gldb.alerts")

SYSTEM_ACTOR = "system"
STALE_KEY = "monitor_stale"
STALE_FACTOR = 2  # silent for this many intervals = stale
SIGNATURE_HEADER = "X-Gldb-Signature"
MAX_DELIVERY_ROWS = 2000
TEST_MIN_GAP_S = (
    30  # a test alert at most every 30 seconds, so the endpoint cannot be used to spam a channel
)

# in-process locks: one evaluation at a time; short critical sections around state read-modify-write
_EVAL_LOCK = threading.Lock()
_STATE_LOCK = threading.RLock()


# --------------------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertMessage:
    kind: str  # alert | reminder | resolved | test | incident
    key: str
    title: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    sent_at: str = ""


def _num(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4g}"


def _where(settings: Settings, path: str) -> str:
    base = settings.alert_base_url.strip().rstrip("/")
    return f"GET {path}" + (f"  ({base}{path})" if base else "")


def build_message(
    kind: str,
    key: str,
    settings: Settings,
    *,
    now: datetime,
    value: float | None = None,
    threshold: float | None = None,
    meaning: str = "",
    action: str = "",
    first_seen: str | None = None,
    extra: dict[str, Any] | None = None,
) -> AlertMessage:
    """Plain text that stands alone: the metric, the number, the limit, what it means, what to do, where to look."""
    stamp = ts(now)
    head = "GLDB fraud tool"
    data: dict[str, Any] = {"metric": key, "value": value, "threshold": threshold, **(extra or {})}
    if kind in ("alert", "reminder"):
        title = f"{'REMINDER' if kind == 'reminder' else 'ALERT'}: {key}"
        lines = [
            f"{head} - {title}",
            f"Value {_num(value)} is above the threshold {_num(threshold)} (as of {stamp}).",
        ]
        if kind == "reminder":
            lines.append(f"Still open and not acknowledged since {first_seen or 'first seen'}.")
        lines += [f"Meaning: {meaning}", f"Action: {action}"]
        lines += [
            f"Read: {_where(settings, '/monitoring')}",
            f"Acknowledge, with a reason: POST /monitoring/alerts/{key}/acknowledge",
        ]
    elif kind == "resolved":
        title = f"RESOLVED: {key}"
        lines = [
            f"{head} - {title}",
            f"Value {_num(value)} is back within the threshold {_num(threshold)} (as of {stamp}).",
            f"Read: {_where(settings, '/monitoring/alerts')}",
        ]
    elif kind == "incident":
        title = f"AI INCIDENT ({data.get('severity', '?')}): #{data.get('incident_id', '?')}"
        lines = [
            f"{head} - {title}",
            f"An AI incident of severity {data.get('severity')} was reported by {data.get('reported_by')}.",
            f"The summary is not sent over this channel. Read it: {_where(settings, '/monitoring/incidents')}",
        ]
    else:  # test
        title = "TEST ALERT (not a real alert)"
        lines = [
            f"{head} - {title}",
            f"Sent at {stamp} to check that alerts reach this place. No action is needed.",
            f"Read: {_where(settings, '/monitoring/alerts')}",
        ]
    return AlertMessage(
        kind=kind, key=key, title=title, text="\n".join(lines), data=data, sent_at=stamp
    )


# --------------------------------------------------------------------------------------
# Sinks
# --------------------------------------------------------------------------------------


class SinkHTTPError(Exception):
    """A non-2xx answer. Carries only the status, never the URL."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status

    @property
    def permanent(self) -> bool:
        return 400 <= self.status < 500 and self.status not in (408, 429)


def error_label(exc: BaseException) -> str:
    """What is stored and shown about a failure: the class (and HTTP status), never the message."""
    if isinstance(exc, SinkHTTPError):
        return f"HTTP_{exc.status}"
    return type(exc).__name__


def _permanent(exc: BaseException) -> bool:
    return (isinstance(exc, SinkHTTPError) and exc.permanent) or isinstance(
        exc, smtplib.SMTPAuthenticationError
    )


class Sink(Protocol):
    name: str

    def send(self, msg: AlertMessage) -> None: ...

    def fingerprint(self) -> str: ...


def _fp(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


class WebhookSink:
    """JSON POST. ``text`` is what Slack and Teams incoming webhooks show; the other keys are for a generic
    receiver. With a secret the raw body is signed: ``X-Gldb-Signature: sha256=<hex hmac>``."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        secret: str = "",
        *,
        timeout: float = 8.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._url, self._secret, self._timeout, self._transport = url, secret, timeout, transport

    def fingerprint(self) -> str:
        return _fp("webhook", self._url, self._secret)

    def send(self, msg: AlertMessage) -> None:
        payload = {
            "text": msg.text,
            "event": msg.kind,
            "key": msg.key,
            "title": msg.title,
            "sent_at": msg.sent_at,
            "alert": msg.data,
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._secret:
            digest = hmac.new(self._secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers[SIGNATURE_HEADER] = f"sha256={digest}"
        with httpx.Client(
            timeout=self._timeout, transport=self._transport, follow_redirects=False
        ) as client:
            resp = client.post(self._url, content=body, headers=headers)
        if not 200 <= resp.status_code < 300:
            raise SinkHTTPError(resp.status_code)


class TelegramSink:
    name = "telegram"
    API = "https://api.telegram.org"

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout: float = 8.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._token, self._chat, self._timeout, self._transport = token, chat_id, timeout, transport

    def fingerprint(self) -> str:
        return _fp("telegram", self._token, self._chat)

    def send(self, msg: AlertMessage) -> None:
        with httpx.Client(
            timeout=self._timeout, transport=self._transport, follow_redirects=False
        ) as client:
            resp = client.post(
                f"{self.API}/bot{self._token}/sendMessage",
                json={
                    "chat_id": self._chat,
                    "text": msg.text[:4000],
                    "disable_web_page_preview": True,
                },
            )
        if not 200 <= resp.status_code < 300:
            raise SinkHTTPError(resp.status_code)


SmtpFactory = Callable[[str, int, float], smtplib.SMTP]


def _default_smtp(host: str, port: int, timeout: float) -> smtplib.SMTP:
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
    return smtplib.SMTP(host, port, timeout=timeout)


class EmailSink:
    name = "email"

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        recipients: Sequence[str],
        *,
        starttls: bool = True,
        user: str = "",
        password: str = "",
        timeout: float = 8.0,
        smtp_factory: SmtpFactory | None = None,
    ):
        self._host, self._port, self._sender = host, port, sender
        self._to, self._starttls, self._user, self._password = (
            list(recipients),
            starttls,
            user,
            password,
        )
        self._timeout = timeout
        self._factory = smtp_factory or _default_smtp

    def fingerprint(self) -> str:
        return _fp(
            "email", self._host, str(self._port), self._sender, ",".join(self._to), self._user,
            self._password,
        )  # fmt: skip

    def send(self, msg: AlertMessage) -> None:
        mail = EmailMessage()
        mail["Subject"] = f"[GLDB] {msg.title}"
        mail["From"] = self._sender
        mail["To"] = ", ".join(self._to)
        mail.set_content(msg.text)
        smtp = self._factory(self._host, self._port, self._timeout)
        try:
            if self._starttls and self._port != 465:
                smtp.starttls(context=ssl.create_default_context())
            if self._user:
                smtp.login(self._user, self._password)
            smtp.send_message(mail)
        finally:
            with contextlib.suppress(Exception):  # a failed goodbye must not hide the real result
                smtp.quit()


class FileSink:
    """Append-only JSON lines. The last resort: it always works, but nobody is told, so on its own it is a record."""

    name = "file"

    def __init__(self, path: Path):
        self._path = Path(path)

    def fingerprint(self) -> str:
        return _fp("file", str(self._path))

    def send(self, msg: AlertMessage) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": msg.sent_at,
                "event": msg.kind,
                "key": msg.key,
                "title": msg.title,
                "text": msg.text,
                "alert": msg.data,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _quiet_http_logs() -> None:
    """httpx logs every request URL at INFO, and a Telegram URL holds the bot token."""
    for name in ("httpx", "httpcore"):
        lg = logging.getLogger(name)
        if lg.level == logging.NOTSET or lg.level < logging.WARNING:
            lg.setLevel(logging.WARNING)


def recipients(settings: Settings) -> list[str]:
    return [r.strip() for r in settings.alert_email_to.split(",") if r.strip()]


def build_sinks(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    smtp_factory: SmtpFactory | None = None,
) -> list[Sink]:
    """The sinks that are configured; an unconfigured one is simply absent."""
    _quiet_http_logs()
    sinks: list[Sink] = []
    private = settings.private_mode == "on"
    allowed = parse_allowed(settings.private_allowed_hosts)
    url = settings.alert_webhook_url.get_secret_value().strip()
    if url and private and not url_allowed(url, allowed):
        log.warning("private mode: the alert webhook points outside the private network; not used")
        url = ""
    if url:
        sinks.append(
            WebhookSink(
                url,
                settings.alert_webhook_secret.get_secret_value(),
                timeout=settings.alert_timeout_s,
                transport=transport,
            )
        )
    if (
        settings.alert_smtp_host.strip()
        and settings.alert_email_from.strip()
        and recipients(settings)
        and not (private and not host_allowed(settings.alert_smtp_host, allowed))
    ):
        sinks.append(
            EmailSink(
                settings.alert_smtp_host.strip(),
                settings.alert_smtp_port,
                settings.alert_email_from.strip(),
                recipients(settings),
                starttls=settings.alert_smtp_starttls,
                user=settings.alert_smtp_user,
                password=settings.alert_smtp_password.get_secret_value(),
                timeout=settings.alert_timeout_s,
                smtp_factory=smtp_factory,
            )
        )
    token = settings.alert_telegram_bot_token.get_secret_value().strip()
    if (
        token and settings.alert_telegram_chat_id.strip() and not private
    ):  # hosted: never in private mode
        sinks.append(
            TelegramSink(
                token,
                settings.alert_telegram_chat_id.strip(),
                timeout=settings.alert_timeout_s,
                transport=transport,
            )
        )
    if settings.alert_file_path:
        sinks.append(FileSink(settings.alert_file_path))
    return sinks


# --------------------------------------------------------------------------------------
# Storage helpers
# --------------------------------------------------------------------------------------


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def get_meta(db: Database, name: str) -> str | None:
    with db.conn() as c:
        row = c.execute("SELECT value FROM monitor_meta WHERE name = ?", (name,)).fetchone()
    return None if row is None else str(row["value"])


def set_meta(db: Database, name: str, value: str, now: datetime) -> None:
    with db.conn() as c:
        c.execute(
            "INSERT INTO monitor_meta (name, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (name, value, ts(now)),
        )


def set_meta_if_missing(db: Database, name: str, value: str, now: datetime) -> str:
    with db.conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO monitor_meta (name, value, updated_at) VALUES (?,?,?)",
            (name, value, ts(now)),
        )
        row = c.execute("SELECT value FROM monitor_meta WHERE name = ?", (name,)).fetchone()
    return str(row["value"])


def get_json_meta(db: Database, name: str) -> dict[str, Any] | None:
    raw = get_meta(db, name)
    if raw is None:
        return None
    try:
        out = json.loads(raw)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _state_dict(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "key": r["key"],
        "status": r["status"],
        "first_seen": r["first_seen"],
        "last_seen": r["last_seen"],
        "last_notified": r["last_notified"],
        "notify_count": r["notify_count"],
        "notified": r["last_notified"] is not None,
        "value": r["value"],
        "threshold": r["threshold"],
        "meaning": r["meaning"],
        "action": r["action"],
        "acknowledged_by": r["acknowledged_by"],
        "acknowledged_at": r["acknowledged_at"],
        "note": r["note"],
        "resolved_at": r["resolved_at"],
    }


def list_alert_state(db: Database) -> list[dict[str, Any]]:
    """Open first, then acknowledged, then resolved; newest first within a status."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM alert_state ORDER BY CASE status WHEN 'open' THEN 0 "
            "WHEN 'acknowledged' THEN 1 ELSE 2 END, last_seen DESC, key"
        ).fetchall()
    return [_state_dict(r) for r in rows]


def recent_deliveries(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT ts, sink, kind, alert_key, ok, attempts, error FROM alert_deliveries "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "ts": r["ts"],
            "sink": r["sink"],
            "kind": r["kind"],
            "alert_key": r["alert_key"],
            "ok": bool(r["ok"]),
            "attempts": r["attempts"],
            "error": r["error"],
        }
        for r in rows
    ]


@dataclass(frozen=True)
class DeliveryResult:
    sink: str
    ok: bool
    attempts: int
    error: str | None
    at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sink": self.sink,
            "ok": self.ok,
            "attempts": self.attempts,
            "error": self.error,
            "at": self.at,
        }


# --------------------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------------------


@dataclass
class Candidate:
    key: str
    status: str  # alert | ok | unknown
    value: float | None
    threshold: float | None
    meaning: str
    action: str


class AlertService:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        *,
        clock: Callable[[], datetime],
        sinks: Sequence[Sink] | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.db = db
        self.settings = settings
        self.clock = clock
        self._sinks = list(sinks) if sinks is not None else None
        self._sleep = sleep or time.sleep
        self.audit = AuditLog(db, settings, clock=clock)

    @property
    def sinks(self) -> list[Sink]:
        if self._sinks is None:
            self._sinks = build_sinks(self.settings)
        return self._sinks

    # ------------------------------------------------------------------ delivery

    def _send_with_retries(self, sink: Sink, msg: AlertMessage) -> tuple[bool, int, str | None]:
        attempts = max(1, int(self.settings.alert_retries))
        error: str | None = None
        for n in range(attempts):
            try:
                sink.send(msg)
                return True, n + 1, None
            except Exception as exc:  # noqa: BLE001 - one bad sink must not stop the others
                error = error_label(exc)
                if _permanent(exc) or n == attempts - 1:
                    return False, n + 1, error
                self._sleep(self.settings.alert_retry_backoff_s * (2**n))
        return False, attempts, error

    def deliver(self, msg: AlertMessage) -> list[DeliveryResult]:
        """Send to every configured sink, record each outcome, return them. Never raises."""
        results: list[DeliveryResult] = []
        for sink in self.sinks:
            ok, attempts, error = self._send_with_retries(sink, msg)
            results.append(DeliveryResult(sink.name, ok, attempts, error, ts(self.clock())))
            if not ok:
                log.warning(
                    "alert delivery failed sink=%s kind=%s key=%s error=%s",
                    sink.name,
                    msg.kind,
                    msg.key,
                    error,
                )
        self._record(results, msg)
        return results

    def _record(self, results: list[DeliveryResult], msg: AlertMessage) -> None:
        if not results:
            return
        try:
            with self.db.conn() as c:
                c.executemany(
                    "INSERT INTO alert_deliveries (ts, sink, kind, alert_key, ok, attempts, error) "
                    "VALUES (?,?,?,?,?,?,?)",
                    [
                        (r.at, r.sink, msg.kind, msg.key, int(r.ok), r.attempts, r.error)
                        for r in results
                    ],
                )
                c.execute(
                    "DELETE FROM alert_deliveries WHERE id <= "
                    "(SELECT MAX(id) FROM alert_deliveries) - ?",
                    (MAX_DELIVERY_ROWS,),
                )
        except Exception as exc:  # noqa: BLE001 - a bookkeeping failure must not lose the alert run
            log.error("could not record alert deliveries: %s", type(exc).__name__)

    # ------------------------------------------------------------------ state machine

    def _apply(self, cand: Candidate, now: datetime) -> str | None:
        """Update the row for one candidate; returns "opened", "resolved" or None. ``unknown`` does nothing."""
        if cand.status == "unknown":
            return None
        stamp = ts(now)
        with _STATE_LOCK, self.db.conn() as c:
            row = c.execute("SELECT * FROM alert_state WHERE key = ?", (cand.key,)).fetchone()
            if cand.status == "alert":
                if row is None or row["status"] == "resolved":
                    c.execute(
                        "INSERT INTO alert_state (key, status, first_seen, last_seen, value, threshold, "
                        "meaning, action) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                        "status='open', first_seen=excluded.first_seen, last_seen=excluded.last_seen, "
                        "last_notified=NULL, notify_count=0, value=excluded.value, "
                        "threshold=excluded.threshold, meaning=excluded.meaning, action=excluded.action, "
                        "acknowledged_by=NULL, acknowledged_at=NULL, note=NULL, resolved_at=NULL, "
                        "resolution_notified=1",
                        (cand.key, "open", stamp, stamp, cand.value, cand.threshold, cand.meaning, cand.action),
                    )  # fmt: skip
                    return "opened"
                c.execute(
                    "UPDATE alert_state SET last_seen=?, value=?, threshold=?, meaning=?, action=? "
                    "WHERE key=?",
                    (stamp, cand.value, cand.threshold, cand.meaning, cand.action, cand.key),
                )
                return None
            # ok: resolve what is open or acknowledged
            if row is not None and row["status"] in ("open", "acknowledged"):
                told = row["notify_count"] > 0
                c.execute(
                    "UPDATE alert_state SET status='resolved', resolved_at=?, last_seen=?, value=?, "
                    "threshold=?, resolution_notified=? WHERE key=?",
                    (stamp, stamp, cand.value, cand.threshold, 0 if told else 1, cand.key),
                )
                return "resolved"
        return None

    def _due(self, now: datetime) -> list[sqlite3.Row]:
        """Open alerts that have not been announced, or whose reminder is due; resolutions not yet announced."""
        renotify = timedelta(hours=self.settings.alert_renotify_hours)
        with self.db.conn() as c:
            rows = c.execute(
                "SELECT * FROM alert_state WHERE status IN ('open','resolved')"
            ).fetchall()
        out = []
        for r in rows:
            if r["status"] == "resolved":
                if not r["resolution_notified"]:
                    out.append(r)
                continue
            last = parse_ts(r["last_notified"])
            if last is None or now - last >= renotify:
                out.append(r)
        return out

    def _announce(self, row: sqlite3.Row, now: datetime) -> dict[str, Any]:
        resolved = row["status"] == "resolved"
        kind = "resolved" if resolved else ("reminder" if row["notify_count"] > 0 else "alert")
        msg = build_message(
            kind,
            row["key"],
            self.settings,
            now=now,
            value=row["value"],
            threshold=row["threshold"],
            meaning=row["meaning"],
            action=row["action"],
            first_seen=row["first_seen"],
        )
        results = self.deliver(msg)
        delivered = [r.sink for r in results if r.ok]
        if delivered:
            with _STATE_LOCK, self.db.conn() as c:
                if resolved:
                    c.execute(
                        "UPDATE alert_state SET resolution_notified=1 WHERE key=?", (row["key"],)
                    )
                else:
                    c.execute(
                        "UPDATE alert_state SET last_notified=?, notify_count=notify_count+1 "
                        "WHERE key=? AND status='open'",
                        (ts(now), row["key"]),
                    )
        return {
            "key": row["key"],
            "kind": kind,
            "delivered_to": delivered,
            "failed": [{"sink": r.sink, "error": r.error} for r in results if not r.ok],
            "no_sink_configured": not results,
        }

    # ------------------------------------------------------------------ evaluation

    def candidates(self, snap: dict[str, Any], *, now: datetime) -> tuple[list[Candidate], bool]:
        """The snapshot's metrics, plus the silence check when a schedule is declared."""
        cands = [
            Candidate(
                a["metric"], a["status"], a["value"], a["threshold"], a["meaning"], a["action"]
            )
            for a in snap["alerts"]
        ]
        stale = False
        interval = self.settings.monitor_interval_minutes
        if interval > 0:
            prev = parse_ts(get_meta(self.db, "last_evaluation"))
            gap_min = (now - prev).total_seconds() / 60 if prev else 0.0
            limit = float(STALE_FACTOR * interval)
            stale = prev is not None and gap_min > limit
            cands.append(
                Candidate(
                    STALE_KEY,
                    "alert" if stale else "ok",
                    round(gap_min, 1),
                    limit,
                    f"Minutes since the monitor last ran, against {STALE_FACTOR} intervals. "
                    "The monitor itself was silent, so alerts may have been missed meanwhile.",
                    "Check that the API process (or the bank's scheduler running monitor_once.py) is "
                    "running; read GET /monitoring now, because nothing watched it in the gap.",
                )
            )
        return cands, stale

    def evaluate(self, *, source: str = "scheduler") -> dict[str, Any]:
        """Snapshot, state update, delivery. Stamps ``last_evaluation`` once the state is updated (a failure
        before that leaves the stamp old, which is what the silence check looks for)."""
        with _EVAL_LOCK:
            now = self.clock()
            snap = snapshot(self.db, self.settings, now=now)
            cands, stale = self.candidates(snap, now=now)
            opened: list[str] = []
            resolved: list[str] = []
            for cand in cands:
                change = self._apply(cand, now)
                if change == "opened":
                    opened.append(cand.key)
                    self._audit_change("monitoring.alert_opened", cand)
                elif change == "resolved":
                    resolved.append(cand.key)
                    self._audit_change("monitoring.alert_resolved", cand)
            set_meta(self.db, "last_evaluation", ts(now), now)
            set_meta(self.db, "last_evaluation_source", source, now)
            set_meta(self.db, "last_error", "", now)
            notified = [self._announce(r, now) for r in self._due(now)] if self.sinks else []
            return {
                "evaluated_at": ts(now),
                "source": source,
                "cases_in_window": snap["cases_in_window"],
                "enough_data": snap["enough_data"],
                "alerting": [c.key for c in cands if c.status == "alert"],
                "opened": opened,
                "resolved": resolved,
                "monitor_stale": stale,
                "sinks": [s.name for s in self.sinks],
                "notified": notified,
                "delivery_failed": any(n["failed"] for n in notified),
            }

    def _audit_change(self, action: str, cand: Candidate) -> None:
        self.audit.write(
            actor=SYSTEM_ACTOR,
            action=action,
            case_id=None,
            detail={"key": cand.key, "value": cand.value, "threshold": cand.threshold},
        )

    # ------------------------------------------------------------------ people

    def acknowledge(self, key: str, *, who: str, reason: str) -> dict[str, Any]:
        """Mark an open alert as seen by ``who`` for ``reason``; written to the append-only audit log."""
        now = self.clock()
        with _STATE_LOCK, self.db.conn() as c:
            row = c.execute("SELECT * FROM alert_state WHERE key = ?", (key,)).fetchone()
            if row is None:
                raise ApiError(
                    404, "not_found", "No alert with this key.", {"kind": "alert", "key": key}
                )
            if row["status"] != "open":
                raise ApiError(
                    409,
                    "alert_not_open",
                    f"This alert is {row['status']}; only an open alert can be acknowledged.",
                    {"status": row["status"]},
                )
            c.execute(
                "UPDATE alert_state SET status='acknowledged', acknowledged_by=?, acknowledged_at=?, "
                "note=? WHERE key=?",
                (who, ts(now), scrub(reason, max_len=300), key),
            )
            self.audit.write(
                actor=who,
                action="monitoring.alert_acknowledged",
                case_id=None,
                detail={
                    "key": key,
                    "reason": scrub(reason, max_len=300),
                    "reason_sha256": sha256_text(reason),
                },
                conn=c,
            )
            row = c.execute("SELECT * FROM alert_state WHERE key = ?", (key,)).fetchone()
        return _state_dict(row)

    def send_test(self, *, who: str) -> dict[str, Any]:
        """A clearly labelled test through every configured sink; the per-sink result and time are kept, because
        readiness (c) needs a recent delivered test."""
        now = self.clock()
        last = get_json_meta(self.db, "last_test")
        prev = parse_ts(last.get("at")) if last else None
        if prev and (now - prev).total_seconds() < TEST_MIN_GAP_S:
            raise ApiError(
                429,
                "rate_limited",
                f"A test alert was sent {int((now - prev).total_seconds())} seconds ago; wait a moment.",
                {"retry_after_s": TEST_MIN_GAP_S},
            )
        sinks = self.sinks
        msg = build_message("test", "test", self.settings, now=now)
        results = self.deliver(msg)
        record = {
            "at": ts(now),
            "by": who,
            "results": [r.as_dict() for r in results],
            "delivered_to": [r.sink for r in results if r.ok],
            "fingerprints": {s.name: s.fingerprint() for s in sinks},
        }
        set_meta(self.db, "last_test", json.dumps(record, sort_keys=True), now)
        self.audit.write(
            actor=who,
            action="monitoring.test_alert",
            case_id=None,
            detail={
                "sinks": [s.name for s in sinks],
                "delivered_to": record["delivered_to"],
                "failed": [{"sink": r.sink, "error": r.error} for r in results if not r.ok],
            },
        )
        return {
            "sent_at": ts(now),
            "sinks_configured": [s.name for s in sinks],
            "results": record["results"],
            "delivered": bool(record["delivered_to"]),
            "message": None
            if sinks
            else "No sink is configured (GLDB_ALERT_WEBHOOK_URL, GLDB_ALERT_SMTP_HOST, "
            "GLDB_ALERT_TELEGRAM_BOT_TOKEN or GLDB_ALERT_FILE_PATH), so nothing was sent.",
        }

    def notify_incident(
        self, *, incident_id: int, severity: str, reported_by: str
    ) -> list[DeliveryResult]:
        """Medium and high incidents are announced; the summary text stays in the audit log, not on the channel."""
        if severity not in ("medium", "high") or not self.sinks:
            return []
        msg = build_message(
            "incident",
            f"incident:{incident_id}",
            self.settings,
            now=self.clock(),
            extra={"incident_id": incident_id, "severity": severity, "reported_by": reported_by},
        )
        return self.deliver(msg)


# --------------------------------------------------------------------------------------
# Scheduler and silence
# --------------------------------------------------------------------------------------


def scheduler_mode(settings: Settings) -> str:
    if settings.monitor_interval_minutes <= 0:
        return "off"
    return "in_process" if settings.monitor_in_process else "external"


def scheduler_status(db: Database, settings: Settings, *, now: datetime) -> dict[str, Any]:
    """What GET /monitoring reports about the monitor itself. ``monitor_stale`` is true when a schedule is on
    and the last evaluation (or, before the first, the moment the schedule was switched on) is more than two
    intervals old. Off is reported plainly, never as healthy."""
    mode = scheduler_mode(settings)
    last = get_meta(db, "last_evaluation")
    source = get_meta(db, "last_evaluation_source")
    base: dict[str, Any] = {
        "scheduler": mode,
        "interval_minutes": settings.monitor_interval_minutes or None,
        "last_evaluated_at": last,
        "last_evaluated_by": source,
        "monitor_stale": False,
    }
    if mode == "off":
        base["scheduler_note"] = (
            "Off: nothing evaluates the thresholds or sends alerts by itself. Set "
            "GLDB_MONITOR_INTERVAL_MINUTES, or run backend/scripts/monitor_once.py from the bank's scheduler."
        )
        return base
    ref = parse_ts(last) or parse_ts(set_meta_if_missing(db, "enabled_since", ts(now), now))
    limit = timedelta(minutes=STALE_FACTOR * settings.monitor_interval_minutes)
    if ref is not None and now - ref > limit:
        base["monitor_stale"] = True
        base["scheduler_note"] = (
            f"Stale: no evaluation for more than {STALE_FACTOR} intervals "
            f"(since {ts(ref)}). Alerts may have been missed."
        )
    else:
        base["scheduler_note"] = "On: evaluations are running on schedule."
    return base


def start_monitor_scheduler(
    db: Database,
    settings: Settings,
    *,
    now: Callable[[], datetime],
    wait: Callable[[float], bool] | None = None,
    sinks: Callable[[], Sequence[Sink] | None] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> threading.Event:
    """Evaluate every ``monitor_interval_minutes`` on a daemon thread (retention's pattern), first at once so
    the silence stamp exists, then after each interval. A failed run is logged (class only) and the loop
    carries on. ``wait`` (seconds -> stop requested) exists so tests do not sleep."""
    stop = threading.Event()
    interval = max(1, int(settings.monitor_interval_minutes)) * 60.0
    waiter = wait or stop.wait
    set_meta(db, "enabled_since", ts(now()), now())

    def loop() -> None:
        while True:
            try:
                service = AlertService(
                    db, settings, clock=now, sinks=sinks() if sinks else None, sleep=sleep
                )
                result = service.evaluate(source="scheduler")
                log.info(
                    "scheduled monitor run: %d alerting, %d opened, %d resolved, sinks=%s",
                    len(result["alerting"]),
                    len(result["opened"]),
                    len(result["resolved"]),
                    ",".join(result["sinks"]) or "none",
                )
            except Exception as exc:  # noqa: BLE001 - the schedule must outlive one bad run
                log.error("scheduled monitor run failed: %s", type(exc).__name__)
                with contextlib.suppress(Exception):
                    set_meta(db, "last_error", f"{type(exc).__name__} at {ts(now())}", now())
            if waiter(interval):
                return

    threading.Thread(target=loop, name="gldb-monitor", daemon=True).start()
    return stop
