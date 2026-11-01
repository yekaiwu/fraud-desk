"""The challenger: argue the innocent reading of each judgement flag.

Asymmetric on purpose. The challenger may only *lower* a flag, and only when it quotes a passage of
the case's own documents (checked word for word against the text it was shown) that explains the
flagged difference. A verdict without a verified quote, an unknown flag id, a malformed reply or a
failed call all leave the flag exactly as it was. A lowered flag becomes ``info`` with the reason in
front of its explanation, so the reviewer still sees it and the trail is complete.
"""

from __future__ import annotations

from typing import Any

from app.agents.call import AgentFailed, ask
from app.agents.roster import is_arguable
from app.core.context import CaseContext
from app.core.llm import wrap_untrusted
from app.core.models import AgentVerdict, Document, Flag, clean_text
from app.semantic.render import render_document

NAME = "challenger"
NEVER = frozenset({"inv_duplicate", "file_reuse", "bs_pass_through"})
QUOTE_MIN_CHARS = 5
ROWS_CITED = 60
NOTE = "Challenged by the review team: "

TASK = """You are the challenger on a fraud-review team at a bank. Other reviewers raised flags on a \
customer's documents. Your only job is to argue the innocent side: for each flag, decide whether the \
documents themselves contain a harmless explanation for what was flagged.

Answer "doubtful" only when you can copy, word for word, a passage from one of the documents that \
explains the flagged difference as harmless. Typical harmless explanations: the flagged text is a \
misreading of a scan, and another place in the case shows the same name, number or identifier printed \
correctly; two spellings, abbreviations or word orders that other documents show to be the same \
person or company; the same amount written in a different form.

Answer "upheld" in every other case: when the flag merely seems unlikely, when you have no passage to \
quote, when a name or figure differs and no document explains it, and when the explanation rests on \
something the customer simply declared. Do not excuse figures that do not add up, missing documents, \
or amounts that differ. When in doubt, uphold.

Give one verdict per flag id. For "doubtful" give the document_id and the quote copied exactly from \
that document's text, and a one-sentence reason in your own words."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "flag_id": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["upheld", "doubtful"]},
                    "reason": {"type": "string"},
                    "document_id": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["flag_id", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def is_candidate(f: Flag) -> bool:
    return (
        f.stage in ("rules", "crossdoc")
        and f.severity in ("medium", "high")
        and f.rule not in NEVER
        and is_arguable(f.rule)
    )


def candidates_among(flags: list[Flag]) -> list[Flag]:
    return [f for f in flags if is_candidate(f)]


def candidates(ctx: CaseContext) -> list[Flag]:
    return candidates_among(ctx.case.flags)


def _fold(text: str) -> str:
    return " ".join(text.casefold().split())


def _render_all(ctx: CaseContext, flags: list[Flag]) -> dict[str, str]:
    """Every document as the model sees it: rows in full only for documents a flag names."""
    cited = {d for f in flags for d in f.document_ids}
    out: dict[str, str] = {}
    for doc in ctx.docs():
        tables = None if doc.id in cited else []
        out[doc.id] = render_document(doc, tables=tables, max_rows=ROWS_CITED)
    return out


def _flags_text(flags: list[Flag], alias: dict[str, str]) -> str:
    lines = []
    for f in flags:
        docs = ",".join(f.document_ids)
        lines.append(
            f"- flag_id={alias[f.id]} rule={f.rule} severity={f.severity} documents={docs}\n"
            f"  explanation: {clean_text(f.explanation, 300)}\n"
            f"  evidence: {clean_text(f.evidence, 300)}"
        )
    return "\n".join(lines)


def _doc_for(ctx: CaseContext, document_id: str) -> Document | None:
    return next((d for d in ctx.case.documents if d.id == document_id), None)


def challenge(ctx: CaseContext, flags: list[Flag]) -> list[AgentVerdict]:
    """One model call for all ``flags``; returns a verdict for each (kept unless a quote verified).
    Raises ``AgentFailed``."""
    rendered = _render_all(ctx, flags)
    try:
        alias = {f.id: f"F{n}" for n, f in enumerate(flags, start=1)}
        flag_block = wrap_untrusted(_flags_text(flags, alias), label="flags")
        doc_blocks = "\n\n".join(wrap_untrusted(t, label=i) for i, t in rendered.items())
    except ValueError:
        raise AgentFailed("document text could not be wrapped safely") from None
    user = f"Flags to rule on:\n{flag_block}\n\nDocuments:\n{doc_blocks}"
    reply = ask(ctx, name=NAME, task=TASK, user=user, schema_name="Verdicts", json_schema=SCHEMA)
    by_alias = {a: f for f in flags for a in [alias[f.id]]}
    folded = {i: _fold(t) for i, t in rendered.items()}
    seen: dict[str, AgentVerdict] = {}
    for item in reply.get("verdicts", []):
        target = by_alias.get(str(item.get("flag_id", "")).strip())
        if target is None or target.id in seen:
            continue
        fid = target.id
        reason = clean_text(str(item.get("reason", "")), 240)
        verdict = "doubtful" if item.get("verdict") == "doubtful" else "upheld"
        doc_id = str(item.get("document_id") or "") or None
        quote = clean_text(str(item.get("quote") or ""), 200) or None
        verified = (
            verdict == "doubtful"
            and doc_id in folded
            and quote is not None
            and len(quote) >= QUOTE_MIN_CHARS
            and _fold(quote) in folded[doc_id]
        )
        seen[fid] = AgentVerdict(
            flag_id=fid,
            verdict=verdict,
            action="lowered" if verified else "kept",
            reason=reason,
            document_id=doc_id if verified else None,
            quote=quote if verified else None,
        )
    for f in flags:  # a flag the model did not rule on is kept
        seen.setdefault(
            f.id,
            AgentVerdict(flag_id=f.id, verdict="upheld", action="kept", reason="No ruling given."),
        )
    return [seen[f.id] for f in flags]


def apply(ctx: CaseContext, verdicts: list[AgentVerdict]) -> int:
    """Lower the flags with a verified explanation; returns how many."""
    lowered = {v.flag_id: v for v in verdicts if v.action == "lowered"}
    for i, f in enumerate(ctx.case.flags):
        v = lowered.get(f.id)
        if v is not None:
            ctx.case.flags[i] = f.model_copy(
                update={"severity": "info", "explanation": NOTE + v.reason + " " + f.explanation}
            )
    return len(lowered)
