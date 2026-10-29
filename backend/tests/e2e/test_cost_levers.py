"""T-integration-loan-08: cost-lever behaviour end to end with the Fake client (plan 6).

Lever 2: a clean digital bundle makes no model call. Lever 5: a tampered digital page costs at most
one cheap call and no strong call. Lever 11: a breached budget keeps the deterministic stages
running, marks the score partial and shows ``budget_exceeded:`` verbatim. Lever 7: a rerun with a
warm cache makes no provider call.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.cache import ResponseCache
from app.pipeline import run_pipeline
from tests.e2e._support import (
    PricedFake,
    Run,
    run_bundle,
    tampered_loan_bundle,
    temp_root,
)


@pytest.fixture(scope="module")
def tamper_root():
    root = temp_root("gldb-e2e-cost-")
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def one_tamper(tamper_root: Path) -> Path:
    return tampered_loan_bundle(tamper_root, "one", ("2025-09",))


@pytest.fixture(scope="module")
def three_tampers(tamper_root: Path) -> Path:
    return tampered_loan_bundle(tamper_root, "three", ("2025-08", "2025-09", "2025-10"))


def test_lever2_consistent_digital_bundles_make_zero_model_calls(
    loan_runs: dict[str, Run],
) -> None:
    """The clean control and the careful forger are internally consistent: the text layer passes its
    own validation, so no model is ever asked (the forger's lie is found by the free rules)."""
    for variant in ("clean", "forger"):
        run = loan_runs[variant]
        assert run.llm.calls == [], variant
        extract = next(s for s in run.case.stages if s.stage == "extract")
        assert extract.detail["llm_cheap_pages"] == 0 and extract.detail["llm_strong_pages"] == 0
        assert extract.detail["text_layer_docs"] == len(
            [d for d in run.case.documents if d.status != "received_not_checked"]
        )


def test_lever5_the_sloppy_fraud_slip_costs_one_cheap_call_and_no_strong_call(
    loan_runs: dict[str, Run],
) -> None:
    """loan-fraud prints one gross profit that does not add up: one page fails the text layer's
    own validation, so exactly one cheap second reading is requested and no strong one."""
    run = loan_runs["fraud"]
    assert [c.model_role for c in run.llm.calls] == ["cheap"]
    assert run.llm.calls[0].purpose == "extract:financial_statement"


def test_lever5_a_tampered_digital_page_costs_at_most_one_cheap_call(
    one_tamper: Path, tamper_root: Path
) -> None:
    run = run_bundle(one_tamper, tmp=tamper_root / "r-one")
    assert "bs_running_balance" in run.rules()  # still caught from the text layer alone
    assert len(run.llm.calls) <= 1
    assert [c.model_role for c in run.llm.calls] == ["cheap"]
    assert all(c.purpose == "extract:bank_statement" for c in run.llm.calls)
    # the failed second reading is a visible warning on the document, never silently dropped
    warned = [d for d in run.case.documents if any("escalation_failed" in w for w in d.warnings)]
    assert len(warned) == 1


def test_lever11_max_calls_breach_keeps_deterministic_stages_running(
    three_tampers: Path, tamper_root: Path
) -> None:
    run = run_bundle(three_tampers, tmp=tamper_root / "r-calls", max_llm_calls_per_case=1)
    case = run.case
    assert len(run.llm.calls) == 1
    reason = next(r for r in case.partial_reasons if r.startswith("budget_exceeded:"))
    assert reason.startswith("budget_exceeded: max_calls (limit 1, used 1)")
    assert case.partial is True and case.score is not None and case.score.partial is True
    assert reason in case.score.partial_reasons  # verbatim, also on the score
    assert case.status == "open"
    assert all(s.status == "ok" for s in case.stages)
    # deterministic checks still ran and still caught all three edited statements
    assert len({f.document_ids[0] for f in run.flags("bs_running_balance")}) == 3
    assert case.score.value >= 60


def test_lever11_max_usd_breach_is_shown_verbatim(three_tampers: Path, tamper_root: Path) -> None:
    run = run_bundle(
        three_tampers,
        tmp=tamper_root / "r-usd",
        llm=PricedFake(usd="1.50"),
        max_usd_per_case="2.00",
    )
    case = run.case
    reason = next(r for r in case.partial_reasons if r.startswith("budget_exceeded:"))
    assert reason.startswith("budget_exceeded: max_usd (limit 2.00, used 3.000000)")
    assert case.score is not None and case.score.partial
    assert sum(c.usd for c in case.costs) == 3  # the two calls that were allowed, nothing after
    assert len(run.llm.calls) == 2
    extract = next(s for s in case.stages if s.stage == "extract")
    assert extract.status == "ok" and extract.cost_usd == 3
    assert "bs_running_balance" in run.rules()


def test_lever11_max_pages_breach_marks_partial_and_checks_still_run(
    three_tampers: Path, tamper_root: Path
) -> None:
    run = run_bundle(three_tampers, tmp=tamper_root / "r-pages", max_pages_per_case=10)
    case = run.case
    reason = next(r for r in case.partial_reasons if r.startswith("budget_exceeded: max_pages"))
    assert "limit 10" in reason
    ingest = next(s for s in case.stages if s.stage == "ingest")
    assert ingest.detail["not_processed_pages"] > 0
    assert case.partial and case.score is not None and case.score.partial
    assert all(s.status == "ok" for s in case.stages)
    # what was processed was still checked, and the cut itself raised no false "missing page"
    assert run.flags("bs_running_balance")
    assert not run.flags("bs_page_chain")
    assert case.coverage is not None and case.coverage.pages_unchecked > 0


def test_budget_reason_reaches_the_api_verbatim(three_tampers: Path, tamper_root: Path) -> None:
    run = run_bundle(three_tampers, tmp=tamper_root / "r-api", max_llm_calls_per_case=1)
    application = main.create_app(run.settings, db=run.db, llm=run.llm, registry=run.registry)
    client = TestClient(application, raise_server_exceptions=False)
    body = client.get(f"/cases/{run.case.id}").json()
    assert body["partial"] is True
    assert any(r.startswith("budget_exceeded: max_calls") for r in body["partial_reasons"])
    assert body["score"]["partial"] is True
    assert any(r.startswith("budget_exceeded:") for r in body["score"]["partial_reasons"])
    queue = client.get("/cases").json()
    assert queue["items"][0]["partial"] is True


def test_lever7_cache_rerun_makes_zero_provider_calls(
    three_tampers: Path, tamper_root: Path
) -> None:
    root = tamper_root / "r-cache"
    first_settings_run = run_bundle(three_tampers, tmp=root, llm=PricedFake(usd="0.10"))
    # run_bundle's client has no cache; build the same case again with a cache-backed client
    db, settings = first_settings_run.db, first_settings_run.settings
    llm = PricedFake(usd="0.10", cache=ResponseCache(db, enabled=True))
    case_id = first_settings_run.case.id
    cold = run_pipeline(
        case_id, db=db, settings=settings, llm=llm, registry=first_settings_run.registry
    )
    cold_calls = len(llm.calls)
    assert cold_calls >= 3 and cold.version == 2
    assert sum(c.usd for c in cold.costs) > 0
    warm = run_pipeline(
        case_id, db=db, settings=settings, llm=llm, registry=first_settings_run.registry
    )
    assert len(llm.calls) == cold_calls  # no new provider call
    assert warm.version == 3
    hits = [c for c in warm.costs if c.cache_hit]
    assert hits and all(c.usd == 0 for c in hits)
    assert sum(c.usd for c in warm.costs) == 0
    # identical analysis either way
    assert cold.provenance and warm.provenance
    assert cold.provenance.flags_digest == warm.provenance.flags_digest
