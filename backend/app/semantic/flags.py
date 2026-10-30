"""A verified claim becomes a Flag here, and nowhere else (docs/semantic-signals.md, invariant 3)."""

from __future__ import annotations

from collections.abc import Sequence

from app.checks.registry import make_flag
from app.core.models import Document, Flag, Severity, clean_text
from app.names import union_region
from app.semantic.claims import VerifiedClaim
from app.semantic.policy import cap_severity

MODEL_DERIVED = "Model-derived signal, quotes verified against the extracted text: "


def make_semantic_flag(
    rule: str,
    claim: VerifiedClaim,
    docs: Sequence[Document],
    *,
    discriminator: str | None = None,
    severity: Severity | None = None,
) -> Flag:
    """Flag for one verified claim. Severity is the claim's (already capped) severity, or
    ``severity`` when the check wants a lower fixed one; it is capped at medium again here.
    ``evidence`` is rebuilt from the verified quotes; the model's sentence is only the explanation,
    prefixed so a reviewer can see where it came from. ``docs`` are the documents the model was shown
    (a re-assembled statement is resolved to its stored pages by ``make_flag``)."""
    by_id = {d.id: d for d in docs}
    cited = [by_id[c.document_id] for c in claim.citations if c.document_id in by_id]
    first = claim.citations[0]
    cells = [cell for c in claim.citations for cell in c.cells]
    region = union_region([cell.box for cell in cells])
    return make_flag(
        rule,
        docs=cited,
        evidence=claim.evidence(),
        explanation=clean_text(MODEL_DERIVED + claim.statement, 300),
        table=first.table,
        field=first.field,
        row=first.row,
        row_doc=by_id.get(first.document_id) if first.row is not None else None,
        region=region,
        severity=cap_severity(severity or claim.severity),  # type: ignore[arg-type]
        discriminator=discriminator or claim.kind,
    )
