"""DOC_SCHEMAS must match data-model.md sections 6.1 to 6.13 exactly."""

import pytest

from app.core.llm import validate_json_schema
from app.core.schemas import (
    DOC_SCHEMAS,
    FS_LINES,
    FS_SUBTOTAL_LINES,
    make_page_model,
    response_json_schema,
    response_model_for,
)

FIELDS = {
    "bank_statement": "bank_name account_holder uen account_number period_start period_end "
    "opening_balance closing_balance currency page_x_of_y total_debits total_credits",
    "invoice": "invoice_number invoice_date due_date supplier_name supplier_uen supplier_gst_no "
    "buyer_name buyer_uen currency subtotal gst_rate gst_amount total po_reference "
    "contract_reference payment_status paid_date",
    "financial_statement": "company_name uen period_start period_end currency basis scale_printed auditor",
    "contract": "contract_number title contract_date effective_date end_date seller_name seller_uen "
    "buyer_name buyer_uen contract_value currency payment_terms signatory_seller signatory_buyer",
    "purchase_order": "po_number po_date buyer_name buyer_uen supplier_name supplier_uen currency "
    "subtotal gst_amount total delivery_date contract_reference",
    "company_profile": "company_name uen entity_type status incorporation_date registered_address "
    "principal_activity ssic_code paid_up_capital currency retrieved_date",
    "certificate_of_incorporation": "company_name uen entity_type incorporation_date certificate_number "
    "issue_date issuer",
    "ownership_chart": "company_name uen chart_date ubo_threshold_percent",
    "shareholder_register": "company_name uen register_date total_shares issued_capital currency",
    "source_of_funds_declaration": "declarant_name company_name uen declaration_date declared_amount "
    "currency origin_category origin_description counterparty_name invoice_reference period_start "
    "period_end receiving_account",
    "national_id": "holder_name id_number date_of_birth sex nationality date_of_issue expiry_date address",
    "passport": "holder_name surname given_names passport_number nationality issuing_country "
    "date_of_birth sex date_of_issue date_of_expiry mrz_line1 mrz_line2",
    "boilerplate": "",
    "other": "",
}
FS_COLS = "line label section current prior is_subtotal"
TABLES = {
    "bank_statement": {
        "transactions": "date description debit credit balance",
        "page_chain": "page page_x page_y account_number",
    },
    "invoice": {"lines": "description quantity unit_price gst_amount amount"},
    "financial_statement": {
        "income_statement": FS_COLS,
        "balance_sheet": FS_COLS,
        "cash_flow": FS_COLS,
    },
    "contract": {"milestones": "description due_date amount"},
    "purchase_order": {"lines": "description quantity unit_price gst_amount amount"},
    "company_profile": {
        "directors": "director_name id_number nationality position appointment_date",
        "shareholders": "holder_name id_or_uen shares percent",
    },
    "certificate_of_incorporation": {},
    "ownership_chart": {"holders": "holder_name holder_type id_or_uen percent parent_name is_ubo"},
    "shareholder_register": {
        "shareholders": "holder_name id_or_uen shares percent share_class date_acquired"
    },
    "source_of_funds_declaration": {},
    "national_id": {},
    "passport": {},
    "boilerplate": {},
    "other": {},
}


@pytest.mark.parametrize("doc_type", sorted(FIELDS))
def test_keys_and_columns_match_data_model(doc_type: str) -> None:
    schema = DOC_SCHEMAS[doc_type]
    assert list(schema.fields) == FIELDS[doc_type].split()
    assert {t: list(s.columns) for t, s in schema.tables.items()} == {
        t: cols.split() for t, cols in TABLES[doc_type].items()
    }


def test_all_fourteen_doc_types_present() -> None:
    assert len(DOC_SCHEMAS) == 14


def test_kinds_and_annotations() -> None:
    bs = DOC_SCHEMAS["bank_statement"]
    assert bs.fields["opening_balance"].kind == "money"
    assert bs.fields["period_start"].kind == "date"
    assert bs.fields["account_number"].kind == "id"
    assert bs.tables["transactions"].row_annotations == ["c4", "c5", "counterparty"]
    assert DOC_SCHEMAS["invoice"].fields["gst_rate"].kind == "percent"
    assert DOC_SCHEMAS["invoice"].tables["lines"].columns["quantity"].kind == "decimal"
    assert DOC_SCHEMAS["passport"].fields["sex"].values == ["M", "F", "X"]
    assert DOC_SCHEMAS["financial_statement"].fields["scale_printed"].values == [
        "1",
        "1000",
        "1000000",
    ]


def test_fs_line_keys() -> None:
    assert "gross_profit" in FS_SUBTOTAL_LINES["income_statement"]
    assert "total_liabilities_and_equity" in FS_LINES["balance_sheet"]
    assert "other" in FS_LINES["cash_flow"]
    spec = DOC_SCHEMAS["financial_statement"].tables["balance_sheet"].columns["section"]
    assert spec.values == [
        "current_assets",
        "non_current_assets",
        "current_liabilities",
        "non_current_liabilities",
        "equity",
        "totals",
    ]


def test_response_model_shape() -> None:
    model = response_model_for("bank_statement")
    assert make_page_model is response_model_for
    assert set(model.model_fields) == {"fields", "tables", "page_marker"}
    assert "page_marker" not in response_model_for("invoice").model_fields
    schema = response_json_schema("invoice")
    good = {
        "fields": dict.fromkeys(DOC_SCHEMAS["invoice"].fields),
        "tables": {"lines": [dict.fromkeys(DOC_SCHEMAS["invoice"].tables["lines"].columns)]},
    }
    assert validate_json_schema(good, schema) is None
    bad = {**good, "fields": {**good["fields"], "extra": None}}
    assert validate_json_schema(bad, schema) is not None  # additionalProperties false
    missing = {"fields": good["fields"], "tables": {}}
    assert validate_json_schema(missing, schema) is not None  # all keys required
    model.model_validate(
        {
            "fields": dict.fromkeys(DOC_SCHEMAS["bank_statement"].fields),
            "tables": {"transactions": [], "page_chain": []},
            "page_marker": None,
        }
    )


def test_fs_rows_use_line_enum_and_omit_is_subtotal() -> None:
    schema = response_json_schema("financial_statement")
    row_ok = {
        "line": "revenue",
        "section": "pnl",
        "label": "Revenue",
        "current": "1.00",
        "prior": None,
    }
    page = {
        "fields": dict.fromkeys(DOC_SCHEMAS["financial_statement"].fields),
        "tables": {"income_statement": [row_ok], "balance_sheet": [], "cash_flow": []},
    }
    assert validate_json_schema(page, schema) is None
    page["tables"]["income_statement"] = [{**row_ok, "line": "not_a_line"}]
    assert validate_json_schema(page, schema) is not None
    page["tables"]["income_statement"] = [{**row_ok, "is_subtotal": "true"}]
    assert validate_json_schema(page, schema) is not None
