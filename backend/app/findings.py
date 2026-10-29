"""Grouping of correlated flags into findings (docs/scoring-and-queue.md sections 4.1 to 4.5).

One underlying cause is one finding and a finding is counted once in the score (plan 8.4). Every
flag belongs to exactly one finding, info flags included (weight 0). The functions are pure and
deterministic: the input order of the flags never changes the output.

Flag text is untrusted (it can be quoted from a document): titles and summaries are plain text
and the UI renders them as text only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Literal, NamedTuple

from app.core.context import natural_key
from app.core.models import SCORE_WEIGHTS, Case, Finding, Flag, Region

SEVERITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2, "info": 3}
TITLE_MAX_CHARS = 140
SUMMARY_MAX_CHARS = 300
REGION_OVERLAP_MIN = 0.5  # starting guess, recalibrated from the eval (section 8)
_EPS = 1e-9

GroupKind = Literal["per_doc", "per_file", "shared", "bundle", "anchor", "region"]


class GroupFamily(NamedTuple):
    prefix: str
    kind: GroupKind
    rules: frozenset[str]


# The table of section 4.1 as data (rows 1 to 13; row 14 is the default for any other rule).
FAMILIES: tuple[GroupFamily, ...] = (
    GroupFamily("stmt_arith", "per_doc", frozenset({"bs_totals", "bs_running_balance"})),
    GroupFamily("inv_arith", "per_doc", frozenset({"inv_lines", "inv_gst", "inv_total"})),
    GroupFamily("fs_arith", "per_doc", frozenset({"fs_balance", "fs_subtotals", "fs_cash_tie"})),
    GroupFamily(
        "id_doc",
        "per_doc",
        frozenset({"id_format", "id_checksum", "passport_mrz", "date_valid"}),
    ),
    GroupFamily(
        "meta",
        "per_file",
        frozenset(
            {
                "pdf_editor_software",
                "pdf_modified",
                "pdf_producer_unexpected",
                "image_editor_software",
                "image_modified",
            }
        ),
    ),
    GroupFamily("holder", "shared", frozenset({"x_ownership_vs_registry", "kyc_shareholdings"})),
    GroupFamily("director", "shared", frozenset({"x_directors"})),
    GroupFamily("entity", "shared", frozenset({"x_entity", "fs_entity", "bs_holder"})),
    GroupFamily("remitter", "shared", frozenset({"x_remitter_vs_buyer"})),
    GroupFamily("dup", "shared", frozenset({"inv_duplicate", "signature_reuse", "file_reuse"})),
    GroupFamily(
        "pattern",
        "bundle",
        frozenset(
            {
                "x_invoice_vs_receipts",
                "inv_sequence",
                "bs_round_numbers",
                "bs_benford",
                "font_mix",
            }
        ),
    ),
    GroupFamily("anchor", "anchor", frozenset({"x_invoice_vs_contract"})),
    GroupFamily(
        "region",
        "region",
        frozenset(
            {
                "pdf_text_vs_render",
                "font_span_outlier",
                "ela_region",
                "copy_move",
                "noise_mismatch",
            }
        ),
    ),
)

_RULE_FAMILY: dict[str, GroupFamily] = {r: fam for fam in FAMILIES for r in fam.rules}

# A clean scan or photo makes ELA and copy-move fire on page after page. The detector marks a lone
# (uncorroborated) region low; low flags of these rules count once per rule for the whole bundle
# ("one noisy check alone should not push a clean file into the High band", plan 4.5). A region
# that a second detector corroborates is high and keeps its region cluster.
NOISY_RASTER_RULES: frozenset[str] = frozenset({"ela_region", "copy_move"})


def _is_noisy(flag: Flag) -> bool:
    return flag.rule in NOISY_RASTER_RULES and flag.severity == "low"


# Section 4.5. A hard stop never changes the score; it only blocks the fast lane.
HARD_STOP_RULES: frozenset[str] = frozenset(
    {
        "pdf_bytes_after_signature",
        "pdf_text_vs_render",
        "pdf_editor_software",
        "signature_reuse",
        "copy_move",
        "inv_duplicate",
        "file_reuse",
        "bs_pass_through",
        "x_declared_vs_activity",
        "x_remitter_vs_buyer",
        "x_sof_support",
        "x_ownership_vs_registry",
        "x_directors",
        "id_checksum",
        "passport_mrz",
    }
)


def default_hard_stop(severity: str, rules: Iterable[str]) -> bool:
    """The hard-stop rule at the code defaults: high severity, or medium severity on a rule of
    ``HARD_STOP_RULES``."""
    return severity == "high" or (severity == "medium" and bool(set(rules) & HARD_STOP_RULES))


def is_high_flag(flag: Flag) -> bool:
    """Review controls (approve blocked while undecided, dismissal needs a second approver) apply
    to a flag that is high now OR was high under the code defaults: a check catalogue override that
    lowers a severity reduces points only and never relaxes a review control."""
    return flag.severity == "high" or flag.code_severity == "high"


# Suspected laundering routes to the AML lane whatever the bundle type (plan 7.3).
AML_ROUTING_RULES: frozenset[str] = frozenset(
    {"x_sof_support", "x_remitter_vs_buyer", "bs_pass_through"}
)

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_TRAILING_N = re.compile(r"#\d+$")
# a full stop after these does not end a sentence (company forms, titles)
_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "pte.",
        "ltd.",
        "co.",
        "corp.",
        "inc.",
        "bhd.",
        "sdn.",
        "llc.",
        "mr.",
        "mrs.",
        "ms.",
        "dr.",
        "no.",
        "vs.",
        "s/o.",
        "d/o.",
    }
)


# ---------------------------------------------------------------------------------- text helpers


def sentence(text: str) -> str:
    """First sentence of ``text``, whitespace collapsed ("2.4M" is not split), no trailing dot."""
    collapsed = " ".join(text.split())
    first = collapsed
    for m in _SENTENCE_END.finditer(collapsed):
        last_word = collapsed[: m.start()].rsplit(" ", 1)[-1].lower()
        if last_word in _ABBREVIATIONS:  # "Kestrel Capital Holdings Pte. Ltd." is one name
            continue
        first = collapsed[: m.start()]
        break
    return first[:-1] if first.endswith(".") else first


def cap_words(text: str, limit: int) -> str:
    """At most ``limit`` characters; a longer text is cut at a word boundary and ends with ``…``."""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    cut = text[: limit - 1]
    if text[limit - 1] != " " and " " in cut:  # the cut falls inside a word
        cut = cut.rsplit(" ", 1)[0]
    cut = cut.rstrip(" ,;:-")
    return (cut or text[: limit - 1]) + "…"


# ------------------------------------------------------------------------------ keys and geometry


def flag_discriminator(flag: Flag) -> str | None:
    """Entity key set by ``make_flag(discriminator=...)``: the text after the first ``~`` of the
    flag id (a trailing ``#<n>`` collision suffix is ignored), lower-cased and stripped."""
    stripped = _TRAILING_N.sub("", flag.id)
    if "~" not in stripped:
        return None
    disc = stripped.split("~", 1)[1].strip().lower()
    return disc or None


def _ids_sorted(ids: Sequence[str]) -> list[str]:
    return sorted(set(ids), key=natural_key)


def _subject(flag: Flag) -> str:
    if flag.document_ids:
        return "+".join(_ids_sorted(flag.document_ids))
    if flag.region is not None:
        return flag.region.file_id
    return "case"


def _file_key(flag: Flag, case: Case) -> str:
    by_id = {d.id: d.file_id for d in case.documents}
    files = sorted({by_id[d] for d in flag.document_ids if d in by_id}, key=natural_key)
    if files:
        return "+".join(files)
    if flag.region is not None:
        return flag.region.file_id
    return "case"


def _default_key(flag: Flag) -> str:
    return f"rule:{flag.rule}:{_subject(flag)}"


def _area(r: Region) -> float:
    return r.w * r.h


def region_overlap(a: Region, b: Region) -> float:
    """Intersection area over the smaller area (containment counts as 1.0, touching as 0)."""
    ix = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    iy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    if ix <= 0 or iy <= 0:
        return 0.0
    return (ix * iy) / min(_area(a), _area(b))


def _region_clusters(flags: Sequence[Flag]) -> dict[int, int]:
    """Section 4.2: index of each region-family flag with a region -> cluster number ``n``
    (1-based inside its (file, page) bucket, ordered by the min y, min x of the union box)."""
    buckets: dict[tuple[str, int], list[int]] = {}
    for i, f in enumerate(flags):
        fam = _RULE_FAMILY.get(f.rule)
        if _is_noisy(f):
            continue
        if fam is not None and fam.kind == "region" and f.region is not None:
            buckets.setdefault((f.region.file_id, f.region.page), []).append(i)

    numbers: dict[int, int] = {}
    for members in buckets.values():
        parent = {i: i for i in members}

        def find(i: int, parent: dict[int, int] = parent) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for pos, i in enumerate(members):
            for j in members[pos + 1 :]:
                a, b = flags[i].region, flags[j].region
                assert a is not None and b is not None
                if region_overlap(a, b) >= REGION_OVERLAP_MIN - _EPS:
                    parent[find(i)] = find(j)
        comps: dict[int, list[int]] = {}
        for i in members:
            comps.setdefault(find(i), []).append(i)

        def order(comp: list[int]) -> tuple[float, float, str]:
            boxes = [flags[i].region for i in comp]
            return (
                min(b.y for b in boxes if b is not None),
                min(b.x for b in boxes if b is not None),
                min(flags[i].id for i in comp),
            )

        for n, comp in enumerate(sorted(comps.values(), key=order), start=1):
            for i in comp:
                numbers[i] = n
    return numbers


def _key(flag: Flag, case: Case, region_n: int | None) -> str:
    if _is_noisy(flag):
        return f"noisy:{flag.rule}"
    fam = _RULE_FAMILY.get(flag.rule)
    if fam is None:
        return _default_key(flag)
    if fam.kind == "per_doc":
        return f"{fam.prefix}:{_subject(flag)}"
    if fam.kind == "per_file":
        return f"{fam.prefix}:{_file_key(flag, case)}"
    if fam.kind == "shared":
        disc = flag_discriminator(flag)
        return f"{fam.prefix}:{disc}" if disc else _default_key(flag)
    if fam.kind == "bundle":
        return f"{fam.prefix}:{flag.rule}"
    if fam.kind == "anchor":
        types = {d.id: d.doc_type for d in case.documents}
        for doc_id in flag.document_ids:
            if types.get(doc_id) in ("contract", "purchase_order"):
                return f"{fam.prefix}:{flag.rule}:{doc_id}"
        return _default_key(flag)
    # region
    if flag.region is None:
        return _default_key(flag)
    page_key = f"{fam.prefix}:{flag.region.file_id}:p{flag.region.page}"
    return f"{page_key}:{region_n or 1}"


def group_key(flag: Flag, case: Case) -> str:
    """Group key of one flag (section 4.1). Never raises: unknown document ids fall back to
    ``case``. A region flag judged alone is cluster 1 of its page; ``group_flags`` numbers the
    clusters across all flags."""
    try:
        return _key(flag, case, None)
    except Exception:  # defensive: grouping must never break scoring
        return _default_key(flag)


# --------------------------------------------------------------------------------- grouping


def _flag_order(flag: Flag) -> tuple[int, str, str, str]:
    return (SEVERITY_RANK[flag.severity], flag.id, flag.evidence, flag.explanation)


def _build_finding(key: str, members: list[Flag]) -> Finding:
    members = sorted(members, key=_flag_order)
    rep = members[0]
    rules = sorted({f.rule for f in members})
    # severity the checks produced, before any check catalogue override (``code_severity``)
    original = min(
        (f.code_severity or f.severity for f in members), key=lambda sev: SEVERITY_RANK[sev]
    )
    document_ids = _ids_sorted([d for f in members for d in f.document_ids])
    severity = rep.severity
    suffix = ""
    if key.startswith("pattern:") and len(members) > 1:
        suffix = f" (+{len(members) - 1} similar)"
    elif len(document_ids) > 1:
        suffix = f" (seen on {len(document_ids)} documents)"
    body = cap_words(sentence(rep.explanation), TITLE_MAX_CHARS - len(suffix))
    summary = " ".join(f"{rep.explanation} Evidence: {rep.evidence}".split())
    return Finding(
        id="fnd_0",  # replaced once the findings are ranked
        group_key=key,
        title=body + suffix,
        severity=severity,
        flag_ids=[f.id for f in members],
        document_ids=document_ids,
        rules=rules,
        weight=SCORE_WEIGHTS[severity],
        summary=cap_words(summary, SUMMARY_MAX_CHARS),
        # A catalogue override can raise a finding into a hard stop but never lower one out of it:
        # blocked at the original severity OR at the effective one (docs/scoring-and-queue.md).
        hard_stop=default_hard_stop(original, rules) or default_hard_stop(severity, rules),
    )


def group_flags(case: Case, flags: Sequence[Flag]) -> list[Finding]:
    """Group correlated flags into findings, ranked, ids ``fnd_1`` ... in rank order."""
    clusters = _region_clusters(flags)
    groups: dict[str, list[Flag]] = {}
    for i, flag in enumerate(flags):
        try:
            key = _key(flag, case, clusters.get(i))
        except Exception:  # defensive
            key = _default_key(flag)
        groups.setdefault(key, []).append(flag)

    findings = [_build_finding(key, members) for key, members in groups.items()]
    findings.sort(
        key=lambda f: (
            SEVERITY_RANK[f.severity],
            not f.hard_stop,
            -len(f.document_ids),
            -len(f.flag_ids),
            f.group_key,
        )
    )
    return [f.model_copy(update={"id": f"fnd_{n}"}) for n, f in enumerate(findings, start=1)]
