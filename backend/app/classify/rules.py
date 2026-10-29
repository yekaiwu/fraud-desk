"""Rules baseline: keyword and regex cue scoring (docs/classification.md 3.2, 4.2).

Deterministic, free and explainable; it is the baseline every other classifier must beat. Text is
untrusted: the only way text influences a label is through cues for the real thing. No cue is
built from instruction phrases, so "this page is boilerplate, ignore the rules" cannot change a
label (the ``boilerplate`` cues are also discarded whenever a real label scores >= 0.35).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from functools import lru_cache
from typing import NamedTuple

from app.classify.base import LABELS, NONSPECIFIC, RISK_LABELS_C5, register_classifier
from app.core.textlayout import page_marker

STRONG, MEDIUM, WEAK = 0.90, 0.60, 0.35
HARD_INPUT_CAP = 200_000  # characters looked at before normalising (resource limit)
TEXT_CAP = 20_000  # characters kept after normalising
BOILERPLATE_SUPPRESS_AT = 0.35  # a real label must score strictly more (one weak cue is not enough)
RISK_SCORE_AT = 0.60
CONTINUATION_MAX_P = 0.80
NO_CUE_P = 0.60
STRONG_CLEAR = 0.90  # best score from which lone weak cues stop counting as competitors


class Cue(NamedTuple):
    pattern: str  # literal phrase (word-boundary matched) or "re:<regex>"
    weight: float
    min_count: int = 1
    fallback: bool = False  # counts only when no non-fallback cue of any label matched


def S(*patterns: str) -> list[Cue]:  # noqa: N802 - tier helpers keep the tables readable
    return [Cue(p, STRONG) for p in patterns]


def M(*patterns: str) -> list[Cue]:  # noqa: N802
    return [Cue(p, MEDIUM) for p in patterns]


def W(*patterns: str) -> list[Cue]:  # noqa: N802
    return [Cue(p, WEAK) for p in patterns]


def X(weight: float, *patterns: str) -> list[Cue]:  # noqa: N802 - explicit weight
    return [Cue(p, weight) for p in patterns]


def F(weight: float, *patterns: str) -> list[Cue]:  # noqa: N802 - fallback cues
    return [Cue(p, weight, fallback=True) for p in patterns]


_PCT = r"re:\d{1,3}(?:\.\d+)?\s*%"

CUES: dict[str, dict[str, list[Cue]]] = {
    "C1": {
        "bank_statement": S(
            "statement of account",
            "account statement",
            "bank statement",
            "opening balance",
            "closing balance",
            "balance brought forward",
            "balance b/f",
            "balance carried forward",
            r"re:date\s+(?:description|transaction details|particulars)\s+(?:debit|withdrawals?)",
        )
        + M(
            "withdrawals",
            "deposits",
            "account number",
            "account no",
            "statement period",
            "total debits",
            "total credits",
            "current account",
        )
        + W(r"re:page \d+ of \d+", "transaction"),
        # integration-loan: "invoice date" must open a line (a contract says "from invoice date"),
        # and the table vocabulary every priced document shares (a PO has subtotal, unit price, GST
        # reg no) is fallback-only so it cannot make an invoice a rival of a purchase order.
        "invoice": S("tax invoice", "invoice number", "invoice no", r"re:^invoice date\b")
        + M("bill to", "amount due", "due date")
        + W("payment terms")
        + F(
            0.60,
            "gst reg",
            "gst registration",
            "subtotal",
            "unit price",
            "qty",
            r"re:gst\s*\(?9",
        ),
        "financial_statement": S(
            "balance sheet",
            "statement of financial position",
            "income statement",
            "statement of comprehensive income",
            "profit and loss",
            "statement of cash flows",
            "cash flow statement",
            "independent auditor",
            "notes to the financial statements",
        )
        + M(
            "total assets",
            "total liabilities",
            "total equity",
            "retained earnings",
            "net profit",
            "profit before tax",
            "current assets",
            "revenue",
        )
        + W("year ended", "in thousands", "s$'000"),
        "contract": S(
            "this agreement is made",
            "contract number",
            "contract value",
            "contract sum",
            "hereinafter",
            "witness whereof",
        )
        + M("agreement", "parties", "governing law", "effective date", "signed by", "scope of work")
        + W("terms and conditions", "clause"),
        "purchase_order": S(r"re:^purchase order\b", "po number", "p.o. number", "po no")
        + M("ship to", "delivery date", "order date", "please deliver")
        + W("buyer", "supplier"),
        "company_profile": S(
            "business profile", "bizfile", "company profile", "business profile of"
        )
        + M(
            "ssic",
            "registered address",
            "principal activities",
            r"re:principal activit(?:y|ies)",
            "officers",
            "shareholders",
            "status live",
        )
        # "entity type" and "date of incorporation" are printed on the certificate too: not cues
        + W("accounting and corporate regulatory authority", "acra"),
        "certificate_of_incorporation": S(
            "certificate of incorporation",
            "hereby certify",
            "is this day incorporated",
            "incorporated under the companies act",
        )
        + M("certificate number", "companies act")
        + W(
            "private company limited by shares",  # also the "Entity type" row of a profile
            "accounting and corporate regulatory authority",
            "acra",
        ),
        "ownership_chart": S(
            "ownership chart",
            "ownership structure",
            "shareholding structure",
            "group structure",
            "ultimate beneficial owner",
            r"re:\bubo\b",
        )
        + M("beneficial owner", "holding company")
        + W(_PCT),  # percentages are printed on profiles and registers too: never a rival reading
        "shareholder_register": S(
            "register of members",
            "register of shareholders",
            "shareholder register",
            "register of allotments",
        )
        + M(
            "no. of shares",
            "share class",
            "ordinary shares",
            "date acquired",
            "total shares",
        )
        + W("holder", "percent", "shares held"),
        "source_of_funds_declaration": S(
            "source of funds declaration",
            "declaration of source of funds",
            "origin of funds",
            "source of funds",
        )
        + M("i hereby declare", "declared amount", "funds originate", "sale proceeds", "the funds")
        + W("declare", "declaration"),
        "national_id": S("national registration identity card", "identity card", "nric")
        # an ID number also appears in profiles and registers: it counts only on a page with no
        # other cue at all (a bare number), never as a rival of the real document type
        + F(MEDIUM, r"re:\b[stfgm]\d{7}[a-z]\b")
        # printed on passports and certificates as well: weak next to the card's own title
        + W("date of birth", "race", "republic of singapore", "address"),
        "passport": S("passport")
        + X(0.95, r"re:^p<[a-z]{3}[a-z<]{39}$")
        + M(
            "date of expiry",
            "place of birth",
            "surname",
            "given names",
            "country code",
            "type p",
        )
        + F(WEAK, "sex", "nationality"),
        "boilerplate": S(
            "this page is intentionally left blank",
            "privacy policy",
            "data protection notice",
            "table of contents",
            "disclaimer",
            "important notice",
            "cover page",
        )
        + M("terms and conditions", "general terms", "notes to users")
        + W("page left blank"),
        "other": [],
    },
    # C3 bank cues; software_generated is computed in _c3_scores
    "C3": {
        "dbs": S("dbs bank", "dbs.com", r"re:\bdbs\b") + M("posb"),
        "ocbc": S("ocbc bank", "oversea-chinese banking corporation", "ocbc.com", r"re:\bocbc\b"),
        "uob": S("united overseas bank", "uob.com", r"re:\buob\b"),
        "other_bank": S(
            "standard chartered",
            "hsbc",
            "citibank",
            "maybank",
            "bank of china",
            "rhb",
            "cimb",
            "ing bank",
        ),
        "software_generated": [],
    },
    "C4": {
        "trade_receipt": S(
            "invoice",
            r"re:\binv[\s#.:-]*\d",
            r"re:\bpo[\s#.:-]*\d",
            "purchase order",
            r"re:payment for (?:goods|services|invoice)",
            "settlement of",
            "sales",
        )
        + M("trade", "goods", "services rendered", "delivery"),
        "customer_payment": F(
            0.75,
            "fast payment from",
            "paynow from",
            "giro from",
            "inward remittance",
            "inward transfer",
            "payment from",
            # generator/bank phrasings of an inbound customer credit (integration-loan)
            "giro credit",
            "funds transfer from",
            "retainer fee",
            "cheque deposit",
            "chq dep",
            "cash deposit",
            "received from",
            "fund transfer from",
        ),
        "financing_proceeds": S(
            r"re:(?<!director )(?<!directors )(?<!director's )(?<!directors' )(?<!shareholder )(?<!dir )\bloan\b",
            "drawdown",
            "draw down",
            "facility",
            r"re:facilities\b",
            "financing",
            "disbursement",
            "term loan",
            "revolving",
            "credit line",
            "working capital",
            "trade finance",
            "supply chain finance",
            "bridging",
        ),
        "owner_transfer": S(
            "director",
            r"re:directors?'?s?\s+loan",
            "shareholder",
            "capital injection",
            "share capital",
            "paid-up",
            "paid up",
            "proprietor",
            "owner",
            "own account",
            "self transfer",
            "capital contribution",
        ),
        "inter_company": S(
            "intercompany",
            "inter-company",
            r"re:\bic (?:transfer|settlement)\b",
            "related company",
            "related party",
            "affiliate",
            "subsidiary",
            "holding company",
            "group company",
        ),
        "refund": S(
            "refund",
            "reversal",
            "reversed",
            "chargeback",
            r"re:returned (?:payment|funds)",
            "credit note",
            "cancellation",
            "cancelled",
        ),
        "other": [],
    },
    "C5": {
        "crypto_virtual_assets": X(
            0.95,
            "crypto",
            "bitcoin",
            r"re:\bbtc\b",
            "ethereum",
            r"re:\beth\b",
            "usdt",
            "tether",
            "stablecoin",
            "binance",
            "coinbase",
            "kraken",
            "bybit",
            "okx",
            "huobi",
            "kucoin",
            "bitfinex",
            "crypto.com",
            "coinhako",
            "independent reserve",
            "luno",
            "virtual asset",
            "digital asset",
            r"re:\bdefi\b",
            r"re:\bnft\b",
            "blockchain",
            "web3",
        )
        + X(0.75, "coin", "token", "digital currency"),
        "gambling": X(
            0.95,
            "casino",
            r"re:\bbet(?:s|ting)?\b",
            "sportsbook",
            "bookmaker",
            "poker",
            "lottery",
            "lotto",
            "toto",
            r"re:\b4d\b",
            "jackpot",
            "wager",
            "sweepstake",
        )
        + X(0.75, "gaming"),
        "money_services": S(
            "remittance",
            r"re:\bremit",
            r"re:money ?chang(?:er|ers)",
            "western union",
            "moneygram",
            "transferwise",
            "wise payments",
            "ria money",
            "forex",
            "foreign exchange",
            "currency exchange",
            "money transfer",
            "payment service",
            "payment processor",
        ),
        "logistics": S(
            "logistics",
            "freight",
            "forwarding",
            "cargo",
            "shipping",
            "courier",
            "haulage",
            r"re:warehous(?:e|ing)",
            "supply chain",
        )
        + M("transport", "express", "marine", r"re:\blines?\b"),
        "trading": S(
            "trading",
            "commodity",
            "commodities",
            "import",
            "export",
            "wholesale",
            "distributor",
            "distribution",
            "supplies",
        )
        + M("merchant", "enterprise", "traders"),
        "manufacturing": S(
            r"re:\bmanufactur",
            r"re:\bfabricat",
            "industries",
            "industrial",
            "precision",
            "plastics",
        )
        + M("engineering", "machinery", "components", "electronics", "assembly"),
        "professional_services": S(
            "consulting",
            "consultancy",
            "consultants",
            "advisory",
            r"re:law (?:corporation|llp|firm)",
            "accountants",
            "accounting",
            "audit",
            "architects",
            r"re:\bllp\b",
            "legal",
        )
        + M("associates", "partners", "solutions", "design", "secretarial"),
        "other": X(
            0.90,
            "payroll",
            "salary",
            "cpf",
            "iras",
            "rent",
            "utilities",
            "service charge",
            "bank charges",
            "interest",
            "gst",
        )
        + X(0.75, "holdings", "capital", "ventures", "properties"),
    },
}

# C3 -------------------------------------------------------------------------
_GENERIC_BANK = re.compile(
    r"\bbank\s+(?:ltd|limited|berhad)\b|\bbank of [a-z]+(?: [a-z]+){0,3} (?:ltd|limited|berhad)\b"
)
SOFTWARE_PRODUCERS = (
    "microsoft word",
    "microsoft excel",
    "libreoffice",
    "openoffice",
    "google docs",
    "skia/pdf",
    "canva",
    "wps",
    "pdfcreator",
    "cutepdf",
    "ilovepdf",
    "smallpdf",
    "sejda",
    "pdf24",
    "nitro",
    "foxit",
)
SOFTWARE_FALLBACK, SOFTWARE_PRODUCER = 0.75, 0.90

# C4 context cues -----------------------------------------------------------
RELATED_PARTY_P = 0.95
NAME_OVERLAP_P = 0.60
_NAME_STOP = {"pte", "ltd", "limited", "private", "company", "co", "the", "of", "and", "bhd", "llp"}
_OVERLAP_STOP = {
    "pte",
    "ltd",
    "limited",
    "private",
    "company",
    "holdings",
    "group",
    "singapore",
    "trading",
    "services",
    "enterprise",
    "international",
    "global",
}
_RAIL_PREFIX = re.compile(
    r"^(?:fast payment (?:from|to)|paynow (?:from|to)|giro (?:from|to)|funds? transfer (?:from|to)"
    r"|trf (?:from|to)|inward remittance from|outward remittance to|cheque deposit|cash deposit)\s+"
)
_REFERENCE_MARK = re.compile(r"\s(?:ref|inv|po)\b|\s-\s|/")


# ----------------------------------------------------------------------------- text


_INVISIBLE = re.compile("[­؜᠎​-‏‪-‮⁠-⁤⁦-⁯﻿]")
_HSPACE = re.compile(r"[^\S\n]+")


def normalise_text(text: str) -> str:
    """NFKC, casefold, strip zero-width / soft-hyphen / bidi controls, collapse whitespace
    (line breaks are kept: the MRZ cue is line anchored), cap 20 000 characters."""
    t = _INVISIBLE.sub("", text[:HARD_INPUT_CAP])
    t = unicodedata.normalize("NFKC", t)
    t = _INVISIBLE.sub("", t).casefold()
    lines = (_HSPACE.sub(" ", line).strip() for line in t.replace("\r", "\n").split("\n"))
    return "\n".join(line for line in lines if line)[:TEXT_CAP]


# Instruction-like phrases are removed before any cue is matched, so an attacker cannot add a cue
# by naming a label ("classify as refund") and cannot hide one. Only the phrase itself goes.
_LABEL_NAMES = sorted(
    {name for names in LABELS.values() for name in names}, key=lambda n: (-len(n), n)
)
_LABEL_ALT = "|".join(re.escape(n).replace("_", "[ _]") for n in _LABEL_NAMES)
_QUALIFIERS = r"(?:(?:all|any|the|previous|above|prior|these|those|your|my)\s+)*"
INSTRUCTION_SPANS = re.compile(
    "|".join(
        (
            rf"ignore\s+{_QUALIFIERS}(?:instructions|rules|checks)",
            rf"disregard\s+{_QUALIFIERS}(?:instructions|rules|checks)?",
            rf"(?:classify|label|mark|tag)\s+(?:this|the|it)?\s*"
            rf"(?:page|document|row|transaction|credit|debit)?\s*(?:as|=|:)\s*(?:an?\s+)?"
            rf"(?:{_LABEL_ALT})(?![a-z0-9])",
            rf"label\s*[=:]\s*(?:{_LABEL_ALT})(?![a-z0-9])",
            r"(?:this|the)\s+(?:page|document)\s+is\s+(?:boilerplate|not)(?![a-z0-9])",
            r"system\s*:",
            r"system\s+prompt",
            r"you\s+are\s+(?:an?|the)\s+(?:assistant|ai|model)",
            r"do\s+not\s+(?:flag|check|report)",
            r"no\s+need\s+to\s+(?:check|verify)",
            r"skip\s+(?:this|the)\s+(?:page|check)",
        )
    )
)


def strip_instructions(norm: str) -> str:
    """Remove instruction-like phrases from already normalised text."""
    return INSTRUCTION_SPANS.sub(" ", norm)


@lru_cache(maxsize=2048)
def _compile(pattern: str) -> re.Pattern[str]:
    if pattern.startswith("re:"):
        return re.compile(pattern[3:], re.MULTILINE)
    return re.compile(rf"(?<![a-z0-9]){re.escape(pattern)}(?:s|es)?(?![a-z0-9])")


def _count(pattern: str, text: str) -> int:
    return sum(1 for _ in _compile(pattern).finditer(text))


def _satisfied(cue: Cue, text: str) -> bool:
    if cue.min_count <= 1:
        return _compile(cue.pattern).search(text) is not None
    return _count(cue.pattern, text) >= cue.min_count


def _combine(weights: Sequence[float]) -> float:
    miss = 1.0
    for w in weights:
        miss *= 1.0 - w
    return round(1.0 - miss, 10)


def _table_scores(
    question: str, text: str, extra: dict[str, list[float]] | None = None
) -> dict[str, float]:
    """Noisy-or score per label of the question's cue table. ``extra`` holds context cues (already
    satisfied). Fallback cues count only when no non-fallback cue of any label matched."""
    table = CUES[question]
    hits: dict[str, list[float]] = {label: list((extra or {}).get(label, [])) for label in table}
    for label, cues in table.items():
        for cue in cues:
            if not cue.fallback and _satisfied(cue, text):
                hits[label].append(cue.weight)
    scores = {label: _combine(w) if w else 0.0 for label, w in hits.items()}
    if not any(hits.values()):
        for label, cues in table.items():
            # alternative phrasings of one piece of evidence: the strongest counts, not their sum
            found = [c.weight for c in cues if c.fallback and _satisfied(c, text)]
            if found:
                scores[label] = max(found)
    return scores


def _tokens(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name)


def _counterparty(text: str, context: dict[str, str]) -> str:
    given = normalise_text(context.get("counterparty", ""))
    if given:
        return given
    line = text.split("\n", 1)[0]
    line = _RAIL_PREFIX.sub("", line)
    cut = _REFERENCE_MARK.search(line)
    return line[: cut.start()] if cut else line


def _related_party_hit(counterparty: str, related: str) -> bool:
    cp = set(_tokens(counterparty)) - _NAME_STOP
    if not cp:
        return False
    for name in related.split("|"):
        tokens = set(_tokens(normalise_text(name))) - _NAME_STOP
        if not tokens:
            continue
        if (len(tokens) >= 2 or len(next(iter(tokens))) >= 5) and tokens <= cp:
            return True
    return False


def _name_overlap(counterparty: str, applicant: str) -> bool:
    cp = {t for t in _tokens(counterparty) if len(t) >= 4 and t not in _OVERLAP_STOP}
    me = {t for t in _tokens(applicant) if len(t) >= 4 and t not in _OVERLAP_STOP}
    return bool(cp & me)


def _c3_scores(text: str, context: dict[str, str]) -> dict[str, float]:
    scores = _table_scores("C3", text)
    named = max(scores.values(), default=0.0)
    generic = _GENERIC_BANK.search(text) is not None and named < MEDIUM
    if generic:
        scores["other_bank"] = _combine(
            [scores["other_bank"], MEDIUM] if scores["other_bank"] else [MEDIUM]
        )
    if named == 0.0 and not generic:
        producer = f"{context.get('producer', '')} {context.get('creator', '')}".casefold()
        known = any(p in producer for p in SOFTWARE_PRODUCERS)
        scores["software_generated"] = SOFTWARE_PRODUCER if known else SOFTWARE_FALLBACK
    return scores


def _c4_scores(text: str, context: dict[str, str]) -> dict[str, float]:
    extra: dict[str, list[float]] = {}
    counterparty = _counterparty(text, context)
    related = context.get("related_parties", "")
    if related and _related_party_hit(counterparty, related):
        extra["owner_transfer"] = [RELATED_PARTY_P]
    applicant = normalise_text(context.get("applicant_name", ""))
    if applicant and _name_overlap(counterparty, applicant):
        extra["inter_company"] = [NAME_OVERLAP_P]
    return _table_scores("C4", text, extra)


def cue_scores(
    question: str, text: str, labels: Sequence[str], context: dict[str, str]
) -> dict[str, float]:
    """Noisy-or cue score (0..1) for every requested label. Unknown labels score 0."""
    if question not in CUES:
        raise ValueError(f"unknown question: {question}")
    norm = strip_instructions(normalise_text(text))
    if question == "C3":
        scores = _c3_scores(norm, context)
    elif question == "C4":
        scores = _c4_scores(norm, context)
    else:
        scores = _table_scores(question, norm)
        if question == "C1" and any(
            s > BOILERPLATE_SUPPRESS_AT for label, s in scores.items() if label != "boilerplate"
        ):
            scores["boilerplate"] = 0.0
        if question == "C1" and title_veto(norm):
            scores = {label: 0.0 for label in scores}  # no cue left: ``other`` at 0.60
    return {label: scores.get(label, 0.0) for label in labels}


# ----------------------------------------------------------------------------- explanation

# Explanation only: these functions re-read the same cue tables to report which cues fired for the
# winning label. They never feed back into ``cue_scores`` or the distribution.
CUE_NAME_MAX = 40
_REGEX_NAMES: dict[str, str] = {
    _PCT: "a percentage figure",
    r"re:^p<[a-z]{3}[a-z<]{39}$": "a machine readable zone line",
    r"re:\b[stfgm]\d{7}[a-z]\b": "an identity number format",
    r"re:date\s+(?:description|transaction details|particulars)\s+(?:debit|withdrawals?)": (
        "a transaction table header"
    ),
}
_REGEX_COMPLEX = re.compile(r"[\\()\[\]{}|*+?$]")
_TIER_PREFIX = ("Found", "Also found", "Weaker hints")
_VETO_REASON = (
    "Title matches a document type that is recorded but not checked, such as a utility bill"
)
_NO_CUE_REASON = "No known keyword or pattern for any document type was found"


def cue_name(cue: Cue, label: str) -> str:
    """A short human-readable name for a cue: the quoted literal phrase, or a plain description of
    a pattern. Never text copied from the page (a pattern can match an identity number)."""
    if not cue.pattern.startswith("re:"):
        return f'"{cue.pattern[:CUE_NAME_MAX]}"'
    named = _REGEX_NAMES.get(cue.pattern)
    if named:
        return named
    body = cue.pattern[3:].replace("\\b", "").replace("^", "")
    body = re.sub(r"\\d\+?", "N", body).replace("\\s+", " ").replace("\\s*", " ")
    body = " ".join(body.split())
    if body and not _REGEX_COMPLEX.search(body):
        return f'"{body[:CUE_NAME_MAX]}"'
    return f"wording typical of {label.replace('_', ' ')}"


def _quoted(names: Sequence[str]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _fit(prefix: str, names: list[str], limit: int) -> str:
    while len(names) > 1 and len(f"{prefix} {_quoted(names)}") > limit:
        names = names[:-1]
    return f"{prefix} {_quoted(names)}"


def explain_label(
    question: str,
    text: str,
    label: str,
    context: dict[str, str] | None = None,
    *,
    max_items: int = 5,
    max_chars: int = 120,
) -> list[str]:
    """The cues that fired for ``label`` as short sentences, strongest first (at most
    ``max_items``, each at most ``max_chars``). Deterministic: the order follows the cue weights
    and then the order of the cue table. Supported for C1 (page) and C3 (issuer); empty otherwise.
    """
    if question not in ("C1", "C3") or label not in CUES.get(question, {}):
        return []
    norm = strip_instructions(normalise_text(text))
    if question == "C1" and title_veto(norm):
        return [_VETO_REASON[:max_chars]]
    table = CUES[question]
    any_regular = any(
        _satisfied(c, norm) for cues in table.values() for c in cues if not c.fallback
    )
    fired = [c for c in table[label] if _satisfied(c, norm) and (not c.fallback or not any_regular)]
    lines: list[str] = []
    if fired:
        weights = sorted({c.weight for c in fired}, reverse=True)
        for tier, weight in enumerate(weights):
            names = list(dict.fromkeys(cue_name(c, label) for c in fired if c.weight == weight))[:4]
            prefix = _TIER_PREFIX[min(tier, len(_TIER_PREFIX) - 1)]
            if all(c.fallback for c in fired):
                prefix = "Only general wording matched:"
            lines.append(_fit(prefix, names, max_chars))
    elif question == "C3":
        lines += _c3_extra_reasons(norm, label, context or {})
    elif not any(_satisfied(c, norm) for cues in table.values() for c in cues):
        lines.append(_NO_CUE_REASON)
    return [line[:max_chars] for line in lines][:max_items]


def _c3_extra_reasons(norm: str, label: str, context: dict[str, str]) -> list[str]:
    if label == "other_bank" and _GENERIC_BANK.search(norm) is not None:
        return ["Found a bank letterhead that is not DBS, OCBC or UOB"]
    if label == "software_generated":
        producer = f"{context.get('producer', '')} {context.get('creator', '')}".casefold()
        if any(p in producer for p in SOFTWARE_PRODUCERS):
            return ["No bank letterhead and the PDF was made by office or PDF software"]
        return ["No bank letterhead found, so the statement looks company-printed"]
    return []


# ----------------------------------------------------------------------------- distribution


def scores_to_distribution(
    scores: dict[str, float], labels: Sequence[str], *, risk_first: Sequence[str] = ()
) -> dict[str, float]:
    """Cue scores -> probabilities (classification.md 4.2). Sums to 1, keys = ``labels``.

    ``p[best] = s_best * (s_best / S)`` (ambiguity penalty). The rest goes to the other labels in
    proportion to their scores, but no other label may reach ``p[best]``: whatever it cannot take
    ("leftover") goes to ``other`` (so a page torn between two labels becomes ``other``, never a
    confident runner-up). With no cue at all: ``other`` 0.60, the rest uniform.
    """
    labels = list(labels)
    if not labels:
        return {}
    s = {label: min(1.0, max(0.0, scores.get(label, 0.0))) for label in labels}
    fallback = "other" if "other" in labels else ("other_bank" if "other_bank" in labels else None)
    total = sum(s.values())
    probs: dict[str, float] = {label: 0.0 for label in labels}
    # integration-loan fix (seam with the generator): lone weak cues shared by every layout
    # ("uen", "page 1 of 1", a percent sign) are not a competing reading. When the best label has
    # a strong cue (score >= STRONG_CLEAR), labels scoring at most WEAK stay out of the ambiguity
    # denominator, so a clearly labelled page is not dragged under the low-confidence threshold.
    best_score = max(s.values(), default=0.0)
    if best_score >= STRONG_CLEAR:
        total = sum(v for v in s.values() if v > WEAK)
    caps: dict[str, float] = {}

    if total <= 0:
        if fallback is None:
            share = round(1.0 / len(labels), 4)
            probs = {label: share for label in labels}
            best = labels[0]
        else:
            best = fallback
            rest = [label for label in labels if label != best]
            probs[best] = NO_CUE_P if rest else 1.0
            for label in rest:
                probs[label] = round((1.0 - NO_CUE_P) / len(rest), 4)
    else:
        best = max(labels, key=lambda label: (s[label], -labels.index(label)))
        waive = False
        for risk in risk_first:
            if risk in s and s[risk] >= RISK_SCORE_AT:
                best, waive = risk, True
                break
        p_best = round(s[best] if waive else s[best] * (s[best] / total), 4)
        probs[best] = p_best
        others = [label for label in labels if label != best]
        if not others:
            probs[best] = 1.0
        else:
            remaining = 1.0 - p_best
            mass = sum(s[label] for label in others)
            overflow = 0.0
            if mass > 0:
                for label in others:
                    # an earlier label that ties with best would win the argmax: keep it just below
                    ceiling = (
                        p_best if labels.index(label) > labels.index(best) else p_best - 0.0001
                    )
                    caps[label] = max(0.0, round(ceiling, 4))
                    share = remaining * s[label] / mass
                    probs[label] = round(min(share, caps[label]), 4)
                    overflow += share - probs[label]
            else:
                overflow = remaining
            sink = fallback if fallback is not None and fallback != best else best
            probs[sink] = round(probs[sink] + overflow, 4)
            if sink == best and mass == 0 and best == fallback:
                # nothing else scored and best is `other`: spread uniformly instead
                probs[best] = p_best
                for label in others:
                    probs[label] = round(remaining / len(others), 4)

    _fix_rounding(probs, best, fallback, caps)
    return probs


def _fix_rounding(
    probs: dict[str, float], best: str, fallback: str | None, caps: dict[str, float]
) -> None:
    """Put the 4-decimal rounding remainder where it cannot change the winner."""
    remainder = round(1.0 - sum(probs.values()), 4)
    if remainder == 0:
        return
    candidates: list[str] = []
    if fallback is not None and fallback != best:
        candidates.append(fallback)
    order = list(probs)
    candidates += sorted((k for k in probs if k != best), key=lambda k: (-probs[k], order.index(k)))
    for label in candidates:
        new = round(probs[label] + remainder, 4)
        if new >= 0 and (label == fallback or new <= caps.get(label, 1.0)):
            probs[label] = new
            return
    probs[best] = round(probs[best] + remainder, 4)


# ----------------------------------------------------------------------------- continuation


def continuation(
    text: str,
    own: dict[str, float],
    prev: tuple[str, float] | None,
    *,
    min_prob: float,
) -> dict[str, float] | None:
    """C1 continuation rule: a table-only page ``n > 1`` of a document inherits the previous page's
    label (at ``min(previous p, 0.80)``) when its own result is ``other`` or below ``min_prob``."""
    if prev is None:
        return None
    marker = page_marker(text)
    if marker is None or marker[0] <= 1:
        return None
    label, p_prev = prev
    if label in NONSPECIFIC["C1"]:
        return None
    own_label = max(own, key=lambda k: own[k]) if own else "other"
    if own_label not in NONSPECIFIC["C1"] and own.get(own_label, 0.0) >= min_prob:
        return None
    p = round(min(p_prev, CONTINUATION_MAX_P), 4)
    return {label: p, "other": round(1.0 - p, 4)}


# Plan 0.4 circle rows (board resolution, proof of address) are "received, not checked". Their
# vocabulary overlaps real types ("signed by" is a contract cue, "amount due" an invoice cue), so a
# title-anchored veto sends them to ``other``. It only looks at the first lines (the document's own
# title): the same words deep in a real invoice or contract change nothing. Matching is on the
# author's text, so a veto can only move a page to the visible, flagged ``other`` label.
VETO_TITLE_LINES = 3
C1_TITLE_VETO = re.compile(
    r"^(?:"
    r"(?:board|directors'?|shareholders'?|members'?|written)\s+resolutions?\b"
    r"|resolutions?\s+of\s+the\s+(?:board|directors|company|members|shareholders)\b"
    r"|(?:[\w&.'-]+\s+){0,4}(?:utility|utilities|electricity|water|gas|telephone|mobile|broadband"
    r"|internet|phone)\s+bills?\b"
    r"|proof\s+of\s+(?:address|residence)\b"
    r")",
    re.MULTILINE,
)


def title_veto(norm: str) -> bool:
    """True when one of the first lines of normalised text titles a plan 0.4 circle document."""
    head = "\n".join(norm.split("\n")[:VETO_TITLE_LINES])
    return C1_TITLE_VETO.search(head) is not None


@register_classifier(name="rules")
class RulesClassifier:
    name = "rules"
    version = "3"  # bump when any cue list or rule changes

    def classify(
        self,
        text: str,
        labels: Sequence[str],
        *,
        question: str,
        context: dict[str, str] | None = None,
    ) -> dict[str, float]:
        labels = list(labels)
        scores = cue_scores(question, text, labels, context or {})
        risk = RISK_LABELS_C5 if question == "C5" else ()
        return scores_to_distribution(scores, labels, risk_first=risk)

    def explain(
        self, text: str, label: str, *, question: str, context: dict[str, str] | None = None
    ) -> list[str]:
        """Why ``label`` won: the cues that fired (explanation only, see ``explain_label``)."""
        return explain_label(question, text, label, context)
