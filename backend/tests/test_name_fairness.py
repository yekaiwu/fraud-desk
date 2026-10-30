"""Larger seeded DEV/TEST name-fairness set and the Wikidata alias scorer (scripts/_eval/name_fairness)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.names import normalise_person
from scripts._eval import name_fairness as nf
from scripts._gen import names_data as nd
from scripts._gen.kyc_fairness import GROUPS


@pytest.fixture(scope="module")
def pairs() -> dict[str, list[nf.Pair]]:
    return {split: nf.build_pairs(split) for split in nf.SPLITS}


@pytest.fixture(scope="module")
def synthetic() -> dict:
    return nf.run_synthetic()


def test_pairs_are_deterministic_and_seed_dependent(pairs: dict[str, list[nf.Pair]]) -> None:
    assert nf.build_pairs("dev") == pairs["dev"]
    assert nf.build_pairs("dev", seed=1) != pairs["dev"]


def test_each_split_has_at_least_100_different_person_pairs_per_group(
    pairs: dict[str, list[nf.Pair]],
) -> None:
    for split, rows in pairs.items():
        for g in GROUPS:
            diff = [p for p in rows if p.group == g and not p.same_person]
            same = [p for p in rows if p.group == g and p.cls == "surface"]
            assert len(diff) >= 100, (split, g, len(diff))
            assert len(same) >= 100, (split, g, len(same))
            assert {p.cls for p in rows if p.group == g and p.same_person} >= {"surface", "typo"}


def test_no_base_name_or_family_token_crosses_the_split(pairs: dict[str, list[nf.Pair]]) -> None:
    for g in GROUPS:
        dev = {p.a for p in pairs["dev"] if p.group == g}
        test = {p.a for p in pairs["test"] if p.group == g}
        assert not dev & test, g
    for g in ("malay_patronymic", "indian_patronymic"):
        for split in nf.SPLITS:
            fathers = {
                normalise_person(p.a).patronym[-1]
                for p in pairs[split]
                if p.group == g and normalise_person(p.a).patronym
            }
            assert fathers, (g, split)
            assert {nd.split_of(f) for f in fathers} == {split}, (g, split)


def test_split_of_is_stable_and_roughly_even() -> None:
    assert nd.split_of("Hafiz") == nd.split_of("hafiz")
    tokens = nd.EXT_MALAY_FATHERS + nd.EXT_INDIAN_FATHERS + nd.EXT_WESTERN_SURNAMES
    share = sum(nd.split_of(t) == "dev" for t in tokens) / len(tokens)
    assert 0.3 < share < 0.7


def test_different_person_pairs_are_never_aliases_or_nested_names(
    pairs: dict[str, list[nf.Pair]],
) -> None:
    for rows in pairs.values():
        for p in rows:
            if p.same_person:
                continue
            if p.cls == "father_son":  # the same two names on purpose, in the other order
                continue
            assert not nf._alias_equal(p.a, p.b), p
            if p.cls in ("shared_family", "shared_given"):
                assert not nf._nested(p.a, p.b), p


def test_father_and_son_swap_is_a_different_person(pairs: dict[str, list[nf.Pair]]) -> None:
    swaps = [p for rows in pairs.values() for p in rows if p.cls == "father_son"]
    assert len(swaps) >= 4 * nf.N_HARD // 2
    assert all(not nf.accepts_new(p.a, p.b) for p in swaps)


def test_the_fix_closes_the_patronymic_gap_on_both_splits(synthetic: dict) -> None:
    for split in nf.SPLITS:
        tables = synthetic["splits"][split]["matchers"]
        new = nf.aggregate(tables["new"], ("near_spelling",))
        old = nf.aggregate(tables["old"], ("near_spelling",))
        for g in GROUPS:
            assert new[g]["rate"] <= 0.02, (split, g, new[g])
        for g in ("malay_patronymic", "indian_patronymic"):
            assert old[g]["rate"] > 0.2, (split, g, old[g])  # the emulated old bug is visible


def test_same_person_false_flags_stay_under_two_percent_in_every_group(synthetic: dict) -> None:
    for split in nf.SPLITS:
        agg = nf.aggregate(synthetic["splits"][split]["matchers"]["new"], ("surface", "alias"))
        for g in GROUPS:
            assert agg[g]["rate"] <= 0.02, (split, g, agg[g])
        rates = [agg[g]["rate"] for g in GROUPS]
        assert max(rates) - min(rates) <= 0.05  # disparity


def test_a_one_letter_typo_of_the_same_person_is_flagged_by_design(synthetic: dict) -> None:
    for split in nf.SPLITS:
        agg = nf.aggregate(synthetic["splits"][split]["matchers"]["new"], ("typo",))
        for g in GROUPS:
            assert agg[g]["rate"] == 1.0, (split, g, agg[g])  # the price of catching the fake


def test_wilson_interval_is_reported(synthetic: dict) -> None:
    cell = synthetic["splits"]["test"]["matchers"]["new"]["malay_patronymic/near_spelling"]
    assert 0 <= cell["lo"] <= cell["rate"] <= cell["hi"] <= 1 and cell["n"] >= 100


# ---------------------------------------------------------------- Wikidata


def _write(path: Path, rows: list[dict[str, str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh, ["qid", "country", "label_en", "alt_en", "pair_type", "split"], lineterminator="\n"
        )
        w.writeheader()
        w.writerows(rows)
    return path


def _row(qid: str, label: str, alt: str, split: str = "test", kind: str = "x") -> dict[str, str]:
    return {
        "qid": qid, "country": "India", "label_en": label, "alt_en": alt,
        "pair_type": kind, "split": split,
    }  # fmt: skip


def test_usable_positive_filter() -> None:
    assert nf.usable_positive(_row("Q1", "Hemant Laxman Gokhale", "H.L.Gokhale"))
    assert not nf.usable_positive(_row("Q2", "Aun Koh", "Chubby Hubby", kind="unrelated_token_set"))
    assert not nf.usable_positive(_row("Q3", "Amandeep Khare", "Cricketer Amandeep Khare"))
    assert not nf.usable_positive(_row("Q4", "Single", "Single Name"))  # one token
    assert not nf.usable_positive(_row("Q5", "John Smith 2", "John Smith"))  # digits
    assert not nf.usable_positive(_row("Q6", "Ravi Kumar", "Anil Sharma"))  # no shared token


def test_wikidata_negatives_are_different_people_one_or_two_letters_apart(tmp_path: Path) -> None:
    rows = [
        _row("Q1", "Ravi Kumar", "R. Kumar"),
        _row("Q2", "Rani Kumar", "Rani K"),  # edit 2 from Ravi
        _row("Q3", "Ravi Kumar", "Ravi K."),  # same label as Q1, another person: never paired
        _row("Q4", "Anand Singh", "A. Singh"),
        _row("Q5", "Mohan Singh", "M. Singh", split="dev"),  # other split: never paired
    ]
    neg = nf.wikidata_negatives(rows, "test")
    assert {frozenset((a, b)) for a, b, _ in neg} == {frozenset(("Ravi Kumar", "Rani Kumar"))}
    assert len(neg) == 2  # Rani Kumar against each of the two Ravi Kumars


def test_run_wikidata_scores_positives_and_negatives(tmp_path: Path) -> None:
    rows = [
        _row("Q1", "Hemant Laxman Gokhale", "H.L.Gokhale"),  # initials: accepted
        _row("Q2", "Mani Ratnam", "Mani Rathnam"),  # one-letter slip: flagged by design
        _row("Q3", "Ravi Kumar", "R. Kumar"),
        _row("Q4", "Rani Kumar", "Rani K Kumar"),
    ]
    out = nf.run_wikidata(_write(tmp_path / "wd.csv", rows))
    t = out["splits"]["test"]
    assert t["positives_used"] == 4 and t["negatives"] == 1
    new = t["matchers"]["new"]
    assert new["false_flag"]["k"] == 1  # only the slip
    assert new["wrong_match"]["k"] == 0
    assert t["slip_positives"]["k"] == 1
    assert "Wilson" not in str(out)  # intervals are numbers, not prose


def test_markdown_report_lists_every_group_and_split(synthetic: dict) -> None:
    text = nf.render_markdown(synthetic, None)
    for g in GROUPS:
        assert g in text
    assert "Synthetic DEV" in text and "Synthetic TEST" in text
    assert "old matcher" in text and "[0." in text
