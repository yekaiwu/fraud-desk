"""Scan derivation (docs 4.1 scan table): digital PDF page -> JPEG with seeded geometry and
appearance, and the matching homography so ground-truth boxes follow the pixels."""

from __future__ import annotations

import hashlib
import io
import random
from dataclasses import dataclass
from datetime import date
from typing import Any

import cv2
import numpy as np
import pymupdf
from PIL import Image

from .render_common import A4_H, A4_W, finalize_pdf

W, H = 1240, 1754
DESK = (96, 78, 64)
PAPER = (246, 246, 244)

PROFILES: dict[str, dict[str, Any]] = {
    "scan_hq": {"dpi": 150, "rot": 0.3, "blur": 0.0, "contrast": 1.0, "noise": 1.5, "q": 90},
    "scan_lq": {"dpi": 150, "rot": 0.8, "blur": 0.6, "contrast": 0.9, "noise": 4.0, "q": 60},
    "photo": {
        "dpi": 200,
        "rot": 2.0,
        "persp": 0.015,
        "border": 0.06,
        "grad": 0.12,
        "vignette": True,
        "blur": 0.8,
        "noise": 6.0,
        "q": 70,
    },
    "scan_pdf": {"dpi": 150, "rot": 0.3, "blur": 0.0, "contrast": 1.0, "noise": 1.5, "q": 85},
}
JPEG_QUALITY = {k: v["q"] for k, v in PROFILES.items()}


@dataclass
class ScanPage:
    jpeg: bytes
    matrix: np.ndarray  # 3x3: source (pre-resized px) -> destination px
    base_size: tuple[int, int]  # (bw, bh) of the pre-resized source

    def transform_box(self, box: dict[str, Any]) -> dict[str, Any]:
        """Digital box (fractions) -> bounding rectangle of the transformed corners, clipped."""
        bw, bh = self.base_size
        x0, y0 = box["x"] * bw, box["y"] * bh
        x1, y1 = (box["x"] + box["w"]) * bw, (box["y"] + box["h"]) * bh
        pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], dtype=np.float64).T
        out = self.matrix @ pts
        xs, ys = out[0] / out[2], out[1] / out[2]
        fx0, fx1 = float(np.clip(xs.min() / W, 0, 1)), float(np.clip(xs.max() / W, 0, 1))
        fy0, fy1 = float(np.clip(ys.min() / H, 0, 1)), float(np.clip(ys.max() / H, 0, 1))
        res = dict(box)
        res.update(
            {
                "x": round(fx0, 5),
                "y": round(fy0, 5),
                "w": round(max(fx1 - fx0, 1e-5), 5),
                "h": round(max(fy1 - fy0, 1e-5), 5),
            }
        )
        return res


def _seed_rngs(seed_key: str) -> tuple[random.Random, np.random.Generator]:
    h = hashlib.sha256(seed_key.encode()).digest()
    return random.Random(seed_key), np.random.default_rng(int.from_bytes(h[:8], "big"))


def _exif_bytes(doc_date: date) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "Mock Scanner"
    exif[0x0110] = "Mock Scanner"
    ifd = exif.get_ifd(0x8769)
    ifd[0x9003] = doc_date.strftime("%Y:%m:%d 09:00:00")
    return exif.tobytes()


def derive_page(
    pdf_bytes: bytes, page_index: int, render: str, seed_key: str, doc_date: date
) -> ScanPage:
    """Render page ``page_index`` (0-based) of the digital PDF and degrade it."""
    prof = PROFILES[render]
    rng, nrng = _seed_rngs(seed_key)
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        pix = doc[page_index].get_pixmap(dpi=prof["dpi"], alpha=False)
        img = (
            np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()
        )
    is_photo = render == "photo"
    border = prof.get("border", 0.0)
    bw, bh = (
        (int(round(W * (1 - 2 * border))), int(round(H * (1 - 2 * border)))) if is_photo else (W, H)
    )
    src = cv2.resize(img, (bw, bh), interpolation=cv2.INTER_AREA)
    ox, oy = (W - bw) / 2, (H - bh) / 2
    base = np.array([[ox, oy], [ox + bw, oy], [ox + bw, oy + bh], [ox, oy + bh]], dtype=np.float32)
    # translation from pre-resized px to canvas px
    T = np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1]], dtype=np.float64)
    P = np.eye(3)
    if prof.get("persp"):
        j = prof["persp"]
        dst = base.copy()
        for i in range(4):
            dst[i, 0] += rng.uniform(-j, j) * W
            dst[i, 1] += rng.uniform(-j, j) * H
        P = cv2.getPerspectiveTransform(base, dst).astype(np.float64)
    ang = rng.uniform(-prof["rot"], prof["rot"])
    R3 = np.vstack([cv2.getRotationMatrix2D((W / 2, H / 2), ang, 1.0), [0, 0, 1]]).astype(
        np.float64
    )
    M = R3 @ P @ T
    bg = DESK if is_photo else PAPER
    out = cv2.warpPerspective(
        src, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=bg
    )
    f = out.astype(np.float32)
    if prof.get("contrast", 1.0) != 1.0:
        f = (f - 128.0) * prof["contrast"] + 128.0
    if prof.get("grad"):
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        g = ((xx / W + yy / H) - 1.0) * rng.choice([-1, 1])
        f *= (1.0 + prof["grad"] * g)[:, :, None]
    if prof.get("vignette"):
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        f *= (1.0 - 0.18 * np.clip(r - 0.4, 0, 1))[:, :, None]
    if prof["blur"] > 0:
        f = cv2.GaussianBlur(f, (0, 0), prof["blur"])
    f += nrng.normal(0.0, prof["noise"], f.shape).astype(np.float32)
    out = np.clip(f, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(out, "RGB").save(
        buf, format="JPEG", quality=prof["q"], exif=_exif_bytes(doc_date)
    )
    return ScanPage(jpeg=buf.getvalue(), matrix=M, base_size=(bw, bh))


def jpegs_to_pdf(jpegs: list[bytes], doc_date: date, title: str) -> bytes:
    """Image-only PDF (JPEG embedded as is) with Producer Mock Scanner 2.1."""
    doc = pymupdf.open()
    for jp in jpegs:
        page = doc.new_page(width=A4_W, height=A4_H)
        page.insert_image(page.rect, stream=jp)
    raw = doc.tobytes(deflate=True, garbage=3)
    doc.close()
    return finalize_pdf(
        raw, producer="Mock Scanner 2.1", creator="Mock Scanner", title=title, doc_date=doc_date
    )
