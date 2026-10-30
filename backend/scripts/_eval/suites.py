"""Orchestration of the eval suites: builds the cases, runs them through the pipeline in a process
pool (with an on-disk result cache keyed by the files, the job and a fingerprint of ``app/``), and
turns the case summaries into the metric tables of docs/test-data-and-eval.md section 4.4.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from scripts._eval import metrics as mx
from scripts._eval.common import (
    DEFAULT_SEED,
    ensure_bundle,
    load_manifest,
    read_json,
)
from scripts._eval.runner import run_jobs, run_pool
from scripts._eval.tamper_core import TamperNotApplicable
from scripts._eval.tamper_defs import TAMPERS, registered_rules, unregistered_rules
from scripts._gen.schema import FIXED_TODAY

SCENARIOS = ("loan", "kyc", "aml")
POOL = 10  # clean pool size P: instance k uses the clean base of company k mod P
# Compute budget for degraded renders: one scan analysis costs about a minute of CPU, so scans
# cover these tamper ids (and one instance per scenario) unless --full-degradation is given.
DEGRADED_SUBSET_HQ = [
    "T01",
    "T02a",
    "T03",
    "T04",
    "T05",
    "T10",
    "T11",
    "T12",
    "T19",
    "T20a",
    "T23",
    "T50a",
    "T53",
    "T57",
    "T58",
    "T59",
    "T60",
]
DEGRADED_SUBSET_OTHER = ["T01", "T10", "T50a", "T53"]
IMAGE_IDS = ["T40a", "T40b", "T41", "T42", "T43"]
# one image-only PDF per document: separates "the rules misfire on scans" from "a statement uploaded as
# one JPEG per page cannot be reassembled" (reported as a diagnostic row, never in recall)
DIAGNOSTIC_RENDER = "scan_pdf"


@dataclass
class Config:
    samples: Path
    out: Path
    seed: int = DEFAULT_SEED
    today: date = FIXED_TODAY
    n: int = 5
    renders: list[str] = field(default_factory=lambda: ["digital", "scan_hq", "scan_lq", "photo"])
    quick: bool = False
    workers: int = 6
    extraction: str = "auto"
    classifier: str = "rules"
    use_cache: bool = True
    full_degradation: bool = False
    n_degraded: int = 1
    ranking_reps: int = 5
    ranking_slots: int = 30
    fairness_per_group: int | None = None
    log: Callable[[str], None] = print

    @property
    def degraded(self) -> list[str]:
        return [r for r in self.renders if r != "digital"]


# ------------------------------------------------------------------------------ job cache


def code_fingerprint() -> str:
    """Hash of path, size and mtime of every source file under ``backend/app`` and the runner, so
    a cached result is never reused after the code changes (and, with ``GLDB_SEMANTIC`` on, of the
    semantic settings)."""
    root = Path(__file__).resolve().parents[2]
    h = hashlib.sha256()
    for p in sorted((root / "app").rglob("*.py")):
        st = p.stat()
        h.update(f"{p.relative_to(root)}:{st.st_size}:{st.st_mtime_ns}".encode())
    for name in ("runner.py", "acceptance.py"):
        p = Path(__file__).with_name(name)
        h.update(f"{name}:{p.stat().st_size}:{p.stat().st_mtime_ns}".encode())
    sem = _semantic_environment()
    if sem:  # only when on, so every key made with the switch off is unchanged
        h.update(sem.encode())
    return h.hexdigest()[:16]


def _semantic_environment() -> str:
    """The model-derived-signal settings that change a result: empty with ``GLDB_SEMANTIC`` off.
    With it on, the switch, the claim cap, the model names, the backend and the endpoint, so an
    on run never reads an off run's cache (or a run against another model or server)."""
    from app.core.config import Settings
    from app.semantic.policy import SEMANTIC_VERSION

    s = Settings(_env_file=None)  # the runner ignores .env files too (runner.make_settings)
    if s.semantic == "off":
        return ""
    settings = dict(
        s.result_settings(), llm_base_url=s.llm_base_url, semantic_version=SEMANTIC_VERSION
    )
    return json.dumps(settings, sort_keys=True)


def _job_key(job: dict[str, Any], fp: str) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(job, sort_keys=True).encode())
    for k in ("manifest", "prior"):
        if job.get(k):
            h.update(Path(job[k]).read_bytes())
    for extra in job.get("extra_files") or []:
        h.update(Path(extra).read_bytes())
    h.update(fp.encode())
    return h.hexdigest()


def run_cached(
    cfg: Config,
    jobs: list[dict[str, Any]],
    fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    label: str = "",
) -> list[dict[str, Any]]:
    """Run ``jobs`` (deduplicated), reading and writing ``<out>/.cache``. Order is preserved."""
    cache_dir = cfg.out / ".cache"
    fp = code_fingerprint()
    keys = [_job_key(j, fp) for j in jobs]
    results: dict[str, dict[str, Any]] = {}
    todo: dict[str, dict[str, Any]] = {}
    for k, j in zip(keys, jobs, strict=True):
        if k in results or k in todo:
            continue
        path = cache_dir / f"{k}.json"
        if cfg.use_cache and path.exists():
            results[k] = json.loads(path.read_text(encoding="utf-8"))
        else:
            todo[k] = j
    if todo:
        cfg.log(
            f"  {label}: running {len(todo)} analyses ({len(jobs) - len(todo)} cached/duplicate)"
        )
        run = (
            (lambda js: run_pool(fn, js, cfg.workers))
            if fn
            else (lambda js: run_jobs(js, cfg.workers))
        )
        fresh = run(list(todo.values()))
        cache_dir.mkdir(parents=True, exist_ok=True)
        for k, res in zip(todo, fresh, strict=True):
            results[k] = res
            if res.get("ok", True):
                (cache_dir / f"{k}.json").write_text(json.dumps(res), encoding="utf-8")
    return [results[k] for k in keys]


def job(cfg: Config, manifest: Path, render: str, **extra: Any) -> dict[str, Any]:
    return {
        "manifest": str(manifest),
        "render": render,
        "today": cfg.today.isoformat(),
        "extraction": cfg.extraction,
        "classifier": cfg.classifier,
        "page_workers": 1 if cfg.workers > 1 else 4,
        **extra,
    }


# ------------------------------------------------------------------------------ clean pool


def prepare_bases(cfg: Config) -> dict[tuple[str, int], Path]:
    """Build the clean pool (companies 0..10 per scenario): digital everywhere, the degraded
    renders for the canonical company 0 (and company 1 with ``--full-degradation``)."""
    out: dict[tuple[str, int], Path] = {}
    for sc in SCENARIOS:
        for k in range(POOL + 1):
            deg = (
                tuple(cfg.degraded)
                if (k == 0 or (cfg.full_degradation and k < cfg.n_degraded + 1))
                else ()
            )
            if k == 0 and deg:
                deg = (*deg, DIAGNOSTIC_RENDER)
            out[(sc, k)] = ensure_bundle(
                cfg.samples,
                sc,
                "clean",
                k,
                seed=cfg.seed,
                today=cfg.today,
                renders=deg,
                update_registry=False,
            )
    from scripts._eval.common import build_registry_for

    build_registry_for(cfg.samples)
    return out


def hard_quirk_bundles(cfg: Config) -> list[Path]:
    from scripts import make_bundle
    from scripts._gen import quirks as qk
    from scripts._gen.schema import BundleSpec, bundle_id

    out = []
    for sc, idx in make_bundle.HARD_COMPANY.items():
        spec = BundleSpec(
            scenario=sc,
            variant="clean",
            company_index=idx,
            seed=cfg.seed,
            today=cfg.today,
            quirks=[*qk.default_quirks(idx), *qk.hard_quirks_for(sc)],
        )  # type: ignore[arg-type]
        bdir = cfg.samples / "bundles" / bundle_id(spec)
        man = bdir / "manifest.json"
        if not man.exists() or not any(q["id"].startswith("H") for q in read_json(man)["quirks"]):
            make_bundle.build_bundle(spec, bdir)
        out.append(bdir)
    from scripts._eval.common import build_registry_for

    build_registry_for(cfg.samples)
    return out


def clean_runs(cfg: Config, bases: dict[tuple[str, int], Path]) -> dict[str, Any]:
    """Analyse every clean bundle: digital for all 33 (with detail for M4 to M6), the hard-quirk
    bundles separately, and the degraded renders where they were built."""
    jobs, meta = [], []
    for (sc, k), bdir in sorted(bases.items()):
        man = bdir / "manifest.json"
        jobs.append(job(cfg, man, "digital", detail=True))
        meta.append(
            {
                "bundle": bdir.name,
                "scenario": sc,
                "company": k,
                "render": "digital",
                "kind": "clean",
                "dir": str(bdir),
            }
        )
        for r in [*cfg.degraded, DIAGNOSTIC_RENDER]:
            if r in read_json(man)["upload_order"] and (r != DIAGNOSTIC_RENDER or cfg.degraded):
                jobs.append(job(cfg, man, r))
                meta.append(
                    {
                        "bundle": bdir.name,
                        "scenario": sc,
                        "company": k,
                        "render": r,
                        "kind": "clean",
                        "dir": str(bdir),
                    }
                )
    for bdir in hard_quirk_bundles(cfg):
        jobs.append(job(cfg, bdir / "manifest.json", "digital", detail=True))
        meta.append(
            {
                "bundle": bdir.name,
                "scenario": bdir.name.split("-")[0],
                "company": 9,
                "render": "digital",
                "kind": "hard_quirk",
                "dir": str(bdir),
            }
        )
    res = run_cached(cfg, jobs, label="clean bundles")
    return {"meta": meta, "results": res}


def false_alarm_table(clean: dict[str, Any]) -> dict[str, Any]:
    """M3 per render for kind ``clean`` and separately for the hard-quirk bundles."""
    out: dict[str, Any] = {"by_render": {}, "hard_quirk": {}, "errors": []}
    groups: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for m, r in zip(clean["meta"], clean["results"], strict=True):
        if not r.get("ok"):
            out["errors"].append(
                {"bundle": m["bundle"], "render": m["render"], "error": r.get("error")}
            )
            continue
        groups.setdefault((m["kind"], m["render"]), []).append((m, r))
    for (kind, render), items in sorted(groups.items()):
        rows = []
        n_ge = n_low = n_any = 0
        allowed_total = 0
        for m, r in items:
            man = read_json(Path(m["dir"]) / "manifest.json")
            may = [t for q in man["quirks"] for t in q.get("may_trigger", [])]
            bad, allowed = mx.false_alarm_flags(r["flags"], may)
            low = [f for f in r["flags"] if f["severity"] == "low"]
            n_ge += len(bad)
            n_low += len(low)
            n_any += 1 if bad else 0
            allowed_total += len(allowed)
            rows.append(
                {
                    "bundle": m["bundle"],
                    "scenario": m["scenario"],
                    "false_alarms": sorted({(f["rule"], f["severity"]) for f in bad}),
                    "n_ge_medium": len(bad),
                    "low": len(low),
                    "allowed_by_quirk": len(allowed),
                    "score": r["score"],
                    "lane": r["lane"],
                    "coverage": r["coverage_ratio"],
                    "extraction_mode": r["extraction_mode"],
                    "fast_lane": r["lane"] == "fast",
                }
            )
        n = len(items)
        entry = {
            "n_clean": n,
            "flags_ge_medium": n_ge,
            "per_10": mx.per_ten(n_ge, n),
            "low_flags": n_low,
            "low_per_10": mx.per_ten(n_low, n),
            "bundles_with_false_alarm": n_any,
            "allowed_by_quirk": allowed_total,
            "fast_lane": sum(1 for x in rows if x["fast_lane"]),
            "rows": rows,
        }
        (out["hard_quirk"] if kind == "hard_quirk" else out["by_render"])[render] = entry
    return out


# ---------------------------------------------------------------------------------- recall


def _instances(cfg: Config, tid: str) -> list[tuple[str, int, list[str]]]:
    """``(scenario, instance, renders)`` to build for tamper ``tid`` under the compute budget."""
    td = TAMPERS[tid]
    out: list[tuple[str, int, list[str]]] = []
    want_deg = [r for r in cfg.degraded if r in td.renders]
    for sc in td.scenarios:
        n_dig = cfg.n if "digital" in td.renders and "digital" in cfg.renders else 0
        n_deg = cfg.n_degraded
        if cfg.full_degradation:
            n_deg = cfg.n
        for k in range(max(n_dig, n_deg if want_deg else 0)):
            rs: list[str] = []
            if k < n_dig:
                rs.append("digital")
            if k < n_deg:
                for r in want_deg:
                    in_budget = (
                        cfg.full_degradation
                        or tid in IMAGE_IDS
                        or (
                            tid in DEGRADED_SUBSET_HQ
                            if r == "scan_hq"
                            else tid in DEGRADED_SUBSET_OTHER
                        )
                    )
                    if in_budget:
                        rs.append(r)
            if rs:
                out.append((sc, k, rs))
    return out


def _build_job(job: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry: build one tampered instance (never raises; failures are returned)."""
    from scripts.make_tampered import apply_tamper

    base_dir = Path(job["base_dir"])
    base = load_manifest(base_dir / "manifest.json")
    try:
        apply_tamper(
            base,
            job["tamper_id"],
            seed=job["seed"],
            instance=job["instance"],
            out_dir=Path(job["dest"]),
            render=job["renders"][0],
            renders=job["renders"],
            base_dir=base_dir,
        )
    except TamperNotApplicable as exc:
        return {"ok": False, "status": f"not applicable: {exc}"}
    except Exception as exc:  # a builder bug is reported, not hidden
        return {"ok": False, "status": f"build error: {type(exc).__name__}: {exc}"}
    return {"ok": True}


def build_tampered(
    cfg: Config, ids: list[str], bases: dict[tuple[str, int], Path]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build every tampered instance (in a process pool); returns ``(instances, skipped)``."""
    out_root = cfg.samples / "tampered"
    insts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    registered = registered_rules()
    jobs: list[dict[str, Any]] = []
    for tid in ids:
        td = TAMPERS[tid]
        gap = unregistered_rules(td, registered) if registered else []
        if gap:
            skipped.append({"tamper_id": tid, "status": f"rule not implemented: gap {gap}"})
            continue
        for sc, k, rs in _instances(cfg, tid):
            bdir = bases[(sc, k % POOL)]
            base = load_manifest(bdir / "manifest.json")
            missing = [r for r in rs if r != "digital" and r not in base.upload_order]
            if missing:
                # the degraded base exists only for the canonical company: use company 0
                bdir = bases[(sc, 0)]
                base = load_manifest(bdir / "manifest.json")
                missing = [r for r in rs if r != "digital" and r not in base.upload_order]
                if missing:
                    skipped.append(
                        {
                            "tamper_id": tid,
                            "scenario": sc,
                            "instance": k,
                            "status": f"not run: base lacks {missing}",
                        }
                    )
                    continue
            dest = out_root / f"{tid}-{base.bundle_id}-i{k}"
            jobs.append(
                {
                    "tamper_id": tid,
                    "scenario": sc,
                    "instance": k,
                    "dir": str(dest),
                    "dest": str(dest),
                    "base_dir": str(bdir),
                    "renders": rs,
                    "seed": cfg.seed,
                }
            )
    results = run_pool(_build_job, jobs, cfg.workers)
    for j, r in zip(jobs, results, strict=True):
        if r.get("ok"):
            insts.append(
                {
                    k: j[k]
                    for k in ("tamper_id", "scenario", "instance", "dir", "base_dir", "renders")
                }
            )
        else:
            skipped.append(
                {
                    "tamper_id": j["tamper_id"],
                    "scenario": j["scenario"],
                    "instance": j["instance"],
                    "status": r["status"],
                }
            )
    return insts, skipped


def recall_runs(
    cfg: Config, insts: list[dict[str, Any]], clean: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Analyse tampered cases and their controls; returns one record per (instance, render)."""
    jobs: list[dict[str, Any]] = []
    plan: list[tuple[dict[str, Any], str, int, int]] = []
    for inst in insts:
        man = Path(inst["dir"]) / "manifest.json"
        base_man = Path(inst["base_dir"]) / "manifest.json"
        tid = inst["tamper_id"]
        for r in inst["renders"]:
            if r not in read_json(man)["upload_order"]:
                continue
            detail = tid == "T01" and r == "digital"
            if tid == "T16c":
                jobs.append(job(cfg, man, r, prior=str(base_man)))
                jobs.append(job(cfg, man, r))
            else:
                jobs.append(job(cfg, man, r, detail=detail))
                jobs.append(job(cfg, base_man, r))
            plan.append((inst, r, len(jobs) - 2, len(jobs) - 1))
    res = run_cached(cfg, jobs, label="tampered cases and controls")
    recs = []
    for inst, r, ia, ib in plan:
        recs.append(score_instance(inst, r, res[ia], res[ib]))
    return recs


def score_instance(
    inst: dict[str, Any], render: str, a: dict[str, Any], b: dict[str, Any]
) -> dict[str, Any]:
    man_path = Path(inst["dir"]) / "manifest.json"
    man = read_json(man_path)
    rec = man["tampers"][0]
    td = TAMPERS[rec["tamper_id"]]
    out: dict[str, Any] = {
        "tamper_id": rec["tamper_id"],
        "scenario": inst["scenario"],
        "instance": inst["instance"],
        "render": render,
        "bundle": Path(inst["dir"]).name,
        "expected": rec["expected_any_of"],
        "hard": td.hard,
        "weak": td.weak,
        "known_miss": td.known_miss,
        "channel": td.channel,
        "edit": rec["edit"],
    }
    if not a.get("ok") or not b.get("ok"):
        out.update(ok=False, error=a.get("error") or b.get("error"))
        return out
    out["ok"] = True
    out["extraction_mode"] = a["extraction_mode"]
    out["score"], out["base_score"] = a["score"], b["score"]
    info_rules = info_by_design_rules(rec["expected_any_of"])
    caught = mx.caught_flags(a["flags"], b["flags"], rec["expected_any_of"], info_rules)
    out["caught"] = bool(caught)
    out["caught_rules"] = sorted({f["rule"] for f in caught})
    out["caught_evidence"] = bool(caught) and all(f.get("has_evidence") for f in caught)
    out["caught_with_location"] = any(
        f.get("regions") or f.get("row") is not None or f.get("field") for f in caught
    )
    out["new_rules"] = sorted(
        {(f["rule"], f["severity"]) for f in mx.new_flags(a["flags"], b["flags"])}
    )
    out["base_fired_expected"] = sorted(
        {
            f["rule"]
            for f in b["flags"]
            if f["rule"] in rec["expected_any_of"]
            and (f["severity"] != "info" or f["rule"] in info_rules)
        }
    )
    target = rec["target"]
    out["localisable"] = mx.localisation_applicable(target)
    out["localised"] = None
    if out["caught"] and out["localisable"]:
        out["localised"] = _localised(man, Path(inst["dir"]), caught, target, render, rec)
    runs = a.get("check_runs", {})
    out["coverage"] = {r: runs.get(r, {"status": "absent"}) for r in rec["expected_any_of"]}
    out["lane"], out["band"] = a["lane"], a["band"]
    out["detail"] = a.get("detail")
    return out


def info_by_design_rules(rules: list[str]) -> set[str]:
    """The rules among ``rules`` whose registered severity is ``info`` (a hint is their catch)."""
    try:
        from app.checks.registry import get_check
    except Exception:  # pragma: no cover - registry import problems are reported elsewhere
        return set()
    return {r for r in rules if (m := get_check(r)) is not None and m.severity == "info"}


def _localised(
    man: dict[str, Any],
    tdir: Path,
    flags: list[dict[str, Any]],
    target: dict[str, Any],
    render: str,
    rec: dict[str, Any],
) -> bool:
    gt = read_json(tdir / "ground_truth.json")
    sha_by_path = {f["path"]: f["sha256"] for f in man["files"]}
    row_sig = None
    box = None
    box_sha = None
    doc = next((d for d in gt["documents"] if d["key"] == target["doc_key"]), None)
    if target.get("row") is not None and doc is not None and target.get("table") in doc["tables"]:
        r = next(
            (x for x in doc["tables"][target["table"]]["rows"] if x["index"] == target["row"]), None
        )
        if r is not None:
            row_sig = {
                c: (r["cells"].get(c) or {}).get("value")
                for c in ("date", "debit", "credit", "balance")
            }
    key = f"box_{render}"
    edited = rec.get("edited") or {}
    if render != "digital" and edited.get(key):
        f, page, x, y, w, h = edited[key].split("|")
        box = {"page": int(page), "x": float(x), "y": float(y), "w": float(w), "h": float(h)}
        box_sha = sha_by_path.get(f)
    elif render != "digital" and doc is not None:
        src = None
        if target.get("row") is not None and target.get("table") in doc["tables"]:
            r = next(
                (x for x in doc["tables"][target["table"]]["rows"] if x["index"] == target["row"]),
                None,
            )
            src = (r or {}).get("boxes", {}).get(render)
        elif target.get("field"):
            src = (doc["fields"].get(target["field"]) or {}).get("boxes", {}).get(render)
        if src:
            box = {k: src[k] for k in ("page", "x", "y", "w", "h")}
            box_sha = sha_by_path.get(src["file"])
    elif target.get("box"):
        tb = target["box"]
        box = {k: tb[k] for k in ("page", "x", "y", "w", "h")}
        box_sha = sha_by_path.get(tb["file"])
    return mx.is_localised(
        flags,
        target,
        target_doc_keys=[target["doc_key"]],
        target_row_sig=row_sig,
        target_box=box,
        target_box_sha=box_sha,
    )


def recall_tables(
    recs: list[dict[str, Any]], skipped: list[dict[str, Any]], ids: list[str], registered: set[str]
) -> dict[str, Any]:
    """Aggregate: per (tamper, render) n / caught / localised with Wilson intervals; core recall
    pooled over non-weak, non-hard, non-known-miss ids; coverage-vs-recall for the misses."""
    rows: list[dict[str, Any]] = []
    for tid in ids:
        td = TAMPERS[tid]
        gap = unregistered_rules(td, registered) if registered else []
        by_render: dict[str, list[dict[str, Any]]] = {}
        for r in recs:
            if r["tamper_id"] == tid:
                by_render.setdefault(r["render"], []).append(r)
        if not by_render:
            why = next((s["status"] for s in skipped if s["tamper_id"] == tid), "not run")
            rows.append(
                {
                    "tamper_id": tid,
                    "edit": td.edit,
                    "channel": td.channel,
                    "render": "-",
                    "n": 0,
                    "caught": 0,
                    "status": ("rule not implemented: gap" if gap else why),
                    "hard": td.hard,
                    "weak": td.weak,
                    "known_miss": td.known_miss,
                    "expected": td.expected_any_of,
                }
            )
            continue
        for render, items in sorted(by_render.items()):
            ok = [i for i in items if i.get("ok")]
            caught = sum(1 for i in ok if i["caught"])
            loc_den = sum(1 for i in ok if i["caught"] and i["localisable"])
            loc = sum(1 for i in ok if i["localised"])
            lo, hi = mx.wilson(caught, len(ok))
            miss = [i for i in ok if not i["caught"]]
            not_run = sum(
                1
                for i in miss
                if all(i["coverage"].get(x, {}).get("status") != "ran" for x in i["expected"])
                and i["expected"]
            )
            already = sum(1 for i in ok if not i["caught"] and i["base_fired_expected"])
            reasons = sorted(
                {
                    f"{x}: "
                    + (
                        "no check run recorded for this use case"
                        if i["coverage"][x].get("status") == "absent"
                        else f"{i['coverage'][x].get('status')} ({i['coverage'][x].get('reason') or 'no reason given'})"
                    )
                    for i in miss
                    for x in i["expected"]
                    if i["coverage"].get(x, {}).get("status") != "ran"
                }
            )
            rows.append(
                {
                    "tamper_id": tid,
                    "edit": td.edit,
                    "channel": td.channel,
                    "render": render,
                    "n": len(ok),
                    "errors": len(items) - len(ok),
                    "caught": caught,
                    "recall": caught / len(ok) if ok else None,
                    "wilson_lo": lo,
                    "wilson_hi": hi,
                    "localisable": loc_den,
                    "localised": loc,
                    "missed_not_run": not_run,
                    "missed_ran": len(miss) - not_run,
                    "already_firing_on_base": already,
                    "not_run_reasons": reasons,
                    "extraction_mode": ok[0]["extraction_mode"] if ok else None,
                    "hard": td.hard,
                    "weak": td.weak,
                    "known_miss": td.known_miss,
                    "expected": td.expected_any_of,
                    "status": "measured",
                }
            )
    core: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.get("status") != "measured" or row["hard"] or row["weak"] or row["known_miss"]:
            continue
        c = core.setdefault(row["render"], {"n": 0, "caught": 0, "localised": 0, "localisable": 0})
        c["n"] += row["n"]
        c["caught"] += row["caught"]
        c["localised"] += row["localised"]
        c["localisable"] += row["localisable"]
    for c in core.values():
        c["recall"] = c["caught"] / c["n"] if c["n"] else None
        c["wilson"] = mx.wilson(c["caught"], c["n"])
    return {"rows": rows, "core_recall": core, "skipped": skipped}


def write_case_summaries(out: Path, recs: list[dict[str, Any]]) -> None:
    d = out / "cases"
    d.mkdir(parents=True, exist_ok=True)
    for r in recs:
        slim = {k: v for k, v in r.items() if k != "detail"}
        (d / f"{r['bundle']}-{r['render']}.json").write_text(
            json.dumps(slim, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )


def log_to(stream=sys.stderr):  # noqa: ANN001, ANN201
    return lambda s: print(s, file=stream, flush=True)
