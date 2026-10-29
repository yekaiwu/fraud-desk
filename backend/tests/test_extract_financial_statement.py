"""Financial statement extraction: three tables, scaling, signs, validation."""

from __future__ import annotations

from decimal import Decimal

from app.extract.financial_statement import map_label, validate_fs
from app.extract.registry import discover, get_extractors
from tests.extract_helpers import FsRow, make_case, render_fs

discover()
TL = get_extractors("financial_statement")["text_layer"]

INCOME = [
    FsRow("Revenue", "10,000", "8,000"),
    FsRow("Cost of sales", "(6,000)", "(5,000)"),
    FsRow("Gross profit", "4,000", "3,000", bold=True),
    FsRow("Other income", "200", "100"),
    FsRow("Operating expenses", "(2,500)", "(2,000)"),
    FsRow("Finance costs", "(100)", "(80)"),
    FsRow("Profit before tax", "1,600", "1,020", bold=True),
    FsRow("Income tax", "(272)", "(173)"),
    FsRow("Net profit", "1,328", "847", bold=True),
]
BALANCE = [
    FsRow("Current assets", heading=True),
    FsRow("Cash and cash equivalents", "3,000", "2,500"),
    FsRow("Trade receivables", "2,000", "1,500"),
    FsRow("Total current assets", "5,000", "4,000", bold=True),
    FsRow("Non-current assets", heading=True),
    FsRow("Property, plant and equipment", "4,000", "3,500"),
    FsRow("Total non-current assets", "4,000", "3,500", bold=True),
    FsRow("Total assets", "9,000", "7,500", bold=True),
    FsRow("Current liabilities", heading=True),
    FsRow("Trade payables", "2,000", "1,800"),
    FsRow("Bank loans", "1,000", "900"),
    FsRow("Total current liabilities", "3,000", "2,700", bold=True),
    FsRow("Non-current liabilities", heading=True),
    FsRow("Bank loans", "2,000", "1,500"),
    FsRow("Total non-current liabilities", "2,000", "1,500", bold=True),
    FsRow("Total liabilities", "5,000", "4,200", bold=True),
    FsRow("Equity", heading=True),
    FsRow("Share capital", "1,000", "1,000"),
    FsRow("Retained earnings", "(500)", "2,300"),
    FsRow("Accumulated reserves", "3,500", "0"),
    FsRow("Total equity", "4,000", "3,300", bold=True),
    FsRow("Total liabilities and equity", "9,000", "7,500", bold=True),
]
CASH = [
    FsRow("Cash flows from operating activities", heading=True),
    FsRow("Profit before tax", "1,600", "1,020"),
    FsRow("Adjustments", "300", "200"),
    FsRow("Changes in working capital", "(400)", "(100)"),
    FsRow("Net cash from operating activities", "1,500", "1,120", bold=True),
    FsRow("Cash flows from investing activities", heading=True),
    FsRow("Purchase of property, plant and equipment", "(700)", "(500)"),
    FsRow("Net cash used in investing activities", "(700)", "(500)", bold=True),
    FsRow("Cash flows from financing activities", heading=True),
    FsRow("Proceeds from bank loans", "500", "400"),
    FsRow("Repayment of bank loans", "(900)", "(300)"),
    FsRow("Net cash used in financing activities", "(400)", "100", bold=True),
    FsRow("Net increase in cash", "400", "720", bold=True),
    FsRow("Cash at beginning of the year", "2,600", "1,880"),
    FsRow("Cash at end of the year", "3,000", "2,600", bold=True),
]
STATEMENTS = {"Income Statement": INCOME, "Balance Sheet": BALANCE, "Statement of Cash Flows": CASH}


def extract(statements=None, **kw):  # noqa: ANN001, ANN003
    pdf = render_fs(statements=statements or STATEMENTS, **kw)
    ctx, doc, _ = make_case(pdf, "financial_statement")
    return TL.extract(doc, ctx), TL.parse_document(doc, ctx), ctx, doc


def line_values(result, table: str, col: str = "current") -> dict[str, str | None]:
    return {
        r.cells["line"].value + "|" + r.cells["label"].value: r.cells[col].value
        for r in result.tables[table].rows
    }


def test_three_tables_with_current_prior_and_subtotal_flags() -> None:
    result, _, ctx, doc = extract()
    inc = result.tables["income_statement"].rows
    assert [r.cells["line"].value for r in inc] == [
        "revenue",
        "cost_of_sales",
        "gross_profit",
        "other_income",
        "operating_expenses",
        "finance_costs",
        "profit_before_tax",
        "income_tax",
        "net_profit",
    ]
    assert [r.cells["current"].value for r in inc][:3] == ["10000.00", "6000.00", "4000.00"]
    assert [r.cells["prior"].value for r in inc][:3] == ["8000.00", "5000.00", "3000.00"]
    assert [r.cells["is_subtotal"].value for r in inc] == [
        "false",
        "false",
        "true",
        "false",
        "false",
        "false",
        "true",
        "false",
        "true",
    ]
    assert {r.cells["section"].value for r in inc} == {"pnl"}
    bs = {r.cells["line"].value: r for r in result.tables["balance_sheet"].rows}
    assert bs["total_assets"].cells["section"].value == "totals"
    assert bs["total_assets"].cells["is_subtotal"].value == "true"
    assert bs["borrowings_current"].cells["section"].value == "current_liabilities"
    assert bs["borrowings_non_current"].cells["section"].value == "non_current_liabilities"
    cf = {r.cells["line"].value: r for r in result.tables["cash_flow"].rows}
    assert cf["capex"].cells["section"].value == "investing"
    assert cf["cash_closing"].cells["section"].value == "cash"
    assert cf["cash_closing"].cells["is_subtotal"].value == "true"
    assert validate_fs(result, ctx, doc).failing == {}
    for table in result.tables.values():
        for row in table.rows:
            assert row.cells["current"].box is not None
            assert row.cells["label"].box is not None
            assert row.cells["current"].verified is True


def test_header_fields_basis_company_and_year_end() -> None:
    result, *_ = extract()
    f = {k: c.value for k, c in result.fields.items()}
    assert f["company_name"] == "Meridian Fastener Supplies Pte. Ltd."
    assert f["uen"] == "201912345R"
    assert f["period_end"] == "2025-12-31"
    assert f["basis"] == "audited"
    assert f["auditor"] == "Fictitious Assurance LLP"
    assert f["scale_printed"] == "1"
    assert "scale_not_printed" in result.warnings
    unaudited, *_ = extract(audited=False)
    assert unaudited.fields["basis"].value == "unaudited"  # management accounts reuse the parser


def test_sign_rules_expenses_positive_retained_earnings_signed_cash_flow_signed() -> None:
    result, *_ = extract()
    inc = line_values(result, "income_statement")
    assert inc["income_tax|Income tax"] == "272.00"  # printed (272): a positive magnitude
    assert inc["cost_of_sales|Cost of sales"] == "6000.00"
    bs = line_values(result, "balance_sheet")
    assert (
        bs["retained_earnings|Retained earnings"] == "-500.00"
    )  # accumulated deficit stays negative
    cf = line_values(result, "cash_flow")
    assert cf["working_capital_changes|Changes in working capital"] == "-400.00"
    assert cf["net_cash_from_investing|Net cash used in investing activities"] == "-700.00"
    raw = result.tables["income_statement"].rows[7].cells["current"].raw
    assert raw == "(272)"  # raw keeps the printed figure


def test_in_thousands_scales_every_money_cell_with_decimal_and_keeps_raw() -> None:
    result, _, ctx, doc = extract(scale_note="(S$'000)", note_column=True)
    assert result.fields["scale_printed"].value == "1000"
    assert result.fields["scale_printed"].raw is not None
    assert "scale_not_printed" not in result.warnings
    rev = result.tables["income_statement"].rows[0].cells["current"]
    assert (rev.value, rev.raw) == ("10000000.00", "10,000")
    assert Decimal(rev.value) == Decimal("10000") * 1000
    assert result.tables["balance_sheet"].rows[0].cells["prior"].value == "2500000.00"
    assert result.fields["currency"].value == "SGD"
    # the Note column (a bare "5") is never read as a figure
    assert all(r.cells["current"].value != "5.00" for t in result.tables.values() for r in t.rows)
    assert validate_fs(result, ctx, doc).failing == {}  # identities hold in printed units


def test_broken_identity_is_flagged_for_the_page_of_the_total() -> None:
    broken = [*BALANCE]
    broken[7] = FsRow("Total assets", "9,500", "7,500", bold=True)  # 5,000 + 4,000 = 9,000
    result, raw, ctx, doc = extract({**STATEMENTS, "Balance Sheet": broken})
    assert validate_fs(raw, ctx, doc).failing == {2: "identity"}
    # the printed (wrong) total is reported as printed, not repaired
    assert line_values(result, "balance_sheet")["total_assets|Total assets"] == "9500.00"


def test_in_thousands_rounding_noise_is_tolerated_by_the_validator() -> None:
    noisy = [*INCOME]
    noisy[2] = FsRow("Gross profit", "4,001", "3,000", bold=True)  # off by one printed unit
    result, raw, ctx, doc = extract({**STATEMENTS, "Income Statement": noisy}, scale_note="S$'000")
    assert validate_fs(raw, ctx, doc).failing == {}


def test_label_mapping_synonyms_and_other() -> None:
    assert map_label("income_statement", "Turnover", None) == ("revenue", "pnl")
    assert map_label("income_statement", "Cost of goods sold", None)[0] == "cost_of_sales"
    assert map_label("income_statement", "Administrative expenses", None)[0] == "operating_expenses"
    assert map_label("income_statement", "Profit for the year", None)[0] == "net_profit"
    assert map_label("income_statement", "Share of associates", None)[0] == "other"
    assert map_label("balance_sheet", "Inventories", None)[0] == "inventories"
    assert map_label("balance_sheet", "Stock", None)[0] == "inventories"
    assert map_label("balance_sheet", "Accumulated losses", "equity")[0] == "retained_earnings"
    assert (
        map_label("balance_sheet", "Total equity and liabilities", None)[0]
        == "total_liabilities_and_equity"
    )
    assert map_label("balance_sheet", "Prepayments", "current_assets") == (
        "other_current_assets",
        "current_assets",
    )
    assert (
        map_label("cash_flow", "Net cash generated from operating activities", None)[0]
        == "net_cash_from_operating"
    )
    assert map_label("cash_flow", "Something odd", "financing") == ("other", "financing")
