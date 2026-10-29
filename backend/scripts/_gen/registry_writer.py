"""Mock registry JSON writer (ACRA-profile shape, docs/data-model.md section 8)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from .schema import BundleSpec, Manifest, write_json
from .world import REGISTRY_AS_OF, registry_entity, world_from_spec


def registry_document(
    entities: Sequence[dict[str, Any]], as_of: date = REGISTRY_AS_OF
) -> dict[str, Any]:
    """Entities sorted by uen, duplicates (same uen) collapsed to the first."""
    seen: dict[str, dict[str, Any]] = {}
    for e in entities:
        seen.setdefault(e["uen"], e)
    return {
        "schema_version": "1",
        "generated_by": "make_bundle.py",
        "as_of": as_of.isoformat(),
        "entities": [seen[k] for k in sorted(seen)],
    }


def write_registry(
    out_path: Path, entities: Sequence[dict[str, Any]], as_of: date = REGISTRY_AS_OF
) -> None:
    write_json(out_path, registry_document(entities, as_of))


def entities_for_spec(spec: BundleSpec) -> list[dict[str, Any]]:
    """Registry entities of the spec's company (and intermediate holders). The registry is the
    truth: it never depends on the variant or mutations, only on the company."""
    w = world_from_spec(spec)
    return [registry_entity(w), *w.intermediates]


def build_registry(out_path: Path, manifests: Sequence[Manifest]) -> None:
    """Union of the entities of every manifest's company, sorted by uen."""
    ents: list[dict[str, Any]] = []
    for m in manifests:
        ents.extend(entities_for_spec(m.spec))
    write_registry(out_path, ents)
