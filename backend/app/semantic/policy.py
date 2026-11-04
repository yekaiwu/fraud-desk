"""Rules of engagement for model-derived signals (docs/semantic-signals.md, invariants 1 and 3).

Pure constants and helpers; no imports from the rest of the app but the custom-rule spec constants, so
the registry, the queue and the scorer can all use them without an import cycle.
"""

from __future__ import annotations

from typing import Protocol

from app.custom_rules.spec import CUSTOM_RULE_PREFIX, is_custom_rule  # noqa: F401 (re-exported)

SEMANTIC_RULE_PREFIX = "sem_"  # every semantic rule id starts with this; the queue relies on it
SEMANTIC_MAX_SEVERITY = "medium"  # an LLM-derived flag is never high
SEMANTIC_VERSION = "1"  # bump when claim handling changes (part of the cache key)
SEMANTIC_STAGES = ("rules", "crossdoc")  # semantic checks run in an existing flag stage

_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}


# Model-derived checks that have a switch of their own, off by default: rule -> setting name. The
# check runs only when ``GLDB_SEMANTIC`` is on AND its own setting is on.
OPT_IN_SETTINGS: dict[str, str] = {"sem_entity_link": "semantic_entity"}


class _HasSemantic(Protocol):
    semantic: str


def is_semantic_rule(rule: str) -> bool:
    return rule.startswith(SEMANTIC_RULE_PREFIX)


# Skip codes of a check that was never asked to run on this deployment: an external source that is not
# configured (no switch, token, key or snapshot) or that private mode blocks. They stay in the case's
# check runs, so nothing is hidden, but they are outside the coverage ratio: a deployment that never
# turned a source on must not see every bundle's coverage fall below the fast-lane floor. "Configured
# but could not be reached" has no code here and still counts as not run.
NOT_ASKED_CODES: frozenset[str] = frozenset({"not_configured", "private_mode"})


def counts_in_coverage(rule: str, status: str, code: str | None = None) -> bool:
    """Whether a check run enters the coverage ratio. A model-derived check counts only when it
    failed (``error``): a failure must lower coverage, but a normal skip (the case has nothing for
    it to read) or a run must leave coverage exactly as it is with the feature off. A skip with a
    ``NOT_ASKED_CODES`` code (source not configured, or blocked by private mode) is never counted.
    A custom rule (docs/custom-rules.md) never counts, whatever happened to it: one that ran, skipped or
    failed leaves coverage exactly as it is without it, so it can neither lower coverage nor lift a case
    over the fast-lane floor."""
    if is_custom_rule(rule):
        return False
    if status == "skipped" and code in NOT_ASKED_CODES:
        return False
    return status == "error" or not is_semantic_rule(rule)


def semantic_enabled(settings: _HasSemantic) -> bool:
    """``GLDB_SEMANTIC`` is anything but ``off``."""
    return settings.semantic != "off"


def switched_off_by_setting(settings: object, rule: str) -> str | None:
    """The reason a model-derived check is switched off by its own setting (``None`` if it is not).
    Reported as a skip with code ``switched_off``: not an error, not a pass, and outside coverage."""
    name = OPT_IN_SETTINGS.get(rule)
    if name is None or getattr(settings, name, "off") == "on":
        return None
    env = "GLDB_" + name.upper()
    return f"Switched off by setting {env}=off"


def cap_severity(severity: str) -> str:
    """The severity a model-derived flag may carry: never above ``medium``."""
    if _RANK.get(severity, 0) > _RANK[SEMANTIC_MAX_SEVERITY]:
        return SEMANTIC_MAX_SEVERITY
    return severity
