"""False alarms on invoices that our generator did not make.

Our own evaluation uses documents drawn by the same people who wrote the checks. This script runs the
real pipeline on a public invoice data set (mychen76/invoices-and-receipts_ocr_v1 on Hugging Face:
images of invoices with their field labels, made by someone else, with other layouts and fonts). The
documents are not tampered, so any medium or high flag is a false alarm; the labels also give an
extraction check on the invoice number and the total.

The data are synthetic invoices (fake companies), not scans of real business paperwork: this removes
"same author" bias, it does not close the real-document gap.

    uv run --with pyarrow python backend/scripts/ext_eval/independent_invoices.py fetch --out DIR --limit 60
    GLDB_LLM_BACKEND=anthropic GLDB_ANTHROPIC_API_KEY=... GLDB_OCR_BACKEND=tesseract \\
        uv run python backend/scripts/ext_eval/independent_invoices.py run --dir DIR --out result.json

Images are downloaded into the folder you name and read as data only. Nothing downloaded is executed.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
for p in (_BACKEND, _BACKEND / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from _eval.metrics import wilson  # noqa: E402
from run_case import analyse_files  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.db import Database  # noqa: E402

BASE = "https://huggingface.co/datasets/mychen76/invoices-and-receipts_ocr_v1/resolve/main/data/"
SPLITS = {
    "test": "test-00000-of-00001-af2d92d1cee28514.parquet",
    "valid": "valid-00000-of-00001-894b4e1f736b5732.parquet",
}
TODAY = date(2026, 10, 27)


def fetch(args: argparse.Namespace) -> int:
    import pyarrow.parquet as pq

    out: Path = args.out
    (out / "images").mkdir(parents=True, exist_ok=True)
    parquet = out / "source.parquet"
    if not parquet.exists():
        req = urllib.request.Request(
            BASE + SPLITS[args.split], headers={"User-Agent": "fraud-desk"}
        )
        with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310 - fixed https URL
            parquet.write_bytes(r.read())
    labels: dict[str, dict] = {}
    for row in pq.read_table(parquet).to_pylist()[: args.limit]:
        name = f"invoice_{row['id']}.jpeg"
        (out / "images" / name).write_bytes(row["image"]["bytes"])
        try:
            parsed = ast.literal_eval(json.loads(row["parsed_data"])["json"])
        except (ValueError, SyntaxError, KeyError, TypeError):
            parsed = {}
        labels[name] = {
            "invoice_no": (parsed.get("header") or {}).get("invoice_no"),
            "total": (parsed.get("summary") or {}).get("total_gross_worth"),
        }
    (out / "labels.json").write_text(json.dumps(labels, indent=1), encoding="utf-8")
    print(f"{len(labels)} images in {out / 'images'}")
    return 0


def money(text: object) -> Decimal | None:
    cleaned = re.sub(r"[^\d.,-]", "", str(text or ""))
    last = max(cleaned.rfind("."), cleaned.rfind(","))
    if last >= 0 and re.fullmatch(
        r"\d{1,2}", cleaned[last + 1 :] or "x"
    ):  # the last separator is the decimal point
        cleaned = re.sub(r"[.,]", "", cleaned[:last]) + "." + cleaned[last + 1 :]
    else:
        cleaned = re.sub(r"[.,]", "", cleaned)
    try:
        return Decimal(cleaned) if cleaned else None
    except InvalidOperation:
        return None


def field_hits(case, label: dict) -> tuple[bool, bool]:  # noqa: ANN001
    inv = [d for d in case.documents if d.doc_type == "invoice"]
    no = {
        re.sub(r"\W", "", (d.fields["invoice_number"].value or ""))
        for d in inv
        if "invoice_number" in d.fields
    }
    totals = {money(d.fields["total"].value) for d in inv if "total" in d.fields}
    want_no = re.sub(r"\W", "", str(label.get("invoice_no") or ""))
    want_total = money(label.get("total"))
    return (bool(want_no) and want_no in no, want_total is not None and want_total in totals)


def run(args: argparse.Namespace) -> int:
    labels = json.loads((args.dir / "labels.json").read_text(encoding="utf-8"))
    images = [p for p in sorted((args.dir / "images").glob("*.jpeg")) if p.name in labels][
        : args.limit
    ]
    rows = []
    spent = Decimal("0")
    for img in images:
        if spent > Decimal(args.max_usd):
            print("BUDGET REACHED", spent, flush=True)
            break
        data_dir = Path(tempfile.mkdtemp(prefix="gldb-ext-"))
        local = (
            {
                "llm_backend": "openai",
                "llm_base_url": args.local_llm,
                "llm_api_key": "local",
                "llm_model_cheap": args.local_model,
                "llm_model_strong": args.local_model,
                "llm_timeout_s": 600.0,
                "llm_enable_thinking": False,
                "llm_supports_vision": False,
                "llm_max_concurrency": 1,
                "semantic": "off",
                "cache_enabled": False,
            }
            if args.local_llm
            else {}
        )
        settings = Settings(
            **local,
            _env_file=None if args.local_llm else args.env,
            data_dir=data_dir,
            db_path=str(data_dir / "gldb.sqlite3"),
            **({"ocr_backend": args.ocr} if args.ocr else {}),
            **({"classifier_mode": args.classifier} if args.classifier else {}),
            max_usd_per_case=Decimal(args.max_usd_per_case),
            max_usd_per_day=Decimal(args.max_usd),
        )
        db = Database(settings.database_path, data_dir=settings.data_dir)
        db.init_schema()
        t0 = time.perf_counter()
        try:
            case = analyse_files([img], use_case="loan", today=TODAY, db=db, settings=settings)
            case = db.load_case(case.id)
        except Exception as exc:  # noqa: BLE001 - a crash is a result too
            rows.append({"file": img.name, "error": type(exc).__name__})
            print(img.name, "ERROR", type(exc).__name__, flush=True)
            continue
        no_ok, total_ok = field_hits(case, labels.get(img.name, {}))
        flags = [{"rule": f.rule, "severity": f.severity} for f in case.flags]
        row = {
            "file": img.name,
            "doc_types": [d.doc_type for d in case.documents],
            "flags": flags,
            "medium_or_high": sum(f["severity"] in ("medium", "high") for f in flags),
            "invoice_no_ok": no_ok,
            "total_ok": total_ok,
            "usd": str(sum((c.usd for c in case.costs), Decimal("0"))),
            "seconds": round(time.perf_counter() - t0, 1),
            "partial": case.partial,
        }
        rows.append(row)
        spent += Decimal(row["usd"])
        print(
            img.name,
            row["doc_types"],
            f"flags>=med: {row['medium_or_high']}",
            f"no={no_ok} total={total_ok}",
            flush=True,
        )
    done = [r for r in rows if "error" not in r]
    n = len(done)
    alarmed = sum(r["medium_or_high"] > 0 for r in done)
    as_invoice = sum("invoice" in r["doc_types"] for r in done)
    summary = {
        "images": len(rows),
        "errors": len(rows) - n,
        "read_as_invoice": as_invoice,
        "false_alarm_cases": alarmed,
        "false_alarm_interval": [round(x, 3) for x in wilson(alarmed, n)],
        "invoice_no_correct": sum(r["invoice_no_ok"] for r in done),
        "total_correct": sum(r["total_ok"] for r in done),
        "by_rule": Counter(
            f["rule"] for r in done for f in r["flags"] if f["severity"] in ("medium", "high")
        ),
        "usd_total": str(sum((Decimal(r["usd"]) for r in done), Decimal("0"))),
    }
    args.out.write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1, default=dict), encoding="utf-8"
    )
    print(json.dumps(summary, indent=1, default=dict))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", type=Path, required=True)
    f.add_argument("--split", default="test", choices=list(SPLITS))
    f.add_argument("--limit", type=int, default=60)
    r = sub.add_parser("run")
    r.add_argument("--dir", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--limit", type=int, default=60)
    r.add_argument(
        "--env", type=Path, default=None, help="a .env file with the model settings (never printed)"
    )
    r.add_argument("--ocr", default=None, choices=["tesseract", "rapidocr", "vision", "none"])
    r.add_argument(
        "--local-llm",
        default=None,
        help="an OpenAI-compatible URL such as http://127.0.0.1:1234/v1",
    )
    r.add_argument("--local-model", default="qwen3.8-27b")
    r.add_argument("--classifier", default=None, choices=["rules", "local", "llm", "cascade"])
    r.add_argument(
        "--max-usd",
        default="1.50",
        help="spend stops here (each case has its own database, so this caps per case chain)",
    )
    r.add_argument("--max-usd-per-case", default="0.30")
    args = ap.parse_args()
    return fetch(args) if args.cmd == "fetch" else run(args)


if __name__ == "__main__":
    sys.exit(main())
