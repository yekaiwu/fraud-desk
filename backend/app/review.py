"""Review workflow (docs/review-workflow-and-audit.md sections 3 and 4.1 to 4.6, api.md section 6).

The human decision step: verdicts per use case, flag decisions, a second approver for dismissing a
high flag, the "suspicion formed" clock, all atomic with their audit row. Nothing here changes
flags, findings or the score, and nothing approves, rejects or escalates without a named reviewer
and a reason (plan principle 2).

Inside ``ReviewService.submit`` only the one transaction connection is used (never
``Database.save_case`` / ``load_case``, which open their own connection or commit a shared one), so
the entry, the status change and the audit row commit or roll back together.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, field_validator, model_validator

from app.audit import AuditLog, iso, redact_flag_id, scrub, sha256_text
from app.core.config import Settings
from app.core.db import Database, ts
from app.core.errors import ApiError
from app.core.models import (
    Case,
    Disposition,
    ReviewEntry,
    Strict,
    UseCase,
    Verdict,
    utcnow,
)
from app.findings import is_high_flag

log = logging.getLogger("gldb.review")

Clock = Callable[[], datetime]

VERDICT_ORDER: tuple[Verdict, ...] = (
    "approve",
    "request_documents",
    "refer_edd",
    "escalate_aml",
    "reject_or_escalate",
)
VERDICTS_BY_USE_CASE: dict[UseCase, frozenset[Verdict]] = {
    "kyc": frozenset({"approve", "request_documents", "refer_edd", "escalate_aml"}),
    "aml": frozenset({"approve", "request_documents", "escalate_aml"}),
    "loan": frozenset({"approve", "request_documents", "escalate_aml", "reject_or_escalate"}),
}
VERDICT_LABELS: dict[Verdict, str] = {
    "approve": "Approve (close, reasons logged)",
    "request_documents": "Request more documents",
    "refer_edd": "Refer for EDD review",
    "escalate_aml": "Escalate to AML investigation (STR assessment)",
    "reject_or_escalate": "Reject or escalate (credit decision stays with the bank)",
}
REASON_MIN, REASON_MAX = 5, 1000
REVIEWER_MIN, REVIEWER_MAX = 2, 64
MAX_FLAGS_PER_ENTRY = 50

# error codes whose rejected attempts are written to the audit log (review-workflow 4.7)
_AUDITED_404_KINDS = {"flag", "entry", "finding"}


class ReviewError(ApiError):
    """A review rule was violated. ``main.py`` renders it as the single error body."""

    @property
    def status(self) -> int:
        return self.status_code


# --------------------------------------------------------------------------------------
# Request models (discriminated union on `kind`, mirrors the TypeScript ReviewRequest)
# --------------------------------------------------------------------------------------


class _ReviewBase(Strict):
    reviewer: str | None = None
    reason: str | None = Field(default=None, max_length=REASON_MAX * 4)
    case_version: int


class VerdictRequest(_ReviewBase):
    kind: Literal["verdict"]
    decision: Verdict


class FlagDecisionRequest(_ReviewBase):
    kind: Literal["flag_decision"]
    decision: Literal["confirm", "dismiss"]
    flag_ids: list[str] | None = None
    finding_id: str | None = None

    @field_validator("flag_ids")
    @classmethod
    def _dedupe(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _exactly_one_target(self) -> FlagDecisionRequest:
        if (self.flag_ids is None) == (self.finding_id is None):
            raise ValueError("give exactly one of flag_ids or finding_id")
        if self.flag_ids is not None and not 1 <= len(self.flag_ids) <= MAX_FLAGS_PER_ENTRY:
            raise ValueError(f"flag_ids must hold 1 to {MAX_FLAGS_PER_ENTRY} ids")
        return self


class SecondApprovalRequest(_ReviewBase):
    kind: Literal["second_approval"]
    decision: Literal["approve", "decline"]
    target_entry_id: str


ReviewRequest = Annotated[
    VerdictRequest | FlagDecisionRequest | SecondApprovalRequest, Field(discriminator="kind")
]
_REQUEST_ADAPTER: TypeAdapter[Any] = TypeAdapter(ReviewRequest)


def parse_review_request(
    data: Mapping[str, Any],
) -> VerdictRequest | FlagDecisionRequest | SecondApprovalRequest:
    return _REQUEST_ADAPTER.validate_python(dict(data))


class ReviewResult(Strict):
    entry: ReviewEntry
    case: dict[str, Any]


# --------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------


def _entry_no(entry: ReviewEntry) -> int:
    tail = entry.id[1:]
    return int(tail) if tail.isdigit() else 0


def allowed_verdicts(case: Case, *, current: bool = True) -> list[Verdict]:
    """``[]`` unless the case is ``open`` and the viewed version is the current one."""
    if case.status != "open" or not current:
        return []
    allowed = VERDICTS_BY_USE_CASE[case.use_case]
    return [v for v in VERDICT_ORDER if v in allowed]


def flag_dispositions(case: Case, entries: Sequence[ReviewEntry]) -> dict[str, Disposition]:
    """Disposition of every flag of ``case`` from the flag decisions of ``case.version``: the latest
    entry that targets a flag decides (a declined dismissal returns the flag to ``open``)."""
    result: dict[str, Disposition] = {f.id: "open" for f in case.flags}
    relevant = [e for e in entries if e.kind == "flag_decision" and e.case_version == case.version]
    for entry in sorted(relevant, key=_entry_no):
        for flag_id in entry.flag_ids:
            if flag_id not in result:
                continue
            if entry.decision == "confirm":
                result[flag_id] = "confirmed" if entry.status == "effective" else "open"
            elif entry.status == "effective":
                result[flag_id] = "dismissed"
            elif entry.status == "pending_second_approval":
                result[flag_id] = "pending_second_approval"
            else:  # declined
                result[flag_id] = "open"
    return result


def open_high_flags(case: Case, dispositions: Mapping[str, Disposition]) -> list[str]:
    """High flags that are neither confirmed nor effectively dismissed (a pending dismissal counts
    as open)."""
    return [
        f.id
        for f in case.flags
        if is_high_flag(f) and dispositions.get(f.id, "open") in ("open", "pending_second_approval")
    ]


def pending_entries(entries: Sequence[ReviewEntry], version: int) -> list[ReviewEntry]:
    return [
        e
        for e in sorted(entries, key=_entry_no)
        if e.case_version == version and e.status == "pending_second_approval"
    ]


def materialise(case: Case, entries: Sequence[ReviewEntry]) -> Case:
    """A copy of ``case`` with ``flags[*].disposition`` set and ``review`` = the entries of
    ``case.version``. Never writes to the database."""
    out = case.model_copy(deep=True)
    dispositions = flag_dispositions(out, entries)
    for flag in out.flags:
        flag.disposition = dispositions.get(flag.id, "open")
    out.review = [e for e in entries if e.case_version == out.version]
    return out


def pending_summary(entries: Sequence[ReviewEntry], version: int) -> list[dict[str, Any]]:
    """The ``pending_second_approvals`` list of the case view."""
    return [
        {
            "entry_id": e.id,
            "flag_ids": list(e.flag_ids),
            "reviewer": e.reviewer,
            "created_at": iso(e.created_at),
        }
        for e in pending_entries(entries, version)
    ]


def pending_counts(db: Database, rows: Sequence[sqlite3.Row]) -> dict[str, int]:
    """Pending second approvals of the *current* version, per case id (queue column)."""
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    versions = {r["id"]: r["version"] for r in rows}
    marks = ",".join("?" for _ in ids)
    counts: Counter[str] = Counter()
    with db.conn() as c:
        found = c.execute(
            "SELECT case_id, case_version FROM review_entries WHERE case_id IN ("
            + marks
            + ") AND json_extract(body, '$.status') = 'pending_second_approval'",
            ids,
        ).fetchall()
    for r in found:
        if versions.get(r["case_id"]) == r["case_version"]:
            counts[r["case_id"]] += 1
    return dict(counts)


def _norm_person(name: str) -> str:
    return name.strip().casefold()


def clean_reviewer(value: str | None) -> str:
    reviewer = (value or "").strip()
    if not REVIEWER_MIN <= len(reviewer) <= REVIEWER_MAX:
        raise ReviewError(
            422,
            "reviewer_required",
            f"A reviewer name of {REVIEWER_MIN} to {REVIEWER_MAX} characters is required.",
            {"field": "reviewer"},
        )
    return reviewer


def clean_reason(value: str | None) -> str:
    reason = (value or "").strip()
    if len(reason) < REASON_MIN:
        raise ReviewError(
            422,
            "reason_required",
            f"A reason of at least {REASON_MIN} characters is required.",
            {"field": "reason"},
        )
    if len(reason) > REASON_MAX:
        raise ApiError(
            422,
            "validation_error",
            f"The reason may have at most {REASON_MAX} characters.",
            {"errors": [{"field": "reason", "message": "too long", "type": "string_too_long"}]},
        )
    return reason


def _not_found(kind: str, **details: Any) -> ReviewError:
    return ReviewError(404, "not_found", f"The {kind} was not found.", {"kind": kind, **details})


def load_entries(db: Database, case_id: str, *, version: int | None = None) -> list[ReviewEntry]:
    """Review entries of a case, oldest first (insertion order); ``version=None`` = every version.
    Raises a 404 ``ReviewError`` for an unknown case."""
    sql = "SELECT body FROM review_entries WHERE case_id = ?"
    params: list[Any] = [case_id]
    if version is not None:
        sql += " AND case_version = ?"
        params.append(version)
    with db.conn() as c:
        if c.execute("SELECT 1 FROM cases WHERE id = ?", (case_id,)).fetchone() is None:
            raise _not_found("case", id=case_id)
        rows = c.execute(sql + " ORDER BY rowid", params).fetchall()
    return [ReviewEntry.model_validate_json(r["body"]) for r in rows]


def version_history(db: Database, case_id: str) -> list[dict[str, Any]]:
    """``GET /cases/{id}/versions`` rows (review-workflow 4.9): version 1 is ``created``, a later
    version is ``documents_added`` if a file was added in it, else ``rerun``. Score and band come
    from each snapshot; the current version comes from the live case."""
    with db.conn() as c:
        live = c.execute("SELECT body, version FROM cases WHERE id = ?", (case_id,)).fetchone()
        if live is None:
            raise _not_found("case", id=case_id)
        snaps = {
            r["version"]: r
            for r in c.execute(
                "SELECT version, created_at, body FROM case_versions WHERE case_id = ?", (case_id,)
            )
        }
    current_version = live["version"]
    current_body = json.loads(live["body"])
    added = {f.get("added_in_version", 1) for f in current_body.get("files", [])}
    out: list[dict[str, Any]] = []
    for v in sorted({*snaps, current_version}):
        body = current_body if v == current_version else json.loads(snaps[v]["body"])
        score = body.get("score") or {}
        created = snaps[v]["created_at"] if v in snaps else body.get("updated_at")
        out.append(
            {
                "version": v,
                "created_at": created,
                "score": score.get("value"),
                "band": score.get("band"),
                "reason": "created" if v == 1 else ("documents_added" if v in added else "rerun"),
                "current": v == current_version,
            }
        )
    return out


class ReviewService:
    def __init__(self, db: Database, audit: AuditLog, settings: Settings, *, clock: Clock = utcnow):
        self.db = db
        self.audit = audit
        self.settings = settings
        self.clock = clock

    # -- reads ---------------------------------------------------------------------------

    def entries(self, case_id: str, *, version: int | None = None) -> list[ReviewEntry]:
        """Review entries, oldest first. ``version=None`` returns every version."""
        return load_entries(self.db, case_id, version=version)

    # -- writes --------------------------------------------------------------------------

    def submit(
        self, case_id: str, req: VerdictRequest | FlagDecisionRequest | SecondApprovalRequest
    ) -> ReviewResult:
        try:
            return self._submit(case_id, req)
        except ReviewError as exc:
            self._audit_rejected(case_id, req, exc)
            raise

    def _submit(
        self, case_id: str, req: VerdictRequest | FlagDecisionRequest | SecondApprovalRequest
    ) -> ReviewResult:
        reviewer = clean_reviewer(req.reviewer)
        reason = clean_reason(req.reason)
        now = self.clock()
        with self.db.conn() as c:
            c.execute("BEGIN IMMEDIATE")  # two reviewers racing: the second waits, then re-reads
            case, entries = self._read(c, case_id)
            if case.status == "processing":
                raise ReviewError(
                    409,
                    "case_processing",
                    "The case is still being analysed.",
                    {"status": case.status},
                )
            if case.status != "open":
                raise ReviewError(
                    409,
                    "case_not_open",
                    "Review actions need a case with status 'open'.",
                    {"status": case.status},
                )
            if req.case_version != case.version:
                raise ReviewError(
                    409,
                    "stale_version",
                    "The case has a newer version than the one you reviewed.",
                    {"current_version": case.version, "sent_version": req.case_version},
                )
            dispositions = flag_dispositions(case, entries)
            if isinstance(req, FlagDecisionRequest):
                entry, audit_action, audit_detail = self._flag_decision(
                    req, case, entries, dispositions, reviewer, reason, now
                )
            elif isinstance(req, SecondApprovalRequest):
                entry, audit_action, audit_detail = self._second_approval(
                    c, req, case, entries, reviewer, reason, now
                )
            else:
                entry, audit_action, audit_detail = self._verdict(
                    c, req, case, dispositions, reviewer, reason, now
                )
            c.execute(
                "INSERT INTO review_entries (id, case_id, case_version, created_at, body) "
                "VALUES (?,?,?,?,?)",
                (
                    f"{case_id}/{entry.id}",
                    case_id,
                    entry.case_version,
                    ts(now),
                    entry.model_dump_json(),
                ),
            )
            audit_detail.update(
                {
                    "reason_sha256": sha256_text(reason),
                    "reason_len": len(reason),
                }
            )
            if not self.settings.pseudonymise_logs:
                audit_detail["reason"] = scrub(reason, max_len=REASON_MAX)
            self.audit.write(
                actor=reviewer, action=audit_action, case_id=case_id, detail=audit_detail, conn=c
            )
            all_entries = [*entries, entry]
            if entry.kind == "second_approval":
                all_entries = [
                    e.model_copy(update={"status": self._target_status_after(entry)})
                    if e.id == entry.target_entry_id
                    else e
                    for e in all_entries
                ]
            summary = {
                "id": case.id,
                "status": case.status,
                "decision": case.decision,
                "escalated_at": iso(case.escalated_at) if case.escalated_at else None,
                "version": case.version,
                "pending_second_approvals": len(pending_entries(all_entries, case.version)),
            }
        return ReviewResult(entry=entry, case=summary)

    # -- kind specific ---------------------------------------------------------------------

    @staticmethod
    def _target_status_after(approval: ReviewEntry) -> Literal["effective", "declined"]:
        return "effective" if approval.decision == "approve" else "declined"

    def _flag_decision(
        self,
        req: FlagDecisionRequest,
        case: Case,
        entries: Sequence[ReviewEntry],
        dispositions: Mapping[str, Disposition],
        reviewer: str,
        reason: str,
        now: datetime,
    ) -> tuple[ReviewEntry, str, dict[str, Any]]:
        if req.finding_id is not None:
            finding = next((f for f in case.findings if f.id == req.finding_id), None)
            if finding is None:
                raise _not_found("finding", id=req.finding_id)
            flag_ids = list(finding.flag_ids)
        else:
            flag_ids = list(req.flag_ids or [])
        by_id = {f.id: f for f in case.flags}
        missing = [i for i in flag_ids if i not in by_id]
        if missing or not flag_ids:
            raise _not_found("flag", flag_ids=missing)
        pending = [i for i in flag_ids if dispositions.get(i) == "pending_second_approval"]
        if pending:
            raise ReviewError(
                409,
                "flag_decision_pending",
                "A dismissal of this flag is waiting for a second approver; approve or decline it first.",
                {"flag_ids": pending},
            )
        needs_second = (
            req.decision == "dismiss"
            and self.settings.require_second_approver
            and any(is_high_flag(by_id[i]) for i in flag_ids)
        )
        entry = ReviewEntry(
            id=f"r{len(entries) + 1}",
            kind="flag_decision",
            reviewer=reviewer,
            decision=req.decision,
            reason=reason,
            flag_ids=flag_ids,
            finding_id=req.finding_id,
            status="pending_second_approval" if needs_second else "effective",
            case_version=case.version,
            created_at=now,
        )
        severities = Counter(by_id[i].severity for i in flag_ids)
        detail = {
            "entry_id": entry.id,
            "decision": entry.decision,
            "status": entry.status,
            "flag_refs": [redact_flag_id(i) for i in flag_ids],
            "finding_id": req.finding_id,
            "severities": dict(sorted(severities.items())),
            "case_version": case.version,
        }
        return entry, "review.flag_decision", detail

    def _second_approval(
        self,
        c: sqlite3.Connection,
        req: SecondApprovalRequest,
        case: Case,
        entries: Sequence[ReviewEntry],
        reviewer: str,
        reason: str,
        now: datetime,
    ) -> tuple[ReviewEntry, str, dict[str, Any]]:
        target = next(
            (e for e in entries if e.id == req.target_entry_id and e.case_version == case.version),
            None,
        )
        if target is None:
            raise _not_found("entry", id=req.target_entry_id)
        if target.status != "pending_second_approval":
            raise ReviewError(
                409,
                "entry_not_pending",
                "That entry is not waiting for a second approver.",
                {"target_entry_id": target.id, "status": target.status},
            )
        if _norm_person(reviewer) == _norm_person(target.reviewer):
            raise ReviewError(
                409,
                "second_approver_must_differ",
                "The second approver must be a different reviewer.",
                {"target_entry_id": target.id},
            )
        entry = ReviewEntry(
            id=f"r{len(entries) + 1}",
            kind="second_approval",
            reviewer=reviewer,
            decision=req.decision,
            reason=reason,
            target_entry_id=target.id,
            status="effective",
            case_version=case.version,
            created_at=now,
        )
        new_status = self._target_status_after(entry)
        updated = target.model_copy(update={"status": new_status})
        # the one permitted UPDATE of review_entries: the status of a pending entry
        c.execute(
            "UPDATE review_entries SET body = ? WHERE id = ? AND "
            "json_extract(body, '$.status') = 'pending_second_approval'",
            (updated.model_dump_json(), f"{case.id}/{target.id}"),
        )
        detail = {
            "entry_id": entry.id,
            "decision": entry.decision,
            "target_entry_id": target.id,
            "target_status_after": new_status,
            "case_version": case.version,
        }
        return entry, "review.second_approval", detail

    def _verdict(
        self,
        c: sqlite3.Connection,
        req: VerdictRequest,
        case: Case,
        dispositions: Mapping[str, Disposition],
        reviewer: str,
        reason: str,
        now: datetime,
    ) -> tuple[ReviewEntry, str, dict[str, Any]]:
        allowed = VERDICTS_BY_USE_CASE[case.use_case]
        if req.decision not in allowed:
            raise ReviewError(
                422,
                "verdict_not_allowed",
                f"The verdict '{req.decision}' is not allowed for a {case.use_case} case.",
                {"allowed": [v for v in VERDICT_ORDER if v in allowed], "use_case": case.use_case},
            )
        unresolved = open_high_flags(case, dispositions)
        if req.decision == "approve" and unresolved:
            raise ReviewError(
                409,
                "high_flags_open",
                "Every high flag must be confirmed or dismissed (with a second approver) before approval.",
                {"flag_ids": unresolved},
            )
        n_entries = c.execute(
            "SELECT COUNT(*) FROM review_entries WHERE case_id = ?", (case.id,)
        ).fetchone()[0]
        entry = ReviewEntry(
            id=f"r{n_entries + 1}",
            kind="verdict",
            reviewer=reviewer,
            decision=req.decision,
            reason=reason,
            status="effective",
            case_version=case.version,
            created_at=now,
        )
        status_before = case.status
        if req.decision == "request_documents":
            case.status = "awaiting_documents"
        else:
            case.status = "closed"
            case.decision = req.decision
            if req.decision == "escalate_aml":
                case.escalated_at = entry.created_at
        case.updated_at = now
        self._write_case(c, case)
        detail: dict[str, Any] = {
            "entry_id": entry.id,
            "decision": entry.decision,
            "status_before": status_before,
            "status_after": case.status,
            "case_version": case.version,
            "score": case.score.value if case.score else None,
            "band": case.score.band if case.score else None,
            "partial": bool(case.partial or (case.score is not None and case.score.partial)),
            "coverage_ratio": case.coverage.ratio if case.coverage else None,
            "open_high_flags": len(unresolved),
        }
        if req.decision == "escalate_aml":
            detail["escalated_at"] = iso(entry.created_at)
        return entry, "review.verdict", detail

    # -- plumbing --------------------------------------------------------------------------

    @staticmethod
    def _read(c: sqlite3.Connection, case_id: str) -> tuple[Case, list[ReviewEntry]]:
        row = c.execute("SELECT body FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise _not_found("case", id=case_id)
        case = Case.model_validate_json(row["body"])
        rows = c.execute(
            "SELECT body FROM review_entries WHERE case_id = ? ORDER BY rowid", (case_id,)
        ).fetchall()
        return case, [ReviewEntry.model_validate_json(r["body"]) for r in rows]

    @staticmethod
    def _write_case(c: sqlite3.Connection, case: Case) -> None:
        # the same columns Database.save_case maintains, on the caller's connection
        cols = Database._denormalised(case)
        assignments = ", ".join(k + " = ?" for k in cols)
        c.execute(
            "UPDATE cases SET " + assignments + ", body = ? WHERE id = ?",
            [*cols.values(), Database._body(case), case.id],
        )

    def _audit_rejected(
        self,
        case_id: str,
        req: VerdictRequest | FlagDecisionRequest | SecondApprovalRequest,
        exc: ReviewError,
    ) -> None:
        kind = exc.details.get("kind") if exc.status_code == 404 else None
        audited = (
            exc.status_code == 409
            or exc.code in ("reason_required", "reviewer_required", "verdict_not_allowed")
            or (exc.status_code == 404 and kind in _AUDITED_404_KINDS)
        )
        if not audited:
            return
        try:
            if not self.db.case_exists(case_id):
                return
            actor = (req.reviewer or "").strip()
            if not REVIEWER_MIN <= len(actor) <= REVIEWER_MAX:
                actor = "anonymous"
            self.audit.write(
                actor=actor,
                action="review.rejected",
                case_id=case_id,
                detail={
                    "kind": req.kind,
                    "decision": req.decision,
                    "code": exc.code,
                    "http_status": exc.status_code,
                    "case_version": req.case_version,
                    "reason_len": len((req.reason or "").strip()),
                },
            )
        except Exception:  # noqa: BLE001 - never mask the rule violation
            log.exception("could not audit a rejected review attempt for %s", case_id)
