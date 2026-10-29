"""Financial statements for the loan world: three years of exactly consistent numbers, the
FY2024 print in S$'000 (Q06), the careful-forger recomputation and a pure identity checker
that ``verify`` also uses (checks-financial.md section 6 formulas)."""

from __future__ import annotations

import random
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .world_types import FinStmt, FSLine

D = Decimal
INCOME = "income_statement"
BALANCE = "balance_sheet"
CASH = "cash_flow"
TAX_RATE = D("0.17")


def whole(x: float | Decimal) -> Decimal:
    return Decimal(int(Decimal(x).quantize(D("1"), rounding=ROUND_HALF_UP)))


# line layout: (line, label, section, is_subtotal)
INCOME_LINES = [
    ("revenue", "Revenue", "pnl", False),
    ("cost_of_sales", "Cost of sales", "pnl", False),
    ("gross_profit", "Gross profit", "pnl", True),
    ("other_income", "Other income", "pnl", False),
    ("operating_expenses", "Operating expenses", "pnl", False),
    ("finance_costs", "Finance costs", "pnl", False),
    ("profit_before_tax", "Profit before tax", "pnl", True),
    ("income_tax", "Income tax expense", "pnl", False),
    ("net_profit", "Net profit for the year", "pnl", True),
]
BALANCE_LINES = [
    ("cash_and_equivalents", "Cash and cash equivalents", "current_assets", False),
    ("trade_receivables", "Trade receivables", "current_assets", False),
    ("inventories", "Inventories", "current_assets", False),
    ("other_current_assets", "Other current assets", "current_assets", False),
    ("total_current_assets", "Total current assets", "current_assets", True),
    ("property_plant_equipment", "Property, plant and equipment", "non_current_assets", False),
    ("other_non_current_assets", "Other non-current assets", "non_current_assets", False),
    ("total_non_current_assets", "Total non-current assets", "non_current_assets", True),
    ("total_assets", "Total assets", "totals", True),
    ("trade_payables", "Trade payables", "current_liabilities", False),
    ("borrowings_current", "Borrowings (current)", "current_liabilities", False),
    ("other_current_liabilities", "Other current liabilities", "current_liabilities", False),
    ("total_current_liabilities", "Total current liabilities", "current_liabilities", True),
    ("borrowings_non_current", "Borrowings (non-current)", "non_current_liabilities", False),
    (
        "total_non_current_liabilities",
        "Total non-current liabilities",
        "non_current_liabilities",
        True,
    ),
    ("total_liabilities", "Total liabilities", "totals", True),
    ("share_capital", "Share capital", "equity", False),
    ("retained_earnings", "Retained earnings", "equity", False),
    ("total_equity", "Total equity", "equity", True),
    ("total_liabilities_and_equity", "Total liabilities and equity", "totals", True),
]
CASH_LINES = [
    ("profit_before_tax", "Profit before tax", "operating", False),
    ("adjustments", "Adjustments (depreciation)", "operating", False),
    ("working_capital_changes", "Changes in working capital", "operating", False),
    ("net_cash_from_operating", "Net cash from operating activities", "operating", True),
    ("capex", "Purchase of property, plant and equipment", "investing", False),
    ("net_cash_from_investing", "Net cash used in investing activities", "investing", True),
    ("loan_proceeds", "Proceeds from borrowings", "financing", False),
    ("loan_repayments", "Repayment of borrowings", "financing", False),
    ("net_cash_from_financing", "Net cash from financing activities", "financing", True),
    ("net_change_in_cash", "Net change in cash", "cash", True),
    ("cash_opening", "Cash at beginning of year", "cash", False),
    ("cash_closing", "Cash at end of year", "cash", True),
]
LAYOUT = {INCOME: INCOME_LINES, BALANCE: BALANCE_LINES, CASH: CASH_LINES}


def _year_numbers(
    rng: random.Random,
    revenue: Decimal,
    cash: Decimal,
    share_capital: Decimal,
    *,
    shape: dict[str, float],
) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    """P&L and the balance-sheet items other than PPE and retained earnings (filled later)."""
    r = revenue
    cogs = whole(r * D(str(shape["cogs"])))
    opex = whole(r * D(str(shape["opex"])))
    other_income = whole(r * D("0.0015"))
    finance = whole(r * D(str(shape["fin"])))
    gp = r - cogs
    pbt = gp + other_income - opex - finance
    tax = whole(max(pbt, D(0)) * TAX_RATE)
    pl = {
        "revenue": r,
        "cost_of_sales": cogs,
        "gross_profit": gp,
        "other_income": other_income,
        "operating_expenses": opex,
        "finance_costs": finance,
        "profit_before_tax": pbt,
        "income_tax": tax,
        "net_profit": pbt - tax,
    }
    bs = {
        "cash_and_equivalents": cash,
        "trade_receivables": whole(r * D(str(shape["rec"]))),
        "inventories": whole(cogs * D(str(shape["inv"]))),
        "other_current_assets": whole(r * D("0.012")),
        "other_non_current_assets": whole(r * D("0.01")),
        "trade_payables": whole(cogs * D(str(shape["pay"]))),
        "borrowings_current": whole(r * D(str(shape["bc"]))),
        "other_current_liabilities": whole(r * D("0.02") + tax),
        "borrowings_non_current": whole(r * D(str(shape["bn"]))),
        "share_capital": share_capital,
    }
    return pl, bs


def exact_statements(
    rng: random.Random, revenue25: Decimal, cash25: Decimal, share_capital: Decimal
) -> dict[int, dict[str, dict[str, Decimal]]]:
    """Exactly consistent FY2023..FY2025 numbers in whole dollars; FY2025 cash = ``cash25``."""
    g24 = D(str(round(rng.uniform(0.80, 0.92), 3)))
    g23 = D(str(round(rng.uniform(0.82, 0.95), 3)))
    rev = {2025: revenue25, 2024: whole(revenue25 * g24 / 1000) * 1000}
    rev[2023] = whole(rev[2024] * g23 / 1000) * 1000
    cash = {
        2025: cash25,
        2024: whole(cash25 * D(str(round(rng.uniform(0.6, 1.05), 3)))),
        2023: whole(cash25 * D(str(round(rng.uniform(0.45, 0.9), 3)))),
    }
    years: dict[int, dict[str, dict[str, Decimal]]] = {}
    nps: dict[int, Decimal] = {}
    for y in (2023, 2024, 2025):
        shape = {
            "cogs": rng.uniform(0.68, 0.76),
            "opex": rng.uniform(0.12, 0.16),
            "fin": rng.uniform(0.003, 0.006),
            "rec": rng.uniform(0.14, 0.2),
            "inv": rng.uniform(0.14, 0.22),
            "pay": rng.uniform(0.10, 0.16),
            "bc": rng.uniform(0.02, 0.035),
            "bn": rng.uniform(0.045, 0.075),
        }
        pl, bs = _year_numbers(rng, rev[y], cash[y], share_capital, shape=shape)
        years[y] = {"pl": pl, "bs": bs}
        nps[y] = pl["net_profit"]
    # retained earnings and PPE plug
    re_cum = {2023: D(0), 2024: nps[2024], 2025: nps[2024] + nps[2025]}
    ppe0 = {}
    for y in (2023, 2024, 2025):
        bs = years[y]["bs"]
        tca = sum(
            bs[k]
            for k in (
                "cash_and_equivalents",
                "trade_receivables",
                "inventories",
                "other_current_assets",
            )
        )
        tl = (
            bs["trade_payables"]
            + bs["borrowings_current"]
            + bs["other_current_liabilities"]
            + bs["borrowings_non_current"]
        )
        ppe0[y] = tl + bs["share_capital"] + re_cum[y] - tca - bs["other_non_current_assets"]
    re23 = max(D(0), whole((D(60000) - min(ppe0.values())) / 1000 + 1) * 1000)
    for y in (2023, 2024, 2025):
        bs = years[y]["bs"]
        bs["retained_earnings"] = re23 + re_cum[y]
        bs["property_plant_equipment"] = ppe0[y] + re23
    # derived subtotals
    for y in (2023, 2024, 2025):
        bs = years[y]["bs"]
        bs["total_current_assets"] = sum(
            bs[k]
            for k in (
                "cash_and_equivalents",
                "trade_receivables",
                "inventories",
                "other_current_assets",
            )
        )
        bs["total_non_current_assets"] = (
            bs["property_plant_equipment"] + bs["other_non_current_assets"]
        )
        bs["total_assets"] = bs["total_current_assets"] + bs["total_non_current_assets"]
        bs["total_current_liabilities"] = (
            bs["trade_payables"] + bs["borrowings_current"] + bs["other_current_liabilities"]
        )
        bs["total_non_current_liabilities"] = bs["borrowings_non_current"]
        bs["total_liabilities"] = (
            bs["total_current_liabilities"] + bs["total_non_current_liabilities"]
        )
        bs["total_equity"] = bs["share_capital"] + bs["retained_earnings"]
        bs["total_liabilities_and_equity"] = bs["total_liabilities"] + bs["total_equity"]
        assert bs["total_assets"] == bs["total_liabilities_and_equity"], (y, bs)
    # cash flow for 2024 and 2025
    for y in (2024, 2025):
        cur, prv = years[y], years[y - 1]
        dep = whole(prv["bs"]["property_plant_equipment"] * D("0.12"))
        capex = -(
            cur["bs"]["property_plant_equipment"] - prv["bs"]["property_plant_equipment"] + dep
        )
        db = (cur["bs"]["borrowings_current"] + cur["bs"]["borrowings_non_current"]) - (
            prv["bs"]["borrowings_current"] + prv["bs"]["borrowings_non_current"]
        )
        proceeds = max(db, D(0))
        repay = -max(-db, D(0))
        dcash = cur["bs"]["cash_and_equivalents"] - prv["bs"]["cash_and_equivalents"]
        pbt = cur["pl"]["profit_before_tax"]
        wc = dcash - capex - proceeds - repay - pbt - dep
        op = pbt + dep + wc
        cf = {
            "profit_before_tax": pbt,
            "adjustments": dep,
            "working_capital_changes": wc,
            "net_cash_from_operating": op,
            "capex": capex,
            "net_cash_from_investing": capex,
            "loan_proceeds": proceeds,
            "loan_repayments": repay,
            "net_cash_from_financing": proceeds + repay,
            "net_change_in_cash": dcash,
            "cash_opening": prv["bs"]["cash_and_equivalents"],
            "cash_closing": cur["bs"]["cash_and_equivalents"],
        }
        assert op + capex + proceeds + repay == dcash
        cur["cf"] = cf
    return years


def _lines(
    layout: list[tuple[str, str, str, bool]],
    cur: dict[str, Decimal],
    prior: dict[str, Decimal] | None,
) -> list[FSLine]:
    out = []
    for line, label, section, sub in layout:
        c = cur.get(line)
        p = prior.get(line) if prior is not None else None
        out.append(FSLine(line, label, section, c, p, sub))
    return out


def to_thousands(
    layout: list[tuple[str, str, str, bool]], cur: dict[str, Decimal]
) -> dict[str, Decimal]:
    """Round every printed figure independently to S$'000 (stored as whole dollars)."""
    return {k: whole(v / 1000) * 1000 for k, v in cur.items()}


def build_fs_documents(
    rng: random.Random,
    *,
    company_name: str,
    uen: str,
    revenue25: Decimal,
    cash25: Decimal,
    share_capital: Decimal,
    unaudited_fy2025: bool,
    thousands_fy2024: bool,
    auditor: str,
) -> dict[str, FinStmt]:
    """FY2024 and FY2025 documents. Loops over fresh draws until the S$'000 print of FY2024
    keeps every identity within one printed unit (Q06)."""
    for _attempt in range(200):
        years = exact_statements(rng, revenue25, cash25, share_capital)
        docs = {}
        for fy, y in (("FY2024", 2024), ("FY2025", 2025)):
            cur = years[y]
            prior = years[y - 1] if fy == "FY2025" else None
            scale = 1000 if (fy == "FY2024" and thousands_fy2024) else 1
            tables = {}
            for tname, key, layout in (
                (INCOME, "pl", INCOME_LINES),
                (BALANCE, "bs", BALANCE_LINES),
                (CASH, "cf", CASH_LINES),
            ):
                c = cur[key]
                if scale == 1000:
                    c = to_thousands(layout, c)
                p = prior[key] if (prior is not None and key in prior) else None
                if fy == "FY2025" and key == "cf":
                    p = years[2024]["cf"]
                tables[tname] = _lines(layout, c, p)
            basis = "unaudited" if (fy == "FY2025" and unaudited_fy2025) else "audited"
            docs[fy] = FinStmt(
                fy=fy,
                period_start=date(y, 1, 1),
                period_end=date(y, 12, 31),
                company_name=company_name,
                uen=uen,
                scale_printed=scale,
                basis=basis,
                auditor=None if basis == "unaudited" else auditor,
                title="Management Accounts (Unaudited)"
                if basis == "unaudited"
                else "Financial Statements",
                tables=tables,
                params={"tax_rate": str(TAX_RATE)},
            )
        if not fs_identity_violations(
            _as_dicts(docs["FY2024"]), docs["FY2024"].scale_printed, "current", strict=True
        ):
            return docs
    raise RuntimeError("could not find a consistent S$'000 print")


def _as_dicts(fs: FinStmt) -> dict[str, list[dict[str, Any]]]:
    return {
        t: [
            {
                "line": ln.line,
                "section": ln.section,
                "current": ln.current,
                "prior": ln.prior,
                "is_subtotal": ln.is_subtotal,
            }
            for ln in lines
        ]
        for t, lines in fs.tables.items()
    }


# --------------------------------------------------------------------------- identity checker


def _get(rows: list[dict[str, Any]], line: str, col: str) -> Decimal | None:
    vals = [r[col] for r in rows if r["line"] == line and r[col] is not None]
    return D(vals[0]) if vals else None


def fs_identity_violations(
    tables: dict[str, list[dict[str, Any]]],
    scale: int,
    col: str = "current",
    *,
    strict: bool = False,
) -> list[str]:
    """The identities of checks-financial.md 6.1-6.3 on one column. ``strict`` limits every
    tolerance to one printed unit (the generator's own bar for Q06 prints)."""
    out: list[str] = []
    one = D(1) if scale == 1 else D(scale)
    base_tol = D("1.00") if scale == 1 else one

    def tol(terms: int) -> Decimal:
        if scale == 1:
            return base_tol
        return one if strict else one * max(1, terms // 2)

    inc, bal, cf = tables.get(INCOME, []), tables.get(BALANCE, []), tables.get(CASH, [])

    def cmp(name: str, printed: Decimal | None, calc: Decimal | None, terms: int) -> None:
        if printed is None or calc is None:
            return
        if abs(printed - calc) > tol(terms):
            out.append(f"{name}: printed {printed} vs computed {calc} ({col})")

    if inc:
        rev, cogs = _get(inc, "revenue", col), _get(inc, "cost_of_sales", col) or D(0)
        gp = _get(inc, "gross_profit", col)
        if rev is not None:
            cmp("gross_profit", gp, rev - cogs, 3)
        base_gp = gp if gp is not None else (rev - cogs if rev is not None else None)
        oi = _get(inc, "other_income", col) or D(0)
        opx = _get(inc, "operating_expenses", col) or D(0)
        fin = _get(inc, "finance_costs", col) or D(0)
        pbt = _get(inc, "profit_before_tax", col)
        if base_gp is not None:
            cmp("profit_before_tax", pbt, base_gp + oi - opx - fin, 5)
        tax = _get(inc, "income_tax", col) or D(0)
        if pbt is not None:
            cmp("net_profit", _get(inc, "net_profit", col), pbt - tax, 3)
    if bal:

        def section_sum(section: str) -> Decimal | None:
            vals = [
                D(r[col])
                for r in bal
                if r["section"] == section and not r["is_subtotal"] and r[col] is not None
            ]
            return sum(vals, D(0)) if vals else None

        tca, tnca = section_sum("current_assets"), section_sum("non_current_assets")
        tcl, tncl, teq = (
            section_sum("current_liabilities"),
            section_sum("non_current_liabilities"),
            section_sum("equity"),
        )
        n_in = lambda s: len([r for r in bal if r["section"] == s and not r["is_subtotal"]])  # noqa: E731
        cmp(
            "total_current_assets",
            _get(bal, "total_current_assets", col),
            tca,
            n_in("current_assets") + 1,
        )
        cmp(
            "total_non_current_assets",
            _get(bal, "total_non_current_assets", col),
            tnca,
            n_in("non_current_assets") + 1,
        )
        ta_p = _get(bal, "total_assets", col)
        if tca is not None and tnca is not None:
            cmp(
                "total_assets",
                ta_p,
                (_get(bal, "total_current_assets", col) or tca)
                + (_get(bal, "total_non_current_assets", col) or tnca),
                3,
            )
        cmp(
            "total_current_liabilities",
            _get(bal, "total_current_liabilities", col),
            tcl,
            n_in("current_liabilities") + 1,
        )
        cmp(
            "total_non_current_liabilities",
            _get(bal, "total_non_current_liabilities", col),
            tncl,
            n_in("non_current_liabilities") + 1,
        )
        tl_p = _get(bal, "total_liabilities", col)
        if tcl is not None and tncl is not None:
            cmp(
                "total_liabilities",
                tl_p,
                (_get(bal, "total_current_liabilities", col) or tcl)
                + (_get(bal, "total_non_current_liabilities", col) or tncl),
                3,
            )
        cmp("total_equity", _get(bal, "total_equity", col), teq, n_in("equity") + 1)
        tle = _get(bal, "total_liabilities_and_equity", col)
        if tl_p is not None and _get(bal, "total_equity", col) is not None:
            cmp("total_liabilities_and_equity", tle, tl_p + _get(bal, "total_equity", col), 3)  # type: ignore[operator]
        if ta_p is not None and tle is not None:
            cmp("fs_balance", ta_p, tle, 3)
    if cf:

        def sec(section: str) -> Decimal | None:
            vals = [
                D(r[col])
                for r in cf
                if r["section"] == section and not r["is_subtotal"] and r[col] is not None
            ]
            return sum(vals, D(0)) if vals else None

        for sub, section in (
            ("net_cash_from_operating", "operating"),
            ("net_cash_from_investing", "investing"),
            ("net_cash_from_financing", "financing"),
        ):
            n = len([r for r in cf if r["section"] == section and not r["is_subtotal"]]) + 1
            cmp(sub, _get(cf, sub, col), sec(section), n)
        nets = [
            _get(cf, k, col)
            for k in (
                "net_cash_from_operating",
                "net_cash_from_investing",
                "net_cash_from_financing",
            )
        ]
        if all(n is not None for n in nets):
            cmp("net_change_in_cash", _get(cf, "net_change_in_cash", col), sum(nets, D(0)), 4)  # type: ignore[arg-type]
        op_, nc_ = _get(cf, "cash_opening", col), _get(cf, "net_change_in_cash", col)
        if op_ is not None and nc_ is not None:
            cmp("cash_closing", _get(cf, "cash_closing", col), op_ + nc_, 3)
        if bal and col == "current":
            cmp(
                "fs_cash_tie",
                _get(cf, "cash_closing", col),
                _get(bal, "cash_and_equivalents", col),
                2,
            )
    return out


# --------------------------------------------------------------------------------- mutation


def find_line(fs: FinStmt, table: str, line: str) -> FSLine:
    for ln in fs.tables[table]:
        if ln.line == line:
            return ln
    raise KeyError(f"{table}.{line}")


def _section_sum(fs: FinStmt, table: str, section: str) -> Decimal:
    return sum(
        (ln.current or D(0))
        for ln in fs.tables[table]
        if ln.section == section and not ln.is_subtotal
    )


def recompute_subtotals(fs: FinStmt, table: str | None = None) -> None:
    """Rewrite every printed subtotal of the current column from its lines."""
    for t in [table] if table else [INCOME, BALANCE, CASH]:
        rows = {ln.line: ln for ln in fs.tables[t]}

        def g(k: str, rows: dict[str, FSLine] = rows) -> Decimal:
            return rows[k].current if k in rows and rows[k].current is not None else D(0)  # type: ignore[return-value]

        if t == INCOME:
            rows["gross_profit"].current = g("revenue") - g("cost_of_sales")
            rows["profit_before_tax"].current = (
                rows["gross_profit"].current
                + g("other_income")
                - g("operating_expenses")
                - g("finance_costs")
            )
            rows["net_profit"].current = rows["profit_before_tax"].current - g("income_tax")
        elif t == BALANCE:
            rows["total_current_assets"].current = _section_sum(fs, t, "current_assets")
            rows["total_non_current_assets"].current = _section_sum(fs, t, "non_current_assets")
            rows["total_assets"].current = (
                rows["total_current_assets"].current + rows["total_non_current_assets"].current
            )
            rows["total_current_liabilities"].current = _section_sum(fs, t, "current_liabilities")
            rows["total_non_current_liabilities"].current = _section_sum(
                fs, t, "non_current_liabilities"
            )
            rows["total_liabilities"].current = (
                rows["total_current_liabilities"].current
                + rows["total_non_current_liabilities"].current
            )
            rows["total_equity"].current = _section_sum(fs, t, "equity")
            rows["total_liabilities_and_equity"].current = (
                rows["total_liabilities"].current + rows["total_equity"].current
            )
        else:
            rows["net_cash_from_operating"].current = _section_sum(fs, t, "operating")
            rows["net_cash_from_investing"].current = _section_sum(fs, t, "investing")
            rows["net_cash_from_financing"].current = _section_sum(fs, t, "financing")
            rows["net_change_in_cash"].current = (
                rows["net_cash_from_operating"].current
                + rows["net_cash_from_investing"].current
                + rows["net_cash_from_financing"].current
            )
            rows["cash_closing"].current = (
                rows["cash_opening"].current + rows["net_change_in_cash"].current
            )


def forge_revenue(fs: FinStmt, new_revenue: Decimal) -> None:
    """The careful forger (tamper T23): inflate revenue, then recompute every subtotal,
    retained earnings, trade receivables, other current liabilities (extra tax accrual) and the
    cash-flow working-capital line so the balance sheet balances and cash ties."""
    inc = {ln.line: ln for ln in fs.tables[INCOME]}
    bal = {ln.line: ln for ln in fs.tables[BALANCE]}
    cfl = {ln.line: ln for ln in fs.tables[CASH]}
    old_pbt, old_tax, old_np = (
        inc["profit_before_tax"].current,
        inc["income_tax"].current,
        inc["net_profit"].current,
    )
    inc["revenue"].current = new_revenue
    inc["gross_profit"].current = new_revenue - inc["cost_of_sales"].current
    new_pbt = (
        inc["gross_profit"].current
        + inc["other_income"].current
        - inc["operating_expenses"].current
        - inc["finance_costs"].current
    )
    inc["profit_before_tax"].current = new_pbt
    rate = D(fs.params.get("tax_rate", str(TAX_RATE)))
    inc["income_tax"].current = whole(max(new_pbt, D(0)) * rate)
    inc["net_profit"].current = new_pbt - inc["income_tax"].current
    d_pbt = new_pbt - old_pbt
    d_tax = inc["income_tax"].current - old_tax
    d_np = inc["net_profit"].current - old_np
    bal["trade_receivables"].current += d_pbt
    bal["other_current_liabilities"].current += d_tax
    bal["retained_earnings"].current += d_np
    recompute_subtotals(fs, BALANCE)
    cfl["profit_before_tax"].current = new_pbt
    cfl["working_capital_changes"].current -= d_pbt
    recompute_subtotals(fs, CASH)
