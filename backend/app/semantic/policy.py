"""Rules of engagement for model-derived signals (docs/semantic-signals.md, invariants 1 and 3).

Pure constants and helpers; no imports from the rest of the app, so the registry, the queue and the
scorer can all use them without an import cycle.
"""

from __future__ import annotations

from typing import Protocol

SEMANTIC_RULE_PREFIX = "sem_"  # every semantic rule id starts with this; the queue relies on it
SEMANTIC_MAX_SEVERITY = "medium"  # an LLM-derived flag is never high
SEMANTIC_VERSION = "1"  # bump when claim handling changes (part of the cache key)
SEMANTIC_STAGES = ("rules", "crossdoc")  # semantic checks run in an existing flag stage

_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}


class _HasSemantic(Protocol):
    semantic: str


def is_semantic_rule(rule: str) -> bool:
    return rule.startswith(SEMANTIC_RULE_PREFIX)


def counts_in_coverage(rule: str, status: str) -> bool:
    """Whether a check run enters the coverage ratio. A model-derived check counts only when it
    failed (``error``): a failure must lower coverage, but a normal skip (the case has nothing for
    it to read) or a run must leave coverage exactly as it is with the feature off."""
    return status == "error" or not is_semantic_rule(rule)


def semantic_enabled(settings: _HasSemantic) -> bool:
    """``GLDB_SEMANTIC`` is anything but ``off``."""
    return settings.semantic != "off"


def cap_severity(severity: str) -> str:
    """The severity a model-derived flag may carry: never above ``medium``."""
    if _RANK.get(severity, 0) > _RANK[SEMANTIC_MAX_SEVERITY]:
        return SEMANTIC_MAX_SEVERITY
    return severity
