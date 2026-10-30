"""TXN evaluation data: messy statement descriptions with the ground truth kept (docs 9.1).

No model is called. The generator's own statements are used to prove the catalogue covers every
description it prints.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from scripts._gen import lexicon as lx
from scripts._gen import sem_txn as t
from scripts._gen import world
from scripts._gen.sem_common import SEEDS, SPLITS, split_of_key, stem_key


@pytest.fixture(scope="module")
def sets() -> dict[str, list[dict]]:
    return {s: t.build_rows(s) for s in SPLITS}


def test_seeds_and_counts(sets: dict[str, list[dict]]) -> None:
    assert SEEDS == {"dev": 5101, "held_out": 7001}
    for rows in sets.values():
        assert len(rows) == 12 * 24 + 2 * 48
        by_cat = Counter(r["truth"]["category"] for r in rows)
        assert set(by_cat) == set(t.CATEGORIES)
        for cat, n in by_cat.items():
            assert n == (48 if cat in t.DOUBLED else 24), cat
        assert Counter(r["level"] for r in rows) == {lv: len(rows) // 4 for lv in t.LEVELS}
        for cat in t.CATEGORIES:
            lv = Counter(r["level"] for r in rows if r["truth"]["category"] == cat)
            assert len(set(lv.values())) == 1, cat  # an equal share at every level


def test_reproducible_from_the_seed_and_different_for_another(sets: dict[str, list[dict]]) -> None:
    assert t.build_rows("dev") == sets["dev"]
    assert t.build_rows("dev", seed=1) != sets["dev"]
    assert sets["dev"] != sets["held_out"]


def test_every_row_keeps_evidence_for_its_labels(sets: dict[str, list[dict]]) -> None:
    for rows in sets.values():
        for r in rows:
            cat = r["truth"]["category"]
            assert t.category_evidence(cat, r["description"]), (cat, r["description"])
            assert r["evidence_ok"], r["description"]
            if r["truth"]["counterparty"] and not r["hard_counterparty"]:
                assert t.c5_evidence(r["truth"]["c5"], r["description"]), r["description"]


def test_labels_come_from_the_generator_template_not_from_the_messy_text(
    sets: dict[str, list[dict]],
) -> None:
    for rows in sets.values():
        for r in rows:
            truth = r["truth"]
            assert t.generator_category(r["clean_description"]) == truth["category"]
            spec = t.SPECS[truth["category"]]
            assert truth["direction"] == spec.direction and truth["c4"] == spec.c4
            assert (r["debit"] is not None) == (spec.direction == "debit")
            assert (r["credit"] is not None) == (spec.direction == "credit")
            if truth["counterparty"]:
                assert truth["counterparty"] in r["clean_description"]
            else:
                assert truth["c5"] == "other"  # the generator's value for a fixed vendor
            if r["level"] == 0:
                assert r["description"] == r["clean_description"] and not r["ops"]
            else:
                assert r["description"] != r["clean_description"] and r["ops"]


def test_the_catalogue_covers_every_description_the_generator_prints() -> None:
    seen = 0
    for scenario in ("loan", "kyc", "aml"):
        for index in (0, 1, 2, 3):
            w = world.build_world(scenario, index)
            for stmt in w.statements:
                for row in stmt.rows:
                    if row.kind == "bf":  # "BALANCE BROUGHT FORWARD" is not a transaction
                        continue
                    want = t.KIND_TO_CATEGORY.get(row.kind) or t.C4_OF_NONTRADE.get(row.c4 or "")
                    assert want, (row.kind, row.description)
                    assert t.generator_category(row.description) == want, row.description
                    seen += 1
    assert seen > 1500


def test_every_perturbation_and_both_foreign_languages_occur(sets: dict[str, list[dict]]) -> None:
    for split, rows in sets.items():
        ops = Counter(o.split(":")[0] for r in rows for o in r["ops"])
        for op in ("abbrev", "typo", "truncate", "case", "ref", "spacing", "channel", "foreign"):
            assert ops[op] >= 15, (split, op, ops)
        langs = Counter(lang for r in rows for lang in r["langs"])
        assert langs["ms"] >= 25 and langs["zh"] >= 25, (split, langs)
        assert not any(r["langs"] for r in rows if r["level"] < 2)  # foreign wording: levels 2-3
        # heavier levels change more
        mean = {lv: sum(len(r["ops"]) for r in rows if r["level"] == lv) for lv in (1, 2, 3)}
        assert mean[1] < mean[2] < mean[3]


def test_abbreviations_cut_the_counterparty_keyword_but_keep_it_recognisable(
    sets: dict[str, list[dict]],
) -> None:
    rows = [r for rows in sets.values() for r in rows if r["truth"]["counterparty"]]
    shortened = [r for r in rows if r["level"] > 0 and "abbrev" in r["ops"]]
    assert len(shortened) >= 40
    assert any("TRDG" in r["description"].upper() for r in rows)
    assert any("PTE LTD" not in r["description"].upper() for r in shortened)


def test_counterparty_brands_and_vendors_never_cross_the_split(
    sets: dict[str, list[dict]],
) -> None:
    brand_words = {w.upper() for w in (*lx.BRANDS, *t.BRANDS_EXT)}
    vendors = {
        v.upper() for pool in (t.UTIL_VENDORS, t.TELCO_VENDORS, t.INSURERS, t.BANKS) for v in pool
    }
    coined = {(a + b).upper() for a in t.COINED_A for b in t.COINED_B}
    seen: dict[str, set[str]] = {s: set() for s in SPLITS}
    for split, rows in sets.items():
        for r in rows:
            text = r["clean_description"]
            for word in brand_words | vendors | coined:
                if word in text:
                    seen[split].add(word)
                    assert split_of_key(stem_key(word.split()[0])) == split, (word, split)
    assert not seen["dev"] & seen["held_out"]
    assert len(seen["dev"]) >= 20 and len(seen["held_out"]) >= 20


def test_risky_labels_are_present_and_hard_counterparties_have_no_keyword(
    sets: dict[str, list[dict]],
) -> None:
    for rows in sets.values():
        c5 = Counter(r["truth"]["c5"] for r in rows if r["truth"]["counterparty"])
        assert set(c5) == set(lx.C5_LABELS)
        assert min(c5.values()) >= 6, c5
        for r in rows:
            if r["hard_counterparty"]:
                assert not lx.has_token(r["truth"]["counterparty"])
            elif r["truth"]["counterparty"]:
                assert lx.label_from_tokens(r["truth"]["counterparty"]) == r["truth"]["c5"]


def test_perturb_is_a_pure_function_of_its_inputs_and_never_returns_labels() -> None:
    args = ("FAST PAYMENT TO ACME LOGISTICS PTE LTD", "supplier_payment", {})
    a = t.perturb(*args, random.Random(3), 3, c5="logistics")
    b = t.perturb(*args, random.Random(3), 3, c5="logistics")
    assert a == b and a.ops
    assert set(vars(a)) == {"text", "ops", "langs", "level_applied"}
    assert t.perturb(*args, random.Random(3), 0).text == args[0]
    with pytest.raises(ValueError):
        t.perturb(*args, random.Random(3), 4)
    # a level that cannot keep the evidence is lowered, never kept with the labels broken
    for seed in range(40):
        m = t.perturb(*args, random.Random(seed), 3, c5="logistics")
        assert t.category_evidence("supplier_payment", m.text)
        assert t.c5_evidence("logistics", m.text)


def test_evidence_functions() -> None:
    assert t.category_evidence("payroll", "Gaji staf julai")
    assert t.category_evidence("payroll", "GIRO SALAR")  # truncated mid-word
    assert t.category_evidence("premises", "Wuye fei")
    assert not t.category_evidence("payroll", "FAST PAYMENT TO ACME")
    assert t.c5_evidence("crypto_virtual_assets", "BEACON DIGI ASSETS PTE")
    assert t.c5_evidence("logistics", "x FREIGHT x")
    assert t.c5_evidence("logistics", "x Freigt x")  # one slip in a long keyword
    assert not t.c5_evidence("gambling", "BEACON TRADING PTE LTD")
    assert not t.c5_evidence("money_services", "FUNDS TRANSFER TO ACME")  # transfer alone is not it
