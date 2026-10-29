"""Retention, purge and delete-case (docs/review-workflow-and-audit.md 4.11, api.md section 10).

Deleting a case removes its data (``Database.delete_case``) but keeps a hashed audit trail: one
``case.deleted`` row whose ``case_ref`` is ``case_ref_hash(id)`` and whose ``detail`` carries the
scrubbed reason, never applicant data. Earlier audit rows are rewritten to the same hash. Escalated
cases are never purged automatically (suspicion records may need longer retention [confirm]).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from app.audit import AuditLog, case_ref_hash, scrub, sha256_text
from app.core.db import CaseNotFound, Database, ts
from app.core.errors import ApiError
from app.review import clean_reason, clean_reviewer

log = logging.getLogger("gldb.retention")

SYSTEM_ACTOR = "system"


def expired_case_ids(db: Database, *, older_than_days: int, now: datetime) -> list[str]:
    """Closed or errored cases not updated for ``older_than_days`` and never escalated."""
    cutoff = ts(now - timedelta(days=older_than_days))
    with db.conn() as c:
        rows = c.execute(
            "SELECT id FROM cases WHERE status IN ('closed', 'error') AND updated_at < ? "
            "AND json_extract(body, '$.escalated_at') IS NULL ORDER BY updated_at, id",
            (cutoff,),
        ).fetchall()
    return [r["id"] for r in rows]


def delete_case_with_audit(
    db: Database,
    audit: AuditLog,
    case_id: str,
    *,
    reviewer: str,
    reason: str,
    trigger: str = "manual",
) -> None:
    """Validate, audit, delete, pseudonymise. ``404`` unknown, ``409 case_processing`` while a job
    runs, ``422`` for a missing reviewer or reason. If the delete itself fails a
    ``case.delete_failed`` row is written and a ``500 internal_error`` is raised (case remains)."""
    reviewer = clean_reviewer(reviewer)
    reason = clean_reason(reason)
    try:
        case = db.load_case(case_id)
    except CaseNotFound as exc:
        raise ApiError(
            404, "not_found", "Case not found.", {"kind": "case", "id": case_id}
        ) from exc
    if case.status == "processing":
        raise ApiError(
            409, "case_processing", "The case is still being analysed.", {"status": case.status}
        )
    audit.write(
        actor=reviewer,
        action="case.deleted",
        case_id=case_id,
        hashed_ref=True,
        detail={
            "reason": scrub(reason, max_len=200),
            "reason_sha256": sha256_text(reason),
            "versions": case.version,
            "files": len(case.files),
            "decision": case.decision,
            "had_escalation": case.escalated_at is not None,
            "trigger": trigger,
        },
    )
    try:
        db.delete_case(case_id)
    except CaseNotFound as exc:  # deleted by someone else between load and delete
        raise ApiError(
            404, "not_found", "Case not found.", {"kind": "case", "id": case_id}
        ) from exc
    except Exception as exc:
        log.exception("delete of %s failed", case_id)
        audit.write(
            actor=reviewer,
            action="case.delete_failed",
            case_id=case_id,
            hashed_ref=True,
            detail={"error": scrub(f"{type(exc).__name__}: {exc}")},
        )
        raise ApiError(500, "internal_error", "The case could not be deleted.") from exc
    audit.pseudonymise_case(case_id)


def purge(
    db: Database,
    audit: AuditLog,
    *,
    older_than_days: int,
    dry_run: bool,
    now: datetime,
) -> dict[str, Any]:
    """List (dry run, the default at the API) or delete expired cases."""
    ids = expired_case_ids(db, older_than_days=older_than_days, now=now)
    if dry_run:
        return {"dry_run": True, "case_ids": ids, "count": len(ids)}
    deleted: list[str] = []
    for case_id in ids:
        try:
            delete_case_with_audit(
                db,
                audit,
                case_id,
                reviewer=SYSTEM_ACTOR,
                reason="retention",
                trigger="retention",
            )
            deleted.append(case_id)
        except ApiError as exc:
            log.warning("retention skipped %s: %s", case_id, exc.code)
    audit.write(
        actor=SYSTEM_ACTOR,
        action="retention.purge",
        case_id=None,
        detail={
            "older_than_days": older_than_days,
            "count": len(deleted),
            "case_ref_hashes": [case_ref_hash(i) for i in deleted],
        },
    )
    return {"dry_run": False, "case_ids": deleted, "count": len(deleted)}
