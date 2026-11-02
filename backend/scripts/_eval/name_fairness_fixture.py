"""A small fixed fixture for the name-matching regression test (``tests/test_name_fairness_regression.py``).

The full DEV/TEST run needs the Wikidata export (not in the repository) and takes seconds to minutes.
The test must be fast, offline and deterministic, so it runs on a sample of pairs that is committed
as JSON. The sample is drawn from the DEV split only, with fixed seeds, and records what the
matcher did with each pair on the day it was drawn:

* ``synthetic``: fictitious pairs from the generator (``name_fairness.build_pairs("dev")``), a fixed
  number per group and class: near-spelled and other different people (must never be accepted),
  initials of a sibling (the baseline already accepts a few), same-person surface forms and aliases
  (must be accepted) and one-letter typos of the same person (flagged by design).
* ``aliases``: real same-person alias pairs (Wikidata labels, CC0), a fixed number per country,
  with the category of ``name_alias_categories`` and whether the frozen baseline accepted them.
* ``different_people``: real pairs of different people one or two letters apart (Wikidata).
* ``collisions``: real pairs of different people who share a name word, drawn so that every pair the
  baseline accepts is in the sample next to a number it does not.

``expect`` holds the numbers the test compares with. They may only move in the right direction
(fewer not accepted, no more wrong matches); a change that makes a number worse fails the test.
Rebuild with ``name_fairness.py --write-fixture PATH --wikidata CSV`` and review the diff.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts._eval import name_alias_categories as ac
from scripts._eval import name_fairness as nf
from scripts._gen.kyc_fairness import GROUPS

FIXTURE_VERSION = 1
SYNTHETIC_PER_CLASS = {
    "near_spelling": 12,
    "shared_family": 6,
    "shared_given": 6,
    "father_son": 6,
    "initials_sibling": 6,
    "surface": 20,
    "alias": 20,
    "typo": 6,
}
ALIASES_PER_COUNTRY = 60
DIFFERENT_PEOPLE = 60
COLLISIONS_ACCEPTED = 40
COLLISIONS_OTHER = 60
SEED = 4002


def build_fixture(wikidata_csv: Path) -> dict[str, Any]:
    split = "dev"
    pairs = nf.build_pairs(split)
    synthetic: list[dict[str, Any]] = []
    for g in GROUPS:
        for cls, n in SYNTHETIC_PER_CLASS.items():
            chosen = [p for p in pairs if p.group == g and p.cls == cls][:n]
            synthetic += [
                {
                    "group": p.group,
                    "cls": p.cls,
                    "a": p.a,
                    "b": p.b,
                    "same": p.same_person,
                    "baseline_accepts": nf.accepts_baseline(p.a, p.b),
                }
                for p in chosen
            ]
    rows = [r for r in nf.read_wikidata(wikidata_csv) if r["split"] == split]
    pos = [r for r in rows if nf.usable_positive(r)]
    rng = random.Random(f"{SEED}:fixture:aliases")
    aliases: list[dict[str, Any]] = []
    for country in sorted({r["country"] for r in pos}):
        sample = rng.sample([r for r in pos if r["country"] == country], ALIASES_PER_COUNTRY)
        for r in sorted(sample, key=lambda r: (r["label_en"], r["alt_en"])):
            a, b = r["label_en"], r["alt_en"]
            aliases.append(
                {
                    "country": country,
                    "a": a,
                    "b": b,
                    "category": ac.categorise(a, b),
                    "baseline_accepts": nf.accepts_baseline(a, b),
                }
            )
    neg = nf.wikidata_negatives(nf.read_wikidata(wikidata_csv), split)
    rng = random.Random(f"{SEED}:fixture:negatives")
    different = [
        {"a": a, "b": b, "baseline_accepts": nf.accepts_baseline(a, b)}
        for a, b, _ in sorted(rng.sample(neg, min(DIFFERENT_PEOPLE, len(neg))))
    ]
    coll = nf.natural_collisions(nf.read_wikidata(wikidata_csv), split)
    rng = random.Random(f"{SEED}:fixture:collisions")
    accepted = [c for c in coll if nf.accepts_baseline(*c)]
    newly = [c for c in coll if nf.accepts_new(*c) and not nf.accepts_baseline(*c)]
    others = [c for c in coll if not nf.accepts_new(*c)]
    chosen_c = (
        rng.sample(accepted, min(COLLISIONS_ACCEPTED, len(accepted)))
        + newly
        + rng.sample(others, COLLISIONS_OTHER)
    )
    collisions = [
        {"a": a, "b": b, "baseline_accepts": nf.accepts_baseline(a, b)}
        for a, b in sorted(set(chosen_c))
    ]
    fixture: dict[str, Any] = {
        "version": FIXTURE_VERSION,
        "note": "Fictitious names (synthetic) and public Wikidata labels (CC0). Drawn from DEV only. "
        "See backend/scripts/_eval/name_fairness_fixture.py.",
        "synthetic": synthetic,
        "aliases": aliases,
        "different_people": different,
        "collisions": collisions,
    }
    fixture["expect"] = expectations(fixture)
    return fixture


def _rate(k: int, n: int) -> float:
    return k / n if n else 0.0


def expectations(fixture: dict[str, Any]) -> dict[str, Any]:
    """What the numbers are today (called with the matcher in ``app.names``)."""
    return measure(fixture)


def measure(fixture: dict[str, Any]) -> dict[str, Any]:
    """The numbers the regression test compares, for the matcher in ``app.names``."""
    syn = fixture["synthetic"]
    diff_classes = ("near_spelling", "shared_family", "shared_given", "father_son")
    wrong: dict[str, int] = defaultdict(int)
    sib: dict[str, int] = defaultdict(int)
    same_n: dict[str, int] = defaultdict(int)
    same_fail: dict[str, int] = defaultdict(int)
    for row in syn:
        acc = nf.accepts_new(row["a"], row["b"])
        if row["cls"] in diff_classes and acc:
            wrong[row["group"]] += 1
        elif row["cls"] == "initials_sibling" and acc:
            sib[row["group"]] += 1
        elif row["cls"] in ("surface", "alias"):
            same_n[row["group"]] += 1
            same_fail[row["group"]] += 0 if acc else 1
    rates = {g: _rate(same_fail[g], same_n[g]) for g in GROUPS}
    by_country_n: dict[str, int] = defaultdict(int)
    by_country_fail: dict[str, int] = defaultdict(int)
    by_country_a: dict[str, int] = defaultdict(int)
    for row in fixture["aliases"]:
        by_country_n[row["country"]] += 1
        if not nf.accepts_new(row["a"], row["b"]):
            by_country_fail[row["country"]] += 1
            if ac.bucket_of(row["category"]) == "a":
                by_country_a[row["country"]] += 1
    c_rates = {c: _rate(by_country_fail[c], by_country_n[c]) for c in by_country_n}
    return {
        "wrong_matches_by_group": {g: wrong[g] for g in GROUPS},
        "initials_sibling_accepted_by_group": {g: sib[g] for g in GROUPS},
        "same_person_false_flag_rate_by_group": rates,
        "same_person_disparity": max(rates.values()) - min(rates.values()),
        "alias_not_accepted_by_country": dict(sorted(by_country_fail.items())),
        "alias_a_not_accepted_by_country": {c: by_country_a[c] for c in sorted(by_country_n)},
        "alias_disparity": max(c_rates.values()) - min(c_rates.values()),
        "different_people_accepted": sum(
            1 for r in fixture["different_people"] if nf.accepts_new(r["a"], r["b"])
        ),
        "collisions_accepted": sum(
            1 for r in fixture["collisions"] if nf.accepts_new(r["a"], r["b"])
        ),
    }


def write_fixture(wikidata_csv: Path, out: Path) -> dict[str, Any]:
    fixture = build_fixture(wikidata_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return fixture
