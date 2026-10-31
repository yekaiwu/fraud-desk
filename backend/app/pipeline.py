"""Stage runner and job runner (docs/architecture.md section 3, cost-efficiency.md 3.1).

``run_pipeline`` runs ingest, classify, extract, forensics, rules, crossdoc and score in order.
Each stage gets a ``StageRecord`` (status, seconds, cost, warnings, detail); the case is saved
after every stage. A stage that raises is recorded as failed and the later stages still run, so a
partial score is always produced and never silent (plan principle 3).
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Literal

from app.checks.registry import run_stage_checks
from app.core.cache import ResponseCache
from app.core.config import Settings
from app.core.context import CaseContext
from app.core.costs import BudgetLimits, CostMeter, DailySpend, LLMBudgetExceeded, PriceTable
from app.core.db import Database, JobRecord
from app.core.fingerprints import (
    SqliteFingerprintStore,
    invoice_fingerprint,
    invoice_key_fingerprint,
)
from app.core.llm import LLMClient, get_llm_client
from app.core.models import (
    STAGE_ORDER,
    Applicant,
    Case,
    Document,
    StageName,
    StageOutcome,
    StageRecord,
    utcnow,
)
from app.core.provenance import build_provenance
from app.core.registry import MockRegistry, load_registry
from app.core.textnorm import company_key, normalise_uen

log = logging.getLogger("gldb.pipeline")

STAGES: tuple[str, ...] = STAGE_ORDER
REGISTRY_STAGES = ("forensics", "rules", "crossdoc")
STAGE_MODULES = {
    "ingest": "app.ingest.stage",
    "classify": "app.classify.stage",
    "extract": "app.extract.stage",
    "score": "app.score",
}
# Tests (or integration code) may put a callable here to override a stage runner.
STAGE_RUNNERS: dict[str, Callable[[CaseContext], StageOutcome]] = {}


def runner_for(stage: str) -> Callable[[CaseContext], StageOutcome]:
    if stage in STAGE_RUNNERS:
        return STAGE_RUNNERS[stage]
    if stage in REGISTRY_STAGES:
        return lambda ctx: _run_registry_stage(ctx, stage)  # type: ignore[arg-type]
    return importlib.import_module(STAGE_MODULES[stage]).run_stage


def _run_registry_stage(
    ctx: CaseContext, stage: Literal["forensics", "rules", "crossdoc"]
) -> StageOutcome:
    runs = run_stage_checks(ctx, stage)
    errors = [r for r in runs if r.status == "error"]
    detail: dict[str, Any] = {
        "checks": len(runs),
        "ran": sum(1 for r in runs if r.status == "ran"),
        "skipped": sum(1 for r in runs if r.status == "skipped"),
        "errors": len(errors),
        "flags": sum(r.flags for r in runs),
    }
    warnings = [f"check_error:{r.rule}: {r.reason}" for r in errors]
    if stage == "crossdoc":
        from app.verify_reading import confirm_scan_flags

        summary = confirm_scan_flags(ctx)
        note = summary.pop("warning", None)
        if note:
            warnings.append(str(note))
        detail.update(summary)
    return StageOutcome(detail=detail, warnings=warnings)


# --------------------------------------------------------------------------------------
# Applicant resolution (data-model.md 4.1)
# --------------------------------------------------------------------------------------


def _cell_text(doc: Document, name: str) -> str:
    cell = doc.fields.get(name)
    return (cell.value or "").strip() if cell is not None else ""


def resolve_applicant(ctx: CaseContext) -> Applicant:
    """Upload values are authoritative. Otherwise the most frequent normalised ``(company_name,
    uen)`` among company_profile, certificate_of_incorporation and financial_statement documents;
    else the earliest bank statement's ``account_holder``; else unchanged (checks then skip)."""
    current = ctx.case.applicant
    pairs: Counter[tuple[str, str]] = Counter()
    first_seen: dict[tuple[str, str], tuple[str, str | None, int]] = {}
    order = 0
    for doc in ctx.docs("company_profile", "certificate_of_incorporation", "financial_statement"):
        name, uen = _cell_text(doc, "company_name"), normalise_uen(_cell_text(doc, "uen"))
        key = (company_key(name), uen)
        if not key[0] and not key[1]:
            continue
        pairs[key] += 1
        first_seen.setdefault(key, (name, uen or None, order))
        order += 1
    candidate: tuple[str, str | None] | None = None
    if pairs:
        best = max(pairs, key=lambda k: (pairs[k], -first_seen[k][2]))
        candidate = (first_seen[best][0], first_seen[best][1])
    else:
        for doc in ctx.docs("bank_statement"):
            holder = _cell_text(doc, "account_holder")
            if holder:
                candidate = (holder, normalise_uen(_cell_text(doc, "uen")) or None)
                break
    if candidate is None:
        return current
    name, uen = candidate
    if current.source == "upload":
        return Applicant(name=current.name or name or None, uen=current.uen or uen, source="upload")
    return Applicant(name=name or None, uen=uen, source="derived")


# --------------------------------------------------------------------------------------
# Fingerprints (recorded after score; checks only look them up)
# --------------------------------------------------------------------------------------


def record_fingerprints(ctx: CaseContext) -> int:
    """Record ``file_sha256`` per file and ``invoice`` full + key per invoice. Replaces what this
    case recorded before (a rerun re-records). Returns the number of fingerprints written."""
    case = ctx.case
    store = ctx.fingerprints
    forget = getattr(store, "forget_case", None)
    if callable(forget):
        forget(case.id)
    uen = case.applicant.uen
    count = 0
    for f in case.files:
        first = next((d for d in ctx.docs() if d.file_id == f.id), None)
        store.record(
            "file_sha256",
            f.sha256,
            case_id=case.id,
            document_id=first.id if first else None,
            meta={"applicant_uen": uen, "doc_type": first.doc_type if first else None},
        )
        count += 1
    for doc in ctx.docs("invoice"):
        supplier = _cell_text(doc, "supplier_uen")
        number = _cell_text(doc, "invoice_number")
        date_ = _cell_text(doc, "invoice_date")
        total = _cell_text(doc, "total")
        meta = {"applicant_uen": uen, "doc_type": "invoice"}
        if supplier and number and date_ and total:
            store.record(
                "invoice",
                invoice_fingerprint(supplier, number, date_, total),
                case_id=case.id,
                document_id=doc.id,
                meta={**meta, "role": "full"},
            )
            count += 1
        if supplier and number:
            store.record(
                "invoice",
                invoice_key_fingerprint(supplier, number),
                case_id=case.id,
                document_id=doc.id,
                meta={**meta, "role": "key"},
            )
            count += 1
    count += _record_crop_fingerprints(ctx)
    return count


def _record_crop_fingerprints(ctx: CaseContext) -> int:
    """Signature/stamp crop hashes (CR-27). Guarded import: the forensics module is optional, and a
    failure to hash crops never loses the file and invoice fingerprints recorded before it."""
    try:
        from app.checks.forensics_image import crop_fingerprints
    except ImportError:  # pragma: no cover - module absent in a stripped build
        return 0
    try:
        crops = crop_fingerprints(ctx)
    except Exception:  # noqa: BLE001
        log.exception("crop fingerprints failed for %s", ctx.case.id)
        return 0
    uen = ctx.case.applicant.uen
    seen: set[tuple[str, str]] = set()
    count = 0
    for crop in crops:
        key = (crop.phash, crop.document_id)
        if key in seen:
            continue
        seen.add(key)
        doc = next((d for d in ctx.docs() if d.id == crop.document_id), None)
        ctx.fingerprints.record(
            "crop_phash",
            crop.phash,
            case_id=ctx.case.id,
            document_id=crop.document_id,
            meta={
                "applicant_uen": uen,
                "doc_type": doc.doc_type if doc else None,
                "page": crop.page,
                "exact": crop.exact,
            },
        )
        count += 1
    return count


# --------------------------------------------------------------------------------------
# Rerun preparation
# --------------------------------------------------------------------------------------


def looks_analysed(case: Case) -> bool:
    return bool(case.stages) or case.provenance is not None or case.status not in ("processing",)


def prepare_rerun(db: Database, case_id: str) -> Case:
    """Snapshot the current version, bump ``version``, clear analysis output and set
    ``status="processing"``. Review entries stay (they carry their ``case_version``). Idempotent
    with ``run_pipeline``: a case already prepared is not bumped again."""
    case = db.load_case(case_id)
    db.save_case(case, snapshot_version=True, touch=False)
    case.version += 1
    case.status = "processing"
    case.documents = []
    case.flags = []
    case.findings = []
    case.score = None
    case.coverage = None
    case.check_runs = []
    case.lane = None
    case.top_finding = None
    case.partial = False
    case.partial_reasons = []
    case.stages = []
    case.provenance = None
    case.costs = []
    case.decision = None
    if case.applicant.source != "upload":
        case.applicant = Applicant()
    db.save_case(case)
    return db.load_case(case_id)


# --------------------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------------------


def _short_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())[:300]
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _add_reason(case: Case, reason: str) -> None:
    case.partial = True
    if reason not in case.partial_reasons:
        case.partial_reasons.append(reason)


def _upsert_record(case: Case, record: StageRecord) -> None:
    for i, existing in enumerate(case.stages):
        if existing.stage == record.stage:
            case.stages[i] = record
            return
    case.stages.append(record)


def run_pipeline(
    case_id: str,
    *,
    db: Database,
    settings: Settings,
    llm: LLMClient | None = None,
    registry: MockRegistry | None = None,
    on_stage: Callable[[StageRecord], None] | None = None,
) -> Case:
    case = db.load_case(case_id)
    if looks_analysed(case):
        case = prepare_rerun(db, case_id)  # version + 1, old version snapshotted

    cache = ResponseCache(db, enabled=settings.cache_enabled)
    if registry is None:
        try:
            registry = load_registry(settings.registry_path)
        except ValueError as exc:  # malformed registry: registry checks skip, and we say so
            log.warning("registry unusable: %s", exc)
            _add_reason(case, "registry_unreadable: registry checks did not run")
    prices = PriceTable.load(settings.prices_path)
    ctx = CaseContext(
        case=case,
        today=case.today,
        settings=settings,
        llm=llm if llm is not None else get_llm_client(settings, cache=cache),
        registry=registry,
        fingerprints=SqliteFingerprintStore(db),
        costs=CostMeter(
            BudgetLimits.from_settings(settings),
            prices,
            daily=DailySpend.from_settings(db, settings, prices),
        ),
        cache=cache,
        page_words={},
        overrides=db.rule_overrides(),  # check catalogue: read once, applies to this run only
    )
    case.status = "processing"
    db.save_case(case)

    try:
        for stage in STAGES:
            if stage == "forensics":
                case.applicant = resolve_applicant(ctx)
            _run_one_stage(ctx, stage, db, on_stage)
        case.provenance = build_provenance(ctx)
        try:
            record_fingerprints(ctx)
        except Exception:
            log.exception("fingerprint recording failed for %s", case.id)
        case.status = "open"
        db.save_case(case, snapshot_version=True)
    except BaseException:
        case.status = "error"
        try:
            db.save_case(case)
        except Exception:  # noqa: BLE001
            log.exception("could not persist error status for %s", case.id)
        raise
    return case


def _run_one_stage(
    ctx: CaseContext,
    stage: str,
    db: Database,
    on_stage: Callable[[StageRecord], None] | None,
) -> None:
    case = ctx.case
    name: StageName = stage  # type: ignore[assignment]
    record = StageRecord(stage=name, status="running", started_at=utcnow())
    _upsert_record(case, record)
    if on_stage:
        on_stage(record.model_copy())
    db.save_case(case)

    started = time.perf_counter()
    outcome = StageOutcome()
    status: Literal["ok", "failed"] = "ok"
    error: str | None = None
    try:
        with ctx.costs.scope(stage=name):
            outcome = runner_for(stage)(ctx)
    except LLMBudgetExceeded as exc:
        outcome = StageOutcome(partial=True, partial_reason=str(exc))
    except Exception as exc:  # noqa: BLE001 - a stage failure must not stop the pipeline
        log.exception("stage %s failed for %s", stage, case.id)
        status, error = "failed", _short_error(exc)
        _add_reason(case, f"stage_failed:{stage}")
    seconds = time.perf_counter() - started

    if outcome.partial:
        _add_reason(case, outcome.partial_reason or f"partial: {stage}")
    breach = ctx.costs.breach()
    if breach is not None and not any(
        r.startswith("budget_exceeded:") for r in case.partial_reasons
    ):
        _add_reason(case, str(breach))
    if stage == "extract" and not case.documents:
        _add_reason(case, "no_documents: nothing was classified or extracted")

    warnings = [*outcome.warnings, *ctx.costs.drain_warnings()]
    final = StageRecord(
        stage=name,
        status=status,
        started_at=record.started_at,
        seconds=round(seconds, 6),
        cost_usd=ctx.costs.total(name),
        error=error,
        warnings=warnings,
        detail=dict(outcome.detail),
    )
    _upsert_record(case, final)
    case.costs = ctx.costs.costs()
    db.save_case(case)
    if on_stage:
        on_stage(final.model_copy())


# --------------------------------------------------------------------------------------
# Bounded job runner (D4)
# --------------------------------------------------------------------------------------


class JobRunner:
    """Bounded ``ThreadPoolExecutor`` inside the API process; job state lives in SQLite."""

    def __init__(
        self,
        db: Database,
        settings: Settings,
        *,
        llm: LLMClient | None = None,
        registry: MockRegistry | None = None,
    ):
        self.db = db
        self.settings = settings
        self.llm = llm
        self.registry = registry
        self._pool = ThreadPoolExecutor(
            max_workers=max(1, settings.job_workers), thread_name_prefix="gldb-job"
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, case_id: str, kind: Literal["analyse", "rerun"] = "analyse") -> JobRecord:
        job = self.db.create_job(case_id, kind)
        future = self._pool.submit(self._run, job.id, case_id)
        with self._lock:
            self._futures[job.id] = future
        return job

    def wait(self, job_id: str, timeout: float | None = None) -> JobRecord:
        """Block until the job finished (for tests and scripts)."""
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        job = self.db.get_job(job_id)
        assert job is not None
        return job

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)

    def _run(self, job_id: str, case_id: str) -> None:
        stages: list[StageRecord] = []

        def on_stage(rec: StageRecord) -> None:
            for i, existing in enumerate(stages):
                if existing.stage == rec.stage:
                    stages[i] = rec
                    break
            else:
                stages.append(rec)
            self.db.update_job(job_id, stages=stages)

        self.db.update_job(job_id, state="running", started_at=utcnow())
        try:
            run_pipeline(
                case_id,
                db=self.db,
                settings=self.settings,
                llm=self.llm,
                registry=self.registry,
                on_stage=on_stage,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", job_id)
            self.db.update_job(
                job_id, state="failed", finished_at=utcnow(), error=_short_error(exc)
            )
            return
        self.db.update_job(job_id, state="succeeded", finished_at=utcnow(), stages=stages)
