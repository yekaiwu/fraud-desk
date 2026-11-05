"""T16a through the real pipeline: the same invoice file uploaded twice in one bundle.

A byte-identical repeat is collapsed by the rules that would double a total (the invoice-vs-receipts
check, the AML totals), but ``inv_duplicate`` must still see it: high, never the fast lane, and no
false 'invoice unpaid' flag from a doubled total. (Building the bundle alone is not a test of this.)
"""

from __future__ import annotations

import shutil

import pytest

from scripts._eval.common import load_manifest
from scripts.make_tampered import apply_tamper
from tests.e2e._support import Run, build_bundles, run_bundle, temp_root


@pytest.fixture(scope="module")
def t16a_runs():
    root = temp_root("gldb-e2e-t16a-")
    runs: dict[str, tuple[Run, Run]] = {}
    for scenario in ("loan", "aml"):
        base = build_bundles(root / scenario, scenario=scenario, variants=("clean",))["clean"]
        dest = root / scenario / "tampered" / "T16a"
        apply_tamper(
            load_manifest(base / "manifest.json"),
            "T16a",
            seed=4002,
            instance=0,
            out_dir=dest,
            render="digital",
            renders=["digital"],
            base_dir=base,
        )
        runs[scenario] = (
            run_bundle(base, tmp=root / scenario / "run-clean"),
            run_bundle(dest, tmp=root / scenario / "run-t16a"),
        )
    yield runs
    shutil.rmtree(root, ignore_errors=True)


@pytest.mark.parametrize("scenario", ["loan", "aml"])
def test_the_invoice_sent_twice_is_inv_duplicate_high_and_not_the_fast_lane(
    t16a_runs: dict[str, tuple[Run, Run]], scenario: str
) -> None:
    clean, tampered = t16a_runs[scenario]
    assert not clean.flags("inv_duplicate")
    dup = tampered.flags("inv_duplicate")
    assert len(dup) == 1 and dup[0].severity == "high"
    assert len(dup[0].document_ids) == 2
    assert tampered.case.lane != "fast"
    assert tampered.case.score is not None and tampered.case.score.band != "low"
    # the repeat is also reported as a reused file
    assert tampered.flags("file_reuse")


@pytest.mark.parametrize("scenario", ["loan", "aml"])
def test_the_doubled_invoice_does_not_double_a_total(
    t16a_runs: dict[str, tuple[Run, Run]], scenario: str
) -> None:
    clean, tampered = t16a_runs[scenario]
    assert tampered.flags("x_invoice_vs_receipts") == clean.flags("x_invoice_vs_receipts") == []
    assert tampered.rules(min_severity="medium") == {"inv_duplicate"}
