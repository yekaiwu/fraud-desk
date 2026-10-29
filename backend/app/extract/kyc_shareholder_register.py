"""Shareholder register text-layer extractor (ingest-and-extraction.md 6.8, data-model 6.9).

Labelled header fields plus one header-anchored table: ``Name``, ``ID / UEN``, ``No. of Shares``,
``%``, ``Class``, ``Date Acquired`` (class and date are optional). ``total_shares`` is read from the
``Total`` row of the table, else from a ``Total shares`` / ``Issued shares`` label. Percentages are the
printed percentage points as strings (``"45.00"``); nothing is recomputed, so a register whose rows do
not add to the printed total stays exactly that for the checks to see.
"""

from __future__ import annotations

import re

from app.core.models import Cell, Table
from app.extract.base import LayoutNotRecognised
from app.extract.kyc_common import (
    SECTION_STOP,
    Form,
    currency_cell,
    fill_rows,
    make_validator,
    run_table,
)
from app.extract.registry import register_extractor, register_prompt_hints
from app.extract.text_layer import ColDef, DocView, TextLayerExtractor, words_text

DOC_TYPE = "shareholder_register"

LABELS = {
    "company_name": [
        r"^(?:Company|Entity)\s+Name",
        r"^Name\s+of\s+(?:the\s+)?(?:Company|Entity)",
        r"^Company(?!\s+(?:No|Reg))",
    ],
    "uen": [r"^UEN", r"^Co\.?\s*Reg(?:istration)?\.?\s*No\.?"],
    "register_date": [
        r"^Register\s+Date",
        r"^Date\s+of\s+Register",
        r"^Register\s+as\s+at",
        r"^As\s+at",
        r"^Information\s+as\s+at",
        r"^Date",
    ],
    "total_shares": [
        r"^Total\s+(?:Issued\s+)?Shares(?:\s+Issued)?",
        r"^Issued\s+Shares",
        r"^Number\s+of\s+Issued\s+Shares",
    ],
    "issued_capital": [
        r"^Issued\s+(?:and\s+Paid[-\s]?up\s+)?(?:Share\s+)?Capital",
        r"^Paid[-\s]?up\s+Capital",
    ],
    "currency": [r"^Currency"],
}


REGISTER_COLS = (
    ColDef("holder_name", r"(?:share)?holder(?:'?s)?(?:\s+name)?|name|member(?:'?s)?(?:\s+name)?"),
    ColDef(
        "id_or_uen",
        r"id\s*/\s*uen|uen\s*/\s*id|id\s*/\s*nric\s*/\s*uen|nric\s*/\s*uen|uen|"
        r"id(?:\s*(?:number|no\.?))?",
    ),
    ColDef(
        "shares",
        r"(?:no\.?\s+of\s+|number\s+of\s+)?shares(?:\s+held)?|no\.?\s+of\s+shares\s+held",
    ),
    ColDef(
        "percent",
        r"%(?:\s*held)?|percent(?:age)?(?:\s+held)?|holding\s*%|%\s*holding|ownership\s*%",
    ),
    ColDef("share_class", r"class(?:\s+of\s+shares)?|share\s+class|type"),
    ColDef(
        "date_acquired",
        r"date\s+acquired|acquired(?:\s+on)?|date\s+of\s+acquisition|acquisition\s+date",
    ),
)


_INT = re.compile(r"\d[\d,]*")
_TOTAL = re.compile(r"Total\b", re.I)


def _is_holder_row(cols: dict) -> bool:
    if not cols.get("holder_name"):
        return False
    shares = words_text(cols.get("shares", []))
    pct = words_text(cols.get("percent", []))
    return bool(_INT.fullmatch(shares.strip()) or "%" in pct or re.fullmatch(r"\d+(?:\.\d+)?", pct))


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class ShareholderRegisterTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        form = Form(view, DOC_TYPE, LABELS, extra_known=[r"^Total\b"])
        parsed = run_table(
            view,
            REGISTER_COLS,
            accept=lambda k: "holder_name" in k and "shares" in k and "percent" in k,
            is_row_start=_is_holder_row,
            wrap_key="holder_name",
            special=lambda ln, cols: "total" if _TOTAL.match(ln.text.strip()) else None,
            stop=lambda ln, cols: bool(SECTION_STOP.match(ln.text.strip())),
        )
        if not parsed.header_pages:
            raise LayoutNotRecognised("no shareholder register table header")
        if not parsed.rows:
            raise LayoutNotRecognised("shareholder register table has no rows")
        fill_rows(view, tables, DOC_TYPE, "shareholders", parsed)

        form.put_all(fields, ["company_name", "uen", "register_date", "issued_capital", "currency"])
        cap = form.first("issued_capital")
        if cap is not None:
            currency_cell(view, fields, cap.value_words)

        # total_shares: the table's Total row first (the shares column), else a labelled total
        for tag, _page, cols, _ln in parsed.specials:
            words = cols.get("shares")
            if tag == "total" and words and fields["total_shares"].raw is None:
                fields["total_shares"] = view.cell(words, "int", "fields.total_shares")
        form.put(fields, "total_shares")

        if fields["company_name"].raw is None and fields["uen"].raw is None:
            raise LayoutNotRecognised("no company name or UEN label on the register")


make_validator(DOC_TYPE)

register_prompt_hints(
    DOC_TYPE,
    "This is a register of members (shareholders). Header fields: `company_name`, `uen`, "
    "`register_date` (the as-at date), `total_shares` (the printed total of issued shares, from the "
    "Total row or label). `shareholders` has one row per listed holder: `shares` is the printed "
    "share count, `percent` the printed percentage (for example `45.00%`), `share_class` and "
    "`date_acquired` only if columns exist. Do not include the Total row as a holder; do not "
    "recompute any number.",
)
