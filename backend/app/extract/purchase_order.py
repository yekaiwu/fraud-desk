"""Purchase order text-layer extractor (labelled forms; ingest-and-extraction.md 6.8)."""

from __future__ import annotations

import re

from app.core.context import CaseContext
from app.core.models import Cell, Document, Table
from app.extract.base import ExtractionResult, LayoutNotRecognised, Validation
from app.extract.invoice import (
    LINE_COLDEFS,  # noqa: F401  (re-exported for extract-kyc style reuse)
    parse_lines_table,
    parse_totals,
    uen_cells,
    validate_lines_and_totals,
)
from app.extract.registry import (
    register_extractor,
    register_prompt_hints,
    register_validator,
)
from app.extract.text_layer import DocView, TextLayerExtractor

DOC_TYPE = "purchase_order"

LABELS = {
    "po_number": [r"P\.?O\.?\s*(?:No\.?|Number)", r"Purchase\s+Order\s+(?:No\.?|Number)"],
    "po_date": [r"Order\s+Date", r"PO\s+Date", r"^Date"],
    "delivery_date": [r"Deliver(?:y)?\s+Date", r"Deliver(?:y)?\s+by", r"Required\s+By"],
    "buyer_name": [r"^Buyer(?!\s+UEN)", r"^Bill\s+To", r"^Customer(?!\s+UEN)"],
    "supplier_name": [r"^Supplier(?!\s+UEN)", r"^Vendor(?!\s+UEN)"],
    "buyer_uen": [r"^Buyer\s+UEN", r"^Customer\s+UEN"],
    "supplier_uen": [r"^Supplier\s+UEN", r"^Vendor\s+UEN"],
    "contract_reference": [r"Contract\s+(?:No\.?|Number|Ref(?:erence)?)"],
    "currency": [r"Currency"],
}
SHIP_TO = [r"Ship\s+To", r"Deliver\s+To"]


def _labels() -> list[str]:
    from app.extract.invoice import BILL_TO, SUBTOTAL, TOTAL, UEN

    return (
        [p for pats in LABELS.values() for p in pats] + SHIP_TO + UEN + BILL_TO + SUBTOTAL + TOTAL
    )


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class PurchaseOrderTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        from app.extract.invoice import currency_cell

        view.set_known_labels(_labels())
        parsed, after = parse_lines_table(view, DOC_TYPE, tables)
        if not parsed.header_pages:
            raise LayoutNotRecognised("no purchase order line table header")
        if not parsed.rows:
            raise LayoutNotRecognised("purchase order line table has no rows")
        parse_totals(view, fields, after, rate_key=None)
        if fields["total"].value is None:
            raise LayoutNotRecognised("no Total line below the purchase order lines")
        for key in ("po_number", "po_date", "delivery_date", "contract_reference"):
            view.field(fields, key, LABELS[key])
        for party in ("buyer", "supplier"):
            name_key, uen_key = f"{party}_name", f"{party}_uen"
            hit = view.first(LABELS[name_key])
            if hit is not None:
                fields[name_key] = view.cell(hit.value_words, "text", f"fields.{name_key}")
            if view.field(fields, uen_key, LABELS[uen_key]) is None and hit is not None:
                near = view.uen_near(hit)
                if near is not None:
                    fields[uen_key] = view.cell(near.value_words[:1], "id", f"fields.{uen_key}")
        self._unlabelled_parties(view, fields)
        currency_cell(view, fields, parsed.currency_raw)

    @staticmethod
    def _unlabelled_parties(view: DocView, fields: dict[str, Cell]) -> None:
        """The buyer is the company named in the letterhead; UENs above the 'Supplier' label are the
        buyer's and the ones below are the supplier's."""
        if fields["buyer_name"].value is None and view.first_page.lines:
            for seg in view.first_page.lines[0].segs:
                if not re.search(r"purchase\s+order|^\W*$", seg.text, re.I):
                    fields["buyer_name"] = view.cell(seg.words, "text", "fields.buyer_name")
                    break
        pending = {k: fields[k] for k in ("buyer_uen", "supplier_uen") if fields[k].value is None}
        if pending:
            uen_cells(view, pending, "buyer_uen", "supplier_uen", divider=LABELS["supplier_name"])
            for key, cell in pending.items():
                fields[key] = cell


@register_validator(doc_types={DOC_TYPE})
def validate_purchase_order(
    result: ExtractionResult, ctx: CaseContext, doc: Document
) -> Validation:
    return validate_lines_and_totals(result, ctx, doc)


register_prompt_hints(
    DOC_TYPE,
    "This is a purchase order. `buyer_*` is the party placing the order and `supplier_*` the party "
    "that will deliver. Return one `lines` object per printed line item. `delivery_date` is the "
    "printed delivery or required-by date; `contract_reference` a printed contract number, if any.",
)
