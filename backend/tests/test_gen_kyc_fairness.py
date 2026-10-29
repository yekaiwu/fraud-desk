"""Fairness pair builder (T-gen-kyc-04) and the whole-suite CLI (T-gen-kyc-05)."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

import pymupdf
import pytest

from scripts import make_bundle
from scripts._gen import kyc_fairness as kf
from scripts._gen.schema import FIXED_TODAY, Manifest, NamePair

from .test_gen_helpers import load

GROUPS = [
    "western_order",
    "chinese_order",
    "malay_patronymic",
    "indian_patronymic",
    "mononym",
    "titles",
]


@pytest.fixture(scope="module")
def full_set(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("fairness")
    pairs = kf.build_fairness(out, seed=4002, today=FIXED_TODAY)
    assert len(pairs) == 270
    return out / "fairness"


def test_group_list_is_fixed_and_counts_per_group(full_set: Path) -> None:
    assert list(kf.GROUPS) == GROUPS
    data = json.loads((full_set / "pairs.json").read_text())
    assert data["schema_version"] == "1"
    pairs = [NamePair.model_validate(p) for p in data["pairs"]]
    c = Counter((p.group, p.same_person) for p in pairs)
    for g in GROUPS:
        assert c[(g, True)] == 30 and c[(g, False)] == 15, g
    assert len({p.pair_id for p in pairs}) == 270
    assert all(p.form_a and p.form_b for p in pairs)


_SKIP = {
    "bin",
    "binte",
    "binti",
    "bte",
    "b",
    "s/o",
    "d/o",
    "dr",
    "mdm",
    "mr",
    "prof",
    "datuk",
    "seri",
    "datin",
}


def _letters(name: str) -> Counter:
    toks = [t for t in re.split(r"[\s,\-.]+", name.lower()) if t and t not in _SKIP]
    return Counter("".join("muhammad" if t == "mohd" else t for t in toks))


def test_same_person_pairs_differ_only_in_surface_form_and_negatives_are_near_spelled(
    full_set: Path,
) -> None:
    pairs = [
        NamePair.model_validate(p)
        for p in json.loads((full_set / "pairs.json").read_text())["pairs"]
    ]
    for p in pairs:
        if p.same_person:
            # the letters of the name tokens (minus markers and titles, Mohd = Muhammad) of one form
            # are contained in the other: only order, case, spacing, hyphens, titles, markers differ
            a, b = _letters(p.name_a), _letters(p.name_b)
            assert not (b - a) or not (a - b), (p.name_a, p.name_b, p.form_b)
        else:
            d = kf.edit_distance(p.name_a, p.name_b)
            assert 1 <= d <= 2, (p.name_a, p.name_b)
            assert p.form_b == f"near_spelling_{d}"
    # every surface form is exercised in its group
    by_group: dict[str, set[str]] = {}
    for p in pairs:
        if p.same_person:
            by_group.setdefault(p.group, set()).add(p.form_b)
    assert {"upper", "reordered", "comma_order", "middle_omitted"} <= by_group["western_order"]
    assert {"given_first", "given_joined", "given_hyphen"} <= by_group["chinese_order"]
    assert {"patronymic_dropped", "patronymic_abbrev"} <= by_group["malay_patronymic"]
    assert {"patronymic_dropped"} <= by_group["indian_patronymic"]
    assert {"padded", "upper"} <= by_group["mononym"]
    assert {"title_dropped", "title_changed"} <= by_group["titles"]


def test_each_pair_is_a_tiny_kyc_case_with_the_registry_side_form(full_set: Path) -> None:
    reg = json.loads((full_set / "registry.json").read_text())
    assert reg["schema_version"] == "1" and len(reg["entities"]) == 270
    uens = [e["uen"] for e in reg["entities"]]
    assert uens == sorted(uens) and len(set(uens)) == 270
    pairs = {p["pair_id"]: p for p in json.loads((full_set / "pairs.json").read_text())["pairs"]}
    pid = "chinese_order-s02"
    case = full_set / "cases" / pid
    m = Manifest.model_validate(load(case, "manifest.json"))
    assert (
        m.kind == "fairness_case"
        and m.bundle_id == pid
        and m.registry_path == "../../registry.json"
    )
    assert sorted(d.doc_type for d in m.documents) == [
        "company_profile",
        "national_id",
        "ownership_chart",
        "shareholder_register",
    ]
    gt = load(case, "ground_truth.json")
    by = {d["doc_type"]: d for d in gt["documents"]}
    name_a, name_b = pairs[pid]["name_a"], pairs[pid]["name_b"]
    assert (
        by["company_profile"]["tables"]["directors"]["rows"][0]["cells"]["director_name"]["value"]
        == name_a
    )
    assert by["national_id"]["fields"]["holder_name"]["value"] == name_a
    for t, key in (("shareholder_register", "shareholders"), ("ownership_chart", "holders")):
        rows = by[t]["tables"][key]["rows"]
        assert len(rows) == 1 and rows[0]["cells"]["holder_name"]["value"].lower() == name_a.lower()
        assert rows[0]["cells"]["percent"]["value"] == "100.00"
    ent = next(e for e in reg["entities"] if e["uen"] == m.applicant["uen"])
    assert ent["officers"][0]["name"] == name_b and ent["shareholders"][0]["name"] == name_b
    assert ent["officers"][0]["id_number"] == by["national_id"]["fields"]["id_number"]["value"]
    assert len(m.files) == 4
    with pymupdf.open(case / m.files[0].path) as d:
        assert len(d) == 1


def test_expectations_split_same_and_different_person(full_set: Path) -> None:
    same = load(full_set / "cases" / "western_order-s01", "manifest.json")
    diff = load(full_set / "cases" / "western_order-d01", "manifest.json")
    assert same["planted"] == [] and same["expected"]["must_fire"] == []
    assert set(same["expected"]["must_not_fire"]) == {
        "x_directors",
        "x_ownership_vs_registry",
        "x_entity",
    }
    assert diff["planted"][0]["kind"] == "near_spelled_registry_name"
    assert diff["expected"]["must_fire"][0]["rule"] == [
        "x_directors",
        "x_ownership_vs_registry",
        "x_entity",
    ]


def test_deterministic_and_seed_dependent(tmp_path: Path) -> None:
    a = kf.build_fairness(
        tmp_path / "a", seed=11, today=FIXED_TODAY, same_per_group=4, diff_per_group=2
    )
    b = kf.build_fairness(
        tmp_path / "b", seed=11, today=FIXED_TODAY, same_per_group=4, diff_per_group=2
    )
    c = kf.build_fairness(
        tmp_path / "c", seed=12, today=FIXED_TODAY, same_per_group=4, diff_per_group=2
    )
    assert a == b and a != c
    for rel in (
        "fairness/pairs.json",
        "fairness/registry.json",
        "fairness/cases/titles-d01/manifest.json",
        "fairness/cases/titles-d01/digital/company_profile.pdf",
    ):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes(), rel
    assert len(a) == 6 * 6


def test_near_spell_never_returns_the_same_name_and_is_deterministic() -> None:
    import random

    for name in (
        "Tan Wei Ming",
        "Suharti",
        "Muhammad Hafiz bin Ismail",
        "Dr Lim Hui Min",
        "Rajesh s/o Kumar",
    ):
        got = {kf.near_spell(name, random.Random(i)) for i in range(20)}
        assert name not in got and all(1 <= kf.edit_distance(name, g) <= 2 for g in got)
        assert kf.near_spell(name, random.Random(3)) == kf.near_spell(name, random.Random(3))


# --------------------------------------------------------------------- whole-suite CLI


def test_all_scenarios_build_in_well_under_a_minute_with_the_union_registry(tmp_path: Path) -> None:
    t0 = time.monotonic()
    assert make_bundle.main(["--scenario", "all", "--variant", "all", "--out", str(tmp_path)]) == 0
    assert time.monotonic() - t0 < 60
    dirs = sorted(p.name for p in (tmp_path / "bundles").iterdir())
    assert dirs == [
        f"{s}-{v}" for s in ("aml", "kyc", "loan") for v in ("clean", "forger", "fraud")
    ]
    reg = json.loads((tmp_path / "registry.json").read_text())
    names = {e["entity_name"] for e in reg["entities"]}
    assert {
        "Meridian Fastener Supplies Pte. Ltd.",
        "Sembrook Freight Pte. Ltd.",
        "Tanjong Ridge Trading Pte. Ltd.",
        "Kestrel Capital Holdings Pte. Ltd.",
    } <= names
    uens = [e["uen"] for e in reg["entities"]]
    assert uens == sorted(uens) and len(uens) == len(set(uens)) == 4
    for d in dirs:
        assert make_bundle.verify_bundle(tmp_path / "bundles" / d) == [], d
    assert make_bundle.main(["--verify", str(tmp_path / "bundles" / "kyc-forger")]) == 0


def test_canaries_pick_up_the_chart_host_and_fairness_flag_runs(tmp_path: Path) -> None:
    assert make_bundle.main(["--canaries", "--out", str(tmp_path)]) == 0
    made = sorted(p.name for p in (tmp_path / "canaries").iterdir())
    assert [n for n in made if n.startswith("canary-chart")] == [
        "canary-chart-P1", "canary-chart-P2", "canary-chart-P3", "canary-chart-P4", "canary-chart-control",
    ]  # fmt: skip
    injected = [n for n in made if not n.endswith("control")]
    assert len(injected) == 12  # 3 hosts x 4 placements
    m = load(tmp_path / "canaries" / "canary-chart-P2", "manifest.json")
    assert (
        m["documents"][0]["doc_type"] == "ownership_chart"
        and m["base_bundle"] == "../canary-chart-control/manifest.json"
    )
    lab = load(tmp_path / "canaries" / "canary-chart-P2", "labels.json")
    assert [p["c1"] for p in lab["pages"]] == ["ownership_chart", "other"]
