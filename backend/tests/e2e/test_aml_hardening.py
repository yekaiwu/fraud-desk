"""T-integration-kyc hardening on the AML bundles (plan 6 levers 5, 6, 7, 11; plan 10 phase 4).

* lever 5: cheap-then-strong extraction escalation, wired through the whole pipeline;
* lever 6: scanned pages go to the model as a small grayscale PNG whose size follows
  ``GLDB_VISION_DPI``;
* lever 7: a re-run of the same bundle makes zero provider calls and gives identical flags;
* lever 11: the budget guard marks the score partial with a visible message and the checks that
  were able to run still run;
* coverage is part of the score.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from app.core.cache import ResponseCache
from app.core.llm import FakeLLMClient
from app.pipeline import run_pipeline
from scripts import make_bundle
from scripts._gen.schema import BundleSpec, Mutation
from scripts.run_case import bundle_files
from tests.e2e._support import (
    EMPTY_STATEMENT_PAGE,
    PricedFake,
    Run,
    build_bundles,
    run_bundle,
    temp_root,
)


def tampered_statement_bundle(
    root: Path, scenario: str, name: str, months: tuple[str, ...]
) -> Path:
    """The clean bundle of ``scenario`` where, in each month, the largest credit is raised by 25%
    with every balance left alone: the printed running balance no longer chains (T01)."""
    from decimal import Decimal

    base = root / "bundles" / f"{scenario}-clean"
    if not (base / "manifest.json").exists():
        build_bundles(root, scenario=scenario, variants=("clean",))
    truth = json.loads((base / "ground_truth.json").read_text(encoding="utf-8"))
    muts = []
    for month in months:
        key = f"bank_statement:{month}"
        stmt = next(d for d in truth["documents"] if d["key"] == key)
        credits = [
            (r["index"], Decimal(r["cells"]["credit"]["value"]))
            for r in stmt["tables"]["transactions"]["rows"]
            if r["cells"]["credit"]["value"]
        ]
        index, amount = max(credits, key=lambda c: c[1])
        value = (amount * Decimal("1.25")).quantize(Decimal("0.01"))
        muts.append(
            Mutation(
                op="override_cell",
                target=key,
                args={
                    "table": "transactions",
                    "row": index,
                    "column": "credit",
                    "value": str(value),
                },
            )
        )
    spec = BundleSpec(scenario=scenario, variant="clean", mutations=muts)  # type: ignore[arg-type]
    bdir = root / "bundles" / name
    make_bundle.build_bundle(spec, bdir)
    return bdir


@pytest.fixture(scope="module")
def root():
    path = temp_root("gldb-e2e-aml-hard-")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="module")
def one_tamper(root: Path) -> Path:
    return tampered_statement_bundle(root, "aml", "aml-one", ("2026-07",))


@pytest.fixture(scope="module")
def three_tampers(root: Path) -> Path:
    return tampered_statement_bundle(root, "aml", "aml-three", ("2026-06", "2026-07", "2026-08"))


# ------------------------------------------------------------------ lever 5: cheap then strong


def test_lever5_a_disagreeing_cheap_reading_is_settled_by_one_strong_call(
    one_tamper: Path, root: Path
) -> None:
    """Text layer says the balance chain breaks; the cheap model reads the page differently, so a
    strong model is asked once. Neither model's numbers replace what is printed, and the
    deterministic check still fires from the text layer."""
    llm = FakeLLMClient(
        {
            "extract:bank_statement:cheap": EMPTY_STATEMENT_PAGE,
            "extract:bank_statement:strong": EMPTY_STATEMENT_PAGE,
        }
    )
    run = run_bundle(one_tamper, tmp=root / "r-cascade", llm=llm, extractor_mode="cascade")
    roles = [c.model_role for c in llm.calls]
    assert roles == ["cheap", "strong"], roles
    assert all(c.purpose == "extract:bank_statement" for c in llm.calls)
    extract = next(s for s in run.case.stages if s.stage == "extract")
    assert extract.detail["text_layer_docs"] == 20  # the printed numbers were never replaced
    tampered = next(d for d in run.case.documents if d.extractor.startswith("cascade("))
    assert tampered.extractor == "cascade(text_layer>llm_cheap>llm_strong)"
    assert any(w.endswith("result=text_layer_kept") for w in tampered.warnings)
    assert "bs_running_balance" in run.rules(min_severity="medium")
    assert run.case.status == "open" and all(s.status == "ok" for s in run.case.stages)


def test_lever5_an_agreeing_cheap_reading_never_reaches_the_strong_model(
    one_tamper: Path, root: Path
) -> None:
    """With no fixture the second reading fails visibly (a warning), and no strong call follows."""
    run = run_bundle(one_tamper, tmp=root / "r-cheap-only")
    assert [c.model_role for c in run.llm.calls] == ["cheap"]
    assert any("escalation_failed" in w for d in run.case.documents for w in d.warnings), (
        "a failed second reading must be visible on the document"
    )
    assert "bs_running_balance" in run.rules(min_severity="medium")


def test_lever5_a_clean_aml_bundle_makes_no_model_call(root: Path) -> None:
    bdir = build_bundles(root / "plain", scenario="aml", variants=("fraud",))["fraud"]
    run = run_bundle(bdir, tmp=root / "r-plain")
    assert run.llm.calls == []
    extract = next(s for s in run.case.stages if s.stage == "extract")
    assert extract.detail["llm_cheap_pages"] == 0 and extract.detail["llm_strong_pages"] == 0


# ------------------------------------------------------------------ lever 6: image size


def test_lever6_scanned_pages_go_to_the_model_as_small_grayscale_png(root: Path) -> None:
    spec = BundleSpec(scenario="aml", variant="clean", renders=["digital", "scan_hq"])
    bdir = root / "bundles" / "aml-scan"
    make_bundle.build_bundle(spec, bdir)
    scans = bundle_files(bdir, "scan_hq")
    statement = next(p for p in scans if "statement" in p.name)
    sizes: dict[int, tuple[int, int, int]] = {}
    for dpi in (72, 150):
        llm = FakeLLMClient(fixtures={"ocr:page": {"lines": []}}, default={"lines": []})
        run_bundle_scan(bdir, root / f"r-dpi-{dpi}", llm, statement, vision_dpi=dpi)
        images = [img for call in llm.calls for img in call.images]
        assert images, f"no image was sent at {dpi} dpi"
        im = Image.open(io.BytesIO(images[0]))
        assert im.format == "PNG" and im.mode == "L", (im.format, im.mode)  # grayscale
        sizes[dpi] = (im.width, im.height, len(images[0]))
    # fewer dots per inch -> a smaller image and fewer bytes (never more than the 2200 px cap)
    assert sizes[72][0] < sizes[150][0] and sizes[72][2] < sizes[150][2]
    assert max(sizes[150][:2]) <= 2200


def run_bundle_scan(bdir: Path, tmp: Path, llm: FakeLLMClient, path: Path, **overrides) -> Run:  # noqa: ANN003
    return run_bundle(bdir, tmp=tmp, llm=llm, paths=[path], **overrides)


# ------------------------------------------------------------------ lever 7: cache


def test_lever7_rerun_of_a_deterministic_bundle_makes_zero_model_calls_and_same_flags(
    root: Path,
) -> None:
    bdir = build_bundles(root / "rerun", scenario="aml", variants=("fraud",))["fraud"]
    run = run_bundle(bdir, tmp=root / "r-rerun")
    db, settings = run.db, run.settings
    llm = PricedFake(cache=ResponseCache(db, enabled=True))
    again = run_pipeline(run.case.id, db=db, settings=settings, llm=llm, registry=run.registry)
    assert llm.calls == [] and again.version == run.case.version + 1
    assert again.provenance and run.case.provenance
    assert again.provenance.flags_digest == run.case.provenance.flags_digest
    assert [(f.id, f.rule, f.severity) for f in again.flags] == [
        (f.id, f.rule, f.severity) for f in run.case.flags
    ]
    assert again.score and run.case.score and again.score.value == run.case.score.value
    assert [f.title for f in again.findings] == [f.title for f in run.case.findings]


def test_lever7_rerun_with_a_warm_cache_makes_no_provider_call(
    three_tampers: Path, root: Path
) -> None:
    first = run_bundle(three_tampers, tmp=root / "r-warm", llm=PricedFake(usd="0.10"))
    db, settings = first.db, first.settings
    llm = PricedFake(usd="0.10", cache=ResponseCache(db, enabled=True))
    cold = run_pipeline(first.case.id, db=db, settings=settings, llm=llm, registry=first.registry)
    cold_calls = len(llm.calls)
    assert cold_calls >= 3 and sum(c.usd for c in cold.costs) > 0
    warm = run_pipeline(first.case.id, db=db, settings=settings, llm=llm, registry=first.registry)
    assert len(llm.calls) == cold_calls
    hits = [c for c in warm.costs if c.cache_hit]
    assert hits and all(c.usd == 0 for c in hits) and sum(c.usd for c in warm.costs) == 0
    assert warm.provenance and cold.provenance
    assert warm.provenance.flags_digest == cold.provenance.flags_digest
    assert warm.score and cold.score and warm.score.value == cold.score.value


# ------------------------------------------------------------------ lever 11: budget guard


def test_lever11_call_ceiling_marks_the_score_partial_and_checks_still_run(
    three_tampers: Path, root: Path
) -> None:
    run = run_bundle(three_tampers, tmp=root / "r-calls", max_llm_calls_per_case=1)
    case = run.case
    assert len(run.llm.calls) == 1
    reason = next(r for r in case.partial_reasons if r.startswith("budget_exceeded:"))
    assert reason.startswith("budget_exceeded: max_calls (limit 1, used 1)")
    assert case.partial is True and case.score is not None and case.score.partial is True
    assert reason in case.score.partial_reasons
    assert case.status == "open" and all(s.status == "ok" for s in case.stages)
    assert len({f.document_ids[0] for f in run.flags("bs_running_balance")}) == 3
    # the cross-document AML rules never needed a model and are unaffected by the cut
    assert case.lane == "aml"


def test_lever11_dollar_ceiling_is_shown_verbatim(three_tampers: Path, root: Path) -> None:
    run = run_bundle(
        three_tampers, tmp=root / "r-usd", llm=PricedFake(usd="1.50"), max_usd_per_case="2.00"
    )
    case = run.case
    reason = next(r for r in case.partial_reasons if r.startswith("budget_exceeded:"))
    assert reason.startswith("budget_exceeded: max_usd (limit 2.00, used 3.000000)")
    assert case.score is not None and case.score.partial
    assert sum(c.usd for c in case.costs) == 3 and len(run.llm.calls) == 2
    assert "bs_running_balance" in run.rules()


def test_lever11_page_ceiling_cuts_pages_deterministically_and_lowers_coverage(
    root: Path,
) -> None:
    bdir = build_bundles(root / "pages", scenario="aml", variants=("fraud",))["fraud"]
    run = run_bundle(bdir, tmp=root / "r-pages", max_pages_per_case=12)
    case = run.case
    reason = next(r for r in case.partial_reasons if r.startswith("budget_exceeded: max_pages"))
    assert "limit 12" in reason
    ingest = next(s for s in case.stages if s.stage == "ingest")
    assert ingest.detail["not_processed_pages"] > 0
    assert case.partial and case.score is not None and case.score.partial
    assert reason in case.score.partial_reasons
    assert case.coverage is not None and case.coverage.pages_unchecked > 0
    assert case.coverage.pages_total > case.coverage.pages_unchecked
    # a second identical run picks the same pages (the cut is deterministic)
    again = run_bundle(bdir, tmp=root / "r-pages2", max_pages_per_case=12)
    assert [(f.rule, f.severity, tuple(f.document_ids)) for f in again.case.flags] == [
        (f.rule, f.severity, tuple(f.document_ids)) for f in case.flags
    ]


# ------------------------------------------------------------------ coverage is in the score


def test_coverage_is_part_of_the_score_and_a_clean_case_is_never_called_fully_checked(
    root: Path,
) -> None:
    bdir = build_bundles(root / "cov", scenario="aml", variants=("clean",))["clean"]
    run = run_bundle(bdir, tmp=root / "r-cov")
    case = run.case
    assert case.coverage is not None and case.score is not None
    assert 0 < case.coverage.ratio < 1  # image/FS checks have nothing to run on, and say so
    assert case.coverage.ran == len({r.rule for r in case.check_runs if r.status == "ran"})
    assert any(s["rule"].startswith("fs_") for s in case.coverage.skipped)
    assert all(s["reason"] for s in case.coverage.skipped)  # every gap has a stated reason
    # the headline never claims more than was checked
    assert (
        case.top_finding
        and f"{case.coverage.ran} of {case.coverage.applicable}" in case.top_finding
    )
    assert case.score.value == 0 and case.score.band == "low"
