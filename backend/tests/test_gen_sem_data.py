"""The semantic evaluation data as files: reproducible writer, manifest, counts, public hygiene
(docs/test-data-and-eval.md section 9). No model is called."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts._eval import sem_data as sd
from scripts._gen.sem_common import DATA_VERSION, SEEDS, SPLITS

ROOT = Path(__file__).resolve().parents[2]
NEW_FILES = [
    *sorted((ROOT / "backend/scripts/_gen").glob("sem_*.py")),
    ROOT / "backend/scripts/_eval/sem_data.py",
    ROOT / "backend/scripts/make_semantic_data.py",
    *sorted((ROOT / "backend/tests").glob("test_gen_sem_*.py")),
]


@pytest.fixture(scope="module")
def written(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("sem")
    return out, sd.write_all(out)


def test_writer_is_byte_reproducible_and_the_manifest_matches_the_files(
    written: tuple[Path, dict], tmp_path: Path
) -> None:
    out, manifest = written
    again = sd.write_all(tmp_path)
    assert again == manifest
    assert manifest["data_version"] == DATA_VERSION
    assert manifest["seeds"] == {"dev": 5101, "held_out": 7001} == SEEDS
    assert set(manifest["files"]) == {f"{s}/{n}.json" for s in SPLITS for n in sd.SETS}
    for rel, meta in manifest["files"].items():
        data = (out / rel).read_bytes()
        assert (
            hashlib.sha256(data).hexdigest()
            == meta["sha256"]
            == hashlib.sha256((tmp_path / rel).read_bytes()).hexdigest()
        )
        assert len(data) == meta["bytes"]
        body = json.loads(data)
        assert len(body["rows"]) == meta["n"]
        assert body["seed"] == SEEDS[body["split"]]
    assert "upper bound" in manifest["note"]


def test_counts_in_the_manifest(written: tuple[Path, dict]) -> None:
    _out, manifest = written
    for split in SPLITS:
        c = manifest["counts"][split]
        assert c["txn"]["rows"] == 384 and len(c["txn"]["by_category"]) == 14
        assert c["txn"]["by_category"]["customer_receipt"] == 48
        assert sum(c["txn"]["by_level"].values()) == 384
        assert c["entity"]["pairs"] == 384 and c["entity"]["by_label"] == {
            "different": 192,
            "same": 192,
        }
        assert len(c["entity"]["by_class"]) == 16
        assert c["fit"]["cases"] == 192
        assert c["fit"]["by_family_variant"]["goods_fit/tampered"] == 24
        assert c["fit"]["by_family_variant"]["income_fit/ordinary"] == 16
        assert len(c["fit"]["by_industry"]) == 8


def test_read_back_equals_build(written: tuple[Path, dict]) -> None:
    out, _ = written
    assert sd.read_set(out, "dev", "entity") == sd.build_split("dev")["entity"]
    assert sd.read_set(out, "held_out", "txn")[0]["split"] == "held_out"


def test_txn_batch_becomes_a_statement_document() -> None:
    rows = sd.build_split("dev")["txn"][:5]
    doc = sd.txn_document(rows)
    table = doc.tables["transactions"]
    assert [r.cells["description"].value for r in table.rows] == [r["description"] for r in rows]
    assert doc.doc_type == "bank_statement"


def test_files_and_generated_data_are_public_safe(written: tuple[Path, dict]) -> None:
    out, _ = written
    sources = [(p.name, p.read_text(encoding="utf-8")) for p in NEW_FILES]
    sources += [
        (str(p.relative_to(out)), p.read_text(encoding="utf-8"))
        for p in sorted(out.rglob("*.json"))
    ]
    for name, text in sources:
        assert "/Us" + "ers/" not in text, name  # no absolute home path
        assert not re.search(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", text), name  # no email address
        assert not re.search(r"https?://", text), name
        assert not re.search(r"\+\d{2}[ -]?\d{3,4}", text), name  # no phone number
    # the generator modules never touch a model
    for p in NEW_FILES:
        if p.name.startswith("test_"):
            continue
        text = p.read_text(encoding="utf-8")
        for banned in ("anthropic", "openai", "LLMClient", "app.core.llm", "requests", "urllib"):
            assert banned not in text, (p.name, banned)


def test_cli_writes_the_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scripts import make_semantic_data

    assert make_semantic_data.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / "manifest.json").exists()
    assert "wrote 6 files" in capsys.readouterr().out
