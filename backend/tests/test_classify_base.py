"""Decision rule, guardrails and the classifier registry (classification.md 3.1, 4.9, tests 15-17)."""

from __future__ import annotations

import itertools

import pytest

from app.classify.base import (
    LABEL_DESCRIPTIONS,
    LABELS,
    NONSPECIFIC,
    RISK_LABELS_C5,
    Candidate,
    ClassifierUnavailable,
    ClassifyItem,
    classify_many,
    decide,
    discover,
    get_classifier,
)
from app.classify.rules import RulesClassifier
from app.core.testing import make_ctx

MIN = 0.70


def cand(name: str, label: str, p: float, labels: tuple[str, ...], version: str = "1") -> Candidate:
    rest = (1 - p) / (len(labels) - 1)
    return Candidate(
        classifier=name,
        version=version,
        distribution={x: (p if x == label else rest) for x in labels},
    )


C1, C5, C3 = LABELS["C1"], LABELS["C5"], LABELS["C3"]


def test_single_candidate_specific_and_confident() -> None:
    d = decide("C1", [cand("rules", "invoice", 0.9, C1)], min_prob=MIN)
    assert (d.label, d.probability, d.classifier, d.version) == ("invoice", 0.9, "rules", "1")
    assert d.low_confidence is False and d.disagreement is None


def test_hosted_other_never_overrides_a_confident_specific_rules_label() -> None:
    d = decide(
        "C1",
        [cand("rules", "invoice", 0.9, C1), cand("llm:fake", "other", 0.95, C1)],
        min_prob=MIN,
    )
    assert d.label == "invoice" and d.classifier == "rules"
    assert d.disagreement is None  # the hosted label is non-specific
    assert d.low_confidence is False


def test_hosted_boilerplate_never_overrides_either() -> None:
    d = decide(
        "C1",
        [cand("rules", "bank_statement", 0.8, C1), cand("llm:fake", "boilerplate", 0.99, C1)],
        min_prob=MIN,
    )
    assert d.label == "bank_statement"


def test_disagreement_between_two_confident_specific_labels() -> None:
    d = decide(
        "C1",
        [cand("rules", "invoice", 0.9, C1), cand("llm:fake", "contract", 0.8, C1)],
        min_prob=MIN,
    )
    assert d.label == "invoice" and d.classifier == "rules"
    assert d.disagreement == "rules=invoice(0.90) llm=contract(0.80)"


def test_highest_probability_wins_and_ties_go_to_the_earlier_candidate() -> None:
    d = decide(
        "C1",
        [cand("rules", "invoice", 0.72, C1), cand("llm:fake", "contract", 0.9, C1)],
        min_prob=MIN,
    )
    assert d.label == "contract" and d.classifier == "llm:fake" and d.version == "1"
    tie = decide(
        "C1",
        [cand("rules", "invoice", 0.8, C1), cand("llm:fake", "contract", 0.8, C1)],
        min_prob=MIN,
    )
    assert tie.label == "invoice"


def test_low_probability_specific_candidates_do_not_count_as_confident() -> None:
    d = decide(
        "C1",
        [cand("rules", "invoice", 0.55, C1), cand("llm:fake", "contract", 0.60, C1)],
        min_prob=MIN,
    )
    assert d.low_confidence is True
    assert d.label == "contract"  # the last consulted candidate's top label


def test_nothing_specific_gives_the_last_candidates_label_and_low_confidence() -> None:
    d = decide(
        "C1",
        [cand("rules", "other", 0.6, C1), cand("llm:fake", "boilerplate", 0.9, C1)],
        min_prob=MIN,
    )
    assert d.low_confidence is True and d.label == "boilerplate"


def test_c5_risk_label_beats_a_more_probable_benign_label() -> None:
    d = decide(
        "C5",
        [cand("rules", "crypto_virtual_assets", 0.95, C5), cand("llm:fake", "trading", 0.9, C5)],
        min_prob=MIN,
    )
    assert d.label == "crypto_virtual_assets"
    assert d.disagreement is not None


def test_c5_risk_rule_applies_from_half_probability_and_priority_order() -> None:
    d = decide(
        "C5",
        [cand("rules", "logistics", 0.9, C5), cand("llm:fake", "gambling", 0.55, C5)],
        min_prob=MIN,
    )
    assert d.label == "gambling" and d.low_confidence is True
    below = decide(
        "C5",
        [cand("rules", "logistics", 0.9, C5), cand("llm:fake", "gambling", 0.45, C5)],
        min_prob=MIN,
    )
    assert below.label == "logistics"
    order = decide(
        "C5",
        [
            cand("rules", "money_services", 0.9, C5),
            cand("llm:fake", "crypto_virtual_assets", 0.6, C5),
        ],
        min_prob=MIN,
    )
    assert order.label == "crypto_virtual_assets"
    assert RISK_LABELS_C5 == ("crypto_virtual_assets", "gambling", "money_services")


def test_c3_has_no_nonspecific_labels() -> None:
    d = decide("C3", [cand("rules", "other_bank", 0.9, C3)], min_prob=MIN)
    assert d.label == "other_bank" and d.low_confidence is False
    assert NONSPECIFIC["C3"] == set()


def test_decide_needs_a_candidate() -> None:
    with pytest.raises(ValueError):
        decide("C1", [], min_prob=MIN)


def test_never_lowers_risk_over_many_combinations() -> None:
    """Whenever a candidate is specific and confident, the decision is specific."""
    labels = ("invoice", "contract", "other", "boilerplate")
    probs = (0.4, 0.75, 0.95)
    options = [(lab, p) for lab in labels for p in probs]
    for first, second in itertools.product(options, repeat=2):
        cands = [cand("rules", *first, C1), cand("llm:fake", *second, C1)]
        d = decide("C1", cands, min_prob=MIN)
        confident = [c for c in (first, second) if c[0] not in NONSPECIFIC["C1"] and c[1] >= MIN]
        if confident:
            assert d.label not in NONSPECIFIC["C1"], (first, second)
            assert d.low_confidence is False
        else:
            assert d.low_confidence is True


def test_every_decision_carries_label_probability_classifier_and_version() -> None:
    d = decide("C1", [cand("rules", "invoice", 0.9, C1)], min_prob=MIN)
    assert d.classifier and d.version and 0 <= d.probability <= 1 and d.distribution


# ----------------------------------------------------------------------------- registry


def test_registry_builds_rules_and_hosted_and_refuses_local_stub() -> None:
    ctx = make_ctx()
    discover()
    discover()  # idempotent
    rules = get_classifier("rules", ctx)
    assert isinstance(rules, RulesClassifier) and rules.version == "3"
    hosted = get_classifier("llm", ctx)
    assert hosted.name == "llm" and hosted.version == "1"
    with pytest.raises(ClassifierUnavailable, match="local classifier not available"):
        get_classifier("local", ctx)
    with pytest.raises(ClassifierUnavailable):
        get_classifier("nonsense", ctx)


def test_classify_many_loops_when_the_class_has_no_batch_method() -> None:
    rules = RulesClassifier()
    items = [ClassifyItem(text="TERM LOAN DRAWDOWN"), ClassifyItem(text="REFUND OF DEPOSIT")]
    out = classify_many(rules, items, LABELS["C4"], question="C4")
    assert [max(o, key=o.get) for o in out if o] == ["financing_proceeds", "refund"]


def test_label_descriptions_cover_every_label() -> None:
    for question, labels in LABELS.items():
        assert set(LABEL_DESCRIPTIONS[question]) == set(labels)
        assert all(LABEL_DESCRIPTIONS[question][label].endswith(".") for label in labels)
