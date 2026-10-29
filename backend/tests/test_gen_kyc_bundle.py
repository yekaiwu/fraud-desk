"""KYC demo bundle: clean control, fraud, careful forger (T-gen-kyc-02, T-gen-kyc-06)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import make_bundle
from scripts._gen import ids as idm
from scripts._gen import kyc_plots, verify
from scripts._gen import lexicon as lx
from scripts._gen import registry_writer as rw
from scripts._gen.bundle_kyc import story_world
from scripts._gen.schema import BundleSpec, Manifest

from .test_gen_kyc_helpers import (
    digital_hashes,
    doc,
    docs_of,
    field,
    kyc_bundle,
    load,
    table_rows,
)

D = Decimal
VARIANTS = ("clean", "fraud", "forger")


def _holders(gt: dict, key: str, table: str) -> dict[str, D]:
    return {
        r["cells"]["holder_name"]["value"]: D(r["cells"]["percent"]["value"])
        for r in table_rows(gt, key, table)
    }


def _statement_rows(bdir: Path) -> list[tuple[str, dict, dict]]:
    gt, lab = load(bdir, "ground_truth.json"), load(bdir, "labels.json")
    by = {(r["doc_key"], r["row"]): r for r in lab["rows"]}
    return [
        (d["key"], r, by[(d["key"], r["index"])])
        for d in docs_of(gt, "bank_statement")
        for r in d["tables"]["transactions"]["rows"]
    ]


def _amount(r: dict) -> D:
    return D(r["cells"]["debit"]["value"] or r["cells"]["credit"]["value"])


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_variant_verifies(variant: str) -> None:
    assert verify.verify_bundle(kyc_bundle(variant)) == []


def test_clean_control_contents_and_expectations() -> None:
    b = kyc_bundle("clean")
    m = load(b, "manifest.json")
    gt = load(b, "ground_truth.json")
    kinds: dict[str, int] = {}
    for d in gt["documents"]:
        kinds[d["doc_type"]] = kinds.get(d["doc_type"], 0) + 1
    assert kinds == {
        "company_profile": 1,
        "certificate_of_incorporation": 1,
        "ownership_chart": 1,
        "shareholder_register": 1,
        "national_id": 2,
        "passport": 1,
        "bank_statement": 3,
    }
    assert m["bundle_id"] == "kyc-clean" and m["use_case"] == "kyc"
    assert m["planted"] == [] and m["tampers"] == []
    assert m["applicant"]["name"] == "Sembrook Freight Pte. Ltd."
    e = m["expected"]
    assert e["lane_any_of"] == ["fast", "edd"] and e["band_max"] == "low"
    assert e["verdict"] == "approve" and e["max_flags_ge_medium"] == 0 and e["must_fire"] == []
    assert sorted(d["key"] for d in docs_of(gt, "bank_statement")) == [
        f"bank_statement:2026-0{n}" for n in (7, 8, 9)
    ]
    assert all(
        d["layout"] == "ocbc_style" for d in m["documents"] if d["doc_type"] == "bank_statement"
    )
    assert (
        field(doc(gt, "company_profile:-"), "principal_activity")
        == "Freight forwarding and logistics"
    )
    assert field(doc(gt, "company_profile:-"), "ssic_code") == "52291"
    qids = {q["id"] for q in m["quirks"]}
    assert {"Q09", "Q11", "Q12"} <= qids and not qids & {"Q02", "Q03", "Q06"}


def test_clean_chart_shows_kestrel_with_its_owner_and_passes_the_ubo_design() -> None:
    gt = load(kyc_bundle("clean"), "ground_truth.json")
    chart = doc(gt, "ownership_chart:-")
    assert field(chart, "ubo_threshold_percent") == "25.00"
    assert chart["fields"]["ubo_threshold_percent"]["raw"] == "25.00%"
    rows = {
        r["cells"]["holder_name"]["value"]: r["cells"]
        for r in table_rows(gt, "ownership_chart:-", "holders")
    }
    assert set(rows) == {
        "Tan Wei Ming",
        "Muhammad Hafiz bin Ismail",
        "Kestrel Capital Holdings Pte. Ltd.",
        "Teo Boon Heng",
    }
    k = rows["Kestrel Capital Holdings Pte. Ltd."]
    assert k["holder_type"]["value"] == "company" and k["percent"]["value"] == "25.00"
    assert k["parent_name"]["value"] is None
    child = rows["Teo Boon Heng"]
    # CR-19: a child's percent is the percent held in the parent
    assert child["parent_name"]["value"] == "Kestrel Capital Holdings Pte. Ltd."
    assert child["percent"]["value"] == "100.00" and child["is_ubo"]["value"] == "true"
    assert rows["Tan Wei Ming"]["is_ubo"]["value"] == "true"
    assert rows["Tan Wei Ming"]["percent"]["value"] == "45.00"


def test_registry_is_identical_across_variants_and_holds_the_story() -> None:
    ents = []
    for v in VARIANTS:
        spec = BundleSpec(scenario="kyc", variant=v)  # type: ignore[arg-type]
        ents.append(json.dumps(rw.entities_for_spec(spec), sort_keys=True))
    assert ents[0] == ents[1] == ents[2]
    reg = json.loads((kyc_bundle("clean").parent.parent / "registry.json").read_text())
    by_name = {e["entity_name"]: e for e in reg["entities"]}
    sem = by_name["Sembrook Freight Pte. Ltd."]
    assert {s["name"]: s["percent"] for s in sem["shareholders"]} == {
        "TAN WEI MING": "45.00",
        "MUHAMMAD HAFIZ BIN ISMAIL": "30.00",
        "Kestrel Capital Holdings Pte. Ltd.": "25.00",
    }
    assert sorted(o["name"] for o in sem["officers"]) == [
        "ELENA VASQUEZ",
        "MUHAMMAD HAFIZ BIN ISMAIL",
        "TAN WEI MING",
    ]
    kes = by_name["Kestrel Capital Holdings Pte. Ltd."]
    assert [(s["name"], s["percent"], s["holder_type"]) for s in kes["shareholders"]] == [
        ("TEO BOON HENG", "100.00", "individual")
    ]
    uens = [e["uen"] for e in reg["entities"]]
    assert uens == sorted(uens)
    from stdnum.sg import uen as sg_uen

    assert all(sg_uen.is_valid(u) for u in uens)


@pytest.mark.parametrize("variant", ["fraud", "forger"])
def test_diff_against_clean_is_limited_to_planted_targets(variant: str) -> None:
    clean, other = kyc_bundle("clean"), kyc_bundle(variant)
    hc, ho = digital_hashes(clean), digital_hashes(other)
    changed = {k for k in ho if hc.get(k) != ho[k]}
    assert set(hc) <= set(ho)
    planted = {p["target"]["doc_key"] for p in load(other, "manifest.json")["planted"]}
    assert changed and changed <= planted
    # untouched documents are byte-identical, not just equal
    for k in set(hc) - planted:
        assert hc[k] == ho[k], k
    assert "company_profile:-" in changed and "national_id:3" in changed


def test_crypto_activity_meets_the_contract_with_the_check_authors() -> None:
    for v in ("fraud", "forger"):
        rows = _statement_rows(kyc_bundle(v))
        total = sum((_amount(r) for _k, r, _l in rows), D(0))
        crypto = [(r, lab) for _k, r, lab in rows if lab["c5"] == "crypto_virtual_assets"]
        value = sum((_amount(r) for r, _l in crypto), D(0))
        assert value / total >= D("0.30"), v
        assert len(crypto) >= 8
        keyworded = [
            lab
            for _r, lab in crypto
            if lx.label_from_tokens(lab["counterparty"]) == "crypto_virtual_assets"
        ]
        assert len(keyworded) == len(crypto) >= 8  # none is a "hard" row
        assert all(lab["c5"] == "crypto_virtual_assets" for _r, lab in crypto)
    clean_rows = _statement_rows(kyc_bundle("clean"))
    assert not [1 for _k, _r, lab in clean_rows if lab["c5"] == "crypto_virtual_assets"]


def test_fraud_chart_omits_kestrel_and_the_register_is_true() -> None:
    gt = load(kyc_bundle("fraud"), "ground_truth.json")
    chart = _holders(gt, "ownership_chart:-", "holders")
    assert chart == {"Tan Wei Ming": D("45.00"), "Muhammad Hafiz bin Ismail": D("30.00")}
    assert sum(chart.values()) == D("75.00")  # so kyc_shareholdings also fires
    reg = _holders(gt, "shareholder_register:-", "shareholders")
    assert sum(reg.values()) == D("100.00") and len(reg) == 3
    assert doc(gt, "shareholder_register:-")["fields"]["total_shares"]["value"] == "500000"


def test_forger_chart_and_register_agree_with_each_other_not_the_registry() -> None:
    gt = load(kyc_bundle("forger"), "ground_truth.json")
    chart = _holders(gt, "ownership_chart:-", "holders")
    reg = {
        verify.person_key(k): v
        for k, v in _holders(gt, "shareholder_register:-", "shareholders").items()
    }
    chart_keys = {verify.person_key(k): v for k, v in chart.items()}
    assert sorted(chart.values()) == [D("40.00"), D("60.00")]
    assert chart["Tan Wei Ming"] == D("60.00") and chart["Muhammad Hafiz bin Ismail"] == D("40.00")
    assert reg == chart_keys
    shares = {
        r["cells"]["holder_name"]["value"]: int(r["cells"]["shares"]["value"])
        for r in table_rows(gt, "shareholder_register:-", "shareholders")
    }
    assert sorted(shares.values()) == [200000, 300000]
    assert sum(shares.values()) == int(field(doc(gt, "shareholder_register:-"), "total_shares"))
    # the profile still shows what the registry shows
    prof = _holders(gt, "company_profile:-", "shareholders")
    assert prof["Kestrel Capital Holdings Pte. Ltd."] == D("25.00")


def test_fourth_director_has_an_id_but_is_not_a_registry_officer() -> None:
    for v in ("fraud", "forger"):
        b = kyc_bundle(v)
        gt = load(b, "ground_truth.json")
        names = [
            r["cells"]["director_name"]["value"]
            for r in table_rows(gt, "company_profile:-", "directors")
        ]
        assert names == [
            "Tan Wei Ming",
            "Muhammad Hafiz bin Ismail",
            "Elena Vasquez",
            "Koh Boon Kiat",
        ]
        ids = {field(d, "holder_name"): field(d, "id_number") for d in docs_of(gt, "national_id")}
        assert set(ids) == {"Tan Wei Ming", "Muhammad Hafiz bin Ismail", "Koh Boon Kiat"}
        assert idm.is_valid_nric(ids["Koh Boon Kiat"])
        profile_id = next(
            r["cells"]["id_number"]["value"]
            for r in table_rows(gt, "company_profile:-", "directors")
            if r["cells"]["director_name"]["value"] == "Koh Boon Kiat"
        )
        assert profile_id == ids["Koh Boon Kiat"]
        reg = json.loads((b.parent.parent / "registry.json").read_text())
        sem = next(e for e in reg["entities"] if e["entity_name"] == "Sembrook Freight Pte. Ltd.")
        assert "KOH BOON KIAT" not in [o["name"] for o in sem["officers"]]
        assert len(sem["officers"]) == 3
    gt = load(kyc_bundle("clean"), "ground_truth.json")
    assert len(table_rows(gt, "company_profile:-", "directors")) == 3
    assert len(docs_of(gt, "national_id")) == 2


def test_clean_control_registry_agrees_with_every_document() -> None:
    """W8 on the clean world: profile, chart, register and registry list the same holders and
    directors (surface forms aside)."""
    b = kyc_bundle("clean")
    gt = load(b, "ground_truth.json")
    reg = json.loads((b.parent.parent / "registry.json").read_text())
    sem = next(e for e in reg["entities"] if e["entity_name"] == "Sembrook Freight Pte. Ltd.")
    reg_h = {
        verify.person_key(s["name"]) or verify.company_key(s["name"]): D(s["percent"])
        for s in sem["shareholders"]
    }
    for key, table in (
        ("company_profile:-", "shareholders"),
        ("shareholder_register:-", "shareholders"),
    ):
        got = {
            verify.person_key(r["cells"]["holder_name"]["value"])
            or verify.company_key(r["cells"]["holder_name"]["value"]): D(
                r["cells"]["percent"]["value"]
            )
            for r in table_rows(gt, key, table)
        }
        assert got == reg_h, key
    direct = {
        verify.person_key(r["cells"]["holder_name"]["value"])
        or verify.company_key(r["cells"]["holder_name"]["value"]): D(r["cells"]["percent"]["value"])
        for r in table_rows(gt, "ownership_chart:-", "holders")
        if not r["cells"]["parent_name"]["value"]
    }
    assert direct == reg_h
    dirs = {
        verify.person_key(r["cells"]["director_name"]["value"])
        for r in table_rows(gt, "company_profile:-", "directors")
    }
    assert dirs == {verify.person_key(o["name"]) for o in sem["officers"]}
    ids = {
        verify.person_key(field(d, "holder_name"))
        for d in docs_of(gt, "national_id") + docs_of(gt, "passport")
    }
    assert ids == dirs


def test_expected_blocks() -> None:
    clean, fraud, forger = (load(kyc_bundle(v), "manifest.json")["expected"] for v in VARIANTS)
    for e in (fraud, forger):
        assert e["lane"] == "edd" and e["verdict"] == "refer_edd"
        rules = [x["rule"] for x in e["must_fire"]]
        assert {"x_declared_vs_activity", "x_ownership_vs_registry", "x_directors"} <= set(rules)
    sev = {x["rule"]: x["min_severity"] for x in forger["must_fire"]}
    assert sev == {
        "x_declared_vs_activity": "medium",
        "x_ownership_vs_registry": "high",
        "x_directors": "high",
    }
    assert fraud["band_min"] == "high" and forger["band_min"] == "medium"
    assert "kyc_shareholdings" in [x["rule"] for x in fraud["must_fire"]]
    assert {
        "kyc_shareholdings",
        "id_format",
        "id_checksum",
        "passport_mrz",
        "date_valid",
        "pdf_editor_software",
        "pdf_modified",
        "bs_running_balance",
        "bs_totals",
        "bs_pass_through",
    } <= set(forger["must_not_fire"])
    assert clean["must_fire"] == []


def test_planted_records_point_at_real_targets_inside_the_page() -> None:
    for v in ("fraud", "forger"):
        b = kyc_bundle(v)
        m = load(b, "manifest.json")
        gt = load(b, "ground_truth.json")
        files = {f["path"] for f in m["files"]}
        kinds = [p["kind"] for p in m["planted"]]
        assert kinds.count("crypto_activity") == 3
        assert "director_not_in_registry" in kinds and "director_id_document" in kinds
        assert ("ownership_omitted" if v == "fraud" else "ownership_redistributed") in kinds
        assert all(p["deck_slide"] == 14 for p in m["planted"])
        for p in m["planted"]:
            t = p["target"]
            box = t["box"]
            assert box is not None, p["id"]
            assert box["file"] in files
            assert (
                box["x"] >= 0
                and box["y"] >= 0
                and box["x"] + box["w"] <= 1.0001
                and box["y"] + box["h"] <= 1.0001
            )
            d = doc(gt, t["doc_key"])
            if t["row"] is not None:
                assert t["row"] < len(d["tables"][t["table"]]["rows"])
        crypto = [p for p in m["planted"] if p["kind"] == "crypto_activity"]
        for p in crypto:
            row = table_rows(gt, p["target"]["doc_key"], "transactions")[p["target"]["row"]]
            desc = row["cells"]["description"]["value"]
            assert lx.label_from_tokens(desc) == "crypto_virtual_assets"
        # the director plant targets the fourth directors row
        pd = next(p for p in m["planted"] if p["kind"] == "director_not_in_registry")
        assert pd["target"]["row"] == 3 and pd["facts"]["director"] == "Koh Boon Kiat"


def test_forger_is_internally_consistent_and_quiet_on_the_bs_family() -> None:
    """Every identity holds (W1-W9 via verify) and nothing looks like pass-through or a round number."""
    b = kyc_bundle("forger")
    gt = load(b, "ground_truth.json")
    assert verify.w1_statements(docs_of(gt, "bank_statement"), True) == []
    assert verify.w2_dates(docs_of(gt, "bank_statement")) == []
    assert (
        verify.w7_ids(docs_of(gt, "national_id"), docs_of(gt, "passport"), date(2026, 10, 27)) == []
    )
    for key, table in (
        ("ownership_chart:-", "holders"),
        ("shareholder_register:-", "shareholders"),
    ):
        assert sum(
            (D(r["cells"]["percent"]["value"]) for r in table_rows(gt, key, table)), D(0)
        ) == D("100.00")
    world, _facts = story_world(BundleSpec(scenario="kyc", variant="forger"))
    assert not kyc_plots.pass_through_pattern(world)
    assert len(kyc_plots.pass_through_hops(world)) <= 2
    assert kyc_plots.crypto_share(world) >= D("0.30")
    # one negative month (the Q07 overdraft month) in every variant
    for v in VARIANTS:
        w, _ = story_world(BundleSpec(scenario="kyc", variant=v))  # type: ignore[arg-type]
        assert kyc_plots.negative_months(w) == {"2026-08"}


def test_amounts_have_no_round_numbers_in_planted_rows() -> None:
    for v in ("fraud", "forger"):
        crypto = [
            r
            for _k, r, lab in _statement_rows(kyc_bundle(v))
            if lab["c5"] == "crypto_virtual_assets"
        ]
        amounts = [_amount(r) for r in crypto]
        assert all(a % 100 != 0 for a in amounts)
        assert len(set(amounts)) == len(amounts)


def test_determinism_equal_arguments_give_identical_bytes(tmp_path: Path) -> None:
    for v in VARIANTS:
        spec = BundleSpec(scenario="kyc", variant=v)  # type: ignore[arg-type]
        a, b = tmp_path / f"a-{v}", tmp_path / f"b-{v}"
        make_bundle.build_bundle(spec, a)
        make_bundle.build_bundle(spec, b)
        fa = {
            p.relative_to(a): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(a.rglob("*"))
            if p.is_file()
        }
        fb = {
            p.relative_to(b): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(b.rglob("*"))
            if p.is_file()
        }
        assert fa == fb and fa
    c = tmp_path / "c"
    make_bundle.build_bundle(BundleSpec(scenario="kyc", variant="fraud", seed=7), c)
    assert (c / "manifest.json").read_bytes() != (
        tmp_path / "a-fraud" / "manifest.json"
    ).read_bytes()


def test_manifest_regenerates_itself_from_its_spec(tmp_path: Path) -> None:
    b = kyc_bundle("fraud")
    m = Manifest.model_validate(load(b, "manifest.json"))
    again = tmp_path / "again"
    make_bundle.build_bundle(m.spec, again)
    assert (again / "manifest.json").read_text() == (b / "manifest.json").read_text()


def test_other_companies_build_and_verify() -> None:
    """The ranking pool uses company indexes 100+; any index must work for every variant."""
    for idx in (3, 100, 101):
        for v in VARIANTS:
            b = kyc_bundle(v, company_index=idx)
            assert verify.verify_bundle(b) == [], (idx, v)
    m = load(kyc_bundle("fraud", company_index=3), "manifest.json")
    assert m["bundle_id"] == "kyc-fraud-c03"
    gt = load(kyc_bundle("fraud", company_index=3), "ground_truth.json")
    assert len(docs_of(gt, "national_id")) == 3
    rows = _statement_rows(kyc_bundle("fraud", company_index=3))
    total = sum((_amount(r) for _k, r, _l in rows), D(0))
    cry = sum((_amount(r) for _k, r, lab in rows if lab["c5"] == "crypto_virtual_assets"), D(0))
    assert cry / total >= D("0.30")


def test_hard_quirk_company_builds(tmp_path: Path) -> None:
    from scripts._gen import quirks as qk

    spec = BundleSpec(
        scenario="kyc",
        variant="clean",
        company_index=91,
        quirks=[*qk.default_quirks(91), *qk.hard_quirks_for("kyc")],
    )
    bdir = tmp_path / "bundles" / "kyc-clean-c91"
    m = make_bundle.build_bundle(spec, bdir)
    make_bundle.build_registry(tmp_path / "registry.json", [m])
    assert verify.verify_bundle(bdir) == []
    assert any(q.id == "H2" for q in m.quirks)
