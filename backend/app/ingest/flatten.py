"""Find the page in a phone photo, straighten it and even out the lighting, for OCR only.

A photo of a statement on a desk is skewed, has a dark border and an uneven light gradient. Measured on
our simulated phone photos (docs/anthropic-haiku.md section 5), Tesseract misses 1.8% of words as shot and
0.34% once the page is found, straightened and the lighting is evened out. The straightened picture is
used only to read the text; the stored page image and every image forensics check keep the original, so
:class:`Flattened` carries the transform that puts word boxes back on the original picture.

Conservative by design: when no page outline is found, when it covers too little of the frame, or when
the picture is already a flat, upright page, the picture is returned unchanged and ``changed`` is False.
"""

from __future__ import annotations

import cv2
import numpy as np
from pydantic import ConfigDict

from app.core.models import Strict

MIN_PAGE_AREA = 0.40  # of the frame; a smaller outline is a card, a table or a shadow
ALREADY_FLAT_AREA = 0.97  # outline this close to the whole frame and upright: nothing to straighten
ALREADY_FLAT_TILT = 0.012  # corner offset, as a fraction of the longer side, that counts as upright
MAX_OUTPUT_SIDE = 4200
SMALL_SIDE = 900  # the outline is searched on a copy no larger than this


class Flattened(Strict):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    png: bytes
    changed: bool
    width_px: int
    height_px: int
    inverse: np.ndarray | None = None  # 3x3: straightened pixels -> original pixels
    original_size: tuple[int, int] = (1, 1)

    def to_original(
        self, x: float, y: float, w: float, h: float
    ) -> tuple[float, float, float, float]:
        """A box in straightened-picture fractions -> the same box in original-picture fractions."""
        if self.inverse is None:
            return x, y, w, h
        pts = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], np.float64) * [
            self.width_px,
            self.height_px,
        ]
        mapped = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), self.inverse).reshape(-1, 2)
        ow, oh = self.original_size
        x0, y0 = mapped.min(axis=0)
        x1, y1 = mapped.max(axis=0)
        return float(x0 / ow), float(y0 / oh), float((x1 - x0) / ow), float((y1 - y0) / oh)


def _order(pts: np.ndarray) -> np.ndarray:
    """Corners as top-left, top-right, bottom-right, bottom-left."""
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], np.float32
    )


def find_page_quad(gray: np.ndarray) -> np.ndarray | None:
    """Corners (original pixels, TL TR BR BL) of the largest light region, or None."""
    h, w = gray.shape
    k = max(1, max(h, w) // SMALL_SIDE)
    small = cv2.GaussianBlur(cv2.resize(gray, (max(1, w // k), max(1, h // k))), (9, 9), 0)
    _, th = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    biggest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(biggest) < MIN_PAGE_AREA * small.shape[0] * small.shape[1]:
        return None
    approx = cv2.approxPolyDP(biggest, 0.02 * cv2.arcLength(biggest, True), True)
    pts = (
        approx.reshape(-1, 2).astype(np.float32)
        if len(approx) == 4
        else cv2.boxPoints(cv2.minAreaRect(biggest)).astype(np.float32)
    )
    return _order(pts) * k


MIN_ASPECT, MAX_ASPECT = 1.15, 1.75  # a page is about 1.29 (letter) to 1.41 (A4) either way up


def flatten(png: bytes) -> Flattened:
    """Straighten the page in ``png``; the picture itself, unchanged, whenever there is nothing to
    straighten or anything about the attempt looks wrong. Never raises: it is an optional help for OCR."""
    try:
        return _flatten(png)
    except (cv2.error, ValueError, np.linalg.LinAlgError, ZeroDivisionError, OverflowError):
        return Flattened(png=png, changed=False, width_px=1, height_px=1)


def _flatten(png: bytes) -> Flattened:
    arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return Flattened(png=png, changed=False, width_px=1, height_px=1)
    h, w = arr.shape[:2]
    same = Flattened(png=png, changed=False, width_px=w, height_px=h, original_size=(w, h))
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    quad = find_page_quad(gray)
    if quad is None:
        return same
    if len({tuple(np.round(p)) for p in quad}) < 4 or not cv2.isContourConvex(
        quad.astype(np.int32)
    ):
        return same
    area = cv2.contourArea(quad)
    frame = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    tilt = float(np.abs(quad - frame).max()) / max(w, h)
    if area >= ALREADY_FLAT_AREA * w * h and tilt < ALREADY_FLAT_TILT:
        return same
    top, bottom = np.linalg.norm(quad[0] - quad[1]), np.linalg.norm(quad[3] - quad[2])
    left, right = np.linalg.norm(quad[0] - quad[3]), np.linalg.norm(quad[1] - quad[2])
    out_w, out_h = int(max(top, bottom)), int(max(left, right))
    if (
        min(out_w, out_h) < 50
        or not MIN_ASPECT <= max(out_w, out_h) / min(out_w, out_h) <= MAX_ASPECT
    ):
        return same  # not page-shaped: a bright desk, a card, a crop
    scale = min(1.0, MAX_OUTPUT_SIDE / max(out_w, out_h, 1))
    out_w, out_h = max(8, int(out_w * scale)), max(8, int(out_h * scale))
    matrix = cv2.getPerspectiveTransform(
        quad, np.array([[0, 0], [out_w, 0], [out_w, out_h], [0, out_h]], np.float32)
    )
    page = cv2.warpPerspective(
        gray, matrix, (out_w, out_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    background = cv2.GaussianBlur(page, (0, 0), sigmaX=out_w / 20)
    even = cv2.divide(page, background, scale=255)
    ok, buf = cv2.imencode(".png", even)
    if not ok:
        return same
    return Flattened(
        png=buf.tobytes(),
        changed=True,
        width_px=out_w,
        height_px=out_h,
        inverse=np.linalg.inv(matrix),
        original_size=(w, h),
    )
