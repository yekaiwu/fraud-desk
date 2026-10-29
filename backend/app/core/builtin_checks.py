"""Info checks emitted from pipeline warnings (docs/architecture.md section 4).

``doc_unreadable``: a page or document could not be read (plan 8.5).
``doc_unclassified``: a document is ``other``/``boilerplate`` or has a low classifier probability
(plan 5.3 guardrail). Both carry weight 0; their job is to make silence visible and to reduce
coverage (pages count as unchecked in ``score.py``).
"""

from __future__ import annotations

from app.checks.registry import check, make_flag
from app.core.context import CaseContext
from app.core.models import Document, Flag, Region

UNREADABLE_PREFIX = "unreadable:"


def _whole_page(file_id: str, page: int, label: str) -> Region:
    return Region(file_id=file_id, page=page, x=0.0, y=0.0, w=1.0, h=1.0, label=label)


def _doc_for_page(ctx: CaseContext, file_id: str, page: int) -> Document | None:
    for d in ctx.docs():
        if d.file_id == file_id and d.page_start <= page <= d.page_end:
            return d
    return None


@check(
    rule="doc_unreadable",
    applies_to={"*"},
    severity="info",
    description="A page or document could not be read, so the checks that need it did not run.",
    tamper=None,
)
def doc_unreadable(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    per_doc: dict[str, list[str]] = {}
    orphan_pages: list[tuple[str, int, str]] = []

    for f in ctx.case.files:
        for p in f.pages:
            if p.error:
                doc = _doc_for_page(ctx, f.id, p.page)
                note = f"{f.id} page {p.page}: {p.error}"
                if doc is not None:
                    per_doc.setdefault(doc.id, []).append(note)
                else:
                    orphan_pages.append((f.id, p.page, p.error))

    for d in ctx.docs():
        notes = per_doc.setdefault(d.id, [])
        if d.status == "failed":
            notes.append(f"document failed: {d.error or 'no reason recorded'}")
        notes.extend(w for w in d.warnings if w.startswith(UNREADABLE_PREFIX))

    docs_by_id = {d.id: d for d in ctx.docs()}
    for doc_id in sorted(per_doc, key=lambda i: (len(i), i)):
        notes = per_doc[doc_id]
        if not notes:
            continue
        doc = docs_by_id[doc_id]
        flags.append(
            make_flag(
                "doc_unreadable",
                docs=[doc],
                evidence="; ".join(dict.fromkeys(notes)),
                explanation="Part of this document could not be read, so checks on it are incomplete.",
                region=_whole_page(doc.file_id, doc.page_start, "unreadable"),
            )
        )
    for file_id, page, error in orphan_pages:
        flags.append(
            make_flag(
                "doc_unreadable",
                docs=[],
                evidence=f"{file_id} page {page}: {error}",
                explanation="A page in the upload could not be read and belongs to no document.",
                region=_whole_page(file_id, page, "unreadable"),
                discriminator=f"{file_id}p{page}",
            )
        )
    return flags


@check(
    rule="doc_unclassified",
    applies_to={"*"},
    severity="info",
    description="A document is unclassified, labelled other or boilerplate, or low confidence.",
    tamper=None,
)
def doc_unclassified(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    min_prob = ctx.settings.classifier_min_prob
    for d in ctx.docs():
        reasons: list[str] = []
        if d.doc_type in ("other", "boilerplate"):
            reasons.append(f"classified as {d.doc_type}")
        if d.classification.probability < min_prob:
            reasons.append(
                f"classifier probability {d.classification.probability:.2f} is below {min_prob:.2f}"
            )
        if d.doc_type != "boilerplate":
            try:
                pages = ctx.file(d.file_id).pages
            except KeyError:
                pages = []
            bp = [
                p.page
                for p in pages
                if d.page_start <= p.page <= d.page_end
                and p.classification is not None
                and p.classification.label == "boilerplate"
            ]
            if bp:
                reasons.append("boilerplate page(s) " + ", ".join(str(n) for n in bp))
        if not reasons:
            continue
        flags.append(
            make_flag(
                "doc_unclassified",
                docs=[d],
                evidence="; ".join(reasons),
                explanation="This document was not confidently recognised, so it was not fully checked.",
                region=_whole_page(d.file_id, d.page_start, "unclassified"),
            )
        )
    return flags
