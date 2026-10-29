"""Score, bands, coverage, partial reasons and the stage (scoring-and-queue.md tests 1, 8 to 12,
16, 17, 24)."""

from __future__ import annotations

import random

import pytest

from app.checks.registry import make_flag
from app.core.models import BAND_THRESHOLDS, SCORE_WEIGHTS, CheckRun, FileRef, PageInfo, StageRecord
from app.core.testing import make_ctx, make_doc
from app.score import (
    band_for,
    collect_partial_reasons,
    compute_coverage,
    compute_score,
    run_stage,
)


def fl(rule: str, docs: list[str], sev: str, *, disc: str | None = None, n: int = 0):
    return make_flag(
        rule,
        docs=docs,
        evidence=f"evidence {rule} {n}",
        explanation=f"Explanation of {rule} number {n}. Extra detail.",
        severity=sev,  # type: ignore[arg-type]
        discriminator=disc,
    )


def ran(count: int) -> list[CheckRun]:
    return [CheckRun(rule=f"r{n:02d}", stage="rules", status="ran") for n in range(count)]


def stage(flags, check_runs=None, *, docs=(), **kw):  # noqa: ANN001, ANN003, ANN201
    ctx = make_ctx(list(docs), check_runs=ran(10) if check_runs is None else check_runs, **kw)
    ctx.case.flags = list(flags)
    outcome = run_stage(ctx)
    return ctx, outcome


# ---------------------------------------------------------------------------- bands and weights


def distinct(sev: str, count: int):  # noqa: ANN201
    """``count`` unrelated findings of one severity (distinct documents, ungrouped rule)."""
    return [fl("bs_dates", [f"d{n}"], sev, n=n) for n in range(1, count + 1)]


def test_clean_case() -> None:
    ctx, outcome = stage([])
    case = ctx.case
    assert case.score and (case.score.value, case.score.band) == (0, "low")
    assert case.findings == [] and case.lane == "fast"
    assert case.top_finding == "No flags raised; 10 of 10 checks ran"
    assert outcome.detail == {"flags": 0, "findings": 0, "score": 0, "applicable": 10, "ran": 10}
    assert outcome.warnings == [] and outcome.partial is False


@pytest.mark.parametrize(
    ("severities", "value", "band"),
    [
        ([], 0, "low"),
        (["high"], 30, "medium"),
        (["high", "medium"], 45, "medium"),
        (["high", "high"], 60, "high"),
        (["medium"] * 4, 60, "high"),
        (["medium", "low", "low", "low"], 30, "medium"),
        (["high"] * 4, 100, "high"),
        (["high"] * 5 + ["medium"], 100, "high"),
    ],
)
def test_section_4_4_examples(severities: list[str], value: int, band: str) -> None:
    flags = [fl("bs_dates", [f"d{n}"], sev, n=n) for n, sev in enumerate(severities, start=1)]
    case = stage(flags)[0].case
    expected = min(100, sum(SCORE_WEIGHTS[s] for s in severities))
    assert expected == value  # the example table, derived from the constants
    assert case.score and (case.score.value, case.score.band) == (value, band)
    assert case.score.value == min(100, sum(f.weight for f in case.findings))


def test_cap_raw_120_is_100() -> None:
    ctx, _ = stage(distinct("high", 4))
    assert sum(f.weight for f in ctx.case.findings) == 120
    assert ctx.case.score and ctx.case.score.value == 100


def test_band_boundaries_built_from_the_constants() -> None:
    medium, high = BAND_THRESHOLDS["medium"], BAND_THRESHOLDS["high"]
    assert [band_for(v) for v in (medium - 1, medium, high - 1, high)] == [
        "low",
        "medium",
        "medium",
        "high",
    ]
    assert band_for(0) == "low" and band_for(100) == "high"


def test_same_cause_counts_once_in_the_score() -> None:
    flags = [fl("x_ownership_vs_registry", [f"d{n}"], "high", disc="lim", n=n) for n in (1, 2, 3)]
    ctx, _ = stage(flags)
    assert ctx.case.score and ctx.case.score.value == SCORE_WEIGHTS["high"]
    assert len(ctx.case.findings) == 1


def test_compute_score_directly() -> None:
    ctx, _ = stage(distinct("medium", 2))
    score = compute_score(ctx.case.findings, partial=True, reasons=["x"])
    assert (score.value, score.partial, score.partial_reasons) == (30, True, ["x"])
    assert compute_score([], partial=False, reasons=[]).value == 0


# ------------------------------------------------------------------------------------ coverage


def test_coverage_ratio_skips_sorted_and_rounded() -> None:
    check_runs = [
        CheckRun(rule="zeta", stage="rules", status="skipped", reason="no doc"),
        CheckRun(rule="alpha", stage="rules", status="error", reason="KeyError: x"),
        CheckRun(rule="mid", stage="rules", status="skipped"),
        *ran(1),
        *[CheckRun(rule=f"z{n}", stage="rules", status="ran") for n in range(3)],
    ]
    cov = stage([], check_runs)[0].case.coverage
    assert cov and (cov.applicable, cov.ran, cov.ratio) == (7, 4, round(4 / 7, 4))
    assert cov.skipped == [
        {"rule": "alpha", "status": "error", "reason": "KeyError: x"},
        {"rule": "mid", "status": "skipped", "reason": ""},
        {"rule": "zeta", "status": "skipped", "reason": "no doc"},
    ]


def test_empty_check_runs_depends_on_partial() -> None:
    assert compute_coverage(make_ctx(check_runs=[]).case).ratio == 1.0
    partial = compute_coverage(make_ctx(check_runs=[], partial=True).case)
    assert partial.ratio == 0.0 and partial.applicable == 0


def test_coverage_page_counts_come_from_the_files() -> None:
    pages = [
        PageInfo(page=n, width_pt=1, height_pt=1, has_text_layer=True, is_scan=False)
        for n in (1, 2)
    ]
    ref = FileRef(
        id="f1", name="a.pdf", sha256="0" * 64, size_bytes=1, mime="application/pdf", pages=pages
    )
    ctx, _ = stage(
        [], docs=[make_doc("invoice", id="d1", page_start=1, page_end=1)], files_extra=[ref]
    )
    cov = ctx.case.coverage
    assert cov and (cov.pages_total, cov.pages_unchecked) == (2, 1)  # page 2 is in no document


# ------------------------------------------------------------------------------------- partial


def test_partial_reasons_in_documented_order() -> None:
    ctx, _ = stage(
        [],
        [CheckRun(rule="boom", stage="rules", status="error", reason="ValueError: x")],
        docs=[
            make_doc("invoice", id="d1", status="failed"),
            make_doc("invoice", id="d2", status="partial"),
        ],
        stages=[
            StageRecord(stage="rules", status="failed"),
            StageRecord(stage="extract", status="failed"),
        ],
        partial=True,
        partial_reasons=["no_documents: x", "stage_failed:rules"],
    )
    score = ctx.case.score
    assert score and score.partial
    assert score.partial_reasons == [
        "no_documents: x",
        "stage_failed:rules",  # de-duplicated, first occurrence kept
        "stage_failed:extract",
        "check_error:boom",
        "document_failed:d1",
        "document_partial:d2",
    ]
    assert score.band == "low" and score.value == 0  # a floor, band label unchanged
    assert ctx.case.lane != "fast"
    assert ctx.case.top_finding and ctx.case.top_finding.startswith("No flags so far, analysis")


def test_test_11_failed_stage_and_errored_check() -> None:
    ctx, _ = stage(
        [],
        [CheckRun(rule="bs_totals", stage="rules", status="error", reason="boom")],
        stages=[StageRecord(stage="rules", status="failed")],
    )
    assert ctx.case.score and ctx.case.score.partial
    assert ctx.case.score.partial_reasons == ["stage_failed:rules", "check_error:bs_totals"]
    assert ctx.case.lane != "fast"


def test_unreadable_pages_make_the_score_partial() -> None:
    pages = [
        PageInfo(page=1, width_pt=1, height_pt=1, has_text_layer=False, is_scan=True, error="bad"),
        PageInfo(page=2, width_pt=1, height_pt=1, has_text_layer=False, is_scan=True, error="bad"),
    ]
    ref = FileRef(
        id="f1", name="a.pdf", sha256="0" * 64, size_bytes=1, mime="application/pdf", pages=pages
    )
    case = make_ctx(files_extra=[ref]).case
    assert collect_partial_reasons(case) == ["pages_unreadable:2"]


def test_partial_reasons_are_capped_at_twenty() -> None:
    case = make_ctx(partial=True, partial_reasons=[f"reason_{n}" for n in range(30)]).case
    assert len(collect_partial_reasons(case)) == 20


def test_partial_without_recorded_reason() -> None:
    assert collect_partial_reasons(make_ctx(partial=True).case) == ["partial: reason not recorded"]
    assert collect_partial_reasons(make_ctx().case) == []


def test_clean_case_is_not_partial() -> None:
    case = stage([])[0].case
    assert case.score and not case.score.partial and case.score.partial_reasons == []


# ------------------------------------------------------------------ determinism and idempotence


def mixed_flags():  # noqa: ANN201
    return [
        fl("x_ownership_vs_registry", ["d1"], "high", disc="lim", n=1),
        fl("x_ownership_vs_registry", ["d2"], "high", disc="lim", n=2),
        fl("bs_totals", ["d3"], "high", n=3),
        fl("bs_running_balance", ["d3"], "high", n=4),
        fl("x_invoice_vs_receipts", ["d4"], "medium", n=5),
        fl("x_invoice_vs_receipts", ["d5"], "medium", n=6),
        fl("pdf_modified", ["d1"], "low", n=7),
        fl("doc_unclassified", ["d6"], "info", n=8),
    ]


def outputs(case):  # noqa: ANN001, ANN201
    return (
        [f.model_dump() for f in case.findings],
        case.score.model_dump() if case.score else None,
        case.coverage.model_dump() if case.coverage else None,
        case.lane,
        case.top_finding,
    )


def test_determinism_under_shuffling() -> None:
    base = outputs(stage(mixed_flags())[0].case)
    rng = random.Random(4002)
    for _ in range(20):
        flags = mixed_flags()
        rng.shuffle(flags)
        assert outputs(stage(flags)[0].case) == base


def test_run_stage_is_idempotent() -> None:
    ctx, _ = stage(mixed_flags())
    first = ctx.case.model_dump_json()
    run_stage(ctx)
    run_stage(ctx)
    assert ctx.case.model_dump_json() == first
    assert len({f.id for f in ctx.case.findings}) == len(ctx.case.findings)


def test_run_stage_writes_only_its_outputs() -> None:
    ctx = make_ctx([make_doc("invoice", id="d1")], check_runs=ran(2))
    ctx.case.flags = mixed_flags()
    before = ctx.case.model_copy(deep=True)
    run_stage(ctx)
    after = ctx.case
    owned = {"findings", "score", "coverage", "lane", "top_finding"}
    for name in type(after).model_fields:
        if name not in owned:
            assert getattr(after, name) == getattr(before, name), name


def test_unknown_document_ids_only_warn() -> None:
    ctx, outcome = stage([fl("bs_totals", ["d99"], "high")], docs=[make_doc("invoice", id="d1")])
    assert ctx.case.score and ctx.case.score.value == 30
    assert outcome.warnings == ["flag bs_totals.d99.- refers to unknown document d99"]


def test_settings_min_prob_is_read_from_the_context() -> None:
    from app.core.config import Settings

    pages = [PageInfo(page=1, width_pt=1, height_pt=1, has_text_layer=True, is_scan=False)]
    from app.core.models import Classification

    pages[0].classification = Classification(
        label="invoice", probability=0.8, classifier="r", version="1"
    )
    ref = FileRef(
        id="f1", name="a.pdf", sha256="0" * 64, size_bytes=1, mime="application/pdf", pages=pages
    )
    docs = [make_doc("invoice", id="d1")]
    lenient = make_ctx(docs, check_runs=ran(5), files_extra=[ref])
    run_stage(lenient)
    assert lenient.case.lane == "fast"
    strict = make_ctx(
        docs,
        check_runs=ran(5),
        files_extra=[ref],
        settings=Settings(
            _env_file=None, db_path=":memory:", llm_backend="fake", classifier_min_prob=0.9
        ),
    )
    run_stage(strict)
    assert strict.case.coverage and strict.case.coverage.pages_unchecked == 1
    assert strict.case.lane == "loan"


# ----------------------------------------------------------------------------- honest quirk 24


def test_careful_forger_scores_zero_and_can_be_fast() -> None:
    """A forger whose edits are arithmetically consistent trips no rule. The score is 0 and the
    case may take the fast lane. This is a miss the eval reports (plan 9.4); a person still
    confirms every fast-lane case."""
    ctx, _ = stage([], ran(21), docs=[make_doc("bank_statement", id="d1")])
    case = ctx.case
    assert case.score and case.score.value == 0 and case.score.band == "low"
    assert case.lane == "fast"
    assert case.top_finding == "No flags raised; 21 of 21 checks ran"
    assert case.decision is None  # nothing is decided by the lane
