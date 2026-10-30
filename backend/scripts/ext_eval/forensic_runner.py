"""External forensic runner: the repo's image-forensics checks on externally obtained forged and genuine
images, at image level.

For each image the real checks run on a one-file case (``app.core.testing.make_ctx``, Fake LLM, no OCR,
no network): ``ela_region``, ``copy_move`` and ``noise_mismatch`` (pixel family) and, on the untouched
file only, ``image_editor_software``, ``image_modified`` and ``ai_provenance_hint`` (metadata family).
Reported per dataset and normalisation mode, with Wilson 95% intervals:

* false-alarm rate on genuine images and detection rate on forged images, each at three strictness
  levels (any non-info flag, medium or high, high only) and per rule; the difference (Youden J) shows
  whether a detector separates the classes at all;
* localisation on forged images that have masks: share of detected images where some flagged region
  lies on tampered pixels (>= 30% of the region inside the mask), pointing-game hit (region centre in
  the mask) next to the chance rate for random boxes, and pixel IoU/precision/recall of the union of
  flagged regions against the mask.

Normalisation (``--normalise``) removes dataset leaks before the checks run:

* ``raw``: the file as distributed (container leaks stay; metadata checks run here only);
* ``jpeg90``: every image re-encoded as JPEG q90 at full resolution (removes the container leak, keeps
  the compression history of the original, which can still leak);
* ``resize1600_jpeg85`` (default): long side resized to 1600 then JPEG q85 (resampling destroys the
  original block grid, so both classes look singly compressed; this also removes real double-JPEG
  evidence, so it measures what survives re-compression).

Datasets (``--dataset``): ``realtext`` (RealText-V2 sample, obtained) and ``folder`` (any dataset
converted to ``labels.csv``; see the README). Adapters for Find-It-Again, AIForge-Doc v2 and SIDTD are
NOT included because none of them could be downloaded: convert them to the folder format by hand.

    uv run python backend/scripts/ext_eval/forensic_runner.py run --dataset realtext \
        --root $EXTERNAL_DATA/realtext-v2 --limit-per-class 5 --workers 4

Images are untrusted data files: read only with Pillow (decoded, never executed), never opened by
anything but this script, which does not trust file names (ids are sanitised).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from scripts._eval import metrics as mx  # noqa: E402
from scripts._eval.runner import run_pool  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
NORMALISE_MODES = ("raw", "jpeg90", "resize1600_jpeg85")
PIXEL_RULES = ("ela_region", "copy_move", "noise_mismatch")
META_RULES = ("image_editor_software", "image_modified", "ai_provenance_hint")
SEV_RANK = mx.SEV_RANK
MASK_MIN_OVERLAP = 0.30  # same threshold as the main eval's localisation (metrics.is_localised)
MAX_PIXELS = 60_000_000  # never decode more (decompression-bomb guard; the check has its own too)

DATASET_NOTES: dict[str, dict[str, str]] = {
    "realtext": {
        "title": "RealText-V2 (sample: 70 forged + 45 pristine, English, Malay, Indonesian)",
        "represents": (
            "Character, word and semantic edits to real-world and AI-synthesised text images, with "
            "pixel masks. Generic documents (agendas, forms, posters, web pages), about 2500 x 3500 px."
        ),
        "does_not": (
            "Financial documents, scans or phone photos of printed bank statements, payslips or IDs. "
            "Not our document types, not our threat model. The sample is not a held-out test set of "
            "ours and the public data has no test split. The forged class is .jpg and the pristine "
            "class is .png (a format leak, hence `raw` is reported only as a labelled leaky row). "
            "Forgeries are largely AI-regenerated text, not splices of the kind ELA/copy-move target, "
            "and the report text is LLM-written. CC BY-NC 4.0."
        ),
    },
    "folder": {
        "title": "Folder dataset (converted by hand)",
        "represents": "Whatever the converter wrote into labels.csv.",
        "does_not": "Anything not in that dataset's own documentation; see its README.",
    },
}


# ------------------------------------------------------------------------------ datasets


@dataclass(frozen=True)
class Sample:
    sample_id: str
    label: str  # "forged" | "genuine"
    path: str
    mask_path: str | None = None
    group: str = ""  # e.g. language, document type


def safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)[:80]


def _pick(samples: list[Sample], limit: int | None) -> list[Sample]:
    """Deterministic subsample per class (hash order, not file order), or everything."""
    if not limit:
        return sorted(samples, key=lambda s: s.sample_id)
    out: list[Sample] = []
    for label in ("forged", "genuine"):
        cls = [s for s in samples if s.label == label]
        cls.sort(key=lambda s: hashlib.sha256(s.sample_id.encode()).hexdigest())
        out += cls[:limit]
    return sorted(out, key=lambda s: s.sample_id)


def load_realtext(root: Path, limit_per_class: int | None = None) -> list[Sample]:
    """RealText-V2 sample from ``sample_manifest.tsv`` (sample_id, type black|white, part, image, mask,
    report). ``black`` = forged (has a mask), ``white`` = pristine."""
    manifest = root / "sample_manifest.tsv"
    lang: dict[str, str] = {}
    meta = root / "metadata.csv"
    if meta.exists():
        with meta.open(newline="", encoding="utf-8") as fh:
            csv.field_size_limit(1 << 28)
            for row in csv.DictReader(fh):
                lang[row["sample_id"]] = row.get("language", "")
    out: list[Sample] = []
    with manifest.open(newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if len(row) < 5:
                continue
            sid, kind, _part, image, mask = row[:5]
            forged = kind == "black"
            mpath = root / "train" / "mask" / mask
            out.append(
                Sample(
                    safe_id(sid),
                    "forged" if forged else "genuine",
                    str(root / "train" / "image" / image),
                    str(mpath) if forged and mpath.exists() else None,
                    lang.get(sid, ""),
                )
            )
    return _pick(out, limit_per_class)


def load_folder(root: Path, limit_per_class: int | None = None) -> list[Sample]:
    """``<root>/labels.csv`` with columns ``path,label[,mask][,group]``; paths relative to ``root``.
    ``label`` is ``forged`` or ``genuine``. ``mask`` is a mask image (nonzero = tampered), or a VIA
    JSON export (``*.json``) whose rectangles/polygons for that image become the mask."""
    out: list[Sample] = []
    with (root / "labels.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = row["label"].strip().lower()
            if label not in ("forged", "genuine"):
                raise ValueError(f"label must be forged or genuine, got {label!r}")
            mask = (row.get("mask") or "").strip()
            out.append(
                Sample(
                    safe_id(Path(row["path"]).stem),
                    label,
                    str(root / row["path"]),
                    str(root / mask) if mask else None,
                    (row.get("group") or "").strip(),
                )
            )
    return _pick(out, limit_per_class)


def via_regions_to_mask(
    via: dict[str, Any], filename: str, size: tuple[int, int]
) -> np.ndarray | None:
    """Boolean mask (h, w) from a VIA 2.x JSON export for ``filename``; rect, polygon and ellipse
    regions are filled. ``None`` when the file has no regions. Written from the VIA format
    description; not exercised on real Find-It-Again data (not obtainable)."""
    import cv2

    meta = via.get("_via_img_metadata", via)
    entry = next((v for v in meta.values() if Path(v.get("filename", "")).name == filename), None)
    if not entry or not entry.get("regions"):
        return None
    w, h = size
    mask = np.zeros((h, w), np.uint8)
    regions = entry["regions"]
    regions = list(regions.values()) if isinstance(regions, dict) else regions
    for r in regions:
        sa = r.get("shape_attributes", {})
        shape = sa.get("name")
        if shape == "rect":
            x, y = int(sa["x"]), int(sa["y"])
            cv2.rectangle(mask, (x, y), (x + int(sa["width"]), y + int(sa["height"])), 1, -1)
        elif shape == "polygon":
            pts = np.array(list(zip(sa["all_points_x"], sa["all_points_y"], strict=True)), np.int32)
            cv2.fillPoly(mask, [pts], 1)
        elif shape == "ellipse":
            cv2.ellipse(
                mask,
                (int(sa["cx"]), int(sa["cy"])),
                (int(sa["rx"]), int(sa["ry"])),
                float(np.degrees(sa.get("theta", 0.0))),
                0,
                360,
                1,
                -1,
            )
    return mask.astype(bool) if mask.any() else None


def load_mask(sample: Sample, size: tuple[int, int]) -> np.ndarray | None:
    """Boolean mask of the tampered pixels at ``size`` (w, h), nearest-neighbour resampled."""
    if not sample.mask_path:
        return None
    path = Path(sample.mask_path)
    if path.suffix.lower() == ".json":
        via = json.loads(path.read_text(encoding="utf-8"))
        with Image.open(sample.path) as im:
            full = im.size
        m = via_regions_to_mask(via, Path(sample.path).name, full)
        if m is None:
            return None
        img = Image.fromarray(m.astype(np.uint8) * 255, "L")
    else:
        with Image.open(path) as im:
            img = im.convert("L")
    if img.size != size:
        img = img.resize(size, Image.Resampling.NEAREST)
    return np.asarray(img) > 127


# ------------------------------------------------------------------------------ normalisation


def prepare_image(src: str, mode: str, dest: Path) -> tuple[Path, tuple[int, int]]:
    """Write the analysed copy of ``src`` under ``dest`` per ``mode``; returns (path, (w, h))."""
    if mode not in NORMALISE_MODES:
        raise ValueError(f"unknown normalisation {mode!r}")
    if mode == "raw":
        if Path(src).suffix.lower() not in (".jpg", ".jpeg", ".png"):
            raise ValueError("raw mode needs a JPEG or PNG file (the checks accept nothing else)")
        target = dest / ("img" + Path(src).suffix.lower())
        shutil.copyfile(src, target)
        with Image.open(target) as im:
            return target, im.size
    with Image.open(src) as im:
        if im.width * im.height > MAX_PIXELS:
            raise ValueError("image above the decode limit")
        rgb = im.convert("RGB")
    if mode == "resize1600_jpeg85":
        long_side = max(rgb.size)
        if long_side > 1600:
            f = 1600 / long_side
            rgb = rgb.resize(
                (max(1, round(rgb.width * f)), max(1, round(rgb.height * f))),
                Image.Resampling.LANCZOS,
            )
        quality = 85
    else:
        quality = 90
    target = dest / "img.jpg"
    rgb.save(target, format="JPEG", quality=quality)
    return target, rgb.size


# ------------------------------------------------------------------------------ analysis


def _run_checks(path: Path, rules: tuple[str, ...]) -> dict[str, Any]:
    """Run the named image-forensics checks on one file; returns flags and per-rule status."""
    from app.checks import forensics_image as fi
    from app.checks.registry import CheckSkipped
    from app.core.models import FileRef, PageInfo
    from app.core.testing import make_ctx, make_doc

    data = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    ref = FileRef(
        id="f1",
        name=path.name,
        sha256="0" * 64,
        size_bytes=len(data),
        mime=mime,  # type: ignore[arg-type]
        pages=[
            PageInfo(
                page=1, width_pt=595, height_pt=842, rotation=0, has_text_layer=False, is_scan=True
            )
        ],
    )
    ctx = make_ctx([make_doc("bank_statement", id="d1", file_id="f1")], files=[(ref, data)])
    flags: list[dict[str, Any]] = []
    status: dict[str, str] = {}
    for rule in rules:
        try:
            found = getattr(fi, rule)(ctx)
            status[rule] = "ran"
        except CheckSkipped as exc:
            status[rule] = f"skipped: {exc}"
            continue
        except Exception as exc:  # a check crash is recorded, never hidden
            status[rule] = f"error: {type(exc).__name__}: {str(exc)[:80]}"
            continue
        for f in found:
            regions = ([f.region] if f.region else []) + list(f.extra_regions)
            flags.append(
                {
                    "rule": f.rule,
                    "severity": f.severity,
                    "notice": f.severity == "info" and f.id.endswith("~notice"),
                    "regions": [
                        {"x": r.x, "y": r.y, "w": r.w, "h": r.h, "label": r.label} for r in regions
                    ],
                    "evidence": (f.evidence or "")[:160],
                }
            )
    return {"flags": flags, "status": status}


def localisation(regions: list[dict[str, float]], mask: np.ndarray) -> dict[str, float | bool]:
    """Region boxes (page fractions) against a boolean mask (h, w)."""
    h, w = mask.shape
    pred = np.zeros((h, w), bool)
    hit_any = point_hit = False
    for r in regions:
        x0, y0 = max(0, int(r["x"] * w)), max(0, int(r["y"] * h))
        x1, y1 = (
            min(w, int(np.ceil((r["x"] + r["w"]) * w))),
            min(h, int(np.ceil((r["y"] + r["h"]) * h))),
        )
        if x1 <= x0 or y1 <= y0:
            continue
        pred[y0:y1, x0:x1] = True
        inside = mask[y0:y1, x0:x1].mean()
        hit_any = hit_any or bool(inside >= MASK_MIN_OVERLAP)
        cy, cx = min(h - 1, (y0 + y1) // 2), min(w - 1, (x0 + x1) // 2)
        point_hit = point_hit or bool(mask[cy, cx])
    inter = int((pred & mask).sum())
    union = int((pred | mask).sum())
    return {
        "hit_any": hit_any,
        "point_hit": point_hit,
        "iou": inter / union if union else 0.0,
        "pixel_precision": inter / int(pred.sum()) if pred.any() else 0.0,
        "pixel_recall": inter / int(mask.sum()) if mask.any() else 0.0,
        "mask_share": float(mask.mean()),
        "pred_share": float(pred.mean()),
    }


def analyse_job(job: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry: one (sample, mode). Never raises."""
    started = time.perf_counter()
    base = {
        "sample_id": job["sample_id"],
        "label": job["label"],
        "mode": job["mode"],
        "group": job["group"],
    }
    tmp = Path(tempfile.mkdtemp(prefix="gldb-ext-forensics-"))
    try:
        path, size = prepare_image(job["path"], job["mode"], tmp)
        rules = PIXEL_RULES + (META_RULES if job["mode"] == "raw" else ())
        res = _run_checks(path, rules)
        det = [f for f in res["flags"] if not f["notice"] and f["severity"] != "info"]
        worst = max((SEV_RANK[f["severity"]] for f in det), default=-1)
        out: dict[str, Any] = {
            **base,
            "ok": True,
            "size": list(size),
            "status": res["status"],
            "notices": [f["evidence"] for f in res["flags"] if f["notice"]],
            "flags": [{k: f[k] for k in ("rule", "severity")} for f in det],
            "pixel_any": any(f["rule"] in PIXEL_RULES for f in det),
            "pixel_ge_medium": any(
                f["rule"] in PIXEL_RULES and SEV_RANK[f["severity"]] >= SEV_RANK["medium"]
                for f in det
            ),
            "pixel_high": any(f["rule"] in PIXEL_RULES and f["severity"] == "high" for f in det),
            "meta_any": any(f["rule"] in META_RULES for f in res["flags"] if not f["notice"]),
            "worst": worst,
            "analysed": any(s == "ran" for r, s in res["status"].items() if r in PIXEL_RULES),
        }
        out["rules"] = sorted({f["rule"] for f in det})
        if job.get("mask_path"):
            sample = Sample(job["sample_id"], job["label"], job["path"], job.get("mask_path"))
            mask = load_mask(sample, size)
            if mask is not None:
                regions = [
                    r
                    for f in res["flags"]
                    if not f["notice"] and f["rule"] in PIXEL_RULES
                    for r in f["regions"]
                ]
                out["loc"] = (
                    localisation(regions, mask) if regions else {"mask_share": float(mask.mean())}
                )
        out["seconds"] = round(time.perf_counter() - started, 2)
        return out
    except Exception as exc:  # reported per image, never swallowed
        return {**base, "ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------------------ aggregation


def _rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = mx.wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n if n else None), "lo": lo, "hi": hi}


LEVELS = (
    ("any", "pixel_any", "any pixel flag (low, medium or high)"),
    ("ge_medium", "pixel_ge_medium", "medium or high"),
    ("high", "pixel_high", "high only (two detectors agree)"),
)


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per normalisation mode: rates with Wilson intervals, per-rule rates, Youden J, localisation."""
    out: dict[str, Any] = {}
    for mode in sorted({r["mode"] for r in results}, key=NORMALISE_MODES.index):
        rs = [r for r in results if r["mode"] == mode]
        ok = [r for r in rs if r.get("ok")]
        gen = [r for r in ok if r["label"] == "genuine"]
        forged = [r for r in ok if r["label"] == "forged"]
        row: dict[str, Any] = {
            "images": len(rs),
            "errors": [{"id": r["sample_id"], "error": r["error"]} for r in rs if not r.get("ok")],
            "genuine": len(gen),
            "forged": len(forged),
            "not_analysable": sum(1 for r in ok if not r["analysed"]),
            "levels": {},
            "rules": {},
        }
        for name, key, text in LEVELS:
            fp = _rate(sum(1 for r in gen if r[key]), len(gen))
            tp = _rate(sum(1 for r in forged if r[key]), len(forged))
            j = (
                (tp["rate"] - fp["rate"])
                if tp["rate"] is not None and fp["rate"] is not None
                else None
            )
            row["levels"][name] = {"text": text, "false_alarm": fp, "detection": tp, "youden_j": j}
        for rule in PIXEL_RULES:
            row["rules"][rule] = {
                "false_alarm": _rate(sum(1 for r in gen if rule in r["rules"]), len(gen)),
                "detection": _rate(sum(1 for r in forged if rule in r["rules"]), len(forged)),
            }
        loc = [r["loc"] for r in forged if r.get("loc") is not None]
        detected = [x for x in loc if "iou" in x]
        row["localisation"] = {
            "forged_with_mask": len(loc),
            "detected_with_regions": len(detected),
            "hit_any": _rate(sum(1 for x in detected if x["hit_any"]), len(detected)),
            "point_hit": _rate(sum(1 for x in detected if x["point_hit"]), len(detected)),
            "chance_point_hit": mx.mean(x["mask_share"] for x in detected),
            "mean_iou": mx.mean(x["iou"] for x in detected),
            "mean_pixel_precision": mx.mean(x["pixel_precision"] for x in detected),
            "mean_pixel_recall": mx.mean(x["pixel_recall"] for x in detected),
            "mean_pred_share": mx.mean(x["pred_share"] for x in detected),
        }
        groups: dict[str, dict[str, Any]] = {}
        for g in sorted({r["group"] for r in ok if r["group"]}):
            gg = [r for r in gen if r["group"] == g]
            ff = [r for r in forged if r["group"] == g]
            groups[g] = {
                "false_alarm_any": _rate(sum(1 for r in gg if r["pixel_any"]), len(gg)),
                "detection_any": _rate(sum(1 for r in ff if r["pixel_any"]), len(ff)),
            }
        row["groups"] = groups
        row["meta"] = (
            {
                "false_alarm": _rate(sum(1 for r in gen if r["meta_any"]), len(gen)),
                "detection": _rate(sum(1 for r in forged if r["meta_any"]), len(forged)),
            }
            if mode == "raw"
            else None
        )
        row["median_seconds"] = mx.percentile_nearest_rank([r["seconds"] for r in ok], 0.5)
        out[mode] = row
    return out


def _fmt(r: dict[str, Any]) -> str:
    return mx.rate_text(r["k"], r["n"]) + (
        "" if r["n"] < 100 else f" [{r['lo']:.2f}-{r['hi']:.2f}]"
    )


def render_markdown(dataset: str, summary: dict[str, Any], meta: dict[str, Any]) -> str:
    note = DATASET_NOTES.get(dataset, DATASET_NOTES["folder"])
    L = [f"# External forensics: {note['title']}\n"]
    L.append(
        "Generated by `backend/scripts/ext_eval/forensic_runner.py` from the repo's own image-forensics "
        "checks (`ela_region`, `copy_move`, `noise_mismatch`; metadata checks on `raw` only). Image-level "
        "results; nothing was tuned on these images.\n"
    )
    L.append(f"**What it represents.** {note['represents']}\n")
    L.append(f"**What it does not represent.** {note['does_not']}\n")
    L.append(
        f"Run: {meta['images']} images, normalisation {', '.join(meta['modes'])}, seed-free and deterministic.\n"
    )
    for mode, row in summary.items():
        leaky = (
            " (LEAKY: container and compression differ between classes; do not read as detection)"
            if mode == "raw"
            else ""
        )
        L.append(f"## Normalisation `{mode}`{leaky}\n")
        L.append(
            f"{row['genuine']} genuine, {row['forged']} forged, {row['not_analysable']} not analysable "
            f"(below the size gate or all pixel checks skipped), {len(row['errors'])} errors, median "
            f"{row['median_seconds']} s per image.\n"
        )
        L.append(
            "| Strictness | False alarms on genuine | Detection of forged | Youden J (detection minus false alarm) |"
        )
        L.append("|---|---|---|---|")
        for lv in row["levels"].values():
            j = "n/a" if lv["youden_j"] is None else f"{lv['youden_j']:+.2f}"
            L.append(
                f"| {lv['text']} | {_fmt(lv['false_alarm'])} | {_fmt(lv['detection'])} | {j} |"
            )
        L.append("\n| Rule | False alarms on genuine | Detection of forged |")
        L.append("|---|---|---|")
        for rule, v in row["rules"].items():
            L.append(f"| {rule} | {_fmt(v['false_alarm'])} | {_fmt(v['detection'])} |")
        if row["meta"]:
            L.append(
                f"\nMetadata checks (editor named, dates disagree, AI generator marker): genuine "
                f"{_fmt(row['meta']['false_alarm'])}, forged {_fmt(row['meta']['detection'])}. These look at "
                "file metadata, which public datasets usually strip.\n"
            )
        loc = row["localisation"]
        if loc["forged_with_mask"]:
            md = lambda v: "n/a" if v is None else f"{v:.3f}"  # noqa: E731
            L.append(
                f"\nLocalisation on {loc['detected_with_regions']} detected forged images with masks "
                f"(of {loc['forged_with_mask']} with masks): region on tampered pixels "
                f"{_fmt(loc['hit_any'])}; pointing-game {_fmt(loc['point_hit'])} versus chance "
                f"{md(loc['chance_point_hit'])} (mean mask share of the page); mean IoU {md(loc['mean_iou'])}, "
                f"pixel precision {md(loc['mean_pixel_precision'])}, pixel recall {md(loc['mean_pixel_recall'])}, "
                f"flagged share of page {md(loc['mean_pred_share'])}.\n"
            )
        if row["groups"]:
            L.append("| Group | False alarm (any) | Detection (any) |")
            L.append("|---|---|---|")
            for g, v in row["groups"].items():
                L.append(f"| {g} | {_fmt(v['false_alarm_any'])} | {_fmt(v['detection_any'])} |")
            L.append("")
        if row["errors"]:
            L.append(
                "Errors: "
                + "; ".join(f"{e['id']}: {e['error']}" for e in row["errors"][:10])
                + "\n"
            )
    L.append("## How to read this\n")
    L.append(
        "- A detector that fires on genuine images as often as on forged ones (Youden J near 0) carries no "
        "signal here, whatever its detection rate. The pixel checks were built for splices and clones in "
        "JPEG scans of statements; AI-regenerated text, in-painting and clean re-typesetting leave none of "
        "those traces, so a low detection rate on such forgeries is expected and is a limit of the tool, "
        "not of the data.\n"
        "- Wilson intervals are shown below 100 images per class; with the sample sizes here they are wide.\n"
        "- Image-level results are not case-level results: the tool's score uses corroboration across "
        "detectors and documents, and a lone ELA or copy-move region is `low`.\n"
    )
    return "\n".join(L) + "\n"


def write_outputs(out: Path, dataset: str, results: list[dict[str, Any]], modes: list[str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    summary = summarise(results)
    meta = {"images": len({r["sample_id"] for r in results}), "modes": modes}
    (out / f"forensics-{dataset}.md").write_text(
        render_markdown(dataset, summary, meta), encoding="utf-8"
    )
    (out / f"forensics-{dataset}.json").write_text(
        json.dumps({"summary": summary, "images": results}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (out / f"forensics-{dataset}.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "mode",
                "strictness",
                "genuine_n",
                "false_alarms",
                "fa_rate",
                "fa_lo",
                "fa_hi",
                "forged_n",
                "detected",
                "det_rate",
                "det_lo",
                "det_hi",
                "youden_j",
            ]
        )
        for mode, row in summary.items():
            for name, lv in row["levels"].items():
                fp, tp = lv["false_alarm"], lv["detection"]
                w.writerow(
                    [
                        mode,
                        name,
                        fp["n"],
                        fp["k"],
                        _f(fp["rate"]),
                        _f(fp["lo"]),
                        _f(fp["hi"]),
                        tp["n"],
                        tp["k"],
                        _f(tp["rate"]),
                        _f(tp["lo"]),
                        _f(tp["hi"]),
                        _f(lv["youden_j"]),
                    ]
                )
    with (out / f"forensics-{dataset}-images.csv").open("w", newline="", encoding="utf-8") as fh:
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
        for r in sorted(results, key=lambda r: (r["mode"], r["sample_id"])):
            w.writerow(
                [
                    r["sample_id"],
                    r["label"],
                    r["group"],
                    r["mode"],
                    r.get("ok"),
                    r.get("pixel_any"),
                    r.get("worst"),
                    "+".join(r.get("rules", [])),
                    _f((r.get("loc") or {}).get("iou")),
                ]
            )


def _f(v: float | None) -> str:
    return "" if v is None else f"{v:.4f}"


# ------------------------------------------------------------------------------ CLI


def run(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    samples = (load_realtext if args.dataset == "realtext" else load_folder)(
        root, args.limit_per_class
    )
    if not samples:
        print("no samples found", file=sys.stderr)
        return 1
    jobs = [
        {
            "sample_id": s.sample_id,
            "label": s.label,
            "path": s.path,
            "mask_path": s.mask_path,
            "group": s.group,
            "mode": mode,
        }
        for mode in args.normalise
        for s in samples
    ]
    print(
        f"{len(samples)} images x {len(args.normalise)} mode(s) = {len(jobs)} analyses",
        file=sys.stderr,
    )
    results = run_pool(analyse_job, jobs, args.workers)
    write_outputs(args.out.resolve(), args.dataset, results, list(args.normalise))
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dataset", choices=["realtext", "folder"], required=True)
    r.add_argument("--root", type=Path, required=True, help="dataset folder (read as data only)")
    r.add_argument("--out", type=Path, default=REPO / "docs" / "eval-results" / "external")
    r.add_argument(
        "--normalise",
        nargs="+",
        choices=NORMALISE_MODES,
        default=["resize1600_jpeg85", "jpeg90", "raw"],
    )
    r.add_argument("--limit-per-class", type=int, default=None)
    r.add_argument("--workers", type=int, default=4)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
