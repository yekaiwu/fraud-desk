"""One scanned bundle through the real pipeline, scored field by field against the generator's truth.

Tesseract reads the page images on this machine; a model turns the OCR text into fields. The model is
the local OpenAI-compatible server (LM Studio, or the gateway and llama-server pair) or, once for a
comparison, the hosted Haiku read through ``Settings(_env_file=...)`` (the key is never printed). A third
mode, ``rules``, runs no model at all: the deterministic parsers read the OCR words.

    uv run python backend/scripts/local_scan_bench.py BUNDLE_DIR OUT.json [RENDER] --mode local|haiku|rules

Output: one JSON with seconds, tokens, per-call log (page, prompt and completion tokens, seconds, the
text sent and the model's answer, so the OCR text and the answer can be read side by side) and every
truth field with the value read. Use fictitious bundles only: LM Studio keeps full prompts in its log.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.core.config import Settings  # noqa: E402
from app.core.llm import FakeLLMClient, get_llm_client  # noqa: E402
from scripts._eval import extraction as ex  # noqa: E402
from scripts._eval import runner  # noqa: E402
from scripts.ext_eval import real_ocr, real_ocr_eval  # noqa: E402
from scripts.run_case import analyse_files  # noqa: E402

LOCAL_URL = "http://127.0.0.1:1234/v1"
LOCAL_MODEL = "qwen3.8-27b"


class LoggingClient:
    """Wraps a client and records every call (text sent, answer, tokens, seconds)."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def complete_json(self, req: Any, *, ctx_costs: Any = None) -> Any:
        t0 = time.perf_counter()
        error = None
        res = None
        try:
            res = self.inner.complete_json(req, ctx_costs=ctx_costs)
            return res
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            raise
        finally:
            with self._lock:
                self.calls.append(
                    {
                        "purpose": req.purpose,
                        "cache_extra": req.cache_extra,
                        "seconds": round(time.perf_counter() - t0, 2),
                        "user": req.user,
                        "answer": res.data if res is not None else None,
                        "prompt_tokens": res.cost.prompt_tokens if res is not None else None,
                        "completion_tokens": res.cost.completion_tokens
                        if res is not None
                        else None,
                        "error": error,
                    }
                )


def make_settings(mode: str, root: Path, env_file: str | None, extra: dict[str, Any]) -> Settings:
    common: dict[str, Any] = dict(
        data_dir=root / "data",
        db_path=str(root / "data" / "gldb.sqlite3"),
        classifier_mode="rules",
        semantic="off",
        agents=False,
        verify_scan_flags=False,
        cache_enabled=False,
        max_page_workers=1,
        max_files_per_case=1000,
        max_pages_per_case=1000,
        max_llm_calls_per_case=400,
        max_usd_per_case=Decimal("5"),
        max_usd_per_day=Decimal("50"),
        ocr_backend="tesseract",
    )
    if mode == "local":
        common.update(
            llm_backend="openai",
            llm_base_url=LOCAL_URL,
            llm_api_key="local",
            llm_model_cheap=LOCAL_MODEL,
            llm_model_strong=LOCAL_MODEL,
            llm_timeout_s=900.0,
            llm_enable_thinking=False,
            llm_supports_vision=False,
            llm_max_concurrency=1,
        )
        common.update(extra)
        s = Settings(_env_file=None, **common)
    elif mode == "haiku":
        common.update(llm_backend="anthropic", llm_vision_fallback=False)
        common.update(extra)
        s = Settings(_env_file=env_file, **common)
    else:  # rules: no model
        common.update(llm_backend="fake", extractor_mode="text_layer")
        common.update(extra)
        s = Settings(_env_file=None, **common)
    s.validate_startup()
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("out")
    ap.add_argument("render", nargs="?", default="scan_hq")
    ap.add_argument("--mode", choices=["local", "haiku", "rules"], default="local")
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--set", action="append", default=[], help="setting=value override")
    a = ap.parse_args()
    extra: dict[str, Any] = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        extra[k] = {"true": True, "false": False}.get(v.lower(), v)
    bundle, out = Path(a.bundle), Path(a.out)
    root = Path(tempfile.mkdtemp(prefix="gldb-bench-"))
    s = make_settings(a.mode, root, a.env_file, extra)
    db = runner.new_db(s)
    man = bundle / "manifest.json"
    files = runner.manifest_files(man, a.render)
    mj = runner.read_json(man)
    llm: Any = FakeLLMClient() if a.mode == "rules" else get_llm_client(s)
    logged = LoggingClient(llm)
    reg = bundle.parent / "registry.json"
    if not reg.exists():
        reg = bundle.parent.parent / "registry.json"
    t0 = time.perf_counter()
    if a.mode == "rules":
        with real_ocr.ocr_words_as_text_layer():
            case = analyse_files(
                files,
                use_case=mj["use_case"],
                today=date(2026, 10, 27),
                db=db,
                settings=s,
                llm=logged,
                registry=runner.manifest_registry(man, reg if reg.exists() else None),
            )
    else:
        case = analyse_files(
            files,
            use_case=mj["use_case"],
            today=date(2026, 10, 27),
            db=db,
            settings=s,
            llm=logged,
            registry=runner.manifest_registry(man, reg if reg.exists() else None),
        )
    wall = time.perf_counter() - t0
    case = db.load_case(case.id)
    orig = runner._doc_key_map
    runner._doc_key_map = real_ocr.raw_doc_key_map
    try:
        res = runner.summarise_case(case, man, render=a.render, mode=real_ocr.OCR_MODE, detail=True)
    finally:
        runner._doc_key_map = orig
    truth = runner.read_json(bundle / "ground_truth.json")
    labels = runner.read_json(bundle / "labels.json")
    docs = real_ocr_eval.merge_documents(res["detail"]["documents"])
    by_key = {d["doc_key"]: d for d in docs}
    fa = ex.field_accuracy(docs, truth)
    fok, fn = sum(v[0] for v in fa.values()), sum(v[1] for v in fa.values())
    tr = ex.table_recall(docs, truth)
    tok, tn = sum(v[0] for v in tr.values()), sum(v[1] for v in tr.values())
    c1ok, c1n = ex.accuracy(ex.c1_pairs(res["detail"]["pages"], labels))
    fields = []
    for gd in truth["documents"]:
        got_doc = by_key.get(gd["key"])
        for name, f in gd["fields"].items():
            tv = f.get("value")
            if tv is None:
                continue
            got = got_doc["fields"].get(name) if got_doc else None
            fields.append(
                {
                    "doc": gd["key"],
                    "type": gd["doc_type"],
                    "field": name,
                    "truth": tv,
                    "got": got,
                    "ok": bool(got_doc) and ex._eq(got, tv),
                }
            )
    calls = [c for c in case.costs if c.purpose and c.model != "fake"]
    ocr_pages = sum(1 for f in case.files for p in f.pages if p.is_scan)
    rec = dict(
        bundle=bundle.name,
        render=a.render,
        mode=a.mode,
        pages=sum(len(f.pages) for f in case.files),
        scanned_pages=ocr_pages,
        wall_seconds=round(wall, 1),
        status=case.status,
        partial=case.partial,
        partial_reason=getattr(case, "partial_reason", None),
        documents=len(case.documents),
        fields=f"{fok}/{fn}",
        table_cells=f"{tok}/{tn}",
        page_type=f"{c1ok}/{c1n}",
        llm_calls=len(calls),
        prompt_tokens=sum(c.prompt_tokens for c in calls),
        completion_tokens=sum(c.completion_tokens for c in calls),
        usd=str(sum((c.usd for c in case.costs), Decimal(0))),
        flags_med_high=[
            (f.rule, f.severity) for f in case.flags if f.severity in ("medium", "high")
        ],
        stages=[(st.stage, round(st.seconds or 0, 1)) for st in case.stages],
        field_detail=fields,
        llm_log=logged.calls,
        table_recall={f"{k[0]}.{k[1]}": v for k, v in tr.items()},
    )
    out.write_text(json.dumps(rec, indent=1, default=str))
    brief = {k: v for k, v in rec.items() if k not in ("field_detail", "llm_log")}
    print(json.dumps(brief, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
