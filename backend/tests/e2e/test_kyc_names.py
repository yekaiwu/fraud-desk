"""Name matching verified through the real pipeline (plan 4.6 note, 7.2 fairness).

Fictitious pairs in six name-form groups (docs/test-data-and-eval.md 4.7). A same-person pair prints
one surface form on the documents and holds another in the registry: no name rule may fire. A
near-spelled different person must still be flagged where the matcher can see it. The remaining
misses are an honest limit, listed here so the test fails loudly if the behaviour changes.
"""

from __future__ import annotations

import shutil

import pytest

from app.names import match_person
from scripts._gen.kyc_fairness import GROUPS, build_fairness
from tests.e2e._support import run_bundle, temp_root

NAME_RULES = {"x_directors", "x_ownership_vs_registry", "x_entity"}


@pytest.fixture(scope="module")
def fairness():
    root = temp_root("gldb-e2e-names-")
    pairs = build_fairness(root, same_per_group=5, diff_per_group=2)
    yield root, pairs
    shutil.rmtree(root, ignore_errors=True)


def _name_flags(root, pair_id: str) -> set[str]:  # noqa: ANN001
    run = run_bundle(root / "fairness" / "cases" / pair_id, tmp=root / "runs" / pair_id)
    assert run.llm.calls == []
    return {f.rule for f in run.case.flags if f.severity != "info" and f.rule in NAME_RULES}


def test_same_person_surface_forms_never_raise_a_name_flag(fairness) -> None:  # noqa: ANN001
    root, pairs = fairness
    same = [p for p in pairs if p.same_person]
    assert {p.group for p in same} == set(GROUPS) and len(same) == 5 * len(GROUPS)
    wrongly_flagged = {p.pair_id: _name_flags(root, p.pair_id) for p in same}
    assert {k: v for k, v in wrongly_flagged.items() if v} == {}, [
        (p.pair_id, p.name_a, p.name_b) for p in same if wrongly_flagged[p.pair_id]
    ]


def test_near_spelled_different_people_are_still_flagged_where_the_matcher_sees_them(
    fairness,  # noqa: ANN001
) -> None:
    root, pairs = fairness
    diff = [p for p in pairs if not p.same_person]
    flagged = {p.pair_id for p in diff if _name_flags(root, p.pair_id)}
    assert flagged, "no near-spelled negative was flagged at all"
    # at least one negative is caught in more than one group (not a single-group accident)
    assert len({pid.rsplit("-", 1)[0] for pid in flagged}) >= 3
    # honest limit (checks-kyc-aml.md Known gaps): a one-letter slip with the same ID number on
    # both sides is read as a typo of the same person, and that is the only kind that slips through
    for p in diff:
        if p.pair_id not in flagged:
            assert p.form_b == "near_spelling_1", (p.pair_id, p.name_a, p.name_b)
            assert match_person(p.name_a, p.name_b).verdict in {"same", "probable"}


@pytest.mark.parametrize(
    ("a", "b", "verdict"),
    [
        ("Wong Jessica", "Jessica Wong", "same"),
        ("Tan Wei Ming", "TAN WEI MING", "same"),
        ("Rajesh s/o Kumar", "Rajesh Kumar", "same"),
        ("Muhammad Hafiz bin Ismail", "Muhammad Hafiz Ismail", "same"),
        ("Tan Wei Ming", "Tan Wei Hong", "different"),
        ("Jessica Wong", "Jessica Teo", "different"),
    ],
)
def test_matcher_accepts_surface_forms_and_separates_different_people(
    a: str, b: str, verdict: str
) -> None:
    assert match_person(a, b).verdict == verdict
