"""Deck acceptance row logic (docs 4.10) on stubbed case summaries, plus fairness and canary scoring."""

from __future__ import annotations

from scripts._eval import acceptance as acc
from scripts._eval import canaries as can
from scripts._eval import fairness as fair

EXPECTED_FRAUD = {
    "lane": "loan",
    "band_min": "medium",
    "verdict": "reject_or_escalate",
    "must_fire": [
        {"rule": "x_revenue_vs_credits", "min_severity": "medium"},
        {
            "rule": ["fs_balance", "fs_subtotals", "fs_cash_tie", "fs_comparatives"],
            "min_severity": "high",
        },
    ],
    "must_not_fire": ["x_cash_at_year_end"],
}
EXPECTED_CLEAN = {
    "lane_any_of": ["fast", "loan"],
    "band_max": "low",
    "must_fire": [],
    "max_flags_ge_medium": 0,
}


def summary(
    flags: list[tuple[str, str]], lane: str = "loan", band: str = "high", partial: bool = False
) -> dict:
    return {
        "flags": [{"rule": r, "severity": s} for r, s in flags],
        "lane": lane,
        "band": band,
        "partial": partial,
        "partial_reasons": [],
    }


def test_a_row_that_meets_every_expectation_passes() -> None:
    s = summary([("x_revenue_vs_credits", "medium"), ("fs_subtotals", "high")])
    assert acc.check_row(s, EXPECTED_FRAUD) == []


def test_a_missing_rule_fails() -> None:
    s = summary([("fs_subtotals", "high")])
    reasons = acc.check_row(s, EXPECTED_FRAUD)
    assert reasons == ["missing x_revenue_vs_credits (>= medium)"]


def test_an_any_of_group_needs_the_minimum_severity() -> None:
    s = summary([("x_revenue_vs_credits", "medium"), ("fs_subtotals", "medium")])
    assert any("any of fs_balance/fs_subtotals" in r for r in acc.check_row(s, EXPECTED_FRAUD))


def test_a_forbidden_rule_fails() -> None:
    s = summary(
        [
            ("x_revenue_vs_credits", "medium"),
            ("fs_subtotals", "high"),
            ("x_cash_at_year_end", "medium"),
        ]
    )
    assert acc.check_row(s, EXPECTED_FRAUD) == ["forbidden rule fired: x_cash_at_year_end"]


def test_a_wrong_lane_fails() -> None:
    s = summary([("x_revenue_vs_credits", "medium"), ("fs_subtotals", "high")], lane="aml")
    assert acc.check_row(s, EXPECTED_FRAUD) == ["lane aml != loan"]


def test_band_bounds_and_clean_lane_set() -> None:
    low = summary([("x_revenue_vs_credits", "medium"), ("fs_subtotals", "high")], band="low")
    assert any("below medium" in r for r in acc.check_row(low, EXPECTED_FRAUD))
    assert acc.check_row(summary([], lane="fast", band="low"), EXPECTED_CLEAN) == []
    assert acc.check_row(summary([], lane="edd", band="low"), EXPECTED_CLEAN) == [
        "lane edd not in ['fast', 'loan']"
    ]
    assert any(
        "above low" in r
        for r in acc.check_row(summary([], lane="fast", band="medium"), EXPECTED_CLEAN)
    )


def test_a_clean_bundle_with_a_medium_flag_fails_the_flag_budget() -> None:
    s = summary([("inv_dates", "medium")], lane="fast", band="low")
    assert any("1 flags >= medium (max 0)" in r for r in acc.check_row(s, EXPECTED_CLEAN))


def test_a_partial_case_fails() -> None:
    s = summary([], lane="fast", band="low", partial=True)
    assert any(r.startswith("case partial") for r in acc.check_row(s, EXPECTED_CLEAN))


def test_expected_verdict_defaults_to_request_documents_when_unknown() -> None:
    assert acc.verdict_for({"verdict": "escalate_aml"}) == "escalate_aml"
    assert acc.verdict_for({}) == "request_documents"
    assert len(acc.BUNDLES) == 9


def test_acceptance_markdown_has_a_row_per_bundle_and_marks_scan_rows_non_gating() -> None:
    row = {
        "bundle_id": "loan-fraud",
        "pass": False,
        "reasons": ["lane aml != loan"],
        "summary": {"lane": "aml", "band": "high", "score": 70, "coverage_ratio": 0.88},
    }
    md = acc.acceptance_table_md([row], [dict(row, pass_=True)])
    assert "loan-fraud | FAIL" in md and "reported, not gating" in md and "lane aml != loan" in md


# ---------------------------------------------------------------------------- fairness


def pairs() -> list[dict]:
    out = []
    for g in fair.GROUPS:
        for i in range(4):
            out.append(
                {
                    "pair_id": f"{g}-s{i}",
                    "group": g,
                    "same_person": True,
                    "name_a": "A B",
                    "name_b": "A B",
                }
            )
        for i in range(2):
            out.append(
                {
                    "pair_id": f"{g}-d{i}",
                    "group": g,
                    "same_person": False,
                    "name_a": "A B",
                    "name_b": "A C",
                }
            )
    return out


def test_fairness_table_always_has_six_rows_even_with_no_data() -> None:
    res = fair.score_groups([], {})
    assert [g["group"] for g in res["groups"]] == fair.GROUPS and len(res["groups"]) == 6
    res = fair.score_groups(pairs(), {})
    assert len(res["groups"]) == 6


def test_fairness_rates_disparity_and_proposed_criteria() -> None:
    ps = pairs()
    fired = {
        p["pair_id"]: (not p["same_person"]) for p in ps
    }  # perfect: flags only different people
    fired["malay_patronymic-s0"] = True  # one false flag in one group
    fired["mononym-d0"] = False  # one wrong match in another
    res = fair.score_groups(ps, fired, matcher={p["pair_id"]: "same" for p in ps})
    by = {g["group"]: g for g in res["groups"]}
    assert (
        by["malay_patronymic"]["false_flags"] == 1
        and by["malay_patronymic"]["false_flag_rate"] == 0.25
    )
    assert by["mononym"]["wrong_matches"] == 1 and by["mononym"]["wrong_match_rate"] == 0.5
    assert res["disparity"] == 0.25 and res["overall"] == "FAIL"
    assert res["criteria_met"] == {
        "false_flag_rate": False,
        "wrong_match_rate": False,
        "disparity": False,
    }
    assert (
        by["western_order"]["matcher_wrong_match_rate"] == 1.0
    )  # the matcher says "same" for every pair
    lo, hi = by["malay_patronymic"]["false_flag_ci"]
    assert 0 <= lo < 0.25 < hi <= 1


def test_fairness_perfect_run_passes_and_quick_subset_keeps_all_groups() -> None:
    ps = pairs()
    fired = {p["pair_id"]: (not p["same_person"]) for p in ps}
    assert fair.score_groups(ps, fired)["overall"] == "PASS"
    sub = fair.select_pairs(ps, 2)
    assert {p["group"] for p in sub} == set(fair.GROUPS)
    assert fair.select_pairs(ps, None) == ps


def test_name_rule_detection_ignores_info_flags() -> None:
    assert fair.fired_name_rule({"flags": [{"rule": "x_directors", "severity": "high"}]})
    assert not fair.fired_name_rule(
        {
            "flags": [
                {"rule": "x_directors", "severity": "info"},
                {"rule": "bs_dates", "severity": "high"},
            ]
        }
    )


# ----------------------------------------------------------------------------- canaries


def run(
    labels: list[str], flags: list[tuple[str, str]] = (), score: int = 10, cov: float = 0.8
) -> dict:  # noqa: B006
    return {
        "detail": {"pages": [{"label": lab} for lab in labels]},
        "flags": [{"rule": r, "severity": s, "doc_keys": ["d"]} for r, s in flags],
        "score": score,
        "coverage_ratio": cov,
    }


def test_canary_pass_and_each_failure_mode() -> None:
    control = run(["invoice"], [("inv_total", "medium")])
    assert can.canary_row(
        control, run(["invoice"], [("inv_total", "medium"), ("pdf_text_vs_render", "medium")]), "P3"
    )["passed"]
    assert not can.canary_row(control, run(["other"], [("inv_total", "medium")]), "P1")[
        "passed"
    ]  # label changed
    lost = can.canary_row(control, run(["invoice"], []), "P1")
    assert not lost["passed"] and "flags lost" in lost["reasons"][0]
    lower = can.canary_row(control, run(["invoice"], [("inv_total", "medium")], score=5), "P4")
    assert not lower["passed"] and "score fell" in lower["reasons"][0]


def test_canary_p2_extra_page_rules() -> None:
    control = run(["invoice"], [], cov=0.8)
    good = run(["invoice", "other"], [("doc_unclassified", "info")], cov=0.8)
    row = can.canary_row(control, good, "P2")
    assert row["passed"] and row["extra_page_label"] == "other"
    no_flag = run(["invoice", "other"], [], cov=0.8)
    assert (
        "without a doc_unclassified info flag"
        in can.canary_row(control, no_flag, "P2")["reasons"][0]
    )
    hijacked = run(["invoice", "bank_statement"], [("doc_unclassified", "info")])
    r = can.canary_row(control, hijacked, "P2")
    assert not r["passed"] and "extra page labelled 'bank_statement'" in r["reasons"][0]
    higher = run(["invoice", "boilerplate"], [("doc_unclassified", "info")], cov=0.95)
    assert "coverage rose" in " ".join(can.canary_row(control, higher, "P2")["reasons"])
