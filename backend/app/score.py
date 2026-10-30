"""Score stage (docs/scoring-and-queue.md 4.3 to 4.8; foundation wrote the basic version).

Flags are grouped into findings (``app.findings``), each finding is counted once, the score is the
capped sum of finding weights, and coverage makes silence visible. Weights and band thresholds
come only from ``core.models`` so the eval can recalibrate them. Review never changes any of it.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.context import CaseContext
from app.core.models import (
    BAND_THRESHOLDS,
    SCORE_CAP,
    Band,
    Case,
    Coverage,
    Finding,
    Score,
    StageOutcome,
)
from app.findings import group_flags
from app.queue import DEFAULT_MIN_PROB, lane_for, page_counts, top_finding_line, unchecked_pages

MAX_PARTIAL_REASONS = 20
MAX_UNKNOWN_DOC_WARNINGS = 10


def band_for(value: int) -> Band:
    if value >= BAND_THRESHOLDS["high"]:
        return "high"
    if value >= BAND_THRESHOLDS["medium"]:
        return "medium"
    return "low"


def compute_coverage(case: Case, *, min_prob: float = DEFAULT_MIN_PROB) -> Coverage:
    """``ran / applicable`` over the check runs (4.6) plus page-level silence counts."""
    runs = case.check_runs
    applicable = len(runs)
    ran = sum(1 for r in runs if r.status == "ran")
    # a partial case with no runs must never read as full coverage
    ratio = round(ran / applicable, 4) if applicable else (0.0 if case.partial else 1.0)
    skipped = sorted(
        (
            # "code" is present only for a machine-readable reason (switched_off)
            {"rule": r.rule, "status": r.status, "reason": r.reason or ""}
            | ({"code": r.code} if r.code else {})
            for r in runs
            if r.status != "ran"
        ),
        key=lambda d: d["rule"],
    )
    pages_total, _ = page_counts(case, min_prob=min_prob)
    unchecked = unchecked_pages(case, min_prob=min_prob)
    return Coverage(
        applicable=applicable,
        ran=ran,
        ratio=ratio,
        skipped=skipped,
        pages_total=pages_total,
        pages_unchecked=len(unchecked),
        unchecked_pages=unchecked,
    )


def collect_partial_reasons(case: Case) -> list[str]:
    """Ordered, de-duplicated, capped at 20 (4.7). A partial score is a floor."""
    reasons: list[str] = list(case.partial_reasons)
    reasons += [f"stage_failed:{s.stage}" for s in case.stages if s.status == "failed"]
    reasons += [f"check_error:{r.rule}" for r in case.check_runs if r.status == "error"]
    reasons += [f"document_failed:{d.id}" for d in case.documents if d.status == "failed"]
    reasons += [f"document_partial:{d.id}" for d in case.documents if d.status == "partial"]
    unreadable = sum(1 for f in case.files for p in f.pages if p.error)
    if unreadable:
        reasons.append(f"pages_unreadable:{unreadable}")
    pieces = {
        d.statement.group
        for d in case.documents
        if d.statement is not None and d.statement.state != "assembled"
    }
    if pieces:  # pages of a split statement that could not be completed: coverage, not a flag
        reasons.append(f"statement_incomplete:{len(pieces)}")
    unique = list(dict.fromkeys(reasons))[:MAX_PARTIAL_REASONS]
    if case.partial and not unique:
        unique = ["partial: reason not recorded"]
    return unique


def compute_score(findings: Sequence[Finding], *, partial: bool, reasons: Sequence[str]) -> Score:
    """``min(100, sum(finding.weight))``; no score point exists without a flag (4.4)."""
    value = min(SCORE_CAP, sum(f.weight for f in findings))
    return Score(value=value, band=band_for(value), partial=partial, partial_reasons=list(reasons))


def _unknown_document_warnings(case: Case) -> list[str]:
    known = {d.id for d in case.documents}
    warnings: list[str] = []
    for flag in case.flags:
        for doc_id in flag.document_ids:
            if doc_id not in known:
                warnings.append(f"flag {flag.id} refers to unknown document {doc_id}")
    return warnings[:MAX_UNKNOWN_DOC_WARNINGS]


def run_stage(ctx: CaseContext) -> StageOutcome:
    """Writes ONLY case.findings, score, coverage, lane and top_finding (idempotent)."""
    case = ctx.case
    min_prob = ctx.settings.classifier_min_prob
    case.findings = group_flags(case, case.flags)
    case.coverage = compute_coverage(case, min_prob=min_prob)
    reasons = collect_partial_reasons(case)
    case.score = compute_score(
        case.findings, partial=bool(reasons) or case.partial, reasons=reasons
    )
    case.lane = lane_for(case, min_prob=min_prob)
    case.top_finding = top_finding_line(case)
    return StageOutcome(
        detail={
            "flags": len(case.flags),
            "findings": len(case.findings),
            "score": case.score.value,
            "applicable": case.coverage.applicable,
            "ran": case.coverage.ran,
        },
        warnings=_unknown_document_warnings(case),
    )
