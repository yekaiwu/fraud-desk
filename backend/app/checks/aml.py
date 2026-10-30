"""AML rules (docs/checks-kyc-aml.md sections 4.8 to 4.10).

``x_sof_support``: a declared source-of-funds amount against the credits that can support it.
``x_remitter_vs_buyer``: the payer of an invoice is its customer.
``bs_pass_through``: credits forwarded to other entities within days (in-case entity graph).

Statements, invoices and declarations are written by the person being checked: names and
descriptions are data. No wall clock: ``ctx.today`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.assemble import logical_docs
from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext, natural_key
from app.core.models import Cell, Document, Flag, Region, Row
from app.core.money import fmt_money
from app.graph import APPLICANT_KEY, EntityGraph, counterparty_of, doc_number, uen_in
from app.names import (
    cell_region,
    entity_key,
    has_legal_token,
    labelled,
    mask_id,
    match_company,
    match_person,
    normalise_id,
    safe_text,
    sorted_rows,
    unverified_note,
)

# --------------------------------------------------------------------------------------
# Constants (starting guesses, recalibrated from the eval; plan 4 intro, 9)
# --------------------------------------------------------------------------------------

SOF_TOL_PCT = Decimal("0.01")
SOF_HIGH_SHARE = Decimal("0.25")
PT_WINDOW_DAYS = 5
PT_TOL_PCT = Decimal("0.03")
PT_MIN_AMOUNT = Decimal("5000.00")
PT_MIN_PAIRS = 3
PT_MIN_ENTITIES = 3
PT_MIN_SHARE = 0.20
RV_PAID_WINDOW_DAYS = 5
RV_DEFAULT_TERMS_DAYS = 30
RV_AFTER_DUE_DAYS = 30
# integration-loan: with 1% parts and no span limit, any 4 of ~30 credits in a 60 day window sum to
# an invoice total within 1.00 by chance (a clean loan bundle flagged high). Instalments are real
# payments of the invoice: each at least a fifth of it, all within a month, none a loan drawdown,
# owner transfer or refund.
RV_MIN_PART_SHARE = Decimal("0.20")
RV_MAX_SPAN_DAYS = 35
RV_NON_TRADE_C4 = frozenset({"financing_proceeds", "owner_transfer", "refund"})
RV_MAX_PARTS = 4
RV_SEARCH_CAP = 40
MAX_EXTRA_REGIONS = 6
USABLE = ("ok", "partial")

SOF_COMPANY_ORIGINS = {
    "sales_proceeds": {"trade_receipt", "customer_payment"},
    "loan": {"financing_proceeds"},
}


def _docs(ctx: CaseContext, *types: str) -> list[Document]:
    # bank statements that arrived page by page are read as one statement; pieces that could not
    # be assembled are left out (the statement counts as not provided, and is reported as such)
    return [d for d in logical_docs(ctx, *types, whole_only=True) if d.status in USABLE]


def _cell(doc: Document, name: str) -> Cell:
    return doc.fields.get(name) or Cell(value=None)


def _currency(doc: Document) -> str:
    return _cell(doc, "currency").text().strip().upper() or "SGD"


def _cap(regions: list[Region | None], limit: int = MAX_EXTRA_REGIONS) -> list[Region]:
    return [r for r in regions if r is not None][:limit]


@dataclass
class _Credit:
    doc: Document
    row: Row
    when: date
    amount: Decimal
    cell: Cell

    @property
    def sort_key(self) -> tuple[date, int, int]:
        return (self.when, doc_number(self.doc.id), self.row.index)

    @property
    def uid(self) -> tuple[str, int]:
        return (self.doc.id, self.row.index)


def _credits(statements: list[Document]) -> list[_Credit]:
    out: list[_Credit] = []
    for doc in statements:
        for row in sorted_rows(doc.tables.get("transactions")):
            when = row.cells["date"].date_()
            amount = row.cells["credit"].money()
            if when is None or amount is None or amount <= 0:
                continue
            if row.cells["debit"].money() is not None:
                continue
            out.append(_Credit(doc, row, when, amount, row.cells["credit"]))
    return sorted(out, key=lambda c: c.sort_key)


def _confident(row: Row, name: str, min_prob: float) -> str | None:
    ann = row.annotations.get(name)
    return ann.label if ann is not None and ann.probability >= min_prob else None


# --------------------------------------------------------------------------------------
# x_sof_support
# --------------------------------------------------------------------------------------


def _name_matches(counterparty: str | None, wanted: str, *, company: bool) -> bool:
    if not counterparty:
        return False
    verdict = (
        match_company(counterparty, wanted).verdict
        if company
        else match_person(counterparty, wanted).verdict
    )
    return verdict in ("same", "probable")


@check(
    rule="x_sof_support",
    applies_to={"source_of_funds_declaration"},
    requires={"source_of_funds_declaration", "bank_statement"},
    stage="crossdoc",
    severity="high",
    use_cases={"aml"},
    description="Declared source-of-funds amount is supported by credits",
    tamper="false source-of-funds evidence",
)
def x_sof_support(ctx: CaseContext) -> list[Flag]:
    declarations = _docs(ctx, "source_of_funds_declaration")
    flags: list[Flag] = []
    evaluated = 0
    skip_reasons: list[str] = []
    for decl in declarations:
        result = _sof_one(ctx, decl)
        if isinstance(result, str):
            skip_reasons.append(result)
        else:
            evaluated += 1
            if result is not None:
                flags.append(result)
    if evaluated == 0:
        raise CheckSkipped(
            skip_reasons[0] if skip_reasons else "no declaration with a readable amount"
        )
    return flags


def _sof_unsupported_account(
    decl: Document, amount_cell: Cell, declared: Decimal, receiving: str
) -> Flag:
    account = safe_text(mask_id(receiving), 24)
    return make_flag(
        "x_sof_support",
        docs=[decl],
        field="declared_amount",
        region=labelled(cell_region(amount_cell), "declared_amount"),
        severity="medium",
        evidence=(
            f"declared {fmt_money(declared)} into account {account}; no statement for that "
            "account was supplied, so the claim could not be tested"
        )
        + unverified_note(amount_cell),
        explanation=(
            f"The {fmt_money(declared)} is declared as received in an account for which no "
            "statement was supplied, so its source cannot be supported."
        ),
    )


def _sof_one(ctx: CaseContext, decl: Document) -> Flag | str | None:  # noqa: PLR0912, PLR0915
    """A flag, None when the claim is supported, or a skip reason (str)."""
    amount_cell = _cell(decl, "declared_amount")
    declared = amount_cell.money()
    if declared is None or declared <= 0:
        return "declared amount not readable"
    currency = _currency(decl)
    statements = [s for s in _docs(ctx, "bank_statement") if _currency(s) == currency]
    if not statements:
        return "currency mismatch"
    receiving = normalise_id(_cell(decl, "receiving_account").value)
    if receiving:
        named = [
            s for s in statements if normalise_id(_cell(s, "account_number").value) == receiving
        ]
        # a statement whose account number could not be read might be the one: judge it as well
        unreadable = [s for s in statements if not normalise_id(_cell(s, "account_number").value)]
        statements = named or unreadable
        if not statements:
            # declaring the funds into an account that was not uploaded must not switch the check
            # off: the claim cannot be tested, which is itself the finding
            return _sof_unsupported_account(decl, amount_cell, declared, receiving)
    origin = _cell(decl, "origin_category").text().strip().lower() or "other"
    wanted = _cell(decl, "counterparty_name").text().strip()
    if origin not in SOF_COMPANY_ORIGINS and not wanted:
        return "origin not testable without a named counterparty"
    company_matcher = origin in SOF_COMPANY_ORIGINS or has_legal_token(wanted)

    declaration_date = _cell(decl, "declaration_date").date_()
    start = _cell(decl, "period_start").date_()
    end = _cell(decl, "period_end").date_()
    if start is None:
        starts = [d for d in (_cell(s, "period_start").date_() for s in statements) if d]
        start = min(starts) if starts else None
    if end is None:
        end = declaration_date
    min_prob = ctx.settings.classifier_min_prob
    supporting: list[_Credit] = []
    unclassified: list[_Credit] = []
    uses_c4 = origin in SOF_COMPANY_ORIGINS
    for credit in _credits(statements):
        if start is not None and credit.when < start:
            continue
        if end is not None and credit.when > end:
            continue
        if declaration_date is not None and credit.when > declaration_date:
            continue
        c4 = _confident(credit.row, "c4", min_prob)
        who = counterparty_of(credit.row)
        if uses_c4:
            if c4 is None:
                unclassified.append(credit)
                continue
            if c4 in SOF_COMPANY_ORIGINS[origin] and (
                not wanted or _name_matches(who, wanted, company=True)
            ):
                supporting.append(credit)
        elif c4 != "refund" and _name_matches(who, wanted, company=company_matcher):
            supporting.append(credit)
    supported = sum((c.amount for c in supporting), Decimal(0))
    unsupported = max(Decimal(0), declared - supported)
    tolerance = max(ctx.settings.money_tolerance, SOF_TOL_PCT * declared)
    if unsupported <= tolerance:
        return None
    severity = "high" if unsupported >= SOF_HIGH_SHARE * declared else "medium"
    window = f"{start.isoformat() if start else '?'}..{end.isoformat() if end else '?'}"
    evidence = (
        f"declared {fmt_money(declared)} ({safe_text(origin, 30)}, {window}); "
        f"{len(supporting)} supporting credit{'s' if len(supporting) != 1 else ''} total "
        f"{fmt_money(supported)}; unsupported {fmt_money(unsupported)} "
        f"({float(unsupported / declared) * 100:.1f}%)"
    )
    if unclassified:
        evidence += (
            f"; {len(unclassified)} credit{'s' if len(unclassified) != 1 else ''} unclassified "
            f"({fmt_money(sum((c.amount for c in unclassified), Decimal(0)))})"
        )
    evidence += unverified_note(amount_cell)
    largest = sorted(supporting, key=lambda c: (-c.amount, c.sort_key))[:5]
    docs = [decl, *{c.doc.id: c.doc for c in supporting}.values()]
    return make_flag(
        "x_sof_support",
        docs=docs,
        field="declared_amount",
        region=labelled(cell_region(amount_cell), "declared_amount"),
        extra_regions=_cap([cell_region(c.cell, c.row) for c in largest], 5),
        severity=severity,  # type: ignore[arg-type]
        evidence=evidence,
        explanation=(
            f"Only {fmt_money(supported)} of the declared {fmt_money(declared)} is supported by "
            f"credits ({fmt_money(unsupported)} unsupported)."
        ),
    )


# --------------------------------------------------------------------------------------
# x_remitter_vs_buyer
# --------------------------------------------------------------------------------------


@dataclass
class _Invoice:
    doc: Document
    number: str
    when: date
    total: Decimal
    currency: str
    buyer: str
    buyer_uen: str
    due: date | None
    paid: date | None


def _applicant_party(ctx: CaseContext) -> tuple[str, str]:
    return (ctx.applicant.name or "").strip(), normalise_id(ctx.applicant.uen)


def _is_applicant(name: str | None, uen: str | None, app_name: str, app_uen: str) -> bool:
    if app_uen and normalise_id(uen) == app_uen:
        return True
    return bool(app_name) and match_company(name, app_name).verdict in ("same", "probable")


def _window(inv: _Invoice) -> tuple[date, date]:
    if inv.paid is not None:
        d = timedelta(days=RV_PAID_WINDOW_DAYS)
        return inv.paid - d, inv.paid + d
    due = inv.due or inv.when + timedelta(days=RV_DEFAULT_TERMS_DAYS)
    return inv.when, due + timedelta(days=RV_AFTER_DUE_DAYS)


def _find_set(pool: list[_Credit], total: Decimal, tol: Decimal) -> list[_Credit] | None:
    """First set (in (date, row) order) of 2 to 4 credits, each above 1% of the total, whose sum is
    within ``tol`` of the total. Search capped at the 40 largest credits; deterministic."""
    eligible = [
        c
        for c in pool
        if c.amount >= RV_MIN_PART_SHARE * total
        and (
            c.row.annotations.get("c4") is None
            or c.row.annotations["c4"].label not in RV_NON_TRADE_C4
        )
    ]
    eligible = sorted(eligible, key=lambda c: (-c.amount, c.sort_key))[:RV_SEARCH_CAP]
    eligible.sort(key=lambda c: c.sort_key)
    if len(eligible) < 2 or sum((c.amount for c in eligible), Decimal(0)) < total - tol:
        return None
    found: list[_Credit] | None = None

    def walk(start: int, chosen: list[_Credit], running: Decimal, size: int) -> bool:
        nonlocal found
        if len(chosen) == size:
            span = (max(c.when for c in chosen) - min(c.when for c in chosen)).days
            if abs(running - total) <= tol and span <= RV_MAX_SPAN_DAYS:
                found = list(chosen)
                return True
            return False
        for i in range(start, len(eligible)):
            c = eligible[i]
            if running + c.amount > total + tol:
                continue
            chosen.append(c)
            if walk(i + 1, chosen, running + c.amount, size):
                return True
            chosen.pop()
        return False

    for size in range(2, RV_MAX_PARTS + 1):
        if walk(0, [], Decimal(0), size):
            return found
    return None


@check(
    rule="x_remitter_vs_buyer",
    applies_to={"invoice"},
    requires={"invoice", "bank_statement"},
    stage="crossdoc",
    severity="high",
    use_cases={"aml", "loan"},
    description="The payer of an invoice is its customer",
    tamper="invoice customer differs from remitter",
)
def x_remitter_vs_buyer(ctx: CaseContext) -> list[Flag]:  # noqa: PLR0912, PLR0915
    app_name, app_uen = _applicant_party(ctx)
    if not app_name and not app_uen:
        raise CheckSkipped("applicant unknown")
    invoices: list[_Invoice] = []
    for doc in _docs(ctx, "invoice"):
        f = doc.fields
        if not _is_applicant(
            f["supplier_name"].text(), f["supplier_uen"].text(), app_name, app_uen
        ):
            continue
        total, when = f["total"].money(), f["invoice_date"].date_()
        buyer, buyer_uen = f["buyer_name"].text().strip(), normalise_id(f["buyer_uen"].text())
        if total is None or total <= 0 or when is None or (not buyer and not buyer_uen):
            continue
        invoices.append(
            _Invoice(
                doc,
                f["invoice_number"].text(),
                when,
                total,
                _currency(doc),
                buyer,
                buyer_uen,
                f["due_date"].date_(),
                f["paid_date"].date_(),
            )
        )
    if not invoices:
        raise CheckSkipped("no invoice issued by the applicant")
    statements = _docs(ctx, "bank_statement")
    by_currency: dict[str, list[_Credit]] = {}
    for cur in {_currency(s) for s in statements}:
        by_currency[cur] = _credits([s for s in statements if _currency(s) == cur])
    if not any(inv.currency in by_currency for inv in invoices):
        raise CheckSkipped("currency mismatch")
    tol = ctx.settings.money_tolerance
    used: set[tuple[str, int]] = set()
    flags: list[Flag] = []
    invoices.sort(key=lambda i: (i.when, natural_key(i.doc.id)))
    for inv in invoices:
        pool = [c for c in by_currency.get(inv.currency, []) if c.uid not in used]
        lo, hi = _window(inv)
        window = [c for c in pool if lo <= c.when <= hi]
        reference = inv.paid or inv.due or inv.when
        singles = [c for c in window if abs(c.amount - inv.total) <= tol]
        candidate: list[_Credit] | None = None
        if singles:
            candidate = [
                min(
                    singles,
                    key=lambda c: (
                        abs((c.when - reference).days),
                        doc_number(c.doc.id),
                        c.row.index,
                    ),
                )
            ]
        else:
            candidate = _find_set(window, inv.total, tol)
        wide = False
        if not candidate and inv.paid is not None:
            # instalments: the first part may have arrived well before the paid date. Looked for
            # in the wider window (invoice date to paid date + 5 days) and kept only when it is a
            # mixed set (some parts from the buyer, a real share from someone else), so a lucky
            # subset sum among unrelated credits cannot raise a flag.
            wide_lo, wide_hi = inv.when, inv.paid + timedelta(days=RV_PAID_WINDOW_DAYS)
            candidate = _find_set([c for c in pool if wide_lo <= c.when <= wide_hi], inv.total, tol)
            wide = bool(candidate)
        if not candidate:
            continue
        if not wide:
            used.update(c.uid for c in candidate)
        remitters = [counterparty_of(c.row) for c in candidate]
        if all(r is None for r in remitters):
            continue  # nothing to compare: an extraction gap, not a finding
        verdicts = [_matches_buyer(c, r, inv) for c, r in zip(candidate, remitters, strict=True)]
        mixed = False
        if any(v is True for v in verdicts):
            # an instalment set where the buyer paid some parts: still worth a look when another
            # payer clearly settled a real share of the invoice (a medium flag, not a high one:
            # a group company or an agent can pay one instalment honestly)
            foreign = [
                i
                for i, (v, c) in enumerate(zip(verdicts, candidate, strict=True))
                if v is False and c.amount >= RV_MIN_PART_SHARE * inv.total
            ]
            if not foreign:
                continue
            mixed = True
        if wide and not mixed:
            continue
        used.update(c.uid for c in candidate)
        if all(v is None for v in verdicts):
            continue
        primary_i = foreign[0] if mixed else next(i for i, v in enumerate(verdicts) if v is False)
        primary = candidate[primary_i]
        remitter = remitters[primary_i] or ""
        own = _is_applicant(
            remitter, uen_in(primary.row.cells["description"].text()), app_name, app_uen
        )
        due_text = f" due {inv.due.isoformat()}" if inv.due else ""
        inv_label = safe_text(inv.number, 30) or inv.doc.id
        head = (
            f"invoice {inv_label} to {safe_text(inv.buyer or inv.buyer_uen)}, "
            f"{fmt_money(inv.total)}{due_text}; "
        )
        if len(candidate) == 1:
            body = (
                f"credit {primary.when.isoformat()} of {fmt_money(primary.amount)} from "
                f"'{safe_text(remitter)}' (row {primary.row.index + 1}, {primary.doc.id})"
            )
        else:
            parts = "; ".join(
                f"{c.when.isoformat()} {fmt_money(c.amount)} from '{safe_text(r or '?')}' "
                f"(row {c.row.index + 1}, {c.doc.id})"
                for c, r in zip(candidate, remitters, strict=True)
            )
            body = f"paid by {len(candidate)} credits: {parts}"
        evidence = head + body + ("; paid by the applicant's own account" if own else "")
        if mixed:
            evidence += "; other parts came from the customer"
        buyer_cell = inv.doc.fields["buyer_name"]
        extra = [cell_region(buyer_cell)]
        extra += [
            cell_region(c.row.cells["description"], c.row) for c in candidate if c is not primary
        ]
        flags.append(
            make_flag(
                "x_remitter_vs_buyer",
                docs=[inv.doc, *{c.doc.id: c.doc for c in candidate}.values()],
                table="transactions",
                row=primary.row.index,
                row_doc=primary.doc,
                region=labelled(
                    cell_region(primary.row.cells["description"], primary.row), "remitter"
                ),
                extra_regions=_cap(extra),
                evidence=evidence + unverified_note(buyer_cell),
                explanation=(
                    f"Invoice {inv_label} to {safe_text(inv.buyer or inv.buyer_uen)} was "
                    + ("partly " if mixed else "")
                    + f"paid by {safe_text(remitter)}"
                    + (" (the applicant's own account)" if own else ", not by the customer")
                    + "."
                ),
                discriminator=entity_key(remitter) or None,
                severity="medium" if mixed else None,
            )
        )
    return flags


def _matches_buyer(credit: _Credit, remitter: str | None, inv: _Invoice) -> bool | None:
    """True if the remitter is the buyer, False if not, None when it cannot be judged."""
    if remitter is None:
        return None
    desc_uen = uen_in(credit.row.cells["description"].text())
    if desc_uen and inv.buyer_uen:
        return desc_uen == inv.buyer_uen
    if not inv.buyer:
        return None
    verdict = match_company(remitter, inv.buyer, allow_truncation=True).verdict
    if verdict == "indeterminate":
        return None
    return verdict in ("same", "probable")


# --------------------------------------------------------------------------------------
# bs_pass_through
# --------------------------------------------------------------------------------------


@check(
    rule="bs_pass_through",
    applies_to={"bank_statement"},
    severity="medium",
    use_cases={"aml", "loan"},
    description="Credits forwarded to other entities within days",
    tamper="funds routed through multiple companies",
)
def bs_pass_through(ctx: CaseContext) -> list[Flag]:
    statements = _docs(ctx, "bank_statement")
    if len({_currency(s) for s in statements}) > 1:
        raise CheckSkipped("currency mismatch")
    graph = EntityGraph.build(ctx)
    if not graph.flows():
        raise CheckSkipped("no transactions with a counterparty")
    pt = graph.pass_through(
        window_days=PT_WINDOW_DAYS,
        tol_pct=PT_TOL_PCT,
        min_amount=PT_MIN_AMOUNT,
        min_pairs=PT_MIN_PAIRS,
        min_entities=PT_MIN_ENTITIES,
        tolerance_abs=ctx.settings.money_tolerance,
    )
    if pt is None or pt.share < PT_MIN_SHARE:
        return []

    def cell_of(document_id: str, row_index: int, column: str) -> tuple[Cell | None, Row | None]:
        doc = ctx.doc(document_id)
        for row in doc.tables["transactions"].rows:
            if row.index == row_index:
                return row.cells[column], row
        return None, None

    first = pt.hops[0]
    cell, row = cell_of(first.credit.document_id, first.credit.row, "credit")
    extra: list[Region | None] = []
    ordered: list[tuple[str, int, str]] = []
    for i, hop in enumerate(pt.hops):
        if i > 0:
            ordered.append((hop.credit.document_id, hop.credit.row, "credit"))
        ordered.append((hop.debit.document_id, hop.debit.row, "debit"))
    for document_id, row_index, column in ordered[:MAX_EXTRA_REGIONS]:
        c, r = cell_of(document_id, row_index, column)
        extra.append(cell_region(c, r))
    docs_involved = sorted(
        {h.credit.document_id for h in pt.hops} | {h.debit.document_id for h in pt.hops},
        key=natural_key,
    )
    name = graph.display
    first_text = (
        f"{first.credit.date.isoformat()} +{fmt_money(first.credit.amount)} "
        f"{safe_text(name(first.credit.src), 40)} -> {first.debit.date.isoformat()} "
        f"-{fmt_money(first.debit.amount)} {safe_text(name(first.debit.dst), 40)}"
    )
    circular = (
        "; circular: " + ", ".join(safe_text(name(k), 40) for k in pt.circular)
        if pt.circular
        else ""
    )
    return [
        make_flag(
            "bs_pass_through",
            docs=docs_involved,
            table="transactions",
            row=first.credit.row,
            region=labelled(cell_region(cell, row), "credit"),
            extra_regions=_cap(extra),
            evidence=(
                f"{len(pt.hops)} credits ({fmt_money(pt.credit_total)}, {pt.share * 100:.1f}% of "
                f"credits) each followed within {PT_WINDOW_DAYS} days by a near-equal debit to "
                f"another entity; {len(pt.entities)} entities; first: {first_text}{circular}"
                + unverified_note(cell)
            ),
            explanation=(
                f"{len(pt.hops)} credits totalling {fmt_money(pt.credit_total)} were passed on to "
                f"other entities within {PT_WINDOW_DAYS} days."
            ),
        )
    ]


__all__ = [
    "APPLICANT_KEY",
    "PT_MIN_AMOUNT",
    "PT_MIN_ENTITIES",
    "PT_MIN_PAIRS",
    "PT_MIN_SHARE",
    "PT_TOL_PCT",
    "PT_WINDOW_DAYS",
    "SOF_HIGH_SHARE",
    "SOF_TOL_PCT",
    "bs_pass_through",
    "x_remitter_vs_buyer",
    "x_sof_support",
]
