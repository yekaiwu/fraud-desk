"""Scorer basics (foundation's tests, kept valid under grouping by score-queue).

Every case here uses flags of one rule on distinct documents (or a single flag), so grouping gives
one finding per flag; grouping itself is covered by test_findings.py.
"""

from __future__ import annotations

import pytest

from app.checks.registry import make_flag
from app.core.models import BAND_THRESHOLDS, SCORE_WEIGHTS, CheckRun, StageRecord
from app.core.testing import make_ctx, make_doc
from app.findings import group_flags
from app.score import (
    band_for,
    collect_partial_reasons,
    compute_coverage,
    compute_score,
    run_stage,
)


def flag(sev: str, n: int = 1, rule: str = "x_rule"):  # noqa: ANN201
    return make_flag(
        rule,
        docs=[f"d{n}"],
        evidence=f"evidence {n}",
        explanation=f"Sentence number {n} with 2.4M in it. Second sentence.",
        severity=sev,
    )  # type: ignore[arg-type]


def scored(*flags, **case_kw):  # noqa: ANN001, ANN002, ANN003, ANN201
    runs = case_kw.pop("check_runs", [CheckRun(rule="r", stage="rules", status="ran")])
    ctx = make_ctx([make_doc("invoice", id="d1")], check_runs=runs, **case_kw)
    ctx.case.flags = list(flags)
    outcome = run_stage(ctx)
    return ctx.case, outcome


def test_clean_case_is_zero_low() -> None:
    case, outcome = scored()
    assert case.score and (case.score.value, case.score.band, case.score.partial) == (
        0,
        "low",
        False,
    )
    assert case.findings == []
    assert case.top_finding == "No flags raised; 1 of 1 checks ran"
    assert outcome.detail == {"flags": 0, "findings": 0, "score": 0, "applicable": 1, "ran": 1}
    assert case.lane == "fast"  # clean, fully covered, score 0 (a label; a person still decides)


def test_one_high_flag_is_30_medium() -> None:
    case, _ = scored(flag("high"))
    assert case.score and (case.score.value, case.score.band) == (30, "medium")
    (finding,) = case.findings
    assert finding.id == "fnd_1" and finding.weight == 30 and finding.group_key == "rule:x_rule:d1"
    assert finding.title == "Sentence number 1 with 2.4M in it"  # not cut at the decimal point
    assert (
        finding.summary.startswith("Sentence number 1")
        and "Evidence: evidence 1" in finding.summary
    )
    assert (
        finding.hard_stop is True  # a high finding is a hard stop (blocks the fast lane only)
        and finding.rules == ["x_rule"]
        and finding.document_ids == ["d1"]
    )
    assert case.top_finding == "Sentence number 1 with 2.4M in it"


@pytest.mark.parametrize(
    ("value", "band"),
    [(0, "low"), (29, "low"), (30, "medium"), (59, "medium"), (60, "high"), (100, "high")],
)
def test_band_boundaries(value: int, band: str) -> None:
    assert band_for(value) == band


def test_score_is_built_from_the_constants() -> None:
    # one flag per severity: the sum must equal the weights, whatever their values
    flags = [flag(sev, n) for n, sev in enumerate(["high", "medium", "low", "info"], start=1)]
    case, _ = scored(*flags)
    assert case.score and case.score.value == min(100, sum(SCORE_WEIGHTS.values()))
    assert [f.severity for f in case.findings] == ["high", "medium", "low", "info"]  # rank order
    assert BAND_THRESHOLDS == {"medium": 30, "high": 60}


def test_cap_at_100() -> None:
    case, _ = scored(*[flag("high", n) for n in range(1, 6)])
    assert case.score and (case.score.value, case.score.band) == (100, "high")
    assert case.top_finding and case.top_finding.endswith("(+4 more)")


def test_info_flags_weigh_nothing() -> None:
    case, _ = scored(flag("info"))
    assert case.score and case.score.value == 0 and case.findings[0].weight == 0
    assert case.top_finding == "No flags raised; 1 of 1 checks ran (1 informational note)"


def test_no_score_point_without_a_flag() -> None:
    score = compute_score([], partial=False, reasons=[])
    assert score.value == 0
    one = flag("medium")
    findings = group_flags(make_ctx().case, [one])
    assert compute_score(findings, partial=False, reasons=[]).value == SCORE_WEIGHTS["medium"]


def test_coverage_ratio_and_skips() -> None:
    runs = [
        CheckRun(rule="b", stage="rules", status="skipped", reason="no doc"),
        CheckRun(rule="a", stage="rules", status="error", reason="KeyError: x"),
        CheckRun(rule="c", stage="rules", status="ran"),
        CheckRun(rule="d", stage="rules", status="ran"),
    ]
    case, _ = scored(check_runs=runs)
    cov = case.coverage
    assert cov and (cov.applicable, cov.ran, cov.ratio) == (4, 2, 0.5)
    assert cov.skipped == [
        {"rule": "a", "status": "error", "reason": "KeyError: x"},
        {"rule": "b", "status": "skipped", "reason": "no doc"},
    ]


def test_coverage_with_no_runs_depends_on_partial() -> None:
    assert compute_coverage(make_ctx(check_runs=[]).case).ratio == 1.0
    assert compute_coverage(make_ctx(check_runs=[], partial=True).case).ratio == 0.0


def test_partial_reasons_and_flag() -> None:
    runs = [CheckRun(rule="boom", stage="rules", status="error", reason="x")]
    case, _ = scored(
        check_runs=runs,
        partial=True,
        partial_reasons=["budget_exceeded: max_calls (limit 2, used 2)"],
        stages=[StageRecord(stage="extract", status="failed")],
    )
    assert case.score and case.score.partial
    assert case.score.partial_reasons == [
        "budget_exceeded: max_calls (limit 2, used 2)",
        "stage_failed:extract",
        "check_error:boom",
    ]
    assert (
        case.top_finding
        == "No flags so far, analysis incomplete: budget_exceeded: max_calls (limit 2, used 2)"
    )
    bare = make_ctx(partial=True).case
    assert collect_partial_reasons(bare) == ["partial: reason not recorded"]
    assert collect_partial_reasons(make_ctx().case) == []


def test_run_stage_twice_does_not_duplicate() -> None:
    ctx = make_ctx([make_doc("invoice", id="d1")])
    ctx.case.flags = [flag("high")]
    run_stage(ctx)
    first = ctx.case.model_dump_json()
    run_stage(ctx)
    assert ctx.case.model_dump_json() == first


def test_lane_follows_use_case() -> None:
    for use_case, lane in (("kyc", "edd"), ("aml", "aml"), ("loan", "loan")):
        ctx = make_ctx(use_case=use_case)  # type: ignore[arg-type]
        ctx.case.flags = [flag("high")]  # score 30: not eligible for the fast lane
        run_stage(ctx)
        assert ctx.case.lane == lane


def test_no_weight_or_band_number_is_duplicated_in_score_py() -> None:
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "score.py").read_text())
    literals = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and not isinstance(n.value, bool) and n.value in (15, 30, 60)
    ]
    assert literals == []  # weights and band thresholds only come from core.models
