"""Generator and manifest models (T-gen-loan-01)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts._gen import schema
from scripts._gen.schema import BundleSpec, Expected, Manifest, Mutation, bundle_id, dump_json


def _manifest() -> Manifest:
    return Manifest(
        bundle_id="loan-clean", kind="bundle", use_case="loan", spec=BundleSpec(scenario="loan"), generator={"version": "1"},
        applicant={"name": "X", "uen": "201912345R"}, registry_path="../../registry.json", files=[], documents=[],
        upload_order={"digital": []}, expected=Expected(lane_any_of=["fast", "loan"], band_max="low"),
    )  # fmt: skip


def test_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        BundleSpec(scenario="loan", nonsense=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Mutation(op="override_cell", target="x", args={}, extra=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        BundleSpec(scenario="bank")  # type: ignore[arg-type]


def test_manifest_round_trips_byte_stable() -> None:
    text = dump_json(_manifest())
    again = Manifest.model_validate(json.loads(text))
    assert dump_json(again) == text
    assert text.endswith("}\n") and not text.endswith("\n\n")
    keys = list(json.loads(text))
    assert keys == sorted(keys)
    assert '  "applicant"' in text  # two-space indent


def test_bundle_id_naming() -> None:
    assert bundle_id(BundleSpec(scenario="loan", variant="fraud")) == "loan-fraud"
    assert bundle_id(BundleSpec(scenario="kyc", company_index=7)) == "kyc-clean-c07"


def test_dump_schemas(tmp_path: Path) -> None:
    written = schema.dump_schemas(tmp_path)
    names = {p.name for p in written}
    assert {
        "manifest.schema.json",
        "bundle_spec.schema.json",
        "mutation.schema.json",
        "queue.schema.json",
        "name_pair.schema.json",
    } <= names
    loaded = json.loads((tmp_path / "manifest.schema.json").read_text())
    assert loaded["additionalProperties"] is False


def test_layout_tables_match_docs() -> None:
    assert schema.LAYOUT_PRODUCER["dbs_style"][0] == "Mock Composer 9.1"
    assert schema.LAYOUT_PRODUCER["ocbc_style"][0] == "Mock Exstream 16.4"
    assert schema.LAYOUT_PRODUCER["uob_style"][0] == "Mock FOP 2.9"
    assert schema.LAYOUT_PRODUCER["software"][0] == "Mock Ledger 5.0"
    assert schema.LAYOUT_C3["software"] == "software_generated"
    assert [f"Q{i:02d}" for i in range(1, 13)] == schema.DEFAULT_QUIRKS
