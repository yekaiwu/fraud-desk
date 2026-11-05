"""Bank statement rules (docs/checks-financial.md section 4).

Foundation wrote ``bs_running_balance`` (the walking-skeleton check); rules-bank extends this file
with the other eight rules of plan 4.1 and keeps that function's behaviour.

Conventions (checks-financial.md 3.3): tolerance is ``ctx.settings.money_tolerance`` and inclusive,
``Flag.row`` is 0-based while evidence prints 1-based rows, names and descriptions are untrusted
text (whitespace collapsed, 60 characters), a document with a missing input is not evaluated and
the rule raises ``CheckSkipped`` only when no document could be evaluated. Today is ``ctx.today``.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from fractions import Fraction

from rapidfuzz import fuzz

from app.assemble import AssembledStatement, logical_docs
from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext, natural_key
from app.core.dates import add_days
from app.core.models import Cell, Document, Flag, ReadingNote, Region, Row, Table, truncate
from app.core.money import approx_eq, fmt_money, quantize
from app.core.textnorm import company_disc, company_keys, company_match, normalise_uen, uen_disc

ZERO = Decimal("0")

# Eval-tunable constants (checks-financial.md 3.6): read at call time so an eval can sweep them.
BS_DATE_GRACE_DAYS = 0
BS_DUP_MIN_AMOUNT = Decimal("20.00")
RN_ROUND_MODULUS = 1000
RN_MIN_CREDITS = 5
RN_MIN_ROUND = 3
RN_MIN_SHARE = 0.50
RN_MIN_UPLIFT = 0.30
RN_MIN_BASELINE_CREDITS = 10
# Count test (second trigger): the number of round credits is far above what the other statements
# of the account lead one to expect. Poisson tail, set before looking at any tampered data; the
# share uplift guards against a statement that is simply busier than the others.
RN_COUNT_ALPHA = 0.005
RN_COUNT_MIN_UPLIFT = 0.15
RN_COUNT_LAMBDA_FLOOR = 0.5
BENFORD_MIN_N = 100
BENFORD_MAD_MIN = 0.015
BENFORD_CHI2_CRIT = 20.09

TEXT_LIMIT = 60  # untrusted text in evidence (convention 11)
DEFAULT_CAP = 5  # flags per rule per document (convention 7)
MAX_EXTRA_REGIONS = 4


def _tol(ctx: CaseContext) -> Decimal:
    return ctx.settings.money_tolerance


def _cell_money(cell: Cell | None) -> Decimal | None:
    return cell.money() if cell is not None else None


def _unverified(cells: Mapping[str, Cell]) -> str:
    """'' if every cell has ``verified is not False`` and no ``alt_raw``; else a suffix naming them."""
    names = sorted(n for n, c in cells.items() if c.verified is False or c.alt_raw is not None)
    if not names:
        return ""
    shown = ", ".join(names[:5])
    return (
        "; unverified reading: " + shown + (f" (+{len(names) - 5} more)" if len(names) > 5 else "")
    )


def _labelled(box: Region | None, label: str) -> Region | None:
    return box.model_copy(update={"label": label}) if box is not None else None


def _clean(text: str | None, limit: int = TEXT_LIMIT) -> str:
    """Untrusted text for evidence: printable characters only, whitespace collapsed, truncated."""
    printable = "".join(ch if ch.isprintable() else " " for ch in (text or ""))
    return truncate(" ".join(printable.split()), limit)


def _statements(ctx: CaseContext) -> list[Document]:
    """Bank statements that were actually read, in document-id order. Pages of one statement that
    arrived as separate images and were put back together (``app.assemble``) appear as one
    statement; pages that could not be assembled appear as they are, so the rules that only need a
    page (running balance, dates, duplicates, holder) still look at them."""
    return [d for d in logical_docs(ctx, "bank_statement") if d.status in ("ok", "partial")]


def _whole_statements(ctx: CaseContext) -> list[Document]:
    """As ``_statements`` without the pieces that could not be assembled. Use it for every rule
    that compares a statement with its own totals, page numbers or neighbours: a piece compared
    with a whole is a false alarm, and those pages are reported as not reviewed instead."""
    docs = logical_docs(ctx, "bank_statement", whole_only=True)
    return [d for d in docs if d.status in ("ok", "partial")]


def _field(doc: Document, name: str) -> Cell | None:
    return doc.fields.get(name)


def _fmoney(doc: Document, name: str) -> Decimal | None:
    return _cell_money(_field(doc, name))


def _fdate(doc: Document, name: str) -> date | None:
    cell = _field(doc, name)
    return cell.date_() if cell is not None else None


def _ftext(doc: Document, name: str) -> str:
    cell = _field(doc, name)
    return cell.text().strip() if cell is not None else ""


def _box(cell: Cell | None, label: str) -> Region | None:
    return _labelled(cell.box, label) if cell is not None else None


def _extras(*regions: Region | None) -> list[Region]:
    return [r for r in regions if r is not None][:MAX_EXTRA_REGIONS]


def _row_money(row: Row, column: str) -> Decimal | None:
    return _cell_money(row.cells.get(column))


def _unread_raw(row: Row, column: str) -> str | None:
    """The text of an amount cell that has text but no single amount (a figure typed over another leaves both
    numbers in the text layer), else None."""
    cell = row.cells.get(column)
    raw = (cell.raw or "").strip() if cell is not None else ""
    return raw if raw and _cell_money(cell) is None else None


def _acct_key(doc: Document) -> str:
    """Account number without spaces and hyphens, upper-case ('' when absent)."""
    return "".join(_ftext(doc, "account_number").replace("-", "").split()).upper()


def _currency(doc: Document) -> str:
    return _ftext(doc, "currency").upper()


def _emit(items: list[dict], cap: int = DEFAULT_CAP) -> list[Flag]:
    """Build flags from ``make_flag`` keyword dicts; when capped the last evidence ends ``+N more``."""
    shown = list(items[:cap])
    hidden = len(items) - len(shown)
    if hidden > 0:
        shown[-1] = {**shown[-1], "evidence": shown[-1]["evidence"] + f"; +{hidden} more"}
    return [make_flag(**kw) for kw in shown]


def _pct(value: Fraction) -> int:
    """Whole percent, half up."""
    return int(value * 100 + Fraction(1, 2))


def _frac(const: float) -> Fraction:
    return Fraction(str(const))


@check(
    rule="bs_running_balance",
    applies_to={"bank_statement"},
    severity="high",
    description="Each row's printed balance equals the previous balance plus that row's credit or debit.",
    tamper="A single edited transaction amount or an edited balance.",
)
def bs_running_balance(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    evaluated_docs: list[Document] = []
    skipped_docs: list[Document] = []
    whole = _whole_statements(ctx)
    for doc in _statements(ctx):
        opening, source = _opening_of(doc, whole)
        flag = _check_statement(ctx, doc, opening, source)
        if flag is _NOT_EVALUATED:
            skipped_docs.append(doc)
            continue
        evaluated += 1
        evaluated_docs.append(doc)
        if flag is not None:
            flags.append(flag)
    if evaluated == 0:
        raise CheckSkipped("no statement with an opening balance and a balance column")
    flags += _missing_balance_flags(skipped_docs, evaluated_docs)
    for doc in evaluated_docs:
        if doc in whole and _fmoney(doc, "opening_balance") is None:
            flags += _no_opening_flags(doc, whole, "bs_running_balance")
    return flags


# ---------------------------------------------------------------------------------------------
# a statement whose opening balance is not printed (blanked, or an edited page that dropped it)
# ---------------------------------------------------------------------------------------------


def _account_key(doc: Document) -> str:
    return _acct_key(doc) or _ftext(doc, "bank_name").upper()


def _previous_closing(doc: Document, whole: list[Document]) -> Decimal | None:
    """The closing balance of the statement of the same account (and currency) that ends the day
    before, or on the day, this one starts; None when the case holds no such statement."""
    key, start = _account_key(doc), _fdate(doc, "period_start")
    if not key or start is None:
        return None
    best: tuple[date, Decimal] | None = None
    for other in whole:
        if other is doc or _account_key(other) != key or _currency(other) != _currency(doc):
            continue
        end, closing = _fdate(other, "period_end"), _fmoney(other, "closing_balance")
        if end is None or closing is None or not 0 <= (start - end).days <= 1:
            continue  # not the neighbour: with a gap or an overlap the balances legitimately differ
        if best is None or end > best[0]:
            best = (end, closing)
    return best[1] if best is not None else None


def _opening_of(doc: Document, whole: list[Document]) -> tuple[Decimal | None, str]:
    """``(opening balance, where it came from)``: the printed one, else the closing balance of the
    previous statement of the same account ("previous"), else ``(None, "none")``."""
    printed = _fmoney(doc, "opening_balance")
    if printed is not None:
        return printed, "printed"
    previous = _previous_closing(doc, whole)
    if previous is not None:
        return previous, "previous"
    return None, "none"


def _no_opening_flags(doc: Document, whole: list[Document], rule: str) -> list[Flag]:
    """LOW flag "opening balance not printed" when the other statements of this account print one
    and at least one of them comes earlier (the first statement of a newly opened account has no
    opening balance to print).

    Blanking the opening balance used to switch off the running-balance, totals and continuity checks
    together, so an edited credit passed unseen. The checks now work around the gap where they can
    (the previous statement's closing balance, else the first printed balance), but this statement
    is still not fully checked: the flag says so, and it carries a reading note so that coverage
    counts the check as not settled for the case. Low, not higher: the cause is as often a template
    quirk as a tamper, and the rows after the first printed balance are still checked."""
    key = _account_key(doc)
    siblings = [
        d
        for d in whole
        if d is not doc and key and _account_key(d) == key and _fmoney(d, "opening_balance")
    ]
    if not siblings:
        return []
    start = _fdate(doc, "period_start")
    if start is not None and all(
        (other_start := _fdate(d, "period_start")) is not None and other_start >= start
        for d in siblings
    ):
        return []  # the earliest statement of the account: nothing before it to carry a balance
    previous = _previous_closing(doc, whole)
    how = (
        "its rows were checked from the closing balance of the previous statement"
        if previous is not None
        else "its rows were checked only from the first printed balance"
    )
    evidence = (
        f"opening balance not printed on {doc.id}, while {len(siblings)} other statement(s) of the "
        f"same account print one; {how}"
    )
    flag = make_flag(
        rule,
        docs=[doc],
        field="opening_balance",
        region=_box(_field(doc, "opening_balance"), "opening balance"),
        severity="low",
        discriminator="no_opening",
        evidence=evidence,
        explanation=(
            "This statement has no opening balance although the other statements of the account "
            "print one, so it could not be checked in full. Compare it with the page image."
        ),
    )
    return [
        flag.model_copy(
            update={
                "reading": ReadingNote(
                    severity="low",
                    kind="opening_not_printed",
                    cells=[f"{doc.id} opening balance: not printed"],
                )
            }
        )
    ]


def _missing_balance_flags(skipped: list[Document], evaluated: list[Document]) -> list[Flag]:
    """A statement with transactions but no readable balance column, among statements of the same
    account that all print one, cannot be checked and a template does not drop a column from one
    month to the next (red-team attack R102: renaming the column heading hid one statement from
    the running-balance check while the others kept the case in the fast lane)."""
    out: list[Flag] = []
    for doc in skipped:
        table = doc.tables.get("transactions")
        if table is None or not table.rows:
            continue
        if any(r.cells["balance"].money() is not None for r in table.rows):
            continue  # it has balances; it was skipped for another reason (no opening balance)
        if any(c.source != "text_layer" for c in table.rows[0].cells.values()):
            continue  # a scan: a column OCR missed is not a statement that dropped its column
        key = _acct_key(doc) or _ftext(doc, "bank_name").upper()
        if not key:
            continue
        siblings = [d for d in evaluated if (_acct_key(d) or _ftext(d, "bank_name").upper()) == key]
        if not siblings:
            continue
        out.append(
            make_flag(
                "bs_running_balance",
                docs=[doc],
                table="transactions",
                severity="high",
                evidence=(
                    f"no balance could be read on any of {len(table.rows)} rows, while "
                    f"{len(siblings)} other statement(s) of the same account print one; "
                    "the running balance of this statement was not checked"
                ),
                explanation=(
                    "This statement shows no readable balance column although the other "
                    "statements of the account do, so its running balance could not be checked."
                ),
            )
        )
    return out


_NOT_EVALUATED = object()

_MONEY_TOKEN = re.compile(r"\d{1,3}(?:,\d{3})*\.\d{2}|\d+\.\d{2}")


def _check_statement(  # noqa: ANN202 - Flag | None | sentinel
    ctx: CaseContext, doc: Document, opening: Decimal | None, source: str = "printed"
):
    """Walk the rows from the opening balance. ``source`` says where it came from: "printed", or
    "previous" (the closing balance of the previous statement of the account, used when this one
    prints none). With no opening balance at all the first printed balance is the anchor and every
    row after it is checked (the first row's own amount cannot be); a statement where that leaves
    nothing to compare is not evaluated."""
    table = doc.tables.get("transactions")
    if table is None or not table.rows:
        return _NOT_EVALUATED
    if not any(r.cells["balance"].money() is not None for r in table.rows):
        return _NOT_EVALUATED

    tol = _tol(ctx)
    anchor = opening
    anchor_label = "previous" if source == "printed" else "previous statement's closing"
    compared = 0
    anchor_row: int | None = None  # 0-based index of the anchor row; None = opening balance
    net = ZERO
    first_since: int | None = None
    amount_parts: list[tuple[str, Decimal]] = []

    first_break: dict | None = None
    later = 0
    # amount cells that hold text but no single amount (two numbers where one was painted over another): the
    # arithmetic counts them as nothing, so the flag must say so instead of printing "+ 0.00"
    unread: list[tuple[int, str, str]] = []

    for i, row in enumerate(table.rows):
        debit = row.cells["debit"].money()
        credit = row.cells["credit"].money()
        balance = row.cells["balance"].money()

        if debit is not None and credit is not None:
            # invalid row (both set): skipped for arithmetic; its own balance becomes the anchor
            if balance is not None:
                anchor, anchor_label, anchor_row = balance, "previous", i
                net, first_since, amount_parts, unread = ZERO, None, [], []
            continue
        if debit is None and credit is None and balance is None:
            continue  # nothing printed on this row
        if (
            anchor is None
        ):  # no opening balance: rows before the first printed balance are unchecked
            if balance is not None:
                anchor, anchor_label, anchor_row = balance, "first printed balance", i
            continue
        for col, value in (("credit", credit), ("debit", debit)):
            raw = (row.cells[col].raw or "").strip()
            if value is None and raw:
                unread.append((i, col, raw))

        amount = (credit or ZERO) - (debit or ZERO)
        net += amount
        if first_since is None:
            first_since = i
        kind = "credit" if credit is not None else "debit" if debit is not None else "net"
        amount_parts.append((kind, credit if credit is not None else (debit or ZERO)))

        if balance is None:
            continue  # carried forward to the next printed balance

        expected = anchor + net
        compared += 1
        if not approx_eq(expected, balance, tol):
            if first_break is None:
                first_break = {
                    "row": i,
                    "expected": expected,
                    "printed": balance,
                    "anchor": anchor,
                    "anchor_label": anchor_label,
                    "anchor_row": anchor_row,
                    "net": net,
                    "first_since": first_since,
                    "parts": list(amount_parts),
                    "unread": list(unread),
                    "tol": tol,
                }
            else:
                later += 1
        # compare against the previous PRINTED balance, so one edit gives one break
        anchor, anchor_label, anchor_row = balance, "previous", i
        net, first_since, amount_parts, unread = ZERO, None, [], []

    if compared == 0:
        return _NOT_EVALUATED
    if first_break is None:
        return None
    return _build_flag(doc, table, first_break, later)


def _build_flag(doc: Document, table, brk: dict, later: int) -> Flag:  # noqa: ANN001
    i = brk["row"]
    row = table.rows[i]
    expected: Decimal = brk["expected"]
    printed: Decimal = brk["printed"]
    anchor: Decimal = brk["anchor"]
    parts: list[tuple[str, Decimal]] = brk["parts"]
    diff = abs(printed - expected)
    direction = "higher" if printed > expected else "lower"

    if len(parts) == 1 and parts[0][0] in ("credit", "debit"):
        kind, amount = parts[0]
        op = "+" if kind == "credit" else "-"
        evidence = (
            f"{brk['anchor_label']} {fmt_money(anchor)} {op} {kind} {fmt_money(amount)} = "
            f"{fmt_money(expected)}, printed {fmt_money(printed)}"
        )
    elif len(parts) == 1:  # balance-only row (amount 0)
        evidence = (
            f"{brk['anchor_label']} {fmt_money(anchor)} + 0.00 = {fmt_money(expected)}, "
            f"printed {fmt_money(printed)}"
        )
    else:
        anchor_text = (
            f"anchor row {brk['anchor_row'] + 1} balance"
            if brk["anchor_row"] is not None
            else "opening balance"
            if brk["anchor_label"] == "previous"
            else "closing balance of the previous statement"
        )
        evidence = (
            f"{anchor_text} {fmt_money(anchor)} + net {fmt_money(brk['net'])} since row "
            f"{brk['first_since'] + 1} = {fmt_money(expected)}, printed {fmt_money(printed)}"
        )
    evidence += f"; diff {fmt_money(diff)}"
    unread: list[tuple[int, str, str]] = brk.get("unread", [])
    fits: list[str] = []
    for r, col, raw in unread:
        evidence += f"; the {col} cell on row {r + 1} reads '{truncate(raw, 60)}', not one amount, and was counted as 0.00"
    if len(unread) == 1:
        _, col, raw = unread[0]
        sign = 1 if col == "credit" else -1
        for token in _MONEY_TOKEN.findall(raw):
            if approx_eq(
                anchor + brk["net"] + sign * Decimal(token.replace(",", "")), printed, brk["tol"]
            ):
                fits.append(token)
        if fits:
            evidence += f"; the printed balance fits {', '.join(fits)}"
    if later:
        evidence += f"; {later} later row(s) also break"

    cells = {"balance": row.cells["balance"]}
    extra: list[Region] = []
    for r, col, _raw in unread:
        box = table.rows[r].cells[col].box
        if box is not None:
            extra.append(_labelled(box, f"{col} (not one amount)"))  # type: ignore[arg-type]
    for col in ("credit", "debit"):
        c = row.cells[col]
        if c.money() is not None:
            cells[col] = c
            if c.box is not None:
                extra.append(_labelled(c.box, col))  # type: ignore[arg-type]
    if i > 0:
        prev = table.rows[i - 1].cells["balance"]
        if prev.box is not None:
            extra.append(_labelled(prev.box, "previous balance"))  # type: ignore[arg-type]
    evidence += _unverified(cells)

    return make_flag(
        "bs_running_balance",
        docs=[doc],
        table="transactions",
        row=i,
        region=_labelled(row.cells["balance"].box, "balance"),
        extra_regions=extra,
        evidence=evidence,
        explanation=(
            (
                f"The balance on row {i + 1} does not follow from the previous balance and the "
                f"transaction amount, because the {unread[0][1]} cell on row {unread[0][0] + 1} holds "
                f"more than one amount and could not be read as one"
                + (f" (the printed balance fits {fits[0]})." if fits else ".")
            )
            if unread
            else (
                f"The balance on row {i + 1} is {fmt_money(diff)} {direction} than the previous "
                "balance plus the transaction amounts."
            )
        ),
    )


# ---------------------------------------------------------------------------------------------
# bs_totals (4.1)
# ---------------------------------------------------------------------------------------------


@check(
    rule="bs_totals",
    applies_to={"bank_statement"},
    severity="high",
    description=(
        "Opening balance plus credits minus debits equals the closing balance, and printed "
        "totals equal the sums of the listed rows."
    ),
    tamper="Edited amount or balance; removed or inserted rows.",
)
def bs_totals(ctx: CaseContext) -> list[Flag]:
    tol = _tol(ctx)
    flags: list[Flag] = []
    evaluated = 0
    whole = _whole_statements(ctx)
    for doc in whole:
        table = doc.tables.get("transactions")
        opening, _source = _opening_of(doc, whole)  # printed, else the previous statement's closing
        closing = _fmoney(doc, "closing_balance")
        if table is None or not table.rows or closing is None:
            continue
        if opening is None:
            # nothing to close the equation with; the printed totals can still be checked, and a
            # statement whose siblings print an opening balance is reported as not fully checked
            flags += _no_opening_flags(doc, whole, "bs_totals")
            if all(_fmoney(doc, n) is None for n in ("total_debits", "total_credits")):
                continue
        evaluated += 1
        credits = debits = ZERO
        n_credit = n_debit = invalid = 0
        used: dict[str, Cell] = {}
        unread: list[
            tuple[int, str, str, Cell]
        ] = []  # amount cells counted as nothing: (row, column, text, cell)
        for i, row in enumerate(table.rows):
            debit, credit = _row_money(row, "debit"), _row_money(row, "credit")
            if debit is not None and credit is not None:
                invalid += 1
                continue
            for col in ("credit", "debit"):
                raw = _unread_raw(row, col)
                if raw:
                    unread.append((i, col, raw, row.cells[col]))
            if debit is not None:
                debits += debit
                n_debit += 1
                used[f"debit row {i + 1}"] = row.cells["debit"]
            if credit is not None:
                credits += credit
                n_credit += 1
                used[f"credit row {i + 1}"] = row.cells["credit"]
        note = f"; {invalid} invalid row(s) excluded" if invalid else ""

        expected = quantize(opening + credits - debits) if opening is not None else None
        opening_cell, closing_cell = _field(doc, "opening_balance"), _field(doc, "closing_balance")
        if opening is not None and expected is not None and not approx_eq(expected, closing, tol):
            cells = {"opening_balance": opening_cell, "closing_balance": closing_cell, **used}
            unread_note, fits = "", []
            for r, col, raw, _cell in unread:
                unread_note += f"; the {col} cell on row {r + 1} reads '{truncate(raw, 60)}', not one amount, and was counted as 0.00"
            if len(unread) == 1:
                _r, col, raw, _cell = unread[0]
                sign = 1 if col == "credit" else -1
                fits = [
                    t
                    for t in _MONEY_TOKEN.findall(raw)
                    if approx_eq(expected + sign * Decimal(t.replace(",", "")), closing, tol)
                ]
                if fits:
                    unread_note += f"; the printed closing balance fits {', '.join(fits)}"
            flags.append(
                make_flag(
                    "bs_totals",
                    docs=[doc],
                    field="closing_balance",
                    region=_box(closing_cell, "closing balance"),
                    extra_regions=_extras(
                        _box(opening_cell, "opening balance"),
                        *[
                            _box(c, f"{col} on row {r + 1} (not one amount)")
                            for r, col, _raw, c in unread
                        ],
                    ),
                    evidence=(
                        f"opening {fmt_money(opening)} + credits {fmt_money(credits)} - debits "
                        f"{fmt_money(debits)} = {fmt_money(expected)}; printed closing "
                        f"{fmt_money(closing)}; diff {fmt_money(abs(closing - expected))}"
                        f"{note}{unread_note}{_unverified({k: v for k, v in cells.items() if v is not None})}"
                    ),
                    explanation=(
                        (
                            f"Opening balance plus deposits minus withdrawals does not equal the closing balance "
                            f"of {fmt_money(closing)}, because the {unread[0][1]} cell on row {unread[0][0] + 1} "
                            f"holds more than one amount and was counted as nothing"
                            + (f" (the closing balance fits {fits[0]})." if fits else ".")
                        )
                        if unread
                        else (
                            f"Opening balance plus deposits minus withdrawals is {fmt_money(expected)}, "
                            f"but the statement prints a closing balance of {fmt_money(closing)}."
                        )
                    ),
                )
            )
        for name, total, count, kind, disc in (
            ("total_debits", debits, n_debit, "debit", "printed_debits"),
            ("total_credits", credits, n_credit, "credit", "printed_credits"),
        ):
            printed = _fmoney(doc, name)
            if printed is None or approx_eq(quantize(total), printed, tol):
                continue
            cell = _field(doc, name)
            cells = {name: cell, **{k: v for k, v in used.items() if k.startswith(kind)}}
            word = "row" if count == 1 else "rows"
            flags.append(
                make_flag(
                    "bs_totals",
                    docs=[doc],
                    field=name,
                    region=_box(cell, name.replace("_", " ")),
                    evidence=(
                        f"printed {name} {fmt_money(printed)}; sum of {count} {kind} {word} "
                        f"{fmt_money(total)}; diff {fmt_money(abs(printed - total))}"
                        f"{note}{_unverified({k: v for k, v in cells.items() if v is not None})}"
                    ),
                    explanation=(
                        f"The printed total of {kind}s does not equal the sum of the listed rows."
                    ),
                    discriminator=disc,
                )
            )
    if evaluated == 0:
        raise CheckSkipped("no statement with transactions, opening balance and closing balance")
    return flags


# ---------------------------------------------------------------------------------------------
# bs_period_continuity (4.3)
# ---------------------------------------------------------------------------------------------


def _unassembled_pieces(ctx: CaseContext) -> list[tuple[str, date | None, date | None]]:
    """``(account, period start, period end)`` of every page of a split statement that could not be
    assembled. A month that is uploaded but not reviewed must not read as a month that is absent."""
    out: list[tuple[str, date | None, date | None]] = []
    for doc in _statements(ctx):
        if doc.statement is None or doc.statement.state == "assembled":
            continue
        acct = _acct_key(doc)
        if not acct:
            chain = doc.tables.get("page_chain")
            for row in chain.rows if chain is not None else []:
                cell = row.cells.get("account_number")
                if cell is not None and cell.value:
                    acct = "".join(cell.text().replace("-", "").split()).upper()
                    break
        out.append((acct, _fdate(doc, "period_start"), _fdate(doc, "period_end")))
    return out


def _gap_explained(
    pieces: Sequence[tuple[str, date | None, date | None]], acct: str, a_end: date, b_start: date
) -> bool:
    """True when the unassembled pages of this account (or of an unknown account) carry periods
    that together cover every day between the two statements. A page without a period, or one
    that covers only part of the gap, explains nothing: one stray page must not hide a month
    that is absent."""
    first, last = add_days(a_end, 1), add_days(b_start, -1)
    spans = sorted(
        (max(start, first), min(end, last))
        for piece_acct, start, end in pieces
        if (not piece_acct or piece_acct == acct)
        and start is not None
        and end is not None
        and start <= last
        and end >= first
    )
    reached = add_days(first, -1)  # last day covered so far
    for start, end in spans:
        if start > add_days(reached, 1):
            return False
        reached = max(reached, end)
    return reached >= last


@check(
    rule="bs_period_continuity",
    applies_to={"bank_statement"},
    severity="high",
    description=(
        "Consecutive statements of one account leave no gap or overlap and the earlier closing "
        "balance equals the next opening balance."
    ),
    tamper=(
        "Swapped or removed month; a month edited with recomputed balances while its neighbours "
        "are untouched."
    ),
)
def bs_period_continuity(ctx: CaseContext) -> list[Flag]:
    tol = _tol(ctx)
    groups: dict[tuple[str, str], list[tuple[date, date, Document]]] = defaultdict(list)
    whole = _whole_statements(ctx)
    for doc in whole:
        start, end = _fdate(doc, "period_start"), _fdate(doc, "period_end")
        acct = _acct_key(doc)
        if not acct or start is None or end is None:
            continue
        groups[(acct, _currency(doc))].append((start, end, doc))

    unassembled = _unassembled_pieces(ctx)
    flags: list[Flag] = []
    pairs = 0
    for key in sorted(groups):
        items = sorted(groups[key], key=lambda t: (t[0], t[1], natural_key(t[2].id)))
        for (a_start, a_end, a), (b_start, b_end, b) in zip(items, items[1:], strict=False):
            pairs += 1
            gap_days = (b_start - a_end).days
            if gap_days > 1:
                if _gap_explained(unassembled, key[0], a_end, b_start):
                    continue  # the missing month is on file but could not be assembled
                flags.append(_pc_flag(a, b, "gap", gap_days - 1, a_start, a_end, b_start, b_end))
            elif gap_days < 0:
                flags.append(_pc_flag(a, b, "overlap", 0, a_start, a_end, b_start, b_end))
            else:
                # Contiguous periods: closing of A must equal opening of B. With a gap or an
                # overlap the balances legitimately differ, so they are not compared then.
                closing, opening = _fmoney(a, "closing_balance"), _fmoney(b, "opening_balance")
                if opening is None and closing is not None:
                    # nothing to compare with: said so, not left as "ran, no flag"
                    flags += _no_opening_flags(b, whole, "bs_period_continuity")
                if closing is None or opening is None or approx_eq(closing, opening, tol):
                    continue
                cells = {
                    "closing_balance": _field(a, "closing_balance"),
                    "opening_balance": _field(b, "opening_balance"),
                }
                flags.append(
                    make_flag(
                        "bs_period_continuity",
                        docs=[a, b],
                        field="opening_balance",
                        region=_box(cells["opening_balance"], "opening balance"),
                        extra_regions=_extras(_box(cells["closing_balance"], "previous closing")),
                        evidence=(
                            f"balance: {a.id} closing {fmt_money(closing)}, {b.id} opening "
                            f"{fmt_money(opening)}; diff {fmt_money(abs(opening - closing))}"
                            f"{_unverified({k: v for k, v in cells.items() if v is not None})}"
                        ),
                        explanation=(
                            "The closing balance of the earlier statement does not match the "
                            "opening balance of the next."
                        ),
                        discriminator="balance",
                    )
                )
    if pairs == 0:
        raise CheckSkipped("no account has two statements with account number and period")
    return flags


def _pc_flag(
    a: Document,
    b: Document,
    kind: str,
    missing: int,
    a_start: date,
    a_end: date,
    b_start: date,
    b_end: date,
) -> Flag:
    cells = {
        "period_start": _field(b, "period_start"),
        "period_end": _field(a, "period_end"),
    }
    if kind == "gap":
        evidence = (
            f"gap: {a.id} ends {a_end.isoformat()}, {b.id} starts {b_start.isoformat()}; "
            f"{missing} day(s) not covered"
        )
        explanation = f"No statement covers {missing} day(s) between these two statements."
        severity = None
    else:
        shared = (min(a_end, b_end) - b_start).days + 1
        same = (a_start, a_end) == (b_start, b_end)
        evidence = (
            f"overlap: {a.id} ends {a_end.isoformat()}, {b.id} starts {b_start.isoformat()} "
            f"({shared} days{'; same period uploaded twice' if same else ''})"
        )
        explanation = "These two statements cover some of the same days."
        severity = "medium"
    return make_flag(
        "bs_period_continuity",
        docs=[a, b],
        field="period_start",
        region=_box(cells["period_start"], "period start"),
        extra_regions=_extras(_box(cells["period_end"], "previous period end")),
        evidence=evidence + _unverified({k: v for k, v in cells.items() if v is not None}),
        explanation=explanation,
        severity=severity,
        discriminator=kind,
    )


# ---------------------------------------------------------------------------------------------
# bs_dates (4.4)
# ---------------------------------------------------------------------------------------------


def _row_dates(table: Table) -> list[tuple[int, date | None, str, str]]:
    """Per row ``(index, date, kind, text)``; kind is ok, inherited, calendar or none.

    An empty date cell (value and raw both None) inherits the previous valid date (many layouts
    print the date once per day). A cell that was read but is not a real date is ``calendar``.
    """
    out: list[tuple[int, date | None, str, str]] = []
    previous: date | None = None
    for i, row in enumerate(table.rows):
        cell = row.cells.get("date")
        if cell is None or (cell.value is None and cell.raw is None):
            out.append((i, previous, "inherited" if previous else "none", ""))
            continue
        parsed = cell.date_()
        if parsed is None:
            out.append(
                (i, None, "calendar", cell.raw if cell.raw is not None else cell.value or "")
            )
            continue
        previous = parsed
        out.append((i, parsed, "ok", parsed.isoformat()))
    return out


def _out_of_order(dates: Sequence[date]) -> set[int]:
    """Indices outside a longest non-decreasing subsequence (patience sorting, ``bisect_right``)."""
    tails: list[date] = []
    tail_idx: list[int] = []
    prev = [-1] * len(dates)
    for i, d in enumerate(dates):
        pos = bisect_right(tails, d)
        if pos == len(tails):
            tails.append(d)
            tail_idx.append(i)
        else:
            tails[pos] = d
            tail_idx[pos] = i
        prev[i] = tail_idx[pos - 1] if pos > 0 else -1
    keep: set[int] = set()
    k = tail_idx[-1] if tail_idx else -1
    while k != -1:
        keep.add(k)
        k = prev[k]
    return set(range(len(dates))) - keep


@check(
    rule="bs_dates",
    applies_to={"bank_statement"},
    severity="medium",
    description=(
        "Transaction dates are real calendar dates, in order, inside the statement period and "
        "not in the future."
    ),
    tamper="Inserted or moved rows; statement dated in the future.",
)
def bs_dates(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _statements(ctx):
        found = _date_flags(ctx, doc)
        if found is None:
            continue
        evaluated += 1
        flags.extend(found)
    if evaluated == 0:
        raise CheckSkipped("no statement with parseable dates")
    return flags


def _date_flags(ctx: CaseContext, doc: Document) -> list[Flag] | None:
    """Flags for one statement, or None when it has no dates to evaluate."""
    table = doc.tables.get("transactions")
    rows = _row_dates(table) if table is not None else []
    start, end = _fdate(doc, "period_start"), _fdate(doc, "period_end")
    if (
        not any(kind in ("ok", "calendar") for _, _, kind, _ in rows)
        and start is None
        and end is None
    ):
        return None
    today = ctx.today

    def date_flag(i: int, evidence: str, explanation: str, disc: str) -> dict:
        assert table is not None
        cell = table.rows[i].cells["date"]
        return {
            "rule": "bs_dates",
            "docs": [doc],
            "table": "transactions",
            "row": i,
            "region": _box(cell, "date"),
            "evidence": evidence + _unverified({"date": cell}),
            "explanation": explanation,
            "discriminator": disc,
        }

    calendar: list[dict] = []
    order: list[dict] = []
    out_of_range: list[dict] = []
    future: list[dict] = []

    for i, _, kind, text in rows:
        if kind == "calendar":
            calendar.append(
                date_flag(
                    i,
                    f'row {i + 1} date "{_clean(text)}" is not a calendar date',
                    f"The date on row {i + 1} is not a real date.",
                    "calendar",
                )
            )

    dated = [(i, d) for i, d, kind, _ in rows if kind == "ok" and d is not None]
    for pos in sorted(_out_of_order([d for _, d in dated])):
        i, d = dated[pos]
        if pos > 0:
            j, e = dated[pos - 1]
            evidence = (
                f"row {i + 1} dated {d.isoformat()} follows row {j + 1} dated "
                f"{e.isoformat()} (out of order)"
            )
        else:
            j, e = dated[pos + 1]
            evidence = (
                f"row {i + 1} dated {d.isoformat()} precedes row {j + 1} dated "
                f"{e.isoformat()} (out of order)"
            )
        order.append(date_flag(i, evidence, f"The date on row {i + 1} is out of order.", "order"))

    for i, d in dated:
        if (start is not None and d < add_days(start, -BS_DATE_GRACE_DAYS)) or (
            end is not None and d > add_days(end, BS_DATE_GRACE_DAYS)
        ):
            span = f"{start.isoformat() if start else '?'}..{end.isoformat() if end else '?'}"
            out_of_range.append(
                date_flag(
                    i,
                    f"row {i + 1} dated {d.isoformat()} outside period {span}",
                    f"The date on row {i + 1} is outside the statement period.",
                    "range",
                )
            )

    for field_name, bad, evidence in (
        (
            "period_end",
            end is not None and end > today,
            f"period_end {end.isoformat() if end else ''} is after today {today.isoformat()}",
        ),
        (
            "period_start",
            start is not None and end is not None and start > end,
            f"period_start {start.isoformat() if start else ''} is after period_end "
            f"{end.isoformat() if end else ''}",
        ),
    ):
        if bad:
            cell = _field(doc, field_name)
            future.append(
                {
                    "rule": "bs_dates",
                    "docs": [doc],
                    "field": field_name,
                    "region": _box(cell, field_name.replace("_", " ")),
                    "evidence": evidence + _unverified({field_name: cell} if cell else {}),
                    "explanation": (
                        "The statement period is in the future."
                        if field_name == "period_end"
                        else "The statement period starts after it ends."
                    ),
                    "discriminator": "future",
                }
            )
    for i, d in dated:
        if d > today:
            future.append(
                date_flag(
                    i,
                    f"row {i + 1} dated {d.isoformat()} is after today {today.isoformat()}",
                    f"The date on row {i + 1} is in the future.",
                    "future",
                )
            )

    flags: list[Flag] = []
    for group in (calendar, order, out_of_range, future):
        flags.extend(_emit(group, cap=3))
    return flags


# ---------------------------------------------------------------------------------------------
# bs_holder (4.5)
# ---------------------------------------------------------------------------------------------


def _match_ratio(a: str, b: str) -> str:
    keys_a, keys_b = company_keys(a), company_keys(b)
    if not keys_a or not keys_b:
        return "no match"
    best = max(fuzz.token_sort_ratio(ka, kb) for ka in keys_a for kb in keys_b)
    return f"match {best / 100:.2f}"


@check(
    rule="bs_holder",
    applies_to={"bank_statement"},
    severity="high",
    description="The statement's account holder (or UEN) is the applicant company.",
    tamper="Someone else's statement.",
)
def bs_holder(ctx: CaseContext) -> list[Flag]:
    applicant = ctx.applicant
    a_name = (applicant.name or "").strip()
    a_uen = normalise_uen(applicant.uen)
    if not a_name and not a_uen:
        raise CheckSkipped("no applicant")
    flags: list[Flag] = []
    evaluated = 0
    for doc in _statements(ctx):
        s_uen = normalise_uen(_ftext(doc, "uen"))
        holder = _ftext(doc, "account_holder")
        if s_uen and a_uen:
            evaluated += 1
            if s_uen != a_uen:
                cell = _field(doc, "uen")
                flags.append(
                    make_flag(
                        "bs_holder",
                        docs=[doc],
                        field="uen",
                        region=_box(cell, "uen"),
                        evidence=(
                            f"statement uen {_clean(s_uen)} vs applicant uen {_clean(a_uen)}"
                            f"{_unverified({'uen': cell} if cell else {})}"
                        ),
                        explanation="The account holder is not the applicant company.",
                        discriminator=uen_disc(s_uen),
                    )
                )
            continue  # equal UENs pass whatever the names say
        if holder and a_name:
            evaluated += 1
            if not company_match(holder, a_name):
                cell = _field(doc, "account_holder")
                flags.append(
                    make_flag(
                        "bs_holder",
                        docs=[doc],
                        field="account_holder",
                        region=_box(cell, "account holder"),
                        evidence=(
                            f'account_holder "{_clean(holder)}" vs applicant "{_clean(a_name)}"; '
                            f"{_match_ratio(holder, a_name)}"
                            f"{_unverified({'account_holder': cell} if cell else {})}"
                        ),
                        explanation="The account holder is not the applicant company.",
                        discriminator=company_disc(holder),
                    )
                )
    if evaluated == 0:
        raise CheckSkipped("no statement with a holder name or UEN to compare with the applicant")
    return flags


# ---------------------------------------------------------------------------------------------
# bs_page_chain (4.6)
# ---------------------------------------------------------------------------------------------


@check(
    rule="bs_page_chain",
    applies_to={"bank_statement"},
    severity="medium",
    description=(
        'The "page x of y" markers are complete and in order, match the page span, and carry '
        "one account number."
    ),
    tamper="Spliced pages.",
)
def bs_page_chain(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    unprocessed = {
        f.id
        for f in ctx.case.files
        if any(p.error and p.error.startswith("not_processed") for p in f.pages)
    }
    for doc in _whole_statements(ctx):
        if doc.file_id in unprocessed:
            continue  # a budget cut removed pages: a "missing page" would be our gap, not theirs
        if isinstance(doc, AssembledStatement) and not doc.all_marked:
            continue  # joined without markers on every page: there is no chain to compare
        table = doc.tables.get("page_chain")
        if table is None:
            continue
        chain = [
            (i, row, row.cells["page_x"].int_())
            for i, row in enumerate(table.rows)
            if "page_x" in row.cells and row.cells["page_x"].int_() is not None
        ]
        if not chain:
            continue
        evaluated += 1
        flags.extend(_page_chain_flags(doc, chain))
    if evaluated == 0:
        raise CheckSkipped("no page markers")
    return flags


def _y_of(row: Row) -> int | None:
    cell = row.cells.get("page_y")
    return cell.int_() if cell is not None else None


def _page_chain_flags(doc: Document, chain: list[tuple[int, Row, int | None]]) -> list[Flag]:
    xs = [x for _, _, x in chain]
    totals = [y for _, row, _ in chain if (y := _y_of(row)) is not None]
    total_known = bool(totals)
    if total_known:
        counts: dict[int, int] = defaultdict(int)
        for y in totals:
            counts[y] += 1
        top = max(counts.values())
        Y = max(y for y, n in counts.items() if n == top)  # ties: the larger  # noqa: N806
    else:
        Y = len(chain)  # noqa: N806

    def marker(row: Row, x: int | None) -> str:
        y = _y_of(row)
        return f"{x}/{y}" if y is not None else f"{x}"

    def x_box(row: Row) -> Region | None:
        return _box(row.cells.get("page_x"), "page marker")

    out: list[Flag] = []
    expected = list(range(1, Y + 1))
    if xs != expected:
        row_idx = chain[-1][0]
        for pos, x in enumerate(xs):
            if pos >= len(expected) or x != expected[pos]:
                row_idx = chain[pos][0]
                break
        missing = [k for k in expected if k not in xs]
        repeated = sorted({x for x in xs if xs.count(x) > 1})
        beyond = sorted({x for x in xs if x > Y or x < 1})
        parts: list[str] = []
        if missing:
            parts.append(
                ("missing page " if len(missing) == 1 else "missing pages ")
                + ", ".join(map(str, missing))
            )
        if repeated:
            parts.append("repeated page " + ", ".join(map(str, repeated)))
        if beyond:
            parts.append("page(s) beyond the total: " + ", ".join(map(str, beyond)))
        if not parts:
            parts.append("pages out of order")
        shown = ", ".join(marker(row, x) for _, row, x in chain)
        exp_text = ", ".join(f"{k}/{Y}" if total_known else f"{k}" for k in expected)
        row = _chain_row(chain, row_idx)
        out.append(
            make_flag(
                "bs_page_chain",
                docs=[doc],
                table="page_chain",
                row=row_idx,
                region=x_box(row),
                evidence=f"page markers {shown}; expected {exp_text}; {'; '.join(parts)}",
                explanation='The page numbers do not form a complete "page x of y" sequence.',
                discriminator="sequence",
            )
        )

    for i, row, x in chain:
        y = _y_of(row)
        if y is not None and y != Y:
            out.append(
                make_flag(
                    "bs_page_chain",
                    docs=[doc],
                    table="page_chain",
                    row=i,
                    region=x_box(row),
                    evidence=f"row {i + 1} says {x}/{y} but other pages say /{Y}",
                    explanation='The page numbers do not form a complete "page x of y" sequence.',
                    discriminator="total",
                )
            )
            break

    span = doc.page_end - doc.page_start + 1
    if len(chain) != span:
        i, row, _ = chain[-1]
        out.append(
            make_flag(
                "bs_page_chain",
                docs=[doc],
                table="page_chain",
                row=i,
                region=x_box(row),
                evidence=f"chain lists {len(chain)} pages; document has {span}",
                explanation='The page numbers do not form a complete "page x of y" sequence.',
                discriminator="count",
            )
        )

    stmt_acct = _acct_key(doc)
    account_items: list[dict] = []
    if stmt_acct:
        for i, row, x in chain:
            cell = row.cells.get("account_number")
            row_acct = "".join(cell.text().replace("-", "").split()).upper() if cell else ""
            if row_acct and row_acct != stmt_acct:
                account_items.append(
                    {
                        "rule": "bs_page_chain",
                        "docs": [doc],
                        "table": "page_chain",
                        "row": i,
                        "region": _box(cell, "account number"),
                        "evidence": (
                            f"page {x} account {_clean(row_acct)} vs {_clean(stmt_acct)} "
                            f"on the statement{_unverified({'account_number': cell} if cell else {})}"
                        ),
                        "explanation": (
                            "This page carries a different account number from the rest of the "
                            "statement."
                        ),
                        "discriminator": "account",
                    }
                )
    out.extend(_emit(account_items, cap=DEFAULT_CAP))
    return out


def _chain_row(chain: list[tuple[int, Row, int | None]], index: int) -> Row:
    for i, row, _ in chain:
        if i == index:
            return row
    return chain[-1][1]


# ---------------------------------------------------------------------------------------------
# bs_duplicates (4.7, hint)
# ---------------------------------------------------------------------------------------------


@check(
    rule="bs_duplicates",
    applies_to={"bank_statement"},
    severity="low",
    description=(
        "Rows with the same date, description and amount of at least 20.00 appear more than once "
        "in a statement (a hint)."
    ),
    tamper="Copy-pasted rows.",
)
def bs_duplicates(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _statements(ctx):
        table = doc.tables.get("transactions")
        if table is None or not table.rows:
            continue
        evaluated += 1
        dates = {i: (d, kind, text) for i, d, kind, text in _row_dates(table)}
        groups: dict[tuple, list[int]] = defaultdict(list)
        for i, row in enumerate(table.rows):
            debit, credit = _row_money(row, "debit"), _row_money(row, "credit")
            if (debit is None) == (credit is None):
                continue  # balance-only or invalid row
            amount = debit if debit is not None else credit
            assert amount is not None
            if amount < BS_DUP_MIN_AMOUNT:
                continue
            d, kind, text = dates[i]
            when = d.isoformat() if d is not None and kind != "calendar" else text.upper()
            desc = " ".join(row.cells["description"].text().upper().split())
            groups[(when, desc, debit, credit)].append(i)
        items: list[dict] = []
        for key, idx in groups.items():
            if len(idx) < 2:
                continue
            first, second = idx[0], idx[1]
            when, _, debit, credit = key
            cell = table.rows[second].cells["description"]
            kind_word, amount = ("debit", debit) if debit is not None else ("credit", credit)
            listing = (
                f"{idx[0] + 1} and {idx[1] + 1}"
                if len(idx) == 2
                else ", ".join(str(k + 1) for k in idx[:-1]) + f" and {idx[-1] + 1}"
            )
            items.append(
                {
                    "rule": "bs_duplicates",
                    "docs": [doc],
                    "table": "transactions",
                    "row": second,
                    "region": _box(cell, "duplicate row"),
                    "extra_regions": _extras(
                        _box(table.rows[first].cells["description"], "first occurrence"),
                        *[
                            _box(table.rows[k].cells["description"], "also identical")
                            for k in idx[2:4]
                        ],
                    ),
                    "evidence": (
                        f'rows {listing} identical: {when} "{_clean(cell.text())}" '
                        f"{kind_word} {fmt_money(amount)}"
                    ),
                    "explanation": (
                        f"Row {second + 1} repeats an earlier row with the same date, "
                        "description and amount."
                    ),
                }
            )
        items.sort(key=lambda kw: kw["row"])
        flags.extend(_emit(items))
    if evaluated == 0:
        raise CheckSkipped("no statement with transactions")
    return flags


# ---------------------------------------------------------------------------------------------
# bs_round_numbers (4.8, hint)
# ---------------------------------------------------------------------------------------------


def _credit_rows(doc: Document) -> list[tuple[int, Decimal]]:
    table = doc.tables.get("transactions")
    if table is None:
        return []
    out: list[tuple[int, Decimal]] = []
    for i, row in enumerate(table.rows):
        credit, debit = _row_money(row, "credit"), _row_money(row, "debit")
        if credit is not None and debit is None:
            out.append((i, credit))
    return out


def _is_round(amount: Decimal) -> bool:
    return amount >= RN_ROUND_MODULUS and amount % RN_ROUND_MODULUS == 0


def _poisson_tail(k: int, lam: float) -> float:
    """P(X >= k) for X ~ Poisson(lam)."""
    term, below = math.exp(-lam), 0.0
    for i in range(k):
        below += term
        term *= lam / (i + 1)
    return max(0.0, 1.0 - below)


@check(
    rule="bs_round_numbers",
    applies_to={"bank_statement"},
    severity="low",
    description=(
        "A statement has a much higher share of round-sum credits, or far more of them than the "
        "other statements of the same account lead one to expect (a hint)."
    ),
    tamper="Invented credits.",
)
def bs_round_numbers(ctx: CaseContext) -> list[Flag]:
    groups: dict[tuple[str, str], list[Document]] = defaultdict(list)
    for doc in _whole_statements(ctx):
        acct = _acct_key(doc)
        if acct:
            groups[(acct, _currency(doc))].append(doc)

    flags: list[Flag] = []
    evaluated = 0
    min_share, min_uplift = _frac(RN_MIN_SHARE), _frac(RN_MIN_UPLIFT)
    for key in sorted(groups):
        docs = groups[key]
        if len(docs) < 2:
            continue
        stats = {}
        for d in docs:
            credits = _credit_rows(d)
            stats[d.id] = (credits, [(i, a) for i, a in credits if _is_round(a)])
        for doc in docs:
            credits, rounds = stats[doc.id]
            if len(credits) < RN_MIN_CREDITS:
                continue
            base_n = sum(len(stats[o.id][0]) for o in docs if o is not doc)
            base_r = sum(len(stats[o.id][1]) for o in docs if o is not doc)
            if base_n < RN_MIN_BASELINE_CREDITS:
                continue
            evaluated += 1
            share = Fraction(len(rounds), len(credits))
            baseline = Fraction(base_r, base_n)
            if len(rounds) < RN_MIN_ROUND:
                continue
            by_share = share >= min_share and share - baseline >= min_uplift
            per_month = max(RN_COUNT_LAMBDA_FLOOR, base_r / (len(docs) - 1))
            tail = _poisson_tail(len(rounds), per_month)
            by_count = tail < RN_COUNT_ALPHA and share - baseline >= _frac(RN_COUNT_MIN_UPLIFT)
            if not (by_share or by_count):
                continue
            count_note = (
                f"; {len(rounds)} round credits against about {per_month:.1f} a month in the "
                f"other statements (chance {tail:.4f})"
                if by_count and not by_share
                else ""
            )
            table = doc.tables["transactions"]
            first = rounds[0][0]
            cell = table.rows[first].cells["credit"]
            others = [_box(table.rows[i].cells["credit"], "round credit") for i, _ in rounds[1:]]
            flags.append(
                make_flag(
                    "bs_round_numbers",
                    docs=[doc],
                    table="transactions",
                    row=first,
                    region=_box(cell, "round credit"),
                    extra_regions=_extras(*others),
                    evidence=(
                        f"{len(rounds)} of {len(credits)} credits are round multiples of "
                        f"{RN_ROUND_MODULUS} ({_pct(share)}%); other statements of this account: "
                        f"{base_r} of {base_n} ({_pct(baseline)}%); uplift "
                        f"{_pct(share - baseline)} points" + count_note
                    ),
                    explanation=(
                        "Unusually many round-sum deposits compared with the other months of "
                        "this account. A hint only, not proof."
                    ),
                )
            )
    if evaluated == 0:
        raise CheckSkipped("no account with two statements and enough credits")
    return flags


# ---------------------------------------------------------------------------------------------
# bs_benford (4.9, hint)
# ---------------------------------------------------------------------------------------------


def _first_digit(amount: Decimal) -> int | None:
    for digit in amount.copy_abs().as_tuple().digits:
        if digit:
            return digit
    return None


@check(
    rule="bs_benford",
    applies_to={"bank_statement"},
    severity="info",
    description=(
        "The first-digit distribution of at least 100 different amounts of one account departs "
        "from Benford's law (a hint with no effect on the score). Recurring identical amounts "
        "count once."
    ),
    tamper="Fabricated activity.",
)
def bs_benford(ctx: CaseContext) -> list[Flag]:
    pools: dict[tuple[str, str], list[Document]] = defaultdict(list)
    for doc in _statements(ctx):
        acct = _acct_key(doc)
        pools[(acct or f"doc:{doc.id}", _currency(doc))].append(doc)

    flags: list[Flag] = []
    evaluated = 0
    for key in sorted(pools):
        docs = pools[key]
        counts = [0] * 10
        seen: set[Decimal] = set()  # recurring identical amounts (rent, payroll) count once
        for doc in docs:
            table = doc.tables.get("transactions")
            for row in table.rows if table is not None else []:
                for column in ("debit", "credit"):
                    amount = _row_money(row, column)
                    if amount is None or amount <= 0 or amount in seen:
                        continue
                    seen.add(amount)
                    if d := _first_digit(amount):
                        counts[d] += 1
        n = sum(counts)
        if n < BENFORD_MIN_N:
            continue
        evaluated += 1
        expected = [math.log10(1 + 1 / d) for d in range(1, 10)]
        observed = [counts[d] / n for d in range(1, 10)]
        mad = sum(abs(o - p) for o, p in zip(observed, expected, strict=True)) / 9
        chi2 = sum(
            (counts[d] - n * expected[d - 1]) ** 2 / (n * expected[d - 1]) for d in range(1, 10)
        )
        if mad > BENFORD_MAD_MIN and chi2 > BENFORD_CHI2_CRIT:
            digits = " ".join(f"{d}:{round(observed[d - 1] * 100)}" for d in range(1, 10))
            flags.append(
                make_flag(
                    "bs_benford",
                    docs=docs,
                    evidence=(
                        f"n {n}; first-digit % {digits}; MAD {mad:.3f}; chi2 {chi2:.1f} "
                        f"(critical {BENFORD_CHI2_CRIT})"
                    ),
                    explanation=(
                        "The leading digits of the amounts depart from the usual pattern. Bank "
                        "amounts often do (fees, fixed salaries), so this is only a hint."
                    ),
                )
            )
    if evaluated == 0:
        raise CheckSkipped(f"no account with at least {BENFORD_MIN_N} amounts")
    return flags
