"""Traceability re-score (plan 0.4 / 0.6): status rules on stubbed eval rows, the eight 'received, not
checked' documents, and the harness runner (T16c needs two cases in one database)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts._eval import traceability_map as tm
from scripts._eval.common import ensure_bundle, load_manifest
from scripts._eval.runner import run_job
from scripts._eval.tamper_defs import TAMPERS

REGISTERED = {r for it in tm.ITEMS for r in it["rules"]}


def rrow(tid: str, caught: int, n: int = 5, render: str = "digital") -> dict[str, Any]:
    return {"tamper_id": tid, "render": render, "n": n, "caught": caught, "status": "measured"}


def rec(
    tid: str,
    caught: bool,
    *,
    evidence: bool = True,
    loc: bool = True,
    render: str = "digital",
    ran: bool = True,
) -> dict[str, Any]:
    exp = TAMPERS[tid].expected_any_of
    return {
        "tamper_id": tid,
        "render": render,
        "ok": True,
        "caught": caught,
        "caught_evidence": evidence and caught,
        "caught_with_location": loc,
        "localisable": True,
        "expected": exp,
        "coverage": {r: {"status": "ran" if ran else "skipped"} for r in exp},
    }


ACC = {
    "digital": [
        {"bundle_id": b, "pass": True}
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
        )
    ]
}
O_PASS = {f"O{i}": {"passed": True, "reasons": []} for i in range(1, 9)}


def score(rows, recs, acc=ACC, o_rows=O_PASS, registered=REGISTERED, **extras):  # noqa: ANN001, ANN003, ANN201
    extras.setdefault("effectiveness_ok", True)
    extras.setdefault("ranking_ran", True)
    return {
        r["name"] + "|" + r["section"]: r
        for r in tm.score_items(rows, recs, acc, o_rows, registered, extras)
    }


def find(res: dict[str, Any], name: str, section: str | None = None) -> dict[str, Any]:
    hits = [
        v
        for k, v in res.items()
        if k.startswith(name + "|") and (section is None or v["section"] == section)
    ]
    assert hits, name
    return hits[0]


def test_every_plan_item_has_a_row() -> None:
    names = [(i["section"], i["name"]) for i in tm.ITEMS]
    assert len(names) == len(set(names)) == 51
    sections = {s: sum(1 for x, _ in names if x == s) for s, _ in names}
    assert sections == {
        "Process themes": 6,
        "Fraud scenarios": 7,
        "Detection techniques": 8,
        "Cross-document": 6,
        "Family A: KYC": 8,
        "Family B: AML": 8,
        "Family C: Loan": 8,
    }
    assert {i["orow"] for i in tm.ITEMS if i["orow"]} == {f"O{n}" for n in range(1, 9)}
    assert all(t in TAMPERS for i in tm.ITEMS for t in i["tampers"])


def test_full_marks_need_a_catch_recall_evidence_and_a_region() -> None:
    ids = ["T01", "T02a", "T03"]
    rows = [rrow(t, 5) for t in ids]
    recs = [rec(t, True) for t in ids for _ in range(5)]
    item = find(score(rows, recs), "Edited bank statement balances")
    assert item["measured"] == tm.FULL and not item["downgraded"]


def test_low_recall_downgrades_to_half_and_says_why() -> None:
    ids = ["T01", "T02a", "T03"]
    rows = [rrow("T01", 5), rrow("T02a", 2), rrow("T03", 1)]
    recs = [rec("T01", True), rec("T02a", True), rec("T03", True)]
    item = find(score(rows, recs), "Edited bank statement balances")
    assert item["planned"] == tm.FULL and item["measured"] == tm.HALF and item["downgraded"]
    assert any("below 0.8" in x for x in item["limits"]), ids


def test_missing_evidence_or_region_stops_a_full_mark() -> None:
    rows = [rrow(t, 5) for t in ("T01", "T02a", "T03")]
    no_ev = [rec(t, True, evidence=False) for t in ("T01", "T02a", "T03")]
    assert find(score(rows, no_ev), "Edited bank statement balances")["measured"] == tm.HALF
    no_loc = [rec(t, True, loc=False) for t in ("T01", "T02a", "T03")]
    item = find(score(rows, no_loc), "Edited bank statement balances")
    assert item["measured"] == tm.HALF and any("no region" in x for x in item["limits"])


def test_never_caught_is_a_circle_with_the_reason() -> None:
    rows = [rrow("T33", 0, n=2)]
    item = find(score(rows, [rec("T33", False)]), "AI-generated image detection")
    assert (
        item["measured"] == tm.NONE and item["downgraded"] and "never caught" in item["limits"][0]
    )


def test_unregistered_rule_is_reported_as_not_implemented() -> None:
    reg = REGISTERED - {"ai_provenance_hint"}
    item = find(
        score([rrow("T33", 5)], [rec("T33", True)], registered=reg), "AI-generated image detection"
    )
    assert (
        item["measured"] == tm.NONE
        and "rule not implemented: ai_provenance_hint" in item["limits"][0]
    )


def test_measured_never_exceeds_planned() -> None:
    ids = ["T50a", "T50b", "T51", "T52", "T41", "T40a", "T40b"]
    rows = [rrow(t, 5) for t in ids]
    recs = [rec(t, True) for t in ids]
    item = find(score(rows, recs), "Modified passports and IDs")
    assert item["planned"] == tm.HALF and item["measured"] == tm.HALF


def test_scan_misses_are_stated_as_a_limit() -> None:
    rows = [rrow(t, 5) for t in ("T01", "T02a", "T03")] + [rrow("T01", 0, n=1, render="scan_hq")]
    recs = [rec(t, True) for t in ("T01", "T02a", "T03")]
    item = find(score(rows, recs), "Edited bank statement balances")
    assert any("scan misses" in x for x in item["limits"])


def test_weak_hard_and_known_miss_ids_stay_out_of_item_recall() -> None:
    rows = [rrow("T01", 5), rrow("T02a", 5), rrow("T03", 5), rrow("T02b", 0, n=5)]
    recs = [rec("T01", True), rec("T02a", True), rec("T03", True)]
    item = find(score(rows, recs), "Edited bank statement balances")
    assert item["measured"] == tm.FULL  # T02b (hard) is not an input to the item's recall


def test_theme_five_needs_all_nine_acceptance_rows_and_effectiveness() -> None:
    item = find(score([], []), "5 Manual review, override, escalation")
    assert item["measured"] == tm.FULL
    broken = {
        "digital": [dict(r, **{"pass": r["bundle_id"] != "aml-fraud"}) for r in ACC["digital"]]
    }
    item = find(score([], [], acc=broken), "5 Manual review, override, escalation")
    assert item["measured"] == tm.HALF and item["downgraded"]
    item = find(score([], [], effectiveness_ok=False), "5 Manual review, override, escalation")
    assert item["measured"] == tm.HALF


def test_circle_items_stay_circles_with_their_reason_and_failed_o_rows_are_shown() -> None:
    res = score([], [])
    board = find(res, "Board resolution")
    assert board["planned"] == tm.NONE and board["measured"] == tm.NONE and not board["downgraded"]
    failed = dict(O_PASS, O1={"passed": False, "reasons": ["classified invoice, not other"]})
    item = find(score([], [], o_rows=failed), "Board resolution")
    assert any("assertions failed" in x for x in item["limits"]) and "FAILED" in item["evidence"][0]


def test_traceability_markdown_shows_downgrades_and_gaps() -> None:
    rows = [rrow("T33", 0, n=2)]
    res = tm.score_items(
        rows,
        [rec("T33", False)],
        ACC,
        O_PASS,
        REGISTERED,
        {"effectiveness_ok": True, "ranking_ran": True},
    )
    md = tm.traceability_md(res, ["rule not implemented: noise_mismatch"], "header line")
    assert "downgraded from ◐" in md and "## Gaps (plan 0.6)" in md and "noise_mismatch" in md
    assert md.count("| item | planned | measured |") == 7


# ----------------------------------------------------------------- the eight O documents


def test_the_eight_o_documents_are_plain_pdfs_with_no_forensic_oddities(tmp_path: Path) -> None:
    import pymupdf

    paths = tm.make_o_documents(tmp_path)
    assert sorted(paths) == [f"O{i}" for i in range(1, 9)]
    for p in paths.values():
        with pymupdf.open(str(p)) as doc:
            text = doc[0].get_text()
            assert (
                len(doc) == 1 and "FICTITIOUS" in text and len(text) > 100
            )  # body text was placed


def test_o_row_assertions_on_stub_runs() -> None:
    blank = {
        "doc_key": None,
        "doc_type": "other",
        "status": "received_not_checked",
    }  # the base's own blank page
    inv = {"doc_key": "invoice:1", "doc_type": "invoice", "status": "ok"}
    flag_a = {"rule": "bs_dates", "severity": "low", "doc_keys": ["bank_statement:2025-07"]}
    unclassified = {"rule": "doc_unclassified", "severity": "info", "doc_keys": []}
    base = {"detail": {"documents": [inv, blank]}, "flags": [flag_a, unclassified]}
    new_doc = {"doc_key": None, "doc_type": "other", "status": "received_not_checked"}
    ok = {
        "detail": {"documents": [inv, blank, new_doc]},
        "flags": [flag_a, unclassified, unclassified],
    }
    assert tm.o_row_result(base, ok)[
        "passed"
    ]  # a second doc_unclassified with the same key still counts
    wrong = {
        "detail": {"documents": [inv, blank, dict(new_doc, doc_type="invoice")]},
        "flags": ok["flags"],
    }
    assert any(
        "classified invoice, not other" in r for r in tm.o_row_result(base, wrong)["reasons"]
    )
    extra = {
        "detail": ok["detail"],
        "flags": [*ok["flags"], {"rule": "inv_dates", "severity": "medium", "doc_keys": ["x"]}],
    }
    assert not tm.o_row_result(base, extra)["passed"]
    lost = {"detail": ok["detail"], "flags": [unclassified, unclassified]}
    assert any("changed or lost" in r for r in tm.o_row_result(base, lost)["reasons"])
    unreadable = {
        "detail": {"documents": [inv, blank, dict(new_doc, status="failed")]},
        "flags": [
            flag_a,
            unclassified,
            {"rule": "doc_unreadable", "severity": "info", "doc_keys": []},
        ],
    }
    reasons = tm.o_row_result(base, unreadable)["reasons"]
    assert any("not received_not_checked" in r for r in reasons) and any(
        "doc_unreadable" in r for r in reasons
    )


# --------------------------------------------------------------------------- the runner


@pytest.fixture(scope="module")
def aml_samples(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("eval-runner")
    ensure_bundle(root, "aml", "clean", 0)
    return root


def test_runner_summarises_flags_with_document_keys_and_is_deterministic(aml_samples: Path) -> None:
    man = aml_samples / "bundles" / "aml-clean" / "manifest.json"
    a = run_job({"manifest": str(man), "render": "digital", "today": "2026-10-27", "detail": True})
    b = run_job({"manifest": str(man), "render": "digital", "today": "2026-10-27"})
    assert a["ok"] and a["status"] == "open" and a["extraction_mode"] == "text_layer"
    assert a["flags_digest"] == b["flags_digest"] and a["ruleset_version"].startswith("rs-")
    assert a["detail"]["pages"] and all(p["label"] for p in a["detail"]["pages"])
    assert all(
        f["doc_keys"] or f["severity"] == "info" or f["rule"].startswith("x_") for f in a["flags"]
    )
    assert a["usd"] == "0" or float(a["usd"]) == 0.0


def test_t16c_needs_two_cases_in_one_database(aml_samples: Path, tmp_path: Path) -> None:
    from scripts.make_tampered import apply_tamper

    bdir = aml_samples / "bundles" / "aml-clean"
    base = load_manifest(bdir / "manifest.json")
    dest = tmp_path / "T16c-aml-clean-i0"
    apply_tamper(base, "T16c", seed=4002, instance=0, out_dir=dest, base_dir=bdir)
    man = dest / "manifest.json"
    alone = run_job({"manifest": str(man), "render": "digital", "today": "2026-10-27"})
    after = run_job(
        {
            "manifest": str(man),
            "render": "digital",
            "today": "2026-10-27",
            "prior": str(bdir / "manifest.json"),
        }
    )
    assert alone["ok"] and after["ok"]
    assert "inv_duplicate" not in {f["rule"] for f in alone["flags"]}
    assert "inv_duplicate" in {f["rule"] for f in after["flags"]}
