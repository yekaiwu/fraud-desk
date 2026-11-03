"""Family 6: cross-document pixel reuse (signatures and stamps)."""

from __future__ import annotations

import io
import random
import zlib

import cv2
import numpy as np
import pikepdf
from pikepdf import Name

from ..core import Bundle, Outcome, attack

F = "F6 cross-document pixel reuse"


def _imgs(pdf: pikepdf.Pdf, data: bytes) -> list:
    """Signature images of page 2 ordered left to right (index 0 = seller, 1 = buyer)."""
    import fitz

    res = pdf.pages[1].Resources.XObject
    by_xref = {v.objgen[0]: v for _k, v in res.items()}
    with fitz.open(stream=data, filetype="pdf") as doc:
        placed = sorted(doc[1].get_image_info(xrefs=True), key=lambda i: i["bbox"][0])
    return [by_xref[i["xref"]] for i in placed]


def get_sig(data: bytes, idx: int) -> tuple[np.ndarray, np.ndarray]:
    pdf = pikepdf.open(io.BytesIO(data))
    im = _imgs(pdf, data)[idx]
    w, h = int(im.Width), int(im.Height)
    rgb = np.frombuffer(im.read_bytes(), np.uint8).reshape(h, w, 3).copy()
    alpha = np.frombuffer(im.SMask.read_bytes(), np.uint8).reshape(h, w).copy()
    return rgb, alpha


def set_sig(data: bytes, idx: int, rgb: np.ndarray, alpha: np.ndarray) -> bytes:
    pdf = pikepdf.open(io.BytesIO(data))
    im = _imgs(pdf, data)[idx]
    im.write(zlib.compress(rgb.tobytes()), filter=Name.FlateDecode)
    im.SMask.write(zlib.compress(alpha.tobytes()), filter=Name.FlateDecode)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def affine(rgb, alpha, angle=0.0, scale=1.0, dx=0, dy=0):
    h, w = alpha.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    M[:, 2] += (dx, dy)
    rgb2 = cv2.warpAffine(rgb, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    a2 = cv2.warpAffine(alpha, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=0)
    return rgb2, a2


@attack(
    "R50",
    F,
    "Buyer signature of contract 1 pasted into contract 2 (different counterparty), rotated 4 deg, scaled 92%, shifted, re-noised",
)
def r50(b: Bundle, rng: random.Random) -> Outcome:
    rgb, a = get_sig(b.files["contract_1.pdf"], 1)
    rgb, a = affine(rgb, a, 4.0, 0.92, 7, -5)
    rgb = np.clip(
        rgb.astype(np.int16)
        + np.random.default_rng(rng.randrange(1 << 30)).normal(0, 2.0, rgb.shape),
        0,
        255,
    ).astype(np.uint8)
    b.replace("contract_2.pdf", set_sig(b.files["contract_2.pdf"], 1, rgb, a))
    return Outcome(
        "One person's signature copied to a second contract with a different counterparty, lightly transformed.",
        ["contract_2.pdf"],
    )


@attack(
    "R51",
    F,
    "Same signature pasted with a one-grey-level random perturbation of every pixel (hash-defeating, visually identical)",
)
def r51(b: Bundle, rng: random.Random) -> Outcome:
    rgb, a = get_sig(b.files["contract_1.pdf"], 1)
    g = np.random.default_rng(rng.randrange(1 << 30))
    rgb = np.clip(rgb.astype(np.int16) + g.integers(-1, 2, rgb.shape), 0, 255).astype(np.uint8)
    b.replace("contract_3.pdf", set_sig(b.files["contract_3.pdf"], 1, rgb, a))
    return Outcome("Exact reuse with +-1 grey level of noise.", ["contract_3.pdf"])


@attack("R52", F, "Same signature pasted with a 6-pixel shift only")
def r52(b: Bundle, rng: random.Random) -> Outcome:
    rgb, a = get_sig(b.files["contract_1.pdf"], 1)
    rgb, a = affine(rgb, a, 0.0, 1.0, 6, 3)
    b.replace("contract_3.pdf", set_sig(b.files["contract_3.pdf"], 1, rgb, a))
    return Outcome("Exact reuse shifted by six pixels.", ["contract_3.pdf"])


@attack(
    "R53",
    F,
    "Both signatures of contract 1 pasted unchanged into contract 3 (calibration)",
    sanity=True,
)
def r53(b: Bundle, rng: random.Random) -> Outcome:
    data = b.files["contract_3.pdf"]
    for i in (0, 1):
        rgb, a = get_sig(b.files["contract_1.pdf"], i)
        data = set_sig(data, i, rgb, a)
    b.replace("contract_3.pdf", data)
    return Outcome("Pixel-identical reuse, known tamper T34.", ["contract_3.pdf"])


@attack("R54", F, "Buyer signature replaced by a freshly drawn scribble (no donor document at all)")
def r54(b: Bundle, rng: random.Random) -> Outcome:
    rgb, a = get_sig(b.files["contract_2.pdf"], 1)
    g = random.Random(rng.randrange(1 << 30))
    canvas = np.full_like(rgb, 255)
    alpha = np.zeros_like(a)
    pts = [(g.randrange(20, 340), g.randrange(30, 90)) for _ in range(9)]
    pts = np.array(pts, np.int32)
    cv2.polylines(alpha, [pts], False, 255, 2, cv2.LINE_AA)
    cv2.polylines(canvas, [pts], False, (20, 20, 90), 2, cv2.LINE_AA)
    b.replace("contract_2.pdf", set_sig(b.files["contract_2.pdf"], 1, canvas, alpha))
    return Outcome(
        "A forger who draws a new signature gives the pixel-reuse check nothing to match.",
        ["contract_2.pdf"],
    )
