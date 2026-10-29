"""Loan bundles: clean control, determinism, variants, labels, packet, CLI (T-gen-loan-08/09/10/12/15/16)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pymupdf

from scripts import make_bundle
from scripts._gen import schema, verify
from scripts._gen.schema import BundleSpec, Manifest

from .test_gen_helpers import doc, docs_of, field, load, loan_bundle

D = Decimal
BACKEND = Path(__file__).resolve().parents[1]


def _bytes(bdir: Path) -> dict[str, str]:
    return {
        str(p.relative_to(bdir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(bdir.rglob("*"))
        if p.is_file()
    }


def test_clean_control_verifies_and_has_no_plants() -> None:
    b = loan_bundle("clean")
    assert verify.verify_bundle(b) == []
    m = load(b, "manifest.json")
    assert m["planted"] == [] and m["tampers"] == [] and m["spec"]["variant"] == "clean"
    assert m["bundle_id"] == "loan-clean" and m["use_case"] == "loan" and m["kind"] == "bundle"
    assert m["expected"]["max_flags_ge_medium"] == 0 and m["expected"]["lane_any_of"] == [
        "fast",
        "loan",
    ]
    assert m["expected"]["verdict"] == "approve" and m["expected"]["band_max"] == "low"


def test_clean_control_contents_match_the_design() -> None:
    b = loan_bundle("clean")
    gt = load(b, "ground_truth.json")
    kinds = {}
    for d in gt["documents"]:
        kinds[d["doc_type"]] = kinds.get(d["doc_type"], 0) + 1
    assert kinds == {
        "company_profile": 1,
        "national_id": 2,
        "bank_statement": 6,
        "financial_statement": 2,
        "invoice": 14,
        "contract": 3,
        "purchase_order": 4,
    }
    months = sorted(d["key"] for d in docs_of(gt, "bank_statement"))
    assert months == [f"bank_statement:2025-{m:02d}" for m in range(7, 13)]
    for d in docs_of(gt, "bank_statement"):
        n = len(d["tables"]["transactions"]["rows"])
        assert 34 <= n <= 59
        assert len(d["tables"]["page_chain"]["rows"]) in (2, 3)
    assert sorted(d["key"] for d in docs_of(gt, "financial_statement")) == [
        "financial_statement:FY2024",
        "financial_statement:FY2025",
    ]
    assert (
        field(doc(gt, "financial_statement:FY2025"), "company_name")
        == "Meridian Fastener Supplies Pte. Ltd."
    )


def test_every_page_of_every_file_is_labelled_and_lined() -> None:
    b = loan_bundle("clean")
    m = Manifest.model_validate(load(b, "manifest.json"))
    lab = load(b, "labels.json")
    gt = load(b, "ground_truth.json")
    page_keys = {(p["file"], p["page"]) for p in lab["pages"]}
    expected = {(f.path, i) for f in m.files for i in range(1, f.page_count + 1)}
    assert page_keys == expected
    assert all(p["c1"] for p in lab["pages"])
    assert {(p["file"], p["page"]) for p in gt["pages"]} == expected
    assert all(p["lines"] for p in gt["pages"])
    starts = [p for p in lab["pages"] if p["segment_start"]]
    assert len(starts) >= len(m.documents)
    sha = {f.path: f.sha256 for f in m.files}
    assert all(p["sha256"] == sha[p["file"]] for p in lab["pages"])
    for f in m.files:
        for pe in f.pages:
            assert pe.page_sha256 == hashlib.sha256(f"{f.sha256}:{pe.page}".encode()).hexdigest()
    st = [d for d in lab["documents"] if d["c1"] == "bank_statement"]
    assert st and all(d["c3"] == "dbs" for d in st)
    credits = [r for r in lab["rows"] if r["c4"]]
    assert credits and all(r["c5"] for r in lab["rows"])


def test_two_builds_are_byte_identical_and_a_new_seed_differs(tmp_path: Path) -> None:
    spec = BundleSpec(scenario="loan", variant="fraud")
    make_bundle.build_bundle(spec, tmp_path / "a")
    make_bundle.build_bundle(spec, tmp_path / "b")
    assert _bytes(tmp_path / "a") == _bytes(tmp_path / "b")
    make_bundle.build_bundle(spec.model_copy(update={"seed": 4003}), tmp_path / "c")
    ha, hc = _bytes(tmp_path / "a"), _bytes(tmp_path / "c")
    assert (
        ha["ground_truth.json"] != hc["ground_truth.json"]
        and ha["manifest.json"] != hc["manifest.json"]
    )


def test_build_is_stable_across_processes_with_different_hash_seeds(tmp_path: Path) -> None:
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from pathlib import Path; from scripts import make_bundle; from scripts._gen.schema import BundleSpec;"
        "make_bundle.build_bundle(BundleSpec(scenario='loan', variant='forger'), Path(sys.argv[2]))"
    )
    outs = []
    for i, hs in enumerate(("1", "12345")):
        out = tmp_path / f"p{i}"
        subprocess.run(
            [sys.executable, "-c", code, str(BACKEND), str(out)],
            check=True,
            env={**os.environ, "PYTHONHASHSEED": hs},
            timeout=100,
        )
        outs.append(_bytes(out))
    assert outs[0] == outs[1]


def test_no_wall_clock_in_pdfs_or_json() -> None:
    b = loan_bundle("clean")
    for p in sorted((b / "digital").glob("bank_statement_*.pdf")):
        with pymupdf.open(p) as d:
            md = d.metadata
        assert md["creationDate"] == md["modDate"]
        assert md["creationDate"].startswith("D:2025") or md["creationDate"].startswith("D:2026")
    # a different `today` changes nothing about historical documents but the manifest records it
    m = load(b, "manifest.json")
    assert m["spec"]["today"] == "2026-10-27"


def test_variants_differ_only_in_planted_targets() -> None:
    clean, fraud, forger = (loan_bundle(v) for v in ("clean", "fraud", "forger"))
    gc, gf, gg = (load(b, "ground_truth.json") for b in (clean, fraud, forger))
    for other, gto in ((fraud, gf), (forger, gg)):
        m = load(other, "manifest.json")
        planted = {p["target"]["doc_key"] for p in m["planted"]}
        changed = {
            d["key"]
            for d, c in zip(gto["documents"], gc["documents"], strict=True)
            if json.dumps(_strip(d)) != json.dumps(_strip(c))
        }
        assert changed and changed <= planted, changed - planted
        # untouched documents are byte for byte the same files
        mc = {f["path"]: f["sha256"] for f in load(clean, "manifest.json")["files"]}
        mo = {f["path"]: f["sha256"] for f in m["files"]}
        same = {p for p in mc if mc[p] == mo[p]}
        assert len(same) >= len(mc) - len(planted)
        for d in m["documents"]:
            if d["key"] not in planted:
                assert mc[d["digital_path"]] == mo[d["digital_path"]]
    reg = [(b.parent.parent / "registry.json").read_bytes() for b in (clean, fraud, forger)]
    assert reg[0] == reg[1] == reg[2]


def _strip(d: dict) -> dict:
    """Ground truth minus per-render file paths (identical anyway)."""
    return {"fields": d["fields"], "tables": d["tables"]}


def test_fraud_and_forger_story_numbers() -> None:
    for variant in ("fraud", "forger"):
        b = loan_bundle(variant)
        gt, m = load(b, "ground_truth.json"), load(b, "manifest.json")
        assert verify.verify_bundle(b) == []
        fs = doc(gt, "financial_statement:FY2025")
        rev = next(
            r
            for r in fs["tables"]["income_statement"]["rows"]
            if r["cells"]["line"]["value"] == "revenue"
        )
        assert rev["cells"]["current"]["value"] == "2400000.00"
        ids = {p["id"] for p in m["planted"]}
        assert ids == {"P1", "P2a", "P2b", "P2c", "P2d", "P3a", "P3b", "P3c"}
        p1 = next(p for p in m["planted"] if p["id"] == "P1")
        assert (
            p1["facts"] == {"revenue_printed": "2400000.00", "revenue_true": "900000.00"}
            and p1["deck_slide"] == 16
        )
        assert p1["target"]["box"]["file"].startswith("digital/") and p1["target"]["row"] == 0
        paid_no_credit = [p for p in m["planted"] if p["kind"] == "paid_without_receipt"]
        assert len(paid_no_credit) == 4
        over = [p for p in m["planted"] if p["kind"] == "invoice_over_contract"]
        assert len(over) == 3
        for p in over:
            assert D(p["facts"]["subtotal"]) >= D(p["facts"]["contract_value"]) * D("1.10")
        exp = m["expected"]
        assert (
            exp["lane"] == "loan"
            and exp["band_min"] == "medium"
            and exp["verdict"] == "reject_or_escalate"
        )
        rules = [r["rule"] for r in exp["must_fire"]]
        assert (
            "x_revenue_vs_credits" in rules
            and "x_invoice_vs_receipts" in rules
            and "x_invoice_vs_contract" in rules
        )
    ex_f = load(loan_bundle("fraud"), "manifest.json")["expected"]
    ex_g = load(loan_bundle("forger"), "manifest.json")["expected"]
    assert any(
        isinstance(r["rule"], list) and "fs_subtotals" in r["rule"] for r in ex_f["must_fire"]
    )
    assert ex_f["must_not_fire"] == ["x_cash_at_year_end"]
    assert {
        "fs_balance",
        "fs_subtotals",
        "fs_cash_tie",
        "fs_comparatives",
        "x_cash_at_year_end",
        "bs_totals",
    } <= set(ex_g["must_not_fire"])


def test_fraud_slip_is_visible_and_forger_balances() -> None:
    gf = load(loan_bundle("fraud"), "ground_truth.json")
    gg = load(loan_bundle("forger"), "ground_truth.json")
    bad = verify.fs_doc_violations(doc(gf, "financial_statement:FY2025"))
    assert any("gross_profit" in v for v in bad)
    assert verify.fs_doc_violations(doc(gg, "financial_statement:FY2025")) == []
    assert verify.fs_doc_violations(doc(gg, "financial_statement:FY2024")) == []
    # the forger recomputed receivables so the balance sheet still balances and cash is unchanged
    gc = load(loan_bundle("clean"), "ground_truth.json")

    def line(g: dict, table: str, name: str) -> Decimal:
        r = next(
            r
            for r in doc(g, "financial_statement:FY2025")["tables"][table]["rows"]
            if r["cells"]["line"]["value"] == name
        )
        return D(r["cells"]["current"]["value"])

    assert line(gg, "balance_sheet", "trade_receivables") > line(
        gc, "balance_sheet", "trade_receivables"
    )
    assert line(gg, "balance_sheet", "cash_and_equivalents") == line(
        gc, "balance_sheet", "cash_and_equivalents"
    )
    assert line(gg, "cash_flow", "cash_closing") == line(gc, "cash_flow", "cash_closing")


def test_trade_credits_vs_revenue_in_the_clean_world() -> None:
    b = loan_bundle("clean")
    gt, lab = load(b, "ground_truth.json"), load(b, "labels.json")
    c4 = {(r["doc_key"], r["row"]): r["c4"] for r in lab["rows"]}
    total = D(0)
    for s in docs_of(gt, "bank_statement"):
        for r in s["tables"]["transactions"]["rows"]:
            if c4.get((s["key"], r["index"])) in ("trade_receipt", "customer_payment"):
                total += D(r["cells"]["credit"]["value"])
    annual = total * D(365) / D(184)
    assert D("0.92") <= annual / D(900000) <= D("1.08")
    assert load(b, "manifest.json")["applicant"]["name"] == "Meridian Fastener Supplies Pte. Ltd."


def test_registry_is_loadable_by_the_app_and_sorted() -> None:
    from app.core.registry import load_registry

    b = loan_bundle("clean")
    reg_path = b.parent.parent / "registry.json"
    reg = load_registry(reg_path)
    assert reg is not None and reg.entities
    ent = reg.lookup(load(b, "manifest.json")["applicant"]["uen"])
    assert ent is not None and ent.entity_name == "Meridian Fastener Supplies Pte. Ltd."
    assert [o.name for o in ent.officers] == ["LIM HUI MIN", "MOHD FAIZAL BIN RAHMAT"]
    assert sum(float(s.percent) for s in ent.shareholders) == 100.0
    uens = [e.uen for e in reg.entities]
    assert uens == sorted(uens)
    from stdnum.sg import uen as sg_uen

    assert all(sg_uen.is_valid(u) for u in uens)


def test_build_registry_is_union_sorted_and_deduplicated(tmp_path: Path) -> None:
    ms = [
        make_bundle.build_bundle(
            BundleSpec(scenario="loan", variant=v, company_index=i), tmp_path / f"{v}{i}"
        )
        for v, i in (("clean", 0), ("fraud", 0), ("clean", 2))
    ]
    out = tmp_path / "registry.json"
    make_bundle.build_registry(out, ms)
    data = json.loads(out.read_text())
    assert len(data["entities"]) == 2
    assert [e["uen"] for e in data["entities"]] == sorted(e["uen"] for e in data["entities"])
    assert data["as_of"] == "2026-09-30" and data["generated_by"] == "make_bundle.py"


def test_packet_segment_starts_and_page_counts() -> None:
    b = loan_bundle("clean", packet=True)
    m = Manifest.model_validate(load(b, "manifest.json"))
    lab = load(b, "labels.json")
    packet_pages = [p for p in lab["pages"] if p["file"] == "packet/packet.pdf"]
    digital_pages = sum(f.page_count for f in m.files if f.path.startswith("digital/"))
    assert len(packet_pages) == digital_pages
    assert [p["page"] for p in packet_pages] == list(range(1, digital_pages + 1))
    starts = [p["doc_key"] for p in packet_pages if p["segment_start"] and p["c1"] != "boilerplate"]
    assert starts == [d.key for d in m.documents]
    assert any(
        p["c1"] == "boilerplate" and p["segment_start"] for p in packet_pages
    )  # Q12 blank invoice page
    with pymupdf.open(b / "packet/packet.pdf") as d:
        assert len(d) == digital_pages


def test_cli_builds_three_variants_and_registry(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    rc = make_bundle.main(["--scenario", "loan", "--variant", "all", "--out", str(tmp_path)])
    assert rc == 0
    for v in ("clean", "fraud", "forger"):
        assert make_bundle.verify_bundle(tmp_path / "bundles" / f"loan-{v}") == []
    assert (tmp_path / "registry.json").exists()
    out = capsys.readouterr().out
    assert "built loan-fraud" in out
    rc = make_bundle.main(["--verify", str(tmp_path / "bundles" / "loan-clean")])
    assert rc == 0
    rc = make_bundle.main(["--dump-schemas", str(tmp_path / "schemas")])
    assert rc == 0 and (tmp_path / "schemas" / "manifest.schema.json").exists()


def test_cli_reports_missing_builders_clearly(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    builders = make_bundle.discover_builders()
    assert "loan" in builders
    if "kyc" in builders:
        return  # gen-kyc has landed
    rc = make_bundle.main(["--scenario", "kyc", "--out", str(tmp_path)])
    assert rc == 0
    assert "not available yet" in capsys.readouterr().out
    try:
        make_bundle.build_bundle(BundleSpec(scenario="kyc"), tmp_path / "x")
    except make_bundle.BuilderNotAvailable as exc:
        assert "not available yet" in str(exc)
    else:
        raise AssertionError("expected BuilderNotAvailable")


def test_builders_are_discovered_without_editing_make_bundle() -> None:
    names = make_bundle.discover_builders()
    assert names["loan"].NAME == "loan"
    assert all(callable(m.build) for m in names.values())


def test_clean_pool_companies_verify_across_layouts() -> None:
    layouts = set()
    for idx in (1, 2, 3, 4):
        b = loan_bundle("clean", idx)
        assert verify.verify_bundle(b) == [], idx
        m = load(b, "manifest.json")
        layouts.update(d["layout"] for d in m["documents"] if d["layout"])
        assert m["bundle_id"] == f"loan-clean-c{idx:02d}"
    assert layouts == set(schema.LAYOUTS)


def test_ground_truth_matches_the_app_document_schemas() -> None:
    """Every schema field and table is present with exactly the data-model section 6 names."""
    from app.core.schemas import DOC_SCHEMAS

    gt = load(loan_bundle("clean"), "ground_truth.json")
    seen = set()
    for d in gt["documents"]:
        schema = DOC_SCHEMAS[d["doc_type"]]
        assert set(d["fields"]) == set(schema.fields), d["key"]
        assert set(d["tables"]) == set(schema.tables), d["key"]
        for name, spec in schema.tables.items():
            assert d["tables"][name]["columns"] == list(spec.columns)
            for r in d["tables"][name]["rows"]:
                assert set(r["cells"]) == set(spec.columns)
        seen.add(d["doc_type"])
    assert seen >= {
        "bank_statement",
        "invoice",
        "financial_statement",
        "contract",
        "purchase_order",
        "company_profile",
        "national_id",
    }


def test_oracle_fake_llm_answers_from_the_bundle() -> None:
    from app.core.llm import FakeLLMClient, LLMRequest

    b = loan_bundle("clean")
    m = load(b, "manifest.json")
    fake = FakeLLMClient.from_ground_truth(b / "manifest.json")
    f = next(f for f in m["files"] if f["path"] == "digital/bank_statement_2025-07.pdf")
    req = LLMRequest(
        purpose="extract:bank_statement",
        system="s",
        user="u",
        schema_name="x",
        json_schema={"type": "object"},
        cache_extra=f"{f['sha256']}:1:text",
    )
    data = fake.complete_json(req).data
    rows = data["tables"]["transactions"]
    assert (
        rows
        and rows[0]["date"]
        and data["fields"]["account_holder"] == "MERIDIAN FASTENER SUPPLIES PTE. LTD."
    )
    ocr = fake.complete_json(req.model_copy(update={"purpose": "ocr:page"})).data
    assert ocr["lines"] and {"text", "x", "y", "w", "h"} <= set(ocr["lines"][0])


def test_cli_quirk_hard_pool_and_fairness_message(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    assert make_bundle.main(["--scenario", "loan", "--quirk-hard", "--out", str(tmp_path)]) == 0
    assert make_bundle.verify_bundle(tmp_path / "bundles" / "loan-clean-c90") == []
    m = json.loads((tmp_path / "bundles" / "loan-clean-c90" / "manifest.json").read_text())
    assert {"H1", "H2", "H3"} <= {q["id"] for q in m["quirks"]}
    assert (
        make_bundle.main(["--scenario", "loan", "--clean-pool", "2", "--out", str(tmp_path)]) == 0
    )
    assert (tmp_path / "bundles" / "loan-clean-c02").exists()
    out = capsys.readouterr().out
    assert "built loan-clean-c01" in out
    import importlib.util

    if importlib.util.find_spec("scripts._gen.kyc_fairness") is None:
        assert make_bundle.main(["--fairness", "--out", str(tmp_path)]) == 0
        assert "fairness set needs gen-kyc" in capsys.readouterr().out
