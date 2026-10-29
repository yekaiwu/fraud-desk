"""Layout details seen in the generator's own PDFs, rebuilt by hand (no dependency on the generator):
dated balance-forward row, unlabelled holder, '(continued)' page headers, '(S$)' column headers,
text flowing across an empty Cheque column, paid stamp with date, letterhead purchase orders,
prose contracts and statements printed 'from ... to ...'."""

from __future__ import annotations

from app.extract.bank_statement import validate_bank_statement
from app.extract.invoice import validate_invoice
from app.extract.registry import discover, get_extractors
from tests.extract_helpers import draw_pages, make_case

discover()


def extract(pdf: bytes, doc_type: str):
    ctx, doc, _ = make_case(pdf, doc_type)
    return get_extractors(doc_type)["text_layer"].extract(doc, ctx), ctx, doc


# --------------------------------------------------------------------------- bank statement

STATEMENT_P1 = [
    (40, 50, "DBS-STYLE MOCK BANK", "l", 16, True),
    (40, 70, "Statement of Account - Current Account"),
    (40, 92, "MERIDIAN FASTENER SUPPLIES PTE. LTD.", "l", 10, True),
    (340, 92, "Account summary", "l", 10, True),
    (40, 106, "UEN: 201873387K"),
    (340, 106, "Opening balance"),
    (555, 106, "61,538.72", "r"),
    (340, 120, "Closing balance"),
    (555, 120, "29,270.25", "r"),
    (40, 134, "Account No: 1091318723"),
    (40, 148, "Statement period: 01 Jul 2025 to 31 Jul 2025"),
    (40, 162, "Currency: SGD"),
    (40, 190, "Date", "l", 9, True),
    (100, 190, "Description", "l", 9, True),
    (400, 190, "Withdrawals", "r", 9, True),
    (480, 190, "Deposits", "r", 9, True),
    (555, 190, "Balance", "r", 9, True),
    (40, 206, "01 Jul 2025"),
    (100, 206, "BALANCE BROUGHT FORWARD"),
    (555, 206, "61,538.72", "r"),
    (40, 220, "01 Jul 2025"),
    (100, 220, "RENT 20 KAKI BUKIT ROAD #01-48"),
    (400, 220, "4,500.00", "r"),
    (555, 220, "57,038.72", "r"),
    (40, 234, "02 Jul 2025"),
    (100, 234, "PAYNOW FROM GOLDLEAF SUPPLIES PTE LTD"),
    (480, 234, "1,664.25", "r"),
    (555, 234, "58,702.97", "r"),
    (40, 800, "Page 1 of 2"),
]
STATEMENT_P2 = [
    (40, 50, "DBS-STYLE MOCK BANK", "l", 16, True),
    (40, 70, "MERIDIAN FASTENER SUPPLIES PTE. LTD. - Account No: 1091318723 (continued)"),
    (40, 100, "Date", "l", 9, True),
    (100, 100, "Description", "l", 9, True),
    (400, 100, "Withdrawals", "r", 9, True),
    (480, 100, "Deposits", "r", 9, True),
    (555, 100, "Balance", "r", 9, True),
    (40, 116, "21 Jul 2025"),
    (100, 116, "TERM LOAN INSTALMENT"),
    (400, 116, "2,580.00", "r"),
    (555, 116, "56,122.97", "r"),
    (40, 130, "TOTAL"),
    (400, 130, "7,080.00", "r"),
    (480, 130, "1,664.25", "r"),
    (40, 800, "Page 2 of 2"),
]


def test_unlabelled_holder_dated_forward_row_continued_header_and_summary_box() -> None:
    result, ctx, doc = extract(draw_pages([STATEMENT_P1, STATEMENT_P2]), "bank_statement")
    f = {k: c.value for k, c in result.fields.items()}
    assert f["account_holder"] == "MERIDIAN FASTENER SUPPLIES PTE. LTD."  # no label printed
    assert f["account_number"] == "1091318723"  # "(continued)" is not part of the number
    assert f["uen"] == "201873387K"
    assert (f["opening_balance"], f["closing_balance"]) == ("61538.72", "29270.25")
    assert (f["period_start"], f["period_end"]) == ("2025-07-01", "2025-07-31")
    assert (f["total_debits"], f["total_credits"]) == ("7080.00", "1664.25")
    assert "label_repeated: account_number" not in result.warnings
    rows = result.tables["transactions"].rows
    # a dated "BALANCE BROUGHT FORWARD" line stays a balance-only row (checks-financial.md 4)
    assert [r.cells["description"].value for r in rows][:2] == [
        "BALANCE BROUGHT FORWARD",
        "RENT 20 KAKI BUKIT ROAD #01-48",
    ]
    first = rows[0].cells
    assert first["debit"].value is None and first["credit"].value is None
    assert first["balance"].value == "61538.72"
    assert len(rows) == 4  # TOTAL line is not a row
    assert [r.box.page for r in rows] == [1, 1, 1, 2]
    chain = result.tables["page_chain"].rows
    assert [r.cells["account_number"].value for r in chain] == ["1091318723", "1091318723"]
    assert validate_bank_statement(result, ctx, doc).failing == {}


def test_long_description_runs_through_an_empty_cheque_column() -> None:
    page = [
        (40, 50, "OCBC-STYLE MOCK BANK", "l", 16, True),
        (40, 90, "Account No: 2210698280"),
        (40, 190, "Date", "l", 9, True),
        (100, 190, "Value Date", "l", 9, True),
        (170, 190, "Description", "l", 9, True),
        (290, 190, "Cheque", "l", 9, True),
        (400, 190, "Withdrawal", "r", 9, True),
        (470, 190, "Deposit", "r", 9, True),
        (555, 190, "Balance", "r", 9, True),
        (40, 206, "03/09/2025"),
        (100, 206, "03/09/2025"),
        (170, 206, "FUNDS TRANSFER FROM TIDEWATER INDUSTRIES PTE LTD"),
        (470, 206, "3,133.05", "r"),
        (555, 206, "13,133.05", "r"),
    ]
    result, *_ = extract(draw_pages([page]), "bank_statement")
    row = result.tables["transactions"].rows[0]
    assert row.cells["description"].value == "FUNDS TRANSFER FROM TIDEWATER INDUSTRIES PTE LTD"
    assert row.cells["credit"].value == "3133.05"
    assert row.cells["date"].value == "2025-09-03"  # the Value Date column is still ignored


# --------------------------------------------------------------------------- invoice and PO

INVOICE = [
    (40, 50, "Meridian Fastener Supplies Pte. Ltd.", "l", 14, True),
    (400, 50, "TAX INVOICE", "l", 14, True),
    (40, 66, "20 Kaki Bukit Road #01-48, Singapore 239198"),
    (40, 84, "UEN: 201873387K"),
    (340, 84, "Invoice No: INV-2025-0101"),
    (40, 98, "GST Reg No: M38619861E"),
    (340, 98, "Invoice Date: 14 Jul 2025"),
    (340, 112, "Due Date: 12 Sep 2025"),
    (340, 126, "PO Ref: PO-ATP-6654"),
    (340, 140, "Contract Ref: C-2025-001"),
    (40, 160, "BILL TO", "l", 9, True),
    (40, 174, "Acme Trading Pte. Ltd."),
    (340, 174, "Currency: SGD"),
    (40, 188, "UEN: 200414209D"),
    (40, 220, "Description", "l", 9, True),
    (330, 220, "Qty", "r", 9, True),
    (410, 220, "Unit price", "r", 9, True),
    (415, 220, "(S$)", "l", 9, True),
    (520, 220, "Amount", "r", 9, True),
    (525, 220, "(S$)", "l", 9, True),
    (40, 236, "M10 flange nut, stainless steel (box of 50)"),
    (330, 236, "326", "r"),
    (437, 236, "23.29", "r"),
    (544, 236, "7,592.54", "r"),
    (40, 250, "Plain washer M10, galvanised (bag of 500)"),
    (330, 250, "295", "r"),
    (437, 250, "26.04", "r"),
    (544, 250, "7,681.80", "r"),
    (340, 280, "Subtotal"),
    (544, 280, "15,274.34", "r"),
    (340, 294, "GST (9%)"),
    (544, 294, "1,374.69", "r"),
    (340, 308, "Total payable", "l", 9, True),
    (544, 308, "16,649.03", "r"),
    (40, 340, "PAID 13 Aug 2025", "l", 10, True),
    (40, 800, "Page 1 of 1"),
]


def test_invoice_with_currency_tokens_in_headers_and_a_paid_stamp_with_date() -> None:
    result, ctx, doc = extract(draw_pages([INVOICE]), "invoice")
    f = {k: c.value for k, c in result.fields.items()}
    assert [r.cells["unit_price"].value for r in result.tables["lines"].rows] == ["23.29", "26.04"]
    assert [r.cells["amount"].value for r in result.tables["lines"].rows] == ["7592.54", "7681.80"]
    assert (f["subtotal"], f["gst_amount"], f["total"]) == ("15274.34", "1374.69", "16649.03")
    assert f["gst_rate"] == "9.00"
    assert f["payment_status"] == "paid"
    assert f["paid_date"] == "2025-08-13"
    assert result.fields["paid_date"].raw == "13 Aug 2025"
    assert f["currency"] == "SGD"
    assert f["contract_reference"] == "C-2025-001"
    assert f["po_reference"] == "PO-ATP-6654"
    assert f["buyer_name"] == "Acme Trading Pte. Ltd."
    assert f["buyer_uen"] == "200414209D"
    assert f["supplier_uen"] == "201873387K"
    assert validate_invoice(result, ctx, doc).failing == {}


PURCHASE_ORDER = [
    (40, 50, "Acme Trading Pte. Ltd.", "l", 14, True),
    (400, 50, "PURCHASE ORDER", "l", 14, True),
    (40, 66, "84 Pandan Crescent #07-58, Singapore 484926"),
    (40, 84, "UEN: 200414209D"),
    (340, 84, "PO No: PO-ATP-6654"),
    (340, 98, "PO Date: 02 Jul 2025"),
    (340, 112, "Delivery by: 11 Jul 2025"),
    (340, 126, "Contract Ref: C-2025-001"),
    (40, 150, "SUPPLIER", "l", 9, True),
    (40, 164, "Meridian Fastener Supplies Pte. Ltd."),
    (340, 164, "Currency: SGD"),
    (40, 178, "UEN: 201873387K"),
    (40, 192, "GST Reg No: M38619861E"),
    (40, 220, "Description", "l", 9, True),
    (330, 220, "Qty", "r", 9, True),
    (437, 220, "Unit price (S$)", "r", 9, True),
    (544, 220, "Amount (S$)", "r", 9, True),
    (40, 236, "M10 flange nut, stainless steel (box of 50)"),
    (330, 236, "326", "r"),
    (437, 236, "23.29", "r"),
    (544, 236, "7,592.54", "r"),
    (340, 280, "Subtotal"),
    (544, 280, "7,592.54", "r"),
    (340, 294, "GST (9%)"),
    (544, 294, "683.33", "r"),
    (340, 308, "Order total", "l", 9, True),
    (544, 308, "8,275.87", "r"),
]


def test_purchase_order_with_letterhead_buyer_and_supplier_block() -> None:
    result, *_ = extract(draw_pages([PURCHASE_ORDER]), "purchase_order")
    f = {k: c.value for k, c in result.fields.items()}
    assert f["po_number"] == "PO-ATP-6654"
    assert f["po_date"] == "2025-07-02"
    assert f["delivery_date"] == "2025-07-11"
    assert f["buyer_name"] == "Acme Trading Pte. Ltd."  # the letterhead, not 'PURCHASE ORDER'
    assert f["buyer_uen"] == "200414209D"  # the UEN above the SUPPLIER block
    assert f["supplier_name"] == "Meridian Fastener Supplies Pte. Ltd."
    assert f["supplier_uen"] == "201873387K"
    assert f["contract_reference"] == "C-2025-001"
    assert f["total"] == "8275.87"
    assert f["currency"] == "SGD"


# --------------------------------------------------------------------------- contract

CONTRACT = [
    [
        (40, 50, "SUPPLY AGREEMENT FOR INDUSTRIAL FASTENERS", "l", 14, True),
        (40, 70, "Contract No: C-2025-001"),
        (40, 86, "Supply Agreement for Industrial Fasteners", "l", 10, True),
        (40, 102, "This Agreement is made on 21 May 2025"),
        (40, 116, "BETWEEN"),
        (40, 130, "(1) Meridian Fastener Supplies Pte. Ltd. (UEN 201873387K), the Seller;"),
        (40, 144, "AND"),
        (40, 158, "(2) Acme Trading Pte. Ltd. (UEN 200414209D), the Buyer."),
        (40, 180, "2. Contract value"),
        (40, 194, "The total value of this Agreement is S$ 120,000.00 (exclusive of GST)."),
        (40, 216, "3. Term"),
        (40, 230, "This Agreement takes effect on 27 May 2025"),
        (40, 244, "and continues until 30 June 2026."),
        (40, 266, "4. Payment"),
        (40, 280, "Payment terms: 45 days from invoice date."),
        (40, 800, "Page 1 of 2"),
    ],
    [
        (40, 50, "SUPPLY AGREEMENT FOR INDUSTRIAL FASTENERS (continued)"),
        (40, 90, "SIGNED by the parties:"),
        (40, 106, "For the Seller"),
        (300, 106, "For the Buyer"),
        (40, 120, "Lim Hui Min"),
        (300, 120, "Tay Cheng Wei"),
        (40, 134, "Director"),
        (300, 134, "Authorised signatory"),
    ],
]


def test_prose_contract_parties_dates_value_terms_and_signature_columns() -> None:
    result, *_ = extract(draw_pages(CONTRACT), "contract")
    f = {k: c.value for k, c in result.fields.items()}
    assert f["contract_number"] == "C-2025-001"
    assert f["title"] == "Supply Agreement for Industrial Fasteners"  # the sentence-case heading
    assert (
        f["seller_name"] == "Meridian Fastener Supplies Pte. Ltd."
    )  # the full stop of Pte. Ltd. stays
    assert f["seller_uen"] == "201873387K"
    assert f["buyer_name"] == "Acme Trading Pte. Ltd."
    assert f["buyer_uen"] == "200414209D"
    assert f["contract_date"] == "2025-05-21"
    assert f["effective_date"] == "2025-05-27"
    assert f["end_date"] == "2026-06-30"
    assert f["contract_value"] == "120000.00"
    assert result.fields["contract_value"].raw == "120,000.00"
    assert f["currency"] == "SGD"
    assert f["payment_terms"] == "45 days from invoice date"
    assert f["signatory_seller"] == "Lim Hui Min"
    assert f["signatory_buyer"] == "Tay Cheng Wei"
    assert "currency_not_printed" not in result.warnings
    assert result.fields["seller_uen"].box is not None


# --------------------------------------------------------------------------- financial statement


def fs_page(title: str, rows: list[tuple[str, str, str]], extra: list[tuple] = ()) -> list[tuple]:  # noqa: B006
    items: list[tuple] = [
        (40, 50, "Meridian Fastener Supplies Pte. Ltd.", "l", 12, True),
        (40, 66, "Financial Statements"),
        (40, 82, title, "l", 11, True),
        (40, 98, "UEN: 201873387K | (Expressed in Singapore dollars)"),
        (*extra[0],) if extra else (40, 112, "Audited"),
        (400, 130, "FY2025", "r", 9, True),
        (500, 130, "FY2024", "r", 9, True),
    ]
    y = 146
    for label, cur, prior in rows:
        items.append((40, y, label))
        if cur:
            items.append((400, y, cur, "r"))
            items.append((500, y, prior, "r"))
        y += 14
    items.append((40, 800, "Page 1 of 3"))
    return items


def test_statement_printed_from_to_currency_phrase_footer_and_cash_heading() -> None:
    income = fs_page(
        "Statement of Comprehensive Income",
        [("Revenue", "900,000", "747,000"), ("Net profit for the year", "135,619", "77,280")],
        extra=[(40, 112, "For the financial year from 1 January 2025 to 31 December 2025")],
    )
    cash = fs_page(
        "Statement of Cash Flows",
        [
            ("Cash flows from operating activities", "", ""),
            ("Net cash from operating activities", "164,510", "81,716"),
            ("Cash and cash equivalents", "", ""),
            ("Net change in cash", "12,988", "(4,844)"),
            ("Cash at beginning of year", "29,877", "34,721"),
            ("Cash at end of year", "42,865", "29,877"),
        ],
    )
    result, *_ = extract(draw_pages([income, cash]), "financial_statement")
    f = {k: c.value for k, c in result.fields.items()}
    assert (f["period_start"], f["period_end"]) == ("2025-01-01", "2025-12-31")
    assert f["currency"] == "SGD"  # "(Expressed in Singapore dollars)"
    assert f["uen"] == "201873387K"  # cut at the "|" separator
    assert [r.cells["line"].value for r in result.tables["income_statement"].rows] == [
        "revenue",
        "net_profit",
    ]  # the "Page 1 of 3" footer is not a row
    cf = {r.cells["line"].value: r for r in result.tables["cash_flow"].rows}
    assert cf["net_change_in_cash"].cells["section"].value == "cash"
    assert cf["net_change_in_cash"].cells["prior"].value == "-4844.00"
    assert cf["cash_opening"].cells["section"].value == "cash"
