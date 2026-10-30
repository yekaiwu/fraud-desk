"""PDF forensics (docs/forensics.md sections 3, 4.2 to 4.4; plan 4.5).

Hint generator, not a verdict: a check that finds nothing means "no signal found", never
"authentic". Everything here reads the raw file (``ctx.file_path``), ``FileRef.pdf_meta`` and the
document list; it never reads extracted cell values, never writes, never calls a model and never
uses the wall clock ("today" is ``ctx.today``).

Rules (all ``stage="forensics"``):
``pdf_editor_software`` ``pdf_modified`` ``pdf_bytes_after_signature`` ``pdf_text_vs_render``
``pdf_producer_unexpected`` ``font_mix`` ``font_span_outlier`` and ``ai_provenance_hint`` (see the
note above that function: it is registered here only when ``forensics_image`` does not own it).

Text taken from the file is untrusted: every such value passes ``_safe`` before it enters a flag,
and ``explanation`` strings are fixed templates.
"""

from __future__ import annotations

import contextlib
import functools
import importlib
import io
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, NamedTuple

import numpy as np
import pikepdf
import pymupdf

from app.checks import registry as _registry
from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext, natural_key
from app.core.models import Document, FileRef, Flag, Region, Severity
from app.core.money import parse_money

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
EDITOR_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in _EDITOR_PATTERN_STRINGS
)

# C3 issuer label -> allowed Producer prefixes. The generator's strings win (R0 decision,
# test-data-and-eval.md 4.1). An empty tuple = no known producer: the document is not evaluated.
EXPECTED_PRODUCERS: dict[str, tuple[str, ...]] = {
    "dbs": ("Mock Composer",),
    "ocbc": ("Mock Exstream",),
    "uob": ("Mock FOP",),
    "software_generated": ("Mock Ledger",),
    "other_bank": (),
}

FONT_MIX_MAX: dict[str, int] = {
    "bank_statement": 3,
    "invoice": 3,
    "financial_statement": 3,
    "contract": 4,
    "purchase_order": 3,
}
FONT_MIX_DEFAULT = 4
FONT_MIX_MIN_WORDS = 3  # a family needs this many words on the page to count
FONT_SIBLING_MIN_FILES = (
    2  # other files of the same document type and producer needed as a baseline
)

MOD_GAP_SECONDS = 300
OVERLAP_MIN = 0.5  # must equal findings.REGION_OVERLAP_MIN

MAX_TEXT_PAGES = 30
MAX_REGIONS_PER_PAGE = 3
MAX_FLAGS_PER_FILE = 10
MAX_EXTRA_REGIONS = 6

REGION_PAD = 0.004
REGION_MIN_SIDE = 0.005
REGION_MAX_AREA = 0.5  # a region covering more of the page than this is not localisation

COVER_MIN = 0.80  # share of a word that an opaque rectangle must cover
REPLACEMENT_MIN = 0.50  # share of a later text span that must lie inside the rectangle
INK_DPI = 100
INK_MIN_PX = 4
INK_THRESHOLD = 40  # gray levels away from the crop median that count as ink
INK_MIN_SHARE = 0.02

MIN_COLUMN = 6
COLUMN_TOL = 0.008  # of the page width, on the right edge x1
MODE_SHARE_MIN = 0.70
SIZE_TOL = 0.3
OUTLIER_FRACTION = 0.10

SIG_RENDER_PAGES = 10
SIG_RENDER_DPI = 72
SIG_PIXEL_DIFF = 24

_CONTROL_CATEGORIES = {"Cc", "Zl", "Zp"}


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


_PDF_DATE = re.compile(
    r"^(?:D:)?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"\s*(?:(Z)(?:\d{2}'?\d{2}'?)?|([+-])(\d{2})(?:'?(\d{2})'?)?)?\s*$",
    re.IGNORECASE,
)


def parse_pdf_date(s: str | None) -> datetime | None:
    """``"D:20261027091500+08'00'"`` -> aware datetime. A date without offset is UTC.
    ``None`` when the string is not a PDF date."""
    if not s:
        return None
    m = _PDF_DATE.match(str(s).strip())
    if not m:
        return None
    year, month, day, hour, minute, second = (int(g) if g else None for g in m.groups()[:6])
    zulu, sign, oh, om = m.group(7), m.group(8), m.group(9), m.group(10)
    try:
        tz = UTC
        if sign and oh is not None:
            delta = timedelta(hours=int(oh), minutes=int(om or 0))
            tz = timezone(delta if sign == "+" else -delta)
        elif zulu:
            tz = UTC
        return datetime(
            year or 1, month or 1, day or 1, hour or 0, minute or 0, second or 0, tzinfo=tz
        )
    except (ValueError, OverflowError):
        return None


def _parse_iso(s: str | None) -> datetime | None:
    """XMP dates (ISO 8601). Naive = UTC."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).strip())
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
_SEPARATORS = re.compile(r"[-,_\s]+")
_STYLE_ONLY = re.compile(r"(?:bold|italic|oblique|regular|roman|narrow|mt|ps)+", re.IGNORECASE)
_BOLD = re.compile(r"bold|black|heavy", re.IGNORECASE)


def font_family(name: str) -> tuple[str, bool]:
    """``"ABCDEF+Arial-BoldMT"`` -> ``("Arial", True)``. Strips the subset prefix, style words
    (bold, italic, oblique, regular, roman, mt, ps, narrow) and separators; the family keeps its
    case for display, compare with ``.lower()``."""
    raw = _SUBSET_PREFIX.sub("", name or "")
    is_bold = bool(_BOLD.search(raw))
    kept: list[str] = []
    for token in _SEPARATORS.split(raw):
        if not token or _STYLE_ONLY.fullmatch(token):
            continue
        while len(token) >= 6 and token[-2:].lower() in ("mt", "ps"):
            token = token[:-2]
        kept.append(token)
    family = "".join(kept) or raw or "unknown"
    return family, is_bold


def _fkey(family: str) -> str:
    return family.lower()


class _Box(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    def inter(self, other: _Box) -> float:
        w = min(self.x1, other.x1) - max(self.x0, other.x0)
        h = min(self.y1, other.y1) - max(self.y0, other.y0)
        return max(0.0, w) * max(0.0, h)

    def union(self, other: _Box) -> _Box:
        return _Box(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def contains_point(self, x: float, y: float, slack: float = 0.0) -> bool:
        return self.x0 - slack <= x <= self.x1 + slack and self.y0 - slack <= y <= self.y1 + slack


def _region(
    file_id: str,
    page: int,
    box: _Box,
    size: tuple[float, float],
    label: str | None = None,
    *,
    origin: tuple[float, float] = (0.0, 0.0),
    pad: float = REGION_PAD,
) -> Region:
    """Page-fraction ``Region``: padded on every side, clamped to [0, 1], at least 0.005 square,
    rounded to 4 decimals (forensics.md 3.4)."""
    w_pt, h_pt = size
    fx0 = (box.x0 - origin[0]) / w_pt - pad
    fx1 = (box.x1 - origin[0]) / w_pt + pad
    fy0 = (box.y0 - origin[1]) / h_pt - pad
    fy1 = (box.y1 - origin[1]) / h_pt + pad

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
    w = min(w, round(1.0 - x, 4)) if x + w > 1.0 else w
    h = min(h, round(1.0 - y, 4)) if y + h > 1.0 else h
    return Region(file_id=file_id, page=page, x=x, y=y, w=w, h=h, label=label)


def _region_area(r: Region) -> float:
    return r.w * r.h


def _region_overlap(a: Region, b: Region) -> float:
    """intersection area / min(area), 0 when on different pages or files."""
    if a.file_id != b.file_id or a.page != b.page:
        return 0.0
    w = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    h = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    if w <= 0 or h <= 0:
        return 0.0
    return (w * h) / min(_region_area(a), _region_area(b))


# --------------------------------------------------------------------------------------
# File and document access
# --------------------------------------------------------------------------------------


def _pdf_files(ctx: CaseContext) -> list[FileRef]:
    files = [f for f in ctx.case.files if f.mime == "application/pdf"]
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


def _cap_flags(flags: list[Flag], dropped: int) -> list[Flag]:
    """Keep at most MAX_FLAGS_PER_FILE flags; the surplus is summarised on the last kept one."""
    kept = flags[:MAX_FLAGS_PER_FILE]
    surplus = dropped + len(flags) - len(kept)
    if surplus > 0 and kept:
        last = kept[-1]
        kept[-1] = last.model_copy(update={"evidence": f"{last.evidence} (+{surplus} more)"})
    return kept


# --------------------------------------------------------------------------------------
# Metadata: XMP (best effort), editor software, modification dates
# --------------------------------------------------------------------------------------

_XMP_KEYS = ("xmp:CreatorTool", "pdf:Producer", "xmp:CreateDate", "xmp:ModifyDate")


@functools.lru_cache(maxsize=32)
def _xmp_values(sha256: str, path: str) -> tuple[tuple[str, str], ...]:
    """XMP tool and date fields of one file; empty on any error (XMP errors are ignored)."""
    out: list[tuple[str, str]] = []
    try:
        with pikepdf.open(path) as pdf:
            meta = pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False)
            for key in _XMP_KEYS:
                try:
                    value = meta.get(key)
                except Exception:  # noqa: BLE001 - malformed XMP is ignored
                    value = None
                if isinstance(value, str) and value.strip():
                    out.append((key, value))
    except Exception:  # noqa: BLE001
        return ()
    return tuple(out)


def _xmp(ctx: CaseContext, f: FileRef) -> dict[str, str]:
    try:
        return dict(_xmp_values(f.sha256, str(ctx.file_path(f.id))))
    except Exception:  # noqa: BLE001
        return {}


@check(
    rule="pdf_editor_software",
    stage="forensics",
    applies_to={"*"},
    severity="medium",
    description="PDF metadata (Info or XMP) names editing software such as an online PDF editor.",
    tamper="editor named in metadata",
)
def pdf_editor_software(ctx: CaseContext) -> list[Flag]:
    files = _pdf_files(ctx)
    if not files:
        raise CheckSkipped("no PDF file in the case")
    flags: list[Flag] = []
    for f in files:
        xmp = _xmp(ctx, f)
        fields = (
            ("Producer", f.pdf_meta.get("Producer", "")),
            ("Creator", f.pdf_meta.get("Creator", "")),
            ("XMP CreatorTool", xmp.get("xmp:CreatorTool", "")),
            ("XMP Producer", xmp.get("pdf:Producer", "")),
        )
        hits: list[str] = []
        names: list[str] = []
        for label, value in fields:
            if not value:
                continue
            for pattern in EDITOR_PATTERNS:
                m = pattern.search(value)
                if m:
                    hits.append(f"{label}='{_safe(value)}'")
                    if m.group(0) not in names:
                        names.append(m.group(0))
                    break
        if not hits:
            continue
        flags.append(
            make_flag(
                "pdf_editor_software",
                docs=_docs_of_file(ctx, f.id),
                evidence="; ".join(hits),
                explanation=f"The PDF metadata names editing software ({_safe(names[0], 40)}).",
                discriminator=f.id,
            )
        )
    return flags


def _date_pair(ctx: CaseContext, f: FileRef) -> tuple[datetime | None, datetime | None]:
    created = parse_pdf_date(f.pdf_meta.get("CreationDate"))
    modified = parse_pdf_date(f.pdf_meta.get("ModDate"))
    if created is None or modified is None:
        xmp = _xmp(ctx, f)
        if created is None:
            created = _parse_iso(xmp.get("xmp:CreateDate"))
        if modified is None:
            modified = _parse_iso(xmp.get("xmp:ModifyDate"))
    return created, modified


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@check(
    rule="pdf_modified",
    stage="forensics",
    applies_to={"*"},
    severity="low",
    description=(
        "PDF was modified more than 300 seconds after creation, before it was created, "
        "or carries a date after the case date."
    ),
    tamper="re-saved after creation",
)
def pdf_modified(ctx: CaseContext) -> list[Flag]:
    files = _pdf_files(ctx)
    flags: list[Flag] = []
    parseable = False
    for f in files:
        created, modified = _date_pair(ctx, f)
        if created is None and modified is None:
            continue
        parseable = True
        reasons: list[str] = []
        gap = None
        if created is not None and modified is not None:
            gap = (modified - created).total_seconds()
            if gap > MOD_GAP_SECONDS:
                reasons.append(f"modified {int(gap)} s after creation")
            elif gap < 0:
                reasons.append("modified before it was created")
        today: date = ctx.today
        # Calendar date as written in the file (its own offset), compared with the case date.
        if (created is not None and created.date() > today) or (
            modified is not None and modified.date() > today
        ):
            reasons.append("a date lies after the case date")
        if not reasons:
            continue
        parts = []
        if created is not None:
            parts.append(f"created {_utc_iso(created)}")
        if modified is not None:
            parts.append(f"modified {_utc_iso(modified)}")
        evidence = ", ".join(parts)
        if gap is not None:
            evidence += f" ({gap:+.0f} s)"
        evidence += "; " + "; ".join(reasons)
        flags.append(
            make_flag(
                "pdf_modified",
                docs=_docs_of_file(ctx, f.id),
                evidence=evidence,
                explanation=(
                    "The PDF's modification date differs from its creation date or lies in the "
                    "future, so it may have been re-saved after it was made."
                ),
                discriminator=f.id,
            )
        )
    if not files:
        raise CheckSkipped("no PDF file in the case")
    if not parseable:
        raise CheckSkipped("no PDF with a parseable CreationDate or ModDate")
    return flags


# --------------------------------------------------------------------------------------
# pdf_producer_unexpected
# --------------------------------------------------------------------------------------


def _doc_pages_are_text(f: FileRef, doc: Document) -> bool:
    """False when any page of the document is a scan (the scanner's producer is not the bank's)."""
    infos = {p.page: p for p in f.pages}
    return not any(infos[p].is_scan for p in range(doc.page_start, doc.page_end + 1) if p in infos)


@check(
    rule="pdf_producer_unexpected",
    stage="forensics",
    applies_to={"*"},
    severity="medium",
    description=(
        "A bank statement PDF was produced by software other than the issuer's known producer."
    ),
    tamper="statement made by other software",
)
def pdf_producer_unexpected(ctx: CaseContext) -> list[Flag]:
    min_prob = ctx.settings.classifier_min_prob
    candidates: list[tuple[Document, FileRef]] = []
    files = {f.id: f for f in _pdf_files(ctx)}
    for doc in ctx.docs("bank_statement"):
        f = files.get(doc.file_id)
        if f is None or doc.issuer is None or doc.issuer.probability < min_prob:
            continue
        candidates.append((doc, f))
    if not candidates:
        raise CheckSkipped(
            f"no bank statement PDF with an issuer classified at probability >= {min_prob:g}"
        )
    flags: list[Flag] = []
    for doc, f in candidates:
        assert doc.issuer is not None
        expected = EXPECTED_PRODUCERS.get(doc.issuer.label, ())
        if not expected or not _doc_pages_are_text(f, doc):
            continue
        producer = f.pdf_meta.get("Producer", "")
        if any(producer.lower().startswith(prefix.lower()) for prefix in expected):
            continue
        shown = f"Producer='{_safe(producer)}'" if producer else "Producer absent"
        flags.append(
            make_flag(
                "pdf_producer_unexpected",
                docs=[doc],
                evidence=(
                    f"issuer={_safe(doc.issuer.label, 30)} (p={doc.issuer.probability:.2f}); "
                    f"{shown}; expected prefix '{expected[0]}'"
                ),
                explanation=(
                    "This statement was made by software that the issuing bank is not known "
                    "to use, which can mean it was rebuilt or re-created."
                ),
                discriminator=f.id,
            )
        )
    return flags


# --------------------------------------------------------------------------------------
# pdf_bytes_after_signature: signed-revision byte-range logic (forensics.md 4.3)
# --------------------------------------------------------------------------------------

_BYTERANGE_RE = re.compile(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]")
_HEX_GAP = re.compile(rb"[0-9A-Fa-f\s]*")


class SigRange(NamedTuple):
    index: int
    a: int
    b: int
    c: int
    d: int
    signed_end: int
    source: Literal["pikepdf", "regex"]


class SigReport(NamedTuple):
    ranges: list[SigRange]
    malformed: list[str]
    last_signed_end: int | None
    trailing: int
    trailing_is_whitespace_only: bool


def _byteranges_pikepdf(data: bytes) -> tuple[list[tuple[int, int, int, int]], list[str]]:
    found: list[tuple[int, int, int, int]] = []
    bad: list[str] = []
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for obj in pdf.objects:
            try:
                raw = obj.get("/ByteRange")
            except Exception:  # noqa: BLE001 - not a dictionary or stream
                continue
            if raw is None:
                continue
            try:
                values = [int(x) for x in raw]
                ok = len(values) == 4 and all(v >= 0 for v in values)
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                bad.append("bad ByteRange syntax")
                continue
            found.append((values[0], values[1], values[2], values[3]))
    return found, bad


def _find_ranges(data: bytes) -> tuple[list[tuple[int, int, int, int, str]], list[str]]:
    """All ``(a, b, c, d, source)`` ByteRanges plus syntax problems. pikepdf is primary; the
    regex also runs so that ranges pikepdf cannot see (unparseable file, superseded object) are
    not missed."""
    ranges: list[tuple[int, int, int, int, str]] = []
    malformed: list[str] = []
    seen: set[tuple[int, int, int, int]] = set()
    try:
        pike, malformed = _byteranges_pikepdf(data)
        for r in pike:
            if r not in seen:
                seen.add(r)
                ranges.append((*r, "pikepdf"))
    except Exception:  # noqa: BLE001 - pikepdf cannot parse it: regex fallback below
        pass
    for m in _BYTERANGE_RE.finditer(data):
        r = (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
        if r not in seen:
            seen.add(r)
            ranges.append((*r, "regex"))
    return ranges, malformed


def signature_ranges(data: bytes) -> list[SigRange]:
    """Every ``/ByteRange`` of four non-negative integers, sorted by the end of the signed bytes."""
    raw, _ = _find_ranges(data)
    ordered = sorted(raw, key=lambda r: (r[2] + r[3], r[1]))
    return [
        SigRange(i, a, b, c, d, c + d, src)  # type: ignore[arg-type]
        for i, (a, b, c, d, src) in enumerate(ordered)
    ]


def _range_problems(r: SigRange, data: bytes) -> list[str]:
    n = len(data)
    label = f"signature {r.index + 1}"
    problems: list[str] = []
    if r.a != 0:
        problems.append(f"{label}: ByteRange does not start at 0")
    if r.b >= r.c:
        problems.append(f"{label}: ByteRange gap is empty or reversed")
    if r.signed_end > n:
        problems.append(
            f"{label}: ByteRange ends at {r.signed_end}, beyond the end of the file ({n})"
        )
    if r.b < r.c <= n:
        delimited = data[r.b : r.b + 1] == b"<" and data[r.c - 1 : r.c] == b">"
        if not delimited or not _HEX_GAP.fullmatch(data[r.b + 1 : r.c - 1]):
            problems.append(f"{label}: gap is not a hex string delimited by < and >")
    return problems


def signed_revision_report(data: bytes) -> SigReport:
    """Pure function of the bytes. ``trailing`` = bytes after the last signed revision."""
    n = len(data)
    _, malformed = _find_ranges(data)
    malformed = list(malformed)
    ranges = signature_ranges(data)
    for r in ranges:
        malformed.extend(_range_problems(r, data))
    for prev, nxt in zip(ranges, ranges[1:], strict=False):
        if nxt.b < prev.signed_end:
            malformed.append(
                f"signature {nxt.index + 1} gap overlaps signed bytes of signature {prev.index + 1}"
            )
    last = max((r.signed_end for r in ranges), default=None)
    trailing = max(n - last, 0) if last is not None else 0
    tail = data[last:] if last is not None else b""
    return SigReport(ranges, malformed, last, trailing, bool(tail) and not tail.strip(b"\r\n"))


def _trailing_is_clean(report: SigReport) -> bool:
    return report.trailing == 0 or (report.trailing <= 2 and report.trailing_is_whitespace_only)


def _gray_pages(data: bytes, limit: int) -> list[np.ndarray]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        out = []
        for i in range(min(doc.page_count, limit)):
            pix = doc[i].get_pixmap(dpi=SIG_RENDER_DPI, colorspace=pymupdf.csGRAY, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
            out.append(arr[:, : pix.width].copy())
        return out
    finally:
        doc.close()


def _render_difference(file_id: str, data: bytes, last: int) -> tuple[str, Region | None]:
    """Render the signed revision and the full file; describe the first visible difference."""
    try:
        signed = _gray_pages(data[:last], SIG_RENDER_PAGES)
    except Exception:  # noqa: BLE001
        return "signed revision not renderable", None
    try:
        full = _gray_pages(data, SIG_RENDER_PAGES)
    except Exception:  # noqa: BLE001
        return "file not renderable for comparison", None
    for i, (a, b) in enumerate(zip(signed, full, strict=False)):
        if a.shape != b.shape:
            return f"render differs on page {i + 1} (page size changed)", None
        mask = np.abs(a.astype(np.int16) - b.astype(np.int16)) > SIG_PIXEL_DIFF
        if mask.any():
            ys, xs = np.nonzero(mask)
            h, w = a.shape
            box = _Box(float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
            region = _region(file_id, i + 1, box, (float(w), float(h)), "changed after signature")
            pct = 100.0 * float(mask.sum()) / float(mask.size)
            return f"render differs on page {i + 1} ({pct:.2f}% of pixels)", region
    if len(full) != len(signed):
        return "render differs: the page count changed", None
    return "render unchanged (appended update adds no visible change)", None


@check(
    rule="pdf_bytes_after_signature",
    stage="forensics",
    applies_to={"*"},
    severity="high",
    description=(
        "Bytes follow the last signed revision of a signed PDF, or a signature byte range is "
        "malformed or does not reach the end of the file."
    ),
    tamper="content appended after a signature",
)
def pdf_bytes_after_signature(ctx: CaseContext) -> list[Flag]:
    files = _pdf_files(ctx)
    if not files:
        raise CheckSkipped("no PDF file in the case")
    flags: list[Flag] = []
    failed: list[str] = []
    for f in files:
        try:
            data = _read_bytes(ctx, f.id)
        except OSError:
            failed.append(f.id)
            continue
        report = signed_revision_report(data)
        clean = _trailing_is_clean(report)
        if not report.ranges and not report.malformed:
            continue  # nothing signed: no signal
        if clean and not report.malformed:
            continue
        parts: list[str] = []
        region: Region | None = None
        if report.malformed:
            shown = "; ".join(_safe(m, 120) for m in report.malformed[:3])
            more = len(report.malformed) - 3
            parts.append(
                f"malformed signature byte range: {shown}"
                + (f" (+{more} more)" if more > 0 else "")
            )
        headline = "The PDF's signature byte range is malformed."
        if not clean and report.last_signed_end is not None:
            last = report.last_signed_end
            covering = max(report.ranges, key=lambda r: r.signed_end)
            parts.append(
                f"{report.trailing} bytes follow the last signed revision "
                f"(signature {covering.index + 1} covers [0,{last}) of {len(data)})"
            )
            tail = data[last:]
            parts.append(
                "trailing bytes include startxref (an incremental update)"
                if b"startxref" in tail
                else "no startxref in the trailing bytes"
            )
            detail, region = _render_difference(f.id, data, last)
            parts.append(detail)
            headline = f"{report.trailing} bytes follow the last signed revision of this PDF."
        flags.append(
            make_flag(
                "pdf_bytes_after_signature",
                docs=_docs_of_file(ctx, f.id),
                evidence="; ".join(parts),
                explanation=(
                    f"{headline} Content added after signing means the signature no longer "
                    "covers everything in the file."
                ),
                region=region,
                severity="high",
                discriminator=f.id,
            )
        )
    if len(failed) == len(files):
        raise RuntimeError("all candidate files unreadable")
    if failed:
        flags.append(
            _notice(
                "pdf_bytes_after_signature",
                [d for fid in failed for d in _docs_of_file(ctx, fid)],
                f"files not analysed (unreadable): {', '.join(failed)}",
            )
        )
    return flags


# --------------------------------------------------------------------------------------
# Text-layer analysis (PyMuPDF, no model call): pdf_text_vs_render, font_span_outlier, font_mix
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Span:
    font: str
    size: float
    flags: int
    box: _Box
    text: str

    @property
    def family(self) -> str:
        return font_family(self.font)[0]

    @property
    def bold(self) -> bool:
        return font_family(self.font)[1] or bool(self.flags & 16)


@dataclass(frozen=True)
class _Trace:
    type: int
    opacity: float
    box: _Box
    seqno: int
    text: str
    color: tuple[float, ...]

    @property
    def invisible(self) -> bool:
        return self.type == 3 or self.opacity == 0


@dataclass
class _Word:
    box: _Box
    text: str
    block: int
    line: int
    no: int
    span: _Span | None = None
    trace: _Trace | None = None

    @property
    def tight(self) -> _Box:
        """Word box with the vertical extent of its text span (tighter than the word box)."""
        if self.trace is None:
            return self.box
        return _Box(self.box.x0, self.trace.box.y0, self.box.x1, self.trace.box.y1)


@dataclass(frozen=True)
class _Cand:
    rule: str
    page: int
    region: Region
    extra: tuple[Region, ...]
    evidence: str
    explanation: str
    severity: Severity = "medium"
    disc: str = ""
    corroborated_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class _FontPage:
    page: int
    families: tuple[tuple[str, int], ...]  # (display name, words), most used first
    keys: tuple[str, ...]  # lower-case family keys in the same order
    seen: frozenset[str] = frozenset()  # every family with at least one word on the page


@dataclass(frozen=True)
class _TextAnalysis:
    file_id: str
    pages_done: int
    pages_total: int
    rotated_skipped: int
    page_errors: int
    cands: tuple[_Cand, ...]
    dropped: tuple[tuple[str, int], ...]
    fonts: tuple[_FontPage, ...]


def _collect_spans(page: pymupdf.Page) -> list[_Span]:
    spans: list[_Span] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for s in line.get("spans", []):
                if not str(s.get("text", "")).strip():
                    continue
                spans.append(
                    _Span(
                        str(s.get("font", "")),
                        float(s.get("size", 0.0)),
                        int(s.get("flags", 0)),
                        _Box(*[float(v) for v in s["bbox"]]),
                        str(s["text"]),
                    )
                )
    return spans


def _collect_traces(page: pymupdf.Page) -> list[_Trace]:
    traces: list[_Trace] = []
    for t in page.get_texttrace():
        text = "".join(chr(c[0]) for c in t.get("chars", ()) if c and c[0] > 0)
        traces.append(
            _Trace(
                int(t.get("type", 0)),
                float(t.get("opacity", 1.0)),
                _Box(*[float(v) for v in t["bbox"]]),
                int(t.get("seqno", 0)),
                text,
                tuple(t.get("color") or ()),
            )
        )
    return traces


def _pick(items: Sequence, word: _Word):  # noqa: ANN202 - generic over _Span / _Trace
    """The span or trace that draws this word: contains its centre, preferably holds its text."""
    cx, cy = word.box.centre
    inside = [i for i in items if i.box.contains_point(cx, cy, 1.0)]
    if not inside:
        return None
    exact = [i for i in inside if word.text in i.text]
    pool = exact or inside
    return min(pool, key=lambda i: i.box.area)


def _collect_words(page: pymupdf.Page, spans: list[_Span], traces: list[_Trace]) -> list[_Word]:
    flags = pymupdf.TEXTFLAGS_WORDS & ~pymupdf.TEXT_MEDIABOX_CLIP
    words: list[_Word] = []
    for x0, y0, x1, y1, text, block, line, no in page.get_text("words", flags=flags):
        if not str(text).strip():
            continue
        w = _Word(_Box(x0, y0, x1, y1), str(text), int(block), int(line), int(no))
        w.span = _pick(spans, w)
        w.trace = _pick(traces, w)
        words.append(w)
    return words


def _fill_text(fill: Sequence[float]) -> str:
    return ",".join(f"{v:g}" for v in fill)


@dataclass(frozen=True)
class _Fill:
    box: _Box
    color: tuple[float, ...]
    seqno: int


def _collect_fills(page: pymupdf.Page) -> list[_Fill]:
    fills: list[_Fill] = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if fill is None:
            continue
        opacity = d.get("fill_opacity")
        if opacity is not None and opacity < 0.99:
            continue
        items = d.get("items", ())
        if any(it[0] == "c" for it in items):  # curves: the bounding box overstates the shape
            continue
        r = d["rect"]
        box = _Box(float(r.x0), float(r.y0), float(r.x1), float(r.y1))
        fills.append(_Fill(box, tuple(float(v) for v in fill), int(d.get("seqno", 0))))
    return fills


def _group_lines(words: list[_Word]) -> list[list[_Word]]:
    """Group words of one (block, line) with consecutive word numbers."""
    groups: list[list[_Word]] = []
    for w in sorted(words, key=lambda x: (x.block, x.line, x.no)):
        last = groups[-1][-1] if groups else None
        if (
            last is not None
            and (last.block, last.line) == (w.block, w.line)
            and w.no == last.no + 1
        ):
            groups[-1].append(w)
        else:
            groups.append([w])
    return groups


def _union(boxes: Sequence[_Box]) -> _Box:
    out = boxes[0]
    for b in boxes[1:]:
        out = out.union(b)
    return out


def _texts(words: Sequence[_Word], n: int = 80) -> str:
    return _safe(" ".join(w.text for w in words), n)


_TVR_EXPLAIN = {
    "hidden": "Page {p} holds text that is not visible on the page.",
    "covered": (
        "Text on page {p} is covered by an opaque box drawn after it, so the visible page "
        "and the text layer disagree."
    ),
    "noink": "Text on page {p} has no visible ink where the text layer says it is.",
}


def _text_vs_render_page(
    page: pymupdf.Page,
    file_id: str,
    pno: int,
    words: list[_Word],
    traces: list[_Trace],
    fills: list[_Fill],
    size: tuple[float, float],
    origin: tuple[float, float],
) -> tuple[list[_Cand], set[int]]:
    """Returns the candidates and the ids of words that are hidden or covered."""
    cands: list[_Cand] = []
    flagged: set[int] = set()

    def reg(box: _Box, label: str) -> Region:
        return _region(file_id, pno, box, size, label, origin=origin)

    # (a) invisible text: render mode 3 or opacity 0
    hidden = [w for w in words if w.trace is not None and w.trace.invisible]
    for group in _group_lines(hidden):
        flagged.update(id(w) for w in group)
        t = group[0].trace
        assert t is not None
        why = "render mode 3" if t.type == 3 else "opacity 0"
        cands.append(
            _Cand(
                "pdf_text_vs_render",
                pno,
                reg(_union([w.box for w in group]), "hidden text"),
                (),
                f"hidden text ({why}): '{_texts(group)}'",
                _TVR_EXPLAIN["hidden"].format(p=pno),
            )
        )

    # (b) covered text: an opaque rectangle drawn later (higher seqno) covers >= 80% of the word
    covered: dict[int, tuple[_Word, _Fill]] = {}
    for w in words:
        if id(w) in flagged or w.trace is None:
            continue
        tight = w.tight
        if tight.area <= 0:
            continue
        best: _Fill | None = None
        best_cov = 0.0
        for fl in fills:
            if fl.seqno <= w.trace.seqno:
                continue
            cov = tight.inter(fl.box) / tight.area
            if cov >= COVER_MIN and cov > best_cov:
                best, best_cov = fl, cov
        if best is not None:
            covered[id(w)] = (w, best)
    by_fill: dict[int, list[_Word]] = {}
    fill_of: dict[int, _Fill] = {}
    for w, fl in covered.values():
        by_fill.setdefault(id(fl), []).append(w)
        fill_of[id(fl)] = fl
    for key in sorted(by_fill, key=lambda k: (fill_of[k].seqno, k)):
        fl = fill_of[key]
        for group in _group_lines(by_fill[key]):
            flagged.update(id(w) for w in group)
            above = [
                t
                for t in sorted(traces, key=lambda t: t.seqno)
                if t.seqno > fl.seqno
                and not t.invisible
                and t.box.area > 0
                and t.box.inter(fl.box) / t.box.area >= REPLACEMENT_MIN
            ]
            above_texts = []
            for t in above:
                s = _safe(t.text.strip(), 40)
                if s and s not in above_texts:
                    above_texts.append(s)
            replaced = (
                "; text drawn above: " + ", ".join(f"'{s}'" for s in above_texts[:2])
                if above_texts
                else ""
            )
            noun = "word" if len(group) == 1 else "words"
            cands.append(
                _Cand(
                    "pdf_text_vs_render",
                    pno,
                    reg(_union([w.box for w in group]), "covered text"),
                    (reg(fl.box, "opaque box"),),
                    f"{noun} '{_texts(group, 60)}' covered by an opaque rectangle "
                    f"(fill {_fill_text(fl.color)}) drawn after it{replaced}",
                    _TVR_EXPLAIN["covered"].format(p=pno),
                )
            )

    # (c) no ink: only now render the page, and only if words are left to test
    rest = [w for w in words if id(w) not in flagged]
    if rest:
        pix = page.get_pixmap(dpi=INK_DPI, colorspace=pymupdf.csGRAY, alpha=False)
        gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
        gray = gray[:, : pix.width]
        scale = INK_DPI / 72.0
        blank: list[_Word] = []
        for w in rest:
            box = w.tight
            px0, py0 = (box.x0 - origin[0]) * scale, (box.y0 - origin[1]) * scale
            px1, py1 = (box.x1 - origin[0]) * scale, (box.y1 - origin[1]) * scale
            if px1 - px0 < INK_MIN_PX or py1 - py0 < INK_MIN_PX:
                continue
            mx, my = (px1 - px0) * 0.10, (py1 - py0) * 0.10
            x0, y0 = int(math.ceil(px0 + mx)), int(math.ceil(py0 + my))
            x1, y1 = int(math.floor(px1 - mx)), int(math.floor(py1 - my))
            x0c, y0c = max(x0, 0), max(y0, 0)
            x1c, y1c = min(x1, pix.width), min(y1, pix.height)
            crop = gray[y0c:y1c, x0c:x1c] if x1c > x0c and y1c > y0c else gray[0:0, 0:0]
            if crop.size == 0:
                blank.append(w)  # outside the rendered page
                continue
            median = int(np.median(crop))
            ink = float((np.abs(crop.astype(np.int16) - median) > INK_THRESHOLD).mean())
            if ink < INK_MIN_SHARE:
                blank.append(w)
        for group in _group_lines(blank):
            cands.append(
                _Cand(
                    "pdf_text_vs_render",
                    pno,
                    reg(_union([w.box for w in group]), "no visible ink"),
                    (),
                    f"no visible ink in the render for text-layer words: '{_texts(group)}'",
                    _TVR_EXPLAIN["noink"].format(p=pno),
                )
            )
    return cands, flagged


def _is_amount_token(text: str) -> bool:
    return re.search(r"\.\d{2}(?!\d)", text) is not None and parse_money(text) is not None


def _mixed_in_word(word: _Word, spans: list[_Span]) -> list[_Span]:
    """Spans that together spell this word when they differ in font or size, else []."""
    _, cy = word.box.centre
    parts = [s for s in spans if s.box.y0 <= cy <= s.box.y1]
    parts = [
        s
        for s in parts
        if s.text.strip()
        and s.box.x0 >= word.box.x0 - 1.0
        and s.box.x1 <= word.box.x1 + 1.0
        and s.text.strip() in word.text
    ]
    parts.sort(key=lambda s: s.box.x0)
    if len(parts) < 2 or "".join(s.text.strip() for s in parts) != word.text:
        return []
    if len({(_fkey(s.family), round(s.size, 1)) for s in parts}) < 2:
        return []
    return parts


def _describe(family: str, size: float, bold: bool) -> str:
    return f"{'bold ' if bold else ''}{_safe(family, 30)} {size:.1f}pt"


def _font_span_outlier_page(
    words: list[_Word],
    spans: list[_Span],
    file_id: str,
    pno: int,
    size: tuple[float, float],
    origin: tuple[float, float],
) -> list[_Cand]:
    tokens = [w for w in words if w.span is not None and _is_amount_token(w.text)]
    if len(tokens) < MIN_COLUMN:
        return []
    tol = COLUMN_TOL * size[0]
    tokens.sort(key=lambda w: (w.box.x1, w.box.y0))
    columns: list[list[_Word]] = []
    for w in tokens:
        if columns and w.box.x1 - columns[-1][-1].box.x1 <= tol:
            columns[-1].append(w)
        else:
            columns.append([w])
    out: list[_Cand] = []
    for col in columns:
        if len(col) < MIN_COLUMN:
            continue
        col.sort(key=lambda w: (w.box.y0, w.box.x0))
        keyed = [(_fkey(w.span.family), round(w.span.size, 1)) for w in col if w.span is not None]
        mode_key, mode_n = Counter(keyed).most_common(1)[0]
        if mode_n / len(col) < MODE_SHARE_MIN:
            continue
        in_mode = [
            w
            for w in col
            if w.span is not None and (_fkey(w.span.family), round(w.span.size, 1)) == mode_key
        ]
        mode_bold = Counter(w.span.bold for w in in_mode if w.span is not None).most_common(1)[0][0]
        mode_first = in_mode[0]
        assert mode_first.span is not None
        mode_family, mode_size = mode_first.span.family, mode_key[1]
        outliers: list[tuple[_Word, str]] = []
        for i, w in enumerate(col):
            assert w.span is not None
            s = w.span
            mixed = _mixed_in_word(w, spans)
            if mixed:
                outliers.append((w, "mixed fonts inside one number"))
            else:
                font_differs = _fkey(s.family) != mode_key[0] or abs(s.size - mode_size) >= SIZE_TOL
                # A weight-only difference is exempt on the first and last row (header, totals).
                weight_differs = s.bold != mode_bold and i not in (0, len(col) - 1)
                if font_differs or weight_differs:
                    outliers.append((w, _describe(s.family, s.size, s.bold)))
        if not outliers or len(outliers) > max(3, int(OUTLIER_FRACTION * len(col))):
            continue
        column_desc = _describe(mode_family, mode_size, mode_bold)
        for w, what in outliers:
            if what == "mixed fonts inside one number":
                evidence = f"'{_safe(w.text, 40)}' mixes fonts inside one number; its column ({len(col)} values) is {column_desc}"
            else:
                evidence = f"'{_safe(w.text, 40)}' is {what}; its column ({len(col)} values) is {column_desc}"
            out.append(
                _Cand(
                    "font_span_outlier",
                    pno,
                    _region(file_id, pno, w.box, size, "retyped number?", origin=origin),
                    (
                        _region(
                            file_id,
                            pno,
                            mode_first.box,
                            size,
                            f"column font: {_safe(mode_family, 30)} {mode_size:.1f}",
                            origin=origin,
                        ),
                    ),
                    evidence,
                    f"A number on page {pno} is set in a different font or size from the rest "
                    "of its column, which can mean it was retyped.",
                )
            )
    return out


def _page_fonts(words: list[_Word], pno: int) -> _FontPage:
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for w in words:
        if w.span is None:
            continue
        key = _fkey(w.span.family)
        counts[key] += 1
        display.setdefault(key, w.span.family)
    ranked = sorted(
        ((k, n) for k, n in counts.items() if n >= FONT_MIX_MIN_WORDS),
        key=lambda kn: (-kn[1], kn[0]),
    )
    return _FontPage(
        pno,
        tuple((display[k], n) for k, n in ranked),
        tuple(k for k, _ in ranked),
        frozenset(counts),
    )


def _analyse_page(
    doc: pymupdf.Document, file_id: str, pno: int
) -> tuple[list[_Cand], list[_Cand], _FontPage]:
    page = doc[pno - 1]
    rect = page.rect
    size = (float(rect.width), float(rect.height))
    origin = (float(rect.x0), float(rect.y0))
    spans = _collect_spans(page)
    traces = _collect_traces(page)
    words = _collect_words(page, spans, traces)
    fills = _collect_fills(page)
    tvr, flagged = _text_vs_render_page(page, file_id, pno, words, traces, fills, size, origin)
    visible = [w for w in words if id(w) not in flagged]
    fso = _font_span_outlier_page(visible, spans, file_id, pno, size, origin)
    return tvr, fso, _page_fonts(words, pno)


def _order(c: _Cand) -> tuple[int, float, float]:
    return (c.page, c.region.y, c.region.x)


def _finalise(cands: list[_Cand], rule: str, file_id: str) -> tuple[list[_Cand], int]:
    """Whole-page filter, per-page cap, discriminators. Returns (kept, dropped_by_cap)."""
    mine = [c for c in cands if c.rule == rule and _region_area(c.region) <= REGION_MAX_AREA]
    mine.sort(key=_order)
    kept: list[_Cand] = []
    dropped = 0
    per_page: Counter[int] = Counter()
    for c in mine:
        if per_page[c.page] >= MAX_REGIONS_PER_PAGE:
            dropped += 1
            continue
        per_page[c.page] += 1
        kept.append(replace(c, disc=f"{file_id}p{c.page}r{per_page[c.page]}"))
    return kept, dropped


def _corroborate(groups: Sequence[list[_Cand]]) -> list[_Cand]:
    """Severity rises to high only when candidates from different rules overlap >= OVERLAP_MIN
    on the same page (forensics.md 4.1 (7))."""
    flat = [c for g in groups for c in g]
    out: list[_Cand] = []
    for c in flat:
        others = sorted(
            {
                o.rule
                for o in flat
                if o.rule != c.rule and _region_overlap(c.region, o.region) >= OVERLAP_MIN
            }
        )
        if others:
            c = replace(c, severity="high", corroborated_by=tuple(others))
        out.append(c)
    return out


@functools.lru_cache(maxsize=32)
def _text_analysis(
    sha256: str, path: str, file_id: str, pages: tuple[int, ...], total: int, rotated: int
) -> _TextAnalysis:
    """One shared analysis per file for the three text-layer rules (order-independent)."""
    doc = pymupdf.open(path)
    tvr_all: list[_Cand] = []
    fso_all: list[_Cand] = []
    fonts: list[_FontPage] = []
    errors = 0
    try:
        for pno in pages:
            try:
                tvr, fso, fp = _analyse_page(doc, file_id, pno)
            except Exception:  # noqa: BLE001 - one bad page must not hide the others
                errors += 1
                continue
            tvr_all.extend(tvr)
            fso_all.extend(fso)
            fonts.append(fp)
    finally:
        doc.close()
    if errors == len(pages) and pages:
        raise RuntimeError("no page could be analysed")
    tvr_kept, tvr_drop = _finalise(tvr_all, "pdf_text_vs_render", file_id)
    fso_kept, fso_drop = _finalise(fso_all, "font_span_outlier", file_id)
    cands = _corroborate([tvr_kept, fso_kept])
    return _TextAnalysis(
        file_id,
        len(pages) - errors,
        total,
        rotated,
        errors,
        tuple(cands),
        (("pdf_text_vs_render", tvr_drop), ("font_span_outlier", fso_drop)),
        tuple(fonts),
    )


def _text_targets(ctx: CaseContext) -> list[tuple[FileRef, tuple[int, ...], int, int]]:
    """PDF files with (eligible pages capped at MAX_TEXT_PAGES, eligible total, rotated count).
    Eligible = text layer, not a scan, no error, rotation 0."""
    out: list[tuple[FileRef, tuple[int, ...], int, int]] = []
    for f in _pdf_files(ctx):
        text_pages = [p for p in f.pages if p.has_text_layer and not p.is_scan and p.error is None]
        pages = sorted(p.page for p in text_pages if p.rotation == 0)
        rotated = sum(1 for p in text_pages if p.rotation != 0)
        if pages or rotated:
            out.append((f, tuple(pages[:MAX_TEXT_PAGES]), len(pages), rotated))
    return out


def _analyses(
    ctx: CaseContext, rule: str
) -> tuple[list[tuple[FileRef, _TextAnalysis]], list[Flag]]:
    """Run (or fetch) the shared analysis for every candidate file; notices for limits."""
    targets = _text_targets(ctx)
    if not targets or not any(t[1] for t in targets):
        raise CheckSkipped("no PDF page with a text layer to analyse")
    results: list[tuple[FileRef, _TextAnalysis]] = []
    notices: list[Flag] = []
    failed: list[str] = []
    for f, pages, total, rotated in targets:
        if not pages:
            notices.append(
                _notice(
                    rule,
                    _docs_of_file(ctx, f.id),
                    f"file {f.id}: {rotated} rotated page(s) skipped (geometry checks need rotation 0)",
                )
            )
            continue
        try:
            analysis = _text_analysis(
                f.sha256, str(ctx.file_path(f.id)), f.id, pages, total, rotated
            )
        except Exception:  # noqa: BLE001
            failed.append(f.id)
            continue
        results.append((f, analysis))
        notes: list[str] = []
        if total > len(pages):
            notes.append(f"pages analysed {len(pages)} of {total} (cap MAX_TEXT_PAGES)")
        if rotated:
            notes.append(f"{rotated} rotated page(s) skipped")
        if analysis.page_errors:
            notes.append(f"{analysis.page_errors} page(s) could not be analysed")
        if notes:
            notices.append(
                _notice(rule, _docs_of_file(ctx, f.id), f"file {f.id}: " + "; ".join(notes))
            )
    if not results:
        raise RuntimeError("all candidate files unreadable")
    if failed:
        notices.append(
            _notice(
                rule,
                [d for fid in failed for d in _docs_of_file(ctx, fid)],
                f"files not analysed (unreadable): {', '.join(failed)}",
            )
        )
    return results, notices


def _cand_flags(ctx: CaseContext, rule: str) -> list[Flag]:
    results, notices = _analyses(ctx, rule)
    flags: list[Flag] = []
    for f, analysis in results:
        mine = [c for c in analysis.cands if c.rule == rule]
        dropped = dict(analysis.dropped).get(rule, 0)
        file_flags: list[Flag] = []
        for c in mine:
            evidence = c.evidence
            if c.corroborated_by:
                evidence += "; corroborated by " + ", ".join(c.corroborated_by)
            file_flags.append(
                make_flag(
                    rule,
                    docs=_docs_for_page(ctx, f.id, c.page),
                    evidence=evidence,
                    explanation=c.explanation,
                    region=c.region,
                    extra_regions=list(c.extra[:MAX_EXTRA_REGIONS]),
                    severity=c.severity,
                    discriminator=c.disc,
                )
            )
        flags.extend(_cap_flags(file_flags, dropped))
    return flags + notices


@check(
    rule="pdf_text_vs_render",
    stage="forensics",
    applies_to={"*"},
    severity="medium",
    description=(
        "Text-layer words are hidden, covered by a later opaque shape, or have no visible ink "
        "in the rendered page."
    ),
    tamper="white box and retyped figure, hidden text",
)
def pdf_text_vs_render(ctx: CaseContext) -> list[Flag]:
    return _cand_flags(ctx, "pdf_text_vs_render")


@check(
    rule="font_span_outlier",
    stage="forensics",
    applies_to={"*"},
    severity="medium",
    description=(
        "One number in a right-aligned amount column uses a different font or size from the "
        "rest of the column."
    ),
    tamper="one retyped amount",
)
def font_span_outlier(ctx: CaseContext) -> list[Flag]:
    return _cand_flags(ctx, "font_span_outlier")


def _doc_type_for_page(ctx: CaseContext, file_id: str, page: int) -> str | None:
    docs = [d for d in _docs_for_page(ctx, file_id, page) if d.page_start <= page <= d.page_end]
    return docs[0].doc_type if docs else None


@check(
    rule="font_mix",
    stage="forensics",
    applies_to={"*"},
    severity="low",
    description="A page uses more font families than its document type normally does.",
    tamper="pasted text in other fonts",
)
def font_mix(ctx: CaseContext) -> list[Flag]:
    results, notices = _analyses(ctx, "font_mix")
    siblings = _sibling_families(ctx, results)
    flags: list[Flag] = []
    for f, analysis in results:
        file_flags: list[Flag] = []
        for fp in analysis.fonts:
            doc_type = _doc_type_for_page(ctx, f.id, fp.page)
            limit = FONT_MIX_MAX.get(doc_type or "", FONT_MIX_DEFAULT)
            group = siblings.get(_sibling_key(f, doc_type), ())
            odd = _sibling_outliers(fp, group, f.id)
            if len(fp.families) <= limit and not odd:
                continue
            listing = ", ".join(f"{_safe(name, 30)}({n})" for name, n in fp.families[:6])
            if len(fp.families) > 6:
                listing += ", ..."
            if len(fp.families) > limit:
                evidence = f"page {fp.page} uses {len(fp.families)} font families: {listing}"
                explanation = (
                    f"Page {fp.page} uses {len(fp.families)} font families where {limit} or "
                    "fewer are usual for this document type, which can mean pasted text."
                )
                region = _font_mix_region(ctx, f, fp, limit)
            else:
                names = ", ".join(_safe(name, 30) for name in odd[:4])
                evidence = (
                    f"page {fp.page} uses {names}, which none of the {len(group) - 1} other "
                    f"{doc_type} files from the same producer use; families here: {listing}"
                )
                explanation = (
                    f"Page {fp.page} has text in a font that no other {doc_type} from the same "
                    "producer in this case uses, which can mean pasted text. A document "
                    "template does not usually change font from one file to the next."
                )
                region = _sibling_region(ctx, f, fp, odd)
            file_flags.append(
                make_flag(
                    "font_mix",
                    docs=_docs_for_page(ctx, f.id, fp.page),
                    evidence=evidence,
                    explanation=explanation,
                    region=region,
                    discriminator=f"{f.id}p{fp.page}r1",
                )
            )
        flags.extend(_cap_flags(file_flags, 0))
    return flags + notices


def _sibling_key(f: FileRef, doc_type: str | None) -> tuple[str | None, str]:
    return (doc_type, str(f.pdf_meta.get("Producer") or "").strip().lower())


def _sibling_families(
    ctx: CaseContext, results: Sequence[tuple[FileRef, _TextAnalysis]]
) -> dict[tuple[str | None, str], tuple[tuple[str, frozenset[str]], ...]]:
    """For every (document type, producer): ``(file id, families seen on any of its pages)`` per
    file. A template does not change font from one month's statement to the next, so a family that
    only one file of a group shows is a hint of pasted text even when the page stays within the
    usual number of families. Needs ``FONT_SIBLING_MIN_FILES`` other files to be a baseline."""
    groups: dict[tuple[str | None, str], dict[str, set[str]]] = {}
    for f, analysis in results:
        for fp in analysis.fonts:
            key = _sibling_key(f, _doc_type_for_page(ctx, f.id, fp.page))
            groups.setdefault(key, {}).setdefault(f.id, set()).update(fp.seen)
    return {
        key: tuple((fid, frozenset(seen)) for fid, seen in sorted(files.items()))
        for key, files in groups.items()
        if key[0] is not None and key[1]
    }


def _sibling_outliers(
    fp: _FontPage, group: Sequence[tuple[str, frozenset[str]]], file_id: str
) -> tuple[str, ...]:
    """Display names of the families (at least ``FONT_MIX_MIN_WORDS`` words) on this page that no
    other file of the group uses at all."""
    others = [seen for fid, seen in group if fid != file_id]
    if len(others) < FONT_SIBLING_MIN_FILES:
        return ()
    known = frozenset().union(*others)
    return tuple(name for name, _n in fp.families if _fkey(name) not in known)


def _sibling_region(
    ctx: CaseContext, f: FileRef, fp: _FontPage, odd: Sequence[str]
) -> Region | None:
    try:
        region = _family_boxes(
            f.sha256, str(ctx.file_path(f.id)), f.id, fp.page, tuple(_fkey(n) for n in odd)
        )
    except Exception:  # noqa: BLE001
        return None
    if region is None or _region_area(region) > REGION_MAX_AREA:
        return None
    return region.model_copy(update={"label": "fonts: " + ", ".join(_safe(n, 20) for n in odd[:3])})


@functools.lru_cache(maxsize=64)
def _family_boxes(
    sha256: str, path: str, file_id: str, page: int, families: tuple[str, ...]
) -> Region | None:
    doc = pymupdf.open(path)
    try:
        pg = doc[page - 1]
        spans = _collect_spans(pg)
        traces = _collect_traces(pg)
        words = _collect_words(pg, spans, traces)
        wanted = set(families)
        boxes = [w.box for w in words if w.span is not None and _fkey(w.span.family) in wanted]
        if not boxes:
            return None
        rect = pg.rect
        region = _region(
            file_id,
            page,
            _union(boxes),
            (float(rect.width), float(rect.height)),
            None,
            origin=(float(rect.x0), float(rect.y0)),
        )
        return region
    finally:
        doc.close()


def _font_mix_region(ctx: CaseContext, f: FileRef, fp: _FontPage, limit: int) -> Region | None:
    """Union box of the words in the least-used families (those beyond the ``limit`` main ones)."""
    surplus = tuple(fp.keys[limit:])
    if not surplus:
        return None
    try:
        region = _family_boxes(f.sha256, str(ctx.file_path(f.id)), f.id, fp.page, surplus)
    except Exception:  # noqa: BLE001
        return None
    if region is None or _region_area(region) > REGION_MAX_AREA:
        return None
    label = "fonts: " + ", ".join(_safe(name, 20) for name, _ in fp.families[limit : limit + 3])
    return region.model_copy(update={"label": label})


# --------------------------------------------------------------------------------------
# ai_provenance_hint
# --------------------------------------------------------------------------------------
# docs/forensics.md 3.1 places this rule in forensics_image.py, while this module's brief also
# lists it. A rule id may be registered by one function only (registry.check raises otherwise), so
# the check below is registered here only when forensics_image does not register it. The logic
# (PDF Info + XMP + C2PA bytes, and JPEG/PNG metadata) lives in plain functions either way.

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
_XMP_PACKET = re.compile(
    rb"<\?xpacket begin.*?<\?xpacket end[^>]*\?>|<x:xmpmeta.*?</x:xmpmeta>", re.S
)


def _xmp_text(data: bytes) -> str:
    return "\n".join(m.group(0).decode("utf-8", "replace") for m in _XMP_PACKET.finditer(data))


def _image_fields(data: bytes) -> list[tuple[str, str]]:
    from PIL import Image

    fields: list[tuple[str, str]] = []
    try:
        with Image.open(io.BytesIO(data)) as im:
            exif = im.getexif()
            for tag, label in ((305, "EXIF Software"), (270, "EXIF ImageDescription")):
                v = exif.get(tag)
                if isinstance(v, str):
                    fields.append((label, v))
            try:
                comment = exif.get_ifd(0x8769).get(37510)
            except Exception:  # noqa: BLE001
                comment = None
            if isinstance(comment, bytes):
                fields.append(("EXIF UserComment", comment.decode("utf-8", "replace")))
            elif isinstance(comment, str):
                fields.append(("EXIF UserComment", comment))
            for key, value in (getattr(im, "text", None) or {}).items():
                if isinstance(value, str):
                    fields.append((f"PNG text {key}", value))
    except Exception:  # noqa: BLE001 - raw XMP scan below still runs
        pass
    return fields


def _ai_hits(f: FileRef, data: bytes) -> list[str]:
    fields: list[tuple[str, str]] = []
    if f.mime == "application/pdf":
        fields.extend((f"PDF Info {k}", v) for k, v in sorted(f.pdf_meta.items()))
    else:
        fields.extend(_image_fields(data))
    xmp = _xmp_text(data)
    if xmp:
        fields.append(("XMP", xmp))
    hits: list[str] = []
    for label, value in fields:
        for pattern in AI_GENERATOR_MARKERS:
            m = pattern.search(value)
            if m:
                hits.append(f"marker '{_safe(m.group(0), 40)}' in {label}")
                break
    if b"c2pa" in data and b"jumb" in data:
        hits.append("content credentials (C2PA manifest) present")
    return hits


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
        hits = _ai_hits(f, data)
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
    if len(failed) == len(files):
        raise RuntimeError("all candidate files unreadable")
    if failed:
        flags.append(
            _notice(
                "ai_provenance_hint",
                [d for fid in failed for d in _docs_of_file(ctx, fid)],
                f"files not analysed (unreadable): {', '.join(failed)}",
            )
        )
    return flags


def _owned_elsewhere(rule: str) -> bool:
    """True when another module (``forensics_image``) already registered ``rule``."""
    with contextlib.suppress(Exception):  # absent or broken: this module provides the check
        importlib.import_module("app.checks.forensics_image")
    meta = _registry._REGISTRY.get(rule)
    return meta is not None and meta.func.__module__ != __name__


if not _owned_elsewhere("ai_provenance_hint"):
    ai_provenance_hint = check(
        rule="ai_provenance_hint",
        stage="forensics",
        applies_to={"*"},
        severity="info",
        description=(
            "Metadata names an image generator or carries content credentials (a hint only; "
            "finding nothing proves nothing)."
        ),
        tamper="generator named in metadata",
    )(ai_provenance_hint)
