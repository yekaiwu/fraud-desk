"""Fairness smoke through the real pipeline (T-integration-kyc-05; plan 7.2, test-data-and-eval 4.7).

The smallest slice that still crosses every group: one same-person pair per designed name group
(six cases) and one near-spelled different person. No name rule may fire on a same-person pair; the
negative must still be flagged; and the eval's own scoring produces the full six-row table from the
same results. ``test_kyc_names.py`` covers the wider set (30 same-person pairs, 12 negatives).
"""

from __future__ import annotations

import shutil

import pytest

from scripts._eval import fairness as ev_fairness
from scripts._gen.kyc_fairness import GROUPS, build_fairness
from tests.e2e._support import run_bundle, temp_root

NAME_RULES = ev_fairness.NAME_RULES


@pytest.fixture(scope="module")
def smoke():
    root = temp_root("gldb-e2e-fairness-smoke-")
    pairs = build_fairness(root, same_per_group=1, diff_per_group=1)
    fired: dict[str, bool] = {}
    for p in pairs:
        run = run_bundle(root / "fairness" / "cases" / p.pair_id, tmp=root / "runs" / p.pair_id)
        assert run.llm.calls == []  # digital documents: no model call, nothing from the network
        fired[p.pair_id] = any(
            f.rule in NAME_RULES and f.severity != "info" for f in run.case.flags
        )
    yield pairs, fired
    shutil.rmtree(root, ignore_errors=True)


def test_one_same_person_pair_per_group_raises_no_name_flag(smoke) -> None:  # noqa: ANN001
    pairs, fired = smoke
    same = [p for p in pairs if p.same_person]
    assert sorted(p.group for p in same) == sorted(GROUPS) and len(same) == 6
    wrongly = [(p.pair_id, p.name_a, p.name_b) for p in same if fired[p.pair_id]]
    assert wrongly == []


def test_a_near_spelled_different_person_is_still_flagged(smoke) -> None:  # noqa: ANN001
    pairs, fired = smoke
    diff = [p for p in pairs if not p.same_person]
    assert diff, "the smoke set has no negative"
    # a one-letter slip with the same ID on both sides is read as a typo (documented limit in
    # checks-kyc-aml.md Known gaps), so the claim is "at least one negative is flagged"
    assert any(fired[p.pair_id] for p in diff), [(p.pair_id, p.name_a, p.name_b) for p in diff]


def test_the_eval_scoring_produces_the_full_six_row_table(smoke) -> None:  # noqa: ANN001
    pairs, fired = smoke
    rows = [
        {
            "pair_id": p.pair_id,
            "group": p.group,
            "same_person": p.same_person,
            "name_a": p.name_a,
            "name_b": p.name_b,
        }
        for p in pairs
    ]
    table = ev_fairness.score_groups(rows, fired, ev_fairness.matcher_verdicts(rows))
    assert [r["group"] for r in table["groups"]] == ev_fairness.GROUPS
    assert all(r["n_same"] == 1 and r["false_flags"] == 0 for r in table["groups"])
    assert table["overall"] in {"PASS", "FAIL"}  # a FAIL is a reported finding, not a build failure
