"""Fitting the calibrated score weights (scripts/fit_score_weights.py, _eval/score_fit.py,
_eval/score_data.py) on a tiny labelled fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app import score_model as sm
from app.core.config import Settings
from app.core.db import Database
from scripts import fit_score_weights as cli
from scripts._eval import score_data as sd
from scripts._eval import score_fit as sf
from tests.test_api_effectiveness import build_scenario
from tests.test_api_support import FakeClock, make_env


def mk_case(
    cid: str,
    kind: str,
    flags: list[tuple[str, str, str]],
    *,
    root_causes: int | None = None,
    coverage: float = 0.9,
) -> dict[str, Any]:
    """``flags``: ``(rule, severity, outcome)``; one finding per flag."""
    tampered = kind != "clean"
    fl = [
        {"id": f"{r}.{i}", "rule": r, "severity": s, "outcome": o}
        for i, (r, s, o) in enumerate(flags)
    ]
    fd = [
        {
            "group_key": f"rule:{r}:{i}",
            "severity": s,
            "rules": [r],
            "flag_ids": [f"{r}.{i}"],
            "hard_stop": False,
            "outcome": o,
        }
        for i, (r, s, o) in enumerate(flags)
    ]
    return {
        "id": cid,
        "kind": kind,
        "scenario": "loan",
        "tampered": tampered,
        "n_planted": root_causes or 0,
        "n_root_causes": root_causes if tampered else 0,
        "partial": False,
        "coverage_ratio": coverage,
        "legacy_score": None,
        "findings": fd,
        "flags": fl,
    }


def dataset(seed: int, slots: int, *, source: str = "synthetic") -> dict[str, Any]:
    """``slots`` companies, four forms each. ``x_directors`` high is always right, ``bs_dates``
    medium always wrong, ``inv_sequence`` low is wrong on a clean case once, and ``x_entity`` high
    fires once (rare)."""
    cases = []
    for s in range(slots):
        base = f"q{seed}-{s:02d}"
        cases.append(mk_case(f"{base}-clean", "clean", [("bs_dates", "medium", "dismissed")]))
        cases.append(
            mk_case(
                f"{base}-deck",
                "deck",
                [
                    ("x_directors", "high", "confirmed"),
                    ("bs_totals", "high", "confirmed"),
                    ("x_invoice_vs_receipts", "medium", "confirmed"),
                    ("bs_dates", "medium", "dismissed"),
                ],
                root_causes=5,
            )
        )
        cases.append(
            mk_case(
                f"{base}-forger",
                "forger",
                [("x_directors", "high", "confirmed"), ("bs_totals", "high", "confirmed")],
                root_causes=3,
            )
        )
        cases.append(
            mk_case(
                f"{base}-single", "single", [("x_directors", "high", "confirmed")], root_causes=1
            )
        )
    cases[0]["flags"].append(
        {"id": "x.9", "rule": "inv_sequence", "severity": "low", "outcome": "dismissed"}
    )
    cases[1]["flags"].append(
        {"id": "y.9", "rule": "x_entity", "severity": "high", "outcome": "confirmed"}
    )
    cases[2]["flags"].append(
        {"id": "i.9", "rule": "ai_provenance_hint", "severity": "info", "outcome": "info"}
    )
    return {
        "schema": sd.SCHEMA,
        "source": source,
        "seed": seed,
        "slots": slots,
        "render": "digital",
        "ruleset_version": "rs-test",
        "cases": cases,
    }


# ------------------------------------------------------------------------------- the fit


def test_case_outcomes_collapse_per_rule_and_skip_info_and_open() -> None:
    case = mk_case(
        "c",
        "deck",
        [("a", "high", "confirmed"), ("a", "high", "dismissed"), ("b", "low", "dismissed")],
    )
    case["flags"] += [
        {"id": "z", "rule": "c", "severity": "info", "outcome": "info"},
        {"id": "o", "rule": "d", "severity": "medium", "outcome": "open"},
    ]
    assert sf.case_outcomes(case) == {"a:high": "confirmed", "b:low": "dismissed"}
    assert sd.finding_outcome(["dismissed", "confirmed"]) == "confirmed"
    assert sd.finding_outcome(["dismissed", "dismissed"]) == "dismissed"
    assert sd.finding_outcome(["dismissed", "open"]) == "unknown"
    assert sd.finding_outcome([]) == "unknown"


def test_precision_ordering_shrinkage_and_unseen_rules() -> None:
    fitted = sf.fit_weights(
        [dataset(1, 10)], prior_strength=8, today=__import__("datetime").date(2026, 11, 3)
    )
    rules = fitted["rules"]
    good, wrong = rules["x_directors:high"], rules["bs_dates:medium"]
    assert good["p"] > sm.default_p("high") * 0.7  # earned its keep
    assert wrong["p"] < sm.default_p("medium") * 0.6  # always dismissed: pushed well below default
    assert wrong["confirmed"] == 0 and wrong["dismissed"] == 20
    # a rule seen once stays near its default (shrinkage), whatever its single outcome
    rare = rules["x_entity:high"]
    assert rare["confirmed"] + rare["dismissed"] == 1
    assert abs(rare["p"] - sm.default_p("high")) < 0.08
    # a rule never seen is not written: the app keeps its severity default
    assert "id_checksum:high" not in rules and not any(
        k.startswith("ai_provenance_hint") for k in rules
    )
    # every p respects the ceiling and the file's own rules
    assert all(0 < r["p"] <= sm.SINGLE_FINDING_CEILING for r in rules.values())


def test_more_evidence_moves_p_further_from_the_default() -> None:
    small = sf.fit_weights([dataset(1, 3)])["rules"]["bs_dates:medium"]["p"]
    large = sf.fit_weights([dataset(1, 30)])["rules"]["bs_dates:medium"]["p"]
    assert large < small < sm.default_p("medium")


def test_prevalence_and_prior_strength_change_the_fit() -> None:
    d = [dataset(1, 10)]
    low = sf.fit_weights(d, prevalence=0.02)["rules"]["x_directors:high"]["p"]
    high = sf.fit_weights(d, prevalence=0.5)["rules"]["x_directors:high"]["p"]
    assert low < high  # at a lower fraud share the same hits are worth less
    weak = sf.fit_weights(d, prior_strength=1)["rules"]["bs_dates:medium"]["p"]
    strong = sf.fit_weights(d, prior_strength=50)["rules"]["bs_dates:medium"]["p"]
    assert weak < strong  # a strong prior holds the rule near its default


def test_review_log_fit_is_plain_beta_without_reweighting_and_not_synthetic() -> None:
    cases = []
    for i in range(10):
        cases.append(mk_case(f"r{i}", "deck", [("x_directors", "high", "confirmed")]))
    for i in range(10, 20):
        cases.append(mk_case(f"r{i}", "deck", [("x_directors", "high", "dismissed")]))
    for c in cases:
        c["tampered"] = None  # a reviewer log has no clean/tampered label per case
    data = {
        "schema": sd.SCHEMA,
        "source": "review_log",
        "seed": None,
        "ruleset_version": "rs",
        "cases": cases,
    }
    fitted = sf.fit_weights([data])
    assert fitted["fitted_on_synthetic"] is False and fitted["source"]["kind"] == "review_log"
    assert fitted["method"]["prevalence"] is None
    row = fitted["rules"]["x_directors:high"]
    assert (row["confirmed"], row["dismissed"]) == (10, 10)
    prior = sm.default_p("high") / sm.SINGLE_FINDING_CEILING
    expected = (sf.DEFAULT_PRIOR_STRENGTH * prior + 10) / (sf.DEFAULT_PRIOR_STRENGTH + 20)
    assert row["precision"] == pytest.approx(expected, abs=1e-3)


def test_mixed_sources_are_refused() -> None:
    with pytest.raises(ValueError, match="mixed sources"):
        sf.fit_weights([dataset(1, 2), dataset(2, 2, source="review_log")])


def test_fitted_file_is_valid_for_the_app_and_deterministic() -> None:
    a = sf.fit_weights([dataset(1, 6)], today=__import__("datetime").date(2026, 11, 3))
    b = sf.fit_weights([dataset(1, 6)], today=__import__("datetime").date(2026, 11, 3))
    assert a == b
    lw = sm.parse_weights(json.dumps(a).encode(), origin="fixture")
    assert lw.weights.fitted_on_synthetic and lw.weights.source["n_cases"] == 24
    assert lw.weights.source["seeds"] == [1] and lw.weights.created.isoformat() == "2026-11-03"
    assert lw.weights.version == "2026-11-03-synthetic-1"
    assert a["method"]["ceiling"] == sm.SINGLE_FINDING_CEILING


# ------------------------------------------------------------------------- the evaluation


def test_evaluation_reports_all_models_and_saturation() -> None:
    d = dataset(2, 12)
    # make the legacy sum hit the cap in the deck form
    for c in d["cases"]:
        if c["kind"] == "deck":
            for i in range(4):
                c["flags"].append(
                    {"id": f"h{i}", "rule": f"rule{i}", "severity": "high", "outcome": "confirmed"}
                )
                c["findings"].append(
                    {
                        "group_key": f"rule:h{i}",
                        "severity": "high",
                        "rules": [f"rule{i}"],
                        "flag_ids": [f"h{i}"],
                        "hard_stop": True,
                        "outcome": "confirmed",
                    }
                )
    lw = sm.parse_weights(json.dumps(sf.fit_weights([dataset(1, 6)])).encode(), origin="fixture")
    ev = sf.evaluate(d["cases"], lw, share=0.1, reps=30)
    assert set(ev) == {"legacy", "noisy_default", "calibrated"}
    leg, cal = ev["legacy"]["metrics"], ev["calibrated"]["metrics"]
    assert leg["saturated_tampered"] == 12 and cal["saturated_tampered"] == 0
    assert 0 <= cal["auroc"] <= 1 and cal["first_fraud_position"] >= 1
    assert cal["top10_n"] == 1 and cal["slots"] == 12
    assert cal["distinct_scores_tampered"] >= leg["distinct_scores_tampered"]
    assert cal["tampered_in_fast_by_score"] == 0
    text = sf.metrics_table("fixture", ev)
    assert "AUROC" in text and "calibrated" in text


def test_calibration_table_buckets_cover_every_case() -> None:
    d = dataset(3, 5)
    scores = {c["id"]: sf.score_case(c, "legacy", None) for c in d["cases"]}
    rows = sf.calibration_table(d["cases"], scores, prevalence=0.1)
    assert sum(r["cases"] for r in rows) == len(d["cases"])
    assert sum(r["tampered"] for r in rows) == 15
    assert rows[0]["bucket"] == "0" and all(r["bucket"] for r in rows)
    shares = [r["share_tampered_at_prevalence"] for r in rows if r["cases"]]
    assert all(0 <= s <= 1 for s in shares)
    assert "@prev" in sf.calibration_text("t", rows, 0.1)


def test_spearman_and_ties() -> None:
    assert sf.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert sf.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert sf.spearman([1, 1, 1], [1, 2, 3]) is None


# --------------------------------------------------------------------------------- the CLI


def test_cli_fits_writes_a_loadable_file_and_evaluates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fit_p, held_p = tmp_path / "fit.json", tmp_path / "held.json"
    sd.save_dataset(fit_p, dataset(1, 8))
    sd.save_dataset(held_p, dataset(2, 8))
    out = tmp_path / "out" / "weights.json"
    rc = cli.run(
        [
            "--dataset",
            str(fit_p),
            "--eval-dataset",
            str(held_p),
            "--out",
            str(out),
            "--report-json",
            str(tmp_path / "report.json"),
            "--reps",
            "20",
            "--work",
            str(tmp_path / "w"),
        ]
    )
    assert rc == 0
    text = capsys.readouterr().out
    assert "FIT on synthetic" in text and "HELD-OUT held.json (digital)" in text
    assert "NOT a probability" in text and "legacy" in text and "calibrated" in text
    lw = sm.load_weights(out)
    assert lw.weights.fitted_on_synthetic and lw.weights.source["seeds"] == [1]
    report = json.loads((tmp_path / "report.json").read_text())
    assert "HELD-OUT held.json (digital)" in report["evaluations"]
    # the file the script wrote scores cases in the app
    s = Settings(
        _env_file=None,
        data_dir=tmp_path,
        db_path=":memory:",
        llm_backend="fake",
        score_model="calibrated",
        score_weights_path=out,
    )
    s.validate_startup()


def test_cli_refuses_to_evaluate_on_the_fit_seed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "fit.json"
    sd.save_dataset(p, dataset(1, 4))
    assert cli.run(["--dataset", str(p), "--eval-dataset", str(p), "--work", str(tmp_path)]) == 2
    assert "fit seed" in capsys.readouterr().err
    assert cli.run(["--dataset", str(p), "--eval-seed", "1", "--work", str(tmp_path)]) == 2
    assert not (tmp_path / "x").exists()


def test_cli_dataset_source_needs_a_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.run(["--source", "dataset", "--work", str(tmp_path)])


def test_wrong_schema_dataset_is_rejected(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"schema": "nope", "cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="not a gldb-score-outcomes/1"):
        sd.load_dataset(p)


# ------------------------------------------------------------------ real reviewer verdicts


def test_review_log_outcomes_match_what_effectiveness_reads(
    settings: Settings, db: Database
) -> None:
    env = make_env(settings, db, FakeClock())
    scenario = build_scenario(env)
    data = sd.outcomes_from_review_log(db)
    assert data["schema"] == sd.SCHEMA and data["source"] == "review_log"
    by_id = {c["id"]: c for c in data["cases"]}
    a = by_id[scenario["a"].id]
    outcome = {f["rule"]: f["outcome"] for f in a["flags"]}
    assert outcome["bs_running_balance"] == "confirmed" and outcome["bs_dates"] == "dismissed"
    assert a["tampered"] is True
    # case c has no decided flag: unlabelled, so it is left out of evaluations
    assert by_id[scenario["c"].id]["tampered"] is None
    # the same collapse the fit uses
    counts = sf.count_outcomes(data["cases"])
    assert counts["bs_running_balance:high"]["confirmed_t"] >= 2
    fitted = sf.fit_weights([data])
    assert fitted["source"]["kind"] == "review_log"
    # the case seeded with a retired rule: it is dropped from the file, which the app would refuse
    assert "retired_rule:low" in fitted["method"]["dropped_unregistered"]
    assert "retired_rule:low" not in fitted["rules"]
    sm.parse_weights(json.dumps(fitted).encode(), origin="review log")


def test_cli_fits_from_the_review_log_database(
    settings: Settings, db: Database, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make_env(settings, db, FakeClock())
    build_scenario(env)
    out = tmp_path / "w.json"
    rc = cli.run(
        [
            "--source",
            "review-log",
            "--db",
            settings.database_path,
            "--out",
            str(out),
            "--work",
            str(tmp_path),
        ]
    )
    assert rc == 0
    lw = sm.load_weights(out)
    assert lw.weights.fitted_on_synthetic is False and lw.weights.source["kind"] == "review_log"
    assert lw.weights.method["prevalence"] is None
    assert "reviewer agreement" in capsys.readouterr().out
    # and the app says so in the score explanation
    s = Settings(
        _env_file=None,
        data_dir=tmp_path,
        db_path=":memory:",
        llm_backend="fake",
        score_model="calibrated",
        score_weights_path=out,
    )
    assert "reviewer verdicts" in sm.model_note(s) and "synthetic" not in sm.model_note(s)


def test_synthetic_fit_never_lowers_a_high_rule_below_the_legacy_points() -> None:
    fitted = sf.fit_weights([dataset(1, 10)], prior_strength=8)
    highs = [r["p"] for k, r in fitted["rules"].items() if k.endswith(":high")]
    assert highs and min(highs) >= sm.default_p("high")
    assert fitted["method"]["high_floor"] == sm.default_p("high")
    # real verdicts are evidence: no floor, and a high rule reviewers always dismissed falls
    cases = [mk_case(f"r{i}", "deck", [("x_directors", "high", "dismissed")]) for i in range(30)]
    for c in cases:
        c["tampered"] = None
    log = {
        "schema": sd.SCHEMA,
        "source": "review_log",
        "seed": None,
        "ruleset_version": "rs",
        "cases": cases,
    }
    low = sf.fit_weights([log], prior_strength=8)
    assert (
        low["rules"]["x_directors:high"]["p"] < sm.default_p("high")
        and low["method"]["high_floor"] is None
    )
