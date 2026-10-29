"""T-integration-kyc-02: the AML bundles end to end (plan 0.5 AML row).

aml-clean stays quiet (including its honest quirks: an instalment payment, a combined receipt, fees,
a blank invoice page); aml-fraud meets ``x_sof_support``, ``x_remitter_vs_buyer`` and
``bs_pass_through`` plus the ``inv_gst`` slip and routes to the AML lane; aml-forger keeps the three
cross-document signals and silences every single-document rule.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

import app.main as main
from tests.e2e._support import Run, build_bundles, run_bundle, temp_root, unmet_expectations

CORE = {"x_sof_support", "x_remitter_vs_buyer", "bs_pass_through"}


@pytest.fixture(scope="module")
def aml_runs():
    root = temp_root("gldb-e2e-aml-")
    bundles = build_bundles(root, scenario="aml")
    runs = {v: run_bundle(b, tmp=root / v) for v, b in bundles.items()}
    yield runs
    shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------------------------ clean


def test_clean_meets_the_manifest_and_is_silent(aml_runs: dict[str, Run]) -> None:
    run = aml_runs["clean"]
    assert unmet_expectations(run.case, run.manifest["expected"]) == []
    assert run.rules(min_severity="low") == set()
    assert {f.rule for f in run.case.flags} <= {"doc_unclassified", "bs_benford"}
    assert run.case.score is not None and run.case.score.value == 0
    assert run.llm.calls == []
    assert run.case.lane == "aml"


def test_clean_instalment_payment_is_matched_and_not_called_unpaid(
    aml_runs: dict[str, Run],
) -> None:
    """Seam fix: an invoice paid in two instalments ('GIRO CREDIT <buyer> SI-2026-0109') has both
    parts attributed to its buyer, so ``x_invoice_vs_receipts`` stays silent (honest quirk Q04)."""
    run = aml_runs["clean"]
    assert not run.flags("x_invoice_vs_receipts") or all(
        f.severity == "info" for f in run.flags("x_invoice_vs_receipts")
    )
    statements = [d for d in run.case.documents if d.doc_type == "bank_statement"]
    parts = []
    for st in statements:
        for row in st.tables["transactions"].rows:
            if (
                "SI-2026-0109" in (row.cells["description"].text() or "")
                and row.cells["credit"].money()
            ):
                parts.append(row)
    assert len(parts) == 2
    for row in parts:
        cp = row.annotations["counterparty"]
        assert cp.label == "CRESTVIEW EXPORTS PTE LTD"  # rail and invoice number stripped


def test_clean_silent_checks_ran(aml_runs: dict[str, Run]) -> None:
    ran = {r.rule for r in aml_runs["clean"].case.check_runs if r.status == "ran"}
    assert CORE | {"x_invoice_vs_receipts", "inv_gst", "bs_running_balance"} <= ran


# ------------------------------------------------------------------------------------ fraud


def test_fraud_meets_every_must_fire_entry(aml_runs: dict[str, Run]) -> None:
    run = aml_runs["fraud"]
    assert unmet_expectations(run.case, run.manifest["expected"]) == []
    severities = {f.rule: f.severity for f in run.case.flags if f.severity != "info"}
    assert severities["x_sof_support"] == "high"
    assert severities["x_remitter_vs_buyer"] == "high"
    assert severities["bs_pass_through"] in {"medium", "high"}
    assert "inv_gst" in severities  # the fraud-only slip a single-document rule can see
    assert "x_cash_at_year_end" not in severities


def test_fraud_lane_band_and_score(aml_runs: dict[str, Run]) -> None:
    case = aml_runs["fraud"].case
    assert case.lane == "aml"
    assert case.score is not None and case.score.band == "high"
    assert case.score.value >= 75
    assert case.partial is False and case.status == "open"


def test_fraud_flags_tell_the_story(aml_runs: dict[str, Run]) -> None:
    run = aml_runs["fraud"]
    sof = run.flags("x_sof_support")[0]
    assert "1000000.00" in sof.explanation and "unsupported" in sof.explanation
    assert "0 supporting credits" in sof.evidence and "2026-07-01..2026-08-31" in sof.evidence
    remit = run.flags("x_remitter_vs_buyer")[0]
    assert "Orchid Bay" in remit.explanation
    # the three planted remitters appear, the buyer never does
    for name in ("VANTAGE CREST", "SILVERLINE", "PACIFIC ANCHOR"):
        assert name in remit.evidence.upper(), name
    through = run.flags("bs_pass_through")[0]
    assert "3 credits" in through.explanation and "5 days" in through.explanation
    for flag in (sof, remit, through):
        assert flag.region is not None, flag.rule


def test_fraud_findings_are_separate_and_the_top_one_is_the_cross_document_story(
    aml_runs: dict[str, Run],
) -> None:
    case = aml_runs["fraud"].case
    rules = [set(f.rules) for f in case.findings if f.severity != "info"]
    assert {"x_sof_support"} in rules and {"x_remitter_vs_buyer"} in rules
    assert {"bs_pass_through"} in rules and {"inv_gst"} in rules
    assert case.top_finding and "Orchid Bay" in case.top_finding
    assert "Pte. Ltd." in " ".join(f.title for f in case.findings) or "Pte" in case.top_finding


# ------------------------------------------------------------------------------------ forger


def test_forger_meets_the_manifest(aml_runs: dict[str, Run]) -> None:
    run = aml_runs["forger"]
    assert unmet_expectations(run.case, run.manifest["expected"]) == []
    assert run.case.lane == "aml" and run.case.score is not None
    assert run.case.score.band == "high" and run.case.score.value == 75


def test_forger_keeps_the_three_and_silences_single_document_rules(
    aml_runs: dict[str, Run],
) -> None:
    run = aml_runs["forger"]
    loud = {f.rule for f in run.case.flags if f.severity != "info"}
    assert loud == CORE
    silent = run.manifest["expected"]["must_not_fire"]
    assert not (set(silent) & run.rules(min_severity="low"))
    ran = {r.rule for r in run.case.check_runs if r.status == "ran"}
    for rule in ("inv_gst", "inv_total", "bs_running_balance", "bs_totals", "bs_period_continuity"):
        assert rule in ran, f"{rule} did not run, so its silence proves nothing"
    assert any(r.startswith("pdf_") for r in ran)
    assert not run.flags("inv_gst")


def test_forger_scores_between_clean_and_fraud(aml_runs: dict[str, Run]) -> None:
    clean, forger, fraud = (aml_runs[v].case.score for v in ("clean", "forger", "fraud"))
    assert clean and forger and fraud
    assert clean.value < forger.value < fraud.value


# ------------------------------------------------------------------------------------ verdict


def test_scripted_analyst_escalates_to_aml_investigation_through_the_api() -> None:
    root = temp_root("gldb-e2e-aml-api-")
    bdir = build_bundles(root, scenario="aml", variants=("fraud",))["fraud"]
    run = run_bundle(bdir, tmp=root)
    application = main.create_app(run.settings, db=run.db, llm=run.llm, registry=run.registry)
    client = TestClient(application, raise_server_exceptions=False)
    case_id = run.case.id
    view = client.get(f"/cases/{case_id}").json()
    assert view["status"] == "open" and view["lane"] == "aml"
    assert "escalate_aml" in view["allowed_verdicts"]
    assert "refer_edd" not in view["allowed_verdicts"]  # a KYC-only verdict
    assert view["escalated_at"] is None

    high = [f["id"] for f in view["flags"] if f["severity"] == "high"]
    assert len(high) >= 2
    r = client.post(
        f"/cases/{case_id}/review",
        json={
            "kind": "flag_decision",
            "decision": "confirm",
            "flag_ids": high,
            "reviewer": "a.tan",
            "reason": "Credits do not come from the declared payer and are passed straight on.",
            "case_version": view["version"],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/cases/{case_id}/review",
        json={
            "kind": "verdict",
            "decision": "escalate_aml",
            "reviewer": "a.tan",
            "reason": "Pass-through of the declared proceeds; escalate for STR assessment.",
            "case_version": view["version"],
        },
    )
    assert r.status_code == 201, r.text
    closed = client.get(f"/cases/{case_id}").json()
    assert closed["status"] == "closed" and closed["decision"] == "escalate_aml"
    assert closed["escalated_at"] is not None  # the 'suspicion formed' time the pack reports
    assert closed["score"]["value"] == view["score"]["value"]
    pack = client.get(f"/cases/{case_id}/pack").json()
    assert pack["escalation"]["escalated_at"] == closed["escalated_at"]
