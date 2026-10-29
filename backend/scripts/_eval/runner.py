"""Analyse a manifest through the same code path as ``POST /cases`` and summarise the case.

``analyse_manifest`` never re-implements upload (it calls ``run_case.analyse_files``). A fresh SQLite
database is used per experiment; ``today`` is injected; no network and no API key (FakeLLMClient,
with the generator's ground truth as the oracle on scans).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts._eval.common import load_manifest, read_json
from scripts._gen.schema import FIXED_TODAY

_ROW_COLS = ("date", "debit", "credit", "balance", "description")


def make_settings(
    root: Path, classifier: str = "rules", extractor_mode: str | None = None, page_workers: int = 4
) -> Any:
    from app.core.config import Settings

    kwargs: dict[str, Any] = dict(
        _env_file=None,
        data_dir=root / "data",
        db_path=str(root / "data" / "gldb.sqlite3"),
        llm_backend="fake",
        classifier_mode=classifier,
        max_llm_calls_per_case=5000,
        max_files_per_case=1000,
        max_pages_per_case=1000,
        max_page_workers=page_workers,
    )
    if extractor_mode:
        kwargs["extractor_mode"] = extractor_mode
    return Settings(**kwargs)


def new_db(settings: Any) -> Any:
    from app.core.db import Database

    db = Database(settings.database_path, data_dir=settings.data_dir)
    db.init_schema()
    return db


def manifest_files(manifest_path: Path, render: str) -> list[Path]:
    man = read_json(manifest_path)
    rels = man["upload_order"].get(render)
    if rels is None:
        raise KeyError(f"{manifest_path.parent.name} has no '{render}' render")
    return [(manifest_path.parent / rel).resolve() for rel in rels]


def manifest_registry(manifest_path: Path, override: Path | None = None) -> Any:
    from app.core.registry import load_registry

    if override is not None and override.exists():
        return load_registry(override)
    man = read_json(manifest_path)
    rel = man.get("registry_path")
    if not rel:
        return None
    path = (manifest_path.parent / rel).resolve()
    return load_registry(path) if path.exists() else None


def extraction_mode_for(render: str, extraction: str) -> str:
    if extraction == "auto":
        return "text_layer" if render == "digital" else "oracle"
    return extraction


def analyse_manifest(
    manifest_path: Path,
    *,
    render: str,
    db: Any,
    settings: Any,
    extraction: str = "auto",
    today: date = FIXED_TODAY,
    registry_path: Path | None = None,
    paths: list[Path] | None = None,
) -> Any:
    """Upload the manifest's ``upload_order`` files for ``render`` as one case and analyse it."""
    from app.core.llm import FakeLLMClient
    from scripts.run_case import analyse_files

    mode = extraction_mode_for(render, extraction)
    if mode == "llm":
        raise RuntimeError("extraction=llm needs an API key; not run in this harness")
    llm = FakeLLMClient.from_ground_truth(manifest_path) if mode == "oracle" else FakeLLMClient()
    man = read_json(manifest_path)
    files = paths if paths is not None else manifest_files(manifest_path, render)
    case = analyse_files(
        files,
        use_case=man["use_case"],
        today=today,
        db=db,
        settings=settings,
        llm=llm,
        registry=manifest_registry(manifest_path, registry_path),
    )
    return db.load_case(case.id)


# ------------------------------------------------------------------------------ summaries


def _doc_key_map(manifest_path: Path) -> dict[tuple[str, int], str | None]:
    m = load_manifest(manifest_path)
    out: dict[tuple[str, int], str | None] = {}
    for f in m.files:
        for p in f.pages:
            out[(f.sha256, p.page)] = p.doc_key
    return out


def summarise_case(
    case: Any, manifest_path: Path, *, render: str, mode: str, detail: bool = False
) -> dict[str, Any]:
    """A JSON-serialisable digest of a case: flags (with document keys, rows, regions), score,
    lane, coverage, per-stage seconds and dollars, check runs."""
    keys = _doc_key_map(manifest_path)
    sha_of = {f.id: f.sha256 for f in case.files}
    docs = {d.id: d for d in case.documents}

    def doc_key(doc_id: str) -> str | None:
        d = docs.get(doc_id)
        if d is None:
            return None
        return keys.get((sha_of.get(d.file_id, ""), d.page_start))

    def region(r: Any) -> dict[str, Any]:
        return {
            "sha": sha_of.get(r.file_id),
            "page": r.page,
            "x": r.x,
            "y": r.y,
            "w": r.w,
            "h": r.h,
        }

    flags: list[dict[str, Any]] = []
    for f in case.flags:
        dk = sorted({k for k in (doc_key(i) for i in f.document_ids) if k})
        row_sig = None
        if f.row is not None and f.table and f.document_ids:
            d = docs.get(f.document_ids[0])
            tbl = d.tables.get(f.table) if d else None
            if tbl is not None:
                row = next((r for r in tbl.rows if r.index == f.row), None)
                if row is not None:
                    row_sig = {c: row.cells[c].value for c in _ROW_COLS if c in row.cells}
        flags.append(
            {
                "id": f.id,
                "rule": f.rule,
                "severity": f.severity,
                "stage": f.stage,
                "doc_keys": dk,
                "table": f.table,
                "field": f.field,
                "row": f.row,
                "row_sig": row_sig,
                "regions": [
                    region(r) for r in ([f.region] if f.region else []) + list(f.extra_regions)
                ],
                "has_evidence": bool(f.evidence and f.explanation),
            }
        )
    stages = {
        s.stage: {"seconds": round(s.seconds or 0.0, 4), "usd": str(s.cost_usd)}
        for s in case.stages
    }
    usd = sum((c.usd for c in case.costs), Decimal("0"))
    pages = sum(len(f.pages) for f in case.files)
    out: dict[str, Any] = {
        "case_id": case.id,
        "bundle_id": manifest_path.parent.name,
        "manifest": str(manifest_path),
        "render": render,
        "extraction_mode": mode,
        "status": case.status,
        "score": case.score.value if case.score else None,
        "band": case.score.band if case.score else None,
        "lane": case.lane,
        "partial": case.partial,
        "partial_reasons": list(case.partial_reasons),
        "coverage_ratio": case.coverage.ratio if case.coverage else None,
        "coverage": case.coverage.model_dump(mode="json") if case.coverage else None,
        "findings": len(case.findings),
        "flags": flags,
        "check_runs": {r.rule: {"status": r.status, "reason": r.reason} for r in case.check_runs},
        "stages": stages,
        "seconds": round(sum(s.seconds or 0.0 for s in case.stages), 4),
        "usd": str(usd),
        "model_calls": len([c for c in case.costs if c.model != "fake"]),
        "pages": pages,
        "ruleset_version": case.provenance.ruleset_version if case.provenance else None,
        "flags_digest": case.provenance.flags_digest if case.provenance else None,
        "applicant": case.applicant.model_dump(mode="json"),
        "files_n": len(case.files),
    }
    if detail:
        out["detail"] = _detail(case, keys, sha_of)
    return out


def _detail(
    case: Any, keys: dict[tuple[str, int], str | None], sha_of: dict[str, str]
) -> dict[str, Any]:
    pages = []
    for f in case.files:
        for p in f.pages:
            cls = p.classification
            pages.append(
                {
                    "sha": f.sha256,
                    "page": p.page,
                    "label": cls.label if cls else None,
                    "prob": cls.probability if cls else None,
                }
            )
    docs = []
    for d in case.documents:
        rows = []
        tbl = d.tables.get("transactions")
        if d.doc_type == "bank_statement" and tbl is not None:
            for r in tbl.rows:
                rows.append(
                    {
                        "index": r.index,
                        **{c: r.cells[c].value for c in _ROW_COLS if c in r.cells},
                        "c4": r.annotations["c4"].label if "c4" in r.annotations else None,
                        "c5": r.annotations["c5"].label if "c5" in r.annotations else None,
                    }
                )
        docs.append(
            {
                "doc_key": keys.get((sha_of.get(d.file_id, ""), d.page_start)),
                "doc_type": d.doc_type,
                "status": d.status,
                "c3": d.issuer.label if d.issuer else None,
                "page_start": d.page_start,
                "fields": {k: c.value for k, c in d.fields.items()},
                "tables": {
                    name: [
                        {c: cell.value for c, cell in r.cells.items()}
                        for r in t.rows
                        if name != "transactions"
                    ]
                    for name, t in d.tables.items()
                    if name != "transactions"
                },
                "rows": rows,
            }
        )
    return {"pages": pages, "documents": docs}


# ------------------------------------------------------------------------------ job entry


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry point. ``job``: manifest, render, extraction, classifier, today,
    ``prior`` (manifest analysed first in the same database, for T16c), ``detail``, ``registry``,
    ``only_paths`` (restrict the upload to these manifest-relative paths)."""
    root = Path(tempfile.mkdtemp(prefix="gldb-eval-"))
    started = time.perf_counter()
    try:
        settings = make_settings(
            root, job.get("classifier", "rules"), page_workers=int(job.get("page_workers", 1))
        )
        db = new_db(settings)
        manifest = Path(job["manifest"])
        render = job["render"]
        today = date.fromisoformat(job["today"])
        registry = Path(job["registry"]) if job.get("registry") else None
        if job.get("prior"):
            analyse_manifest(
                Path(job["prior"]),
                render=render,
                db=db,
                settings=settings,
                extraction=job.get("extraction", "auto"),
                today=today,
                registry_path=registry,
            )
        paths = None
        if job.get("only_paths"):
            paths = [(manifest.parent / p).resolve() for p in job["only_paths"]]
        if job.get("extra_files"):
            paths = (paths if paths is not None else manifest_files(manifest, render)) + [
                Path(p) for p in job["extra_files"]
            ]
        case = analyse_manifest(
            manifest,
            render=render,
            db=db,
            settings=settings,
            extraction=job.get("extraction", "auto"),
            today=today,
            registry_path=registry,
            paths=paths,
        )
        mode = extraction_mode_for(render, job.get("extraction", "auto"))
        out = summarise_case(
            case, manifest, render=render, mode=mode, detail=bool(job.get("detail"))
        )
        out["wall_seconds"] = round(time.perf_counter() - started, 3)
        out["ok"] = True
        return out
    except Exception as exc:  # a harness error is reported per job, never swallowed silently
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "manifest": str(job.get("manifest")),
            "render": job.get("render"),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_pool(
    fn: Callable[[dict[str, Any]], dict[str, Any]], jobs: list[dict[str, Any]], workers: int = 6
) -> list[dict[str, Any]]:
    """Run ``fn`` over jobs in a process pool (results in job order). ``workers <= 1`` runs inline."""
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, jobs, chunksize=1))


def run_jobs(jobs: list[dict[str, Any]], workers: int = 6) -> list[dict[str, Any]]:
    return run_pool(run_job, jobs, workers)


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
