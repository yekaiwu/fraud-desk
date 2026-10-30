"""RealText-V2 breakdown (backend/scripts/ext_eval/realtext_breakdown.py)."""

from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image

from scripts.ext_eval import realtext_breakdown as rb

REPORT = """# FORGERY ANALYSIS REPORT
### ANOMALY_001: Visual Clumsy (a smudge)
### ANOMALY_002: semantic_subtle (name)
### ANOMALY_003: Logical Fraud Type (dates)
"""


def test_anomaly_types_normalises_names() -> None:
    assert rb.anomaly_types(REPORT) == {"visual_clumsy", "semantic_subtle", "logical_fraud"}
    assert rb.anomaly_types("No anomalies detected.") == set()


def build(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "rt"
    for d in ("report", "mask"):
        (root / "train" / d).mkdir(parents=True)
    rows = []
    for i, (label, any_flag, sev, share) in enumerate(
        [
            ("forged", True, 2, 0.5),
            ("forged", False, -1, 0.0),
            ("genuine", False, -1, 0.0),
            ("genuine", True, 1, 0.0),
        ]
    ):
        sid = f"s{i}"
        if label == "forged":
            (root / "train" / "report" / f"{sid}_report.md").write_text(REPORT, encoding="utf-8")
            im = Image.new("L", (10, 10), 0)
            for x in range(10):
                for y in range(int(10 * share)):
                    im.putpixel((x, y), 255)
            im.save(root / "train" / "mask" / f"{sid}_mask.png")
        rows.append([sid, label, "g", rb.MODE, True, any_flag, sev, "", ""])
    images = tmp_path / "images.csv"
    with images.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "sample_id",
                "label",
                "group",
                "mode",
                "ok",
                "pixel_any",
                "worst_severity",
                "rules",
                "iou",
            ]
        )
        w.writerows(rows)
    return root, images


def test_breakdown_counts_forged_by_size_and_references_pristine(tmp_path: Path) -> None:
    root, images = build(tmp_path)
    forged, genuine = rb.classify(rb.load_rows(images, rb.MODE), root)
    assert len(forged) == 2 and len(genuine) == 2
    assert sorted(round(i["share"], 1) for i in forged) == [0.0, 0.5]
    text = rb.build_markdown(forged, genuine)
    assert "1/2 = 0.50" in text  # false alarms on the two pristine images
    assert "2% or more" in text and "below 0.5%" in text
    assert "`visual_clumsy` 2" in text


def test_unsafe_sample_ids_are_skipped(tmp_path: Path) -> None:
    root, images = build(tmp_path)
    rows = list(csv.reader(images.open(encoding="utf-8")))
    rows.append(["../evil", "forged", "g", rb.MODE, "True", "True", "2", "", ""])
    with images.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    forged, _ = rb.classify(rb.load_rows(images, rb.MODE), root)
    assert len(forged) == 2
