"""Score stage (docs/scoring-and-queue.md 4.3 to 4.8; foundation wrote the basic version).

Flags are grouped into findings (``app.findings``), each finding is counted once and coverage makes
silence visible. The score is one of two models (``GLDB_SCORE_MODEL``, ``app.score_model``): the
``legacy`` capped sum of finding weights, or the ``calibrated`` noisy-OR over per-finding
probabilities. Band thresholds come only from ``core.models``. Review never changes any of it.
"""

from __future__ import annotations

from collections.abc import Sequence

from app import score_model
from app.core.context import CaseContext
from app.core.models import (
    BAND_THRESHOLDS,
    SCORE_CAP,
    Band,
    Case,
    Coverage,
    Finding,
    Flag,
    Score,
    StageOutcome,
)
from app.findings import group_flags
from app.queue import DEFAULT_MIN_PROB, lane_for, page_counts, top_finding_line, unchecked_pages
from app.semantic.policy import counts_in_coverage

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
    # model-derived checks enter only when they failed (docs/semantic-signals.md section 6)
    runs = [r for r in case.check_runs if counts_in_coverage(r.rule, r.status, r.code)]
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
    # a check whose flag was lowered because the readers disagree has not been settled either way
    limited = {f.rule for f in case.flags if f.reading is not None}
    if limited:
        ran -= sum(1 for r in runs if r.status == "ran" and r.rule in limited)
        skipped = sorted(
            [
                *skipped,
                *(
                    {
                        "rule": r.rule,
                        "status": "reading_limited",
                        "reason": "two readers of the photo disagree on a figure this check uses; "
                        "check the page image by eye",
                        "code": "reading_limited",
                    }
                    for r in runs
                    if r.status == "ran" and r.rule in limited
                ),
            ],
            key=lambda d: d["rule"],
        )
        ratio = round(ran / applicable, 4) if applicable else ratio
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
    limited = sum(1 for f in case.flags if f.reading is not None)
    if limited:
        reasons.append(f"reading_limited:{limited}")
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
    """Legacy model: ``min(100, sum(finding.weight))``; no score point exists without a flag (4.4).
    Custom-rule findings are one family: together they add at most one point short of the High band,
    so unreviewed custom rules cannot carry a case to High on their own."""
    custom = sum(
        f.weight for f in findings if score_model.family_of(f.rules) == score_model.CUSTOM_FAMILY
    )
    builtin = sum(f.weight for f in findings) - custom
    value = min(SCORE_CAP, builtin + min(custom, BAND_THRESHOLDS["high"] - 1))
    return Score(value=value, band=band_for(value), partial=partial, partial_reasons=list(reasons))


def score_findings(
    findings: list[Finding],
    flags: Sequence[Flag],
    *,
    partial: bool,
    reasons: Sequence[str],
    settings,  # noqa: ANN001 - Settings
) -> tuple[Score, list[Finding]]:
    """Score under ``settings.score_model``. Legacy returns the findings untouched. Calibrated
    returns them with ``weight`` = the points the finding would score alone (the findings are
    combined by a noisy-OR, so the weights no longer add up to the score) and a score that never
    depends on the order of the findings."""
    if score_model.active_model(settings) == score_model.LEGACY:
        return compute_score(findings, partial=partial, reasons=reasons), findings
    weights = score_model.load_weights(settings.score_weights_path)
    value, points = score_model.calibrated_points(findings, flags, weights)
    scored = [f.model_copy(update={"weight": w}) for f, w in zip(findings, points, strict=True)]
    score = Score(value=value, band=band_for(value), partial=partial, partial_reasons=list(reasons))
    return score, scored


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
    case.score, case.findings = score_findings(
        case.findings,
        case.flags,
        partial=bool(reasons) or case.partial,
        reasons=reasons,
        settings=ctx.settings,
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
