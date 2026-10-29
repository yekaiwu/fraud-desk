"""T-integration-loan-03: the clean control is silent (plan 0.5, 9.3 false alarms)."""

from __future__ import annotations

from tests.e2e._support import SEVERITY_RANK, Run, unmet_expectations


def test_clean_loan_has_no_flag_above_info(loan_runs: dict[str, Run]) -> None:
    run = loan_runs["clean"]
    loud = [(f.rule, f.severity, f.id) for f in run.case.flags if f.severity != "info"]
    assert loud == []
    assert run.case.score is not None and run.case.score.value == 0
    assert run.case.score.band == "low"
    assert not [f for f in run.case.findings if f.severity != "info"]


def test_clean_loan_meets_its_manifest(loan_runs: dict[str, Run]) -> None:
    run = loan_runs["clean"]
    assert unmet_expectations(run.case, run.manifest["expected"]) == []
    assert run.case.lane in {"fast", "loan"}  # both allowed by the manifest


def test_clean_loan_is_complete_and_reports_coverage(loan_runs: dict[str, Run]) -> None:
    run = loan_runs["clean"]
    case = run.case
    assert case.status == "open" and case.partial is False
    assert case.score is not None and case.score.partial is False
    assert [s.stage for s in case.stages] == [
        "ingest",
        "classify",
        "extract",
        "forensics",
        "rules",
        "crossdoc",
        "score",
    ]
    assert all(s.status == "ok" for s in case.stages)
    assert not [r for r in case.check_runs if r.status == "error"]
    cov = case.coverage
    assert cov is not None and cov.applicable > 0 and 0.0 < cov.ratio <= 1.0
    assert cov.ran == sum(1 for r in case.check_runs if r.status == "ran")
    # every skipped check states why (informs CR-28 / FAST_LANE_MIN_COVERAGE)
    skipped = [r for r in case.check_runs if r.status == "skipped"]
    assert skipped and all(r.reason for r in skipped)
    assert {s["rule"] for s in cov.skipped} == {r.rule for r in skipped}


def test_clean_loan_uses_no_model_and_costs_nothing(loan_runs: dict[str, Run]) -> None:
    run = loan_runs["clean"]
    assert run.llm.calls == []  # lever 2: the text layer sufficed
    assert run.case.costs == []
    assert all(s.cost_usd == 0 for s in run.case.stages)


def test_clean_loan_extracted_everything_it_has_an_extractor_for(loan_runs: dict[str, Run]) -> None:
    run = loan_runs["clean"]
    by_type: dict[str, int] = {}
    for d in run.case.documents:
        by_type[d.doc_type] = by_type.get(d.doc_type, 0) + 1
        if d.doc_type in {"bank_statement", "invoice", "financial_statement", "contract"}:
            assert d.status == "ok", (d.id, d.doc_type, d.status, d.warnings)
    assert by_type["bank_statement"] == 6 and by_type["financial_statement"] == 2
    assert by_type["invoice"] == 14 and by_type["contract"] == 3
    assert by_type["purchase_order"] == 4


def test_clean_loan_applicant_is_resolved_from_the_documents(loan_runs: dict[str, Run]) -> None:
    run = loan_runs["clean"]
    expected = run.manifest["applicant"]
    assert run.case.applicant.uen == expected["uen"]
    assert run.case.applicant.name == expected["name"]
    assert run.case.applicant.source == "derived"
    assert SEVERITY_RANK["info"] == 0
