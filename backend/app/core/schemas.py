"""Machine-readable document schemas (docs/data-model.md sections 5 and 6).

``DOC_SCHEMAS[doc_type].fields`` lists EVERY key of ``Document.fields``; ``.tables`` lists every
table with its exact column names. ``response_model_for`` builds the strict Pydantic model the LLM
extractor returns (model-facing JSON: every value is a string or null, ingest-and-extraction 3.5).
"""

from __future__ import annotations

from functools import cache
from typing import Any, Literal

from pydantic import ConfigDict, create_model

from app.core.models import CellKind, Strict


class FieldSpec(Strict):
    kind: CellKind
    required: bool = False
    values: list[str] | None = None


class TableSpec(Strict):
    columns: dict[str, FieldSpec]
    row_annotations: list[str] = []
    required: bool = False


class DocSchema(Strict):
    fields: dict[str, FieldSpec]
    tables: dict[str, TableSpec] = {}


def _f(kind: CellKind, required: bool = True, values: list[str] | None = None) -> FieldSpec:
    return FieldSpec(kind=kind, required=required, values=values)


def _opt(kind: CellKind, values: list[str] | None = None) -> FieldSpec:
    return FieldSpec(kind=kind, required=False, values=values)


# --------------------------------------------------------------------------------------
# Financial statement line keys (data-model.md 6.3)
# --------------------------------------------------------------------------------------

FS_TABLES = ("income_statement", "balance_sheet", "cash_flow")

FS_SECTIONS: dict[str, list[str]] = {
    "income_statement": ["pnl"],
    "balance_sheet": [
        "current_assets",
        "non_current_assets",
        "current_liabilities",
        "non_current_liabilities",
        "equity",
        "totals",
    ],
    "cash_flow": ["operating", "investing", "financing", "cash"],
}

FS_SUBTOTAL_LINES: dict[str, list[str]] = {
    "income_statement": ["gross_profit", "profit_before_tax", "net_profit"],
    "balance_sheet": [
        "total_current_assets",
        "total_non_current_assets",
        "total_assets",
        "total_liabilities",
        "total_liabilities_and_equity",
        "total_current_liabilities",
        "total_non_current_liabilities",
        "total_equity",
    ],
    "cash_flow": [
        "net_cash_from_operating",
        "net_cash_from_investing",
        "net_cash_from_financing",
        "net_change_in_cash",
        "cash_closing",
    ],
}

_FS_PLAIN_LINES: dict[str, list[str]] = {
    "income_statement": [
        "revenue",
        "cost_of_sales",
        "other_income",
        "operating_expenses",
        "finance_costs",
        "income_tax",
    ],
    "balance_sheet": [
        "cash_and_equivalents",
        "trade_receivables",
        "inventories",
        "other_current_assets",
        "property_plant_equipment",
        "other_non_current_assets",
        "trade_payables",
        "borrowings_current",
        "other_current_liabilities",
        "borrowings_non_current",
        "other_non_current_liabilities",
        "share_capital",
        "retained_earnings",
    ],
    "cash_flow": [
        "profit_before_tax",
        "adjustments",
        "working_capital_changes",
        "capex",
        "loan_proceeds",
        "loan_repayments",
        "cash_opening",
    ],
}

FS_LINES: dict[str, list[str]] = {
    t: sorted({*_FS_PLAIN_LINES[t], *FS_SUBTOTAL_LINES[t], "other"}) for t in FS_TABLES
}


def _fs_table(name: str) -> TableSpec:
    return TableSpec(
        columns={
            "line": _f("enum", values=FS_LINES[name]),
            "label": _f("text"),
            "section": _f("enum", values=FS_SECTIONS[name]),
            "current": _f("money"),
            "prior": _opt("money"),
            "is_subtotal": _f("bool"),
        },
        required=True,
    )


_LINES_TABLE = TableSpec(
    columns={
        "description": _f("text"),
        "quantity": _f("decimal"),
        "unit_price": _f("decimal"),
        "gst_amount": _opt("money"),
        "amount": _f("money"),
    },
    required=True,
)

_CURRENCY = "enum"  # ISO 4217 code, free enum (no value list)


def _build() -> dict[str, DocSchema]:
    s: dict[str, DocSchema] = {}

    s["bank_statement"] = DocSchema(
        fields={
            "bank_name": _f("text"),
            "account_holder": _f("text"),
            "uen": _opt("id"),
            "account_number": _f("id"),
            "period_start": _f("date"),
            "period_end": _f("date"),
            "opening_balance": _f("money"),
            "closing_balance": _f("money"),
            "currency": _f(_CURRENCY),
            "page_x_of_y": _f("text"),
            "total_debits": _opt("money"),
            "total_credits": _opt("money"),
        },
        tables={
            "transactions": TableSpec(
                columns={
                    "date": _f("date"),
                    "description": _f("text"),
                    "debit": _f("money"),
                    "credit": _f("money"),
                    "balance": _f("money"),
                },
                row_annotations=["c4", "c5", "counterparty"],
                required=True,
            ),
            "page_chain": TableSpec(
                columns={
                    "page": _f("int"),
                    "page_x": _f("int"),
                    "page_y": _f("int"),
                    "account_number": _f("id"),
                },
                required=True,
            ),
        },
    )

    s["invoice"] = DocSchema(
        fields={
            "invoice_number": _f("id"),
            "invoice_date": _f("date"),
            "due_date": _opt("date"),
            "supplier_name": _f("text"),
            "supplier_uen": _f("id"),
            "supplier_gst_no": _opt("id"),
            "buyer_name": _f("text"),
            "buyer_uen": _opt("id"),
            "currency": _f(_CURRENCY),
            "subtotal": _f("money"),
            "gst_rate": _opt("percent"),
            "gst_amount": _f("money"),
            "total": _f("money"),
            "po_reference": _opt("id"),
            "contract_reference": _opt("id"),
            "payment_status": _opt("enum", ["paid", "unpaid", "partial"]),
            "paid_date": _opt("date"),
        },
        tables={"lines": _LINES_TABLE},
    )

    s["financial_statement"] = DocSchema(
        fields={
            "company_name": _f("text"),
            "uen": _f("id"),
            "period_start": _f("date"),
            "period_end": _f("date"),
            "currency": _f(_CURRENCY),
            "basis": _f("enum", values=["audited", "unaudited", "unknown"]),
            "scale_printed": _f("enum", values=["1", "1000", "1000000"]),
            "auditor": _opt("text"),
        },
        tables={t: _fs_table(t) for t in FS_TABLES},
    )

    s["contract"] = DocSchema(
        fields={
            "contract_number": _opt("id"),
            "title": _f("text"),
            "contract_date": _f("date"),
            "effective_date": _opt("date"),
            "end_date": _opt("date"),
            "seller_name": _f("text"),
            "seller_uen": _opt("id"),
            "buyer_name": _f("text"),
            "buyer_uen": _opt("id"),
            "contract_value": _f("money"),
            "currency": _f(_CURRENCY),
            "payment_terms": _opt("text"),
            "signatory_seller": _opt("text"),
            "signatory_buyer": _opt("text"),
        },
        tables={
            "milestones": TableSpec(
                columns={
                    "description": _f("text"),
                    "due_date": _f("date"),
                    "amount": _f("money"),
                },
                required=False,
            )
        },
    )

    s["purchase_order"] = DocSchema(
        fields={
            "po_number": _f("id"),
            "po_date": _f("date"),
            "buyer_name": _f("text"),
            "buyer_uen": _opt("id"),
            "supplier_name": _f("text"),
            "supplier_uen": _opt("id"),
            "currency": _f(_CURRENCY),
            "subtotal": _f("money"),
            "gst_amount": _opt("money"),
            "total": _f("money"),
            "delivery_date": _opt("date"),
            "contract_reference": _opt("id"),
        },
        tables={"lines": _LINES_TABLE.model_copy(deep=True)},
    )

    s["company_profile"] = DocSchema(
        fields={
            "company_name": _f("text"),
            "uen": _f("id"),
            "entity_type": _f("text"),
            "status": _f("text"),
            "incorporation_date": _f("date"),
            "registered_address": _opt("text"),
            "principal_activity": _f("text"),
            "ssic_code": _opt("id"),
            "paid_up_capital": _opt("money"),
            "currency": _opt(_CURRENCY),
            "retrieved_date": _opt("date"),
        },
        tables={
            "directors": TableSpec(
                columns={
                    "director_name": _f("text"),
                    "id_number": _opt("id"),
                    "nationality": _opt("text"),
                    "position": _opt("text"),
                    "appointment_date": _opt("date"),
                }
            ),
            "shareholders": TableSpec(
                columns={
                    "holder_name": _f("text"),
                    "id_or_uen": _opt("id"),
                    "shares": _f("int"),
                    "percent": _f("percent"),
                }
            ),
        },
    )

    s["certificate_of_incorporation"] = DocSchema(
        fields={
            "company_name": _f("text"),
            "uen": _f("id"),
            "entity_type": _opt("text"),
            "incorporation_date": _f("date"),
            "certificate_number": _opt("id"),
            "issue_date": _opt("date"),
            "issuer": _opt("text"),
        }
    )

    s["ownership_chart"] = DocSchema(
        fields={
            "company_name": _f("text"),
            "uen": _f("id"),
            "chart_date": _opt("date"),
            "ubo_threshold_percent": _opt("percent"),
        },
        tables={
            "holders": TableSpec(
                columns={
                    "holder_name": _f("text"),
                    "holder_type": _f("enum", values=["individual", "company"]),
                    "id_or_uen": _opt("id"),
                    "percent": _f("percent"),
                    "parent_name": _opt("text"),
                    "is_ubo": _opt("bool"),
                },
                required=True,
            )
        },
    )

    s["shareholder_register"] = DocSchema(
        fields={
            "company_name": _f("text"),
            "uen": _f("id"),
            "register_date": _f("date"),
            "total_shares": _f("int"),
            "issued_capital": _opt("money"),
            "currency": _opt(_CURRENCY),
        },
        tables={
            "shareholders": TableSpec(
                columns={
                    "holder_name": _f("text"),
                    "id_or_uen": _opt("id"),
                    "shares": _f("int"),
                    "percent": _f("percent"),
                    "share_class": _opt("text"),
                    "date_acquired": _opt("date"),
                },
                required=True,
            )
        },
    )

    s["source_of_funds_declaration"] = DocSchema(
        fields={
            "declarant_name": _f("text"),
            "company_name": _opt("text"),
            "uen": _opt("id"),
            "declaration_date": _f("date"),
            "declared_amount": _f("money"),
            "currency": _f(_CURRENCY),
            "origin_category": _f(
                "enum",
                values=[
                    "sales_proceeds",
                    "salary",
                    "investment_returns",
                    "inheritance",
                    "loan",
                    "asset_sale",
                    "other",
                ],
            ),
            "origin_description": _f("text"),
            "counterparty_name": _opt("text"),
            "invoice_reference": _opt("id"),
            "period_start": _opt("date"),
            "period_end": _opt("date"),
            "receiving_account": _opt("id"),
        }
    )

    s["national_id"] = DocSchema(
        fields={
            "holder_name": _f("text"),
            "id_number": _f("id"),
            "date_of_birth": _f("date"),
            "sex": _opt("enum", ["M", "F"]),
            "nationality": _opt("text"),
            "date_of_issue": _opt("date"),
            "expiry_date": _opt("date"),
            "address": _opt("text"),
        }
    )

    s["passport"] = DocSchema(
        fields={
            "holder_name": _f("text"),
            "surname": _f("text"),
            "given_names": _f("text"),
            "passport_number": _f("id"),
            "nationality": _f("text"),
            "issuing_country": _f("text"),
            "date_of_birth": _f("date"),
            "sex": _f("enum", values=["M", "F", "X"]),
            "date_of_issue": _opt("date"),
            "date_of_expiry": _f("date"),
            "mrz_line1": _f("text"),
            "mrz_line2": _f("text"),
        }
    )

    s["boilerplate"] = DocSchema(fields={})
    s["other"] = DocSchema(fields={})
    return s


DOC_SCHEMAS: dict[str, DocSchema] = _build()

# Document types that have a schema with something to extract.
EXTRACTABLE_TYPES: tuple[str, ...] = tuple(
    t for t, sch in DOC_SCHEMAS.items() if sch.fields or sch.tables
)


def schema_for(doc_type: str) -> DocSchema:
    return DOC_SCHEMAS[doc_type]


def table_columns(doc_type: str, table: str) -> list[str]:
    return list(DOC_SCHEMAS[doc_type].tables[table].columns)


# --------------------------------------------------------------------------------------
# Model-facing response model (ingest-and-extraction.md 3.5)
# --------------------------------------------------------------------------------------


def _row_model(doc_type: str, table: str, spec: TableSpec) -> type[Strict]:
    defs: dict[str, Any] = {}
    for col in spec.columns:
        if doc_type == "financial_statement":
            if col == "is_subtotal":
                continue  # derived from `line`, never model output
            if col == "line":
                defs[col] = (Literal[tuple(FS_LINES[table])], ...)  # type: ignore[valid-type]
                continue
            if col == "section":
                defs[col] = (Literal[tuple(FS_SECTIONS[table])], ...)  # type: ignore[valid-type]
                continue
        defs[col] = (str | None, ...)
    return create_model(f"Row_{doc_type}_{table}", __config__=ConfigDict(extra="forbid"), **defs)  # type: ignore[call-overload]


@cache
def response_model_for(doc_type: str) -> type[Strict]:
    """Strict Pydantic model of what the LLM returns for one page of ``doc_type``.

    Shape: ``{"fields": {key: str|null}, "tables": {name: [{col: str|null}]}, "page_marker": str|null}``
    All keys required (nullable) so the model must address each; ``page_marker`` only for bank
    statements. Nothing numeric, no dates, no confidence: our code parses the strings.
    """
    schema = DOC_SCHEMAS[doc_type]
    fields_model = create_model(
        f"Fields_{doc_type}",
        __config__=ConfigDict(extra="forbid"),
        **{k: (str | None, ...) for k in schema.fields},  # type: ignore[call-overload]
    )
    tables_defs: dict[str, Any] = {
        name: (list[_row_model(doc_type, name, spec)], ...)  # type: ignore[misc]
        for name, spec in schema.tables.items()
    }
    tables_model = create_model(
        f"Tables_{doc_type}",
        __config__=ConfigDict(extra="forbid"),
        **tables_defs,  # type: ignore[call-overload]
    )
    top: dict[str, Any] = {"fields": (fields_model, ...), "tables": (tables_model, ...)}
    if doc_type == "bank_statement":
        top["page_marker"] = (str | None, ...)
    return create_model(
        f"Page_{doc_type}",
        __config__=ConfigDict(extra="forbid"),
        **top,  # type: ignore[call-overload]
    )  # type: ignore[return-value]


make_page_model = response_model_for  # name used by ingest-and-extraction.md 3.5


def response_json_schema(doc_type: str) -> dict[str, Any]:
    """JSON schema (``LLMRequest.json_schema``) of ``response_model_for(doc_type)``."""
    return response_model_for(doc_type).model_json_schema()
