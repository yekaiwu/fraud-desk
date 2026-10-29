"""KYC / AML document renderers, scan derivatives and world-channel mutations (T-gen-kyc-01, -06)."""

from __future__ import annotations

import io
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from scripts import make_bundle
from scripts._gen import ids as idm
from scripts._gen import kyc_view as kv
from scripts._gen import verify
from scripts._gen.bundle_kyc import render_docs as kyc_render_docs
from scripts._gen.bundle_kyc import story_world
from scripts._gen.kyc_passport import issue_date_of
from scripts._gen.render_common import FOOTER_TEXT
from scripts._gen.schema import BundleSpec, Mutation

from .test_gen_kyc_helpers import aml_bundle, doc, docs_of, field, kyc_bundle, load, table_rows

D = Decimal
KYC_TYPES = {
    "certificate_of_incorporation",
    "ownership_chart",
    "shareholder_register",
    "passport",
    "source_of_funds_declaration",
}


def _info(pdf: bytes) -> dict[str, str]:
    with pikepdf.open(io.BytesIO(pdf)) as p:
        return {str(k)[1:]: str(v) for k, v in p.docinfo.items()}


def _text(path: Path) -> str:
    with pymupdf.open(path) as d:
        return "\n".join(pg.get_text() for pg in d)


# --------------------------------------------------------------------------- the renderers


def test_each_kyc_document_has_a_text_layer_footer_producer_and_honest_dates() -> None:
    seen: set[str] = set()
    for b in (kyc_bundle("clean"), aml_bundle("clean")):
        m = load(b, "manifest.json")
        for d in m["documents"]:
            if d["doc_type"] not in KYC_TYPES:
                continue
            seen.add(d["doc_type"])
            path = b / d["digital_path"]
            txt = _text(path)
            assert FOOTER_TEXT in txt and len(txt) > 200
            info = _info(path.read_bytes())
            want = (
                "Mock ID Imager 1.0" if d["doc_type"] == "passport" else "Mock Registry Portal 2.0"
            )
            assert info["Producer"] == want
            assert info["ModDate"] == info["CreationDate"]  # honest PDFs are unmodified
            assert d["layout"] is None and d["issuer"] is None
    assert seen == KYC_TYPES


def test_every_page_is_labelled_with_its_document_type() -> None:
    for b in (kyc_bundle("fraud"), aml_bundle("fraud")):
        m, lab = load(b, "manifest.json"), load(b, "labels.json")
        c1 = {d["key"]: d["doc_type"] for d in m["documents"]}
        for p in lab["pages"]:
            if p["c1"] == "boilerplate":
                assert p["doc_key"] is None
                continue
            assert p["c1"] == c1[p["doc_key"]]
        types = {p["c1"] for p in lab["pages"]}
        assert {
            "ownership_chart",
            "shareholder_register",
        } <= types or "source_of_funds_declaration" in types
    kinds = {p["c1"] for p in load(kyc_bundle("clean"), "labels.json")["pages"]}
    assert KYC_TYPES - {"source_of_funds_declaration"} <= kinds
    assert "boilerplate" in kinds  # the ocbc T&C page (Q12)


def test_passport_mrz_passes_icao_check_digits_and_agrees_with_the_visual_zone() -> None:
    gt = load(kyc_bundle("clean"), "ground_truth.json")
    p = doc(gt, "passport:1")
    l1, l2 = field(p, "mrz_line1"), field(p, "mrz_line2")
    assert re.fullmatch(r"[A-Z0-9<]{44}", l1) and re.fullmatch(r"[A-Z0-9<]{44}", l2)
    assert idm.mrz_line2_valid(l2)
    assert l1.startswith("P<ESPVASQUEZ<<ELENA<")
    assert l2[0:9].rstrip("<") == field(p, "passport_number")
    assert l2[10:13] == field(p, "nationality") == field(p, "issuing_country") == "ESP"
    dob = date.fromisoformat(field(p, "date_of_birth"))
    assert l2[13:19] == dob.strftime("%y%m%d") and l2[20] == field(p, "sex") == "F"
    exp = date.fromisoformat(field(p, "date_of_expiry"))
    assert l2[21:27] == exp.strftime("%y%m%d") and exp > date(2026, 10, 27)
    assert field(p, "surname") == "Vasquez" and field(p, "given_names") == "Elena"
    assert p["fields"]["mrz_line2"]["raw"] == l2 and p["fields"]["mrz_line2"]["box"] is not None
    # the printed MRZ is in the text layer exactly, with "<" preserved
    assert l1 in _text(kyc_bundle("clean") / "digital" / "passport_1.pdf")
    # ICAO 9303 specimen (docs 4.2): the builder reproduces it
    s1, s2 = idm.build_td3(
        issuing="UTO", surname="ERIKSSON", given_names="ANNA MARIA", passport_no="L898902C3",
        nationality="UTO", dob=date(1974, 8, 12), sex="F", expiry=date(2012, 4, 15), personal_no="ZE184226B",
    )  # fmt: skip
    assert s1 == "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
    assert s2 == "L898902C36UTO7408122F1204159ZE184226B<<<<<10"


def test_passport_dates_and_ids_are_plausible_for_every_company() -> None:
    for idx in (0, 3, 100):
        w, _ = story_world(BundleSpec(scenario="kyc", variant="clean", company_index=idx))
        p = next(p for p in w.people if p.id_kind == "passport")
        assert issue_date_of(p) < date(2026, 10, 27) < p.passport_expiry
        l1, l2 = kv.passport_mrz(w, p)
        assert idm.mrz_line2_valid(l2) and len(l1) == 44


def test_chart_truth_matches_the_view_and_every_row_has_a_box() -> None:
    gt = load(kyc_bundle("clean"), "ground_truth.json")
    rows = table_rows(gt, "ownership_chart:-", "holders")
    assert [r["cells"]["holder_name"]["value"] for r in rows][:3] == [
        "Tan Wei Ming", "Muhammad Hafiz bin Ismail", "Kestrel Capital Holdings Pte. Ltd.",
    ]  # fmt: skip
    for r in rows:
        assert r["box"] is not None
        assert r["cells"]["percent"]["raw"] == f"{D(r['cells']['percent']['value']):.2f}%"
        for c in ("holder_name", "holder_type", "percent"):
            assert r["cells"][c]["box"] is not None
    # the child sits below its parent on the page, drawn in the same column
    kes, teo = rows[2], rows[3]
    assert teo["cells"]["holder_name"]["box"]["y"] > kes["cells"]["holder_name"]["box"]["y"]
    assert teo["cells"]["parent_name"]["raw"] == kes["cells"]["holder_name"]["value"]
    chart = doc(gt, "ownership_chart:-")
    assert (
        field(chart, "company_name") == "Sembrook Freight Pte. Ltd."
        and field(chart, "chart_date") == "2026-09-30"
    )
    txt = _text(kyc_bundle("clean") / "digital" / "ownership_chart.pdf")
    for needle in (
        "OWNERSHIP STRUCTURE CHART",
        "45.00%",
        "30.00%",
        "25.00%",
        "100.00%",
        "UBO",
        "Held in",
    ):
        assert needle in txt, needle


def test_certificate_register_and_profile_agree_on_the_applicant() -> None:
    gt = load(kyc_bundle("clean"), "ground_truth.json")
    cert, reg, prof = (
        doc(gt, k)
        for k in ("certificate_of_incorporation:-", "shareholder_register:-", "company_profile:-")
    )
    assert field(cert, "uen") == field(reg, "uen") == field(prof, "uen")
    assert field(cert, "incorporation_date") == field(prof, "incorporation_date")
    assert field(cert, "entity_type") == "Private Company Limited by Shares"
    assert field(cert, "certificate_number") == f"COI-{field(cert, 'uen')}"
    assert field(reg, "total_shares") == "500000" and field(reg, "issued_capital") == "500000.00"
    shares = [
        int(r["cells"]["shares"]["value"])
        for r in table_rows(gt, "shareholder_register:-", "shareholders")
    ]
    assert sum(shares) == 500000
    for r in table_rows(gt, "shareholder_register:-", "shareholders"):
        c = r["cells"]
        assert abs(D(c["shares"]["value"]) / 500000 * 100 - D(c["percent"]["value"])) < D("0.005")


def test_sof_document_fields_and_boxes() -> None:
    gt = load(aml_bundle("fraud"), "ground_truth.json")
    sof = doc(gt, "source_of_funds_declaration:-")
    for name in (
        "declarant_name",
        "declared_amount",
        "origin_category",
        "origin_description",
        "counterparty_name",
        "invoice_reference",
        "period_start",
        "period_end",
        "receiving_account",
        "declaration_date",
    ):
        assert sof["fields"][name]["box"] is not None, name
    assert sof["fields"]["declared_amount"]["raw"] == "S$ 1,000,000.00"
    assert sof["fields"]["origin_category"]["raw"] == "Sales proceeds"
    assert field(sof, "company_name") == "Tanjong Ridge Trading Pte. Ltd."
    assert field(sof, "uen") == field(doc(gt, "company_profile:-"), "uen")
    assert date.fromisoformat(field(sof, "declaration_date")) <= date(2026, 9, 30)


# ------------------------------------------------------------------------------ scan renders


def test_scan_renders_have_boxes_inside_the_page_lines_and_labels(tmp_path: Path) -> None:
    spec = BundleSpec(scenario="kyc", variant="fraud", renders=["digital", "scan_hq", "scan_pdf"])
    bdir = tmp_path / "bundles" / "kyc-fraud"
    m = make_bundle.build_bundle(spec, bdir)
    make_bundle.build_registry(tmp_path / "registry.json", [m])
    assert verify.verify_bundle(bdir) == []
    gt, lab = load(bdir, "ground_truth.json"), load(bdir, "labels.json")
    scan_files = [f for f in m.files if f.render != "digital"]
    assert any(f.render == "scan_hq" for f in scan_files) and any(
        f.render == "scan_pdf" for f in scan_files
    )
    covered = {(p["file"], p["page"]) for p in gt["pages"]}
    for f in m.files:
        for pe in f.pages:
            assert (f.path, pe.page) in covered, (f.path, pe.page)
    labelled = {(p["file"], p["page"]) for p in lab["pages"]}
    assert covered <= labelled | {(f, 1) for f, _ in covered}
    n_boxes = 0

    def inside(b: dict) -> None:
        nonlocal n_boxes
        n_boxes += 1
        assert (
            0 <= b["x"] <= 1
            and 0 <= b["y"] <= 1
            and b["x"] + b["w"] <= 1.0001
            and b["y"] + b["h"] <= 1.0001
        ), b

    for d in gt["documents"]:
        for f in d["fields"].values():
            for b in (f.get("boxes") or {}).values():
                inside(b)
        for t in d["tables"].values():
            for r in t["rows"]:
                for b in (r.get("boxes") or {}).values():
                    inside(b)
                for c in r["cells"].values():
                    for b in (c.get("boxes") or {}).values():
                        inside(b)
    assert n_boxes > 200
    chart = doc(gt, "ownership_chart:-")
    assert {s["render"] for s in chart["sources"]} == {"digital", "scan_hq", "scan_pdf"}
    # scan lines let the rules classifier read the heading of a scanned chart
    hq = next(f for f in m.files if f.render == "scan_hq" and "ownership_chart" in f.path)
    lines = next(p["lines"] for p in gt["pages"] if p["file"] == hq.path)
    assert any("OWNERSHIP STRUCTURE CHART" in ln["text"] for ln in lines)
    assert all(0 <= ln["x"] <= 1 and 0 <= ln["y"] <= 1 for ln in lines)


# ----------------------------------------------------------------------------- mutations


def _mut(target: str, op: str, **args: object) -> dict:
    return {"op": op, "target": target, "args": args}


def test_chart_mutation_omit_holder_and_rescale() -> None:
    b = kyc_bundle(
        "clean",
        mutations=[
            _mut(
                "ownership_chart:-", "set_field", field="omit_holder", value="Kestrel", rescale=True
            )
        ],
        tag="omit",
    )
    gt = load(b, "ground_truth.json")
    rows = table_rows(gt, "ownership_chart:-", "holders")
    assert [r["cells"]["holder_name"]["value"] for r in rows] == [
        "Tan Wei Ming",
        "Muhammad Hafiz bin Ismail",
    ]
    assert [r["cells"]["percent"]["value"] for r in rows] == ["60.00", "40.00"]
    assert len(table_rows(gt, "shareholder_register:-", "shareholders")) == 3  # register untouched


def test_register_mutation_override_percent_and_shares() -> None:
    muts = [
        _mut(
            "shareholder_register:-",
            "override_cell",
            table="shareholders",
            row=0,
            column="percent",
            value="40.00",
        ),
        _mut(
            "shareholder_register:-",
            "override_cell",
            table="shareholders",
            row=0,
            column="shares",
            value="200000",
        ),
    ]
    gt = load(kyc_bundle("clean", mutations=muts, tag="reg"), "ground_truth.json")
    r0 = table_rows(gt, "shareholder_register:-", "shareholders")[0]["cells"]
    assert r0["percent"]["value"] == "40.00" and r0["shares"]["value"] == "200000"


def test_profile_mutation_adds_a_director_with_an_id() -> None:
    b = kyc_bundle(
        "clean",
        mutations=[
            _mut("company_profile:-", "set_field", field="add_director", value="Ong Kai Wen")
        ],
        tag="dir",
    )
    gt = load(b, "ground_truth.json")
    names = [
        r["cells"]["director_name"]["value"]
        for r in table_rows(gt, "company_profile:-", "directors")
    ]
    assert names[-1] == "Ong Kai Wen" and len(docs_of(gt, "national_id")) == 3


def test_passport_mutation_breaks_the_mrz_only() -> None:
    w, _ = story_world(BundleSpec(scenario="kyc", variant="clean"))
    p = next(p for p in w.people if p.id_kind == "passport")
    good = kv.passport_mrz(w, p)[1]
    bad = good[:9] + str((int(good[9]) + 1) % 10) + good[10:]
    b = kyc_bundle(
        "clean",
        mutations=[_mut("passport:1", "set_field", field="mrz_line2", value=bad)],
        tag="mrz",
    )
    pp = doc(load(b, "ground_truth.json"), "passport:1")
    assert field(pp, "mrz_line2") == bad and not idm.mrz_line2_valid(bad)
    assert field(pp, "passport_number") == p.id_number  # the visual zone is untouched
    b2 = kyc_bundle(
        "clean",
        mutations=[_mut("passport:1", "set_field", field="date_of_birth", value="1989-01-01")],
        tag="dob",
    )
    pp2 = doc(load(b2, "ground_truth.json"), "passport:1")
    assert field(pp2, "date_of_birth") == "1989-01-01" and idm.mrz_line2_valid(
        field(pp2, "mrz_line2")
    )


def test_sof_mutation_and_plot_ops_still_reach_apply_plot() -> None:
    b = aml_bundle(
        "clean",
        mutations=[
            _mut(
                "source_of_funds_declaration:-",
                "set_field",
                field="declared_amount",
                value="5000000.00",
            )
        ],
        tag="sof",
    )
    sof = doc(load(b, "ground_truth.json"), "source_of_funds_declaration:-")
    assert field(sof, "declared_amount") == "5000000.00"
    # an ordinary world op (T01-style) on a KYC bundle goes through world.apply_plot
    m = _mut(
        "bank_statement:2026-08",
        "override_cell",
        table="transactions",
        row=3,
        column="credit",
        value="999.99",
    )
    gt0 = load(kyc_bundle("clean"), "ground_truth.json")
    row0 = table_rows(gt0, "bank_statement:2026-08", "transactions")[3]["cells"]
    if row0["credit"]["value"] is None:
        m = _mut(
            "bank_statement:2026-08",
            "override_cell",
            table="transactions",
            row=3,
            column="debit",
            value="999.99",
        )
    gt1 = load(kyc_bundle("clean", mutations=[m], tag="t01"), "ground_truth.json")
    row1 = table_rows(gt1, "bank_statement:2026-08", "transactions")[3]["cells"]
    assert "999.99" in (row1["credit"]["value"], row1["debit"]["value"])
    # T50a: NRIC check letter changed on a national_id
    nid = field(doc(gt0, "national_id:1"), "id_number")
    bad = nid[:-1] + ("A" if nid[-1] != "A" else "B")
    gt2 = load(
        kyc_bundle(
            "clean",
            mutations=[_mut("national_id:1", "override_cell", field="id_number", value=bad)],
            tag="t50",
        ),
        "ground_truth.json",
    )
    assert field(doc(gt2, "national_id:1"), "id_number") == bad and not idm.is_valid_nric(bad)


def test_unsupported_mutations_fail_loudly() -> None:
    w, _ = story_world(BundleSpec(scenario="kyc", variant="clean"))
    with pytest.raises(ValueError):
        kv.apply_kyc_mutation(
            w,
            Mutation(
                op="set_field",
                target="ownership_chart:-",
                args={"field": "omit_holder", "value": "Nobody"},
            ),
        )
    with pytest.raises(ValueError):
        kv.apply_kyc_mutation(
            w,
            Mutation(
                op="set_field", target="company_profile:-", args={"field": "nope", "value": "x"}
            ),
        )
    with pytest.raises(ValueError):
        kv.apply_kyc_mutation(
            w,
            Mutation(
                op="set_field",
                target="source_of_funds_declaration:-",
                args={"field": "declared_amount", "value": "1"},
            ),
        )
    assert kv.is_kyc_target("passport:1") and not kv.is_kyc_target("bank_statement:2026-07")


def test_omit_holder_rescale_keeps_totals_exact() -> None:
    for idx in (0, 3):
        w, _ = story_world(BundleSpec(scenario="kyc", variant="clean", company_index=idx))
        v = kv.view_of(w)
        before = v.total_shares
        last = [r for r in v.chart if r.parent is None][-1].name
        assert kv.omit_holder(v, last, rescale=True) == last
        assert sum((r.percent for r in v.chart if r.parent is None), D(0)) == D("100.00")
        assert sum((r.percent for r in v.register), D(0)) == D("100.00")
        assert sum(r.shares for r in v.register) == before
        assert all(r.parent != last for r in v.chart)


def test_render_docs_is_a_pure_function_of_the_world() -> None:
    w, _ = story_world(BundleSpec(scenario="kyc", variant="fraud"))
    a = [(d.key, d.pdf) for d in kyc_render_docs(w)]
    b = [(d.key, d.pdf) for d in kyc_render_docs(w)]
    assert a == b
    json.dumps([k for k, _ in a])
