"""External forensic runner (backend/scripts/ext_eval/forensic_runner.py): adapters, normalisation,
mask localisation, the real checks on a synthetic forged/genuine pair, and the aggregation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts.ext_eval import forensic_runner as fr
from tests.forensics_image_factory import (
    CLONE_DST,
    CLONE_SRC,
    clone_only,
    jpeg_bytes,
    photo_page,
)

# ------------------------------------------------------------------------------ adapters


def make_realtext(root: Path) -> None:
    (root / "train" / "image").mkdir(parents=True)
    (root / "train" / "mask").mkdir(parents=True)
    rows = []
    for i, kind in enumerate(["black", "black", "white", "white", "white"]):
        sid = f"GenText_{i:03d}"
        ext = "jpg" if kind == "black" else "png"
        Image.new("RGB", (40, 30), (200, 200, 200)).save(root / "train" / "image" / f"{sid}.{ext}")
        if kind == "black":
            Image.new("L", (40, 30), 0).save(root / "train" / "mask" / f"{sid}_mask.png")
        rows.append([sid, kind, "part000", f"{sid}.{ext}", f"{sid}_mask.png", f"{sid}_report.md"])
    with (root / "sample_manifest.tsv").open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, delimiter="\t").writerows(rows)
    with (root / "metadata.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sample_id", "language"])
        w.writerows([[r[0], "English" if i % 2 == 0 else "Malay"] for i, r in enumerate(rows)])


def test_load_realtext_reads_labels_masks_and_groups(tmp_path: Path) -> None:
    make_realtext(tmp_path)
    samples = fr.load_realtext(tmp_path)
    assert [s.label for s in samples].count("forged") == 2
    forged = [s for s in samples if s.label == "forged"]
    assert all(s.mask_path and Path(s.mask_path).exists() for s in forged)
    assert all(s.mask_path is None for s in samples if s.label == "genuine")
    assert {s.group for s in samples} == {"English", "Malay"}
    limited = fr.load_realtext(tmp_path, limit_per_class=1)
    assert sorted(s.label for s in limited) == ["forged", "genuine"]
    assert limited == fr.load_realtext(tmp_path, limit_per_class=1)  # deterministic


def test_load_folder_and_label_validation(tmp_path: Path) -> None:
    (tmp_path / "labels.csv").write_text(
        "path,label,mask,group\na/x y.png,Forged,m.png,id\nb.jpg,genuine,,\n", encoding="utf-8"
    )
    s = fr.load_folder(tmp_path)
    by_id = {x.sample_id: x for x in s}  # sorted by id, so compare by id
    assert (by_id["x_y"].label, by_id["x_y"].group) == ("forged", "id")
    assert (by_id["b"].label, by_id["b"].group) == ("genuine", "")
    assert by_id["x_y"].mask_path == str(tmp_path / "m.png")
    (tmp_path / "labels.csv").write_text("path,label\nz.png,maybe\n", encoding="utf-8")
    with pytest.raises(ValueError):
        fr.load_folder(tmp_path)


def test_safe_id_strips_path_tricks() -> None:
    assert fr.safe_id("../../etc/passwd") == ".._.._etc_passwd"
    assert len(fr.safe_id("a" * 500)) == 80


def test_via_json_rect_and_polygon_masks() -> None:
    via = {
        "_via_img_metadata": {
            "k": {
                "filename": "r1.png",
                "regions": [
                    {"shape_attributes": {"name": "rect", "x": 2, "y": 3, "width": 4, "height": 5}},
                    {
                        "shape_attributes": {
                            "name": "polygon",
                            "all_points_x": [10, 15, 15, 10],
                            "all_points_y": [10, 10, 15, 15],
                        }
                    },
                ],
            },
            "k2": {"filename": "empty.png", "regions": []},
        }
    }
    m = fr.via_regions_to_mask(via, "r1.png", (20, 20))
    assert m is not None and m.shape == (20, 20)
    assert m[4, 3] and m[12, 12] and not m[0, 0] and not m[19, 19]
    assert fr.via_regions_to_mask(via, "empty.png", (20, 20)) is None
    assert fr.via_regions_to_mask(via, "unknown.png", (20, 20)) is None


def test_mask_is_resized_nearest_to_the_analysed_size(tmp_path: Path) -> None:
    mask = np.zeros((40, 60), np.uint8)
    mask[10:20, 30:60] = 255
    Image.fromarray(mask, "L").save(tmp_path / "m.png")
    s = fr.Sample("a", "forged", str(tmp_path / "x.png"), str(tmp_path / "m.png"))
    m = fr.load_mask(s, (30, 20))
    assert m is not None and m.shape == (20, 30)
    assert abs(m.mean() - (10 * 30) / (40 * 60)) < 0.05
    assert fr.load_mask(fr.Sample("a", "genuine", "x", None), (30, 20)) is None


# ------------------------------------------------------------------------------ normalisation


def test_normalisation_modes(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    Image.new("RGB", (3000, 2000), (180, 180, 180)).save(src)
    out = {}
    for mode in fr.NORMALISE_MODES:
        d = tmp_path / mode
        d.mkdir()
        path, size = fr.prepare_image(str(src), mode, d)
        out[mode] = (path, size)
    assert out["raw"][0].suffix == ".png" and out["raw"][1] == (3000, 2000)
    assert out["jpeg90"][0].suffix == ".jpg" and out["jpeg90"][1] == (3000, 2000)
    assert out["resize1600_jpeg85"][1] == (1600, 1067)
    for mode in ("jpeg90", "resize1600_jpeg85"):
        with Image.open(out[mode][0]) as im:
            assert im.format == "JPEG"  # both classes end up in the same container
    with pytest.raises(ValueError):
        fr.prepare_image(str(src), "nope", tmp_path)
    Image.new("RGB", (50, 50)).save(tmp_path / "x.bmp")
    with pytest.raises(ValueError, match="JPEG or PNG"):
        fr.prepare_image(str(tmp_path / "x.bmp"), "raw", tmp_path)
    path, _ = fr.prepare_image(str(tmp_path / "x.bmp"), "jpeg90", tmp_path)  # normalised is fine
    assert path.suffix == ".jpg"


def test_normalisation_removes_the_container_leak(tmp_path: Path) -> None:
    """A forged .jpg and a pristine .png of the same pixels are indistinguishable by container
    after normalisation."""
    px = Image.new("RGB", (300, 300), (150, 150, 150))
    px.save(tmp_path / "forged.jpg")
    px.save(tmp_path / "pristine.png")
    fmts = set()
    for name in ("forged.jpg", "pristine.png"):
        d = tmp_path / ("n_" + name)
        d.mkdir()
        path, _ = fr.prepare_image(str(tmp_path / name), "jpeg90", d)
        with Image.open(path) as im:
            fmts.add(im.format)
    assert fmts == {"JPEG"}


# ------------------------------------------------------------------------------ localisation


def test_localisation_hits_and_misses() -> None:
    mask = np.zeros((100, 200), bool)
    mask[40:60, 100:150] = True
    on = fr.localisation([{"x": 0.5, "y": 0.4, "w": 0.25, "h": 0.2}], mask)
    assert on["hit_any"] and on["point_hit"] and on["iou"] == pytest.approx(1.0, abs=0.05)
    off = fr.localisation([{"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}], mask)
    assert not off["hit_any"] and not off["point_hit"] and off["iou"] == 0.0
    assert off["mask_share"] == pytest.approx(mask.mean())
    wide = fr.localisation([{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}], mask)
    assert wide["pixel_recall"] == 1.0 and wide["pixel_precision"] == pytest.approx(mask.mean())


# ------------------------------------------------------------------------------ the real checks


def test_analyse_job_flags_a_cloned_patch_and_localises_it(tmp_path: Path) -> None:
    """Forged = a cloned patch (copy-move), genuine = the original page: the repo's checks run on
    each, and the clone lands on the mask."""
    original = photo_page(sigma=5.0)
    (tmp_path / "forged.jpg").write_bytes(jpeg_bytes(clone_only(original), 90))
    (tmp_path / "genuine.jpg").write_bytes(jpeg_bytes(original, 90))
    mask = np.zeros((800, 1200), np.uint8)
    x0, y0, x1, y1 = CLONE_SRC
    dx, dy = CLONE_DST
    mask[dy : dy + (y1 - y0), dx : dx + (x1 - x0)] = 255  # where the clone landed
    mask[y0:y1, x0:x1] = 255  # and its source (either clone is a correct place to point at)
    Image.fromarray(mask, "L").save(tmp_path / "mask.png")
    base = {"group": "", "mode": "raw", "mask_path": None}
    forged = fr.analyse_job(
        {
            **base,
            "sample_id": "f",
            "label": "forged",
            "path": str(tmp_path / "forged.jpg"),
            "mask_path": str(tmp_path / "mask.png"),
        }
    )
    genuine = fr.analyse_job(
        {**base, "sample_id": "g", "label": "genuine", "path": str(tmp_path / "genuine.jpg")}
    )
    assert forged["ok"] and genuine["ok"], (forged, genuine)
    assert "copy_move" in forged["rules"] and forged["pixel_any"]
    assert genuine["pixel_any"] is False
    assert forged["analysed"] and set(forged["status"]) >= set(fr.PIXEL_RULES) | set(fr.META_RULES)
    assert forged["loc"]["hit_any"] and forged["loc"]["iou"] > 0.2


def test_analyse_job_reports_a_corrupt_file_as_an_error(tmp_path: Path) -> None:
    (tmp_path / "bad.jpg").write_bytes(b"not an image")
    res = fr.analyse_job(
        {
            "sample_id": "b",
            "label": "genuine",
            "path": str(tmp_path / "bad.jpg"),
            "mask_path": None,
            "group": "",
            "mode": "jpeg90",
        }
    )
    assert res["ok"] is False and "error" in res


def test_metadata_checks_run_on_raw_only(tmp_path: Path) -> None:
    im = Image.new("RGB", (400, 400), (200, 200, 200))
    exif = Image.Exif()
    exif[0x0131] = "Adobe Photoshop 25.0"
    im.save(tmp_path / "p.jpg", exif=exif.tobytes())
    job = {
        "sample_id": "p",
        "label": "forged",
        "path": str(tmp_path / "p.jpg"),
        "mask_path": None,
        "group": "",
    }
    raw = fr.analyse_job({**job, "mode": "raw"})
    norm = fr.analyse_job({**job, "mode": "jpeg90"})
    assert raw["meta_any"] is True  # image_editor_software names Photoshop
    assert "image_editor_software" not in norm["status"] and norm["meta_any"] is False


# ------------------------------------------------------------------------------ aggregation


def result(
    label: str, any_: bool, medium: bool = False, loc: dict | None = None, mode: str = "jpeg90"
) -> dict:
    r = {
        "sample_id": f"{label}{any_}{medium}{id(loc)}",
        "label": label,
        "mode": mode,
        "group": "g",
        "ok": True,
        "analysed": True,
        "pixel_any": any_,
        "pixel_ge_medium": medium,
        "pixel_high": False,
        "meta_any": False,
        "rules": ["ela_region"] if any_ else [],
        "seconds": 1.0,
    }
    if loc is not None:
        r["loc"] = loc
    return r


def test_summarise_rates_wilson_and_youden() -> None:
    loc = {
        "hit_any": True,
        "point_hit": False,
        "iou": 0.2,
        "pixel_precision": 0.5,
        "pixel_recall": 0.3,
        "mask_share": 0.05,
        "pred_share": 0.1,
    }
    results = (
        [result("genuine", False)] * 8
        + [result("genuine", True)] * 2
        + [result("forged", True, loc=loc)] * 3
        + [result("forged", False)] * 1
        + [
            {
                "sample_id": "bad",
                "label": "forged",
                "mode": "jpeg90",
                "group": "g",
                "ok": False,
                "error": "x",
            }
        ]
    )
    s = fr.summarise(results)["jpeg90"]
    any_ = s["levels"]["any"]
    assert any_["false_alarm"]["k"] == 2 and any_["false_alarm"]["n"] == 10
    assert any_["detection"]["k"] == 3 and any_["detection"]["n"] == 4
    assert any_["false_alarm"]["lo"] < 0.2 < any_["false_alarm"]["hi"]
    assert any_["youden_j"] == pytest.approx(0.75 - 0.2)
    assert len(s["errors"]) == 1
    assert s["localisation"]["detected_with_regions"] == 3
    assert s["localisation"]["hit_any"]["k"] == 3 and s["localisation"]["point_hit"]["k"] == 0
    assert s["localisation"]["chance_point_hit"] == pytest.approx(0.05)
    assert s["meta"] is None  # metadata only on raw


def test_report_marks_raw_as_leaky_and_states_the_dataset_limits(tmp_path: Path) -> None:
    results = [result("genuine", False, mode="raw"), result("forged", True, mode="raw")]
    fr.write_outputs(tmp_path, "realtext", results, ["raw"])
    md = (tmp_path / "forensics-realtext.md").read_text(encoding="utf-8")
    assert "LEAKY" in md and "does not represent" in md and "Wilson" in md
    assert (tmp_path / "forensics-realtext.csv").exists()
    data = json.loads((tmp_path / "forensics-realtext.json").read_text(encoding="utf-8"))
    assert data["summary"]["raw"]["genuine"] == 1
    header = (tmp_path / "forensics-realtext.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("mode,strictness")
