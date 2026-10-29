"""Shared, cached builders for the KYC / AML generator tests (no tests in this module)."""

from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts import make_bundle
from scripts._gen import quirks as qk
from scripts._gen.schema import BundleSpec, Manifest, Mutation

from .test_gen_helpers import doc, docs_of, field, load, samples_root

__all__ = [
    "aml_bundle",
    "digital_hashes",
    "doc",
    "docs_of",
    "field",
    "kyc_bundle",
    "load",
    "rows_of",
    "table_rows",
]


@functools.cache
def _bundle(
    scenario: str,
    variant: str,
    company_index: int,
    renders: tuple[str, ...],
    mutations: tuple[str, ...],
    tag: str,
) -> Path:
    root = samples_root()
    spec = BundleSpec(
        scenario=scenario,  # type: ignore[arg-type]
        variant=variant,  # type: ignore[arg-type]
        company_index=company_index,
        quirks=qk.default_quirks(company_index),
        renders=list(renders),  # type: ignore[arg-type]
        mutations=[Mutation.model_validate(json.loads(m)) for m in mutations],
    )
    bdir = root / "bundles" / f"{scenario}-{variant}-{company_index}-{'+'.join(renders)}-{tag}"
    make_bundle.build_bundle(spec, bdir)
    make_bundle.build_registry(
        root / "registry.json",
        [
            Manifest.model_validate(json.loads((p / "manifest.json").read_text()))
            for p in sorted((root / "bundles").glob("*"))
            if (p / "manifest.json").exists()
        ],
    )
    return bdir


def kyc_bundle(
    variant: str = "clean",
    company_index: int = 0,
    renders: tuple[str, ...] = ("digital",),
    mutations: list[dict[str, Any]] | None = None,
    tag: str = "",
) -> Path:
    return _bundle(
        "kyc",
        variant,
        company_index,
        renders,
        tuple(json.dumps(m, sort_keys=True) for m in (mutations or [])),
        tag,
    )


def aml_bundle(
    variant: str = "clean",
    company_index: int = 0,
    renders: tuple[str, ...] = ("digital",),
    mutations: list[dict[str, Any]] | None = None,
    tag: str = "",
) -> Path:
    return _bundle(
        "aml",
        variant,
        company_index,
        renders,
        tuple(json.dumps(m, sort_keys=True) for m in (mutations or [])),
        tag,
    )


def digital_hashes(bdir: Path) -> dict[str, str]:
    """``doc_key -> sha256`` of every digital file of a bundle (via its manifest)."""
    man = load(bdir, "manifest.json")
    path_of = {d["key"]: d["digital_path"] for d in man["documents"]}
    return {k: hashlib.sha256((bdir / p).read_bytes()).hexdigest() for k, p in path_of.items()}


def table_rows(gt: dict[str, Any], key: str, table: str) -> list[dict[str, Any]]:
    return doc(gt, key)["tables"][table]["rows"]


def rows_of(row: dict[str, Any], *cols: str) -> tuple[str | None, ...]:
    return tuple(row["cells"][c]["value"] for c in cols)
