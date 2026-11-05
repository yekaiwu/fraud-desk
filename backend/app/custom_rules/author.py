"""The rule assistant: classify first, then draft, ideate and route (docs/custom-rules.md).

Every request an officer types starts with a classification: cheap deterministic pre-checks first (no
model call for an empty, forbidden, protected-attribute or injection-looking request), then one Claude Haiku
call that fills the closed schema of ``capabilities`` and returns a ``ModelDraft``. CODE, not the model,
decides the final status (``decide_status``): the model's status can be lowered, never raised, so a
"ready" with an unmapped phrase, an unsupported part, a question left open or an invalid spec is not ready.
Every sentence the officer reads about what the rule will and will not check is built by code from the spec
(``readback``), never from model prose.

What is too complex is helped and routed: an unsupported request gets a ticket for the tech team at once
(``tickets``); a partly supported one shows "This rule WILL check" and "It will NOT check" and the officer
may save the smaller rule and send the rest; a refused one (high severity, hard stop, protected attribute,
changing built-in checks or the score, injection, nonsense) is not ticketed, is refused with the reason and
is written to the audit log (a hash of the text, never the text).

The model never sees another account's rule text. The officer's words reach it only inside
``wrap_untrusted``. Authoring calls are charged to the signed-in account's daily spend with their own purpose
label (``custom_rule:draft``, ``custom_rule:repair``) and never filed on a case.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, StrictBool, ValidationError, model_validator

from app.audit import AuditLog
from app.catalogue import clean_reason
from app.core.config import Settings
from app.core.costs import BudgetLimits, CostMeter, DailySpend, LLMBudgetExceeded, PriceTable
from app.core.errors import ApiError, LLMError, LLMRefusal
from app.core.llm import LLMClient, LLMRequest, wrap_untrusted
from app.core.models import Strict, clean_text
from app.custom_rules import (
    capabilities,
    drafts,
    dryrun,
    duplicates,
    labels,
    readback,
    store,
    tickets,
)
from app.custom_rules.capabilities import PURPOSE_DRAFT, PURPOSE_REPAIR, SCHEMA_NAME
from app.custom_rules.readback import CURRENCY_NOT_APPLIED
from app.custom_rules.spec import (
    CUSTOM_SEVERITIES,
    DRAFT_MAX_TURNS,
    MAX_ASSUMPTIONS,
    MAX_CANDIDATES,
    MAX_IDEAS,
    MAX_PHRASES,
    MAX_QUESTIONS,
    MAX_UNSUPPORTED,
    MODEL_CALLS_PER_TURN,
    REFUSAL_TEXT,
    REQUEST_MAX_CHARS,
    REQUEST_MIN_CHARS,
    RULE_DESCRIPTION_MAX,
    RULE_NAME_MAX,
    RULE_NAME_MIN,
    TEMPLATE_TEXT,
    TEMPLATES,
    CandidateView,
    CustomRuleSpec,
    CustomSeverity,
    DraftResponse,
    DraftStatus,
    DryRunResult,
    DuplicateNote,
    FieldMissing,
    FieldVsConstant,
    Idea,
    ModelDraft,
    Question,
    QuestionOption,
    Refusal,
    RefusalReason,
    SpecProblem,
    TicketRef,
    UnsupportedPart,
    addressable_fields,
    parse_spec,
)

AUTHOR_MAX_USD_PER_TURN = Decimal("0.05")
BODY_TEXT_MAX = 5_000  # a body field longer than this is a 422 before anything reads it
UNAVAILABLE_MESSAGE = (
    "The rule assistant is not available right now. You can still send the idea to the tech team."
)

_save_lock = threading.Lock()  # one save at a time: check, create and close the draft together

# lowest first: a status may be lowered to one earlier in this list, never raised to a later one
_RANK: dict[str, int] = {
    "refused": 0,
    "unsupported": 1,
    "partially_supported": 2,
    "ambiguous": 3,
    "needs_clarification": 4,
    "ready": 5,
}


# --------------------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------------------


class AnswerRequest(Strict):
    question_id: str
    option_id: str


class TurnRequest(Strict):
    """POST /custom-rules/drafts/{id}/turns: exactly one of the three."""

    text: str | None = Field(default=None, max_length=BODY_TEXT_MAX)
    answer: AnswerRequest | None = None
    choose_candidate: int | None = None

    @model_validator(mode="after")
    def _one(self) -> TurnRequest:
        given = [v is not None for v in (self.text, self.answer, self.choose_candidate)]
        if sum(given) != 1:
            raise ValueError("send exactly one of text, answer or choose_candidate")
        return self


class StartDraftRequest(Strict):
    text: str = Field(max_length=BODY_TEXT_MAX)
    severity: CustomSeverity | None = None
    edit_of: str | None = None


class SaveRuleRequest(Strict):
    draft_id: str
    name: str | None = Field(default=None, max_length=BODY_TEXT_MAX)
    description: str | None = Field(default=None, max_length=BODY_TEXT_MAX)
    reason: str | None = Field(default=None, max_length=BODY_TEXT_MAX)
    accept_narrower: StrictBool = False
    confirm_warnings: StrictBool = False
    submit_ticket: StrictBool = False


class TicketRequest(Strict):
    """POST /custom-rules/tickets: a draft to send on, or the officer's own words."""

    draft_id: str | None = None
    text: str | None = Field(default=None, max_length=BODY_TEXT_MAX)
    severity: CustomSeverity | None = None

    @model_validator(mode="after")
    def _one(self) -> TicketRequest:
        if (self.draft_id is None) == (self.text is None):
            raise ValueError("send exactly one of draft_id or text")
        return self


@dataclass(frozen=True)
class SaveResult:
    rule: store.RuleRow
    version: store.VersionRow
    ticket: tickets.TicketRow | None


# --------------------------------------------------------------------------------------
# Pre-checks (deterministic, no model)
# --------------------------------------------------------------------------------------


# Cyrillic and Greek letters that look like a Latin one; NFKC does not fold them, so a word spelled with
# one ("n\u0430tionality") would otherwise slip past the patterns and the squashed-stem check.
_CONFUSABLES = str.maketrans(
    {
        **dict(
            zip(
                "\u0430\u0435\u043e\u0440\u0441\u0445\u0443\u0456\u0455\u0458",
                "aeopcxyisj",
                strict=True,
            )
        ),
        **dict(zip("\u043d\u043a\u0442\u043c\u0432", "hktmb", strict=True)),
        **dict(
            zip(
                "\u03b1\u03bf\u03b5\u03b9\u03bd\u03c1\u03c4\u03ba\u03b7\u03bc\u03c5\u03c7",
                "aoeivptknmux",
                strict=True,
            )
        ),
    }
)


def _rx(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


# words for a person's nationality or ethnic group; not "polish", "turkish" or "english", which are also
# plain words, and not country names, which are checked next to a person or an identity document
_DEMONYMS = (
    "singaporean",
    "malaysian",
    "chinese",
    "indian",
    "indonesian",
    "thai",
    "vietnamese",
    "filipino",
    "japanese",
    "korean",
    "burmese",
    "bangladeshi",
    "pakistani",
    "american",
    "british",
    "australian",
    "european",
    "african",
    "arab",
    "russian",
    "iranian",
    "nigerian",
    "taiwanese",
    "nepalese",
    "sri lankan",
)


# a nationality word directly followed by the name of a currency is the currency ("Australian dollars")
_NOT_A_CURRENCY = (
    r"(?!\s+(?:dollars?|pounds?|yen|rupees?|baht|yuan|ringgit|pesos?|rupiah|dong|francs?|krona|kronor|"
    r"kroner|dirhams?|riyals?|won(?!\s+(?:the|a|an|this|that|it|him|her|them|us|me|our|their)\b))\b)"
)
# a person, for the patterns that read a word next to one ("minor" beside a director)
_PERSON_WORD = (
    r"(?:child|children|kid|kids|boy|girl|person|people|applicant|director|owner|shareholder|signatory|"
    r"individual|customer|holder|guarantor|borrower|employee|student|man|men)s?"
)

# Each list is matched against the lower-cased, NFKC-normalised, cleaned request. Hits AND near misses are
# pinned in tests/test_custom_rules_author.py. "high debt", "highest", "total assets", "average" and
# "percentage" are allowed; "age", "aged" and "years old" refuse (so "aged receivables" is refused and the
# message tells the officer to say how many days before the case date instead).
INJECTION_PATTERNS = _rx(
    r"\b(?:ignore|disregard|forget|override)\b.{0,40}\b(?:instructions?|system prompt|guidelines)\b",
    r"\bsystem prompt\b",
    r"\byou are now\b",
    r"\bdeveloper mode\b",
    r"\bact as\b",
    r"\bpretend (?:to be|you)\b",
    r"\[\[\[doc-",
    r"begin label=",
    r"<\|",
    r"</",
    r"\bassistant\s*:",
    r"\btool_use\b",
    r"\bjailbreak",
)
PROTECTED_PATTERNS = _rx(
    r"\bnationalit(?:y|ies)\b",
    r"\bcitizen(?:s|ship)?\b",
    r"\brac(?:e|ial|ially)\b",
    r"\bethnic(?:ity|ities)?\b",
    r"\breligio(?:n|us)\b",
    r"\bgender\b",
    r"\bsex\b",
    r"\b(?:male|female|woman|women|marital status|pregnan\w*|disabilit\w*|disabled|sexual orientation)\b",
    r"\bskin colou?r\b",
    r"\bsound(?:s|ing)? foreign\b",
    # the predicate form ("the director is Malay") names an ethnic group with no noun after it
    r"\b(?:malay|tamil|eurasian|bumiputera|caucasian|sikh)s?\b",
    r"\bdate of birth\b",
    r"\bplace of birth\b",
    r"\bd\.?o\.?b\b",
    r"\bbirth ?days?\b",
    r"\bborn (?:before|after|on|in)\b",
    r"\byears? of birth\b",
    r"\bbirth ?(?:year|date)s?\b",
    r"\bages?\b",
    r"\baged\b",
    r"\byears? old\b",
    r"\bname origins?\b",
    r"-sounding\b",
    r"\bsounding names?\b",
    r"\bsurnames?\b",
    r"\b(?:first|last|given|family|full|middle) names?\b",
    r"\b(?:director|shareholder|owner|holder|signatory|person|people|individual|applicant|customer)'?s? names?\b",
    r"\b(?:chinese|malay|indian|eurasian|caucasian)\s+(?:names?|surnames?|directors?|owners?|"
    r"shareholders?|people|persons?|nationals?|men|women|origin|descent)\b",
    r"\bforeigners?\b",
    r"\bforeign nationals?\b",
    r"\bnon[- ]?(?:singaporeans?|citizens?|residents?|nationals?)\b",
    r"\bimmigrants?\b",
    r"\bprc\b",
    r"\b(?:" + "|".join(_DEMONYMS) + r")s?\b" + _NOT_A_CURRENCY,
    r"\bnon[- ]?(?:" + "|".join(_DEMONYMS) + r")s?\b" + _NOT_A_CURRENCY,
    r"\b(?:elderly|pensioners?|retirees?|underage|teenagers?|toddlers?|infants?)\b",
    # "minor rounding differences" is not a person; "minors", "a minor" and "minor" next to a person word are
    r"\bminors\b",
    r"\b(?:is|was|are|were|be|as) an? minor\b",
    r"\ban? minor\s*(?:$|[.,;:!?])",
    r"\bminor\b.{0,20}\b" + _PERSON_WORD + r"\b",
    r"\b" + _PERSON_WORD + r"\b.{0,20}\bminor\b",
    r"\byear[- ]?olds?\b",
    r"\b\d{1,3}\s?(?:yo|y/o)\b",
    r"\b(?:older|younger) than \d{1,3}\b(?!\s*(?:days?|weeks?|months?|years?|yrs?|hours?|[%x,.\d]))",
    # what a national identity number or passport says about a person's origin
    r"\b(?:nric|fin|identity card|id card|national id|passport)\b.{0,40}"
    r"\b(?:start\w*|begin\w*|prefix\w*|first letter|issued by|issuing (?:country|authority)|"
    r"country of (?:issue|issuance|origin)|place of issue)\b",
    r"\b(?:start\w*|begin\w*|prefix\w*|first letter)\b.{0,40}\b(?:nric|fin|identity card|id card|national id)\b",
)
# a country named next to a person or identity document is a question about origin
COUNTRY_NAMES = (
    "singapore|malaysia|china|india|indonesia|thailand|vietnam|philippines|japan|korea|myanmar|burma|"
    "bangladesh|pakistan|sri lanka|nepal|taiwan|hong kong|macau|america|united states|usa|britain|"
    "united kingdom|australia|russia|iran|iraq|nigeria|africa|europe|germany|france|italy|spain"
)
_PERSON = r"(?:director|shareholder|owner|signatory|applicant|individual|person|people|holder|customer|man|men)s?"
COUNTRY_NEXT_TO_ORIGIN = _rx(
    r"\b(?:passport|identity|nric|fin|visa|work pass|pass holder|nationals?|origin|descent|born)\b",
    r"\b" + _PERSON + r"\b.{0,25}\b(?:from|of)\b",
)
COUNTRY_RX = re.compile(r"\b(?:" + COUNTRY_NAMES + r")\b")
# Distinctive stems looked for again in the request with every space and symbol removed, so that a word
# split by hidden characters or spaces ("n a t i o n a l i t y") does not slip past the patterns.
SQUASHED_PROTECTED = (
    "nationalit",
    "citizenship",
    "ethnicit",
    "religio",
    "woman",
    "pregnan",
    "disabilit",
    "elderly",
    "dateofbirth",
    "yearofbirth",
    "bornbefore",
    "placeofbirth",
    "birthday",
    "surname",
    "gender",
)
_HIGH_TAIL = (
    r"\s*(?:$|[.,;:!]|\b(?:if|when|for|where|because|whenever|severity|priority|risk|alert)\b)"
)
HIGH_PATTERNS = _rx(
    r"\bseverity\s*(?:is|=|:|of|to|as)?\s*high\b",
    r"\bas high" + _HIGH_TAIL,
    r"\b(?:flag|mark|set|make|rate|treat|raise)\s+(?:it\s+|this\s+|them\s+|these\s+)?(?:as\s+|to\s+)?high"
    + _HIGH_TAIL,
    r"\bhigh[- ](?:severity|priority)\b",
    r"\b(?:level|alert|rating|grade|importance|urgency|priority)\s*(?:is|=|:|of|to|as|at)?\s*high\b",
    r"\bhigh\s+(?:alert|flag|rating|grade|importance|urgency)\b",
    r",\s*high(?:\s+please)?\s*[.!]?\s*$",
    r"\bhigh,?\s+please\b",
    r"\bcritical\b",
)
_HIGH_WORD = re.compile(r"\b(?:high|highest|critical|urgent)\b")
HARD_STOP_PATTERNS = _rx(
    r"\bhard[- ]?stops?\b",
    r"\b(?:block|stop|prevent|disable|skip)\w*\s+(?:the\s+)?fast[- ]?lane\b",
    r"\bblock(?:s|ing)?\s+(?:the\s+)?(?:case|approval|application)\b",
    r"\bstop(?:s|ping)?\s+(?:the\s+)?case\b",
    r"\bauto(?:matically)?[- ]?(?:reject|decline|approve)\b",
)
_CHANGE_VERBS = (
    r"(?:switch|turn)\w*\s+off|disabl\w*|mut(?:e|ed|ing)|suppress\w*|ignor\w*|skip\w*|overrid\w*|"
    r"lower\w*|rais\w*|chang\w*|stop(?:s|ping)?|silenc\w*|bypass\w*|remov\w*|delet\w*|replac\w*|exclud\w*|modif\w*"
)
CHANGES_BUILTIN_PATTERNS = _rx(
    r"\b(?:" + _CHANGE_VERBS + r")\b.{0,30}\b(?:checks?|rules?|catalogue|built-?ins?)\b",
    r"\b(?:switch|turn)\b.{0,25}\boff\b",
    r"\b(?:checks?|rules?)\b.{0,20}\b(?:off|disabled|muted)\b",
)
_SCORE_NOUN = r"\b(?:scores?|points?|weights?|coverage|lanes?|bands?)\b"
_SCORE_VERB = r"\b(?:add|increase|decrease|change|reduce|boost|adjust|lift|cut)\b"
# "set" only when the score is what is set ("set the score to 0"), not "set it to low"
# in either order ("set the score to 0", "set to zero score", "score set to 0")
_SET_SCORE = (
    r"\bset\b(?:\s+[\w.]+){0,3}?\s+"
    + _SCORE_NOUN
    + r"|"
    + _SCORE_NOUN
    + r"\s+(?:is\s+|are\s+)?set\s+(?:to|at)\b"
)
_CREDIT_SCORE = re.compile(r"\bcredit\s+scores?\b")
_WORD = re.compile(r"[a-z]{4,}")
# Short words that change what a rule means or which documents it reads: a dropped "not" reverses the
# rule. Checked as whole words; "only", "except", "without" and "excluding" are ordinary 4+ letter words.
SHORT_WORDS = frozenset({"not", "no", "non", "nil", "po", "gst", "uen", "id"})

STOP_WORDS = frozenset(
    [
        "flag",
        "flags",
        "flagged",
        "rule",
        "rules",
        "please",
        "when",
        "that",
        "this",
        "with",
        "from",
        "have",
        "than",
        "more",
        "less",
        "should",
        "would",
        "could",
        "case",
        "cases",
        "document",
        "documents",
        "there",
        "their",
        "them",
        "they",
        "what",
        "which",
        "where",
        "about",
        "into",
        "over",
        "under",
        "also",
        "then",
        "very",
        "some",
        "such",
        "each",
        "every",
        "like",
        "make",
        "mark",
        "show",
        "raise",
        "alert",
        "will",
        "must",
        "need",
        "needs",
        "want",
        "wants",
        "been",
        "being",
        "were",
        "does",
        "your",
        "just",
        "same",
        "many",
        "much",
        "most",
        "those",
        "these",
        "other",
        "anything",
        "something",
    ]
)


def clean_request(text: str) -> str:
    """The request as one clean line, or 422: officers' words are untrusted plain text."""
    cleaned = clean_text(text or "", REQUEST_MAX_CHARS + 1)
    if len(cleaned) < REQUEST_MIN_CHARS:
        raise ApiError(
            422,
            "validation_error",
            "Describe what should be flagged and when.",
            {"errors": [{"field": "text", "message": "too short", "type": "length"}]},
        )
    if len(cleaned) > REQUEST_MAX_CHARS:
        raise ApiError(
            422,
            "validation_error",
            f"Keep the request under {REQUEST_MAX_CHARS} characters.",
            {"errors": [{"field": "text", "message": "too long", "type": "length"}]},
        )
    return cleaned


def _builtin_ids() -> frozenset[str]:
    from app.checks.registry import all_checks

    return frozenset(m.rule.lower() for m in all_checks())


def _word_count(token: str) -> int:
    """Words in one space-separated token. A run of two or more characters of a script written without
    spaces (Chinese, Japanese, Thai) is a phrase, not a single word."""
    if not any(ch.isalpha() for ch in token):
        return 0
    return 2 if sum(1 for ch in token if unicodedata.category(ch) == "Lo") >= 2 else 1


def _normalised(text: str) -> str:
    cleaned = clean_text(text or "", REQUEST_MAX_CHARS + 1)
    return unicodedata.normalize("NFKC", cleaned).lower().translate(_CONFUSABLES)


# a figure with no unit next to a person word is an age ("directors over 65"); an amount is not: "a total over
# 500 for a customer" has an amount word in front of the figure, and "over 65 years" is an age wherever it is
_FIGURE = re.compile(
    r"\b(?:over|under|above|below|older than|younger than)\s+(\d{1,3})\b(?![,.\d%])"
)
_AGE_UNIT = re.compile(r"\s*(?:years?|yrs?|y/?o|old|aged)\b")
_AMOUNT_WORD = re.compile(
    r"\b(?:total|subtotal|amount|value|price|sum|balance|fee|cost|revenue|turnover|tax|payment|sales|"
    r"profit|income|debt|equity|assets?|liabilit(?:y|ies)|rate|interest|cash|capital|credit|debit)\b|"
    r"[$\u20ac\u00a3\u00a5]"
)
_NEAR_PERSON = re.compile(r"\b" + _PERSON_WORD + r"\b")


def _amount_before(before: str) -> bool:
    """Whether the figure that follows is an amount: an amount word comes before it with no person word
    after that amount word ("a total over 500", but "a balance of customers above 60" is about customers)."""
    amounts = list(_AMOUNT_WORD.finditer(before))
    return bool(amounts) and not _NEAR_PERSON.search(before, amounts[-1].end())


def figure_next_to_person(low: str) -> bool:
    """An age: a figure after over/under/older than with a person word within 40 characters either side,
    unless an amount word comes before the figure with no person between them (and no age unit follows)."""
    for m in _FIGURE.finditer(low):
        aged = bool(_AGE_UNIT.match(low, m.end()))
        if not aged and _amount_before(low[max(0, m.start() - 30) : m.start()]):
            continue
        if _NEAR_PERSON.search(low[m.end() : m.end() + 40]) or _NEAR_PERSON.search(
            low[max(0, m.start() - 40) : m.start()]
        ):
            return True
    return False


def protected_attribute(text: str) -> bool:
    """Whether the words are about a protected attribute (docs/name-fairness.md), by the word lists
    alone: used on the request, and again on every phrase the model reports as not expressible."""
    low = _normalised(text)
    return (
        any(p.search(low) for p in PROTECTED_PATTERNS)
        or figure_next_to_person(low)
        or any(stem in re.sub(r"[^a-z0-9]", "", low) for stem in SQUASHED_PROTECTED)
        or bool(COUNTRY_RX.search(low) and any(p.search(low) for p in COUNTRY_NEXT_TO_ORIGIN))
    )


def precheck(text: str) -> Refusal | None:
    """The first deterministic refusal that applies, or ``None``. Order: injection, protected attribute,
    high severity, hard stop, changing a built-in check, changing the score, nonsense."""
    low = _normalised(text)

    def hit(patterns: Iterable[re.Pattern[str]], subject: str = low) -> bool:
        return any(p.search(subject) for p in patterns)

    reason: RefusalReason | None = None
    if hit(INJECTION_PATTERNS):
        reason = "prompt_injection"
    elif protected_attribute(text):
        reason = "protected_attribute"
    elif hit(HIGH_PATTERNS):
        reason = "high_severity"
    elif hit(HARD_STOP_PATTERNS):
        reason = "hard_stop"
    elif hit(CHANGES_BUILTIN_PATTERNS) or (
        re.search(_CHANGE_VERBS, low) and _builtin_ids() & set(re.findall(r"[a-z0-9_]+", low))
    ):
        reason = "changes_builtin"
    else:
        scoreless = _CREDIT_SCORE.sub(" ", low)
        if re.search(_SCORE_NOUN, scoreless) and (
            re.search(_SCORE_VERB, scoreless) or re.search(_SET_SCORE, scoreless)
        ):
            reason = "changes_score"
        else:
            words = sum(_word_count(t) for t in low.split())
            non_space = [ch for ch in low if not ch.isspace()]
            letters = sum(1 for ch in non_space if ch.isalpha())
            if words < 2 or (non_space and letters * 2 < len(non_space)):
                reason = "nonsense"
    return None if reason is None else Refusal(reason=reason, message=REFUSAL_TEXT[reason])


def _apostrophes(text: str) -> str:
    return text.replace("\u2019", "'").replace("\u2018", "'")


def _stems(token: str) -> set[str]:
    out = {token}
    for suffix in ("es", "s", "ed", "d", "ing"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            out.add(token[: -len(suffix)])
    return out


# negation words (and "n't" forms): a rule that is itself the negation carries them, so the model need not
# have mapped them to a phrase of its own
_NEGATIONS = frozenset({"no", "not", "non", "nil", "without"})


def _negation_carried(specs: Sequence[CustomRuleSpec]) -> bool:
    """Whether every reading of the request is a rule that IS a negation: a missing field, or a "not equal"
    / "not in" comparison. A comparison such as "greater than" is not: a dropped "not" would flip it."""
    if not specs:
        return False
    for spec in specs:
        c = spec.condition
        if isinstance(c, FieldMissing):
            continue
        if isinstance(c, FieldVsConstant) and c.operator in ("ne", "not_in"):
            continue
        return False
    return True


def _field_words(specs: Sequence[CustomRuleSpec]) -> set[str]:
    """The words of the field names the rule reads (``invoice.po_reference`` gives invoice, po, reference)."""
    words: set[str] = set()
    for spec in specs:
        field = getattr(spec.condition, "field", None)
        if isinstance(field, str):
            words.update(re.findall(r"[a-z]+", field.lower()))
    return words


def _word_pieces(text: str) -> list[str]:
    """Letters and digits apart, so that "PO123" is "po" and "123" on whichever side it is read."""
    return re.findall(r"[a-z]+(?:'t)?|[0-9]+", text)


def uncovered_words(
    text: str, phrases: Sequence[str], specs: Sequence[CustomRuleSpec] = ()
) -> list[str]:
    """Words of the officer's request (4+ letters, not a stop word; and the short ones that change what a
    rule means, ``SHORT_WORDS``) that no phrase of the model's phrase map or unsupported parts contains.
    A model that skipped a word cannot slip it past the officer: a dropped "not" or "no" reverses a rule.

    ``specs`` are the rule (or the readings) the model gave: a negation word is covered when every one is a
    negation by its template or operator (a missing field, "not equal", "not in"), and a short word that is
    part of a field the rule reads (``po`` for ``invoice.po_reference``) is covered too."""
    blob = " ".join(_apostrophes(p.lower()) for p in phrases)
    blob_words = set(_word_pieces(blob))
    negated = _negation_carried(specs)
    field_words = _field_words(specs)
    seen: list[str] = []
    low = _apostrophes(unicodedata.normalize("NFKC", text).lower())
    for token in re.findall(r"[a-z]+n't|[a-z]{4,}|[a-z]{2,3}", low):
        if token in seen:
            continue
        if len(token) <= 3 or token.endswith("n't"):
            # short words must appear as whole words: "no" is inside "number" and "not" inside "note"
            if (
                (token in SHORT_WORDS or token.endswith("n't"))
                and token not in blob_words
                and not (negated and (token in _NEGATIONS or token.endswith("n't")))
                and not (token in field_words and token not in _NEGATIONS)
            ):
                seen.append(token)
            continue
        if token in STOP_WORDS:
            continue
        if token == "without" and negated:
            continue
        if not any(stem in blob for stem in _stems(token)):
            seen.append(token)
    return seen


# --------------------------------------------------------------------------------------
# Currencies: a rule compares an amount as printed, in any currency
# --------------------------------------------------------------------------------------

# ISO codes that are not also English words ("all", "try", "mad" are left out), the names of the
# currencies the desk meets, and the symbols
_CURRENCY_CODES = (
    "sgd|usd|eur|gbp|jpy|aud|myr|idr|thb|cny|rmb|hkd|inr|nzd|cad|chf|krw|php|vnd|twd|aed|sar|zar|brl|"
    "mxn|sek|nok|dkk"
)
_CURRENCY_NAMES = (
    "dollars?|yen|ringgit|rupees?|rupiah|baht|yuan|renminbi|euros?|sterling|pounds?|francs?|pesos?|"
    "dirhams?|riyals?|kron(?:a|or|er)"
)
# "Euros Trading Pte Ltd" is a company
_COMPANY_TAIL = r"(?!\s+(?:trading|pte|ltd|limited|holdings|group|corp|inc|llc|bank)\b)"
_CODE = r"(?<![a-z])(" + _CURRENCY_CODES + r")(?![a-z])"
_NAME = r"(?<![a-z])(" + _CURRENCY_NAMES + r")(?![a-z])" + _COMPANY_TAIL
_IN_CURRENCY = r"(?<![a-z])(?:in|currency(?:\s+is)?\s*:?)\s+"
_SYMBOLS = "$\u20ac\u00a3\u00a5"
# A sign always counts. A code or a name counts only where it is plainly a currency: next to a figure, or
# after "in" / "currency" ("SAR" as a suspicious activity report, a dollar amount and a company called
# Euros Trading are not). Every pattern has one group, the currency as written.
CURRENCY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        "([" + _SYMBOLS + "])",
        r"(?<![a-z])(rm)(?=\s?\d)",
        _CODE + "(?=\\s?[" + _SYMBOLS + r"]?\s?\d)",  # SGD 50000, USD100000
        r"(?<=\d)\s?" + _CODE,  # 50000 SGD
        _IN_CURRENCY + _CODE,  # in USD, currency is SGD
        _NAME + "(?=\\s?[" + _SYMBOLS + r"]?\s?\d)",  # dollars 500
        r"\d[\d,.]*\s?(?:[a-z]+\s+)?" + _NAME,  # 500 Singapore dollars
        _IN_CURRENCY
        + r"(?:[a-z]+\s+){0,2}?"
        + _NAME,  # in Singapore dollars, currency is British pound
    )
)


def currencies_named(text: str) -> list[str]:
    """The currencies the officer's words name (a code, a name or a symbol), as written, without repeats."""
    normal = unicodedata.normalize("NFKC", text)
    found = sorted(
        (m.start(1), m.group(1)) for rx in CURRENCY_PATTERNS for m in rx.finditer(normal)
    )
    codes = _CURRENCY_CODES.split("|")
    return list(dict.fromkeys(f.upper() if f.lower() in codes else f.lower() for _, f in found))


def _reads_currency(spec: CustomRuleSpec) -> bool:
    c = spec.condition
    return isinstance(c, FieldVsConstant) and c.field.partition(".")[2] == "currency"


def _with_currency_part(model: ModelDraft, spec: CustomRuleSpec | None, text: str) -> ModelDraft:
    """A request that names a currency while the rule does not read the currency field checks less than
    asked: every amount is compared as printed, whatever its currency. Code adds that as a part the rule
    will NOT check, so the status cannot stay ready and saving needs the tick.

    Only for a rule there is: with no rule yet (ambiguous, or a question to answer first) the part is added
    once a reading is chosen or the answer completes the rule, so a caveat made by code never turns a
    request into an unsupported one by itself."""
    named = currencies_named(text)
    if not named or spec is None or _reads_currency(spec):
        return model
    known = " ".join(p.phrase.lower() for p in model.unsupported_parts)
    new = [n for n in named if n.lower() not in known]
    if not new or len(model.unsupported_parts) >= MAX_UNSUPPORTED:
        return model
    part = UnsupportedPart(
        phrase=clean_text(", ".join(new), 120),
        reason="field_not_extracted",
        detail=CURRENCY_NOT_APPLIED,
    )
    return model.model_copy(update={"unsupported_parts": [*model.unsupported_parts, part]})


# --------------------------------------------------------------------------------------
# Code decides the status
# --------------------------------------------------------------------------------------


def protected_parts(model: ModelDraft) -> list[str]:
    """Phrases the model gave up on (an unsupported part, or a phrase it mapped to nothing) that are about a
    protected attribute, whatever reason it gave: code, not the model, decides what counts as one."""
    return [
        phrase
        for phrase in (
            *(p.phrase for p in model.unsupported_parts),
            *(m.phrase for m in model.phrase_map if m.slot is None),
        )
        if protected_attribute(phrase)
    ]


def _phrase_matches(phrase: str, others: Sequence[str]) -> bool:
    p = phrase.lower().strip()
    return any(p == o or p in o or o in p for o in others if o)


def decide_status(
    model: ModelDraft,
    *,
    spec: CustomRuleSpec | None,
    problems: Sequence[SpecProblem],
    candidates: Sequence[CustomRuleSpec],
    questions: Sequence[Question],
    uncovered: Sequence[str],
    claimed: DraftStatus | None = None,
) -> tuple[DraftStatus, list[str]]:
    """The final status and the code-written notes saying why it is not what the model claimed.

    ``claimed`` is the status the model's answer stands for (``model.status`` unless an officer's answer
    settled a question since). Phrases the model left unmapped and words it left out are never taken as
    settled by an answer (see ``_carry_dropped_words``). The result is never above ``claimed``."""
    claimed = claimed or model.status
    notes: list[str] = []
    parts = model.unsupported_parts
    unsupported_phrases = [p.phrase.lower().strip() for p in parts]
    unmapped = [
        m.phrase
        for m in model.phrase_map
        if m.slot is None and not _phrase_matches(m.phrase, unsupported_phrases)
    ]
    missed = list(uncovered)

    # a claim the rest of the answer cannot back is read as the nearest status that it can
    if claimed == "ambiguous" and len(candidates) < 2:
        notes.append("The assistant found the request ambiguous but gave fewer than two readings.")
        claimed = "needs_clarification"
    if claimed == "partially_supported" and not parts:
        notes.append(
            "The assistant said part of the request cannot be checked but did not say which part."
        )
        claimed = "needs_clarification"

    if (
        model.refusal_reason
        or model.status == "refused"
        or any(p.reason == "policy_forbidden" for p in parts)
        or protected_parts(model)
    ):
        computed: DraftStatus = "refused"
    elif model.status == "unsupported" or (parts and spec is None):
        computed = "unsupported"
    elif parts:
        computed = "partially_supported"
    elif (
        spec is None
        and len(candidates) >= 2
        or model.status == "ambiguous"
        and len(candidates) >= 2
    ):
        computed = "ambiguous"
    elif spec is None or problems or questions or unmapped or missed:
        computed = "needs_clarification"
    else:
        computed = "ready"

    if computed in ("partially_supported", "needs_clarification", "ready", "ambiguous"):
        if unmapped:
            notes.append(
                "These phrases of your request were not matched to anything: "
                + ", ".join(f'"{clean_text(p, 60)}"' for p in unmapped[:5])
                + "."
            )
        if missed:
            notes.append(
                "These words of your request were not accounted for: " + ", ".join(missed[:6]) + "."
            )
    if computed in ("needs_clarification", "ready") and problems:
        notes.append(
            "The assistant's rule was not valid: "
            + "; ".join(f"{p.loc}: {clean_text(p.message, 120)}" for p in problems[:3])
        )
    if computed == "needs_clarification" and spec is None and not problems and not candidates:
        notes.append("No usable rule came out of the request. Add detail, or rephrase it.")
    if questions and computed in ("needs_clarification", "partially_supported", "ready"):
        notes.append("There is a question to answer before this can be saved.")
    if parts and computed == "partially_supported" and claimed == "ready":
        notes.append("Part of your request cannot be checked by a custom rule.")

    # never above the model's own claim: when the model was the more cautious, its status stands
    final = computed if _RANK[computed] <= _RANK[claimed] else claimed
    return final, notes


# --------------------------------------------------------------------------------------
# Answers (no model call)
# --------------------------------------------------------------------------------------

_T1_SLOTS = {"field", "operator", "value", "values"}
_SLOTS_BY_TEMPLATE = {
    "field_vs_constant": _T1_SLOTS,
    "date_window": {"field", "direction", "days"},
    "field_missing": {"field"},
    "fs_ratio": {"left", "right", "operator", "multiplier", "column"},
}
_LIST_OPS = ("in", "not_in")
_DAYS_RX = re.compile(r"[0-9]{1,6}", re.ASCII)


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _set_slot(cond: dict[str, Any], slot: str, value: str) -> str | None:
    """Put an answer into one slot of a condition (a dict). Returns why it cannot, else ``None``."""
    template = cond.get("template", "")
    if slot not in _SLOTS_BY_TEMPLATE.get(template, set()):
        return f"'{slot}' is not part of this kind of rule"
    if slot in ("left", "values"):
        cond[slot] = _split(value)
        if slot == "values":
            cond.pop("value", None)
    elif slot == "days":
        # ASCII digits only: "\u00b2" and Arabic-Indic digits are digits to str.isdigit() but not to int()
        if not _DAYS_RX.fullmatch(value):
            return "days must be a whole number"
        cond["days"] = int(value)
    elif slot == "value":
        if cond.get("operator") in _LIST_OPS:
            return "this rule compares with a list; the answer must give the list of values"
        cond["value"] = value
        cond.pop("values", None)
    elif slot == "operator":
        was_list = cond.get("operator") in _LIST_OPS
        now_list = value in _LIST_OPS
        if template == "field_vs_constant":
            if now_list and not was_list and "value" in cond:
                cond["values"] = [cond.pop("value")]
            elif was_list and not now_list and "values" in cond:
                values = cond.pop("values")
                if len(values) != 1:
                    return "a list of several values cannot be compared with a single value"
                cond["value"] = values[0]
        cond["operator"] = value
    else:
        cond[slot] = value
    return None


def apply_answer(
    spec: CustomRuleSpec | None, question: Question, option: QuestionOption
) -> tuple[CustomRuleSpec | None, list[SpecProblem]]:
    """The spec with the officer's chosen option put into the question's slot, revalidated. Invalid
    results come back as problems (code text), never as an exception."""
    if spec is None:
        return None, [
            SpecProblem(loc="spec", message="there is no rule yet to apply the answer to")
        ]
    try:
        raw = spec.model_dump(mode="json", exclude_none=True)
        value = option.value.strip()
        if question.slot == "severity":
            if value not in CUSTOM_SEVERITIES:
                return None, [
                    SpecProblem(loc="severity", message="severity must be info, low or medium")
                ]
            raw["severity"] = value
        else:
            condition = dict(raw["condition"])
            problem = _set_slot(condition, question.slot, value)
            if problem:
                return None, [SpecProblem(loc=question.slot, message=problem)]
            raw["condition"] = condition
        return parse_spec(raw)
    except (ValueError, TypeError, ArithmeticError, KeyError, AttributeError):
        # the model wrote the option; whatever is in it, the answer is a problem, never a crash
        return None, [SpecProblem(loc=question.slot, message="that answer cannot be applied")]


# --------------------------------------------------------------------------------------
# The model call
# --------------------------------------------------------------------------------------


@dataclass
class ModelOutcome:
    model: ModelDraft | None  # None: the model refused (LLMRefusal)
    spec: CustomRuleSpec | None
    problems: list[SpecProblem]
    candidates: list[CustomRuleSpec]
    weaker: CustomRuleSpec | None
    calls: int
    micro_usd: int
    purposes: list[str] = field(default_factory=list)  # the purpose label of each call made


def _clip(value: Any, limit: int) -> Any:
    return value[:limit] if isinstance(value, str) else value


def _lenient(data: dict[str, Any]) -> dict[str, Any]:
    """Cut over-long strings and lists to the limits of ``ModelDraft`` so one wordy field does not lose the
    whole answer. Content is not changed otherwise."""
    d = dict(data)
    d["understood"] = _clip(d.get("understood", ""), 300)
    d["assumptions"] = [_clip(a, 200) for a in (d.get("assumptions") or [])][:MAX_ASSUMPTIONS]
    d["phrase_map"] = [
        {**m, "phrase": _clip(m.get("phrase", ""), 120)} for m in (d.get("phrase_map") or [])
    ][:MAX_PHRASES]
    d["unsupported_parts"] = [
        {**p, "phrase": _clip(p.get("phrase", ""), 120), "detail": _clip(p.get("detail", ""), 200)}
        for p in (d.get("unsupported_parts") or [])
    ][:MAX_UNSUPPORTED]
    d["candidates"] = list(d.get("candidates") or [])[:MAX_CANDIDATES]
    questions = []
    for q in list(d.get("questions") or [])[:MAX_QUESTIONS]:
        questions.append(
            {
                **q,
                "text": _clip(q.get("text", ""), 200),
                "options": [
                    {
                        **o,
                        "label": _clip(o.get("label", ""), 80),
                        "value": _clip(o.get("value", ""), 200),
                    }
                    for o in (q.get("options") or [])
                ][:4],
            }
        )
    d["questions"] = questions
    d["closest_templates"] = list(dict.fromkeys(d.get("closest_templates") or []))[: len(TEMPLATES)]
    return d


def _parse_model(data: dict[str, Any]) -> tuple[ModelDraft | None, list[SpecProblem]]:
    try:
        return ModelDraft.model_validate(_lenient(data)), []
    except (ValidationError, TypeError, AttributeError) as exc:
        if isinstance(exc, ValidationError):
            return None, [
                SpecProblem(
                    loc=".".join(str(p) for p in e.get("loc", ()))[:120] or "answer",
                    message=str(e.get("msg", "invalid"))[:300],
                )
                for e in exc.errors()[:4]
            ]
        return None, [
            SpecProblem(loc="answer", message="the answer is not an object of the schema")
        ]


def _build_outcome(model: ModelDraft, calls: int, micro_usd: int) -> ModelOutcome:
    spec, problems = parse_spec(model.spec)
    candidates = [s for s in (parse_spec(c)[0] for c in model.candidates) if s is not None]
    weaker = parse_spec(model.weaker_spec)[0]
    return ModelOutcome(model, spec, problems, candidates, weaker, calls, micro_usd)


def _repair_text(problems: Sequence[SpecProblem]) -> str:
    lines = "\n".join(f"- {p.loc}: {clean_text(p.message, 200)}" for p in problems[:4])
    return (
        "\n\nYour previous answer could not be used:\n"
        f"{lines}\n"
        "Answer again with a corrected rule. Use only the listed paths, operators and value forms."
    )


def _map_llm_error(exc: LLMError) -> ApiError:
    """LLM failures as the API's clear messages. Casework is unaffected: the job runner has its own meters."""
    if isinstance(exc, LLMBudgetExceeded):
        if exc.limit == "user_daily_cap":
            return ApiError(
                429,
                "user_budget_used",
                f"Your model budget for today (${exc.limit_value}) is used up. It resets at midnight "
                "UTC. You can still send the idea to the tech team.",
                {"limit_usd": exc.limit_value, "used_usd": exc.used},
            )
        if exc.limit == "daily_cap":
            return ApiError(
                429,
                "site_budget_used",
                "The model budget for today is used up. It resets at midnight UTC. You can still "
                "send the idea to the tech team.",
                {"limit_usd": exc.limit_value, "used_usd": exc.used},
            )
    return ApiError(503, "authoring_unavailable", UNAVAILABLE_MESSAGE)


def _converse(
    call: Callable[[str, str], dict[str, Any]], meter: CostMeter, user: str
) -> ModelOutcome | None:
    """The draft call, then one repair call when its answer cannot be used (it does not fit the schema, or
    its spec does not validate). ``None``: the model declined."""
    try:
        model, parse_problems = _parse_model(call(PURPOSE_DRAFT, user))
    except LLMRefusal:
        return None
    first = _build_outcome(model, 0, 0) if model is not None else None
    unusable = first is None or (model.spec is not None and first.spec is None)  # type: ignore[union-attr]
    if unusable and meter.calls_used < MODEL_CALLS_PER_TURN:
        problems = parse_problems if first is None else first.problems
        try:
            again, _ = _parse_model(call(PURPOSE_REPAIR, user + _repair_text(problems)))
        except LLMRefusal:
            return None if first is None else first
        except LLMError:
            if first is None:
                raise
            return first  # the first answer is all there is (its problems are shown to the officer)
        if again is not None:
            second = _build_outcome(again, 0, 0)
            if first is None or second.spec is not None:
                return second
    if first is None:
        raise ApiError(503, "authoring_unavailable", UNAVAILABLE_MESSAGE)
    return first


def _ask(
    db: Any,
    settings: Settings,
    llm: LLMClient,
    *,
    account: str,
    user: str,
    now: datetime,
    draft_id: str | None,
) -> ModelOutcome:
    """One turn's model calls (at most ``MODEL_CALLS_PER_TURN``). The meter is a throwaway (nothing is filed
    on a case) charging the account's daily spend. The spend of a turn that fails is added to the draft
    all the same."""
    prices = PriceTable.load(settings.prices_path)
    meter = CostMeter(
        BudgetLimits(max_pages=1, max_calls=MODEL_CALLS_PER_TURN, max_usd=AUTHOR_MAX_USD_PER_TURN),
        prices,
        daily=DailySpend.from_settings(db, settings, prices, now=lambda: now, account=account),
    )
    schema = capabilities.model_output_schema()
    system = capabilities.capability_prompt()

    made: list[str] = []

    def call(purpose: str, text: str) -> dict[str, Any]:
        made.append(purpose)
        req = LLMRequest(
            purpose=purpose,
            model_role="cheap",
            system=system,
            user=text,
            schema_name=SCHEMA_NAME,
            json_schema=schema,
        )
        return llm.complete_json(req, ctx_costs=meter).data

    def spent() -> int:
        return int(meter.total() * 1_000_000)

    try:
        with meter.scope(stage="rules"):  # CostMeter needs a stage; this meter is discarded
            outcome = _converse(call, meter, user)
    except (LLMError, ApiError) as exc:
        if draft_id is not None:
            drafts.add_spend(db, draft_id, calls=meter.calls_used, micro_usd=spent())
        raise (_map_llm_error(exc) if isinstance(exc, LLMError) else exc) from exc
    if outcome is None:  # the model declined
        outcome = ModelOutcome(None, None, [], [], None, 0, 0)
    outcome.calls, outcome.micro_usd, outcome.purposes = meter.calls_used, spent(), made
    return outcome


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _wrapped(text: str, label: str) -> str:
    try:
        return wrap_untrusted(text, label=label)
    except ValueError as exc:
        raise ApiError(
            422, "validation_error", "The request cannot be read as plain text.", {"field": "text"}
        ) from exc


def _questions_text(questions: Sequence[Question]) -> str:
    return _json(
        [
            {"id": q.id, "slot": q.slot, "text": q.text, "options": [o.label for o in q.options]}
            for q in questions
        ]
    )


def _start_user(text: str, severity: str | None, edit_spec: CustomRuleSpec | None) -> str:
    parts = [f"The officer's request:\n{_wrapped(text, 'officer_request')}"]
    if severity:
        parts.append(f"Severity chosen on the form: {severity}")
    if edit_spec is not None:
        parts.append(
            "The officer is changing a rule of their own. Its current spec (JSON, from the system):\n"
            + _json(edit_spec.model_dump(mode="json", exclude_none=True))
        )
    parts.append("Answer by calling the function once.")
    return "\n\n".join(parts)


def _continue_user(
    earlier: str,
    new_text: str,
    spec: CustomRuleSpec | None,
    questions: Sequence[Question],
    severity: str | None,
) -> str:
    parts = (
        [f"The officer's request so far:\n{_wrapped(earlier, 'officer_request')}"]
        if earlier
        else []
    )
    if severity:
        parts.append(f"Severity chosen on the form: {severity}")
    parts.append(
        "The current draft rule (JSON, from the system): "
        + (_json(spec.model_dump(mode="json", exclude_none=True)) if spec else "none yet")
    )
    if questions:
        parts.append("Questions still open (JSON, from the system):\n" + _questions_text(questions))
    parts.append(f"The officer's new message:\n{_wrapped(new_text, 'officer_answer')}")
    parts.append(
        "Answer by calling the function once, for the whole request including the new message."
    )
    return "\n\n".join(parts)


# --------------------------------------------------------------------------------------
# Drafts: state in, state out
# --------------------------------------------------------------------------------------


def _spec_dict(spec: CustomRuleSpec | None) -> dict[str, Any] | None:
    return None if spec is None else spec.model_dump(mode="json", exclude_none=True)


def _with_severity(spec: CustomRuleSpec | None, severity: str | None) -> CustomRuleSpec | None:
    if spec is None or severity is None or spec.severity == severity:
        return spec
    raw = spec.model_dump(mode="json", exclude_none=True)
    raw["severity"] = severity
    return parse_spec(raw)[0] or spec


def _prefilter_questions(
    spec: CustomRuleSpec | None, questions: Sequence[Question]
) -> list[Question]:
    """Drop answers that would give an invalid rule, and questions left with fewer than two. A question
    that could never be answered would leave the officer stuck."""
    if spec is None:
        return list(questions)
    out: list[Question] = []
    for q in questions:
        options = [o for o in q.options if apply_answer(spec, q, o)[0] is not None]
        if len(options) < 2:
            continue
        default = q.default if q.default in {o.id for o in options} else None
        out.append(q.model_copy(update={"options": options, "default": default}))
    return out


_FIELD_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "is",
        "are",
        "was",
        "if",
        "and",
        "or",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "by",
        "its",
        "it",
        "their",
        "this",
        "that",
        "any",
        "every",
        "each",
        "has",
        "have",
        "had",
        "be",
        "been",
        "when",
        "than",
        "then",
    ]
)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKC", text).lower()))


def _stem(token: str) -> str:
    for suffix in ("ies", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def doubtful_field(model: ModelDraft, spec: CustomRuleSpec | None, text: str) -> list[str]:
    """Paths the officer's word for the field could mean, when the model picked one of them without asking.

    The phrase the model mapped to the field slot ("the amount", "capital") is compared with the names
    and meanings of the fields of the same kind, within the documents the officer named (or all of them).
    A field whose own name is exactly the phrase settles it; otherwise two or more fields that carry the
    phrase's words mean the model chose between them. Empty when the choice was not a guess."""
    if spec is None or spec.condition.template == "fs_ratio":
        return []
    chosen = spec.condition.field  # type: ignore[union-attr]
    info = addressable_fields().get(chosen)
    phrase = next((m.phrase for m in model.phrase_map if m.slot == "field"), None)
    if info is None or not phrase:
        return []
    low = text.lower()
    named = {
        d
        for d in labels.DOC_LABELS
        if re.search(
            r"\b(?:"
            + re.escape(labels.doc_label(d))
            + "|"
            + re.escape(labels.doc_plural(d))
            + r")\b",
            low,
        )
    }
    doc_words = {
        _stem(w) for d in (named or {info.doc_type}) for w in _tokens(labels.doc_label(d))
    } | {_stem(w) for w in _tokens(info.doc_type.replace("_", " "))}
    key = {_stem(w) for w in _tokens(phrase)} - {_stem(w) for w in _FIELD_STOP} - doc_words
    if not key:
        return []
    scope = [
        f
        for f in addressable_fields().values()
        if f.kind == info.kind and (not named or f.doc_type in named)
    ]
    matches: list[str] = []
    for f in scope:
        name = {_stem(w) for w in _tokens(f.field.replace("_", " "))} - doc_words
        if name == key:
            return []  # the field is called exactly that
        meaning = {_stem(w) for w in _tokens(labels.field_meaning(f.path))}
        if key <= name | meaning:
            matches.append(f.path)
    guess = list(dict.fromkeys([chosen, *matches]))
    return guess[:4] if len(guess) >= 2 else []


def _all_text(state: drafts.DraftState) -> str:
    return " ".join(h["text"] for h in state.history if h.get("kind") == "text" and h.get("text"))


def _severity_word(text: str) -> str | None:
    m = re.search(r"\b(?:as|severity|at)\s+(info|low|medium)\b", text.lower())
    return m.group(1) if m else None


def _asked_for_high(model: ModelDraft) -> bool:
    """A phrase of the request that the model mapped to the severity slot names the top level."""
    return any(
        m.slot == "severity" and _HIGH_WORD.search(m.phrase.lower()) for m in model.phrase_map
    )


def _apply_outcome(
    state: drafts.DraftState, outcome: ModelOutcome, *, text: str, severity: str | None
) -> None:
    """Fill ``state`` from the model's answer and let code decide the status."""
    model = outcome.model
    if model is None:  # the model declined
        state.model = None
        state.model_status = state.status = "refused"
        state.refusal = Refusal(reason="model_refused", message=REFUSAL_TEXT["model_refused"])
        state.spec, state.weaker = None, None
        state.candidates, state.questions, state.problems = [], [], []
        state.uncovered, state.notes = [], []
        return
    if model.status != "refused" and not model.refusal_reason and _asked_for_high(model):
        # the wording slipped past the pre-check, but the model itself mapped it to the severity slot:
        # the closed schema has no 'high', so it must not come back as a quietly lowered rule
        model = model.model_copy(update={"status": "refused", "refusal_reason": "high_severity"})
    spec = _with_severity(outcome.spec, severity)
    candidates = [c for c in (_with_severity(c, severity) for c in outcome.candidates) if c]
    if model.status != "refused" and not model.refusal_reason:
        model = _with_currency_part(model, spec, text)
    questions = _prefilter_questions(spec, model.questions)
    phrases = [m.phrase for m in model.phrase_map] + [p.phrase for p in model.unsupported_parts]
    uncovered = uncovered_words(text, phrases, [spec] if spec is not None else candidates)
    status, notes = decide_status(
        model,
        spec=spec,
        problems=outcome.problems,
        candidates=candidates,
        questions=questions,
        uncovered=uncovered,
    )
    if (
        severity
        and outcome.spec is not None
        and spec is not None
        and outcome.spec.severity != severity
    ):
        notes.append(f"The severity was set to {severity}, as you chose.")
    if len(questions) < len(model.questions):
        notes.append("A question was left out because its answers did not fit the rule.")
    doubt = doubtful_field(model, spec, text) if status == "ready" else []
    if doubt:
        # the model chose between fields the officer's word could mean: the officer chooses, no default
        status = "needs_clarification"
        questions = [
            Question(
                id="q1",
                slot="field",
                text="Which field do you mean?",
                options=[
                    QuestionOption(
                        id=f"f{i}",
                        label=labels.field_label(path)[:80],
                        value=path,
                    )
                    for i, path in enumerate(doubt, 1)
                ],
            )
        ]
        notes.append(
            "Your wording could mean more than one field, so the assistant's choice was not taken as final."
        )
    if status == "refused":
        # nothing the model said about a refused request is kept: not what it understood, not its phrases
        # or parts (docs/custom-rules.md). Only the reason, which code picks, is stored.
        why: RefusalReason = model.refusal_reason or (
            "protected_attribute" if protected_parts(model) else "model_refused"
        )
        state.model, state.model_status, state.status = None, "refused", "refused"
        state.refusal = Refusal(reason=why, message=REFUSAL_TEXT[why])
        state.spec, state.weaker, state.dry_run = None, None, None
        state.candidates, state.questions, state.problems = [], [], []
        state.uncovered, state.notes = [], []
        return
    if status == "ambiguous":
        spec = None
    state.model, state.model_status, state.status = model, model.status, status
    state.refusal = None
    state.spec = _spec_dict(spec) if status != "unsupported" else None
    state.weaker = _spec_dict(_with_severity(outcome.weaker, severity))
    state.candidates = (
        [c.model_dump(mode="json", exclude_none=True) for c in candidates][:MAX_CANDIDATES]
        if status == "ambiguous"
        else []
    )
    state.questions = questions if status not in ("unsupported", "ambiguous") else []
    state.problems = list(outcome.problems)
    state.uncovered = uncovered
    state.notes = notes
    state.dry_run = None


def _audit_refusal(
    db: Any,
    settings: Settings,
    *,
    account: str,
    draft_id: str,
    reason: str,
    turn: int,
    text: str,
    now: datetime,
) -> None:
    AuditLog(db, settings, clock=lambda: now).write(
        actor=account,
        action="custom_rule.refused",
        case_id=None,
        detail={
            "draft_id": draft_id,
            "reason": reason,
            "turn": turn,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
    )


def _audit_authoring(
    db: Any,
    settings: Settings,
    *,
    account: str,
    draft_id: str,
    turn: int,
    outcome: ModelOutcome,
    now: datetime,
) -> None:
    """One audit row per turn that called the model: which purposes, how many calls, what it cost. The
    spend itself is in the account's daily ledger; this is the label (it is never filed on a case)."""
    if outcome.calls <= 0:
        return
    AuditLog(db, settings, clock=lambda: now).write(
        actor=account,
        action="custom_rule.authoring",
        case_id=None,
        detail={
            "draft_id": draft_id,
            "turn": turn,
            "purposes": outcome.purposes,
            "calls": outcome.calls,
            "micro_usd": outcome.micro_usd,
        },
    )


def _desired_severity(state: drafts.DraftState, text: str) -> str | None:
    if state.desired_severity:
        return state.desired_severity
    spec = parse_spec(state.spec)[0]
    return (spec.severity if spec else None) or _severity_word(text)


def _doc_types_of(state: drafts.DraftState, text: str) -> list[str]:
    found: set[str] = set()
    for raw in (state.spec, state.weaker, *state.candidates):
        spec = parse_spec(raw)[0]
        if spec is not None:
            found.update(spec.doc_types())
    low = text.lower()
    for doc_type, label in labels.DOC_LABELS.items():
        if re.search(r"\b" + re.escape(label) + r"s?\b", low):
            found.add(doc_type)
    return sorted(found)


def _missing_of(state: drafts.DraftState) -> list[dict[str, str]]:
    parts = state.model.unsupported_parts if state.model else []
    return [{"phrase": p.phrase, "reason": p.reason} for p in parts]


def _own_draft(db: Any, account: str, draft_id: str) -> drafts.DraftRecord:
    rec = (
        drafts.load_draft(db, draft_id)
        if re.fullmatch(r"crd_[0-9a-f]{12}", draft_id or "")
        else None
    )
    if rec is None or rec.owner != account:
        raise ApiError(
            404, "not_found", "The draft was not found.", {"kind": "draft", "id": draft_id}
        )
    return rec


def _owned_active_rule(db: Any, account: str, rule_id: str) -> store.RuleRow:
    rule = store.get_rule(db, rule_id)
    if rule is None or rule.owner != account:
        raise ApiError(
            404, "not_found", "Custom rule not found.", {"kind": "custom_rule", "id": rule_id}
        )
    if rule.status != "active":
        raise ApiError(
            409, "rule_retired", "This custom rule has been retired.", {"rule_id": rule_id}
        )
    return rule


def _ensure_ticket(
    db: Any, settings: Settings, rec: drafts.DraftRecord, *, now: datetime, origin: str
) -> tickets.TicketRow:
    state = rec.body
    text = _all_text(state)
    intent = (state.model.understood if state.model and state.model.understood else text) or text
    return tickets.create_ticket(
        db,
        settings,
        account=rec.owner,
        origin=origin,
        intent=intent,
        desired_severity=_desired_severity(state, text),
        doc_types=_doc_types_of(state, text),
        missing=_missing_of(state),
        original_words=text,
        draft_id=rec.id,
        weaker_rule_id=None,
        now=now,
    )


def _route_unsupported(
    db: Any, settings: Settings, rec: drafts.DraftRecord, *, now: datetime
) -> None:
    """An unsupported request is sent to the tech team at once (once per draft)."""
    if rec.body.status == "unsupported" and rec.body.ticket_id is None:
        rec.body.ticket_id = _ensure_ticket(db, settings, rec, now=now, origin="unsupported").id


# --------------------------------------------------------------------------------------
# Rendering (everything the officer reads is built here, from state and code)
# --------------------------------------------------------------------------------------


def _ideas(state: drafts.DraftState, text: str) -> list[Idea]:
    ideas: list[Idea] = []
    weaker = parse_spec(state.weaker)[0]
    if weaker is not None:
        ideas.append(Idea(kind="weaker_rule", text=readback.readback(weaker), spec=weaker))
    model = state.model
    blob = " ".join(
        [
            text,
            model.understood if model else "",
            *(p.phrase for p in (model.unsupported_parts if model else [])),
        ]
    )
    doc_types = set(_doc_types_of(state, blob))
    # the built-ins that talk about the same things as the request come first (the same words), then the
    # ones that merely look at the same documents
    words = {_stem(w) for w in _tokens(blob) if len(w) >= 4 and w not in STOP_WORDS}
    related = duplicates.related_builtins(doc_types, limit=20)
    related.sort(
        key=lambda i: -len(words & {_stem(w) for w in _tokens(f"{i.rule} {i.text}") if len(w) >= 4})
    )
    ideas += related[:3]
    for template in state.model.closest_templates if state.model else []:
        ideas.append(Idea(kind="template", text=TEMPLATE_TEXT[template], template=template))
    return ideas[:MAX_IDEAS]


def render(db: Any, rec: drafts.DraftRecord) -> DraftResponse:
    """The response for a draft in its current state. Duplicates are looked up again each time, so a rule
    saved or adopted since the last turn is noticed."""
    state = rec.body
    status = state.status
    spec = parse_spec(state.spec)[0] if status not in ("refused", "unsupported") else None
    model = state.model
    parts = list(model.unsupported_parts) if model else []
    text = _all_text(state)
    candidates = [
        CandidateView(index=i, spec=s, readback=readback.readback(s))
        for i, s in enumerate(parse_spec(c)[0] for c in state.candidates)
        if s is not None
    ]
    dups: list[DuplicateNote] = []
    if spec is not None:
        dups = duplicates.find_duplicates(db, spec, viewer=rec.owner, exclude_rule=rec.edit_of)
    questions = list(state.questions)
    save_allowed = (
        rec.state == "open"
        and status in ("ready", "partially_supported")
        and spec is not None
        and not questions
        and duplicates.blocking(dups) is None
    )
    notes = list(state.notes)
    if rec.turns >= DRAFT_MAX_TURNS and rec.state == "open":
        notes.append(
            "This was the last turn of the draft. Save it, or send the idea to the tech team."
        )
    if status == "unsupported":
        notes.append(
            "This is too complex for a custom rule today. It goes to the tech team; the ideas below show "
            "what is possible and which built-in checks already look at related things."
        )
    ticket = tickets.get_ticket(db, state.ticket_id) if state.ticket_id else None
    dry = (
        state.dry_run
        if spec is not None and state.dry_run and state.dry_run.spec_hash == spec.spec_hash()
        else None
    )
    return DraftResponse(
        draft_id=rec.id,
        turn=rec.turns,
        turns_left=max(0, DRAFT_MAX_TURNS - rec.turns),
        status=status,
        understood=clean_text(model.understood, 300) if model else "",
        spec=spec,
        spec_hash=spec.spec_hash() if spec else None,
        readback=readback.readback(spec) if spec else None,
        candidates=candidates,
        questions=questions,
        assumptions=[clean_text(a, 200) for a in model.assumptions] if model else [],
        phrase_map=list(model.phrase_map) if model else [],
        unsupported_parts=parts,
        closest_templates=list(model.closest_templates) if model else [],
        will_check=readback.will_check(spec) if spec and status != "refused" else [],
        will_not_check=readback.will_not_check(parts, spec) if status != "refused" else [],
        refusal=state.refusal if status == "refused" else None,
        duplicates=dups,
        ideas=_ideas(state, text) if status in ("partially_supported", "unsupported") else [],
        ticket=(
            TicketRef(id=ticket.id, status="submitted", message=tickets.ticket_message(ticket.id))
            if ticket
            else None
        ),
        notes=notes,
        dry_run=dry,
        save_allowed=save_allowed,
        needs_narrower_ack=status == "partially_supported",
        edit_of=rec.edit_of,
        model_calls=state.last_calls,
    )


# --------------------------------------------------------------------------------------
# The conversation
# --------------------------------------------------------------------------------------


def start_draft(
    db: Any,
    settings: Settings,
    llm: LLMClient,
    *,
    account: str,
    req: StartDraftRequest,
    now: datetime,
) -> DraftResponse:
    """Turn 1: classify the request and answer with the first draft."""
    text = clean_request(req.text)
    edit_spec: CustomRuleSpec | None = None
    if req.edit_of:
        rule = _owned_active_rule(db, account, req.edit_of)
        version = store.get_version(db, rule.id, rule.current_version)
        edit_spec = version.spec if version else None
    state = drafts.DraftState(desired_severity=req.severity)
    refusal = precheck(text)
    outcome: ModelOutcome | None = None
    if refusal is not None:
        # a refused request is never sent on: the history keeps no text (the next turn shows the model
        # only what was not refused) and the audit log keeps only its hash
        state.history.append({"turn": 1, "kind": "refused", "at": now.isoformat()})
        state.status = state.model_status = "refused"
        state.refusal = refusal
    else:
        state.history.append({"turn": 1, "kind": "text", "text": text, "at": now.isoformat()})
        outcome = _ask(
            db,
            settings,
            llm,
            account=account,
            user=_start_user(text, req.severity, edit_spec),
            now=now,
            draft_id=None,
        )
        _apply_outcome(state, outcome, text=text, severity=req.severity)
        if state.status == "refused":  # refused by the model: nothing of the words is kept either
            state.history[-1] = {"turn": 1, "kind": "refused", "at": now.isoformat()}
    state.last_calls = outcome.calls if outcome else 0
    state.history[-1]["status"] = state.status
    rec = drafts.create_draft(
        db,
        owner=account,
        request="" if state.status == "refused" else text,
        edit_of=req.edit_of,
        state=state,
        prompt_sha256=capabilities.prompt_sha256(),
        now=now,
        model_calls=outcome.calls if outcome else 0,
        micro_usd=outcome.micro_usd if outcome else 0,
    )
    if outcome is not None:
        _audit_authoring(
            db, settings, account=account, draft_id=rec.id, turn=1, outcome=outcome, now=now
        )
    if state.status == "refused" and state.refusal is not None:
        _audit_refusal(
            db,
            settings,
            account=account,
            draft_id=rec.id,
            reason=state.refusal.reason,
            turn=1,
            text=text,
            now=now,
        )
    if state.status == "unsupported":
        before = rec.turns
        _route_unsupported(db, settings, rec, now=now)
        rec = drafts.update_draft(db, rec, expected_turns=before, now=now)
    return render(db, rec)


def _settle(state: drafts.DraftState) -> None:
    """An officer's answer or choice settles what the model left open: a claim of ambiguity or of needing
    clarification no longer holds the status down."""
    if state.model_status in ("ambiguous", "needs_clarification", "ready"):
        state.model_status = "ready"


def _carry_dropped_words(state: drafts.DraftState) -> None:
    """What the model left out of the rule is never settled by an answer to another question: each phrase
    it mapped to nothing, and each word of the request no phrase accounted for, becomes a part that the
    rule will NOT check. The officer then sees it and must tick that the rule checks less than asked."""
    model = state.model
    if model is None:
        return
    known = [p.phrase.lower().strip() for p in model.unsupported_parts]
    dropped = [
        m.phrase
        for m in model.phrase_map
        if m.slot is None and not _phrase_matches(m.phrase, known)
    ]
    blob = " ".join([*known, *(p.lower() for p in dropped)])
    dropped += [w for w in state.uncovered if not any(stem in blob for stem in _stems(w))]
    room = MAX_UNSUPPORTED - len(model.unsupported_parts)
    added = [
        UnsupportedPart(
            phrase=clean_text(p, 120),
            reason="needs_judgement",
            detail="The assistant did not turn this into any part of the rule.",
        )
        for p in dict.fromkeys(dropped)
    ][: max(room, 0)]
    if added:
        state.model = model.model_copy(
            update={"unsupported_parts": [*model.unsupported_parts, *added]}
        )
    if len(added) == len(set(dropped)):  # all of it fits; otherwise it still holds the draft back
        state.uncovered = []


def _recompute(state: drafts.DraftState) -> None:
    _carry_dropped_words(state)
    model = state.model
    if model is None:
        return
    spec = parse_spec(state.spec)[0]
    if model.status != "refused" and not model.refusal_reason:
        # a reading chosen, or a question answered, completes the rule: the currency caveat comes with it
        model = _with_currency_part(model, spec, _all_text(state))
        state.model = model
    candidates = [c for c in (parse_spec(x)[0] for x in state.candidates) if c is not None]
    status, notes = decide_status(
        model,
        spec=spec,
        problems=[],
        candidates=candidates,
        questions=state.questions,
        uncovered=state.uncovered,
        claimed=state.model_status,
    )
    state.status, state.notes = status, notes
    state.problems = []


def _bad_answer(message: str, problems: Sequence[SpecProblem] = ()) -> ApiError:
    return ApiError(
        422,
        "bad_answer",
        message,
        {"errors": [{"field": p.loc, "message": p.message} for p in problems]},
    )


def continue_draft(
    db: Any,
    settings: Settings,
    llm: LLMClient,
    *,
    account: str,
    draft_id: str,
    req: TurnRequest,
    now: datetime,
) -> DraftResponse:
    """One more turn: an answer or a choice (no model call), or more words (classified again)."""
    rec = _own_draft(db, account, draft_id)
    if rec.state != "open":
        raise ApiError(409, "draft_closed", "This draft is closed.", {"draft_id": rec.id})
    if rec.turns >= DRAFT_MAX_TURNS:
        raise ApiError(
            409,
            "draft_turn_limit",
            "This draft has used all its turns. Start again, or send the idea to the tech team.",
            {"max_turns": DRAFT_MAX_TURNS},
        )
    state = rec.body
    expected = rec.turns
    turn = rec.turns + 1
    state.last_calls = 0
    text_turn: str | None = None
    history: dict[str, Any] = {"turn": turn, "at": now.isoformat()}

    if (req.answer is not None or req.choose_candidate is not None) and state.status == "refused":
        raise _bad_answer("Nothing to answer: the last message was refused. Send new words.")
    if req.answer is not None:
        question = next((q for q in state.questions if q.id == req.answer.question_id), None)
        option = (
            next((o for o in question.options if o.id == req.answer.option_id), None)
            if question
            else None
        )
        if question is None or option is None:
            raise _bad_answer("There is no such open question or answer.")
        spec = parse_spec(state.spec)[0]
        if spec is None:  # nothing to put the answer into yet: the model reads it with the request
            text_turn = f"Question: {question.text}\nAnswer: {option.label} ({option.value})"
            history |= {"kind": "answer", "question_id": question.id, "option_id": option.id}
        else:
            new_spec, problems = apply_answer(spec, question, option)
            if new_spec is None:
                raise _bad_answer("That answer does not give a valid rule.", problems)
            state.spec = _spec_dict(new_spec)
            state.questions = [q for q in state.questions if q.id != question.id]
            state.dry_run = None
            _settle(state)
            _recompute(state)
            history |= {"kind": "answer", "question_id": question.id, "option_id": option.id}
    elif req.choose_candidate is not None:
        candidates = [parse_spec(c)[0] for c in state.candidates]
        index = req.choose_candidate
        if not 0 <= index < len(candidates) or candidates[index] is None:
            raise _bad_answer("There is no such reading to choose.")
        state.spec = state.candidates[index]
        state.candidates = []
        state.dry_run = None
        _settle(state)
        _recompute(state)
        history |= {"kind": "candidate", "index": index}
    else:
        assert req.text is not None
        new_text = clean_request(req.text)
        text_turn = new_text
        history |= {"kind": "text", "text": new_text}
        refusal = precheck(new_text)
        if (
            refusal is not None
        ):  # refused: the turn is counted and the draft is emptied, no model call
            history = {"turn": turn, "at": now.isoformat(), "kind": "refused"}
            state.status = state.model_status = "refused"
            state.refusal = refusal
            state.spec, state.weaker, state.model = None, None, None
            state.candidates, state.questions, state.notes = [], [], []
            state.dry_run = None
            text_turn = None
            _audit_refusal(
                db,
                settings,
                account=account,
                draft_id=rec.id,
                reason=refusal.reason,
                turn=turn,
                text=new_text,
                now=now,
            )

    claimed = False
    try:
        if text_turn is not None:
            # marked before the paid call: parallel text turns on one draft cannot each buy a call
            drafts.claim_model(db, rec.id, expected_turns=expected, now=now)
            claimed = True
            earlier = _all_text(state)
            combined_text = (
                earlier if history["kind"] == "answer" else f"{earlier} {text_turn}".strip()
            )
            spec = parse_spec(state.spec)[0]
            if rec.edit_of and spec is None:
                edit_rule = store.get_rule(db, rec.edit_of)
                version = (
                    store.get_version(db, rec.edit_of, edit_rule.current_version)
                    if edit_rule
                    else None
                )
                spec = version.spec if version else None
            user = _continue_user(earlier, text_turn, spec, state.questions, state.desired_severity)
            outcome = _ask(db, settings, llm, account=account, user=user, now=now, draft_id=rec.id)
            _apply_outcome(state, outcome, text=combined_text, severity=state.desired_severity)
            state.last_calls = outcome.calls
            if (
                state.status == "refused"
            ):  # refused by the model: the words are not kept or sent again
                history = {"turn": turn, "at": now.isoformat(), "kind": "refused"}
            drafts.add_spend(db, rec.id, calls=outcome.calls, micro_usd=outcome.micro_usd)
            _audit_authoring(
                db, settings, account=account, draft_id=rec.id, turn=turn, outcome=outcome, now=now
            )
            if state.status == "refused" and state.refusal is not None:
                _audit_refusal(
                    db,
                    settings,
                    account=account,
                    draft_id=rec.id,
                    reason=state.refusal.reason,
                    turn=turn,
                    text=text_turn,
                    now=now,
                )
    except Exception:
        if claimed:
            drafts.release_model(db, rec.id)
        raise
    history["status"] = state.status
    state.history.append(history)
    rec.turns = turn
    rec.body = state
    rec = _commit(db, settings, rec, expected_turns=expected, now=now, release_model=claimed)
    return render(db, rec)


def _commit(
    db: Any,
    settings: Settings,
    rec: drafts.DraftRecord,
    *,
    expected_turns: int,
    now: datetime,
    release_model: bool = False,
) -> drafts.DraftRecord:
    """Store the turn. ``release_model``: this turn set the "waiting for the model" mark, so its commit
    clears it (nobody else's)."""
    _route_unsupported(db, settings, rec, now=now)
    return drafts.update_draft(
        db, rec, expected_turns=expected_turns, now=now, release_model=release_model
    )


def saved_dry_run(
    db: Any, account: str, draft_id: str | None, spec_hash: str
) -> dict[str, Any] | None:
    """The dry run made for a rule's current version when it was saved (the owner's, when the draft is
    still there and ran this exact spec)."""
    rec = drafts.load_draft(db, draft_id) if draft_id else None
    if rec is None or rec.owner != account:
        return None
    dry = rec.body.dry_run
    return dry.model_dump(mode="json") if dry and dry.spec_hash == spec_hash else None


def get_draft(db: Any, *, account: str, draft_id: str) -> DraftResponse:
    return render(db, _own_draft(db, account, draft_id))


def dry_run_draft(
    db: Any, settings: Settings, *, account: str, draft_id: str, now: datetime
) -> DryRunResult:
    """Run the draft's spec over the stored cases and keep the result with the draft (saving needs it)."""
    rec = _own_draft(db, account, draft_id)
    if rec.state != "open":
        raise ApiError(409, "draft_closed", "This draft is closed.", {"draft_id": rec.id})
    spec = (
        parse_spec(rec.body.spec)[0] if rec.body.status not in ("refused", "unsupported") else None
    )
    if spec is None:
        raise ApiError(
            409, "no_spec", "This draft has no valid rule to test yet.", {"draft_id": rec.id}
        )
    result = dryrun.dry_run(db, spec, limit=settings.custom_rules_dry_run_cases)
    rec.body.dry_run = result
    drafts.update_draft(db, rec, expected_turns=rec.turns, now=now)
    return result


# --------------------------------------------------------------------------------------
# Tickets
# --------------------------------------------------------------------------------------


def ticket_from_draft(
    db: Any, settings: Settings, *, account: str, draft_id: str, severity: str | None, now: datetime
) -> tuple[tickets.TicketRow, bool]:
    """Send a draft to the tech team. ``(ticket, existing)``; a draft has at most one ticket. A refused
    draft is never ticketed."""
    rec = _own_draft(db, account, draft_id)
    if rec.body.status == "refused":
        raise ApiError(
            409,
            "draft_refused",
            "A refused request cannot be sent to the tech team.",
            {"reason": rec.body.refusal.reason if rec.body.refusal else None},
        )
    existing = tickets.ticket_for_draft(db, rec.id)
    if existing is not None:
        return existing, True
    if severity and not rec.body.desired_severity:
        rec.body.desired_severity = severity
    origin = (
        "unsupported"
        if rec.body.status == "unsupported"
        else ("partial" if rec.body.model and rec.body.model.unsupported_parts else "officer")
    )
    ticket = _ensure_ticket(db, settings, rec, now=now, origin=origin)
    if rec.state == "open":
        rec.body.ticket_id = ticket.id
        drafts.update_draft(db, rec, expected_turns=rec.turns, now=now)
    return ticket, False


def ticket_from_text(
    db: Any, settings: Settings, *, account: str, text: str, severity: str | None, now: datetime
) -> tickets.TicketRow:
    """The officer's own words, sent to the tech team without the assistant (works with no model and no
    budget). The same pre-checks apply: a refused request is not ticketed."""
    cleaned = clean_request(text)
    refusal = precheck(cleaned)
    if refusal is not None:
        _audit_refusal(
            db,
            settings,
            account=account,
            draft_id="",
            reason=refusal.reason,
            turn=0,
            text=cleaned,
            now=now,
        )
        raise ApiError(409, "draft_refused", refusal.message, {"reason": refusal.reason})
    return tickets.create_ticket(
        db,
        settings,
        account=account,
        origin="officer",
        intent=cleaned,
        desired_severity=severity or _severity_word(cleaned),
        doc_types=_doc_types_of(drafts.DraftState(), cleaned),
        missing=[],
        original_words=cleaned,
        draft_id=None,
        weaker_rule_id=None,
        now=now,
    )


# --------------------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------------------


def _clean_name(value: str | None) -> str:
    name = clean_text(value or "", 10_000)
    if len(name) < RULE_NAME_MIN or len(name) > RULE_NAME_MAX:
        raise ApiError(
            422,
            "name_required",
            f"Give the rule a name of {RULE_NAME_MIN} to {RULE_NAME_MAX} characters.",
            {"field": "name"},
        )
    return name


def save_draft(
    db: Any,
    settings: Settings,
    *,
    account: str,
    req: SaveRuleRequest,
    admins: frozenset[str],
    now: datetime,
) -> SaveResult:
    """Save a draft as a new rule (or, for an edit, a new version of the rule). The gates, in order: the
    draft is the account's and open; the status and spec allow it; a narrower rule is acknowledged; no
    built-in or custom rule already does this; the spec changed (edits); a dry run of exactly this spec
    exists and its warnings are confirmed; a name; a reason; room under the account's limit."""
    del admins  # reserved: saving is always the owner's own act
    with _save_lock:
        rec = _own_draft(db, account, req.draft_id)
        if rec.state != "open":
            raise ApiError(409, "draft_closed", "This draft is closed.", {"draft_id": rec.id})
        state = rec.body
        spec = (
            parse_spec(state.spec)[0] if state.status in ("ready", "partially_supported") else None
        )
        if spec is None or state.questions:
            raise ApiError(
                422,
                "draft_not_ready",
                "This draft cannot be saved yet: it needs a valid rule and no open question.",
                {"status": state.status, "open_questions": len(state.questions)},
            )
        if state.status == "partially_supported" and not req.accept_narrower:
            raise ApiError(
                422,
                "narrower_not_accepted",
                "This rule checks less than you asked for. Confirm that you accept this to save it.",
                {"will_not_check": readback.will_not_check(state.model.unsupported_parts, spec)},
            )
        notes = duplicates.find_duplicates(db, spec, viewer=account, exclude_rule=rec.edit_of)
        block = duplicates.blocking(notes)
        if block is not None and block.kind == "builtin":
            raise ApiError(
                409,
                "duplicate_builtin",
                "A built-in check already does this. " + block.action,
                {"rule": block.rule, "what": block.what, "action": block.action},
            )
        if block is not None:
            raise ApiError(
                409,
                "duplicate_custom",
                "A custom rule that does the same already exists. " + block.action,
                {"rule_id": block.rule, "name": block.name},
            )
        edited: store.RuleRow | None = None
        if rec.edit_of:
            edited = _owned_active_rule(db, account, rec.edit_of)
            current = store.get_version(db, edited.id, edited.current_version)
            if current is not None and current.spec_hash == spec.spec_hash():
                raise ApiError(
                    422,
                    "no_change",
                    "This is the same as the current version of the rule.",
                    {"rule_id": edited.id},
                )
        dry = state.dry_run
        if dry is None or dry.spec_hash != spec.spec_hash():
            raise ApiError(
                409,
                "dry_run_required",
                "Run the test on stored cases for this exact rule before saving it.",
                {"spec_hash": spec.spec_hash()},
            )
        if dry.needs_confirmation and not req.confirm_warnings:
            raise ApiError(
                422,
                "warnings_not_confirmed",
                "The test on stored cases gave a warning. Confirm that you want to save anyway.",
                {"warnings": [w.model_dump(mode="json") for w in dry.warnings]},
            )
        name = _clean_name(req.name if req.name is not None else (edited.name if edited else None))
        description = clean_text(
            req.description
            if req.description is not None
            else (edited.description if edited else ""),
            RULE_DESCRIPTION_MAX,
        )
        reason = clean_reason(req.reason)
        if (
            edited is None
            and store.count_active_owned(db, account) >= settings.custom_rules_max_per_account
        ):
            raise ApiError(
                409,
                "rule_limit",
                f"You already have {settings.custom_rules_max_per_account} active custom rules. "
                "Retire one first.",
                {"max_rules": settings.custom_rules_max_per_account},
            )
        text = readback.readback(spec)
        # the rule and the closing of the draft are ONE transaction: a turn that got in since the draft was
        # read makes the closing fail (409 draft_busy) and the rule is not created either
        with db.conn() as c:
            if edited is not None:
                rule = store.add_version(
                    db,
                    edited.id,
                    actor=account,
                    spec=spec,
                    readback=text,
                    reason=reason,
                    at=now,
                    draft_id=rec.id,
                    name=name,
                    description=description,
                    conn=c,
                )
            else:
                rule = store.create_rule(
                    db,
                    owner=account,
                    name=name,
                    description=description,
                    spec=spec,
                    readback=text,
                    reason=reason,
                    at=now,
                    draft_id=rec.id,
                    conn=c,
                )
            rec.state, rec.saved_rule_id, rec.body = "saved", rule.id, state
            drafts.update_draft(db, rec, expected_turns=rec.turns, now=now, conn=c)
        ticket: tickets.TicketRow | None = None
        if req.submit_ticket and state.model and state.model.unsupported_parts:
            ticket = tickets.create_ticket(
                db,
                settings,
                account=account,
                origin="partial",
                intent=state.model.understood or _all_text(state),
                desired_severity=spec.severity,
                doc_types=_doc_types_of(state, _all_text(state)),
                missing=_missing_of(state),
                original_words=_all_text(state),
                draft_id=rec.id,
                weaker_rule_id=rule.id,
                now=now,
            )
            drafts.attach_ticket(db, rec.id, ticket.id)
    version = store.get_version(db, rule.id, rule.current_version)
    assert version is not None
    return SaveResult(rule=rule, version=version, ticket=ticket)
