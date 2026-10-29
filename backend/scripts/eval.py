"""Evaluation harness CLI (docs/test-data-and-eval.md sections 3.1 and 4): recall by tamper type, false
alarms, localisation, extraction, classification, canaries, cost, prioritisation, fairness, deck
acceptance and the traceability re-score. Fully offline: no network, no API key (FakeLLMClient; the
generator's ground truth stands in for OCR on scans). Fictitious data. Results are an upper bound.

    uv run python backend/scripts/eval.py --suite all --write-validation-report
    uv run python backend/scripts/eval.py --suite acceptance
    uv run python backend/scripts/eval.py --quick --suite recall
    uv run python backend/scripts/eval.py --regress docs/eval-results/baseline.json

Exit codes: 0 ok; 1 harness error; 2 regression against the baseline; 3 a gating acceptance row failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts._eval import demo as demo_mod  # noqa: E402
from scripts._eval import report as rp  # noqa: E402
from scripts._eval import suites as su  # noqa: E402
from scripts._eval import suites_more as sm  # noqa: E402
from scripts._eval import traceability_map as tm  # noqa: E402
from scripts._eval.common import DEFAULT_SEED, HEADER, read_json, write_json_file  # noqa: E402
from scripts._eval.tamper_defs import TAMPERS, registered_rules, unregistered_rules  # noqa: E402
from scripts._gen.schema import FIXED_TODAY  # noqa: E402

SUITES = [
    "all",
    "acceptance",
    "recall",
    "false_alarms",
    "extraction",
    "classification",
    "canaries",
    "cost",
    "ranking",
    "fairness",
    "reproducibility",
    "traceability",
]
RESULTS = "results.json"
REGRESSION = {"recall_drop": 0.20, "false_alarm_rise": 1.0, "core_recall_drop": 0.10}


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GLDB eval harness (offline).")
    p.add_argument("--samples", default="samples")
    p.add_argument("--out", default="docs/eval-results")
    p.add_argument("--suite", default="all", choices=SUITES)
    p.add_argument(
        "--render",
        default="digital,scan_hq,scan_lq,photo",
        help="comma list of digital,scan_hq,scan_lq,photo",
    )
    p.add_argument("--n-per-tamper", type=int, default=5)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--today", default=FIXED_TODAY.isoformat())
    p.add_argument("--extraction", default="auto", choices=["auto", "text_layer", "oracle", "llm"])
    p.add_argument("--classifier", default="rules", choices=["rules", "local", "llm", "cascade"])
    p.add_argument(
        "--quick",
        action="store_true",
        help="n-per-tamper 1, digital only, one ranking repetition, 10 fairness pairs per group",
    )
    p.add_argument("--regress", metavar="BASELINE.json")
    p.add_argument("--write-baseline", action="store_true")
    p.add_argument("--write-validation-report", action="store_true")
    p.add_argument(
        "--tamper", nargs="+", metavar="ID", help="restrict the recall suite to these tamper ids"
    )
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument(
        "--full-degradation",
        action="store_true",
        help="every degradable tamper on every requested render, --n-per-tamper instances (hours of CPU)",
    )
    p.add_argument(
        "--n-degraded",
        type=int,
        default=1,
        help="degraded instances per scenario and tamper (default 1)",
    )
    p.add_argument("--regress-recall-drop", type=float, default=REGRESSION["recall_drop"])
    p.add_argument("--regress-false-alarm-rise", type=float, default=REGRESSION["false_alarm_rise"])
    p.add_argument("--regress-core-recall-drop", type=float, default=REGRESSION["core_recall_drop"])
    return p.parse_args(argv)


def make_config(a: argparse.Namespace) -> su.Config:
    renders = [r.strip() for r in a.render.split(",") if r.strip()]
    quick = bool(a.quick)
    return su.Config(
        samples=Path(a.samples),
        out=Path(a.out),
        seed=a.seed,
        today=date.fromisoformat(a.today),
        n=1 if quick else a.n_per_tamper,
        renders=["digital"] if quick else renders,
        quick=quick,
        workers=a.workers,
        extraction=a.extraction,
        classifier=a.classifier,
        use_cache=not a.no_cache,
        full_degradation=a.full_degradation,
        n_degraded=a.n_degraded,
        ranking_reps=1 if quick else 5,
        fairness_per_group=10 if quick else None,
        log=su.log_to(),
    )


# ------------------------------------------------------------------------------- results


def load_results(out: Path) -> dict[str, Any]:
    p = out / RESULTS
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_results(out: Path, R: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / RESULTS).write_text(json.dumps(R, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _tuples_to_lists(o: Any) -> Any:
    return json.loads(json.dumps(o, default=str))


# ---------------------------------------------------------------------------------- run


def run(cfg: su.Config, suite: str, only_tampers: list[str] | None) -> dict[str, Any]:
    R = load_results(cfg.out)
    want = (
        {suite}
        if suite != "all"
        else {
            "acceptance",
            "recall",
            "false_alarms",
            "extraction",
            "classification",
            "canaries",
            "cost",
            "ranking",
            "fairness",
            "reproducibility",
            "traceability",
        }
    )
    if "reproducibility" in want:
        want.add("acceptance")
    if "traceability" in want:
        want |= {"recall", "acceptance"}
    if want & {"cost", "extraction", "classification", "false_alarms"}:
        want.add("false_alarms")
    if "cost" in want:
        want |= {"acceptance", "recall"}
    if "extraction" in want:
        want.add("recall")
    log = cfg.log
    registered = registered_rules()
    log(HEADER)
    bases = None
    clean = None
    need_bases = want & {"recall", "false_alarms", "extraction", "classification", "cost"}
    if need_bases:
        log("building the clean pool")
        bases = su.prepare_bases(cfg)
    if "false_alarms" in want and bases is not None:
        log("analysing the clean pool")
        clean = su.clean_runs(cfg, bases)
        R["clean_runs"] = _tuples_to_lists(clean)
        R["false_alarms"] = _tuples_to_lists(su.false_alarm_table(clean))
        R["extraction"] = _tuples_to_lists(sm.extraction_tables(clean))
        R["classification"] = _tuples_to_lists(
            {**sm.classification_tables(clean), "gate": sm.gate_tables(cfg.out)}
        )
    if "recall" in want and bases is not None:
        ids = only_tampers or list(TAMPERS)
        log(f"building tampered cases for {len(ids)} tamper id(s)")
        insts, skipped = su.build_tampered(cfg, ids, bases)
        log(f"built {len(insts)} instances; analysing")
        recs = su.recall_runs(cfg, insts)
        tables = su.recall_tables(recs, skipped, ids, registered)
        su.write_case_summaries(cfg.out, recs)
        R["recall_tables"] = _tuples_to_lists(tables)
        R["recall_recs"] = _tuples_to_lists(
            [{k: v for k, v in r.items() if k != "detail"} for r in recs]
        )
        R["never_fix"] = sm.never_fix_chain(cfg, recs)
    if "acceptance" in want:
        log("deck acceptance")
        R["acceptance"] = _tuples_to_lists(sm.acceptance_suite(cfg))
    if "canaries" in want:
        log("injection canaries")
        R["canaries"] = _tuples_to_lists(sm.canary_suite(cfg))
    if "fairness" in want:
        log("fairness slice")
        R["fairness"] = _tuples_to_lists(sm.fairness_suite(cfg))
    if "ranking" in want:
        log("ranking")
        R["ranking"] = _tuples_to_lists(sm.ranking_suite(cfg))
    if "cost" in want and clean is not None and "acceptance" in R and "recall_recs" in R:
        digital = [
            r
            for m, r in zip(clean["meta"], clean["results"], strict=True)
            if r.get("ok") and m["render"] == "digital"
        ]
        fa = R["false_alarms"]["by_render"].get("digital", {})
        clean_ok = sum(1 for r in fa.get("rows", []) if r["n_ge_medium"] == 0)
        caught = sum(1 for r in R["recall_recs"] if r.get("caught") and r["render"] == "digital")
        acc_ok = sum(
            1
            for r in R["acceptance"]["digital"]
            if r["pass"] and not r["bundle_id"].endswith("-clean")
        )
        summaries = digital + [r["summary"] for r in R["acceptance"]["digital"] if r.get("ok")]
        R["cost"] = _tuples_to_lists(
            sm.cost_tables(
                cfg,
                summaries,
                {
                    "false_alarm_flags": fa.get("flags_ge_medium", 0),
                    "n_correct": clean_ok + caught + acc_ok,
                },
            )
        )
        R["burden"] = _tuples_to_lists(
            sm.burden_tables({"meta": clean["meta"], "results": clean["results"]}, R["acceptance"])
        )
    if "traceability" in want and "recall_tables" in R and "acceptance" in R:
        log("traceability")
        R["traceability"] = _tuples_to_lists(run_traceability(cfg, R, registered))
    R["generated_with"] = generated_with(cfg, R)
    R["gaps"] = gaps_list(R, registered)
    R["limits"] = {"measured": measured_limits(R)}
    return R


def generated_with(cfg: su.Config, R: dict[str, Any]) -> dict[str, Any]:
    from app.core.config import APP_VERSION

    versions = {
        r.get("ruleset_version")
        for r in (R.get("clean_runs", {}).get("results") or [])
        if r.get("ok") and r.get("ruleset_version")
    }
    versions |= {
        (r.get("summary") or {}).get("ruleset_version")
        for r in (R.get("acceptance", {}).get("digital") or [])
        if r.get("ok")
    }
    versions.discard(None)
    ruleset = (
        sorted(versions)[0]
        if versions
        else (R.get("generated_with", {}).get("ruleset_version") or "unknown")
    )
    pol = "scan_hq for a representative subset of tampers, scan_lq and photo for a smaller subset, plus every image-channel tamper; one instance per scenario (compute budget)"
    if cfg.full_degradation:
        pol = "every degradable tamper on every requested render"
    return {
        "today": cfg.today.isoformat(),
        "seed": cfg.seed,
        "ruleset_version": ruleset,
        "app_version": APP_VERSION,
        "extraction": {
            "digital": "text_layer",
            "scan_hq": "oracle",
            "scan_lq": "oracle",
            "photo": "oracle",
        }
        if cfg.extraction == "auto"
        else cfg.extraction,
        "classifier": cfg.classifier,
        "llm_backend": "fake",
        "renders": cfg.renders,
        "n": cfg.n,
        "n_degraded": cfg.n_degraded if not cfg.full_degradation else cfg.n,
        "degradation_policy": pol,
        "clean_pool": f"{su.POOL} per scenario plus the canonical company (33 bundles) and 3 hard-quirk bundles",
        "ranking_slots": 6 if cfg.quick else cfg.ranking_slots,
        "ranking_reps": cfg.ranking_reps,
        "fairness_mode": "quick subset" if cfg.fairness_per_group else "full set",
        "quick": cfg.quick,
    }


def run_traceability(cfg: su.Config, R: dict[str, Any], registered: set[str]) -> dict[str, Any]:
    bases = su.prepare_bases(cfg)
    o_dir = cfg.samples / "received_not_checked"
    docs = tm.make_o_documents(o_dir)
    base_man = bases[("loan", 0)] / "manifest.json"
    jobs = [su.job(cfg, base_man, "digital", detail=True)]
    for _oid, p in sorted(docs.items()):
        jobs.append(su.job(cfg, base_man, "digital", detail=True, extra_files=[str(p)]))
    res = su.run_cached(cfg, jobs, label="received-not-checked documents")
    base = res[0]
    o_rows = {}
    for (oid, _p), r in zip(sorted(docs.items()), res[1:], strict=True):
        o_rows[oid] = (
            tm.o_row_result(base, r)
            if r.get("ok") and base.get("ok")
            else {"passed": False, "reasons": [f"harness error: {r.get('error')}"]}
        )
    eff_ok = all(
        not any(("effectiveness" in x or "pack" in x) for x in r["reasons"])
        for r in R["acceptance"]["digital"]
        if r.get("effectiveness") is not None
    )
    extras = {"effectiveness_ok": eff_ok, "ranking_ran": "ranking" in R}
    results = tm.score_items(
        R["recall_tables"]["rows"], R["recall_recs"], R["acceptance"], o_rows, registered, extras
    )
    return {"items": results, "o_rows": o_rows}


def gaps_list(R: dict[str, Any], registered: set[str]) -> list[str]:
    gaps: list[str] = []
    for t in TAMPERS.values():
        miss = unregistered_rules(t, registered) if registered else []
        if miss:
            gaps.append(f"rule not implemented: {', '.join(miss)} (expected by {t.id})")
    for s in (R.get("recall_tables") or {}).get("skipped", []):
        if "not built" in s["status"]:
            gaps.append(f"{s['tamper_id']}: {s['status']}")
    for oid, row in sorted(((R.get("traceability") or {}).get("o_rows") or {}).items()):
        if not row["passed"]:
            gaps.append(f"row {oid} failed: {'; '.join(row['reasons'])}")
    seen = set()
    gaps = [g for g in gaps if not (g in seen or seen.add(g))]
    gaps += [
        "plan 0.4 circle items (board resolution, proof of address, business licences, tax returns, wealth evidence, RFI supporting documents, collateral, trade documents) are meant to be classified as other and listed as received, not checked; rows O1 to O8 test it",
        "no layout-fingerprint rule (template comparison relies on the issuer label and the producer table)",
        "no dedicated reader-disagreement rule (OCR versus visual mismatch is pdf_text_vs_render plus alt_raw)",
        "ratio benchmarking against peers (industry profile and financial performance) is not built",
        "C2 packet boundary detection (stretch) is not run; `local` and `llm` classifiers and the real-model extraction modes are not run (no model, no API key)",
        "real model cost is not measured (fake client); cost experiments say `not_run: no api key`",
    ]
    return gaps


def measured_limits(R: dict[str, Any]) -> list[str]:
    out: list[str] = []
    rt = R.get("recall_tables")
    if rt:
        for r in rt["rows"]:
            if r.get("status") == "measured" and r["n"] and r["caught"] < r["n"]:
                tag = (
                    "known miss, as expected"
                    if r["known_miss"]
                    else "weak"
                    if r["weak"]
                    else "hard case"
                    if r["hard"]
                    else "core"
                )
                note = f"{r['tamper_id']} on {r['render']}: caught {r['caught']}/{r['n']} ({tag}); misses: {r['missed_ran']} ran and missed, {r['missed_not_run']} not run"
                if r["already_firing_on_base"]:
                    note += f"; {r['already_firing_on_base']} had the expected rule already firing on the untampered base, so no catch is credited"
                out.append(note)
    fa = R.get("false_alarms")
    if fa:
        worst = max(
            (e["per_10"] for e in fa["by_render"].values() if e["per_10"] is not None), default=0
        )
        if worst:
            out.append(
                "False alarms on clean bundles: "
                + "; ".join(
                    f"{render} {e['flags_ge_medium']} over {e['n_clean']} bundles ({e['per_10']:.1f} per 10)"
                    for render, e in sorted(fa["by_render"].items())
                    if e["flags_ge_medium"]
                )
                + " (rules per render in section 4)"
            )
    a = R.get("acceptance")
    if a:
        for r in a["digital"]:
            if not r["pass"]:
                out.append(f"Acceptance FAIL {r['bundle_id']}: {'; '.join(r['reasons'])}")
    c = R.get("canaries")
    if c:
        for r in c["rows"]:
            if not r["passed"]:
                out.append(f"Canary FAIL {r['host']} {r['placement']}: {'; '.join(r['reasons'])}")
    f = R.get("fairness")
    if f and f["overall"] != "PASS":
        out.append(f"Fairness slice: {f['overall']} against our proposed criteria")
    return out


# --------------------------------------------------------------------------- baseline/regress


def baseline_of(R: dict[str, Any]) -> dict[str, Any]:
    recall = {}
    for r in R["recall_tables"]["rows"]:
        if r.get("status") == "measured" and r["render"] == "digital" and r["n"]:
            recall[r["tamper_id"]] = r["caught"] / r["n"]
    core = R["recall_tables"]["core_recall"].get("digital", {})
    cost = R.get("cost", {})
    p5 = None
    for s in (R.get("ranking") or {}).get("sweep", {}).get("shares", []):
        if abs(s["share"] - 0.2) < 1e-9:
            p5 = s["all"]["P@5"]["mean"]
    return {
        "schema_version": "1",
        "ruleset_version": R["generated_with"]["ruleset_version"],
        "recall_by_tamper_digital": recall,
        "core_recall_digital": core.get("recall"),
        "false_alarms_per_10": {k: v["per_10"] for k, v in R["false_alarms"]["by_render"].items()},
        "acceptance": {r["bundle_id"]: bool(r["pass"]) for r in R["acceptance"]["digital"]},
        "seconds": cost.get("seconds"),
        "usd_mean": cost.get("usd_mean"),
        "stage_seconds": {k: v["mean"] for k, v in cost.get("stage_seconds", {}).items()},
        "P@5_at_f0.2": p5,
        "note": "first full run; later runs report ratios to these values",
    }


def regress(R: dict[str, Any], base: dict[str, Any], a: argparse.Namespace) -> list[str]:
    """Reasons the run regresses against ``base`` (empty = no regression)."""
    out: list[str] = []
    now = baseline_of(R)
    for tid, old in base.get("recall_by_tamper_digital", {}).items():
        cur = now["recall_by_tamper_digital"].get(tid)
        if cur is not None and old - cur > a.regress_recall_drop + 1e-12:
            out.append(f"digital recall of {tid} fell from {old:.2f} to {cur:.2f}")
    old_fa = base.get("false_alarms_per_10", {}).get("digital")
    cur_fa = now["false_alarms_per_10"].get("digital")
    if (
        old_fa is not None
        and cur_fa is not None
        and cur_fa - old_fa > a.regress_false_alarm_rise + 1e-12
    ):
        out.append(f"false alarms per 10 rose from {old_fa:.2f} to {cur_fa:.2f}")
    oc, cc = base.get("core_recall_digital"), now["core_recall_digital"]
    if oc is not None and cc is not None and oc - cc > a.regress_core_recall_drop + 1e-12:
        out.append(f"core recall fell from {oc:.2f} to {cc:.2f}")
    for b, passed in base.get("acceptance", {}).items():
        if passed and not now["acceptance"].get(b, False):
            out.append(f"acceptance row {b} passed in the baseline and now fails")
    return out


def changelog_row(R: dict[str, Any], base: dict[str, Any] | None) -> dict[str, str]:
    core = R["recall_tables"]["core_recall"].get("digital", {})
    fa = R["false_alarms"]["by_render"].get("digital", {})
    acc = R["acceptance"]["digital"]
    summary = f"core recall digital {core.get('caught')}/{core.get('n')}; false alarms per 10 clean bundles {fa.get('per_10'):.2f} ({fa.get('n_clean')} bundles); deck acceptance {sum(1 for r in acc if r['pass'])}/{len(acc)}; canaries {R['canaries']['passed']}/{R['canaries']['total']}"
    deltas = "no baseline yet"
    if base and base.get("ruleset_version") == R["generated_with"]["ruleset_version"]:
        deltas = "baseline recorded for this ruleset version (later runs report deltas against it)"
    elif base:
        b = base.get("core_recall_digital")
        deltas = (
            f"core recall {b:.2f} to {core.get('recall'):.2f}; false alarms per 10 {base.get('false_alarms_per_10', {}).get('digital')} to {fa.get('per_10'):.2f}"
            if b is not None and core.get("recall") is not None
            else "baseline lacks comparable fields"
        )
    return {
        "ruleset_version": R["generated_with"]["ruleset_version"],
        "summary": summary,
        "deltas": deltas,
    }


# ------------------------------------------------------------------------------- printing


def print_summary(R: dict[str, Any]) -> None:
    g = R["generated_with"]
    print(f"\n== eval summary (ruleset {g['ruleset_version']}, today {g['today']}) ==")
    print(HEADER)
    rt = R.get("recall_tables")
    if rt:
        for render, c in sorted(rt["core_recall"].items()):
            print(f"core recall {render}: {c['caught']}/{c['n']} = {c['recall']:.2f}")
        hard = [r for r in rt["rows"] if r.get("hard") and r.get("status") == "measured"]
        for r in hard:
            print(f"hard case {r['tamper_id']} {r['render']}: {r['caught']}/{r['n']} caught")
    fa = R.get("false_alarms")
    if fa:
        for render, e in sorted(fa["by_render"].items()):
            print(
                f"false alarms {render}: {e['flags_ge_medium']} over {e['n_clean']} clean bundles = {e['per_10']:.2f} per 10"
            )
    a = R.get("acceptance")
    if a:
        print(
            f"acceptance digital: {sum(1 for r in a['digital'] if r['pass'])}/{len(a['digital'])} pass; reproducible {a['reproducible']}/{a['reproducible_total']}"
        )
        if a.get("scan_hq"):
            print(
                f"acceptance scan_hq (oracle extraction, non-gating): {sum(1 for r in a['scan_hq'] if r['pass'])}/{len(a['scan_hq'])} pass"
            )
            for r in a["scan_hq"]:
                print(
                    f"  scan_hq {r['bundle_id']}: {'PASS' if r['pass'] else 'FAIL'} {'; '.join(r['reasons'])[:140]}"
                )
    c = R.get("canaries")
    if c:
        print(f"canaries: {c['passed']}/{c['total']}")
    f = R.get("fairness")
    if f:
        print(f"fairness: {f['overall']} (disparity {f['disparity']})")
    r = R.get("ranking")
    if r:
        for s in r["sweep"]["shares"]:
            print(
                f"ranking share {s['share']:.0%}: top-5 fraud mean {s['all']['top5_fraud']['mean']:.1f}, first fraud top-down {s['all']['first_top_down']['mean']:.1f} vs upload {s['all']['first_upload']['mean']:.1f}"
            )
    cost = R.get("cost")
    if cost and cost["no_model_cost_measured"]:
        print("no real model cost measured (fake client)")


def latest_json(R: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for r in (R.get("recall_tables") or {}).get("rows", []):
        if r.get("status") == "measured":
            rows.append(
                {
                    "tamper_id": r["tamper_id"],
                    "render": r["render"],
                    "n": r["n"],
                    "caught": r["caught"],
                    "localised": r["localised"],
                    "extraction_mode": r.get("extraction_mode"),
                }
            )
    core = {
        k: {"n": v["n"], "caught": v["caught"]}
        for k, v in ((R.get("recall_tables") or {}).get("core_recall", {})).items()
    }
    fa = {
        k: {
            "n_clean": v["n_clean"],
            "flags_ge_medium": v["flags_ge_medium"],
            "per_10": v["per_10"],
            "low_flags": v["low_flags"],
        }
        for k, v in ((R.get("false_alarms") or {}).get("by_render", {})).items()
    }
    acc = R.get("acceptance") or {"digital": [], "reproducible": 0, "reproducible_total": 0}
    return {
        "schema_version": "1",
        "generated_with": R["generated_with"],
        "recall": rows,
        "core_recall": core,
        "false_alarms": fa,
        "hard_quirk_false_alarms": {
            k: {
                "n_clean": v["n_clean"],
                "flags_ge_medium": v["flags_ge_medium"],
                "per_10": v["per_10"],
            }
            for k, v in ((R.get("false_alarms") or {}).get("hard_quirk", {})).items()
        },
        "extraction": (R.get("extraction") or {}).get("statements", {}),
        "classification": (R.get("classification") or {}).get("rules", {}),
        "canaries": {
            "passed": (R.get("canaries") or {}).get("passed"),
            "total": (R.get("canaries") or {}).get("total"),
        },
        "cost": R.get("cost", {}),
        "ranking": (R.get("ranking") or {}).get("sweep", {}),
        "fairness": {
            "overall": (R.get("fairness") or {}).get("overall"),
            "disparity": (R.get("fairness") or {}).get("disparity"),
            "groups": (R.get("fairness") or {}).get("groups", []),
        },
        "burden": R.get("burden", {}),
        "reproducibility": {"passed": acc["reproducible"], "total": acc["reproducible_total"]},
        "acceptance": [
            {"bundle_id": r["bundle_id"], "pass": r["pass"], "reasons": r["reasons"]}
            for r in acc["digital"]
        ],
        "gaps": R.get("gaps", []),
    }


def main(argv: Sequence[str] | None = None) -> int:
    a = parse_args(argv)
    cfg = make_config(a)
    try:
        R = run(cfg, a.suite, a.tamper)
        full = all(
            k in R
            for k in (
                "recall_tables",
                "false_alarms",
                "acceptance",
                "canaries",
                "fairness",
                "ranking",
                "cost",
                "burden",
                "extraction",
                "classification",
            )
        )
        baseline_path = cfg.out / "baseline.json"
        base = (
            read_json(Path(a.regress))
            if a.regress
            else (read_json(baseline_path) if baseline_path.exists() else None)
        )
        if full:
            R["changelog_row"] = changelog_row(R, base)
            R["latest"] = latest_json(R)
            if "traceability" in R:
                header = f"{HEADER} Ruleset {R['generated_with']['ruleset_version']}, today {R['generated_with']['today']}."
                (cfg.out.parent / "traceability.md").write_text(
                    tm.traceability_md(R["traceability"]["items"], R["gaps"], header),
                    encoding="utf-8",
                )
            (cfg.out.parent / "demo-script.md").write_text(
                demo_mod.demo_script_md(R), encoding="utf-8"
            )
            extra = {
                "ranking.csv": sm.ranking_csv(R["ranking"]),
                "fairness.csv": sm.fairness_csv(R["fairness"]),
            }
            rp.write_outputs(cfg.out, {**R, "recall_tables": R["recall_tables"]}, extra)
        save_results(cfg.out, {k: v for k, v in R.items() if k not in ("latest",)})
        print_summary(R)
        code = 0
        if a.write_baseline and full:
            write_json_file(cfg.out / "baseline.json", baseline_of(R))
            print(f"wrote {cfg.out / 'baseline.json'}")
        if a.write_validation_report:
            if not full:
                print("--write-validation-report needs a full run (--suite all)", file=sys.stderr)
                return 1
            vr = cfg.out.parent / "validation-report.md"
            changed = rp.write_validation_report(vr, R)
            print(f"{vr} {'updated' if changed else 'unchanged'}")
        if a.regress and full:
            reasons = regress(R, base or {}, a)
            if reasons:
                print("REGRESSION:\n  " + "\n  ".join(reasons))
                code = 2
            else:
                print("no regression against", a.regress)
        if (
            "acceptance" in R
            and any(not r["pass"] for r in R["acceptance"]["digital"])
            and a.suite in ("all", "acceptance")
        ):
            print("deck acceptance FAILED on a gating (digital) row")
            code = 3 if code == 0 else code
        return code
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
