"""Case pack: JSON + readable HTML evidence pack (docs/review-workflow-and-audit.md 4.10, api.md 8).

``build_pack`` returns a plain JSON-ready dict. ``render_pack_html`` turns it into one
self-contained printable page: a Jinja2 environment with autoescape ON, no ``|safe`` anywhere,
inline CSS, no script and no external request. Flag evidence is untrusted text and is rendered as
text only (architecture.md section 11). Page images are optional ``data:`` URIs of at most 800 px.
"""

from __future__ import annotations

import base64
import io
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jinja2

from app.audit import iso, read_audit
from app.core.costs import summarise_costs
from app.core.db import CaseNotFound, Database
from app.core.errors import ApiError
from app.core.models import Case, Document, utcnow
from app.review import flag_dispositions, load_entries, pending_entries, version_history

Clock = Callable[[], datetime]

DISCLAIMER = "Decision support only; a human decides; not legal advice"
STUDENT_CAVEAT = (
    "The mapping of verdicts to MAS and STR obligations is our reading as students and is not "
    "legal advice. Retention, STR timing, UBO threshold and tipping-off points are marked "
    "[confirm] and need compliance sign-off."
)
CONFIDENTIALITY = (
    "Internal. Do not disclose flags or this pack to the applicant (tipping-off risk) [confirm]."
)
ESCALATION_NOTE = (
    "Suspicion-formed clock. The tool does not file an STR; compliance decides outside the tool."
)
PACK_VERSION = "1"
PACK_IMAGE_MAX_WIDTH = 800
PACK_MAX_IMAGES = 20

_SEVERITY_COLOURS = {
    "high": (200, 30, 30),
    "medium": (230, 140, 0),
    "low": (110, 110, 110),
    "info": (110, 110, 110),
}


# --------------------------------------------------------------------------------------
# Page rendering (shared with api/files.py)
# --------------------------------------------------------------------------------------


# A page is rendered at the requested width, so its height follows the page's shape: a 1 x 14400 pt PDF or a
# 1 x 50 000 000 px image would ask for gigabytes. Refuse anything above this many output pixels (an A4 page at
# the widest allowed render is about 6 million).
MAX_RENDER_PIXELS = 40_000_000


def _check_render_size(width: int, height: float) -> None:
    if width * height > MAX_RENDER_PIXELS:
        raise ValueError("page too large to render")


def render_page_png(path: Path, mime: str, page: int, width: int) -> bytes:
    """PNG of one page, ``width`` px wide. PDF via PyMuPDF; images are re-encoded after
    ``ImageOps.exif_transpose`` so region fractions line up with the oriented image.

    Raises ``LookupError`` when the page does not exist and ``ValueError`` for unreadable input."""
    if page < 1:
        raise LookupError("pages are numbered from 1")
    if mime == "application/pdf":
        import pymupdf

        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:  # noqa: BLE001 - corrupt file
            raise ValueError(f"unreadable PDF: {type(exc).__name__}") from exc
        try:
            if page > doc.page_count:
                raise LookupError("page out of range")
            pg = doc.load_page(page - 1)
            if pg.rect.width <= 0 or pg.rect.height <= 0:
                raise ValueError("degenerate page size")
            zoom = width / float(pg.rect.width)
            _check_render_size(width, pg.rect.height * zoom)
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()
    if page != 1:
        raise LookupError("an image has exactly one page")
    from PIL import Image, ImageOps

    try:
        with Image.open(path) as raw:
            img = ImageOps.exif_transpose(raw)
            img.load()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"unreadable image: {type(exc).__name__}") from exc
    height = max(1, round(img.height * width / img.width))
    _check_render_size(width, height)
    buf = io.BytesIO()
    img.convert("RGB").resize((width, height), Image.Resampling.LANCZOS).save(buf, "PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------------------


def page_span(file_id: str, start: int, end: int) -> str:
    return f"{file_id}:{start}" if start == end else f"{file_id}:{start}-{end}"


def not_checked_documents(case: Case) -> list[dict[str, str]]:
    """Documents received but not checked, plus pages the classifier labelled boilerplate that sit
    in no listed document (plan 5.3: boilerplate is visible, never silently dropped)."""
    out: list[dict[str, str]] = []
    covered: set[tuple[str, int]] = set()
    for doc in case.documents:
        if doc.status != "received_not_checked":
            continue
        reason = (
            f"classified as {doc.doc_type}: no checks apply"
            if doc.doc_type in ("other", "boilerplate")
            else "no checks for this document type"
        )
        out.append(
            {
                "document_id": doc.id,
                "doc_type": doc.doc_type,
                "pages": page_span(doc.file_id, doc.page_start, doc.page_end),
                "reason": reason,
            }
        )
        covered.update((doc.file_id, p) for p in range(doc.page_start, doc.page_end + 1))
    for f in case.files:
        for p in f.pages:
            label = p.classification.label if p.classification else None
            if label == "boilerplate" and (f.id, p.page) not in covered:
                owner = _owner(case.documents, f.id, p.page)
                out.append(
                    {
                        "document_id": owner.id if owner else "-",
                        "doc_type": "boilerplate",
                        "pages": page_span(f.id, p.page, p.page),
                        "reason": "page classified as boilerplate",
                    }
                )
    return out


def _owner(documents: Sequence[Document], file_id: str, page: int) -> Document | None:
    for d in documents:
        if d.file_id == file_id and d.page_start <= page <= d.page_end:
            return d
    return None


def _classification(c: Any) -> dict[str, Any] | None:
    if c is None:
        return None
    return {
        "label": c.label,
        "probability": c.probability,
        "classifier": c.classifier,
        "version": c.version,
        "reasons": list(c.reasons),
        "rationale": c.rationale,
    }


# --------------------------------------------------------------------------------------
# JSON pack
# --------------------------------------------------------------------------------------


def build_pack(
    db: Database, case_id: str, *, version: int | None = None, clock: Clock = utcnow
) -> dict[str, Any]:
    """The evidence pack as a JSON-ready dict. ``version`` reads a snapshot."""
    try:
        live = db.load_case(case_id)
    except CaseNotFound as exc:
        raise ApiError(
            404, "not_found", "Case not found.", {"kind": "case", "id": case_id}
        ) from exc
    if live.status == "processing" and (version is None or version == live.version):
        raise ApiError(
            409, "case_processing", "The case is still being analysed.", {"status": live.status}
        )
    case = live
    if version is not None and version != live.version:
        try:
            case = db.load_case(case_id, version)
        except CaseNotFound as exc:
            raise ApiError(
                404,
                "not_found",
                "Case version not found.",
                {"kind": "version", "id": case_id, "version": version},
            ) from exc
    entries = load_entries(db, case_id)
    dispositions = flag_dispositions(case, entries)
    flags = []
    for f in case.flags:
        d = f.model_dump(mode="json")
        flags.append(
            {
                "id": d["id"],
                "rule": d["rule"],
                "stage": d["stage"],
                "severity": d["severity"],
                "document_ids": d["document_ids"],
                "table": d["table"],
                "field": d["field"],
                "row": d["row"],
                "region": d["region"],
                "extra_regions": d["extra_regions"],
                "evidence": d["evidence"],
                "explanation": d["explanation"],
                "disposition": dispositions.get(f.id, "open"),
            }
        )
    score = case.score.model_dump(mode="json") if case.score else None
    coverage = None
    if case.coverage is not None:
        cv = case.coverage.model_dump(mode="json")
        coverage = {
            "applicable": cv["applicable"],
            "ran": cv["ran"],
            "ratio": cv["ratio"],
            "skipped": cv["skipped"],
            "pages_total": cv["pages_total"],
            "pages_unchecked": cv["pages_unchecked"],
        }
    unresolved = (
        [e.id for e in pending_entries(entries, case.version)] if case.status == "closed" else []
    )
    return {
        "pack_version": PACK_VERSION,
        "generated_at": iso(clock()),
        "disclaimer": DISCLAIMER,
        "caveat": STUDENT_CAVEAT,
        "confidentiality": CONFIDENTIALITY,
        "case": {
            "id": case.id,
            "reference": case.reference,
            "use_case": case.use_case,
            "status": case.status,
            "version": case.version,
            "today": case.today.isoformat(),
            "created_at": iso(case.created_at),
            "updated_at": iso(case.updated_at),
            "decision": case.decision,
            "lane": case.lane,
            "top_finding": case.top_finding,
        },
        "applicant": case.applicant.model_dump(mode="json"),
        "files": [
            {
                "id": f.id,
                "name": f.name,
                "sha256": f.sha256,
                "size_bytes": f.size_bytes,
                "mime": f.mime,
                "pages": len(f.pages),
                "added_in_version": f.added_in_version,
            }
            for f in case.files
        ],
        "documents": [
            {
                "id": d.id,
                "file_id": d.file_id,
                "pages": page_span(d.file_id, d.page_start, d.page_end),
                "doc_type": d.doc_type,
                "status": d.status,
                "classification": _classification(d.classification),
                "issuer": d.issuer.label if d.issuer else None,
                "extractor": d.extractor,
                "warnings": list(d.warnings),
            }
            for d in case.documents
        ],
        "not_checked_documents": not_checked_documents(case),
        "score": score,
        "coverage": coverage,
        "findings": [
            {
                "id": f.id,
                "title": f.title,
                "severity": f.severity,
                "weight": f.weight,
                "hard_stop": f.hard_stop,
                "rules": list(f.rules),
                "document_ids": list(f.document_ids),
                "flag_ids": list(f.flag_ids),
                "summary": f.summary,
            }
            for f in case.findings
        ],
        "flags": flags,
        "check_runs": [
            {"rule": r.rule, "status": r.status, "reason": r.reason, "flags": r.flags}
            for r in case.check_runs
        ],
        "stages": [s.model_dump(mode="json") for s in case.stages],
        "cost_summary": summarise_costs(case),
        "provenance": case.provenance.model_dump(mode="json") if case.provenance else None,
        "review": [e.model_dump(mode="json") for e in entries],
        "unresolved_at_close": unresolved,
        "escalation": {
            "escalated_at": iso(case.escalated_at) if case.escalated_at else None,
            "note": ESCALATION_NOTE,
        },
        "versions": version_history(db, case_id),
        "audit": read_audit(db, case_id),
    }


# --------------------------------------------------------------------------------------
# Page images with flag boxes (HTML only)
# --------------------------------------------------------------------------------------


def _regions_of(flag: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    out = []
    if flag.get("region"):
        out.append(flag["region"])
    out.extend(flag.get("extra_regions") or [])
    return out


def pages_with_regions(pack: Mapping[str, Any]) -> list[tuple[str, int]]:
    """``(file_id, page)`` of every page that carries a flag region, in flag order."""
    seen: dict[tuple[str, int], None] = {}
    for flag in pack["flags"]:
        for region in _regions_of(flag):
            seen.setdefault((region["file_id"], int(region["page"])), None)
    return list(seen)


def build_page_images(
    db: Database, pack: Mapping[str, Any], *, max_images: int = PACK_MAX_IMAGES
) -> dict[tuple[str, int], bytes]:
    """PNG bytes (<= 800 px wide, flag boxes drawn: red high, amber medium, grey low/info, with the
    flag number) for the pages that carry a flag region; at most ``max_images`` pages."""
    from PIL import Image, ImageDraw, ImageFont

    files = {f["id"]: f for f in pack["files"]}
    numbers = {f["id"]: i for i, f in enumerate(pack["flags"], start=1)}
    font = ImageFont.load_default()
    out: dict[tuple[str, int], bytes] = {}
    for file_id, page in pages_with_regions(pack):
        if len(out) >= max_images:
            break
        ref = files.get(file_id)
        if ref is None:
            continue
        try:
            path = db.files.path_for(ref["sha256"])
            if not path.is_file():
                continue
            png = render_page_png(path, ref["mime"], page, PACK_IMAGE_MAX_WIDTH)
        except (LookupError, ValueError, OSError):
            continue
        with Image.open(io.BytesIO(png)) as base:
            img = base.convert("RGB")
        draw = ImageDraw.Draw(img)
        for flag in pack["flags"]:
            colour = _SEVERITY_COLOURS.get(flag["severity"], (110, 110, 110))
            for region in _regions_of(flag):
                if region["file_id"] != file_id or int(region["page"]) != page:
                    continue
                x0, y0 = region["x"] * img.width, region["y"] * img.height
                x1, y1 = x0 + region["w"] * img.width, y0 + region["h"] * img.height
                draw.rectangle([x0, y0, x1, y1], outline=colour, width=2)
                label = str(numbers[flag["id"]])
                tw = int(draw.textlength(label, font=font)) + 6
                top = max(0, y0 - 12)
                draw.rectangle([x0, top, x0 + tw, top + 12], fill=colour)
                draw.text((x0 + 3, top), label, fill=(255, 255, 255), font=font)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        out[(file_id, page)] = buf.getvalue()
    return out


# --------------------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------------------

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Case pack {{ pack.case.id }} v{{ pack.case.version }}</title>
<style>
@page { size: A4; margin: 14mm; }
body { font-family: Arial, Helvetica, sans-serif; font-size: 13px; color: #1b1b1b; margin: 0 auto;
       max-width: 960px; padding: 16px; line-height: 1.4; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 24px 0 8px; border-bottom: 1px solid #bbb; padding-bottom: 3px; }
h3 { font-size: 14px; margin: 0 0 4px; }
.banner { border: 2px solid #8a1c1c; background: #fdf0f0; padding: 8px 10px; margin: 8px 0; }
.note { border: 1px solid #aaa; background: #f6f6f6; padding: 8px 10px; margin: 8px 0; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 12px; }
th, td { border: 1px solid #ccc; padding: 4px 6px; text-align: left; vertical-align: top;
         overflow-wrap: anywhere; }
th { background: #efefef; }
.finding { border: 1px solid #999; padding: 8px 10px; margin: 10px 0; break-inside: avoid; }
.flag { break-inside: avoid; page-break-inside: avoid; }
.sev { font-weight: bold; text-transform: uppercase; }
.sev-high { color: #b00020; }
.sev-medium { color: #a05a00; }
.sev-low, .sev-info { color: #555; }
.mono { font-family: Consolas, Menlo, monospace; font-size: 12px; overflow-wrap: anywhere; }
figure { margin: 10px 0; break-inside: avoid; }
figure img { max-width: 100%; border: 1px solid #999; }
.muted { color: #666; }
footer { margin-top: 28px; border-top: 1px solid #bbb; padding-top: 8px; font-size: 12px; }
@media print { body { padding: 0; max-width: none; } }
</style>
</head>
<body>
<header>
<h1>Case pack: {{ pack.case.id }} (version {{ pack.case.version }})</h1>
<p class="muted">Generated {{ pack.generated_at }} &middot; use case {{ pack.case.use_case }} &middot;
status {{ pack.case.status }}{% if pack.case.reference %} &middot; reference {{ pack.case.reference }}{% endif %}
&middot; analysis date {{ pack.case.today }}</p>
<p><strong>{{ pack.disclaimer }}</strong></p>
<p class="note">{{ pack.caveat }}</p>
<p class="banner">{{ pack.confidentiality }}</p>
</header>

<h2>Applicant and decision</h2>
<table>
<tr><th>Applicant</th><td>{{ pack.applicant.name or "not known" }}</td>
<th>UEN</th><td>{{ pack.applicant.uen or "not known" }}</td>
<th>Source</th><td>{{ pack.applicant.source }}</td></tr>
<tr><th>Decision</th><td>{{ pack.case.decision or "none yet" }}</td>
<th>Lane</th><td>{{ pack.case.lane or "-" }}</td>
<th>Top finding</th><td>{{ pack.case.top_finding or "-" }}</td></tr>
</table>

<h2>Score and coverage</h2>
{% if pack.score %}
<p>Score <strong>{{ pack.score.value }}</strong> of 100, band <strong>{{ pack.score.band }}</strong>{% if pack.score.partial %}
(<strong>partial</strong>: the score is a floor, checks that did not run can only add flags){% endif %}.</p>
{% if pack.score.partial_reasons %}
<ul>{% for r in pack.score.partial_reasons %}<li>{{ r }}</li>{% endfor %}</ul>
{% endif %}
{% else %}<p class="muted">No score yet.</p>{% endif %}
{% if pack.coverage %}
<p>{{ pack.coverage.ran }} of {{ pack.coverage.applicable }} checks ran (ratio {{ pack.coverage.ratio }});
{{ pack.coverage.pages_unchecked }} of {{ pack.coverage.pages_total }} pages not checked.</p>
{% if pack.coverage.skipped %}
<table><tr><th>Check</th><th>Status</th><th>Reason</th></tr>
{% for s in pack.coverage.skipped %}<tr><td>{{ s.rule }}</td><td>{{ s.status }}</td><td>{{ s.reason }}</td></tr>{% endfor %}
</table>
{% endif %}
{% endif %}

<h2>Escalation</h2>
{% if pack.escalation.escalated_at %}
<p>Suspicion formed (escalated to AML investigation): <strong>{{ pack.escalation.escalated_at }}</strong>{% if elapsed %}
&middot; {{ elapsed }} elapsed at generation{% endif %}.</p>
{% else %}<p class="muted">Not escalated.</p>{% endif %}
<p class="muted">{{ pack.escalation.note }}</p>

<h2>Findings and flags</h2>
{% if not pack.findings %}<p class="muted">No findings.</p>{% endif %}
{% for f in pack.findings %}
<section class="finding">
<h3><span class="sev sev-{{ f.severity }}">{{ f.severity }}</span> {{ f.title }}</h3>
<p class="muted">{{ f.id }} &middot; weight {{ f.weight }}{% if f.hard_stop %} &middot; hard stop{% endif %}
&middot; rules: {{ f.rules | join(", ") }} &middot; documents: {{ f.document_ids | join(", ") or "-" }}</p>
<p>{{ f.summary }}</p>
<table>
<tr><th>#</th><th>Severity</th><th>Rule</th><th>Where</th><th>Disposition</th><th>Explanation and evidence</th></tr>
{% for fid in f.flag_ids %}{% set fl = flags_by_id.get(fid) %}{% if fl %}
<tr class="flag"><td>{{ flag_no[fid] }}</td>
<td><span class="sev sev-{{ fl.severity }}">{{ fl.severity }}</span></td>
<td>{{ fl.rule }}</td>
<td>{{ fl.document_ids | join(", ") }}{% if fl.table %} / {{ fl.table }}{% endif %}{% if fl.row is not none %} row {{ fl.row + 1 }}{% endif %}{% if fl.field %} / {{ fl.field }}{% endif %}</td>
<td>{{ fl.disposition }}</td>
<td>{{ fl.explanation }}<br><span class="mono">{{ fl.evidence }}</span></td></tr>
{% endif %}{% endfor %}
</table>
</section>
{% endfor %}
{% if loose_flags %}
<h3>Flags not in a finding</h3>
<table>
<tr><th>#</th><th>Severity</th><th>Rule</th><th>Disposition</th><th>Explanation and evidence</th></tr>
{% for fl in loose_flags %}
<tr class="flag"><td>{{ flag_no[fl.id] }}</td><td><span class="sev sev-{{ fl.severity }}">{{ fl.severity }}</span></td>
<td>{{ fl.rule }}</td><td>{{ fl.disposition }}</td>
<td>{{ fl.explanation }}<br><span class="mono">{{ fl.evidence }}</span></td></tr>
{% endfor %}
</table>
{% endif %}

{% if images or omitted_pages %}
<h2>Pages with flagged regions</h2>
{% for im in images %}
<figure><img alt="Page {{ im.page }} of file {{ im.file_id }} with flagged regions" src="{{ im.uri }}">
<figcaption class="muted">File {{ im.file_id }}, page {{ im.page }}; flags {{ im.numbers | join(", ") }}</figcaption></figure>
{% endfor %}
{% for om in omitted_pages %}<p class="muted">Image omitted: file {{ om.file_id }}, page {{ om.page }} (flags {{ om.numbers | join(", ") }}).</p>{% endfor %}
{% endif %}

<h2>Documents received but not checked</h2>
{% if pack.not_checked_documents %}
<table><tr><th>Document</th><th>Type</th><th>Pages</th><th>Reason</th></tr>
{% for d in pack.not_checked_documents %}<tr><td>{{ d.document_id }}</td><td>{{ d.doc_type }}</td><td>{{ d.pages }}</td><td>{{ d.reason }}</td></tr>{% endfor %}
</table>
{% else %}<p class="muted">None.</p>{% endif %}

<h2>Files and documents</h2>
<table><tr><th>File</th><th>Name</th><th>Type</th><th>Pages</th><th>Added in</th><th>SHA-256</th></tr>
{% for f in pack.files %}<tr><td>{{ f.id }}</td><td>{{ f.name }}</td><td>{{ f.mime }}</td><td>{{ f.pages }}</td><td>v{{ f.added_in_version }}</td><td class="mono">{{ f.sha256 }}</td></tr>{% endfor %}
</table>
<table><tr><th>Document</th><th>Pages</th><th>Type</th><th>Status</th><th>Classifier</th><th>Warnings</th></tr>
{% for d in pack.documents %}<tr><td>{{ d.id }}</td><td>{{ d.pages }}</td><td>{{ d.doc_type }}</td><td>{{ d.status }}</td>
<td>{{ d.classification.classifier if d.classification else "-" }} ({{ "%.2f" | format(d.classification.probability) if d.classification else "-" }})</td>
<td>{{ d.warnings | join("; ") }}</td></tr>{% endfor %}
</table>

<h2>Review log</h2>
{% if pack.unresolved_at_close %}
<p class="banner">Unresolved at close: dismissals still waiting for a second approver: {{ pack.unresolved_at_close | join(", ") }}.</p>
{% endif %}
{% if pack.review %}
<table><tr><th>Entry</th><th>Version</th><th>When</th><th>Reviewer</th><th>Kind</th><th>Decision</th><th>Status</th><th>Targets</th><th>Reason</th></tr>
{% for e in pack.review %}<tr><td>{{ e.id }}</td><td>v{{ e.case_version }}</td><td>{{ e.created_at }}</td><td>{{ e.reviewer }}</td>
<td>{{ e.kind }}</td><td>{{ e.decision }}</td><td>{{ e.status }}</td>
<td>{{ (e.flag_ids | join(", ")) or e.target_entry_id or e.finding_id or "-" }}</td><td>{{ e.reason }}</td></tr>{% endfor %}
</table>
{% else %}<p class="muted">No review entries.</p>{% endif %}

<h2>Provenance and versions</h2>
{% if pack.provenance %}
<table>
{% for k in ["app_version", "ruleset_version", "settings_hash", "flags_digest", "prompt_hash", "today", "created_at"] %}
<tr><th>{{ k }}</th><td class="mono">{{ pack.provenance[k] }}</td></tr>{% endfor %}
<tr><th>models</th><td class="mono">{{ pack.provenance.models | tojson }}</td></tr>
</table>
{% else %}<p class="muted">No provenance stamp yet.</p>{% endif %}
<table><tr><th>Version</th><th>Created</th><th>Score</th><th>Band</th><th>Reason</th><th>Current</th></tr>
{% for v in pack.versions %}<tr><td>{{ v.version }}</td><td>{{ v.created_at }}</td><td>{{ v.score if v.score is not none else "-" }}</td>
<td>{{ v.band or "-" }}</td><td>{{ v.reason }}</td><td>{{ "yes" if v.current else "" }}</td></tr>{% endfor %}
</table>
<p class="muted">Cost: {{ pack.cost_summary.total_usd }} USD over {{ pack.cost_summary.calls }} model call(s),
{{ pack.cost_summary.cache_hits }} cache hit(s).</p>

<h2>Audit log</h2>
{% if pack.audit %}
<table><tr><th>#</th><th>When</th><th>Actor</th><th>Action</th><th>Detail (ids and hashes only)</th></tr>
{% for a in pack.audit %}<tr><td>{{ a.id }}</td><td>{{ a.ts }}</td><td>{{ a.actor }}</td><td>{{ a.action }}</td>
<td class="mono">{{ a.detail | tojson }}</td></tr>{% endfor %}
</table>
{% else %}<p class="muted">No audit rows.</p>{% endif %}

<footer>
<p><strong>{{ pack.disclaimer }}</strong></p>
<p>{{ pack.caveat }}</p>
<p>{{ pack.confidentiality }}</p>
</footer>
</body>
</html>
"""

_ENV = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined)
_COMPILED = _ENV.from_string(_TEMPLATE)


def _elapsed(since: str, until: str) -> str:
    try:
        a = datetime.fromisoformat(since)
        b = datetime.fromisoformat(until)
    except ValueError:
        return ""
    seconds = max(0, int((b.astimezone(UTC) - a.astimezone(UTC)).total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = ([f"{days} d"] if days else []) + ([f"{hours} h"] if hours else []) + [f"{minutes} min"]
    return " ".join(parts)


def render_pack_html(
    pack: Mapping[str, Any], *, page_images: Mapping[tuple[str, int], bytes] | None = None
) -> str:
    """One self-contained printable page. ``page_images`` maps ``(file_id, page)`` to PNG bytes
    (see ``build_page_images``); pages with flag regions but no image are listed as omitted."""
    flags = list(pack["flags"])
    flag_no = {f["id"]: i for i, f in enumerate(flags, start=1)}
    flags_by_id = {f["id"]: f for f in flags}
    in_finding = {fid for fnd in pack["findings"] for fid in fnd["flag_ids"]}
    loose = [f for f in flags if f["id"] not in in_finding]

    numbers_by_page: dict[tuple[str, int], list[int]] = {}
    for f in flags:
        for region in _regions_of(f):
            key = (region["file_id"], int(region["page"]))
            lst = numbers_by_page.setdefault(key, [])
            if flag_no[f["id"]] not in lst:
                lst.append(flag_no[f["id"]])
    images = []
    omitted = []
    given = page_images or {}
    for key, numbers in numbers_by_page.items():
        entry = {"file_id": key[0], "page": key[1], "numbers": numbers}
        data = given.get(key)
        if data is not None:
            entry["uri"] = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
            images.append(entry)
        else:
            omitted.append(entry)
    escalated = pack["escalation"].get("escalated_at")
    return _COMPILED.render(
        pack=pack,
        flag_no=flag_no,
        flags_by_id=flags_by_id,
        loose_flags=loose,
        images=images,
        omitted_pages=omitted,
        elapsed=_elapsed(escalated, pack["generated_at"]) if escalated else "",
    )
