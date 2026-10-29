"""Rule effectiveness (docs/review-workflow-and-audit.md 4.14, api.md section 9).

Population: the latest version of each case (optionally filtered by use case and by the creation
date). Counts use the effective dispositions of that version (``flag_dispositions``). Times run from
the analysis stamp (``provenance.created_at``) to the entry that decided. This measures reviewer
agreement with a rule, not ground truth (ground truth comes from the eval).
"""

from __future__ import annotations

import statistics
from datetime import date, datetime
from typing import Any

from app.checks.registry import all_checks
from app.core.db import Database
from app.core.models import Case, ReviewEntry
from app.review import flag_dispositions, load_entries

NOT_REGISTERED = "(rule no longer registered)"


def _median(values: list[float]) -> int | float | None:
    if not values:
        return None
    m = statistics.median(values)
    return int(m) if float(m).is_integer() else round(float(m), 1)


def _decision_times(
    case: Case, entries: list[ReviewEntry], dispositions: dict[str, str]
) -> dict[str, datetime]:
    """When each confirmed or dismissed flag was decided. A dismissal that needed a second approver
    counts from the approving countersignature (that is when it became effective)."""
    approvals: dict[str, datetime] = {}
    for e in entries:
        if (
            e.kind == "second_approval"
            and e.decision == "approve"
            and e.case_version == case.version
            and e.target_entry_id
        ):
            approvals[e.target_entry_id] = e.created_at
    times: dict[str, datetime] = {}
    flag_entries = [
        e for e in entries if e.kind == "flag_decision" and e.case_version == case.version
    ]
    for e in flag_entries:  # insertion order: the latest entry that targets a flag wins
        for fid in e.flag_ids:
            if dispositions.get(fid) in ("confirmed", "dismissed"):
                times[fid] = approvals.get(e.id, e.created_at)
    return times


def compute_effectiveness(
    db: Database, *, use_case: str | None = None, since: date | None = None
) -> dict[str, Any]:
    checks = all_checks()
    where: list[str] = []
    params: list[Any] = []
    if use_case:
        where.append("use_case = ?")
        params.append(use_case)
    if since is not None:
        where.append("created_at >= ?")
        params.append(since.isoformat())
    sql = (
        "SELECT id FROM cases" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    )
    with db.conn() as c:
        ids = [r["id"] for r in c.execute(sql, params).fetchall()]

    stats: dict[str, dict[str, Any]] = {}

    def row_for(rule: str, stage: str, severity: str, description: str) -> dict[str, Any]:
        if rule not in stats:
            stats[rule] = {
                "rule": rule,
                "stage": stage,
                "default_severity": severity,
                "description": description,
                "cases": set(),
                "times_fired": 0,
                "confirmed": 0,
                "dismissed": 0,
                "pending": 0,
                "open": 0,
                "decide_seconds": [],
                "cases_ran": 0,
                "cases_skipped": 0,
            }
        return stats[rule]

    for meta in checks:
        row_for(meta.rule, meta.stage, meta.severity, meta.description)

    cases_decided = 0
    verdict_seconds: list[float] = []
    for case_id in ids:
        case = db.load_case(case_id)
        entries = load_entries(db, case_id)
        dispositions = flag_dispositions(case, entries)
        stamp = case.provenance.created_at if case.provenance else None
        if case.decision is not None:
            cases_decided += 1
            verdict = next(
                (
                    e
                    for e in reversed(entries)
                    if e.kind == "verdict"
                    and e.case_version == case.version
                    and e.decision == case.decision
                ),
                None,
            )
            if verdict is not None and stamp is not None:
                verdict_seconds.append((verdict.created_at - stamp).total_seconds())
        times = _decision_times(case, entries, dispositions)
        for flag in case.flags:
            r = row_for(flag.rule, flag.stage, flag.severity, NOT_REGISTERED)
            r["times_fired"] += 1
            r["cases"].add(case_id)
            disp = dispositions.get(flag.id, "open")
            key = "pending" if disp == "pending_second_approval" else disp
            if key == "confirmed":
                r["confirmed"] += 1
            elif key == "dismissed":
                r["dismissed"] += 1
            elif key == "pending":
                r["pending"] += 1
            else:
                r["open"] += 1
            if flag.id in times and stamp is not None:
                r["decide_seconds"].append((times[flag.id] - stamp).total_seconds())
        for run in case.check_runs:
            r = row_for(run.rule, run.stage, "info", NOT_REGISTERED)
            if run.status == "ran":
                r["cases_ran"] += 1
            else:
                r["cases_skipped"] += 1

    rules = []
    for r in stats.values():
        denom = r["confirmed"] + r["dismissed"]
        rules.append(
            {
                "rule": r["rule"],
                "stage": r["stage"],
                "default_severity": r["default_severity"],
                "description": r["description"],
                "cases_fired": len(r["cases"]),
                "times_fired": r["times_fired"],
                "confirmed": r["confirmed"],
                "dismissed": r["dismissed"],
                "pending": r["pending"],
                "open": r["open"],
                "dismissal_rate": round(r["dismissed"] / denom, 4) if denom else None,
                "median_seconds_to_decide": _median(r["decide_seconds"]),
                "cases_ran": r["cases_ran"],
                "cases_skipped": r["cases_skipped"],
            }
        )
    rules.sort(key=lambda x: (-x["times_fired"], x["rule"]))
    return {
        "generated_for": {
            "use_case": use_case,
            "since": since.isoformat() if since else None,
        },
        "cases_total": len(ids),
        "cases_decided": cases_decided,
        "median_seconds_to_verdict": _median(verdict_seconds),
        "rules": rules,
    }
