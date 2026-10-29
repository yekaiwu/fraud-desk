"""Invoice text-layer extractor, validator and post-processing (ingest-and-extraction.md 6.8).

GST on the net total and GST per line are both accepted: a printed GST column fills
``lines[*].gst_amount``, otherwise it stays empty. Rows are never recomputed. The machinery for the
lines table and the totals block is shared with ``purchase_order.py``.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.core.context import CaseContext
from app.core.models import Cell, Document, Table, Word
from app.core.money import approx_eq
from app.extract.base import (
    ExtractionResult,
    LayoutNotRecognised,
    Validation,
    missing_required,
    unverified_failures,
)
from app.extract.registry import (
    register_extractor,
    register_postprocessor,
    register_prompt_hints,
    register_validator,
)
from app.extract.text_layer import (
    ColDef,
    DocView,
    Line,
    ParsedTable,
    TextLayerExtractor,
    blank_row,
    is_amount_text,
    parse_table,
    row_box,
    words_text,
)

DOC_TYPE = "invoice"

LINE_COLDEFS = (
    ColDef("description", r"description|item|details|particulars"),
    ColDef("quantity", r"qty|quantity", align="right"),
    ColDef("unit_price", r"unit\s+price|rate|price", align="right"),
    ColDef("gst_amount", r"gst|vat|tax", align="right"),
    ColDef("amount", r"amount|line\s+total|total", align="right"),
)

UEN = [r"UEN", r"Co\.?\s*Reg(?:\.|istration)?\s*No\.?"]
BILL_TO = [r"^Bill\s+To", r"^Customer", r"^Buyer", r"^Sold\s+To"]
LABELS = {
    "invoice_number": [r"Tax\s+Invoice\s+No\.?", r"Invoice\s+(?:No\.?|Number|#)"],
    "invoice_date": [r"Invoice\s+Date", r"^Date"],
    "due_date": [r"Due\s+Date", r"Payment\s+Due"],
    "supplier_gst_no": [r"GST\s+(?:Reg(?:istration)?\.?\s+)?(?:No\.?|Number)"],
    "supplier_name": [r"^From", r"^Supplier", r"^Vendor"],
    "po_reference": [r"P\.?O\.?\s*(?:No\.?|Number|Ref(?:erence)?)"],
    "contract_reference": [r"Contract\s+(?:No\.?|Number|Ref(?:erence)?)"],
    "paid_date": [r"Paid\s+(?:on|Date)"],
    "payment_status": [r"Payment\s+Status", r"Status"],
    "currency": [r"Currency"],
}
SUBTOTAL = [r"^Sub-?total"]
GST = [r"^(?:GST|VAT)\b(?!\s+(?:Reg|No))(?:\s*[(@]?\s*\d+(?:\.\d+)?\s*%\s*\)?)?"]
TOTAL = [
    r"^(?:Grand\s+|Order\s+)?Total(?:\s+Amount)?(?:\s+(?:Payable|Due))?(?!\s+GST)",
    r"^Amount\s+Due",
]
STATUS_STAMP = re.compile(
    r"^(PAID|UNPAID|PARTIALLY\s+PAID|PART\s+PAID)\b(?:\s+(?:on\s+|date\s*:?\s*)?(.+))?$", re.I
)

_STOP = re.compile(
    r"^(?:sub-?total|gst\b|vat\b|(?:order\s+)?total\b|grand\s+total|amount\s+due|balance\s+due)",
    re.I,
)
_FOOTER = re.compile(r"specimen|fictitious|page\s+\d+\s*(?:of|/)\s*\d+", re.I)
_RATE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def all_labels() -> list[str]:
    out = [p for pats in LABELS.values() for p in pats]
    return out + UEN + BILL_TO + SUBTOTAL + TOTAL


def _accept(keys: set[str]) -> bool:
    return {"description", "amount"} <= keys and "quantity" in keys


def _row_start(cols: dict[str, list[Word]]) -> bool:
    amt = cols.get("amount")
    if amt and is_amount_text(words_text(amt[:1])):
        return True
    return bool(cols.get("quantity"))


def _stop(line: Line, cols: dict[str, list[Word]]) -> bool:
    if not _STOP.match(line.text):
        return False
    return len(line.words) <= 8


def parse_lines_table(
    view: DocView, doc_type: str, tables: dict[str, Table]
) -> tuple[ParsedTable, list[Line]]:
    """Fill ``tables['lines']``; returns the parse and the lines at/after the totals block."""
    parsed = parse_table(
        view,
        LINE_COLDEFS,
        accept=_accept,
        is_row_start=_row_start,
        stop=_stop,
        footer=_FOOTER,
    )
    table = tables["lines"]
    row_pages: list[int] = []
    for i, pr in enumerate(parsed.rows):
        row = blank_row(doc_type, "lines", i)
        where = f"lines[{i}]"
        row.cells["description"] = view.cell(
            pr.cols.get("description", []), "text", f"{where}.description"
        )
        row.cells["quantity"] = view.cell(
            pr.cols.get("quantity", []), "decimal", f"{where}.quantity"
        )
        row.cells["unit_price"] = view.cell(
            pr.cols.get("unit_price", []), "decimal", f"{where}.unit_price"
        )
        row.cells["gst_amount"] = view.cell(
            pr.cols.get("gst_amount", []), "money", f"{where}.gst_amount"
        )
        row.cells["amount"] = view.cell(pr.cols.get("amount", []), "money", f"{where}.amount")
        row.box = row_box(row.cells.values())
        table.rows.append(row)
        row_pages.append(pr.page)
    table.pages = sorted({pr.page for pr in parsed.rows})
    view.row_pages["lines"] = row_pages
    if parsed.stop_at is not None:
        after = [ln for ln in view.all_lines if (ln.page, ln.index) >= parsed.stop_at]
    elif parsed.rows:
        last = max((pr.lines[-1] for pr in parsed.rows), key=lambda ln: (ln.page, ln.index))
        after = [ln for ln in view.all_lines if (ln.page, ln.index) > (last.page, last.index)]
    else:
        after = []
    return parsed, after


def parse_totals(
    view: DocView,
    fields: dict[str, Cell],
    after: list[Line],
    *,
    keys: tuple[str, str, str] = ("subtotal", "gst_amount", "total"),
    rate_key: str | None = "gst_rate",
) -> None:
    sub_key, gst_key, total_key = keys
    sub = [h for h in view.find_labelled(SUBTOTAL, lines=after) if h.value_words]
    if sub:
        fields[sub_key] = view.cell(_last_amount(sub[0].value_words), "money", f"fields.{sub_key}")
    gst = [h for h in view.find_labelled(GST, lines=after) if h.value_words]
    if gst:
        hit = gst[0]
        fields[gst_key] = view.cell(_last_amount(hit.value_words), "money", f"fields.{gst_key}")
        if rate_key is not None:
            m = _RATE.search(hit.line.text)
            if m:
                words = [w for w in hit.line.words if "%" in w.text or m.group(1) in w.text][:2]
                words = [
                    w for w in words if _RATE.search(w.text) or w.text.strip("()@ ") == m.group(1)
                ]
                fields[rate_key] = view.cell(
                    words or hit.label_words,
                    "percent",
                    f"fields.{rate_key}",
                    raw_override=m.group(0),
                )
    tot = [h for h in view.find_labelled(TOTAL, lines=after) if h.value_words]
    if tot:
        fields[total_key] = view.cell(
            _last_amount(tot[-1].value_words), "money", f"fields.{total_key}"
        )


def _last_amount(words: list[Word]) -> list[Word]:
    """The rightmost amount-looking token (with a CR/DR-less currency prefix kept as printed)."""
    amounts = [w for w in words if is_amount_text(w.text)]
    if not amounts:
        return words
    last = amounts[-1]
    idx = words.index(last)
    if idx > 0 and re.fullmatch(r"(?:S\$|US\$|SGD|USD|\$)", words[idx - 1].text.strip(), re.I):
        return [words[idx - 1], last]
    return [last]


def uen_cells(
    view: DocView,
    fields: dict[str, Cell],
    above_key: str,
    below_key: str,
    divider: list[str] | None = None,
) -> None:
    """Assign UENs by position relative to a divider label: the first one above it goes to
    ``above_key``, the first one at or below it to ``below_key``. Invoices: 'Bill To' divides
    supplier (above) from buyer (below). Purchase orders: 'Supplier' divides buyer from supplier."""
    hits = [h for h in view.find_labelled(UEN) if h.value_words]
    if not hits:
        return
    bill = view.first(divider if divider is not None else BILL_TO)
    above = below = None
    if bill is not None:
        pos = (bill.line.page, bill.line.index)
        before = [h for h in hits if (h.line.page, h.line.index) < pos]
        after = [h for h in hits if (h.line.page, h.line.index) >= pos]
        above = before[0] if before else None
        below = after[0] if after else None
    else:
        above = hits[0]
        below = hits[1] if len(hits) > 1 else None
    if above is not None and above_key in fields:
        fields[above_key] = view.cell(above.value_words[:1], "id", f"fields.{above_key}")
    if below is not None and below_key in fields:
        fields[below_key] = view.cell(below.value_words[:1], "id", f"fields.{below_key}")


def party_name(view: DocView, fields: dict[str, Cell], key: str, patterns: list[str]) -> None:
    hit = view.first(patterns)
    if hit is not None:
        # the first segment only: a name followed by a UEN on the same line stays separate
        fields[key] = view.cell(hit.value_words, "text", f"fields.{key}")


def currency_cell(view: DocView, fields: dict[str, Cell], header_currency: str | None) -> None:
    if view.field(fields, "currency", LABELS["currency"]) is not None:
        return
    for ln in view.all_lines:
        for w in ln.words:
            t = w.text.strip("() ")
            if re.fullmatch(r"S\$|SGD|USD|US\$", t, re.I):
                fields["currency"] = view.cell([w], "enum", "fields.currency")
                return
            m = re.match(r"^(S\$|SGD|US\$|USD)\d", t, re.I)
            if m:
                fields["currency"] = view.cell(
                    [w], "enum", "fields.currency", raw_override=m.group(1)
                )
                return
    view.warn("currency_not_printed")


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class InvoiceTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        view.set_known_labels(all_labels())
        parsed, after = parse_lines_table(view, DOC_TYPE, tables)
        if not parsed.header_pages:
            raise LayoutNotRecognised(
                "no invoice line table header (description, quantity, amount)"
            )
        if not parsed.rows:
            raise LayoutNotRecognised("invoice line table header found but no line rows")
        parse_totals(view, fields, after)
        if fields["total"].value is None and fields["subtotal"].value is None:
            raise LayoutNotRecognised("no Subtotal or Total line below the invoice lines")

        for key in (
            "invoice_number",
            "invoice_date",
            "due_date",
            "supplier_gst_no",
            "po_reference",
            "contract_reference",
            "paid_date",
        ):
            view.field(fields, key, LABELS[key])
        party_name(view, fields, "buyer_name", BILL_TO)
        uen_cells(view, fields, "supplier_uen", "buyer_uen")
        self._supplier(view, fields)
        currency_cell(view, fields, parsed.currency_raw)
        self._status(view, fields)

    @staticmethod
    def _supplier(view: DocView, fields: dict[str, Cell]) -> None:
        if view.field(fields, "supplier_name", LABELS["supplier_name"]) is not None:
            return
        first = view.first_page.lines[:1]
        for ln in first:
            for seg in ln.segs:
                if not re.search(r"invoice|tax|^\W*$", seg.text, re.I):
                    fields["supplier_name"] = view.cell(seg.words, "text", "fields.supplier_name")
                    return

    @staticmethod
    def _status(view: DocView, fields: dict[str, Cell]) -> None:
        values = ["paid", "unpaid", "partial"]
        for ln in view.all_lines:
            for seg in ln.segs:
                m = STATUS_STAMP.match(seg.text.strip())
                if m is None:
                    continue
                n_status = len(m.group(1).split())
                fields["payment_status"] = view.cell(
                    seg.words[:n_status], "enum", "fields.payment_status", values=values
                )
                rest = seg.words[n_status:]
                rest = [w for w in rest if w.text.strip().lower() not in ("on", "date", "date:")]
                if rest and fields["paid_date"].value is None and fields["paid_date"].raw is None:
                    fields["paid_date"] = view.cell(rest, "date", "fields.paid_date")
                return
        view.field(fields, "payment_status", LABELS["payment_status"], lines=None)


# --------------------------------------------------------------------------------------
# Validation and post-processing (both backends)
# --------------------------------------------------------------------------------------


def validate_lines_and_totals(
    result: ExtractionResult,
    ctx: CaseContext,
    doc: Document,
    *,
    keys: tuple[str, str, str] = ("subtotal", "gst_amount", "total"),
) -> Validation:
    v = Validation()
    tol: Decimal = ctx.settings.money_tolerance
    table = result.tables["lines"]
    pages = result.row_pages.get("lines") or [doc.page_start] * len(table.rows)
    amounts: list[Decimal] = []
    complete = bool(table.rows)
    for row, page in zip(table.rows, pages, strict=False):
        qty = row.cells["quantity"].money()
        price = row.cells["unit_price"].money()
        amount = row.cells["amount"].money()
        if amount is None:
            complete = False
            continue
        amounts.append(amount)
        if qty is not None and price is not None and not approx_eq(qty * price, amount, tol):
            v.failing.setdefault(page, "total")
    sub_key, gst_key, total_key = keys
    sub = result.fields[sub_key].money()
    gst = result.fields[gst_key].money()
    total = result.fields[total_key].money()
    fpage = result.field_pages.get(total_key) or result.field_pages.get(sub_key) or doc.page_start
    if sub is not None and complete and not approx_eq(sum(amounts, Decimal(0)), sub, tol):
        v.failing.setdefault(fpage, "total")
    if (
        sub is not None
        and total is not None
        and not approx_eq(sub + (gst or Decimal(0)), total, tol)
    ):
        v.failing.setdefault(fpage, "total")
    for page, reason in unverified_failures(result, doc).items():
        v.failing.setdefault(page, reason)
    v.missing_required = missing_required(result, doc.doc_type)
    return v


@register_validator(doc_types={DOC_TYPE})
def validate_invoice(result: ExtractionResult, ctx: CaseContext, doc: Document) -> Validation:
    return validate_lines_and_totals(result, ctx, doc)


@register_postprocessor(doc_types={DOC_TYPE})
def postprocess_invoice(result: ExtractionResult, ctx: CaseContext, doc: Document) -> None:
    """``gst_rate`` defaults to ``9.00`` only when a GST line exists and prints no rate."""
    rate = result.fields["gst_rate"]
    gst = result.fields["gst_amount"]
    if rate.value is None and gst.value is not None:
        result.fields["gst_rate"] = Cell(
            value="9.00", raw=None, kind="percent", source="derived", verified=None
        )
        result.warnings.append("gst_rate_defaulted")


register_prompt_hints(
    DOC_TYPE,
    "This is a tax invoice. `supplier_*` is the party issuing the invoice, `buyer_*` the party "
    "billed. Return one `lines` object per printed line item (description, quantity, unit price, "
    "per-line GST only if a GST column is printed, line amount). `gst_rate` is the percentage "
    "printed beside the GST label, e.g. '9%'; null if none is printed. `payment_status` is the "
    "printed stamp or label (PAID, UNPAID, PARTIALLY PAID) and `paid_date` the date printed beside "
    "'Paid on'; both null if absent.",
)
