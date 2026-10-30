"""Registration of semantic checks (docs/semantic-signals.md, invariant 1).

``@semantic_check`` is ``@check`` for model-derived checks, with three differences. The check goes
into a registry of its own, so ``checks.registry.all_checks()`` (the catalogue, the ruleset hash, the
effectiveness report) does not see it unless ``GLDB_SEMANTIC`` is on. The rule id must start with
``sem_``. And its severity can never be above ``medium``: the returned flags are capped again here.

To add one, put a module under ``app/semantic/checks/``; ``discover_semantic()`` imports every module
there (and only runs when the feature is on or a ``sem_`` rule is looked up).
"""

from __future__ import annotations

import importlib
import pkgutil
import threading
from collections.abc import Callable, Iterable

from app.checks.registry import CheckMeta
from app.core.config import Settings
from app.core.context import CaseContext
from app.core.models import Flag, FlagStage, Severity
from app.semantic.policy import (
    SEMANTIC_MAX_SEVERITY,
    SEMANTIC_RULE_PREFIX,
    SEMANTIC_STAGES,
    cap_severity,
    semantic_enabled,
)

_SEMANTIC: dict[str, CheckMeta] = {}
_lock = threading.RLock()
_discovered = False


def semantic_check(
    *,
    rule: str,
    applies_to: Iterable[str],
    description: str,
    severity: Severity = "medium",
    stage: FlagStage = "rules",
    requires: Iterable[str] = frozenset(),
    use_cases: Iterable[str] = frozenset({"kyc", "aml", "loan"}),
    version: str = "1",
) -> Callable[[Callable[[CaseContext], list[Flag]]], Callable[[CaseContext], list[Flag]]]:
    """Register a model-derived check. The function is returned unchanged (tests call it directly)
    and must raise ``SemanticNotRun`` (never return ``[]``) when the model call fails."""
    if not rule.startswith(SEMANTIC_RULE_PREFIX):
        raise ValueError(f"semantic rule {rule!r} must start with {SEMANTIC_RULE_PREFIX!r}")
    if stage not in SEMANTIC_STAGES:
        raise ValueError(f"semantic rule {rule!r}: stage must be one of {SEMANTIC_STAGES}")
    if cap_severity(severity) != severity:
        raise ValueError(f"semantic rule {rule!r}: severity above {SEMANTIC_MAX_SEVERITY}")

    def decorator(func: Callable[[CaseContext], list[Flag]]) -> Callable[[CaseContext], list[Flag]]:
        meta = CheckMeta(
            rule=rule,
            stage=stage,
            severity=severity,
            applies_to=frozenset(applies_to),
            requires=frozenset(requires),
            use_cases=frozenset(use_cases),
            description=description,
            tamper=None,
            version=version,
            func=func,
        )
        with _lock:
            existing = _SEMANTIC.get(rule)
            if existing is not None and (
                existing.func.__module__ != func.__module__
                or existing.func.__qualname__ != func.__qualname__
            ):
                raise ValueError(
                    f"semantic rule {rule!r} is already registered by another function"
                )
            _SEMANTIC[rule] = meta
        return func

    return decorator


def discover_semantic() -> None:
    """Import ``app.semantic.checks.*`` (not ``_private`` modules). Idempotent."""
    global _discovered
    with _lock:
        import app.semantic.checks as package

        for m in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
            if m.name.startswith("_"):
                continue
            name = f"{package.__name__}.{m.name}"
            try:
                importlib.import_module(name)
            except Exception as exc:
                raise ImportError(f"semantic check module {name} failed to import: {exc}") from exc
        _discovered = True


def semantic_checks(settings: Settings) -> list[CheckMeta]:
    """Registered semantic checks when the feature is on, else ``[]`` (and nothing is imported)."""
    if not semantic_enabled(settings):
        return []
    discover_semantic()
    with _lock:
        return list(_SEMANTIC.values())


def get_semantic_check(rule: str) -> CheckMeta | None:
    """Metadata of one semantic rule (imports the check modules on first use)."""
    with _lock:
        meta = _SEMANTIC.get(rule)
    if meta is None and not _discovered:
        discover_semantic()
        with _lock:
            meta = _SEMANTIC.get(rule)
    return meta


def unregister(rule: str) -> None:
    """For tests: forget a rule registered by a test module."""
    with _lock:
        _SEMANTIC.pop(rule, None)
