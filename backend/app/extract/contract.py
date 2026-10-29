"""Contract text-layer extractor (labelled forms only; ingest-and-extraction.md 6.8).

Free-text agreements ("between ... (the "Seller")") have no labels: the parser raises
``LayoutNotRecognised`` and the cascade falls to the LLM path. Signatories are the line below a
``Signed for and on behalf of`` block and are never judged.
"""

from __future__ import annotations

import re

from app.core.context import CaseContext
from app.core.models import Cell, Document, Table
from app.core.textnorm import company_match
from app.extract.base import (
    ExtractionResult,
    LayoutNotRecognised,
    Validation,
    missing_required,
    unverified_failures,
)
from app.extract.normalise import looks_like_date
from app.extract.registry import register_extractor, register_prompt_hints, register_validator
from app.extract.text_layer import (
    ColDef,
    DocView,
    TextLayerExtractor,
    blank_row,
    flat_text,
    parse_table,
    row_box,
    words_text,
)

DOC_TYPE = "contract"

LABELS = {
    "contract_number": [r"^Contract\s+(?:No\.?|Number|Ref(?:erence)?)"],
    "contract_date": [
        r"^Agreement\s+Date",
        r"^Contract\s+Date",
        r"^Date\s+of\s+Agreement",
        r"^Dated?",
    ],
    "effective_date": [r"^Effective\s+Date", r"^Commencement\s+Date"],
    "end_date": [r"^(?:Expiry|End|Termination)\s+Date"],
    "seller_name": [r"^Seller(?!\s+UEN)", r"^Supplier(?!\s+UEN)", r"^Vendor(?!\s+UEN)"],
    "buyer_name": [r"^Buyer(?!\s+UEN)", r"^Customer(?!\s+UEN)", r"^Purchaser(?!\s+UEN)"],
    "seller_uen": [r"^(?:Seller|Supplier|Vendor)\s+UEN"],
    "buyer_uen": [r"^(?:Buyer|Customer|Purchaser)\s+UEN"],
    "contract_value": [r"^Contract\s+(?:Value|Sum|Price)", r"^Total\s+Contract\s+Value"],
    "payment_terms": [r"^Payment\s+Terms"],
    "currency": [r"^Currency"],
}
SIGNED = [r"^Signed\s+for\s+and\s+on\s+behalf\s+of"]
MILESTONE_COLS = (
    ColDef("description", r"milestone|description|deliverable"),
    ColDef(
        "due_date",
        r"due\s+date|date",
    ),
    ColDef("amount", r"amount|payment", align="right"),
)
_CURRENCY_WORD = re.compile(r"^(?:S\$|SGD|USD|US\$)$", re.I)

_DATE = r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
_ROLE = r"(?P<role>Seller|Supplier|Vendor|Buyer|Customer|Purchaser)"
_PARTY = re.compile(
    r"(?:\(\s*\d\s*\)|\bbetween|\band)\s+(?P<name>[^()]+?)\s*"
    r"\(\s*(?:UEN|Co\.?\s*Reg\.?\s*No\.?)\s*[:.]?\s*(?P<uen>[0-9A-Z]+)\s*\)\s*,?\s*"
    r"(?:\(?\s*(?:hereinafter\s+)?(?:referred\s+to\s+as\s+)?)?(?:the\s+)?[\"\u201c]?" + _ROLE,
    re.I,
)
_PROSE_DATES = {
    "contract_date": re.compile(r"(?:made|dated|entered\s+into)\s+on\s+(?P<v>" + _DATE + ")", re.I),
    "effective_date": re.compile(
        r"(?:takes\s+effect|commences?|effective)\s+(?:on|from)\s+(?P<v>" + _DATE + ")", re.I
    ),
    "end_date": re.compile(
        r"(?:continues\s+until|expires?\s+on|ends?\s+on|until)\s+(?P<v>" + _DATE + ")", re.I
    ),
}
_PROSE_VALUE = re.compile(
    r"(?:total\s+)?(?:contract\s+)?(?:value|sum|price)\s+of\s+this\s+Agreement\s+is\s+"
    r"(?P<cur>S\$|SGD|US\$|USD)?\s*(?P<v>\d[\d,]*(?:\.\d{1,2})?)",
    re.I,
)
_PROSE_TERMS = re.compile(r"Payment\s+terms\s*:\s*(?P<v>[^.]+?)\s*\.(?:\s|$)", re.I)


def _all_labels() -> list[str]:
    return [p for pats in LABELS.values() for p in pats] + SIGNED


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class ContractTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        view.set_known_labels(_all_labels())
        for key in (
            "contract_number",
            "contract_date",
            "effective_date",
            "end_date",
            "payment_terms",
        ):
            view.field(fields, key, LABELS[key])
        terms = fields["payment_terms"]
        if terms.raw and terms.raw.endswith("."):  # a sentence-ending full stop is not the value
            fields["payment_terms"] = terms.model_copy(
                update={"raw": terms.raw.rstrip("."), "value": (terms.value or "").rstrip(".")}
            )
        value_hit = view.field(fields, "contract_value", LABELS["contract_value"])
        for party in ("seller", "buyer"):
            hit = view.first(LABELS[f"{party}_name"])
            if hit is not None:
                fields[f"{party}_name"] = view.cell(hit.value_words, "text", f"fields.{party}_name")
            if (
                view.field(fields, f"{party}_uen", LABELS[f"{party}_uen"]) is None
                and hit is not None
            ):
                near = view.uen_near(hit)
                if near is not None:
                    fields[f"{party}_uen"] = view.cell(
                        near.value_words[:1], "id", f"fields.{party}_uen"
                    )
        self._prose(view, fields)
        found = sum(
            1
            for k in ("contract_value", "seller_name", "buyer_name", "contract_number")
            if fields[k].value is not None
        )
        if found < 2:
            raise LayoutNotRecognised("fewer than two labelled contract fields found")
        self._title(view, fields)
        self._currency(view, fields, value_hit)
        self._signatories(view, fields)
        self._milestones(view, tables)

    @staticmethod
    def _prose(view: DocView, fields: dict[str, Cell]) -> None:
        """Sentences of the usual agreement form, read only where no label already gave a value."""
        flat = flat_text(view)
        for m in _PARTY.finditer(flat.text):
            role = m.group("role").lower()
            side = "seller" if role in ("seller", "supplier", "vendor") else "buyer"
            if fields[f"{side}_name"].value is None:
                name = m.group("name").strip(" ,;:\u201c\u201d\"'")
                start = m.start("name") + m.group("name").find(name)
                fields[f"{side}_name"] = view.cell(
                    flat.words_in(start, start + len(name)),
                    "text",
                    f"fields.{side}_name",
                    raw_override=name,
                )
            if fields[f"{side}_uen"].value is None:
                fields[f"{side}_uen"] = view.cell(
                    flat.words_in(m.start("uen"), m.end("uen")),
                    "id",
                    f"fields.{side}_uen",
                    raw_override=m.group("uen"),
                )
        for key, rx in _PROSE_DATES.items():
            m = rx.search(flat.text)
            if m and fields[key].value is None and fields[key].raw is None:
                fields[key] = view.cell(
                    flat.words_in(m.start("v"), m.end("v")),
                    "date",
                    f"fields.{key}",
                    raw_override=m.group("v"),
                )
        m = _PROSE_VALUE.search(flat.text)
        if m and fields["contract_value"].value is None:
            fields["contract_value"] = view.cell(
                flat.words_in(m.start("v"), m.end("v")),
                "money",
                "fields.contract_value",
                raw_override=m.group("v"),
            )
            if m.group("cur") and fields["currency"].value is None:
                fields["currency"] = view.cell(
                    flat.words_in(m.start("cur"), m.end("cur")),
                    "enum",
                    "fields.currency",
                    raw_override=m.group("cur"),
                )
        m = _PROSE_TERMS.search(flat.text)
        if m and fields["payment_terms"].value is None:
            fields["payment_terms"] = view.cell(
                flat.words_in(m.start("v"), m.end("v")),
                "text",
                "fields.payment_terms",
                raw_override=m.group("v"),
            )

    @staticmethod
    def _title(view: DocView, fields: dict[str, Cell]) -> None:
        lines = [ln for ln in view.first_page.lines if ln.top <= 0.3]
        if not lines:
            return
        best = max(lines, key=lambda ln: (ln.max_size, -ln.index))
        # a heading repeated in sentence case further down is the title as written
        same = [
            ln
            for ln in lines
            if ln is not best
            and ln.text.casefold() == best.text.casefold()
            and not ln.text.isupper()
        ]
        pick = same[0] if best.text.isupper() and same else best
        fields["title"] = view.cell(pick.words, "text", "fields.title")

    @staticmethod
    def _currency(view: DocView, fields: dict[str, Cell], value_hit) -> None:  # noqa: ANN001
        if fields["currency"].value is not None:
            return
        if view.field(fields, "currency", LABELS["currency"]) is not None:
            return
        if value_hit is not None:
            for w in value_hit.value_words:
                t = w.text.strip()
                if _CURRENCY_WORD.match(t):
                    fields["currency"] = view.cell([w], "enum", "fields.currency")
                    return
                m = re.match(r"^(S\$|SGD|US\$|USD)\d", t, re.I)
                if m:
                    fields["currency"] = view.cell(
                        [w], "enum", "fields.currency", raw_override=m.group(1)
                    )
                    return
        view.warn("currency_not_printed")

    @staticmethod
    def _signature_columns(view: DocView, fields: dict[str, Cell]) -> None:
        """'For the Seller   For the Buyer' with the signatory names on the next line."""
        for ln in view.all_lines:
            roles = {}
            for seg in ln.segs:
                m = re.fullmatch(
                    r"For\s+the\s+(Seller|Supplier|Vendor|Buyer|Customer|Purchaser)",
                    seg.text.strip(),
                    re.I,
                )
                if m:
                    side = (
                        "seller"
                        if m.group(1).lower() in ("seller", "supplier", "vendor")
                        else "buyer"
                    )
                    roles[side] = seg
            if not roles:
                continue
            page_lines = next(p.lines for p in view.pages if p.page == ln.page)
            nxt = next((c for c in page_lines if c.index == ln.index + 1), None)
            if nxt is None:
                continue
            for side, label_seg in roles.items():
                cand = [s for s in nxt.segs if abs(s.x0 - label_seg.x0) <= 0.05]
                key = f"signatory_{side}"
                if cand and fields[key].value is None:
                    fields[key] = view.cell(cand[0].words, "text", f"fields.{key}")
            return

    @staticmethod
    def _signatories(view: DocView, fields: dict[str, Cell]) -> None:
        ContractTextLayer._signature_columns(view, fields)
        hits = view.find_labelled(SIGNED, below=False)
        seller = fields["seller_name"].text()
        buyer = fields["buyer_name"].text()
        for order, hit in enumerate(hits):
            party_text = words_text(hit.value_words)
            key = None
            if seller and company_match(party_text, seller):
                key = "signatory_seller"
            elif buyer and company_match(party_text, buyer):
                key = "signatory_buyer"
            elif order < 2:
                key = ("signatory_seller", "signatory_buyer")[order]
            if key is None or fields[key].value is not None:
                continue
            page_lines = next(p.lines for p in view.pages if p.page == hit.line.page)
            nxt = next((ln for ln in page_lines if ln.index == hit.line.index + 1), None)
            if nxt is None or not nxt.segs:
                continue
            words = nxt.segs[0].words
            text = words_text(words)
            text = re.sub(r"^(?:Name|By)\s*:?\s*", "", text, flags=re.I)
            if text:
                fields[key] = view.cell(
                    [w for w in words if w.text.strip().rstrip(":").lower() not in ("name", "by")],
                    "text",
                    f"fields.{key}",
                )

    @staticmethod
    def _milestones(view: DocView, tables: dict[str, Table]) -> None:
        parsed = parse_table(
            view,
            MILESTONE_COLS,
            accept=lambda keys: {"description", "due_date", "amount"} <= keys,
            is_row_start=lambda cols: (
                bool(cols.get("due_date")) and looks_like_date(words_text(cols["due_date"]))
            ),
        )
        table = tables["milestones"]
        pages: list[int] = []
        for i, pr in enumerate(parsed.rows):
            row = blank_row(DOC_TYPE, "milestones", i)
            row.cells["description"] = view.cell(
                pr.cols.get("description", []), "text", f"milestones[{i}].description"
            )
            row.cells["due_date"] = view.cell(
                pr.cols.get("due_date", []), "date", f"milestones[{i}].due_date"
            )
            row.cells["amount"] = view.cell(
                pr.cols.get("amount", []), "money", f"milestones[{i}].amount", magnitude=True
            )
            row.box = row_box(row.cells.values())
            table.rows.append(row)
            pages.append(pr.page)
        table.pages = sorted(set(pages))
        view.row_pages["milestones"] = pages


@register_validator(doc_types={DOC_TYPE})
def validate_contract(result: ExtractionResult, ctx: CaseContext, doc: Document) -> Validation:
    v = Validation()
    for page, reason in unverified_failures(result, doc).items():
        v.failing.setdefault(page, reason)
    v.missing_required = missing_required(result, DOC_TYPE)
    return v


register_prompt_hints(
    DOC_TYPE,
    "This is a commercial contract or agreement. `seller_*` is the party supplying goods or "
    "services and `buyer_*` the party receiving them, as named in the parties clause or party "
    "block. `contract_value` is the total contract sum with its printed currency symbol. Dates are "
    "returned exactly as printed. `signatory_*` are the printed names under 'Signed for and on "
    "behalf of'; do not judge signatures. `milestones` only if a payment schedule is printed.",
)
