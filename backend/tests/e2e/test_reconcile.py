"""T-integration-loan-02: the wave-1 pieces agree with each other on the generated loan bundles.

Text-layer parser vs generator layouts, classifier cues vs generator header texts, C4/C5 lexicon vs
annotations, producer strings vs ``EXPECTED_PRODUCERS``, applicant resolution, ``FileRef.pdf_meta``,
page-fraction boxes vs highlight regions. Every number here is measured on real generator output.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from scripts import make_bundle
from scripts._gen.schema import BundleSpec, Mutation
from tests.e2e._support import Run, run_bundle, temp_root

VARIANTS = ("clean", "fraud", "forger")
TRADE_C4 = {"trade_receipt", "customer_payment"}  # the revenue check treats both as trade
EXTRACTED = {"bank_statement", "invoice", "financial_statement", "contract", "purchase_order"}


def _truth(run: Run) -> tuple[dict, dict]:
    return (
        json.loads((run.bundle_dir / "labels.json").read_text(encoding="utf-8")),
        json.loads((run.bundle_dir / "ground_truth.json").read_text(encoding="utf-8")),
    )


def _norm(value: str | None) -> str | None:
    return None if value is None else " ".join(value.casefold().split())


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_check_ran_or_was_skipped_with_a_reason_and_no_stage_failed(
    loan_runs: dict[str, Run], variant: str
) -> None:
    case = loan_runs[variant].case
    assert [s.status for s in case.stages] == ["ok"] * 7
    assert not [r for r in case.check_runs if r.status == "error"]
    assert all(r.reason for r in case.check_runs if r.status == "skipped")
    assert case.partial is False and case.partial_reasons == []


@pytest.mark.parametrize("variant", VARIANTS)
def test_classifier_cues_match_generator_header_texts(
    loan_runs: dict[str, Run], variant: str
) -> None:
    """C1 on real generator pages: every page right except the deliberately blank page (Q12),
    and every page of a real document is confident enough not to be 'unclassified'."""
    run = loan_runs[variant]
    labels, _ = _truth(run)
    by_sha = {f.sha256: f for f in run.case.files}
    wrong, weak = [], []
    for p in labels["pages"]:
        info = by_sha[p["sha256"]].pages[p["page"] - 1]
        assert info.classification is not None
        if info.classification.label != p["c1"]:
            wrong.append((p["file"], p["page"], p["c1"], info.classification.label))
        elif info.classification.probability < run.settings.classifier_min_prob:
            weak.append((p["file"], p["page"], info.classification.probability))
    assert wrong == [("digital/invoice_INV-2025-0104.pdf", 2, "boilerplate", "other")]
    assert weak == []
    # C3: every statement's issuer is the layout's bank
    issuers = {d.issuer.label for d in run.case.documents if d.doc_type == "bank_statement"}
    assert issuers == {"dbs"}
    # segmentation: one document per manifest document, plus the unrecognised blank page
    expected = defaultdict(int)
    for d in run.manifest["documents"]:
        expected[d["doc_type"]] += 1
    got: dict[str, int] = defaultdict(int)
    for d in run.case.documents:
        got[d.doc_type] += 1
    assert got.pop("other") == 1
    assert dict(got) == dict(expected)


@pytest.mark.parametrize("variant", VARIANTS)
def test_c4_c5_lexicon_agrees_with_row_annotations(loan_runs: dict[str, Run], variant: str) -> None:
    run = loan_runs[variant]
    labels, _ = _truth(run)
    name_of = {f.id: f.name for f in run.case.files}
    docs = {name_of[d.file_id]: d for d in run.case.documents if d.doc_type == "bank_statement"}
    ok4 = n4 = ok5 = n5 = 0
    wrong_nontrade = []
    for row in labels["rows"]:
        doc = docs[f"bank_statement_{row['doc_key'].split(':')[1]}.pdf"]
        ann = doc.tables["transactions"].rows[row["row"]].annotations
        n5 += 1
        ok5 += bool(ann.get("c5") and ann["c5"].label == row["c5"])
        if row["c4"]:
            n4 += 1
            got = ann["c4"].label if ann.get("c4") else None
            same = got == row["c4"] or (got in TRADE_C4 and row["c4"] in TRADE_C4)
            ok4 += same
            if not same and row["c4"] not in TRADE_C4:
                wrong_nontrade.append((row["c4"], got))
    assert n4 == 61 and n5 == 234
    assert ok4 / n4 >= 0.95  # trade vs customer payment are interchangeable for the checks
    assert wrong_nontrade == []  # no loan, owner transfer or refund ever read as sales
    assert ok5 / n5 >= 0.85  # ~10% of counterparties are 'hard rows' left to the model (docs 4.1)


@pytest.mark.parametrize("variant", VARIANTS)
def test_text_layer_extraction_matches_the_ground_truth(
    loan_runs: dict[str, Run], variant: str
) -> None:
    """Fields and table rows of the five financial document types are read exactly as printed
    (canonical-equal: the generator's truth is cased, the page prints the bank's casing)."""
    run = loan_runs[variant]
    _, truth = _truth(run)
    by_name = {f.name: f for f in run.case.files}
    mismatches = []
    checked = 0
    for t in truth["documents"]:
        if t["doc_type"] not in EXTRACTED:
            continue
        file = by_name[Path(t["sources"][0]["file"]).name]
        doc = next(
            d for d in run.case.documents if d.file_id == file.id and d.doc_type == t["doc_type"]
        )
        for name, tv in t["fields"].items():
            if tv["value"] is None:
                continue
            checked += 1
            cell = doc.fields.get(name)
            if cell is None or _norm(cell.value) != _norm(tv["value"]):
                mismatches.append((t["key"], name, tv["value"], cell.value if cell else None))
        for tname, tt in t["tables"].items():
            table = doc.tables.get(tname)
            for i, row in enumerate(tt["rows"]):
                for col, cv in row["cells"].items():
                    if cv["value"] is None:
                        continue
                    checked += 1
                    got = table.rows[i].cells.get(col) if table and i < len(table.rows) else None
                    if got is None or _norm(got.value) != _norm(cv["value"]):
                        mismatches.append((t["key"], tname, i, col, cv["value"], got and got.value))
    assert checked > 1500
    assert mismatches == []


@pytest.mark.parametrize("variant", VARIANTS)
def test_applicant_resolution_and_pdf_meta(loan_runs: dict[str, Run], variant: str) -> None:
    run = loan_runs[variant]
    assert run.case.applicant.uen == run.manifest["applicant"]["uen"]
    assert run.case.applicant.name == run.manifest["applicant"]["name"]
    for f in run.case.files:
        assert {"Producer", "Creator", "CreationDate"} <= set(f.pdf_meta), f.name
        assert f.pages and not any(p.is_scan for p in f.pages)
        # every page has a text layer except the deliberately blank page (footer only, Q12)
        no_text = [p.page for p in f.pages if not p.has_text_layer]
        assert no_text == ([2] if f.name == "invoice_INV-2025-0104.pdf" else []), f.name


@pytest.mark.parametrize("variant", VARIANTS)
def test_producer_strings_agree_with_the_expected_producer_table(
    loan_runs: dict[str, Run], variant: str
) -> None:
    run = loan_runs[variant]
    ran = {r.rule for r in run.case.check_runs if r.status == "ran"}
    assert {"pdf_producer_unexpected", "pdf_editor_software", "pdf_modified"} <= ran
    assert not [
        f for f in run.case.flags if f.rule.startswith("pdf_") or f.rule.startswith("font_")
    ]


def test_a_foreign_statement_producer_is_flagged() -> None:
    """The other direction (T32c): same bundle, one statement stamped by a word processor."""
    root = temp_root("gldb-e2e-producer-")
    spec = BundleSpec(
        scenario="loan",
        variant="clean",
        mutations=[
            Mutation(
                op="set_producer",
                target="bank_statement:2025-09",
                args={"producer": "Mock Word Processor 3.1", "creator": "Mock Word Processor 3.1"},
            )
        ],
    )
    bdir = root / "bundles" / "loan-producer"
    make_bundle.build_bundle(spec, bdir)
    run = run_bundle(bdir, tmp=root / "run")
    flags = run.flags("pdf_producer_unexpected")
    assert len(flags) == 1
    doc = next(d for d in run.case.documents if d.id == flags[0].document_ids[0])
    assert (
        next(f.name for f in run.case.files if f.id == doc.file_id) == "bank_statement_2025-09.pdf"
    )


@pytest.mark.parametrize("variant", VARIANTS)
def test_flag_ids_and_regions_are_well_formed(loan_runs: dict[str, Run], variant: str) -> None:
    case = loan_runs[variant].case
    ids = [f.id for f in case.flags]
    assert len(ids) == len(set(ids))
    for flag in case.flags:
        # <rule>.<docs>.<locator>[~discriminator]: exactly two dots, discriminator charset
        head, _, disc = flag.id.partition("~")
        assert head.count(".") == 2, flag.id
        assert re.fullmatch(r"[a-z0-9_:]*", disc), flag.id
        for r in [flag.region, *flag.extra_regions]:
            if r is not None:
                assert 0.0 <= r.x <= 1.0 and 0.0 <= r.y <= 1.0
                assert r.x + r.w <= 1.0001 and r.y + r.h <= 1.0001
                assert any(f.id == r.file_id for f in case.files)
