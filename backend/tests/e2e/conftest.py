"""Session fixtures for the end-to-end tests: loan bundles are generated and analysed once."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.e2e._support import Run, build_bundles, run_bundle, temp_root


@pytest.fixture(scope="session")
def loan_root() -> Path:
    root = temp_root("gldb-e2e-loan-")
    build_bundles(root)
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def loan_bundles(loan_root: Path) -> dict[str, Path]:
    return {v: loan_root / "bundles" / f"loan-{v}" for v in ("clean", "fraud", "forger")}


@pytest.fixture(scope="session")
def loan_runs(loan_bundles: dict[str, Path]) -> dict[str, Run]:
    """Each variant analysed once, each in its own fresh database (no cross-case fingerprint hits)."""
    runs = {v: run_bundle(b) for v, b in loan_bundles.items()}
    yield runs
    for run in runs.values():
        shutil.rmtree(run.settings.data_dir.parent, ignore_errors=True)
