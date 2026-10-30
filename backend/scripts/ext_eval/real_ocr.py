"""Real-OCR evaluation mode: scan, photo and simulated renders go through the repo's own local OCR
(Apple Vision via ``ocrmac``, ``app/ingest/ocr_backends.py``) instead of the generator's oracle.

How it plugs in without editing ``backend/app``:

* ``Settings(ocr_backend="vision")`` makes ``ingest.ocr.ocr_page_ex`` read the pixels with the local
  backend (the same path a text-only deployment takes), so classification runs on real OCR text.
* There is no LLM here (Fake client, no fixture), so the page reading step cannot be the vision
  model. Instead ``extractor_mode="text_layer"`` is used and ``cascade.page_state`` is wrapped in
  this process so that OCR pages are not flagged ``is_scan``: the repo's deterministic text-layer
  extractors then parse the OCR words (boxes are the OCR line boxes cut proportionally per token,
  ``ingest/ocr.py:lines_to_words``). This measures "OCR + deterministic layout parser". It is NOT the
  production vision-LLM path (no key was available), so a production run with a vision model can
  score higher or lower; the numbers are a conservative stand-in, and any page whose layout the
  parser cannot recognise comes out unreadable (counted, never hidden).

``run_job`` is a process-pool entry point with the same job shape as ``_eval.runner.run_job``;
``extraction="oracle"`` is delegated to the unchanged runner so the oracle rows are exactly the
existing eval's numbers.
"""

from __future__ import annotations

import contextlib
import dataclasses
import shutil
import sys
import tempfile
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts._eval import runner  # noqa: E402
from scripts._eval.common import read_json  # noqa: E402

OCR_MODE = "ocr"
ORACLE_MODE = "oracle"
MODES = (ORACLE_MODE, OCR_MODE)

OCR_SENTENCE = (
    "Real-OCR rows: the page pixels are read by Apple Vision (ocrmac) and parsed by the repo's "
    "deterministic text-layer extractors; no language model is involved (Fake client). This is a "
    "stand-in for, not a measurement of, the production vision-model path."
)


def make_ocr_settings(root: Path, classifier: str = "rules", page_workers: int = 1) -> Any:
    """Eval settings with the local OCR backend forced on and the deterministic extractors only."""
    base = runner.make_settings(
        root, classifier, extractor_mode="text_layer", page_workers=page_workers
    )
    return base.model_copy(update={"ocr_backend": "vision"})


@contextlib.contextmanager
def ocr_words_as_text_layer() -> Iterator[None]:
    """Let the text-layer extractors read OCR words (patches ``cascade.page_state`` in-process).

    The cascade skips the text-layer extractor when any page ``is_scan``; the OCR words are in
    ``ctx.page_words`` exactly like text-layer words, so reporting the page as not a scan is all
    that is needed. Restored on exit."""
    from app.extract import cascade

    original = cascade.page_state

    def patched(doc: Any, ctx: Any, page: int) -> Any:
        state = original(doc, ctx, page)
        return dataclasses.replace(state, is_scan=False) if state.is_scan else state

    cascade.page_state = patched
    try:
        yield
    finally:
        cascade.page_state = original


def raw_doc_key_map(manifest_path: Path) -> dict[tuple[str, int], str | None]:
    """``(file sha256, page) -> doc_key`` read as plain JSON (simulated manifests carry render names
    the generator's pydantic ``Render`` literal does not know, so ``load_manifest`` would refuse)."""
    man = read_json(manifest_path)
    return {
        (f["sha256"], int(p["page"])): p.get("doc_key") for f in man["files"] for p in f["pages"]
    }


def _ocr_stats(case: Any) -> dict[str, Any]:
    errors: dict[str, int] = {}
    pages = 0
    for f in case.files:
        for p in f.pages:
            pages += 1
            if p.error:
                kind = p.error.split(":", 1)[0]
                errors[kind] = errors.get(kind, 0) + 1
    return {"pages": pages, "pages_with_error": sum(errors.values()), "errors": errors}


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry. ``job``: manifest, render, extraction (``ocr`` | ``oracle``), classifier,
    today, detail, registry, only_paths. Never raises: a failure is returned as ``ok=False``."""
    if job.get("extraction", OCR_MODE) == ORACLE_MODE:
        return runner.run_job(job)
    root = Path(tempfile.mkdtemp(prefix="gldb-ocr-eval-"))
    started = time.perf_counter()
    try:
        settings = make_ocr_settings(
            root, job.get("classifier", "rules"), int(job.get("page_workers", 1))
        )
        db = runner.new_db(settings)
        manifest = Path(job["manifest"])
        render = job["render"]
        paths = None
        if job.get("only_paths"):
            paths = [(manifest.parent / p).resolve() for p in job["only_paths"]]
        with ocr_words_as_text_layer():
            case = runner.analyse_manifest(
                manifest,
                render=render,
                db=db,
                settings=settings,
                extraction=OCR_MODE,
                today=date.fromisoformat(job["today"]),
                registry_path=Path(job["registry"]) if job.get("registry") else None,
                paths=paths,
            )
        original = runner._doc_key_map
        runner._doc_key_map = raw_doc_key_map
        try:
            out = runner.summarise_case(
                case, manifest, render=render, mode=OCR_MODE, detail=bool(job.get("detail"))
            )
        finally:
            runner._doc_key_map = original
        out["ocr"] = _ocr_stats(case)
        out["wall_seconds"] = round(time.perf_counter() - started, 3)
        out["ok"] = True
        return out
    except Exception as exc:  # a harness error is reported per job, never swallowed silently
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "manifest": str(job.get("manifest")),
            "render": job.get("render"),
            "extraction_mode": job.get("extraction", OCR_MODE),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)
