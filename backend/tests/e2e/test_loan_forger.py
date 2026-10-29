"""T-integration-loan-05: the careful forger is invisible to every single-document rule.

Every internal identity is kept consistent (subtotals, retained earnings, receivables, cash flow,
GST, running balances, clean metadata), so only the cross-document comparisons can see the lie.
"""

from __future__ import annotations

from tests.e2e._support import Run, unmet_expectations

CROSSDOC_MUST = {"x_revenue_vs_credits", "x_invoice_vs_receipts", "x_invoice_vs_contract"}


def test_forger_meets_the_manifest(loan_runs: dict[str, Run]) -> None:
    run = loan_runs["forger"]
    assert unmet_expectations(run.case, run.manifest["expected"]) == []


def test_forger_is_caught_by_cross_document_rules_only(loan_runs: dict[str, Run]) -> None:
    run = loan_runs["forger"]
    loud = {f.rule: f.stage for f in run.case.flags if f.severity != "info"}
    assert set(loud) >= CROSSDOC_MUST
    # every flag above info comes from the crossdoc stage: nothing single-document saw it
    assert {stage for stage in loud.values()} == {"crossdoc"}, loud


def test_forger_must_not_fire_list_is_silent_and_those_checks_ran(
    loan_runs: dict[str, Run],
) -> None:
    """Silence must come from checks that ran, not from checks that were skipped."""
    run = loan_runs["forger"]
    silent = run.manifest["expected"]["must_not_fire"]
    assert "fs_balance" in silent and "bs_running_balance" in silent
    # ``bs_benford`` is an info-level statistical hint (weight 0) and fires on the generator's
    # honest amounts too (clean control included): reported as a gap, not counted as a firing.
    fired = run.rules(min_severity="low")
    assert not (set(silent) & fired)
    assert {f.rule for f in loan_runs["clean"].case.flags if f.severity == "info"} <= {
        "bs_benford",
        "doc_unclassified",
    }
    ran = {r.rule for r in run.case.check_runs if r.status == "ran"}
    for rule in (
        "fs_subtotals",
        "fs_balance",
        "fs_comparatives",
        "bs_running_balance",
        "bs_totals",
    ):
        assert rule in ran, f"{rule} did not run, so its silence proves nothing"


def test_forger_lands_in_a_reviewable_band_with_the_loan_lane(loan_runs: dict[str, Run]) -> None:
    case = loan_runs["forger"].case
    assert case.lane == "loan"
    assert case.score is not None and case.score.band in {"medium", "high"}
    assert case.top_finding and "revenue" in case.top_finding.lower()


def test_forger_scores_below_fraud_but_above_clean(loan_runs: dict[str, Run]) -> None:
    clean = loan_runs["clean"].case.score
    forger = loan_runs["forger"].case.score
    fraud = loan_runs["fraud"].case.score
    assert clean and forger and fraud
    assert clean.value < forger.value <= fraud.value
