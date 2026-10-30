"""Deterministic print-scan / camera SIMULATOR (SIMULATED: a physical print-and-scan was not possible).

Takes the digital PDF pages of a generated bundle and degrades each page with a seeded pipeline:

    render (200 dpi) -> ink coverage -> bleed + dot gain -> halftone screen -> ink on tinted paper
    -> paper texture -> rotation + perspective warp (+ desk border) -> uneven light, vignette, shadows
    -> defocus + motion blur -> downscale to the output dpi -> sensor noise (Gaussian + shot)
    -> JPEG recompression chain (re-saves, one optional resize)

Three severity levels (``light``, ``med``, ``heavy``) draw every parameter uniformly from the ranges
in ``LEVELS`` (the table is also printed by ``describe_levels()`` and copied into the README).
The same ``(seed, level, file, page)`` always gives byte-identical pixels.

What this is not: it is a software model of the artefacts, not a physical scan. It cannot reproduce
a real scanner's firmware sharpening, a real printer's halftone/ink behaviour, lens distortion,
auto-exposure, HDR/AI "beautify" filters or messaging-app transcoding. Colour is discarded (grey ink
on tinted paper). Results measured on these renders are indicative of robustness to the listed
artefacts only.

CLI (from the repo root):

    uv run python backend/scripts/ext_eval/scan_sim.py build --bundle samples/bundles/loan-clean \
        --out samples/sim --levels light med heavy
    uv run python backend/scripts/ext_eval/scan_sim.py preview --pdf some.pdf --level heavy --out p.jpg
    uv run python backend/scripts/ext_eval/scan_sim.py levels
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402

SIM_LABEL = "SIMULATED print-scan (gldb ext_eval); not a physical scan"
SRC_DPI = 200
A4_IN = (8.27, 11.69)
A4_PT = (595.0, 842.0)
PAPER_RGB = (244.0, 242.0, 235.0)  # slightly warm office paper
INK_RGB = (22.0, 22.0, 26.0)
LID_RGB = (226.0, 226.0, 224.0)  # scanner lid / platen seen outside the page
DESK_RGB = (104.0, 86.0, 72.0)  # wooden desk behind a photographed page
FIXED_PDF_DATE = "D:20261027090000Z"

Range = tuple[float, float]


@dataclass(frozen=True)
class Level:
    name: str
    story: str
    out_dpi: int
    rot_deg: Range  # |rotation| in degrees, random sign
    persp: Range  # corner jitter as a fraction of the page size
    border: Range  # fraction of the canvas left as background on every side
    dot_gain: Range  # extra ink coverage at mid tones (0.1 = 10 points)
    halftone: Range  # 0 = continuous tone, 1 = full screen
    halftone_cell_px: Range  # screen period at 200 dpi
    paper_texture: Range  # relative amplitude of grain + mottling
    light_gradient: Range  # relative brightness change across the page
    vignette: Range
    shadow: Range  # darkening of an edge shadow and a blob (0 = none)
    defocus_r: Range  # disk blur radius at 200 dpi
    motion_len: Range  # linear motion blur length at 200 dpi (0 = none)
    noise_sigma: Range  # Gaussian sensor noise, 0-255 units
    shot_noise: Range  # signal-dependent noise factor
    jpeg_chain: tuple[tuple[float, int], ...]  # (resize factor, quality) per re-save


LEVELS: dict[str, Level] = {
    "light": Level(
        name="light",
        story="decent flatbed scan",
        out_dpi=200,
        rot_deg=(0.0, 0.5),
        persp=(0.0, 0.0),
        border=(0.0, 0.0),
        dot_gain=(0.02, 0.06),
        halftone=(0.0, 0.0),
        halftone_cell_px=(5.0, 5.0),
        paper_texture=(0.008, 0.02),
        light_gradient=(0.02, 0.06),
        vignette=(0.0, 0.0),
        shadow=(0.0, 0.0),
        defocus_r=(0.3, 0.7),
        motion_len=(0.0, 0.0),
        noise_sigma=(1.5, 3.0),
        shot_noise=(0.2, 0.5),
        jpeg_chain=((1.0, 88),),
    ),
    "med": Level(
        name="med",
        story="office MFP scan or careful phone photo",
        out_dpi=150,
        rot_deg=(0.4, 1.5),
        persp=(0.004, 0.012),
        border=(0.0, 0.02),
        dot_gain=(0.06, 0.12),
        halftone=(0.25, 0.45),
        halftone_cell_px=(5.0, 7.0),
        paper_texture=(0.02, 0.04),
        light_gradient=(0.06, 0.15),
        vignette=(0.05, 0.12),
        shadow=(0.0, 0.12),
        defocus_r=(0.7, 1.2),
        motion_len=(0.0, 3.0),
        noise_sigma=(3.0, 5.0),
        shot_noise=(0.5, 1.0),
        jpeg_chain=((1.0, 85), (1.0, 70)),
    ),
    "heavy": Level(
        name="heavy",
        story="hurried phone photo, low resolution, forwarded through a messaging app",
        out_dpi=100,
        rot_deg=(1.5, 3.5),
        persp=(0.015, 0.035),
        border=(0.03, 0.07),
        dot_gain=(0.12, 0.2),
        halftone=(0.5, 0.8),
        halftone_cell_px=(4.0, 6.0),
        paper_texture=(0.04, 0.07),
        light_gradient=(0.15, 0.3),
        vignette=(0.15, 0.3),
        shadow=(0.15, 0.35),
        defocus_r=(1.2, 2.0),
        motion_len=(2.0, 7.0),
        noise_sigma=(5.0, 8.0),
        shot_noise=(1.0, 2.0),
        jpeg_chain=((1.0, 80), (0.8, 60), (1.0, 50)),
    ),
}
LEVEL_NAMES = tuple(LEVELS)


def describe_levels() -> str:
    """The parameter table as Markdown (kept in sync with the README by a test)."""
    fields = [
        ("out_dpi", "output resolution (dpi)"),
        ("rot_deg", "rotation magnitude (deg, random sign)"),
        ("persp", "perspective corner jitter (fraction of page)"),
        ("border", "background border (fraction of canvas)"),
        ("dot_gain", "dot gain (extra mid-tone ink coverage)"),
        ("halftone", "halftone blend (0 none, 1 full screen)"),
        ("halftone_cell_px", "halftone period (px at 200 dpi)"),
        ("paper_texture", "paper grain and mottling amplitude"),
        ("light_gradient", "uneven lighting (relative)"),
        ("vignette", "vignette strength"),
        ("shadow", "shadow darkening"),
        ("defocus_r", "defocus disk radius (px at 200 dpi)"),
        ("motion_len", "motion blur length (px at 200 dpi)"),
        ("noise_sigma", "Gaussian sensor noise (0-255)"),
        ("shot_noise", "signal-dependent noise factor"),
    ]

    def fmt(v: Any) -> str:
        if isinstance(v, int):
            return str(v)
        lo, hi = v
        return f"{lo:g}" if lo == hi else f"{lo:g} to {hi:g}"

    lines = ["| Parameter | " + " | ".join(LEVEL_NAMES) + " |", "|---|" + "---|" * len(LEVEL_NAMES)]
    lines.append("| story | " + " | ".join(LEVELS[n].story for n in LEVEL_NAMES) + " |")
    for key, label in fields:
        lines.append(
            f"| {label} | " + " | ".join(fmt(getattr(LEVELS[n], key)) for n in LEVEL_NAMES) + " |"
        )
    chains = []
    for n in LEVEL_NAMES:
        chains.append(
            " then ".join(
                (f"resize x{s:g}, " if s != 1.0 else "") + f"q{q}" for s, q in LEVELS[n].jpeg_chain
            )
        )
    lines.append("| JPEG chain | " + " | ".join(chains) + " |")
    return "\n".join(lines)


# ------------------------------------------------------------------------------ random helpers


def _rng(key: str) -> np.random.Generator:
    digest = hashlib.sha256(key.encode()).digest()
    return np.random.default_rng(int.from_bytes(digest[:8], "big"))


def _u(rng: np.random.Generator, r: Range) -> float:
    return float(rng.uniform(r[0], r[1])) if r[1] > r[0] else float(r[0])


def _lowfreq(rng: np.random.Generator, h: int, w: int, cells: int) -> np.ndarray:
    """Smooth random field in about [-1, 1]: a coarse grid of random values, cubic-upscaled."""
    grid = rng.uniform(-1.0, 1.0, size=(cells, max(2, round(cells * w / h)))).astype(np.float32)
    return cv2.resize(grid, (w, h), interpolation=cv2.INTER_CUBIC)


def _disk_kernel(radius: float) -> np.ndarray:
    size = int(math.ceil(radius)) * 2 + 1
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = size // 2
    k = ((xx - c) ** 2 + (yy - c) ** 2 <= radius * radius + 0.25).astype(np.float32)
    return k / k.sum()


def _motion_kernel(length: float, angle_deg: float) -> np.ndarray:
    size = int(math.ceil(length)) | 1
    size = max(size, 3)
    k = np.zeros((size, size), np.float32)
    c = size // 2
    dx, dy = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    cv2.line(
        k,
        (round(c - dx * length / 2), round(c - dy * length / 2)),
        (round(c + dx * length / 2), round(c + dy * length / 2)),
        1.0,
        1,
    )
    if k.sum() == 0:
        k[c, c] = 1.0
    return k / k.sum()


# ------------------------------------------------------------------------------ the pipeline


def degrade_page(
    rgb: np.ndarray, level: str | Level, key: str
) -> tuple[np.ndarray, dict[str, Any]]:
    """Degrade one page (uint8 RGB rendered at ``SRC_DPI``). Returns (uint8 RGB at the level's
    output dpi, the parameters drawn for this page). Deterministic in ``(level, key)``."""
    lv = LEVELS[level] if isinstance(level, str) else level
    rng = _rng(f"{lv.name}:{key}")
    h, w = rgb.shape[:2]
    p: dict[str, Any] = {
        "rot_deg": _u(rng, lv.rot_deg) * (1 if rng.random() < 0.5 else -1),
        "persp": _u(rng, lv.persp),
        "border": _u(rng, lv.border),
        "dot_gain": _u(rng, lv.dot_gain),
        "halftone": _u(rng, lv.halftone),
        "halftone_cell_px": _u(rng, lv.halftone_cell_px),
        "paper_texture": _u(rng, lv.paper_texture),
        "light_gradient": _u(rng, lv.light_gradient),
        "vignette": _u(rng, lv.vignette),
        "shadow": _u(rng, lv.shadow),
        "defocus_r": _u(rng, lv.defocus_r),
        "motion_len": _u(rng, lv.motion_len),
        "motion_angle": float(rng.uniform(0, 180)),
        "noise_sigma": _u(rng, lv.noise_sigma),
        "shot_noise": _u(rng, lv.shot_noise),
    }

    # 1. print: ink coverage with bleed, dot gain and an optional halftone screen
    gray = rgb.astype(np.float32).mean(axis=2) / 255.0
    cov = 1.0 - gray
    cov = cv2.GaussianBlur(cov, (0, 0), 0.5)
    g = p["dot_gain"]
    cov = np.clip(cov + g * 4.0 * cov * (1.0 - cov), 0.0, 1.0)
    if p["halftone"] > 0:
        cell = max(2.0, p["halftone_cell_px"])
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        ca, sa = math.cos(math.radians(45)), math.sin(math.radians(45))
        u, v = (xx * ca + yy * sa) / cell, (-xx * sa + yy * ca) / cell
        screen = 0.5 + 0.24 * (np.cos(2 * np.pi * u) + np.cos(2 * np.pi * v))
        hard = np.clip((cov - screen) * 25.0 + 0.5, 0.0, 1.0)
        cov = (1.0 - p["halftone"]) * cov + p["halftone"] * hard
    paper = np.array(PAPER_RGB, np.float32)
    ink = np.array(INK_RGB, np.float32)
    img = paper[None, None, :] * (1.0 - cov[..., None]) + ink[None, None, :] * cov[..., None]

    # 2. paper texture: fine grain plus low-frequency mottling
    if p["paper_texture"] > 0:
        grain = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.2) * 2.5
        mottle = _lowfreq(rng, h, w, 14)
        tex = 1.0 + p["paper_texture"] * (0.6 * grain + 1.0 * mottle)
        img *= tex[..., None]

    # 3. geometry: rotation, perspective, and a background border
    b = p["border"]
    sw, sh = w * (1 - 2 * b), h * (1 - 2 * b)
    ox, oy = (w - sw) / 2, (h - sh) / 2
    src_pts = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    dst = np.array([[ox, oy], [ox + sw, oy], [ox + sw, oy + sh], [ox, oy + sh]], np.float32)
    if p["persp"] > 0:
        jit = (rng.uniform(-1, 1, (4, 2)) * p["persp"] * np.array([w, h])).astype(np.float32)
        dst = dst + jit
    m_p = cv2.getPerspectiveTransform(src_pts, dst)
    rot = cv2.getRotationMatrix2D((w / 2, h / 2), p["rot_deg"], 1.0)
    m = np.vstack([rot, [0, 0, 1]]) @ m_p
    bg = DESK_RGB if lv.name == "heavy" else LID_RGB
    img = cv2.warpPerspective(
        img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=bg
    )

    # 4. uneven lighting, vignette and shadows
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    light = np.ones((h, w), np.float32)
    if p["light_gradient"] > 0:
        theta = rng.uniform(0, 2 * np.pi)
        proj = ((xx / w - 0.5) * math.cos(theta) + (yy / h - 0.5) * math.sin(theta)) * 2.0
        light *= 1.0 + p["light_gradient"] * (0.7 * proj + 0.3 * _lowfreq(rng, h, w, 4))
    if p["vignette"] > 0:
        r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
        light *= 1.0 - p["vignette"] * np.clip(r - 0.3, 0.0, 1.0)
    if p["shadow"] > 0:
        theta = rng.uniform(0, 2 * np.pi)
        proj = (xx / w - 0.5) * math.cos(theta) + (yy / h - 0.5) * math.sin(theta)
        edge = 1.0 / (1.0 + np.exp(-(proj - rng.uniform(0.15, 0.35)) / 0.04))
        cx, cy = rng.uniform(0.2, 0.8) * w, rng.uniform(0.2, 0.8) * h
        sig = rng.uniform(0.08, 0.16) * max(w, h)
        blob = np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sig * sig)))
        light *= 1.0 - p["shadow"] * np.clip(edge + 0.6 * blob, 0.0, 1.0)
    img *= light[..., None]

    # 5. defocus + motion blur
    if p["defocus_r"] > 0.4:
        img = cv2.filter2D(img, -1, _disk_kernel(p["defocus_r"]), borderType=cv2.BORDER_REFLECT)
    elif p["defocus_r"] > 0:
        img = cv2.GaussianBlur(img, (0, 0), p["defocus_r"])
    if p["motion_len"] >= 1.5:
        img = cv2.filter2D(
            img,
            -1,
            _motion_kernel(p["motion_len"], p["motion_angle"]),
            borderType=cv2.BORDER_REFLECT,
        )

    # 6. low dpi: downscale to the output resolution
    if lv.out_dpi != SRC_DPI:
        ow, oh = round(A4_IN[0] * lv.out_dpi), round(A4_IN[1] * lv.out_dpi)
        img = cv2.resize(img, (ow, oh), interpolation=cv2.INTER_AREA)

    # 7. sensor noise: Gaussian plus signal-dependent shot noise
    img = np.clip(img, 0.0, 255.0)
    shot = (
        rng.normal(0, 1, img.shape).astype(np.float32)
        * np.sqrt(img / 255.0)
        * p["shot_noise"]
        * 2.0
    )
    img = img + rng.normal(0, p["noise_sigma"], img.shape).astype(np.float32) + shot
    out = np.clip(img + 0.5, 0, 255).astype(np.uint8)
    return out, p


def jpeg_chain(img: np.ndarray, chain: tuple[tuple[float, int], ...], comment: str) -> bytes:
    """Re-save ``img`` through each ``(resize factor, quality)`` step; the last save is returned."""
    cur = Image.fromarray(img, "RGB")
    data = b""
    for i, (scale, quality) in enumerate(chain):
        if scale != 1.0:
            cur = cur.resize(
                (max(1, round(cur.width * scale)), max(1, round(cur.height * scale))),
                Image.Resampling.BOX,
            )
        buf = io.BytesIO()
        last = i == len(chain) - 1
        cur.save(buf, format="JPEG", quality=quality, comment=comment.encode() if last else b"")
        data = buf.getvalue()
        cur = Image.open(io.BytesIO(data)).convert("RGB")
    return data


def render_pdf_pages(pdf: Path | bytes, dpi: int = SRC_DPI) -> list[np.ndarray]:
    doc = pymupdf.open(stream=pdf, filetype="pdf") if isinstance(pdf, bytes) else pymupdf.open(pdf)
    out: list[np.ndarray] = []
    with doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            out.append(arr[:, :, :3].copy())
    return out


def simulate_pdf_pages(
    pdf: Path | bytes, level: str, seed: int, name: str
) -> list[tuple[bytes, dict[str, Any]]]:
    """``(final JPEG bytes, drawn parameters)`` for every page of ``pdf``."""
    lv = LEVELS[level]
    out = []
    for i, page in enumerate(render_pdf_pages(pdf), start=1):
        img, params = degrade_page(page, lv, f"{seed}:{name}:{i}")
        comment = f"{SIM_LABEL}; level={level}; seed={seed}"
        out.append((jpeg_chain(img, lv.jpeg_chain, comment), params))
    return out


def jpegs_to_pdf(jpegs: list[bytes], title: str) -> bytes:
    """Image-only PDF (each JPEG embedded unchanged), producer says SIMULATED, fixed dates."""
    doc = pymupdf.open()
    for jp in jpegs:
        page = doc.new_page(width=A4_PT[0], height=A4_PT[1])
        page.insert_image(page.rect, stream=jp)
    doc.set_metadata(
        {
            "producer": SIM_LABEL,
            "creator": "",
            "title": title,
            "creationDate": FIXED_PDF_DATE,
            "modDate": FIXED_PDF_DATE,
        }
    )
    data = doc.tobytes(deflate=True, no_new_id=True)
    doc.close()
    return data


# ------------------------------------------------------------------------------ bundle builder


def render_name(level: str, container: str) -> str:
    if container not in ("pdf", "jpeg"):
        raise ValueError(f"container must be pdf or jpeg, not {container!r}")
    return f"scan_sim_{level}" + ("" if container == "pdf" else "_jpg")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _page_sha(file_sha: str, page: int) -> str:
    return hashlib.sha256(f"{file_sha}:{page}".encode()).hexdigest()


def build_sim_bundle(
    src_dir: Path,
    out_root: Path,
    *,
    levels: tuple[str, ...] = LEVEL_NAMES,
    containers: tuple[str, ...] = ("pdf",),
    seed: int = 4002,
    max_files: int | None = None,
) -> Path:
    """Write ``<out_root>/<src name>/`` with ``manifest.json``, ``labels.json``, ``ground_truth.json``
    and one folder per simulated render. ``upload_order`` lists only the simulated renders, so the
    folder is a drop-in manifest for ``real_ocr.py``. The ground truth is the source bundle's
    (tamper records included), so recall can be scored against the base bundle's simulated render."""
    src_manifest = json.loads((src_dir / "manifest.json").read_text(encoding="utf-8"))
    labels = json.loads((src_dir / "labels.json").read_text(encoding="utf-8"))
    digital = list(src_manifest["upload_order"]["digital"])
    if max_files is not None:
        digital = digital[:max_files]
    entries = {f["path"]: f for f in src_manifest["files"] if f["render"] == "digital"}
    page_labels = {(r["file"], int(r["page"])): r for r in labels["pages"] if r["file"] in entries}
    out_dir = out_root / src_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    new_labels: list[dict[str, Any]] = []
    upload: dict[str, list[str]] = {}
    sim_info: dict[str, Any] = {
        "simulated": True,
        "label": SIM_LABEL,
        "seed": seed,
        "levels": {
            n: LEVELS[n].__dict__ | {"jpeg_chain": list(LEVELS[n].jpeg_chain)} for n in levels
        },
    }
    for level in levels:
        for container in containers:
            render = render_name(level, container)
            (out_dir / render).mkdir(exist_ok=True)
            upload[render] = []
            for rel in digital:
                entry = entries[rel]
                stem = Path(rel).stem
                pages = simulate_pdf_pages(src_dir / rel, level, seed, rel)
                if container == "pdf":
                    data = jpegs_to_pdf([j for j, _ in pages], f"{SIM_LABEL}: {stem}")
                    path = f"{render}/{stem}.pdf"
                    (out_dir / path).write_bytes(data)
                    sha = _sha(data)
                    files.append(
                        _file_entry(path, render, sha, data, "application/pdf", entry, rel)
                    )
                    upload[render].append(path)
                    for i in range(len(pages)):
                        new_labels.append(_label(page_labels, rel, i + 1, path, sha, i + 1, False))
                else:
                    for i, (jp, _) in enumerate(pages):
                        path = f"{render}/{stem}_p{i + 1:02d}.jpg"
                        (out_dir / path).write_bytes(jp)
                        sha = _sha(jp)
                        files.append(
                            _file_entry(path, render, sha, jp, "image/jpeg", entry, rel, page=i + 1)
                        )
                        upload[render].append(path)
                        new_labels.append(_label(page_labels, rel, i + 1, path, sha, 1, True))
    manifest = dict(src_manifest)
    manifest["files"] = files
    manifest["upload_order"] = upload
    manifest["simulated"] = sim_info
    manifest["base_bundle"] = src_manifest.get("base_bundle")
    reg = src_manifest.get("registry_path")
    if reg:
        target = (src_dir / reg).resolve()
        manifest["registry_path"] = Path(os.path.relpath(target, out_dir.resolve())).as_posix()
    _dump(out_dir / "manifest.json", manifest)
    new_labels_doc = dict(labels)
    new_labels_doc["pages"] = new_labels
    _dump(out_dir / "labels.json", new_labels_doc)
    gt = (src_dir / "ground_truth.json").read_text(encoding="utf-8")
    (out_dir / "ground_truth.json").write_text(gt, encoding="utf-8")
    return out_dir


def _file_entry(
    path: str,
    render: str,
    sha: str,
    data: bytes,
    mime: str,
    src_entry: dict[str, Any],
    src_rel: str,
    page: int | None = None,
) -> dict[str, Any]:
    src_pages = src_entry["pages"]
    pages = [p for p in src_pages if page is None or p["page"] == page]
    return {
        "path": path,
        "render": render,
        "sha256": sha,
        "size_bytes": len(data),
        "mime": mime,
        "page_count": len(pages),
        "pages": [
            {"page": i + 1, "page_sha256": _page_sha(sha, i + 1), "doc_key": p["doc_key"]}
            for i, p in enumerate(pages)
        ],
        "doc_keys": sorted({p["doc_key"] for p in pages if p["doc_key"]}),
        "derived_from": src_rel,
        "inherited": False,
    }


def _label(
    page_labels: dict[tuple[str, int], dict[str, Any]],
    src_rel: str,
    src_page: int,
    path: str,
    sha: str,
    page: int,
    per_page_file: bool,
) -> dict[str, Any]:
    src = dict(page_labels.get((src_rel, src_page)) or {})
    src.update({"file": path, "sha256": sha, "page": page})
    if per_page_file:
        src["doc_page"] = src_page
    else:
        src.pop("doc_page", None)
    return src


def _dump(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", "utf-8")


# ------------------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="simulate every digital PDF of a bundle directory")
    b.add_argument("--bundle", type=Path, required=True, nargs="+", help="bundle dir(s)")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--levels", nargs="+", choices=LEVEL_NAMES, default=list(LEVEL_NAMES))
    b.add_argument("--containers", nargs="+", choices=("pdf", "jpeg"), default=["pdf"])
    b.add_argument("--seed", type=int, default=4002)
    b.add_argument("--max-files", type=int, default=None, help="only the first N digital files")
    pv = sub.add_parser("preview", help="degrade page 1 of one PDF to a JPEG")
    pv.add_argument("--pdf", type=Path, required=True)
    pv.add_argument("--level", choices=LEVEL_NAMES, default="med")
    pv.add_argument("--seed", type=int, default=4002)
    pv.add_argument("--out", type=Path, required=True)
    sub.add_parser("levels", help="print the parameter table")
    a = ap.parse_args(argv)
    if a.cmd == "levels":
        print(describe_levels())
        return 0
    if a.cmd == "preview":
        jp, params = simulate_pdf_pages(a.pdf, a.level, a.seed, a.pdf.name)[0]
        a.out.write_bytes(jp)
        print(json.dumps({k: round(v, 4) for k, v in params.items()}, indent=2))
        return 0
    for bundle in a.bundle:
        out = build_sim_bundle(
            bundle,
            a.out,
            levels=tuple(a.levels),
            containers=tuple(a.containers),
            seed=a.seed,
            max_files=a.max_files,
        )
        print(f"{bundle.name}: wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
