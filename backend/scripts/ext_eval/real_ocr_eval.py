"""Real-OCR evaluation: the same bundles on the oracle and on real OCR, side by side.

For every requested render (the generator's ``scan_hq``, ``scan_lq``, ``photo``, ``scan_pdf`` and the
SIMULATED ``scan_sim_light|med|heavy`` from ``scan_sim.py``) the clean bundles and a set of tampered
cases are analysed in each mode (``oracle`` = generator ground truth stands in for OCR, ``ocr`` = Apple
Vision + the repo's deterministic parsers) and the tables below are written:

* extraction: statement row recall and precision, exact descriptions, chain validity, field accuracy,
  table recall, page classification, documents found, OCR page errors;
* rule outcomes: false alarms on clean bundles (high or medium flags, M3) and tamper recall (M1) with
  Wilson intervals.

    uv run python backend/scripts/ext_eval/real_ocr_eval.py run --renders scan_pdf scan_sim_med \
        --modes oracle ocr --scenarios loan --tampers T01 T10 --workers 4

Results go to ``docs/eval-results/real-ocr/`` (``real-ocr.md``, ``extraction.csv``, ``false_alarms.csv``,
``recall.csv``, ``results.json``). Tune nothing on these results: this script has no thresholds.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts._eval import extraction as ex  # noqa: E402
from scripts._eval import metrics as mx  # noqa: E402
from scripts._eval import suites  # noqa: E402
from scripts._eval.common import DEFAULT_SEED, read_json  # noqa: E402
from scripts._eval.runner import run_pool  # noqa: E402
from scripts._eval.tamper_defs import TAMPERS, registered_rules  # noqa: E402
from scripts._gen.schema import FIXED_TODAY  # noqa: E402
from scripts.ext_eval import real_ocr, scan_sim  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
STANDARD_RENDERS = ("scan_hq", "scan_lq", "photo", "scan_pdf")
SIM_RENDERS = tuple(f"scan_sim_{n}" for n in scan_sim.LEVEL_NAMES)
SIM_JPEG_RENDERS = tuple(f"{r}_jpg" for r in SIM_RENDERS)
DEFAULT_RENDERS = (*STANDARD_RENDERS, *SIM_RENDERS)
SIM_CHANNELS = (
    "world",
    "bundle",
)  # the simulator carries page pixels only (no PDF or EXIF metadata)
DEFAULT_TAMPERS = ["T01", "T03", "T10", "T12", "T19", "T20a", "T23", "T58", "T59", "T60"]

LIMITS = [
    "The OCR is Apple Vision through `ocrmac`; the parser is the repo's deterministic text-layer "
    "extractors fed OCR words whose per-token boxes are cut proportionally from the OCR line box. No "
    "vision LLM was run (no key), so this is a conservative stand-in for production, not a measure of it.",
    "Scan and photo renders uploaded as one JPEG per page (`scan_hq`, `scan_lq`, `photo`, `*_jpg`) cannot "
    "be reassembled by the pipeline (segmentation is per file), so a two-page statement becomes two "
    "documents: `bs_totals`, `bs_page_chain` and cross-document sums fire on the fragments whatever the "
    "OCR quality. Extraction metrics merge fragments by document key to isolate OCR quality; the rule "
    "tables do not. `scan_pdf` and `scan_sim_*` (one image-only PDF per document) are the fair rows.",
    "The oracle on per-page JPEG renders ignores `doc_page` (core/llm.py, outside this harness), which "
    "inflates its flags further; oracle rows on `scan_pdf` are not affected.",
    "Simulated renders (`scan_sim_*`) are software models of print-scan and camera artefacts, not a "
    "physical print-and-scan. They are derived from the digital PDFs only, so they carry no PDF or EXIF "
    "metadata and no pixel-level tampers; tampers of channel `pdf_edit`, `metadata` and `image` are not "
    "planned on them. Oracle mode is not available on them (no transformed ground-truth boxes).",
    "All documents are our own fictitious, cleanly typeset generator output in four statement layouts. "
    "Real bank, payroll and ID layouts, handwriting, stamps, folds, staples and multi-column scans are "
    "not represented. Results are an upper bound for real documents.",
    "Recall counts a new flag of an expected rule relative to the same render of the base bundle. With "
    "real OCR the clean base already carries many OCR-noise flags of the same arithmetic rules "
    "(`bs_totals`, `bs_running_balance`), so a catch can be a coincidence with noise and recall can "
    "be inflated; read it together with the false-alarm table. Oracle recall on per-page JPEG renders "
    "is depressed by the oracle defect above and by the unreassembled statements (rules that did not "
    "run), not by the tamper.",
    "A forgery that is internally consistent and typeset like the original (every total recomputed, "
    "plausible values) is not caught by the arithmetic rules at any scan quality; scanning also "
    "removes the PDF-level evidence (metadata, text layer, fonts) the digital checks rely on.",
]


# ------------------------------------------------------------------------------ planning


def code_salt() -> str:
    """Changes when this harness changes, so the result cache never serves stale numbers."""
    h = hashlib.sha256()
    for name in ("real_ocr.py", "scan_sim.py"):  # the files that change a result, not the report
        h.update((Path(__file__).with_name(name)).read_bytes())
    return h.hexdigest()[:12]


def is_sim(render: str) -> bool:
    return render.startswith("scan_sim_")


def sim_level_container(render: str) -> tuple[str, str]:
    """``scan_sim_med_jpg`` -> ``("med", "jpeg")``; ``scan_sim_med`` -> ``("med", "pdf")``."""
    body = render.removeprefix("scan_sim_")
    if body.endswith("_jpg"):
        return body.removesuffix("_jpg"), "jpeg"
    return body, "pdf"


def _build_sim_worker(job: dict[str, Any]) -> dict[str, Any]:
    try:
        out = scan_sim.build_sim_bundle(
            Path(job["src"]),
            Path(job["out_root"]),
            levels=tuple(job["levels"]),
            containers=tuple(job["containers"]),
            seed=job["seed"],
            max_files=job.get("max_files"),
        )
        return {"ok": True, "dir": str(out)}
    except Exception as exc:  # reported, never hidden
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "src": job["src"]}


def ensure_sim(
    srcs: Sequence[Path],
    sim_root: Path,
    renders: Sequence[str],
    *,
    seed: int,
    workers: int,
    max_files: int | None = None,
    log: Any = print,
) -> dict[Path, Path]:
    """Build (or reuse) the simulated bundle of each source dir; returns ``src -> sim dir``."""
    wanted = [r for r in renders if is_sim(r)]
    levels = sorted({sim_level_container(r)[0] for r in wanted}, key=scan_sim.LEVEL_NAMES.index)
    containers = sorted({sim_level_container(r)[1] for r in wanted})
    result: dict[Path, Path] = {}
    todo: list[dict[str, Any]] = []
    for src in srcs:
        dest = sim_root / src.name
        man = dest / "manifest.json"
        have = set(read_json(man)["upload_order"]) if man.exists() else set()
        if man.exists() and all(r in have for r in wanted):
            result[src] = dest
        else:
            todo.append(
                {
                    "src": str(src),
                    "out_root": str(sim_root),
                    "levels": levels,
                    "containers": containers,
                    "seed": seed,
                    "max_files": max_files,
                }
            )
    if todo:
        log(f"  building simulated renders for {len(todo)} bundle dir(s) ...")
        for job, res in zip(todo, run_pool(_build_sim_worker, todo, workers), strict=True):
            if not res["ok"]:
                raise RuntimeError(res["error"])
            result[Path(job["src"])] = Path(res["dir"])
    return result


def _manifest(d: Path) -> dict[str, Any]:
    return read_json(d / "manifest.json")


def clean_dirs(samples: Path, scenarios: Sequence[str]) -> list[Path]:
    return [
        d
        for sc in scenarios
        if (d := samples / "bundles" / f"{sc}-clean").exists() and (d / "manifest.json").exists()
    ]


def tamper_dirs(samples: Path, ids: Sequence[str], scenarios: Sequence[str]) -> list[Path]:
    out = []
    for tid in ids:
        for d in sorted((samples / "tampered").glob(f"{tid}-*-i0")):
            if (d / "manifest.json").exists() and _manifest(d)["use_case"] in scenarios:
                out.append(d)
    return out


def base_dir_of(tdir: Path) -> Path:
    rel = _manifest(tdir).get("base_bundle")
    if not rel:
        raise ValueError(f"{tdir.name} has no base_bundle")
    return (tdir / rel).resolve().parent


@dataclass
class Item:
    kind: str  # clean | tamper
    render: str
    mode: str
    bundle: str
    scenario: str
    dir: str
    base_dir: str | None = None
    tamper_id: str | None = None

    def meta(self) -> dict[str, Any]:
        return dict(self.__dict__)


def plan(
    samples: Path,
    sim_root: Path,
    *,
    renders: Sequence[str],
    modes: Sequence[str],
    scenarios: Sequence[str],
    tamper_ids: Sequence[str],
    tamper_renders: Sequence[str] | None = None,
    seed: int = DEFAULT_SEED,
    workers: int = 4,
    max_files: int | None = None,
    log: Any = print,
) -> tuple[list[Item], list[dict[str, str]]]:
    """Items to run and a list of ``{render, mode, why}`` entries that were left out."""
    skipped: list[dict[str, str]] = []
    items: list[Item] = []
    cleans = clean_dirs(samples, scenarios)
    tamps = tamper_dirs(samples, tamper_ids, scenarios)
    sim_renders = [r for r in renders if is_sim(r)]
    sim_tamp_renders = [r for r in sim_renders if tamper_renders is None or r in tamper_renders]
    sim_tamps = [t for t in tamps if TAMPERS[t.name.split("-")[0]].channel in SIM_CHANNELS]
    sim_map: dict[Path, Path] = {}
    if sim_renders:  # clean bundles get every requested level
        sim_map.update(
            ensure_sim(
                cleans,
                sim_root,
                sim_renders,
                seed=seed,
                workers=workers,
                max_files=max_files,
                log=log,
            )
        )
    if sim_tamp_renders and sim_tamps:  # tampered cases only the levels that will be tampered
        sim_map.update(
            ensure_sim(
                sim_tamps,
                sim_root,
                sim_tamp_renders,
                seed=seed,
                workers=workers,
                max_files=max_files,
                log=log,
            )
        )
    for render in renders:
        for mode in modes:
            if is_sim(render) and mode == real_ocr.ORACLE_MODE:
                skipped.append(
                    {"render": render, "mode": mode, "why": "no oracle for simulated renders"}
                )
                continue
            for d in cleans:
                dd = sim_map[d] if is_sim(render) else d
                if render not in _manifest(dd)["upload_order"]:
                    skipped.append({"render": render, "mode": mode, "why": f"{d.name} lacks it"})
                    continue
                man = _manifest(dd)
                items.append(Item("clean", render, mode, d.name, man["use_case"], str(dd)))
            for t in tamps:
                tid = t.name.split("-")[0]
                if tamper_renders is not None and render not in tamper_renders:
                    continue
                if is_sim(render):
                    if t not in sim_map:
                        skipped.append(
                            {"render": render, "mode": mode, "why": f"{tid}: channel not simulable"}
                        )
                        continue
                    td, bd = sim_map[t], sim_map[base_dir_of(t)]
                else:
                    td, bd = t, base_dir_of(t)
                    if (
                        render not in _manifest(td)["upload_order"]
                        or render not in _manifest(bd)["upload_order"]
                    ):
                        skipped.append(
                            {"render": render, "mode": mode, "why": f"{t.name} lacks it"}
                        )
                        continue
                items.append(
                    Item(
                        "tamper",
                        render,
                        mode,
                        t.name,
                        _manifest(t)["use_case"],
                        str(td),
                        base_dir=str(bd),
                        tamper_id=tid,
                    )
                )
    return items, skipped


def jobs_for(
    items: Sequence[Item], today: date, salt: str, limit_files: int | None = None
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    """Unique jobs and, for each item, the job keys ``(self, base)`` to look the results up."""
    jobs: dict[str, dict[str, Any]] = {}
    refs: list[tuple[str, str, str]] = []

    def add(d: str, render: str, mode: str, detail: bool) -> str:
        job = {
            "manifest": str(Path(d) / "manifest.json"),
            "render": render,
            "extraction": mode,
            "today": today.isoformat(),
            "classifier": "rules",
            "page_workers": 1,
            "detail": detail,
            "salt": salt,
        }
        if limit_files:
            job["only_paths"] = _manifest(Path(d))["upload_order"][render][:limit_files]
        key = json.dumps(job, sort_keys=True)
        jobs.setdefault(key, job)
        return key

    for it in items:
        own = add(it.dir, it.render, it.mode, it.kind == "clean")
        base = (
            add(it.base_dir, it.render, it.mode, True) if it.base_dir else ""
        )  # same key as the clean run
        refs.append((it.kind, own, base))
    return list(jobs.values()), refs


# ------------------------------------------------------------------------------ aggregation


def merge_documents(docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fragments with one ``doc_key`` (a statement uploaded as one JPEG per page) become one document:
    rows and table rows concatenated in file order, the first non-empty value of each field wins."""
    merged: dict[str, dict[str, Any]] = {}
    rank = {"ok": 0, "partial": 1, "failed": 2}
    out: list[dict[str, Any]] = []
    for d in docs:
        key = d.get("doc_key")
        if key is None or key not in merged:
            copy = {
                **d,
                "rows": list(d["rows"]),
                "fields": dict(d["fields"]),
                "tables": {k: list(v) for k, v in d["tables"].items()},
            }
            if key is not None:
                merged[key] = copy
            out.append(copy)
            continue
        m = merged[key]
        m["rows"] += d["rows"]
        for k, v in d["fields"].items():
            if m["fields"].get(k) in (None, "") and v not in (None, ""):
                m["fields"][k] = v
        for k, v in d["tables"].items():
            m["tables"].setdefault(k, []).extend(v)
        if rank.get(d["status"], 0) > rank.get(m["status"], 0):
            m["status"] = d["status"]
    return out


def extraction_row(pairs: Sequence[tuple[Item, dict[str, Any]]]) -> dict[str, Any]:
    """Extraction metrics pooled over the clean bundles of one (render, mode)."""
    st = {"truth": 0, "extracted": 0, "aligned": 0, "exact": 0, "stmts": 0, "found": 0, "chain": 0}
    fields_ok = fields_n = 0
    tables_ok = tables_n = 0
    c1_ok = c1_n = 0
    docs_found = docs_n = 0
    ocr_pages = ocr_err = 0
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for it, res in pairs:
        d = Path(it.dir)
        truth = read_json(d / "ground_truth.json")
        labels = read_json(d / "labels.json")
        detail = res["detail"]
        docs = merge_documents(detail["documents"])
        for m in ex.statement_metrics(docs, truth):
            st["stmts"] += 1
            st["truth"] += m["truth_rows"]
            st["extracted"] += m["extracted_rows"]
            st["aligned"] += m["aligned"]
            st["exact"] += m["exact"]
            st["found"] += 1 if m["found"] and m["extracted_rows"] else 0
            st["chain"] += 1 if m["chain_valid"] else 0
        for (dt, _f), (ok, n) in ex.field_accuracy(docs, truth).items():
            fields_ok, fields_n = fields_ok + ok, fields_n + n
            by_type[dt][0] += ok
            by_type[dt][1] += n
        for _k, (ok, n) in ex.table_recall(docs, truth).items():
            tables_ok, tables_n = tables_ok + ok, tables_n + n
        c1_ok_i, c1_n_i = ex.accuracy(ex.c1_pairs(detail["pages"], labels))
        c1_ok, c1_n = c1_ok + c1_ok_i, c1_n + c1_n_i
        have = {
            x["doc_key"] for x in docs if x["status"] != "failed" and (x["rows"] or x["fields"])
        }
        for gd in truth["documents"]:
            docs_n += 1
            docs_found += 1 if gd["key"] in have else 0
        o = res.get("ocr") or {}
        ocr_pages += o.get("pages", res.get("pages", 0))
        ocr_err += o.get("pages_with_error", 0)
    return {
        "n_bundles": len(pairs),
        "statements": st["stmts"],
        "row_recall": (st["aligned"], st["truth"]),
        "row_precision": (st["aligned"], st["extracted"]),
        "desc_exact": (st["exact"], st["aligned"]),
        "chain_valid": (st["chain"], st["stmts"]),
        "field_acc": (fields_ok, fields_n),
        "table_recall": (tables_ok, tables_n),
        "c1_acc": (c1_ok, c1_n),
        "docs_found": (docs_found, docs_n),
        "pages_unreadable": (ocr_err, ocr_pages),
        "field_acc_by_type": {k: tuple(v) for k, v in sorted(by_type.items())},
    }


def false_alarm_row(pairs: Sequence[tuple[Item, dict[str, Any]]]) -> dict[str, Any]:
    n_ge = n_any = fast = 0
    scores: list[float] = []
    covs: list[float] = []
    unreviewed = total_pages = 0
    rows = []
    rules: dict[str, int] = defaultdict(int)
    for it, res in pairs:
        cov = res.get("coverage") or {}
        unreviewed += cov.get("pages_unchecked") or 0
        total_pages += cov.get("pages_total") or 0
        man = _manifest(Path(it.dir))
        may = [t for q in man.get("quirks", []) for t in q.get("may_trigger", [])]
        bad, allowed = mx.false_alarm_flags(res["flags"], may)
        n_ge += len(bad)
        n_any += 1 if bad else 0
        fast += 1 if res["lane"] == "fast" else 0
        if res["score"] is not None:
            scores.append(res["score"])
        if res["coverage_ratio"] is not None:
            covs.append(res["coverage_ratio"])
        for f in bad:
            rules[f["rule"]] += 1
        rows.append(
            {
                "bundle": it.bundle,
                "ge_medium": len(bad),
                "rules": sorted({f["rule"] for f in bad}),
                "score": res["score"],
                "lane": res["lane"],
                "coverage": res["coverage_ratio"],
                "partial": res["partial"],
            }
        )
    return {
        "n_clean": len(pairs),
        "bundles_with_false_alarm": (n_any, len(pairs)),
        "flags_ge_medium": n_ge,
        "per_10": mx.per_ten(n_ge, len(pairs)),
        "fast_lane": (fast, len(pairs)),
        "mean_score": mx.mean(scores),
        "mean_coverage": mx.mean(covs),
        "pages_not_reviewed": (unreviewed, total_pages),
        "rules": dict(sorted(rules.items(), key=lambda kv: -kv[1])),
        "rows": rows,
    }


def recall_records(
    pairs: Sequence[tuple[Item, dict[str, Any], dict[str, Any]]],
) -> list[dict[str, Any]]:
    recs = []
    for it, a, b in pairs:
        inst = {
            "tamper_id": it.tamper_id,
            "scenario": it.scenario,
            "instance": 0,
            "dir": it.dir,
            "base_dir": it.base_dir,
            "renders": [it.render],
        }
        try:
            rec = suites.score_instance(inst, it.render, a, b)
        except Exception as exc:  # a scoring problem is reported, not hidden
            rec = {
                "tamper_id": it.tamper_id,
                "render": it.render,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "scenario": it.scenario,
            }
        rec["mode"] = it.mode
        rec.pop("detail", None)
        recs.append(rec)
    return recs


def recall_table(recs: Sequence[dict[str, Any]], ids: Sequence[str]) -> dict[str, Any]:
    return suites.recall_tables(list(recs), [], list(ids), registered_rules())


# ------------------------------------------------------------------------------ running


def run(args: argparse.Namespace) -> int:
    samples = args.samples.resolve()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    log = lambda s: print(s, file=sys.stderr, flush=True)  # noqa: E731
    sim_root = args.sim_root.resolve()
    if args.limit_files:  # a truncated simulated bundle must never be reused by a full run
        sim_root = sim_root / f"smoke-{args.limit_files}"
    items, skipped = plan(
        samples,
        sim_root,
        renders=args.renders,
        modes=args.modes,
        scenarios=args.scenarios,
        tamper_ids=args.tampers,
        tamper_renders=args.tamper_renders,
        seed=args.seed,
        workers=args.workers,
        max_files=args.limit_files,
        log=log,
    )
    jobs, refs = jobs_for(items, args.today, code_salt(), args.limit_files)
    log(f"{len(items)} items, {len(jobs)} unique analyses")
    cfg = suites.Config(
        samples=samples, out=out, workers=args.workers, use_cache=not args.no_cache, log=log
    )
    results = suites.run_cached(cfg, jobs, fn=real_ocr.run_job, label="real-ocr eval")
    by_key = {json.dumps(j, sort_keys=True): r for j, r in zip(jobs, results, strict=True)}
    payload = aggregate(items, refs, by_key, args.tampers, skipped)
    payload["smoke"] = args.limit_files
    write_outputs(out, payload)
    log(f"wrote {out}")
    return 0


def aggregate(
    items: Sequence[Item],
    refs: Sequence[tuple[str, str, str]],
    by_key: dict[str, dict[str, Any]],
    tamper_ids: Sequence[str],
    skipped: Sequence[dict[str, str]],
) -> dict[str, Any]:
    clean: dict[tuple[str, str], list[tuple[Item, dict[str, Any]]]] = defaultdict(list)
    tamp: dict[tuple[str, str], list[tuple[Item, dict[str, Any], dict[str, Any]]]] = defaultdict(
        list
    )
    errors: list[dict[str, Any]] = []
    for it, (kind, own, base) in zip(items, refs, strict=True):
        a = by_key[own]
        if not a.get("ok"):
            errors.append(
                {"bundle": it.bundle, "render": it.render, "mode": it.mode, "error": a.get("error")}
            )
            continue
        if kind == "clean":
            clean[(it.render, it.mode)].append((it, a))
        else:
            b = by_key[base]
            if not b.get("ok"):
                errors.append(
                    {
                        "bundle": it.bundle + " (base)",
                        "render": it.render,
                        "mode": it.mode,
                        "error": b.get("error"),
                    }
                )
                continue
            tamp[(it.render, it.mode)].append((it, a, b))
    payload: dict[str, Any] = {
        "extraction": {},
        "false_alarms": {},
        "recall": {},
        "errors": errors,
        "skipped": list(skipped),
        "limits": LIMITS,
    }
    for (render, mode), pairs in sorted(clean.items()):
        k = f"{render}|{mode}"
        payload["extraction"][k] = extraction_row(pairs)
        payload["false_alarms"][k] = false_alarm_row(pairs)
    for (render, mode), triples in sorted(tamp.items()):
        recs = recall_records(triples)
        payload["recall"][f"{render}|{mode}"] = {
            "records": recs,
            "table": recall_table([r for r in recs if r.get("ok") is not None], tamper_ids),
        }
    return payload


# ------------------------------------------------------------------------------ output


def _rate(pair: Sequence[int]) -> str:
    k, n = pair
    return mx.rate_text(k, n)


def _pct(pair: Sequence[int]) -> str:
    k, n = pair
    return "n/a" if n == 0 else f"{k / n:.3f}"


def write_outputs(out: Path, payload: dict[str, Any]) -> None:
    (out / "results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=list) + "\n", encoding="utf-8"
    )
    with (out / "extraction.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "render",
                "mode",
                "bundles",
                "row_recall",
                "row_precision",
                "desc_exact",
                "chain_valid",
                "field_acc",
                "table_recall",
                "c1_acc",
                "docs_found",
                "pages_unreadable",
            ]
        )
        for k, r in payload["extraction"].items():
            render, mode = k.split("|")
            w.writerow(
                [
                    render,
                    mode,
                    r["n_bundles"],
                    *[
                        _pct(r[c])
                        for c in (
                            "row_recall",
                            "row_precision",
                            "desc_exact",
                            "chain_valid",
                            "field_acc",
                            "table_recall",
                            "c1_acc",
                            "docs_found",
                        )
                    ],
                    _pct(r["pages_unreadable"]),
                ]
            )
    with (out / "false_alarms.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["render", "mode", "bundle", "flags_ge_medium", "rules", "score", "lane", "coverage"]
        )
        for k, r in payload["false_alarms"].items():
            render, mode = k.split("|")
            for row in r["rows"]:
                w.writerow(
                    [
                        render,
                        mode,
                        row["bundle"],
                        row["ge_medium"],
                        "+".join(row["rules"]),
                        row["score"],
                        row["lane"],
                        row["coverage"],
                    ]
                )
    with (out / "recall.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["render", "mode", "tamper_id", "n", "caught", "recall", "wilson_lo", "wilson_hi"]
        )
        for k, r in payload["recall"].items():
            render, mode = k.split("|")
            for row in r["table"]["rows"]:
                if row.get("status") == "measured":
                    w.writerow(
                        [
                            render,
                            mode,
                            row["tamper_id"],
                            row["n"],
                            row["caught"],
                            f"{row['recall']:.3f}",
                            f"{row['wilson_lo']:.3f}",
                            f"{row['wilson_hi']:.3f}",
                        ]
                    )
    (out / "real-ocr.md").write_text(render_markdown(payload), encoding="utf-8")


def render_markdown(p: dict[str, Any]) -> str:
    L: list[str] = []
    L.append("# Real OCR versus oracle extraction, and simulated print-scan renders\n")
    L.append(
        "Generated by `backend/scripts/ext_eval/real_ocr_eval.py`. Fictitious data from our own generator, "
        "Fake LLM client, no network. `scan_sim_*` rows are SIMULATED (a physical print-and-scan was not "
        "possible). Nothing was tuned on these results.\n"
    )
    if p.get("smoke"):
        L.append(
            f"**SMOKE RUN: only the first {p['smoke']} uploads of each case were analysed, so recall and "
            "docs-found denominators include documents that were never uploaded. Do not quote these numbers.**\n"
        )
    L.append("## Limits that apply to every number below\n")
    L += [f"- {x}" for x in p["limits"]]
    L.append("\n## Extraction quality (clean bundles; fragments merged by document key)\n")
    L.append(
        "| Render | Mode | Docs found | Stmt row recall | Row precision | Desc exact | Chain valid | Field acc | C1 acc | Pages unreadable |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for k, r in p["extraction"].items():
        render, mode = k.split("|")
        L.append(
            f"| {render} | {mode} | {_rate(r['docs_found'])} | {_rate(r['row_recall'])} | "
            f"{_rate(r['row_precision'])} | {_rate(r['desc_exact'])} | {_rate(r['chain_valid'])} | "
            f"{_rate(r['field_acc'])} | {_rate(r['c1_acc'])} | {_rate(r['pages_unreadable'])} |"
        )
    L.append("\nField accuracy by document type:\n")
    L.append(
        "| Render | Mode | "
        + " | ".join(sorted({t for r in p["extraction"].values() for t in r["field_acc_by_type"]}))
        + " |"
    )
    types = sorted({t for r in p["extraction"].values() for t in r["field_acc_by_type"]})
    L.append("|---|---|" + "---|" * len(types))
    for k, r in p["extraction"].items():
        render, mode = k.split("|")
        L.append(
            f"| {render} | {mode} | "
            + " | ".join(_pct(r["field_acc_by_type"].get(t, (0, 0))) for t in types)
            + " |"
        )
    L.append("\n## False alarms on clean bundles (high or medium flags, quirk-allowed excluded)\n")
    L.append(
        "| Render | Mode | Clean bundles | With a false alarm | Flags per 10 | Fast lane | Mean score | Mean coverage | Pages not reviewed | Rules firing |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for k, r in p["false_alarms"].items():
        render, mode = k.split("|")
        top = sorted(r["rules"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        rules = ", ".join(f"{a} x{b}" for a, b in top) or "-"
        L.append(
            f"| {render} | {mode} | {r['n_clean']} | {_rate(r['bundles_with_false_alarm'])} | "
            f"{r['per_10']:.1f} | {_rate(r['fast_lane'])} | "
            f"{(r['mean_score'] if r['mean_score'] is not None else float('nan')):.1f} | "
            f"{(r['mean_coverage'] if r['mean_coverage'] is not None else float('nan')):.2f} | "
            f"{_pct(r['pages_not_reviewed']) if r.get('pages_not_reviewed') else 'n/a'} | {rules} |"
        )
    L.append(
        "\nNo flags with low coverage is not a clean result: a render the tool could barely read (for "
        "example a heavy simulated render) raises nothing because there was little to check. Read the "
        "false-alarm columns together with coverage and pages not reviewed.\n"
    )
    L.append(
        "\n## Tamper recall (new flag of an expected rule relative to the same render of the base bundle)\n"
    )
    L.append(
        "| Render | Mode | Tamper | Cases n | Caught | Recall (Wilson 95%) | Missed: rule did not run | Missed: ran |"
    )
    L.append("|---|---|---|---|---|---|---|---|")
    for k, r in p["recall"].items():
        render, mode = k.split("|")
        for row in r["table"]["rows"]:
            if row.get("status") != "measured":
                continue
            L.append(
                f"| {render} | {mode} | {row['tamper_id']} | {row['n']} | {row['caught']} | "
                f"{row['recall']:.2f} [{row['wilson_lo']:.2f}-{row['wilson_hi']:.2f}] | "
                f"{row['missed_not_run']} | {row['missed_ran']} |"
            )
    L.append("\nPooled over measured tamper ids:\n")
    L.append("| Render | Mode | n | Caught | Recall (Wilson 95%) |")
    L.append("|---|---|---|---|---|")
    for k, r in p["recall"].items():
        render, mode = k.split("|")
        rows = [x for x in r["table"]["rows"] if x.get("status") == "measured"]
        n = sum(x["n"] for x in rows)
        c = sum(x["caught"] for x in rows)
        L.append(f"| {render} | {mode} | {n} | {c} | {mx.rate_text(c, n)} |")
    if p["errors"]:
        L.append("\n## Harness errors (not hidden)\n")
        L += [f"- {e['bundle']} {e['render']} {e['mode']}: {e['error']}" for e in p["errors"]]
    if p["skipped"]:
        L.append("\n## Planned but not run\n")
        seen = set()
        for s in p["skipped"]:
            key = (s["render"], s["mode"], s["why"])
            if key not in seen:
                seen.add(key)
                L.append(f"- {s['render']} / {s['mode']}: {s['why']}")
    return "\n".join(L) + "\n"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="plan, run and report")
    r.add_argument("--samples", type=Path, default=REPO / "samples")
    r.add_argument("--sim-root", type=Path, default=REPO / "samples" / "sim")
    r.add_argument("--out", type=Path, default=REPO / "docs" / "eval-results" / "real-ocr")
    r.add_argument(
        "--renders",
        nargs="+",
        default=list(DEFAULT_RENDERS),
        choices=[*STANDARD_RENDERS, *SIM_RENDERS, *SIM_JPEG_RENDERS],
    )
    r.add_argument("--modes", nargs="+", default=list(real_ocr.MODES), choices=list(real_ocr.MODES))
    r.add_argument(
        "--scenarios", nargs="+", default=["loan", "kyc", "aml"], choices=["loan", "kyc", "aml"]
    )
    r.add_argument(
        "--tampers", nargs="*", default=DEFAULT_TAMPERS, help="tamper ids; none = clean only"
    )
    r.add_argument(
        "--tamper-renders",
        nargs="*",
        default=None,
        help="renders that also get the tampered cases (default: all requested)",
    )
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--seed", type=int, default=DEFAULT_SEED)
    r.add_argument("--today", type=date.fromisoformat, default=FIXED_TODAY)
    r.add_argument(
        "--limit-files", type=int, default=None, help="smoke test: first N uploads per case"
    )
    r.add_argument("--no-cache", action="store_true")
    rep = sub.add_parser("report", help="re-render the tables from an existing results.json")
    rep.add_argument("--results", type=Path, required=True)
    rep.add_argument("--out", type=Path, default=None, help="default: next to results.json")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "report":
        payload = json.loads(args.results.read_text(encoding="utf-8"))
        payload["limits"] = LIMITS  # current wording; the numbers are the stored ones
        write_outputs(args.out or args.results.parent, payload)
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
