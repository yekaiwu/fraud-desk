"""FraudRAG-style local test: five published fraud categories as simple PDF bank statements.

RE-IMPLEMENTED FROM THE PUBLISHED DESCRIPTION, NOT THE ORIGINAL DATASET. FraudRAG-2400 is not
obtainable (its maintainer states the dataset is missing) and nothing from that repository was run
or copied. Only the names of the categories and the idea of "manipulation, then compare" were read
as text: balance manipulation, amount (digit) substitution, date forgery, entity-name mismatch and
fabricated transactions. The statements below are our own: a seeded generator, two table layouts,
Singapore-style fictitious companies and fictitious counterparties. No real person, bank or
account number is involved.

What is measured: the arithmetic, date and entity logic of the repo (``bs_totals``,
``bs_running_balance``, ``bs_dates``, ``bs_holder``, ``x_entity``, ``bs_round_numbers``,
``bs_period_continuity`` ...) on digital PDFs read by the repo's deterministic text-layer extractor
(``extractor_mode="text_layer"``, Fake LLM, no network). Every category has a NAIVE variant (the
forger changes one printed thing and nothing else) and, where it makes sense, a CONSISTENT variant
(every dependent balance and total recomputed, as the published LedgerShift manipulations do). A
consistent forgery cannot be caught from one statement by arithmetic, so a miss there is a limit of
the method, reported as such and never "fixed". Because the forgery is regenerated at data level
and rendered afresh, there are no PDF edit traces (metadata, fonts, hidden text) either: this is the
forger's best case for the PDF-forensics family, not a measurement of it.

Before any result was looked at, each variant was assigned the rules that ought to catch it
(``EXPECTED``). A case counts as detected when one of those rules raises a medium or high flag that
the matching clean statement does not. Nothing was tuned on these results: the generator and the
layouts were debugged on a development seed (``DEV_SEED``) and the report is run on a different
held-out seed (``REPORT_SEED``).

    uv run python backend/scripts/ext_eval/fraudrag_local.py run --workers 4 \
        --out docs/eval-results/external
    uv run python backend/scripts/ext_eval/fraudrag_local.py preview --dir /tmp/fraudrag-preview
"""

from __future__ import annotations

import argparse
import calendar
import csv
import io
import json
import random
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from reportlab.pdfbase.pdfmetrics import stringWidth  # noqa: E402
from reportlab.pdfgen import canvas as rl_canvas  # noqa: E402

from scripts._eval import metrics as mx  # noqa: E402
from scripts._eval.runner import make_settings, new_db, run_pool  # noqa: E402

DEV_SEED = 11
REPORT_SEED = 20261029
TODAY = date(2026, 10, 27)
ROWS_PER_PAGE = 22
A4_W, A4_H = 595.28, 841.89
CENT = Decimal("0.01")
SENTENCE = (
    "Re-implemented from the published description of FraudRAG (five fraud categories); not the "
    "original FraudRAG-2400 dataset, which is not obtainable. Statements are our own."
)

CATEGORIES = (
    "balance_manipulation",
    "amount_digit_substitution",
    "date_forgery",
    "entity_name_mismatch",
    "fabricated_transactions",
)

ARITH = ("bs_totals", "bs_running_balance")
# Declared before running: the rules that ought to catch each variant. ``None`` = expected NOT to be
# caught by any rule on one statement (reported as a limit, not as a failure of the harness).
EXPECTED: dict[str, tuple[str, ...] | None] = {
    "balance_manipulation/closing_header": ARITH,
    "balance_manipulation/row_balance": ARITH,
    "balance_manipulation/shifted_consistent": None,
    "balance_manipulation/continuity_consistent": ("bs_period_continuity",),
    "amount_digit_substitution/naive": ARITH,
    "amount_digit_substitution/consistent": None,
    "date_forgery/outside_period": ("bs_dates",),
    "date_forgery/out_of_order": ("bs_dates",),
    "date_forgery/invalid_calendar": ("bs_dates",),
    "date_forgery/shift_in_order": None,
    "entity_name_mismatch/unrelated_company": ("bs_holder", "x_entity"),
    "entity_name_mismatch/similar_name": ("bs_holder", "x_entity"),
    "entity_name_mismatch/one_char_typo": ("bs_holder", "x_entity"),
    "entity_name_mismatch/person_name": ("bs_holder", "x_entity"),
    "entity_name_mismatch/suffix_only_benign": None,
    "fabricated_transactions/insert_naive": ARITH,
    "fabricated_transactions/insert_consistent_plain": None,
    "fabricated_transactions/insert_consistent_round": ("bs_round_numbers",),
    "fabricated_transactions/insert_round_in_sequence": ("bs_round_numbers",),
    "fabricated_transactions/insert_round_in_sequence_many": ("bs_round_numbers",),
}
# Variants that are NOT forgeries: a flag on them is a false alarm, not a detection.
BENIGN = {"entity_name_mismatch/suffix_only_benign"}
N_CLEAN = 60
SEVERE = ("high", "medium")

# ------------------------------------------------------------------------------ data


def q2(d: Decimal | int | str) -> Decimal:
    return Decimal(d).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Row:
    date_raw: str  # exactly what is printed
    date: date | None  # None when the printed date is not a real calendar date
    desc: str
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal


@dataclass(frozen=True)
class Stmt:
    sid: str
    layout: str  # two_col | one_col
    holder: str
    account: str
    start: date
    end: date
    opening: Decimal
    closing: Decimal  # as printed
    total_debits: Decimal  # as printed
    total_credits: Decimal  # as printed
    rows: tuple[Row, ...]
    bank: str = "FICTIONAL TEST BANK"


@dataclass
class Truth:
    """What the forger did: enough to localise a flag and to bucket the result."""

    category: str = ""
    variant: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    rows: list[int] = field(default_factory=list)  # printed row indices touched


@dataclass
class CaseSpec:
    case_id: str
    category: str  # a CATEGORIES name, or "clean"
    variant: str
    statements: list[Stmt]
    truth: Truth
    applicant: str
    seed: int


# ------------------------------------------------------------------------------ generation

_ADJ = (
    "Apex",
    "Harbour",
    "Lumen",
    "Orchid",
    "Banyan",
    "Sterling",
    "Meridian",
    "Kestrel",
    "Tanjong",
)
_NOUN = (
    "Logistics",
    "Trading",
    "Engineering",
    "Foods",
    "Marine",
    "Textiles",
    "Digital",
    "Supplies",
)
_TAIL = ("Holdings", "Resources", "Services", "Solutions", "Industries", "Ventures")
_SUPPLIERS = (
    "Delta Packaging", "Rowan Freight", "Pinnacle Stationery", "Bluewater Utilities",
    "Nimbus Cloud", "Crestline Insurance", "Oakridge Property", "Falcon Fuel", "Quill Printing",
    "Summit Hardware", "Lotus Catering", "Vertex Telecom",
)  # fmt: skip
_CUSTOMERS = (
    "Amber Retail", "Kopi Corner", "Zenith Clinic", "Horizon Schools", "Riverside Hotel",
    "Granite Builders", "Willow Pharmacy", "Cobalt Motors", "Maple Studios", "Jade Garden",
)  # fmt: skip
_PEOPLE = ("Tan Wei Ming", "Lim Hui Ling", "Wong Kah Seng", "Ng Siew Lan", "Chua Boon Kiat")
_DEBIT_KINDS = (
    "GIRO PAYMENT",
    "FAST PAYMENT TO",
    "CHEQUE CLEARING",
    "CARD PURCHASE",
    "BILL PAYMENT",
)
_CREDIT_KINDS = ("FAST TRANSFER FROM", "GIRO COLLECTION", "INWARD REMITTANCE", "CHEQUE DEPOSIT")


def company_name(rng: random.Random) -> str:
    parts = [rng.choice(_ADJ), rng.choice(_NOUN)]
    if rng.random() < 0.35:
        parts.append(rng.choice(_TAIL))
    return " ".join(parts) + " Pte Ltd"


def _cents(rng: random.Random, lo: int, hi: int, round_share: float = 0.04) -> Decimal:
    """A log-uniform amount with mostly non-zero cents (round sums are rare, as in real ledgers)."""
    import math

    whole = int(math.exp(rng.uniform(math.log(lo), math.log(hi))))
    if rng.random() < round_share:
        return Decimal(max(lo, round(whole, -1))).quantize(CENT)
    return Decimal(whole) + Decimal(rng.randint(1, 99)) / 100


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def make_statement(
    rng: random.Random,
    sid: str,
    *,
    holder: str | None = None,
    account: str | None = None,
    year: int | None = None,
    month: int | None = None,
    opening: Decimal | None = None,
    layout: str | None = None,
    n_rows: int | None = None,
) -> Stmt:
    holder = holder or company_name(rng)
    account = (
        account or f"{rng.randint(100, 999)}-{rng.randint(100000, 999999)}-{rng.randint(1, 9)}"
    )
    year = year or 2026
    month = month or rng.randint(1, 9)
    start, end = month_bounds(year, month)
    layout = layout or rng.choice(("two_col", "one_col"))
    n = n_rows or rng.randint(14, 34)
    opening = opening if opening is not None else q2(rng.randint(30_000, 150_000) + rng.random())
    days = sorted(rng.randint(1, end.day) for _ in range(n))
    rows: list[Row] = []
    balance = opening
    for i, day in enumerate(days):
        when = date(year, month, day)
        credit_turn = balance < 8_000 or rng.random() < 0.42
        if credit_turn:
            amt = _cents(rng, 300, 15_000)
            desc = f"{rng.choice(_CREDIT_KINDS)} {rng.choice(_CUSTOMERS).upper()} REF{rng.randint(10000, 99999)}"
            debit, credit = None, amt
            balance += amt
        else:
            amt = min(_cents(rng, 12, 6_000), balance - Decimal("500"))
            amt = max(amt, Decimal("12.35"))
            desc = f"{rng.choice(_DEBIT_KINDS)} {rng.choice(_SUPPLIERS).upper()} INV{rng.randint(1000, 9999)}"
            debit, credit = amt, None
            balance -= amt
        rows.append(Row(_fmt_date(when), when, desc, debit, credit, q2(balance)))
        del i
    st = Stmt(
        sid=sid,
        layout=layout,
        holder=holder,
        account=account,
        start=start,
        end=end,
        opening=q2(opening),
        closing=q2(balance),
        total_debits=Decimal(0),
        total_credits=Decimal(0),
        rows=tuple(rows),
    )
    return recompute(st, balances=False)


def _fmt_date(d: date) -> str:
    return f"{d.day:02d} {calendar.month_abbr[d.month]} {d.year}"


def recompute(st: Stmt, *, balances: bool = True) -> Stmt:
    """Totals and closing from the rows; with ``balances`` every running balance as well (this is
    what a careful forger does after any edit)."""
    rows = list(st.rows)
    if balances:
        bal = st.opening
        for i, r in enumerate(rows):
            bal = bal + (r.credit or Decimal(0)) - (r.debit or Decimal(0))
            rows[i] = replace(r, balance=q2(bal))
    td = q2(sum((r.debit or Decimal(0) for r in rows), Decimal(0)))
    tc = q2(sum((r.credit or Decimal(0) for r in rows), Decimal(0)))
    closing = rows[-1].balance if rows else st.opening
    return replace(st, rows=tuple(rows), total_debits=td, total_credits=tc, closing=closing)


# ------------------------------------------------------------------------------ rendering


def _amount(d: Decimal) -> str:
    return f"{abs(q2(d)):,.2f}"


def render_pdf(st: Stmt) -> bytes:
    """A digital PDF with a text layer, invariant bytes for a given statement."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(A4_W, A4_H), invariant=1, pageCompression=1)
    n_pages = max(1, -(-len(st.rows) // ROWS_PER_PAGE))

    def text(x: float, y: float, s: str, size: float = 8.5, bold: bool = False, align: str = "l"):
        font = "Helvetica-Bold" if bold else "Helvetica"
        w = stringWidth(s, font, size)
        left = x if align == "l" else x - w
        c.setFont(font, size)
        c.drawString(left, A4_H - y - size * 0.82, s)

    one = st.layout == "one_col"
    cols = (
        [("Date", 36, "l"), ("Description", 104, "l"), ("Amount", 450, "r"), ("Balance", 559, "r")]
        if one
        else [
            ("Date", 36, "l"),
            ("Description", 100, "l"),
            ("Withdrawals", 380, "r"),
            ("Deposits", 465, "r"),
            ("Balance", 559, "r"),
        ]
    )
    for pno in range(1, n_pages + 1):
        if pno > 1:
            c.showPage()
        text(36, 40, st.bank, 14, True)
        if pno == 1:
            text(36, 62, "Statement of Account - Current Account", 10.5, True)
            text(36, 90, f"Account Name: {st.holder}", 9, True)
            text(36, 104, f"Account Number: {st.account}", 8.5)
            text(36, 118, f"Statement Period: {_fmt_date(st.start)} to {_fmt_date(st.end)}", 8.5)
            text(36, 132, "Currency: SGD", 8.5)
            text(330, 90, "Account summary", 8.5, True)
            text(330, 104, "Opening Balance", 8.5)
            text(559, 104, _amount(st.opening), 8.5, align="r")
            text(330, 118, "Closing Balance", 8.5)
            text(559, 118, _amount(st.closing), 8.5, align="r")
            text(330, 132, "Total Debits", 8.5)
            text(559, 132, _amount(st.total_debits), 8.5, align="r")
            text(330, 146, "Total Credits", 8.5)
            text(559, 146, _amount(st.total_credits), 8.5, align="r")
            y0 = 180.0
        else:
            text(36, 58, f"{st.holder}  -  Account Number: {st.account}  (continued)", 8.5)
            y0 = 100.0
        for head, x, al in cols:
            text(x, y0, head, 8, True, al)
        c.setLineWidth(0.8)
        c.line(36, A4_H - y0 - 12, 559, A4_H - y0 - 12)
        y = y0 + 20
        for r in st.rows[(pno - 1) * ROWS_PER_PAGE : pno * ROWS_PER_PAGE]:
            text(36, y, r.date_raw, 7.5)
            text(cols[1][1], y, r.desc[:52], 7.5)
            if one:
                if r.debit is not None:
                    text(450, y, f"{_amount(r.debit)} DR", 7.5, align="r")
                elif r.credit is not None:
                    text(450, y, f"{_amount(r.credit)} CR", 7.5, align="r")
            else:
                if r.debit is not None:
                    text(380, y, _amount(r.debit), 7.5, align="r")
                if r.credit is not None:
                    text(465, y, _amount(r.credit), 7.5, align="r")
            text(559, y, _amount(r.balance), 7.5, align="r")
            y += 15
        text(559, 800, f"Page {pno} of {n_pages}", 8, align="r")
        text(36, 800, "SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT", 7)
    c.save()
    return buf.getvalue()


# ------------------------------------------------------------------------------ manipulations


def _pick_row(rng: random.Random, st: Stmt, *, interior: bool = True) -> int:
    n = len(st.rows)
    lo, hi = (2, n - 3) if interior else (0, n - 1)
    return rng.randint(lo, max(lo, hi))


def _sub_digit(rng: random.Random, amount: Decimal) -> Decimal:
    """Change one digit of the integer part (or the cents) so the amount really changes."""
    s = f"{amount:.2f}"
    pos = [i for i, ch in enumerate(s) if ch.isdigit() and not (i == 0 and ch in "0")]
    for _ in range(20):
        i = rng.choice(pos)
        choices = [d for d in "0123456789" if d != s[i] and not (i == 0 and d == "0")]
        new = s[:i] + rng.choice(choices) + s[i + 1 :]
        out = Decimal(new)
        if out != amount and out > 0:
            return out
    return amount + Decimal("100.00")


def balance_closing_header(rng: random.Random, st: Stmt, delta: Decimal) -> tuple[Stmt, Truth]:
    sign = 1 if rng.random() < 0.5 else -1
    return replace(st, closing=q2(st.closing + sign * delta)), Truth(
        "balance_manipulation", "closing_header", {"delta": str(delta)}
    )


def balance_row(rng: random.Random, st: Stmt, delta: Decimal) -> tuple[Stmt, Truth]:
    i = _pick_row(rng, st)
    sign = 1 if rng.random() < 0.5 else -1
    rows = list(st.rows)
    rows[i] = replace(rows[i], balance=q2(rows[i].balance + sign * delta))
    return replace(st, rows=tuple(rows)), Truth(
        "balance_manipulation", "row_balance", {"delta": str(delta)}, [i]
    )


def balance_shifted(rng: random.Random, st: Stmt, delta: Decimal) -> tuple[Stmt, Truth]:
    """Opening balance raised and everything downstream recomputed: internally consistent."""
    out = recompute(replace(st, opening=q2(st.opening + delta)))
    return out, Truth("balance_manipulation", "shifted_consistent", {"delta": str(delta)})


def amount_digit(rng: random.Random, st: Stmt, *, consistent: bool) -> tuple[Stmt, Truth]:
    i = _pick_row(rng, st)
    row = st.rows[i]
    old = row.debit if row.debit is not None else row.credit
    assert old is not None
    new = _sub_digit(rng, old)
    rows = list(st.rows)
    rows[i] = replace(row, debit=new) if row.debit is not None else replace(row, credit=new)
    out = replace(st, rows=tuple(rows))
    # naive: printed totals and balances are left as they were
    out = recompute(out) if consistent else replace(out, closing=st.closing)
    diff = abs(new - old)
    return out, Truth(
        "amount_digit_substitution",
        "consistent" if consistent else "naive",
        {
            "old": str(old),
            "new": str(new),
            "diff": str(diff),
            "side": "debit" if row.debit else "credit",
        },
        [i],
    )


def date_outside_period(rng: random.Random, st: Stmt) -> tuple[Stmt, Truth]:
    i = _pick_row(rng, st)
    other = date(st.start.year, st.start.month, 1)
    prev_month = (other - timedelta(days=1)).replace(day=rng.randint(10, 27))
    rows = list(st.rows)
    rows[i] = replace(rows[i], date=prev_month, date_raw=_fmt_date(prev_month))
    return replace(st, rows=tuple(rows)), Truth("date_forgery", "outside_period", {}, [i])


def date_out_of_order(rng: random.Random, st: Stmt) -> tuple[Stmt, Truth]:
    """A date inside the period but earlier than the row before it by at least a week."""
    cands = [
        i for i in range(1, len(st.rows)) if st.rows[i - 1].date and st.rows[i - 1].date.day >= 10
    ]
    if not cands:
        cands = [len(st.rows) - 1]
    i = rng.choice(cands)
    prev = st.rows[i - 1].date
    assert prev is not None
    when = date(prev.year, prev.month, max(1, prev.day - rng.randint(7, 9)))
    rows = list(st.rows)
    rows[i] = replace(rows[i], date=when, date_raw=_fmt_date(when))
    return replace(st, rows=tuple(rows)), Truth("date_forgery", "out_of_order", {}, [i])


def date_invalid(rng: random.Random, st: Stmt) -> tuple[Stmt, Truth]:
    i = _pick_row(rng, st)
    feb = date(st.start.year, 2, 1)
    raw = rng.choice(
        (f"30 Feb {feb.year}", f"31 Feb {feb.year}", f"31 Apr {feb.year}", f"31 Jun {feb.year}")
    )
    rows = list(st.rows)
    rows[i] = replace(rows[i], date=None, date_raw=raw)
    return replace(st, rows=tuple(rows)), Truth(
        "date_forgery", "invalid_calendar", {"raw": raw}, [i]
    )


def date_shift_in_order(rng: random.Random, st: Stmt) -> tuple[Stmt, Truth]:
    """Move one date by one to three days while keeping the order and the period: plausible."""
    for _ in range(50):
        i = _pick_row(rng, st)
        d = st.rows[i].date
        if d is None:
            continue
        shift = rng.choice((-3, -2, -1, 1, 2, 3))
        when = date(
            d.year, d.month, min(max(1, d.day + shift), calendar.monthrange(d.year, d.month)[1])
        )
        lo = st.rows[i - 1].date or st.start
        hi = st.rows[i + 1].date or st.end
        if when != d and lo <= when <= hi:
            rows = list(st.rows)
            rows[i] = replace(rows[i], date=when, date_raw=_fmt_date(when))
            return replace(st, rows=tuple(rows)), Truth(
                "date_forgery", "shift_in_order", {"shift": shift}, [i]
            )
    return date_outside_period(rng, st)


def entity_change(rng: random.Random, st: Stmt, kind: str) -> tuple[Stmt, Truth]:
    name = st.holder
    if kind == "unrelated_company":
        new = company_name(rng)
        while new == name:
            new = company_name(rng)
    elif kind == "similar_name":
        words = name.split()
        core = words[:-2]  # drop "Pte Ltd"
        if len(core) >= 3:
            core = core[:-1]
        else:
            core = [*core, rng.choice(("Group", "International", "Services"))]
        new = " ".join([*core, "Pte Ltd"])
    elif kind == "one_char_typo":
        letters = [i for i, ch in enumerate(name) if ch.isalpha() and i < len(name) - 8]
        i = rng.choice(letters)
        repl = "e" if name[i].lower() != "e" else "a"
        new = name[:i] + repl + name[i + 1 :]
    elif kind == "person_name":
        new = rng.choice(_PEOPLE)
    elif kind == "suffix_only_benign":
        new = name.replace(" Pte Ltd", " Pte. Ltd.")
    else:
        raise ValueError(kind)
    return replace(st, holder=new), Truth("entity_name_mismatch", kind, {"printed": new})


def fabricate_insert(
    rng: random.Random,
    st: Stmt,
    *,
    consistent: bool,
    round_sum: bool = False,
    count: tuple[int, int] = (1, 3),
) -> tuple[Stmt, Truth]:
    k = rng.randint(*count)
    rows = list(st.rows)
    touched: list[int] = []
    for _ in range(k):
        pos = rng.randint(2, max(2, len(rows) - 3))
        prev = rows[pos - 1]
        when = prev.date or st.start
        amt = (
            Decimal(rng.choice((20_000, 30_000, 50_000, 80_000)))
            if round_sum
            else _cents(rng, 2_000, 20_000, round_share=0)
        )
        desc = f"FAST TRANSFER FROM {rng.choice(_CUSTOMERS).upper()} REF{rng.randint(10000, 99999)}"
        rows.insert(pos, Row(_fmt_date(when), when, desc, None, amt, q2(prev.balance + amt)))
        touched = [t + 1 if t >= pos else t for t in touched]
        touched.append(pos)
    out = replace(st, rows=tuple(rows))
    if consistent:
        out = recompute(out)
    return out, Truth(
        "fabricated_transactions",
        "insert_naive"
        if not consistent
        else ("insert_consistent_round" if round_sum else "insert_consistent_plain"),
        {"inserted": k},
        sorted(touched),
    )


# ------------------------------------------------------------------------------ the suite

CLOSING_DELTAS = ("0.01", "0.50", "1.00", "5.00", "50.00", "500.00", "5000.00")


def build_cases(seed: int, scale: int = 1) -> list[CaseSpec]:
    """The whole suite. ``scale`` multiplies the per-variant sample (4 per delta, 24 for others)."""
    cases: list[CaseSpec] = []
    counter = 0

    def new_base() -> tuple[random.Random, Stmt]:
        nonlocal counter
        counter += 1
        r = random.Random(f"{seed}:{counter}")
        return r, make_statement(r, f"s{counter:04d}")

    def add(category: str, variant: str, fn: Callable[[random.Random, Stmt], tuple[Stmt, Truth]]):
        r, base = new_base()
        stmt, truth = fn(r, base)
        cases.append(
            CaseSpec(
                case_id=f"{category}__{variant}__{base.sid}",
                category=category,
                variant=variant,
                statements=[replace(stmt, sid=base.sid)],
                truth=truth,
                applicant=base.holder,
                seed=seed,
            )
        )

    for _ in range(N_CLEAN * scale):
        r, base = new_base()
        cases.append(
            CaseSpec(f"clean__{base.sid}", "clean", "clean", [base], Truth(), base.holder, seed)
        )
    for delta in CLOSING_DELTAS:
        d = Decimal(delta)
        for _ in range(4 * scale):
            add(
                "balance_manipulation",
                "closing_header",
                lambda r, s, d=d: balance_closing_header(r, s, d),
            )
        for _ in range(4 * scale):
            add("balance_manipulation", "row_balance", lambda r, s, d=d: balance_row(r, s, d))
    for delta in ("100.00", "1000.00", "10000.00"):
        d = Decimal(delta)
        for _ in range(8 * scale):
            add(
                "balance_manipulation",
                "shifted_consistent",
                lambda r, s, d=d: balance_shifted(r, s, d),
            )
    for _ in range(40 * scale):
        add("amount_digit_substitution", "naive", lambda r, s: amount_digit(r, s, consistent=False))
    for _ in range(40 * scale):
        add(
            "amount_digit_substitution",
            "consistent",
            lambda r, s: amount_digit(r, s, consistent=True),
        )
    for variant, fn in (
        ("outside_period", date_outside_period),
        ("out_of_order", date_out_of_order),
        ("invalid_calendar", date_invalid),
        ("shift_in_order", date_shift_in_order),
    ):
        for _ in range(24 * scale):
            add("date_forgery", variant, fn)
    for kind in (
        "unrelated_company",
        "similar_name",
        "one_char_typo",
        "person_name",
        "suffix_only_benign",
    ):
        for _ in range(24 * scale):
            add("entity_name_mismatch", kind, lambda r, s, k=kind: entity_change(r, s, k))
    for _ in range(30 * scale):
        add(
            "fabricated_transactions",
            "insert_naive",
            lambda r, s: fabricate_insert(r, s, consistent=False),
        )
    for _ in range(30 * scale):
        add(
            "fabricated_transactions",
            "insert_consistent_plain",
            lambda r, s: fabricate_insert(r, s, consistent=True),
        )
    for _ in range(30 * scale):
        add(
            "fabricated_transactions",
            "insert_consistent_round",
            lambda r, s: fabricate_insert(r, s, consistent=True, round_sum=True),
        )
    # continuity: three consecutive months of one account; the middle month is shifted consistently
    for n in range(20 * scale):
        for mode in ("clean", "shift", "round", "round_many"):
            r = random.Random(f"{seed}:cont:{n}")
            holder, account = (
                company_name(r),
                f"{r.randint(100, 999)}-{r.randint(100000, 999999)}-{r.randint(1, 9)}",
            )
            layout = r.choice(("two_col", "one_col"))
            first_month = r.randint(1, 6)
            stmts: list[Stmt] = []
            opening: Decimal | None = None
            for m in range(3):
                s = make_statement(
                    r, f"c{n:03d}m{m}", holder=holder, account=account, month=first_month + m,
                    opening=opening, layout=layout, n_rows=r.randint(14, 24),
                )  # fmt: skip
                stmts.append(s)
                opening = s.closing
            truth = Truth()
            if mode in ("round", "round_many"):
                # the LAST month gets round credits and is recomputed: the neighbours stay
                # consistent, and the round-number rule has a baseline to compare against
                many = mode == "round_many"
                stmts[2], t = fabricate_insert(
                    random.Random(f"{seed}:round:{n}"),
                    stmts[2],
                    consistent=True,
                    round_sum=True,
                    count=(5, 8) if many else (1, 3),
                )
                var = "insert_round_in_sequence_many" if many else "insert_round_in_sequence"
                truth = Truth("fabricated_transactions", var, t.detail, t.rows)
                cat = "fabricated_transactions"
            elif mode == "shift":
                delta = Decimal(r.choice(("500.00", "5000.00", "25000.00")))
                mid = recompute(replace(stmts[1], opening=q2(stmts[1].opening + delta)))
                stmts[1] = mid
                truth = Truth(
                    "balance_manipulation", "continuity_consistent", {"delta": str(delta)}
                )
                cat, var = "balance_manipulation", "continuity_consistent"
            else:
                cat, var = "clean", "clean_continuity"
            cases.append(CaseSpec(f"{cat}__{var}__{n:03d}", cat, var, stmts, truth, holder, seed))
    return cases


# ------------------------------------------------------------------------------ running


def _summarise_case(case: Any, spec: CaseSpec) -> dict[str, Any]:
    flags = [
        {"rule": f.rule, "severity": f.severity, "stage": f.stage, "row": f.row, "table": f.table}
        for f in case.flags
    ]
    docs = [d for d in case.documents if d.doc_type == "bank_statement"]
    printed = [list(s.rows) for s in spec.statements]
    row_ok = 0
    row_total = sum(len(p) for p in printed)
    amounts_ok = 0
    for doc, rows in zip(sorted(docs, key=lambda d: d.file_id), printed, strict=False):
        table = doc.tables.get("transactions")
        if table is None:
            continue
        for got, want in zip(table.rows, rows, strict=False):
            row_ok += 1
            c = got.cells
            try:
                same = (
                    (c["balance"].money() == want.balance)
                    and ((c["debit"].money() if "debit" in c else None) == want.debit)
                    and ((c["credit"].money() if "credit" in c else None) == want.credit)
                )
            except (KeyError, AttributeError):
                same = False
            amounts_ok += bool(same)
    return {
        "flags": flags,
        "n_docs": len(case.documents),
        "n_bank_statement": len(docs),
        "rows_expected": row_total,
        "rows_parsed": sum(
            len(d.tables["transactions"].rows) for d in docs if "transactions" in d.tables
        ),
        "rows_amounts_exact": amounts_ok,
        "status": getattr(case, "status", None),
        "score": case.score.value if getattr(case, "score", None) is not None else None,
        "lane": case.lane,
    }


def run_job(job: dict[str, Any]) -> dict[str, Any]:
    """Process-pool entry: write the PDFs of one case, analyse them, return flags and extraction."""
    from scripts.run_case import analyse_files

    spec: CaseSpec = job["spec"]
    root = Path(tempfile.mkdtemp(prefix="gldb-fraudrag-"))
    started = time.perf_counter()
    try:
        files = []
        for st in spec.statements:
            p = root / "in" / f"{st.sid}.pdf"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(render_pdf(st))
            files.append(p)
        settings = make_settings(root, "rules", extractor_mode="text_layer", page_workers=1)
        db = new_db(settings)
        case = analyse_files(
            files,
            use_case="loan",
            applicant_name=spec.applicant,
            today=TODAY,
            db=db,
            settings=settings,
        )
        out = _summarise_case(case, spec)
        out |= {"ok": True, "wall_seconds": round(time.perf_counter() - started, 2)}
    except Exception as exc:  # reported per case, never swallowed silently
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        shutil.rmtree(root, ignore_errors=True)
    out |= {
        "case_id": spec.case_id,
        "category": spec.category,
        "variant": spec.variant,
        "detail": spec.truth.detail,
        "tampered_rows": spec.truth.rows,
    }
    return out


# ------------------------------------------------------------------------------ scoring


# The PDF-forensics family reads file metadata and fonts. Every PDF here comes from one
# non-issuer program, so ``pdf_producer_unexpected`` fires on every statement, clean or not, which
# says something about the generator and nothing about the logic under test. Those flags are
# counted separately and left out of every rate below.
IGNORED_STAGES = ("forensics",)


def content_flags(res: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for f in res.get("flags", []) if f["stage"] not in IGNORED_STAGES]


def flagged_rules(res: dict[str, Any], severities: Sequence[str] = SEVERE) -> set[str]:
    return {f["rule"] for f in content_flags(res) if f["severity"] in severities}


def verdict(res: dict[str, Any]) -> dict[str, Any]:
    """Per case: ``detected`` (an expected rule fired at medium or high), ``any_severe`` (any rule
    fired at medium or high), ``localised`` (a flag of an expected rule points at a touched row, or
    at the row after it, which is where a running-balance break shows)."""
    key = f"{res['category']}/{res['variant']}"
    expected = EXPECTED.get(key)
    severe = flagged_rules(res)
    out = {
        "any_severe": bool(severe),
        "detected": False,
        "detected_any_severity": False,
        "localised": None,
    }
    if expected:
        flags = content_flags(res)
        hit = [f for f in flags if f["rule"] in expected and f["severity"] in SEVERE]
        out["detected"] = bool(hit)
        out["detected_any_severity"] = any(f["rule"] in expected for f in flags)
        rows = set(res.get("tampered_rows") or [])
        if rows and hit:
            near = rows | {r + 1 for r in rows} | {r - 1 for r in rows}
            with_row = [f for f in hit if f["row"] is not None]
            out["localised"] = any(f["row"] in near for f in with_row) if with_row else None
    return out


def _rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = mx.wilson(k, n)
    return {
        "k": k,
        "n": n,
        "rate": round(k / n, 4) if n else None,
        "lo": round(lo, 4),
        "hi": round(hi, 4),
    }


def delta_band(x: Decimal) -> str:
    if x <= 1:
        return "up to 1.00 (inside the tolerance)"
    if x < 10:
        return "1.01 to 9.99"
    if x < 100:
        return "10.00 to 99.99"
    return "100.00 or more"


def aggregate(results: Sequence[dict[str, Any]], seed: int) -> dict[str, Any]:
    ok = [r for r in results if r.get("ok")]
    errors = [r for r in results if not r.get("ok")]
    by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in ok:
        by_variant[f"{r['category']}/{r['variant']}"].append(r)

    clean = [r for r in ok if r["category"] == "clean" and r["variant"] == "clean"]
    clean_cont = [r for r in ok if r["variant"] == "clean_continuity"]
    clean_fa = sum(1 for r in clean if verdict(r)["any_severe"])
    clean_cont_fa = sum(1 for r in clean_cont if verdict(r)["any_severe"])
    # low flags (a hint, such as bs_round_numbers) are outside "severe" but still a false alarm
    clean_low = sum(1 for r in clean + clean_cont if flagged_rules(r, ("low",)))

    variants: list[dict[str, Any]] = []
    for key in EXPECTED:
        rs = by_variant.get(key, [])
        if not rs:
            continue
        verds = [verdict(r) for r in rs]
        n = len(rs)
        expected = EXPECTED[key]
        row: dict[str, Any] = {
            "variant": key,
            "expected_rules": list(expected) if expected else None,
            "benign": key in BENIGN,
            "n": n,
            "detected": _rate(sum(v["detected"] for v in verds), n) if expected else None,
            "detected_any_severity": (
                _rate(sum(v["detected_any_severity"] for v in verds), n) if expected else None
            ),
            "any_severe": _rate(sum(v["any_severe"] for v in verds), n),
        }
        loc = [v["localised"] for v in verds if v["localised"] is not None]
        if loc:
            row["localised"] = _rate(sum(1 for x in loc if x), len(loc))
        row["rules_seen"] = dict(
            sorted(_count(r_ for r in rs for r_ in flagged_rules(r)).items(), key=lambda kv: -kv[1])
        )
        variants.append(row)

    # the size of the change decides whether the arithmetic can see it
    bands: dict[str, dict[str, list[int]]] = {}
    for label, key, field_ in (
        (
            "amount digit substitution, naive (difference of the edited amount)",
            "amount_digit_substitution/naive",
            "diff",
        ),
        (
            "closing balance printed header only (offset)",
            "balance_manipulation/closing_header",
            "delta",
        ),
        ("one running balance edited (offset)", "balance_manipulation/row_balance", "delta"),
    ):
        groups: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in by_variant.get(key, []):
            band = delta_band(Decimal(r["detail"][field_]))
            groups[band][1] += 1
            groups[band][0] += int(verdict(r)["detected"])
        bands[label] = {b: _rate(*v) for b, v in sorted(groups.items())}
    deltas: dict[str, dict[str, Any]] = {}
    for key in ("balance_manipulation/closing_header", "balance_manipulation/row_balance"):
        per: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in by_variant.get(key, []):
            d = r["detail"]["delta"]
            per[d][1] += 1
            per[d][0] += int(verdict(r)["detected"])
        deltas[key] = {d: _rate(*per[d]) for d in sorted(per, key=Decimal)}

    forensics = [r for r in ok if any(f["stage"] in IGNORED_STAGES for f in r["flags"])]
    extraction = {
        "forensics_flags_on_cases": _rate(len(forensics), len(ok)),
        "cases": len(ok),
        "statements_expected": sum(r["rows_expected"] > 0 for r in ok),
        "rows_expected": sum(r["rows_expected"] for r in ok),
        "rows_parsed": sum(r["rows_parsed"] for r in ok),
        "rows_amounts_exact": sum(r["rows_amounts_exact"] for r in ok),
        "cases_with_fewer_bank_statements": sum(
            1 for r in ok if r["n_bank_statement"] < (3 if "cont" in r["variant"] else 1)
        ),
    }
    return {
        "seed": seed,
        "cases": len(results),
        "errors": [{"case_id": e["case_id"], "error": e["error"]} for e in errors],
        "clean": {
            "single_statement": _rate(clean_fa, len(clean)),
            "three_month_sequence": _rate(clean_cont_fa, len(clean_cont)),
            "any_low_flag": _rate(clean_low, len(clean) + len(clean_cont)),
            "rules_seen": _count(r_ for r in clean + clean_cont for r_ in flagged_rules(r)),
        },
        "variants": variants,
        "bands": bands,
        "deltas": deltas,
        "extraction": extraction,
    }


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for i in items:
        out[i] += 1
    return dict(out)


# ------------------------------------------------------------------------------ report


def _fmt(r: dict[str, Any] | None) -> str:
    if not r or r["rate"] is None:  # no cases of this kind
        return "n/a"
    return f"{r['k']}/{r['n']} = {r['rate']:.2f} [{r['lo']:.2f}-{r['hi']:.2f}]"


WHY = {
    "balance_manipulation/closing_header": "closing balance edited in the header only",
    "balance_manipulation/row_balance": "one running balance edited, nothing else",
    "balance_manipulation/shifted_consistent": "opening balance raised, every balance and total recomputed (one statement alone)",
    "balance_manipulation/continuity_consistent": "same, but the neighbouring months are untouched (3 months of one account)",
    "amount_digit_substitution/naive": "one digit of one amount changed; balances and totals not touched",
    "amount_digit_substitution/consistent": "one digit changed and every balance, closing and total recomputed",
    "date_forgery/outside_period": "one date moved into the previous month",
    "date_forgery/out_of_order": "one date moved a week or more before the row above it, inside the period",
    "date_forgery/invalid_calendar": "a printed date that does not exist (30 Feb, 31 Apr ...)",
    "date_forgery/shift_in_order": "one date moved one to three days, order and period kept",
    "entity_name_mismatch/unrelated_company": "holder replaced by another company",
    "entity_name_mismatch/similar_name": "holder is the applicant's name with a word dropped or added",
    "entity_name_mismatch/one_char_typo": "holder is the applicant's name with one letter changed",
    "entity_name_mismatch/person_name": "holder is an individual's name",
    "entity_name_mismatch/suffix_only_benign": "NOT a forgery: 'Pte Ltd' printed as 'Pte. Ltd.' (a flag here is a false alarm)",
    "fabricated_transactions/insert_naive": "1 to 3 credits inserted; later balances and totals not touched",
    "fabricated_transactions/insert_consistent_plain": "1 to 3 plausible credits inserted, everything recomputed",
    "fabricated_transactions/insert_consistent_round": "1 to 3 round-sum credits (20,000 to 80,000) inserted, everything recomputed (one statement alone: the rule needs other statements of the account as a baseline)",
    "fabricated_transactions/insert_round_in_sequence": "1 to 3 round credits in the last month of a 3-month sequence whose other months are untouched (the rule needs at least 3 round credits)",
    "fabricated_transactions/insert_round_in_sequence_many": "5 to 8 round credits in the same setting",
}


def render_markdown(p: dict[str, Any]) -> str:
    out: list[str] = []
    a = out.append
    a("# FraudRAG-style local test: five fraud categories on simple PDF statements\n")
    a(f"> {SENTENCE}\n")
    a(
        "Generated by `backend/scripts/ext_eval/fraudrag_local.py`. Digital PDFs with a text layer, read by the repo's "
        "deterministic text-layer extractor (Fake LLM, no network), then the repo's rules. Seed "
        f"{p['seed']} (the generator and layouts were debugged on development seed {DEV_SEED}; the report seed is {REPORT_SEED}). The rules expected to catch each "
        "variant were written down before the run (`EXPECTED` in the script); a case is **detected** when one of them "
        "raises a medium or high flag. Nothing was tuned on these numbers.\n"
    )
    a(
        "**What it represents.** Data-level manipulations of a bank statement, rendered afresh: the arithmetic, date and "
        "entity logic. Naive variants change one printed thing; consistent variants recompute every dependent number, "
        "which is the published balance-preserving idea.\n"
    )
    a(
        "**What it does not represent.** Real statements (our two layouts, fictitious Singapore-style data), scans or photos "
        "(digital PDFs only), PDF-level traces (the forgery is regenerated, so there are none: the forger's best case for "
        "the PDF-forensics family), or the original FraudRAG-2400 data. Detection rates here say nothing about its published AUC/F1.\n"
    )
    e = p["extraction"]
    a(
        f"PDF-forensics flags (`pdf_producer_unexpected`: every PDF comes from one non-issuer program) fired on "
        f"{_fmt(e['forensics_flags_on_cases'])} of cases, clean or forged, and are excluded from every rate below.\n"
    )
    a(
        f"Cases: {p['cases']} ({len(p['errors'])} errors). Extraction: {e['rows_parsed']}/{e['rows_expected']} printed rows parsed, "
        f"{e['rows_amounts_exact']} with debit, credit and balance exactly as printed; "
        f"{e['cases_with_fewer_bank_statements']} cases had fewer bank-statement documents than files.\n"
    )
    c = p["clean"]
    a("## Clean controls (false alarms)\n")
    a(f"- single clean statements, any medium or high flag: {_fmt(c['single_statement'])}")
    a(
        f"- clean three-month sequences of one account, any medium or high flag: {_fmt(c['three_month_sequence'])}"
    )
    a(
        f"- clean cases (both kinds) with any low flag, which the two lines above do not count: "
        f"{_fmt(c['any_low_flag'])}. The clean generator has almost no round-thousand credits, so "
        "this says little about the round-number count trigger."
    )
    if c["rules_seen"]:
        a(
            f"- rules that fired on clean cases: {', '.join(f'{k} x{v}' for k, v in c['rules_seen'].items())}"
        )
    a("")
    a("## Per category and variant\n")
    a(
        "| Category / variant | What was done | Rules that ought to catch it | n | Detected (medium or high) | Expected rule at any severity | Any other medium/high flag | Row localised |"
    )
    a("|---|---|---|---|---|---|---|---|")
    for v in p["variants"]:
        rules = (
            ", ".join(f"`{r}`" for r in v["expected_rules"])
            if v["expected_rules"]
            else "none (cannot be caught from one statement)"
        )
        det = _fmt(v["detected"]) if v["detected"] else "n/a"
        low = _fmt(v["detected_any_severity"]) if v["detected_any_severity"] else "n/a"
        a(
            f"| `{v['variant']}` | {WHY.get(v['variant'], '')} | {rules} | {v['n']} | {det} | {low} | {_fmt(v['any_severe'])} | {_fmt(v.get('localised'))} |"
        )
    a("")
    a(
        "`Detected` counts only the expected rules at medium or high. `Expected rule at any severity` also counts a low hint "
        "(`bs_round_numbers` is low by design). `Any medium/high flag` counts every content rule: for the variants with no "
        "expected rule it shows whether anything else noticed the edit (for the benign suffix variant it is the false-alarm rate). "
        "`Row localised` is the share of detected cases in which "
        "a flag of an expected rule points at the touched row or its neighbour.\n"
    )
    a("## How the size of the change decides\n")
    for label, groups in p["bands"].items():
        a(f"**{label}**\n")
        a("| Band | Detected |")
        a("|---|---|")
        for band, r in groups.items():
            a(f"| {band} | {_fmt(r)} |")
        a("")
    a("Offsets for the two balance variants, one by one:\n")
    for key, per in p["deltas"].items():
        a(f"- `{key}`: " + "; ".join(f"{d}: {_fmt(r)}" for d, r in per.items()))
    a("")
    a("## Limits\n")
    a(
        "- A consistent forgery (every dependent balance and total recomputed, plausible values, dates in order, "
        "no round sums) is invisible to arithmetic, date and entity logic on that statement. Only an outside reference "
        "(the bank, a neighbouring month, the applicant's accounts) can expose it; the neighbour case above shows that "
        "`bs_period_continuity` does when the neighbours are uploaded and untouched, and says nothing when they are not."
    )
    a(
        "- The tolerance for money is 1.00: a difference of up to and including 1.00 is not flagged, by design (rounding in real statements).\n- One-letter changes to the holder's name are mostly accepted: the company matcher is a fuzzy match on purpose (OCR and transliteration noise), so a near-identical name is not a mismatch. A forger who picks a look-alike name is therefore not caught by `bs_holder`; only a UEN comparison would help, and only if the statement prints one.\n- `bs_round_numbers` compares a statement with other statements of the same account; it cannot judge a single statement."
    )
    a(
        "- A date moved but kept in order and inside the period, and a name that differs only in legal suffix, are "
        "plausible and are not flagged; the second is intended."
    )
    a(
        "- Native digital forgery regenerated from data has no PDF traces. A forger who edits the existing PDF "
        "leaves some; that is measured by the main eval, not here."
    )
    a(
        "- `bs_round_numbers` is a low-severity hint by design: where it fires it does not reach the medium/high column, "
        "and it needs at least 3 round credits (the 1-to-3 variant is partly below that floor)."
    )
    a(
        "- Declared-in-advance caveat: the single-statement round-number variant was declared as catchable before the rule's "
        "need for a baseline was known; the dev-seed run showed 0/30, the rule's constants were then read, and the two "
        "in-sequence variants (and the 5 to 8 credit count) were added on that basis. They were never tuned on the report seed."
    )
    a("- Sample sizes are 20 to 60 per variant; read the intervals.")
    return "\n".join(out) + "\n"


def write_outputs(out: Path, payload: dict[str, Any], results: Sequence[dict[str, Any]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "fraudrag-local.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "fraudrag-local.md").write_text(render_markdown(payload), encoding="utf-8")
    with (out / "fraudrag-local-cases.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "case_id",
                "category",
                "variant",
                "ok",
                "detected",
                "any_severe",
                "rules_medium_high",
                "rows_parsed",
                "rows_expected",
            ]
        )
        for r in results:
            if not r.get("ok"):
                w.writerow([r["case_id"], r["category"], r["variant"], False, "", "", "", "", ""])
                continue
            v = verdict(r)
            w.writerow([
                r["case_id"], r["category"], r["variant"], True, v["detected"], v["any_severe"],
                " ".join(sorted(flagged_rules(r))), r["rows_parsed"], r["rows_expected"],
            ])  # fmt: skip


def cmd_run(args: argparse.Namespace) -> int:
    specs = build_cases(args.seed, args.scale)
    if args.limit:
        specs = specs[: args.limit]
    jobs = [{"spec": s} for s in specs]
    print(f"{len(jobs)} cases, seed {args.seed}", file=sys.stderr)
    results = run_pool(run_job, jobs, args.workers)
    payload = aggregate(results, args.seed)
    payload["sentence"] = SENTENCE
    write_outputs(Path(args.out), payload, results)
    print(render_markdown(payload))
    return 0 if not payload["errors"] else 1


def cmd_preview(args: argparse.Namespace) -> int:
    d = Path(args.dir)
    d.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    for layout in ("two_col", "one_col"):
        st = make_statement(rng, f"preview_{layout}", layout=layout)
        (d / f"{st.sid}.pdf").write_bytes(render_pdf(st))
        print(d / f"{st.sid}.pdf")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="generate, analyse and report")
    r.add_argument("--seed", type=int, default=REPORT_SEED)
    r.add_argument("--scale", type=int, default=1)
    r.add_argument("--limit", type=int, default=0, help="only the first N cases (smoke test)")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--out", default="docs/eval-results/external")
    r.set_defaults(fn=cmd_run)
    p = sub.add_parser("preview", help="write two example PDFs")
    p.add_argument("--dir", required=True)
    p.add_argument("--seed", type=int, default=DEV_SEED)
    p.set_defaults(fn=cmd_preview)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
