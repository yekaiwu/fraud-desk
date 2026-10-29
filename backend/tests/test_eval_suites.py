"""Aggregation in the suites: core recall pooling, coverage vs recall (M14), honest-case reporting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts._eval import suites as su
from scripts._eval.suites import score_instance


def rec(
    tid: str,
    render: str = "digital",
    caught: bool = True,
    ran: bool = True,
    base_fired: bool = False,
    **kw: Any,
) -> dict[str, Any]:
    from scripts._eval.tamper_defs import TAMPERS

    td = TAMPERS[tid]
    out = {
        "tamper_id": tid,
        "render": render,
        "ok": True,
        "caught": caught,
        "localisable": True,
        "localised": caught,
        "extraction_mode": "text_layer",
        "expected": td.expected_any_of,
        "coverage": {r: {"status": "ran" if ran else "skipped"} for r in td.expected_any_of},
        "base_fired_expected": td.expected_any_of if base_fired else [],
    }
    out.update(kw)
    return out


def test_core_recall_pools_only_core_tampers_and_reports_the_rest_apart() -> None:
    recs = (
        [rec("T01") for _ in range(4)]
        + [rec("T01", caught=False)]
        + [rec("T08", caught=False)] * 2
        + [rec("T02b", caught=True)] * 2
        + [rec("T02c", caught=False)]
    )
    tables = su.recall_tables(recs, [], ["T01", "T08", "T02b", "T02c"], registered=set())
    core = tables["core_recall"]["digital"]
    assert (core["n"], core["caught"]) == (5, 4) and core["recall"] == 0.8
    by = {r["tamper_id"]: r for r in tables["rows"]}
    assert by["T08"]["weak"] and by["T02b"]["hard"] and by["T02c"]["known_miss"]
    assert (
        by["T02c"]["caught"] == 0 and by["T02c"]["n"] == 1
    )  # a known miss is reported, not an error


def test_missed_cases_split_into_not_run_and_ran_and_missed() -> None:
    recs = [
        rec("T01", caught=False, ran=False),
        rec("T01", caught=False, ran=True),
        rec("T01", caught=False, ran=True, base_fired=True),
    ]
    row = su.recall_tables(recs, [], ["T01"], registered=set())["rows"][0]
    assert (row["missed_not_run"], row["missed_ran"], row["already_firing_on_base"]) == (1, 2, 1)


def test_a_tamper_that_could_not_run_is_listed_with_its_reason_not_as_an_error() -> None:
    skipped = [
        {
            "tamper_id": "T56b",
            "scenario": "kyc",
            "instance": 0,
            "status": "not applicable: generator gap",
        }
    ]
    tables = su.recall_tables([], skipped, ["T56b"], registered=set())
    row = tables["rows"][0]
    assert row["n"] == 0 and row["status"] == "not applicable: generator gap"
    gap = su.recall_tables([], [], ["T33"], registered={"x"})["rows"][0]
    assert gap["status"] == "rule not implemented: gap"


def test_score_instance_credits_only_new_flags_and_localises(tmp_path: Path) -> None:
    from scripts._eval.tamper_core import Ctx  # noqa: F401  (imported to fail early on a broken core)

    d = tmp_path / "T01-loan-clean-i0"
    d.mkdir()
    sha = "a" * 64
    manifest = {
        "files": [{"path": "digital/bank_statement_2025-07.pdf", "sha256": sha}],
        "tampers": [
            {
                "tamper_id": "T01",
                "edit": "an edit",
                "expected_any_of": ["bs_running_balance"],
                "edited": {},
                "target": {
                    "doc_key": "bank_statement:2025-07",
                    "table": "transactions",
                    "row": 4,
                    "field": None,
                    "box": None,
                },
            }
        ],
    }
    (d / "manifest.json").write_text(json.dumps(manifest))
    (d / "ground_truth.json").write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "key": "bank_statement:2025-07",
                        "tables": {
                            "transactions": {
                                "rows": [
                                    {
                                        "index": 4,
                                        "cells": {
                                            "date": {"value": "2025-07-03"},
                                            "debit": {"value": None},
                                            "credit": {"value": "100.00"},
                                            "balance": {"value": "500.00"},
                                        },
                                    }
                                ]
                            }
                        },
                    }
                ]
            }
        )
    )
    flag = {
        "rule": "bs_running_balance",
        "severity": "high",
        "doc_keys": ["bank_statement:2025-07"],
        "table": "transactions",
        "row": 4,
        "field": None,
        "row_sig": None,
        "regions": [],
        "has_evidence": True,
    }
    a = {
        "ok": True,
        "flags": [flag],
        "extraction_mode": "text_layer",
        "score": 30,
        "lane": "loan",
        "band": "medium",
        "check_runs": {"bs_running_balance": {"status": "ran"}},
    }
    b = {"ok": True, "flags": [], "score": 0}
    out = score_instance({"dir": str(d), "scenario": "loan", "instance": 0}, "digital", a, b)
    assert (
        out["caught"]
        and out["localised"] is True
        and out["caught_evidence"]
        and out["caught_with_location"]
    )
    already = score_instance(
        {"dir": str(d), "scenario": "loan", "instance": 0},
        "digital",
        a,
        {"ok": True, "flags": [flag], "score": 30},
    )
    assert not already["caught"] and already["base_fired_expected"] == ["bs_running_balance"]
    failed = score_instance(
        {"dir": str(d), "scenario": "loan", "instance": 0},
        "digital",
        {"ok": False, "error": "boom"},
        b,
    )
    assert failed["ok"] is False and failed["error"] == "boom"


def clean_bundle(tmp_path: Path, name: str, quirks: list[dict[str, Any]]) -> Path:
    d = tmp_path / name
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"quirks": quirks}))
    return d


def test_false_alarms_per_ten_with_quirk_allowance_and_hard_quirks_apart(tmp_path: Path) -> None:
    quirk_dir = clean_bundle(
        tmp_path, "loan-clean-c01", [{"id": "Q12", "may_trigger": ["doc_unclassified"]}]
    )
    plain = clean_bundle(tmp_path, "loan-clean", [])
    hard = clean_bundle(
        tmp_path, "loan-clean-c90", [{"id": "H1", "may_trigger": ["x_invoice_vs_receipts"]}]
    )

    def res(flags: list[tuple[str, str]]) -> dict[str, Any]:
        return {
            "ok": True,
            "score": 0,
            "lane": "fast",
            "coverage_ratio": 0.9,
            "extraction_mode": "text_layer",
            "flags": [{"rule": r, "severity": s} for r, s in flags],
        }

    clean = {
        "meta": [
            {
                "bundle": "loan-clean",
                "scenario": "loan",
                "render": "digital",
                "kind": "clean",
                "dir": str(plain),
            },
            {
                "bundle": "loan-clean-c01",
                "scenario": "loan",
                "render": "digital",
                "kind": "clean",
                "dir": str(quirk_dir),
            },
            {
                "bundle": "loan-clean-c90",
                "scenario": "loan",
                "render": "digital",
                "kind": "hard_quirk",
                "dir": str(hard),
            },
        ],
        "results": [
            res([("bs_totals", "high"), ("bs_dates", "low")]),
            res([("doc_unclassified", "medium")]),
            res([("x_invoice_vs_receipts", "medium")]),
        ],
    }
    table = su.false_alarm_table(clean)
    digital = table["by_render"]["digital"]
    assert digital["n_clean"] == 2
    assert (
        digital["flags_ge_medium"] == 1 and digital["per_10"] == 5.0 and digital["low_flags"] == 1
    )
    assert digital["allowed_by_quirk"] == 1 and digital["bundles_with_false_alarm"] == 1
    hq = table["hard_quirk"]["digital"]
    assert (
        hq["n_clean"] == 1 and hq["flags_ge_medium"] == 0 and hq["allowed_by_quirk"] == 1
    )  # reported apart, not counted


def test_instances_respect_the_compute_budget_and_image_tampers_use_scans_only(
    tmp_path: Path,
) -> None:
    cfg = su.Config(
        samples=tmp_path, out=tmp_path, n=5, renders=["digital", "scan_hq", "scan_lq", "photo"]
    )
    t01 = su._instances(cfg, "T01")
    assert (
        sum(1 for *_x, rs in t01 if "digital" in rs) == 15
        and sum(1 for *_x, rs in t01 if "scan_hq" in rs) == 3
    )
    assert all("scan_lq" in rs for *_x, rs in t01 if "scan_hq" in rs)  # T01 is in both subsets
    t40 = su._instances(cfg, "T40a")
    assert all("digital" not in rs for *_x, rs in t40) and {sc for sc, *_ in t40} == {"kyc", "loan"}
    quick = su.Config(samples=tmp_path, out=tmp_path, n=1, renders=["digital"])
    assert all(rs == ["digital"] for *_x, rs in su._instances(quick, "T01"))
    assert su._instances(quick, "T40a") == []
