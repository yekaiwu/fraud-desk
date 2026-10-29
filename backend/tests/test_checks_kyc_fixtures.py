"""Hand-built documents and registry shared by the KYC/AML test modules (no tests in here).

All data is fictitious. NRIC check letters are computed by ``nric_check_letter`` and pinned by the
vectors in test_checks_kyc_ids.py. Documents are built with ``make_doc``; nothing depends on the
extraction stage.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.checks.kyc import mrz_check_digit
from app.core.models import Annotation, Applicant, Cell, Document, Region
from app.core.registry import MockRegistry
from app.core.testing import FIXED_TODAY, make_ctx, make_doc

UEN = "201912345R"
APPLICANT_NAME = "Example Logistics Pte. Ltd."
APPLICANT = Applicant(name=APPLICANT_NAME, uen=UEN, source="upload")

TAN_ID = "S1234567D"
LIM_ID = "S8222222G"
RAVI_ID = "T0812345J"

SG_LINE1 = "P<SGPTAN<<WEI<MING<<<<<<<<<<<<<<<<<<<<<<<<<<"
SG_LINE2 = "K1234567D9SGP8503127M3106154<<<<<<<<<<<<<<<8"
ICAO_LINE1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
ICAO_LINE2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"

__all__ = [
    "APPLICANT",
    "FIXED_TODAY",
    "ICAO_LINE1",
    "ICAO_LINE2",
    "LIM_ID",
    "RAVI_ID",
    "SG_LINE1",
    "SG_LINE2",
    "TAN_ID",
    "UEN",
    "ann",
    "box",
    "chart_doc",
    "clean_kyc_documents",
    "company_profile_doc",
    "ctx_for",
    "invoice_doc",
    "mrz_line1",
    "mrz_line2",
    "national_id_doc",
    "passport_doc",
    "register_doc",
    "set_row_cell",
    "registry_dict",
    "registry_with",
    "set_cell",
    "sof_doc",
    "statement_doc",
]


def box(x: float = 0.1, y: float = 0.1, w: float = 0.2, h: float = 0.03, page: int = 1) -> Region:
    return Region(file_id="f1", page=page, x=x, y=y, w=w, h=h)


def ann(label: str, prob: float = 0.9, name: str = "c4") -> Annotation:
    return Annotation(label=label, probability=prob, classifier="test", version="1")


def set_cell(doc: Document, field: str, value: str | None, **kw: Any) -> None:
    """Replace a header field (keeps its kind)."""
    old = doc.fields[field]
    doc.fields[field] = Cell(value=value, kind=old.kind, **kw)


def set_row_cell(
    doc: Document, table: str, row: int, col: str, value: str | None, **kw: Any
) -> None:
    old = doc.tables[table].rows[row].cells[col]
    doc.tables[table].rows[row].cells[col] = Cell(value=value, kind=old.kind, **kw)


def registry_dict(
    *,
    uen: str = UEN,
    officers: list[dict] | None = None,
    shareholders: list[dict] | None = None,
    extra_entities: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "1",
        "generated_by": "tests",
        "as_of": "2026-09-30",
        "entities": [
            {
                "uen": uen,
                "entity_name": APPLICANT_NAME,
                "entity_type": "Private Company Limited by Shares",
                "status": "Live",
                "incorporation_date": "2019-04-12",
                "principal_activity": "Freight forwarding and logistics",
                "paid_up_capital": "500000.00",
                "currency": "SGD",
                "officers": officers
                if officers is not None
                else [
                    {"name": "Tan Wei Ming", "id_number": TAN_ID, "position": "Director"},
                    {"name": "Ravi Kumar", "id_number": RAVI_ID, "position": "Director"},
                    {"name": "Mary Goh", "id_number": None, "position": "Secretary"},
                ],
                "shareholders": shareholders
                if shareholders is not None
                else [
                    {"name": "Tan Wei Ming", "id_or_uen": TAN_ID, "holder_type": "individual",
                     "shares": 300000, "percent": "60.00"},
                    {"name": "Lim Siew Hoon", "id_or_uen": LIM_ID, "holder_type": "individual",
                     "shares": 150000, "percent": "30.00"},
                    {"name": "Ravi Kumar", "id_or_uen": RAVI_ID, "holder_type": "individual",
                     "shares": 50000, "percent": "10.00"},
                ],
            },
            *(extra_entities or []),
        ],
    }  # fmt: skip


def registry_with(**kw: Any) -> MockRegistry:
    return MockRegistry.model_validate(registry_dict(**kw))


def register_doc(
    doc_id: str = "d2",
    rows: Sequence[tuple[str, str | None, str | None, str | None]] | None = None,
    *,
    total_shares: str | None = "500000",
) -> Document:
    """rows: (holder_name, id, shares, percent)."""
    rows = rows or [
        ("Tan Wei Ming", TAN_ID, "300000", "60.00"),
        ("Lim Siew Hoon", LIM_ID, "150000", "30.00"),
        ("Ravi Kumar", RAVI_ID, "50000", "10.00"),
    ]
    return make_doc(
        "shareholder_register",
        id=doc_id,
        fields={"company_name": APPLICANT_NAME, "uen": UEN, "register_date": "2026-09-01",
                "total_shares": total_shares},
        tables={"shareholders": [
            {"holder_name": n, "id_or_uen": i, "shares": s, "percent": p} for n, i, s, p in rows
        ]},
    )  # fmt: skip


def chart_doc(
    doc_id: str = "d3",
    rows: Sequence[tuple[str, str, str | None, str | None, str | None, str | None]] | None = None,
    *,
    threshold: str | None = None,
) -> Document:
    """rows: (holder_name, holder_type, id, percent, parent_name, is_ubo)."""
    rows = rows or [
        ("Tan Wei Ming", "individual", TAN_ID, "60.00", None, "true"),
        ("Lim Siew Hoon", "individual", LIM_ID, "30.00", None, "true"),
        ("Ravi Kumar", "individual", RAVI_ID, "10.00", None, "false"),
    ]
    return make_doc(
        "ownership_chart",
        id=doc_id,
        fields={"company_name": APPLICANT_NAME, "uen": UEN, "ubo_threshold_percent": threshold},
        tables={"holders": [
            {"holder_name": n, "holder_type": t, "id_or_uen": i, "percent": p,
             "parent_name": parent, "is_ubo": u} for n, t, i, p, parent, u in rows
        ]},
    )  # fmt: skip


def company_profile_doc(
    doc_id: str = "d1",
    *,
    activity: str | None = "Freight forwarding and logistics",
    directors: Sequence[tuple[str, str | None]] | None = None,
    shareholders: Sequence[tuple[str, str | None, str | None]] | None = None,
) -> Document:
    directors = (
        directors if directors is not None else [("Tan Wei Ming", TAN_ID), ("Ravi Kumar", RAVI_ID)]
    )
    shareholders = shareholders if shareholders is not None else [
        ("Tan Wei Ming", TAN_ID, "60.00"),
        ("Lim Siew Hoon", LIM_ID, "30.00"),
        ("Ravi Kumar", RAVI_ID, "10.00"),
    ]  # fmt: skip
    return make_doc(
        "company_profile",
        id=doc_id,
        fields={"company_name": APPLICANT_NAME, "uen": UEN, "status": "Live",
                "principal_activity": activity},
        tables={
            "directors": [{"director_name": n, "id_number": i} for n, i in directors],
            "shareholders": [
                {"holder_name": n, "id_or_uen": i, "shares": None, "percent": p}
                for n, i, p in shareholders
            ],
        },
    )  # fmt: skip


def national_id_doc(
    doc_id: str,
    name: str,
    number: str,
    dob: str | None,
    *,
    issue: str | None = "2015-01-01",
    expiry: str | None = "2030-01-01",
) -> Document:
    return make_doc(
        "national_id",
        id=doc_id,
        fields={"holder_name": name, "id_number": number, "date_of_birth": dob,
                "date_of_issue": issue, "expiry_date": expiry},
    )  # fmt: skip


def mrz_line1(surname: str, given: str, country: str = "SGP") -> str:
    """A TD3 line 1 padded to 44 characters."""
    return f"P<{country}{surname.upper()}<<{given.upper().replace(' ', '<')}".ljust(44, "<")


def mrz_line2(
    number: str = "K1234567D",
    dob: str = "850312",
    expiry: str = "310615",
    nationality: str = "SGP",
    sex: str = "M",
    personal: str = "<" * 14,
) -> str:
    """A valid TD3 line 2 (all check digits computed)."""
    n = number.ljust(9, "<")
    body = (
        n
        + str(mrz_check_digit(n))
        + nationality
        + dob
        + str(mrz_check_digit(dob))
        + sex
        + expiry
        + str(mrz_check_digit(expiry))
        + personal
    )
    pd = "<" if set(personal) == {"<"} else str(mrz_check_digit(personal))
    body += pd
    composite = body[0:10] + body[13:20] + body[21:43]
    return body + str(mrz_check_digit(composite))


def passport_doc(
    doc_id: str = "d6",
    *,
    line1: str | None = SG_LINE1,
    line2: str | None = SG_LINE2,
    **overrides: str | None,
) -> Document:
    fields: dict[str, str | None] = {
        "holder_name": "Tan Wei Ming",
        "surname": "Tan",
        "given_names": "Wei Ming",
        "passport_number": "K1234567D",
        "nationality": "SGP",
        "issuing_country": "SGP",
        "date_of_birth": "1985-03-12",
        "sex": "M",
        "date_of_issue": "2021-06-16",
        "date_of_expiry": "2031-06-15",
        "mrz_line1": line1,
        "mrz_line2": line2,
    }
    fields.update(overrides)
    return make_doc("passport", id=doc_id, fields=fields)


def statement_doc(
    doc_id: str = "d7",
    rows: Sequence[dict[str, Any]] = (),
    *,
    currency: str = "SGD",
    account: str = "123-456-789",
    start: str = "2026-01-01",
    end: str = "2026-06-30",
) -> Document:
    """rows: dicts with date, desc, debit/credit, optional cp (counterparty annotation label),
    c4, c5 (labels) and p (probability, default 0.9)."""
    table_rows = [
        {
            "date": r["date"],
            "description": r.get("desc", "FAST PAYMENT FROM " + str(r.get("cp", "X")).upper()),
            "debit": r.get("debit"),
            "credit": r.get("credit"),
            "balance": None,
        }
        for r in rows
    ]
    doc = make_doc(
        "bank_statement",
        id=doc_id,
        fields={"bank_name": "DBS", "account_holder": APPLICANT_NAME, "uen": UEN,
                "account_number": account, "period_start": start, "period_end": end,
                "currency": currency},
        tables={"transactions": table_rows},
    )  # fmt: skip
    for row, spec in zip(doc.tables["transactions"].rows, rows, strict=True):
        p = spec.get("p", 0.9)
        if spec.get("cp"):
            row.annotations["counterparty"] = Annotation(
                label=spec["cp"], probability=1.0, classifier="counterparty-parser", version="1"
            )
        if spec.get("c4"):
            row.annotations["c4"] = ann(spec["c4"], p)
        if spec.get("c5"):
            row.annotations["c5"] = ann(spec["c5"], p)
    return doc


def invoice_doc(
    doc_id: str = "d4",
    *,
    number: str = "INV-0042",
    buyer: str = "Alpha Trading Pte Ltd",
    buyer_uen: str | None = None,
    total: str = "12500.00",
    date_: str = "2026-02-01",
    due: str | None = "2026-03-01",
    paid: str | None = None,
    supplier: str = APPLICANT_NAME,
    supplier_uen: str | None = UEN,
) -> Document:
    return make_doc(
        "invoice",
        id=doc_id,
        fields={"invoice_number": number, "invoice_date": date_, "due_date": due,
                "supplier_name": supplier, "supplier_uen": supplier_uen, "buyer_name": buyer,
                "buyer_uen": buyer_uen, "currency": "SGD", "total": total, "paid_date": paid},
    )  # fmt: skip


def sof_doc(
    doc_id: str = "d5",
    *,
    amount: str = "1000000.00",
    origin: str = "sales_proceeds",
    counterparty: str | None = None,
    currency: str = "SGD",
    start: str | None = "2026-01-01",
    end: str | None = "2026-06-30",
    declared: str = "2026-07-01",
    account: str | None = None,
) -> Document:
    return make_doc(
        "source_of_funds_declaration",
        id=doc_id,
        fields={"declarant_name": "Tan Wei Ming", "declaration_date": declared,
                "declared_amount": amount, "currency": currency, "origin_category": origin,
                "counterparty_name": counterparty, "period_start": start, "period_end": end,
                "receiving_account": account},
    )  # fmt: skip


def clean_kyc_documents() -> list[Document]:
    """A KYC bundle that agrees with ``registry_with()`` everywhere (all 8 KYC rules should run)."""
    statement = statement_doc(
        "d7",
        [
            {"date": "2026-02-02", "credit": "20000.00", "cp": "ACME TRADING PTE LTD",
             "c4": "trade_receipt", "c5": "trading"},
            {"date": "2026-02-10", "debit": "8000.00", "cp": "SEA FREIGHT PTE LTD", "c5": "logistics"},
            {"date": "2026-03-02", "credit": "15000.00", "cp": "GLOBAL CARGO PTE LTD",
             "c4": "customer_payment", "c5": "logistics"},
        ],
    )  # fmt: skip
    return [
        company_profile_doc("d1"),
        register_doc("d2"),
        chart_doc("d3"),
        national_id_doc("d4", "Tan Wei Ming", TAN_ID, "1985-03-12"),
        national_id_doc("d5", "Ravi Kumar", RAVI_ID, "2004-02-02", issue="2022-05-01"),
        passport_doc("d6"),
        statement,
    ]


def ctx_for(
    documents: Sequence[Document],
    *,
    use_case: str = "kyc",
    registry: MockRegistry | bool | None = True,
    **kw: Any,
):  # noqa: ANN201
    return make_ctx(
        documents,
        use_case=use_case,  # type: ignore[arg-type]
        registry=registry_with() if registry is True else (registry or None),
        applicant=kw.pop("applicant", APPLICANT),
        **kw,
    )
