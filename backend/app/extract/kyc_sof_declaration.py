"""Source-of-funds declaration text-layer extractor (ingest-and-extraction.md 6.8, data-model 6.10).

Declarations are partly a form (labelled lines) and partly a sentence ("... declare that the amount
of S$1,000,000.00 represents sales proceeds from Orchid Bay Holdings Pte. Ltd. under invoice
INV-2026-0117 received between 01 Jul 2026 and 31 Aug 2026 ..."). Labels win; the sentence fills
what no label gave. ``declared_amount`` is the first money token after ``amount of`` / ``sum of``.
``origin_description`` is the first 500 characters of the declaration body (the first prose block),
copied as printed. ``origin_category`` accepts the printed category in any case with spaces or
underscores (``Sales proceeds`` -> ``sales_proceeds``); a category outside the list is left empty with
an ``unparsed:`` warning, never mapped to ``other`` by guessing.

The declaration is the applicant's own statement: nothing here says it is true.
"""

from __future__ import annotations

import re

from app.core.models import Cell, Table, Word
from app.core.schemas import DOC_SCHEMAS
from app.extract.base import LayoutNotRecognised
from app.extract.kyc_common import FOOTER, Form, currency_cell, flat_of, make_validator
from app.extract.normalise import DATE_FRAGMENT, format_warning
from app.extract.registry import register_extractor, register_prompt_hints
from app.extract.text_layer import DocView, FlatText, TextLayerExtractor, flat_text, words_text

DOC_TYPE = "source_of_funds_declaration"

LABELS = {
    "declarant_name": [
        r"^Declarant(?:'?s)?(?:\s+Name)?",
        r"^Name\s+of\s+Declarant",
        r"^Declared\s+by",
        r"^Full\s+Name",
        r"^Name(?!\s+of\s+(?:Company|Entity|Business|Buyer|Customer|Payer|Remitter|Counterparty))",
    ],
    "company_name": [
        r"^Company(?:\s+Name)?(?!\s+Account)",  # "Company account" is a bank-slot value
        r"^Entity(?:\s+Name)?",
        r"^Name\s+of\s+(?:Company|Entity|Business)",
        r"^On\s+behalf\s+of",
    ],
    "uen": [r"^UEN", r"^Co\.?\s*Reg(?:istration)?\.?\s*No\.?"],
    "declaration_date": [
        r"^Declaration\s+Date",
        r"^Date\s+of\s+Declaration",
        r"^Declared\s+on",
        r"^Date(?!\s+of\s+Birth)",
    ],
    "declared_amount": [
        r"^Declared\s+Amount",
        r"^Amount\s+Declared",
        r"^Amount\s+of\s+Funds",
        r"^Total\s+Amount",
        r"^Amount",
    ],
    "currency": [r"^Currency"],
    "origin_category": [
        r"^(?:Source|Origin)\s+of\s+(?:Funds|Wealth)(?:\s+Category)?",
        r"^(?:Source|Origin)\s+Category",
        r"^Category",
        r"^Nature\s+of\s+Funds",
    ],
    "origin_description": [
        r"^Description(?:\s+of\s+(?:the\s+)?(?:Funds|Source|Origin))?",
        r"^(?:Origin|Source)\s+(?:of\s+Funds\s+)?Description",
        r"^Description\s+of\s+(?:the\s+)?(?:Source|Origin)",
        r"^Details\s+of\s+(?:the\s+)?(?:Source|Origin)",
        r"^Explanation",
    ],
    "counterparty_name": [
        r"^Counterparty(?:\s+Name)?",
        r"^(?:Buyer|Payer|Remitter)(?:\s+Name)?",
        r"^Customer\s+Name",
        r"^Received\s+from",
        r"^Paid\s+by",
        r"^Name\s+of\s+(?:Buyer|Customer|Payer|Remitter|Counterparty)",
    ],
    "invoice_reference": [
        r"^Invoice\s+(?:Reference|Ref\.?|No\.?|Number)",
        r"^Related\s+Invoice",
        r"^Supporting\s+Invoice",
    ],
    "period": [
        r"^(?:Receipt\s+|Receiving\s+)?(?:Period|Window)",
        r"^Received\s+(?:between|during|in)",
    ],
    "period_start": [
        r"^(?:Period|Window)\s+(?:Start|From)",
        r"^Funds\s+received\s+from",
    ],
    "period_end": [
        r"^(?:Period|Window)\s+(?:End|To)",
        r"^Funds\s+received\s+to",
    ],
    "receiving_account": [
        r"^(?:Receiving|Credited|Deposit|Beneficiary)\s+Account(?:\s+(?:No\.?|Number))?",
        r"^Account\s+(?:No\.?|Number)",
        r"^Bank\s+Account(?:\s+(?:No\.?|Number))?",
    ],
}

ACCOUNT_NO = re.compile(r"^\d[\d-]{5,}\d$")
_CUR = r"(?:S\$|US\$|SGD|USD|\$)"
_NUM = r"\d[\d,]*(?:\.\d{1,2})?"
_AMOUNT_PROSE = re.compile(
    r"(?:amount|sum)\s+of\s+(?:(?P<cur>" + _CUR + r")\s*(?P<num>" + _NUM + r")"
    r"|(?P<num2>" + _NUM + r")\s*(?P<cur2>SGD|USD)\b)",
    re.I,
)
_DATE_PAT = DATE_FRAGMENT.pattern
_PERIOD_PROSE = re.compile(
    r"(?P<d1>" + _DATE_PAT + r")\s*(?:to|until|through|and|[-–—])\s*(?P<d2>" + _DATE_PAT + r")",
    re.I,
)
_INVOICE_PROSE = re.compile(
    r"\binvoice(?:\s+(?:no\.?|number|ref(?:erence)?\.?))?\s*:?\s*(?P<v>[A-Z]{2,5}-[A-Z0-9][A-Z0-9-]*)",
    re.I,
)
_ACCOUNT_PROSE = re.compile(
    r"\baccount(?:\s+(?:no\.?|number))?\s*:?\s*(?:#\s*)?(?P<v>\d[\d-]{5,}\d)", re.I
)
_PROSE_SENTENCE = re.compile(
    r"(?:source|origin)\s+of\s+(?:the\s+)?(?:these\s+)?(?:funds|wealth)\s+(?:is|are|being)\s*:?\s*"
    r"(?P<v>[A-Za-z][A-Za-z_ \-]{2,40}?)(?=\s*(?:[.,;]|\bfrom\b|\breceived\b|\bpaid\b|\bunder\b|$))",
    re.I,
)
_PROSE_CATEGORY = re.compile(
    r"\b(?:represents?|constitutes?|comprises?|are|is|being|as)\s+(?:the\s+)?"
    r"(?P<v>sales\s+proceeds|investment\s+returns|asset\s+sale|loan\s+proceeds|salary|inheritance)\b",
    re.I,
)
_DECLARANT_PROSE = re.compile(r"\bI,\s+(?P<v>[A-Z][A-Za-z'.\- ]{2,60}?)\s*,")
_COMPANY_END = {
    "ltd",
    "ltd.",
    "limited",
    "llp",
    "inc",
    "inc.",
    "corp",
    "corp.",
    "corporation",
    "bhd",
    "bhd.",
    "co",
    "co.",
    "company",
    "holdings",
    "group",
    "trading",
    "capital",
    "ventures",
    "imports",
}
_PROSE_WORDS = re.compile(r"fund|proceed|sale|receiv|declar|origin|source|invoice|amount", re.I)

_CATEGORY_SYNONYMS = {
    "sales": "sales_proceeds",
    "sale_proceeds": "sales_proceeds",
    "proceeds_from_sales": "sales_proceeds",
    "proceeds_of_sales": "sales_proceeds",
    "investment_return": "investment_returns",
    "investment_income": "investment_returns",
    "loan_proceeds": "loan",
    "asset_sales": "asset_sale",
    "sale_of_asset": "asset_sale",
    "sale_of_assets": "asset_sale",
}


def map_category(printed: str) -> str | None:
    values = DOC_SCHEMAS[DOC_TYPE].fields["origin_category"].values or []
    key = re.sub(r"[^a-z]+", "_", printed.casefold()).strip("_")
    if key in values:
        return key
    mapped = _CATEGORY_SYNONYMS.get(key)
    return mapped if mapped in values else None


def _company_name_after_from(tokens: list[str]) -> int:
    """Number of leading tokens that form a company name ending in a legal-form word, else 0."""
    best = 0
    for i, tok in enumerate(tokens[:9]):
        bare = tok.strip(",;:()")
        if not (bare[:1].isupper() or bare in ("&", "and", "of", "the")):
            break
        if bare.casefold() in _COMPANY_END or bare.casefold().rstrip(".") in _COMPANY_END:
            best = i + 1
            if bare.casefold().rstrip(".") in ("ltd", "limited", "llp", "inc", "bhd"):
                break
    return best


@register_extractor(doc_types={DOC_TYPE}, backend="text_layer")
class SourceOfFundsTextLayer(TextLayerExtractor):
    doc_types = frozenset({DOC_TYPE})
    name = "text_layer"
    version = "1"

    def parse(self, view: DocView, fields: dict[str, Cell], tables: dict[str, Table]) -> None:
        form = Form(view, DOC_TYPE, LABELS)
        form.put_all(
            fields,
            [
                "declarant_name",
                "company_name",
                "uen",
                "declaration_date",
                "currency",
                "counterparty_name",
                "invoice_reference",
                "period_start",
                "period_end",
            ],
        )
        self._account(view, form, fields)
        amount_hit = form.put(fields, "declared_amount")
        if amount_hit is not None:
            currency_cell(view, fields, amount_hit.value_words)
        self._category(view, form, fields)
        self._description(view, form, fields)
        self._period(view, form, fields)
        self._prose(view, fields)
        found = sum(
            1
            for k in ("declarant_name", "declared_amount", "declaration_date", "origin_category")
            if fields[k].raw is not None
        )
        if found < 2:
            raise LayoutNotRecognised("fewer than two source-of-funds fields found")
        if fields["currency"].raw is None:
            view.warn("currency_not_printed")

    # ------------------------------------------------------------------ account
    @staticmethod
    def _account(view: DocView, form: Form, fields: dict[str, Cell]) -> None:
        """The account number printed after the label. A bank name may sit between the label and the
        number (``Receiving account  UOB-STYLE MOCK BANK  6323451406``): the number is the first
        word on the label's line that looks like one, else the labelled value as printed."""
        hit = form.first("receiving_account")
        if hit is None:
            return
        label_end = max(w.x + w.w for w in hit.label_words)
        same_line = [
            w
            for w in hit.line.words
            if w.x >= label_end - 1e-6 and ACCOUNT_NO.match(w.text.strip().strip(".,;:"))
        ]
        words = same_line[:1] or ([] if hit.how == "below" else hit.value_words)
        if hit.how == "below":
            words = [w for w in hit.value_words if ACCOUNT_NO.match(w.text.strip())][
                :1
            ] or hit.value_words
        fields["receiving_account"] = view.cell(words, "id", "fields.receiving_account")

    # ------------------------------------------------------------------ category
    @staticmethod
    def _category_cell(view: DocView, words: list[Word], printed: str) -> Cell:
        value = map_category(printed)
        if value is None:
            view.warn(format_warning("unparsed", "fields.origin_category", printed))
        return Cell(
            value=value,
            raw=printed[:500],
            kind="enum",
            box=view.region(words),
            source="text_layer",
            verified=True,
        )

    def _category(self, view: DocView, form: Form, fields: dict[str, Cell]) -> None:
        hits = form.hits("origin_category")
        # a heading such as "SOURCE OF FUNDS DECLARATION" also looks like the label: prefer the
        # first occurrence whose value is a known category, else the first occurrence
        chosen = next((h for h in hits if map_category(words_text(h.value_words))), None)
        if chosen is not None:
            fields["origin_category"] = self._category_cell(
                view, chosen.value_words, words_text(chosen.value_words)
            )
            return
        flat = flat_text(view)
        m = _PROSE_SENTENCE.search(flat.text) or _PROSE_CATEGORY.search(flat.text)
        if m:
            fields["origin_category"] = self._category_cell(
                view, flat.words_in(m.start("v"), m.end("v")), m.group("v").strip()
            )
        elif hits:  # printed, but not one of the categories: kept as read, warned
            fields["origin_category"] = self._category_cell(
                view, hits[0].value_words, words_text(hits[0].value_words)
            )

    # ------------------------------------------------------------------ description
    def _description(self, view: DocView, form: Form, fields: dict[str, Cell]) -> None:
        hit = form.first("origin_description")
        words: list[Word] = (
            form.extend(hit, max_extra=12) if hit is not None else self._body_block(view)
        )
        if not words:
            return
        text = " ".join(words_text(words).split())[:500]
        fields["origin_description"] = view.cell(
            words, "text", "fields.origin_description", raw_override=text
        )

    @staticmethod
    def _body_block(view: DocView) -> list[Word]:
        """First run of consecutive prose lines (7+ words each) that talks about funds."""
        runs: list[list[list[Word]]] = []
        for pv in view.pages:
            cur: list[list[Word]] = []
            last = -2
            for ln in pv.lines:
                prose = (
                    len(ln.words) >= 7
                    and len(ln.segs) == 1
                    and (ln.page, ln.index) not in view.claimed
                    and not FOOTER.search(ln.text)
                )
                if prose and ln.index == last + 1 and cur:
                    cur.append(ln.words)
                    last = ln.index
                elif prose:
                    if cur:
                        runs.append(cur)
                    cur, last = [ln.words], ln.index
                else:
                    if cur:
                        runs.append(cur)
                    cur, last = [], -2
            if cur:
                runs.append(cur)
        for run in runs:
            if _PROSE_WORDS.search(" ".join(words_text(line) for line in run)):
                return [w for line in run for w in line]
        return [w for line in runs[0] for w in line] if runs else []

    # ------------------------------------------------------------------ period
    @staticmethod
    def _period_cells(
        view: DocView, flat: FlatText, m: re.Match[str], fields: dict[str, Cell]
    ) -> None:
        for key, grp in (("period_start", "d1"), ("period_end", "d2")):
            if fields[key].raw is None:
                fields[key] = view.cell(
                    flat.words_in(m.start(grp), m.end(grp)),
                    "date",
                    f"fields.{key}",
                    raw_override=m.group(grp),
                )

    def _period(self, view: DocView, form: Form, fields: dict[str, Cell]) -> None:
        if fields["period_start"].raw is not None and fields["period_end"].raw is not None:
            return
        hit = form.first("period")
        if hit is not None:
            flat = flat_of(hit.value_words)
            m = _PERIOD_PROSE.search(flat.text)
            if m:
                self._period_cells(view, flat, m, fields)
                return
        flat_all = flat_text(view)
        m = _PERIOD_PROSE.search(flat_all.text)
        if m:
            self._period_cells(view, flat_all, m, fields)

    # ------------------------------------------------------------------ prose fallbacks
    def _prose(self, view: DocView, fields: dict[str, Cell]) -> None:
        flat = flat_text(view)
        text = flat.text
        if fields["declared_amount"].raw is None:
            m = _AMOUNT_PROSE.search(text)
            if m:
                num_grp, cur_grp = ("num", "cur") if m.group("num") else ("num2", "cur2")
                if m.group("cur") and m.group("num"):
                    span = (m.start("cur"), m.end("num"))
                    raw = text[span[0] : span[1]]
                else:
                    span = (m.start(num_grp), m.end(num_grp))
                    raw = m.group(num_grp)
                fields["declared_amount"] = view.cell(
                    flat.words_in(*span), "money", "fields.declared_amount", raw_override=raw
                )
                if fields["currency"].raw is None and m.group(cur_grp):
                    fields["currency"] = view.cell(
                        flat.words_in(m.start(cur_grp), m.end(cur_grp)),
                        "enum",
                        "fields.currency",
                        raw_override=m.group(cur_grp),
                    )
        if fields["declarant_name"].raw is None:
            m = _DECLARANT_PROSE.search(text)
            if m:
                fields["declarant_name"] = view.cell(
                    flat.words_in(m.start("v"), m.end("v")),
                    "text",
                    "fields.declarant_name",
                    raw_override=m.group("v"),
                )
        if fields["company_name"].raw is None:
            m = re.search(r"\b(?:director|owner|shareholder|officer|partner)\s+of\s+", text, re.I)
            if m:
                tokens = text[m.end() :].split()
                n = _company_name_after_from(tokens)
                if n:
                    name = " ".join(tokens[:n]).rstrip(",;:")
                    fields["company_name"] = view.cell(
                        flat.words_in(m.end(), m.end() + len(name)),
                        "text",
                        "fields.company_name",
                        raw_override=name,
                    )
        if fields["invoice_reference"].raw is None:
            m = _INVOICE_PROSE.search(text)
            if m:
                fields["invoice_reference"] = view.cell(
                    flat.words_in(m.start("v"), m.end("v")),
                    "id",
                    "fields.invoice_reference",
                    raw_override=m.group("v"),
                )
        if fields["receiving_account"].raw is None:
            m = _ACCOUNT_PROSE.search(text)
            if m:
                fields["receiving_account"] = view.cell(
                    flat.words_in(m.start("v"), m.end("v")),
                    "id",
                    "fields.receiving_account",
                    raw_override=m.group("v"),
                )
        if fields["counterparty_name"].raw is None:
            self._counterparty(view, flat, fields)

    @staticmethod
    def _counterparty(view: DocView, flat: FlatText, fields: dict[str, Cell]) -> None:
        for m in re.finditer(
            r"\b(?:proceeds|funds|payment|payments|received)\s+from\s+", flat.text, re.I
        ):
            tail = flat.text[m.end() :]
            tokens = tail.split()
            n = _company_name_after_from(tokens)
            if not n:
                continue
            name = " ".join(tokens[:n]).rstrip(",;:")
            start = m.end()
            fields["counterparty_name"] = view.cell(
                flat.words_in(start, start + len(name)),
                "text",
                "fields.counterparty_name",
                raw_override=name,
            )
            return


make_validator(DOC_TYPE)

register_prompt_hints(
    DOC_TYPE,
    "This is the applicant's own source-of-funds declaration; transcribe it, do not judge it. "
    "`declared_amount` is the amount the declaration says the funds total, copied with its "
    "currency symbol (for example `S$1,000,000.00`). `origin_category` is the category the "
    "declaration names, one of: sales_proceeds, salary, investment_returns, inheritance, loan, "
    "asset_sale, other (return the printed words; null if none is named). `origin_description` is "
    "the first 500 characters of the declaration's explanatory text, verbatim. "
    "`counterparty_name` is the buyer or source the declaration names, `invoice_reference` an "
    "invoice number it cites, `period_start`/`period_end` the dates between which it says the funds "
    "were received, `receiving_account` the account number that received them.",
)
