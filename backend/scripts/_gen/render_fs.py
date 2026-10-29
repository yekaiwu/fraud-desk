"""Financial statements renderer: three pages (income statement, balance sheet, cash flow)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .render_common import (
    DocCanvas,
    DocTruth,
    PageLabel,
    RenderedDoc,
    finalize_pdf,
    fmt_date,
    money_str,
    union_box,
)
from .world_fs import BALANCE, CASH, INCOME
from .world_types import FinStmt, FSLine

LEFT = 36.0
SECTION_TITLES = {
    "pnl": None,
    "current_assets": "Current assets",
    "non_current_assets": "Non-current assets",
    "current_liabilities": "Current liabilities",
    "non_current_liabilities": "Non-current liabilities",
    "equity": "Equity",
    "totals": None,
    "operating": "Cash flows from operating activities",
    "investing": "Cash flows from investing activities",
    "financing": "Cash flows from financing activities",
    "cash": "Cash and cash equivalents",
}
EXPENSE_LINES = {"cost_of_sales", "operating_expenses", "finance_costs", "income_tax"}
PAGE_TITLES = {
    INCOME: "Statement of Comprehensive Income",
    BALANCE: "Statement of Financial Position",
    CASH: "Statement of Cash Flows",
}


def _printed(v: Decimal | None, scale: int, bracket: bool) -> str | None:
    if v is None:
        return None
    units = abs(v) / scale
    s = f"{units:,.0f}"
    return f"({s})" if bracket else s


def render_fs(fs: FinStmt, overrides: dict[str, Any] | None = None) -> RenderedDoc:
    key = fs.key
    truth = DocTruth(key=key, doc_type="financial_statement")
    f = truth.set_field
    cv = DocCanvas()
    scale = fs.scale_printed
    has_prior = any(ln.prior is not None for t in fs.tables.values() for ln in t)
    cols_x = (430.0, 545.0) if has_prior else (545.0, 545.0)
    labels: list[PageLabel] = []
    first = True
    for ti, tname in enumerate((INCOME, BALANCE, CASH)):
        if not first:
            cv.new_page()
        first = False
        page = ti + 1
        b = cv.text(LEFT, 44, fs.company_name, size=14, font="Helvetica-Bold")
        cv.text(LEFT, 64, fs.title, size=11, font="Helvetica-Bold")
        cv.text(LEFT, 79, PAGE_TITLES[tname], size=10.5)
        yrs = f"For the financial year from {fmt_date(fs.period_start, 'long')} to {fmt_date(fs.period_end, 'long')}"
        scale_txt = "(Expressed in Singapore dollars)" if scale == 1 else "(Expressed in S$'000)"
        if page == 1:
            f("company_name", fs.company_name, fs.company_name, 1, b)
            bu = cv.text(LEFT, 94, f"UEN: {fs.uen}", size=8.5)
            f("uen", fs.uen, fs.uen, 1, bu)
            b1 = cv.text(LEFT, 108, yrs, size=8.5)
            f("period_start", fs.period_start.isoformat(), fmt_date(fs.period_start, "long"), 1, b1)
            f("period_end", fs.period_end.isoformat(), fmt_date(fs.period_end, "long"), 1, b1)
            bs = cv.text(LEFT, 122, scale_txt, size=8.5)
            f("scale_printed", str(scale), "S$'000" if scale == 1000 else None, 1, bs)
            f("currency", "SGD", "SGD", 1, bs)
            basis_raw = "Unaudited management accounts" if fs.basis == "unaudited" else "Audited"
            bb = cv.text(LEFT, 136, basis_raw, size=8.5)
            f("basis", fs.basis, basis_raw, 1, bb)
            if fs.auditor:
                ba = cv.text(LEFT, 150, f"Independent auditor: {fs.auditor}", size=8.5)
                f("auditor", fs.auditor, fs.auditor, 1, ba)
            else:
                f("auditor", None, None, 1, None)
        else:
            cv.text(LEFT, 94, f"UEN: {fs.uen}  |  {scale_txt}", size=8.5)
        y = 172.0
        cv.line(LEFT, y + 14, 559, y + 14, 0.8)
        cv.text(
            cols_x[0], y, fs.period_end.strftime("FY%Y"), size=8.5, font="Helvetica-Bold", align="r"
        )
        if has_prior:
            cv.text(
                cols_x[1],
                y,
                f"FY{fs.period_end.year - 1}",
                size=8.5,
                font="Helvetica-Bold",
                align="r",
            )
        y += 24
        truth.table(tname, ["line", "label", "section", "current", "prior", "is_subtotal"])
        last_section = None
        for ln in fs.tables[tname]:
            title = SECTION_TITLES.get(ln.section)
            if ln.section != last_section and title:
                cv.text(LEFT, y, title, size=9, font="Helvetica-Bold")
                y += 16
            last_section = ln.section
            _draw_line(cv, truth, tname, ln, y, cols_x, scale, has_prior, page)
            y += 17
        cv.footer(f"Page {page} of 3")
        labels.append(PageLabel("financial_statement", key))
    raw = cv.finish()
    ov = overrides or {}
    pdf = finalize_pdf(
        raw,
        producer=str(ov.get("producer", "Mock Word Processor 3.1")),
        creator=str(ov.get("creator", "Mock Word Processor")),
        title=f"{fs.title} {fs.fy}",
        doc_date=fs.period_end,
    )
    return RenderedDoc(
        key=key,
        doc_type="financial_statement",
        stem=f"financial_statement_{fs.fy}",
        pdf=pdf,
        page_labels=labels,
        truth=truth,
        doc_date=fs.period_end,
        page_start=1,
        page_end=3,
    )


def _draw_line(
    cv: DocCanvas,
    truth: DocTruth,
    tname: str,
    ln: FSLine,
    y: float,
    cols_x: tuple[float, float],
    scale: int,
    has_prior: bool,
    page: int,
) -> None:
    font = "Helvetica-Bold" if ln.is_subtotal else "Helvetica"
    if ln.is_subtotal:
        cv.line(cols_x[0] - 70, y - 1.5, 559, y - 1.5, 0.4)
    bl = cv.text(LEFT + (0 if ln.is_subtotal else 12), y, ln.label, size=8.5, font=font)

    def fmt(v: Decimal | None) -> str | None:
        if v is None:
            return None
        bracket = (v < 0) or (tname == INCOME and ln.line in EXPENSE_LINES)
        return _printed(v, scale, bracket)

    cur_raw, pri_raw = fmt(ln.current), fmt(ln.prior)
    bc = cv.text(cols_x[0], y, cur_raw, size=8.5, align="r", font=font) if cur_raw else None
    bp = (
        cv.text(cols_x[1], y, pri_raw, size=8.5, align="r", font=font)
        if (has_prior and pri_raw)
        else None
    )
    cells = {
        "line": (ln.line, None, None),
        "label": (ln.label, ln.label, bl),
        "section": (ln.section, None, None),
        "current": (money_str(ln.current), cur_raw, bc),
        "prior": (money_str(ln.prior), pri_raw, bp),
        "is_subtotal": ("true" if ln.is_subtotal else "false", None, None),
    }
    truth.add_row(tname, cells, page, union_box([c[2] for c in cells.values() if c[2]]))
