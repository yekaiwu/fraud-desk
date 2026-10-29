"""Lanes, page-level silence, the one-line reason and queue order (scoring-and-queue.md 4.5 to 4.9).

Tests 1, 10, 13 to 15, 19, 20, 22 and 23 of section 6 (test 21 belongs to api-backend).
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest

from app import queue as queue_mod
from app.checks.registry import all_checks, make_flag
from app.core.db import Database
from app.core.models import (
    Case,
    CheckRun,
    Classification,
    Coverage,
    FileRef,
    PageInfo,
    Score,
)
from app.core.testing import make_ctx, make_doc
from app.findings import (
    AML_ROUTING_RULES,
    FAMILIES,
    HARD_STOP_RULES,
    REGION_OVERLAP_MIN,
)
from app.queue import (
    FAST_LANE_MAX_SCORE,
    FAST_LANE_MIN_COVERAGE,
    TOP_FINDING_MAX_CHARS,
    effective_coverage,
    lane_for,
    page_counts,
    top_finding_line,
    unchecked_pages,
)
from app.score import run_stage
from tests.test_no_wallclock_in_checks import wallclock_calls

APP = Path(__file__).resolve().parents[1] / "app"


def fl(rule: str, docs: list[str], sev: str, *, disc: str | None = None, expl: str = "It is off."):
    return make_flag(
        rule,
        docs=docs,
        evidence="e",
        explanation=expl,
        severity=sev,  # type: ignore[arg-type]
        discriminator=disc,
    )


def runs(ran: int, other: int = 0, status: str = "skipped") -> list[CheckRun]:
    out = [CheckRun(rule=f"ran_{n:02d}", stage="rules", status="ran") for n in range(ran)]
    out += [
        CheckRun(rule=f"oth_{n:02d}", stage="rules", status=status, reason="why")  # type: ignore[arg-type]
        for n in range(other)
    ]
    return out


def page(n: int, *, label: str = "invoice", prob: float = 0.95, error: str | None = None):
    return PageInfo(
        page=n,
        width_pt=595,
        height_pt=842,
        has_text_layer=True,
        is_scan=False,
        classification=Classification(
            label=label, probability=prob, classifier="rules", version="1"
        ),
        error=error,
    )


def file_ref(fid: str, pages: list[PageInfo]) -> FileRef:
    return FileRef(
        id=fid,
        name=f"{fid}.pdf",
        sha256="0" * 64,
        size_bytes=10,
        mime="application/pdf",
        pages=pages,
    )


def scored(flags, check_runs, *, use_case="loan", docs=(), files=(), **kw) -> Case:  # noqa: ANN001, ANN003
    ctx = make_ctx(
        list(docs), use_case=use_case, check_runs=check_runs, files_extra=list(files), **kw
    )
    ctx.case.flags = list(flags)
    run_stage(ctx)
    return ctx.case


# ------------------------------------------------------------------------------- test 1 and 10


def test_clean_case_is_fast_with_coverage_in_the_sentence() -> None:
    case = scored([], runs(21))
    assert case.score and case.score.value == 0 and case.score.band == "low"
    assert case.findings == []
    assert case.lane == "fast"
    assert case.top_finding == "No flags raised; 21 of 21 checks ran"


def test_info_only_case_reads_as_informational_note() -> None:
    case = scored([fl("doc_unclassified", ["d1"], "info")], runs(21))
    assert case.score and case.score.value == 0
    assert case.top_finding == "No flags raised; 21 of 21 checks ran (1 informational note)"
    two = scored(
        [fl("doc_unclassified", ["d1"], "info"), fl("doc_unreadable", ["d2"], "info")], runs(3)
    )
    assert two.top_finding == "No flags raised; 3 of 3 checks ran (2 informational notes)"


# ----------------------------------------------------------------------------------- test 13


def test_hard_stop_blocks_the_fast_lane() -> None:
    case = scored([fl("pdf_editor_software", ["d1"], "medium")], runs(10))
    assert case.score and case.score.value == 15 <= FAST_LANE_MAX_SCORE
    assert case.findings[0].hard_stop is True
    assert case.lane == "loan"
    kyc = scored([fl("pdf_editor_software", ["d1"], "medium")], runs(10), use_case="kyc")
    assert kyc.lane == "edd"


def test_a_low_score_without_hard_stop_is_fast_but_only_a_label() -> None:
    case = scored([fl("fs_entity", ["d1"], "medium", disc="abc")], runs(10))
    assert case.score and case.score.value == 15
    assert not case.findings[0].hard_stop
    assert case.lane == "fast"


def test_score_above_threshold_is_not_fast() -> None:
    case = scored(
        [fl("fs_entity", ["d1"], "medium", disc="a"), fl("bs_dates", ["d2"], "medium")], runs(10)
    )
    assert case.score and case.score.value == 30 > FAST_LANE_MAX_SCORE
    assert case.lane == "loan"


# ----------------------------------------------------------------------------------- test 14


def test_low_check_coverage_is_not_fast() -> None:
    case = scored([], runs(3, 1))
    assert case.coverage and case.coverage.ratio == 0.75
    assert case.score and case.score.value == 0
    assert case.lane == "loan"


def test_unchecked_pages_pull_effective_coverage_below_the_gate() -> None:
    pages = [page(n) for n in range(1, 15)]
    # 3 of 14 pages unchecked: one error, one low probability, one labelled other
    pages[0] = page(1, error="unreadable")
    pages[1] = page(2, prob=0.5)
    pages[2] = page(3, label="other")
    case = scored(
        [],
        runs(19, 1),
        docs=[make_doc("invoice", id="d1", page_start=1, page_end=14)],
        files=[file_ref("f1", pages)],
    )
    assert case.coverage and case.coverage.ratio == 0.95
    assert (case.coverage.pages_total, case.coverage.pages_unchecked) == (14, 3)
    assert effective_coverage(case) == pytest.approx(0.95 * (1 - 3 / 14))
    assert effective_coverage(case) < FAST_LANE_MIN_COVERAGE
    assert case.lane == "loan" and case.score and case.score.partial  # unreadable page -> partial


def test_fully_read_pages_keep_the_fast_lane() -> None:
    case = scored(
        [],
        runs(20),
        docs=[make_doc("invoice", id="d1", page_start=1, page_end=3)],
        files=[file_ref("f1", [page(n) for n in (1, 2, 3)])],
    )
    assert case.coverage and (case.coverage.pages_total, case.coverage.pages_unchecked) == (3, 0)
    assert case.lane == "fast"


def test_page_counts_rules() -> None:
    ok = make_doc("invoice", id="d1", page_start=1, page_end=1)
    not_checked = make_doc(
        "other", id="d2", page_start=2, page_end=2, status="received_not_checked"
    )
    failed = make_doc("invoice", id="d3", page_start=3, page_end=3, status="failed")
    case = make_ctx(
        [ok, not_checked, failed],
        files_extra=[file_ref("f1", [page(n) for n in (1, 2, 3, 4)])],
    ).case
    # page 4 belongs to no document
    assert page_counts(case) == (4, 3)
    # a threshold above the page probability makes the good page unchecked too
    assert page_counts(case, min_prob=0.99) == (4, 4)
    assert page_counts(make_ctx().case) == (0, 0)
    assert effective_coverage(make_ctx().case) == 1.0


def test_unchecked_pages_say_which_page_and_why() -> None:
    ok = make_doc("invoice", id="d1", page_start=1, page_end=1)
    not_checked = make_doc(
        "other", id="d2", page_start=2, page_end=2, status="received_not_checked"
    )
    failed = make_doc("invoice", id="d3", page_start=3, page_end=3, status="failed")
    case = make_ctx(
        [ok, not_checked, failed],
        files_extra=[file_ref("f1", [page(n) for n in (1, 2, 3, 4)])],
    ).case
    listed = [(u.file_id, u.page, u.reason) for u in unchecked_pages(case)]
    assert listed == [
        ("f1", 2, "not_checked"),
        ("f1", 3, "failed"),
        ("f1", 4, "no_document"),
    ]
    assert len(listed) == page_counts(case)[1]


def test_unchecked_pages_name_low_confidence_and_unrecognised_pages() -> None:
    case = make_ctx(
        [make_doc("invoice", id="d1", page_start=1, page_end=2)],
        files_extra=[file_ref("f1", [page(1, label="boilerplate"), page(2)])],
    ).case
    assert [(u.page, u.reason) for u in unchecked_pages(case)] == [(1, "unrecognised_type")]
    assert [(u.page, u.reason) for u in unchecked_pages(case, min_prob=0.99)] == [
        (1, "unrecognised_type"),
        (2, "low_confidence"),
    ]


def test_page_without_classification_is_judged_by_its_document() -> None:
    bare = PageInfo(page=1, width_pt=1, height_pt=1, has_text_layer=True, is_scan=False)
    case = make_ctx([make_doc("invoice", id="d1")], files_extra=[file_ref("f1", [bare])]).case
    assert page_counts(case) == (1, 0)


def test_other_page_label_in_a_checked_document_is_unchecked() -> None:
    case = make_ctx(
        [make_doc("invoice", id="d1", page_start=1, page_end=2)],
        files_extra=[file_ref("f1", [page(1), page(2, label="boilerplate")])],
    ).case
    assert page_counts(case) == (2, 1)


# ----------------------------------------------------------------------------------- test 15


def test_remitter_flag_routes_a_kyc_case_to_aml() -> None:
    case = scored(
        [fl("x_remitter_vs_buyer", ["d1"], "high", disc="acme")], runs(10), use_case="kyc"
    )
    assert case.lane == "aml"
    loan = scored([fl("bs_pass_through", ["d1"], "medium")], runs(10), use_case="loan")
    assert loan.lane == "aml"


def test_kyc_entity_finding_stays_in_edd() -> None:
    case = scored(
        [fl("x_entity", ["d1"], "medium", disc="acme"), fl("bs_dates", ["d2"], "medium")],
        runs(10),
        use_case="kyc",
    )
    assert case.lane == "edd"
    assert scored([], runs(10), use_case="aml").lane == "fast"
    heavy = scored([fl("bs_totals", ["d1"], "high")], runs(10), use_case="aml")
    assert heavy.lane == "aml"


def test_zero_weight_aml_rule_does_not_route() -> None:
    case = scored([fl("x_sof_support", ["d1"], "info")], runs(10), use_case="kyc")
    assert case.lane == "fast"


# --------------------------------------------------------------------------------- lane guards


def test_partial_and_error_cases_are_never_fast() -> None:
    partial = scored([], runs(10), partial=True, partial_reasons=["budget_exceeded: x"])
    assert partial.score and partial.score.partial and partial.lane == "loan"
    case = scored([], runs(10))
    assert lane_for(case) == "fast"
    case.status = "error"
    assert lane_for(case) == "loan"


def test_lane_is_none_without_a_score() -> None:
    assert lane_for(make_ctx().case) is None
    assert top_finding_line(make_ctx().case) is None


# ----------------------------------------------------------------------------- top finding line


def test_top_line_for_partial_case_without_flags() -> None:
    case = scored(
        [], runs(1), partial=True, partial_reasons=["no_documents: nothing was classified"]
    )
    assert (
        case.top_finding
        == "No flags so far, analysis incomplete: no_documents: nothing was classified"
    )
    assert case.score and "≥" not in (case.top_finding or "")


def test_top_line_long_title_and_more_suffix() -> None:
    title_flag = fl("bs_totals", ["d1"], "high", expl="x" * 200)
    others = [fl("bs_dates", [f"d{n}"], "low") for n in (2, 3, 4)]
    case = scored([title_flag, *others], runs(5))
    line = case.top_finding
    assert line is not None and len(line) <= TOP_FINDING_MAX_CHARS
    assert line.endswith("(+3 more)")


def test_top_line_does_not_cut_at_a_decimal_point() -> None:
    expl = "Revenue 2.4M on the statements but only 0.9M of credits. More text."
    case = scored([fl("bs_totals", ["d1"], "high", expl=expl)], runs(5))
    assert case.top_finding == "Revenue 2.4M on the statements but only 0.9M of credits"


def test_top_line_ignores_info_findings_in_the_more_count() -> None:
    flags = [fl("bs_totals", ["d1"], "high"), fl("doc_unclassified", ["d2"], "info")]
    case = scored(flags, runs(5))
    assert case.top_finding == "It is off"


def test_top_line_never_exceeds_the_cap() -> None:
    case = scored([], runs(1), partial=True, partial_reasons=["r" * 400])
    assert case.top_finding is not None and len(case.top_finding) <= TOP_FINDING_MAX_CHARS


# ---------------------------------------------------------------------- constants (T-score-queue-07)


def test_recalibration_constants_and_starting_values() -> None:
    from app.core.models import BAND_THRESHOLDS, SCORE_WEIGHTS

    assert FAST_LANE_MAX_SCORE == 20 and FAST_LANE_MIN_COVERAGE == 0.80
    assert REGION_OVERLAP_MIN == 0.5 and TOP_FINDING_MAX_CHARS == 160
    assert SCORE_WEIGHTS == {"high": 30, "medium": 15, "low": 5, "info": 0}
    assert BAND_THRESHOLDS == {"medium": 30, "high": 60}


def test_queue_reexports_the_rule_sets() -> None:
    assert queue_mod.HARD_STOP_RULES is HARD_STOP_RULES
    assert queue_mod.AML_ROUTING_RULES is AML_ROUTING_RULES


# ----------------------------------------------------------------------------------- test 19


def test_families_reference_registered_rules() -> None:
    registered = {m.rule for m in all_checks()}
    named = set(HARD_STOP_RULES) | set(AML_ROUTING_RULES)
    for family in FAMILIES:
        named |= family.rules
    assert sorted(named - registered) == [], "renamed or removed rules need a Contract request"


# ----------------------------------------------------------------------------------- test 22


def row_case(cid: str, status: str, score: int | None, ratio: float | None, age_days: int) -> Case:
    case = make_ctx(id=cid, status=status).case  # type: ignore[arg-type]
    case.created_at = case.created_at - timedelta(days=age_days)
    if score is not None:
        case.score = Score(value=score, band="high" if score >= 60 else "low")
        case.coverage = Coverage(applicable=10, ran=int((ratio or 0) * 10), ratio=ratio or 0.0)
    return case


def test_priority_order_matches_the_frozen_sort_through_the_database() -> None:
    db = Database(":memory:")
    db.init_schema()
    cases = [
        row_case("c_open80_full", "open", 80, 1.0, 5),
        row_case("c_open80_low", "open", 80, 0.5, 1),
        row_case("c_open40", "open", 40, 0.9, 9),
        row_case("c_processing", "processing", None, None, 3),
        row_case("c_closed99", "closed", 99, 1.0, 2),
        row_case("c_awaiting90", "awaiting_documents", 90, 1.0, 4),
    ]
    for case in cases:
        db.create_case(case)
    rows, total = db.list_cases(None, "priority", 1, 25)
    assert total == 6
    assert [r["id"] for r in rows] == [
        "c_open80_low",
        "c_open80_full",
        "c_open40",
        "c_processing",
        "c_awaiting90",
        "c_closed99",
    ]


def test_priority_tie_break_is_age_then_id() -> None:
    db = Database(":memory:")
    db.init_schema()
    for case in (
        row_case("c_b", "open", 50, 0.8, 1),
        row_case("c_a", "open", 50, 0.8, 1),
        row_case("c_old", "open", 50, 0.8, 7),
    ):
        db.create_case(case)
    rows, _ = db.list_cases(None, "priority", 1, 25)
    assert [r["id"] for r in rows] == ["c_old", "c_a", "c_b"]


# ----------------------------------------------------------------------------------- test 23


@pytest.mark.parametrize("name", ["findings.py", "score.py", "queue.py"])
def test_no_wall_clock_in_scoring_modules(name: str) -> None:
    assert wallclock_calls((APP / name).read_text()) == []


def test_no_threshold_numbers_duplicated_outside_constants() -> None:
    tree = ast.parse((APP / "score.py").read_text())
    values = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)]
    assert not {15, 30, 60} & {v for v in values if isinstance(v, int) and not isinstance(v, bool)}
