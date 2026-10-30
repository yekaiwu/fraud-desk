"""Counterparty and entity adjudication (docs/semantic-signals.md, section "Entity adjudication").

The string matchers of the rule checks (``names.match_company``, ``names.match_person``, equal UENs,
truncations) decide first and are never overridden. Only a counterparty that no matcher links to any
declared party is a candidate, and only when it shares something with one (a word, an abbreviation of
a word, initials). Code builds the candidate pairs. The model then answers one closed question per
pair: ``same_entity``, ``different_entity`` or ``cannot_tell``, with a one-line reason, and cites both
strings (document id, field or row, the quote). Code verifies both citations and that they are the
two cells of the pair it asked about; anything else is dropped and counted.

What a verdict does:

* ``same_entity``  -> an ``info`` flag (weight 0): "the rules found no match, the model reads these as
  one entity". It never lowers a score or removes a rule's flag, and it is not a match: nothing else
  reads it.
* ``cannot_tell``  -> a ``low`` flag: needs ID (the names do not settle it). Never treated as a match.
* ``different_entity`` -> nothing is shown; it is counted.

No document text is in the system prompt. The pair sheet is in the user message between
``wrap_untrusted`` delimiters. Everything here is deterministic code except the one model call per
batch of pairs.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from app.core.context import CaseContext
from app.core.llm import wrap_untrusted
from app.core.models import Cell, Document, Flag, Row, clean_text
from app.graph import counterparty_of, uen_in
from app.names import (
    GENERIC_COMPANY_WORDS,
    LEGAL_TOKENS,
    entity_key,
    looks_like_nric,
    match_company,
    match_person,
    normalise_company,
    normalise_id,
    safe_text,
    sorted_rows,
)
from app.semantic.call import UNTRUSTED_NOTICE, SemanticNotRun, call_model
from app.semantic.claims import (
    Verification,
    VerifiedClaim,
    claims_json_schema,
    norm,
    verify_claims,
)
from app.semantic.flags import make_semantic_flag

RULE = "sem_entity_link"
PROMPT_NAME = "entity_adjudication"  # registered as purpose "semantic:entity_adjudication"

SAME, DIFFERENT, UNSURE = "same_entity", "different_entity", "cannot_tell"
VERDICTS = (SAME, DIFFERENT, UNSURE)
SEVERITY = {SAME: "info", UNSURE: "low"}  # fixed by code; the model's own severity is ignored
PREFIX = {SAME: "Possible link, the rules found no match. ", UNSURE: "Needs ID. "}

BATCH = 8  # pairs per model call
MAX_BATCHES = 3  # per case: at most 24 pairs are examined, highest overlap first
PER_COUNTERPARTY = 3  # declared parties one counterparty can be paired with
TEXT_MAX = 120
MIN_NAME_CHARS = 3

# ---------------------------------------------------------------------------------- the prompt

TASK = """\
You compare two names taken from a company's documents. Name A was seen on a bank statement or an \
invoice. Name B was declared in the application documents. Decide for each numbered pair whether A \
and B are the same legal entity written two ways.

Decisions:
- same_entity: A is clearly another written form of B. Typical cases: punctuation, case or spacing \
(A.B.C and ABC, Pte Ltd and Pte. Ltd.); a legal suffix spelled another way or left out on one side \
(Private Limited, (Pte) Ltd, Corporation, Corp, Sendirian Berhad, nothing); words in another order \
(Hale & Mercer and Mercer & Hale, "The" moved to the end); an abbreviated word (Elec Eng, Soln, \
Svcs, Trdg, Tech, Techs, Technologies, Logis, Logistics, Invest, Investments); a descriptor \
translated into Malay (see the word list below); a place written two ways (Hong Kong and HK, \
Singapore and (S), Malaysia and (M)); the same Chinese name spelled in dialect and in Pinyin (see below); a one-letter typing slip in a brand word \
(a letter dropped, added, doubled, two letters swapped, or one letter wrong) when everything else \
is identical (Wexfrd and Wexford, Alderton and Aldertonn, Harcourt and Hracourt). Compare the \
letters of each word one by one before you decide.
- different_entity: A and B are different entities, even when they share a word. This covers: a \
different brand with the same descriptor (Alder Freight Services and Birch Freight Services); the \
same brand with a different line of business (Larkin Plastics and Larkin Transport); a holding or \
group company and its trading company (Orwell Holdings and Orwell Foods); two different real words \
that look alike (Silverlake and Silvermont); different given names after the same surname (Yeo Kah \
Min and Yeo Kah Fatt); initials that differ in any letter or order (A.B.C. and A.B.D., X.Y.Z. and \
Z.Y.X.); two different legal forms or places for one name, when both sides state one (Inc and Pte \
Ltd, Pte Ltd and Sdn Bhd, Pte Ltd and Corp, (S) and (HK), LLP and Corp). A suffix that is missing on \
one side is not a different legal form.
- cannot_tell: the two strings do not settle it. For example only a common surname or one generic \
word is shared, or one name is only initials. A one-letter difference in a person's own name (given \
names and surnames, not a brand word) is cannot_tell, never same_entity.

Malay descriptors: Kejuruteraan = Engineering, Perniagaan = Trading, Perkhidmatan = Services, \
Hartanah = Properties, Perindustrian = Industries, Pembinaan = Construction, Pengangkutan = \
Transport, Teknologi = Technology, Perkapalan = Shipping, Pembangunan = Development, Makanan = Foods, \
Perubatan = Medical, Perladangan = Plantations, Percetakan = Printing, Pengurusan = Management, \
Sendirian Berhad = Sdn Bhd.

Chinese names: the same syllable is often spelled differently in dialect (Hokkien, Cantonese, \
Teochew) and in Pinyin. Treat a name as the same when every syllable of A can be the same sound as \
the syllable of B in the same position, and the descriptor and legal form agree. Common pairs: Tan = \
Chen, Lim = Lin, Lee = Li, Ng = Wu or Huang, Wong = Huang or Wang, Goh = Wu, Koh = Gao or Ge, Teo = \
Zhang, Yeo = Yang, Soh = Su or Xu, Seah = Xie, Chew = Zhou, Ho = He, Ong = Wang, Lau = Liu, Chua = \
Cai, Low = Luo, Sim = Shen, Kim = Jin, Seng = Sheng, Huat = Fa, Kee = Ji, Hup = He, Chye = Cai, \
Meng = Ming, Chuan = Quan, Hock = Fu, Teck = De, Hwa = Hua, Soon = Shun, Leong = Liang, Peng = Ping, \
Beng = Ming, Eng = Ying, Boon = Wen, Kiat = Jie. A syllable with no such pair, or a \
different number of syllables, makes it different_entity.

Rules: judge from the two strings only. Do not use outside knowledge of real companies. If you are \
unsure, answer cannot_tell, never same_entity.\
"""

ANSWER_RULES = (
    "Reply with exactly one claim for each pair, in order. Fields: pair_id (as printed), kind (your "
    "decision), statement (one sentence of at most 25 words that mentions both names), citations "
    "(exactly two entries, one for A and one for B; each repeats the document_id and the table, row "
    "and column of that name's line exactly as printed, with field null, and quote: that name's "
    "text copied exactly), severity (always low). A claim whose quote is not in the cited cell is "
    "discarded."
)

SYSTEM = f"{TASK}\n\n{UNTRUSTED_NOTICE}\n\n{ANSWER_RULES}"

# ---------------------------------------------------------------------------------- the data


@dataclass(frozen=True)
class Side:
    """One name and the cell it lives in. ``text`` is what the model is shown and quotes from."""

    doc: Document
    text: str
    texts: tuple[str, ...]  # value and printed form of the cell, for the quote check
    field: str | None = None
    table: str | None = None
    row: int | None = None
    column: str | None = None
    uen: str = ""
    role: str = ""
    kind: str = "auto"  # person | company | auto (which code matcher applies)
    rows: int = 1  # how many statement rows carry this counterparty

    def key(self) -> tuple[str, str | None, str | None, int | None]:
        return (self.doc.id, self.field, self.table, self.row)

    def hint(self) -> str:
        if self.field is not None:
            return f"document_id={self.doc.id} field={self.field}"
        return f"document_id={self.doc.id} table={self.table} row={self.row} column={self.column}"


@dataclass(frozen=True)
class Candidate:
    a: Side  # observed (statement line or invoice party)
    b: Side  # declared party
    score: float = 0.0


@dataclass(frozen=True)
class Adjudication:
    candidate: Candidate
    claim: VerifiedClaim

    @property
    def verdict(self) -> str:
        return self.claim.kind


@dataclass
class Plan:
    candidates: list[Candidate] = field(default_factory=list)
    observed: int = 0
    declared: int = 0
    resolved: int = 0  # observed names that a code matcher already links to a declared party
    examined: int = 0  # candidates sent to the model (after the cap)


# ------------------------------------------------------------------------- candidate generation

_NOISE = frozenset(
    {
        "fast", "giro", "paynow", "ibg", "tt", "inward", "outward", "payment", "transfer", "credit",
        "debit", "from", "to", "ref", "reference", "invoice", "inv", "payout", "fund", "funds",
        "fee", "fees", "cash", "deposit", "withdrawal", "bank", "pay", "paid", "for", "the", "and",
        "of", "salary", "refund",
    }
)  # fmt: skip
_WORD = re.compile(r"[^\W\d_]+")
_MATCHED = ("same", "probable")


def _distinct_tokens(text: str) -> list[str]:
    """Words that identify a name: letters only, no legal suffix, no generic or bank-channel word."""
    seen: dict[str, None] = {}
    for tok in _WORD.findall(normalise_company(text)):
        if tok in _NOISE or tok in LEGAL_TOKENS or tok in GENERIC_COMPANY_WORDS:
            continue
        seen.setdefault(tok)
    return list(seen)


def _subsequence(short: str, long_: str) -> bool:
    it = iter(long_)
    return all(ch in it for ch in short)


def _one_edit(x: str, y: str) -> bool:
    if abs(len(x) - len(y)) > 1 or x == y:
        return False
    if len(x) == len(y):
        return sum(1 for p, q in zip(x, y, strict=True) if p != q) == 1
    short, long_ = (x, y) if len(x) < len(y) else (y, x)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))


def _strong(x: str, y: str) -> bool:
    """Two words that plausibly stand for each other: equal, one a prefix of the other, a short
    form of the other (``trdg``/``trading``), or one letter apart (words of five letters or more)."""
    if x == y:
        return True
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    if len(short) >= MIN_NAME_CHARS and long_.startswith(short):
        return True
    if (
        len(short) >= MIN_NAME_CHARS
        and short[0] == long_[0]
        and short[-1] == long_[-1]
        and _subsequence(short, long_)
    ):
        return True
    return len(short) >= 5 and _one_edit(x, y)


def _initialism(tokens: list[str], other: list[str]) -> bool:
    """One word of ``tokens`` is the initials of two or more words of ``other``."""
    if len(other) < 2:
        return False
    initials = "".join(t[0] for t in other)
    return any(2 <= len(t) <= 6 and t == initials for t in tokens)


def overlap(a: str, b: str) -> float:
    """0 when the two names share nothing worth asking about; otherwise the number of words of ``a``
    that have a plausible counterpart in ``b`` (an initialism counts as one). Initial letters alone
    (``W`` for ``Wei``) never make a candidate."""
    ta, tb = _distinct_tokens(a), _distinct_tokens(b)
    strong = sum(1 for x in ta if any(_strong(x, y) for y in tb))
    if _initialism(ta, tb) or _initialism(tb, ta):
        strong += 1
    if not strong:
        return 0.0
    weak = sum(
        0.5
        for x in ta
        for y in tb
        if (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))
    )
    return strong + min(weak, 1.0)


def _uen_of(text: str | None) -> str:
    """A UEN-shaped identifier (the only kind a statement description can carry), else ''."""
    ident = normalise_id(text)
    return ident if ident and uen_in(ident) == ident.upper() else ""


def code_matches(a: Side, b: Side) -> bool:
    """True when the rule checks' own matchers link the two names (same UEN, or a ``same`` or
    ``probable`` name match, or a truncation). The model is never asked about such a pair."""
    ua, ub = _uen_of(a.uen), _uen_of(b.uen)
    if ua and ub:
        return ua == ub
    kinds = ("person", "company") if b.kind == "auto" else (b.kind,)
    for kind in kinds:
        matcher = match_person if kind == "person" else match_company
        if matcher(a.text, b.text).verdict in _MATCHED:
            return True
    return False


def id_conflict(a: Side, b: Side) -> bool:
    ua, ub = _uen_of(a.uen), _uen_of(b.uen)
    return bool(ua and ub and ua != ub)


# ------------------------------------------------------------------------------ party gathering


def _usable(doc: Document) -> bool:
    return doc.status in ("ok", "partial")


def _side(
    doc: Document,
    cell: Cell | None,
    *,
    text: str | None = None,
    field_name: str | None = None,
    table: str | None = None,
    row: Row | None = None,
    column: str | None = None,
    uen: str = "",
    role: str = "",
    kind: str = "auto",
) -> Side | None:
    if cell is None:
        return None
    shown = safe_text(text if text is not None else cell.text(), TEXT_MAX)
    if len(shown) < MIN_NAME_CHARS:
        return None
    texts = tuple(t for t in (cell.value, cell.raw) if t)
    return Side(
        doc=doc,
        text=shown,
        texts=texts,
        field=field_name,
        table=table,
        row=row.index if row is not None else None,
        column=column,
        uen=uen,
        role=role,
        kind=kind,
    )


def _text(doc: Document, name: str) -> str:
    cell = doc.fields.get(name)
    return cell.text().strip() if cell is not None else ""


def _is_applicant(ctx: CaseContext, name: str, uen: str) -> bool:
    app_name, app_uen = (ctx.applicant.name or "").strip(), normalise_id(ctx.applicant.uen)
    if app_uen and normalise_id(uen) == app_uen:
        return True
    return bool(app_name) and match_company(name, app_name).verdict in _MATCHED


def _other_party(
    ctx: CaseContext, doc: Document, first: str, second: str, first_role: str, second_role: str
) -> Side | None:
    """The party of ``doc`` that is not the applicant (``first``/``second`` are field-name prefixes
    such as ``supplier``/``buyer``), as a Side; None when the applicant is neither party."""
    parties = []
    for prefix, role in ((first, first_role), (second, second_role)):
        name = _text(doc, f"{prefix}_name")
        uen = _text(doc, f"{prefix}_uen")
        parties.append((prefix, role, name, uen))
    mine = [p for p in parties if p[2] and _is_applicant(ctx, p[2], p[3])]
    if len(mine) != 1:
        return None
    other = next(p for p in parties if p is not mine[0])
    if not other[2]:
        return None
    return _side(
        doc,
        doc.fields.get(f"{other[0]}_name"),
        field_name=f"{other[0]}_name",
        uen=other[3],
        role=other[1],
        kind="company",
    )


def declared_parties(ctx: CaseContext) -> list[Side]:
    """Names the application documents put forward, each with the cell it is in: the other party of
    a contract or purchase order, the source-of-funds counterparty and declarant, the applicant,
    directors and shareholders. De-duplicated by role and normalised name (first document wins)."""
    out: list[Side] = []
    for doc in ctx.docs():
        if not _usable(doc):
            continue
        t = doc.doc_type
        if t == "contract":
            side = _other_party(ctx, doc, "seller", "buyer", "supplier", "customer")
        elif t == "purchase_order":
            side = _other_party(ctx, doc, "supplier", "buyer", "supplier", "customer")
        elif t == "source_of_funds_declaration":
            for fname, role, kind in (
                ("counterparty_name", "source-of-funds counterparty", "auto"),
                ("declarant_name", "declarant", "person"),
            ):
                s = _side(doc, doc.fields.get(fname), field_name=fname, role=role, kind=kind)
                if s is not None:
                    out.append(s)
            continue
        elif t in ("company_profile", "certificate_of_incorporation"):
            s = _side(
                doc,
                doc.fields.get("company_name"),
                field_name="company_name",
                uen=_text(doc, "uen"),
                role="applicant",
                kind="company",
            )
            if s is not None:
                out.append(s)
            if t == "certificate_of_incorporation":
                continue
            for row in sorted_rows(doc.tables.get("directors")):
                s = _side(
                    doc,
                    row.cells.get("director_name"),
                    table="directors",
                    row=row,
                    column="director_name",
                    role="director",
                    kind="person",
                )
                if s is not None:
                    out.append(s)
            out += _holders(doc, "shareholders")
            continue
        elif t == "shareholder_register":
            out += _holders(doc, "shareholders")
            continue
        elif t == "ownership_chart":
            out += _holders(doc, "holders")
            continue
        else:
            continue
        if side is not None:
            out.append(side)
    seen: set[tuple[str, str]] = set()
    unique = []
    for s in out:
        key = (s.role, normalise_company(s.text))
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


def _holders(doc: Document, table: str) -> list[Side]:
    out = []
    for row in sorted_rows(doc.tables.get(table)):
        ident = row.cells["id_or_uen"].text() if "id_or_uen" in row.cells else ""
        holder_type = row.cells["holder_type"].text() if "holder_type" in row.cells else ""
        if holder_type == "individual" or looks_like_nric(ident):
            kind = "person"
        elif holder_type == "company" or _uen_of(ident):
            kind = "company"
        else:
            kind = "auto"
        s = _side(
            doc,
            row.cells.get("holder_name"),
            table=table,
            row=row,
            column="holder_name",
            uen=ident,
            role="shareholder",
            kind=kind,
        )
        if s is not None:
            out.append(s)
    return out


def observed_names(ctx: CaseContext) -> list[Side]:
    """Counterparties as the case shows them: each distinct name on a statement line (the first line
    that carries it; ``rows`` counts the rest) and the other party of each invoice."""
    found: dict[str, Side] = {}
    for doc in ctx.docs("bank_statement"):
        if not _usable(doc):
            continue
        for row in sorted_rows(doc.tables.get("transactions")):
            desc = row.cells.get("description")
            if desc is None or not desc.text().strip():
                continue
            name = counterparty_of(row) or ""
            haystacks = [norm(t) for t in (desc.value, desc.raw) if t]
            if not name or not any(norm(name) in h for h in haystacks):
                name = desc.text()  # an annotation label that is not in the cell cannot be quoted
            side = _side(
                doc,
                desc,
                text=name,
                table="transactions",
                row=row,
                column="description",
                uen=uen_in(desc.text()) or "",
                role="counterparty",
            )
            if side is None:
                continue
            key = norm(side.text)
            if key in found:
                found[key] = dataclasses.replace(found[key], rows=found[key].rows + 1)
            else:
                found[key] = side
    for doc in ctx.docs("invoice"):
        if not _usable(doc):
            continue
        side = _other_party(ctx, doc, "supplier", "buyer", "supplier", "customer")
        if side is not None:
            found.setdefault(norm(side.text), side)
    return list(found.values())


def build_candidates(ctx: CaseContext) -> Plan:
    """Pairs of (observed name, declared party) that no code matcher links and that share something
    worth asking about, best overlap first, capped at ``BATCH * MAX_BATCHES``."""
    observed, declared = observed_names(ctx), declared_parties(ctx)
    plan = Plan(observed=len(observed), declared=len(declared))
    scored: list[Candidate] = []
    for a in observed:
        if _is_applicant(ctx, a.text, a.uen) or any(code_matches(a, b) for b in declared):
            plan.resolved += 1
            continue
        mine = []
        for b in declared:
            if b.key() == a.key() or id_conflict(a, b):
                continue
            score = overlap(a.text, b.text)
            if score > 0:
                mine.append(Candidate(a, b, score))
        mine.sort(key=lambda c: (-c.score, c.b.doc.id, c.b.row or 0, c.b.text))
        scored += mine[:PER_COUNTERPARTY]
    scored.sort(key=lambda c: (-c.score, norm(c.a.text), norm(c.b.text), c.b.role))
    plan.candidates = scored[: BATCH * MAX_BATCHES]
    plan.examined = len(plan.candidates)
    return plan


# ------------------------------------------------------------------------- the model call


def pair_sheet(chunk: Sequence[Candidate]) -> str:
    """The user message: the pairs, untrusted-wrapped. Document ids and locators are ours; the names
    are document text, so the whole sheet sits inside one delimited block."""
    lines = ["Decide each pair."]
    for n, c in enumerate(chunk, 1):
        role = f" (B is the declared {c.b.role})" if c.b.role else ""
        lines.append(f"P{n}{role}")
        lines.append(f"  A  {c.a.hint()}  text={json.dumps(c.a.text, ensure_ascii=False)}")
        lines.append(f"  B  {c.b.hint()}  text={json.dumps(c.b.text, ensure_ascii=False)}")
    return wrap_untrusted("\n".join(lines), label="name_pairs")


def pair_schema(n: int) -> dict:
    """Claims schema with a closed ``pair_id`` and the decision first in each claim."""
    schema = claims_json_schema(
        VERDICTS,
        extra_properties={
            "pair_id": {"type": "string", "enum": [f"P{i}" for i in range(1, n + 1)]}
        },
        extra_required=["pair_id"],
    )
    item = schema["properties"]["claims"]["items"]
    cite = item["properties"]["citations"]["items"]
    cite["required"] = ["document_id", "field", "table", "row", "column", "quote"]
    props = item["properties"]
    item["properties"] = {k: props[k] for k in ("pair_id", "kind", "statement", "citations")} | {
        "severity": props["severity"]
    }
    return schema


def _cites(claim: VerifiedClaim, side: Side) -> bool:
    """The claim cites exactly this cell, with a quote that is in this cell's text."""
    for c in claim.citations:
        if (c.document_id, c.field, c.table, c.row) != side.key():
            continue
        if c.column not in (None, side.column):
            continue
        q = norm(c.quote)
        if q and any(q in norm(t) for t in side.texts):
            return True
    return False


def verify_pairs(
    reply: dict, chunk: Sequence[Candidate], docs: Sequence[Document]
) -> tuple[list[Adjudication], Verification]:
    """Verified answers for a chunk. Besides the citation checks of ``verify_claims``, a claim must
    name one of the chunk's pairs and cite that pair's two cells; at most one verdict is kept per
    pair, and a pair with two different verdicts keeps none. Drops are counted, never shown."""
    base = verify_claims(
        reply, docs, kinds=VERDICTS, max_claims=2 * len(chunk), extra_keys=("pair_id",)
    )
    dropped = Counter(base.dropped)
    by_pair: dict[int, list[VerifiedClaim]] = {}
    for claim in base.claims:
        pid = str(claim.attrs.get("pair_id", ""))
        m = re.fullmatch(r"P(\d+)", pid)
        index = int(m.group(1)) - 1 if m else -1
        if not 0 <= index < len(chunk):
            dropped["unknown_pair"] += 1
        elif not (_cites(claim, chunk[index].a) and _cites(claim, chunk[index].b)):
            dropped["pair_mismatch"] += 1
        else:
            by_pair.setdefault(index, []).append(claim)
    kept: list[Adjudication] = []
    for index in sorted(by_pair):
        claims = by_pair[index]
        if len({c.kind for c in claims}) > 1:
            dropped["conflict"] += len(claims)
            continue
        dropped["duplicate"] += len(claims) - 1
        kept.append(Adjudication(chunk[index], claims[0]))
    out = Verification(
        claims=[k.claim for k in kept],
        proposed=base.proposed,
        dropped=dropped,
        severity_capped=base.severity_capped,
    )
    return kept, out


def adjudicate_chunk(
    ctx: CaseContext, chunk: Sequence[Candidate], docs: Sequence[Document]
) -> tuple[list[Adjudication], Verification]:
    """One model call for up to ``BATCH`` pairs; raises ``SemanticNotRun`` when the call fails."""
    reply = call_model(
        ctx,
        name=PROMPT_NAME,
        system=SYSTEM,
        user=pair_sheet(chunk),
        schema_name="Claims",
        json_schema=pair_schema(len(chunk)),
        stage="crossdoc",
    )
    return verify_pairs(reply, chunk, docs)


def adjudicate(ctx: CaseContext, candidates: Sequence[Candidate]) -> list[Adjudication]:
    """All chunks of a case; the counts go to the check run through ``ctx.semantic_tally``.
    A failed chunk makes the whole check not run (``SemanticNotRun``): no partial answers. A chunk
    in which no pair got a verified answer (an empty reply, or every claim dropped) is a failed
    chunk, not an all-clear."""
    docs = ctx.docs()
    results: list[Adjudication] = []
    tally = Verification()
    for start in range(0, len(candidates), BATCH):
        chunk = candidates[start : start + BATCH]
        kept, verification = adjudicate_chunk(ctx, chunk, docs)
        if not kept:  # the prompt asks for one claim per pair: none verified is a failed call
            raise SemanticNotRun(
                f"no verified answer for any of {len(chunk)} pairs ({verification.note()})"
            )
        results += kept
        tally.merge(verification)
    if candidates:
        ctx.semantic_tally.setdefault(RULE, Verification()).merge(tally)
    return results


def flag_for(adj: Adjudication, docs: Sequence[Document]) -> Flag | None:
    """The flag of one verdict, or None for ``different_entity``. Severity is fixed here."""
    severity = SEVERITY.get(adj.verdict)
    if severity is None:
        return None
    claim = dataclasses.replace(adj.claim, statement=PREFIX[adj.verdict] + adj.claim.statement)
    b = adj.candidate.b
    return make_semantic_flag(
        RULE,
        claim,
        docs,
        discriminator=f"{adj.verdict}:{b.role}:{entity_key(b.text, 'auto')}"[:60],
        severity=severity,  # type: ignore[arg-type]
    )


def flags_from(results: Iterable[Adjudication], docs: Sequence[Document]) -> list[Flag]:
    return [f for f in (flag_for(a, docs) for a in results) if f is not None]


# ---------------------------------------------------------- name pairs without documents (eval)


@dataclass(frozen=True)
class NamePair:
    pair_id: str
    a: str
    b: str
    role: str = "name"


@dataclass(frozen=True)
class PairResult:
    pair_id: str
    verdict: str | None  # same_entity | different_entity | cannot_tell | None (no verified answer)
    reason: str | None = None
    error: str | None = None  # set when the whole call failed


def _pair_documents(pairs: Sequence[NamePair], n: int) -> tuple[list[Candidate], list[Document]]:
    from app.core.testing import make_doc  # test and script helper: builds a Document

    stmt = make_doc(
        "bank_statement",
        id=f"s{n}",
        tables={"transactions": [{"description": p.a} for p in pairs]},
    )
    prof = make_doc(
        "company_profile",
        id=f"p{n}",
        tables={"directors": [{"director_name": p.b} for p in pairs]},
    )
    cands = []
    for i, p in enumerate(pairs):
        a_row, b_row = stmt.tables["transactions"].rows[i], prof.tables["directors"].rows[i]
        a = _side(
            stmt,
            a_row.cells["description"],
            table="transactions",
            row=a_row,
            column="description",
            role="counterparty",
        )
        b = _side(
            prof,
            b_row.cells["director_name"],
            table="directors",
            row=b_row,
            column="director_name",
            role=p.role,
        )
        if a is None or b is None:
            raise ValueError(
                f"pair {p.pair_id}: a name is shorter than {MIN_NAME_CHARS} characters"
            )
        cands.append(Candidate(a, b))
    return cands, [stmt, prof]


def adjudicate_name_pairs(
    ctx: CaseContext,
    pairs: Sequence[NamePair],
    *,
    batch: int = BATCH,
    on_error: Callable[[Exception], None] | None = None,
) -> tuple[list[PairResult], Verification]:
    """The same prompt, schema and verification as the check, for bare name pairs (the evaluation
    path). Each pair becomes a statement line (A) and a director row (B) of two small documents.
    A failed call gives ``error`` on that batch's pairs and the run goes on. Not code-matched first:
    the caller decides which pairs to send."""
    results: dict[str, PairResult] = {}
    total = Verification()
    for n, start in enumerate(range(0, len(pairs), batch)):
        chunk_pairs = pairs[start : start + batch]
        cands, docs = _pair_documents(chunk_pairs, n)
        try:
            kept, verification = adjudicate_chunk(ctx, cands, docs)
        except SemanticNotRun as exc:
            if on_error is not None:
                on_error(exc)
            for p in chunk_pairs:
                results[p.pair_id] = PairResult(p.pair_id, None, error=exc.reason)
            continue
        total.merge(verification)
        by_cand = {id(a.candidate): a for a in kept}
        for p, c in zip(chunk_pairs, cands, strict=True):
            adj = by_cand.get(id(c))
            results[p.pair_id] = (
                PairResult(p.pair_id, adj.verdict, clean_text(adj.claim.statement, 240))
                if adj is not None
                else PairResult(p.pair_id, None)
            )
    return [results[p.pair_id] for p in pairs], total
