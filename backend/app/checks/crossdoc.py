"""Cross-document rules (docs/checks-financial.md section 7, plan 4.4) and ``file_reuse``.

All rules here are ``stage="crossdoc"``. They compare only equal currencies; a bundle that lacks
the documents a rule needs lowers coverage (the rule is skipped), it never passes silently.
Amounts are ``Decimal``; "today" is ``ctx.today`` (not needed by these rules).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext, natural_key
from app.core.models import Cell, Document, Flag, Region, Row, Table
from app.core.money import approx_eq, fmt_money, quantize
from app.core.textnorm import company_disc, company_key, company_match, uen_disc

ZERO = Decimal("0")
USABLE = ("ok", "partial")

X_CASH_REL_TOL = Decimal("0.05")
X_REV_LOW_RATIO = Decimal("0.60")
X_REV_HIGH_RATIO = Decimal("1.50")
X_REV_MIN_COVER_DAYS = 30
X_RECEIPT_EARLY_DAYS = 7
X_RECEIPT_AFTER_PAID_DAYS = 7
X_RECEIPT_UNPAID_WINDOW_DAYS = 90
X_RECEIPT_FEE_ALLOW = Decimal("35.00")
X_CONTRACT_POST_END_DAYS = 90

NON_TRADE_C4 = frozenset({"financing_proceeds", "owner_transfer", "inter_company", "refund"})
LOW_C4 = ("trade_receipt", "customer_payment")
_EMPTY = Cell(value=None)


# ---------------------------------------------------------------- helpers


def _tol(ctx: CaseContext) -> Decimal:
    return ctx.settings.money_tolerance


def _clip(text: str | None, n: int = 60) -> str:
    out = " ".join((text or "").split())
    return out if len(out) <= n else out[: n - 3] + "..."


def _unverified(cells: Mapping[str, Cell]) -> str:
    names = sorted(n for n, c in cells.items() if c.verified is False or c.alt_raw is not None)
    return "; unverified reading: " + ", ".join(names) if names else ""


def _lab(box: Region | None, label: str | None = None) -> Region | None:
    if box is None:
        return None
    return box.model_copy(update={"label": label}) if label else box


def _regions(cells: list[Cell], limit: int = 4) -> list[Region]:
    return [c.box for c in cells if c.box is not None][:limit]


def _docs(ctx: CaseContext, *types: str) -> list[Document]:
    return [d for d in ctx.docs(*types) if d.status in USABLE]


def _c(doc: Document, name: str) -> Cell:
    return doc.fields.get(name, _EMPTY)


def _rc(row: Row, name: str) -> Cell:
    return row.cells.get(name, _EMPTY)


def _norm_uen(value: str | None) -> str:
    return "".join((value or "").split()).upper()


def _cur(doc: Document) -> str:
    return _c(doc, "currency").text().upper()


def _same_cur(a: str, b: str) -> bool:
    return not a or not b or a == b


def _party_is(uen_a: str, name_a: str, uen_b: str, name_b: str) -> bool:
    if uen_a and uen_b:
        return uen_a == uen_b
    return company_match(name_a, name_b)


def _rows(doc: Document, table: str) -> list[Row]:
    t: Table | None = doc.tables.get(table)
    return list(t.rows) if t is not None else []


def _row_dates(doc: Document) -> list[date | None]:
    """Per-row date; an empty date cell inherits the previous row's date."""
    out: list[date | None] = []
    prev: date | None = None
    for r in _rows(doc, "transactions"):
        cell = _rc(r, "date")
        d = cell.date_()
        if d is not None:
            prev = d
        elif cell.value is None and cell.raw is None:
            d = prev
        out.append(d)
    return out


def _period(doc: Document) -> tuple[date, date] | None:
    s, e = _c(doc, "period_start").date_(), _c(doc, "period_end").date_()
    return (s, e) if s is not None and e is not None and s <= e else None


def _merge(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1] + timedelta(days=1):
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _fs_sum(doc: Document, table: str, key: str, col: str = "current") -> Decimal | None:
    vals = [
        _rc(r, col).money()
        for r in _rows(doc, table)
        if _rc(r, "line").text() == key and _rc(r, col).money() is not None
    ]
    return sum(vals, ZERO) if vals else None  # type: ignore[arg-type]


def _fs_row(doc: Document, table: str, key: str) -> Row | None:
    for r in _rows(doc, table):
        if _rc(r, "line").text() == key and _rc(r, "current").money() is not None:
            return r
    return None


def _capped(items: list[Flag], cap: int) -> list[Flag]:
    if len(items) <= cap:
        return items
    kept = list(items[:cap])
    kept[-1] = kept[-1].model_copy(
        update={"evidence": f"{kept[-1].evidence}; +{len(items) - cap} more"}
    )
    return kept


# ---------------------------------------------------------------- x_entity


@dataclass
class _Claim:
    doc: Document
    name_field: str
    uen_field: str

    @property
    def name_cell(self) -> Cell:
        return _c(self.doc, self.name_field)

    @property
    def uen_cell(self) -> Cell:
        return _c(self.doc, self.uen_field)

    @property
    def name(self) -> str:
        return self.name_cell.text()

    @property
    def uen(self) -> str:
        return _norm_uen(self.uen_cell.value)


_CLAIM_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "bank_statement": (("account_holder", "uen"),),
    "invoice": (("supplier_name", "supplier_uen"), ("buyer_name", "buyer_uen")),
    "financial_statement": (("company_name", "uen"),),
    "company_profile": (("company_name", "uen"),),
    "certificate_of_incorporation": (("company_name", "uen"),),
    "ownership_chart": (("company_name", "uen"),),
    "shareholder_register": (("company_name", "uen"),),
    "source_of_funds_declaration": (("company_name", "uen"),),
    "contract": (("seller_name", "seller_uen"), ("buyer_name", "buyer_uen")),
    "purchase_order": (("buyer_name", "buyer_uen"), ("supplier_name", "supplier_uen")),
}
_APPLICANT_DOCS = (
    "company_profile",
    "certificate_of_incorporation",
    "ownership_chart",
    "shareholder_register",
    "source_of_funds_declaration",
)


def _ids_text(ids: list[str]) -> str:
    if len(ids) <= 1:
        return "".join(ids)
    return ", ".join(ids[:-1]) + " and " + ids[-1]


def _first_docs(claims: list[_Claim], limit: int = 4) -> list[Document]:
    seen: dict[str, Document] = {}
    for c in claims:
        seen.setdefault(c.doc.id, c.doc)
    return [seen[k] for k in sorted(seen, key=natural_key)][:limit]


@check(
    rule="x_entity",
    stage="crossdoc",
    applies_to={
        "bank_statement",
        "invoice",
        "financial_statement",
        "contract",
        "purchase_order",
        "company_profile",
        "certificate_of_incorporation",
        "ownership_chart",
        "shareholder_register",
        "source_of_funds_declaration",
    },
    severity="high",
    description="Company names and UENs agree across the bundle and with the applicant.",
    tamper="Documents of different companies spliced into one bundle, or the applicant's UEN under another name.",
)
def x_entity(ctx: CaseContext) -> list[Flag]:
    claims: list[_Claim] = []
    for doc in _docs(ctx, *_CLAIM_FIELDS):
        for nf, uf in _CLAIM_FIELDS[doc.doc_type]:
            claim = _Claim(doc, nf, uf)
            if claim.name or claim.uen:
                claims.append(claim)
    app_name, app_uen = (ctx.applicant.name or ""), _norm_uen(ctx.applicant.uen)
    has_applicant = bool(app_name or app_uen)
    part_b = len(claims) >= 2
    a_docs = _docs(ctx, *_APPLICANT_DOCS, "contract", "purchase_order") if has_applicant else []
    if not part_b and not a_docs:
        raise CheckSkipped("fewer than two named parties and nothing to compare with the applicant")
    flags: list[Flag] = []

    # (B) conflicts inside the bundle -------------------------------------------------
    by_uen: dict[str, list[_Claim]] = defaultdict(list)
    for cl in claims:
        if cl.uen:
            by_uen[cl.uen].append(cl)
    for uen in sorted(by_uen):
        clusters: list[list[_Claim]] = []
        for cl in by_uen[uen]:
            if not cl.name:
                continue
            for cluster in clusters:
                if any(company_match(cl.name, m.name) for m in cluster):
                    cluster.append(cl)
                    break
            else:
                clusters.append([cl])
        if len(clusters) < 2:
            continue
        reps = [cluster[0] for cluster in clusters]
        shown = reps[:3]
        parts = [f'"{_clip(r.name)}" on {r.doc.id}' for r in shown]
        flags.append(
            make_flag(
                "x_entity",
                docs=_first_docs(reps),
                field=reps[0].uen_field,
                region=_lab(reps[0].uen_cell.box, "uen"),
                extra_regions=_regions([r.uen_cell for r in reps[1:]]),
                discriminator=uen_disc(uen)[:40],
                evidence=f"UEN {_clip(uen)} is {_ids_text(parts)}"
                + _unverified({"uen": reps[0].uen_cell}),
                explanation="The same UEN appears with different company names in this bundle.",
            )
        )
    by_name: dict[str, list[_Claim]] = defaultdict(list)
    for cl in claims:
        key = company_key(cl.name) if cl.name else ""
        if key and cl.uen:
            by_name[key].append(cl)
    for key in sorted(by_name):
        per_uen: dict[str, _Claim] = {}
        for cl in by_name[key]:
            per_uen.setdefault(cl.uen, cl)
        if len(per_uen) < 2:
            continue
        reps = [per_uen[u] for u in sorted(per_uen)]
        parts = [f"{_clip(r.uen)} on {r.doc.id}" for r in reps[:3]]
        flags.append(
            make_flag(
                "x_entity",
                docs=_first_docs(reps),
                field=reps[0].name_field,
                region=_lab(reps[0].name_cell.box, "name"),
                extra_regions=_regions([r.name_cell for r in reps[1:]]),
                discriminator=company_disc(reps[0].name)[:40],
                evidence=f'name "{_clip(key)}" has UEN {_ids_text(parts)}'
                + _unverified({"name": reps[0].name_cell}),
                explanation="The same company name appears with different UENs in this bundle.",
            )
        )

    # (A) the applicant against documents without a dedicated rule ---------------------
    for doc in a_docs:
        if doc.doc_type in ("contract", "purchase_order"):
            pairs = _CLAIM_FIELDS[doc.doc_type]
            parties = [(_c(doc, n), _norm_uen(_c(doc, u).value), n) for n, u in pairs]
            if not any(c.text() or u for c, u, _ in parties):
                continue
            if any(_party_is(app_uen, app_name, u, c.text()) for c, u, _ in parties):
                continue
            first, second = parties[0], parties[1]
            flags.append(
                make_flag(
                    "x_entity",
                    docs=[doc],
                    field=first[2],
                    region=_lab(first[0].box, first[2]),
                    extra_regions=_regions([second[0]]),
                    severity="medium",
                    discriminator="applicant_absent",
                    evidence=(
                        f'{doc.id} parties "{_clip(first[0].text())}" and "{_clip(second[0].text())}" '
                        f'do not include applicant "{_clip(app_name or app_uen)}"'
                    ),
                    explanation="Neither party on this document is the applicant.",
                )
            )
            continue
        name_c, uen_c = _c(doc, "company_name"), _c(doc, "uen")
        d_uen = _norm_uen(uen_c.value)
        if d_uen and app_uen:
            if d_uen != app_uen:
                flags.append(
                    make_flag(
                        "x_entity",
                        docs=[doc],
                        field="uen",
                        region=_lab(uen_c.box, "uen"),
                        discriminator=uen_disc(d_uen)[:40],
                        evidence=f"{doc.id} uen {_clip(d_uen)} vs applicant uen {_clip(app_uen)}"
                        + _unverified({"uen": uen_c}),
                        explanation="This document names a different company from the applicant.",
                    )
                )
        elif name_c.text() and app_name and not company_match(name_c.text(), app_name):
            flags.append(
                make_flag(
                    "x_entity",
                    docs=[doc],
                    field="company_name",
                    region=_lab(name_c.box, "company_name"),
                    discriminator=company_disc(name_c.text())[:40],
                    evidence=f'{doc.id} company_name "{_clip(name_c.text())}" vs applicant '
                    f'"{_clip(app_name)}"' + _unverified({"company_name": name_c}),
                    explanation="This document names a different company from the applicant.",
                )
            )
    return flags


# ---------------------------------------------------------------- x_cash_at_year_end


def _balance_at(doc: Document, when: date) -> tuple[Decimal, Cell] | None:
    """Printed balance after the last row dated on or before ``when``; else the opening balance."""
    dates = _row_dates(doc)
    chosen: Row | None = None
    for r, d in zip(_rows(doc, "transactions"), dates, strict=False):
        if d is not None and d <= when and _rc(r, "balance").money() is not None:
            chosen = r
    if chosen is not None:
        cell = _rc(chosen, "balance")
        return cell.money(), cell  # type: ignore[return-value]
    opening = _c(doc, "opening_balance")
    value = opening.money()
    return (value, opening) if value is not None else None


@check(
    rule="x_cash_at_year_end",
    stage="crossdoc",
    applies_to={"financial_statement"},
    requires={"bank_statement"},
    severity="medium",
    use_cases={"aml", "loan"},
    description="Cash on the balance sheet matches the bank balance on the financial year-end date within 5%.",
    tamper="Inflated cash on the balance sheet.",
)
def x_cash_at_year_end(ctx: CaseContext) -> list[Flag]:
    tol = _tol(ctx)
    statements = _docs(ctx, "bank_statement")
    flags: list[Flag] = []
    evaluated = 0
    reason = "no financial statement with year-end cash"
    for fs in _docs(ctx, "financial_statement"):
        end = _c(fs, "period_end").date_()
        cash = _fs_sum(fs, "balance_sheet", "cash_and_equivalents")
        cash_row = _fs_row(fs, "balance_sheet", "cash_and_equivalents")
        if end is None or cash is None:
            continue
        reason = f"no statement covers {end.isoformat()}"
        per_account: dict[str, tuple[Document, Decimal, Cell]] = {}
        for st in statements:
            period = _period(st)
            if period is None or not (period[0] <= end <= period[1]):
                continue
            if not _same_cur(_cur(fs), _cur(st)):
                reason = "currency mismatch"
                continue
            account = _c(st, "account_number").text() or st.id
            if account in per_account:
                continue
            got = _balance_at(st, end)
            if got is not None:
                per_account[account] = (st, got[0], got[1])
        if not per_account:
            continue
        evaluated += 1
        bank = sum((v[1] for v in per_account.values()), ZERO)
        tol_x = max(tol, X_CASH_REL_TOL * max(abs(cash), abs(bank)))
        if abs(bank - cash) <= tol_x:
            continue
        used = [per_account[a] for a in sorted(per_account)]
        ids = [u[0].id for u in used]
        n = len(used)
        short = bank < cash
        cash_cell = _rc(cash_row, "current") if cash_row is not None else _EMPTY
        flags.append(
            make_flag(
                "x_cash_at_year_end",
                docs=[fs, *[u[0] for u in used]],
                table="balance_sheet",
                row=cash_row.index if cash_row is not None else None,
                region=_lab(cash_cell.box, "cash"),
                extra_regions=_regions([u[2] for u in used]),
                discriminator="short" if short else "excess",
                evidence=(
                    f"FS cash at {end.isoformat()}: {fmt_money(cash)}; bank balance at "
                    f"{end.isoformat()}: {fmt_money(bank)} ({n} account{'s' if n != 1 else ''}, "
                    f"statement{'s' if n != 1 else ''} {', '.join(ids)}); diff "
                    f"{fmt_money(abs(bank - cash))}; tolerance {fmt_money(tol_x)}"
                    + _unverified({"cash": cash_cell})
                ),
                explanation=(
                    "Cash on the balance sheet is higher than the balance in the supplied bank "
                    "statements on that date; accounts not supplied would explain this."
                    if short
                    else "The bank statements show more cash on the year-end date than the balance sheet."
                ),
            )
        )
    if evaluated == 0:
        raise CheckSkipped(reason)
    return flags


# ---------------------------------------------------------------- x_revenue_vs_credits


@check(
    rule="x_revenue_vs_credits",
    stage="crossdoc",
    applies_to={"financial_statement"},
    requires={"bank_statement"},
    severity="medium",
    use_cases={"aml", "loan"},
    description="Reported revenue, prorated to the days the statements cover, is supported by trade deposits in the bank statements.",
    tamper="Inflated revenue on the financial statements.",
)
def x_revenue_vs_credits(ctx: CaseContext) -> list[Flag]:
    statements = _docs(ctx, "bank_statement")
    flags: list[Flag] = []
    evaluated = 0
    for fs in _docs(ctx, "financial_statement"):
        start, end = _c(fs, "period_start").date_(), _c(fs, "period_end").date_()
        revenue = _fs_sum(fs, "income_statement", "revenue")
        if start is None or end is None or start > end or revenue is None:
            continue
        period_days = (end - start).days + 1
        usable = [
            (st, p)
            for st in statements
            if (p := _period(st)) is not None and _same_cur(_cur(fs), _cur(st))
        ]
        clipped = [(max(p[0], start), min(p[1], end)) for _, p in usable]
        covered = _merge([iv for iv in clipped if iv[0] <= iv[1]])
        covered_days = sum((e - s).days + 1 for s, e in covered)
        if covered_days < X_REV_MIN_COVER_DAYS:
            continue
        expected = revenue * covered_days / period_days
        if expected <= ZERO:
            continue
        evaluated += 1

        buckets: dict[str, Decimal] = defaultdict(lambda: ZERO)
        seen: dict[tuple, str] = {}
        covering = [st for st, p in usable if max(p[0], start) <= min(p[1], end)]
        for st, _ in usable:
            dates = _row_dates(st)
            account = _c(st, "account_number").text()
            for r, d in zip(_rows(st, "transactions"), dates, strict=False):
                credit = _rc(r, "credit").money()
                if credit is None or credit <= ZERO or d is None:
                    continue
                if not any(s <= d <= e for s, e in covered):
                    continue
                key = (account, d, _rc(r, "description").text(), credit, _rc(r, "balance").value)
                if seen.setdefault(key, st.id) != st.id:
                    continue  # the same statement line uploaded twice (overlapping statements)
                label = r.annotations["c4"].label if "c4" in r.annotations else "other"
                bucket = label if label in NON_TRADE_C4 or label in LOW_C4 else "unclassified"
                buckets[bucket] += credit
        qualifying_low = sum((buckets[k] for k in LOW_C4), ZERO)
        unclassified = buckets["unclassified"]
        qualifying = qualifying_low + unclassified
        excluded = {k: v for k, v in sorted(buckets.items()) if k in NON_TRADE_C4 and v}
        ratio = qualifying / expected
        if X_REV_LOW_RATIO <= ratio <= X_REV_HIGH_RATIO:
            continue
        shortfall = ratio < X_REV_LOW_RATIO
        rev_row = _fs_row(fs, "income_statement", "revenue")
        rev_cell = _rc(rev_row, "current") if rev_row is not None else _EMPTY
        trade = ", ".join(f"{k} {fmt_money(buckets[k])}" for k in LOW_C4)
        excl_text = ", ".join(f"{k} {fmt_money(v)}" for k, v in excluded.items())
        flags.append(
            make_flag(
                "x_revenue_vs_credits",
                docs=[fs, *covering],
                table="income_statement",
                row=rev_row.index if rev_row is not None else None,
                region=_lab(rev_cell.box, "revenue"),
                extra_regions=_regions([_c(st, "closing_balance") for st in covering]),
                severity=None if shortfall else "info",
                discriminator="shortfall" if shortfall else "excess",
                evidence=(
                    f"FS revenue {fmt_money(revenue)} for {start.isoformat()}..{end.isoformat()} "
                    f"({period_days} d); statements cover {covered_days} d; prorated "
                    f"{fmt_money(expected)}; qualifying credits {fmt_money(qualifying)} ({trade}, "
                    f"unclassified {fmt_money(unclassified)}); excluded "
                    f"{fmt_money(sum(excluded.values(), ZERO))}"
                    + (f" ({excl_text})" if excl_text else "")
                    + f"; ratio {fmt_money(ratio)} "
                    + (
                        f"< {fmt_money(X_REV_LOW_RATIO)}"
                        if shortfall
                        else f"> {fmt_money(X_REV_HIGH_RATIO)}"
                    )
                    + _unverified({"revenue": rev_cell})
                ),
                explanation=(
                    f"Bank deposits that look like sales support only {quantize(ratio * 100, 0):f}% of "
                    "the revenue reported for the days the statements cover; sales banked in "
                    "accounts not supplied would explain this."
                    if shortfall
                    else f"Deposits that look like sales are {quantize(ratio * 100, 0):f}% of the "
                    "revenue reported for the days the statements cover; a note about possible "
                    "undeclared income, not evidence against the applicant."
                ),
            )
        )
    if evaluated == 0:
        raise CheckSkipped(
            f"no financial statement with revenue and at least {X_REV_MIN_COVER_DAYS} days of "
            "bank statement coverage"
        )
    return flags


# ---------------------------------------------------------------- x_invoice_vs_receipts


@dataclass
class _Credit:
    day: date
    account: str
    doc: Document
    row: int
    amount: Decimal
    counterparty: str
    currency: str
    cell: Cell


def _credits(statements: list[Document]) -> list[_Credit]:
    out: list[_Credit] = []
    for st in statements:
        dates = _row_dates(st)
        account = _c(st, "account_number").text()
        for i, (r, d) in enumerate(zip(_rows(st, "transactions"), dates, strict=False)):
            amount = _rc(r, "credit").money()
            if amount is None or amount <= ZERO or d is None:
                continue
            cp = r.annotations["counterparty"].label if "counterparty" in r.annotations else ""
            out.append(_Credit(d, account, st, i, amount, cp, _cur(st), _rc(r, "credit")))
    out.sort(key=lambda c: (c.day, c.account, natural_key(c.doc.id), c.row))
    return out


def _buyer_key(inv: Document) -> str:
    return _norm_uen(_c(inv, "buyer_uen").value) or company_key(_c(inv, "buyer_name").text())


@check(
    rule="x_invoice_vs_receipts",
    stage="crossdoc",
    applies_to={"invoice"},
    requires={"bank_statement"},
    severity="medium",
    use_cases={"aml", "loan"},
    description="Every invoice marked paid has a matching deposit in the bank statements inside its payment window.",
    tamper="Fake or unpaid invoices presented as paid.",
)
def x_invoice_vs_receipts(ctx: CaseContext) -> list[Flag]:
    tol, fee = _tol(ctx), X_RECEIPT_FEE_ALLOW
    statements = [s for s in _docs(ctx, "bank_statement") if _period(s) is not None]
    credits = _credits(statements)

    paid = [
        d
        for d in _docs(ctx, "invoice")
        if _c(d, "payment_status").value == "paid" and _c(d, "total").money() is not None
    ]
    paid.sort(key=lambda d: (_c(d, "invoice_date").date_() or date.min, natural_key(d.id)))

    windows: dict[str, tuple[date, date]] = {}
    evaluable: list[Document] = []
    uncovered: list[tuple[Document, str]] = []
    for inv in paid:
        inv_date = _c(inv, "invoice_date").date_()
        if inv_date is None:
            uncovered.append((inv, "no invoice date"))
            continue
        paid_date = _c(inv, "paid_date").date_()
        w_start = inv_date - timedelta(days=X_RECEIPT_EARLY_DAYS)
        w_end = (
            paid_date + timedelta(days=X_RECEIPT_AFTER_PAID_DAYS)
            if paid_date is not None
            else inv_date + timedelta(days=X_RECEIPT_UNPAID_WINDOW_DAYS)
        )
        same = [s for s in statements if _same_cur(_cur(inv), _cur(s))]
        if not same:
            uncovered.append((inv, "currency"))
            continue
        merged = _merge([p for s in same if (p := _period(s)) is not None])
        if not any(a <= w_start and w_end <= b for a, b in merged):
            uncovered.append((inv, "outside statement coverage"))
            continue
        windows[inv.id] = (w_start, w_end)
        evaluable.append(inv)
    if not evaluable:
        raise CheckSkipped("no statements cover invoice payment windows")

    used: set[int] = set()
    matched: set[str] = set()

    def total_of(inv: Document) -> Decimal:
        return _c(inv, "total").money()  # type: ignore[return-value]

    def in_window(inv: Document, k: int) -> bool:
        lo, hi = windows[inv.id]
        return lo <= credits[k].day <= hi and _same_cur(_cur(inv), credits[k].currency)

    def covers(target: Decimal, got: Decimal) -> bool:
        return -tol <= target - got <= fee

    # 2. one credit per invoice
    for inv in evaluable:
        buyer = _c(inv, "buyer_name").text()
        cands = [
            k
            for k in range(len(credits))
            if k not in used and in_window(inv, k) and covers(total_of(inv), credits[k].amount)
        ]
        if cands:
            best = min(
                cands,
                key=lambda k: (0 if company_match(credits[k].counterparty, buyer) else 1, k),
            )
            used.add(best)
            matched.add(inv.id)

    # 3. batch: one credit settles 2 to 4 unmatched invoices of one buyer
    for k in range(len(credits)):
        if k in used:
            continue
        buckets: dict[str, list[Document]] = defaultdict(list)
        for inv in evaluable:
            if inv.id not in matched and in_window(inv, k) and _buyer_key(inv):
                buckets[_buyer_key(inv)].append(inv)
        for key in sorted(buckets):
            group = buckets[key][:12]
            hit = next(
                (
                    combo
                    for size in (2, 3, 4)
                    for combo in combinations(group, size)
                    if covers(sum((total_of(i) for i in combo), ZERO), credits[k].amount)
                ),
                None,
            )
            if hit:
                used.add(k)
                matched.update(i.id for i in hit)
                break

    # 4. instalments: 2 or 3 credits from the buyer
    for inv in evaluable:
        if inv.id in matched:
            continue
        buyer = _c(inv, "buyer_name").text()
        total = total_of(inv)
        pool = [
            k
            for k in range(len(credits))
            if k not in used
            and in_window(inv, k)
            and credits[k].counterparty
            and company_match(credits[k].counterparty, buyer)
            and credits[k].amount > total * Decimal("0.01")
        ][:15]
        found = next(
            (
                combo
                for size in (2, 3)
                for combo in combinations(pool, size)
                if covers(total, sum((credits[k].amount for k in combo), ZERO))
            ),
            None,
        )
        if found:
            used.update(found)
            matched.add(inv.id)

    # 5. flags
    flags: list[Flag] = []
    for inv in evaluable:
        if inv.id in matched:
            continue
        total = total_of(inv)
        lo, hi = windows[inv.id]
        tot_cell = _c(inv, "total")
        paid_date = _c(inv, "paid_date").date_()
        in_win = [c for k, c in enumerate(credits) if in_window(inv, k)]
        nearest = min(in_win, key=lambda c: abs(total - c.amount)) if in_win else None
        n_st = len([s for s in statements if _same_cur(_cur(inv), _cur(s))])
        flags.append(
            make_flag(
                "x_invoice_vs_receipts",
                docs=[inv],
                field="total",
                region=_lab(tot_cell.box, "total"),
                evidence=(
                    f"invoice {_clip(_c(inv, 'invoice_number').text())} total {fmt_money(total)} "
                    f"marked paid"
                    + (f" (paid_date {paid_date.isoformat()})" if paid_date else "")
                    + f"; no credit between {lo.isoformat()} and {hi.isoformat()} of "
                    f"{fmt_money(total)} (tolerance -{fmt_money(tol)}/+{fmt_money(fee)}) in "
                    f"{n_st} statement{'s' if n_st != 1 else ''}"
                    + (
                        f"; nearest {fmt_money(nearest.amount)} on {nearest.day.isoformat()}"
                        if nearest
                        else ""
                    )
                    + _unverified({"total": tot_cell})
                ),
                explanation="This invoice is marked paid, but no matching deposit appears in the bank statements.",
            )
        )
    if uncovered:
        shown = ", ".join(
            f"{_clip(_c(i, 'invoice_number').text(), 30)} ({why})" for i, why in uncovered[:10]
        )
        flags.append(
            make_flag(
                "x_invoice_vs_receipts",
                docs=[i for i, _ in uncovered[:5]],
                severity="info",
                discriminator="uncovered",
                evidence=f"{len(uncovered)} paid invoice(s) not checked against deposits: {shown}",
                explanation="Some paid invoices fall outside the period of the supplied bank statements, so they were not checked.",
            )
        )
    return flags


# ---------------------------------------------------------------- x_invoice_vs_contract


def _ref(cell: Cell) -> str:
    return "".join((cell.value or "").split()).upper()


@check(
    rule="x_invoice_vs_contract",
    stage="crossdoc",
    applies_to={"contract", "purchase_order"},
    requires={"invoice"},
    severity="medium",
    use_cases={"aml", "loan"},
    description="Invoices agree with the contract or purchase order they cite on parties, amount and dates.",
    tamper="Forged or altered contract or purchase order values, or invoices that bill more than the contract.",
)
def x_invoice_vs_contract(ctx: CaseContext) -> list[Flag]:
    tol = _tol(ctx)
    contracts = {
        _ref(_c(d, "contract_number")): d
        for d in _docs(ctx, "contract")
        if _ref(_c(d, "contract_number"))
    }
    pos = {
        _ref(_c(d, "po_number")): d
        for d in _docs(ctx, "purchase_order")
        if _ref(_c(d, "po_number"))
    }
    invoices = _docs(ctx, "invoice")
    flags: list[Flag] = []
    missing: list[tuple[Document, str]] = []
    cited_any = False
    by_contract: dict[str, list[Document]] = defaultdict(list)

    for inv in invoices:
        c_ref, p_ref = _ref(_c(inv, "contract_reference")), _ref(_c(inv, "po_reference"))
        targets: list[tuple[str, Document]] = []
        for ref, table, label in ((c_ref, contracts, "contract"), (p_ref, pos, "PO")):
            if not ref:
                continue
            cited_any = True
            if ref in table:
                targets.append((label, table[ref]))
            else:
                missing.append((inv, f"{label} {_clip(ref, 40)}"))
        inv_no = _clip(_c(inv, "invoice_number").text())
        sub_c, tot_c = _c(inv, "subtotal"), _c(inv, "total")
        subtotal, total = sub_c.money(), tot_c.money()
        inv_date = _c(inv, "invoice_date").date_()
        s_uen, b_uen = (
            _norm_uen(_c(inv, "supplier_uen").value),
            _norm_uen(_c(inv, "buyer_uen").value),
        )
        s_name, b_name = _c(inv, "supplier_name").text(), _c(inv, "buyer_name").text()

        for label, other in targets:
            is_contract = label == "contract"
            ref_text = _ref(_c(other, "contract_number" if is_contract else "po_number"))
            seller_f, seller_u = (
                ("seller_name", "seller_uen") if is_contract else ("supplier_name", "supplier_uen")
            )
            o_seller, o_buyer = _c(other, seller_f), _c(other, "buyer_name")
            o_s_uen, o_b_uen = (
                _norm_uen(_c(other, seller_u).value),
                _norm_uen(_c(other, "buyer_uen").value),
            )
            # party
            for role, inv_uen, inv_name, inv_cell_name, o_uen, o_cell in (
                ("supplier", s_uen, s_name, "supplier_name", o_s_uen, o_seller),
                ("buyer", b_uen, b_name, "buyer_name", o_b_uen, o_buyer),
            ):
                if not (inv_name or inv_uen) or not (o_cell.text() or o_uen):
                    continue
                comparable = bool((inv_uen and o_uen) or (inv_name and o_cell.text()))
                if comparable and not _party_is(inv_uen, inv_name, o_uen, o_cell.text()):
                    role_label = "seller" if (role == "supplier" and is_contract) else role
                    flags.append(
                        make_flag(
                            "x_invoice_vs_contract",
                            docs=[inv, other],
                            field=inv_cell_name,
                            region=_lab(_c(inv, inv_cell_name).box, inv_cell_name),
                            extra_regions=_regions([o_cell]),
                            discriminator="party",
                            evidence=(
                                f'invoice {role} "{_clip(inv_name)}" vs {label} {role_label} '
                                f'"{_clip(o_cell.text())}"'
                            ),
                            explanation="The parties on this invoice differ from the contract.",
                        )
                    )
            # amount
            same_cur = _same_cur(_cur(inv), _cur(other))
            if is_contract:
                limit_c = _c(other, "contract_value")
                limit = limit_c.money()
                milestone_ok = any(
                    subtotal is not None
                    and _rc(r, "amount").money() is not None
                    and approx_eq(subtotal, _rc(r, "amount").money(), tol)  # type: ignore[arg-type]
                    for r in _rows(other, "milestones")
                )
                if same_cur and limit is not None and (subtotal is not None or total is not None):
                    ok = (
                        milestone_ok
                        or (subtotal is not None and subtotal <= limit + tol)
                        or (total is not None and total <= limit + tol)
                    )
                    if not ok:
                        field_name = "total" if total is not None else "subtotal"
                        flags.append(
                            make_flag(
                                "x_invoice_vs_contract",
                                docs=[inv, other],
                                field=field_name,
                                region=_lab(_c(inv, field_name).box, field_name),
                                extra_regions=_regions([limit_c]),
                                discriminator="amount",
                                evidence=(
                                    f"invoice {inv_no} subtotal "
                                    f"{fmt_money(subtotal) if subtotal is not None else 'n/a'} / total "
                                    f"{fmt_money(total) if total is not None else 'n/a'} exceeds "
                                    f"contract {_clip(ref_text, 40)} value {fmt_money(limit)}"
                                    + _unverified({"total": tot_c, "contract_value": limit_c})
                                ),
                                explanation="This invoice bills more than the contract or purchase order it cites.",
                            )
                        )
                by_contract[ref_text].append(inv)
                start = _c(other, "effective_date").date_() or _c(other, "contract_date").date_()
                end = _c(other, "end_date").date_()
                start_f, end_f = (
                    ("effective_date" if _c(other, "effective_date").date_() else "contract_date"),
                    "end_date",
                )
            else:
                sub_limit_c, tot_limit_c = _c(other, "subtotal"), _c(other, "total")
                sub_limit, tot_limit = sub_limit_c.money(), tot_limit_c.money()
                if (
                    same_cur
                    and (sub_limit is not None or tot_limit is not None)
                    and (subtotal is not None or total is not None)
                ):
                    ok = (
                        subtotal is not None
                        and sub_limit is not None
                        and subtotal <= sub_limit + tol
                    ) or (total is not None and tot_limit is not None and total <= tot_limit + tol)
                    if not ok:
                        field_name = "total" if total is not None else "subtotal"
                        flags.append(
                            make_flag(
                                "x_invoice_vs_contract",
                                docs=[inv, other],
                                field=field_name,
                                region=_lab(_c(inv, field_name).box, field_name),
                                extra_regions=_regions([tot_limit_c, sub_limit_c]),
                                discriminator="amount",
                                evidence=(
                                    f"invoice {inv_no} subtotal "
                                    f"{fmt_money(subtotal) if subtotal is not None else 'n/a'} / total "
                                    f"{fmt_money(total) if total is not None else 'n/a'} exceeds PO "
                                    f"{_clip(ref_text, 40)} subtotal "
                                    f"{fmt_money(sub_limit) if sub_limit is not None else 'n/a'} / total "
                                    f"{fmt_money(tot_limit) if tot_limit is not None else 'n/a'}"
                                    + _unverified({"total": tot_c, "po_total": tot_limit_c})
                                ),
                                explanation="This invoice bills more than the contract or purchase order it cites.",
                            )
                        )
                start, end = _c(other, "po_date").date_(), None
                start_f, end_f = "po_date", ""
            # date
            if inv_date is not None:
                inv_date_c = _c(inv, "invoice_date")
                if start is not None and inv_date < start:
                    flags.append(
                        make_flag(
                            "x_invoice_vs_contract",
                            docs=[inv, other],
                            field="invoice_date",
                            region=_lab(inv_date_c.box, "invoice_date"),
                            extra_regions=_regions([_c(other, start_f)]),
                            discriminator="date",
                            evidence=(
                                f"invoice_date {inv_date.isoformat()} before {label} {start_f} "
                                f"{start.isoformat()}"
                            ),
                            explanation="The invoice date is outside the contract term.",
                        )
                    )
                elif end is not None and inv_date > end + timedelta(days=X_CONTRACT_POST_END_DAYS):
                    flags.append(
                        make_flag(
                            "x_invoice_vs_contract",
                            docs=[inv, other],
                            field="invoice_date",
                            region=_lab(inv_date_c.box, "invoice_date"),
                            extra_regions=_regions([_c(other, end_f)]),
                            discriminator="date",
                            evidence=(
                                f"invoice_date {inv_date.isoformat()} is more than "
                                f"{X_CONTRACT_POST_END_DAYS} days after {label} {end_f} {end.isoformat()}"
                            ),
                            explanation="The invoice date is outside the contract term.",
                        )
                    )

    # cumulative over-billing per contract: flag only the invoice that first crosses the value
    for ref_text in sorted(by_contract):
        contract = contracts[ref_text]
        limit = _c(contract, "contract_value").money()
        if limit is None:
            continue
        ordered = sorted(
            {i.id: i for i in by_contract[ref_text]}.values(),
            key=lambda i: (_c(i, "invoice_date").date_() or date.min, natural_key(i.id)),
        )
        running = ZERO
        for position, inv in enumerate(ordered, start=1):
            if not _same_cur(_cur(inv), _cur(contract)):
                continue
            subtotal, total = _c(inv, "subtotal").money(), _c(inv, "total").money()
            amount = subtotal if subtotal is not None else total
            if amount is None:
                continue
            alone_ok = (subtotal is not None and subtotal <= limit + tol) or (
                total is not None and total <= limit + tol
            )
            if (
                subtotal is not None
                and not (subtotal <= limit + tol)
                and total is not None
                and total <= limit + tol
            ):
                amount = total
            running += amount
            if running > limit + tol:
                if alone_ok:
                    field_name = "subtotal" if subtotal is not None else "total"
                    flags.append(
                        make_flag(
                            "x_invoice_vs_contract",
                            docs=[inv, contract],
                            field=field_name,
                            region=_lab(_c(inv, field_name).box, field_name),
                            extra_regions=_regions([_c(contract, "contract_value")]),
                            discriminator="cumulative",
                            evidence=(
                                f"running total of {position} invoices citing "
                                f"{_clip(ref_text, 40)} is {fmt_money(running)} > "
                                f"{fmt_money(limit)} at {_clip(_c(inv, 'invoice_number').text())}"
                            ),
                            explanation="This invoice bills more than the contract or purchase order it cites.",
                        )
                    )
                break

    if missing:
        shown = ", ".join(
            f"{_clip(_c(i, 'invoice_number').text(), 30)} cites {why}" for i, why in missing[:10]
        )
        flags.append(
            make_flag(
                "x_invoice_vs_contract",
                docs=[i for i, _ in missing[:5]],
                severity="info",
                discriminator="not_supplied",
                evidence=f"cited document not supplied: {shown}",
                explanation="An invoice cites a contract or purchase order that is not in the bundle, so it was not compared.",
            )
        )
    if not cited_any:
        raise CheckSkipped("no invoice cites a contract or purchase order")
    return flags


# ---------------------------------------------------------------- file_reuse


@check(
    rule="file_reuse",
    stage="crossdoc",
    applies_to={"*"},
    severity="medium",
    description="The same file is not submitted twice in this case or in another case.",
    tamper="The same statement, invoice or ID file reused across applicants.",
)
def file_reuse(ctx: CaseContext) -> list[Flag]:
    files = list(ctx.case.files)
    if not files:
        raise CheckSkipped("no files")
    docs_by_file: dict[str, list[Document]] = defaultdict(list)
    for d in ctx.case.documents:
        docs_by_file[d.file_id].append(d)
    flags: list[Flag] = []

    first_by_sha: dict[str, str] = {}
    for f in files:
        if f.sha256 in first_by_sha:
            original = first_by_sha[f.sha256]
            docs = docs_by_file.get(f.id) or docs_by_file.get(original) or []
            flags.append(
                make_flag(
                    "file_reuse",
                    docs=docs,
                    severity="low",
                    discriminator=f.sha256[:12],
                    evidence=f"file {f.id} (sha256 {f.sha256[:12]}...) is identical to {original} in this case",
                    explanation="This exact file was already submitted in this case.",
                )
            )
        else:
            first_by_sha[f.sha256] = f.id

    applicant_uen = _norm_uen(ctx.applicant.uen)
    for f in files:
        try:
            hits = ctx.fingerprints.lookup("file_sha256", f.sha256, exclude_case_id=ctx.case.id)
        except Exception:  # noqa: BLE001 - an unavailable store must not break the check
            continue
        seen: set[str] = set()
        for hit in hits:
            if hit.case_id in seen:
                continue
            seen.add(hit.case_id)
            hit_uen = _norm_uen(str(hit.meta.get("applicant_uen") or ""))
            if hit_uen and hit_uen != applicant_uen:
                severity, who = "medium", " for a different applicant"
            elif hit_uen:
                severity, who = "info", " for the same applicant"
            else:
                severity, who = "info", ""
            flags.append(
                make_flag(
                    "file_reuse",
                    docs=docs_by_file.get(f.id, []),
                    severity=severity,  # type: ignore[arg-type]
                    discriminator="store:" + hit.case_id,
                    evidence=(
                        f"file {f.id} (sha256 {f.sha256[:12]}...) seen in case {hit.case_id} on "
                        f"{hit.created_at.date().isoformat()}{who}"
                    ),
                    explanation=f"This exact file was already submitted in another case ({hit.case_id}).",
                )
            )
    return flags
