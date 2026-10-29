"""Shared, cached builders for the generator tests (no tests in this module)."""

from __future__ import annotations

import atexit
import functools
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from scripts import make_bundle
from scripts._gen import quirks as qk
from scripts._gen.schema import BundleSpec


@functools.lru_cache(maxsize=1)
def samples_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="gen_samples_"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    return root


@functools.cache
def loan_bundle(
    variant: str = "clean",
    company_index: int = 0,
    hard: bool = False,
    renders: tuple[str, ...] = ("digital",),
    packet: bool = False,
) -> Path:
    """Build (once) and return the bundle directory under a shared temp ``samples`` root."""
    root = samples_root()
    quirks = (
        [*qk.default_quirks(company_index), *qk.hard_quirks_for("loan")]
        if hard
        else qk.default_quirks(company_index)
    )
    spec = BundleSpec(
        scenario="loan",
        variant=variant,
        company_index=company_index,
        quirks=quirks,
        renders=list(renders),
        packet=packet,
    )  # type: ignore[arg-type]
    tag = f"{variant}-{company_index}-{int(hard)}-{'+'.join(renders)}-{int(packet)}"
    bdir = root / "bundles" / tag
    make_bundle.build_bundle(spec, bdir)
    # the registry sits at <root>/registry.json = bdir/../../registry.json
    make_bundle.build_registry(
        root / "registry.json",
        [
            make_bundle.Manifest.model_validate(json.loads((p / "manifest.json").read_text()))
            for p in sorted((root / "bundles").glob("*"))
        ],
    )
    return bdir


def load(bdir: Path, name: str) -> Any:
    return json.loads((bdir / name).read_text(encoding="utf-8"))


def doc(gt: dict[str, Any], key: str) -> dict[str, Any]:
    return next(d for d in gt["documents"] if d["key"] == key)


def docs_of(gt: dict[str, Any], doc_type: str) -> list[dict[str, Any]]:
    return [d for d in gt["documents"] if d["doc_type"] == doc_type]


def field(d: dict[str, Any], name: str) -> str | None:
    return d["fields"][name]["value"]
