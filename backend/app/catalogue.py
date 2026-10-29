"""Check catalogue overrides (docs/scoring-and-queue.md "Catalogue overrides").

Checks stay defined in code (``@check``). An officer can lay an OVERRIDE on top of one: switch it
off, change its severity, or edit its display name, description and an internal note. Every edit
needs the reviewer's name and a written reason and is appended, with the values before and after,
to the permanent ``rule_changes`` log. The overrides are read once when an analysis starts, so a
change applies to analyses run afterwards and a stored result is never rewritten.

Name, description and note are display-only plain text: stored and returned, never given to a
model and never put into flags, evidence or prompts.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Literal

from pydantic import StrictBool

from app.checks.registry import CheckMeta, get_check
from app.core.db import Database
from app.core.errors import ApiError
from app.core.models import RuleOverride, Strict, clean_text

NAME_MAX = 80
DESCRIPTION_MAX = 400
NOTE_MAX = 600
REVIEWER_MIN, REVIEWER_MAX = 2, 80
REASON_MIN, REASON_MAX = 5, 500
TEXT_LIMITS: dict[str, int] = {"name": NAME_MAX, "description": DESCRIPTION_MAX, "note": NOTE_MAX}
CHANGES_DEFAULT_LIMIT, CHANGES_MAX_LIMIT = 50, 200

# the state that means "no override": the row is deleted and the change is logged as a reset
DEFAULT_STATE: dict[str, Any] = {
    "enabled": True,
    "severity": None,
    "name": None,
    "description": None,
    "note": None,
}
_FIELDS = tuple(DEFAULT_STATE)
_lock = threading.Lock()  # one process: serialises read-merge-write of an override


class OverrideRequest(Strict):
    """``PUT /rules/{rule}/override``. A field that is omitted keeps its value; ``null`` (or an
    empty string for the text fields) clears that field's override. ``reviewer`` and ``reason`` are
    checked by ``clean_reviewer`` / ``clean_reason`` so a missing one gets its own error code."""

    enabled: StrictBool | None = None
    severity: Literal["high", "medium", "low", "info"] | None = None
    name: str | None = None
    description: str | None = None
    note: str | None = None
    reviewer: str | None = None
    reason: str | None = None


class ResetRequest(Strict):
    """``DELETE /rules/{rule}/override`` (the same two values may come as query parameters)."""

    reviewer: str | None = None
    reason: str | None = None


def _too_long(field: str, limit: int) -> ApiError:
    return ApiError(
        422,
        "validation_error",
        f"The {field} may have at most {limit} characters.",
        {"errors": [{"field": field, "message": "too long", "type": "string_too_long"}]},
    )


def clean_field(field: str, value: str) -> str | None:
    """Plain text of one free-text field (control, bidi and zero-width characters become spaces,
    whitespace collapsed). Empty after cleaning means "clear the override of that field"."""
    cleaned = clean_text(value, 1_000_000)
    if len(cleaned) > TEXT_LIMITS[field]:
        raise _too_long(field, TEXT_LIMITS[field])
    return cleaned or None


def clean_reviewer(value: str | None) -> str:
    reviewer = clean_text(value or "", 1_000_000)
    if not REVIEWER_MIN <= len(reviewer) <= REVIEWER_MAX:
        raise ApiError(
            422,
            "reviewer_required",
            f"A reviewer name of {REVIEWER_MIN} to {REVIEWER_MAX} characters is required.",
            {"field": "reviewer"},
        )
    return reviewer


def clean_reason(value: str | None) -> str:
    reason = clean_text(value or "", 1_000_000)
    if len(reason) < REASON_MIN:
        raise ApiError(
            422,
            "reason_required",
            f"A reason of at least {REASON_MIN} characters is required.",
            {"field": "reason"},
        )
    if len(reason) > REASON_MAX:
        raise _too_long("reason", REASON_MAX)
    return reason


def rule_item(meta: CheckMeta, override: RuleOverride | None) -> dict[str, Any]:
    """One catalogue item: the code definition (``severity`` stays the code default) plus the
    effective settings."""
    return {
        "rule": meta.rule,
        "stage": meta.stage,
        "severity": meta.severity,
        "applies_to": sorted(meta.applies_to),
        "requires": sorted(meta.requires),
        "use_cases": sorted(meta.use_cases),
        "description": meta.description,
        "tamper": meta.tamper,
        "version": meta.version,
        "default_severity": meta.severity,
        "effective_severity": (override.severity if override else None) or meta.severity,
        "enabled": override.enabled if override else True,
        "override": override.model_dump() if override else None,
    }


def known_rule(rule: str) -> CheckMeta:
    meta = get_check(rule)
    if meta is None:
        raise ApiError(404, "unknown_rule", "No check has this rule id.", {"rule": rule})
    return meta


def put_override(db: Database, rule: str, req: OverrideRequest, *, now: datetime) -> dict[str, Any]:
    meta = known_rule(rule)
    reviewer = clean_reviewer(req.reviewer)
    reason = clean_reason(req.reason)
    with _lock:
        current = db.get_rule_override(rule)
        state = (
            {f: getattr(current, f) for f in _FIELDS}
            if current is not None
            else dict(DEFAULT_STATE)
        )
        new = dict(state)
        for field in req.model_fields_set & set(_FIELDS):
            value = getattr(req, field)
            if field in TEXT_LIMITS:
                value = clean_field(field, value) if value is not None else None
            elif field == "enabled" and value is None:
                raise ApiError(
                    422,
                    "validation_error",
                    "enabled must be true or false.",
                    {"errors": [{"field": "enabled", "message": "not null", "type": "bool_type"}]},
                )
            new[field] = value
        if new == state:
            raise ApiError(
                422,
                "no_change",
                "Nothing would change: these values are already in effect.",
                {"rule": rule},
            )
        # the defaults again: delete the row, keep the change log entry (action "reset")
        written = db.write_rule_override(
            rule, None if new == DEFAULT_STATE else new, reviewer=reviewer, reason=reason, at=now
        )
    return rule_item(meta, written)


def reset_override(db: Database, rule: str, req: ResetRequest, *, now: datetime) -> dict[str, Any]:
    meta = known_rule(rule)
    reviewer = clean_reviewer(req.reviewer)
    reason = clean_reason(req.reason)
    with _lock:
        if db.get_rule_override(rule) is None:
            raise ApiError(
                404, "no_override", "This check has no override to reset.", {"rule": rule}
            )
        db.write_rule_override(rule, None, reviewer=reviewer, reason=reason, at=now)
    return rule_item(meta, None)
