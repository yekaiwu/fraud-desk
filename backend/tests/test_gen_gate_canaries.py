"""Gate set and injection canaries (T-gen-loan-13/14)."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pymupdf
import pytest

from scripts import make_bundle
from scripts._gen import canaries, gate, verify
from scripts._gen.schema import Manifest

from .test_gen_helpers import samples_root

PLACEMENTS = ("P1", "P2", "P3", "P4")


@pytest.fixture(scope="module")
def gate_dir() -> tuple[Path, list[str]]:
    root = samples_root()
    short = gate.build_gate_set(root, seed=4002)
    return root / "gate", short


@pytest.fixture(scope="module")
def canary_dirs() -> list[Path]:
    return canaries.build_canaries(samples_root(), seed=4002)


def test_gate_set_has_80_pages_40_digital_40_scanned(gate_dir) -> None:  # noqa: ANN001
    d, _ = gate_dir
    lab = json.loads((d / "labels.json").read_text())
    assert len(lab["pages"]) == 80
    kinds = Counter()
    for p in lab["pages"]:
        name = Path(p["file"]).name
        kinds[
            "digital"
            if name.endswith(".pdf")
            else re.search(r"_(scan_hq|scan_lq|photo)\.jpg$", name).group(1)
        ] += 1  # type: ignore[union-attr]
    assert kinds == {"digital": 40, "scan_hq": 20, "scan_lq": 10, "photo": 10}
    for p in lab["pages"]:
        data = (d / p["file"]).read_bytes()
        import hashlib

        assert hashlib.sha256(data).hexdigest() == p["sha256"] and p["page"] == 1
    m = Manifest.model_validate(json.loads((d / "manifest.json").read_text()))
    assert m.kind == "gate" and len(m.files) == 80


def test_gate_set_c1_coverage_and_shortfalls_name_missing_kyc_labels(gate_dir) -> None:  # noqa: ANN001
    d, short = gate_dir
    lab = json.loads((d / "labels.json").read_text())
    counts = Counter(p["c1"] for p in lab["pages"])
    for label in (
        "bank_statement",
        "invoice",
        "financial_statement",
        "contract",
        "purchase_order",
        "company_profile",
        "national_id",
    ):
        assert counts[label] >= 3, label
    assert counts["boilerplate"] >= 5 and counts["other"] >= 5
    if "kyc" not in make_bundle.discover_builders():
        text = "\n".join(short)
        for missing in (
            "certificate_of_incorporation",
            "ownership_chart",
            "shareholder_register",
            "source_of_funds_declaration",
            "passport",
        ):
            assert missing in text and "gen-kyc" in text
        assert "bank_statement" not in text


def test_gate_set_statements_cover_all_layouts_with_row_truth(gate_dir) -> None:  # noqa: ANN001
    d, _ = gate_dir
    lab = json.loads((d / "labels.json").read_text())
    stmt = [p for p in lab["pages"] if p["c1"] == "bank_statement"]
    assert {p["c3"] for p in stmt} == {"dbs", "ocbc", "uob", "software_generated"}
    digital = [p for p in stmt if p["file"].endswith(".pdf")]
    assert {p["c3"] for p in digital} == {"dbs", "ocbc", "uob", "software_generated"}
    keys = {p["doc_key"] for p in stmt}
    rows = [r for r in lab["rows"] if r["doc_key"] in keys]
    assert rows and all(r["c5"] for r in rows)
    assert all(r["c4"] for r in rows if r["c4"] is not None)
    assert any(r["c4"] for r in rows)
    gt = json.loads((d / "ground_truth.json").read_text())
    assert len(gt["pages"]) == 80 and all(p["lines"] for p in gt["pages"])
    scan = next(p for p in lab["pages"] if p["file"].endswith(".jpg"))
    with pymupdf.open(
        d / next(p["file"] for p in lab["pages"] if p["file"].endswith(".pdf"))
    ) as pdf:
        assert pdf[0].get_text().strip()
    assert scan["sha256"] and scan["c1"]


def test_gate_set_is_deterministic(tmp_path: Path) -> None:
    gate.build_gate_set(tmp_path, seed=4002)
    a = (tmp_path / "gate" / "labels.json").read_bytes()
    first = {p.name: p.read_bytes() for p in (tmp_path / "gate" / "files").glob("*.pdf")}
    gate.build_gate_set(tmp_path, seed=4002)
    assert (tmp_path / "gate" / "labels.json").read_bytes() == a
    assert first == {p.name: p.read_bytes() for p in (tmp_path / "gate" / "files").glob("*.pdf")}


def test_canaries_two_loan_hosts_times_four_placements(canary_dirs: list[Path]) -> None:
    names = {p.name for p in canary_dirs}
    for host in ("statement", "invoice"):
        assert f"canary-{host}-control" in names
        for pl in PLACEMENTS:
            assert f"canary-{host}-{pl}" in names
    for p in canary_dirs:
        m = json.loads((p / "manifest.json").read_text())
        assert m["kind"] == "canary"
        if not p.name.endswith("control"):
            ctl = (p / m["base_bundle"]).resolve()
            assert ctl.exists() and ctl.parent.name.endswith("control")
            assert m["planted"][0]["facts"]["text"]
        assert verify.w12_files(p, m) == []


def _text(p: Path) -> list[str]:
    m = json.loads((p / "manifest.json").read_text())
    with pymupdf.open(p / m["files"][0]["path"]) as d:
        return [pg.get_text() for pg in d]


def test_canary_placements_p1_p2_p3(canary_dirs: list[Path]) -> None:
    root = canary_dirs[0].parent
    for host in ("statement", "invoice"):
        ctl = _text(root / f"canary-{host}-control")
        inj = {
            pl: json.loads((root / f"canary-{host}-{pl}" / "manifest.json").read_text())["planted"][
                0
            ]["facts"]["text"]
            for pl in PLACEMENTS
        }
        assert all(t not in "\n".join(ctl) for t in inj.values())
        p1 = _text(root / f"canary-{host}-P1")
        assert inj["P1"] in p1[-1] and len(p1) == len(ctl)
        p2 = _text(root / f"canary-{host}-P2")
        assert len(p2) == len(ctl) + 1 and inj["P2"] in p2[-1] and p2[:-1] == ctl
        p3dir = root / f"canary-{host}-P3"
        m = json.loads((p3dir / "manifest.json").read_text())
        with pymupdf.open(p3dir / m["files"][0]["path"]) as d:
            spans = [
                s
                for b in d[0].get_text("dict")["blocks"]
                for ln in b.get("lines", [])
                for s in ln["spans"]
                if inj["P3"] in s["text"]
            ]
        assert spans and spans[0]["color"] == 0xFFFFFF and spans[0]["size"] <= 1.01


def test_canary_p4_injection_lives_in_the_data(canary_dirs: list[Path]) -> None:
    root = canary_dirs[0].parent
    for host, table, col in (
        ("statement", "transactions", "description"),
        ("invoice", "lines", "description"),
    ):
        d = root / f"canary-{host}-P4"
        m = json.loads((d / "manifest.json").read_text())
        text = m["planted"][0]["facts"]["text"]
        gt = json.loads((d / "ground_truth.json").read_text())
        cells = [r["cells"][col]["value"] for r in gt["documents"][0]["tables"][table]["rows"]]
        assert text in cells
        assert any(text in t for t in _text(d))
    labels = json.loads((root / "canary-statement-P2" / "labels.json").read_text())
    assert [p["c1"] for p in labels["pages"]][-1] == "other"


def test_injection_strings_cycle(canary_dirs: list[Path]) -> None:
    used = []
    for p in sorted(canary_dirs):
        m = json.loads((p / "manifest.json").read_text())
        if m["planted"]:
            used.append(m["planted"][0]["facts"]["text"])
    assert set(used) <= set(canaries.INJECTIONS) and len(set(used)) >= 6
