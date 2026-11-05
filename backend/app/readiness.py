"""Readiness for MAS AI Risk Management Guidelines 5.23 (a) to (c) (docs/ai-incidents.md).

Three things must exist before monitoring means anything, and the software can only check that they are
recorded, not that they are right:

* (a) a named accountable person (``GLDB_AI_OWNER``);
* (b) thresholds the bank has approved: an approval is an audit-log row holding a hash of the threshold values
  at that moment. It counts only while the current thresholds hash to the same value;
* (c) alerts that reach a place someone watches: at least one sink is configured and a test alert reached it
  recently. The in-app sink (the Notifications page) counts as a sink, and the text says plainly that
  delivery to a page is not the same as someone watching it.

Nothing here decides anything. The names, the approval and the choice of where alerts land are the bank's.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from app.alerts import Sink, get_json_meta, parse_ts
from app.audit import AuditLog, scrub
from app.core.config import Settings
from app.core.db import Database, ts
from app.notifications import IN_APP

APPROVAL_ACTION = "monitoring.thresholds_approved"
THRESHOLD_FIELDS = (
    "monitor_window_days",
    "monitor_min_cases",
    "monitor_partial_rate_max",
    "monitor_check_error_rate_max",
    "monitor_agent_failure_rate_max",
    "monitor_daily_spend_ratio_max",
    "monitor_rule_drift_max",
)
STATEMENT = (
    "These three items are decisions for the bank to make: who is accountable, which thresholds are right, and "
    "where alerts are watched. The software only records them and checks that they are recorded and still "
    "current; it cannot judge whether the person, the thresholds or the place are the right ones."
)


def threshold_values(settings: Settings) -> dict[str, float | int]:
    return {name: getattr(settings, name) for name in THRESHOLD_FIELDS}


def thresholds_hash(settings: Settings) -> str:
    """sha256 of the canonical JSON of the threshold values (floats as Python prints them, so 0.30 and 0.3 agree)."""
    canonical = json.dumps(threshold_values(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def latest_approval(db: Database) -> dict[str, Any] | None:
    with db.conn() as c:
        row = c.execute(
            "SELECT id, ts, actor, detail FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT 1",
            (APPROVAL_ACTION,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "approved_at": row["ts"],
        "recorded_by": row["actor"],
        **json.loads(row["detail"]),
    }


def approve_thresholds(
    db: Database,
    audit: AuditLog,
    settings: Settings,
    *,
    actor: str,
    approver: str,
    role: str,
    note: str,
    approver_name: str | None = None,
) -> dict[str, Any]:
    """Record that ``approver`` approved the thresholds as they are now. The audit row is append-only.
    ``approver_name`` is the name typed into the form when ``approver`` is a signed-in account (a shared
    login, where the account says little about who approved); it is kept next to the account, never in
    its place."""
    digest = thresholds_hash(settings)
    typed = scrub(approver_name, max_len=80) if approver_name else ""
    name = {"approver_name": typed} if typed and typed != scrub(approver, max_len=80) else {}
    row_id = audit.write(
        actor=actor,
        action=APPROVAL_ACTION,
        case_id=None,
        detail={
            "thresholds_sha256": digest,
            "thresholds": threshold_values(settings),
            "approver": scrub(approver, max_len=80),
            **name,
            "role": scrub(role, max_len=80),
            "note": scrub(note, max_len=300),
        },
    )
    return {
        "id": row_id,
        "recorded_by": actor,
        "approver": scrub(approver, max_len=80),
        **name,
        "role": scrub(role, max_len=80),
        "thresholds_sha256": digest,
        "thresholds": threshold_values(settings),
    }


def _item(
    item_id: str, name: str, met: bool, detail: str, missing: list[str], **extra: Any
) -> dict[str, Any]:
    return {
        "id": item_id,
        "name": name,
        "status": "met" if met else "not_met",
        "detail": detail,
        "missing": [] if met else missing,
        **extra,
    }


def _owner_item(settings: Settings) -> dict[str, Any]:
    owner = settings.ai_owner.strip()
    if owner:
        return _item(
            "5.23(a)",
            "Accountable person named",
            True,
            "GLDB_AI_OWNER is set. The software cannot tell whether this is the right person; "
            "the bank confirms that.",
            [],
            value=owner,
        )
    return _item(
        "5.23(a)",
        "Accountable person named",
        False,
        "GLDB_AI_OWNER is empty. The bank must name the person (name and role) accountable for this AI system.",
        ["Set GLDB_AI_OWNER to the accountable person's name and role."],
    )


def _thresholds_item(db: Database, settings: Settings) -> dict[str, Any]:
    current = thresholds_hash(settings)
    approval = latest_approval(db)
    common = {"thresholds": threshold_values(settings), "thresholds_sha256": current}
    if approval is None:
        return _item(
            "5.23(b)",
            "Thresholds approved",
            False,
            "No approval is recorded. The thresholds are starting values, not calibrated on real cases.",
            [
                "The bank agrees the thresholds, then an approver records it: POST /monitoring/thresholds/approve."
            ],
            **common,
        )
    who = {
        "approved_by": approval.get("approver"),
        "approver_role": approval.get("role"),
        "approved_at": approval["approved_at"],
    }
    if approval.get("thresholds_sha256") != current:
        return _item(
            "5.23(b)",
            "Thresholds approved",
            False,
            "Thresholds changed since approval: the approval on record was for different values.",
            [
                "Review the new thresholds and approve them again: POST /monitoring/thresholds/approve."
            ],
            **common,
            **who,
        )
    return _item(
        "5.23(b)",
        "Thresholds approved",
        True,
        "The approval on record matches the current thresholds. The software cannot judge whether the "
        "thresholds are the right ones.",
        [],
        **common,
        **who,
    )


def _alerts_item(
    db: Database, settings: Settings, sinks: Sequence[Sink], now: datetime
) -> dict[str, Any]:
    name = "Alerts reach a watched place"
    names = [s.name for s in sinks]
    max_age = settings.alert_test_max_age_days
    base = {"sinks_configured": names, "test_max_age_days": max_age}
    if not sinks:
        return _item(
            "5.23(c)",
            name,
            False,
            "No alert sink is configured, so nothing reaches anyone.",
            ["Configure at least one of GLDB_ALERT_WEBHOOK_URL, GLDB_ALERT_SMTP_HOST, "
             "GLDB_ALERT_TELEGRAM_BOT_TOKEN, GLDB_ALERT_FILE_PATH, then send a test: POST /monitoring/alerts/test."],
            **base,
        )  # fmt: skip
    test = get_json_meta(db, "last_test")
    if test is None:
        return _item(
            "5.23(c)", name, False, "No test alert has been sent yet.",
            ["Send a test alert and confirm someone receives it: POST /monitoring/alerts/test."],
            last_test_at=None, **base,
        )  # fmt: skip
    at = parse_ts(test.get("at"))
    base["last_test_at"] = test.get("at")
    base["last_test_results"] = test.get("results", [])
    fingerprints = test.get("fingerprints", {})
    # a sink counts only if it delivered in the test AND is configured the same way now
    working = [
        s.name
        for s in sinks
        if s.name in test.get("delivered_to", []) and fingerprints.get(s.name) == s.fingerprint()
    ]
    if not working:
        changed = [s.name for s in sinks if s.name in test.get("delivered_to", [])]
        detail = (
            "The sink configuration changed since the last delivered test."
            if changed
            else "The last test alert was not delivered by any configured sink."
        )
        return _item(
            "5.23(c)", name, False, detail,
            ["Fix the sink and send a new test: POST /monitoring/alerts/test."],
            **base,
        )  # fmt: skip
    if at is None or now - at > timedelta(days=max_age):
        return _item(
            "5.23(c)", name, False,
            f"The last delivered test alert is older than {max_age} days.",
            ["Send a fresh test alert: POST /monitoring/alerts/test."],
            **base,
        )  # fmt: skip
    detail = f"A test alert was delivered by {', '.join(working)} within {max_age} days."
    if working == ["file"]:
        detail += " Only the local file received it; the bank must confirm that someone or something watches that file."
    if IN_APP in working:
        detail += (
            " In-app means the test appeared on the Notifications page. That is not the same as "
            "someone watching the page: the bank decides who looks at it, how often and what they do next."
        )
    return _item(
        "5.23(c)", name, True, detail, [], delivered_by=working,
        page_only=working == [IN_APP], watching_confirmed=False, **base,
    )  # fmt: skip


def readiness(
    db: Database, settings: Settings, sinks: Sequence[Sink], *, now: datetime
) -> dict[str, Any]:
    items = [
        _owner_item(settings),
        _thresholds_item(db, settings),
        _alerts_item(db, settings, sinks, now),
    ]
    return {
        "as_of": ts(now),
        "ready": all(i["status"] == "met" for i in items),
        "statement": STATEMENT,
        "items": items,
    }


def readiness_summary(full: dict[str, Any]) -> dict[str, Any]:
    return {
        "ready": full["ready"],
        "accountable_person": full["items"][0]["status"],
        "thresholds_approved": full["items"][1]["status"],
        "alerts_watched": full["items"][2]["status"],
        "detail": "GET /monitoring/readiness",
    }
