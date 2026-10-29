"""Financial statement rules (docs/checks-financial.md section 6, plan 4.3).

Values are already normalised to whole currency units by the extractor; ``scale_printed`` only
widens the tolerance (statements printed in thousands carry about one printed unit of rounding
per two terms). Amounts are ``Decimal``; "today" is never needed here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.checks.registry import CheckSkipped, check, make_flag
from app.core.context import CaseContext
from app.core.models import Cell, Document, Flag, Region, Row
from app.core.money import approx_eq, fmt_money
from app.core.textnorm import company_disc, company_match, uen_disc

ZERO = Decimal("0")
USABLE = ("ok", "partial")
FS_TABLES = ("income_statement", "balance_sheet", "cash_flow")
FS_YEAR_GAP_DAYS: tuple[int, int] = (350, 380)
DOC_CAP = 5


# ---------------------------------------------------------------- helpers

_EMPTY = Cell(value=None)


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


def _capped(items: list[Flag], cap: int) -> list[Flag]:
    if len(items) <= cap:
        return items
    kept = list(items[:cap])
    kept[-1] = kept[-1].model_copy(
        update={"evidence": f"{kept[-1].evidence}; +{len(items) - cap} more"}
    )
    return kept


def _fs_tol(ctx: CaseContext, doc: Document, terms: int) -> Decimal:
    try:
        scale = int(_c(doc, "scale_printed").value or "1")
    except ValueError:
        scale = 1
    if scale == 1:
        return _tol(ctx)
    return Decimal(scale) * max(1, terms // 2)


def _rows(doc: Document, table: str) -> list[Row]:
    t = doc.tables.get(table)
    return list(t.rows) if t is not None else []


def _line(row: Row) -> str:
    return _rc(row, "line").text()


def _is_sub(row: Row) -> bool:
    return _rc(row, "is_subtotal").value == "true"


def _first_row(doc: Document, table: str, key: str, col: str = "current") -> Row | None:
    """First row with this line key that has a value in ``col`` (else the first row with the key)."""
    rows = [r for r in _rows(doc, table) if _line(r) == key]
    for r in rows:
        if _rc(r, col).money() is not None:
            return r
    return rows[0] if rows else None


def _L(doc: Document, table: str, key: str, col: str = "current") -> Decimal | None:
    """Sum of ``col`` over rows with ``line == key`` (first row only for a printed subtotal)."""
    rows = [r for r in _rows(doc, table) if _line(r) == key and _rc(r, col).money() is not None]
    if not rows:
        return None
    if any(_is_sub(r) for r in rows):
        return _rc(next(r for r in rows if _is_sub(r)), col).money()
    return sum((_rc(r, col).money() for r in rows), ZERO)  # type: ignore[misc]


def _section_rows(doc: Document, table: str, section: str, col: str) -> list[Row]:
    return [
        r
        for r in _rows(doc, table)
        if _rc(r, "section").value == section and not _is_sub(r) and _rc(r, col).money() is not None
    ]


def _section_sum(
    doc: Document, table: str, section: str, col: str
) -> tuple[Decimal, list[Row]] | None:
    rows = _section_rows(doc, table, section, col)
    if not rows:
        return None
    return sum((_rc(r, col).money() for r in rows), ZERO), rows  # type: ignore[misc]


_BS_SECTION = {
    "total_current_assets": "current_assets",
    "total_non_current_assets": "non_current_assets",
    "total_current_liabilities": "current_liabilities",
    "total_non_current_liabilities": "non_current_liabilities",
    "total_equity": "equity",
}


def _bs(doc: Document, key: str, col: str) -> Decimal | None:
    """A balance sheet total: the printed one, else derived from its section subtotals or lines."""
    printed = _L(doc, "balance_sheet", key, col)
    if printed is not None:
        return printed
    if key in _BS_SECTION:
        s = _section_sum(doc, "balance_sheet", _BS_SECTION[key], col)
        return s[0] if s else None
    parts = {
        "total_assets": ("total_current_assets", "total_non_current_assets"),
        "total_liabilities": ("total_current_liabilities", "total_non_current_liabilities"),
    }.get(key)
    if parts:
        a, b = _bs(doc, parts[0], col), _bs(doc, parts[1], col)
        if a is None and b is None:
            return None
        return (a or ZERO) + (b or ZERO)
    return None


def _cell_of(row: Row | None, col: str) -> Cell:
    return _rc(row, col) if row is not None else _EMPTY


# ---------------------------------------------------------------- fs_balance


@check(
    rule="fs_balance",
    applies_to={"financial_statement"},
    severity="high",
    use_cases={"aml", "loan"},
    description="Total assets equal total liabilities plus equity on the balance sheet.",
    tamper="A balance sheet figure changed without rebalancing.",
)
def fs_balance(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "financial_statement"):
        for col in ("current", "prior"):
            ta = _bs(doc, "total_assets", col)
            if ta is None:
                continue
            tle = _L(doc, "balance_sheet", "total_liabilities_and_equity", col)
            if tle is not None:
                other, terms, side_row = (
                    tle,
                    3,
                    _first_row(doc, "balance_sheet", "total_liabilities_and_equity", col),
                )
                desc = f"total_liabilities_and_equity {fmt_money(tle)}"
            else:
                tl, te = _bs(doc, "total_liabilities", col), _bs(doc, "total_equity", col)
                if tl is None or te is None:
                    continue
                other, terms, side_row = (
                    tl + te,
                    3,
                    _first_row(doc, "balance_sheet", "total_equity", col),
                )
                desc = (
                    f"total_liabilities {fmt_money(tl)} + total_equity {fmt_money(te)} = "
                    f"{fmt_money(other)}"
                )
            evaluated += 1
            if approx_eq(ta, other, _fs_tol(ctx, doc, terms)):
                continue
            ta_row = _first_row(doc, "balance_sheet", "total_assets", col)
            ta_cell = _cell_of(ta_row, col)
            flags.append(
                make_flag(
                    "fs_balance",
                    docs=[doc],
                    table="balance_sheet",
                    row=ta_row.index if ta_row is not None else None,
                    region=_lab(ta_cell.box, "total_assets"),
                    extra_regions=_regions([_cell_of(side_row, col)]),
                    discriminator="prior" if col == "prior" else None,
                    evidence=(
                        f"{desc}; printed total_assets {fmt_money(ta)}; diff "
                        f"{fmt_money(abs(ta - other))}"
                        + (" (prior year)" if col == "prior" else "")
                        + _unverified({"total_assets": ta_cell})
                    ),
                    explanation="Total assets do not equal total liabilities plus equity.",
                )
            )
    if evaluated == 0:
        raise CheckSkipped("no financial statement with derivable balance sheet totals")
    return flags


# ---------------------------------------------------------------- fs_subtotals


@dataclass
class _Formula:
    expected: Decimal
    terms: list[tuple[str, str, Decimal, Cell]] = field(
        default_factory=list
    )  # (sign, name, value, cell)
    kind: str = "formula"  # "formula" | "sum"
    n_lines: int = 0
    cells: list[Cell] = field(default_factory=list)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(n for _, n, _, _ in self.terms)

    def text(self) -> str:
        if self.kind == "sum":
            return f"sum of {self.n_lines} lines {fmt_money(self.expected)}"
        pieces: list[str] = []
        for i, (sign, name, value, _) in enumerate(self.terms):
            piece = f"{name} {fmt_money(value)}"
            pieces.append(("-" if sign == "-" else "") + piece if i == 0 else f"{sign} {piece}")
        return f"{' '.join(pieces)} = {fmt_money(self.expected)}"


def _sum_formula(doc: Document, table: str, section: str, col: str) -> _Formula | None:
    s = _section_sum(doc, table, section, col)
    if s is None:
        return None
    total, rows = s
    return _Formula(total, kind="sum", n_lines=len(rows), cells=[_rc(r, col) for r in rows])


def _terms_formula(
    doc: Document, table: str, col: str, spec: list[tuple[str, str]], required: set[str]
) -> _Formula | None:
    """``spec`` = [(sign, line key)]; keys missing from the statement are optional unless required."""
    terms: list[tuple[str, str, Decimal, Cell]] = []
    for sign, key in spec:
        value = _L(doc, table, key, col)
        if value is None:
            if key in required:
                return None
            continue
        row = _first_row(doc, table, key, col)
        terms.append((sign, key, value, _cell_of(row, col)))
    if not terms:
        return None
    total = ZERO
    for sign, _, value, _ in terms:
        total += value if sign == "+" else -value
    return _Formula(total, terms=terms, cells=[t[3] for t in terms])


def _bs_total_formula(doc: Document, key: str, col: str) -> _Formula | None:
    if key in _BS_SECTION:
        return _sum_formula(doc, "balance_sheet", _BS_SECTION[key], col)
    parts = {
        "total_assets": ("total_current_assets", "total_non_current_assets"),
        "total_liabilities": ("total_current_liabilities", "total_non_current_liabilities"),
        "total_liabilities_and_equity": ("total_liabilities", "total_equity"),
    }[key]
    terms: list[tuple[str, str, Decimal, Cell]] = []
    for name in parts:
        value = _bs(doc, name, col)
        if value is not None:
            row = _first_row(doc, "balance_sheet", name, col)
            terms.append(("+", name, value, _cell_of(row, col)))
    if not terms:
        return None
    return _Formula(sum((t[2] for t in terms), ZERO), terms=terms, cells=[t[3] for t in terms])


_CF_SECTION = {
    "net_cash_from_operating": "operating",
    "net_cash_from_investing": "investing",
    "net_cash_from_financing": "financing",
}


def _cf_net(doc: Document, key: str, col: str) -> Decimal | None:
    printed = _L(doc, "cash_flow", key, col)
    if printed is not None:
        return printed
    s = _section_sum(doc, "cash_flow", _CF_SECTION[key], col)
    return s[0] if s else None


def _formula_for(doc: Document, table: str, key: str, col: str) -> _Formula | None:
    if table == "income_statement":
        if key == "gross_profit":
            return _terms_formula(
                doc, table, col, [("+", "revenue"), ("-", "cost_of_sales")], {"revenue"}
            )
        if key == "profit_before_tax":
            tail = [("+", "other_income"), ("-", "operating_expenses"), ("-", "finance_costs")]
            if _L(doc, table, "gross_profit", col) is not None:
                return _terms_formula(
                    doc, table, col, [("+", "gross_profit"), *tail], {"gross_profit"}
                )
            return _terms_formula(
                doc, table, col, [("+", "revenue"), ("-", "cost_of_sales"), *tail], {"revenue"}
            )
        if key == "net_profit":
            return _terms_formula(
                doc,
                table,
                col,
                [("+", "profit_before_tax"), ("-", "income_tax")],
                {"profit_before_tax"},
            )
    elif table == "balance_sheet":
        if key in _BS_SECTION or key in (
            "total_assets",
            "total_liabilities",
            "total_liabilities_and_equity",
        ):
            return _bs_total_formula(doc, key, col)
    elif table == "cash_flow":
        if key in _CF_SECTION:
            return _sum_formula(doc, table, _CF_SECTION[key], col)
        if key == "net_change_in_cash":
            terms = []
            for name in _CF_SECTION:
                value = _cf_net(doc, name, col)
                if value is not None:
                    terms.append(
                        ("+", name, value, _cell_of(_first_row(doc, table, name, col), col))
                    )
            if not terms:
                return None
            return _Formula(
                sum((t[2] for t in terms), ZERO), terms=terms, cells=[t[3] for t in terms]
            )
        if key == "cash_closing":
            opening = _L(doc, table, "cash_opening", col)
            change = _L(doc, table, "net_change_in_cash", col)
            if change is None:
                parts = [_cf_net(doc, n, col) for n in _CF_SECTION]
                if any(p is not None for p in parts):
                    change = sum((p for p in parts if p is not None), ZERO)
            if opening is None or change is None:
                return None
            terms = [
                (
                    "+",
                    "cash_opening",
                    opening,
                    _cell_of(_first_row(doc, table, "cash_opening", col), col),
                ),
                (
                    "+",
                    "net_change_in_cash",
                    change,
                    _cell_of(_first_row(doc, table, "net_change_in_cash", col), col),
                ),
            ]
            return _Formula(opening + change, terms=terms, cells=[t[3] for t in terms])
    return None


_SUBTOTAL_KEYS: dict[str, tuple[str, ...]] = {
    "income_statement": ("gross_profit", "profit_before_tax", "net_profit"),
    "balance_sheet": (
        "total_current_assets",
        "total_non_current_assets",
        "total_assets",
        "total_current_liabilities",
        "total_non_current_liabilities",
        "total_liabilities",
        "total_equity",
        "total_liabilities_and_equity",
    ),
    "cash_flow": (
        "net_cash_from_operating",
        "net_cash_from_investing",
        "net_cash_from_financing",
        "net_change_in_cash",
        "cash_closing",
    ),
}


@check(
    rule="fs_subtotals",
    applies_to={"financial_statement"},
    severity="high",
    use_cases={"aml", "loan"},
    description="Each printed subtotal on the income statement, balance sheet and cash flow equals the sum of its lines.",
    tamper="An edited line or an edited subtotal that was not rebuilt.",
)
def fs_subtotals(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "financial_statement"):
        doc_flags: list[Flag] = []
        unmapped_rows = [
            r
            for r in _rows(doc, "income_statement")
            if _line(r) == "other" and not _is_sub(r) and _rc(r, "current").money() is not None
        ]
        for table in FS_TABLES:
            for key in _SUBTOTAL_KEYS[table]:
                row = next(
                    (
                        r
                        for r in _rows(doc, table)
                        if _line(r) == key and _is_sub(r) and _rc(r, "current").money() is not None
                    ),
                    None,
                )
                if row is None:
                    continue
                if table == "income_statement" and key == "profit_before_tax" and unmapped_rows:
                    labels = ", ".join(
                        f'"{_clip(_rc(r, "label").text())}"' for r in unmapped_rows[:3]
                    )
                    doc_flags.append(
                        make_flag(
                            "fs_subtotals",
                            docs=[doc],
                            table=table,
                            row=unmapped_rows[0].index,
                            region=_lab(_rc(unmapped_rows[0], "current").box, "unmapped line"),
                            severity="info",
                            discriminator="unmapped",
                            evidence=(
                                f"subtotal profit_before_tax not verified: unmapped line(s) {labels}"
                            ),
                            explanation=(
                                "A line on the income statement could not be mapped, so the profit "
                                "subtotal was not verified."
                            ),
                        )
                    )
                    continue
                current = _formula_for(doc, table, key, "current")
                if current is None:
                    continue
                evaluated += 1
                for col in ("current", "prior"):
                    printed = _rc(row, col).money()
                    if printed is None:
                        continue
                    formula = (
                        current if col == "current" else _formula_for(doc, table, key, "prior")
                    )
                    if formula is None or (col == "prior" and not current.names <= formula.names):
                        continue
                    n = len(formula.terms) or formula.n_lines
                    if approx_eq(printed, formula.expected, _fs_tol(ctx, doc, n)):
                        continue
                    cell = _rc(row, col)
                    label = _rc(row, "label").text() or key
                    doc_flags.append(
                        make_flag(
                            "fs_subtotals",
                            docs=[doc],
                            table=table,
                            row=row.index,
                            region=_lab(cell.box, key),
                            extra_regions=_regions(formula.cells),
                            discriminator="prior" if col == "prior" else None,
                            evidence=(
                                f"{key}: printed {fmt_money(printed)}; {formula.text()}; diff "
                                f"{fmt_money(abs(printed - formula.expected))}"
                                + (" (prior year)" if col == "prior" else "")
                                + _unverified({key: cell})
                            ),
                            explanation=f"The printed {_clip(label)} does not equal the sum of its lines.",
                        )
                    )
        flags.extend(_capped(doc_flags, DOC_CAP))
    if evaluated == 0 and not flags:
        raise CheckSkipped("no financial statement has a printed subtotal with a derivable formula")
    return flags


# ---------------------------------------------------------------- fs_cash_tie


@check(
    rule="fs_cash_tie",
    applies_to={"financial_statement"},
    severity="high",
    use_cases={"aml", "loan"},
    description="Closing cash on the cash-flow statement equals cash on the balance sheet, and opening cash equals last year's balance sheet cash.",
    tamper="A cash figure changed on one statement only.",
)
def fs_cash_tie(ctx: CaseContext) -> list[Flag]:
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "financial_statement"):
        bs_row = _first_row(doc, "balance_sheet", "cash_and_equivalents", "current")
        bs_cash = _L(doc, "balance_sheet", "cash_and_equivalents", "current")
        closing = _L(doc, "cash_flow", "cash_closing", "current")
        if closing is not None and bs_cash is not None:
            evaluated += 1
            if not approx_eq(closing, bs_cash, _fs_tol(ctx, doc, 2)):
                cf_row = _first_row(doc, "cash_flow", "cash_closing", "current")
                cell = _cell_of(cf_row, "current")
                flags.append(
                    make_flag(
                        "fs_cash_tie",
                        docs=[doc],
                        table="cash_flow",
                        row=cf_row.index if cf_row is not None else None,
                        region=_lab(cell.box, "cash_closing"),
                        extra_regions=_regions([_cell_of(bs_row, "current")]),
                        evidence=(
                            f"cash_closing {fmt_money(closing)} (cash flow) vs cash_and_equivalents "
                            f"{fmt_money(bs_cash)} (balance sheet); diff "
                            f"{fmt_money(abs(closing - bs_cash))}"
                            + _unverified({"cash_closing": cell})
                        ),
                        explanation=(
                            "Closing cash on the cash-flow statement does not equal cash on the "
                            "balance sheet."
                        ),
                    )
                )
        opening = _L(doc, "cash_flow", "cash_opening", "current")
        prior_cash = _L(doc, "balance_sheet", "cash_and_equivalents", "prior")
        if opening is not None and prior_cash is not None:
            evaluated += 1
            if not approx_eq(opening, prior_cash, _fs_tol(ctx, doc, 2)):
                op_row = _first_row(doc, "cash_flow", "cash_opening", "current")
                cell = _cell_of(op_row, "current")
                flags.append(
                    make_flag(
                        "fs_cash_tie",
                        docs=[doc],
                        table="cash_flow",
                        row=op_row.index if op_row is not None else None,
                        region=_lab(cell.box, "cash_opening"),
                        extra_regions=_regions([_cell_of(bs_row, "prior")]),
                        severity="medium",
                        discriminator="opening",
                        evidence=(
                            f"cash_opening {fmt_money(opening)} (cash flow) vs prior-year "
                            f"cash_and_equivalents {fmt_money(prior_cash)} (balance sheet); diff "
                            f"{fmt_money(abs(opening - prior_cash))}"
                            + _unverified({"cash_opening": cell})
                        ),
                        explanation=(
                            "Opening cash on the cash-flow statement does not equal last year's "
                            "balance sheet cash."
                        ),
                    )
                )
    if evaluated == 0:
        raise CheckSkipped(
            "no financial statement has both a cash-flow and a balance sheet cash figure"
        )
    return flags


# ---------------------------------------------------------------- fs_comparatives


def _same_entity(a: Document, b: Document) -> bool:
    ua, ub = _c(a, "uen").text().upper(), _c(b, "uen").text().upper()
    if ua and ub:
        return ua == ub
    return company_match(_c(a, "company_name").text(), _c(b, "company_name").text())


@check(
    rule="fs_comparatives",
    applies_to={"financial_statement"},
    severity="high",
    use_cases={"aml", "loan"},
    description="The prior-year column of one year's statement equals what the previous year's statement reported.",
    tamper="Prior-year figures rewritten to flatter the trend.",
)
def fs_comparatives(ctx: CaseContext) -> list[Flag]:
    docs = [d for d in _docs(ctx, "financial_statement") if _c(d, "period_end").date_() is not None]
    flags: list[Flag] = []
    evaluated = 0
    lo, hi = FS_YEAR_GAP_DAYS
    for older in docs:
        for newer in docs:
            if older is newer:
                continue
            gap = (_c(newer, "period_end").date_() - _c(older, "period_end").date_()).days  # type: ignore[operator]
            if not (lo <= gap <= hi) or not _same_entity(older, newer):
                continue
            evaluated += 1
            tol = max(_fs_tol(ctx, newer, 2), _fs_tol(ctx, older, 2))
            pair_flags: list[Flag] = []
            year_n = _c(newer, "period_end").date_().year  # type: ignore[union-attr]
            year_o = _c(older, "period_end").date_().year  # type: ignore[union-attr]
            for table in FS_TABLES:
                by_key_n: dict[str, list[Row]] = defaultdict(list)
                by_key_o: dict[str, list[Row]] = defaultdict(list)
                for r in _rows(newer, table):
                    by_key_n[_line(r)].append(r)
                for r in _rows(older, table):
                    by_key_o[_line(r)].append(r)
                compared: list[tuple[Row, Row, bool]] = []  # (new row, old row, mismatch)
                for key in by_key_n:
                    if (
                        key in ("other", "")
                        or len(by_key_n[key]) != 1
                        or len(by_key_o.get(key, [])) != 1
                    ):
                        continue
                    rn, ro = by_key_n[key][0], by_key_o[key][0]
                    prior, current = _rc(rn, "prior").money(), _rc(ro, "current").money()
                    if prior is None or current is None:
                        continue
                    compared.append((rn, ro, not approx_eq(prior, current, tol)))
                # a section whose printed subtotals all agree: line mismatches are a reclassification
                # (not applied to the income statement: its single section would hide a rewritten revenue)
                sub_state: dict[str, list[bool]] = defaultdict(list)
                for rn, _, bad in compared:
                    if _is_sub(rn):
                        sub_state[_rc(rn, "section").text()].append(bad)
                suppressed = {
                    s
                    for s, states in sub_state.items()
                    if states and not any(states) and table != "income_statement"
                }
                for rn, ro, bad in sorted(compared, key=lambda t: t[0].index):
                    if not bad:
                        continue
                    if not _is_sub(rn) and _rc(rn, "section").text() in suppressed:
                        continue
                    key = _line(rn)
                    prior_cell = _rc(rn, "prior")
                    restated = (
                        "restated"
                        in (_rc(rn, "label").text() + " " + _rc(ro, "label").text()).lower()
                    )
                    pair_flags.append(
                        make_flag(
                            "fs_comparatives",
                            docs=[older, newer],
                            table=table,
                            row=rn.index,
                            region=_lab(prior_cell.box, "prior"),
                            extra_regions=_regions([_rc(ro, "current")]),
                            severity="info" if restated else None,
                            discriminator="restated" if restated else None,
                            evidence=(
                                f"{key}: {year_n} prior column {fmt_money(prior_cell.money())} vs "  # type: ignore[arg-type]
                                f"{year_o} reported {fmt_money(_rc(ro, 'current').money())}; diff "  # type: ignore[arg-type]
                                f"{fmt_money(abs(prior_cell.money() - _rc(ro, 'current').money()))}"  # type: ignore[operator]
                                + (" (restated)" if restated else "")
                                + _unverified({"prior": prior_cell, "reported": _rc(ro, "current")})
                            ),
                            explanation=(
                                "The prior-year figure for "
                                f"{_clip(_rc(rn, 'label').text() or key)} differs from what last "
                                "year's statement reported."
                            ),
                        )
                    )
            flags.extend(_capped(pair_flags, DOC_CAP))
    if evaluated == 0:
        raise CheckSkipped("needs two years")
    return flags


# ---------------------------------------------------------------- fs_entity


@check(
    rule="fs_entity",
    applies_to={"financial_statement"},
    severity="medium",
    use_cases={"aml", "loan"},
    description="The company named on the financial statements is the applicant.",
    tamper="Financial statements belonging to a different company.",
)
def fs_entity(ctx: CaseContext) -> list[Flag]:
    app_name, app_uen = (
        (ctx.applicant.name or ""),
        "".join((ctx.applicant.uen or "").split()).upper(),
    )
    if not (app_name or app_uen):
        raise CheckSkipped("no applicant")
    flags: list[Flag] = []
    evaluated = 0
    for doc in _docs(ctx, "financial_statement"):
        uen_c, name_c = _c(doc, "uen"), _c(doc, "company_name")
        uen = "".join(uen_c.text().split()).upper()
        name = name_c.text()
        if uen and app_uen:
            evaluated += 1
            if uen != app_uen:
                flags.append(
                    make_flag(
                        "fs_entity",
                        docs=[doc],
                        field="uen",
                        region=_lab(uen_c.box, "uen"),
                        discriminator=uen_disc(uen),
                        evidence=f"uen {_clip(uen)} vs applicant uen {_clip(app_uen)}"
                        + _unverified({"uen": uen_c}),
                        explanation="These financial statements are for a different company from the applicant.",
                    )
                )
            continue
        if name and app_name:
            evaluated += 1
            if not company_match(name, app_name):
                flags.append(
                    make_flag(
                        "fs_entity",
                        docs=[doc],
                        field="company_name",
                        region=_lab(name_c.box, "company_name"),
                        discriminator=company_disc(name),
                        evidence=f'company_name "{_clip(name)}" vs applicant "{_clip(app_name)}"'
                        + _unverified({"company_name": name_c}),
                        explanation="These financial statements are for a different company from the applicant.",
                    )
                )
    if evaluated == 0:
        raise CheckSkipped("no financial statement has a name or UEN to compare")
    return flags
