"""Fixture builders for the invoice, financial statement and cross-document rule tests.

Not collected by pytest (no ``test_`` prefix). Fixtures follow docs/checks-financial.md section 9.1:
``F-INV-1`` (``make_invoice``), ``F-FS-1`` / ``F-FS-0`` (``make_fs``), bank statements built with
running balances computed from the rows (``make_statement``) and a clean bundle (``clean_bundle``).
All data is fictitious; UENs are valid under ``stdnum.sg.uen``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from app.checks.registry import CheckSkipped, get_check
from app.core.models import Annotation, Applicant, Document, Flag, Region
from app.core.money import fmt_money
from app.core.testing import make_ctx, make_doc

UEN_A = "201912345R"  # the applicant
UEN_B = "202012345H"  # the buyer
UEN_C = "201934567C"
UEN_D = "53012345B"
APPLICANT = Applicant(name="Example Logistics Pte. Ltd.", uen=UEN_A, source="upload")
SELLER = "EXAMPLE LOGISTICS PTE LTD"
BUYER = "ACME TRADING PTE LTD"


def box(x: float = 0.1, y: float = 0.1, *, page: int = 1, file_id: str = "f1") -> Region:
    return Region(file_id=file_id, page=page, x=x, y=y, w=0.1, h=0.02)


def put_box(doc: Document, field: str, region: Region | None = None) -> Region:
    """Attach a box to a document field's cell and return it."""
    region = region or box()
    doc.fields[field] = doc.fields[field].model_copy(update={"box": region})
    return region


def put_row_box(
    doc: Document, table: str, row: int, col: str, region: Region | None = None
) -> Region:
    region = region or box()
    cell = doc.tables[table].rows[row].cells[col]
    doc.tables[table].rows[row].cells[col] = cell.model_copy(update={"box": region})
    return region


def run_rule(rule: str, ctx: Any) -> list[Flag]:
    """Call a registered rule's function directly (raises CheckSkipped like the runner sees it)."""
    meta = get_check(rule)
    assert meta is not None, rule
    return list(meta.func(ctx))


def skipped(rule: str, ctx: Any) -> str | None:
    try:
        run_rule(rule, ctx)
    except CheckSkipped as exc:
        return exc.reason
    return None


def ctx_of(*docs: Document, applicant: Applicant | None = APPLICANT, **kw: Any):  # noqa: ANN201
    return make_ctx(list(docs), applicant=applicant, **kw)


# ---------------------------------------------------------------- invoice (F-INV-1)

LINES_INV_1: list[dict[str, str | None]] = [
    {"description": "Freight Mar", "quantity": "4", "unit_price": "2000.00", "amount": "8000.00"},
    {"description": "Handling", "quantity": "20", "unit_price": "100.00", "amount": "2000.00"},
]


def make_invoice(
    id: str = "d1",  # noqa: A002
    *,
    fields: dict[str, str | None] | None = None,
    lines: list[dict[str, str | None]] | None = None,
) -> Document:
    base: dict[str, str | None] = {
        "invoice_number": "INV-0042",
        "invoice_date": "2026-03-10",
        "due_date": "2026-04-09",
        "supplier_name": SELLER,
        "supplier_uen": UEN_A,
        "supplier_gst_no": "M21234567K",
        "buyer_name": BUYER,
        "buyer_uen": UEN_B,
        "currency": "SGD",
        "subtotal": "10000.00",
        "gst_rate": "9.00",
        "gst_amount": "900.00",
        "total": "10900.00",
    }
    base.update(fields or {})
    return make_doc(
        "invoice",
        id=id,
        fields=base,
        tables={"lines": [dict(r) for r in (LINES_INV_1 if lines is None else lines)]},
    )


# ---------------------------------------------------------------- bank statement


def R(  # noqa: N802 - terse row constructor used in test tables
    day: str,
    description: str,
    debit: str | None = None,
    credit: str | None = None,
    cp: str | None = None,
    c4: str | None = None,
) -> tuple[str, str, str | None, str | None, str | None, str | None]:
    return day, description, debit, credit, cp, c4


def make_statement(
    id: str = "d3",  # noqa: A002
    *,
    rows: Sequence[tuple] = (),
    opening: str = "10000.00",
    start: str = "2026-03-01",
    end: str = "2026-03-31",
    account: str = "0012345678",
    holder: str = SELLER,
    uen: str | None = UEN_A,
    currency: str = "SGD",
    closing: str | None = None,
    file_id: str = "f1",
) -> Document:
    balance = Decimal(opening)
    table_rows: list[dict[str, str | None]] = []
    annotations: dict[int, dict[str, Annotation]] = {}
    for i, (day, desc, debit, credit, cp, c4) in enumerate(rows):
        balance += Decimal(credit or "0") - Decimal(debit or "0")
        table_rows.append(
            {
                "date": day,
                "description": desc,
                "debit": debit,
                "credit": credit,
                "balance": fmt_money(balance),
            }
        )
        ann: dict[str, Annotation] = {}
        if cp:
            ann["counterparty"] = Annotation(
                label=cp, probability=1.0, classifier="counterparty-parser", version="1"
            )
        if c4:
            ann["c4"] = Annotation(label=c4, probability=0.9, classifier="rules", version="1")
        annotations[i] = ann
    doc = make_doc(
        "bank_statement",
        id=id,
        file_id=file_id,
        fields={
            "bank_name": "FICTITIOUS BANK",
            "account_holder": holder,
            "uen": uen,
            "account_number": account,
            "period_start": start,
            "period_end": end,
            "opening_balance": opening,
            "closing_balance": closing if closing is not None else fmt_money(balance),
            "currency": currency,
        },
        tables={"transactions": table_rows},
    )
    for i, ann in annotations.items():
        doc.tables["transactions"].rows[i].annotations = ann
    return doc


def march_statement(id: str = "d3", **kw: Any) -> Document:  # noqa: A002
    """Cash-rich March statement of the clean bundle (closing 250890.71)."""
    rows = kw.pop(
        "rows",
        [
            R(
                "2026-03-02",
                "FAST PAYMENT FROM ACME TRADING PTE LTD",
                None,
                "70000.00",
                BUYER,
                "trade_receipt",
            ),
            R("2026-03-05", "GIRO PAYMENT TO SUPPLIER ALPHA", "30000.00", None, "SUPPLIER ALPHA"),
            R("2026-03-15", "MONTHLY ACCOUNT FEE", "12.50"),
            R(
                "2026-03-20",
                "FAST PAYMENT FROM ACME TRADING PTE LTD",
                None,
                "10900.00",
                BUYER,
                "trade_receipt",
            ),
            R("2026-03-31", "INTEREST CREDIT", None, "3.21", None, "other"),
        ],
    )
    return make_statement(id, rows=rows, opening=kw.pop("opening", "200000.00"), **kw)


def april_statement(id: str = "d4", **kw: Any) -> Document:  # noqa: A002
    return make_statement(
        id,
        rows=[
            R(
                "2026-04-10",
                "FAST PAYMENT FROM ACME TRADING PTE LTD",
                None,
                "5000.00",
                BUYER,
                "trade_receipt",
            )
        ],
        opening=kw.pop("opening", "250890.71"),
        start="2026-04-01",
        end="2026-04-30",
        **kw,
    )


# ---------------------------------------------------------------- financial statement (F-FS-1 / F-FS-0)

# (line, section, F-FS-1 current, F-FS-0 current, is_subtotal); F-FS-1 prior == F-FS-0 current
FS_DATA: dict[str, list[tuple[str, str, str, str, bool]]] = {
    "income_statement": [
        ("revenue", "pnl", "1200000", "1000000", False),
        ("cost_of_sales", "pnl", "700000", "600000", False),
        ("gross_profit", "pnl", "500000", "400000", True),
        ("operating_expenses", "pnl", "300000", "250000", False),
        ("finance_costs", "pnl", "20000", "20000", False),
        ("profit_before_tax", "pnl", "180000", "130000", True),
        ("income_tax", "pnl", "30600", "22100", False),
        ("net_profit", "pnl", "149400", "107900", True),
    ],
    "balance_sheet": [
        ("cash_and_equivalents", "current_assets", "250000", "190000", False),
        ("trade_receivables", "current_assets", "300000", "250000", False),
        ("inventories", "current_assets", "150000", "120000", False),
        ("total_current_assets", "current_assets", "700000", "560000", True),
        ("property_plant_equipment", "non_current_assets", "400000", "380000", False),
        ("total_non_current_assets", "non_current_assets", "400000", "380000", True),
        ("total_assets", "totals", "1100000", "940000", True),
        ("trade_payables", "current_liabilities", "200000", "180000", False),
        ("borrowings_current", "current_liabilities", "100000", "100000", False),
        ("total_current_liabilities", "current_liabilities", "300000", "280000", True),
        ("borrowings_non_current", "non_current_liabilities", "200000", "250000", False),
        ("total_non_current_liabilities", "non_current_liabilities", "200000", "250000", True),
        ("total_liabilities", "totals", "500000", "530000", True),
        ("share_capital", "equity", "100000", "100000", False),
        ("retained_earnings", "equity", "500000", "310000", False),
        ("total_equity", "equity", "600000", "410000", True),
        ("total_liabilities_and_equity", "totals", "1100000", "940000", True),
    ],
    "cash_flow": [
        ("profit_before_tax", "operating", "180000", "130000", False),
        ("adjustments", "operating", "30000", "25000", False),
        ("working_capital_changes", "operating", "-40000", "-35000", False),
        ("net_cash_from_operating", "operating", "170000", "120000", True),
        ("capex", "investing", "-60000", "-50000", False),
        ("net_cash_from_investing", "investing", "-60000", "-50000", True),
        ("loan_repayments", "financing", "-50000", "-50000", False),
        ("net_cash_from_financing", "financing", "-50000", "-50000", True),
        ("net_change_in_cash", "cash", "60000", "20000", True),
        ("cash_opening", "cash", "190000", "170000", False),
        ("cash_closing", "cash", "250000", "190000", True),
    ],
}


def fs_rows(
    table: str, *, year: int = 1, scale: int = 1, prior: bool | None = None
) -> list[dict[str, str | None]]:
    """Rows of one table. ``year=1``: F-FS-1 (prior column = F-FS-0); ``year=0``: F-FS-0."""
    show_prior = (year == 1) if prior is None else prior
    out: list[dict[str, str | None]] = []
    for line, section, f1, f0, sub in FS_DATA[table]:
        cur = f1 if year == 1 else f0
        out.append(
            {
                "line": line,
                "label": line.replace("_", " ").title(),
                "section": section,
                "current": cur,
                "prior": f0 if show_prior else None,
                "is_subtotal": "true" if sub else "false",
            }
        )
    return out


def make_fs(
    id: str = "d5",  # noqa: A002
    *,
    year: int = 1,
    fields: dict[str, str | None] | None = None,
    edits: dict[tuple[str, str], dict[str, str | None]] | None = None,
    extra: Sequence[tuple[str, dict[str, str | None]]] = (),
    prior: bool | None = None,
    **kw: Any,
) -> Document:
    base: dict[str, str | None] = {
        "company_name": "EXAMPLE LOGISTICS PTE LTD",
        "uen": UEN_A,
        "period_start": "2025-04-01" if year == 1 else "2024-04-01",
        "period_end": "2026-03-31" if year == 1 else "2025-03-31",
        "currency": "SGD",
        "basis": "unaudited",
        "scale_printed": "1",
    }
    base.update(fields or {})
    tables = {t: fs_rows(t, year=year, prior=prior) for t in FS_DATA}
    for (table, line), changes in (edits or {}).items():
        hit = [r for r in tables[table] if r["line"] == line]
        assert hit, (table, line)
        hit[0].update(changes)
    for table, row in extra:
        full = {
            "line": "other",
            "label": "Other",
            "section": "pnl",
            "current": None,
            "prior": None,
            "is_subtotal": "false",
        }
        full.update(row)
        tables[table].append(full)
    return make_doc("financial_statement", id=id, fields=base, tables=tables, **kw)


def row_index(doc: Document, table: str, line: str) -> int:
    for r in doc.tables[table].rows:
        if r.cells["line"].value == line:
            return r.index
    raise KeyError(line)


# ---------------------------------------------------------------- contract / PO / misc


def make_contract(
    id: str = "d7",  # noqa: A002
    *,
    fields: dict[str, str | None] | None = None,
    milestones: list[dict[str, str | None]] | None = None,
) -> Document:
    base: dict[str, str | None] = {
        "contract_number": "C-2026-007",
        "title": "Freight services agreement",
        "contract_date": "2025-12-15",
        "effective_date": "2026-01-01",
        "end_date": "2026-12-31",
        "seller_name": SELLER,
        "seller_uen": UEN_A,
        "buyer_name": BUYER,
        "buyer_uen": UEN_B,
        "contract_value": "500000.00",
        "currency": "SGD",
    }
    base.update(fields or {})
    return make_doc("contract", id=id, fields=base, tables={"milestones": milestones or []})


def make_po(
    id: str = "d8",  # noqa: A002
    *,
    fields: dict[str, str | None] | None = None,
) -> Document:
    base: dict[str, str | None] = {
        "po_number": "PO-7781",
        "po_date": "2026-03-01",
        "buyer_name": BUYER,
        "buyer_uen": UEN_B,
        "supplier_name": SELLER,
        "supplier_uen": UEN_A,
        "currency": "SGD",
        "subtotal": "100000.00",
        "total": "109000.00",
    }
    base.update(fields or {})
    return make_doc("purchase_order", id=id, fields=base)


def make_entity_doc(
    doc_type: str,
    id: str,  # noqa: A002
    *,
    name: str = "Example Logistics Pte. Ltd.",
    uen: str | None = UEN_A,
) -> Document:
    fields: dict[str, str | None] = {"company_name": name, "uen": uen}
    if doc_type == "company_profile":
        fields.update(
            entity_type="Private Company",
            status="Live",
            incorporation_date="2019-04-12",
            principal_activity="Freight forwarding",
        )
    if doc_type == "certificate_of_incorporation":
        fields.update(incorporation_date="2019-04-12")
    if doc_type == "shareholder_register":
        fields.update(register_date="2026-03-31", total_shares="500000")
    return make_doc(doc_type, id=id, fields=fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------- clean bundle (docs 9.2 test 1)

CLEAN_PAID_INVOICE_FIELDS = {
    "payment_status": "paid",
    "paid_date": "2026-03-21",
    "contract_reference": "C-2026-007",
}


def clean_bundle() -> list[Document]:
    """Statements, invoice paid by a matching credit, FS for two years, matching contract."""
    return [
        make_invoice("d1", fields=dict(CLEAN_PAID_INVOICE_FIELDS)),
        march_statement("d3"),
        april_statement("d4"),
        make_fs("d5", year=1),
        make_fs("d6", year=0),
        make_contract("d7"),
        make_entity_doc("certificate_of_incorporation", "d8"),
        make_entity_doc("company_profile", "d9"),
    ]
