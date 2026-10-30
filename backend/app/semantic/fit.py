"""FIT: closed "does it fit" questions about what a business or a person declares (docs/semantic-signals.md).

Three questions, one model call each per case, each with a small ordinal answer and cited quotes:

* ``goods_fit``      do the invoice lines the company issued fit its declared principal activity?
* ``income_fit``     is the declared amount plausible for the job the source-of-funds text names?
* ``coherence_fit``  do the counterparties on the bank statement fit the declared principal activity?

The model answers ``fits`` / ``unusual`` / ``does_not_fit`` and, unless it says ``fits``, cites the
data it rests on. Only ``does_not_fit`` with at least one verified claim becomes a flag (medium, the
cap). ``unusual`` is the model's way out for an ordinary-but-odd case: it is recorded in the result
and counted, and never shown as a flag. Every citation is verified against the extracted data by the
shared verifier; each claim must also cite the *evidence side* of its question (an invoice line, a
statement line, the source description), and the declared side (the profile's activity, the declared
amount) is added by code, not by the model, so a flag always shows both halves of the comparison.

Inputs are small and chosen by code: only the cells a question is about, no names (a company or
person name is never sent), at most a few dozen lines. A failed call is ``SemanticNotRun``; a case
without the needed fields is ``CheckSkipped``. The checks that use this module are registered in
``app/semantic/checks/fit.py``; this module registers nothing.
"""

from __future__ import annotations

import copy
import dataclasses
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.checks.registry import USABLE_DOC_STATUSES, CheckSkipped
from app.core.context import CaseContext
from app.core.models import Document, Flag, Row
from app.names import ids_conflict_or_equal, match_company
from app.semantic.call import SemanticNotRun, call_model, system_prompt
from app.semantic.claims import (
    Verification,
    VerifiedCitation,
    VerifiedClaim,
    claims_json_schema,
    norm,
    verify_claims,
)
from app.semantic.flags import make_semantic_flag
from app.semantic.render import render_document, wrap_documents

FIT_VERSION = "2"  # part of the cache key: bump when inputs or answer handling change

ANSWERS = ("fits", "unusual", "does_not_fit")
FLAGGED_ANSWER = "does_not_fit"  # the only answer that can become a flag
FLAG_SEVERITY = "medium"  # fixed; the model's own severity is not used

MAX_CLAIMS = 3  # per question
MAX_INVOICES = 6
MAX_LINES_PER_INVOICE = 8
MAX_LINES = 30
MAX_STATEMENT_ROWS = 40
MIN_STATEMENT_ROWS = 3
MAX_DECLARATIONS = 3

# Extra reasons counted in ``Verification.dropped`` on top of the shared verifier's
# (``claims.DROP_REASONS``).
WRONG_ANCHOR = "wrong_anchor"  # cites no cell of the evidence side of the question
NO_CLAIM = "no_claim"  # answered does_not_fit and cited nothing
ANSWER_PREFIX = "answer_"  # claims that came with a fits / unusual answer: "answer_unusual"

# ------------------------------------------------------------------------------------ prompts

_ANSWER_RULES = (
    'Reply with "answer", exactly one of: fits, unusual, does_not_fit. '
    "If the answer is fits, return no claims. "
    'If it is unusual or does_not_fit, return at most 3 claims of kind "{kind}", each one citing '
    "the data it rests on, with the quotes copied exactly. "
    "In a citation, document_id is the id printed after the word document on the first line of the "
    "block (for example invoice:INV-0001 or company_profile:-), never the DOC-... marker of the "
    "block. To cite a field give field, the name printed after the word field. To cite a table line "
    "give table, row (the number printed after the word row) and column. "
    "When unsure between fits and unusual, answer fits. When unsure between unusual and "
    "does_not_fit, answer unusual."
)

GOODS_TASK = (
    "You answer one closed question about a company, using only the text provided.\n\n"
    "Question: do the invoice lines the company issued fit its declared principal activity?\n"
    "- fits: the lines are what a company with that activity sells, or items of the same trade or "
    "sold alongside it (accessories, consumables, packaging, a small side item). Delivery, "
    "installation, repair, rental, training, warranty and handling charges, and a general or "
    "generic declared activity (for example general trade), all fit.\n"
    "- unusual: a line is outside the declared activity but could have an ordinary reason.\n"
    "- does_not_fit: at least one line is a different kind of goods or service from a different "
    "industry than the declared activity (for example veterinary services or tractor parts on the "
    "invoices of a bakery), it is not an accessory, a charge or a side item of the declared trade, and "
    "nothing in the text explains it. One such line is enough.\n"
    "Judge only the kind of goods or services. Do not judge prices, quantities, totals, dates or "
    "names.\n"
    "Each claim cites only the odd invoice lines; the declared activity is added by the system. A "
    "citation looks like "
    '{"document_id": "invoice:INV-0001", "table": "lines", "row": 2, "column": '
    '"description", "quote": "<the description copied exactly>"}; always give table and row.\n'
    + _ANSWER_RULES.format(kind="goods_industry_mismatch")
)

INCOME_TASK = (
    "You answer one closed question about a source-of-funds declaration, using only the text "
    "provided.\n\n"
    "Question: is the declared amount plausible for the source the description gives? The "
    "description usually names a job or role and may say how the money arose (salary, bonus, "
    "savings over some years) or that it came from something else (sale of property, inheritance, "
    "a loan, a business).\n"
    "- fits: the amount is within what that job could earn or save over the period described, or "
    "the money is said to come from something other than the job, or no job is described.\n"
    "- unusual: the amount is high for the job, but the text gives a reason that could explain it "
    "(a former better-paid role, a long period, another source).\n"
    "- does_not_fit: the amount is many times what the job named as the source could plausibly "
    "earn or save in the period described, and the text offers no other explanation.\n"
    "Use ordinary pay levels for the currency shown. Judge the job against the amount only; do not "
    "judge names, dates or account numbers. If there are several declarations, answer for the "
    "least plausible one.\n"
    "Each claim cites the description, always with its field name: "
    '{"document_id": "source_of_funds_declaration:-", "field": "origin_description", "quote": '
    '"<the words naming the job or role, copied exactly>"}. '
    "It may also cite the field declared_amount the same way.\n"
    + _ANSWER_RULES.format(kind="income_occupation_implausible")
)

COHERENCE_TASK = (
    "You answer one closed question about a company, using only the text provided.\n\n"
    "Question: do the counterparties and payment descriptions on the bank statement fit the "
    "company's declared principal activity? Lines show the description with the debit or credit "
    "amount.\n"
    "- fits: the trading counterparties (the customers it is paid by and the suppliers of goods it "
    "pays) are ordinary for that activity, or for any activity the profile declares. Overheads and "
    "one-off purchases (staff pay, rent, utilities, tax, bank charges, loans, transfers between own "
    "accounts, fit-out, signage, a vehicle, legal, audit or insurance fees) never count against "
    "the company. A general or generic declared activity fits anything.\n"
    "- unusual: one trading counterparty looks unrelated to the activity but could have an "
    "ordinary reason.\n"
    "- does_not_fit: two or more trading counterparties clearly belong to a different line of "
    "business than the declared one (for example fertiliser and tractor dealers as suppliers of a "
    "hairdressing salon), the profile does not "
    "declare that other business, and nothing in the text explains them.\n"
    "Judge only what the counterparty names and descriptions suggest about the kind of business. "
    "Do not judge amounts, dates or whether a name is familiar.\n"
    "Each claim cites only the odd statement lines; the declared activity is added by the system. "
    "A citation looks like "
    '{"document_id": "bank_statement:2025-09", "table": "transactions", "row": 4, "column": '
    '"description", "quote": "<the description copied exactly>"}; always give table and row.\n'
    + _ANSWER_RULES.format(kind="activity_incoherence")
)

USER_PREFIX = "Answer the question for the documents below."


def fit_schema(kind: str, *, cite_field: bool = False) -> dict[str, Any]:
    """Claims schema with a leading ``answer`` (closed set): ``{"answer": ..., "claims": [...]}``.

    ``cite_field``: every citation must name a field (a question whose evidence is a field of the
    document, not a table line). The local model otherwise leaves the optional ``field`` out of a
    citation, and the shared verifier drops the claim as ``bad_shape``."""
    base = claims_json_schema([kind])
    if cite_field:
        base = copy.deepcopy(base)
        cite = base["properties"]["claims"]["items"]["properties"]["citations"]["items"]
        cite["properties"]["field"] = {"type": "string"}
        cite["required"] = ["document_id", "field", "quote"]
    props = {"answer": {"type": "string", "enum": list(ANSWERS)}, **base["properties"]}
    return {
        "type": "object",
        "properties": props,
        "required": ["answer", *base["required"]],
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------------- the inputs


@dataclass(frozen=True)
class Anchor:
    """The evidence side of a question: a field, or one column of a table, of some documents."""

    doc_ids: frozenset[str]
    field: str | None = None
    table: str | None = None
    column: str | None = None


@dataclass
class Prepared:
    """What one call is about: the (trimmed) documents shown, and how to judge the reply."""

    shown: list[Document]  # what the model sees; rows keep their original index
    originals: list[Document]  # the real documents, for flags
    fields: dict[str, list[str]]
    tables: dict[str, list[str]]
    columns: dict[str, dict[str, list[str]]]
    anchor: Anchor
    declared: tuple[str, str]  # (document id, field) added to every flag as the declared side


@dataclass(frozen=True)
class Question:
    key: str  # prompt id is "semantic:<key>"
    rule: str
    kind: str
    task: str
    prepare: Callable[[CaseContext], Prepared]
    description: str
    cite_field: bool = False  # the evidence is a field: citations must name one (see fit_schema)


@dataclass
class FitResult:
    """One question answered: the ordinal, what survived verification, and the counts."""

    question: str
    answer: str
    verification: Verification  # final counts (kept = claims, after every filter)
    claims: list[VerifiedClaim] = field(default_factory=list)  # kept, declared side added
    originals: list[Document] = field(default_factory=list)

    def flags(self, rule: str) -> list[Flag]:
        if self.answer != FLAGGED_ANSWER:
            return []
        out: list[Flag] = []
        for c in self.claims:
            first = c.citations[0]
            where = f"r{first.row}" if first.row is not None else (first.field or "x")
            out.append(
                make_semantic_flag(
                    rule,
                    c,
                    self.originals,
                    discriminator=f"{c.kind}-{where}",
                    severity=FLAG_SEVERITY,
                )
            )
        return out


def _usable(ctx: CaseContext, *types: str) -> list[Document]:
    return [d for d in ctx.docs(*types) if d.status in USABLE_DOC_STATUSES]


def _value(doc: Document, name: str) -> str:
    cell = doc.fields.get(name)
    return (cell.value or "").strip() if cell is not None else ""


def _same_company(
    uen_a: str | None, name_a: str | None, uen_b: str | None, name_b: str | None
) -> bool | None:
    """True / False when the ids or names settle it, None when nothing can be compared."""
    ids = ids_conflict_or_equal(uen_a, uen_b)
    if ids != "na":
        return ids == "equal"
    verdict = match_company(name_a, name_b).verdict
    if verdict in ("same", "probable"):
        return True
    return False if verdict == "different" else None


def _profile(ctx: CaseContext) -> Document:
    """The company profile the declared activity is read from (the applicant's when there are
    several)."""
    profiles = [p for p in _usable(ctx, "company_profile") if _value(p, "principal_activity")]
    if not profiles:
        raise CheckSkipped("no company profile with a declared principal activity")
    if len(profiles) == 1:
        return profiles[0]
    app_ = ctx.applicant
    hits = [
        p
        for p in profiles
        if _same_company(app_.uen, app_.name, _value(p, "uen"), _value(p, "company_name")) is True
    ]
    if len(hits) != 1:
        raise CheckSkipped("several company profiles and none identified as the applicant's")
    return hits[0]


def _with_rows(doc: Document, table: str, rows: list[Row]) -> Document:
    """A copy of ``doc`` whose ``table`` holds only ``rows`` (they keep their index, so a citation
    of a shown row is a citation of the real one)."""
    trimmed = doc.tables[table].model_copy(update={"rows": rows})
    return doc.model_copy(update={"tables": {**doc.tables, table: trimmed}})


def prepare_goods(ctx: CaseContext) -> Prepared:
    profile = _profile(ctx)
    issued: list[Document] = []
    for inv in _usable(ctx, "invoice"):
        same = _same_company(
            _value(profile, "uen"),
            _value(profile, "company_name"),
            _value(inv, "supplier_uen"),
            _value(inv, "supplier_name"),
        )
        if same is True and "lines" in inv.tables:
            issued.append(inv)
    shown: list[Document] = []
    budget = MAX_LINES
    for inv in issued[:MAX_INVOICES]:
        rows = [r for r in inv.tables["lines"].rows if _text(r, "description")]
        rows = rows[: min(MAX_LINES_PER_INVOICE, budget)]
        if rows:
            shown.append(_with_rows(inv, "lines", rows))
            budget -= len(rows)
        if budget <= 0:
            break
    if not shown:
        raise CheckSkipped("no invoice line issued by the profiled company")
    return Prepared(
        shown=[profile, *shown],
        originals=[profile, *[d for d in issued if d.id in {s.id for s in shown}]],
        fields={profile.id: ["principal_activity"], **{d.id: [] for d in shown}},
        tables={profile.id: [], **{d.id: ["lines"] for d in shown}},
        columns={d.id: {"lines": ["description"]} for d in shown},
        anchor=Anchor(frozenset(d.id for d in shown), table="lines", column="description"),
        declared=(profile.id, "principal_activity"),
    )


def prepare_income(ctx: CaseContext) -> Prepared:
    decls = [
        d
        for d in _usable(ctx, "source_of_funds_declaration")
        if _value(d, "origin_description")
        and d.fields.get("declared_amount") is not None
        and d.fields["declared_amount"].money() is not None
    ][:MAX_DECLARATIONS]
    if not decls:
        raise CheckSkipped("no source-of-funds declaration with a description and an amount")
    names = [
        "origin_category",
        "origin_description",
        "declared_amount",
        "currency",
    ]  # not the period fields: they date the receiving account, not the earning
    return Prepared(
        shown=decls,
        originals=decls,
        fields={d.id: names for d in decls},
        tables={d.id: [] for d in decls},
        columns={},
        anchor=Anchor(frozenset(d.id for d in decls), field="origin_description"),
        declared=(decls[0].id, "declared_amount"),
    )


def _amount(row: Row) -> Decimal:
    best = Decimal(0)
    for col in ("debit", "credit"):
        cell = row.cells.get(col)
        value = cell.money() if cell is not None else None
        if value is not None and abs(value) > best:
            best = abs(value)
    return best


def prepare_coherence(ctx: CaseContext) -> Prepared:
    profile = _profile(ctx)
    pool: list[tuple[Decimal, str, Row]] = []
    for st in _usable(ctx, "bank_statement"):
        same = _same_company(
            _value(profile, "uen"),
            _value(profile, "company_name"),
            _value(st, "uen"),
            _value(st, "account_holder"),
        )
        if same is False or "transactions" not in st.tables:
            continue  # another party's account (a director's own, say) says nothing here
        pool += [
            (_amount(r), st.id, r)
            for r in st.tables["transactions"].rows
            if _text(r, "description")
        ]
    if len(pool) < MIN_STATEMENT_ROWS:
        raise CheckSkipped("fewer than 3 statement lines with a description")
    # the largest lines carry the business; keep them in statement order
    keep = sorted(pool, key=lambda t: (-t[0], t[1], t[2].index))[:MAX_STATEMENT_ROWS]
    by_doc: dict[str, list[Row]] = {}
    for _, doc_id, row in sorted(keep, key=lambda t: (t[1], t[2].index)):
        by_doc.setdefault(doc_id, []).append(row)
    originals = [d for d in _usable(ctx, "bank_statement") if d.id in by_doc]
    shown = [_with_rows(d, "transactions", by_doc[d.id]) for d in originals]
    return Prepared(
        shown=[profile, *shown],
        originals=[profile, *originals],
        fields={profile.id: ["principal_activity"], **{d.id: [] for d in shown}},
        tables={profile.id: [], **{d.id: ["transactions"] for d in shown}},
        columns={d.id: {"transactions": ["description", "debit", "credit"]} for d in shown},
        anchor=Anchor(frozenset(by_doc), table="transactions", column="description"),
        declared=(profile.id, "principal_activity"),
    )


def _text(row: Row, column: str) -> str:
    cell = row.cells.get(column)
    return ((cell.raw or cell.value or "").strip()) if cell is not None else ""


# ------------------------------------------------------------------------------ the questions

GOODS = Question(
    key="goods_fit",
    rule="sem_goods_fit",
    kind="goods_industry_mismatch",
    task=GOODS_TASK,
    prepare=prepare_goods,
    description=(
        "Model-derived: invoice lines the company issued belong to a different industry than its "
        "declared principal activity. Quotes are verified against the extracted data."
    ),
)
INCOME = Question(
    key="income_fit",
    rule="sem_income_fit",
    kind="income_occupation_implausible",
    task=INCOME_TASK,
    prepare=prepare_income,
    description=(
        "Model-derived: the declared amount is implausible for the job the source-of-funds "
        "description names. Quotes are verified against the extracted data."
    ),
    cite_field=True,
)
COHERENCE = Question(
    key="coherence_fit",
    rule="sem_coherence_fit",
    kind="activity_incoherence",
    task=COHERENCE_TASK,
    prepare=prepare_coherence,
    description=(
        "Model-derived: counterparties on the bank statement point to a different line of business "
        "than the declared principal activity. Quotes are verified against the extracted data."
    ),
)
QUESTIONS = {q.key: q for q in (GOODS, INCOME, COHERENCE)}


# --------------------------------------------------------------------------- ask and verify


def _on_anchor(c: VerifiedCitation, anchor: Anchor, docs: dict[str, Document]) -> bool:
    if c.document_id not in anchor.doc_ids:
        return False
    if anchor.field is not None:
        return c.field == anchor.field
    if c.table != anchor.table or anchor.column is None:
        return False
    if c.column is not None:
        return c.column == anchor.column
    # a row citation without a column counts when the quote is in the evidence column
    doc = docs.get(c.document_id)
    row = (
        next((r for r in doc.tables[anchor.table].rows if r.index == c.row), None) if doc else None
    )
    cell = row.cells.get(anchor.column) if row is not None else None
    if cell is None:
        return False
    quote = norm(c.quote)
    return any(quote in norm(t) for t in (cell.value, cell.raw) if t)


def _declared_citation(prep: Prepared, kind: str) -> VerifiedCitation | None:
    """The declared side as a verified citation, built by the shared verifier from the real cell."""
    doc_id, name = prep.declared
    doc = next((d for d in prep.originals if d.id == doc_id), None)
    cell = doc.fields.get(name) if doc is not None else None
    text = (cell.raw or cell.value or "") if cell is not None else ""
    if doc is None or not text.strip():
        return None
    reply = {
        "claims": [
            {
                "kind": kind,
                "severity": "low",
                "statement": "declared side",
                "citations": [{"document_id": doc_id, "field": name, "quote": text[:120]}],
            }
        ]
    }
    got = verify_claims(reply, [doc], kinds=[kind])
    return got.claims[0].citations[0] if got.claims else None


def ask(ctx: CaseContext, q: Question) -> FitResult:
    """One call for question ``q``. Raises ``CheckSkipped`` when the case lacks the fields and
    ``SemanticNotRun`` when the model call or its output fails."""
    prep = q.prepare(ctx)
    rendered = [
        (
            d.id,
            render_document(
                d,
                fields=prep.fields.get(d.id),
                tables=prep.tables.get(d.id),
                columns=prep.columns.get(d.id),
                max_rows=MAX_STATEMENT_ROWS,
            ),
        )
        for d in prep.shown
    ]
    try:
        body = wrap_documents(rendered)
    except ValueError:
        raise SemanticNotRun("document text could not be wrapped safely") from None
    reply = call_model(
        ctx,
        name=q.key,
        system=system_prompt(q.task),
        user=f"{USER_PREFIX}\n\n{body}",
        schema_name="FitAnswer",
        json_schema=fit_schema(q.kind, cite_field=q.cite_field),
        stage="crossdoc",
        cache_extra=f"fit{FIT_VERSION}",
    )
    answer = reply.get("answer")
    if answer not in ANSWERS:
        raise SemanticNotRun("malformed answer")
    got = verify_claims(
        reply,
        prep.shown,
        kinds=[q.kind],
        max_claims=min(ctx.settings.semantic_max_claims, MAX_CLAIMS),
    )
    final = Verification(
        proposed=got.proposed, dropped=Counter(got.dropped), severity_capped=got.severity_capped
    )
    result = FitResult(q.key, answer, final, originals=prep.originals)
    if answer != FLAGGED_ANSWER:
        if got.claims:
            final.dropped[ANSWER_PREFIX + answer] += len(got.claims)
    elif got.proposed == 0:
        final.proposed = 1
        final.dropped[NO_CLAIM] += 1
    else:
        by_id = {d.id: d for d in prep.shown}
        declared = _declared_citation(prep, q.kind)
        for c in got.claims:
            if not any(_on_anchor(x, prep.anchor, by_id) for x in c.citations):
                final.dropped[WRONG_ANCHOR] += 1
                continue
            cites = sorted(c.citations, key=lambda x: not _on_anchor(x, prep.anchor, by_id))
            if declared is not None and declared.locator() not in {x.locator() for x in cites}:
                cites.append(declared)
            c = dataclasses.replace(c, citations=tuple(cites))  # evidence first, declared last
            final.claims.append(c)
            result.claims.append(c)
    ctx.semantic_tally.setdefault(q.rule, Verification()).merge(final)
    return result


def run_question(ctx: CaseContext, q: Question) -> list[Flag]:
    """The body of a registered check: ask, then turn the verified claims into flags."""
    return ask(ctx, q).flags(q.rule)


__all__ = [
    "ANSWERS",
    "COHERENCE",
    "FitResult",
    "GOODS",
    "INCOME",
    "QUESTIONS",
    "Question",
    "ask",
    "fit_schema",
    "run_question",
]
