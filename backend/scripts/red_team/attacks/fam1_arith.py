"""Family 1: arithmetic and tolerance games. Every figure the document's own rules look at is
recomputed; the question is which cross-document comparisons are left with slack."""

from __future__ import annotations

import random
from decimal import Decimal

from ..core import Bundle, Outcome, attack
from ._util import credits, fin, stmts

F = "F1 arithmetic and tolerances"


@attack(
    "R01",
    F,
    "One credit +S$2,600 in Aug, chain recomputed to Dec (closing stays inside the 5% cash tolerance)",
)
def r01(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    row = credits(ss, 1)[0]
    new = row.amount + Decimal("2600")
    ss.stmts[1].set_amount(row, new)
    ss.recompute_from(1)
    ss.write_back(b.files)
    return Outcome(
        "Inflate one August credit by S$2,600 and recompute every later balance; the December closing balance moves by 4.7%, inside the 5% year-end cash tolerance.",
        [s.name for s in ss.stmts[1:]],
        [f"{new:,.2f}"],
    )


@attack(
    "R02",
    F,
    "Balance-neutral window dressing: +S$48,000 credit in Aug, offsetting debit in Dec, chain recomputed",
    consistent=True,
)
def r02(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    bump = Decimal("48000")
    row = credits(ss, 1)[0]
    ss.stmts[1].set_amount(row, row.amount + bump)
    # the December hijack: a mid-sized debit raised by the same amount so the closing balance is unchanged
    debits = [r for r in ss.stmts[5].rows if r.kind == "wd" and r.amount > Decimal("5000")]
    d = debits[0]
    ss.stmts[5].set_amount(d, d.amount + bump)
    ss.recompute_from(1)
    ss.write_back(b.files)
    return Outcome(
        "Show a higher balance from Aug to Nov by inflating one credit and burying the same amount in a December debit, so the year-end cash and every FS figure stay true.",
        [s.name for s in ss.stmts[1:]],
    )


@attack(
    "R03",
    F,
    "Gross-up: FY2025 revenue and cost of sales both +S$480,000 (profit, balance sheet and cash flow untouched)",
)
def r03(b: Bundle, rng: random.Random) -> Outcome:
    f = fin(b, 2025)
    rev, cos = f.get("Revenue"), f.get("Cost of sales")
    up = 480_000
    f.put("Revenue", rev + up)
    f.put("Cost of sales", cos - up)  # negative figure: more cost
    b.replace("financial_statement_FY2025.pdf", f.dump())
    return Outcome(
        "Turnover 900,000 -> 1,380,000 with a matching rise in cost of sales: gross profit, tax, equity, cash flow and every subtotal stay as they were.",
        ["financial_statement_FY2025.pdf"],
        [f"{rev + up:,}"],
    )


@attack(
    "R04",
    F,
    "History rewrite: FY2024 revenue and cost of sales grossed up in both FY2024 and the FY2025 comparative column",
    consistent=True,
)
def r04(b: Bundle, rng: random.Random) -> Outcome:
    for yr, col, up in ((2024, 0, 250), (2025, 1, 250_000)):  # FY2024 is printed in thousands
        f = fin(b, yr)
        f.put("Revenue", f.get("Revenue", col) + up, col)
        f.put("Cost of sales", f.get("Cost of sales", col) - up, col)
        b.replace(f"financial_statement_FY{yr}.pdf", f.dump())
    return Outcome(
        "Make the prior-year turnover 250,000 larger in the two statements that both print it; profit unchanged.",
        ["financial_statement_FY2024.pdf", "financial_statement_FY2025.pdf"],
    )


@attack("R05", F, "Omit the FY2024 statement after editing the FY2025 comparative column")
def r05(b: Bundle, rng: random.Random) -> Outcome:
    f = fin(b, 2025)
    f.put("Revenue", f.get("Revenue", 1) + 120_000, 1)
    f.put("Cost of sales", f.get("Cost of sales", 1) - 120_000, 1)
    b.replace("financial_statement_FY2025.pdf", f.dump())
    b.remove("financial_statement_FY2024.pdf")
    return Outcome(
        "Edit the prior-year column of the current statements and simply do not upload the earlier year it is compared with.",
        ["financial_statement_FY2025.pdf"],
    )


@attack(
    "R06",
    F,
    "Spread inflation: every month's biggest credit +S$400 (below any single-edit signal), chain recomputed",
)
def r06(b: Bundle, rng: random.Random) -> Outcome:
    ss = stmts(b)
    for k, s in enumerate(ss.stmts):
        row = credits(ss, k)[0]
        s.set_amount(row, row.amount + Decimal("400"))
    ss.recompute_from(0)
    ss.write_back(b.files)
    return Outcome(
        "Inflate one credit in each of six months by S$400 (total S$2,400, 4.3% of year-end cash), every balance recomputed.",
        [s.name for s in ss.stmts],
    )
