"""Bank statement text-layer extractor and validator (docs/ingest-and-extraction.md 6.8).

Covers the generator's four layout families: ``dbs_style`` (Withdrawals/Deposits, ``Page 1 of 3``,
brought-forward row, totals row), ``ocbc_style`` (Value Date and Cheque ignored, ``05/03/2026``,
``Page 1/3``), ``uob_style`` (single Amount column with ``CR``/``DR``) and ``software`` (ISO dates,
bare ``1/3`` footer). Quirks honoured: wrapped descriptions are joined; brought-forward, carried-
forward and totals lines are never rows; a printed ``31 Feb 2026`` keeps ``raw`` with
``value=None``; an ID such as ``S8222222A`` is text, never an amount; a statement without a balance
column leaves ``balance`` empty. Nothing is derived or repaired.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.core.context import CaseContext
from app.core.models import Cell, Document, Table, Word
from app.core.money import approx_eq
from app.core.textlayout import layout_text, page_marker
from app.extract.base import (
    ExtractionResult,
    LayoutNotRecognised,
    Validation,
    missing_required,
    unverified_failures,
)
from app.extract.normalise import DATE_FRAGMENT, format_warning, looks_like_date
from app.extract.registry import register_extractor, register_prompt_hints, register_validator
from app.extract.text_layer import (
    ColDef,
    DocView,
    Line,
    TextLayerExtractor,
    blank_row,
    is_amount_text,
    parse_table,
    row_box,
    words_text,
)

DOC_TYPE = "bank_statement"

COLDEFS = (
    ColDef("_value_date", r"value\s+date", ignore=True),
    ColDef("_cheque", r"cheque(?:\s+no\.?)?", ignore=True),
    ColDef("_ref", r"ref(?:erence)?(?:\s+no\.?)?", ignore=True),
    ColDef("date", r"(?:txn\s+|transaction\s+)?date"),
    ColDef(
        "description",
        r"description|transaction(?:\s+details)?|particulars|details",
    ),
    ColDef("debit", r"debit|withdrawals?|withdrawn", align="right"),
    ColDef("credit", r"credit|deposits?|deposited", align="right"),
    ColDef("amount", r"amount", align="right"),
    ColDef("balance", r"(?:running\s+)?balance", align="right"),
)

LABELS = {
    "account_holder": [r"Account\s+(?:Name|Holder)", r"Customer\s+Name"],
    "account_number": [r"Account\s+(?:No\.?|Number)", r"A/C\s+No\.?"],
    "uen": [r"UEN", r"Co\.?\s*Reg(?:istration)?\s*No\.?"],
    "period": [r"Statement\s+Period", r"Period"],
    "opening": [r"Opening\s+Balance", r"Balance\s+(?:Brought\s+Forward|B/F)"],
    "closing": [r"Closing\s+Balance", r"Balance\s+(?:Carried\s+Forward|C/F)"],
    "currency": [r"Currency"],
    "total_debits": [r"Total\s+(?:Debits?|Withdrawals?)"],
    "total_credits": [r"Total\s+(?:Credits?|Deposits?)"],
}

_BF = re.compile(r"balance\s+(?:brought\s+forward|b/?f)\b|opening\s+balance|b/f\s+balance", re.I)
_CF = re.compile(r"balance\s+(?:carried\s+forward|c/?f)\b|closing\s+balance|c/f\s+balance", re.I)
_TOTAL = re.compile(r"^(?:\S+\s+){0,3}?total\b", re.I)
_FOOTER = re.compile(
    r"specimen|fictitious|page\s+\d+\s*(?:of|/)\s*\d+|this is a computer|^\s*\d+\s*/\s*\d+\s*$|"
    r"pages?,\s*this is page",
    re.I,
)
_CONTINUED = re.compile(r"^\(?continued\)?$", re.I)
_COMPANY = re.compile(r"\b(?:pte|ltd|limited|llp|inc|corp|co\.|enterprise|trading)\b", re.I)
_BANK_WORDS = re.compile(r"\b(?:bank|dbs|ocbc|uob|ltd|limited)\b", re.I)


def _accept(keys: set[str]) -> bool:
    return {"date", "description"} <= keys and ({"debit", "credit"} <= keys or "amount" in keys)


def _line_text(cols: dict[str, list[Word]]) -> str:
    return " ".join(words_text(cols[k]) for k in ("date", "description") if k in cols)


def _special(line: Line, cols: dict[str, list[Word]]) -> str | None:
    text = line.text
    if _BF.search(text) and not looks_like_date(words_text(cols.get("date", []))):
        return "bf"
    if _CF.search(text) and not looks_like_date(words_text(cols.get("date", []))):
        return "cf"
    if (
        _TOTAL.search(text)
        and ("debit" in cols or "credit" in cols)
        and not looks_like_date(words_text(cols.get("date", [])))
    ):
        return "total"
    return None


def _is_row_start(cols: dict[str, list[Word]]) -> bool:
    date_words = cols.get("date")
    return bool(date_words) and looks_like_date(words_text(date_words))


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class BankStatementTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    # ------------------------------------------------------------------ parse
    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        view.set_known_labels([p for pats in LABELS.values() for p in pats])
        parsed = parse_table(
            view,
            COLDEFS,
            accept=_accept,
            is_row_start=_is_row_start,
            special=_special,
            footer=_FOOTER,
        )
        if not parsed.header_pages:
            raise LayoutNotRecognised("no transaction table header (date, description, amounts)")
        if not parsed.rows:
            raise LayoutNotRecognised("transaction table header found but no dated rows")

        table = tables["transactions"]
        single = any(b for b in parsed.rows[0].cols if b == "amount") or not any(
            "debit" in r.cols or "credit" in r.cols for r in parsed.rows
        )
        has_amount_col = "amount" in {k for r in parsed.rows for k in r.cols} or single
        row_pages: list[int] = []
        for i, pr in enumerate(parsed.rows):
            row = blank_row(DOC_TYPE, "transactions", i)
            cells = row.cells
            where = f"transactions[{i}]"
            cells["date"] = view.cell(pr.cols.get("date", []), "date", f"{where}.date")
            cells["description"] = view.cell(
                pr.cols.get("description", []), "text", f"{where}.description"
            )
            if has_amount_col and "amount" in pr.cols:
                self._single_amount(view, pr.cols["amount"], cells, where)
            else:
                cells["debit"] = view.cell(
                    pr.cols.get("debit", []), "money", f"{where}.debit", magnitude=True
                )
                cells["credit"] = view.cell(
                    pr.cols.get("credit", []), "money", f"{where}.credit", magnitude=True
                )
            cells["balance"] = view.cell(pr.cols.get("balance", []), "money", f"{where}.balance")
            row.box = row_box(cells.values())
            table.rows.append(row)
            row_pages.append(pr.page)
        table.pages = sorted({pr.page for pr in parsed.rows} | set(parsed.header_pages))

        specials = {}
        for tag, page, cols, line in parsed.specials:
            specials.setdefault(tag, (page, cols, line))

        self._fields(view, fields, parsed.currency_raw, specials)
        self._page_chain(view, fields, tables)
        # column honesty: no balance column at all -> an empty balance is not an error
        if "balance" not in {k for r in parsed.rows for k in r.cols}:
            view.warn("no_balance_column")
        view.row_pages["transactions"] = row_pages

    # ------------------------------------------------------------------ pieces
    @staticmethod
    def _single_amount(
        view: DocView, words: list[Word], cells: dict[str, Cell], where: str
    ) -> None:
        text = words_text(words)
        m = re.search(r"\b(CR|DR)\s*$", text, re.I)
        if m is None:
            view.warn(format_warning("unparsed", f"{where}.amount", text))
            return
        side = "credit" if m.group(1).upper() == "CR" else "debit"
        cells[side] = view.cell(words, "money", f"{where}.{side}", magnitude=True)

    def _fields(
        self,
        view: DocView,
        fields: dict[str, Cell],
        header_currency: str | None,
        specials: dict[str, tuple[int, dict[str, list[Word]], Line]],
    ) -> None:
        # bank name: largest-font line in the top 15 % of page 1
        page1 = view.first_page
        top = [ln for ln in page1.lines if ln.top <= 0.15]
        pool = top or page1.lines
        named = [ln for ln in pool if _BANK_WORDS.search(ln.text)]
        best = max(named or pool, key=lambda ln: (ln.max_size, -ln.index))
        fields["bank_name"] = view.cell(best.words, "text", "fields.bank_name")
        view.claimed.discard((best.page, best.index))

        if view.field(fields, "account_holder", LABELS["account_holder"]) is None:
            self._unlabelled_holder(view, fields, best)
        view.field(fields, "account_number", LABELS["account_number"], trim=_CONTINUED)
        view.field(fields, "uen", LABELS["uen"])
        self._period(view, fields)

        if view.field(fields, "opening_balance", LABELS["opening"]) is None and "bf" in specials:
            fields["opening_balance"] = self._special_balance(
                view, specials["bf"], "opening_balance"
            )
        if view.field(fields, "closing_balance", LABELS["closing"]) is None and "cf" in specials:
            fields["closing_balance"] = self._special_balance(
                view, specials["cf"], "closing_balance"
            )

        if view.field(fields, "currency", LABELS["currency"]) is None:
            if header_currency:
                fields["currency"] = view.cell(
                    [w for w in view.first_page.words if w.text.strip("() ") == header_currency][
                        :1
                    ],
                    "enum",
                    "fields.currency",
                )
            if fields["currency"].value is None:
                view.warn("currency_not_printed")

        if (
            view.field(fields, "total_debits", LABELS["total_debits"]) is None
            and "total" in specials
        ):
            _, cols, _ = specials["total"]
            if cols.get("debit"):
                fields["total_debits"] = view.cell(
                    cols["debit"], "money", "fields.total_debits", magnitude=True
                )
        if (
            view.field(fields, "total_credits", LABELS["total_credits"]) is None
            and "total" in specials
        ):
            _, cols, _ = specials["total"]
            if cols.get("credit"):
                fields["total_credits"] = view.cell(
                    cols["credit"], "money", "fields.total_credits", magnitude=True
                )

    @staticmethod
    def _unlabelled_holder(view: DocView, fields: dict[str, Cell], bank_line: Line) -> None:
        """Some layouts print the holder as a bare company-name line above the table."""
        limit = min((ln for (pg, ln) in view.claimed if pg == view.first_page.page), default=10**6)
        for ln in view.first_page.lines:
            if ln is bank_line or ln.index >= limit:
                continue
            for seg in ln.segs:
                if _COMPANY.search(seg.text):
                    fields["account_holder"] = view.cell(seg.words, "text", "fields.account_holder")
                    return

    @staticmethod
    def _special_balance(
        view: DocView, special: tuple[int, dict[str, list[Word]], Line], key: str
    ) -> Cell:
        _, cols, line = special
        words = cols.get("balance")
        if not words:  # amount printed in another column: take the rightmost amount on the line
            amounts = [w for w in line.words if is_amount_text(w.text)]
            words = amounts[-1:]
        return view.cell(words, "money", f"fields.{key}")

    @staticmethod
    def _period(view: DocView, fields: dict[str, Cell]) -> None:
        hit = view.first(LABELS["period"])
        if hit is None:
            return
        words = hit.value_words
        text = ""
        spans: list[tuple[int, int]] = []
        for w in words:
            if text:
                text += " "
            spans.append((len(text), len(text) + len(w.text.strip())))
            text += w.text.strip()
        frags = [m for m in DATE_FRAGMENT.finditer(text)][:2]
        if len(frags) < 2:
            view.warn(format_warning("unparsed", "fields.period_start", text))
            return
        for key, m in zip(("period_start", "period_end"), frags, strict=True):
            ws = [
                w for w, (a, b) in zip(words, spans, strict=True) if a < m.end() and b > m.start()
            ]
            fields[key] = view.cell(ws, "date", f"fields.{key}")

    @staticmethod
    def _page_chain(view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        table = tables["page_chain"]
        markers: list[str] = []
        for pv in view.pages:
            text = layout_text(pv.words)
            marker = page_marker(text)
            acct = view.find_labelled(LABELS["account_number"], lines=pv.lines, below=True)
            for h in acct:
                h.value_words = [w for w in h.value_words if not _CONTINUED.match(w.text.strip())]
            acct = [h for h in acct if h.value_words]
            if marker is None and not acct:
                continue
            row = blank_row(DOC_TYPE, "page_chain", len(table.rows))
            row.cells["page"] = Cell(
                value=str(pv.page), kind="int", source="derived", verified=None, raw=None
            )
            if marker is not None:
                box_words = _marker_words(pv, marker)
                marker_raw = words_text(box_words)
                for key, num in (("page_x", marker[0]), ("page_y", marker[1])):
                    row.cells[key] = Cell(
                        value=str(num),
                        raw=marker_raw,
                        kind="int",
                        box=view.region(box_words),
                        source="text_layer",
                        verified=True,
                    )
                markers.append(f"{marker[0]}/{marker[1]}")
            if acct:
                row.cells["account_number"] = view.cell(
                    acct[0].value_words, "id", f"page_chain[{row.index}].account_number"
                )
            row.box = row_box(row.cells.values())
            table.rows.append(row)
            table.pages.append(pv.page)
        if markers:
            fields["page_x_of_y"] = Cell(
                value=",".join(markers), kind="text", source="derived", verified=None
            )


def _marker_words(pv, marker: tuple[int, int]) -> list[Word]:  # noqa: ANN001
    """Words of the last segment on the page whose text reads as that page marker."""
    found: list[Word] = []
    for ln in pv.lines:
        for seg in ln.segs:
            if page_marker(seg.text) == marker:
                found = list(seg.words)
        if not found and page_marker(ln.text) == marker:
            found = list(ln.words)
    return found


# --------------------------------------------------------------------------------------
# Validation (decides only whether to escalate; never edits a value)
# --------------------------------------------------------------------------------------


@register_validator(doc_types={DOC_TYPE})
def validate_bank_statement(
    result: ExtractionResult, ctx: CaseContext, doc: Document
) -> Validation:
    v = Validation()
    table = result.tables.get("transactions")
    if table is None or not table.rows:
        v.structural.append("no transaction rows")
        return v
    pages = result.row_pages.get("transactions") or [doc.page_start] * len(table.rows)
    tol: Decimal = ctx.settings.money_tolerance
    opening = result.fields["opening_balance"].money()
    prev: Decimal | None = opening
    for row, page in zip(table.rows, pages, strict=False):
        debit = row.cells["debit"].money()
        credit = row.cells["credit"].money()
        balance = row.cells["balance"].money()
        if debit is None and credit is None:
            prev = balance if balance is not None else prev
            continue
        amount = (credit or Decimal(0)) - (debit or Decimal(0))
        if balance is not None and prev is not None and not approx_eq(prev + amount, balance, tol):
            v.failing.setdefault(page, "balance_chain")
        if balance is not None:
            prev = balance
        elif prev is not None:
            prev = prev + amount
    for page, reason in unverified_failures(result, doc).items():
        v.failing.setdefault(page, reason)
    v.missing_required = missing_required(result, DOC_TYPE)
    return v


register_prompt_hints(
    DOC_TYPE,
    "This page is part of a bank statement. Return every transaction row printed on THIS page, one "
    "object per printed row in printed order, with the date, the description (join a wrapped "
    "description into one string), the debit or credit amount exactly as printed, and the "
    "running balance exactly as printed. Rows with a single Amount column ending in CR or DR: put "
    "the printed amount, with its CR/DR suffix, in `credit` for CR and in `debit` for DR and leave "
    "the other null. Do not return a Balance Brought Forward line, a Balance Carried Forward line "
    "or a Totals line as a transaction; put the opening and closing balances and the printed "
    "totals in the fields. `page_marker` is the text such as 'Page 2 of 3' printed on this page. "
    "Return an empty list for `page_chain` (it is built from `page_marker`).",
)
