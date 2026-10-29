"""T-integration-loan-04: the deck fraud example end to end, then an analyst decides via the API."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.main as main
from tests.e2e._support import Run, run_bundle, temp_root, text_under, unmet_expectations

FS_RULES = {"fs_balance", "fs_subtotals", "fs_cash_tie", "fs_comparatives"}


def test_fraud_meets_every_must_fire_entry(loan_runs: dict[str, Run]) -> None:
    run = loan_runs["fraud"]
    assert unmet_expectations(run.case, run.manifest["expected"]) == []
    rules = run.rules()
    assert {"x_revenue_vs_credits", "x_invoice_vs_receipts", "x_invoice_vs_contract"} <= rules
    assert FS_RULES & run.rules(min_severity="high")  # fraud-only slip: fs_subtotals
    assert "x_cash_at_year_end" not in rules  # must_not_fire


def test_fraud_lane_band_and_score(loan_runs: dict[str, Run]) -> None:
    case = loan_runs["fraud"].case
    assert case.lane == "loan"
    assert case.score is not None and case.score.band in {"medium", "high"}
    assert case.score.value >= 60  # high flag + several medium findings (weights 30 + 15 each)
    assert case.partial is False and case.status == "open"


def test_each_planted_finding_is_its_own_finding(loan_runs: dict[str, Run]) -> None:
    case = loan_runs["fraud"].case
    by_rule: dict[str, list] = {}
    for fnd in case.findings:
        for rule in fnd.rules:
            by_rule.setdefault(rule, []).append(fnd)
    assert {"x_revenue_vs_credits", "x_invoice_vs_receipts", "fs_subtotals"} <= set(by_rule)
    # the three over-billed invoices are three separate findings, none of them merged with revenue
    contract = {f.id for f in by_rule["x_invoice_vs_contract"]}
    assert len(contract) == 3
    revenue = {f.id for f in by_rule["x_revenue_vs_credits"]}
    assert revenue.isdisjoint(contract) and revenue.isdisjoint(
        {f.id for f in by_rule["fs_subtotals"]}
    )
    # every flag above info belongs to exactly one finding
    in_findings = [fid for fnd in case.findings for fid in fnd.flag_ids]
    loud = {f.id for f in case.flags if f.severity != "info"}
    assert loud <= set(in_findings) and len(in_findings) == len(set(in_findings))


def test_top_finding_is_the_highest_ranked_planted_finding(loan_runs: dict[str, Run]) -> None:
    case = loan_runs["fraud"].case
    assert case.top_finding
    top = max(case.findings, key=lambda f: (f.severity == "high", f.weight))
    # the printed gross profit slip (high) outranks the revenue shortfall (medium) in fraud
    assert top.severity == "high" and "fs_subtotals" in top.rules
    assert case.top_finding.startswith(top.title.split(" (")[0][:40])
    revenue = [f for f in case.findings if "x_revenue_vs_credits" in f.rules]
    assert revenue and "revenue" in revenue[0].summary.lower()


def test_fraud_flags_point_at_the_edited_cells(loan_runs: dict[str, Run]) -> None:
    """Localisation: the fs_subtotals flag sits on the income statement subtotal row."""
    run = loan_runs["fraud"]
    flag = run.flags("fs_subtotals")[0]
    assert flag.table == "income_statement" and flag.region is not None
    doc = next(d for d in run.case.documents if d.id == flag.document_ids[0])
    assert doc.doc_type == "financial_statement"
    row = doc.tables["income_statement"].rows[flag.row]
    assert row.cells["line"].value == "gross_profit"
    # the highlight sits on the printed amount of that row in the original PDF
    printed = text_under(run, flag.region)
    assert printed and printed in {c.raw for c in row.cells.values() if c.raw}
    assert 0.0 <= flag.region.x <= 1.0 and 0.0 <= flag.region.y <= 1.0


def test_scripted_analyst_confirms_high_flags_and_rejects_through_the_api() -> None:
    from tests.e2e._support import build_bundles

    root = temp_root("gldb-e2e-fraud-api-")
    bdir = build_bundles(root, variants=("fraud",))["fraud"]
    run = run_bundle(bdir, tmp=root)
    application = main.create_app(run.settings, db=run.db, llm=run.llm, registry=run.registry)
    client = TestClient(application, raise_server_exceptions=False)
    case_id = run.case.id
    view = client.get(f"/cases/{case_id}").json()
    assert view["status"] == "open" and "reject_or_escalate" in view["allowed_verdicts"]

    high = [f["id"] for f in view["flags"] if f["severity"] == "high"]
    assert high
    r = client.post(
        f"/cases/{case_id}/review",
        json={
            "kind": "flag_decision",
            "decision": "confirm",
            "flag_ids": high,
            "reviewer": "a.tan",
            "reason": "Printed gross profit does not equal its lines.",
            "case_version": view["version"],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/cases/{case_id}/review",
        json={
            "kind": "verdict",
            "decision": "reject_or_escalate",
            "reviewer": "a.tan",
            "reason": "Revenue overstated against bank credits; invoices unpaid.",
            "case_version": view["version"],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["case"]["status"] == "closed"
    closed = client.get(f"/cases/{case_id}").json()
    assert closed["status"] == "closed" and closed["decision"] == "reject_or_escalate"
    # analysis is immutable: the score did not move
    assert closed["score"]["value"] == view["score"]["value"]
