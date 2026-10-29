"""Eval harness metric functions (docs/test-data-and-eval.md section 4.4): Wilson intervals, nearest
rank percentiles, the 'new relative to the base' catch logic, localisation, false alarms, ranking."""

from __future__ import annotations

import math

import pytest

from scripts._eval import metrics as mx


def flag(rule: str, severity: str = "high", docs: tuple[str, ...] = ("d1",), **kw):  # noqa: ANN003, ANN201
    return {"rule": rule, "severity": severity, "doc_keys": list(docs), **kw}


# ------------------------------------------------------------------------------- intervals


def test_wilson_on_known_counts() -> None:
    lo, hi = mx.wilson(4, 5)
    assert lo == pytest.approx(0.3755, abs=1e-3) and hi == pytest.approx(0.9638, abs=1e-3)
    assert mx.wilson(0, 10)[0] == 0.0 and mx.wilson(0, 10)[1] == pytest.approx(0.2775, abs=1e-3)
    lo, hi = mx.wilson(5, 5)
    assert hi == 1.0 and lo == pytest.approx(0.5655, abs=1e-3)
    assert mx.wilson(0, 0) == (0.0, 1.0)


def test_rate_text_prints_denominator_and_interval_below_100_only() -> None:
    assert mx.rate_text(4, 5).startswith("4/5 = 0.80 [0.38-0.96]")
    assert "[" not in mx.rate_text(90, 100)
    assert "no samples" in mx.rate_text(0, 0)


def test_nearest_rank_percentile() -> None:
    vals = [float(x) for x in range(1, 21)]  # 1..20
    assert mx.percentile_nearest_rank(vals, 0.95) == 19.0  # ceil(0.95 x 20) - 1 = index 18
    assert mx.percentile_nearest_rank(vals, 0.5) == 10.0
    assert mx.percentile_nearest_rank([5.0], 0.95) == 5.0
    assert mx.percentile_nearest_rank([], 0.5) is None
    assert mx.percentile_nearest_rank([3.0, 1.0, 2.0], 0.95) == 3.0


# ---------------------------------------------------------------------------- catch logic


def test_already_firing_rule_is_not_a_catch() -> None:
    base = [flag("bs_dates", "low", docs=("bank_statement:2025-07",))]
    tampered = [flag("bs_dates", "low", docs=("bank_statement:2025-07",))]
    assert mx.caught_flags(tampered, base, ["bs_dates"]) == []


def test_new_flag_on_another_document_is_a_catch() -> None:
    base = [flag("bs_dates", "low", docs=("bank_statement:2025-07",))]
    tampered = [*base, flag("bs_dates", "medium", docs=("bank_statement:2025-09",))]
    got = mx.caught_flags(tampered, base, ["bs_dates"])
    assert [f["doc_keys"] for f in got] == [["bank_statement:2025-09"]]


def test_info_flags_never_count_as_a_catch() -> None:
    assert mx.caught_flags([flag("inv_sequence", "info")], [], ["inv_sequence"]) == []


def test_info_by_design_rule_counts_as_a_catch() -> None:
    f = flag("ai_provenance_hint", "info")
    assert mx.caught_flags([f], [], ["ai_provenance_hint"], info_rules={"ai_provenance_hint"}) == [
        f
    ]
    # still not a catch when the base already carried it
    assert (
        mx.caught_flags([f], [f], ["ai_provenance_hint"], info_rules={"ai_provenance_hint"}) == []
    )


def test_registered_info_rules_are_detected() -> None:
    from scripts._eval.suites import info_by_design_rules

    assert info_by_design_rules(["ai_provenance_hint", "bs_dates"]) == {"ai_provenance_hint"}


def test_cross_document_flags_compare_by_sorted_document_keys() -> None:
    a = flag("x_invoice_vs_receipts", docs=("invoice:1", "bank_statement:2"))
    b = flag("x_invoice_vs_receipts", docs=("bank_statement:2", "invoice:1"))
    assert mx.flag_key(a) == mx.flag_key(b)
    assert mx.caught_flags([b], [a], ["x_invoice_vs_receipts"]) == []


# ------------------------------------------------------------------------- localisation


def test_localisation_row_target() -> None:
    target = {"doc_key": "s", "table": "transactions", "row": 4, "field": None, "box": None}
    hit = flag("bs_running_balance", table="transactions", row=4, docs=("s",))
    miss = flag("bs_running_balance", table="transactions", row=9, docs=("s",))
    assert mx.is_localised([hit], target, target_doc_keys=["s"])
    assert not mx.is_localised([miss], target, target_doc_keys=["s"])


def test_localisation_row_target_matches_by_printed_cells_when_numbering_differs() -> None:
    target = {"doc_key": "s", "table": "transactions", "row": 4}
    sig = {"date": "2025-07-03", "credit": "100.00", "balance": "500.00"}
    f = flag(
        "bs_running_balance",
        table="transactions",
        row=3,
        docs=("s",),
        row_sig=dict(sig, debit=None),
    )
    assert mx.is_localised([f], target, target_doc_keys=["s"], target_row_sig=sig)
    other = flag(
        "bs_running_balance",
        table="transactions",
        row=3,
        docs=("s",),
        row_sig=dict(sig, balance="1.00"),
    )
    assert not mx.is_localised([other], target, target_doc_keys=["s"], target_row_sig=sig)


def test_localisation_field_target_needs_the_same_document() -> None:
    target = {"doc_key": "inv", "field": "total", "row": None}
    assert mx.is_localised(
        [flag("inv_total", field="total", docs=("inv",))], target, target_doc_keys=["inv"]
    )
    assert not mx.is_localised(
        [flag("inv_total", field="total", docs=("other",))], target, target_doc_keys=["inv"]
    )
    assert not mx.is_localised(
        [flag("inv_total", field="subtotal", docs=("inv",))], target, target_doc_keys=["inv"]
    )


def test_localisation_box_overlap_threshold() -> None:
    box = {"page": 1, "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.1}
    target = {"doc_key": "s", "box": box, "row": None, "field": None}

    def at(x: float, w: float = 0.2) -> dict:
        return flag(
            "pdf_text_vs_render",
            docs=("s",),
            regions=[{"sha": "abc", "page": 1, "x": x, "y": 0.5, "w": w, "h": 0.1}],
        )

    ok = dict(target_doc_keys=["s"], target_box=box, target_box_sha="abc")
    assert mx.is_localised([at(0.5)], target, **ok)
    assert mx.is_localised([at(0.56)], target, **ok)  # 70% of the target box covered
    assert not mx.is_localised([at(0.65)], target, **ok)  # 25% covered: below 0.30
    wrong_file = dict(ok, target_box_sha="zzz")
    assert not mx.is_localised([at(0.5)], target, **wrong_file)


def test_file_level_targets_are_not_localisable() -> None:
    assert not mx.localisation_applicable({"doc_key": "s", "row": None, "field": None, "box": None})
    assert mx.localisation_applicable({"doc_key": "s", "row": 0})
    assert mx.localisation_applicable({"doc_key": "s", "field": "total"})


# ------------------------------------------------------------------------- false alarms


def test_false_alarms_count_high_and_medium_only_and_skip_quirk_allowed_rules() -> None:
    flags = [
        flag("bs_totals", "high"),
        flag("inv_dates", "medium"),
        flag("bs_dates", "low"),
        flag("doc_unclassified", "info"),
        flag("x_invoice_vs_receipts", "medium"),
    ]
    bad, allowed = mx.false_alarm_flags(flags, may_trigger=["x_invoice_vs_receipts"])
    assert sorted(f["rule"] for f in bad) == ["bs_totals", "inv_dates"]
    assert [f["rule"] for f in allowed] == ["x_invoice_vs_receipts"]


def test_per_ten() -> None:
    assert mx.per_ten(3, 30) == pytest.approx(1.0)
    assert mx.per_ten(0, 0) is None


# -------------------------------------------------------------------------------- ranking


def test_precision_recall_and_first_position_on_a_perfect_queue() -> None:
    q = [True, True, True, False, False, False, False, False, False, False]
    assert mx.precision_at_k(q, 5) == pytest.approx(0.6)
    assert mx.recall_at_k(q, 5) == 1.0
    assert mx.first_position(q) == 1
    assert mx.first_position([False, False, True]) == 3
    assert mx.first_position([False]) is None


def test_auroc_perfect_random_and_inverted() -> None:
    perfect = [((10 - i, 0.0), i < 3) for i in range(10)]
    inverted = [((i, 0.0), i < 3) for i in range(10)]
    ties = [((1, 0.0), i % 2 == 0) for i in range(10)]
    assert mx.auroc(perfect) == 1.0
    assert mx.auroc(inverted) == 0.0
    assert mx.auroc(ties) == 0.5
    assert mx.auroc([((1, 0.0), True)]) is None


def test_random_first_position_expectation() -> None:
    assert mx.random_first_position(30, 5) == pytest.approx(31 / 6)
    assert math.isclose(mx.random_first_position(30, 29), 31 / 30)


def test_priority_order_score_desc_then_coverage_asc_then_upload() -> None:
    cases = [
        {"score": 40, "coverage_ratio": 0.9, "upload_index": 0},
        {"score": 90, "coverage_ratio": 0.9, "upload_index": 1},
        {"score": 40, "coverage_ratio": 0.5, "upload_index": 2},
        {"score": 40, "coverage_ratio": 0.5, "upload_index": 1},
        {"score": None, "coverage_ratio": None, "upload_index": 5},
    ]
    assert mx.priority_order(cases) == [1, 3, 2, 0, 4]
