"""The claim schema and the evidence verifier (docs/semantic-signals.md, invariant 2).

A model never produces a flag. It produces *claims*, and each claim cites the extracted data it rests
on: a document id, a field name or a table row (and optionally a column), and the quoted value.
``verify_claims`` checks every citation against the documents the model was shown; a claim with any
citation that cannot be found is dropped and counted, and never becomes a flag. The model's words
are used only as the explanation text; the evidence line is rebuilt from the verified quotes.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.models import Cell, Document, clean_text
from app.semantic.policy import SEMANTIC_MAX_SEVERITY, cap_severity

STATEMENT_MAX = 240
QUOTE_MAX = 200
MIN_QUOTE_CHARS = 3  # a shorter quote only counts when it is the WHOLE cell text
MAX_CITATIONS = 4
MODEL_SEVERITIES = ("low", "medium", "high")  # "high" is accepted from the model and capped

# Reasons a claim is dropped (the keys of ``Verification.dropped``).
DROP_REASONS = (
    "bad_shape",
    "bad_kind",
    "no_citation",
    "empty_quote",
    "document_not_provided",
    "unknown_field",
    "unknown_table",
    "unknown_row",
    "unknown_column",
    "quote_not_found",
    "duplicate",
    "over_limit",
)

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))
_SPACES = re.compile(r"\s+")


def norm(text: str) -> str:
    """Comparison form of a quote or a cell: NFKC, no zero-width characters, case-folded, single
    spaces. Nothing else is forgiven: a quote must be the text, not a paraphrase of it."""
    folded = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH).casefold()
    return _SPACES.sub(" ", folded).strip()


# ---------------------------------------------------------------------------------- the schema


def citation_schema() -> dict[str, Any]:
    nullable_str = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "field": nullable_str,  # a field of the document, or null when citing a table row
            "table": nullable_str,
            "row": {"type": ["integer", "null"]},  # the row number printed in the prompt
            "column": nullable_str,  # optional: narrow a row citation to one column
            "quote": {"type": "string"},  # the value, copied exactly
        },
        "required": ["document_id", "quote"],
        "additionalProperties": False,
    }


def claims_json_schema(
    kinds: Sequence[str],
    *,
    extra_properties: Mapping[str, Any] | None = None,
    extra_required: Sequence[str] = (),
) -> dict[str, Any]:
    """JSON schema of a claims reply: ``{"claims": [{kind, severity, statement, citations, ...}]}``.

    ``kinds`` is the closed set of claim kinds a check accepts (a model cannot invent one).
    ``extra_properties`` adds check-specific claim attributes (for example a ``category`` label);
    they come back in ``VerifiedClaim.attrs`` and are *not* evidence: only citations are verified.
    """
    props: dict[str, Any] = {
        "kind": {"type": "string", "enum": list(kinds)},
        "severity": {"type": "string", "enum": list(MODEL_SEVERITIES)},
        "statement": {"type": "string"},
        "citations": {"type": "array", "items": citation_schema()},
    }
    props.update(extra_properties or {})
    claim = {
        "type": "object",
        "properties": props,
        "required": ["kind", "severity", "statement", "citations", *extra_required],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"claims": {"type": "array", "items": claim}},
        "required": ["claims"],
        "additionalProperties": False,
    }


CLAIM_FORMAT_RULES = (
    "Reply with a list of claims. A claim states one concrete concern and cites the data it rests "
    "on. Each citation names the document id and either a field name, or a table name with the row "
    "number (and optionally a column), and the quote: the cell text copied exactly as shown, never "
    "reworded. A claim without a citation, or whose quote is not in the cited cell, is discarded. "
    "Report only what the cited text itself shows. If there is no concern, return an empty list. "
    "Severity is low or medium (a model cannot raise anything higher)."
)


# ---------------------------------------------------------------------------- verified results


@dataclass(frozen=True)
class VerifiedCitation:
    document_id: str
    field: str | None
    table: str | None
    row: int | None
    column: str | None
    quote: str  # as the model wrote it (cleaned), verified present in ``cell_text``
    cell_text: str  # the extracted text it was found in
    cells: tuple[Cell, ...] = ()  # the cell(s) behind it, for the highlight region

    def locator(self) -> str:
        if self.field:
            return f"{self.document_id} {self.field}"
        col = f" {self.column}" if self.column else ""
        return f"{self.document_id} {self.table}[{self.row}]{col}"


@dataclass(frozen=True)
class VerifiedClaim:
    kind: str
    severity: str  # already capped
    statement: str  # the model's words: explanation text only, never evidence
    citations: tuple[VerifiedCitation, ...]
    attrs: Mapping[str, Any] = field(default_factory=dict)

    def evidence(self, limit: int = 600) -> str:
        """Evidence line rebuilt from the verified quotes: where it is and what the cell says."""
        parts = [f'{c.locator()}: "{clean_text(c.quote, QUOTE_MAX)}"' for c in self.citations]
        return clean_text("; ".join(parts), limit)


@dataclass
class Verification:
    """What survived the verifier, and what did not (never shown as flags, always counted)."""

    claims: list[VerifiedClaim] = field(default_factory=list)
    proposed: int = 0
    dropped: Counter[str] = field(default_factory=Counter)
    severity_capped: int = 0

    @property
    def n_dropped(self) -> int:
        return sum(self.dropped.values())

    def note(self) -> str:
        """One parseable line for ``CheckRun.reason`` (``parse_note`` reads it back)."""
        parts = [
            f"proposed={self.proposed}",
            f"kept={len(self.claims)}",
            f"dropped={self.n_dropped}",
        ]
        if self.severity_capped:
            parts.append(f"capped={self.severity_capped}")
        if self.dropped:
            parts.append("why=" + ",".join(f"{k}:{v}" for k, v in sorted(self.dropped.items())))
        return "claims " + " ".join(parts)

    def merge(self, other: Verification) -> None:
        self.claims += other.claims
        self.proposed += other.proposed
        self.dropped += other.dropped
        self.severity_capped += other.severity_capped


_NOTE_NUM = re.compile(r"\b(proposed|kept|dropped|capped)=(\d+)")


def parse_note(note: str | None) -> dict[str, int]:
    """Counts from a ``Verification.note()`` line ({} for anything else)."""
    return {k: int(v) for k, v in _NOTE_NUM.findall(note or "")}


# ------------------------------------------------------------------------------- the verifier


def _cell_texts(cell: Cell) -> list[str]:
    return [t for t in (cell.value, cell.raw) if t]


def _matches(quote_n: str, texts: Iterable[str]) -> str | None:
    """The first text (normalised) that holds the quote, else None."""
    for text in texts:
        text_n = norm(text)
        if not text_n:
            continue
        if quote_n == text_n or (len(quote_n) >= MIN_QUOTE_CHARS and quote_n in text_n):
            return text
    return None


def _verify_citation(
    raw: object, docs: Mapping[str, Document]
) -> tuple[VerifiedCitation | None, str | None]:
    """(citation, None) when it holds, else (None, drop reason)."""
    if not isinstance(raw, dict):
        return None, "bad_shape"
    doc_id = raw.get("document_id")
    quote = raw.get("quote")
    if not isinstance(doc_id, str) or not isinstance(quote, str):
        return None, "bad_shape"
    quote_n = norm(quote)
    if not quote_n:
        return None, "empty_quote"
    doc = docs.get(doc_id)
    if doc is None:
        return None, "document_not_provided"
    field_name, table_name = raw.get("field"), raw.get("table")
    row_no, column = raw.get("row"), raw.get("column")
    quote_clean = clean_text(quote, QUOTE_MAX)

    if isinstance(field_name, str) and field_name and table_name in (None, ""):
        cell = doc.fields.get(field_name)
        if cell is None:
            return None, "unknown_field"
        hit = _matches(quote_n, _cell_texts(cell))
        if hit is None:
            return None, "quote_not_found"
        return (
            VerifiedCitation(doc_id, field_name, None, None, None, quote_clean, hit, (cell,)),
            None,
        )

    if isinstance(table_name, str) and table_name and isinstance(row_no, int):
        table = doc.tables.get(table_name)
        if table is None:
            return None, "unknown_table"
        row = next((r for r in table.rows if r.index == row_no), None)
        if row is None:
            return None, "unknown_row"
        if isinstance(column, str) and column:
            cell = row.cells.get(column)
            if cell is None:
                return None, "unknown_column"
            chosen = [cell]
        else:
            column = None
            chosen = list(row.cells.values())
        for cell in chosen:
            hit = _matches(quote_n, _cell_texts(cell))
            if hit is not None:
                return (
                    VerifiedCitation(
                        doc_id, None, table_name, row_no, column, quote_clean, hit, (cell,)
                    ),
                    None,
                )
        return None, "quote_not_found"
    return None, "bad_shape"


def verify_claims(
    reply: Mapping[str, Any],
    docs: Sequence[Document],
    *,
    kinds: Sequence[str],
    max_claims: int = 8,
    extra_keys: Sequence[str] = (),
) -> Verification:
    """Verify a claims reply against the documents the model was shown (``docs``; a citation of any
    other document is dropped). Never raises for model output: a malformed claim is a drop. The
    caller has already schema-validated the reply (``claims_json_schema``); this is defence in
    depth, so a hand-built reply is checked too."""
    by_id = {d.id: d for d in docs}
    out = Verification()
    raw_claims = reply.get("claims")
    if not isinstance(raw_claims, list):
        out.dropped["bad_shape"] += 1
        return out
    seen: set[tuple[object, ...]] = set()
    allowed_kinds = set(kinds)
    for raw in raw_claims:
        out.proposed += 1
        if not isinstance(raw, dict):
            out.dropped["bad_shape"] += 1
            continue
        kind, statement, severity = raw.get("kind"), raw.get("statement"), raw.get("severity")
        if not isinstance(kind, str) or not isinstance(statement, str) or not statement.strip():
            out.dropped["bad_shape"] += 1
            continue
        if kind not in allowed_kinds:
            out.dropped["bad_kind"] += 1
            continue
        citations_raw = raw.get("citations")
        if not isinstance(citations_raw, list) or not citations_raw:
            out.dropped["no_citation"] += 1
            continue
        verified: list[VerifiedCitation] = []
        failure: str | None = None
        for c in citations_raw[:MAX_CITATIONS]:
            vc, reason = _verify_citation(c, by_id)
            if vc is None:
                failure = reason or "bad_shape"
                break
            verified.append(vc)
        if failure is not None:
            out.dropped[failure] += 1
            continue
        key = (
            kind,
            "|".join(sorted(norm(str(raw.get(k))) for k in extra_keys)),
            tuple(sorted((c.locator(), norm(c.quote)) for c in verified)),
        )
        if key in seen:
            out.dropped["duplicate"] += 1
            continue
        seen.add(key)
        if len(out.claims) >= max_claims:
            out.dropped["over_limit"] += 1
            continue
        sev = severity if isinstance(severity, str) else "low"
        capped = cap_severity(sev if sev in MODEL_SEVERITIES else "low")
        if capped != sev and sev in MODEL_SEVERITIES:
            out.severity_capped += 1
        attrs = {k: raw[k] for k in extra_keys if k in raw}
        out.claims.append(
            VerifiedClaim(
                kind=kind,
                severity=capped,
                statement=clean_text(statement, STATEMENT_MAX),
                citations=tuple(verified),
                attrs=attrs,
            )
        )
    return out


__all__ = [
    "CLAIM_FORMAT_RULES",
    "DROP_REASONS",
    "SEMANTIC_MAX_SEVERITY",
    "VerifiedCitation",
    "VerifiedClaim",
    "Verification",
    "claims_json_schema",
    "norm",
    "parse_note",
    "verify_claims",
]
