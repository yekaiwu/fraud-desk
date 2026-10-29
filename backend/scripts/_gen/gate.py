"""``--gate-set``: 80 labelled pages for the classifier gate (docs 4.8).

40 digital (single-page PDFs) and 40 scanned (20 scan_hq, 10 scan_lq, 10 photo) pages sampled
from clean bundles across the four statement layouts, with C1 to C5 truth. Samples from whatever
bundle builders are installed; per-label shortfalls are returned (KYC document labels need
gen-kyc, so the full set is rebuilt in wave 3)."""

from __future__ import annotations

import io
import json
import random
import shutil
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pymupdf

from . import render_scan as rsc
from .bundle_common import generator_info, page_sha, sha256_hex
from .render_common import DocCanvas, finalize_pdf, printed_lines
from .schema import FIXED_TODAY, BundleSpec, Expected, FileEntry, Manifest, PageEntry, write_json

C1_ALL = (
    "bank_statement", "invoice", "financial_statement", "contract", "purchase_order", "company_profile",
    "certificate_of_incorporation", "ownership_chart", "shareholder_register", "source_of_funds_declaration",
    "national_id", "passport", "boilerplate", "other",
)  # fmt: skip
MIN_PAGES = {lab: 3 for lab in C1_ALL}
MIN_PAGES.update({"other": 5, "boilerplate": 5})
# digital / scanned quota per label for the loan builder
QUOTA = {
    "bank_statement": 14, "invoice": 6, "financial_statement": 3, "contract": 3, "purchase_order": 3,
    "company_profile": 2, "national_id": 3, "boilerplate": 3, "other": 3,
}  # fmt: skip
LAYOUT_COMPANY = {"dbs_style": 4, "ocbc_style": 1, "uob_style": 2, "software": 3}
OTHER_TEXTS = [
    ["Cover letter", "To the credit officer,", "Please find enclosed the documents for our working capital facility application.", "Yours faithfully, Director"],
    ["Delivery order", "DO-48213", "Goods delivered to the buyer's godown; received in good order by the storekeeper."],
    ["Meeting notes", "Agenda: supplier terms for the coming quarter.", "Action: confirm revised price list by Friday."],
    ["Utility statement (sample)", "Account no 77-1234-90", "Amount due this month is shown on the last page."],
    ["Business card collection", "Please file these under general correspondence."],
    ["Courier waybill", "WB 90321 - one carton, fasteners, handle with care."],
]  # fmt: skip


def _other_page(i: int, seed: int) -> bytes:
    cv = DocCanvas()
    lines = OTHER_TEXTS[i % len(OTHER_TEXTS)]
    cv.text(60, 70, lines[0], size=15, font="Helvetica-Bold")
    y = 110.0
    for ln in lines[1:]:
        cv.text(60, y, ln, size=10)
        y += 20
    cv.footer(None)
    return finalize_pdf(
        cv.finish(),
        producer="Mock Word Processor 3.1",
        creator="Mock Word Processor",
        title="Correspondence",
        doc_date=date(2025, 12, 1 + (i % 20)),
    )


def _single_page(pdf: bytes, page: int) -> bytes:
    src = pymupdf.open(stream=pdf, filetype="pdf")
    meta = src.metadata
    out = pymupdf.open()
    out.insert_pdf(src, from_page=page - 1, to_page=page - 1)
    raw = out.tobytes(deflate=True, garbage=3)
    src.close()
    out.close()
    from .render_common import creation_date_for  # noqa: F401

    return finalize_pdf(
        raw,
        producer=meta.get("producer") or "Mock Composer 9.1",
        creator=meta.get("creator") or "Mock",
        title=meta.get("title") or "page",
        doc_date=date(2025, 12, 31),
    )


def build_gate_set(out_root: Path, *, seed: int = 4002, today: date = FIXED_TODAY) -> list[str]:
    from .bundle_loan import render_docs, resolve_spec
    from .world import world_from_spec

    gate_dir = out_root / "gate"
    if gate_dir.exists():
        shutil.rmtree(gate_dir)
    (gate_dir / "files").mkdir(parents=True)
    rng = random.Random(f"gate:{seed}")
    # candidate pages from clean loan companies, one per layout
    cands: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for idx in sorted(LAYOUT_COMPANY.values()):
        spec = resolve_spec(BundleSpec(scenario="loan", company_index=idx, seed=seed, today=today))
        w = world_from_spec(spec)
        for d in render_docs(w, spec):
            for i, pl in enumerate(d.page_labels):
                cands[pl.c1].append(
                    {"doc": d, "page": i + 1, "layout": d.layout, "company": idx, "c1": pl.c1}
                )
    for i in range(12):
        cands["other"].append(
            {"doc": None, "page": 1, "layout": None, "company": 0, "c1": "other", "i": i}
        )
    chosen_dig: list[dict[str, Any]] = []
    chosen_scan: list[dict[str, Any]] = []
    for label, quota in QUOTA.items():
        pool = list(cands.get(label, []))
        rng.shuffle(pool)
        if label == "bank_statement":  # balanced across the four layouts: 14 digital + 14 scanned
            per = defaultdict(list)
            for c in pool:
                per[c["layout"]].append(c)
            for lay, (nd, ns) in zip(sorted(per), ((4, 3), (4, 3), (3, 4), (3, 4)), strict=False):
                chosen_dig += per[lay][:nd]
                chosen_scan += per[lay][nd : nd + ns]
            continue
        take = pool[: 2 * quota]
        chosen_dig += take[0::2][:quota]
        chosen_scan += take[1::2][:quota]
    renders = ["scan_hq"] * 20 + ["scan_lq"] * 10 + ["photo"] * 10
    rng.shuffle(chosen_scan)
    labels_pages: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    gt_pages: list[dict[str, Any]] = []
    files: list[FileEntry] = []
    upload: dict[str, list[str]] = {"digital": [], "scan_hq": [], "scan_lq": [], "photo": []}
    counts: dict[str, int] = defaultdict(int)

    def add(c: dict[str, Any], n: int, render: str) -> None:
        d = c["doc"]
        pdf = d.pdf if d is not None else _other_page(c["i"], seed)
        label = c["c1"]
        counts[label] += 1
        gkey = f"gate:{render}:{n:02d}"
        stem = f"{'d' if render == 'digital' else 's'}{n:02d}_{label}"
        lines = printed_lines(pdf)
        page_lines = next((p["lines"] for p in lines if p["page"] == c["page"]), [])
        if render == "digital":
            data, ext, mime = _single_page(pdf, c["page"]), "pdf", "application/pdf"
            fl = [ln for ln in printed_lines(data)[0]["lines"]]
            xf = None
        else:
            sp = rsc.derive_page(
                pdf, c["page"] - 1, render, f"scan:{seed}:{stem}:{render}", date(2025, 12, 31)
            )
            data, ext, mime = sp.jpeg, "jpg", "image/jpeg"
            fl = [
                {
                    "text": ln["text"],
                    **{k: v for k, v in sp.transform_box(ln).items() if k in "xywh"},
                }
                for ln in page_lines
            ]
            xf = sp
        path = f"files/{stem}_{render}.{ext}" if render != "digital" else f"files/{stem}.{ext}"
        (gate_dir / path).write_bytes(data)
        fsha = sha256_hex(data)
        files.append(
            FileEntry(
                path=path,
                render=render,
                sha256=fsha,
                size_bytes=len(data),
                mime=mime,
                page_count=1,
                pages=[PageEntry(page=1, page_sha256=page_sha(fsha, 1), doc_key=gkey)],
                doc_keys=[gkey],
            )
        )  # type: ignore[arg-type]
        upload[render].append(path)
        labels_pages.append(
            {
                "file": path, "sha256": fsha, "page": 1, "doc_key": gkey, "c1": label, "segment_start": True,
                "layout": c["layout"], "c3": d.issuer if (d is not None and label == "bank_statement") else None,
                "source": {"doc_key": d.key if d is not None else None, "page": c["page"], "company_index": c["company"]},
            }
        )  # fmt: skip
        gt_pages.append({"file": path, "page": 1, "lines": fl})
        if d is not None and label == "bank_statement":
            rows = [r for r in d.truth.tables["transactions"]["rows"] if r["page"] == c["page"]]
            for k, r in enumerate(rows):
                lab = d.row_labels[r["index"]]
                label_rows.append(
                    {
                        "doc_key": gkey,
                        "row": k,
                        "page": 1,
                        "c4": lab["c4"],
                        "c5": lab["c5"],
                        "counterparty": lab["counterparty"],
                        "description": r["cells"]["description"]["value"],
                    }
                )
        del xf

    for n, c in enumerate(chosen_dig):
        add(c, n + 1, "digital")
    for n, (c, r) in enumerate(zip(chosen_scan, renders, strict=False)):
        add(c, n + 1, r)
    spec = BundleSpec(
        scenario="loan", seed=seed, today=today, renders=["digital", "scan_hq", "scan_lq", "photo"]
    )
    manifest = Manifest(
        bundle_id="gate", kind="gate", use_case="loan", spec=spec, generator=generator_info(), applicant={"name": "gate set", "uen": ""},
        registry_path="../registry.json", files=files, documents=[], upload_order={k: v for k, v in upload.items() if v}, expected=Expected(),
    )  # fmt: skip
    write_json(gate_dir / "manifest.json", manifest)
    write_json(
        gate_dir / "labels.json",
        {
            "schema_version": "1",
            "bundle_id": "gate",
            "pages": labels_pages,
            "documents": [],
            "rows": label_rows,
        },
    )
    write_json(
        gate_dir / "ground_truth.json",
        {"schema_version": "1", "bundle_id": "gate", "documents": [], "pages": gt_pages},
    )
    short = []
    for label in C1_ALL:
        have = counts.get(label, 0)
        if have < MIN_PAGES[label]:
            short.append(
                f"C1 {label}: {have} page(s), need {MIN_PAGES[label]}"
                + (
                    " (needs gen-kyc)"
                    if label
                    in (
                        "certificate_of_incorporation",
                        "ownership_chart",
                        "shareholder_register",
                        "source_of_funds_declaration",
                        "passport",
                    )
                    else ""
                )
            )
    return short


def gate_summary(gate_dir: Path) -> dict[str, Any]:
    lab = json.loads((gate_dir / "labels.json").read_text(encoding="utf-8"))
    by = defaultdict(int)
    for p in lab["pages"]:
        by[p["c1"]] += 1
    return dict(by)


__all__ = ["build_gate_set", "gate_summary", "io"]
