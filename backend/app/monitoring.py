"""Monitoring against thresholds, and the AI incident log (docs/ai-incidents.md).

``snapshot`` looks at the cases updated in the last ``monitor_window_days`` and compares them with
fixed thresholds from the settings, and with the window before for each rule's firing rate. It says
"not enough data" instead of a verdict when a window has fewer than ``monitor_min_cases`` cases, and
every alert names the number, the threshold and what to do. It reads, it never changes anything.
"""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.costs import DailySpend, PriceTable
from app.core.db import Database, ts

CASE_LIMIT = 500  # newest cases per window; enough for a demo, bounded for a real database
INCIDENT_ACTION = "ai.incident_reported"


def disk_encryption() -> dict[str, str]:
    """Whether the volume holding the data is encrypted. Only macOS FileVault can be asked here; anywhere
    else the answer is "unknown", which the bank's own platform control must cover."""
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(
                ["fdesetup", "status"], capture_output=True, text=True, timeout=3, check=False
            ).stdout.strip()
            return {"status": "on" if "is On" in out else "off", "detail": out[:80] or "no answer"}
        except (OSError, subprocess.SubprocessError):
            pass
    return {"status": "unknown", "detail": "not checkable from the application"}


def _bodies(db: Database, start: datetime, end: datetime) -> list[dict[str, Any]]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT body FROM cases WHERE updated_at >= ? AND updated_at < ? AND status != 'processing' "
            "ORDER BY updated_at DESC LIMIT ?",
            (ts(start), ts(end), CASE_LIMIT),
        ).fetchall()
    return [json.loads(r["body"]) for r in rows]


def _rule_rates(cases: list[dict[str, Any]]) -> dict[str, float]:
    if not cases:
        return {}
    fired: dict[str, int] = {}
    for c in cases:
        for rule in {
            f["rule"] for f in c.get("flags", []) if f.get("severity") in ("medium", "high")
        }:
            fired[rule] = fired.get(rule, 0) + 1
    return {r: n / len(cases) for r, n in fired.items()}


def _warnings(case: dict[str, Any]) -> list[str]:
    return [w for st in case.get("stages", []) for w in st.get("warnings", [])]


def _alert(metric: str, value: float | None, limit: float, why: str, action: str) -> dict[str, Any]:
    status = "unknown" if value is None else ("alert" if value > limit else "ok")
    return {
        "metric": metric,
        "value": None if value is None else round(value, 4),
        "threshold": limit,
        "status": status,
        "meaning": why,
        "action": action,
    }


def snapshot(db: Database, settings: Settings, *, now: datetime) -> dict[str, Any]:
    window = timedelta(days=settings.monitor_window_days)
    cur = _bodies(db, now - window, now + timedelta(seconds=1))
    prev = _bodies(db, now - 2 * window, now - window)
    n = len(cur)
    enough = n >= settings.monitor_min_cases

    partial = sum(1 for c in cur if c.get("partial") or (c.get("score") or {}).get("partial"))
    check_errors = sum(1 for c in cur if any(w.startswith("check_error:") for w in _warnings(c)))
    with_team = [c for c in cur if c.get("agents")]
    team_failed = sum(1 for c in with_team if any(a.get("status") == "failed" for a in c["agents"]))
    budget = sum(
        1 for c in cur if any(r.startswith("budget_exceeded") for r in c.get("partial_reasons", []))
    )

    drift: dict[str, float] = {}
    if enough and len(prev) >= settings.monitor_min_cases:
        a, b = _rule_rates(cur), _rule_rates(prev)
        drift = {r: round(a.get(r, 0.0) - b.get(r, 0.0), 4) for r in set(a) | set(b)}
    worst_rule = max(drift, key=lambda r: abs(drift[r]), default=None)

    spend: dict[str, Any] = {"today_usd": None, "cap_usd": None, "ratio": None}
    daily = DailySpend.from_settings(
        db, settings, PriceTable.load(settings.prices_path), now=lambda: now
    )
    if daily is not None:
        today = float(daily.spent())
        spend = {
            "today_usd": round(today, 4),
            "cap_usd": float(daily.cap),
            "ratio": round(today / float(daily.cap), 4) if daily.cap else None,
        }

    def rate(k: int, d: int) -> float | None:
        return k / d if enough and d else None

    alerts = [
        _alert(
            "partial_rate",
            rate(partial, n),
            settings.monitor_partial_rate_max,
            "Share of cases where a stage failed, a page could not be read or the budget ran out.",
            "Open the partial cases; check the model service and the budget settings.",
        ),
        _alert(
            "check_error_rate",
            rate(check_errors, n),
            settings.monitor_check_error_rate_max,
            "Share of cases where a check raised an error instead of running.",
            "Read the check_error warnings on those cases and raise an incident.",
        ),
        _alert(
            "review_team_failure_rate",
            rate(team_failed, len(with_team)),
            settings.monitor_agent_failure_rate_max,
            "Share of cases with a review-team member that could not run.",
            "Check the model service; the flags stood, so no case was cleared by the failure.",
        ),
        _alert(
            "daily_spend_ratio",
            spend["ratio"],
            settings.monitor_daily_spend_ratio_max,
            "Today's model spend against the daily cap.",
            "Expect refusals soon; raise the cap on purpose or wait for the next UTC day.",
        ),
        _alert(
            "rule_drift",
            abs(drift[worst_rule]) if worst_rule else None,
            settings.monitor_rule_drift_max,
            f"Largest change in how often one rule fires, this window against the last (rule: {worst_rule or 'none'}).",
            "A sudden swing means a new kind of document, a changed reader or a changed rule; compare a few cases by hand.",
        ),
    ]
    owner = settings.ai_owner.strip()
    return {
        "as_of": ts(now),
        "window_days": settings.monitor_window_days,
        "cases_in_window": n,
        "cases_in_previous_window": len(prev),
        "enough_data": enough,
        "budget_exceeded_cases": budget,
        "spend": spend,
        "alerts": alerts,
        "alerting": [a["metric"] for a in alerts if a["status"] == "alert"],
        "accountable_owner": owner or None,
        "owner_named": bool(owner),
        "disk_encryption": disk_encryption(),
    }


def incidents(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, ts, actor, detail FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT ?",
            (INCIDENT_ACTION, limit),
        ).fetchall()
    parsed = [(r, json.loads(r["detail"])) for r in rows]
    closed = {d["resolves"] for _, d in parsed if d.get("resolves")}
    out = []
    for r, d in parsed:
        row = {"id": r["id"], "reported_at": r["ts"], "reported_by": r["actor"], **d}
        if r["id"] in closed:
            row["status"] = "resolved"
        out.append(row)
    return out
