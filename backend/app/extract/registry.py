"""Extractor registry and auto-discovery (docs/ingest-and-extraction.md 3.3, architecture.md 4).

Extractors register with a decorator on a class implementing ``Extractor``::

    @register_extractor(doc_types={"invoice"}, backend="text_layer")     # alias: @extractor(...)
    class InvoiceTextLayer(TextLayerExtractor): ...

``discover()`` imports every module of ``app.extract`` whose name does not start with ``_`` so a new
file (for example extract-kyc's ``kyc_*.py``) registers without anyone editing an index.

Besides extractors, two per-document-type hooks live here so both backends share them:

* ``@register_validator(doc_types=...)``: ``fn(result, ctx, doc) -> Validation`` (arithmetic and
  structure; decides only whether to escalate, never edits a value);
* ``@register_postprocessor(doc_types=...)``: ``fn(result, ctx, doc) -> None`` applied to the final
  result of either backend (scaling, sign rules, derived flags).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import threading
from collections.abc import Callable
from typing import Any, Literal

log = logging.getLogger("gldb.extract")

Backend = Literal["text_layer", "llm"]

_lock = threading.RLock()
_EXTRACTORS: dict[str, dict[str, Any]] = {}
_HINTS: dict[str, list[str]] = {}
_VALIDATORS: dict[str, Callable[..., Any]] = {}
_POSTPROCESSORS: dict[str, Callable[..., Any]] = {}
_discovered = False


def register_extractor(
    *, doc_types: set[str] | frozenset[str], backend: Backend
) -> Callable[[type], type]:
    """Class decorator: register an instance of the class for each doc type and backend."""
    if backend not in ("text_layer", "llm"):
        raise ValueError(f"unknown extractor backend {backend!r}")
    types = frozenset(doc_types)
    if not types:
        raise ValueError("register_extractor needs at least one doc type")

    def deco(cls: type) -> type:
        instance = cls()
        with _lock:
            for dt in sorted(types):
                existing = _EXTRACTORS.setdefault(dt, {}).get(backend)
                if existing is not None and (
                    type(existing).__qualname__ != cls.__qualname__
                    or type(existing).__module__ != cls.__module__
                ):
                    raise ValueError(
                        f"extractor for {dt}/{backend} already registered by "
                        f"{type(existing).__module__}.{type(existing).__qualname__}"
                    )
                _EXTRACTORS[dt][backend] = instance
        return cls

    return deco


extractor = (
    register_extractor  # short alias used by the brief: @extractor(doc_types=..., backend=...)
)


def unregister_extractor(doc_type: str, backend: Backend) -> None:
    """Test helper: drop one registration."""
    with _lock:
        _EXTRACTORS.get(doc_type, {}).pop(backend, None)


def get_extractors(doc_type: str) -> dict[str, Any]:
    """``{"text_layer": inst, "llm": inst}``; keys present only if registered."""
    with _lock:
        return dict(_EXTRACTORS.get(doc_type, {}))


def has_extractor(doc_type: str) -> bool:
    """True when the type can be extracted: a type-specific (text layer) extractor is registered, or
    someone supplied prompt hints for it so the generic LLM extractor has real instructions. The LLM
    class itself is registered for every schema type and does not count on its own, so a type nobody
    owns yet (KYC types before extract-kyc lands) stays ``received_not_checked``."""
    with _lock:
        return "text_layer" in _EXTRACTORS.get(doc_type, {}) or bool(_HINTS.get(doc_type))


def registered_doc_types() -> set[str]:
    """Doc types that have at least one registered extractor (any backend)."""
    with _lock:
        return {dt for dt, by in _EXTRACTORS.items() if by}


def register_prompt_hints(doc_type: str, text: str) -> None:
    """Doc-type specific sentences appended to the common system prompt (identical text is kept once)."""
    with _lock:
        hints = _HINTS.setdefault(doc_type, [])
        if text not in hints:
            hints.append(text)


def prompt_hints(doc_type: str) -> list[str]:
    with _lock:
        return list(_HINTS.get(doc_type, []))


def register_validator(*, doc_types: set[str] | frozenset[str]) -> Callable[[Callable], Callable]:
    def deco(fn: Callable) -> Callable:
        with _lock:
            for dt in doc_types:
                _VALIDATORS[dt] = fn
        return fn

    return deco


def get_validator(doc_type: str) -> Callable[..., Any] | None:
    with _lock:
        return _VALIDATORS.get(doc_type)


def register_postprocessor(
    *, doc_types: set[str] | frozenset[str]
) -> Callable[[Callable], Callable]:
    def deco(fn: Callable) -> Callable:
        with _lock:
            for dt in doc_types:
                _POSTPROCESSORS[dt] = fn
        return fn

    return deco


def get_postprocessor(doc_type: str) -> Callable[..., Any] | None:
    with _lock:
        return _POSTPROCESSORS.get(doc_type)


def discover() -> None:
    """Import every module of ``app.extract`` (and its sub-packages) whose name does not start with
    ``_``. Idempotent; a module that fails to import is re-raised with its name."""
    import app.extract as pkg

    global _discovered
    with _lock:
        for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
            if info.name.startswith("_"):
                continue
            name = f"{pkg.__name__}.{info.name}"
            try:
                importlib.import_module(name)
            except Exception as exc:  # pragma: no cover - re-raised with the module name
                raise ImportError(f"extractor module {name} failed to import: {exc}") from exc
        _discovered = True
    # prompts depend on the hints every module registered; make provenance see them all
    try:
        from app.extract import llm_extractor

        llm_extractor.register_all_prompts()
    except ImportError:  # pragma: no cover
        log.debug("llm_extractor not importable during discovery")
