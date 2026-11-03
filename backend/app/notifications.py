"""The in-app notification feed: what the Notifications page lists (docs/ai-incidents.md).

``InAppSink`` is a sink like the webhook or the file, with one difference: it is always there. Every message
the alert service announces (an alert raised, a reminder, a resolution, a medium or high incident, a test, the
monitor's own silence) is also written to the ``notifications`` table, so the page shows it even when no
outside sink is configured.

Delivery to the page is not the same as someone watching the page. Nothing here knows whether a person looked;
"read" only records that someone pressed the button, under their name, in the audit log.

What is stored is limited on purpose: the metric name, the number, the threshold, the meaning and action text
the code itself wrote, an incident id and severity. Never a case's content, an applicant, a file name or the
free text an analyst typed (an incident's summary stays in the audit log).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.core.db import Database, ts

if TYPE_CHECKING:
    from app.alerts import AlertMessage

IN_APP = "in-app"
STALE_KEY = "monitor_stale"  # the dead-man alert; kept equal to app.alerts.STALE_KEY (tested)
MAX_ROWS = 1000  # the newest notifications kept; the audit log and alert state keep the history
CATEGORIES = ("alerts", "incidents", "system")
ALERT_KINDS = ("alert", "reminder")
DATA_KEYS = ("metric", "value", "threshold", "incident_id", "severity", "reported_by")
ACTION_READ = "monitoring.notifications_read"
AUDIT_IDS_MAX = 50


def category_of(kind: str, key: str) -> str:
    if kind == "incident":
        return "incidents"
    if kind == "test" or key == STALE_KEY:
        return "system"
    return "alerts"


def _severity(msg: AlertMessage) -> str:
    if msg.kind == "incident":
        return "high" if msg.data.get("severity") == "high" else "attention"
    return "attention" if msg.kind in ALERT_KINDS else "info"


def _title(msg: AlertMessage) -> str:
    if msg.kind == "alert":
        return f"Alert raised: {msg.key}"
    if msg.kind == "reminder":
        return f"Still open: {msg.key}"
    if msg.kind == "resolved":
        return f"Back within the limit: {msg.key}"
    if msg.kind == "incident":
        return f"AI incident #{msg.data.get('incident_id', '?')} reported ({msg.data.get('severity', '?')})"
    return "Test alert (not a real alert)"


def _text(msg: AlertMessage) -> str:
    """The message as a person reads it on the page: no endpoint names, no instructions for a client."""
    if msg.kind == "incident":
        return (
            f"An AI incident of severity {msg.data.get('severity')} was reported by "
            f"{msg.data.get('reported_by')}. Its summary is in the incident list on this page."
        )
    if msg.kind == "test":
        return "Sent to check that alerts reach this page. No action is needed."
    keep = [
        line
        for line in msg.text.split("\n")[1:]  # the first line is the product name and the title
        if not line.startswith(("Read:", "Acknowledge, with a reason"))
    ]
    # the time is the notification's own; the message's raw "(as of 2026-10-27T10:00:00.000000Z)" is noise here
    return re.sub(r" \(as of [^)]*\)", "", "\n".join(keep))


class InAppSink:
    """Writes each announcement to the ``notifications`` table. Idempotent per ``msg.event``."""

    name = IN_APP

    def __init__(self, db: Database):
        self._db = db

    def fingerprint(self) -> str:
        # Nothing to configure, so nothing that can change underneath a recorded test.
        return hashlib.sha256(b"in-app").hexdigest()[:16]

    def send(self, msg: AlertMessage) -> None:
        data = (
            {}
            if msg.kind == "test"
            else {k: msg.data[k] for k in DATA_KEYS if msg.data.get(k) is not None}
        )
        with self._db.conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO notifications "
                "(ts, kind, category, key, event, severity, title, text, data) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    msg.sent_at,
                    msg.kind,
                    category_of(msg.kind, msg.key),
                    msg.key,
                    msg.event or None,
                    _severity(msg),
                    _title(msg),
                    _text(msg),
                    json.dumps(data, sort_keys=True),
                ),
            )
            c.execute(
                "DELETE FROM notifications WHERE id <= (SELECT MAX(id) FROM notifications) - ?",
                (MAX_ROWS,),
            )


# --------------------------------------------------------------------------------------
# Reading and marking
# --------------------------------------------------------------------------------------


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "ts": r["ts"],
        "kind": r["kind"],
        "category": r["category"],
        "key": r["key"],
        "severity": r["severity"],
        "title": r["title"],
        "text": r["text"],
        "data": json.loads(r["data"] or "{}"),
        "read": r["read_at"] is not None,
        "read_at": r["read_at"],
        "read_by": r["read_by"],
    }


def unread_count(db: Database) -> int:
    with db.conn() as c:
        return int(
            c.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]
        )


def list_notifications(
    db: Database,
    *,
    limit: int = 50,
    before: int | None = None,
    since: datetime | None = None,
    category: str | None = None,
    unread_only: bool = False,
) -> tuple[list[dict[str, Any]], int | None]:
    """Newest first. ``before`` is the id of the last row of the previous page; ``since`` keeps rows newer than
    that time. Returns the rows and the ``before`` for the next page (None on the last page)."""
    where, params = ["1=1"], []  # type: list[str], list[Any]
    if before is not None:
        where.append("id < ?")
        params.append(before)
    if since is not None:
        where.append("ts > ?")
        params.append(ts(since))
    if category is not None:
        where.append("category = ?")
        params.append(category)
    if unread_only:
        where.append("read_at IS NULL")
    with db.conn() as c:
        rows = c.execute(
            f"SELECT * FROM notifications WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",  # noqa: S608
            (*params, limit + 1),
        ).fetchall()
    page = rows[:limit]
    more = len(rows) > limit
    return [_row(r) for r in page], (page[-1]["id"] if more and page else None)


def summary(db: Database) -> dict[str, Any]:
    """What the navigation badge needs, cheaply (no monitoring snapshot).

    ``badge`` counts each open alert once: an unread "alert raised" notification for an alert that is still open
    is the same event, so it is not added on top of the alert itself."""
    with db.conn() as c:
        unread = int(
            c.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]
        )
        open_alerts = int(
            c.execute("SELECT COUNT(*) FROM alert_state WHERE status = 'open'").fetchone()[0]
        )
        shadow = int(
            c.execute(
                "SELECT COUNT(*) FROM notifications n WHERE n.read_at IS NULL AND n.kind IN ('alert','reminder') "
                "AND EXISTS (SELECT 1 FROM alert_state a WHERE a.key = n.key AND a.status = 'open')"
            ).fetchone()[0]
        )
    return {"unread": unread, "open_alerts": open_alerts, "badge": open_alerts + unread - shadow}


def mark_alert_read(c: sqlite3.Connection, key: str, *, who: str, at: str) -> None:
    """An alert that someone acknowledged has been seen: its own notifications are read too."""
    c.execute(
        "UPDATE notifications SET read_at = ?, read_by = ? "
        "WHERE key = ? AND kind IN ('alert','reminder') AND read_at IS NULL",
        (at, who, key),
    )


def mark_incident_read(db: Database, incident_id: int, *, who: str, at: datetime) -> None:
    with db.conn() as c:
        c.execute(
            "UPDATE notifications SET read_at = ?, read_by = ? "
            "WHERE key = ? AND kind = 'incident' AND read_at IS NULL",
            (ts(at), who, f"incident:{incident_id}"),
        )


def mark_read(
    db: Database, *, ids: list[int] | None, who: str, at: datetime
) -> tuple[list[int], int]:
    """Mark these notifications (or every unread one when ``ids`` is None) as read by ``who``.
    Returns the ids that changed and the unread count left. Unknown or already-read ids change nothing."""
    stamp = ts(at)
    with db.conn() as c:
        if ids is None:
            rows = c.execute("SELECT id FROM notifications WHERE read_at IS NULL").fetchall()
        else:
            marks = ",".join("?" for _ in ids)
            rows = c.execute(
                f"SELECT id FROM notifications WHERE read_at IS NULL AND id IN ({marks})",  # noqa: S608
                ids,
            ).fetchall()
        changed = [int(r["id"]) for r in rows]
        if changed and ids is None:
            c.execute(
                "UPDATE notifications SET read_at = ?, read_by = ? WHERE read_at IS NULL",
                (stamp, who),
            )
        elif changed:
            marks = ",".join("?" for _ in changed)
            c.execute(
                f"UPDATE notifications SET read_at = ?, read_by = ? WHERE id IN ({marks})",  # noqa: S608
                (stamp, who, *changed),
            )
        left = int(
            c.execute("SELECT COUNT(*) FROM notifications WHERE read_at IS NULL").fetchone()[0]
        )
    return changed, left
