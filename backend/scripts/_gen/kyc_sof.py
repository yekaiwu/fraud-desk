"""Source-of-funds declaration renderer (data-model 6.10)."""

from __future__ import annotations

from typing import Any

from .kyc_draw import GREY, LEFT, PRODUCER_PORTAL, RIGHT, finish, wrap
from .kyc_view import view_of
from .render_common import (
    DocCanvas,
    DocTruth,
    PageLabel,
    RenderedDoc,
    fmt_amount,
    fmt_date,
    money_str,
    union_box,
)
from .world import World

CATEGORY_RAW = {
    "sales_proceeds": "Sales proceeds",
    "salary": "Salary",
    "investment_returns": "Investment returns",
    "inheritance": "Inheritance",
    "loan": "Loan",
    "asset_sale": "Sale of assets",
    "other": "Other",
}


def render_sof(w: World, overrides: dict[str, Any] | None = None) -> RenderedDoc:
    key = "source_of_funds_declaration:-"
    truth = DocTruth(key=key, doc_type="source_of_funds_declaration")
    s = view_of(w).sof
    if s is None:
        raise ValueError("world has no source-of-funds declaration")
    f = truth.set_field
    c = w.company
    cv = DocCanvas()
    ds = "dmy_mon"
    cv.text(LEFT, 42, "SOURCE OF FUNDS DECLARATION", size=14, font="Helvetica-Bold")
    cv.text(LEFT, 62, "Customer declaration - mock layout, not an official form", size=8.5,
            color=(0.3, 0.3, 0.3))  # fmt: skip
    y = 96.0

    def row(label: str, name: str, value: str | None, raw: str | None, size: float = 9.5) -> None:
        nonlocal y
        cv.text(LEFT, y, label, size=8.5, color=GREY)
        box = cv.text(210, y, raw, size=size) if raw else None
        f(name, value, raw, 1, box)
        y += 18

    row("Declarant", "declarant_name", s.declarant, s.declarant)
    row("Company", "company_name", c.party.name, c.party.name)
    row("UEN", "uen", c.party.uen, c.party.uen)
    row(
        "Date of declaration", "declaration_date", s.declaration_date.isoformat(),
        fmt_date(s.declaration_date, ds),
    )  # fmt: skip
    y += 8
    cv.line(LEFT, y, RIGHT, y, 0.6)
    y += 12
    amt_raw = f"S$ {fmt_amount(s.declared_amount)}"
    row("Amount declared", "declared_amount", money_str(s.declared_amount), amt_raw, 12)
    f("currency", s.currency, s.currency, 1, None)
    cat_raw = CATEGORY_RAW[s.origin_category]
    row("Origin of funds", "origin_category", s.origin_category, cat_raw)
    cv.text(LEFT, y, "Description", size=8.5, color=GREY)
    boxes = []
    for ln in wrap(s.origin_description, RIGHT - 210):
        boxes.append(cv.text(210, y, ln, size=9.5))
        y += 14
    f("origin_description", s.origin_description, s.origin_description, 1, union_box(boxes))
    y += 4
    row("Received from", "counterparty_name", s.counterparty, s.counterparty)
    row("Invoice reference", "invoice_reference", s.invoice_reference, s.invoice_reference)
    row(
        "Funds received from", "period_start", s.period_start.isoformat() if s.period_start else None,
        fmt_date(s.period_start, ds) if s.period_start else None,
    )  # fmt: skip
    row(
        "Funds received to", "period_end", s.period_end.isoformat() if s.period_end else None,
        fmt_date(s.period_end, ds) if s.period_end else None,
    )  # fmt: skip
    cv.text(LEFT, y, "Receiving account", size=8.5, color=GREY)
    if s.receiving_account:
        cv.text(210, y, c.bank_name or "Company account", size=9.5)
        abox = cv.text(210 + 150, y, s.receiving_account, size=9.5)
        f("receiving_account", s.receiving_account, s.receiving_account, 1, abox)
    else:
        f("receiving_account", None, None, 1, None)
    y += 40
    cv.text(LEFT, y, "I declare that the information above is true and complete.", size=9)
    cv.line(LEFT, y + 62, LEFT + 190, y + 62, 0.6)
    cv.text(LEFT, y + 68, s.declarant, size=8.5)
    cv.text(LEFT, y + 80, "Authorised signatory (typed, specimen)", size=7.5, color=GREY)
    cv.footer("Page 1 of 1")
    pdf = finish(
        cv,
        producer=PRODUCER_PORTAL,
        title="Source of Funds Declaration",
        doc_date=s.declaration_date,
        overrides=overrides,
    )
    return RenderedDoc(
        key=key,
        doc_type="source_of_funds_declaration",
        stem="source_of_funds_declaration",
        pdf=pdf,
        page_labels=[PageLabel("source_of_funds_declaration", key)],
        truth=truth,
        doc_date=s.declaration_date,
        page_start=1,
        page_end=1,
    )
