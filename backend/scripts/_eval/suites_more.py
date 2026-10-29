"""Remaining suites: ranking, fairness, canaries, acceptance, extraction, classification, cost."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from scripts._eval import acceptance as acc
from scripts._eval import canaries as can
from scripts._eval import extraction as ex
from scripts._eval import fairness as fair
from scripts._eval import metrics as mx
from scripts._eval import ranking as rk
from scripts._eval.common import ensure_bundle, read_json
from scripts._eval.suites import Config, job, run_cached

# ----------------------------------------------------------------------------- ranking


def ranking_suite(cfg: Config) -> dict[str, Any]:
    n_slots = 6 if cfg.quick else cfg.ranking_slots
    qf, paths = rk.build_cases(
        cfg.samples, seed=cfg.seed, n_slots=n_slots, today=cfg.today, log=cfg.log
    )
    qf.reps = cfg.ranking_reps
    qf.assignments = [a for a in qf.assignments if a.rep < cfg.ranking_reps]
    jobs, meta = [], []
    for slot, forms in sorted(paths.items()):
        for kind, d in forms.items():
            meta.append((slot, kind))
            jobs.append(
                job(
                    cfg,
                    d / "manifest.json",
                    "digital",
                    registry=str(cfg.samples / "ranking" / f"q{cfg.seed}" / "registry.json"),
                )
            )
    res = run_cached(cfg, jobs, label="ranking cases")
    results: dict[int, dict[str, dict[str, Any]]] = {}
    errors = []
    for (slot, kind), r in zip(meta, res, strict=True):
        if not r.get("ok"):
            errors.append({"slot": slot, "kind": kind, "error": r.get("error")})
            continue
        results.setdefault(slot, {})[kind] = r
    complete = {s: v for s, v in results.items() if len(v) == 4}
    qf_use = qf.model_copy(update={"slots": [s for s in qf.slots if s.slot in complete]})
    base, per_flag = rk.SIM_BASE_MINUTES, rk.SIM_PER_FLAG_MINUTES
    timed = timed_test(cfg)
    if timed.get("mode") == "measured" and timed.get("minutes_per_flag") is not None:
        per_flag = timed["minutes_per_flag"]
    sweep = rk.run_sweep(complete, qf_use, base=base, per_flag=per_flag)
    per_case = [
        {
            "slot": s,
            "kind": k,
            "scenario": next(x.scenario for x in qf.slots if x.slot == s),
            "score": r["score"],
            "band": r["band"],
            "lane": r["lane"],
            "coverage": r["coverage_ratio"],
            "non_info_flags": sum(1 for f in r["flags"] if f["severity"] != "info"),
        }
        for s, forms in sorted(complete.items())
        for k, r in forms.items()
    ]
    return {
        "sweep": sweep,
        "movement": rk.movement_lines(sweep),
        "caveats": rk.CAVEATS,
        "cases": per_case,
        "errors": errors,
        "n_slots": len(complete),
        "single_tampers": {s.slot: s.single_tamper for s in qf.slots},
        "sim_model": {
            "base_minutes": base,
            "per_flag_minutes": per_flag,
            "source": "timed-test.csv medians"
            if timed.get("mode") == "measured"
            else "assumed (placeholder until the timed test is run)",
        },
        "queue_id": qf.queue_id,
    }


def ranking_csv(r: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["share", "set", "metric", "mean", "min", "max", "n"])
    for row in r["sweep"]["shares"]:
        for setname in ("all", "detectable"):
            for metric, v in sorted(row[setname].items()):
                w.writerow([row["share"], setname, metric, v["mean"], v["min"], v["max"], v["n"]])
    return buf.getvalue()


# ----------------------------------------------------------------------------- timed test


def timed_test(cfg: Config) -> dict[str, Any]:
    """``timed-test.csv`` medians when rows exist, else ``not run`` (docs 4.11)."""
    path = cfg.out / "timed-test.csv"
    if not path.exists():
        return {"mode": "not run", "reason": "no timed-test.csv"}
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    rows = [r for r in rows if any((v or "").strip() for v in r.values())]
    if not rows:
        return {
            "mode": "not run",
            "reason": "timed-test.csv has a header and no rows (the timed test is a manual task)",
        }
    import statistics

    def med(col: str, scale: float = 1.0) -> float | None:
        vals = [float(r[col]) / scale for r in rows if (r.get(col) or "").strip()]
        return statistics.median(vals) if vals else None

    return {
        "mode": "measured",
        "rows": len(rows),
        "median_seconds_to_first_fraud": med("seconds_to_first_fraud"),
        "minutes_per_flag": med("seconds_per_flag", 60.0),
    }


# ----------------------------------------------------------------------------- fairness


def fairness_suite(cfg: Config) -> dict[str, Any]:
    root = fair.ensure_fairness(cfg.samples, cfg.seed, cfg.today)
    pairs = fair.select_pairs(fair.load_pairs(cfg.samples), cfg.fairness_per_group)
    reg = root / "registry.json"
    jobs = [
        job(cfg, root / "cases" / p["pair_id"] / "manifest.json", "digital", registry=str(reg))
        for p in pairs
    ]
    res = run_cached(cfg, jobs, label="fairness pairs")
    fired: dict[str, bool] = {}
    errors = []
    for p, r in zip(pairs, res, strict=True):
        if not r.get("ok"):
            errors.append({"pair": p["pair_id"], "error": r.get("error")})
            continue
        fired[p["pair_id"]] = fair.fired_name_rule(r)
    scored_pairs = [p for p in pairs if p["pair_id"] in fired]
    out = fair.score_groups(scored_pairs, fired, fair.matcher_verdicts(scored_pairs))
    out["n_pairs"] = len(scored_pairs)
    out["errors"] = errors
    out["mode"] = "quick subset" if cfg.fairness_per_group else "full set"
    return out


def fairness_csv(f: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "group",
            "n_same",
            "false_flags",
            "false_flag_rate",
            "ff_lo",
            "ff_hi",
            "n_diff",
            "wrong_matches",
            "wrong_match_rate",
            "wm_lo",
            "wm_hi",
            "matcher_false_flag_rate",
            "matcher_wrong_match_rate",
        ]
    )
    for g in f["groups"]:
        ff, wm = g["false_flag_ci"] or (None, None), g["wrong_match_ci"] or (None, None)
        w.writerow(
            [
                g["group"],
                g["n_same"],
                g["false_flags"],
                g["false_flag_rate"],
                ff[0],
                ff[1],
                g["n_diff"],
                g["wrong_matches"],
                g["wrong_match_rate"],
                wm[0],
                wm[1],
                g.get("matcher_false_flag_rate"),
                g.get("matcher_wrong_match_rate"),
            ]
        )
    return buf.getvalue()


# ----------------------------------------------------------------------------- canaries


def canary_suite(cfg: Config) -> dict[str, Any]:
    dirs = can.canary_dirs(cfg.samples)
    if len(dirs) < len(can.HOSTS) * (len(can.PLACEMENTS) + 1):
        from scripts import make_bundle
        from scripts._gen import canaries as gen

        for sc in ("kyc",):  # the chart host needs the KYC clean bundle
            ensure_bundle(cfg.samples, sc, "clean", 0, seed=cfg.seed, today=cfg.today)
        gen.build_canaries(cfg.samples, seed=cfg.seed, today=cfg.today)
        _ = make_bundle
        dirs = can.canary_dirs(cfg.samples)
    jobs, keys = [], []
    for k, d in sorted(dirs.items()):
        jobs.append(job(cfg, d / "manifest.json", "digital", detail=True))
        keys.append(k)
    res = dict(zip(keys, run_cached(cfg, jobs, label="canaries"), strict=True))
    rows = []
    for host in can.HOSTS:
        ctrl = res.get((host, "control"))
        for p in can.PLACEMENTS:
            r = res.get((host, p))
            if ctrl is None or r is None or not ctrl.get("ok") or not r.get("ok"):
                rows.append(
                    {
                        "host": host,
                        "placement": p,
                        "passed": False,
                        "reasons": ["not run: canary or control missing or failed to analyse"],
                        "extra_page_label": None,
                    }
                )
                continue
            row = can.canary_row(ctrl, r, p)
            rows.append({"host": host, "placement": p, **row})
    passed = sum(1 for r in rows if r["passed"])
    return {"rows": rows, "passed": passed, "total": len(rows), "classifier": cfg.classifier}


# --------------------------------------------------------------------------- acceptance


def acceptance_suite(cfg: Config) -> dict[str, Any]:
    jobs = []
    for b in acc.BUNDLES:
        from scripts._eval.common import ensure_bundle

        sc, v = b.split("-")
        bdir = ensure_bundle(
            cfg.samples, sc, v, 0, seed=cfg.seed, today=cfg.today, renders=("digital", "scan_hq")
        )
        jobs.append(
            {
                "manifest": str(bdir / "manifest.json"),
                "render": "digital",
                "today": cfg.today.isoformat(),
            }
        )
    digital = run_cached(cfg, jobs, fn=acc.analyst_job, label="acceptance digital (analyst path)")
    scan_rows: list[dict[str, Any]] = []
    if not cfg.quick and "scan_hq" in cfg.renders:
        sjobs = [job(cfg, Path(j["manifest"]), "scan_hq") for j in jobs]
        sres = run_cached(cfg, sjobs, label="acceptance scan_hq (oracle)")
        for j, r in zip(jobs, sres, strict=True):
            man = read_json(Path(j["manifest"]))
            if not r.get("ok"):
                scan_rows.append(
                    {
                        "bundle_id": man["bundle_id"],
                        "render": "scan_hq",
                        "pass": False,
                        "reasons": [f"harness error: {r.get('error')}"],
                        "summary": {},
                    }
                )
                continue
            reasons = acc.check_row(r, man.get("expected", {}))
            scan_rows.append(
                {
                    "bundle_id": man["bundle_id"],
                    "render": "scan_hq",
                    "pass": not reasons,
                    "reasons": reasons,
                    "summary": r,
                }
            )
    return {
        "digital": digital,
        "scan_hq": scan_rows,
        "reproducible": sum(1 for r in digital if r.get("reproducible")),
        "reproducible_total": len(digital),
    }


# ---------------------------------------------------------------- extraction, classification


def _bundle_truth(dir_: str) -> tuple[dict[str, Any], dict[str, Any]]:
    d = Path(dir_)
    return read_json(d / "ground_truth.json"), read_json(d / "labels.json")


def extraction_tables(clean: dict[str, Any]) -> dict[str, Any]:
    stm: dict[str, dict[str, int]] = {}
    fields: dict[tuple[str, str], list[int]] = {}
    tables: dict[tuple[str, str], list[int]] = {}
    per_bundle = []
    for m, r in zip(clean["meta"], clean["results"], strict=True):
        if not r.get("ok") or "detail" not in r or m["kind"] != "clean":
            continue
        truth, _labels = _bundle_truth(m["dir"])
        rows = ex.statement_metrics(r["detail"]["documents"], truth)
        t = stm.setdefault(
            r["extraction_mode"],
            {
                "docs": 0,
                "truth_rows": 0,
                "extracted": 0,
                "aligned": 0,
                "exact": 0,
                "chain_valid": 0,
                "chain_match": 0,
            },
        )
        for x in rows:
            t["docs"] += 1
            t["truth_rows"] += x["truth_rows"]
            t["extracted"] += x["extracted_rows"]
            t["aligned"] += x["aligned"]
            t["exact"] += x["exact"]
            t["chain_valid"] += int(x["chain_valid"])
            t["chain_match"] += int(x["chain_valid"] == x["truth_chain_valid"])
        for k, (a, b) in ex.field_accuracy(r["detail"]["documents"], truth).items():
            fields.setdefault(k, [0, 0])
            fields[k][0] += a
            fields[k][1] += b
        for k, (a, b) in ex.table_recall(r["detail"]["documents"], truth).items():
            tables.setdefault(k, [0, 0])
            tables[k][0] += a
            tables[k][1] += b
        per_bundle.append(m["bundle"])
    return {
        "statements": stm,
        "fields": {f"{a}.{b}": v for (a, b), v in sorted(fields.items())},
        "tables": {f"{a}.{b}": v for (a, b), v in sorted(tables.items())},
        "bundles": per_bundle,
    }


def never_fix_chain(cfg: Config, recs: list[dict[str, Any]]) -> dict[str, Any]:
    """On T01 digital cases the printed chain is broken by construction; the extraction must keep
    it broken (``chain_valid_matches_truth``)."""
    out = {"n": 0, "kept_broken": 0, "rows": []}
    for rec in recs:
        if (
            rec["tamper_id"] != "T01"
            or rec["render"] != "digital"
            or not rec.get("ok")
            or not rec.get("detail")
        ):
            continue
        d = cfg.samples / "tampered" / rec["bundle"]
        truth = read_json(d / "ground_truth.json")
        for x in ex.statement_metrics(rec["detail"]["documents"], truth):
            if not x["truth_chain_valid"]:
                out["n"] += 1
                out["kept_broken"] += int(not x["chain_valid"])
                out["rows"].append(
                    {
                        "bundle": rec["bundle"],
                        "doc": x["doc_key"],
                        "extracted_chain_valid": x["chain_valid"],
                    }
                )
    return out


def classification_tables(clean: dict[str, Any], min_prob: float = 0.70) -> dict[str, Any]:
    per_render: dict[str, dict[str, Any]] = {}
    for m, r in zip(clean["meta"], clean["results"], strict=True):
        if not r.get("ok") or "detail" not in r or m["kind"] != "clean":
            continue
        truth, labels = _bundle_truth(m["dir"])
        t = per_render.setdefault(
            m["render"], {"c1": [], "c3": [], "c4": [], "c5": [], "low": 0, "pages": 0}
        )
        t["c1"] += ex.c1_pairs(r["detail"]["pages"], labels)
        t["c3"] += ex.c3_pairs(r["detail"]["documents"], labels)
        c4, c5 = ex.c45_pairs(r["detail"]["documents"], labels, truth)
        t["c4"] += c4
        t["c5"] += c5
        t["pages"] += len(r["detail"]["pages"])
        t["low"] += sum(1 for p in r["detail"]["pages"] if (p["prob"] or 0) < min_prob)
    out: dict[str, Any] = {"rules": {}}
    for render, t in sorted(per_render.items()):
        a1 = ex.accuracy(t["c1"])
        out["rules"][render] = {
            "C1": {"correct": a1[0], "n": a1[1], "macro_f1": ex.macro_f1(t["c1"])},
            "C3": dict(zip(("correct", "n"), ex.accuracy(t["c3"]), strict=True)),
            "C4": dict(zip(("correct", "n"), ex.accuracy(t["c4"]), strict=True)),
            "C5": dict(zip(("correct", "n"), ex.accuracy(t["c5"]), strict=True)),
            "low_conf_rate": t["low"] / t["pages"] if t["pages"] else None,
            "pages": t["pages"],
            "c1_errors": sorted({(a, str(b)) for a, b in t["c1"] if a != b}),
        }
    return out


def gate_tables(out_dir: Path) -> dict[str, Any]:
    path = out_dir / "classifier-gate.json"
    if not path.exists():
        return {
            "status": "not run: docs/eval-results/classifier-gate.json is missing (run backend/scripts/classifier_gate.py)"
        }
    g = read_json(path)
    return {
        "status": "imported",
        "pages": g.get("pages"),
        "ocr": g.get("ocr"),
        "tasks": {
            t: {
                c: {
                    k: v
                    for k, v in d.items()
                    if k in ("accuracy", "macro_f1", "n", "low_conf_rate", "by_render")
                }
                for c, d in cl.items()
            }
            for t, cl in g.get("tasks", {}).items()
        },
        "classifiers": {c: d.get("status") for c, d in g.get("classifiers", {}).items()},
        "decision": g.get("decision", {}).get("recommendation"),
        "latency": g.get("latency"),
        "usd_per_100_pages": g.get("usd_per_100_pages"),
    }


# ------------------------------------------------------------------------------- cost


def cost_tables(
    cfg: Config, summaries: list[dict[str, Any]], correct: dict[str, Any]
) -> dict[str, Any]:
    ok = [s for s in summaries if s.get("ok") and s["render"] == "digital"]
    secs = [s["seconds"] for s in ok]
    usd = [float(s["usd"]) for s in ok]
    stages: dict[str, list[float]] = {}
    pages = 0
    for s in ok:
        pages += s["pages"]
        for k, v in s["stages"].items():
            stages.setdefault(k, []).append(v["seconds"])
    timed = timed_test(cfg)
    minutes = timed.get("minutes_per_flag") if timed.get("mode") == "measured" else 2.0
    placeholder = timed.get("mode") != "measured"
    rate = 60.0
    fa_flags = correct["false_alarm_flags"]
    n_correct = correct["n_correct"]
    total_usd = sum(usd)
    cost_per = (total_usd + fa_flags * minutes / 60.0 * rate) / n_correct if n_correct else None
    return {
        "bundles": len(ok),
        "seconds": {
            "mean": mx.mean(secs),
            "p50": mx.percentile_nearest_rank(secs, 0.5),
            "p95": mx.percentile_nearest_rank(secs, 0.95),
        },
        "usd_total": total_usd,
        "usd_mean": mx.mean(usd),
        "pages": pages,
        "stage_seconds": {
            k: {"mean": mx.mean(v), "p95": mx.percentile_nearest_rank(v, 0.95), "usd_per_page": 0.0}
            for k, v in stages.items()
        },
        "no_model_cost_measured": all(u == 0 for u in usd),
        "cost_per_correct_bundle": cost_per,
        "n_correct": n_correct,
        "assumptions": {
            "minutes_per_false_flag": minutes,
            "analyst_rate_usd_per_hour": rate,
            "placeholder": placeholder or True,
        },
        "timed_test": timed,
        "experiments": {
            name: {"status": "not_run: no api key"}
            for name in ("model_sweep", "text_vs_vision", "dpi_sweep")
        },
    }


def burden_tables(clean: dict[str, Any], acceptance: dict[str, Any]) -> dict[str, Any]:
    def stats(vals: list[int]) -> dict[str, Any]:
        return {
            "n": len(vals),
            "mean": mx.mean(vals),
            "median": mx.percentile_nearest_rank(vals, 0.5),
            "p95": mx.percentile_nearest_rank(vals, 0.95),
        }

    def non_info(r: dict[str, Any]) -> int:
        return sum(1 for f in r["flags"] if f["severity"] != "info")

    clean_flags = [
        non_info(r)
        for m, r in zip(clean["meta"], clean["results"], strict=True)
        if r.get("ok") and m["render"] == "digital" and m["kind"] == "clean"
    ]
    fraud = [
        non_info(r["summary"])
        for r in acceptance["digital"]
        if r.get("ok") and r["bundle_id"].endswith("-fraud")
    ]
    return {
        "clean": {"flags_per_bundle": stats(clean_flags)},
        "deck_fraud": {"flags_per_bundle": stats(fraud)},
        "timed_test": "see cost.timed_test",
    }
