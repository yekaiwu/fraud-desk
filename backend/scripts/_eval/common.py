"""Shared helpers for the eval harness: paths, manifest loading, building base bundles."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts._gen.schema import (  # noqa: E402
    FIXED_TODAY,
    BundleSpec,
    Manifest,
    bundle_id,
    dump_json,
)

DEFAULT_SEED = 4002
SCENARIOS = ("loan", "kyc", "aml")
DEGRADED = ("scan_hq", "scan_lq", "photo")
ORACLE_SENTENCE = (
    "Scan and photo rows use oracle extraction (the generator's ground truth stands in for OCR), "
    "so they measure the rules and the image forensics, not text recognition."
)
HEADER = (
    "Run with no network and no API key: FakeLLMClient (oracle on scans), a fresh SQLite database per "
    "experiment, fictitious data from our own generator (results are an upper bound)."
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_file(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json(obj), encoding="utf-8")


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate(read_json(path))


def posix_rel(target: Path, start: Path) -> str:
    return Path(os.path.relpath(target, start)).as_posix()


def build_registry_for(samples: Path) -> None:
    from scripts import make_bundle

    manifests = []
    for p in sorted(samples.glob("bundles/*/manifest.json")):
        manifests.append(load_manifest(p))
    if manifests:
        make_bundle.build_registry(samples / "registry.json", manifests)


def ensure_bundle(
    samples: Path,
    scenario: str,
    variant: str = "clean",
    company_index: int = 0,
    *,
    seed: int = DEFAULT_SEED,
    today: date = FIXED_TODAY,
    renders: tuple[str, ...] = ("digital",),
    update_registry: bool = True,
) -> Path:
    """Build ``samples/bundles/<id>`` if it is missing or lacks a requested render; return its dir."""
    from scripts import make_bundle

    spec = BundleSpec(
        scenario=scenario,  # type: ignore[arg-type]
        variant=variant,  # type: ignore[arg-type]
        company_index=company_index,
        seed=seed,
        today=today,
        renders=list(dict.fromkeys(["digital", *renders])),  # type: ignore[arg-type]
    )
    bdir = samples / "bundles" / bundle_id(spec)
    man = bdir / "manifest.json"
    if man.exists():
        m = load_manifest(man)
        if (
            m.spec.seed == seed
            and m.spec.today == today
            and all(r in m.upload_order for r in spec.renders)
        ):
            return bdir
    make_bundle.build_bundle(spec, bdir)
    if update_registry:
        build_registry_for(samples)
    return bdir


def truth_of(bdir: Path) -> dict[str, Any]:
    return read_json(bdir / "ground_truth.json")


def doc_from_truth(truth: dict[str, Any], key: str) -> dict[str, Any] | None:
    return next((d for d in truth["documents"] if d["key"] == key), None)
