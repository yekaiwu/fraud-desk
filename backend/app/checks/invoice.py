"""Invoice rules (docs/checks-financial.md section 5, plan 4.2).

Pure checks ``(ctx) -> list[Flag]``: amounts are ``Decimal``, "today" is ``ctx.today``, the
fingerprint store is only looked up (the pipeline records). Every extracted string is untrusted:
it only appears in evidence after whitespace collapse and truncation to 60 characters.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from stdnum.exceptions import ValidationError
from stdnum.sg import uen as sg_uen

from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext, natural_key
from app.core.fingerprints import invoice_fingerprint, invoice_key_fingerprint
from app.core.models import Cell, Document, Flag, Region, Row
from app.core.money import approx_eq, fmt_money, quantize
from app.core.textnorm import company_disc, company_key, company_match, uen_disc  # noqa: F401

ZERO = Decimal("0")
USABLE = ("ok", "partial")

# [confirm on IRAS] (plan 1.4): statutory GST rate by effective date, newest first.
GST_RATE_TABLE: list[tuple[date, str]] = [
    (date(2024, 1, 1), "9.00"),
    (date(2023, 1, 1), "8.00"),
    (date(2007, 7, 1), "7.00"),
]
# [confirm on IRAS] accepted compact GST registration formats (a valid UEN is also accepted).
GST_NO_PATTERNS: list[re.Pattern[str]] = [re.compile(r"^M\d{8}[A-Z]$")]
DEFAULT_GST_RATE = Decimal("9.00")
LINE_CAP = 5


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


def _docs(ctx: CaseContext, *types: str) -> list[Document]:
    return [d for d in ctx.docs(*types) if d.status in USABLE]


_EMPTY = Cell(value=None)


def _c(doc: Document, name: str) -> Cell:
    return doc.fields.get(name, _EMPTY)


def _rc(row: Row, name: str) -> Cell:
    return row.cells.get(name, _EMPTY)


def _regions(*cells: Cell, labels: tuple[str, ...] = ()) -> list[Region]:
    out: list[Region] = []
    for i, cell in enumerate(cells):
        if cell.box is not None:
            out.append(_lab(cell.box, labels[i] if i < len(labels) else None))  # type: ignore[arg-type]
    return out


def _num(d: Decimal) -> str:
    """Quantity-style number: no exponent, no trailing zeros."""
    text = format(d.normalize(), "f")
    return text


def _price(d: Decimal) -> str:
    return fmt_money(d) if d == quantize(d, 2) else format(d.normalize(), "f")


def _capped(items: list[Flag], cap: int) -> list[Flag]:
    if len(items) <= cap:
        return items
    kept = list(items[:cap])
    kept[-1] = kept[-1].model_copy(
        update={"evidence": f"{kept[-1].evidence}; +{len(items) - cap} more"}
    )
    return kept


# ---------------------------------------------------------------- inv_lines


@check(
    rule="inv_lines",
    applies_to={"invoice"},
    severity="high",
    use_cases={"aml", "loan"},
    description="Each invoice line's amount equals quantity times unit price and the lines add up to the subtotal.",
    tamper="An edited invoice line amount, quantity or unit price.",
)
def inv_lines(ctx: CaseContext) -> list[Flag]:
    tol = _tol(ctx)
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "invoice"):
        table = doc.tables.get("lines")
        if table is None or not table.rows:
            continue
        doc_flags: list[Flag] = []
        total_amount = ZERO
        n_amounts = 0
        did = False
        for i, row in enumerate(table.rows):
            amount = _rc(row, "amount").money()
            if amount is not None:
                total_amount += amount
                n_amounts += 1
            qty, price = _rc(row, "quantity").money(), _rc(row, "unit_price").money()
            if qty is None or price is None or amount is None:
                continue
            did = True
            expected = quantize(qty * price, 2)
            line_tol = Decimal("0.005") * abs(qty) + Decimal("0.005")
            diff = abs(amount - expected)
            # Exclusive of the tolerance itself (T-INV-LN-5: 37.52 vs 37.50 must flag).
            if diff >= line_tol:
                cells = {
                    "amount": _rc(row, "amount"),
                    "quantity": _rc(row, "quantity"),
                    "unit_price": _rc(row, "unit_price"),
                }
                doc_flags.append(
                    make_flag(
                        "inv_lines",
                        docs=[doc],
                        table="lines",
                        row=i,
                        region=_lab(cells["amount"].box, "amount"),
                        extra_regions=_regions(
                            cells["quantity"],
                            cells["unit_price"],
                            labels=("quantity", "unit_price"),
                        ),
                        evidence=(
                            f"row {i + 1}: quantity {_num(qty)} x unit price {_price(price)} = "
                            f"{fmt_money(expected)}; printed {fmt_money(amount)}; "
                            f"diff {fmt_money(diff)}" + _unverified(cells)
                        ),
                        explanation=f"The amount on line {i + 1} is not quantity times unit price.",
                    )
                )
        subtotal_cell = _c(doc, "subtotal")
        subtotal = subtotal_cell.money()
        if subtotal is not None and n_amounts:
            did = True
            total = _c(doc, "total").money()
            gst = _c(doc, "gst_amount").money() or ZERO
            ok = approx_eq(total_amount, subtotal, tol)
            if not ok and total is not None:
                ok = approx_eq(total_amount, total, tol) and approx_eq(subtotal + gst, total, tol)
            if not ok:
                doc_flags.append(
                    make_flag(
                        "inv_lines",
                        docs=[doc],
                        field="subtotal",
                        region=_lab(subtotal_cell.box, "subtotal"),
                        discriminator="sum",
                        evidence=(
                            f"lines sum {fmt_money(total_amount)}; printed subtotal "
                            f"{fmt_money(subtotal)}; diff {fmt_money(abs(total_amount - subtotal))}"
                            + _unverified({"subtotal": subtotal_cell})
                        ),
                        explanation="The invoice lines do not add up to the printed subtotal.",
                    )
                )
        if did:
            evaluated += 1
        flags.extend(_capped(doc_flags, LINE_CAP))
    if evaluated == 0:
        raise CheckSkipped("no invoice with line items to check")
    return flags


# ---------------------------------------------------------------- inv_gst


def _statutory_rate(on: date | None) -> Decimal | None:
    if on is None:
        return DEFAULT_GST_RATE
    for start, rate in GST_RATE_TABLE:
        if on >= start:
            return Decimal(rate)
    return None


@check(
    rule="inv_gst",
    applies_to={"invoice"},
    severity="high",
    use_cases={"aml", "loan"},
    description="The GST amount equals the GST rate applied to the subtotal, summed per line, or as printed per line.",
    tamper="An edited GST amount or total.",
)
def inv_gst(ctx: CaseContext) -> list[Flag]:
    tol = _tol(ctx)
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "invoice"):
        gst_cell, sub_cell = _c(doc, "gst_amount"), _c(doc, "subtotal")
        gst, subtotal = gst_cell.money(), sub_cell.money()
        if gst is None or subtotal is None:
            continue
        evaluated += 1
        inv_date = _c(doc, "invoice_date").date_()
        printed_rate = _c(doc, "gst_rate").money()
        statutory = _statutory_rate(inv_date)
        rate = printed_rate if printed_rate is not None else statutory

        rate_differs = (
            printed_rate is not None
            and inv_date is not None
            and statutory is not None
            and printed_rate not in (ZERO, statutory)
        )
        if rate_differs:
            rate_cell = _c(doc, "gst_rate")
            flags.append(
                make_flag(
                    "inv_gst",
                    docs=[doc],
                    field="gst_rate",
                    region=_lab(rate_cell.box, "gst_rate"),
                    extra_regions=_regions(_c(doc, "invoice_date"), labels=("invoice_date",)),
                    severity="medium",
                    discriminator="rate",
                    evidence=(
                        f"printed gst_rate {fmt_money(printed_rate)} on {inv_date.isoformat()}; "
                        f"statutory {fmt_money(statutory)}" + _unverified({"gst_rate": rate_cell})
                    ),
                    explanation="The GST rate printed is not the rate in force on the invoice date.",
                )
            )
        if rate is None:
            continue

        lines = [r for r in (doc.tables["lines"].rows if "lines" in doc.tables else [])]
        line_amounts = [(_rc(r, "amount").money(), _rc(r, "gst_amount").money()) for r in lines]
        with_amount = [(a, g) for a, g in line_amounts if a is not None]
        candidates: list[tuple[Decimal, str]] = [
            (quantize(subtotal * rate / 100, 2), f"net {fmt_money(rate)}% of {fmt_money(subtotal)}")
        ]
        if rate != ZERO and with_amount:
            if all(g is not None for _, g in with_amount):
                candidates.append((sum((g for _, g in with_amount), ZERO), "sum of line GST"))  # type: ignore[misc]
            candidates.append(
                (
                    sum((quantize(a * rate / 100, 2) for a, _ in with_amount), ZERO),
                    "per-line computed",
                )
            )
        if any(approx_eq(gst, value, tol) for value, _ in candidates):
            continue
        diff = min(abs(gst - value) for value, _ in candidates)
        listing = ", ".join(f"{fmt_money(v)} ({why})" for v, why in candidates)
        flags.append(
            make_flag(
                "inv_gst",
                docs=[doc],
                field="gst_amount",
                region=_lab(gst_cell.box, "gst_amount"),
                extra_regions=_regions(sub_cell, labels=("subtotal",)),
                evidence=(
                    f"gst_amount {fmt_money(gst)}; expected {listing}; diff {fmt_money(diff)}"
                    + _unverified({"gst_amount": gst_cell, "subtotal": sub_cell})
                ),
                explanation=(
                    f"The GST amount is not {fmt_money(rate)}% of the subtotal, computed on the "
                    "total or line by line."
                ),
            )
        )
    if evaluated == 0:
        raise CheckSkipped("no invoice with a subtotal and a GST amount")
    return flags


# ---------------------------------------------------------------- inv_total


@check(
    rule="inv_total",
    applies_to={"invoice"},
    severity="high",
    use_cases={"aml", "loan"},
    description="Subtotal plus GST equals the printed invoice total.",
    tamper="An edited invoice total.",
)
def inv_total(ctx: CaseContext) -> list[Flag]:
    tol = _tol(ctx)
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "invoice"):
        sub_cell, tot_cell, gst_cell = _c(doc, "subtotal"), _c(doc, "total"), _c(doc, "gst_amount")
        subtotal, total, gst = sub_cell.money(), tot_cell.money(), gst_cell.money()
        if subtotal is None or total is None:
            continue
        evaluated += 1
        computed = subtotal + (gst or ZERO)
        if approx_eq(computed, total, tol):
            continue
        note = " (no GST printed)" if gst is None else ""
        flags.append(
            make_flag(
                "inv_total",
                docs=[doc],
                field="total",
                region=_lab(tot_cell.box, "total"),
                extra_regions=_regions(sub_cell, gst_cell, labels=("subtotal", "gst_amount")),
                evidence=(
                    f"subtotal {fmt_money(subtotal)} + gst {fmt_money(gst or ZERO)} = "
                    f"{fmt_money(computed)}{note}; printed total {fmt_money(total)}; "
                    f"diff {fmt_money(abs(computed - total))}"
                    + _unverified({"total": tot_cell, "subtotal": sub_cell, "gst_amount": gst_cell})
                ),
                explanation=(
                    f"Subtotal plus GST is {fmt_money(computed)}, but the invoice prints a total "
                    f"of {fmt_money(total)}."
                ),
            )
        )
    if evaluated == 0:
        raise CheckSkipped("no invoice with a subtotal and a total")
    return flags


# ---------------------------------------------------------------- inv_ids


def _uen_error(value: str) -> str | None:
    """Exception class name when ``value`` fails python-stdnum UEN validation, else None."""
    try:
        sg_uen.validate(value)
    except ValidationError as exc:
        return type(exc).__name__
    return None


def _gst_no_ok(value: str) -> bool:
    compact = re.sub(r"[\s-]", "", value.upper())
    if any(p.match(compact) for p in GST_NO_PATTERNS):
        return True
    return _uen_error(compact) is None


@check(
    rule="inv_ids",
    applies_to={"invoice"},
    severity="high",
    use_cases={"aml", "loan"},
    description="The supplier and buyer UENs validate, the GST number has a recognised format, and GST charged comes with a GST number.",
    tamper="An invented supplier with a made-up UEN or GST registration number.",
)
def inv_ids(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "invoice"):
        sup, buy, gst_no = _c(doc, "supplier_uen"), _c(doc, "buyer_uen"), _c(doc, "supplier_gst_no")
        gst = _c(doc, "gst_amount").money()
        if sup.value or buy.value or gst_no.value or gst is not None:
            evaluated += 1
        if sup.value:
            err = _uen_error(sup.value)
            if err:
                flags.append(
                    make_flag(
                        "inv_ids",
                        docs=[doc],
                        field="supplier_uen",
                        region=_lab(sup.box, "supplier_uen"),
                        evidence=f"supplier_uen {_clip(sup.value)} fails UEN validation ({err})"
                        + _unverified({"supplier_uen": sup}),
                        explanation="The supplier UEN is not a valid Singapore UEN.",
                    )
                )
        if buy.value:
            err = _uen_error(buy.value)
            if err:
                flags.append(
                    make_flag(
                        "inv_ids",
                        docs=[doc],
                        field="buyer_uen",
                        region=_lab(buy.box, "buyer_uen"),
                        severity="medium",
                        discriminator="buyer_uen",
                        evidence=f"buyer_uen {_clip(buy.value)} fails UEN validation ({err})"
                        + _unverified({"buyer_uen": buy}),
                        explanation="The buyer UEN is not a valid Singapore UEN.",
                    )
                )
        if gst_no.value:
            if not _gst_no_ok(gst_no.value):
                flags.append(
                    make_flag(
                        "inv_ids",
                        docs=[doc],
                        field="supplier_gst_no",
                        region=_lab(gst_no.box, "supplier_gst_no"),
                        severity="medium",
                        discriminator="supplier_gst_no",
                        evidence=(
                            f"supplier_gst_no {_clip(gst_no.value)} does not match M + 8 digits + "
                            "letter and is not a valid UEN"
                            + _unverified({"supplier_gst_no": gst_no})
                        ),
                        explanation="The GST registration number is not in a recognised format.",
                    )
                )
        elif gst is not None and gst > ZERO:
            flags.append(
                make_flag(
                    "inv_ids",
                    docs=[doc],
                    field="supplier_gst_no",
                    region=_lab(_c(doc, "gst_amount").box, "gst_amount"),
                    severity="medium",
                    discriminator="gst_no_missing",
                    evidence=f"GST {fmt_money(gst)} charged but no supplier GST number printed",
                    explanation="GST is charged but no GST registration number is shown.",
                )
            )
    if evaluated == 0:
        raise CheckSkipped("no invoice has a supplier UEN, GST number or buyer UEN to test")
    return flags


# ---------------------------------------------------------------- inv_dates


@check(
    rule="inv_dates",
    applies_to={"invoice"},
    severity="medium",
    use_cases={"aml", "loan"},
    description="The invoice is not dated in the future, and its due and paid dates are not before the invoice date.",
    tamper="A back-dated or forward-dated invoice.",
)
def inv_dates(ctx: CaseContext) -> list[Flag]:
    today = ctx.today
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "invoice"):
        inv_cell, due_cell, paid_cell = (
            _c(doc, "invoice_date"),
            _c(doc, "due_date"),
            _c(doc, "paid_date"),
        )
        inv, due, paid = inv_cell.date_(), due_cell.date_(), paid_cell.date_()
        if inv is None:
            continue
        evaluated += 1
        extra = _regions(inv_cell, labels=("invoice_date",))
        if due is not None and due < inv:
            flags.append(
                make_flag(
                    "inv_dates",
                    docs=[doc],
                    field="due_date",
                    region=_lab(due_cell.box, "due_date"),
                    extra_regions=extra,
                    discriminator="due",
                    evidence=f"due_date {due.isoformat()} is before invoice_date {inv.isoformat()}"
                    + _unverified({"due_date": due_cell, "invoice_date": inv_cell}),
                    explanation="The invoice has a due date before it was issued.",
                )
            )
        if inv > today:
            flags.append(
                make_flag(
                    "inv_dates",
                    docs=[doc],
                    field="invoice_date",
                    region=_lab(inv_cell.box, "invoice_date"),
                    discriminator="future",
                    evidence=f"invoice_date {inv.isoformat()} is after today {today.isoformat()}"
                    + _unverified({"invoice_date": inv_cell}),
                    explanation="The invoice is dated in the future.",
                )
            )
        if paid is not None and paid < inv:
            flags.append(
                make_flag(
                    "inv_dates",
                    docs=[doc],
                    field="paid_date",
                    region=_lab(paid_cell.box, "paid_date"),
                    extra_regions=extra,
                    discriminator="paid_before",
                    evidence=f"paid_date {paid.isoformat()} is before invoice_date {inv.isoformat()}"
                    + _unverified({"paid_date": paid_cell, "invoice_date": inv_cell}),
                    explanation="The invoice has a paid date before it was issued.",
                )
            )
        if paid is not None and paid > today:
            flags.append(
                make_flag(
                    "inv_dates",
                    docs=[doc],
                    field="paid_date",
                    region=_lab(paid_cell.box, "paid_date"),
                    discriminator="paid_future",
                    evidence=f"paid_date {paid.isoformat()} is after today {today.isoformat()}"
                    + _unverified({"paid_date": paid_cell}),
                    explanation="The invoice shows a paid date in the future.",
                )
            )
    if evaluated == 0:
        raise CheckSkipped("no invoice has an invoice date")
    return flags


# ---------------------------------------------------------------- inv_parties


def _party_is(uen_a: str, name_a: str, uen_b: str, name_b: str) -> bool:
    """Same party: UEN equality when both UENs are present, else company-name match."""
    if uen_a and uen_b:
        return uen_a == uen_b
    return company_match(name_a, name_b)


def _norm_uen(value: str | None) -> str:
    return "".join((value or "").split()).upper()


def _quote(name: str | None, uen: str | None) -> str:
    return f'"{_clip(name)}"' + (f" ({_clip(uen)})" if uen else "")


@check(
    rule="inv_parties",
    applies_to={"invoice"},
    severity="medium",
    use_cases={"aml", "loan"},
    description="Seller and buyer are different companies and the applicant is one of the invoice parties or the buyer is a named counterparty.",
    tamper="Round-tripping between related companies, or an invoice between two unrelated companies.",
)
def inv_parties(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    applicant = ctx.applicant
    app_name, app_uen = (applicant.name or ""), _norm_uen(applicant.uen)
    counterparties = [
        _c(d, "buyer_name").text() for d in _docs(ctx, "contract", "purchase_order")
    ] + [_c(d, "counterparty_name").text() for d in _docs(ctx, "source_of_funds_declaration")]
    counterparties = [c for c in counterparties if c]
    evaluated = 0
    for doc in _docs(ctx, "invoice"):
        s_name_c, b_name_c = _c(doc, "supplier_name"), _c(doc, "buyer_name")
        s_name, b_name = s_name_c.text(), b_name_c.text()
        s_uen, b_uen = (
            _norm_uen(_c(doc, "supplier_uen").value),
            _norm_uen(_c(doc, "buyer_uen").value),
        )
        if not (s_name or b_name or s_uen or b_uen):
            continue
        evaluated += 1
        same = bool(s_uen and b_uen and s_uen == b_uen) or (
            bool(s_name and b_name) and company_match(s_name, b_name)
        )
        if same:
            flags.append(
                make_flag(
                    "inv_parties",
                    docs=[doc],
                    field="buyer_name",
                    region=_lab(b_name_c.box, "buyer_name"),
                    extra_regions=_regions(s_name_c, labels=("supplier_name",)),
                    discriminator="self",
                    evidence=(
                        f"supplier {_quote(s_name, s_uen)} and buyer {_quote(b_name, b_uen)} "
                        "are the same company"
                    ),
                    explanation="Seller and buyer on this invoice are the same company.",
                )
            )
        if not (app_name or app_uen):
            continue
        is_supplier = _party_is(app_uen, app_name, s_uen, s_name)
        is_buyer = _party_is(app_uen, app_name, b_uen, b_name)
        if is_supplier or is_buyer:
            continue
        if b_name and any(company_match(b_name, c) for c in counterparties):
            continue
        flags.append(
            make_flag(
                "inv_parties",
                docs=[doc],
                field="supplier_name",
                region=_lab(s_name_c.box, "supplier_name"),
                extra_regions=_regions(b_name_c, labels=("buyer_name",)),
                discriminator="applicant_absent",
                evidence=(
                    f'supplier "{_clip(s_name)}" and buyer "{_clip(b_name)}" are neither applicant '
                    f'"{_clip(app_name or app_uen)}" nor a named counterparty'
                ),
                explanation="Neither party on this invoice is the applicant.",
            )
        )
    if evaluated == 0:
        raise CheckSkipped("no invoice has party data")
    return flags


# ---------------------------------------------------------------- inv_duplicate


def _inv_keys(doc: Document) -> tuple[str | None, str | None]:
    """(full fingerprint, key fingerprint) from canonical cell values; None when inputs missing."""
    uen, num = _c(doc, "supplier_uen").value, _c(doc, "invoice_number").value
    if not uen or not num:
        return None, None
    key = invoice_key_fingerprint(uen, num)
    d, total = _c(doc, "invoice_date").value, _c(doc, "total").value
    full = invoice_fingerprint(uen, num, d, total) if d and total else None
    return full, key


@check(
    rule="inv_duplicate",
    applies_to={"invoice"},
    severity="high",
    use_cases={"aml", "loan"},
    description="The same invoice (supplier, number, date and total) is not submitted twice in this case or in an earlier case.",
    tamper="The same invoice submitted twice, or an invoice number reused with an edited total.",
)
def inv_duplicate(ctx: CaseContext) -> list[Flag]:
    invoices = _docs(ctx, "invoice")
    keyed = [(d, *_inv_keys(d)) for d in invoices]
    keyed = [(d, full, key) for d, full, key in keyed if key is not None]
    if not keyed:
        raise CheckSkipped("no invoice with a supplier UEN and an invoice number")
    flags: list[Flag] = []

    # 1. in-case: group by key (supplier UEN + number); the lowest document id is the original
    by_key: dict[str, list[tuple[Document, str | None]]] = defaultdict(list)
    for d, full, key in keyed:
        by_key[key].append((d, full))  # type: ignore[index]
    for key in sorted(by_key):
        group = sorted(by_key[key], key=lambda t: natural_key(t[0].id))
        if len(group) < 2:
            continue
        orig, orig_full = group[0]
        for dup, dup_full in group[1:]:
            identical = orig_full is not None and orig_full == dup_full
            num_c, uen_c = _c(dup, "invoice_number"), _c(dup, "supplier_uen")
            number = _clip(num_c.text())
            if identical:
                fp = orig_full
                evidence = (
                    f"invoice {number} of {_clip(uen_c.text())} on "
                    f"{_c(dup, 'invoice_date').text()} total "
                    f"{fmt_money(_c(dup, 'total').money() or ZERO)} also appears as {orig.id} "
                    f"(same fingerprint {fp[:8]})"  # type: ignore[index]
                )
            else:
                fp = key
                o_total, d_total = _c(orig, "total").money(), _c(dup, "total").money()
                if o_total != d_total:
                    why = "same number, different total"
                else:
                    why = "same number, different date"
                shown = fmt_money(o_total) if o_total is not None else "unknown"
                evidence = (
                    f"invoice {number} of {_clip(uen_c.text())} also appears as {orig.id} "
                    f"with total {shown} ({why})"
                )
            flags.append(
                make_flag(
                    "inv_duplicate",
                    docs=[orig, dup],
                    field="invoice_number",
                    region=_lab(num_c.box, "invoice_number"),
                    extra_regions=_regions(_c(orig, "invoice_number"), labels=("original",)),
                    discriminator=fp[:12],  # type: ignore[index]
                    evidence=evidence + _unverified({"invoice_number": num_c}),
                    explanation="This invoice was already submitted in this case.",
                )
            )

    # 2. store: earlier cases (the current case is excluded, so a rerun never matches itself)
    for d, full, key in keyed:
        seen: dict[str, tuple[str, str, str | None, str]] = {}
        for fp in (full, key):
            if fp is None:
                continue
            try:
                hits = ctx.fingerprints.lookup("invoice", fp, exclude_case_id=ctx.case.id)
            except Exception:  # noqa: BLE001 - an unavailable store must not break the check
                continue
            for hit in hits:
                seen.setdefault(
                    hit.case_id,
                    (fp, hit.case_id, hit.document_id, hit.created_at.date().isoformat()),
                )
        for case_id in sorted(seen):
            fp, _, hit_doc, when = seen[case_id]
            where = f" (document {hit_doc})" if hit_doc else ""
            num_c = _c(d, "invoice_number")
            flags.append(
                make_flag(
                    "inv_duplicate",
                    docs=[d],
                    field="invoice_number",
                    region=_lab(num_c.box, "invoice_number"),
                    discriminator="store:" + case_id,
                    evidence=f"fingerprint {fp[:8]} seen in case {case_id}{where} on {when}",
                    explanation=f"This invoice was already submitted in case {case_id}.",
                )
            )
    return flags


# ---------------------------------------------------------------- inv_sequence

_NUMBER_TAIL = re.compile(r"^(.*?)(\d+)$")


@check(
    rule="inv_sequence",
    applies_to={"invoice"},
    severity="low",
    use_cases={"aml", "loan"},
    description="Invoice numbers from one supplier are not reused or back-dated, and gaps in the range are noted.",
    tamper="Fabricated invoices with reused or back-dated numbering.",
)
def inv_sequence(ctx: CaseContext) -> list[Flag]:
    groups: dict[tuple[str, str, int], list[tuple[int, Document, str]]] = defaultdict(list)
    for doc in _docs(ctx, "invoice"):
        raw = _c(doc, "invoice_number").text()
        m = _NUMBER_TAIL.match(raw)
        if not m:
            continue
        supplier = _norm_uen(_c(doc, "supplier_uen").value) or company_key(
            _c(doc, "supplier_name").text()
        )
        if not supplier:
            continue
        prefix, digits = m.group(1), m.group(2)
        width = len(digits) if digits.startswith("0") else 0
        groups[(supplier, prefix, width)].append((int(digits), doc, raw))

    flags: list[Flag] = []
    evaluated = 0
    for gkey in sorted(groups):
        members = sorted(groups[gkey], key=lambda t: (t[0], natural_key(t[1].id)))
        if len(members) < 3:
            continue
        evaluated += 1
        _, prefix, width = gkey
        fmt = (
            (lambda n, p=prefix, w=width: f"{p}{n:0{w}d}")
            if width
            else (lambda n, p=prefix: f"{p}{n}")
        )
        group_flags: list[Flag] = []

        # reuse: the same number twice with different date or total
        by_num: dict[int, list[tuple[int, Document, str]]] = defaultdict(list)
        for m_ in members:
            by_num[m_[0]].append(m_)
        for number in sorted(by_num):
            same = by_num[number]
            first = same[0]
            for other in same[1:]:
                f_d, o_d = _c(first[1], "invoice_date").value, _c(other[1], "invoice_date").value
                f_t, o_t = _c(first[1], "total").money(), _c(other[1], "total").money()
                if f_d == o_d and f_t == o_t:
                    continue  # identical content is inv_duplicate
                num_c = _c(other[1], "invoice_number")
                group_flags.append(
                    make_flag(
                        "inv_sequence",
                        docs=[first[1], other[1]],
                        field="invoice_number",
                        region=_lab(num_c.box, "invoice_number"),
                        discriminator="reuse",
                        evidence=(
                            f"{_clip(other[2])} appears on {first[1].id} (dated {f_d}) and "
                            f"{other[1].id} (dated {o_d}) with different content"
                        ),
                        explanation="The invoice numbering from this supplier looks out of sequence.",
                    )
                )

        # order: dated earlier than a lower-numbered invoice of the same group
        best: tuple[date, Document, str] | None = None  # latest date among lower numbers
        lower_pending: list[tuple[date, Document, str]] = []
        current_number: int | None = None
        for number, doc, raw in members:
            if number != current_number:
                for item in lower_pending:
                    if best is None or item[0] > best[0]:
                        best = item
                lower_pending = []
                current_number = number
            d = _c(doc, "invoice_date").date_()
            if d is None:
                continue
            if best is not None and d < best[0]:
                num_c = _c(doc, "invoice_number")
                group_flags.append(
                    make_flag(
                        "inv_sequence",
                        docs=[best[1], doc],
                        field="invoice_number",
                        region=_lab(num_c.box, "invoice_number"),
                        extra_regions=_regions(
                            _c(best[1], "invoice_number"), labels=("earlier number",)
                        ),
                        discriminator="order",
                        evidence=(
                            f"{_clip(raw)} dated {d.isoformat()} precedes {_clip(best[2])} "
                            f"dated {best[0].isoformat()}"
                        ),
                        explanation="The invoice numbering from this supplier looks out of sequence.",
                    )
                )
            lower_pending.append((d, doc, raw))
        flags.extend(_capped(group_flags, LINE_CAP))

        # gap: info only, one flag per group on the first invoice
        numbers = sorted(by_num)
        gaps = [(a, b) for a, b in zip(numbers, numbers[1:], strict=False) if b - a > 1]
        if gaps:
            parts = []
            for a, b in gaps[:3]:
                if b - a == 2:
                    parts.append(f"number {fmt(a + 1)} absent between {fmt(a)} and {fmt(b)}")
                else:
                    parts.append(
                        f"numbers {fmt(a + 1)} to {fmt(b - 1)} absent between {fmt(a)} and {fmt(b)}"
                    )
            more = f"; +{len(gaps) - 3} more gap(s)" if len(gaps) > 3 else ""
            first_doc = members[0][1]
            flags.append(
                make_flag(
                    "inv_sequence",
                    docs=[m_[1] for m_ in members],
                    field="invoice_number",
                    region=_lab(_c(first_doc, "invoice_number").box, "invoice_number"),
                    severity="info",
                    discriminator="gap",
                    evidence="; ".join(parts) + more,
                    explanation="Some invoice numbers in this range are not in the bundle.",
                )
            )
    if evaluated == 0:
        raise CheckSkipped("no supplier has three invoices with a numeric tail")
    return flags
