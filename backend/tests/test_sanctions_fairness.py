"""Offline regression gate for sanctions-screening false positives by name group
(docs/verification-sources.md, docs/name-fairness.md).

Runs on backend/tests/data/sanctions_fairness_fixture.json: a sample of public official list records
(plus every record the pseudo-customers could touch), 900 fictitious customers in the six generator name
groups, and 600 persons drawn from the records and screened without their own listing. No network.

It fails when a change to the matcher (or to ``app/names.py``) makes screening hit more people who are not
on a list, or hit one group more than another, or stop finding a listed person whose name is written in
another order or with one letter slipped. The thresholds are OUR proposal; the bank decides what is fair
and which attributes are protected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts._eval import metrics as mx
from scripts._eval import name_fairness as nf
from scripts._eval import sanctions_fairness as sf
from scripts._gen.kyc_fairness import GROUPS

FIXTURE = Path(__file__).parent / "data" / "sanctions_fairness_fixture.json"

# measured when the fixture was written (2026-11-03); a rise is a regression
EXPECTED_DRAW_HITS = {"other_or_unstated": 1, "iran": 1, "russia": 3, "russian federation": 2}
MAX_CUSTOMER_HITS = (
    0  # a fictitious customer is on no list: any medium-or-higher hit is a false positive
)
# the fixture's groups hold 35 to 428 draws, so these are loose by design; the exact counts above are the gate
MAX_DRAW_RATE = 0.08  # per group
MAX_DISPARITY = 0.06
MIN_REORDER_RECALL = 0.97
MIN_SLIP_RECALL = 0.88


@pytest.fixture(scope="module")
def fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def now(fixture: dict[str, Any]) -> dict[str, Any]:
    return sf.measure_fixture(fixture)


def test_the_fixture_covers_every_group_and_is_the_documented_version(
    fixture: dict[str, Any],
) -> None:
    assert fixture["version"] == sf.FIXTURE_VERSION
    assert set(fixture["customers"]) == set(GROUPS) and all(
        len(v) >= 100 for v in fixture["customers"].values()
    )
    assert {d["group"] for d in fixture["draws"]} == set(EXPECTED_DRAW_HITS)
    assert len(fixture["entries"]) >= 1500


def test_customer_names_are_the_generators_not_real_people(fixture: dict[str, Any]) -> None:
    drawn = {p.a for p in nf.build_pairs("test")} | {p.b for p in nf.build_pairs("test")}
    for names in fixture["customers"].values():
        assert set(names) <= drawn


def test_no_fictitious_customer_in_any_group_is_hit(now: dict[str, Any]) -> None:
    for group, (k, n) in now["customers"].items():
        lo, hi = mx.wilson(k, n)
        assert k <= MAX_CUSTOMER_HITS, f"{group}: {k}/{n} hit (95% interval {lo:.3f} to {hi:.3f})"


def test_real_list_persons_screened_without_their_own_listing_do_not_hit_more_than_before(
    now: dict[str, Any],
) -> None:
    for group, (k, n) in now["draws"].items():
        assert k <= EXPECTED_DRAW_HITS[group], f"{group}: {k}/{n}"
        assert k / n <= MAX_DRAW_RATE


def test_the_gap_between_name_groups_stays_small(now: dict[str, Any]) -> None:
    rates = {g: k / n for g, (k, n) in now["draws"].items()}
    assert max(rates.values()) - min(rates.values()) <= MAX_DISPARITY
    groups = {g: [(1 if i < k else 0, 1) for i in range(n)] for g, (k, n) in now["draws"].items()}
    d = nf.disparity(groups, b=200)
    assert d["point"] <= MAX_DISPARITY and d["hi"] <= 2 * MAX_DISPARITY


def test_it_still_finds_a_listed_person_written_in_another_order_or_with_one_letter_slipped(
    now: dict[str, Any],
) -> None:
    assert now["recall"]["reordered"]["rate"] >= MIN_REORDER_RECALL
    assert now["recall"]["one_letter_slip"]["rate"] >= MIN_SLIP_RECALL
