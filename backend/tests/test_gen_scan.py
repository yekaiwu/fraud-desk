"""Scan derivation (T-gen-loan-07)."""

from __future__ import annotations

import io
import json
from datetime import date

import pymupdf
import pytest
from PIL import Image

from scripts._gen import render_scan as rsc
from scripts._gen import world
from scripts._gen.render_statement import render_statement
from scripts._gen.schema import BundleSpec

from .test_gen_helpers import load, loan_bundle

DOC_DATE = date(2025, 7, 31)


@pytest.fixture(scope="module")
def stmt():  # noqa: ANN201
    w = world.build_world("loan")
    return render_statement(w.statements[0], w.quirks)


@pytest.mark.parametrize(
    "render,q", [("scan_hq", 90), ("scan_lq", 60), ("photo", 70), ("scan_pdf", 85)]
)
def test_dimensions_and_jpeg_quality_tables(stmt, render: str, q: int) -> None:  # noqa: ANN001
    p = rsc.derive_page(stmt.pdf, 0, render, f"scan:4002:x:{render}", DOC_DATE)
    im = Image.open(io.BytesIO(p.jpeg))
    assert im.size == (1240, 1754) and im.format == "JPEG"
    ref = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(ref, format="JPEG", quality=q)
    ref_q = Image.open(io.BytesIO(ref.getvalue())).quantization
    assert im.quantization == ref_q
    assert rsc.JPEG_QUALITY[render] == q


def test_exif_has_scanner_make_model_and_document_date_but_no_software(stmt) -> None:  # noqa: ANN001
    p = rsc.derive_page(stmt.pdf, 0, "scan_hq", "k", DOC_DATE)
    im = Image.open(io.BytesIO(p.jpeg))
    ex = im.getexif()
    assert ex.get(0x010F) == "Mock Scanner" and ex.get(0x0110) == "Mock Scanner"
    assert ex.get_ifd(0x8769)[0x9003] == "2025:07:31 09:00:00"
    assert 0x0131 not in ex  # Software
    assert "software" not in {k.lower() for k in im.info}


def test_same_seed_gives_identical_bytes_and_other_seed_differs(stmt) -> None:  # noqa: ANN001
    a = rsc.derive_page(stmt.pdf, 0, "photo", "seed-a", DOC_DATE)
    b = rsc.derive_page(stmt.pdf, 0, "photo", "seed-a", DOC_DATE)
    c = rsc.derive_page(stmt.pdf, 0, "photo", "seed-b", DOC_DATE)
    assert a.jpeg == b.jpeg and a.jpeg != c.jpeg


@pytest.mark.parametrize("render", ["scan_hq", "scan_lq", "photo"])
def test_transformed_boxes_stay_inside_the_unit_square(stmt, render: str) -> None:  # noqa: ANN001
    p = rsc.derive_page(stmt.pdf, 0, render, f"box:{render}", DOC_DATE)
    boxes = [r["box"] for r in stmt.truth.tables["transactions"]["rows"] if r["page"] == 1]
    assert boxes
    for b in boxes:
        t = p.transform_box(b)
        assert (
            t["x"] >= 0
            and t["y"] >= 0
            and t["x"] + t["w"] <= 1.0001
            and t["y"] + t["h"] <= 1.0001
            and t["w"] > 0
            and t["h"] > 0
        )
    # the photo shrinks the page into a desk border; scans keep it almost full size
    t0 = p.transform_box({"page": 1, "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
    if render == "photo":
        assert t0["w"] < 0.95 and t0["x"] > 0.03
    else:
        assert t0["w"] > 0.98


def test_geometry_follows_the_pixels(stmt) -> None:  # noqa: ANN001
    """The darkest ink in a transformed row box should be inside that box."""
    import numpy as np

    p = rsc.derive_page(stmt.pdf, 0, "scan_hq", "ink", DOC_DATE)
    img = np.asarray(Image.open(io.BytesIO(p.jpeg)).convert("L")).astype(float)
    row = stmt.truth.tables["transactions"]["rows"][5]
    t = p.transform_box(row["box"])
    x0, x1 = int(t["x"] * 1240), int((t["x"] + t["w"]) * 1240)
    y0, y1 = int(t["y"] * 1754), int((t["y"] + t["h"]) * 1754)
    inside = img[y0:y1, x0:x1].min()
    band = img[max(0, y0 - 40) : y0 - 12, x0:x1].min()  # above the row: blank paper or another row
    assert inside < 120 and inside <= band + 200


def test_scan_pdf_is_image_only_with_scanner_producer(stmt) -> None:  # noqa: ANN001
    p = rsc.derive_page(stmt.pdf, 0, "scan_pdf", "pdf", DOC_DATE)
    pdf = rsc.jpegs_to_pdf([p.jpeg, p.jpeg], DOC_DATE, "t")
    with pymupdf.open(stream=pdf, filetype="pdf") as d:
        assert len(d) == 2 and d.metadata["producer"] == "Mock Scanner 2.1"
        assert d[0].get_text().strip() == "" and len(d[0].get_images()) == 1


def test_bundle_scan_renders_have_no_text_layer_and_boxes_per_render() -> None:
    b = loan_bundle("clean", 0, False, ("digital", "scan_hq", "scan_lq", "photo", "scan_pdf"))
    m = load(b, "manifest.json")
    by_render: dict[str, list[dict]] = {}
    for f in m["files"]:
        by_render.setdefault(f["render"], []).append(f)
    assert set(by_render) == {"digital", "scan_hq", "scan_lq", "photo", "scan_pdf"}
    assert len(by_render["scan_hq"]) == sum(f["page_count"] for f in by_render["digital"])
    for f in by_render["scan_hq"][:3] + by_render["scan_pdf"][:2]:
        with pymupdf.open(b / f["path"]) as d:
            assert sum(len(pg.get_text().split()) for pg in d) == 0
        assert f["derived_from"].startswith("digital/")
    assert m["upload_order"]["scan_hq"] == [f["path"] for f in by_render["scan_hq"]]
    gt = load(b, "ground_truth.json")
    scan_pages = [p for p in gt["pages"] if p["file"].startswith("scan_hq/")]
    assert scan_pages and all(p["lines"] for p in scan_pages)
    for p in scan_pages[:5]:
        for ln in p["lines"]:
            assert ln["x"] + ln["w"] <= 1.0001 and ln["y"] + ln["h"] <= 1.0001
    st = next(d for d in gt["documents"] if d["key"] == "bank_statement:2025-07")
    cell = st["tables"]["transactions"]["rows"][2]["cells"]["credit"]
    assert set(cell["boxes"]) == {"scan_hq", "scan_lq", "photo", "scan_pdf"}
    assert cell["boxes"]["scan_hq"]["file"].startswith("scan_hq/") and cell["boxes"]["scan_pdf"][
        "file"
    ].startswith("scan_pdf/")
    assert any(s["render"] == "photo" for s in st["sources"])
    lab = load(b, "labels.json")
    files = {p["file"] for p in lab["pages"]}
    assert all(f["path"] in files for f in m["files"])
    assert json.dumps(BundleSpec.model_validate(m["spec"]).renders)
