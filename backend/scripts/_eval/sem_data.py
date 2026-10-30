"""Build, write, count and load the semantic evaluation data (docs/test-data-and-eval.md section 9).

    uv run python backend/scripts/make_semantic_data.py --out samples/semantic

Three sets per split (``dev`` seed 5101, ``held_out`` seed 7001), all from hand-written catalogues
in ``scripts/_gen/sem_*.py``; no model is called and no network is used. The data is reproducible:
the same seed and generator version give byte-identical files (``manifest.json`` records the
SHA-256 of each).

* ``txn.json``     messy bank-statement descriptions with the category, direction, c4, c5 and
                   counterparty kept as ground truth (TXN feature).
* ``entity.json``  pairs of company names, same legal entity or different (ENTITY feature).
* ``fit.json``     bundles of extracted documents with an injected semantic tamper, matched
                   unusual-but-legitimate controls and ordinary clean cases (FIT feature).

The files contain the answer key next to the text. A harness must show a model only what the
``*_view`` functions return. Hand-written by us and fictitious: every number measured on this data
is an upper bound.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts._gen import sem_entity, sem_fit, sem_txn
from scripts._gen.sem_common import DATA_VERSION, SEEDS, SPLITS, UPPER_NOTE, dump_json, sha256_text

SETS = ("txn", "entity", "fit")


def build_split(split: str, seed: int | None = None) -> dict[str, list[dict[str, Any]]]:
    """All three sets of one split, in memory."""
    return {
        "txn": sem_txn.build_rows(split, seed),
        "entity": sem_entity.build_pairs(split, seed),
        "fit": sem_fit.build_cases(split, seed),
    }


def counts(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Counts per class of one split (what the report and the manifest quote)."""
    txn, ent, fit = data["txn"], data["entity"], data["fit"]
    return {
        "txn": {
            "rows": len(txn),
            "by_category": dict(sorted(Counter(r["truth"]["category"] for r in txn).items())),
            "by_level": dict(sorted(Counter(str(r["level"]) for r in txn).items())),
            "foreign_language_rows": sum(1 for r in txn if r["langs"]),
            "hard_counterparties": sum(1 for r in txn if r["hard_counterparty"]),
        },
        "entity": {
            "pairs": len(ent),
            "by_label": dict(sorted(Counter(p["label"] for p in ent).items())),
            "by_class": dict(sorted(Counter(f"{p['label']}/{p['class']}" for p in ent).items())),
        },
        "fit": {
            "cases": len(fit),
            "by_family_variant": dict(
                sorted(Counter(f"{c['family']}/{c['variant']}" for c in fit).items())
            ),
            "by_family_subtype": dict(
                sorted(Counter(f"{c['family']}/{c['subtype']}" for c in fit).items())
            ),
            "tampered_by_difficulty": dict(
                sorted(
                    Counter(
                        f"{c['family']}/{c['difficulty']}" for c in fit if c["label"] == "tampered"
                    ).items()
                )
            ),
            "by_industry": dict(sorted(Counter(c["industry"] for c in fit).items())),
        },
    }


def write_all(out: Path, seeds: dict[str, int] | None = None) -> dict[str, Any]:
    """Write ``<out>/<split>/{txn,entity,fit}.json`` and ``<out>/manifest.json``."""
    seeds = seeds or SEEDS
    manifest: dict[str, Any] = {
        "data_version": DATA_VERSION,
        "note": UPPER_NOTE,
        "seeds": {s: seeds[s] for s in SPLITS},
        "files": {},
        "counts": {},
    }
    for split in SPLITS:
        data = build_split(split, seeds[split])
        manifest["counts"][split] = counts(data)
        for name in SETS:
            text = dump_json({"split": split, "seed": seeds[split], "rows": data[name]})
            path = out / split / f"{name}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            manifest["files"][f"{split}/{name}.json"] = {
                "sha256": sha256_text(text),
                "bytes": len(text.encode("utf-8")),
                "n": len(data[name]),
            }
    (out / "manifest.json").write_text(dump_json(manifest), encoding="utf-8")
    return manifest


def read_set(out: Path, split: str, name: str) -> list[dict[str, Any]]:
    import json

    return json.loads((out / split / f"{name}.json").read_text(encoding="utf-8"))["rows"]


# --------------------------------------------------------------------------- what a model may see


def txn_view(row: dict[str, Any]) -> dict[str, Any]:
    """The columns of a statement line; no label, no clean form, no diagnostics."""
    return {k: row[k] for k in ("row_id", "date", "description", "debit", "credit")}


def entity_view(pair: dict[str, Any]) -> dict[str, Any]:
    return {k: pair[k] for k in ("pair_id", "a", "b")}


def fit_view(case: dict[str, Any]) -> dict[str, Any]:
    """The documents only (their ids are the document keys); no label, subtype or evidence."""
    return {"case_id": case["case_id"], "documents": case["documents"]}


# ------------------------------------------------------------------------ extracted documents


def _document(doc: dict[str, Any], file_id: str = "f1") -> Any:
    from app.core.models import Cell, Classification, Document, Row, Table
    from app.core.schemas import DOC_SCHEMAS

    schema = DOC_SCHEMAS[doc["doc_type"]]
    fields = {
        name: Cell(value=c["value"], raw=c["raw"], kind=schema.fields[name].kind)
        for name, c in doc["fields"].items()
    }
    tables = {}
    for name, t in doc["tables"].items():
        spec = schema.tables[name]
        rows = [
            Row(
                index=r["index"],
                cells={
                    col: Cell(value=c["value"], raw=c["raw"], kind=spec.columns[col].kind)
                    for col, c in r["cells"].items()
                },
            )
            for r in t["rows"]
        ]
        tables[name] = Table(name=name, columns=list(t["columns"]), rows=rows, pages=[1])
    return Document(
        id=doc["key"],
        file_id=file_id,
        page_start=1,
        page_end=1,
        doc_type=doc["doc_type"],
        classification=Classification(
            label=doc["doc_type"], probability=1.0, classifier="test", version="1"
        ),
        fields=fields,
        tables=tables,
    )


def case_documents(case: dict[str, Any]) -> list[Any]:
    """The case's documents as ``app.core.models.Document`` objects (ids are the document keys)."""
    return [_document(d, f"f{i}") for i, d in enumerate(case["documents"], 1)]


def txn_document(rows: Sequence[dict[str, Any]], doc_id: str = "txn-batch") -> Any:
    """A bank statement whose transactions are ``rows`` (date, description, debit, credit)."""
    from scripts._gen.sem_fit import cell, make_document

    cells = [
        {
            "date": cell(r["date"]),
            "description": cell(r["description"]),
            "debit": cell(r["debit"]),
            "credit": cell(r["credit"]),
            "balance": cell(None),
        }
        for r in rows
    ]
    doc = make_document(doc_id, "bank_statement", {}, {"transactions": cells})
    return _document(doc)
