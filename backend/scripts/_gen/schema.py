"""Generator and manifest models (docs/test-data-and-eval.md sections 3.2 and 3.4).

No import from ``app``: the generator is a standalone tool and its literals are copied here.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

Scenario = Literal["kyc", "aml", "loan"]
Variant = Literal["clean", "fraud", "forger"]
Render = Literal["digital", "scan_hq", "scan_lq", "photo", "scan_pdf"]
Layout = Literal["dbs_style", "ocbc_style", "uob_style", "software"]
Channel = Literal["world", "bundle", "pdf_edit", "metadata", "image"]
Severity = Literal["high", "medium", "low", "info"]
Lane = Literal["edd", "aml", "loan", "fast"]
Band = Literal["low", "medium", "high"]
Verdict = Literal["approve", "request_documents", "refer_edd", "escalate_aml", "reject_or_escalate"]
MutationOp = Literal[
    "override_cell",
    "recompute_chain",
    "drop_document",
    "drop_page",
    "insert_rows",
    "set_field",
    "swap_party",
    "reuse_signature",
    "set_producer",
]

FIXED_TODAY = date(2026, 10, 27)
LAYOUTS: tuple[str, ...] = ("dbs_style", "ocbc_style", "uob_style", "software")
RENDERS: tuple[str, ...] = ("digital", "scan_hq", "scan_lq", "photo", "scan_pdf")
# C3 label per statement layout (labels.json "c3")
LAYOUT_C3: dict[str, str] = {
    "dbs_style": "dbs",
    "ocbc_style": "ocbc",
    "uob_style": "uob",
    "software": "software_generated",
}
# Info /Producer and /Creator per layout (docs section 4.1, forensics normal-producer table)
LAYOUT_PRODUCER: dict[str, tuple[str, str]] = {
    "dbs_style": ("Mock Composer 9.1", "Mock Statement Engine"),
    "ocbc_style": ("Mock Exstream 16.4", "Mock Statement Engine"),
    "uob_style": ("Mock FOP 2.9", "Mock Statement Engine"),
    "software": ("Mock Ledger 5.0", "Mock Ledger Desktop"),
}
DEFAULT_QUIRKS: list[str] = [f"Q{i:02d}" for i in range(1, 13)]
HARD_QUIRKS: list[str] = ["H1", "H2", "H3"]


class G(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Mutation(G):
    """World-channel edit; recorded in BundleSpec so a manifest regenerates itself.

    ``target`` is a doc_key (``bank_statement:2025-09``, ``financial_statement:FY2025``,
    ``invoice:INV-2025-0107``, ``-`` for world level). Args per op are documented in
    ``world.apply_plot``.
    """

    op: MutationOp
    target: str
    args: dict[str, str | int | float | bool | None] = {}


class BundleSpec(G):
    scenario: Scenario
    variant: Variant = "clean"
    company_index: int = 0
    seed: int = 4002
    today: date = FIXED_TODAY
    layout: Layout | None = None
    renders: list[Render] = ["digital"]
    quirks: list[str] = list(DEFAULT_QUIRKS)
    invoice_count: int | None = None
    statement_months: int | None = None
    packet: bool = False
    mutations: list[Mutation] = []


class Box(G):
    file: str
    page: int
    x: float
    y: float
    w: float
    h: float


class Target(G):
    doc_key: str
    table: str | None = None
    row: int | None = None
    field: str | None = None
    box: Box | None = None


class PageEntry(G):
    page: int
    page_sha256: str
    doc_key: str | None


class FileEntry(G):
    path: str
    render: Render
    sha256: str
    size_bytes: int
    mime: Literal["application/pdf", "image/jpeg", "image/png"]
    page_count: int
    pages: list[PageEntry]
    doc_keys: list[str]
    derived_from: str | None = None
    inherited: bool = False


class DocEntry(G):
    key: str
    doc_type: str
    layout: Layout | None
    issuer: str | None
    digital_path: str
    page_start: int
    page_end: int


class Quirk(G):
    id: str
    description: str
    may_trigger: list[str] = []
    hard: bool = False


class Planted(G):
    id: str
    kind: str
    description: str
    deck_slide: int | None
    target: Target | None
    facts: dict[str, str] = {}


class RuleExpectation(G):
    rule: str | list[str]
    min_severity: Severity = "low"
    doc_key: str | None = None


class Expected(G):
    lane: Lane | None = None
    lane_any_of: list[Lane] = []
    band_min: Band | None = None
    band_max: Band | None = None
    verdict: Verdict | Literal["reject_or_escalate"] | None = None
    must_fire: list[RuleExpectation] = []
    must_not_fire: list[str] = []
    max_flags_ge_medium: int | None = None


class TamperRecord(G):
    tamper_id: str
    instance: int
    channel: Channel
    edit: str
    target: Target
    original: dict[str, str | None]
    edited: dict[str, str | None]
    expected_any_of: list[str]
    may_also_fire: list[str]
    hard: bool
    known_miss: bool
    weak: bool
    seed_key: str


class Manifest(G):
    schema_version: Literal["1"] = "1"
    bundle_id: str
    kind: Literal["bundle", "tampered", "ranking_case", "fairness_case", "canary", "gate"]
    use_case: Scenario
    spec: BundleSpec
    generator: dict[str, str]
    applicant: dict[str, str]
    registry_path: str
    files: list[FileEntry]
    documents: list[DocEntry]
    upload_order: dict[Render, list[str]]
    planted: list[Planted] = []
    tampers: list[TamperRecord] = []
    expected: Expected = Expected()
    quirks: list[Quirk] = []
    base_bundle: str | None = None


BundleManifest = Manifest


class QueueSlot(G):
    slot: int
    scenario: Scenario
    company_index: int
    upload_index: int
    cases: dict[Literal["clean", "deck", "forger", "single"], str]
    single_tamper: str


class Assignment(G):
    share: float
    rep: int
    fraud_slots: list[int]
    kind_by_slot: dict[str, Literal["deck", "forger", "single"]]


class QueueFile(G):
    schema_version: Literal["1"] = "1"
    queue_id: str
    seed: int
    slots: list[QueueSlot]
    shares: list[float] = [0.1, 0.2, 0.3, 0.5]
    reps: int = 5
    assignments: list[Assignment]


class NamePair(G):
    pair_id: str
    group: Literal[
        "western_order",
        "chinese_order",
        "malay_patronymic",
        "indian_patronymic",
        "mononym",
        "titles",
    ]
    same_person: bool
    name_a: str
    name_b: str
    form_a: str
    form_b: str


class BundleBuilder(Protocol):
    NAME: str

    def build(self, spec: BundleSpec, out_dir: Path) -> Manifest: ...


SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "bundle_spec": BundleSpec,
    "mutation": Mutation,
    "manifest": Manifest,
    "queue": QueueFile,
    "name_pair": NamePair,
    "tamper_record": TamperRecord,
}


def bundle_id(spec: BundleSpec) -> str:
    """``loan-clean`` for company 0, ``loan-clean-c07`` otherwise."""
    base = f"{spec.scenario}-{spec.variant}"
    return base if spec.company_index == 0 else f"{base}-c{spec.company_index:02d}"


def dump_json(obj: Any) -> str:
    """Byte-stable JSON: UTF-8, sorted keys, 2-space indent, trailing newline."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json(obj), encoding="utf-8")


def dump_schemas(out_dir: Path) -> list[Path]:
    """Write ``<name>.schema.json`` for every public model."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in sorted(SCHEMA_MODELS.items()):
        path = out_dir / f"{name}.schema.json"
        path.write_text(dump_json(model.model_json_schema()), encoding="utf-8")
        written.append(path)
    return written
