"""A second reading before a scan flag counts.

A flag raised on scanned pages can come from the content or from a misreading. The two readers err
differently (Tesseract text mixes up ID characters and name endings, the model reading page images
stumbles on the MRZ font), so a flag both reproduce is far more likely to be real. When a case has
medium or high flags on scanned documents, the case is read a second time by the other reader in a
shadow copy (never stored), the rules and cross-document checks run on that reading, and a flag
survives only if the shadow run raised the same rule on the same first document.

Flags the shadow run did not reproduce stay on the case as ``info`` with the reason in front of their
explanation, so an analyst can still see them and the audit trail is complete; they no longer add to
the score or the queue. If the second reading cannot be made (budget, error) nothing is dropped and a
warning says so. Duplicate-invoice flags and every forensic or model-based flag are left alone: they do
not depend on how the text was read.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from decimal import Decimal

from app.core.context import CaseContext
from app.core.models import Case, Flag
from app.semantic.policy import is_semantic_rule

log = logging.getLogger("gldb.verify")

CHECK_STAGES = ("rules", "crossdoc")
EXEMPT_RULES = frozenset({"inv_duplicate"})  # compares with earlier cases, not with the reading
NOTE = "Not confirmed by a second reading of the pages: "


def is_scan_document(case: Case, document_id: str, ctx: CaseContext) -> bool:
    doc = next((d for d in case.documents if d.id == document_id), None)
    if doc is None:
        return False
    try:
        pages = ctx.file(doc.file_id).pages
    except KeyError:
        return False
    return any(p.is_scan for p in pages if doc.page_start <= p.page <= doc.page_end)


def flag_key(case: Case, flag: Flag) -> tuple[str, str]:
    """(rule, file of the first document the flag names): stable between two readings, unlike
    document ids, row numbers and the set of statements a cross-document flag happens to cite."""
    first = next(
        (d for d in case.documents if flag.document_ids and d.id == flag.document_ids[0]), None
    )
    return flag.rule, first.file_id if first is not None else ""


def candidates(ctx: CaseContext) -> list[Flag]:
    out = []
    for f in ctx.case.flags:
        if (
            f.stage in CHECK_STAGES
            and f.severity in ("medium", "high")
            and not is_semantic_rule(f.rule)
            and f.rule not in EXEMPT_RULES
            and any(is_scan_document(ctx.case, d, ctx) for d in f.document_ids)
        ):
            out.append(f)
    return out


def other_reader(ctx: CaseContext) -> dict[str, object] | None:
    """Settings that make the shadow run read the pages the other way, or None when there is no other
    reader to ask (the second reading must differ from the first to be worth anything)."""
    s = ctx.settings
    if s.effective_llm_backend() != "anthropic":
        return None
    if s.ocr_backend in ("tesseract", "rapidocr", "vision"):
        return {"ocr_backend": "none", "llm_supports_vision": True, "llm_vision_fallback": False}
    from app.ingest.ocr_backends import find_tesseract

    if find_tesseract() is None:
        return None
    return {"ocr_backend": "tesseract", "llm_supports_vision": False, "llm_vision_fallback": False}


def shadow_reading(ctx: CaseContext, reader: dict[str, object]) -> tuple[Case, list]:
    """Read the case again with the other reader, in memory, up to and including the cross-document
    checks. Returns the shadow case and its cost entries."""
    from app.core.cache import ResponseCache
    from app.core.costs import BudgetLimits, CostMeter, DailySpend, PriceTable
    from app.core.db import Database
    from app.core.fingerprints import SqliteFingerprintStore
    from app.pipeline import _run_one_stage, resolve_applicant

    base = ctx.case
    shadow = base.model_copy(deep=True)
    shadow.documents, shadow.flags, shadow.findings, shadow.check_runs = [], [], [], []
    shadow.score = shadow.coverage = shadow.lane = shadow.top_finding = shadow.provenance = None
    shadow.stages, shadow.costs, shadow.partial_reasons, shadow.partial = [], [], [], False
    shadow.decision = None
    if shadow.applicant.source != "upload":
        from app.core.models import Applicant

        shadow.applicant = Applicant()
    settings = ctx.settings.model_copy(
        update={
            **reader,
            "semantic": "off",
            "verify_scan_flags": False,
            "max_usd_per_day": Decimal("100"),
        }
    )
    db = Database(":memory:", data_dir=ctx.settings.data_dir)
    db.init_schema()
    db.create_case(shadow)
    cache = ResponseCache(db, enabled=settings.cache_enabled)
    prices = PriceTable.load(settings.prices_path)
    sctx = replace(
        ctx,
        case=shadow,
        settings=settings,
        fingerprints=SqliteFingerprintStore(db),
        costs=CostMeter(
            BudgetLimits.from_settings(settings),
            prices,
            daily=DailySpend.from_settings(db, settings, prices),
        ),
        cache=cache,
        page_words={},
        overrides={},
        semantic_tally={},
        semantic_state={},
    )
    for stage in ("ingest", "classify", "extract", "rules", "crossdoc"):
        if stage == "rules":
            shadow.applicant = resolve_applicant(sctx)
        _run_one_stage(sctx, stage, db, None)
    return shadow, sctx.costs.costs()


def confirm_scan_flags(ctx: CaseContext) -> dict[str, object]:
    """Apply the second-reading rule to ``ctx.case``. Returns a small summary for the stage record."""
    if not ctx.settings.verify_scan_flags:
        return {}
    flagged = candidates(ctx)
    if not flagged:
        return {}
    reader = other_reader(ctx)
    if reader is None:
        return {
            "verification": "unavailable: no second reader",
            "warning": "verification_unavailable: no second reader",
        }
    try:
        shadow, costs = shadow_reading(ctx, reader)
    except Exception as exc:  # noqa: BLE001 - never lose the first reading's flags to a failed second one
        log.warning(
            "second reading failed for %s: %s", ctx.case.id, type(exc).__name__, exc_info=True
        )
        return {
            "verification": "unavailable",
            "warning": f"verification_unavailable: {type(exc).__name__}",
        }
    for c in costs:  # the second reading is paid for by this case
        with ctx.costs.scope(stage="crossdoc", document_id=c.document_id):
            ctx.costs.record(
                purpose=f"verify:{c.purpose}",
                model=c.model,
                prompt_tokens=c.prompt_tokens,
                completion_tokens=c.completion_tokens,
                seconds=c.seconds,
                cache_hit=c.cache_hit,
                usd=c.usd,
            )
    shadow_failed = any(r.status == "error" for r in shadow.check_runs) or not shadow.documents
    if shadow_failed:
        return {
            "verification": "unavailable",
            "warning": "verification_unavailable: second reading incomplete",
        }
    confirmed = {flag_key(shadow, f) for f in shadow.flags if f.stage in CHECK_STAGES}
    drop = {f.id for f in flagged if flag_key(ctx.case, f) not in confirmed}
    if drop:
        by_rule: dict[str, int] = {}
        for i, f in enumerate(ctx.case.flags):
            if f.id in drop:
                ctx.case.flags[i] = f.model_copy(
                    update={"severity": "info", "explanation": (NOTE + f.explanation)}
                )
                by_rule[f.rule] = by_rule.get(f.rule, 0) + 1
    return {
        "verification": "done",
        "verified_flags": len(flagged) - len(drop),
        "unconfirmed_flags": len(drop),
    }
