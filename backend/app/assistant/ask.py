"""Answer one question about a case. Read-only: flags, scores, lanes and decisions are not written.

The model sees extracted cells, wrapped as untrusted text, and must quote them. ``_verify_citation``
drops any quote that is not in the cited cell. A sentence with nothing left is not shown. A question
that asks for a verdict gets a fixed hand-back and cited facts only.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.assistant import context as context_mod
from app.assistant import memory
from app.assistant import worklist as worklist_mod
from app.assistant.prompts import (
    DECISION_PREAMBLE,
    NOT_FOUND,
    PROMPT_SHA256,
    PURPOSE,
    SCHEMA_NAME,
    SERVICE,
    SYSTEM,
    reply_schema,
)
from app.core.config import Settings
from app.core.costs import BudgetLimits, CostMeter, DailySpend, LLMBudgetExceeded, PriceTable, usd6
from app.core.db import Database
from app.core.errors import ApiError, LLMError, LLMSchemaError
from app.core.llm import LLMClient, LLMRequest, wrap_untrusted
from app.core.models import Case, Document, Flag, clean_text
from app.core.privacy import PrivateModeViolation
from app.semantic.claims import MAX_CITATIONS, _verify_citation, norm
from app.semantic.render import shown

SENTENCE_MAX = 4
TEXT_MAX = 400
FLAG_LINKS_MAX = 6
# A piece of a flag, the review team or the service note must be at least this long and start and end
# on a word boundary. Three letters ("the") appear in any prose and would let any sentence through.
PROSE_QUOTE_MIN = 12
# The worklist values a quote may copy (docs/case-assistant.md). Not the labels around them.
WORKLIST_FIELDS = ("reference", "applicant", "score", "band", "lane")

_DECISION = re.compile(
    r"("
    r"\b(?:should|shall|do|can|would|could)\s+(?:i|we)\b.{0,80}"
    r"\b(?:approv|reject|escalat|clear|accept|declin)"
    r"|\b(?:should|shall)\b.{0,40}\b(?:be\s+)?(?:approv|reject|escalat|clear)"
    r"|\bis\s+(?:this|it|that)(?:\s+(?:case|application|applicant|company))?\s+(?:a\s+)?fraud"
    r"|\b(?:approve|reject|escalate)\s+this\b"
    r"|\byour (?:verdict|decision|recommendation)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_VERDICT_WORD = re.compile(
    r"\b(?:approve|approved|approving|approval|reject|rejected|rejecting|rejection|"
    r"escalate|escalated|escalating|escalation|clear|cleared|clearing|clearance)\b",
    re.IGNORECASE,
)

# A recommendation of a verdict, whatever the question was. The reply never decides.
_RECOMMENDATION = re.compile(
    r"\b(?:should|must|ought\s+to|recommend\w*|advisable|best\s+to|safe\s+to"
    r"|(?:i|we)\s+(?:would|suggest|advise))\b[^.;]{0,60}?\b(?:approv|reject|escalat|declin)\w*"
    r"|\b(?:approve|reject|escalate|decline|clear)\s+(?:the|this)\s+(?:case|application|applicant)\b",
    re.IGNORECASE,
)


def _names_rule(text: str, ids: set[str]) -> bool:
    """True when the sentence names a rule id or flag id from this case (``x_remitter_vs_buyer``)."""
    lowered = text.lower()
    for rid in ids:
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(rid) + r"(?![A-Za-z0-9_])", lowered):
            return True
    return False


# A background sentence is general knowledge with no citation. It may not talk about this case.
_CASE_TALK = re.compile(
    r"\b(?:this|your)\s+case\b|\bthe\s+case\b(?!\s+of)|\b(?:this|your|the)\s+(?:applicant|worklist)\b"
    r"|\b(?:this|your)\s+(?:company|customer|client|document|file|page|statement|invoice|receipt"
    r"|flag|finding|bundle|upload|scan|photo|id|card|passport|contract)\b"
    r"|\bon\s+(?:the\s+)?(?:screen|worklist)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
CASE_NUMBER_MIN_DIGITS = 3
CASE_PHRASE_MIN = 4


def _digits(token: str) -> str:
    return token.replace(",", "").rstrip(".")


def case_values(
    case: Case, docs: Sequence[Document], work: Sequence[Mapping[str, Any]]
) -> tuple[set[str], set[str]]:
    """(phrases, numbers) the model was shown on this case: cell values, flag text, the worklist.

    A background sentence that repeats one of them is a case fact with no citation, so it is dropped.
    """
    texts: list[str] = []
    for doc in docs:
        for cell in doc.fields.values():
            texts.append(shown(cell))
        for table in doc.tables.values():
            for row in table.rows:
                texts.extend(shown(cell) for cell in row.cells.values())
    for flag in case.flags:
        texts.extend([flag.evidence or "", flag.explanation or ""])
    phrases: set[str] = set()
    for text in texts:
        text_n = norm(text)
        flat = re.sub(r"[^a-z0-9]+", " ", text_n).strip()
        # A value that is a name, an id or an amount. A lone common word ("payment") is not one.
        specific = any(ch.isdigit() for ch in flat) or (len(flat.split()) >= 2 and len(flat) >= 6)
        if specific and CASE_PHRASE_MIN <= len(text_n) <= 80:
            phrases.add(text_n)
    for item in work:
        if item.get("case_id") in ("service", "review-team"):
            continue
        for key in ("reference", "applicant"):
            value = norm(str(item.get(key) or ""))
            if len(value) >= CASE_PHRASE_MIN:
                phrases.add(value)
        texts.append(str(item.get("top_finding") or ""))
    numbers: set[str] = set()
    for text in texts:
        for token in _NUMBER.findall(text):
            d = _digits(token)
            if sum(ch.isdigit() for ch in d) >= CASE_NUMBER_MIN_DIGITS:
                numbers.add(d)
    # Flag explanations are the check's own template words, not a case value. Only a short value
    # (a name, an id, an amount) counts as a phrase.
    phrases -= {norm(f.explanation or "") for f in case.flags}
    phrases -= {norm(f.evidence or "") for f in case.flags if len(norm(f.evidence or "")) > 40}
    return phrases, numbers


def _talks_about_case(text: str, values: tuple[set[str], set[str]]) -> bool:
    """True when a background sentence refers to this case or repeats something shown on it."""
    if _CASE_TALK.search(text):
        return True
    phrases, numbers = values
    text_n = norm(text)
    padded = f" {re.sub(r'[^a-z0-9]+', ' ', text_n)} "
    for phrase in phrases:
        flat = re.sub(r"[^a-z0-9]+", " ", phrase).strip()
        if len(flat) >= CASE_PHRASE_MIN and f" {flat} " in padded:
            return True
    for token in _NUMBER.findall(text):
        d = _digits(token)
        if sum(ch.isdigit() for ch in d) >= CASE_NUMBER_MIN_DIGITS and d in numbers:
            return True
    return False


def _relays_officer(text: str, kept: Sequence[Mapping[str, Any]], officer: Sequence[str]) -> bool:
    """True when every recommendation in the sentence is the case officer's recorded words.

    "The case officer's next step is to escalate the case to compliance" relays a next step, cited
    from the review team. The sentence must name the case officer, and each recommending phrase
    must sit inside a cited quote that is itself the officer's summary or a next step.
    """
    if "case officer" not in norm(text):
        return False
    officer_n = [norm(t) for t in officer]
    quotes = [
        norm(str(c.get("quote") or ""))
        for c in kept
        if c.get("worklist_id") == "review-team"
        and any(norm(str(c.get("quote") or "")) in t for t in officer_n)
    ]
    quotes = [q for q in quotes if q]
    if not quotes:
        return False
    return all(any(norm(m.group(0)) in q for q in quotes) for m in _RECOMMENDATION.finditer(text))


def is_decision_question(question: str) -> bool:
    """True when the officer is asking the tool to decide. The reply is then fixed."""
    return _DECISION.search(question) is not None


def _blank(value: object) -> bool:
    return value is None or value == ""


def _region(cell_box: Any) -> dict[str, Any] | None:
    if cell_box is None:
        return None
    return {
        "file_id": cell_box.file_id,
        "page": cell_box.page,
        "x": cell_box.x,
        "y": cell_box.y,
        "w": cell_box.w,
        "h": cell_box.h,
    }


def _candidates(file_id: str, page: int, docs: Sequence[Document]) -> list[Document]:
    return [d for d in docs if d.file_id == file_id and d.page_start <= page <= d.page_end]


def _as_verifier_citation(raw: Mapping[str, Any], doc: Document) -> dict[str, Any] | None:
    """The model's file/page citation as the shape ``_verify_citation`` checks, or None."""
    quote = raw.get("quote")
    field = raw.get("field")
    table = raw.get("table")
    row = raw.get("row")
    base: dict[str, Any] = {"document_id": doc.id, "quote": quote}
    if isinstance(field, str) and field and _blank(table):
        base["field"] = field
        return base
    if isinstance(row, int) and not isinstance(row, bool):
        if isinstance(table, str) and table:
            base["table"] = table
            base["row"] = row
            return base
        names = [name for name, t in doc.tables.items() if any(r.index == row for r in t.rows)]
        if len(names) == 1:
            base["table"] = names[0]
            base["row"] = row
            return base
        return None
    return None


def _flag_quote(quote: str, flag: Flag) -> bool:
    """True when the quote is in the evidence or explanation as the model was shown it (trimmed)."""
    shown_texts = context_mod.flag_texts(flag)
    return any(_quote_in(quote, text) for text in shown_texts if text)


def _quote_in(quote: str, text: str) -> bool:
    """True when the quote is the whole text, or a piece of at least PROSE_QUOTE_MIN characters
    that starts and ends on a word boundary."""
    quote_n = norm(quote)
    text_n = norm(text)
    if not quote_n or not text_n:
        return False
    if quote_n == text_n:
        return True
    if len(quote_n) < PROSE_QUOTE_MIN:
        return False
    start = text_n.find(quote_n)
    while start != -1:
        end = start + len(quote_n)
        before = start == 0 or not text_n[start - 1].isalnum()
        after = end == len(text_n) or not text_n[end].isalnum()
        if before and after:
            return True
        start = text_n.find(quote_n, start + 1)
    return False


def _worklist_quote(quote: str, item: Mapping[str, Any]) -> bool:
    """A worklist line: a whole reference, applicant, score, band or lane, or a piece of the top
    finding. The service note and the review team are prose and use the prose rule."""
    if "top_finding" not in item:
        return _quote_in(quote, str(item["text"]))
    quote_n = norm(quote)
    if not quote_n:
        return False
    for key in WORKLIST_FIELDS:
        value = item.get(key)
        if value is None or value == "":
            continue
        if quote_n in (norm(str(value)), norm(f"{key} {value}")):
            return True
    top = str(item.get("top_finding") or "")
    return bool(top) and _quote_in(quote, top)


def _check_worklist(
    raw: Mapping[str, Any], items: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any] | None, str | None]:
    """A citation of one worklist line. The quote must be in that case's block."""
    case_id = raw.get("worklist_id")
    quote = raw.get("quote")
    if not isinstance(case_id, str) or not case_id or not isinstance(quote, str):
        return None, "bad_shape"
    item = next((i for i in items if i["case_id"] == case_id), None)
    if item is None:
        return None, "unknown_case"
    if not _worklist_quote(quote, item):
        return None, "quote_not_found"
    return (
        {
            "file_id": "",
            "page": 1,
            "document_id": "",
            "doc_type": "",
            "field": None,
            "table": None,
            "row": None,
            "flag_id": None,
            "rule": None,
            "worklist_id": case_id,
            "reference": item["reference"] or case_id,
            "quote": clean_text(quote, 200),
            "region": None,
        },
        None,
    )


def _check_flag(
    raw: Mapping[str, Any], flags: Sequence[Flag], case: Case
) -> tuple[dict[str, Any] | None, str | None]:
    """A citation of a flag the model was shown. The quote must be in its evidence or explanation."""
    flag_id = raw.get("flag_id")
    quote = raw.get("quote")
    if not isinstance(flag_id, str) or not flag_id or not isinstance(quote, str):
        return None, "bad_shape"
    flag = next((f for f in flags if f.id == flag_id), None)
    if flag is None:
        return None, "unknown_flag"
    if not _flag_quote(quote, flag):
        return None, "quote_not_found"
    file_id, page, doc_id = context_mod.flag_anchor(flag, case)
    if not file_id:
        return None, "document_not_provided"
    doc = next((d for d in case.documents if d.id == doc_id), None)
    return (
        {
            "file_id": file_id,
            "page": page,
            "document_id": doc_id,
            "doc_type": doc.doc_type if doc else "",
            "field": None,
            "table": None,
            "row": None,
            "flag_id": flag.id,
            "rule": flag.rule,
            "quote": clean_text(quote, 200),
            "region": _region(flag.region),
        },
        None,
    )


def _check_citation(
    raw: object,
    docs: Sequence[Document],
    flags: Sequence[Flag],
    case: Case,
    work: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    """One citation against the worklist, the flags, and the documents the model was shown."""
    if not isinstance(raw, dict):
        return None, "bad_shape"
    worklist_id = raw.get("worklist_id")
    if isinstance(worklist_id, str) and worklist_id:
        return _check_worklist(raw, work)
    flag_id = raw.get("flag_id")
    if isinstance(flag_id, str) and flag_id:
        return _check_flag(raw, flags, case)
    file_id, page, quote = raw.get("file_id"), raw.get("page"), raw.get("quote")
    if (
        not isinstance(file_id, str)
        or not file_id
        or not isinstance(page, int)
        or isinstance(page, bool)
    ):
        return None, "bad_shape"
    if not isinstance(quote, str):
        return None, "bad_shape"
    found = _candidates(file_id, page, docs)
    if not found:
        return None, "document_not_provided"
    shaped = None
    for doc in found:
        shaped = _as_verifier_citation(raw, doc)
        if shaped is not None:
            break
    if shaped is None:
        return None, "bad_shape"
    last_reason = "bad_shape"
    for doc in found:
        attempt = dict(shaped)
        attempt["document_id"] = doc.id
        verified, reason = _verify_citation(attempt, {doc.id: doc})
        if verified is None:
            last_reason = reason or "bad_shape"
            continue
        if not context_mod.was_shown(
            doc, field=verified.field, table=verified.table, row=verified.row
        ):
            # A row past MAX_ROWS, or a cell cut off by the block limit, was never in the prompt.
            last_reason = "not_shown"
            continue
        cell = verified.cells[0] if verified.cells else None
        box = cell.box if cell is not None else None
        if box is not None and (box.page != page or box.file_id != file_id):
            last_reason = "page_mismatch"
            continue
        return (
            {
                "file_id": file_id,
                "page": page,
                "document_id": doc.id,
                "doc_type": doc.doc_type,
                "field": verified.field,
                "table": verified.table,
                "row": verified.row,
                "quote": verified.quote,
                "region": _region(box),
            },
            None,
        )
    return None, last_reason


def _open_flags(case: Case, document_ids: set[str]) -> list[dict[str, Any]]:
    """Open flags the reply can link to. Cited documents first, then any other open flag."""
    open_flags = [f for f in case.flags if f.disposition == "open"]

    def view(flag: Flag) -> dict[str, Any]:
        return {
            "id": flag.id,
            "rule": flag.rule,
            "severity": flag.severity,
            "explanation": clean_text(flag.explanation, TEXT_MAX),
        }

    cited = [f for f in open_flags if document_ids.intersection(f.document_ids)]
    rest = [f for f in open_flags if f not in cited and f.severity != "info"]
    return [view(f) for f in (cited or rest)[:FLAG_LINKS_MAX]]


def verify_reply(
    reply: Mapping[str, Any],
    docs: Sequence[Document],
    flags: Sequence[Flag],
    case: Case,
    work: Sequence[Mapping[str, Any]],
    *,
    decision: bool,
) -> dict[str, Any]:
    """Keep only sentences that cite a real cell. Verdict wording is dropped on a decision question."""
    # The model can also hand the question back. Either way, a verdict word is not shown.
    decision = decision or reply.get("status") == "decision_request"
    dropped: Counter[str] = Counter()
    sentences: list[dict[str, Any]] = []
    raw_sentences = reply.get("sentences")
    if not isinstance(raw_sentences, list):
        dropped["bad_shape"] += 1
        raw_sentences = []
    citations_kept = 0
    citations_dropped = 0
    ids = context_mod.rule_ids(case)
    officer = context_mod.officer_texts(case)
    values = case_values(case, docs, work)
    for raw in raw_sentences:
        if len(sentences) >= SENTENCE_MAX:
            dropped["over_limit"] += 1
            continue
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("text"), str)
            or not raw["text"].strip()
        ):
            dropped["bad_shape"] += 1
            continue
        text = clean_text(raw["text"], TEXT_MAX)
        if decision and _VERDICT_WORD.search(text):
            dropped["verdict_language"] += 1
            continue
        if _names_rule(text, ids):
            # The screen shows prose. A rule id is a label for the check, not words for a person.
            dropped["rule_id"] += 1
            continue
        cites_raw = raw.get("citations")
        cites_list: list[Any] = cites_raw if isinstance(cites_raw, list) else []
        no_cites = not cites_list
        if no_cites and raw.get("background") is True:
            # General knowledge, labelled as such. It carries no case fact, no verdict and no rule id.
            if decision:
                dropped["background_on_decision"] += 1
            elif _RECOMMENDATION.search(text):
                dropped["verdict_language"] += 1
            elif _talks_about_case(text, values):
                dropped["case_fact_in_background"] += 1
            else:
                sentences.append({"text": text, "citations": [], "background": True})
            continue
        if no_cites:
            dropped["no_citation"] += 1
            continue
        kept: list[dict[str, Any]] = []
        failed: list[str] = []
        for item in cites_list[:MAX_CITATIONS]:
            citation, reason = _check_citation(item, docs, flags, case, work)
            if citation is None:
                failed.append(reason or "bad_shape")
                continue
            kept.append(citation)
        if _RECOMMENDATION.search(text) and not _relays_officer(text, kept, officer):
            # A verdict recommended in the assistant's own voice. Relaying the case officer's
            # recorded next step, cited, is allowed.
            dropped["verdict_language"] += 1
            continue
        citations_dropped += len(failed)
        for reason in failed:
            dropped[reason] += 1
        if not kept:
            dropped["no_citation"] += 1
            continue
        citations_kept += len(kept)
        sentences.append({"text": text, "citations": kept})
    model_status = reply.get("status")
    cited_sentences = [s for s in sentences if not s.get("background")]
    if decision or model_status == "decision_request":
        status = "decision_request"
    elif cited_sentences:
        status = "answered"
    elif sentences:
        status = "general"
    else:
        status = "not_found"
    cited_docs = {
        c["document_id"] for s in sentences for c in s["citations"] if c.get("document_id")
    }
    return {
        "status": status,
        "sentences": sentences,
        "citations_kept": citations_kept,
        "citations_dropped": citations_dropped,
        "dropped": dict(dropped),
        "cited_docs": cited_docs,
    }


def _from_challenger(case: Case, question: str) -> dict[str, Any] | None:
    """A plain answer when the officer asks what the challenger lowered and the model abstained.

    Built only from the challenger's recorded verdicts. It names the reason, not a rule id.
    """
    q = norm(question)
    if "challenger" not in q and "review team" not in q:
        return None
    if not any(
        word in q for word in ("reduc", "lower", "kept", "keep", "change", "what did", "which")
    ):
        return None
    report = next((a for a in case.agents if a.agent == "challenger"), None)
    if report is None or report.status != "ok":
        return None
    lowered = [v for v in report.verdicts if v.action == "lowered"]
    kept = [v for v in report.verdicts if v.action == "kept"]
    sentences: list[dict[str, Any]] = []
    if not report.verdicts:
        sentences.append(
            {"text": "The challenger did not review a flag on this case.", "citations": []}
        )
    elif not lowered:
        sentences.append(
            {
                "text": "The challenger lowered no finding. It kept the ones it reviewed.",
                "citations": [],
            }
        )
    else:
        ids = context_mod.rule_ids(case)
        reasons = [
            clean_text(v.reason, 240)
            for v in lowered
            if v.reason and not _names_rule(v.reason, ids)
        ]
        lead = (
            "The challenger lowered one finding."
            if len(lowered) == 1
            else f"The challenger lowered {len(lowered)} findings."
        )
        sentences.append({"text": " ".join([lead, *reasons]), "citations": []})
        if kept:
            noun = "finding" if len(kept) == 1 else "findings"
            sentences.append(
                {"text": f"It kept {len(kept)} other {noun} it reviewed.", "citations": []}
            )
    return {
        "status": "answered",
        "preamble": None,
        "message": None,
        "sentences": sentences[:SENTENCE_MAX],
        "flags": [],
        "dropped": {"citations_kept": 0, "citations_dropped": 0, "why": {}},
    }


def _quoted_flags(question: str, flags: Sequence[Flag]) -> list[str]:
    """Flag ids whose explanation or evidence the officer pasted into the question."""
    qn = norm(question)
    if len(qn) < 40:
        return []
    ids: list[str] = []
    for flag in flags:
        for text in (flag.explanation, flag.evidence):
            tn = norm(text)
            if len(tn) >= 40 and (tn[:40] in qn or qn[:40] in tn):
                ids.append(flag.id)
                break
    return ids


def _user_message(
    docs: Sequence[Document],
    flags: Sequence[Flag],
    case: Case,
    work: Sequence[Mapping[str, Any]],
    question: str,
    earlier: Sequence[Mapping[str, Any]],
) -> str:
    parts = []
    if work:
        try:
            parts.append(worklist_mod.blocks_for_worklist(list(work)))
        except ValueError as exc:
            raise ApiError(
                422,
                "validation_error",
                "The worklist cannot be read as plain text.",
                {"field": "worklist"},
            ) from exc
    if flags:
        try:
            parts.append(context_mod.blocks_for_flags(flags, case))
        except ValueError as exc:
            raise ApiError(
                422,
                "validation_error",
                "A flag on this case cannot be read as plain text.",
                {"field": "flags"},
            ) from exc
    if docs:
        parts.append(blocks_for_safe(docs))
    for turn in earlier:
        parts.append(
            "Earlier question:\n" + wrap_untrusted(str(turn["question"]), label="earlier_question")
        )
        shown_answer = _earlier_answer(turn)
        parts.append(
            "Earlier answer, already checked:\n"
            + wrap_untrusted(shown_answer, label="earlier_answer")
        )
    quoted = _quoted_flags(question, flags)
    if quoted:
        parts.append(
            "The officer's question quotes these flags: "
            + ", ".join(quoted)
            + ". Explain those flags and cite them."
        )
    try:
        parts.append(
            "The officer's question:\n" + wrap_untrusted(question, label="officer_question")
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "validation_error",
            "The question cannot be read as plain text.",
            {"field": "question"},
        ) from exc
    parts.append("Answer by calling the function once.")
    return "\n\n".join(p for p in parts if p)


def blocks_for_safe(docs: Sequence[Document]) -> str:
    try:
        return context_mod.blocks_for(docs)
    except ValueError as exc:
        raise ApiError(
            422,
            "validation_error",
            "A document on this case cannot be read as plain text.",
            {"field": "documents"},
        ) from exc


def _earlier_answer(turn: Mapping[str, Any]) -> str:
    answer = turn.get("answer")
    if not isinstance(answer, dict):
        return NOT_FOUND
    sentences = answer.get("sentences")
    lines: list[str] = []
    if answer.get("status") == "decision_request":
        lines.append(DECISION_PREAMBLE)
    if isinstance(sentences, list):
        for sentence in sentences:
            if isinstance(sentence, dict) and isinstance(sentence.get("text"), str):
                tag = (
                    "(general background, not from the case) " if sentence.get("background") else ""
                )
                lines.append(tag + sentence["text"])
    if answer.get("message"):
        lines.append(str(answer["message"]))
    return "\n".join(lines) or NOT_FOUND


def _meter(db: Database, settings: Settings, case: Case, account: str, now: datetime) -> CostMeter:
    """A meter that counts this question against the case, the day and this account."""
    prices = PriceTable.load(settings.prices_path)
    user_cap = settings.user_cap_for(account)
    daily = DailySpend.from_settings(db, settings, prices, now=lambda: now, account=account)
    if daily is None and (settings.max_usd_per_day > 0 or user_cap > 0):
        # The fake client skips the ledger. Ask still refuses once that ledger is already full.
        daily = DailySpend(
            db,
            settings.max_usd_per_day,
            now=lambda: now,
            account=account,
            user_cap=user_cap,
        )
    meter = CostMeter(BudgetLimits.from_settings(settings), prices, daily=daily)
    ask_calls, ask_usd = memory.prior_spend(db, case.id)
    prior_calls = sum(1 for c in case.costs if not c.cache_hit) + ask_calls
    prior_usd = sum((c.usd for c in case.costs), Decimal("0")) + ask_usd
    meter.account_for_prior(calls=prior_calls, usd=prior_usd)
    return meter


def _public(
    verified: Mapping[str, Any],
    case: Case,
) -> dict[str, Any]:
    status = str(verified["status"])
    sentences = list(verified["sentences"])
    message = NOT_FOUND if not sentences else None
    preamble = DECISION_PREAMBLE if status == "decision_request" else None
    cited = verified["cited_docs"] if isinstance(verified["cited_docs"], set) else set()
    flags = _open_flags(case, cited) if status == "decision_request" else []
    return {
        "status": status,
        "preamble": preamble,
        "message": message,
        "sentences": sentences,
        "flags": flags,
        "dropped": {
            "citations_kept": verified["citations_kept"],
            "citations_dropped": verified["citations_dropped"],
            "why": verified["dropped"],
        },
    }


def _stored_answer(public: Mapping[str, Any]) -> dict[str, Any]:
    """What the next turn is allowed to see: the text we showed, not the model's raw reply."""
    return {
        "status": public["status"],
        "preamble": public["preamble"],
        "message": public["message"],
        "sentences": [
            {
                "text": s["text"],
                "citations": [{"quote": c["quote"]} for c in s["citations"]],
                **({"background": True} if s.get("background") else {}),
            }
            for s in public["sentences"]
        ],
    }


def answer(
    db: Database,
    settings: Settings,
    llm: LLMClient,
    case: Case,
    *,
    account: str,
    question: str,
    scope: str,
    file_id: str | None,
    page: int | None,
    now: datetime,
) -> dict[str, Any]:
    """One question. Does not write the case. Returns the body the route sends back."""
    decision = is_decision_question(question)
    docs = context_mod.select_documents(case, scope=scope, file_id=file_id, page=page)
    flags = context_mod.select_flags(case, scope=scope, file_id=file_id)
    work = worklist_mod.load_worklist(db, on_screen=case.id)
    work.append(
        {"case_id": "service", "reference": "this service", "text": SERVICE, "on_screen": False}
    )
    team = context_mod.render_team(case)
    if team:
        work.append(
            {"case_id": "review-team", "reference": "review team", "text": team, "on_screen": True}
        )
    earlier = memory.recent(db, account=account, case_id=case.id, version=case.version)
    meter: CostMeter | None = None
    seeded = 0
    if not docs and not flags and not work:
        verified = _empty(decision, why={})
    else:
        meter = _meter(db, settings, case, account, now)
        seeded = meter._calls
        req = LLMRequest(
            purpose=PURPOSE,
            model_role="cheap",
            system=SYSTEM,
            user=_user_message(docs, flags, case, work, question, earlier),
            schema_name=SCHEMA_NAME,
            json_schema=reply_schema(),
        )
        try:
            with meter.scope(stage="score"):
                result = llm.complete_json(req, ctx_costs=meter)
        except LLMBudgetExceeded as exc:
            _remember_failure(
                db,
                case,
                account,
                question,
                scope,
                file_id,
                page,
                now,
                status="budget",
                meter=meter,
                seeded=seeded,
            )
            raise _budget_error(exc) from exc
        except LLMSchemaError:
            # The model spoke, but not in the schema. Abstain. Do not show its words.
            # The call was still reserved, and is counted below.
            verified = _empty(decision, why={"bad_shape": 1})
        except LLMError as exc:
            _remember_failure(
                db,
                case,
                account,
                question,
                scope,
                file_id,
                page,
                now,
                status="unavailable",
                meter=meter,
                seeded=seeded,
            )
            raise _unavailable(exc) from exc
        except PrivateModeViolation as exc:
            _remember_failure(
                db,
                case,
                account,
                question,
                scope,
                file_id,
                page,
                now,
                status="unavailable",
                meter=meter,
                seeded=seeded,
            )
            raise _unavailable(exc) from exc
        else:
            verified = verify_reply(result.data, docs, flags, case, work, decision=decision)
    called, model, prompt_tokens, completion_tokens, seconds, usd = _from_meter(meter, seeded)
    public = _public(verified, case)
    if public["status"] == "not_found" and not decision:
        filled = _from_challenger(case, question)
        if filled is not None:
            filled["dropped"] = public["dropped"]  # what the model said that was not shown
            public = filled
    memory.remember(
        db,
        account=account,
        case_id=case.id,
        version=case.version,
        scope=scope,
        file_id=file_id,
        page=page,
        question=question,
        status=str(public["status"]),
        answer=_stored_answer(public),
        citations_kept=int(verified["citations_kept"]),
        citations_dropped=int(verified["citations_dropped"]),
        dropped=dict(verified["dropped"]),
        model=model,
        prompt_sha256=PROMPT_SHA256,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=usd,
        seconds=seconds,
        called=called,
        ts=_iso(now),
    )
    return {
        "body": public,
        "call": {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "usd": usd,
            "seconds": seconds,
            "citations_kept": int(verified["citations_kept"]),
            "citations_dropped": int(verified["citations_dropped"]),
            "dropped": dict(verified["dropped"]),
            "status": str(public["status"]),
        },
    }


def _empty(decision: bool, *, why: dict[str, int]) -> dict[str, Any]:
    return {
        "status": "decision_request" if decision else "not_found",
        "sentences": [],
        "citations_kept": 0,
        "citations_dropped": 0,
        "dropped": why,
        "cited_docs": set(),
    }


def _iso(now: datetime) -> str:
    from app.core.db import ts

    return ts(now)


def _from_meter(meter: CostMeter | None, seeded: int) -> tuple[int, str, int, int, float, str]:
    """Provider calls this question reserved, including one whose answer was rejected."""
    if meter is None:
        return 0, "", 0, 0, 0.0, "0.000000"
    rows = [c for c in meter._costs if c.purpose == PURPOSE]
    reserved = max(0, meter._calls - seeded)
    if not rows:
        return reserved, "", 0, 0, 0.0, "0.000000"
    usd = sum((c.usd for c in rows), Decimal("0"))
    return (
        max(reserved, len(rows)),
        rows[-1].model,
        sum(c.prompt_tokens for c in rows),
        sum(c.completion_tokens for c in rows),
        sum(c.seconds for c in rows),
        format(usd6(usd), "f"),
    )


def _remember_failure(
    db: Database,
    case: Case,
    account: str,
    question: str,
    scope: str,
    file_id: str | None,
    page: int | None,
    now: datetime,
    *,
    status: str,
    meter: CostMeter | None = None,
    seeded: int = 0,
) -> None:
    called, model, prompt_tokens, completion_tokens, seconds, usd = _from_meter(meter, seeded)
    if called == 0:
        # Nothing was spent (a full budget refuses before the call). The audit row records it.
        return
    # A call was made and must count toward the case cap. The question is not a turn the next
    # question should read: it got no answer, so its words are not kept.
    memory.remember(
        db,
        account=account,
        case_id=case.id,
        version=case.version,
        scope=scope,
        file_id=file_id,
        page=page,
        question=question,
        keep_words=False,
        status=status,
        answer={"status": status, "sentences": [], "message": None, "preamble": None},
        citations_kept=0,
        citations_dropped=0,
        dropped={},
        model=model,
        prompt_sha256=PROMPT_SHA256,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=usd,
        seconds=seconds,
        called=called,
        ts=_iso(now),
    )


def _budget_error(exc: LLMBudgetExceeded) -> ApiError:
    if exc.limit == "user_daily_cap":
        return ApiError(
            429,
            "user_budget_used",
            "Your model budget for today is used up. It resets at midnight UTC. "
            "The checks and flags are unaffected.",
            {"limit_usd": exc.limit_value, "used_usd": exc.used},
        )
    if exc.limit == "daily_cap":
        return ApiError(
            429,
            "site_budget_used",
            "The model budget for today is used up. It resets at midnight UTC. "
            "The checks and flags are unaffected.",
            {"limit_usd": exc.limit_value, "used_usd": exc.used},
        )
    return ApiError(
        429,
        "case_budget_used",
        "This case has used its model budget. The checks and flags are unaffected.",
        {"limit": exc.limit, "limit_value": exc.limit_value, "used": exc.used},
    )


def _unavailable(exc: Exception) -> ApiError:
    from app.assistant.prompts import UNAVAILABLE

    return ApiError(503, "assistant_unavailable", UNAVAILABLE, {"reason": type(exc).__name__})


def audit_detail(
    *,
    version: int,
    question: str,
    scope: str,
    status: str,
    citations_kept: int,
    citations_dropped: int,
    dropped: Mapping[str, int],
    model: str,
    usd: str,
    prompt_tokens: int,
    completion_tokens: int,
    seconds: float,
) -> dict[str, Any]:
    """Ids, hashes and counts. Never the question text."""
    from app.audit import sha256_text

    return {
        "case_version": version,
        "question_len": len(question),
        "question_sha256": sha256_text(question),
        "scope": scope,
        "status": status,
        "citations_kept": citations_kept,
        "citations_dropped": citations_dropped,
        "dropped": dict(dropped),
        "model": model,
        "prompt_sha256": PROMPT_SHA256,
        "usd": usd,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "seconds": seconds,
    }
