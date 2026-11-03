"""Monitoring against thresholds, and the AI incident log (docs/ai-incidents.md).

``snapshot`` looks at the cases updated in the last ``monitor_window_days`` and compares them with
fixed thresholds from the settings, and with the window before for each rule's firing rate. It says
"not enough data" instead of a verdict when a window has fewer than ``monitor_min_cases`` cases, and
every alert names the number, the threshold and what to do. It reads, it never changes anything; telling
someone is ``app/alerts.py``.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import datetime, timedelta
from typing import Any

from app import score_model
from app.core.config import Settings
from app.core.costs import DailySpend, PriceTable
from app.core.db import Database, ts

CASE_LIMIT = 500  # newest cases per window; enough for a demo, bounded for a real database
INCIDENT_ACTION = "ai.incident_reported"
INCIDENT_ACK_ACTION = (
    "ai.incident_acknowledged"  # an acknowledgement or assignment note; never changes open/resolved
)


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


ENCRYPTION_SAMPLE = 40  # items per category: half the oldest, half the newest (bounded for speed)
# What stays readable on purpose (docs/encryption-at-rest.md section 3): listing, sorting and search need it.
PLAINTEXT_BY_DESIGN = [
    "cases.applicant_name and cases.reference (queue display and substring search)",
    "ids, status, score, band, lane, dates and counts (queue columns)",
    "fingerprint hashes and the applicant UEN beside them",
    "audit_log (already pseudonymised: ids, hashes, enums, counts)",
    "file names on disk are the sha256 of the plaintext",
]
_DB_CATEGORIES = (  # (name, table, column)
    ("cases", "cases", "body"),
    ("top_finding", "cases", "top_finding"),
    ("case_versions", "case_versions", "body"),
    ("review_entries", "review_entries", "body"),
    ("llm_cache", "llm_cache", "value"),
)


def _tally(
    vault: Any, headers: list[str | bytes], counts: dict[str, int], *, text: bool
) -> dict[str, Any]:
    enc = plain = old = unknown = 0
    active = vault.active_key_id
    known = set(vault.key_ids())
    for h in headers:
        is_enc = vault.is_encrypted_text(h) if text else vault.is_encrypted(h)  # type: ignore[arg-type]
        if not is_enc:
            plain += 1
            continue
        enc += 1
        kid = vault.key_id_of_text(h) if text else vault.key_id_of(h)  # type: ignore[arg-type]
        if kid not in known:
            unknown += 1
        elif kid != active:
            old += 1
    return {
        "sampled": len(headers),
        "total": counts.get("total"),
        "encrypted": enc,
        "plaintext": plain,
        "under_old_key": old,
        "key_not_loaded": unknown,
    }


def _walk_headers(root: Any, limit: int) -> list[bytes]:
    """First 48 bytes (the header) of up to ``limit`` files two levels below ``root`` (a bounded walk)."""
    out: list[bytes] = []
    if not root.is_dir():
        return out
    try:
        for shard in os.scandir(root):
            if not shard.is_dir():
                continue
            for entry in os.scandir(shard.path):
                if entry.name.startswith(".") or not entry.is_file():
                    continue
                with open(entry.path, "rb") as fh:
                    out.append(fh.read(48))
                if len(out) >= limit:
                    return out
    except OSError:
        pass
    return out


def encryption_status(
    db: Database, settings: Settings, *, sample: int = ENCRYPTION_SAMPLE
) -> dict[str, Any]:
    """Application-level encryption at rest: mode, active key id and whether the sampled stored
    items are encrypted. Reads headers only (no decryption, no content), a bounded number of items."""
    vault = db.vault
    mode = "on" if vault.enabled else "off"
    out: dict[str, Any] = {
        "mode": mode,
        "algorithm": "AES-256-GCM",
        "active_key_id": vault.active_key_id,
        "key_ids": vault.key_ids(),
        "plaintext_by_design": PLAINTEXT_BY_DESIGN,
    }
    try:
        half = max(1, sample // 2)
        categories: dict[str, Any] = {}
        files = _walk_headers(db.files.files_dir, sample)
        categories["files"] = _tally(vault, list(files), {}, text=False)  # type: ignore[arg-type]
        renders = _walk_headers(db.files.renders_dir, sample)
        categories["renders"] = _tally(vault, list(renders), {}, text=False)  # type: ignore[arg-type]
        with db.conn() as c:
            for name, table, column in _DB_CATEGORIES:
                sql = f"SELECT substr({column},1,80) AS h FROM {table}"
                if column == "top_finding":
                    sql += " WHERE top_finding IS NOT NULL"
                total = c.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL"
                ).fetchone()[0]
                rows = c.execute(sql + " ORDER BY rowid ASC LIMIT ?", (half,)).fetchall()
                if total > half:
                    rows += c.execute(sql + " ORDER BY rowid DESC LIMIT ?", (half,)).fetchall()
                headers = [r["h"] for r in rows][: sample if total > half else half]
                categories[name] = _tally(vault, headers, {"total": total}, text=True)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - monitoring must answer even when sampling fails
        out.update(
            status="error",
            all_sampled_encrypted=None,
            message=f"Encryption status could not be sampled ({type(exc).__name__}).",
        )
        return out
    sampled = sum(c["sampled"] for c in categories.values())
    plain = sum(c["plaintext"] for c in categories.values())
    unknown = sum(c["key_not_loaded"] for c in categories.values())
    old = sum(c["under_old_key"] for c in categories.values())
    out.update(
        categories=categories,
        sampled_items=sampled,
        plaintext_items=plain,
        all_sampled_encrypted=(plain == 0) if sampled else None,
    )
    if mode == "off":
        out["status"] = "off"
        out["message"] = (
            "Application-level encryption is OFF: uploaded files, page renders, case data, review "
            "entries and the response cache are stored as plain data and rely on the volume's "
            "encryption (see disk_encryption)."
        )
        if sampled - plain:
            out["message"] += (
                f" {sampled - plain} sampled item(s) are encrypted and still readable."
            )
    elif unknown:
        out["status"] = "key_missing"
        out["message"] = (
            f"Encryption is ON but {unknown} sampled item(s) are encrypted with a key that is not "
            "loaded: add the old key to the keyring or those items cannot be read."
        )
    elif plain:
        out["status"] = "on_with_plaintext"
        out["message"] = (
            f"Encryption is ON, but {plain} of {sampled} sampled stored item(s) are still plaintext "
            "(written before it was switched on). Run backend/scripts/encrypt_data.py."
        )
    else:
        out["status"] = "on"
        out["message"] = (
            f"Encryption is ON (AES-256-GCM, active key {vault.active_key_id!r}): "
            + ("all " if sampled else "")
            + f"{sampled} sampled stored item(s) are encrypted"
            + (f"; {old} still use an older key (run encrypt_data.py --rotate)." if old else ".")
        )
    return out


def _bodies(db: Database, start: datetime, end: datetime) -> list[dict[str, Any]]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, body FROM cases WHERE updated_at >= ? AND updated_at < ? AND status != 'processing' "
            "ORDER BY updated_at DESC LIMIT ?",
            (ts(start), ts(end), CASE_LIMIT),
        ).fetchall()
    return [json.loads(db.open_case_body(r["id"], r["body"])) for r in rows]


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
        "encryption_at_rest": encryption_status(db, settings),
        "score_model": score_model.status(settings),
    }


def incidents(db: Database, limit: int = 50) -> list[dict[str, Any]]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, ts, actor, detail FROM audit_log WHERE action = ? ORDER BY id DESC LIMIT ?",
            (INCIDENT_ACTION, limit),
        ).fetchall()
        acks = c.execute(
            "SELECT id, ts, actor, detail FROM audit_log WHERE action = ? ORDER BY id",
            (INCIDENT_ACK_ACTION,),
        ).fetchall()
    parsed = [(r, json.loads(r["detail"])) for r in rows]
    closed = {d["resolves"] for _, d in parsed if d.get("resolves")}
    latest_ack: dict[int, dict[str, Any]] = {}
    for a in acks:  # oldest first, so the newest note for an incident wins
        d = json.loads(a["detail"])
        if isinstance(d.get("incident"), int):
            latest_ack[d["incident"]] = {
                "acknowledged_by": a["actor"],
                "acknowledged_at": a["ts"],
                "ack_note": d.get("note"),
                "assigned_to": d.get("assigned_to"),
            }
    out = []
    for r, d in parsed:
        row = {"id": r["id"], "reported_at": r["ts"], "reported_by": r["actor"], **d}
        if r["id"] in closed:
            row["status"] = "resolved"
        if r["id"] in latest_ack:
            row.update(latest_ack[r["id"]])
        out.append(row)
    return out


def open_incident(db: Database, incident_id: int) -> dict[str, Any] | None:
    """The incident report with this id when it is a report still open (not itself a resolution, not resolved)."""
    with db.conn() as c:
        row = c.execute(
            "SELECT id, detail FROM audit_log WHERE id = ? AND action = ?",
            (incident_id, INCIDENT_ACTION),
        ).fetchone()
        if row is None:
            return None
        detail = json.loads(row["detail"])
        if detail.get("status") != "open":
            return None
        for (d,) in c.execute("SELECT detail FROM audit_log WHERE action = ?", (INCIDENT_ACTION,)):
            if json.loads(d).get("resolves") == incident_id:
                return None
    return {"id": row["id"], **detail}
