"""Auto-discovered check framework (docs/architecture.md section 4).

To add a check, add a module under ``app/checks/`` that decorates a function with ``@check``.
Nothing else is edited: ``discover()`` imports every module in the package. Checks are pure
``(ctx) -> list[Flag]``: no network, no wall clock, no mutation of ``ctx``.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence

from pydantic import ConfigDict, Field

from app.core.context import CaseContext, natural_key
from app.core.models import (
    FLAG_STAGE_ORDER,
    CheckRun,
    Document,
    Flag,
    FlagStage,
    Region,
    RuleOverride,
    Severity,
    Strict,
    clean_text,
)

BUILTIN_MODULES = ("app.core.builtin_checks",)
SKIP_MODULES = {"registry"}
USABLE_DOC_STATUSES = ("ok", "partial")
_DISC_BAD = re.compile(r"[^a-z0-9_:]")
SWITCHED_OFF = "switched_off"  # CheckRun.code of a check switched off in the check catalogue


class CheckSkipped(Exception):  # noqa: N818
    """Raised inside a check that cannot evaluate (missing field, currency mismatch ...)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class CheckMeta(Strict):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    rule: str
    stage: FlagStage = "rules"
    severity: Severity
    applies_to: frozenset[str]  # DocType values, or {"*"}; ANY-of
    requires: frozenset[str] = frozenset()  # ALL-of
    use_cases: frozenset[str] = frozenset({"kyc", "aml", "loan"})
    description: str
    tamper: str | None = None
    version: str = "1"
    func: Callable[[CaseContext], list[Flag]] = Field(exclude=True)


_REGISTRY: dict[str, CheckMeta] = {}
_lock = threading.RLock()
_discovered = False


def check(
    *,
    rule: str,
    applies_to: Iterable[str],
    severity: Severity,
    description: str,
    stage: FlagStage = "rules",
    requires: Iterable[str] = frozenset(),
    use_cases: Iterable[str] = frozenset({"kyc", "aml", "loan"}),
    tamper: str | None = None,
    version: str = "1",
) -> Callable[[Callable[[CaseContext], list[Flag]]], Callable[[CaseContext], list[Flag]]]:
    """Register a check. The decorated function is returned unchanged (so tests can call it)."""

    def decorator(func: Callable[[CaseContext], list[Flag]]) -> Callable[[CaseContext], list[Flag]]:
        meta = CheckMeta(
            rule=rule,
            stage=stage,
            severity=severity,
            applies_to=frozenset(applies_to),
            requires=frozenset(requires),
            use_cases=frozenset(use_cases),
            description=description,
            tamper=tamper,
            version=version,
            func=func,
        )
        with _lock:
            existing = _REGISTRY.get(rule)
            if existing is not None and (
                existing.func.__module__ != func.__module__
                or existing.func.__qualname__ != func.__qualname__
            ):
                raise ValueError(f"check rule {rule!r} is already registered by another function")
            _REGISTRY[rule] = meta
        return func

    return decorator


def discover() -> None:
    """Import ``app.checks.*`` (except ``_private`` modules and ``registry``) and the built-in
    checks. Idempotent. A module that fails to import is re-raised with its name."""
    global _discovered
    with _lock:
        import app.checks as package

        names = [
            f"{package.__name__}.{m.name}"
            for m in pkgutil.iter_modules(package.__path__)
            if not m.name.startswith("_") and m.name not in SKIP_MODULES
        ]
        for name in [*sorted(names), *BUILTIN_MODULES]:
            try:
                importlib.import_module(name)
            except Exception as exc:
                raise ImportError(f"check module {name} failed to import: {exc}") from exc
        _discovered = True


def _stage_rank(stage: str) -> int:
    return FLAG_STAGE_ORDER.index(stage)


def all_checks() -> list[CheckMeta]:
    """Every registered check, sorted by (stage order, rule). Runs ``discover()`` first."""
    discover()
    with _lock:
        return sorted(_REGISTRY.values(), key=lambda m: (_stage_rank(m.stage), m.rule))


def get_check(rule: str) -> CheckMeta | None:
    """Metadata of a registered rule (runs discovery once if the rule is not known yet)."""
    with _lock:
        meta = _REGISTRY.get(rule)
    if meta is None and not _discovered:
        discover()
        with _lock:
            meta = _REGISTRY.get(rule)
    return meta


def applicable(ctx: CaseContext) -> list[CheckMeta]:
    """Checks whose ``use_cases`` contain the case's use case, plus any other check whose
    documents are present and usable. Scoping by use case alone would silently drop checks for
    documents that were uploaded and read fine (silence is not safety, plan principle 3); a check
    scoped out of a use case whose documents are absent is not counted, so coverage is not
    lowered for documents the bundle never carries."""
    return [
        m for m in all_checks() if ctx.use_case in m.use_cases or requirement_gap(m, ctx) is None
    ]


def _usable_types(ctx: CaseContext) -> set[str]:
    return {d.doc_type for d in ctx.case.documents if d.status in USABLE_DOC_STATUSES}


def requirement_gap(meta: CheckMeta, ctx: CaseContext) -> str | None:
    """Why ``meta`` cannot run on this case's documents (``None`` if it can)."""
    present = _usable_types(ctx)
    if "*" in meta.applies_to:
        if not (ctx.case.files or ctx.case.documents):
            return "no files or documents in the case"
    elif not (meta.applies_to & present):
        return "no usable document of type " + "/".join(sorted(meta.applies_to))
    missing = sorted(meta.requires - present)
    if missing:
        return "missing document of type " + ", ".join(missing)
    return None


def _clean_error(exc: Exception) -> str:
    msg = " ".join(str(exc).split())[:300]
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def _unique_id(flag_id: str, taken: set[str]) -> str:
    if flag_id not in taken:
        return flag_id
    n = 2
    while f"{flag_id}#{n}" in taken:
        n += 1
    return f"{flag_id}#{n}"


def switched_off_detail(override: RuleOverride) -> str:
    """Human-readable skip reason of a check that an officer switched off."""
    return clean_text(
        f"Switched off in the check catalogue by {override.updated_by} "
        f"on {override.updated_at[:10]}: {override.reason}",
        700,
    )


def run_stage_checks(ctx: CaseContext, stage: FlagStage) -> list[CheckRun]:
    """Run every applicable check of ``stage``; append flags to ``ctx.case.flags`` and the runs to
    ``ctx.case.check_runs``. Idempotent: the stage's previous flags and runs are replaced.

    Each check is wrapped in try/except and timed. A check that returns ``[]`` still counted as ran.

    Catalogue overrides (``ctx.overrides``): a check switched off is not executed and is recorded
    as skipped with code ``switched_off`` (it stays applicable, so coverage drops and the check is
    listed as not run); an override severity replaces the severity of every flag the check returns
    (the produced severity is kept in ``Flag.code_severity``, which hard stops are judged on).
    """
    case = ctx.case
    case.flags = [f for f in case.flags if f.stage != stage]
    case.check_runs = [r for r in case.check_runs if r.stage != stage]
    taken = {f.id for f in case.flags}
    runs: list[CheckRun] = []
    for meta in applicable(ctx):
        if meta.stage != stage:
            continue
        gap = requirement_gap(meta, ctx)
        if gap is not None:
            runs.append(CheckRun(rule=meta.rule, stage=stage, status="skipped", reason=gap))
            continue
        override = ctx.overrides.get(meta.rule)
        if override is not None and not override.enabled:
            runs.append(
                CheckRun(
                    rule=meta.rule,
                    stage=stage,
                    status="skipped",
                    reason=switched_off_detail(override),
                    code=SWITCHED_OFF,
                )
            )
            continue
        started = time.perf_counter()
        try:
            produced = list(meta.func(ctx))
        except CheckSkipped as skip:
            runs.append(
                CheckRun(
                    rule=meta.rule,
                    stage=stage,
                    status="skipped",
                    reason=skip.reason[:300],
                    seconds=time.perf_counter() - started,
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001 - one broken check must not stop the others
            runs.append(
                CheckRun(
                    rule=meta.rule,
                    stage=stage,
                    status="error",
                    reason=_clean_error(exc),
                    seconds=time.perf_counter() - started,
                )
            )
            continue
        seconds = time.perf_counter() - started
        for flag in produced:
            update: dict[str, object] = {
                "rule": meta.rule,
                "stage": meta.stage,
                "id": _unique_id(flag.id, taken),
            }
            if override is not None and override.severity and override.severity != flag.severity:
                update["severity"] = override.severity
                update["code_severity"] = flag.severity
            fixed = flag.model_copy(update=update)
            taken.add(fixed.id)
            case.flags.append(fixed)
        runs.append(
            CheckRun(
                rule=meta.rule,
                stage=stage,
                status="ran",
                flags=len(produced),
                seconds=seconds,
            )
        )
    case.check_runs.extend(runs)
    return runs


def clean_discriminator(value: str) -> str:
    """Lower-case and replace every character outside ``[a-z0-9_:]`` with ``_``."""
    return _DISC_BAD.sub("_", value.lower())


def _doc_id(doc: Document | str) -> str:
    return doc if isinstance(doc, str) else doc.id


def make_flag(
    rule: str,
    *,
    docs: Sequence[Document | str],
    evidence: str,
    explanation: str,
    table: str | None = None,
    field: str | None = None,
    row: int | None = None,
    region: Region | None = None,
    extra_regions: Sequence[Region] = (),
    severity: Severity | None = None,
    discriminator: str | None = None,
) -> Flag:
    """The only sanctioned way to build a flag.

    Id is ``"<rule>.<doc ids sorted, joined by '+'>.<locator>"`` where locator is ``field``, or
    ``"<table>:<row>"`` (row 0-based), or ``"-"``; ``~<discriminator>`` is appended when given.
    ``stage`` and default ``severity`` come from the registered check (``severity`` is then
    optional); an unregistered rule needs an explicit ``severity``.
    """
    meta = get_check(rule)
    if severity is None:
        if meta is None:
            raise ValueError(f"rule {rule!r} is not registered: pass severity explicitly")
        severity = meta.severity
    stage: FlagStage = meta.stage if meta is not None else "rules"
    doc_ids = sorted({_doc_id(d) for d in docs}, key=natural_key)
    if field:
        locator = field
    elif table is not None and row is not None:
        locator = f"{table}:{row}"
    else:
        locator = "-"
    locator = locator.replace(".", "_")
    flag_id = f"{rule}.{'+'.join(doc_ids)}.{locator}"
    if discriminator:
        flag_id += "~" + clean_discriminator(discriminator)
    return Flag(
        id=flag_id,
        rule=rule,
        stage=stage,
        severity=severity,
        document_ids=doc_ids,
        table=table,
        field=field,
        row=row,
        region=region,
        extra_regions=list(extra_regions),
        evidence=evidence,
        explanation=explanation,
    )


def checks_by_rule() -> dict[str, CheckMeta]:
    return {m.rule: m for m in all_checks()}
