"""Image forensics (docs/forensics.md sections 3, 4.2, 4.5 to 4.7; plan 4.5).

Hint generator, not a verdict: a check that finds nothing means "no signal found", never
"authentic". Everything here reads the raw file (``ctx.file_path``), ``FileRef.pdf_meta`` and the
document list; it never reads extracted cell values, never writes, never calls a model and never
uses the wall clock ("today" is ``ctx.today``).

Rules (all ``stage="forensics"``):
``ai_provenance_hint`` ``image_editor_software`` ``image_modified`` (metadata, cheap),
``ela_region`` ``copy_move`` ``noise_mismatch`` (the "raster set": pixel checks that localise a
lazy edit) and ``signature_reuse`` (one signature/stamp image pasted on documents of different
parties; no claim that any signature is genuine).

Cost discipline (plan 6 lever 10): pixel checks run only on *candidate rasters* (uploads, scanned
pages, pages dominated by one embedded image) of documents that have a schema, above a size gate,
and under deterministic caps (``MAX_RASTER_PAGES``, 1600 px analysis side, ORB ``nfeatures``,
no ELA above 12 MP). Hitting a cap emits an info limit notice, never silence.

Severity changes only when two *different* checks of the raster set overlap on the same region
(``OVERLAP_MIN``): one noisy check alone never makes a clean file High.

Everything taken from a file (metadata strings) is untrusted: it passes ``_safe`` before it enters
a flag, and ``explanation`` strings are fixed templates.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import cv2
import imagehash
import numpy as np
import pymupdf
from PIL import Image, ImageOps

from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext, natural_key
from app.core.models import Document, FileRef, Flag, Region, Severity
from app.core.textnorm import normalise_uen

# --------------------------------------------------------------------------------------
# Constants (thresholds are starting guesses, recalibrated from the eval; forensics.md section 8)
# --------------------------------------------------------------------------------------

_EDITOR_PATTERN_STRINGS: tuple[str, ...] = (
    r"photoshop",
    r"\bgimp\b",
    r"pixelmator",
    r"affinity (photo|designer|publisher)",
    r"paint\.net",
    r"\bcanva\b",
    r"ilovepdf",
    r"smallpdf",
    r"sejda",
    r"pdf-?xchange",
    r"pdfescape",
    r"pdfelement|wondershare",
    r"foxit (phantom|pdf editor)",
    r"nitro (pro|pdf)",
    r"pdf24",
    r"soda ?pdf",
    r"pdffiller",
    r"illustrator",
    r"indesign",
    r"adobe acrobat(?! distiller)( pro| dc| standard)?",
)
# Byte-for-byte the same list as forensics_pdf.EDITOR_PATTERNS (duplicated on purpose; a test in
# each module asserts the equality).
EDITOR_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in _EDITOR_PATTERN_STRINGS
)

AI_GENERATOR_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bmidjourney\b",
        r"\bdall-?e\b",
        r"\bstable ?diffusion\b",
        r"\bstability\.ai\b",
        r"\badobe firefly\b|\bfirefly\b",
        r"\bopenai\b",
        r"\bimagen\b",
        r"\bgemini\b",
        r"\bcomfyui\b",
        r"\bautomatic1111\b",
        r"\binvokeai\b",
        r"\bleonardo\.ai\b",
        r"\bideogram\b",
        r"\bsora\b",
        r"\bflux\b",
        r"\btrainedAlgorithmicMedia\b",
        r"\bcompositeWithTrainedAlgorithmicMedia\b",
        r"\balgorithmicMedia\b",
    )
)

MOD_GAP_SECONDS = 300
OVERLAP_MIN = 0.5  # must equal findings.REGION_OVERLAP_MIN

MAX_RASTER_PAGES = 8  # raster sources analysed per file
MAX_SIGNATURE_PAGES = 30  # pages scanned for signature/stamp images per file
MAX_REGIONS_PER_PAGE = 3
MAX_FLAGS_PER_FILE = 10
MAX_EXTRA_REGIONS = 6

ANALYSIS_LONG_SIDE = 1600
MIN_SHORT_SIDE = 256  # raster gate: every raster rule
MIN_AREA_PIXELS = 250_000  # raster gate: copy_move and noise_mismatch (0.25 MP)
ELA_MAX_PIXELS = 12_000_000
MAX_DECODE_PIXELS = 60_000_000  # never decode anything larger (memory guard)
PDF_IMAGE_PAGE_SHARE = 0.60  # one embedded image covering this much makes the page a raster

REGION_PAD = 0.004
REGION_MIN_SIDE = 0.005
REGION_MAX_AREA = 0.5  # a region covering more of the page than this is not localisation

BLOCK = 32
MAD_SCALE = 1.4826
MAD_FLOOR = 0.25  # raw units; keeps a degenerate MAD of 0 from dividing by zero

ELA_QUALITY = 90
ELA_TEXTURE_STD = 6.0
ELA_MIN_TEXTURED_BLOCKS = 20
ELA_MAD_K = 5.0
ELA_MIN_SCORE = 2.0
ELA_MIN_BLOCKS = 2
MAX_COVERED_SHARE = 0.40  # components covering more than this are global, not local

ORB_FEATURES = 1500
CM_RATIO = 0.75
CM_MIN_DISTANCE = 0.08  # of max(w, h)
CM_MIN_INLIERS = 15
CM_SCALE_RANGE = (0.9, 1.1)
CM_MAX_ROTATION_DEG = 5.0
CM_MIN_AREA_SHARE = 0.005
CM_RANSAC_THRESHOLD = 3.0
CM_MAX_CLUSTERS = 4
CM_MIN_NCC = 0.85  # the warped source patch must correlate with the destination patch
CM_PATCH_PAD = 15  # analysis px added around inlier keypoints (ORB patch is 31 px)
CM_SEED = 4002

NOISE_FLAT_PERCENTILE = 60
NOISE_MAD_K = 5.0
NOISE_MIN_DEVIATION = 1.0
NOISE_MIN_BLOCKS = 3
NOISE_MIN_FLAT_BLOCKS = 20
NOISE_SMOOTH_SIGMA = 1.5  # px; used only to judge flatness

SIG_MIN_Y_CENTRE = 0.40
SIG_AREA_RANGE = (0.002, 0.15)
SIG_ASPECT_RANGE = (0.2, 6.0)
SIG_INK_RANGE = (0.01, 0.60)
SIG_INK_GRAY = 200
SIG_MARGIN = 2
SIG_SIZE = 64
SIG_HASH_SIZE = 16
SIG_MAX_HAMMING = 6

RASTER_RULES = ("ela_region", "copy_move", "noise_mismatch")
# rules whose lone, uncorroborated region is only "low" (findings.NOISY_RASTER_RULES groups them)
LONE_LOW_RULES = frozenset({"ela_region", "copy_move"})

_CONTROL_CATEGORIES = {"Cc", "Zl", "Zp"}

_ELA_LABEL = "ELA"
_CLONE_LABEL = "cloned patch"
_SOURCE_LABEL = "copy source"
_NOISE_LABEL = "noise"


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def _safe(s: object, n: int = 80) -> str:
    """Strip control characters and newlines, collapse whitespace, truncate with ``...``."""
    out: list[str] = []
    for ch in str(s):
        cat = unicodedata.category(ch)
        if cat == "Cf":  # zero-width, bidi overrides and other invisible format characters
            continue
        out.append(" " if cat in _CONTROL_CATEGORIES or ch in "\r\n\t" else ch)
    text = " ".join("".join(out).split())
    return text if len(text) <= n else text[: max(0, n - 3)] + "..."


def _round_region(fx0: float, fy0: float, fx1: float, fy1: float) -> tuple[float, ...]:
    def fit(lo: float, hi: float) -> tuple[float, float]:
        lo, hi = max(0.0, lo), min(1.0, hi)
        if hi < lo:  # box entirely off the page: pin to the nearest edge
            lo = hi = 0.0 if hi <= 0.0 else 1.0
        if hi - lo < REGION_MIN_SIDE:
            mid = (lo + hi) / 2
            lo, hi = mid - REGION_MIN_SIDE / 2, mid + REGION_MIN_SIDE / 2
            if lo < 0.0:
                lo, hi = 0.0, REGION_MIN_SIDE
            if hi > 1.0:
                lo, hi = 1.0 - REGION_MIN_SIDE, 1.0
        return lo, hi

    fx0, fx1 = fit(fx0, fx1)
    fy0, fy1 = fit(fy0, fy1)
    x, y = round(fx0, 4), round(fy0, 4)
    w = max(round(fx1 - fx0, 4), REGION_MIN_SIDE)
    h = max(round(fy1 - fy0, 4), REGION_MIN_SIDE)
    if x + w > 1.0:
        w = round(1.0 - x, 4)
    if y + h > 1.0:
        h = round(1.0 - y, 4)
    return x, y, w, h


def _page_region(
    file_id: str,
    page: int,
    box: tuple[float, float, float, float],
    label: str | None = None,
    *,
    pad: float = REGION_PAD,
) -> Region:
    """Page-fraction ``Region`` from a box ``(x0, y0, x1, y1)`` in page fractions: padded on every
    side, clamped to [0, 1], at least 0.005 square, rounded to 4 decimals (forensics.md 3.4)."""
    x, y, w, h = _round_region(box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
    return Region(file_id=file_id, page=page, x=x, y=y, w=w, h=h, label=label)


class _Mapping(NamedTuple):
    """Where the raster sits on the page, in page fractions (identity for uploads)."""

    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0


_IDENTITY = _Mapping()


def _px_region(
    file_id: str,
    page: int,
    box_px: tuple[float, float, float, float],
    size: tuple[int, int],
    mapping: _Mapping,
    label: str | None,
) -> Region:
    """Pixel box ``(x0, y0, x1, y1)`` of an image of ``size = (w, h)`` -> page-fraction Region.

    A raster inside a PDF page maps through the image bbox: ``x_page = img.x + sub.x * img.w``.
    """
    w, h = size
    x0 = mapping.x + (box_px[0] / w) * mapping.w
    x1 = mapping.x + (box_px[2] / w) * mapping.w
    y0 = mapping.y + (box_px[1] / h) * mapping.h
    y1 = mapping.y + (box_px[3] / h) * mapping.h
    return _page_region(file_id, page, (x0, y0, x1, y1), label)


def _area(r: Region) -> float:
    return r.w * r.h


def _overlap(a: Region, b: Region) -> float:
    """intersection area / min(area); 0 on different pages or files."""
    if a.file_id != b.file_id or a.page != b.page:
        return 0.0
    w = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    h = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    if w <= 0 or h <= 0:
        return 0.0
    return (w * h) / min(_area(a), _area(b))


def _reading_key(r: Region) -> tuple[int, float]:
    """Reading order: rows of 2% page height (so a 1 px jitter does not reorder two boxes that
    sit on one line), then x."""
    return (round(r.y / 0.02), r.x)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------------------
# File and document access
# --------------------------------------------------------------------------------------


def _upload_files(ctx: CaseContext) -> list[FileRef]:
    files = [f for f in ctx.case.files if f.mime in ("image/jpeg", "image/png")]
    return sorted(files, key=lambda f: natural_key(f.id))


def _docs_of_file(ctx: CaseContext, file_id: str) -> list[Document]:
    return sorted(
        (d for d in ctx.case.documents if d.file_id == file_id), key=lambda d: natural_key(d.id)
    )


def _docs_for_page(ctx: CaseContext, file_id: str, page: int) -> list[Document]:
    found = [
        d for d in ctx.case.documents if d.file_id == file_id and d.page_start <= page <= d.page_end
    ]
    return sorted(found, key=lambda d: natural_key(d.id)) or _docs_of_file(ctx, file_id)


def _read_bytes(ctx: CaseContext, file_id: str) -> bytes:
    return Path(ctx.file_path(file_id)).read_bytes()


def _notice(
    rule: str, docs: Sequence[Document], evidence: str, *, discriminator: str = "notice"
) -> Flag:
    return make_flag(
        rule,
        docs=docs,
        evidence=evidence,
        explanation=(
            "This check could not cover every page or file, so a clean result here is partial."
        ),
        severity="info",
        discriminator=discriminator,
    )


def _cap_flags(flags: list[Flag], dropped: int = 0) -> list[Flag]:
    """Keep at most MAX_FLAGS_PER_FILE flags; the surplus is summarised on the last kept one."""
    kept = flags[:MAX_FLAGS_PER_FILE]
    surplus = dropped + len(flags) - len(kept)
    if surplus > 0 and kept:
        last = kept[-1]
        kept[-1] = last.model_copy(update={"evidence": f"{last.evidence} (+{surplus} more)"})
    return kept


# --------------------------------------------------------------------------------------
# Metadata extraction (EXIF, XMP, PNG text) - best effort, never raises
# --------------------------------------------------------------------------------------

_XMP_PACKET = re.compile(
    rb"<\?xpacket begin.*?<\?xpacket end[^>]*\?>|<x:xmpmeta.*?</x:xmpmeta>", re.S
)
_EXIF_DATE = re.compile(r"^\s*(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_XMP_ISO = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?")


def _xmp_text(data: bytes) -> str:
    return "\n".join(m.group(0).decode("utf-8", "replace") for m in _XMP_PACKET.finditer(data))


def _xmp_value(xmp: str, name: str) -> str | None:
    """Value of ``name`` (``xmp:CreatorTool``) as an attribute or as an element; regex only (the
    packet is untrusted, so no XML parser)."""
    esc = re.escape(name)
    m = re.search(rf'{esc}\s*=\s*"([^"]*)"', xmp) or re.search(
        rf"<{esc}[^>]*>(.*?)</{esc}>", xmp, re.S
    )
    if not m:
        return None
    value = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
    return value or None


def _as_str(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\x00 ")
    if isinstance(value, str):
        return value.strip("\x00 ")
    return None


def _decode_user_comment(value: object) -> str | None:
    if isinstance(value, bytes):
        head, body = value[:8], value[8:]
        if head.startswith(b"UNICODE"):
            return body.decode("utf-16", "replace").strip("\x00 ")
        return body.decode("utf-8", "replace").strip("\x00 ")
    return _as_str(value)


def _parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    m = _EXIF_DATE.match(text)
    if m:
        try:
            return datetime(*(int(g) for g in m.groups()))
        except ValueError:
            return None
    m = _XMP_ISO.match(text)
    if m:
        y, mo, d, hh, mm, ss = m.groups()
        try:
            return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class _ImageMeta:
    exif_software: str | None = None
    exif_description: str | None = None
    exif_user_comment: str | None = None
    exif_datetime: str | None = None
    exif_original: str | None = None
    exif_digitized: str | None = None
    xmp: str = ""
    xmp_creator_tool: str | None = None
    xmp_create_date: str | None = None
    xmp_modify_date: str | None = None
    png_text: tuple[tuple[str, str], ...] = ()


@functools.lru_cache(maxsize=64)
def _image_meta(sha256: str, path: str) -> _ImageMeta | None:
    """Metadata of one JPEG/PNG; ``None`` when the image cannot be opened."""
    try:
        data = Path(path).read_bytes()
        im = Image.open(io.BytesIO(data))
    except Exception:  # noqa: BLE001 - unreadable image: the caller reports it
        return None
    exif_fields: dict[str, str | None] = {}
    png_text: list[tuple[str, str]] = []
    try:
        with im:
            try:
                exif = im.getexif()
                exif_fields["software"] = _as_str(exif.get(305))
                exif_fields["description"] = _as_str(exif.get(270))
                exif_fields["datetime"] = _as_str(exif.get(306))
                ifd = exif.get_ifd(0x8769)
                exif_fields["original"] = _as_str(ifd.get(36867))
                exif_fields["digitized"] = _as_str(ifd.get(36868))
                exif_fields["user_comment"] = _decode_user_comment(ifd.get(37510))
            except Exception:  # noqa: BLE001 - broken EXIF is the same as no EXIF
                pass
            if im.format == "PNG" and im.width * im.height <= MAX_DECODE_PIXELS:
                with contextlib.suppress(Exception):
                    im.load()  # text chunks after IDAT are only known after a full read
            for key, value in (getattr(im, "text", None) or {}).items():
                if isinstance(value, str):
                    png_text.append((str(key), value))
    except Exception:  # noqa: BLE001
        return None
    xmp = _xmp_text(data)
    return _ImageMeta(
        exif_software=exif_fields.get("software"),
        exif_description=exif_fields.get("description"),
        exif_user_comment=exif_fields.get("user_comment"),
        exif_datetime=exif_fields.get("datetime"),
        exif_original=exif_fields.get("original"),
        exif_digitized=exif_fields.get("digitized"),
        xmp=xmp,
        xmp_creator_tool=_xmp_value(xmp, "xmp:CreatorTool") if xmp else None,
        xmp_create_date=_xmp_value(xmp, "xmp:CreateDate") if xmp else None,
        xmp_modify_date=_xmp_value(xmp, "xmp:ModifyDate") if xmp else None,
        png_text=tuple(png_text),
    )


def _meta_for(ctx: CaseContext, f: FileRef) -> _ImageMeta | None:
    try:
        path = ctx.file_path(f.id)
    except Exception:  # noqa: BLE001
        return None
    return _image_meta(f.sha256, str(path))


def _first_pattern(
    value: str, patterns: Sequence[re.Pattern[str]] = EDITOR_PATTERNS
) -> re.Match[str] | None:
    for pattern in patterns:
        m = pattern.search(value)
        if m:
            return m
    return None


def _all_unreadable(rule: str, failed: list[str], total: int) -> None:
    if total and len(failed) == total:
        raise RuntimeError("all candidate files unreadable")


def _unreadable_notice(ctx: CaseContext, rule: str, failed: list[str]) -> Flag:
    docs = [d for fid in failed for d in _docs_of_file(ctx, fid)]
    return _notice(
        rule,
        docs,
        f"files not analysed (unreadable): {', '.join(_safe(fid, 20) for fid in failed)}",
    )


# --------------------------------------------------------------------------------------
# ai_provenance_hint
# --------------------------------------------------------------------------------------


def _ai_fields(f: FileRef, ctx: CaseContext, data: bytes) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    if f.mime == "application/pdf":
        fields.extend((f"PDF Info {k}", v) for k, v in sorted(f.pdf_meta.items()))
        xmp = _xmp_text(data)
    else:
        meta = _meta_for(ctx, f)
        xmp = meta.xmp if meta else _xmp_text(data)
        if meta:
            for label, value in (
                ("EXIF Software", meta.exif_software),
                ("EXIF ImageDescription", meta.exif_description),
                ("EXIF UserComment", meta.exif_user_comment),
            ):
                if value:
                    fields.append((label, value))
            wanted = {"parameters", "software", "comment", "description"}
            fields.extend(
                (f"PNG text {k}", v) for k, v in meta.png_text if k.strip().lower() in wanted
            )
    if xmp:
        fields.append(("XMP", xmp))
    return fields


def _ai_hits(f: FileRef, ctx: CaseContext, data: bytes) -> list[str]:
    hits: list[str] = []
    for label, value in _ai_fields(f, ctx, data):
        for pattern in AI_GENERATOR_MARKERS:
            m = pattern.search(value)
            if m:
                hits.append(f"marker '{_safe(m.group(0), 40)}' in {label}")
                break
    if b"c2pa" in data and b"jumb" in data:
        hits.append("content credentials (C2PA manifest) present")
    return hits


@check(
    rule="ai_provenance_hint",
    stage="forensics",
    applies_to={"*"},
    severity="info",
    description=(
        "Metadata names an image generator or carries content credentials (a hint only; "
        "finding nothing proves nothing)."
    ),
    tamper="generator named in metadata",
)
def ai_provenance_hint(ctx: CaseContext) -> list[Flag]:
    files = [f for f in ctx.case.files if f.mime in ("application/pdf", "image/jpeg", "image/png")]
    files.sort(key=lambda f: natural_key(f.id))
    if not files:
        raise CheckSkipped("no PDF or image file in the case")
    flags: list[Flag] = []
    failed: list[str] = []
    for f in files:
        try:
            data = _read_bytes(ctx, f.id)
        except OSError:
            failed.append(f.id)
            continue
        hits = _ai_hits(f, ctx, data)
        if not hits:
            continue
        flags.append(
            make_flag(
                "ai_provenance_hint",
                docs=_docs_of_file(ctx, f.id),
                evidence="; ".join(hits[:4]),
                explanation=(
                    "Metadata names an image generator or carries content credentials: a "
                    "provenance hint only, not a finding that the content is AI-generated, and "
                    "finding nothing proves nothing."
                ),
                severity="info",
                discriminator=f.id,
            )
        )
    _all_unreadable("ai_provenance_hint", failed, len(files))
    if failed:
        flags.append(_unreadable_notice(ctx, "ai_provenance_hint", failed))
    return flags


# --------------------------------------------------------------------------------------
# image_editor_software and image_modified
# --------------------------------------------------------------------------------------


@check(
    rule="image_editor_software",
    stage="forensics",
    applies_to={"*"},
    severity="medium",
    description="An image editor is named in the EXIF, XMP or PNG text of an uploaded image.",
    tamper="editor named in metadata",
)
def image_editor_software(ctx: CaseContext) -> list[Flag]:
    files = _upload_files(ctx)
    if not files:
        raise CheckSkipped("no JPEG or PNG upload in the case")
    flags: list[Flag] = []
    failed: list[str] = []
    for f in files:
        meta = _meta_for(ctx, f)
        if meta is None:
            failed.append(f.id)
            continue
        candidates: list[tuple[str, str | None]] = [
            ("EXIF Software", meta.exif_software),
            ("XMP CreatorTool", meta.xmp_creator_tool),
        ]
        candidates.extend(
            ("PNG Software", v) for k, v in meta.png_text if k.strip().lower() == "software"
        )
        found: list[str] = []
        names: list[str] = []
        for label, value in candidates:
            if not value:
                continue
            m = _first_pattern(value)
            if m:
                found.append(f"{label}='{_safe(value)}'")
                names.append(_safe(m.group(0), 40))
        if not found:
            continue
        flags.append(
            make_flag(
                "image_editor_software",
                docs=_docs_of_file(ctx, f.id),
                evidence="; ".join(found),
                explanation=(
                    f"The image metadata names editing software ({names[0]}). Messaging apps "
                    "strip metadata, so the absence of it is not a finding."
                ),
                discriminator=f.id,
            )
        )
    _all_unreadable("image_editor_software", failed, len(files))
    if failed:
        flags.append(_unreadable_notice(ctx, "image_editor_software", failed))
    return flags


def _modified_reasons(ctx: CaseContext, meta: _ImageMeta) -> list[str]:
    reasons: list[str] = []
    dt = _parse_dt(meta.exif_datetime)
    orig = _parse_dt(meta.exif_original)
    dig = _parse_dt(meta.exif_digitized)
    if dt and orig:
        gap = (dt - orig).total_seconds()
        if gap > MOD_GAP_SECONDS:
            reasons.append(
                f"EXIF DateTime {_fmt_dt(dt)} is {int(gap)} s after DateTimeOriginal {_fmt_dt(orig)}"
            )
        elif gap < 0:
            reasons.append(
                f"EXIF DateTime {_fmt_dt(dt)} is before DateTimeOriginal {_fmt_dt(orig)}"
            )
    for label, value in (("DateTime", dt), ("DateTimeOriginal", orig), ("DateTimeDigitized", dig)):
        if value is not None and value.date() > ctx.today:
            reasons.append(f"EXIF {label} {_fmt_dt(value)} is after the case date {ctx.today}")
    created = _parse_dt(meta.xmp_create_date)
    modified = _parse_dt(meta.xmp_modify_date)
    if created and modified:
        gap = (modified - created).total_seconds()
        if gap > MOD_GAP_SECONDS:
            reasons.append(
                f"XMP ModifyDate {_fmt_dt(modified)} is {int(gap)} s after CreateDate "
                f"{_fmt_dt(created)}"
            )
    return reasons


@check(
    rule="image_modified",
    stage="forensics",
    applies_to={"*"},
    severity="low",
    description="EXIF/XMP dates of an uploaded image disagree or lie in the future.",
    tamper="re-saved after capture",
)
def image_modified(ctx: CaseContext) -> list[Flag]:
    files = _upload_files(ctx)
    if not files:
        raise CheckSkipped("no JPEG or PNG upload in the case")
    flags: list[Flag] = []
    failed: list[str] = []
    for f in files:
        meta = _meta_for(ctx, f)
        if meta is None:
            failed.append(f.id)
            continue
        reasons = _modified_reasons(ctx, meta)
        if not reasons:
            continue
        flags.append(
            make_flag(
                "image_modified",
                docs=_docs_of_file(ctx, f.id),
                evidence="; ".join(reasons),
                explanation=(
                    "The image dates disagree or lie after the case date, which can mean the "
                    "file was saved again after it was captured; a re-save by a viewer looks "
                    "the same."
                ),
                discriminator=f.id,
            )
        )
    _all_unreadable("image_modified", failed, len(files))
    if failed:
        flags.append(_unreadable_notice(ctx, "image_modified", failed))
    return flags


# --------------------------------------------------------------------------------------
# Raster analysis: candidates
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Cand:
    """One localised pixel finding of one rule on one page (page-fraction regions)."""

    rule: str
    file_id: str
    page: int
    region: Region
    extra: tuple[Region, ...]
    evidence: str
    explanation: str
    severity: Severity = "medium"
    # copy_move only: both clones (primary chosen once the other rules' candidates are known)
    clones: tuple[Region, Region] | None = None


@dataclass(frozen=True)
class _RasterResult:
    status: str  # "ok" | "failed" | "none"
    eligible: int = 0  # raster sources passing the gate
    analysed: int = 0  # ... of which analysed (cap MAX_RASTER_PAGES)
    jpeg_sources: int = 0  # analysed JPEG sources (ELA candidates)
    big_sources: int = 0  # analysed sources above the 0.25 MP gate (copy_move, noise_mismatch)
    notes: tuple[str, ...] = ()  # limit notices common to every raster rule
    ela_notes: tuple[str, ...] = ()
    cands: tuple[_Cand, ...] = ()


def _to_rgb(im: Image.Image) -> np.ndarray:
    """RGB uint8 array; transparency is composited on white."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        return np.asarray(bg.convert("RGB"))
    return np.asarray(im.convert("RGB"))


def _analysis_gray(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    longest = max(h, w)
    if longest > ANALYSIS_LONG_SIDE:
        k = ANALYSIS_LONG_SIDE / longest
        gray = cv2.resize(
            gray, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA
        )
    return gray


def _mark_surplus(out: list[_Cand], dropped: int) -> list[_Cand]:
    """Candidates beyond the per-page cap are not silent: the last kept one says how many."""
    if dropped > 0 and out:
        out[-1] = replace(out[-1], evidence=f"{out[-1].evidence} (+{dropped} more)")
    return out


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """8-connected components of a block mask -> (x, y, w, h, block count) in block units."""
    n, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    out = []
    for i in range(1, n):
        x, y, w, h, count = (int(v) for v in stats[i])
        out.append((x, y, w, h, count))
    return out


def _block_grid(arr: np.ndarray, block: int) -> np.ndarray:
    """Reshape a 2-D array into (rows, block, cols, block), dropping the ragged border."""
    rows, cols = arr.shape[0] // block, arr.shape[1] // block
    return arr[: rows * block, : cols * block].reshape(rows, block, cols, block)


def _ela_candidates(rgb: np.ndarray, file_id: str, page: int, mapping: _Mapping) -> list[_Cand]:
    h, w = rgb.shape[:2]
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=ELA_QUALITY)
    buf.seek(0)
    resaved = np.asarray(Image.open(buf).convert("RGB"))
    diff = np.abs(rgb.astype(np.int16) - resaved.astype(np.int16)).max(axis=2)
    scores = _block_grid(diff, BLOCK).mean(axis=(1, 3))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    textured = _block_grid(gray, BLOCK).std(axis=(1, 3)) >= ELA_TEXTURE_STD
    if scores.size == 0 or int(textured.sum()) < ELA_MIN_TEXTURED_BLOCKS:
        return []
    values = scores[textured]
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    scale = max(MAD_SCALE * mad, MAD_FLOOR)
    mask = (scores > median + ELA_MAD_K * scale) & (scores >= ELA_MIN_SCORE)
    total = scores.size
    comps = []
    for x, y, bw, bh, count in _components(mask):
        if count < ELA_MIN_BLOCKS or count / total > MAX_COVERED_SHARE:
            continue
        sel = _component_scores(scores, mask, x, y, bw, bh)
        comps.append((float(sel.mean()), x, y, bw, bh, count))
    comps.sort(key=lambda c: (-c[0], c[2], c[1]))
    out: list[_Cand] = []
    for mean_score, x, y, bw, bh, count in comps[:MAX_REGIONS_PER_PAGE]:
        box = (x * BLOCK, y * BLOCK, (x + bw) * BLOCK, (y + bh) * BLOCK)
        region = _px_region(file_id, page, box, (w, h), mapping, _ELA_LABEL)
        if _area(region) > REGION_MAX_AREA:
            continue
        z = (mean_score - median) / scale
        out.append(
            _Cand(
                rule="ela_region",
                file_id=file_id,
                page=page,
                region=region,
                extra=(),
                evidence=(
                    f"{count} blocks, mean ELA {mean_score:.1f} vs page median {median:.1f} "
                    f"(+{z:.1f} MAD)"
                ),
                explanation=(
                    f"This area recompresses with a clearly higher error level ({mean_score:.1f}"
                    f" vs a page median of {median:.1f}), which can mean it was edited after "
                    "the image was saved. Honest scans can also show this."
                ),
            )
        )
    return _mark_surplus(out, len(comps) - MAX_REGIONS_PER_PAGE)


def _component_scores(
    scores: np.ndarray, mask: np.ndarray, x: int, y: int, bw: int, bh: int
) -> np.ndarray:
    sub = mask[y : y + bh, x : x + bw]
    vals = scores[y : y + bh, x : x + bw][sub]
    return vals if vals.size else np.zeros(1)


def _orb_pairs(
    kps: Sequence[cv2.KeyPoint], des: np.ndarray, min_distance: float
) -> list[tuple[int, int]]:
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    knn = matcher.knnMatch(des, des, k=3)
    pairs: list[tuple[int, int]] = []
    for matches in knn:
        rest = [m for m in matches if m.trainIdx != m.queryIdx]
        if len(rest) < 2:
            continue
        m1, m2 = rest[0], rest[1]
        if not m1.distance < CM_RATIO * m2.distance:
            continue
        a, b = kps[m1.queryIdx].pt, kps[m1.trainIdx].pt
        if math.hypot(a[0] - b[0], a[1] - b[1]) < min_distance:
            continue
        pairs.append((m1.queryIdx, m1.trainIdx))
    return _canonical_pairs(kps, pairs)


def _canonical_pairs(
    kps: Sequence[cv2.KeyPoint], pairs: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """A match is found from both ends (a to b and b to a). Keep one orientation per pair, chosen
    by the quantised sign of the displacement (down, else right), so every pair of one clone
    shares the same shift and RANSAC sees one consistent transform instead of two opposite ones."""
    out: set[tuple[int, int]] = set()
    for i, j in pairs:
        dx = kps[j].pt[0] - kps[i].pt[0]
        dy = kps[j].pt[1] - kps[i].pt[1]
        qx, qy = round(dx / 8), round(dy / 8)
        if qy < 0 or (qy == 0 and qx < 0):
            i, j = j, i
        out.add((i, j))
    return sorted(out)


def _bbox(
    points: np.ndarray, pad: float, size: tuple[int, int]
) -> tuple[float, float, float, float]:
    w, h = size
    return (
        float(max(0.0, points[:, 0].min() - pad)),
        float(max(0.0, points[:, 1].min() - pad)),
        float(min(w, points[:, 0].max() + pad)),
        float(min(h, points[:, 1].max() + pad)),
    )


def _patch_ncc(
    gray: np.ndarray, matrix: np.ndarray, box: tuple[float, float, float, float]
) -> float:
    """Normalised cross-correlation between the destination patch ``box`` and the source patch
    warped onto it by ``matrix``. Repeated table rows pass the keypoint test but not this one
    (their digits differ); a real clone correlates almost perfectly."""
    h, w = gray.shape
    x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
    x1, y1 = min(w, int(math.ceil(box[2]))), min(h, int(math.ceil(box[3])))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return 0.0
    warped = cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
    valid = cv2.warpAffine(np.ones_like(gray), matrix, (w, h), flags=cv2.INTER_NEAREST) > 0
    a = gray[y0:y1, x0:x1].astype(np.float32)
    b = warped[y0:y1, x0:x1].astype(np.float32)
    m = valid[y0:y1, x0:x1]
    if int(m.sum()) < 64:
        return 0.0
    a, b = a[m] - a[m].mean(), b[m] - b[m].mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denom) if denom > 1e-6 else 0.0


def _copy_move_candidates(
    gray: np.ndarray, file_id: str, page: int, mapping: _Mapping
) -> list[_Cand]:
    h, w = gray.shape
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    kps, des = orb.detectAndCompute(gray, None)
    if des is None or len(kps) < 2 * CM_MIN_INLIERS:
        return []
    pts = np.float32([k.pt for k in kps])
    pairs = _orb_pairs(kps, des, CM_MIN_DISTANCE * max(w, h))
    found: list[tuple[int, tuple[float, ...], tuple[float, ...], float, float]] = []
    for _ in range(CM_MAX_CLUSTERS):
        if len(pairs) < CM_MIN_INLIERS:
            break
        src = pts[[i for i, _j in pairs]]
        dst = pts[[j for _i, j in pairs]]
        cv2.setRNGSeed(CM_SEED)
        matrix, inliers = cv2.estimateAffinePartial2D(
            src, dst, method=cv2.RANSAC, ransacReprojThreshold=CM_RANSAC_THRESHOLD, maxIters=2000
        )
        if matrix is None or inliers is None:
            break
        sel = inliers.ravel().astype(bool)
        count = int(sel.sum())
        if count < CM_MIN_INLIERS:
            break
        scale = math.hypot(float(matrix[0, 0]), float(matrix[1, 0]))
        rotation = abs(math.degrees(math.atan2(float(matrix[1, 0]), float(matrix[0, 0]))))
        box_a = _bbox(src[sel], CM_PATCH_PAD, (w, h))
        box_b = _bbox(dst[sel], CM_PATCH_PAD, (w, h))
        area = min(
            (box_a[2] - box_a[0]) * (box_a[3] - box_a[1]),
            (box_b[2] - box_b[0]) * (box_b[3] - box_b[1]),
        )
        if (
            CM_SCALE_RANGE[0] <= scale <= CM_SCALE_RANGE[1]
            and rotation < CM_MAX_ROTATION_DEG
            and area / (w * h) >= CM_MIN_AREA_SHARE
            and _patch_ncc(gray, matrix, _bbox(dst[sel], 0.0, (w, h))) >= CM_MIN_NCC
        ):
            found.append((count, box_a, box_b, float(matrix[0, 2]), float(matrix[1, 2])))
        used = {i for (i, _j), keep in zip(pairs, sel, strict=True) if keep} | {
            j for (_i, j), keep in zip(pairs, sel, strict=True) if keep
        }
        pairs = [(i, j) for i, j in pairs if i not in used and j not in used]
    found.sort(key=lambda c: (-c[0], c[1][1], c[1][0]))
    out: list[_Cand] = []
    for count, box_a, box_b, _tx, _ty in found[:MAX_REGIONS_PER_PAGE]:
        reg_a = _px_region(file_id, page, box_a, (w, h), mapping, _CLONE_LABEL)
        reg_b = _px_region(file_id, page, box_b, (w, h), mapping, _CLONE_LABEL)
        if max(_area(reg_a), _area(reg_b)) > REGION_MAX_AREA:
            continue
        first, second = sorted((reg_a, reg_b), key=_reading_key)
        dx = (second.x + second.w / 2) - (first.x + first.w / 2)
        dy = (second.y + second.h / 2) - (first.y + first.h / 2)
        out.append(
            _Cand(
                rule="copy_move",
                file_id=file_id,
                page=page,
                region=second,
                extra=(first.model_copy(update={"label": _SOURCE_LABEL}),),
                evidence=(
                    f"{count} inlier matches between two patches, shift "
                    f"({dx:.2f}, {dy:.2f}) of page"
                ),
                explanation=(
                    f"Two areas of the page match almost exactly ({count} matching points), "
                    "which can mean a patch was copied over another part of the image. Repeated "
                    "stamps and logos can look the same."
                ),
                clones=(first, second),
            )
        )
    return _mark_surplus(out, len(found) - MAX_REGIONS_PER_PAGE)


def _noise_candidates(gray: np.ndarray, file_id: str, page: int, mapping: _Mapping) -> list[_Cand]:
    g = gray.astype(np.float32)
    h2, w2 = (g.shape[0] // 2) * 2, (g.shape[1] // 2) * 2
    g = g[:h2, :w2]
    a, b = g[0::2, 0::2], g[0::2, 1::2]
    c, d = g[1::2, 0::2], g[1::2, 1::2]
    hh = (a - b - c + d) / 2.0
    half = BLOCK // 2
    hh_blocks = _block_grid(np.abs(hh), half)  # (rows, half, cols, half)
    rows, cols = hh_blocks.shape[0], hh_blocks.shape[2]
    if rows < 2 or cols < 2:
        return []
    flat_hh = hh_blocks.transpose(0, 2, 1, 3).reshape(rows, cols, -1)
    sigma = np.median(flat_hh, axis=2) / 0.6745
    # structure, not noise: flatness is judged on a smoothed copy, otherwise the very blocks that
    # carry extra noise would drop out of the "flat" set they are meant to be compared with
    smooth = cv2.GaussianBlur(g, (0, 0), NOISE_SMOOTH_SIGMA)
    energy_map = np.zeros_like(smooth)
    energy_map[:, :-1] += np.diff(smooth, axis=1) ** 2
    energy_map[:-1, :] += np.diff(smooth, axis=0) ** 2
    energy = _block_grid(energy_map, BLOCK).mean(axis=(1, 3))
    energy = energy[:rows, :cols]
    flat = energy < np.percentile(energy, NOISE_FLAT_PERCENTILE)
    if int(flat.sum()) < NOISE_MIN_FLAT_BLOCKS:
        return []
    values = sigma[flat]
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    scale = max(MAD_SCALE * mad, MAD_FLOOR)
    deviation = np.abs(sigma - median)
    mask = flat & (deviation > NOISE_MAD_K * scale) & (deviation >= NOISE_MIN_DEVIATION)
    total = rows * cols
    comps = []
    for x, y, bw, bh, count in _components(mask):
        if count < NOISE_MIN_BLOCKS or count / total > MAX_COVERED_SHARE:
            continue
        sub = mask[y : y + bh, x : x + bw]
        mean_sigma = float(sigma[y : y + bh, x : x + bw][sub].mean())
        comps.append(
            (float(deviation[y : y + bh, x : x + bw][sub].mean()), mean_sigma, x, y, bw, bh, count)
        )
    comps.sort(key=lambda c: (-c[0], c[3], c[2]))
    out: list[_Cand] = []
    size = (g.shape[1], g.shape[0])
    for dev, mean_sigma, x, y, bw, bh, count in comps[:MAX_REGIONS_PER_PAGE]:
        box = (x * BLOCK, y * BLOCK, (x + bw) * BLOCK, (y + bh) * BLOCK)
        region = _px_region(file_id, page, box, size, mapping, _NOISE_LABEL)
        if _area(region) > REGION_MAX_AREA:
            continue
        out.append(
            _Cand(
                rule="noise_mismatch",
                file_id=file_id,
                page=page,
                region=region,
                extra=(),
                evidence=(
                    f"{count} blocks, residual noise sigma {mean_sigma:.1f} vs page median "
                    f"{median:.1f} (deviation {dev:.1f})"
                ),
                explanation=(
                    f"The image noise in this area (sigma {mean_sigma:.1f}) differs from the "
                    f"rest of the page (median {median:.1f}), which can mean a patch from "
                    "another source was pasted in. Smooth or rescaled regions can look the same."
                ),
            )
        )
    return _mark_surplus(out, len(comps) - MAX_REGIONS_PER_PAGE)


# --------------------------------------------------------------------------------------
# Corroboration (the only severity change, forensics.md 4.1 (7))
# --------------------------------------------------------------------------------------


def _best_overlap(region: Region, others: Sequence[_Cand]) -> float:
    return max((_overlap(region, o.region) for o in others), default=0.0)


def _choose_primary(cands: list[_Cand]) -> list[_Cand]:
    """For copy_move, the primary region is the clone overlapping another raster-set candidate
    most (tie: the later clone in reading order); the other clone goes to ``extra_regions``."""
    out: list[_Cand] = []
    for c in cands:
        if c.rule != "copy_move" or c.clones is None:
            out.append(c)
            continue
        others = [o for o in cands if o.rule != "copy_move" and o.page == c.page]
        first, second = c.clones
        ov_first, ov_second = _best_overlap(first, others), _best_overlap(second, others)
        if ov_first > ov_second:
            primary, source = first, second
        else:
            primary, source = second, first
        out.append(
            replace(
                c,
                region=primary,
                extra=(source.model_copy(update={"label": _SOURCE_LABEL}),),
            )
        )
    return out


def _corroborate(cands: list[_Cand]) -> list[_Cand]:
    """Pairs of candidates from different rules on one page with overlap >= OVERLAP_MIN become
    high; nothing else raises severity. A lone ELA or copy-move region is demoted to low."""
    partners: dict[int, list[str]] = {}
    for i, a in enumerate(cands):
        for j in range(i + 1, len(cands)):
            b = cands[j]
            if a.rule == b.rule or a.file_id != b.file_id or a.page != b.page:
                continue
            if _overlap(a.region, b.region) >= OVERLAP_MIN - 1e-9:
                partners.setdefault(i, []).append(b.rule)
                partners.setdefault(j, []).append(a.rule)
    out: list[_Cand] = []
    for i, c in enumerate(cands):
        rules = sorted(set(partners.get(i, [])))
        if rules:
            c = replace(
                c,
                severity="high",
                evidence=c.evidence + "; corroborated by " + ", ".join(rules),
            )
        elif c.rule in LONE_LOW_RULES:
            # a clean scan or photo shows ELA and cloned-looking patches on its own (text edges,
            # repeated table rows), so one such region alone is a hint, not a finding (plan 4.5)
            c = replace(c, severity="low")
        out.append(c)
    return out


# --------------------------------------------------------------------------------------
# Raster sources and the per-file analysis (memoised)
# --------------------------------------------------------------------------------------


class _Source(NamedTuple):
    page: int
    xref: int  # 0 for an upload
    width: int
    height: int
    mapping: _Mapping


def _open_upload(path: str) -> tuple[np.ndarray, bool] | None | str:
    """(rgb, is_jpeg) of an upload, EXIF-oriented; ``"large"`` when above MAX_DECODE_PIXELS;
    ``None`` when unreadable."""
    try:
        with Image.open(path) as im:
            if im.width * im.height > MAX_DECODE_PIXELS:
                return "large"
            is_jpeg = im.format == "JPEG"
            oriented = ImageOps.exif_transpose(im)
            return _to_rgb(oriented), is_jpeg
    except Exception:  # noqa: BLE001 - corrupt or truncated image
        return None


def _decode_pdf_image(doc: pymupdf.Document, xref: int) -> tuple[np.ndarray, bool] | None:
    try:
        info = doc.extract_image(xref)
        if not info or not info.get("image"):
            return None
        is_jpeg = str(info.get("ext", "")).lower() in ("jpeg", "jpg")
        with Image.open(io.BytesIO(info["image"])) as im:
            if im.width * im.height > MAX_DECODE_PIXELS:
                return None
            im.load()
            return _to_rgb(im), is_jpeg
    except Exception:  # noqa: BLE001
        return None


def _pdf_raster_sources(
    doc: pymupdf.Document, pages: tuple[int, ...], scan_pages: frozenset[int]
) -> tuple[list[_Source], int]:
    """Raster sources among ``pages`` (1-based): a scanned page or a page where one embedded image
    covers >= 60% of it. Returns (sources passing the size gate, rotated pages skipped)."""
    sources: list[_Source] = []
    rotated = 0
    for pno in pages:
        if pno < 1 or pno > doc.page_count:
            continue
        try:
            page = doc[pno - 1]
            rect = page.rect
            infos = page.get_image_info(xrefs=True)
        except Exception:  # noqa: BLE001
            continue
        best = None
        best_area = 0.0
        for info in infos:
            xref = int(info.get("xref") or 0)
            if xref <= 0:
                continue
            bbox = pymupdf.Rect(info["bbox"]) & rect
            area = max(0.0, bbox.width) * max(0.0, bbox.height)
            if area > best_area:
                best, best_area = (info, bbox), area
        if best is None:
            continue
        share = best_area / (rect.width * rect.height) if rect.width * rect.height else 0.0
        if pno not in scan_pages and share < PDF_IMAGE_PAGE_SHARE:
            continue
        if page.rotation != 0:
            rotated += 1
            continue
        info, bbox = best
        width, height = int(info.get("width") or 0), int(info.get("height") or 0)
        if min(width, height) < MIN_SHORT_SIDE:
            continue
        mapping = _Mapping(
            (bbox.x0 - rect.x0) / rect.width,
            (bbox.y0 - rect.y0) / rect.height,
            bbox.width / rect.width,
            bbox.height / rect.height,
        )
        sources.append(_Source(pno, int(info["xref"]), width, height, mapping))
    return sources, rotated


def _analyse_raster(
    rgb: np.ndarray, is_jpeg: bool, file_id: str, page: int, mapping: _Mapping
) -> tuple[list[_Cand], str | None]:
    """All raster-set candidates of one source and an ELA limit note (or None)."""
    h, w = rgb.shape[:2]
    cands: list[_Cand] = []
    ela_note = None
    if is_jpeg:
        if h * w > ELA_MAX_PIXELS:
            ela_note = f"page {page}: ELA skipped, image above {ELA_MAX_PIXELS // 1_000_000} MP"
        else:
            cands += _ela_candidates(rgb, file_id, page, mapping)
    if h * w >= MIN_AREA_PIXELS:
        gray = _analysis_gray(rgb)
        cands += _copy_move_candidates(gray, file_id, page, mapping)
        cands += _noise_candidates(gray, file_id, page, mapping)
    return cands, ela_note


@functools.lru_cache(maxsize=32)
def _raster_candidates(
    sha256: str,
    path: str,
    file_id: str,
    mime: str,
    pages: tuple[int, ...],
    scan_pages: frozenset[int],
    cap: int,
) -> _RasterResult:
    """One analysis per file, shared by ``ela_region``, ``copy_move`` and ``noise_mismatch`` so
    the order in which the registry runs them cannot change a result (forensics.md 4.1 (6))."""
    notes: list[str] = []
    ela_notes: list[str] = []
    cands: list[_Cand] = []
    jpeg_sources = 0
    big_sources = 0
    if mime in ("image/jpeg", "image/png"):
        try:
            with Image.open(path) as im:
                short = min(im.size)
        except Exception:  # noqa: BLE001
            return _RasterResult(status="failed")
        if short < MIN_SHORT_SIDE:
            return _RasterResult(status="none")
        opened = _open_upload(path)
        if opened is None:
            return _RasterResult(status="failed")
        if opened == "large":
            return _RasterResult(
                status="ok",
                eligible=1,
                analysed=0,
                notes=(f"image above {MAX_DECODE_PIXELS // 1_000_000} MP not analysed",),
            )
        rgb, is_jpeg = opened  # type: ignore[misc]
        found, ela_note = _analyse_raster(rgb, is_jpeg, file_id, 1, _IDENTITY)
        cands += found
        if ela_note:
            ela_notes.append(ela_note)
        return _RasterResult(
            status="ok",
            eligible=1,
            analysed=1,
            jpeg_sources=1 if is_jpeg else 0,
            big_sources=1 if rgb.shape[0] * rgb.shape[1] >= MIN_AREA_PIXELS else 0,
            ela_notes=tuple(ela_notes),
            cands=tuple(_finish(cands)),
        )
    try:
        doc = pymupdf.open(path)
    except Exception:  # noqa: BLE001
        return _RasterResult(status="failed")
    with doc:
        sources, rotated = _pdf_raster_sources(doc, pages, scan_pages)
        if rotated:
            notes.append(f"{rotated} rotated page(s) skipped")
        eligible = len(sources)
        if eligible == 0:
            return _RasterResult(status="none" if not notes else "ok", notes=tuple(notes))
        analysed = 0
        for src in sources[:cap]:
            decoded = _decode_pdf_image(doc, src.xref)
            if decoded is None:
                notes.append(f"page {src.page}: embedded image could not be decoded")
                continue
            rgb, is_jpeg = decoded
            if min(rgb.shape[:2]) < MIN_SHORT_SIDE:
                continue
            analysed += 1
            found, ela_note = _analyse_raster(rgb, is_jpeg, file_id, src.page, src.mapping)
            cands += found
            jpeg_sources += 1 if is_jpeg else 0
            big_sources += 1 if rgb.shape[0] * rgb.shape[1] >= MIN_AREA_PIXELS else 0
            if ela_note:
                ela_notes.append(ela_note)
        if eligible > cap:
            notes.insert(
                0, f"pages analysed {min(cap, eligible)} of {eligible} (cap MAX_RASTER_PAGES)"
            )
    return _RasterResult(
        status="ok",
        eligible=eligible,
        analysed=analysed,
        jpeg_sources=jpeg_sources,
        big_sources=big_sources,
        notes=tuple(notes),
        ela_notes=tuple(ela_notes),
        cands=tuple(_finish(cands)),
    )


def _finish(cands: list[_Cand]) -> list[_Cand]:
    """Choose primary clones, then corroborate; deterministic order (page, y, x, rule)."""
    ordered = sorted(cands, key=lambda c: (c.page, c.rule, c.region.y, c.region.x))
    primary = _choose_primary(ordered)
    return _corroborate(primary)


@dataclass(frozen=True)
class _FileOutcome:
    file: FileRef
    result: _RasterResult


def _raster_outcomes(ctx: CaseContext) -> list[_FileOutcome]:
    outcomes: list[_FileOutcome] = []
    for f in sorted(ctx.case.files, key=lambda x: natural_key(x.id)):
        if f.mime == "application/pdf":
            # every page is a candidate whatever its label or extraction state: a classifier label
            # must never reduce checking (plan 5.3), and an "other" page is where a forger hides
            pages = tuple(p.page for p in f.pages)
            scan_pages = frozenset(p for p in pages if _is_scan(f, p))
            if not pages:
                continue
        else:
            pages, scan_pages = (1,), frozenset()
        try:
            path = str(ctx.file_path(f.id))
        except Exception:  # noqa: BLE001
            outcomes.append(_FileOutcome(f, _RasterResult(status="failed")))
            continue
        result = _raster_candidates(
            f.sha256, path, f.id, f.mime, pages, scan_pages, MAX_RASTER_PAGES
        )
        if result.status != "none":
            outcomes.append(_FileOutcome(f, result))
    return outcomes


def _is_scan(f: FileRef, page: int) -> bool:
    return any(p.page == page and p.is_scan for p in f.pages)


def _cand_flag(ctx: CaseContext, c: _Cand, n: int) -> Flag:
    return make_flag(
        c.rule,
        docs=_docs_for_page(ctx, c.file_id, c.page),
        region=c.region.model_copy(),  # the cached candidate must stay immutable
        extra_regions=[r.model_copy() for r in c.extra][:MAX_EXTRA_REGIONS],
        evidence=c.evidence,
        explanation=c.explanation,
        severity=c.severity,
        discriminator=f"{c.file_id}p{c.page}r{n}",
    )


def _raster_rule(ctx: CaseContext, rule: str) -> list[Flag]:
    outcomes = _raster_outcomes(ctx)
    ok = [o for o in outcomes if o.result.status == "ok"]
    failed = [o.file.id for o in outcomes if o.result.status == "failed"]
    if not outcomes:
        raise CheckSkipped("no jpeg raster" if rule == "ela_region" else "no raster source")
    if not ok:
        raise RuntimeError("all candidate files unreadable")
    # a file whose raster pages were all set aside (rotated, undecodable, too large) leaves a
    # notice instead of silence, so the check "ran" even though no source was left to judge
    noted = any(o.result.notes and not o.result.analysed for o in ok)
    if rule == "ela_region" and not any(o.result.jpeg_sources for o in ok) and not noted:
        raise CheckSkipped("no jpeg raster")
    if rule != "ela_region" and not any(o.result.big_sources for o in ok) and not noted:
        raise CheckSkipped("no raster source")
    flags: list[Flag] = []
    for o in ok:
        mine = [c for c in o.result.cands if c.rule == rule]
        per_page: dict[int, int] = {}
        file_flags: list[Flag] = []
        for c in sorted(mine, key=lambda x: (x.page, *_reading_key(x.region))):
            per_page[c.page] = per_page.get(c.page, 0) + 1
            file_flags.append(_cand_flag(ctx, c, per_page[c.page]))
        flags.extend(_cap_flags(file_flags))
        notes = list(o.result.notes)
        if rule == "ela_region":
            notes += list(o.result.ela_notes)
        if notes:
            flags.append(_notice(rule, _docs_of_file(ctx, o.file.id), "; ".join(notes)))
    if failed:
        flags.append(_unreadable_notice(ctx, rule, failed))
    return flags


@check(
    rule="ela_region",
    stage="forensics",
    applies_to={"*"},
    severity="medium",
    description="Error-level analysis: a JPEG area recompresses differently from the rest.",
    tamper="local edit on a JPEG",
)
def ela_region(ctx: CaseContext) -> list[Flag]:
    return _raster_rule(ctx, "ela_region")


@check(
    rule="copy_move",
    stage="forensics",
    applies_to={"*"},
    severity="medium",
    description="Copy-move: two areas of a raster match closely (a cloned patch).",
    tamper="cloned patch",
)
def copy_move(ctx: CaseContext) -> list[Flag]:
    return _raster_rule(ctx, "copy_move")


@check(
    rule="noise_mismatch",
    stage="forensics",
    applies_to={"*"},
    severity="medium",
    description="Residual-noise mismatch: an area of a raster has other noise than the rest.",
    tamper="pasted patch with other noise",
)
def noise_mismatch(ctx: CaseContext) -> list[Flag]:
    return _raster_rule(ctx, "noise_mismatch")


# --------------------------------------------------------------------------------------
# signature_reuse
# --------------------------------------------------------------------------------------


class CropFingerprint(NamedTuple):
    phash: str
    exact: str
    document_id: str
    file_id: str
    page: int
    region: Region


class _PageCrop(NamedTuple):
    """A candidate signature/stamp crop on one PDF page (page-fraction box)."""

    page: int
    xref: int
    phash: str
    exact: str
    x: float
    y: float
    w: float
    h: float


def _normalise_crop(im: Image.Image) -> Image.Image | None:
    """Alpha on white, gray, autocrop to ink with a 2 px margin, ink fraction gate, 64 x 64."""
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        gray = bg.convert("L")
    else:
        gray = im.convert("L")
    arr = np.asarray(gray)
    ink = arr < SIG_INK_GRAY
    if not ink.any():
        return None
    ys, xs = np.where(ink)
    x0, x1 = max(0, int(xs.min()) - SIG_MARGIN), min(arr.shape[1], int(xs.max()) + 1 + SIG_MARGIN)
    y0, y1 = max(0, int(ys.min()) - SIG_MARGIN), min(arr.shape[0], int(ys.max()) + 1 + SIG_MARGIN)
    fraction = float(ink[y0:y1, x0:x1].mean())
    if not SIG_INK_RANGE[0] <= fraction <= SIG_INK_RANGE[1]:
        return None
    return gray.crop((x0, y0, x1, y1)).resize((SIG_SIZE, SIG_SIZE), Image.Resampling.LANCZOS)


def _load_embedded(doc: pymupdf.Document, xref: int) -> Image.Image | None:
    try:
        info = doc.extract_image(xref)
        if not info or not info.get("image"):
            return None
        with Image.open(io.BytesIO(info["image"])) as raw:
            if raw.width * raw.height > MAX_DECODE_PIXELS:
                return None
            raw.load()
            im = raw.convert("RGB")
        smask = int(info.get("smask") or 0)
        if smask:
            mask_info = doc.extract_image(smask)
            if mask_info and mask_info.get("image"):
                with Image.open(io.BytesIO(mask_info["image"])) as m:
                    mask = m.convert("L").resize(im.size)
                im.putalpha(mask)
        return im
    except Exception:  # noqa: BLE001
        return None


@functools.lru_cache(maxsize=32)
def _pdf_page_crops(path: str, sha256: str, pages: tuple[int, ...]) -> tuple[_PageCrop, ...] | None:
    """Candidate crops on ``pages`` (1-based) of one PDF; ``None`` when it cannot be opened."""
    try:
        doc = pymupdf.open(path)
    except Exception:  # noqa: BLE001
        return None
    out: list[_PageCrop] = []
    hashed: dict[int, tuple[str, str] | None] = {}
    with doc:
        for pno in pages:
            if pno < 1 or pno > doc.page_count:
                continue
            try:
                page = doc[pno - 1]
                if page.rotation != 0:
                    continue
                rect = page.rect
                infos = page.get_image_info(xrefs=True)
            except Exception:  # noqa: BLE001
                continue
            for info in infos:
                xref = int(info.get("xref") or 0)
                bbox = pymupdf.Rect(info["bbox"]) & rect
                if xref <= 0 or bbox.is_empty or rect.width <= 0 or rect.height <= 0:
                    continue
                fx, fy = (bbox.x0 - rect.x0) / rect.width, (bbox.y0 - rect.y0) / rect.height
                fw, fh = bbox.width / rect.width, bbox.height / rect.height
                if fy + fh / 2 < SIG_MIN_Y_CENTRE:
                    continue
                if not SIG_AREA_RANGE[0] <= fw * fh <= SIG_AREA_RANGE[1]:
                    continue
                aspect = (bbox.width / bbox.height) if bbox.height else 0.0
                if not SIG_ASPECT_RANGE[0] <= aspect <= SIG_ASPECT_RANGE[1]:
                    continue
                if xref not in hashed:
                    hashed[xref] = None
                    im = _load_embedded(doc, xref)
                    norm = _normalise_crop(im) if im is not None else None
                    if norm is not None:
                        hashed[xref] = (
                            str(imagehash.phash(norm, hash_size=SIG_HASH_SIZE)),
                            hashlib.sha256(norm.tobytes()).hexdigest(),
                        )
                digest = hashed[xref]
                if digest is None:
                    continue
                out.append(_PageCrop(pno, xref, digest[0], digest[1], fx, fy, fw, fh))
    return tuple(out)


@dataclass(frozen=True)
class _Crop:
    fp: CropFingerprint
    xref: int
    # the party (UEN key) whose signature this crop is, when the page layout says so (a contract
    # has a seller and a buyer signature); "" = not attributed, the document's parties apply
    party: str = ""


def _signature_pages(ctx: CaseContext, f: FileRef) -> tuple[tuple[int, ...], int]:
    """Pages of ``f`` covered by a document (capped at MAX_SIGNATURE_PAGES) and how many were cut."""
    wanted: set[int] = set()
    for d in _docs_of_file(ctx, f.id):
        if d.status in ("ok", "partial"):
            wanted.update(range(d.page_start, d.page_end + 1))
    valid = {p.page for p in f.pages if p.error is None} or wanted
    pages = sorted(wanted & valid)
    return tuple(pages[:MAX_SIGNATURE_PAGES]), max(0, len(pages) - MAX_SIGNATURE_PAGES)


def _collect_crops(ctx: CaseContext) -> tuple[list[_Crop], list[str], list[str]]:
    """Candidate crops of the case (documents in id order, then reading order), the ids of files
    that could not be read and limit notes."""
    crops: list[_Crop] = []
    failed: list[str] = []
    notes: list[str] = []
    first_page: dict[tuple[str, int], int] = {}
    for f in sorted(ctx.case.files, key=lambda x: natural_key(x.id)):
        if f.mime != "application/pdf":
            continue
        pages, cut = _signature_pages(ctx, f)
        if not pages:
            continue
        try:
            path = str(ctx.file_path(f.id))
        except Exception:  # noqa: BLE001
            failed.append(f.id)
            continue
        found = _pdf_page_crops(path, f.sha256, pages)
        if found is None:
            failed.append(f.id)
            continue
        if cut:
            notes.append(
                f"{f.id}: signature images read on {len(pages)} of {len(pages) + cut} pages"
            )
        file_docs = [d for d in _docs_of_file(ctx, f.id) if d.status in ("ok", "partial")]
        for pc in sorted(found, key=lambda c: (c.page, c.y, c.x)):
            doc = next((d for d in file_docs if d.page_start <= pc.page <= d.page_end), None)
            if doc is None:
                continue
            # the same xref on several pages of one document counts once (first page wins)
            if first_page.setdefault((doc.id, pc.xref), pc.page) != pc.page:
                continue
            region = _page_region(
                f.id,
                pc.page,
                (pc.x, pc.y, pc.x + pc.w, pc.y + pc.h),
                "signature/stamp image",
                pad=0.0,
            )
            crops.append(
                _Crop(
                    CropFingerprint(pc.phash, pc.exact, doc.id, f.id, pc.page, region),
                    pc.xref,
                    _signing_party(doc, region),
                )
            )
    crops.sort(
        key=lambda c: (natural_key(c.fp.document_id), c.fp.page, c.fp.region.y, c.fp.region.x)
    )
    return crops, failed, notes


def _signing_party(doc: Document, region: Region) -> str:
    """UEN key of the contract side (seller or buyer) whose signatory label is nearest to the
    crop, or "" when the document is no contract or either label, box or UEN is missing."""
    if doc.doc_type != "contract":
        return ""
    sides: list[tuple[float, str]] = []
    for label, uen_field in (("signatory_seller", "seller_uen"), ("signatory_buyer", "buyer_uen")):
        cell, uen = doc.fields.get(label), doc.fields.get(uen_field)
        box = cell.box if cell is not None else None
        key = normalise_uen(uen.value) if uen is not None and uen.value else ""
        if box is None or not key or (box.file_id, box.page) != (region.file_id, region.page):
            return ""
        dx = (region.x + region.w / 2) - (box.x + box.w / 2)
        dy = (region.y + region.h / 2) - (box.y + box.h / 2)
        sides.append((dx * dx + dy * dy, key))
    (d1, k1), (d2, k2) = sides
    return "" if k1 == k2 or d1 == d2 else (k1 if d1 < d2 else k2)


def crop_fingerprints(ctx: CaseContext) -> list[CropFingerprint]:
    """Candidate signature/stamp crops of the case; the pipeline records them as
    ``kind="crop_phash"``. Pure and deterministic."""
    crops, _failed, _notes = _collect_crops(ctx)
    return [c.fp for c in crops]


def _hamming(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


def party_keys(doc: Document) -> set[str]:
    """Who the document is from/for; two documents of disjoint non-empty party sets should not
    carry the same signature or stamp image. Empty set = unknown (never flagged)."""

    def field(name: str) -> str:
        cell = doc.fields.get(name)
        return normalise_uen(cell.value) if cell and cell.value else ""

    keys: set[str]
    if doc.doc_type == "invoice":
        keys = {field("supplier_uen")}
    elif doc.doc_type == "purchase_order":
        keys = {field("buyer_uen")}
    elif doc.doc_type == "contract":
        keys = {field("seller_uen"), field("buyer_uen")}
    elif doc.doc_type == "source_of_funds_declaration":
        uen = field("uen")
        if uen:
            keys = {uen}
        else:
            cell = doc.fields.get("declarant_name")
            keys = {_entity_key(cell.value)} if cell and cell.value else set()
    elif doc.doc_type == "bank_statement":
        keys = {"bank:" + doc.issuer.label} if doc.issuer is not None else set()
    else:
        keys = set()
    return {k for k in keys if k}


def _entity_key(name: str | None) -> str:
    try:
        from app.names import entity_key

        return entity_key(name)
    except Exception:  # noqa: BLE001 - names.py unavailable: a plain normalised form is enough
        return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _is_reuse(a: _Crop, b: _Crop, docs: dict[str, Document]) -> bool:
    da, db = docs[a.fp.document_id], docs[b.fp.document_id]
    if da.id == db.id:
        # one image at two places on a contract: seller and buyer sign with the same image
        return da.doc_type == "contract" and a.fp.region != b.fp.region
    if da.doc_type == "bank_statement" and db.doc_type == "bank_statement":
        return False
    # a crop attributed to one side of a contract speaks for that party only: every contract of a
    # bundle names the applicant, so judging by the whole document would never see a buyer's
    # signature pasted onto another buyer's contract
    ka = {a.party} if a.party else party_keys(da)
    kb = {b.party} if b.party else party_keys(db)
    return bool(ka) and bool(kb) and ka.isdisjoint(kb)


def _doc_label(doc: Document) -> str:
    keys = sorted(party_keys(doc))
    if doc.doc_type == "invoice" and keys:
        who = f"supplier {_safe(keys[0], 24)}"
    elif doc.doc_type == "purchase_order" and keys:
        who = f"buyer {_safe(keys[0], 24)}"
    elif doc.doc_type == "contract" and keys:
        who = "parties " + ", ".join(_safe(k, 24) for k in keys)
    elif doc.doc_type == "bank_statement" and keys:
        who = f"issuer {_safe(keys[0].removeprefix('bank:'), 24)}"
    elif keys:
        who = f"party {_safe(keys[0], 24)}"
    else:
        return f"{doc.doc_type} {doc.id}"
    return f"{doc.doc_type} {doc.id} ({who})"


def _join(items: Sequence[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _clusters(crops: list[_Crop]) -> list[list[int]]:
    parent = list(range(len(crops)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(crops)):
        for j in range(i + 1, len(crops)):
            a, b = crops[i].fp, crops[j].fp
            if a.exact == b.exact or _hamming(a.phash, b.phash) <= SIG_MAX_HAMMING:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(crops)):
        groups.setdefault(find(i), []).append(i)
    return [sorted(g) for g in groups.values() if len(g) > 1]


def _cross_case_flags(ctx: CaseContext, crops: list[_Crop]) -> list[Flag]:
    flags: list[Flag] = []
    seen: set[tuple[str, str]] = set()
    applicant_uen = normalise_uen(ctx.applicant.uen)
    for crop in crops:
        key = (crop.fp.document_id, crop.fp.phash)
        if key in seen:
            continue
        seen.add(key)
        hits = ctx.fingerprints.lookup("crop_phash", crop.fp.phash, exclude_case_id=ctx.case.id)
        if not hits:
            continue
        differs = any(
            h.meta.get("applicant_uen")
            and normalise_uen(str(h.meta["applicant_uen"])) != applicant_uen
            for h in hits
        )
        shown = "; ".join(
            f"case {_safe(h.case_id, 24)} document {_safe(h.document_id or '-', 12)} "
            f"on {h.created_at.date().isoformat()}"
            for h in hits[:3]
        )
        more = f" (+{len(hits) - 3} more)" if len(hits) > 3 else ""
        flags.append(
            make_flag(
                "signature_reuse",
                docs=[crop.fp.document_id],
                region=crop.fp.region,
                severity="medium" if differs else "info",
                discriminator=f"xcase_{crop.fp.phash[:8]}",
                evidence=(
                    f"signature/stamp image on document {crop.fp.document_id} was also seen in "
                    f"{shown}{more}: exact image hash match"
                    + ("" if differs else " (same applicant or no applicant recorded)")
                ),
                explanation=(
                    "The same signature or stamp image appears in another case"
                    + (" of a different applicant" if differs else "")
                    + ". This says nothing about whether the signature is genuine."
                ),
            )
        )
    return flags


@check(
    rule="signature_reuse",
    stage="forensics",
    applies_to={"*"},
    severity="medium",
    description=(
        "The same signature or stamp image appears on documents of different parties "
        "(no claim that any signature is genuine)."
    ),
    tamper="one signature/stamp image pasted on documents by different parties",
)
def signature_reuse(ctx: CaseContext) -> list[Flag]:
    if not any(f.mime == "application/pdf" for f in ctx.case.files):
        raise CheckSkipped("no PDF file in the case")
    crops, failed, notes = _collect_crops(ctx)
    if not crops:
        if failed:
            raise RuntimeError("all candidate files unreadable")
        raise CheckSkipped("no signature or stamp image candidates in any document")
    docs = {d.id: d for d in ctx.case.documents}
    flags: list[Flag] = []
    for members in _clusters(crops):
        group = [crops[i] for i in members]
        reuse_pairs = [
            (a, b)
            for ai, a in enumerate(group)
            for b in group[ai + 1 :]
            if (a.fp.exact == b.fp.exact or _hamming(a.fp.phash, b.fp.phash) <= SIG_MAX_HAMMING)
            and _is_reuse(a, b, docs)
        ]
        if not reuse_pairs:
            continue
        doc_ids = sorted({c.fp.document_id for c in group}, key=natural_key)
        exact = all(a.fp.exact == b.fp.exact for a, b in reuse_pairs)
        worst = max(_hamming(a.fp.phash, b.fp.phash) for a, b in reuse_pairs)
        how = (
            "exact pixel match"
            if exact
            else f"near-identical (hamming {worst}/{SIG_HASH_SIZE * SIG_HASH_SIZE})"
        )
        first = group[0].fp
        flags.append(
            make_flag(
                "signature_reuse",
                docs=doc_ids,
                region=first.region,
                extra_regions=[c.fp.region for c in group[1:]][:MAX_EXTRA_REGIONS],
                discriminator=first.phash[:8],
                evidence=(
                    "same signature/stamp image on "
                    + _join([_doc_label(docs[i]) for i in doc_ids])
                    + f": {how}"
                ),
                explanation=(
                    "The same signature or stamp image appears on documents that should come "
                    "from different parties. This says nothing about whether any signature is "
                    "genuine."
                ),
            )
        )
    flags.extend(_cross_case_flags(ctx, crops))
    flags = _cap_flags(flags)
    if notes:
        flags.append(_notice("signature_reuse", ctx.docs(), "; ".join(notes)))
    if failed:
        flags.append(_unreadable_notice(ctx, "signature_reuse", failed))
    return flags


__all__ = [
    "AI_GENERATOR_MARKERS",
    "EDITOR_PATTERNS",
    "OVERLAP_MIN",
    "CropFingerprint",
    "ai_provenance_hint",
    "copy_move",
    "crop_fingerprints",
    "ela_region",
    "image_editor_software",
    "image_modified",
    "noise_mismatch",
    "party_keys",
    "signature_reuse",
]
