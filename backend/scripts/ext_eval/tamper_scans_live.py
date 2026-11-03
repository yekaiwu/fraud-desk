"""Tamper check on scanned invoices, live: clean against one changed amount, printed with decimal
points and with decimal commas, through the real pipeline (Tesseract reads, Haiku extracts).

    uv run python backend/scripts/ext_eval/tamper_scans_live.py --out DIR --env /path/to/.env

Six small synthetic invoices (clean, one line amount changed, only the total changed; two number
formats), about USD 0.012 in all. The images are drawn here, so nothing is downloaded. Prints the
rule flags of each. Fictitious data.
"""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
for p in (_BACKEND, _BACKEND / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pymupdf  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402
from run_case import analyse_files  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.core.db import Database  # noqa: E402

ROWS = [
    ("Desk lamp", "2.00", "100.00", "200.00"),
    ("Office chair", "3.00", "150.00", "450.00"),
    ("Filing cabinet", "1.00", "320.00", "320.00"),
    ("Whiteboard", "4.00", "45.00", "180.00"),
]
SUBTOTAL, VAT, TOTAL = "1150.00", "115.00", "1265.00"


def draw(path: Path, rows: list[tuple[str, ...]], total: str, comma: bool) -> None:
    def num(v: str) -> str:
        return v.replace(".", ",") if comma else v

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 11)
    c.drawString(60, 800, "Invoice no: 55120934")
    c.drawString(60, 780, "Date of issue: 18/09/2025")
    c.drawString(60, 755, "Seller: Bradley-Andrade Supplies")
    c.drawString(60, 740, "Client: Castro Trading")
    y = 700
    c.drawString(60, y, "No. Description")
    c.drawRightString(300, y, "Qty")
    c.drawRightString(390, y, "Net price")
    c.drawRightString(500, y, "Net worth")
    for i, (desc, qty, price, amount) in enumerate(rows, 1):
        y -= 22
        c.drawString(60, y, f"{i}. {desc}")
        for x, v in ((300, qty), (390, price), (500, amount)):
            c.drawRightString(x, y, num(v))
    y -= 40
    for label, value in (("Subtotal", SUBTOTAL), ("VAT 10%", VAT), ("Total", total)):
        c.drawString(330, y, label)
        c.drawRightString(500, y, "$ " + num(value))
        y -= 20
    c.save()
    pymupdf.open(stream=buf.getvalue(), filetype="pdf")[0].get_pixmap(dpi=150).save(str(path))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True, help="folder for the drawn images")
    ap.add_argument("--env", type=Path, required=True, help="a .env file with the model settings")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for comma in (False, True):
        tag = "comma" if comma else "point"
        changed = list(ROWS)
        changed[1] = ("Office chair", "3.00", "150.00", "850.00")  # one line amount changed
        draw(args.out / f"clean_{tag}.png", ROWS, TOTAL, comma)
        draw(args.out / f"line_{tag}.png", changed, TOTAL, comma)
        draw(args.out / f"total_{tag}.png", ROWS, "1765.00", comma)
    for img in sorted(args.out.glob("*.png")):
        data_dir = Path(tempfile.mkdtemp(prefix="gldb-tamper-"))
        settings = Settings(
            _env_file=args.env,
            data_dir=data_dir,
            db_path=str(data_dir / "gldb.sqlite3"),
            ocr_backend="tesseract",
            max_usd_per_case=Decimal("0.3"),
            max_usd_per_day=Decimal("0.5"),
        )
        db = Database(settings.database_path, data_dir=settings.data_dir)
        db.init_schema()
        case = analyse_files(
            [img], use_case="loan", today=date(2026, 10, 27), db=db, settings=settings
        )
        case = db.load_case(case.id)
        flags = [(f.rule, f.severity) for f in case.flags if f.rule.startswith(("inv_", "rcpt_"))]
        usd = sum((c.usd for c in case.costs), Decimal("0"))
        print(img.name, [d.doc_type for d in case.documents], flags, f"usd {usd}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
