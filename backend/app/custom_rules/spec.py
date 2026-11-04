"""Custom rules: the rule spec, the severity policy and the authoring draft (docs/custom-rules.md).

An officer describes a new check in plain English. The model fills ``ModelDraft`` (a closed schema) and
code decides what happens next (``DraftResponse``). A saved rule is DATA, never code: one of four
templates with one condition each, run by the fixed engine in ``app/custom_rules/engine.py``.

* ``field_vs_constant``: a field of a document type compared with a number, a date or an enum value.
* ``date_window``: a date field older or newer than N days before the case's ``today`` (or after it).
* ``field_missing``: a field is absent or empty on a document of that type that is present.
* ``fs_ratio``: the sum of one to four financial-statement lines compared with a multiplier times
  another line, on the current or the prior-year column.

Everything a rule may address is derived from ``DOC_SCHEMAS`` and ``FS_LINES``, so the spec can never
name a field the extractor does not read. Protected attributes (nationality, sex, date of birth,
person names, see docs/name-fairness.md) are not addressable at all. A custom flag is never above
``medium`` and never a hard stop. Numbers are decimal strings (never floats) and their ranges are
checked here, after parsing: the repo's own JSON validator ignores ``minimum``/``maximum``.

This module imports nothing from the app but ``core.models`` and ``core.schemas``, so the registry,
the scorer and the policy module can import it without a cycle. Text in a draft that came from the
officer or the model is untrusted plain text: it is stored and shown as text, never as markup.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.schemas import DOC_SCHEMAS, EXTRACTABLE_TYPES, FS_LINES

# --------------------------------------------------------------------------------------
# Rule ids and the severity policy
# --------------------------------------------------------------------------------------

CUSTOM_RULE_PREFIX = "cus_"  # every custom rule id starts with this; never collides with a built-in
RULE_ID_RE = re.compile(r"\Acus_[0-9a-f]{10}\Z")
SPEC_VERSION = 1  # bump when the meaning of a stored spec changes

CustomSeverity = Literal["info", "low", "medium"]
CUSTOM_SEVERITIES: tuple[str, ...] = get_args(CustomSeverity)
CUSTOM_MAX_SEVERITY: CustomSeverity = "medium"  # never high, never a hard stop
_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}

RULE_NAME_MIN, RULE_NAME_MAX = 3, 80
RULE_DESCRIPTION_MAX = 400
REQUEST_MIN_CHARS, REQUEST_MAX_CHARS = 3, 1000  # one officer message


def is_custom_rule(rule: str) -> bool:
    return rule.startswith(CUSTOM_RULE_PREFIX)


def new_rule_id() -> str:
    return CUSTOM_RULE_PREFIX + uuid.uuid4().hex[:10]


def cap_custom_severity(severity: str) -> CustomSeverity:
    """The severity a custom flag may carry: never above ``medium`` (unknown values become ``low``)."""
    if severity not in _RANK:
        return "low"
    if _RANK[severity] > _RANK[CUSTOM_MAX_SEVERITY]:
        return CUSTOM_MAX_SEVERITY
    return severity  # type: ignore[return-value]


# --------------------------------------------------------------------------------------
# Templates and operators
# --------------------------------------------------------------------------------------

TemplateId = Literal["field_vs_constant", "date_window", "field_missing", "fs_ratio"]
TEMPLATES: tuple[str, ...] = get_args(TemplateId)

CompareOp = Literal["gt", "gte", "lt", "lte", "eq", "ne"]
ListOp = Literal["in", "not_in"]
FieldOp = Literal["gt", "gte", "lt", "lte", "eq", "ne", "in", "not_in"]
RatioOp = Literal["gt", "gte", "lt", "lte"]
DateDirection = Literal["older_than", "newer_than", "in_future"]
FsColumn = Literal["current", "prior"]

COMPARE_OPS: tuple[str, ...] = get_args(CompareOp)
LIST_OPS: tuple[str, ...] = get_args(ListOp)
RATIO_OPS: tuple[str, ...] = get_args(RatioOp)

# Plain words, used by the code-built readback and by the capability prompt (one source).
TEMPLATE_TEXT: dict[str, str] = {
    "field_vs_constant": "one field of one document type compared with a fixed number, date or listed value",
    "date_window": "a date field more than, or less than, N days before the case date, or after it",
    "field_missing": "a field that is absent or empty on a document of that type in the case",
    "fs_ratio": (
        "the sum of one to four financial-statement lines compared with a multiplier times another "
        "line, on the current or the prior-year column"
    ),
}
OPERATOR_TEXT: dict[str, str] = {
    "gt": "is greater than",
    "gte": "is at least",
    "lt": "is less than",
    "lte": "is at most",
    "eq": "is",
    "ne": "is not",
    "in": "is one of",
    "not_in": "is none of",
}
DATE_OPERATOR_TEXT: dict[str, str] = {
    "gt": "is after",
    "gte": "is on or after",
    "lt": "is before",
    "lte": "is on or before",
    "eq": "is",
    "ne": "is not",
}

# Which operators suit which field kind. A kind that is not listed (text, id, bool) can only be used
# with ``field_missing``: there is no "greater than" on a name.
NUMERIC_KINDS: frozenset[str] = frozenset({"money", "decimal", "percent", "int"})
OPERATORS_BY_KIND: dict[str, tuple[str, ...]] = {
    **{k: COMPARE_OPS for k in NUMERIC_KINDS},
    "date": COMPARE_OPS,
    "enum": ("eq", "ne", "in", "not_in"),
}

MAX_LIST_VALUES = 20
MAX_LIST_VALUE_CHARS = 40
MAX_ABS_NUMBER = Decimal("1000000000000000")  # 10**15
MAX_NUMBER_DP = 4
MIN_DATE, MAX_DATE = date(1900, 1, 1), date(2100, 12, 31)
MAX_DAYS = 36_500
FS_MAX_LEFT_LINES = 4
FS_MAX_MULTIPLIER = Decimal("1000")

# --------------------------------------------------------------------------------------
# What a rule may address (derived from DOC_SCHEMAS, so it never drifts)
# --------------------------------------------------------------------------------------

# Protected attributes and person names (docs/name-fairness.md): not addressable by any template,
# on any document type. The request pre-check in author.py refuses rules that talk about them.
PROTECTED_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "nationality",
        "issuing_country",
        "sex",
        "date_of_birth",
        "mrz_line1",
        "mrz_line2",
        "holder_name",
        "surname",
        "given_names",
        "declarant_name",
        "signatory_seller",
        "signatory_buyer",
        "address",
    }
)
FS_RULE_TABLES: tuple[str, ...] = ("balance_sheet", "income_statement")
# a catch-all line means nothing on its own
FS_EXCLUDED_LINES: frozenset[str] = frozenset({"other"})


@dataclass(frozen=True)
class AddressableField:
    """One document field a rule may name: ``path`` is ``"<doc_type>.<field>"``."""

    path: str
    doc_type: str
    field: str
    kind: str
    required: bool
    # the allowed values of an enum field (None: a free enum such as currency)
    values: tuple[str, ...] | None

    @property
    def operators(self) -> tuple[str, ...]:
        return OPERATORS_BY_KIND.get(self.kind, ())


@dataclass(frozen=True)
class FsLine:
    """One financial-statement line a ``fs_ratio`` rule may name: ``path`` is ``"<table>.<line>"``."""

    path: str
    table: str
    line: str


@functools.cache
def addressable_fields() -> dict[str, AddressableField]:
    """Every addressable document field by path, in schema order. Protected fields are left out."""
    out: dict[str, AddressableField] = {}
    for doc_type in EXTRACTABLE_TYPES:
        for name, spec in DOC_SCHEMAS[doc_type].fields.items():
            if name in PROTECTED_FIELD_NAMES:
                continue
            path = f"{doc_type}.{name}"
            out[path] = AddressableField(
                path=path,
                doc_type=doc_type,
                field=name,
                kind=spec.kind,
                required=spec.required,
                values=tuple(spec.values) if spec.values else None,
            )
    return out


@functools.cache
def fs_lines() -> dict[str, FsLine]:
    """Every financial-statement line a ``fs_ratio`` rule may name, by path."""
    return {
        f"{table}.{line}": FsLine(path=f"{table}.{line}", table=table, line=line)
        for table in FS_RULE_TABLES
        for line in FS_LINES[table]
        if line not in FS_EXCLUDED_LINES
    }


def field_info(path: str) -> AddressableField | None:
    return addressable_fields().get(path)


def is_protected_path(path: str) -> bool:
    """A ``doc_type.field`` path that names a protected attribute (refused, never 'unknown field')."""
    _, _, name = path.partition(".")
    return name in PROTECTED_FIELD_NAMES


# --------------------------------------------------------------------------------------
# Values (decimal strings and ISO dates; never floats)
# --------------------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?[0-9]{1,16}(\.[0-9]{1,4})?", re.ASCII)
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_CURRENCY_RE = re.compile(r"[A-Z]{3}", re.ASCII)


def parse_number(text: str) -> Decimal | None:
    """A plain decimal string (``"-12.5"``, ``"100000"``; no separators, at most 4 decimals) within
    +-10**15, else None."""
    if not isinstance(text, str) or not _NUMBER_RE.fullmatch(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if abs(value) <= MAX_ABS_NUMBER else None


def parse_date_value(text: str) -> date | None:
    """An ISO date ``YYYY-MM-DD`` between 1900-01-01 and 2100-12-31, else None."""
    if not isinstance(text, str) or not _DATE_RE.fullmatch(text):
        return None
    try:
        value = date.fromisoformat(text)
    except ValueError:
        return None
    return value if MIN_DATE <= value <= MAX_DATE else None


def normal_number(text: str) -> str:
    """Canonical text of a parsed number: ``"2.50"`` and ``"2.5"`` are both ``"2.5"``, ``"-0"`` is ``"0"``."""
    value = Decimal(text)
    if value == 0:
        return "0"
    out = format(value.normalize(), "f")
    return out.rstrip("0").rstrip(".") if "." in out else out


def _value_problem(info: AddressableField, value: str) -> str | None:
    """Why ``value`` cannot be compared with ``info``'s field (None when it can)."""
    if info.kind in NUMERIC_KINDS:
        if parse_number(value) is None:
            return f"{value!r} is not a plain number (digits, optional '-', at most 4 decimals)"
        return None
    if info.kind == "date":
        if parse_date_value(value) is None:
            return f"{value!r} is not a date written YYYY-MM-DD between 1900 and 2100"
        return None
    if info.kind == "enum":
        if info.values is not None:
            if value not in info.values:
                return f"{value!r} is not one of {', '.join(info.values)}"
            return None
        if not _CURRENCY_RE.fullmatch(value):
            return f"{value!r} is not a three-letter currency code such as SGD"
        return None
    return f"a {info.kind} field cannot be compared with a value"


def _canonical_value(info: AddressableField, value: str) -> str:
    return normal_number(value) if info.kind in NUMERIC_KINDS else value


def _sha256(data: Any) -> str:
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# The four templates (one condition each, no AND/OR)
# --------------------------------------------------------------------------------------


class _Frozen(BaseModel):
    """Strict (no type coercion), closed (no extra keys) and immutable."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _field_or_raise(path: str) -> AddressableField:
    if is_protected_path(path):
        raise ValueError(f"{path} is a protected attribute and cannot be used in a rule")
    info = field_info(path)
    if info is None:
        raise ValueError(f"{path} is not a field this system reads")
    return info


class FieldVsConstant(_Frozen):
    """T1: ``<doc_type>.<field> <operator> <value>`` (or ``in``/``not_in`` a list, for enums)."""

    template: Literal["field_vs_constant"]
    field: str = Field(min_length=3, max_length=80)
    operator: FieldOp
    value: str | None = None
    values: list[str] | None = None

    @model_validator(mode="after")
    def _check(self) -> FieldVsConstant:
        info = _field_or_raise(self.field)
        if not info.operators:
            raise ValueError(
                f"{self.field} is a {info.kind} field: it can only be checked for being missing"
            )
        if self.operator not in info.operators:
            raise ValueError(
                f"operator {self.operator} does not suit the {info.kind} field {self.field} "
                f"(allowed: {', '.join(info.operators)})"
            )
        if self.operator in LIST_OPS:
            if self.value is not None or not self.values:
                raise ValueError(f"operator {self.operator} needs 'values' (a list) and no 'value'")
            if len(self.values) > MAX_LIST_VALUES:
                raise ValueError(f"at most {MAX_LIST_VALUES} values")
            if len(set(self.values)) != len(self.values):
                raise ValueError("the values repeat")
            for v in self.values:
                if len(v) > MAX_LIST_VALUE_CHARS:
                    raise ValueError(f"a value may have at most {MAX_LIST_VALUE_CHARS} characters")
                problem = _value_problem(info, v)
                if problem:
                    raise ValueError(problem)
        else:
            if self.value is None or self.values is not None:
                raise ValueError(f"operator {self.operator} needs one 'value' and no 'values'")
            problem = _value_problem(info, self.value)
            if problem:
                raise ValueError(problem)
        return self

    def canonical(self) -> dict[str, Any]:
        info = _field_or_raise(self.field)
        operator = self.operator
        out: dict[str, Any] = {"template": self.template, "field": self.field}
        if self.values is not None:
            values = sorted({_canonical_value(info, v) for v in self.values})
            if len(values) == 1 and operator in ("in", "not_in"):
                # 'in [X]' is 'is X' and 'not in [X]' is 'is not X': one rule, one hash
                operator = "eq" if operator == "in" else "ne"
                out["value"] = values[0]
            else:
                out["values"] = values
            out["operator"] = operator
        else:
            out["operator"] = operator
            assert self.value is not None
            out["value"] = _canonical_value(info, self.value)
        return out

    def paths(self) -> tuple[str, ...]:
        return (self.field,)


class DateWindow(_Frozen):
    """T2: a date field more than ``days`` before the case date (``older_than``), less than ``days``
    before it or later (``newer_than``), or after the case date (``in_future``, no ``days``)."""

    template: Literal["date_window"]
    field: str = Field(min_length=3, max_length=80)
    direction: DateDirection
    days: int | None = None

    @model_validator(mode="after")
    def _check(self) -> DateWindow:
        info = _field_or_raise(self.field)
        if info.kind != "date":
            raise ValueError(f"{self.field} is a {info.kind} field, not a date")
        if self.direction == "in_future":
            if self.days is not None:
                raise ValueError("'in_future' takes no days")
        elif self.days is None or not 0 <= self.days <= MAX_DAYS:
            raise ValueError(f"'{self.direction}' needs days between 0 and {MAX_DAYS}")
        return self

    def canonical(self) -> dict[str, Any]:
        direction, days = self.direction, self.days
        if direction == "newer_than" and days == 0:
            # 'less than 0 days before the case date or later' is 'after the case date'
            direction, days = "in_future", None
        out: dict[str, Any] = {
            "template": self.template,
            "field": self.field,
            "direction": direction,
        }
        if days is not None:
            out["days"] = days
        return out

    def paths(self) -> tuple[str, ...]:
        return (self.field,)


class FieldMissing(_Frozen):
    """T3: the field is absent or empty (blank after trimming) on a usable document of its type."""

    template: Literal["field_missing"]
    field: str = Field(min_length=3, max_length=80)

    @model_validator(mode="after")
    def _check(self) -> FieldMissing:
        _field_or_raise(self.field)
        return self

    def canonical(self) -> dict[str, Any]:
        return {"template": self.template, "field": self.field}

    def paths(self) -> tuple[str, ...]:
        return (self.field,)


class FsRatio(_Frozen):
    """T4: ``sum(left) <operator> multiplier x right`` on one column of a financial statement.

    ``left`` and ``right`` are ``"<table>.<line>"`` paths of ``fs_lines()``. A left line that is not
    printed counts as 0 as long as at least one left line is printed; a statement without the right
    line is skipped (engine.py)."""

    template: Literal["fs_ratio"]
    left: list[str] = Field(min_length=1, max_length=FS_MAX_LEFT_LINES)
    operator: RatioOp
    multiplier: str = Field(min_length=1, max_length=24)
    right: str = Field(min_length=3, max_length=80)
    column: FsColumn = "current"

    @model_validator(mode="after")
    def _check(self) -> FsRatio:
        lines = fs_lines()
        for path in [*self.left, self.right]:
            if path not in lines:
                raise ValueError(f"{path} is not a financial-statement line this system reads")
        if len(set(self.left)) != len(self.left):
            raise ValueError("a line repeats on the left")
        if self.right in self.left:
            raise ValueError("the right-hand line is also on the left")
        m = parse_number(self.multiplier)
        if m is None or m <= 0 or m > FS_MAX_MULTIPLIER:
            raise ValueError(
                f"the multiplier must be a number above 0 and at most {FS_MAX_MULTIPLIER}"
            )
        return self

    def canonical(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "left": sorted(self.left),
            "operator": self.operator,
            "multiplier": normal_number(self.multiplier),
            "right": self.right,
            "column": self.column,
        }

    def paths(self) -> tuple[str, ...]:
        return (*sorted(self.left), self.right)


Condition = Annotated[
    FieldVsConstant | DateWindow | FieldMissing | FsRatio, Field(discriminator="template")
]


class CustomRuleSpec(_Frozen):
    """A complete custom rule: what it checks and how serious a match is. Name, description and
    owner are metadata of the stored rule (store.py), not part of the spec."""

    spec_version: Literal[1] = 1
    severity: CustomSeverity
    condition: Condition

    @property
    def template(self) -> str:
        return self.condition.template

    def doc_types(self) -> tuple[str, ...]:
        """The document types the rule reads (it runs only when one of them is in the case)."""
        if isinstance(self.condition, FsRatio):
            return ("financial_statement",)
        return (self.condition.field.partition(".")[0],)

    def paths(self) -> tuple[str, ...]:
        """Every field or line path the rule reads."""
        return self.condition.paths()

    def canonical(self) -> dict[str, Any]:
        """Order- and spelling-independent form: list values sorted, numbers normalised."""
        return {
            "spec_version": self.spec_version,
            "severity": self.severity,
            "condition": self.condition.canonical(),
        }

    def condition_hash(self) -> str:
        """sha256 of the canonical CONDITION only: two rules that check the same thing at different
        severities are duplicates (duplicates.py)."""
        return _sha256(self.condition.canonical())

    def spec_hash(self) -> str:
        """sha256 of the whole canonical spec: recorded on every check run and in provenance."""
        return _sha256(self.canonical())


class SpecProblem(_Frozen):
    """Why a spec the model wrote is not usable (code-written text, shown to the officer)."""

    loc: str = Field(max_length=120)
    message: str = Field(max_length=300)


def parse_spec(raw: object) -> tuple[CustomRuleSpec | None, list[SpecProblem]]:
    """Validate a spec the model (or an old stored row) produced. Never raises."""
    if raw is None:
        return None, []
    try:
        return CustomRuleSpec.model_validate(raw), []
    except ValidationError as exc:
        problems = [
            SpecProblem(
                loc=".".join(str(p) for p in e.get("loc", ()))[:120] or "spec",
                message=str(e.get("msg", "invalid"))[:300],
            )
            for e in exc.errors()[:5]
        ]
        return None, problems


# --------------------------------------------------------------------------------------
# Classification and the authoring draft
# --------------------------------------------------------------------------------------

DraftStatus = Literal[
    "ready", "needs_clarification", "ambiguous", "partially_supported", "unsupported", "refused"
]
DRAFT_STATUSES: tuple[str, ...] = get_args(DraftStatus)

DRAFT_MAX_TURNS = 6  # officer messages and answers per draft, the first request included
MODEL_CALLS_PER_TURN = 2  # one call, plus one repair or schema retry at most
MAX_CANDIDATES = 3
MAX_QUESTIONS = 3
MAX_ASSUMPTIONS = 5
MAX_PHRASES = 12
MAX_UNSUPPORTED = 6
MAX_IDEAS = 6
# a dry run that fires on more than this share of the cases evaluated needs a confirmation to save
DRY_RUN_MAX_FIRE_RATE = 0.30

UnsupportedReason = Literal[
    "field_not_extracted",  # the document carries it, but the extractor does not read it
    "doc_not_extracted",  # a document type the system does not read
    "needs_external",  # an outside source or lookup
    "needs_trend",  # more than current against prior year
    "needs_image",  # pictures, logos, layout, signatures
    "needs_judgement",  # "looks suspicious", "unusual"
    "needs_cross_case",  # compares with other cases or customers
    "needs_cross_document",  # compares two documents (these templates read one)
    "needs_algorithm",  # counting, grouping, matching names, statistics
    "needs_several_conditions",  # AND / OR of conditions (one condition per rule in this version)
    "policy_forbidden",  # allowed by nothing: protected attributes, built-ins, the score
]
UNSUPPORTED_REASONS: tuple[str, ...] = get_args(UnsupportedReason)
UNSUPPORTED_REASON_TEXT: dict[str, str] = {
    "field_not_extracted": "the system does not read this from the documents",
    "doc_not_extracted": "the system does not read this kind of document",
    "needs_external": "it needs outside data or a lookup, which custom rules cannot use",
    "needs_trend": "it needs a trend over more than this year and last year",
    "needs_image": "it needs the picture of the page (images, logos, layout, signatures)",
    "needs_judgement": "it needs a judgement that cannot be written as a fixed condition",
    "needs_cross_case": "it compares with other cases, which custom rules cannot see",
    "needs_cross_document": "it compares two documents; a custom rule reads one document type",
    "needs_algorithm": "it needs counting, grouping or matching that the templates do not do",
    "needs_several_conditions": "it needs more than one condition; a custom rule has exactly one",
    "policy_forbidden": "custom rules are not allowed to do this",
}

RefusalReason = Literal[
    "high_severity",
    "hard_stop",
    "protected_attribute",
    "changes_builtin",
    "changes_score",
    "prompt_injection",
    "nonsense",
    "model_refused",
]
REFUSAL_REASONS: tuple[str, ...] = get_args(RefusalReason)
REFUSAL_TEXT: dict[str, str] = {
    "high_severity": (
        "A custom rule can be info, low or medium, never high. High findings come only from "
        "checks the development team wrote and tested."
    ),
    "hard_stop": "A custom rule can never block the fast lane or stop a case on its own.",
    "protected_attribute": (
        "A rule may not look at nationality, sex, age or date of birth, or at people's names. "
        "If you meant how old a document is, say how many days before the case date it is dated."
    ),
    "changes_builtin": (
        "A custom rule cannot switch off or change a built-in check. Use the check catalogue to "
        "switch a check off or change its severity."
    ),
    "changes_score": "A custom rule cannot change how the score, coverage or lanes are worked out.",
    "prompt_injection": "The request contains instructions to the system rather than a rule.",
    "nonsense": "The request could not be read as a rule. Describe what should be flagged and when.",
    "model_refused": "The assistant declined this request.",
}

PhraseSlot = Literal[
    "doc_type",
    "field",
    "operator",
    "value",
    "days",
    "direction",
    "left",
    "right",
    "multiplier",
    "column",
    "severity",
    "filler",  # carries no meaning for the rule ("please", "flag it")
]
QuestionSlot = Literal[
    "severity",
    "field",
    "operator",
    "value",
    "values",
    "days",
    "direction",
    "multiplier",
    "column",
    "left",
    "right",
]
QUESTION_SLOTS: tuple[str, ...] = get_args(QuestionSlot)

_OPTION_ID = r"^[a-z0-9_]{1,24}$"
_QUESTION_ID = r"^q[0-9]{1,2}$"


class PhraseMapping(_Frozen):
    """One phrase of the officer's request and the slot of the spec it became (None: not mapped,
    which code never lets through as ``ready``)."""

    phrase: str = Field(min_length=1, max_length=120)
    slot: PhraseSlot | None


class UnsupportedPart(_Frozen):
    """A phrase of the request the templates cannot express, and why."""

    phrase: str = Field(min_length=1, max_length=120)
    reason: UnsupportedReason
    # the model's own words: untrusted, shown as plain text
    detail: str = Field(default="", max_length=200)


class QuestionOption(_Frozen):
    """One answer to a question. ``value`` is the slot's new value as text (a number, a path, an
    operator; for ``left`` the paths joined by commas); code applies it and validates the result."""

    id: str = Field(pattern=_OPTION_ID)
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=200)


class Question(_Frozen):
    """A question to the officer. ``default`` is a proposal shown as such: it is NEVER applied
    without the officer choosing it."""

    id: str = Field(pattern=_QUESTION_ID)
    slot: QuestionSlot
    text: str = Field(min_length=1, max_length=200)
    options: list[QuestionOption] = Field(min_length=2, max_length=4)
    default: str | None = None  # an option id

    @model_validator(mode="after")
    def _check(self) -> Question:
        ids = [o.id for o in self.options]
        if len(set(ids)) != len(ids):
            raise ValueError("option ids repeat")
        if self.default is not None and self.default not in ids:
            raise ValueError("the default is not one of the options")
        return self


class ModelDraft(_Frozen):
    """What the model returns for one turn (the closed schema of ``capabilities.model_output_schema``).

    The specs are left as raw objects here and validated one by one with ``parse_spec``, so a single
    bad spec downgrades the draft instead of losing the whole answer. The model's ``status`` is a
    proposal: ``author.decide_status`` decides the final one."""

    status: DraftStatus
    understood: str = Field(max_length=300)
    spec: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    questions: list[Question] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    assumptions: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=MAX_ASSUMPTIONS
    )
    phrase_map: list[PhraseMapping] = Field(default_factory=list, max_length=MAX_PHRASES)
    unsupported_parts: list[UnsupportedPart] = Field(
        default_factory=list, max_length=MAX_UNSUPPORTED
    )
    closest_templates: list[TemplateId] = Field(default_factory=list, max_length=len(TEMPLATES))
    refusal_reason: RefusalReason | None = None
    weaker_spec: dict[str, Any] | None = None  # a narrower rule the officer could save instead


# -- what the API returns (built by code from a ModelDraft, the pre-checks and the stores) ----------


class CandidateView(_Frozen):
    """One reading of an ambiguous request, with the code-built readback."""

    index: int = Field(ge=0, lt=MAX_CANDIDATES)
    spec: CustomRuleSpec
    readback: str


class Refusal(_Frozen):
    reason: RefusalReason
    message: str  # REFUSAL_TEXT[reason], code-written


class TicketRef(_Frozen):
    """A request routed to the tech team (tickets.py)."""

    id: str = Field(pattern=r"^crt_[0-9a-f]{12}$")
    status: Literal["submitted"] = "submitted"
    message: str  # "This will be submitted to the tech team (ticket crt_...)."


class DuplicateNote(_Frozen):
    """A built-in check or a custom rule that already checks this (``identical`` blocks saving and
    says where to go instead; ``similar`` is a warning)."""

    kind: Literal["builtin", "custom"]
    level: Literal["identical", "similar"]
    rule: str
    # a built-in's description, or a custom rule's name when the viewer may see that rule
    name: str | None = None
    what: str  # code-written: what the existing rule checks
    action: str  # code-written: what to do instead


class Idea(_Frozen):
    """Help with a request that is too complex: something close that IS possible."""

    kind: Literal["weaker_rule", "related_builtin", "template"]
    text: str  # code-written (a readback, a built-in's description, a template's description)
    spec: CustomRuleSpec | None = None  # kind weaker_rule
    rule: str | None = None  # kind related_builtin
    template: TemplateId | None = None  # kind template


class DryRunWarning(_Frozen):
    code: Literal["fires_on_none", "fires_too_often", "no_cases"]
    message: str


class DryRunResult(_Frozen):
    """The spec run over stored cases (dryrun.py). Counts only, plus a few case ids as examples."""

    spec_hash: str
    cases_considered: int = Field(ge=0)  # stored cases looked at (newest first, capped)
    evaluated: int = Field(ge=0)  # cases with a usable document of the rule's type
    fired: int = Field(ge=0)  # cases where the rule raised at least one flag
    # evaluated cases where the field was missing on every document of the type
    skipped_missing: int = Field(ge=0)
    scanned_evaluated: int = Field(ge=0)  # evaluated cases whose document is a scan or a photo
    scanned_fired: int = Field(ge=0)
    fire_rate: float = Field(ge=0, le=1)  # fired / evaluated (0 when nothing was evaluated)
    warnings: list[DryRunWarning] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list, max_length=5)  # case ids where it fired
    needs_confirmation: bool = False  # any warning: saving needs confirm_warnings=true


class DraftResponse(_Frozen):
    """One turn of the authoring conversation, as the API returns it. Every sentence in it is built
    by code except ``understood``, ``assumptions``, question texts and option labels and
    ``unsupported_parts[].detail`` (model text, untrusted, shown as plain text) and the officer's
    own phrases."""

    draft_id: str = Field(pattern=r"^crd_[0-9a-f]{12}$")
    turn: int = Field(ge=1, le=DRAFT_MAX_TURNS)
    turns_left: int = Field(ge=0, le=DRAFT_MAX_TURNS - 1)
    status: DraftStatus
    understood: str = ""
    spec: CustomRuleSpec | None = None
    spec_hash: str | None = None
    readback: str | None = None  # readback.py, from the spec only
    candidates: list[CandidateView] = Field(default_factory=list, max_length=MAX_CANDIDATES)
    questions: list[Question] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    assumptions: list[str] = Field(default_factory=list, max_length=MAX_ASSUMPTIONS)
    phrase_map: list[PhraseMapping] = Field(default_factory=list, max_length=MAX_PHRASES)
    unsupported_parts: list[UnsupportedPart] = Field(
        default_factory=list, max_length=MAX_UNSUPPORTED
    )
    closest_templates: list[TemplateId] = Field(default_factory=list, max_length=len(TEMPLATES))
    will_check: list[str] = Field(default_factory=list)  # code-built: "This rule WILL check"
    will_not_check: list[str] = Field(default_factory=list)  # code-built: "It will NOT check"
    refusal: Refusal | None = None
    duplicates: list[DuplicateNote] = Field(default_factory=list)
    ideas: list[Idea] = Field(default_factory=list, max_length=MAX_IDEAS)
    ticket: TicketRef | None = None
    notes: list[str] = Field(default_factory=list)  # code-written: why the status was lowered
    dry_run: DryRunResult | None = None  # the last dry run of THIS spec, when one was made
    save_allowed: bool = False
    needs_narrower_ack: bool = False  # partially_supported: saving needs accept_narrower=true
    edit_of: str | None = None  # the rule this draft would give a new version
    model_calls: int = Field(default=0, ge=0, le=MODEL_CALLS_PER_TURN)  # made in this turn

    @field_validator("edit_of")
    @classmethod
    def _rule_id(cls, v: str | None) -> str | None:
        if v is not None and not RULE_ID_RE.match(v):
            raise ValueError("not a custom rule id")
        return v
