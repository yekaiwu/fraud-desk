"""Receipts: a point-of-sale slip read by the generic extractor (no text-layer parser).

A receipt has the same lines table as an invoice (description, quantity, unit price, amount) and a
subtotal, a tax line and a total, so the arithmetic validator is the invoice one with the receipt's
field names. What it does not have is an invoice number, a UEN or a GST number: those are not in
the schema, so no invoice rule can ask for them. Docs: ingest-and-extraction.md "Receipts".
"""

from __future__ import annotations

from app.core.context import CaseContext
from app.core.models import Document
from app.extract.base import ExtractionResult, Validation
from app.extract.invoice import validate_lines_and_totals
from app.extract.registry import register_prompt_hints, register_validator

DOC_TYPE = "receipt"
KEYS = ("subtotal", "tax_amount", "total")


@register_validator(doc_types={DOC_TYPE})
def validate_receipt(result: ExtractionResult, ctx: CaseContext, doc: Document) -> Validation:
    return validate_lines_and_totals(result, ctx, doc, keys=KEYS)


register_prompt_hints(
    DOC_TYPE,
    "This is a point-of-sale receipt (a shop, restaurant or fuel slip). `merchant_name` is the "
    "business that issued it (usually the first printed line); `receipt_number` a printed "
    "transaction, check or receipt number; `receipt_date` the printed date. Return EVERY printed "
    "item row as one `lines` object (description, quantity, unit price, line amount): also "
    "modifiers and add-ons that carry their own price, and discounts, coupons and savings as rows "
    "with a negative amount (keep the printed minus sign); never leave a priced row out. A line "
    "that prints only a price has `quantity` null. `subtotal` is the printed sum before tax, "
    "`tax_rate` the percentage printed beside the tax label (null if none), `tax_amount` the "
    "printed tax, `total` the final amount charged. Tips, tender, change and card lines are not "
    "item rows.",
)
