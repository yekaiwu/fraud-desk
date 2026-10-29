"""Eval outputs: validation-report writer (idempotent, markers only), regression exit code, cost and
timed-test support, the oracle sentence on scan tables, and the 'no empty metric' rule."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts import eval as eval_cli
from scripts._eval import report as rp
from scripts._eval import suites_more as sm
from scripts._eval.common import ORACLE_SENTENCE
from scripts._eval.suites import Config

SKELETON = (
    "# Validation report\n\nStatus: NOT VALIDATED\n\nFixed text that must never change.\n\n"
    + "\n\n".join(
        f"## {i} Section {name}\n<!-- BEGIN eval:{name} -->\n_Not run yet._\n<!-- END eval:{name} -->"
        for i, name in enumerate(rp.MARKERS, start=1)
    )
    + "\n\n## 15 Sign-off\n\n| Role | Name | Date | Comment |\n|---|---|---|---|\n| Model owner | | | |\n"
)


def row(
    tid: str, render: str = "digital", n: int = 5, caught: int = 5, **kw: Any
) -> dict[str, Any]:
    lo, hi = (0.57, 1.0) if caught == n else (0.1, 0.8)
    base = {
        "tamper_id": tid,
        "edit": "an edit",
        "channel": "world",
        "render": render,
        "n": n,
        "errors": 0,
        "caught": caught,
        "recall": caught / n,
        "wilson_lo": lo,
        "wilson_hi": hi,
        "localisable": caught,
        "localised": caught,
        "missed_not_run": 0,
        "missed_ran": n - caught,
        "already_firing_on_base": 0,
        "extraction_mode": "text_layer" if render == "digital" else "oracle",
        "hard": False,
        "weak": False,
        "known_miss": False,
        "expected": ["bs_running_balance"],
        "status": "measured",
    }
    base.update(kw)
    return base


def acceptance_rows(fail: str | None = None) -> list[dict[str, Any]]:
    out = []
    for b in (
        "kyc-clean",
        "kyc-fraud",
        "kyc-forger",
        "aml-clean",
        "aml-fraud",
        "aml-forger",
        "loan-clean",
        "loan-fraud",
        "loan-forger",
    ):
        out.append(
            {
                "bundle_id": b,
                "pass": b != fail,
                "reasons": ["lane aml != loan"] if b == fail else [],
                "reproducible": True,
                "ok": True,
                "summary": {
                    "lane": "loan",
                    "band": "high",
                    "score": 70,
                    "coverage_ratio": 0.88,
                    "ruleset_version": "rs-test",
                    "flags": [{"rule": "x_directors", "severity": "high"}],
                },
                "effectiveness": {"times_fired": 3},
                "human_path": {"verdict": "refer_edd", "confirmed_high_flags": 2, "closed": True},
            }
        )
    return out


def fake_results(fail: str | None = None) -> dict[str, Any]:
    shares = []
    for s, k in ((0.1, 3), (0.2, 6), (0.3, 9), (0.5, 15)):
        stat = {"mean": 2.0, "min": 1.0, "max": 3.0, "n": 5}
        block = {
            m: dict(stat)
            for m in (
                "fraud",
                "top5_fraud",
                "top10_fraud",
                "P@5",
                "R@5",
                "lift@5",
                "auroc",
                "first_top_down",
                "first_upload",
                "random_first_position",
                "sim_minutes_top_down",
                "sim_minutes_upload",
            )
        }
        shares.append({"share": s, "k": k, "all": block, "detectable": copy.deepcopy(block)})
    groups = [
        {
            "group": g,
            "n_same": 30,
            "false_flags": 0,
            "false_flag_rate": 0.0,
            "false_flag_ci": [0.0, 0.11],
            "n_diff": 15,
            "wrong_matches": 3,
            "wrong_match_rate": 0.2,
            "wrong_match_ci": [0.07, 0.45],
            "matcher_false_flag_rate": 0.0,
            "matcher_wrong_match_rate": 0.2,
        }
        for g in (
            "western_order",
            "chinese_order",
            "malay_patronymic",
            "indian_patronymic",
            "mononym",
            "titles",
        )
    ]
    R: dict[str, Any] = {
        "generated_with": {
            "today": "2026-10-27",
            "seed": 4002,
            "ruleset_version": "rs-test",
            "app_version": "0.1.0",
            "extraction": "auto",
            "classifier": "rules",
            "llm_backend": "fake",
            "renders": ["digital", "scan_hq"],
            "n": 5,
            "n_degraded": 1,
            "degradation_policy": "compute budget",
            "clean_pool": "33",
            "ranking_slots": 30,
            "ranking_reps": 5,
            "fairness_mode": "full set",
            "quick": False,
        },
        "recall_tables": {
            "rows": [
                row("T01"),
                row("T01", "scan_hq", n=1, caught=1),
                row("T02b", hard=True, n=2, caught=1),
                row("T02c", known_miss=True, n=1, caught=0, expected=[]),
                row("T33", n=2, caught=0),
                {
                    "tamper_id": "T56b",
                    "edit": "x",
                    "channel": "world",
                    "render": "-",
                    "n": 0,
                    "caught": 0,
                    "status": "not applicable: generator gap",
                    "hard": False,
                    "weak": False,
                    "known_miss": False,
                    "expected": ["x_directors"],
                },
            ],
            "core_recall": {
                "digital": {
                    "n": 7,
                    "caught": 5,
                    "recall": 5 / 7,
                    "wilson": [0.36, 0.92],
                    "localised": 5,
                    "localisable": 5,
                }
            },
            "skipped": [],
        },
        "false_alarms": {
            "by_render": {
                "digital": {
                    "n_clean": 33,
                    "flags_ge_medium": 2,
                    "per_10": 0.6,
                    "low_flags": 1,
                    "low_per_10": 0.3,
                    "bundles_with_false_alarm": 1,
                    "allowed_by_quirk": 0,
                    "fast_lane": 20,
                    "rows": [
                        {
                            "bundle": "loan-clean",
                            "scenario": "loan",
                            "false_alarms": [["bs_totals", "high"]],
                            "n_ge_medium": 1,
                            "low": 0,
                            "allowed_by_quirk": 0,
                            "score": 30,
                            "lane": "loan",
                            "coverage": 0.9,
                            "extraction_mode": "text_layer",
                            "fast_lane": False,
                        }
                    ],
                },
                "scan_hq": {
                    "n_clean": 3,
                    "flags_ge_medium": 9,
                    "per_10": 30.0,
                    "low_flags": 0,
                    "low_per_10": 0.0,
                    "bundles_with_false_alarm": 3,
                    "allowed_by_quirk": 0,
                    "fast_lane": 0,
                    "rows": [],
                },
            },
            "hard_quirk": {
                "digital": {
                    "n_clean": 3,
                    "flags_ge_medium": 1,
                    "per_10": 3.3,
                    "low_flags": 0,
                    "low_per_10": 0.0,
                    "bundles_with_false_alarm": 1,
                    "allowed_by_quirk": 1,
                    "fast_lane": 0,
                    "rows": [
                        {
                            "bundle": "loan-clean-c90",
                            "false_alarms": [],
                            "allowed_by_quirk": 1,
                            "score": 5,
                            "lane": "fast",
                        }
                    ],
                }
            },
            "errors": [],
        },
        "extraction": {
            "statements": {
                "text_layer": {
                    "docs": 5,
                    "truth_rows": 100,
                    "extracted": 100,
                    "aligned": 99,
                    "exact": 98,
                    "chain_valid": 5,
                    "chain_match": 5,
                }
            },
            "fields": {"invoice.total": [10, 10]},
            "tables": {"invoice.lines": [30, 30]},
            "bundles": [],
        },
        "never_fix": {"n": 4, "kept_broken": 4, "rows": []},
        "classification": {
            "rules": {
                "digital": {
                    "C1": {"correct": 90, "n": 100, "macro_f1": 0.9},
                    "C3": {"correct": 5, "n": 5},
                    "C4": {"correct": 8, "n": 10},
                    "C5": {"correct": 90, "n": 100},
                    "low_conf_rate": 0.02,
                    "pages": 100,
                    "c1_errors": [["other", "invoice"]],
                }
            },
            "gate": {"status": "not run: missing"},
        },
        "canaries": {
            "rows": [
                {
                    "host": "statement",
                    "placement": p,
                    "passed": True,
                    "reasons": [],
                    "extra_page_label": None,
                }
                for p in ("P1", "P2", "P3", "P4")
            ],
            "passed": 4,
            "total": 4,
            "classifier": "rules",
        },
        "cost": {
            "bundles": 9,
            "seconds": {"mean": 5.0, "p50": 4.0, "p95": 9.0},
            "usd_total": 0.0,
            "usd_mean": 0.0,
            "pages": 100,
            "stage_seconds": {"ingest": {"mean": 1.0, "p95": 2.0, "usd_per_page": 0.0}},
            "no_model_cost_measured": True,
            "cost_per_correct_bundle": 0.1,
            "n_correct": 10,
            "assumptions": {
                "minutes_per_false_flag": 2.0,
                "analyst_rate_usd_per_hour": 60.0,
                "placeholder": True,
            },
            "timed_test": {"mode": "not run", "reason": "no timed-test.csv"},
            "experiments": {"model_sweep": {"status": "not_run: no api key"}},
        },
        "ranking": {
            "sweep": {"shares": shares, "n_slots": 30, "reps": 5},
            "movement": ["a line per share"] * 4,
            "caveats": ["The generator is ours: upper bound."],
            "cases": [],
            "errors": [],
            "n_slots": 30,
            "sim_model": {
                "base_minutes": 4.0,
                "per_flag_minutes": 0.75,
                "source": "assumed (placeholder until the timed test is run)",
            },
            "queue_id": "q4002",
        },
        "fairness": {
            "groups": groups,
            "disparity": 0.0,
            "criteria_proposed": {
                "false_flag_rate": 0.02,
                "wrong_match_rate": 0.05,
                "disparity": 0.05,
            },
            "criteria_met": {"false_flag_rate": True, "wrong_match_rate": False, "disparity": True},
            "overall": "FAIL",
            "note": "x",
            "n_pairs": 270,
            "errors": [],
            "mode": "full set",
        },
        "burden": {
            "clean": {"flags_per_bundle": {"n": 33, "mean": 0.5, "median": 0, "p95": 2}},
            "deck_fraud": {"flags_per_bundle": {"n": 3, "mean": 8.0, "median": 7, "p95": 11}},
        },
        "acceptance": {
            "digital": acceptance_rows(fail),
            "scan_hq": [
                {
                    "bundle_id": "loan-fraud",
                    "render": "scan_hq",
                    "pass": False,
                    "reasons": ["x"],
                    "summary": {"lane": "loan", "band": "high", "score": 100},
                }
            ],
            "reproducible": 9,
            "reproducible_total": 9,
        },
        "gaps": ["rule not implemented: noise_mismatch"],
        "limits": {"measured": ["T33 on digital: caught 0/2"]},
        "changelog_row": {
            "ruleset_version": "rs-test",
            "summary": "core recall 5/7",
            "deltas": "no baseline yet",
        },
    }
    return R


# ------------------------------------------------------------------------- the report


def test_every_section_renders_with_no_empty_metric() -> None:
    R = fake_results()
    for name, fn in rp.SECTION_FNS.items():
        text = fn(R)
        assert text.strip(), name
        assert "nan" not in text.lower().split() and "None/" not in text, name


def test_write_validation_report_is_idempotent_and_touches_only_the_markers(tmp_path: Path) -> None:
    path = tmp_path / "validation-report.md"
    path.write_text(SKELETON, encoding="utf-8")
    R = fake_results()
    assert rp.write_validation_report(path, R) is True
    first = path.read_text(encoding="utf-8")
    assert rp.write_validation_report(path, R) is False  # running it twice changes nothing
    assert path.read_text(encoding="utf-8") == first

    def outside(text: str) -> str:
        return re.sub(r"<!-- BEGIN eval:\w+ -->.*?<!-- END eval:\w+ -->", "", text, flags=re.S)

    strip_status = lambda t: re.sub(r"^Status: .*$", "Status: X", t, flags=re.M)  # noqa: E731
    assert strip_status(outside(first)) == strip_status(outside(SKELETON))
    assert re.search(r"^Status: NOT VALIDATED. Evaluated for ruleset rs-test", first, re.M)
    for name in rp.MARKERS:
        assert f"<!-- BEGIN eval:{name} -->\n_Not run yet._" not in first


def test_changelog_appends_one_row_per_ruleset_version(tmp_path: Path) -> None:
    path = tmp_path / "v.md"
    path.write_text(SKELETON, encoding="utf-8")
    R = fake_results()
    rp.write_validation_report(path, R)
    R2 = copy.deepcopy(R)
    R2["generated_with"]["ruleset_version"] = "rs-next"
    R2["changelog_row"] = {
        "ruleset_version": "rs-next",
        "summary": "after triage",
        "deltas": "core recall up",
    }
    rp.write_validation_report(path, R2)
    block = rp.existing_marker(path.read_text(encoding="utf-8"), "changelog")
    assert block.count("| rs-test |") == 1 and block.count("| rs-next |") == 1
    rp.write_validation_report(path, R2)  # same version again: replaced, not duplicated
    assert (
        rp.existing_marker(path.read_text(encoding="utf-8"), "changelog").count("| rs-next |") == 1
    )


def test_scan_tables_carry_the_oracle_sentence_and_the_upper_bound_framing() -> None:
    R = fake_results()
    assert ORACLE_SENTENCE in rp.section_recall(R)
    assert ORACLE_SENTENCE in rp.section_false_alarms(R)
    assert ORACLE_SENTENCE in rp.section_acceptance(R)
    assert ORACLE_SENTENCE in rp.section_extraction(R)
    assert "upper bound" in rp.section_limits(R)


def test_hard_case_and_known_miss_are_stated_either_way() -> None:
    text = rp.section_recall(fake_results())
    assert "T02b on digital: MISSED in 1/2" not in text and "CAUGHT in 1/2" in text
    assert (
        "T02c on digital: MISSED in 0/1 instances" in text
        and "known miss: content checks cannot see it" in text
    )
    assert "rule not implemented" in rp.section_limits(fake_results()) or "gap" in text


def test_cost_section_says_no_real_model_cost_measured_and_flags_placeholders() -> None:
    text = rp.section_cost(fake_results())
    assert (
        "No real model cost measured" in text
        and "PLACEHOLDER" in text
        and "not_run: no api key" in text
    )


def test_ranking_section_has_a_row_per_share_and_the_caveats() -> None:
    text = rp.section_ranking(fake_results())
    for share in ("10%", "20%", "30%", "50%"):
        assert f"| {share} |" in text
    assert "Caveats" in text and "assumed" in text


# ---------------------------------------------------------------------------- regression


def args(**kw: Any):  # noqa: ANN401, ANN202
    return (
        eval_cli.parse_args([])
        if not kw
        else eval_cli.parse_args(
            sum(([f"--{k.replace('_', '-')}", str(v)] for k, v in kw.items()), [])
        )
    )


def test_regress_detects_each_kind_of_drop() -> None:
    R = fake_results()
    base = eval_cli.baseline_of(R)
    assert eval_cli.regress(R, base, args()) == []
    doctored = copy.deepcopy(base)
    doctored["recall_by_tamper_digital"]["T01"] = 1.0
    R2 = copy.deepcopy(R)
    R2["recall_tables"]["rows"][0] = row("T01", n=5, caught=3)  # 1.00 -> 0.60
    assert any("T01" in r for r in eval_cli.regress(R2, doctored, args()))
    d2 = copy.deepcopy(base)
    d2["false_alarms_per_10"]["digital"] = -1.0  # now 0.6: rose by 1.6 > 1.0
    assert any("false alarms" in r for r in eval_cli.regress(R, d2, args()))
    d3 = copy.deepcopy(base)
    d3["core_recall_digital"] = 0.95
    assert any("core recall" in r for r in eval_cli.regress(R, d3, args()))
    R3 = fake_results(fail="loan-fraud")
    assert any("loan-fraud" in r for r in eval_cli.regress(R3, base, args()))


def test_main_exits_2_on_a_doctored_baseline_and_3_on_a_failed_acceptance_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "docs" / "eval-results"
    out.mkdir(parents=True)
    (out.parent / "validation-report.md").write_text(SKELETON, encoding="utf-8")
    good = fake_results()
    monkeypatch.setattr(eval_cli, "run", lambda cfg, suite, only: copy.deepcopy(good))
    doctored = eval_cli.baseline_of(good)
    doctored["core_recall_digital"] = 1.0
    doctored["recall_by_tamper_digital"]["T01"] = 1.0
    bl = tmp_path / "baseline.json"
    bl.write_text(json.dumps(doctored), encoding="utf-8")
    common = ["--samples", str(tmp_path / "s"), "--out", str(out)]
    assert eval_cli.main([*common, "--regress", str(bl)]) == 2
    assert "REGRESSION" in capsys.readouterr().out
    fine = tmp_path / "fine.json"
    fine.write_text(json.dumps(eval_cli.baseline_of(good)), encoding="utf-8")
    assert (
        eval_cli.main(
            [*common, "--regress", str(fine), "--write-baseline", "--write-validation-report"]
        )
        == 0
    )
    assert (
        (out / "baseline.json").exists()
        and (out / "latest.json").exists()
        and (out / "acceptance.md").exists()
    )
    for name in (
        "recall.csv",
        "false_alarms.csv",
        "extraction.csv",
        "classification.csv",
        "cost.csv",
        "ranking.csv",
        "fairness.csv",
        "name-slice.json",
    ):
        assert (out / name).exists(), name
    assert "Evaluated for ruleset rs-test" in (out.parent / "validation-report.md").read_text(
        encoding="utf-8"
    )
    bad = fake_results(fail="aml-fraud")
    monkeypatch.setattr(eval_cli, "run", lambda cfg, suite, only: copy.deepcopy(bad))
    assert eval_cli.main([*common, "--suite", "acceptance"]) == 3


def test_latest_json_has_the_documented_keys() -> None:
    R = fake_results()
    latest = eval_cli.latest_json(R)
    for key in (
        "schema_version",
        "generated_with",
        "recall",
        "core_recall",
        "false_alarms",
        "extraction",
        "classification",
        "canaries",
        "cost",
        "ranking",
        "fairness",
        "burden",
        "reproducibility",
        "acceptance",
        "gaps",
    ):
        assert key in latest
    assert latest["canaries"] == {"passed": 4, "total": 4} and latest["reproducibility"] == {
        "passed": 9,
        "total": 9,
    }
    assert latest["acceptance"][0]["bundle_id"] == "kyc-clean"


# ----------------------------------------------------------------------- cost and timed test


def test_cost_tables_p95_formula_and_no_zero_reported_as_a_result(tmp_path: Path) -> None:
    cfg = Config(samples=tmp_path, out=tmp_path)
    summaries = [
        {
            "ok": True,
            "render": "digital",
            "seconds": float(s),
            "usd": "0",
            "pages": 10,
            "stages": {"ingest": {"seconds": float(s) / 2, "usd": "0"}},
        }
        for s in range(1, 21)
    ]
    res = sm.cost_tables(cfg, summaries, {"false_alarm_flags": 3, "n_correct": 6})
    assert res["seconds"]["p95"] == 19.0 and res["seconds"]["p50"] == 10.0
    # model usd (0) + 3 false flags x 2.0 min / 60 x 60 USD per hour = 6.0, over 6 correct bundles
    assert res["cost_per_correct_bundle"] == pytest.approx(1.0)
    assert res["no_model_cost_measured"] is True and res["assumptions"]["placeholder"] is True
    assert all(v["status"] == "not_run: no api key" for v in res["experiments"].values())
    assert "usd_per_page" in res["stage_seconds"]["ingest"]
    text = rp.section_cost({**fake_results(), "cost": res})
    assert "No real model cost measured" in text and "usd per page | \n" not in text


def test_timed_test_mode_is_reported_with_and_without_the_file(tmp_path: Path) -> None:
    cfg = Config(samples=tmp_path, out=tmp_path)
    assert sm.timed_test(cfg)["mode"] == "not run"
    (tmp_path / "timed-test.csv").write_text(
        "tester,order,queue_id,rep,seconds_to_first_fraud,cases_opened,flags_read,false_flags_dismissed,seconds_per_flag,notes\n",
        encoding="utf-8",
    )
    t = sm.timed_test(cfg)
    assert t["mode"] == "not run" and "no rows" in t["reason"]
    (tmp_path / "timed-test.csv").write_text(
        "tester,order,queue_id,rep,seconds_to_first_fraud,cases_opened,flags_read,false_flags_dismissed,seconds_per_flag,notes\na,top,q1,0,300,5,10,2,60,\nb,top,q1,0,500,7,12,3,90,\n",
        encoding="utf-8",
    )
    t = sm.timed_test(cfg)
    assert (
        t["mode"] == "measured"
        and t["median_seconds_to_first_fraud"] == 400
        and t["minutes_per_flag"] == 1.25
    )
    text = rp.section_burden(fake_results())
    assert "not run" in text


def test_committed_timed_test_file_has_the_header_and_no_data() -> None:
    path = Path(__file__).resolve().parents[2] / "docs" / "eval-results" / "timed-test.csv"
    assert (
        path.read_text(encoding="utf-8").strip()
        == "tester,order,queue_id,rep,seconds_to_first_fraud,cases_opened,flags_read,false_flags_dismissed,seconds_per_flag,notes"
    )


def test_demo_script_quotes_only_eval_numbers_with_sources_and_avoids_banned_words() -> None:
    from scripts._eval import demo

    R = fake_results()
    for r in R["acceptance"]["digital"]:
        r["human_path"] = {"verdict": "refer_edd", "confirmed_high_flags": 2, "closed": True}
        r["summary"]["flags"] = [
            {"rule": "x_directors", "severity": "high"},
            {"rule": "bs_dates", "severity": "low"},
        ]
    R["recall_tables"]["rows"].append(row("T33", n=2, caught=1))
    text = demo.demo_script_md(R)
    assert text.count("## ") >= 8 and "Appendix" in text
    for needle in (
        "`[core_recall.digital]`",
        "`[false_alarms.digital]`",
        "ranking.shares[share=0.2]",
        "`[recall[T02b,digital]]`",
        "`[recall[T02c,digital]]`",
        "upper bound",
        "assumed model",
        "mock mode",
    ):
        assert needle in text, needle
    assert not re.search(r"\b(authentic|verified|compliant|complies|genuine)\b", text, re.I)
    assert (
        "`x_directors` (high)" in text
        and "bs_dates" not in text.split("## 1.")[1].split("## 2.")[0]
    )


def test_scan_pdf_diagnostic_sentence_is_read_from_the_data() -> None:
    """The diagnostic must not claim the image-forensics rules fire unless they did."""
    quiet = {"flags_ge_medium": 0, "rows": [{"false_alarms": []}]}
    chain = {"flags_ge_medium": 2, "rows": [{"false_alarms": [["bs_totals", "high"]]}]}
    forensic = {"flags_ge_medium": 2, "rows": [{"false_alarms": [["ela_region", "medium"]]}]}
    assert "both silent" in rp._diag_tail(quiet)
    assert "image-forensics" not in rp._diag_tail(chain) and "bs_totals" in rp._diag_tail(chain)
    assert "ela_region" in rp._diag_tail(forensic) and "do not" in rp._diag_tail(forensic)
