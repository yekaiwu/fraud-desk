"""Outcome lane, page-level silence and the one-line reason (docs/scoring-and-queue.md 4.5 to 4.8).

The fast lane is a label for "look at this last": a person still decides and nothing is
auto-approved (plan 0.4 theme 1). ``build_queue_item`` lives in ``app/api/queue.py`` (api-backend).
"""

from __future__ import annotations

from app.core.models import Case, Document, Lane, UncheckedPage, UncheckedReason, Verdict
from app.findings import AML_ROUTING_RULES, HARD_STOP_RULES  # noqa: F401  (re-exported)
from app.semantic.policy import is_semantic_rule

FAST_LANE_MAX_SCORE = 20  # starting guess, recalibrate from the eval
FAST_LANE_MIN_COVERAGE = 0.80  # starting guess, recalibrate from the eval
LOW_COVERAGE_RATIO = 0.70  # below this the UI calls coverage "low"; a display marker, no routing
TOP_FINDING_MAX_CHARS = 160
DEFAULT_MIN_PROB = 0.70  # GLDB_CLASSIFIER_MIN_PROB

BASE_LANE: dict[str, Lane] = {"kyc": "edd", "aml": "aml", "loan": "loan"}
# The verdict each lane suggests to the person deciding (a suggestion only, never preselected).
LANE_SUGGESTED_VERDICT: dict[Lane, Verdict] = {
    "edd": "refer_edd",
    "aml": "escalate_aml",
    "loan": "reject_or_escalate",
    "fast": "approve",
}
_UNCHECKED_DOC_STATUS = ("received_not_checked", "failed")
_UNCHECKED_LABELS = ("boilerplate", "other")


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


# ------------------------------------------------------------------------------------ coverage


def unchecked_pages(case: Case, *, min_prob: float = DEFAULT_MIN_PROB) -> list[UncheckedPage]:
    """Every page no check looked at, with the first reason that applies (section 4.6). A page is
    unchecked when it lies in a document that was received but not checked or failed, could not be
    read, belongs to a statement whose pages could not be put together (``app.assemble``), has a
    low page classification probability, is labelled boilerplate or other, or belongs to no
    document."""
    docs_by_file: dict[str, list[Document]] = {}
    for doc in case.documents:
        docs_by_file.setdefault(doc.file_id, []).append(doc)

    out: list[UncheckedPage] = []
    for file in case.files:
        for page in file.pages:
            doc = next(
                (
                    d
                    for d in docs_by_file.get(file.id, [])
                    if d.page_start <= page.page <= d.page_end
                ),
                None,
            )
            cls = page.classification
            reason: UncheckedReason | None = None
            if doc is None:
                reason = "no_document"
            elif page.error is not None:
                reason = "unreadable"
            elif doc.statement is not None and doc.statement.state != "assembled":
                reason = "statement_incomplete"
            elif doc.status in _UNCHECKED_DOC_STATUS:
                reason = "not_checked" if doc.status == "received_not_checked" else "failed"
            elif doc.doc_type in _UNCHECKED_LABELS or (
                cls is not None and cls.label in _UNCHECKED_LABELS
            ):
                reason = "unrecognised_type"
            elif cls is not None and cls.probability < min_prob:
                reason = "low_confidence"
            if reason is not None:
                out.append(UncheckedPage(file_id=file.id, page=page.page, reason=reason))
    return out


def page_counts(case: Case, *, min_prob: float = DEFAULT_MIN_PROB) -> tuple[int, int]:
    """``(total_pages, unchecked_pages)`` (section 4.6); see ``unchecked_pages`` for the rule."""
    total = sum(len(file.pages) for file in case.files)
    return total, len(unchecked_pages(case, min_prob=min_prob))


def _check_ratio(case: Case) -> float:
    """Coverage of the checks for the fast-lane gate. Model-derived (semantic) checks are left out:
    an LLM-only signal must not lift a case over the coverage bar (docs/semantic-signals.md)."""
    if any(is_semantic_rule(r.rule) for r in case.check_runs):
        runs = [r for r in case.check_runs if not is_semantic_rule(r.rule)]
        if runs:
            return round(sum(1 for r in runs if r.status == "ran") / len(runs), 4)
        return 0.0 if case.partial else 1.0
    if case.coverage is not None:
        return case.coverage.ratio
    from app.score import compute_coverage  # lazy: score imports this module

    return compute_coverage(case).ratio


def effective_coverage(case: Case, *, min_prob: float = DEFAULT_MIN_PROB) -> float:
    """``ratio * (1 - unchecked / total)``; the plain ratio when the case has no pages. Feeds the
    fast-lane gate only: ``Coverage.ratio`` itself stays ``ran / applicable``."""
    ratio = _check_ratio(case)
    total, unchecked = page_counts(case, min_prob=min_prob)
    if total == 0:
        return ratio
    return ratio * (1 - unchecked / total)


# ------------------------------------------------------------------------------------- lanes


def fast_lane_eligible(case: Case, *, min_prob: float = DEFAULT_MIN_PROB) -> bool:
    score = case.score
    if score is None or case.status == "error" or score.partial:
        return False
    if score.value > FAST_LANE_MAX_SCORE:
        return False
    if any(f.hard_stop for f in case.findings):
        return False
    # a signal the model derived is a reason to look at the case now, never one to look last
    if any(f.weight > 0 and f.rules and all(map(is_semantic_rule, f.rules)) for f in case.findings):
        return False
    # switching a hard-stop check off must not open the fast lane that its flags would have shut
    if any(r.code == "switched_off" and r.rule in HARD_STOP_RULES for r in case.check_runs):
        return False
    return effective_coverage(case, min_prob=min_prob) >= FAST_LANE_MIN_COVERAGE


def lane_for(case: Case, *, min_prob: float = DEFAULT_MIN_PROB) -> Lane | None:
    """Recommended outcome lane (section 4.5): ``edd``, ``aml``, ``loan`` or ``fast``; ``None``
    while the case has no score. A recommendation only, it never preselects a verdict."""
    if case.score is None:
        return None
    base = BASE_LANE[case.use_case]
    if any(f.weight > 0 and set(f.rules) & AML_ROUTING_RULES for f in case.findings):
        base = "aml"
    if fast_lane_eligible(case, min_prob=min_prob):
        return "fast"
    return base


# --------------------------------------------------------------------------- one-line reason


def top_finding_line(case: Case) -> str | None:
    """One line for the queue (section 4.8); coverage is in the sentence because silence is not
    safety."""
    score = case.score
    if score is None:
        return None
    nonzero = [f for f in case.findings if f.severity != "info"]
    if not nonzero:
        if score.partial:
            first = score.partial_reasons[0] if score.partial_reasons else "unknown"
            return _cap(f"No flags so far, analysis incomplete: {first}", TOP_FINDING_MAX_CHARS)
        if case.coverage is not None:
            ran, applicable = case.coverage.ran, case.coverage.applicable
        else:
            runs = case.check_runs
            ran, applicable = sum(1 for r in runs if r.status == "ran"), len(runs)
        line = f"No flags raised; {ran} of {applicable} checks ran"
        k = sum(1 for f in case.findings if f.severity == "info")
        if k:
            line += f" ({k} informational note{'s' if k != 1 else ''})"
        return _cap(line, TOP_FINDING_MAX_CHARS)
    extra = len(nonzero) - 1
    suffix = f" (+{extra} more)" if extra else ""
    return _cap(nonzero[0].title, TOP_FINDING_MAX_CHARS - len(suffix)) + suffix
