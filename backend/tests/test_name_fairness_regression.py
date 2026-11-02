"""Fast, offline regression gate for person-name matching (docs/name-fairness.md, "Metric and thresholds").

Runs on a small fixed fixture (backend/tests/data/name_fairness_fixture.json: fictitious generator
pairs and public Wikidata labels, DEV split only), so it needs no network and no export. It fails when
a change to ``app/names.py`` makes the matcher accept a near-spelled or other different person, or when
the false-flag rate of the same person, or the gap between name groups, rises.

The thresholds are OUR proposal; the bank decides what is fair and which attributes are protected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.names import SURNAME_VARIANTS
from scripts._eval import name_fairness as nf
from scripts._eval import name_fairness_fixture as fx
from scripts._gen.kyc_fairness import GROUPS

FIXTURE = Path(__file__).parent / "data" / "name_fairness_fixture.json"

# proposed thresholds (docs/name-fairness.md)
MAX_WRONG_MATCHES = 0  # different people accepted, near-spelled and hard negatives, per group
MAX_SAME_PERSON_FALSE_FLAG = 0.02  # surface forms and aliases, per group
MAX_SAME_PERSON_DISPARITY = 0.05  # max minus min over the six groups


@pytest.fixture(scope="module")
def fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def now(fixture: dict[str, Any]) -> dict[str, Any]:
    return fx.measure(fixture)


def test_fixture_is_the_documented_version_and_has_every_group_and_class(
    fixture: dict[str, Any],
) -> None:
    assert fixture["version"] == fx.FIXTURE_VERSION
    seen = {(r["group"], r["cls"]) for r in fixture["synthetic"]}
    for g in GROUPS:
        assert {(g, c) for c in ("near_spelling", "surface", "typo")} <= seen
    assert {r["country"] for r in fixture["aliases"]} == {"India", "Malaysia", "Singapore"}
    assert len(fixture["different_people"]) >= 50 and len(fixture["collisions"]) >= 50


def test_synthetic_part_of_the_fixture_is_what_the_generator_draws(fixture: dict[str, Any]) -> None:
    drawn = {(p.group, p.cls, p.a, p.b) for p in nf.build_pairs("dev")}
    for r in fixture["synthetic"]:
        assert (r["group"], r["cls"], r["a"], r["b"]) in drawn, r


def test_no_near_spelled_or_hard_negative_pair_is_accepted_in_any_group(
    now: dict[str, Any],
) -> None:
    assert all(v <= MAX_WRONG_MATCHES for v in now["wrong_matches_by_group"].values()), now[
        "wrong_matches_by_group"
    ]


def test_real_different_people_one_or_two_letters_apart_are_never_accepted(
    now: dict[str, Any],
) -> None:
    assert now["different_people_accepted"] == 0


def test_sibling_initials_and_natural_collisions_do_not_rise(
    fixture: dict[str, Any], now: dict[str, Any]
) -> None:
    """A sibling written as initials, and two real people who share a name word, are accepted
    sometimes by design (an initial that lines up is ``probable``); the count must not rise above
    what the frozen baseline accepts in this fixture."""
    base_sib = {
        g: sum(
            1
            for r in fixture["synthetic"]
            if r["cls"] == "initials_sibling" and r["group"] == g and r["baseline_accepts"]
        )
        for g in GROUPS
    }
    for g in GROUPS:
        assert now["initials_sibling_accepted_by_group"][g] <= base_sib[g], g
    # the fixture holds every pair the adopted rules accept in addition to the baseline: their cost
    # (a few pairs) is recorded and must not grow
    assert now["collisions_accepted"] <= fixture["expect"]["collisions_accepted"]


def test_same_person_false_flags_and_the_gap_between_groups_stay_low(now: dict[str, Any]) -> None:
    for g, rate in now["same_person_false_flag_rate_by_group"].items():
        assert rate <= MAX_SAME_PERSON_FALSE_FLAG, (g, rate)
    assert now["same_person_disparity"] <= MAX_SAME_PERSON_DISPARITY


def test_same_person_numbers_never_rise_above_the_recorded_ones(
    fixture: dict[str, Any], now: dict[str, Any]
) -> None:
    rec = fixture["expect"]
    assert now["same_person_disparity"] <= rec["same_person_disparity"] + 1e-9
    for g, rate in now["same_person_false_flag_rate_by_group"].items():
        assert rate <= rec["same_person_false_flag_rate_by_group"][g] + 1e-9, g


def test_real_alias_not_accepted_counts_never_rise_in_any_country(
    fixture: dict[str, Any], now: dict[str, Any]
) -> None:
    rec = fixture["expect"]
    for c, k in rec["alias_not_accepted_by_country"].items():
        assert now["alias_not_accepted_by_country"][c] <= k, c
    for c, k in rec["alias_a_not_accepted_by_country"].items():
        assert now["alias_a_not_accepted_by_country"].get(c, 0) <= k, c
    assert now["alias_disparity"] <= rec["alias_disparity"] + 1e-9


def test_the_new_matcher_accepts_every_real_alias_the_baseline_accepted(
    fixture: dict[str, Any],
) -> None:
    for r in fixture["aliases"]:
        if r["baseline_accepts"]:
            assert nf.accepts_new(r["a"], r["b"]), r


def test_the_new_matcher_accepts_every_same_person_form_the_baseline_accepted(
    fixture: dict[str, Any],
) -> None:
    for r in fixture["synthetic"]:
        if r["same"] and r["baseline_accepts"]:
            assert nf.accepts_new(r["a"], r["b"]), r


def test_rules_that_were_adopted_keep_their_gains_in_the_fixture(fixture: dict[str, Any]) -> None:
    gained = [
        r
        for r in fixture["aliases"]
        if not r["baseline_accepts"] and nf.accepts_new(r["a"], r["b"])
    ]
    assert gained, "the fixture should hold at least one alias the new rules gained"


def test_every_surname_variant_is_a_pair_the_one_letter_rule_never_sees() -> None:
    from rapidfuzz.distance import Levenshtein

    members = [m for group in SURNAME_VARIANTS for m in group]
    assert len(members) == len(set(members))
    for group in SURNAME_VARIANTS:
        for i, x in enumerate(group):
            for y in group[i + 1 :]:
                assert Levenshtein.distance(x, y) >= 2, (x, y)
