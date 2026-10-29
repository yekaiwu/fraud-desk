"""Repository plumbing owned by the foundation: ignore rules, .env.example, CI, imports, deps."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def check_ignore(path: str) -> bool:
    out = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
    return out.returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "data/gldb.sqlite3",
        ".env",
        "samples/bundle/a.pdf",
        "samples/registry.json",
        "docs/eval-results/results.json",
        "node_modules/x/index.js",
        "frontend/.next/cache",
        "backend/app/__pycache__/x.cpython-312.pyc",
        ".venv/bin/python",
        "backend/tests/_out/a.txt",
    ],
)
def test_ignored_paths(path: str) -> None:
    assert check_ignore(path), path


@pytest.mark.parametrize(
    "path",
    [
        "samples/README.md",
        "docs/eval-results/baseline.json",
        "docs/eval-results/timed-test.csv",
        ".env.example",
        "uv.lock",
        "backend/app/main.py",
        "docs/tasks.md",
    ],
)
def test_tracked_paths_are_not_ignored(path: str) -> None:
    assert not check_ignore(path), path


def test_env_example_lists_every_setting_without_secrets() -> None:
    lines = (ROOT / ".env.example").read_text().splitlines()
    assigned = {
        ln.split("=", 1)[0]: ln.split("=", 1)[1]
        for ln in lines
        if "=" in ln and not ln.startswith("#")
    }
    for name in Settings.model_fields:
        assert f"GLDB_{name.upper()}" in assigned, name
    assert assigned["GLDB_LLM_API_KEY"] == ""
    assert (
        assigned["GLDB_MONEY_TOLERANCE"] == "1.00" and assigned["GLDB_MAX_USD_PER_CASE"] == "2.00"
    )
    defaults = Settings(_env_file=None)
    assert assigned["GLDB_CLASSIFIER_MIN_PROB"] == "0.70" and defaults.classifier_min_prob == 0.70
    assert "NEXT_PUBLIC_API_BASE_URL" in assigned
    assert not any("sk-" in v for v in assigned.values())


def test_ci_workflow_is_valid_yaml_with_the_expected_commands() -> None:
    wf = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    runs = [s.get("run", "") for s in wf["jobs"]["backend"]["steps"]]
    for cmd in (
        "uv sync --dev",
        "uv lock --check",
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run pytest",
    ):
        assert cmd in runs, cmd
    assert any("gen_check_catalogue.py --check" in r for r in runs)
    assert "hashFiles('frontend/package.json')" in wf["jobs"]["frontend"]["if"]
    steps = " ".join(s.get("run", "") for s in wf["jobs"]["frontend"]["steps"])
    for cmd in ("npm ci", "npm run lint", "npx tsc --noEmit", "npm test", "npm run build"):
        assert cmd in steps
    assert "pip" not in (ROOT / ".github/workflows/ci.yml").read_text()


def test_pyproject_contract() -> None:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert cfg["project"]["requires-python"] == ">=3.12,<3.13"
    assert (ROOT / ".python-version").read_text().strip() == "3.12"
    deps = " ".join(cfg["project"]["dependencies"]).lower()
    for pkg in (
        "fastapi",
        "uvicorn",
        "pydantic",
        "pydantic-settings",
        "python-multipart",
        "httpx",
        "openai",
        "pymupdf",
        "pillow",
        "numpy",
        "opencv-python-headless",
        "imagehash",
        "reportlab",
        "pikepdf",
        "python-stdnum",
        "rapidfuzz",
        "python-dateutil",
        "networkx",
        "jinja2",
    ):
        assert pkg in deps, pkg
    dev = " ".join(cfg["dependency-groups"]["dev"]).lower()
    for pkg in ("pytest", "pytest-timeout", "pytest-cov", "ruff"):
        assert pkg in dev, pkg
    assert "local" not in cfg.get("project", {}).get(
        "optional-dependencies", {}
    )  # R0: no local extra
    ruff = cfg["tool"]["ruff"]
    assert ruff["line-length"] == 100 and ruff["target-version"] == "py312"
    assert ruff["lint"]["select"] == ["E", "F", "I", "UP", "B", "SIM"]
    pytest_cfg = cfg["tool"]["pytest"]["ini_options"]
    assert pytest_cfg["testpaths"] == ["backend/tests"] and pytest_cfg["pythonpath"] == ["backend"]
    assert "--timeout=120" in pytest_cfg["addopts"]


@pytest.mark.parametrize("cwd", ["", "backend/scripts"])
def test_app_and_scripts_import_from_repo_root_and_scripts_dir(cwd: str) -> None:
    out = subprocess.run(
        [sys.executable, "-c", "import app, scripts; print(app.__name__, scripts.__name__)"],
        cwd=ROOT / cwd,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "app scripts"


def test_required_dependencies_import() -> None:
    import cv2  # noqa: F401
    import fastapi  # noqa: F401
    import imagehash  # noqa: F401
    import jinja2  # noqa: F401
    import networkx  # noqa: F401
    import pikepdf  # noqa: F401
    import pymupdf  # noqa: F401
    import rapidfuzz  # noqa: F401
    import reportlab  # noqa: F401
    import stdnum  # noqa: F401
    from stdnum.sg import uen  # noqa: F401
