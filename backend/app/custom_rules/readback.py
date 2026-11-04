"""The plain-English readback of a custom rule, built by code from the spec (docs/custom-rules.md).

The officer sees these sentences before saving and on every case the rule runs on. They are never
model prose: only the spec and ``labels`` go in, so what is read back is exactly what will run.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.core.models import clean_text
from app.custom_rules import labels
from app.custom_rules.spec import (
    DATE_OPERATOR_TEXT,
    OPERATOR_TEXT,
    UNSUPPORTED_REASON_TEXT,
    CustomRuleSpec,
    DateWindow,
    FieldMissing,
    FieldVsConstant,
    FsRatio,
    UnsupportedPart,
    field_info,
    normal_number,
    parse_date_value,
    parse_number,
)

EXPLANATION_MAX = 300  # Flag.explanation is cut at this length
PHRASE_SHOWN = 120


def _grouped(value: Decimal, places: int) -> str:
    """Thousands separators and at least ``places`` decimals; more when the constant has more, so the
    readback never shows a rounder number than the one that runs."""
    shown = len(normal_number(format(value, "f")).partition(".")[2])
    places = max(places, shown)
    return format(value.quantize(Decimal(1).scaleb(-places)), f",.{places}f")


def format_value(path: str, value: str) -> str:
    """A constant as an officer would write it: money ``100,000.00``, percent ``9%``, a date
    ``1 February 2025``, other numbers with thousands separators. Unknown input is returned as it is."""
    info = field_info(path)
    kind = info.kind if info is not None else "text"
    if kind == "date":
        d = parse_date_value(value)
        return f"{d.day} {d:%B} {d.year}" if d is not None else value
    if kind in ("money", "decimal", "percent", "int"):
        n = parse_number(value)
        if n is None:
            return value
        if kind == "money":
            return _grouped(n, 2)
        if kind == "percent":
            return normal_number(value) + "%"
        if kind == "int":
            return _grouped(n, 0)
        return normal_number(value)
    return value


def days_text(n: int) -> str:
    return f"{n:,} day" + ("" if n == 1 else "s")


def _subject(path: str) -> str:
    """``an invoice's total``: the field in the possessive of its document."""
    doc_type = path.partition(".")[0]
    label = labels.doc_label(doc_type)
    return f"{labels.article(label)} {label}'s {labels.field_name(path)}"


def _multiplier_text(text: str) -> str:
    value = normal_number(text)
    return "" if value == "1" else f"{value} times "


def condition_text(spec: CustomRuleSpec) -> str:
    """The condition only, in words (no severity, no full stop)."""
    c = spec.condition
    if isinstance(c, FieldVsConstant):
        info = field_info(c.field)
        if c.values is not None:
            listed = ", ".join(format_value(c.field, v) for v in c.values)
            return f"{_subject(c.field)} {OPERATOR_TEXT[c.operator]} {listed}"
        assert c.value is not None
        words = (
            DATE_OPERATOR_TEXT[c.operator]
            if info is not None and info.kind == "date"
            else OPERATOR_TEXT[c.operator]
        )
        return f"{_subject(c.field)} {words} {format_value(c.field, c.value)}"
    if isinstance(c, DateWindow):
        subject = _subject(c.field)
        if c.direction == "in_future" or (c.direction == "newer_than" and c.days == 0):
            return f"{subject} is after the case date"
        assert c.days is not None
        if c.direction == "older_than":
            if c.days == 0:
                return f"{subject} is before the case date"
            return f"{subject} is more than {days_text(c.days)} before the case date"
        return f"{subject} is less than {days_text(c.days)} before the case date, or later"
    if isinstance(c, FieldMissing):
        doc_type = c.field.partition(".")[0]
        label = labels.doc_label(doc_type)
        return f"{labels.article(label)} {label} in the case has no {labels.field_label(c.field)}"
    assert isinstance(c, FsRatio)
    left = " + ".join(labels.line_label(p) for p in c.left)
    column = "current-year" if c.column == "current" else "prior-year"
    return (
        f"on a financial statement's {column} column, {left} {OPERATOR_TEXT[c.operator]} "
        f"{_multiplier_text(c.multiplier)}{labels.line_label(c.right)}"
    )


def readback(spec: CustomRuleSpec) -> str:
    """``Flags low when an invoice's total is greater than 100,000.00.``"""
    comma = "," if isinstance(spec.condition, FsRatio) else ""
    return f"Flags {spec.severity} when{comma} {condition_text(spec)}."


def flag_explanation(spec: CustomRuleSpec) -> str:
    """The explanation on every flag of this rule: code text only. It never contains the rule's name or
    description (other accounts' cases and the review team's model calls read flag explanations)."""
    text = f"Custom rule (unreviewed): {condition_text(spec)}."
    return text if len(text) <= EXPLANATION_MAX else text[: EXPLANATION_MAX - 3] + "..."


def will_check(spec: CustomRuleSpec) -> list[str]:
    """The "This rule WILL check" block: the readback plus what the engine does around it."""
    doc_type = spec.doc_types()[0]
    out = [
        readback(spec),
        f"Only {labels.doc_plural(doc_type)} in the case are read.",
    ]
    c = spec.condition
    if isinstance(c, FieldMissing):
        out.append(
            "A document that was read only in part is flagged at low, not at the rule's severity."
        )
    else:
        out.append(
            "A document where the figure or date is not printed or could not be read is skipped, not flagged."
        )
    out.append("A reading that could not be confirmed lowers the flag to low and says so.")
    return out


def will_not_check(parts: Sequence[UnsupportedPart], spec: CustomRuleSpec | None) -> list[str]:
    """The "It will NOT check" block: each phrase the templates cannot express, with the reason in
    plain words, plus the one limit every ``fs_ratio`` rule has."""
    out: list[str] = []
    for part in parts:
        phrase = clean_text(part.phrase, PHRASE_SHOWN)
        out.append(f'"{phrase}": {UNSUPPORTED_REASON_TEXT[part.reason]}')
    if spec is not None and isinstance(spec.condition, FsRatio):
        right = labels.line_label(spec.condition.right)
        out.append(
            f"A line that is not printed counts as 0; a statement without {right} is skipped."
        )
    return out
