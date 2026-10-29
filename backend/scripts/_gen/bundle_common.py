"""Writes a bundle folder (docs 3.3 / 3.4) from rendered documents: digital PDFs, scan derivations,
``manifest.json``, ``labels.json``, ``ground_truth.json``. Shared by every ``bundle_*.py``."""

from __future__ import annotations

import hashlib
import io
import platform
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pikepdf
import PIL
import pymupdf
import reportlab

from . import render_scan as rsc
from .packet import build_packet
from .render_common import RenderedDoc, printed_lines
from .schema import (
    BundleSpec,
    DocEntry,
    Expected,
    FileEntry,
    Manifest,
    PageEntry,
    Planted,
    Quirk,
    bundle_id,
    write_json,
)

GENERATOR_VERSION = "1"


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def page_sha(file_sha: str, page: int) -> str:
    return sha256_hex(f"{file_sha}:{page}".encode())


def generator_info() -> dict[str, str]:
    return {
        "version": GENERATOR_VERSION,
        "python": platform.python_version(),
        "reportlab": reportlab.Version,
        "pymupdf": pymupdf.__version__,
        "pikepdf": pikepdf.__version__,
        "pillow": PIL.__version__,
        "numpy": np.__version__,
    }


def _box(file: str, b: dict[str, Any] | None, page: int | None = None) -> dict[str, Any] | None:
    if not b:
        return None
    return {
        "file": file,
        "page": page if page is not None else b["page"],
        "x": b["x"],
        "y": b["y"],
        "w": b["w"],
        "h": b["h"],
    }


def write_bundle(
    spec: BundleSpec,
    out_dir: Path,
    docs: list[RenderedDoc],
    *,
    applicant: dict[str, str],
    expected: Expected,
    planted: list[Planted] | None = None,
    quirks: list[Quirk] | None = None,
    kind: str = "bundle",
    bid: str | None = None,
    registry_path: str = "../../registry.json",
    doc_date: date | None = None,
) -> Manifest:
    if (out_dir / "manifest.json").exists():  # rebuilding a bundle: drop stale renders
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bid = bid or bundle_id(spec)
    renders: list[str] = list(dict.fromkeys(["digital", *spec.renders]))
    files: list[FileEntry] = []
    labels_pages: list[dict[str, Any]] = []
    gt_pages: list[dict[str, Any]] = []
    upload_order: dict[str, list[str]] = {r: [] for r in renders}
    doc_entries: list[DocEntry] = []
    # per document, per render: file path(s) and per-page transforms
    scan_pages: dict[
        tuple[str, str], list[tuple[str, int, rsc.ScanPage]]
    ] = {}  # (doc key, render) -> (file, file page, ScanPage) per doc page
    gt_docs: list[dict[str, Any]] = []
    label_docs: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    all_date = doc_date or spec.today

    for d in docs:
        dpath = f"digital/{d.stem}.pdf"
        (out_dir / "digital").mkdir(exist_ok=True)
        (out_dir / dpath).write_bytes(d.pdf)
        fsha = sha256_hex(d.pdf)
        pages_entries = [
            PageEntry(page=i + 1, page_sha256=page_sha(fsha, i + 1), doc_key=pl.doc_key)
            for i, pl in enumerate(d.page_labels)
        ]
        files.append(
            FileEntry(
                path=dpath,
                render="digital",
                sha256=fsha,
                size_bytes=len(d.pdf),
                mime="application/pdf",
                page_count=len(d.page_labels),
                pages=pages_entries,
                doc_keys=sorted({pl.doc_key for pl in d.page_labels if pl.doc_key}),
            )
        )
        upload_order["digital"].append(dpath)
        doc_entries.append(
            DocEntry(
                key=d.key,
                doc_type=d.doc_type,
                layout=d.layout,
                issuer=d.issuer,
                digital_path=dpath,
                page_start=d.page_start,
                page_end=d.page_end,
            )  # type: ignore[arg-type]
        )
        for i, pl in enumerate(d.page_labels):
            labels_pages.append(
                {
                    "file": dpath,
                    "sha256": fsha,
                    "page": i + 1,
                    "doc_key": pl.doc_key,
                    "c1": pl.c1,
                    "segment_start": i == 0 or pl.c1 == "boilerplate",
                    "layout": pl.layout,
                }
            )
        for pr in printed_lines(d.pdf):
            gt_pages.append({"file": dpath, "page": pr["page"], "lines": pr["lines"]})
        label_docs.append({"key": d.key, "c1": d.doc_type, "c3": d.issuer})
        for rl in d.row_labels:
            label_rows.append(
                {
                    "doc_key": d.key,
                    "row": rl["row"],
                    "c4": rl["c4"],
                    "c5": rl["c5"],
                    "counterparty": rl["counterparty"],
                }
            )
        # scans
        for render in renders:
            if render == "digital":
                continue
            _make_scans(
                spec,
                out_dir,
                d,
                dpath,
                render,
                files,
                upload_order,
                labels_pages,
                gt_pages,
                scan_pages,
                all_date,
            )

    # packet (C2)
    if spec.packet:
        pk, recs = build_packet(docs, all_date)
        (out_dir / "packet").mkdir(exist_ok=True)
        (out_dir / "packet/packet.pdf").write_bytes(pk)
        psha = sha256_hex(pk)
        files.append(
            FileEntry(
                path="packet/packet.pdf",
                render="digital",
                sha256=psha,
                size_bytes=len(pk),
                mime="application/pdf",
                page_count=len(recs),
                pages=[
                    PageEntry(
                        page=r["page"], page_sha256=page_sha(psha, r["page"]), doc_key=r["doc_key"]
                    )
                    for r in recs
                ],
                doc_keys=sorted({r["doc_key"] for r in recs if r["doc_key"]}),
            )
        )
        for r in recs:
            labels_pages.append(
                {
                    "file": "packet/packet.pdf",
                    "sha256": psha,
                    "page": r["page"],
                    "doc_key": r["doc_key"],
                    "c1": r["c1"],
                    "segment_start": r["segment_start"],
                    "layout": r["layout"],
                }
            )
        for pr in printed_lines(pk):
            gt_pages.append({"file": "packet/packet.pdf", "page": pr["page"], "lines": pr["lines"]})

    # ground truth documents
    for d in docs:
        dpath = f"digital/{d.stem}.pdf"
        sources: list[dict[str, Any]] = [
            {"render": "digital", "file": dpath, "page_start": d.page_start, "page_end": d.page_end}
        ]
        multi = len(renders) > 1
        fields: dict[str, Any] = {}
        for name, fv in d.truth.fields.items():
            entry = {
                "value": fv["value"],
                "raw": fv["raw"],
                "page": fv["page"],
                "box": _box(dpath, fv["box"]),
            }
            if multi:
                entry["boxes"] = _scan_boxes(d, scan_pages, fv["box"], renders)
            fields[name] = entry
        tables: dict[str, Any] = {}
        for tname, tv in d.truth.tables.items():
            rows = []
            for r in tv["rows"]:
                cells = {}
                for col, c in r["cells"].items():
                    ce = {"value": c["value"], "raw": c["raw"], "box": _box(dpath, c["box"])}
                    if multi:
                        ce["boxes"] = _scan_boxes(d, scan_pages, c["box"], renders)
                    cells[col] = ce
                row = {
                    "index": r["index"],
                    "page": r["page"],
                    "box": _box(dpath, r["box"]),
                    "cells": cells,
                }
                if multi:
                    row["boxes"] = _scan_boxes(d, scan_pages, r["box"], renders)
                rows.append(row)
            tables[tname] = {"columns": tv["columns"], "rows": rows}
        for render in renders:
            if render == "digital":
                continue
            for fpath, fpage, _sp in scan_pages.get((d.key, render), []):
                sources.append(
                    {"render": render, "file": fpath, "page_start": fpage, "page_end": fpage}
                )
        gd: dict[str, Any] = {
            "key": d.key,
            "doc_type": d.doc_type,
            "sources": sources,
            "fields": fields,
            "tables": tables,
        }
        if d.truth.extras:
            gd["extras"] = {
                k: {kk: _box(dpath, vv) for kk, vv in v.items()} if isinstance(v, dict) else v
                for k, v in d.truth.extras.items()
            }
        gt_docs.append(gd)

    for r in renders:
        upload_order[r] = list(upload_order[r])
    manifest = Manifest(
        bundle_id=bid,
        kind=kind,  # type: ignore[arg-type]
        use_case=spec.scenario,
        spec=spec,
        generator=generator_info(),
        applicant=applicant,
        registry_path=registry_path,
        files=files,
        documents=doc_entries,
        upload_order=upload_order,  # type: ignore[arg-type]
        planted=planted or [],
        tampers=[],
        expected=expected,
        quirks=quirks or [],
    )
    write_json(out_dir / "manifest.json", manifest)
    write_json(
        out_dir / "labels.json",
        {
            "schema_version": "1",
            "bundle_id": bid,
            "pages": labels_pages,
            "documents": label_docs,
            "rows": label_rows,
        },
    )
    write_json(
        out_dir / "ground_truth.json",
        {"schema_version": "1", "bundle_id": bid, "documents": gt_docs, "pages": gt_pages},
    )
    return manifest


def _make_scans(
    spec: BundleSpec,
    out_dir: Path,
    d: RenderedDoc,
    dpath: str,
    render: str,
    files: list[FileEntry],
    upload_order: dict[str, list[str]],
    labels_pages: list[dict[str, Any]],
    gt_pages: list[dict[str, Any]],
    scan_pages: dict[tuple[str, str], list[tuple[str, int, rsc.ScanPage]]],
    doc_date: date,
) -> None:
    lines_by_page = {p["page"]: p["lines"] for p in printed_lines(d.pdf)}
    n = len(d.page_labels)
    pages: list[tuple[str, int, rsc.ScanPage]] = []
    jpegs: list[bytes] = []
    pdf_path = f"scan_pdf/{d.stem}.pdf"
    for i in range(n):
        seed_key = (
            f"scan:{spec.seed}:{dpath if render != 'scan_pdf' else pdf_path}:{i + 1}:{render}"
        )
        sp = rsc.derive_page(d.pdf, i, render, seed_key, d.doc_date)
        jpegs.append(sp.jpeg)
        pages.append((dpath, i + 1, sp))
    if render == "scan_pdf":
        pdf = rsc.jpegs_to_pdf(jpegs, d.doc_date, f"Scan of {d.stem}")
        (out_dir / "scan_pdf").mkdir(exist_ok=True)
        (out_dir / pdf_path).write_bytes(pdf)
        fsha = sha256_hex(pdf)
        files.append(
            FileEntry(
                path=pdf_path,
                render="scan_pdf",
                sha256=fsha,
                size_bytes=len(pdf),
                mime="application/pdf",
                page_count=n,
                pages=[
                    PageEntry(page=i + 1, page_sha256=page_sha(fsha, i + 1), doc_key=pl.doc_key)
                    for i, pl in enumerate(d.page_labels)
                ],
                doc_keys=sorted({pl.doc_key for pl in d.page_labels if pl.doc_key}),
                derived_from=dpath,
            )
        )
        upload_order["scan_pdf"].append(pdf_path)
        scan_pages[(d.key, render)] = [
            (pdf_path, i + 1, sp) for i, (_f, _p, sp) in enumerate(pages)
        ]
        for i in range(n):
            pl = d.page_labels[i]
            labels_pages.append(
                {
                    "file": pdf_path,
                    "sha256": fsha,
                    "page": i + 1,
                    "doc_key": pl.doc_key,
                    "c1": pl.c1,
                    "segment_start": i == 0 or pl.c1 == "boilerplate",
                    "layout": pl.layout,
                }
            )
        for i, (_f, _p, sp) in enumerate(pages):
            gt_pages.append(
                {
                    "file": pdf_path,
                    "page": i + 1,
                    "lines": [_tline(sp, ln) for ln in lines_by_page.get(i + 1, [])],
                }
            )
        return
    (out_dir / render).mkdir(exist_ok=True)
    out_pages: list[tuple[str, int, rsc.ScanPage]] = []
    for i, (_f, _p, sp) in enumerate(pages):
        path = f"{render}/{d.stem}_p{i + 1:02d}.jpg"
        (out_dir / path).write_bytes(sp.jpeg)
        fsha = sha256_hex(sp.jpeg)
        pl = d.page_labels[i]
        files.append(
            FileEntry(
                path=path,
                render=render,  # type: ignore[arg-type]
                sha256=fsha,
                size_bytes=len(sp.jpeg),
                mime="image/jpeg",
                page_count=1,
                pages=[PageEntry(page=1, page_sha256=page_sha(fsha, 1), doc_key=pl.doc_key)],
                doc_keys=[pl.doc_key] if pl.doc_key else [],
                derived_from=dpath,
            )
        )
        upload_order[render].append(path)
        out_pages.append((path, 1, sp))
        labels_pages.append(
            {
                "file": path,
                "sha256": fsha,
                "page": 1,
                "doc_key": pl.doc_key,
                "c1": pl.c1,
                "segment_start": i == 0 or pl.c1 == "boilerplate",
                "layout": pl.layout,
                "doc_page": i + 1,
            }
        )
        gt_pages.append(
            {
                "file": path,
                "page": 1,
                "lines": [_tline(sp, ln) for ln in lines_by_page.get(i + 1, [])],
            }
        )
    scan_pages[(d.key, render)] = out_pages


def _tline(sp: rsc.ScanPage, ln: dict[str, Any]) -> dict[str, Any]:
    t = sp.transform_box({"x": ln["x"], "y": ln["y"], "w": ln["w"], "h": ln["h"]})
    return {"text": ln["text"], "x": t["x"], "y": t["y"], "w": t["w"], "h": t["h"]}


def _scan_boxes(
    d: RenderedDoc,
    scan_pages: dict[tuple[str, str], list[tuple[str, int, rsc.ScanPage]]],
    box: dict[str, Any] | None,
    renders: list[str],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not box:
        return out
    for render in renders:
        if render == "digital":
            continue
        pages = scan_pages.get((d.key, render))
        if not pages:
            continue
        fpath, fpage, sp = pages[box["page"] - 1]
        t = sp.transform_box(box)
        out[render] = {
            "file": fpath,
            "page": fpage,
            "x": t["x"],
            "y": t["y"],
            "w": t["w"],
            "h": t["h"],
        }
    return out


__all__ = ["write_bundle", "sha256_hex", "page_sha", "generator_info", "io"]
