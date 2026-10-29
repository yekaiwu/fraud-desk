"""Financial statement text-layer extractor, validator and post-processing (6.8, data-model 6.3).

Statements are located by title (income statement, balance sheet, cash flow); the later year column
is ``current`` and the earlier ``prior``. Row labels map to the canonical ``line`` keys by synonym
lists (first match wins); an unmatched label is ``line="other"`` with the label kept as printed.

After parsing (either backend) the post-processor multiplies every money cell by ``scale_printed``
(``Decimal``, so values are whole currency units; ``raw`` keeps the printed figure), applies the
sign conventions of data-model 6.3 and derives ``is_subtotal`` from ``line``.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.core.context import CaseContext
from app.core.models import Cell, Document, Table, Word
from app.core.money import fmt_money
from app.core.schemas import FS_SUBTOTAL_LINES
from app.extract.base import (
    ExtractionResult,
    LayoutNotRecognised,
    Validation,
    missing_required,
    unverified_failures,
)
from app.extract.normalise import DATE_FRAGMENT, SCALE_WORDS, currency_code
from app.extract.registry import (
    register_extractor,
    register_postprocessor,
    register_prompt_hints,
    register_validator,
)
from app.extract.text_layer import (
    DocView,
    Line,
    TextLayerExtractor,
    blank_row,
    is_amount_text,
    row_box,
)

DOC_TYPE = "financial_statement"
TABLES = ("income_statement", "balance_sheet", "cash_flow")

_TITLES = {
    "income_statement": re.compile(
        r"^(?:the\s+)?(?:consolidated\s+)?(?:income\s+statements?|statements?\s+of\s+"
        r"(?:comprehensive\s+)?income|(?:statements?\s+of\s+)?profit\s+(?:and|&)\s+loss"
        r"(?:\s+account)?)(?:\s+for\s+the\s+.*)?$",
        re.I,
    ),
    "balance_sheet": re.compile(
        r"^(?:the\s+)?(?:consolidated\s+)?(?:balance\s+sheet|statements?\s+of\s+financial\s+"
        r"position)(?:\s+as\s+at\s+.*)?$",
        re.I,
    ),
    "cash_flow": re.compile(
        r"^(?:the\s+)?(?:consolidated\s+)?(?:statements?\s+of\s+)?cash\s+flows?"
        r"(?:\s+statements?)?(?:\s+for\s+the\s+.*)?$",
        re.I,
    ),
}

LABELS = {
    "company_name": [r"^Company(?:\s+Name)?", r"^Entity(?:\s+Name)?"],
    "uen": [r"UEN", r"Co\.?\s*Reg(?:istration)?\s*No\.?"],
    "currency": [r"^Currency"],
    "auditor": [r"Independent\s+Auditors?", r"Auditors?", r"Audited\s+by"],
}

_YEAR = re.compile(r"^(?:FY)?'?((?:19|20)\d{2})$", re.I)
_NEUTRAL = re.compile(
    r"^(?:\d{1,2}|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|"
    r"dec\w*|notes?|s\$|sgd|usd|\$|\(?s?\$?'?000\)?|'?000'?s?|\(?s\$\)?|as|at|for|the|year|ended)$",
    re.I,
)
_FOOTER = re.compile(r"specimen|fictitious|page\s+\d+\s*(?:of|/)\s*\d+", re.I)
_COMPANY = re.compile(r"\b(?:pte|ltd|limited|llp|inc|corp|co\.)\b", re.I)
_END_DATE = re.compile(r"(?:(?:year|period)\s+ended|as\s+at)\s+", re.I)

# --------------------------------------------------------------------------------------
# Label synonyms (first match wins), per table
# --------------------------------------------------------------------------------------

_INCOME: list[tuple[str, str]] = [
    (r"^gross\s+(?:profit|margin|loss)", "gross_profit"),
    (
        r"^(?:net\s+)?(?:profit|loss|\(?loss\)?/?\(?profit\)?|profit/?\(?loss\)?)\s+before\s+(?:income\s+)?tax",
        "profit_before_tax",
    ),
    (
        r"^(?:net\s+)?(?:profit|loss|income|profit/\(loss\)|\(loss\)/profit)(?:\s+(?:and\s+total\s+comprehensive\s+income\s+)?(?:for\s+the\s+(?:year|period)))?(?:\s+after\s+(?:income\s+)?tax(?:ation)?)?$",
        "net_profit",
    ),
    (r"^(?:profit|loss).*after\s+(?:income\s+)?tax", "net_profit"),
    (r"^(?:total\s+)?(?:revenue|sales|turnover)\b", "revenue"),
    (r"^cost\s+of\s+(?:sales|goods\s+sold|revenue)", "cost_of_sales"),
    (r"^other\s+(?:operating\s+)?(?:income|gains?)", "other_income"),
    (
        r"^(?:finance|financing|interest)\s+(?:costs?|expenses?)|^interest\s+expense",
        "finance_costs",
    ),
    (r"^(?:income\s+)?tax(?:ation)?(?:\s+(?:expense|charge))?$|^income\s+tax", "income_tax"),
    (
        r"^(?:total\s+)?(?:operating|administrative|admin|general|selling|distribution)\b.*\bexpenses?\b",
        "operating_expenses",
    ),
]

_BALANCE: list[tuple[str, str]] = [
    (
        r"^total\s+(?:equity\s+and\s+liabilities|liabilities\s+and\s+(?:shareholders'?\s+)?equity)",
        "total_liabilities_and_equity",
    ),
    (r"^total\s+non-?\s*current\s+assets", "total_non_current_assets"),
    (r"^total\s+current\s+assets", "total_current_assets"),
    (r"^total\s+assets", "total_assets"),
    (r"^total\s+non-?\s*current\s+liabilities", "total_non_current_liabilities"),
    (r"^total\s+current\s+liabilities", "total_current_liabilities"),
    (r"^total\s+liabilities", "total_liabilities"),
    (r"^total\s+(?:shareholders'?\s+|stockholders'?\s+)?equity", "total_equity"),
    (
        r"^cash(?:\s+and\s+(?:cash\s+)?(?:equivalents|bank\s+balances?))?$|^cash\s+and\s+cash\s+equivalents",
        "cash_and_equivalents",
    ),
    (r"^trade\s+(?:and\s+other\s+)?receivables", "trade_receivables"),
    (r"^(?:inventories|stock)", "inventories"),
    (r"^property,?\s+plant\s+(?:and|&)\s+equipment|^ppe\b", "property_plant_equipment"),
    (r"^trade\s+(?:and\s+other\s+)?payables", "trade_payables"),
    (r"^(?:share|paid-?\s*up)\s+capital", "share_capital"),
    (r"^retained\s+(?:earnings|profits?)|^accumulated\s+(?:profits?|losses)", "retained_earnings"),
]
_BALANCE_BORROW = re.compile(r"^(?:bank\s+)?(?:loans?|borrowings?)|loans?\s+and\s+borrowings", re.I)
_BALANCE_OTHER = [
    (
        r"^other\s+(?:current\s+)?(?:assets|receivables)|^prepayments|^deposits",
        "other_current_assets",
        "current_assets",
    ),
    (r"^other\s+non-?\s*current\s+assets", "other_non_current_assets", "non_current_assets"),
    (
        r"^other\s+(?:current\s+)?(?:liabilities|payables)|^accruals",
        "other_current_liabilities",
        "current_liabilities",
    ),
    (
        r"^other\s+non-?\s*current\s+liabilities",
        "other_non_current_liabilities",
        "non_current_liabilities",
    ),
]

_CASH: list[tuple[str, str]] = [
    (r"^net\s+cash.*operating", "net_cash_from_operating"),
    (r"^net\s+cash.*investing", "net_cash_from_investing"),
    (r"^net\s+cash.*financing", "net_cash_from_financing"),
    (r"^net\s+(?:increase|decrease|change|\(?decrease\)?).*cash", "net_change_in_cash"),
    (r"^cash.*(?:beginning|opening|start)", "cash_opening"),
    (r"^cash.*(?:end|closing)", "cash_closing"),
    (r"^(?:net\s+)?(?:profit|loss).*before\s+tax", "profit_before_tax"),
    (r"^adjustments", "adjustments"),
    (
        r"^(?:changes?\s+in\s+)?working\s+capital|^movements?\s+in\s+working",
        "working_capital_changes",
    ),
    (
        r"^(?:purchase|acquisition)s?\s+of\s+(?:property|plant|equipment|fixed)|^capital\s+expenditure|^capex",
        "capex",
    ),
    (r"^proceeds\s+from\s+(?:bank\s+)?(?:loans?|borrowings?)", "loan_proceeds"),
    (r"^repayments?\s+of\s+(?:bank\s+)?(?:loans?|borrowings?)", "loan_repayments"),
]

_KEY_SECTION = {
    "total_current_assets": "current_assets",
    "cash_and_equivalents": "current_assets",
    "trade_receivables": "current_assets",
    "inventories": "current_assets",
    "other_current_assets": "current_assets",
    "total_non_current_assets": "non_current_assets",
    "property_plant_equipment": "non_current_assets",
    "other_non_current_assets": "non_current_assets",
    "total_assets": "totals",
    "total_liabilities": "totals",
    "total_liabilities_and_equity": "totals",
    "trade_payables": "current_liabilities",
    "borrowings_current": "current_liabilities",
    "other_current_liabilities": "current_liabilities",
    "total_current_liabilities": "current_liabilities",
    "borrowings_non_current": "non_current_liabilities",
    "other_non_current_liabilities": "non_current_liabilities",
    "total_non_current_liabilities": "non_current_liabilities",
    "share_capital": "equity",
    "retained_earnings": "equity",
    "total_equity": "equity",
    "net_cash_from_operating": "operating",
    "net_cash_from_investing": "investing",
    "net_cash_from_financing": "financing",
    "net_change_in_cash": "cash",
    "cash_opening": "cash",
    "cash_closing": "cash",
    "profit_before_tax": "operating",
    "adjustments": "operating",
    "working_capital_changes": "operating",
    "capex": "investing",
    "loan_proceeds": "financing",
    "loan_repayments": "financing",
}

_HEADINGS: dict[str, list[tuple[str, str]]] = {
    "balance_sheet": [
        (r"^non-?\s*current\s+assets$", "non_current_assets"),
        (r"^current\s+assets$", "current_assets"),
        (r"^non-?\s*current\s+liabilities$", "non_current_liabilities"),
        (r"^current\s+liabilities$", "current_liabilities"),
        (
            r"^(?:shareholders'?\s+|stockholders'?\s+)?equity$|^capital\s+and\s+reserves$|^equity\s+attributable.*",
            "equity",
        ),
    ],
    "cash_flow": [
        (r".*operating\s+activities.*", "operating"),
        (r".*investing\s+activities.*", "investing"),
        (r".*financing\s+activities.*", "financing"),
        (r"cash\s+and\s+cash\s+equivalents|net\s+(?:change|increase).*", "cash"),
    ],
    "income_statement": [],
}


def map_label(table: str, label: str, section: str | None) -> tuple[str, str]:
    """``(line, section)`` for a printed row label."""
    text = re.sub(r"\(\s*note[s]?\s*[\d, ]+\)", "", label, flags=re.I)
    text = " ".join(text.replace(":", " ").split()).strip().casefold()
    if table == "income_statement":
        for rx, key in _INCOME:
            if re.search(rx, text):
                return key, "pnl"
        return "other", "pnl"
    if table == "balance_sheet":
        for rx, key in _BALANCE:
            if re.search(rx, text):
                return key, _KEY_SECTION.get(key, section or "current_assets")
        if _BALANCE_BORROW.search(text):
            noncurrent = section == "non_current_liabilities" or re.search(
                r"non-?\s*current|long[- ]term", text
            )
            key = "borrowings_non_current" if noncurrent else "borrowings_current"
            return key, _KEY_SECTION[key]
        for rx, key, sect in _BALANCE_OTHER:
            if re.search(rx, text) and (
                section in (None, sect) or key.startswith("other_non") is False
            ):
                return key, sect
        return "other", section or "current_assets"
    for rx, key in _CASH:
        if re.search(rx, text):
            return key, _KEY_SECTION.get(key, section or "operating")
    return "other", section or "operating"


def _is_title(line: Line) -> str | None:
    if len(line.words) > 12 or any(is_amount_text(w.text) for w in line.words):
        return None
    text = " ".join(line.text.split())
    for table, rx in _TITLES.items():
        if rx.match(text):
            return table
    return None


def _year_header(line: Line) -> tuple[list[tuple[int, Word]], Word | None] | None:
    years: list[tuple[int, Word]] = []
    note: Word | None = None
    other = 0
    for w in line.words:
        t = w.text.strip()
        m = _YEAR.match(t)
        if m:
            years.append((int(m.group(1)), w))
        elif re.fullmatch(r"notes?", t, re.I):
            note = w
        elif not _NEUTRAL.match(t):
            other += 1
    if not years or other:
        return None
    return years, note


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class FinancialStatementTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        view.set_known_labels([p for pats in LABELS.values() for p in pats])
        self._statements(view, tables)
        if not any(t.rows for t in tables.values()):
            raise LayoutNotRecognised("no statement title with a year header and value rows")
        self._fields(view, fields)

    # ------------------------------------------------------------------ statements
    def _statements(self, view: DocView, tables: dict[str, Table]) -> None:
        table_name: str | None = None
        header = None
        section: str | None = None
        warned_dup: set[str] = set()
        for line in view.all_lines:
            title = _is_title(line)
            if title is not None:
                if title in warned_dup or tables[title].rows:
                    view.warn(f"label_repeated: {title}")
                table_name, header, section = title, None, None
                warned_dup.add(title)
                view.claimed.add((line.page, line.index))
                continue
            if table_name is None or _FOOTER.search(line.text):
                continue
            if header is None:
                yh = _year_header(line)
                if yh is not None:
                    header = self._anchors(yh)
                    view.claimed.add((line.page, line.index))
                continue
            self._row(view, tables[table_name], table_name, line, header, section_ref := [section])
            section = section_ref[0]

    @staticmethod
    def _anchors(yh) -> dict:  # noqa: ANN001
        years, note = yh
        years = sorted(years, key=lambda p: -p[0])
        anchors = {"current": years[0][1].x + years[0][1].w}
        if len(years) > 1 and years[1][0] != years[0][0]:
            anchors["prior"] = years[1][1].x + years[1][1].w
        if note is not None:
            anchors["note"] = note.x + note.w
        return anchors

    def _row(
        self,
        view: DocView,
        table: Table,
        name: str,
        line: Line,
        anchors: dict,
        section_ref: list[str | None],
    ) -> None:
        values: dict[str, list[Word]] = {}
        label: list[Word] = []
        for w in line.words:
            t = w.text.strip()
            if is_amount_text(t) or re.fullmatch(r"[-–—]{1,2}", t):
                col, dist = min(
                    ((k, abs((w.x + w.w) - x1)) for k, x1 in anchors.items()), key=lambda p: p[1]
                )
                if dist <= 0.06 and w.x > 0.3:
                    values.setdefault(col, []).append(w)
                    continue
            label.append(w)
        label_text = " ".join(w.text.strip() for w in label).strip()
        real = {k: v for k, v in values.items() if k != "note"}
        if not real:
            if label_text:  # heading
                for rx, sect in _HEADINGS.get(name, []):
                    if re.fullmatch(rx, " ".join(label_text.casefold().split()).rstrip(":")):
                        section_ref[0] = sect
                        break
                view.claimed.add((line.page, line.index))
            return
        if not label_text:
            return
        key, sect = map_label(name, label_text, section_ref[0])
        # a keyed total sets the section for the lines that follow only via headings
        i = len(table.rows)
        row = blank_row(DOC_TYPE, name, i)
        where = f"{name}[{i}]"
        row.cells["label"] = view.cell(label, "text", f"{where}.label")
        row.cells["line"] = Cell(value=key, kind="enum", source="derived", verified=None)
        row.cells["section"] = Cell(value=sect, kind="enum", source="derived", verified=None)
        for col in ("current", "prior"):
            words = [w for w in values.get(col, []) if re.search(r"\d", w.text)]
            row.cells[col] = view.cell(words, "money", f"{where}.{col}")
        row.box = row_box(row.cells.values())
        table.rows.append(row)
        table.pages = sorted({*table.pages, line.page})
        view.row_pages.setdefault(name, []).append(line.page)
        view.claimed.add((line.page, line.index))

    # ------------------------------------------------------------------ fields
    def _fields(self, view: DocView, fields: dict[str, Cell]) -> None:
        first = view.first_page
        if view.field(fields, "company_name", LABELS["company_name"]) is None:
            cands = [ln for ln in first.lines if _COMPANY.search(ln.text)]
            if cands:
                best = max(cands, key=lambda ln: (ln.max_size, -ln.index))
                seg = next((s for s in best.segs if _COMPANY.search(s.text)), best.segs[0])
                fields["company_name"] = view.cell(seg.words, "text", "fields.company_name")
        view.field(fields, "uen", LABELS["uen"])
        view.field(fields, "auditor", LABELS["auditor"])
        self._period(view, fields)
        self._currency(view, fields)
        self._basis(view, fields)
        self._scale(view, fields)

    @staticmethod
    def _period(view: DocView, fields: dict[str, Cell]) -> None:
        for ln in view.all_lines:
            text = ln.text
            rng = list(DATE_FRAGMENT.finditer(text))
            if re.search(r"\bfrom\b", text, re.I) and len(rng) >= 2:
                for key, frag in zip(("period_start", "period_end"), rng[:2], strict=True):
                    ws = _words_in_span(ln, frag.start(), frag.end())
                    fields[key] = view.cell(ws, "date", f"fields.{key}")
                return
            m = _END_DATE.search(text)
            if m is None:
                continue
            frag = DATE_FRAGMENT.search(text, m.end())
            if frag is None:
                continue
            ws = _words_in_span(ln, frag.start(), frag.end())
            fields["period_end"] = view.cell(ws, "date", "fields.period_end")
            rng = list(DATE_FRAGMENT.finditer(text))
            if len(rng) >= 2 and re.search(r"\bto\b|–|-", text[rng[0].end() : rng[1].start()]):
                ws0 = _words_in_span(ln, rng[0].start(), rng[0].end())
                fields["period_start"] = view.cell(ws0, "date", "fields.period_start")
            return

    @staticmethod
    def _currency(view: DocView, fields: dict[str, Cell]) -> None:
        if view.field(fields, "currency", LABELS["currency"]) is not None:
            return
        for ln in view.all_lines:
            for w in ln.words:
                m = re.match(r"^\(?(S\$|SGD|USD|US\$)(?![A-Za-z])", w.text.strip(), re.I)
                if m:
                    fields["currency"] = view.cell(
                        [w], "enum", "fields.currency", raw_override=m.group(1)
                    )
                    return
        for ln in view.all_lines:
            m = re.search(r"expressed\s+in\s+(?P<c>[A-Za-z$' ]+?)(?:\)|$|\s{2,})", ln.text, re.I)
            if m and currency_code(m.group("c")):
                ws = _words_in_span(ln, m.start("c"), m.end("c"))
                fields["currency"] = view.cell(
                    ws, "enum", "fields.currency", raw_override=m.group("c")
                )
                return
        view.warn("currency_not_printed")

    @staticmethod
    def _basis(view: DocView, fields: dict[str, Cell]) -> None:
        spec = ["audited", "unaudited", "unknown"]
        for rx, value in (
            (r"\bun-?audited\b|management\s+accounts", "unaudited"),
            (r"\baudited\b|independent\s+auditor", "audited"),
        ):
            for ln in view.all_lines:
                m = re.search(rx, ln.text, re.I)
                if m:
                    ws = _words_in_span(ln, m.start(), m.end())
                    fields["basis"] = view.cell(
                        ws, "enum", "fields.basis", values=spec, raw_override=m.group(0)
                    )
                    fields["basis"] = fields["basis"].model_copy(update={"value": value})
                    return
        fields["basis"] = Cell(value="unknown", kind="enum", source="derived", verified=None)

    @staticmethod
    def _scale(view: DocView, fields: dict[str, Cell]) -> None:
        for ln in view.all_lines:
            for rx, scale in SCALE_WORDS:
                m = rx.search(ln.text)
                if m:
                    ws = _words_in_span(ln, m.start(), m.end())
                    cell = view.cell(ws, "enum", "fields.scale_printed", raw_override=m.group(0))
                    fields["scale_printed"] = cell.model_copy(update={"value": scale})
                    return
        fields["scale_printed"] = Cell(value="1", kind="enum", source="derived", verified=None)
        view.warn("scale_not_printed")


def _words_in_span(line: Line, start: int, end: int) -> list[Word]:
    """Words of a line whose characters overlap ``[start, end)`` of ``line.text``."""
    out: list[Word] = []
    pos = 0
    for w in line.words:
        t = w.text.strip()
        if not t:
            continue
        a, b = pos, pos + len(t)
        if a < end and b > start:
            out.append(w)
        pos = b + 1
    return out


# --------------------------------------------------------------------------------------
# Post-processing (both backends)
# --------------------------------------------------------------------------------------

_EXPENSE_LINES = {"cost_of_sales", "operating_expenses", "finance_costs", "income_tax"}


@register_postprocessor(doc_types={DOC_TYPE})
def postprocess_fs(result: ExtractionResult, ctx: CaseContext, doc: Document) -> None:
    scale_cell = result.fields["scale_printed"]
    scale = Decimal(scale_cell.value or "1")
    if scale_cell.value is None:
        result.fields["scale_printed"] = Cell(
            value="1", kind="enum", source="derived", verified=None
        )
        if "scale_not_printed" not in result.warnings:
            result.warnings.append("scale_not_printed")
    for tname in TABLES:
        table = result.tables[tname]
        subtotals = set(FS_SUBTOTAL_LINES[tname])
        for row in table.rows:
            line = row.cells["line"].value or "other"
            row.cells["is_subtotal"] = Cell(
                value="true" if line in subtotals else "false",
                kind="bool",
                source="derived",
                verified=None,
            )
            for col in ("current", "prior"):
                cell = row.cells[col]
                value = cell.money()
                if value is None:
                    continue
                if (
                    tname == "income_statement"
                    and line in _EXPENSE_LINES
                    or tname == "balance_sheet"
                    and line != "retained_earnings"
                ):
                    value = abs(value)
                row.cells[col] = cell.model_copy(update={"value": fmt_money(value * scale)})


# --------------------------------------------------------------------------------------
# Validation (identities in printed units; decides only whether to escalate)
# --------------------------------------------------------------------------------------


# (table, total line, components (line, sign), required components)
_IDENTITIES: list[tuple[str, str, list[tuple[str, int]], set[str]]] = [
    (
        "income_statement",
        "gross_profit",
        [("revenue", 1), ("cost_of_sales", -1)],
        {"revenue", "cost_of_sales"},
    ),
    (
        "income_statement",
        "profit_before_tax",
        [
            ("gross_profit", 1),
            ("other_income", 1),
            ("operating_expenses", -1),
            ("finance_costs", -1),
        ],
        {"gross_profit"},
    ),
    (
        "income_statement",
        "net_profit",
        [("profit_before_tax", 1), ("income_tax", -1)],
        {"profit_before_tax"},
    ),
    (
        "balance_sheet",
        "total_assets",
        [("total_current_assets", 1), ("total_non_current_assets", 1)],
        {"total_current_assets", "total_non_current_assets"},
    ),
    (
        "balance_sheet",
        "total_assets",
        [("total_liabilities_and_equity", 1)],
        {"total_liabilities_and_equity"},
    ),
    (
        "balance_sheet",
        "total_liabilities_and_equity",
        [("total_liabilities", 1), ("total_equity", 1)],
        {"total_liabilities", "total_equity"},
    ),
    (
        "balance_sheet",
        "total_liabilities",
        [("total_current_liabilities", 1), ("total_non_current_liabilities", 1)],
        {"total_current_liabilities", "total_non_current_liabilities"},
    ),
    (
        "cash_flow",
        "net_change_in_cash",
        [
            ("net_cash_from_operating", 1),
            ("net_cash_from_investing", 1),
            ("net_cash_from_financing", 1),
        ],
        {"net_cash_from_operating", "net_cash_from_investing", "net_cash_from_financing"},
    ),
    (
        "cash_flow",
        "cash_closing",
        [("cash_opening", 1), ("net_change_in_cash", 1)],
        {"cash_opening", "net_change_in_cash"},
    ),
]
# (table, total line, section whose non-total lines must sum to it)
_SECTION_SUMS = [
    ("balance_sheet", "total_current_assets", "current_assets"),
    ("balance_sheet", "total_non_current_assets", "non_current_assets"),
    ("balance_sheet", "total_current_liabilities", "current_liabilities"),
    ("balance_sheet", "total_non_current_liabilities", "non_current_liabilities"),
    ("balance_sheet", "total_equity", "equity"),
    ("cash_flow", "net_cash_from_operating", "operating"),
    ("cash_flow", "net_cash_from_investing", "investing"),
    ("cash_flow", "net_cash_from_financing", "financing"),
]


def _magnitude(table: str, line: str, value: Decimal) -> Decimal:
    if table == "income_statement" and line in _EXPENSE_LINES:
        return abs(value)
    if table == "balance_sheet" and line != "retained_earnings":
        return abs(value)
    return value


@register_validator(doc_types={DOC_TYPE})
def validate_fs(result: ExtractionResult, ctx: CaseContext, doc: Document) -> Validation:
    """Printed identities, evaluated in printed units (so ``in thousands`` rounding is tolerated)."""
    v = Validation()
    if not any(t.rows for t in result.tables.values()):
        v.structural.append("no statement rows")
        return v
    base_tol: Decimal = (
        ctx.settings.money_tolerance
        if (result.fields["scale_printed"].value or "1") == "1"
        else Decimal(0)
    )

    def fail(table: str, row_index: int) -> None:
        pages = result.row_pages.get(table) or []
        v.failing.setdefault(
            pages[row_index] if row_index < len(pages) else doc.page_start, "identity"
        )

    def holds(total: Decimal, parts: list[Decimal]) -> bool:
        return abs(total - sum(parts, Decimal(0))) <= max(
            base_tol, Decimal(1), Decimal(len(parts) + 1) / 2
        )

    for col in ("current", "prior"):
        found: dict[str, dict[str, tuple[Decimal, int]]] = {}
        for tname in TABLES:
            found[tname] = {}
            for i, row in enumerate(result.tables[tname].rows):
                val = row.cells[col].money()
                line = row.cells["line"].value or "other"
                if val is not None and line != "other":
                    found[tname].setdefault(line, (_magnitude(tname, line, val), i))
        for tname, total, comps, required in _IDENTITIES:
            lines = found[tname]
            if total not in lines or not required <= set(lines):
                continue
            parts = [sign * lines[k][0] for k, sign in comps if k in lines]
            if not holds(lines[total][0], parts):
                fail(tname, lines[total][1])
        for tname, total, sect in _SECTION_SUMS:
            table = result.tables[tname]
            lines = found[tname]
            if total not in lines:
                continue
            parts, keyed = [], True
            for row in table.rows:
                line = row.cells["line"].value or "other"
                val = row.cells[col].money()
                if row.cells["section"].value != sect or line == total or val is None:
                    continue
                if line in FS_SUBTOTAL_LINES[tname]:
                    continue
                parts.append(_magnitude(tname, line, val))
                keyed = keyed and line != "other"
            if parts and keyed and not holds(lines[total][0], parts):
                fail(tname, lines[total][1])
    for page, reason in unverified_failures(result, doc).items():
        v.failing.setdefault(page, reason)
    v.missing_required = missing_required(result, DOC_TYPE, ignore={"period_start"})
    return v


register_prompt_hints(
    DOC_TYPE,
    "This page holds one or more financial statements (income statement, balance sheet, cash "
    "flow). Return one row per printed line that carries a figure, in the table of its statement. "
    "`label` is the printed row label; `current` is the figure in the later-year column and "
    "`prior` the earlier-year column, exactly as printed (brackets and 'in thousands' scaling are "
    "kept as printed: do not multiply, negate or convert). `line` is the canonical key that "
    "best fits the label (`other` if none fits) and `section` the section heading the row sits "
    "under. `scale_printed` is the units statement such as 'in thousands' or S$'000 if one is printed.",
)
