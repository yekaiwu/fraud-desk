"""T-integration-kyc-01: the KYC bundles end to end (plan 0.5 KYC row).

kyc-clean stays quiet; kyc-fraud meets its ``must_fire`` list and routes to EDD; kyc-forger (every
registry-independent identity kept consistent) is caught by the three cross-document rules only.
Everything runs through the real pipeline offline with the Fake LLM; ``today`` is passed in.
"""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

import app.main as main
from tests.e2e._support import (
    Run,
    build_bundles,
    run_bundle,
    temp_root,
    text_under,
    unmet_expectations,
)

CORE = {"x_declared_vs_activity", "x_ownership_vs_registry", "x_directors"}


@pytest.fixture(scope="module")
def kyc_runs():
    root = temp_root("gldb-e2e-kyc-")
    bundles = build_bundles(root, scenario="kyc")
    runs = {v: run_bundle(b, tmp=root / v) for v, b in bundles.items()}
    yield runs
    shutil.rmtree(root, ignore_errors=True)


def _loud(run: Run) -> set[str]:
    return run.rules(min_severity="low")


# ------------------------------------------------------------------------------------ clean


def test_clean_meets_the_manifest_and_is_silent(kyc_runs: dict[str, Run]) -> None:
    run = kyc_runs["clean"]
    assert unmet_expectations(run.case, run.manifest["expected"]) == []
    assert _loud(run) == set()
    # only informational notes (the bank's boilerplate T&C pages) may appear
    assert {f.rule for f in run.case.flags} <= {"doc_unclassified", "bs_benford"}
    assert run.case.score is not None and run.case.score.value == 0
    assert run.case.score.band == "low"


def test_clean_documents_are_all_recognised_with_confidence(kyc_runs: dict[str, Run]) -> None:
    """The seam fix: every KYC page type reaches the 0.70 threshold with the rules classifier, so
    the clean control carries no spurious 'not confidently recognised' note on a checked document."""
    case = kyc_runs["clean"].case
    kinds = {d.doc_type for d in case.documents}
    assert {
        "company_profile",
        "certificate_of_incorporation",
        "ownership_chart",
        "shareholder_register",
        "national_id",
        "passport",
        "bank_statement",
    } <= kinds
    for d in case.documents:
        if d.status == "ok":
            assert d.classification.probability >= 0.70, (d.id, d.doc_type)
    unclassified = {d for f in case.flags if f.rule == "doc_unclassified" for d in f.document_ids}
    assert {d.doc_type for d in case.documents if d.id in unclassified} == {"boilerplate"}


def test_clean_uses_no_model_and_the_silent_checks_actually_ran(
    kyc_runs: dict[str, Run],
) -> None:
    run = kyc_runs["clean"]
    assert run.llm.calls == []
    ran = {r.rule for r in run.case.check_runs if r.status == "ran"}
    # silence has to come from checks that ran, not from checks that were skipped
    assert (
        CORE | {"kyc_shareholdings", "id_format", "id_checksum", "passport_mrz", "date_valid"}
        <= ran
    )
    assert run.case.lane == "edd"  # KYC bundles always go to the EDD lane family, never fast


def test_clean_name_surface_forms_do_not_trip_the_name_rules(kyc_runs: dict[str, Run]) -> None:
    """Q11: the registry says ``Tan Wei Ming``, the documents print ``TAN WEI MING`` and the
    patronymic in short form; the name-matching fix keeps x_directors / x_ownership silent."""
    run = kyc_runs["clean"]
    assert not run.flags("x_directors") and not run.flags("x_ownership_vs_registry")
    assert not run.flags("x_entity")


# ------------------------------------------------------------------------------------ fraud


def test_fraud_meets_every_must_fire_entry(kyc_runs: dict[str, Run]) -> None:
    run = kyc_runs["fraud"]
    assert unmet_expectations(run.case, run.manifest["expected"]) == []
    severities = {f.rule: f.severity for f in run.case.flags if f.severity != "info"}
    assert set(severities) >= CORE
    assert severities["x_ownership_vs_registry"] == "high"
    assert severities["x_directors"] == "high"
    assert severities["x_declared_vs_activity"] in {"medium", "high"}
    # the slip a single-document rule can see: the chart's holders sum to 75%
    assert "kyc_shareholdings" in severities


def test_fraud_lane_band_and_score(kyc_runs: dict[str, Run]) -> None:
    case = kyc_runs["fraud"].case
    assert case.lane == "edd"
    # the chart's 75% sum and the registry holder gap are one finding (findings.py groups the
    # kyc_shareholdings slip with x_ownership_vs_registry), so 30 + 30 + 15 = 75, not 100
    assert case.score is not None and case.score.band == "high" and case.score.value == 75
    assert case.partial is False and case.status == "open"
    # the two 30-point findings tie; either may lead the queue line, the planted director stays visible
    assert case.top_finding
    assert any("Koh Boon Kiat" in f.title for f in case.findings)


def test_fraud_flags_explain_themselves_and_point_at_the_page(kyc_runs: dict[str, Run]) -> None:
    run = kyc_runs["fraud"]
    activity = run.flags("x_declared_vs_activity")[0]
    assert "crypto" in activity.explanation.lower() and "logistics" in activity.evidence
    assert "39.8%" in activity.explanation or "%" in activity.explanation
    director = run.flags("x_directors")[0]
    assert "Koh Boon Kiat" in director.explanation
    assert "S****591F" in director.evidence  # the ID is masked in evidence
    assert "S1101" not in director.evidence and "591F" in director.evidence
    chart = run.flags("kyc_shareholdings")[0]
    assert "75.00%" in chart.explanation
    owner = run.flags("x_ownership_vs_registry")[0]
    assert "Kestrel Capital Holdings Pte. Ltd." in owner.explanation
    for flag in (activity, director, chart, owner):
        assert flag.region is not None, flag.rule
        assert 0.0 <= flag.region.x <= 1.0 and 0.0 <= flag.region.y <= 1.0


def test_fraud_director_flag_lands_on_the_planted_row(kyc_runs: dict[str, Run]) -> None:
    run = kyc_runs["fraud"]
    flag = run.flags("x_directors")[0]
    printed = text_under(run, flag.region)
    assert "Koh" in printed or "Boon" in printed or "Kiat" in printed, printed


def test_finding_titles_keep_a_company_name_whole(kyc_runs: dict[str, Run]) -> None:
    """Seam fix: 'Pte. Ltd.' is not a sentence end, so the finding title is not cut at 'Pte'."""
    titles = " | ".join(f.title for f in kyc_runs["forger"].case.findings)
    assert "Kestrel Capital Holdings Pte. Ltd. holds 25.00%" in titles
    assert "Pte (" not in titles and not any(
        f.title.endswith("Pte") for f in kyc_runs["forger"].case.findings
    )


def test_each_planted_fraud_is_its_own_finding(kyc_runs: dict[str, Run]) -> None:
    case = kyc_runs["fraud"].case
    by_rule = {}
    for fnd in case.findings:
        for rule in fnd.rules:
            by_rule.setdefault(rule, []).append(fnd)
    assert CORE | {"kyc_shareholdings"} <= set(by_rule)
    # the declared-vs-activity finding is one finding over the three statements, not three
    assert len(by_rule["x_declared_vs_activity"]) == 1
    assert len(by_rule["x_declared_vs_activity"][0].document_ids) == 3
    loud = {f.id for f in case.flags if f.severity != "info"}
    in_findings = [fid for fnd in case.findings for fid in fnd.flag_ids]
    assert loud <= set(in_findings) and len(in_findings) == len(set(in_findings))


# ------------------------------------------------------------------------------------ forger


def test_forger_meets_the_manifest(kyc_runs: dict[str, Run]) -> None:
    run = kyc_runs["forger"]
    assert unmet_expectations(run.case, run.manifest["expected"]) == []
    assert run.case.lane == "edd" and run.case.score is not None
    assert run.case.score.band == "high"


def test_forger_is_caught_by_the_cross_document_rules_only(kyc_runs: dict[str, Run]) -> None:
    run = kyc_runs["forger"]
    loud = {f.rule: f.stage for f in run.case.flags if f.severity != "info"}
    assert set(loud) == CORE
    assert set(loud.values()) == {"crossdoc"}, loud


def test_forger_silence_comes_from_checks_that_ran(kyc_runs: dict[str, Run]) -> None:
    run = kyc_runs["forger"]
    silent = run.manifest["expected"]["must_not_fire"]
    assert "kyc_shareholdings" in silent and "id_checksum" in silent and "pdf_modified" in silent
    # bs_benford is an info-level statistical hint (weight 0) that fires on the generator's amounts
    # in the clean control too; it is reported as a gap, never counted as a firing
    assert not (set(silent) & run.rules(min_severity="low"))
    ran = {r.rule for r in run.case.check_runs if r.status == "ran"}
    for rule in ("kyc_shareholdings", "id_format", "id_checksum", "passport_mrz", "date_valid"):
        assert rule in ran, f"{rule} did not run, so its silence proves nothing"
    assert any(r.startswith("pdf_") for r in ran) and any(r.startswith("bs_") for r in ran)


def test_forger_chart_and_register_both_disagree_with_the_registry(
    kyc_runs: dict[str, Run],
) -> None:
    flags = kyc_runs["forger"].flags("x_ownership_vs_registry")
    docs = {d for f in flags for d in f.document_ids}
    assert len(docs) == 2  # the chart and the register, which the forger made agree
    text = " ".join(f.evidence for f in flags)
    assert "TAN WEI MING" in text and "60.00%" in text and "45.00%" in text
    assert "Kestrel" in text


def test_forger_and_fraud_score_high_and_clean_scores_low(kyc_runs: dict[str, Run]) -> None:
    """The order between forger and fraud is not asserted: the forger has three separate holder
    mismatches (three findings) while the fraud's chart slip and holder gap share one finding."""
    clean, forger, fraud = (kyc_runs[v].case.score for v in ("clean", "forger", "fraud"))
    assert clean and forger and fraud
    assert clean.value < forger.value and clean.value < fraud.value
    assert forger.band == "high" and fraud.band == "high" and clean.band == "low"


# ------------------------------------------------------------------------------------ verdict


def test_scripted_analyst_refers_to_edd_through_the_api() -> None:
    root = temp_root("gldb-e2e-kyc-api-")
    bdir = build_bundles(root, scenario="kyc", variants=("fraud",))["fraud"]
    run = run_bundle(bdir, tmp=root)
    application = main.create_app(run.settings, db=run.db, llm=run.llm, registry=run.registry)
    client = TestClient(application, raise_server_exceptions=False)
    case_id = run.case.id
    view = client.get(f"/cases/{case_id}").json()
    assert view["status"] == "open" and view["lane"] == "edd"
    assert "refer_edd" in view["allowed_verdicts"]
    assert "reject_or_escalate" not in view["allowed_verdicts"]  # a loan-only verdict

    high = [f["id"] for f in view["flags"] if f["severity"] == "high"]
    assert high
    r = client.post(
        f"/cases/{case_id}/review",
        json={
            "kind": "flag_decision",
            "decision": "confirm",
            "flag_ids": high,
            "reviewer": "a.tan",
            "reason": "A fourth director is not on the registry; ownership differs.",
            "case_version": view["version"],
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/cases/{case_id}/review",
        json={
            "kind": "verdict",
            "decision": "refer_edd",
            "reviewer": "a.tan",
            "reason": "Crypto-heavy activity against a logistics declaration; refer for EDD.",
            "case_version": view["version"],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["case"]["status"] == "closed"
    closed = client.get(f"/cases/{case_id}").json()
    assert closed["decision"] == "refer_edd" and closed["escalated_at"] is None
    assert closed["score"]["value"] == view["score"]["value"]  # analysis is immutable
