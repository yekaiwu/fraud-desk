"""Print-scan / camera simulator (backend/scripts/ext_eval/scan_sim.py): determinism, monotone
severity, output sizes, labelling, bundle builder and the README parameter table."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw

from scripts.ext_eval import scan_sim as ss

README = Path(__file__).resolve().parents[1] / "scripts" / "ext_eval" / "README.md"


def text_page(w: int = 827, h: int = 1169) -> np.ndarray:
    """A white page with black lines of text-like strokes at 100 dpi (cheap to degrade)."""
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for i in range(30):
        y = 60 + i * 30
        d.text(
            (50, y),
            f"01 Jul 2025  PAYMENT {i:03d}   1,234.{i:02d}   56,789.{i:02d}",
            fill=(0, 0, 0),
        )
    return np.asarray(im)


@pytest.fixture(scope="module")
def page() -> np.ndarray:
    # a small stand-in for the 200 dpi render (the pipeline is size-agnostic; keeps the tests fast)
    return text_page()


def test_degrade_is_deterministic_and_key_sensitive(page: np.ndarray) -> None:
    a, pa = ss.degrade_page(page, "med", "k1")
    b, pb = ss.degrade_page(page, "med", "k1")
    c, _ = ss.degrade_page(page, "med", "k2")
    assert np.array_equal(a, b) and pa == pb
    assert not np.array_equal(a, c)


@pytest.mark.parametrize("level", ss.LEVEL_NAMES)
def test_output_size_follows_the_level_dpi(page: np.ndarray, level: str) -> None:
    out, params = ss.degrade_page(page, level, "size")
    lv = ss.LEVELS[level]
    if lv.out_dpi == ss.SRC_DPI:
        assert out.shape[:2] == page.shape[:2]
    else:
        assert out.shape[:2] == (round(ss.A4_IN[1] * lv.out_dpi), round(ss.A4_IN[0] * lv.out_dpi))
    assert out.dtype == np.uint8 and out.shape[2] == 3
    assert {"rot_deg", "dot_gain", "noise_sigma"} <= set(params)


def test_drawn_parameters_stay_inside_the_documented_ranges(page: np.ndarray) -> None:
    for level in ss.LEVEL_NAMES:
        lv = ss.LEVELS[level]
        for i in range(3):
            _, p = ss.degrade_page(page, level, f"r{i}")
            assert lv.dot_gain[0] <= p["dot_gain"] <= lv.dot_gain[1]
            assert lv.noise_sigma[0] <= p["noise_sigma"] <= lv.noise_sigma[1]
            assert lv.rot_deg[0] <= abs(p["rot_deg"]) <= lv.rot_deg[1]
            assert lv.defocus_r[0] <= p["defocus_r"] <= lv.defocus_r[1]
            assert lv.halftone[0] <= p["halftone"] <= lv.halftone[1]


def test_severity_is_monotone(page: np.ndarray) -> None:
    """Heavier levels differ more from the clean page (brought to a common size) and correlate less
    with it."""
    base = cv2.cvtColor(
        cv2.resize(page, (827, 1169), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY
    ).astype(np.float32)
    diffs, corrs = [], []
    for level in ss.LEVEL_NAMES:
        out, _ = ss.degrade_page(page, level, "mono")
        g = cv2.cvtColor(
            cv2.resize(out, (827, 1169), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY
        ).astype(np.float32)
        diffs.append(float(np.abs(g - base).mean()))
        corrs.append(float(np.corrcoef(g.ravel(), base.ravel())[0, 1]))
    assert diffs == sorted(diffs) and diffs[0] < diffs[-1]
    assert corrs == sorted(corrs, reverse=True) and corrs[0] > corrs[-1]


def test_jpeg_chain_is_a_jpeg_with_a_simulated_comment(page: np.ndarray) -> None:
    out, _ = ss.degrade_page(page, "heavy", "chain")
    data = ss.jpeg_chain(out, ss.LEVELS["heavy"].jpeg_chain, "SIMULATED test")
    im = Image.open(io.BytesIO(data))
    assert im.format == "JPEG"
    assert b"SIMULATED test" in data
    # the resize step of the heavy chain shrinks the final image
    assert im.width == round(out.shape[1] * 0.8)


def test_levels_table_lists_every_level() -> None:
    table = ss.describe_levels()
    for name in ss.LEVEL_NAMES:
        assert name in table.splitlines()[0]
    assert "JPEG chain" in table and "resize x0.8, q60" in table


def test_readme_contains_the_current_parameter_table() -> None:
    assert README.exists()
    assert ss.describe_levels() in README.read_text(encoding="utf-8")


def test_render_names() -> None:
    assert ss.render_name("med", "pdf") == "scan_sim_med"
    assert ss.render_name("heavy", "jpeg") == "scan_sim_heavy_jpg"
    with pytest.raises(ValueError):
        ss.render_name("med", "tiff")


# ------------------------------------------------------------------------------ bundle builder


def make_source_bundle(root: Path) -> Path:
    """A minimal bundle: one 2-page digital PDF, manifest, labels, ground truth."""
    bdir = root / "tiny-clean"
    (bdir / "digital").mkdir(parents=True)
    doc = pymupdf.open()
    for i in range(2):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((50, 100), f"STATEMENT page {i + 1} 1,234.56", fontsize=12)
    pdf = doc.tobytes()
    doc.close()
    (bdir / "digital" / "stmt.pdf").write_bytes(pdf)
    sha = hashlib.sha256(pdf).hexdigest()
    manifest = {
        "bundle_id": "tiny-clean",
        "use_case": "loan",
        "registry_path": "../registry.json",
        "upload_order": {"digital": ["digital/stmt.pdf"]},
        "files": [
            {
                "path": "digital/stmt.pdf",
                "render": "digital",
                "sha256": sha,
                "mime": "application/pdf",
                "pages": [
                    {"page": 1, "page_sha256": "a", "doc_key": "bank_statement:x"},
                    {"page": 2, "page_sha256": "b", "doc_key": "bank_statement:x"},
                ],
            }
        ],
        "tampers": [],
    }
    labels = {
        "pages": [
            {
                "file": "digital/stmt.pdf",
                "sha256": sha,
                "page": 1,
                "doc_key": "bank_statement:x",
                "c1": "bank_statement",
            },
            {
                "file": "digital/stmt.pdf",
                "sha256": sha,
                "page": 2,
                "doc_key": "bank_statement:x",
                "c1": "bank_statement",
            },
        ],
        "documents": [],
        "rows": [],
    }
    (bdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (bdir / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    (bdir / "ground_truth.json").write_text(json.dumps({"documents": []}), encoding="utf-8")
    return bdir


def test_build_sim_bundle_writes_consistent_files(tmp_path: Path) -> None:
    src = make_source_bundle(tmp_path / "src")
    out = ss.build_sim_bundle(src, tmp_path / "sim", levels=("light",), containers=("pdf", "jpeg"))
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert set(man["upload_order"]) == {"scan_sim_light", "scan_sim_light_jpg"}
    assert man["simulated"]["simulated"] is True and "SIMULATED" in man["simulated"]["label"]
    assert man["use_case"] == "loan"
    # the relative registry path still points at the same place after the move
    assert (out / man["registry_path"]).resolve() == (src / "../registry.json").resolve()
    for f in man["files"]:
        data = (out / f["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == f["sha256"]
        assert f["derived_from"] == "digital/stmt.pdf"
    pdf_entry = next(f for f in man["files"] if f["render"] == "scan_sim_light")
    assert pdf_entry["page_count"] == 2 and pdf_entry["mime"] == "application/pdf"
    with pymupdf.open(out / pdf_entry["path"]) as d:
        assert d.page_count == 2
        assert "SIMULATED" in d.metadata["producer"]
        assert d[0].get_images()  # image-only pages
        assert not d[0].get_text().strip()
    jpg_entries = [f for f in man["files"] if f["render"] == "scan_sim_light_jpg"]
    assert len(jpg_entries) == 2 and all(f["mime"] == "image/jpeg" for f in jpg_entries)
    labels = json.loads((out / "labels.json").read_text(encoding="utf-8"))
    by_file = {(r["file"], r["page"]): r for r in labels["pages"]}
    pdf_path = pdf_entry["path"]
    assert by_file[(pdf_path, 1)]["sha256"] == pdf_entry["sha256"]
    assert by_file[(pdf_path, 2)]["doc_key"] == "bank_statement:x"
    jpg2 = next(f for f in jpg_entries if f["path"].endswith("_p02.jpg"))
    assert (
        by_file[(jpg2["path"], 1)]["doc_page"] == 2
    )  # per-page files keep the page in the document


def test_build_sim_bundle_is_byte_deterministic(tmp_path: Path) -> None:
    src = make_source_bundle(tmp_path / "src")
    a = ss.build_sim_bundle(src, tmp_path / "a", levels=("med",))
    b = ss.build_sim_bundle(src, tmp_path / "b", levels=("med",))
    pa, pb = a / "scan_sim_med" / "stmt.pdf", b / "scan_sim_med" / "stmt.pdf"
    assert pa.read_bytes() == pb.read_bytes()
    assert (
        json.loads((a / "manifest.json").read_text())["files"]
        == json.loads((b / "manifest.json").read_text())["files"]
    )


def test_jpegs_to_pdf_has_fixed_metadata() -> None:
    img = np.full((100, 80, 3), 200, np.uint8)
    jp = ss.jpeg_chain(img, ((1.0, 80),), "SIMULATED t")
    pdf = ss.jpegs_to_pdf([jp], "t")
    with pymupdf.open(stream=pdf, filetype="pdf") as d:
        assert d.metadata["creationDate"] == ss.FIXED_PDF_DATE
        assert ss.SIM_LABEL in d.metadata["producer"]
