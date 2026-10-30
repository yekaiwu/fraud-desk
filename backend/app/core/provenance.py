"""Reproducibility stamp (plan 7.2; docs/data-model.md `Provenance`)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from app.core.cache import canonical_json
from app.core.config import APP_VERSION
from app.core.context import CaseContext
from app.core.llm import prompt_registry
from app.core.models import Case, Flag, Provenance, RuleOverride, utcnow

if TYPE_CHECKING:
    from app.checks.registry import CheckMeta


def ruleset_version(checks: Sequence[CheckMeta]) -> str:
    """``"rs-" + sha256(sorted "rule@version" joined by newline)[:12]``."""
    entries = sorted(f"{c.rule}@{c.version}" for c in checks)
    return "rs-" + hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()[:12]


def flags_digest(flags: Iterable[Flag]) -> str:
    """sha256 of the sorted ``(flag.id, severity, evidence)`` triples; independent of flag order."""
    rows = sorted((f.id, f.severity, f.evidence) for f in flags)
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def settings_hash(ctx_settings) -> str:  # noqa: ANN001 - Settings, kept loose to avoid a cycle
    return hashlib.sha256(
        canonical_json(ctx_settings.result_settings()).encode("utf-8")
    ).hexdigest()


def catalogue_stamp(overrides: Mapping[str, RuleOverride]) -> dict[str, Any] | None:
    """``{"overrides": [{rule, enabled, severity, revision}...sorted by rule], "hash": sha256}``;
    ``None`` when the check catalogue has no override, so a case analysed without any keeps a
    provenance (and every hash) identical to before overrides existed."""
    if not overrides:
        return None
    entries = [
        {"rule": rule, "enabled": o.enabled, "severity": o.severity, "revision": o.revision}
        for rule, o in sorted(overrides.items())
    ]
    return {
        "overrides": entries,
        "hash": hashlib.sha256(canonical_json(entries).encode("utf-8")).hexdigest(),
    }


def _join(values: Iterable[str]) -> str:
    return "+".join(sorted(set(values)))


def classifier_versions(case: Case) -> dict[str, str]:
    """Derived from the data: the distinct ``"<classifier>@<version>"`` strings in
    ``PageInfo.classification`` (C1), ``Document.issuer`` (C3) and ``Row.annotations`` c4/c5."""
    found: dict[str, set[str]] = {"C1": set(), "C3": set(), "C4": set(), "C5": set()}
    for f in case.files:
        for p in f.pages:
            if p.classification:
                found["C1"].add(f"{p.classification.classifier}@{p.classification.version}")
    for d in case.documents:
        if d.issuer:
            found["C3"].add(f"{d.issuer.classifier}@{d.issuer.version}")
        for t in d.tables.values():
            for r in t.rows:
                for key, q in (("c4", "C4"), ("c5", "C5")):
                    a = r.annotations.get(key)
                    if a:
                        found[q].add(f"{a.classifier}@{a.version}")
    return {q: _join(v) for q, v in found.items() if v}


def extractor_versions(case: Case) -> dict[str, str]:
    found: dict[str, set[str]] = {}
    for d in case.documents:
        if d.extractor:
            found.setdefault(d.doc_type, set()).add(d.extractor)
    return {t: _join(v) for t, v in sorted(found.items())}


def build_provenance(ctx: CaseContext) -> Provenance:
    """Stamp the case after ``score``: ruleset, settings, prompts, models, inputs, flags digest."""
    from app.checks.registry import all_checks

    case = ctx.case
    s = ctx.settings
    fake = s.effective_llm_backend() == "fake"
    prompts = prompt_registry()
    prompt_hashes = {
        purpose: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for purpose, text in prompts.items()
    }
    prompt_hash = hashlib.sha256(
        "".join(f"{p}\0{h}\0" for p, h in sorted(prompt_hashes.items())).encode("utf-8")
    ).hexdigest()
    classifier = s.classifier_mode
    if classifier in ("local", "cascade") and s.local_classifier_model:
        classifier += f":{s.local_classifier_model}"
    return Provenance(
        app_version=APP_VERSION,
        ruleset_version=ruleset_version(all_checks(s)),
        models={
            "extract_cheap": "fake" if fake else s.llm_model_cheap,
            "extract_strong": "fake" if fake else s.llm_model_strong,
            "classifier_c1": classifier,
        },
        prompt_hash=prompt_hash,
        prompt_hashes=prompt_hashes,
        classifier_versions=classifier_versions(case),
        extractor_versions=extractor_versions(case),
        input_hashes={f.id: f.sha256 for f in case.files},
        settings_hash=settings_hash(s),
        today=ctx.today,
        flags_digest=flags_digest(case.flags),
        created_at=utcnow(),
        catalogue=catalogue_stamp(ctx.overrides),
    )
