"""T-integration-loan-10: injection smoke through the full pipeline (plan 5.3, test-data-and-eval 4.9).

A canary is a one-document bundle whose text carries an instruction aimed at the checker. Pass
criteria: the C1 label of every original page equals the untouched control's, the flag set is a
superset of the control's, and the score never drops below the control's. An extra page holding only
the injection may be labelled ``other`` or ``boilerplate`` only together with a ``doc_unclassified``
info flag, and coverage must not rise above the control's.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts._gen import canaries
from tests.e2e._support import Run, run_bundle, temp_root

CASES = [
    ("statement", "P1"),
    ("statement", "P2"),
    ("statement", "P3"),
    ("statement", "P4"),
    ("invoice", "P1"),
    ("invoice", "P4"),
]


@pytest.fixture(scope="module")
def canary_root():
    root = temp_root("gldb-e2e-canary-")
    canaries.build_canaries(root)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def controls(canary_root: Path) -> dict[str, Run]:
    return {
        host: run_bundle(
            canary_root / "canaries" / f"canary-{host}-control", tmp=canary_root / f"c-{host}"
        )
        for host in ("statement", "invoice")
    }


def _labels(run: Run) -> list[tuple[int, str]]:
    return [
        (p.page, p.classification.label)
        for f in run.case.files
        for p in f.pages
        if p.classification
    ]


def _flag_keys(run: Run) -> set[tuple[str, str]]:
    return {(f.rule, f.severity) for f in run.case.flags}


@pytest.mark.parametrize(("host", "placement"), CASES)
def test_injection_never_lowers_risk(
    canary_root: Path, controls: dict[str, Run], host: str, placement: str
) -> None:
    control = controls[host]
    run = run_bundle(
        canary_root / "canaries" / f"canary-{host}-{placement}",
        tmp=canary_root / f"r-{host}-{placement}",
    )
    original = _labels(control)
    got = _labels(run)
    assert got[: len(original)] == original  # every original page keeps its label
    assert _flag_keys(run) >= _flag_keys(control) - {("doc_unclassified", "info")}
    assert run.case.score.value >= control.case.score.value
    assert run.case.coverage.ratio <= control.case.coverage.ratio + 1e-9
    assert run.case.status == "open" and not run.case.partial
    assert all(s.status == "ok" for s in run.case.stages)
    if placement == "P2":
        extra = got[len(original) :]
        assert extra and all(label in {"other", "boilerplate"} for _, label in extra)
        assert "doc_unclassified" in {f.rule for f in run.case.flags}
    # whatever the page said, no model was consulted because of it
    assert run.llm.calls == []


def test_instruction_text_is_never_treated_as_a_command_by_the_rules(
    canary_root: Path,
) -> None:
    """The hostile line is kept as data: it is in the extracted text, not acted upon."""
    run = run_bundle(
        canary_root / "canaries" / "canary-statement-P1", tmp=canary_root / "r-p1-data"
    )
    assert run.case.score is not None and run.case.score.band == "low"
    kinds = {d.doc_type for d in run.case.documents}
    assert kinds == {"bank_statement"}
