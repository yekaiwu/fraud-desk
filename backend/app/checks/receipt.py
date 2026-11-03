"""Receipt rules (docs/check-catalogue.md, docs/ingest-and-extraction.md "Receipts").

A receipt is a point-of-sale slip: a merchant, a date, items, tax and a total. It has no invoice
number, UEN or GST number, so none of the invoice rules apply to it (they are registered for
``invoice`` only) and these six are all it gets. Severities are honest about what a till slip can
show: arithmetic that does not hold on a completely read receipt is medium, never high (receipts
print tips, service charges, discounts and rounding that the schema cannot itemise, and the amounts
are small), and a receipt that was only partly read is low and says so.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.checks.invoice import incomplete_flag, reading_incomplete
from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext, natural_key
from app.core.fingerprints import receipt_fingerprint
from app.core.models import Cell, Document, Flag, Region
from app.core.money import fmt_money, quantize

ZERO = Decimal("0")
USABLE = ("ok", "partial")
# a till slip rounds each printed figure: a cent or two is not a mismatch (the invoice tolerance of
# a whole dollar would hide a changed receipt)
CENT_TOLERANCE = Decimal("0.02")
# a total above subtotal plus tax by up to this share of the subtotal reads as a tip, a service
# charge or rounding that the page prints but the schema does not carry
EXTRA_SHARE = Decimal("0.25")
IMPLAUSIBLY_OLD_YEARS = 10
_EMPTY = Cell(value=None)


def _docs(ctx: CaseContext) -> list[Document]:
    return [d for d in ctx.docs("receipt") if d.status in USABLE]


def _c(doc: Document, name: str) -> Cell:
    return doc.fields.get(name, _EMPTY)


def _box(cell: Cell, label: str) -> Region | None:
    return cell.box.model_copy(update={"label": label}) if cell.box is not None else None


def _unverified(*cells: tuple[str, Cell]) -> str:
    names = sorted(n for n, c in cells if c.verified is False or c.alt_raw is not None)
    return "; unverified reading: " + ", ".join(names) if names else ""


_US_DATE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\s*$")


def _receipt_date(cell: Cell) -> date | None:
    """The printed date. The normaliser reads numeric dates day first and never guesses; a US till
    prints month first ("5/19/2017"), which is unambiguous only when the first number cannot be a
    month's second half (the second is above 12). That case, and only that, is read here."""
    if cell.date_() is not None:
        return cell.date_()
    m = _US_DATE.match(cell.raw or "")
    if m is None:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if day <= 12 or month > 12:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _doubtful(*cells: Cell) -> bool:
    """A cell the page text could not confirm, or that two readers read differently. A mismatch
    that rests on such a cell is a hint to look, not a finding: it is reported low."""
    return any(c.verified is False or c.alt_raw is not None for c in cells)


def _line_amount_cells(doc: Document) -> list[Cell]:
    table = doc.tables.get("lines")
    return [r.cells.get("amount", _EMPTY) for r in table.rows] if table is not None else []


def _line_amounts(doc: Document) -> list[Decimal]:
    table = doc.tables.get("lines")
    if table is None:
        return []
    out: list[Decimal] = []
    for row in table.rows:
        amount = row.cells.get("amount", _EMPTY).money()
        if amount is not None:
            out.append(amount)
    return out


# ---------------------------------------------------------------- rcpt_lines


@check(
    rule="rcpt_lines",
    applies_to={"receipt"},
    severity="medium",
    use_cases=frozenset(),  # listed, and counted in coverage, only when a receipt is in the case
    description="The item lines of a receipt add up to the printed subtotal.",
    tamper="An edited item amount or subtotal on a receipt.",
)
def rcpt_lines(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx):
        amounts = _line_amounts(doc)
        sub_cell = _c(doc, "subtotal")
        subtotal = sub_cell.money()
        if subtotal is None or not amounts:
            continue
        evaluated += 1
        note = reading_incomplete(doc)
        if note is not None:
            flags.append(incomplete_flag("rcpt_lines", doc, note))
            continue
        total = sum(amounts, ZERO)
        tol = CENT_TOLERANCE + Decimal("0.005") * len(amounts)
        if abs(total - subtotal) <= tol:
            continue
        flags.append(
            make_flag(
                "rcpt_lines",
                docs=[doc],
                field="subtotal",
                region=_box(sub_cell, "subtotal"),
                severity="low" if _doubtful(sub_cell, *_line_amount_cells(doc)) else None,
                evidence=(
                    f"{len(amounts)} lines sum {fmt_money(total)}; printed subtotal "
                    f"{fmt_money(subtotal)}; diff {fmt_money(abs(total - subtotal))}"
                    + _unverified(("subtotal", sub_cell))
                ),
                explanation="The item lines of this receipt do not add up to its subtotal.",
            )
        )
    if evaluated == 0:
        raise CheckSkipped("no receipt with item lines and a subtotal")
    return flags


# ---------------------------------------------------------------- rcpt_tax


@check(
    rule="rcpt_tax",
    applies_to={"receipt"},
    severity="medium",
    use_cases=frozenset(),  # listed, and counted in coverage, only when a receipt is in the case
    description="The tax on a receipt equals the printed tax rate applied to the subtotal.",
    tamper="An edited tax amount or tax rate on a receipt.",
)
def rcpt_tax(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx):
        tax_cell, rate_cell, sub_cell = (
            _c(doc, "tax_amount"),
            _c(doc, "tax_rate"),
            _c(doc, "subtotal"),
        )
        tax, rate, subtotal = tax_cell.money(), rate_cell.money(), sub_cell.money()
        if tax is None or rate is None or subtotal is None:
            continue  # without a printed rate there is nothing to hold the tax against
        evaluated += 1
        note = reading_incomplete(doc)
        if note is not None:
            flags.append(incomplete_flag("rcpt_tax", doc, note))
            continue
        expected = quantize(subtotal * rate / 100, 2)
        # tax is often charged on part of the basket only, or rounded per line: allow a cent or two
        if abs(tax - expected) <= CENT_TOLERANCE + Decimal("0.005") * len(_line_amounts(doc)):
            continue
        # a rate printed beside the tax label can apply to the taxable part only; tax below the
        # full-basket figure is therefore a weaker sign than tax above it
        severity = "low" if tax < expected or _doubtful(tax_cell, rate_cell, sub_cell) else None
        flags.append(
            make_flag(
                "rcpt_tax",
                docs=[doc],
                field="tax_amount",
                region=_box(tax_cell, "tax_amount"),
                extra_regions=[b for b in [_box(sub_cell, "subtotal")] if b is not None],
                severity=severity,
                evidence=(
                    f"tax {fmt_money(tax)}; {fmt_money(rate)}% of subtotal {fmt_money(subtotal)} "
                    f"is {fmt_money(expected)}; diff {fmt_money(abs(tax - expected))}"
                    + _unverified(("tax_amount", tax_cell), ("subtotal", sub_cell))
                ),
                explanation=(
                    "The tax on this receipt is not the printed rate applied to the subtotal."
                ),
            )
        )
    if evaluated == 0:
        raise CheckSkipped("no receipt with a subtotal, a tax amount and a printed tax rate")
    return flags


# ---------------------------------------------------------------- rcpt_total


@check(
    rule="rcpt_total",
    applies_to={"receipt"},
    severity="medium",
    use_cases=frozenset(),  # listed, and counted in coverage, only when a receipt is in the case
    description="Subtotal plus tax equals the printed total of a receipt.",
    tamper="An edited receipt total.",
)
def rcpt_total(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx):
        sub_cell, tax_cell, tot_cell = _c(doc, "subtotal"), _c(doc, "tax_amount"), _c(doc, "total")
        subtotal, tax, total = sub_cell.money(), tax_cell.money(), tot_cell.money()
        if subtotal is None or total is None:
            continue
        evaluated += 1
        note = reading_incomplete(doc)
        if note is not None:
            flags.append(incomplete_flag("rcpt_total", doc, note))
            continue
        computed = subtotal + (tax or ZERO)
        if abs(computed - total) <= CENT_TOLERANCE:
            continue
        over = total - computed
        # more charged than subtotal plus tax: a tip, a service charge or rounding is the usual
        # reason, and the page prints it on a line the schema does not carry. Less charged, or far
        # more, is not explained that way.
        explained = ZERO < over <= EXTRA_SHARE * max(subtotal, ZERO)
        flags.append(
            make_flag(
                "rcpt_total",
                docs=[doc],
                field="total",
                region=_box(tot_cell, "total"),
                extra_regions=[
                    b for b in (_box(sub_cell, "subtotal"), _box(tax_cell, "tax_amount")) if b
                ],
                severity="low" if explained or _doubtful(sub_cell, tax_cell, tot_cell) else None,
                evidence=(
                    f"subtotal {fmt_money(subtotal)} + tax {fmt_money(tax or ZERO)} = "
                    f"{fmt_money(computed)}; printed total {fmt_money(total)}; "
                    f"diff {fmt_money(abs(over))}"
                    + (" (could be a tip or service charge)" if explained else "")
                    + _unverified(("total", tot_cell), ("subtotal", sub_cell))
                ),
                explanation=(
                    f"Subtotal plus tax is {fmt_money(computed)}, but the receipt prints a "
                    f"total of {fmt_money(total)}."
                ),
            )
        )
    if evaluated == 0:
        raise CheckSkipped("no receipt with a subtotal and a total")
    return flags


# ---------------------------------------------------------------- rcpt_dates


@check(
    rule="rcpt_dates",
    applies_to={"receipt"},
    severity="medium",
    use_cases=frozenset(),  # listed, and counted in coverage, only when a receipt is in the case
    description="The receipt date is not in the future and is not implausibly old.",
    tamper="A receipt dated after the day it was submitted.",
)
def rcpt_dates(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    today: date = ctx.today
    for doc in _docs(ctx):
        cell = _c(doc, "receipt_date")
        when = _receipt_date(cell)
        if when is None:
            continue
        evaluated += 1
        if when > today:
            flags.append(
                make_flag(
                    "rcpt_dates",
                    docs=[doc],
                    field="receipt_date",
                    region=_box(cell, "receipt_date"),
                    severity="low" if _doubtful(cell) else None,
                    discriminator="future",
                    evidence=(
                        f"receipt date {when.isoformat()} is after {today.isoformat()}"
                        + _unverified(("receipt_date", cell))
                    ),
                    explanation="The receipt is dated in the future.",
                )
            )
        elif when.year < today.year - IMPLAUSIBLY_OLD_YEARS:
            flags.append(
                make_flag(
                    "rcpt_dates",
                    docs=[doc],
                    field="receipt_date",
                    region=_box(cell, "receipt_date"),
                    severity="low",
                    discriminator="old",
                    evidence=(
                        f"receipt date {when.isoformat()} is more than {IMPLAUSIBLY_OLD_YEARS} "
                        f"years before {today.isoformat()}" + _unverified(("receipt_date", cell))
                    ),
                    explanation="The receipt is very old for the purpose it was submitted for.",
                )
            )
    if evaluated == 0:
        raise CheckSkipped("no receipt with a readable date")
    return flags


# ---------------------------------------------------------------- rcpt_merchant


@check(
    rule="rcpt_merchant",
    applies_to={"receipt"},
    severity="low",
    use_cases=frozenset(),  # listed, and counted in coverage, only when a receipt is in the case
    description="A receipt names the merchant that issued it.",
    tamper="A receipt with the merchant's name removed.",
)
def rcpt_merchant(ctx: CaseContext) -> list[Flag]:
    docs = _docs(ctx)
    if not docs:
        raise CheckSkipped("no receipt")
    flags: list[Flag] = []
    for doc in docs:
        name = _c(doc, "merchant_name").value
        if name and any(ch.isalpha() for ch in name):
            continue
        flags.append(
            make_flag(
                "rcpt_merchant",
                docs=[doc],
                field="merchant_name",
                evidence="no merchant name could be read from this receipt",
                explanation="The receipt does not show who issued it.",
            )
        )
    return flags


# ---------------------------------------------------------------- rcpt_duplicate


def _fingerprint(doc: Document) -> str | None:
    merchant = _c(doc, "merchant_name").value
    when, total = _c(doc, "receipt_date").value, _c(doc, "total").value
    if not (merchant and when and total):
        return None
    return receipt_fingerprint(merchant, when, total, _c(doc, "receipt_number").value or "")


@check(
    rule="rcpt_duplicate",
    applies_to={"receipt"},
    severity="medium",
    use_cases=frozenset(),  # listed, and counted in coverage, only when a receipt is in the case
    description="The same receipt (merchant, date, total and receipt number) is not submitted twice in this case or in an earlier case.",
    tamper="The same receipt submitted twice.",
)
def rcpt_duplicate(ctx: CaseContext) -> list[Flag]:
    keyed = [(d, _fingerprint(d)) for d in _docs(ctx)]
    keyed = [(d, fp) for d, fp in keyed if fp is not None]
    if not keyed:
        raise CheckSkipped("no receipt with a merchant, a date and a total")
    flags: list[Flag] = []
    groups: dict[str, list[Document]] = defaultdict(list)
    for doc, fp in keyed:
        groups[fp].append(doc)
    for fp in sorted(groups):
        group = sorted(groups[fp], key=lambda d: natural_key(d.id))
        for dup in group[1:]:
            flags.append(
                make_flag(
                    "rcpt_duplicate",
                    docs=[group[0], dup],
                    field="total",
                    region=_box(_c(dup, "total"), "total"),
                    discriminator=fp[:12],
                    evidence=f"also appears as {group[0].id} (same fingerprint {fp[:8]})",
                    explanation="This receipt was already submitted in this case.",
                )
            )
    for doc, fp in keyed:
        assert fp is not None
        try:
            hits = ctx.fingerprints.lookup("receipt", fp, exclude_case_id=ctx.case.id)
        except Exception:  # noqa: BLE001 - an unavailable store must not break the check
            continue
        seen: dict[str, tuple[str | None, str]] = {}
        for hit in hits:
            seen.setdefault(hit.case_id, (hit.document_id, hit.created_at.date().isoformat()))
        for case_id in sorted(seen):
            hit_doc, when = seen[case_id]
            where = f" (document {hit_doc})" if hit_doc else ""
            flags.append(
                make_flag(
                    "rcpt_duplicate",
                    docs=[doc],
                    field="total",
                    region=_box(_c(doc, "total"), "total"),
                    discriminator=f"{fp[:12]}-{case_id}",
                    evidence=f"the same receipt was submitted in case {case_id}{where} on {when}",
                    explanation="This receipt was already submitted in an earlier case.",
                )
            )
    return flags
