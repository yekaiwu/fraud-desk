"""Plain-English labels for what a custom rule may address (docs/custom-rules.md).

Everything here is keyed by the paths of ``spec.addressable_fields()`` and ``spec.fs_lines()``, which
come from ``DOC_SCHEMAS`` and ``FS_LINES``. ``tests/test_custom_rules_labels.py`` fails when a field or
a line is added to the schemas without a meaning here, so the readback and the capability prompt
(which the model reads to learn what is possible) cannot drift from the extractor.

Code-written text only: nothing here comes from an officer or a document.
"""

from __future__ import annotations

from app.custom_rules.spec import addressable_fields, fs_lines

DOC_LABELS: dict[str, str] = {
    "bank_statement": "bank statement",
    "invoice": "invoice",
    "receipt": "receipt",
    "financial_statement": "financial statement",
    "contract": "contract",
    "purchase_order": "purchase order",
    "company_profile": "company profile",
    "certificate_of_incorporation": "certificate of incorporation",
    "ownership_chart": "ownership chart",
    "shareholder_register": "shareholder register",
    "source_of_funds_declaration": "source of funds declaration",
    "national_id": "national ID",
    "passport": "passport",
}

DOC_PLURALS: dict[str, str] = {
    "certificate_of_incorporation": "certificates of incorporation",
    "source_of_funds_declaration": "source of funds declarations",
}

# one line per addressable field: what it is, as an officer would say it
FIELD_MEANINGS: dict[str, str] = {
    "bank_statement.bank_name": "the name of the bank printed on the statement",
    "bank_statement.account_holder": "the account holder as printed on the statement",
    "bank_statement.uen": "the account holder's UEN, when the statement prints one",
    "bank_statement.account_number": "the account number",
    "bank_statement.period_start": "the first day the statement covers",
    "bank_statement.period_end": "the last day the statement covers",
    "bank_statement.opening_balance": "the balance at the start of the period",
    "bank_statement.closing_balance": "the balance at the end of the period",
    "bank_statement.currency": "the account currency (three letters, such as SGD)",
    "bank_statement.page_x_of_y": "the page marker, such as 1 of 3",
    "bank_statement.total_debits": "the printed total of money going out",
    "bank_statement.total_credits": "the printed total of money coming in",
    "invoice.invoice_number": "the invoice number",
    "invoice.invoice_date": "the date of the invoice",
    "invoice.due_date": "the date payment is due",
    "invoice.supplier_name": "the supplier (seller) named on the invoice",
    "invoice.supplier_uen": "the supplier's UEN",
    "invoice.supplier_gst_no": "the supplier's GST registration number",
    "invoice.buyer_name": "the buyer named on the invoice",
    "invoice.buyer_uen": "the buyer's UEN",
    "invoice.currency": "the invoice currency (three letters, such as SGD)",
    "invoice.subtotal": "the amount before GST",
    "invoice.gst_rate": "the GST rate printed on the invoice, as a percentage",
    "invoice.gst_amount": "the GST amount",
    "invoice.total": "the invoice total including GST",
    "invoice.po_reference": "the purchase order the invoice refers to",
    "invoice.contract_reference": "the contract the invoice refers to",
    "invoice.payment_status": "whether the invoice says paid, unpaid or partial",
    "invoice.paid_date": "the date the invoice says it was paid",
    "receipt.merchant_name": "the shop or merchant on the receipt",
    "receipt.receipt_number": "the receipt number",
    "receipt.receipt_date": "the date on the receipt",
    "receipt.currency": "the receipt currency (three letters, such as SGD)",
    "receipt.subtotal": "the amount before tax",
    "receipt.tax_rate": "the tax rate printed on the receipt, as a percentage",
    "receipt.tax_amount": "the tax amount",
    "receipt.total": "the receipt total",
    "financial_statement.company_name": "the company the statements are for",
    "financial_statement.uen": "the company's UEN",
    "financial_statement.period_start": "the first day of the financial year",
    "financial_statement.period_end": "the last day of the financial year",
    "financial_statement.currency": "the reporting currency (three letters, such as SGD)",
    "financial_statement.basis": "whether the statements are audited, unaudited or not stated",
    "financial_statement.scale_printed": "the unit the figures are printed in: 1, 1000 or 1000000",
    "financial_statement.auditor": "the auditor named on the statements",
    "contract.contract_number": "the contract number",
    "contract.title": "the title of the contract",
    "contract.contract_date": "the date the contract was signed or made",
    "contract.effective_date": "the date the contract takes effect",
    "contract.end_date": "the date the contract ends",
    "contract.seller_name": "the seller named in the contract",
    "contract.seller_uen": "the seller's UEN",
    "contract.buyer_name": "the buyer named in the contract",
    "contract.buyer_uen": "the buyer's UEN",
    "contract.contract_value": "the total value of the contract",
    "contract.currency": "the contract currency (three letters, such as SGD)",
    "contract.payment_terms": "the payment terms, as printed",
    "purchase_order.po_number": "the purchase order number",
    "purchase_order.po_date": "the date of the purchase order",
    "purchase_order.buyer_name": "the buyer named on the purchase order",
    "purchase_order.buyer_uen": "the buyer's UEN",
    "purchase_order.supplier_name": "the supplier named on the purchase order",
    "purchase_order.supplier_uen": "the supplier's UEN",
    "purchase_order.currency": "the purchase order currency (three letters, such as SGD)",
    "purchase_order.subtotal": "the amount before GST",
    "purchase_order.gst_amount": "the GST amount",
    "purchase_order.total": "the purchase order total",
    "purchase_order.delivery_date": "the date delivery is promised",
    "purchase_order.contract_reference": "the contract the order refers to",
    "company_profile.company_name": "the company's registered name",
    "company_profile.uen": "the company's UEN",
    "company_profile.entity_type": "the kind of entity, as printed (for example a private limited company)",
    "company_profile.status": "the registry status, as printed (for example live)",
    "company_profile.incorporation_date": "the date the company was incorporated",
    "company_profile.registered_address": "the registered office address, as printed",
    "company_profile.principal_activity": "the company's main business activity, as printed",
    "company_profile.ssic_code": "the industry (SSIC) code",
    "company_profile.paid_up_capital": "the paid-up capital",
    "company_profile.currency": "the currency of the paid-up capital",
    "company_profile.retrieved_date": "the date the profile was retrieved from the registry",
    "certificate_of_incorporation.company_name": "the company named on the certificate",
    "certificate_of_incorporation.uen": "the company's UEN",
    "certificate_of_incorporation.entity_type": "the kind of entity, as printed",
    "certificate_of_incorporation.incorporation_date": "the date of incorporation",
    "certificate_of_incorporation.certificate_number": "the certificate number",
    "certificate_of_incorporation.issue_date": "the date the certificate was issued",
    "certificate_of_incorporation.issuer": "who issued the certificate",
    "ownership_chart.company_name": "the company the chart is for",
    "ownership_chart.uen": "the company's UEN",
    "ownership_chart.chart_date": "the date of the chart",
    "ownership_chart.ubo_threshold_percent": "the ownership percentage the chart uses for beneficial owners",
    "shareholder_register.company_name": "the company the register is for",
    "shareholder_register.uen": "the company's UEN",
    "shareholder_register.register_date": "the date of the register",
    "shareholder_register.total_shares": "the total number of shares issued",
    "shareholder_register.issued_capital": "the issued share capital",
    "shareholder_register.currency": "the currency of the issued capital",
    "source_of_funds_declaration.company_name": "the company making the declaration",
    "source_of_funds_declaration.uen": "the company's UEN",
    "source_of_funds_declaration.declaration_date": "the date of the declaration",
    "source_of_funds_declaration.declared_amount": "the amount of money declared",
    "source_of_funds_declaration.currency": "the currency of the declared amount",
    "source_of_funds_declaration.origin_category": (
        "where the money is said to come from: sales_proceeds, salary, investment_returns, "
        "inheritance, loan, asset_sale or other"
    ),
    "source_of_funds_declaration.origin_description": "the declaration's own words on where the money comes from",
    "source_of_funds_declaration.counterparty_name": "the other party named in the declaration",
    "source_of_funds_declaration.invoice_reference": "the invoice the declaration refers to",
    "source_of_funds_declaration.period_start": "the first day of the period the declaration covers",
    "source_of_funds_declaration.period_end": "the last day of the period the declaration covers",
    "source_of_funds_declaration.receiving_account": "the account the money goes to",
    "national_id.id_number": "the identity card number",
    "national_id.date_of_issue": "the date the card was issued",
    "national_id.expiry_date": "the date the card expires",
    "passport.passport_number": "the passport number",
    "passport.date_of_issue": "the date the passport was issued",
    "passport.date_of_expiry": "the date the passport expires",
}

# financial statement lines (balance sheet and income statement); the catch-all "other" is not addressable
FS_LINE_LABELS: dict[str, str] = {
    "balance_sheet.borrowings_current": "borrowings (current)",
    "balance_sheet.borrowings_non_current": "borrowings (non-current)",
    "balance_sheet.cash_and_equivalents": "cash and cash equivalents",
    "balance_sheet.inventories": "inventories",
    "balance_sheet.other_current_assets": "other current assets",
    "balance_sheet.other_current_liabilities": "other current liabilities",
    "balance_sheet.other_non_current_assets": "other non-current assets",
    "balance_sheet.other_non_current_liabilities": "other non-current liabilities",
    "balance_sheet.property_plant_equipment": "property, plant and equipment",
    "balance_sheet.retained_earnings": "retained earnings",
    "balance_sheet.share_capital": "share capital",
    "balance_sheet.total_assets": "total assets",
    "balance_sheet.total_current_assets": "total current assets",
    "balance_sheet.total_current_liabilities": "total current liabilities",
    "balance_sheet.total_equity": "total equity",
    "balance_sheet.total_liabilities": "total liabilities",
    "balance_sheet.total_liabilities_and_equity": "total liabilities and equity",
    "balance_sheet.total_non_current_assets": "total non-current assets",
    "balance_sheet.total_non_current_liabilities": "total non-current liabilities",
    "balance_sheet.trade_payables": "trade payables",
    "balance_sheet.trade_receivables": "trade receivables",
    "income_statement.cost_of_sales": "cost of sales",
    "income_statement.finance_costs": "finance costs",
    "income_statement.gross_profit": "gross profit",
    "income_statement.income_tax": "income tax",
    "income_statement.net_profit": "net profit",
    "income_statement.operating_expenses": "operating expenses",
    "income_statement.other_income": "other income",
    "income_statement.profit_before_tax": "profit before tax",
    "income_statement.revenue": "revenue",
}

# names the rule above gets wrong (the field repeats a word of its document but is not that word's date)
_NAME_OVERRIDES: dict[str, str] = {
    "certificate_of_incorporation.incorporation_date": "date of incorporation",
}

_TOKEN_CASE = {
    "uen": "UEN",
    "gst": "GST",
    "id": "ID",
    "po": "PO",
    "ssic": "SSIC",
    "ubo": "UBO",
}


def doc_label(doc_type: str) -> str:
    return DOC_LABELS.get(doc_type, doc_type.replace("_", " "))


def doc_plural(doc_type: str) -> str:
    return DOC_PLURALS.get(doc_type) or doc_label(doc_type) + "s"


def article(noun: str) -> str:
    """ "a" or "an" for a noun phrase (the documents here start with a vowel letter or a consonant)."""
    return "an" if noun[:1].lower() in "aeiou" else "a"


def _words(name: str) -> str:
    return " ".join(_TOKEN_CASE.get(t, t) for t in name.split("_"))


def _split(path: str) -> tuple[str, str, list[str]]:
    doc_type, _, name = path.partition(".")
    return doc_type, name, doc_type.split("_")


def field_name(path: str) -> str:
    """The field's own name without its document: ``invoice.total`` -> ``total``, ``passport.date_of_expiry``
    -> ``date of expiry``. A leading word that repeats the document type (``invoice_number``,
    ``register_date``) is dropped: ``number``, ``date``."""
    doc_type, name, tokens = _split(path)
    if path in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[path]
    for prefix in (doc_type, tokens[-1]):
        if name.startswith(prefix + "_"):
            return _words(name[len(prefix) + 1 :])
    return _words(name)


def field_label(path: str) -> str:
    """A name that stands on its own: ``invoice total``, ``passport date of expiry``, ``receipt number``."""
    doc_type, name, tokens = _split(path)
    if path in _NAME_OVERRIDES:
        return f"{doc_label(doc_type)} {_NAME_OVERRIDES[path]}"
    if name.startswith(doc_type + "_") or name.startswith(tokens[0] + "_"):
        return _words(name)  # the name already says what document it is of
    return f"{doc_label(doc_type)} {field_name(path)}"


def field_meaning(path: str) -> str:
    return FIELD_MEANINGS.get(path) or field_label(path)


def line_label(path: str) -> str:
    return FS_LINE_LABELS.get(path) or path.partition(".")[2].replace("_", " ")


def complete() -> list[str]:
    """Paths that have no label yet (empty when the labels cover the schemas; the drift test)."""
    missing = [p for p in addressable_fields() if p not in FIELD_MEANINGS]
    missing += [p for p in fs_lines() if p not in FS_LINE_LABELS]
    return missing
