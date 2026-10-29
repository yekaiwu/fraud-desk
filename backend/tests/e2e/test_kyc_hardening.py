"""T-integration-kyc hardening on the KYC bundles (plan 6 levers 5, 7, 11; plan 10 phase 4).

The KYC identity checks (``x_directors``, ``x_ownership_vs_registry``, ``kyc_shareholdings``) need
no model, so a re-run is free and identical; a tampered statement still costs at most one cheap
call; a breached budget is shown to the analyst and never silently lowers the risk.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.cache import ResponseCache
from app.pipeline import run_pipeline
from tests.e2e._support import PricedFake, build_bundles, run_bundle, temp_root
from tests.e2e.test_aml_hardening import tampered_statement_bundle


@pytest.fixture(scope="module")
def root():
    path = temp_root("gldb-e2e-kyc-hard-")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="module")
def fraud(root: Path) -> Path:
    return build_bundles(root / "f", scenario="kyc", variants=("fraud",))["fraud"]


@pytest.fixture(scope="module")
def tampered(root: Path) -> Path:
    return tampered_statement_bundle(root, "kyc", "kyc-tampered", ("2026-08",))


def test_rerun_makes_zero_model_calls_and_identical_flags(fraud: Path, root: Path) -> None:
    first = run_bundle(fraud, tmp=root / "r1")
    assert first.llm.calls == []
    llm = PricedFake(cache=ResponseCache(first.db, enabled=True))
    again = run_pipeline(
        first.case.id, db=first.db, settings=first.settings, llm=llm, registry=first.registry
    )
    assert llm.calls == [] and again.version == first.case.version + 1
    assert again.provenance and first.case.provenance
    assert again.provenance.flags_digest == first.case.provenance.flags_digest
    assert [(f.rule, f.severity, f.evidence) for f in again.flags] == [
        (f.rule, f.severity, f.evidence) for f in first.case.flags
    ]
    assert again.lane == "edd" and again.score.value == first.case.score.value
    assert not again.partial


def test_a_tampered_kyc_statement_costs_one_page_reading_and_is_cached_on_rerun(
    tampered: Path, root: Path
) -> None:
    first = run_bundle(tampered, tmp=root / "r-tamper", llm=PricedFake(usd="0.10"))
    assert "bs_running_balance" in first.rules(min_severity="medium")
    # one failing page: a cheap second reading; the empty answer disagrees with the text layer, so
    # the strong model is asked once more and that is the ceiling (lever 5)
    assert [c.model_role for c in first.llm.calls] == ["cheap", "strong"]
    llm = PricedFake(usd="0.10", cache=ResponseCache(first.db, enabled=True))
    cold = run_pipeline(
        first.case.id, db=first.db, settings=first.settings, llm=llm, registry=first.registry
    )
    paid = len(llm.calls)
    # the cold cache pays at most once per role (the Fake gives both roles one model name, so the
    # strong reading may already be a cache hit)
    assert 1 <= paid <= 2
    warm = run_pipeline(
        first.case.id, db=first.db, settings=first.settings, llm=llm, registry=first.registry
    )
    assert len(llm.calls) == paid  # and never again
    assert sum(c.usd for c in warm.costs) == 0
    assert warm.provenance.flags_digest == cold.provenance.flags_digest


def test_page_ceiling_marks_the_kyc_case_partial_and_says_so_in_the_api(
    fraud: Path, root: Path
) -> None:
    run = run_bundle(fraud, tmp=root / "r-pages", max_pages_per_case=6)
    case = run.case
    reason = next(r for r in case.partial_reasons if r.startswith("budget_exceeded: max_pages"))
    assert "limit 6" in reason
    assert case.partial and case.score is not None and case.score.partial
    assert case.coverage is not None and case.coverage.pages_unchecked > 0
    assert case.status == "open" and all(s.status == "ok" for s in case.stages)
    # a cut is never silence: the documents past the ceiling are reported, not dropped
    ingest = next(s for s in case.stages if s.stage == "ingest")
    assert ingest.detail["not_processed_pages"] > 0
    application = main.create_app(run.settings, db=run.db, llm=run.llm, registry=run.registry)
    client = TestClient(application, raise_server_exceptions=False)
    body = client.get(f"/cases/{case.id}").json()
    assert body["partial"] is True and body["score"]["partial"] is True
    assert any(r.startswith("budget_exceeded: max_pages") for r in body["score"]["partial_reasons"])
    assert client.get("/cases").json()["items"][0]["partial"] is True


def test_a_partial_case_never_reads_as_lower_risk_than_the_full_one(
    fraud: Path, root: Path
) -> None:
    """The guard drops pages, never flags: what was processed still raises its flags, and the
    cut case is marked partial rather than quietly scoring zero."""
    full = run_bundle(fraud, tmp=root / "r-full")
    cut = run_bundle(fraud, tmp=root / "r-cut", max_pages_per_case=6)
    assert full.case.score and cut.case.score
    assert cut.case.score.partial and not full.case.score.partial
    assert {f.rule for f in cut.case.flags if f.severity != "info"} <= {
        f.rule for f in full.case.flags if f.severity != "info"
    }
    assert cut.case.coverage.ratio <= full.case.coverage.ratio
