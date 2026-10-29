"""Case endpoints (api.md sections 3, 5, 6, 10): create, view, delete, add documents, rerun,
versions, document words, retention purge.

``init`` wraps the job runner so that the end of every analysis job writes its audit row
(``case.analysis_finished`` / ``case.analysis_failed``) without editing ``pipeline.py``.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from app.api._common import (
    ANONYMOUS,
    MUTATION_LOCK,
    ApiRoute,
    DbDep,
    RunnerDep,
    SettingsDep,
    accepted,
    audit_for,
    get_clock,
    load_case_or_404,
    not_found,
    read_uploads,
    require_state,
)
from app.audit import AuditLog, scrub
from app.core.cases import add_file, new_case
from app.core.costs import summarise_costs
from app.core.db import CaseNotFound, Database
from app.core.errors import ApiError
from app.core.models import Strict, UseCase, utcnow
from app.pack import not_checked_documents
from app.pipeline import prepare_rerun
from app.retention import delete_case_with_audit, purge
from app.review import (
    allowed_verdicts,
    load_entries,
    materialise,
    pending_summary,
    version_history,
)

log = logging.getLogger("gldb.api.cases")

router = APIRouter(tags=["cases"], route_class=ApiRoute)


# --------------------------------------------------------------------------------------
# Startup hook: audit the end of every analysis job
# --------------------------------------------------------------------------------------


def _audit_job_outcome(app: Any, job_id: str, case_id: str) -> None:
    db: Database = app.state.db
    job = db.get_job(job_id)
    if job is None:
        return
    audit = AuditLog(
        db, app.state.settings, clock=lambda: (getattr(app.state, "clock", None) or utcnow)()
    )
    try:
        case = db.load_case(case_id)
    except CaseNotFound:
        return  # deleted while the job ran
    if job.state == "succeeded":
        prov = case.provenance
        audit.write(
            actor="system",
            action="case.analysis_finished",
            case_id=case_id,
            detail={
                "version": case.version,
                "score": case.score.value if case.score else None,
                "band": case.score.band if case.score else None,
                "partial": bool(case.partial or (case.score is not None and case.score.partial)),
                "coverage_ratio": case.coverage.ratio if case.coverage else None,
                "lane": case.lane,
                "ruleset_version": prov.ruleset_version if prov else None,
                "flags_digest": prov.flags_digest if prov else None,
            },
        )
    elif job.state == "failed":
        audit.write(
            actor="system",
            action="case.analysis_failed",
            case_id=case_id,
            detail={"version": case.version, "error": scrub(job.error or "", max_len=200)},
        )


def init(app: Any, settings: Any) -> None:
    runner = app.state.runner
    if getattr(runner, "_audit_wrapped", False):
        return
    original = runner._run

    def wrapped(job_id: str, case_id: str) -> None:
        original(job_id, case_id)
        try:
            with MUTATION_LOCK:  # waits for the request that queued the job to write its own row
                _audit_job_outcome(app, job_id, case_id)
        except Exception:  # noqa: BLE001 - auditing must not break the job runner
            log.exception("could not audit the end of job %s", job_id)

    runner._run = wrapped
    runner._audit_wrapped = True


# --------------------------------------------------------------------------------------
# POST /cases
# --------------------------------------------------------------------------------------


@router.post("/cases", status_code=202)
def create_case(
    request: Request,
    use_case: Annotated[UseCase, Form()],
    *,
    files: Annotated[list[UploadFile] | None, File()] = None,
    reference: Annotated[str | None, Form(max_length=80)] = None,
    applicant_name: Annotated[str | None, Form(max_length=120)] = None,
    applicant_uen: Annotated[str | None, Form(max_length=20)] = None,
    db: DbDep,
    settings: SettingsDep,
    runner: RunnerDep,
) -> JSONResponse:
    uploads = read_uploads(files or [], settings)
    case = new_case(
        settings,
        use_case,
        reference=reference,
        applicant_name=applicant_name,
        applicant_uen=applicant_uen,
    )
    for name, data in uploads:
        add_file(case, db.files, name=name, data=data)
    db.create_case(case)
    with MUTATION_LOCK:
        job = runner.submit(case.id, "analyse")
        audit_for(request).write(
            actor=ANONYMOUS,
            action="case.created",
            case_id=case.id,
            detail={
                "version": case.version,
                "use_case": case.use_case,
                "file_count": len(case.files),
                "file_sha256s": [f.sha256 for f in case.files],
                "job_id": job.id,
            },
        )
    return accepted(
        {
            "case_id": case.id,
            "job_id": job.id,
            "status_url": f"/jobs/{job.id}",
            "case_url": f"/cases/{case.id}",
        },
        job.id,
    )


# --------------------------------------------------------------------------------------
# GET /cases/{id}
# --------------------------------------------------------------------------------------


@router.get("/cases/{case_id}")
def get_case(
    case_id: str,
    *,
    version: Annotated[int | None, Query(ge=1)] = None,
    include: str | None = None,
    db: DbDep,
) -> dict[str, Any]:
    wanted = {i.strip() for i in (include or "").split(",") if i.strip()}
    unknown = wanted - {"words"}
    if unknown:
        raise ApiError(
            422,
            "validation_error",
            "Unknown include value(s): " + ", ".join(sorted(unknown)) + ".",
            {"errors": [{"field": "include", "message": "allowed: words", "type": "enum"}]},
        )
    live = load_case_or_404(db, case_id)
    case = (
        live
        if version is None or version == live.version
        else load_case_or_404(db, case_id, version)
    )
    entries = load_entries(db, case_id)
    view = materialise(case, entries)
    data = view.model_dump(mode="json")
    if "words" not in wanted:
        for doc in data["documents"]:
            doc.pop("words", None)
    data["pending_second_approvals"] = pending_summary(entries, case.version)
    data["allowed_verdicts"] = allowed_verdicts(case, current=case.version == live.version)
    data["not_checked_documents"] = not_checked_documents(case)
    data["cost_summary"] = summarise_costs(case)
    return data


@router.get("/cases/{case_id}/versions")
def list_versions(case_id: str, db: DbDep) -> dict[str, Any]:
    load_case_or_404(db, case_id)
    return {"versions": version_history(db, case_id)}


@router.get("/cases/{case_id}/documents/{doc_id}/words")
def document_words(case_id: str, doc_id: str, db: DbDep) -> dict[str, Any]:
    case = load_case_or_404(db, case_id)
    doc = next((d for d in case.documents if d.id == doc_id), None)
    if doc is None:
        raise not_found("document", id=doc_id)
    return {
        "document_id": doc.id,
        "words": [w.model_dump(mode="json") for w in doc.words],
    }


# --------------------------------------------------------------------------------------
# Add documents / rerun (new version, review entries kept)
# --------------------------------------------------------------------------------------


@router.post("/cases/{case_id}/files", status_code=202)
def add_files(
    case_id: str,
    request: Request,
    *,
    files: Annotated[list[UploadFile] | None, File()] = None,
    db: DbDep,
    settings: SettingsDep,
    runner: RunnerDep,
) -> JSONResponse:
    with MUTATION_LOCK:
        current = load_case_or_404(db, case_id)
        require_state(current, ("awaiting_documents", "open"))
        uploads = read_uploads(files or [], settings)
        before = current.version
        case = prepare_rerun(db, case_id)  # snapshot, version + 1, outputs cleared, processing
        for name, data in uploads:
            add_file(case, db.files, name=name, data=data)
        db.save_case(case)
        job = runner.submit(case_id, "rerun")
        audit_for(request).write(
            actor=ANONYMOUS,
            action="case.documents_added",
            case_id=case_id,
            detail={
                "version_from": before,
                "version_to": case.version,
                "file_sha256s": [
                    f.sha256 for f in case.files if f.added_in_version == case.version
                ],
                "job_id": job.id,
            },
        )
    return accepted(
        {
            "case_id": case_id,
            "job_id": job.id,
            "status_url": f"/jobs/{job.id}",
            "version": case.version,
        },
        job.id,
    )


@router.post("/cases/{case_id}/rerun", status_code=202)
def rerun_case(
    case_id: str,
    request: Request,
    db: DbDep,
    runner: RunnerDep,
) -> JSONResponse:
    with MUTATION_LOCK:
        current = load_case_or_404(db, case_id)
        require_state(current, ("open", "awaiting_documents", "error"))
        before = current.version
        case = prepare_rerun(db, case_id)
        job = runner.submit(case_id, "rerun")
        audit_for(request).write(
            actor=ANONYMOUS,
            action="case.rerun",
            case_id=case_id,
            detail={"version_from": before, "version_to": case.version, "job_id": job.id},
        )
    return accepted(
        {
            "case_id": case_id,
            "job_id": job.id,
            "status_url": f"/jobs/{job.id}",
            "version": case.version,
        },
        job.id,
    )


# --------------------------------------------------------------------------------------
# Delete and retention
# --------------------------------------------------------------------------------------


@router.delete("/cases/{case_id}", status_code=204)
def delete_case(
    case_id: str,
    request: Request,
    *,
    reviewer: str | None = None,
    reason: str | None = None,
    db: DbDep,
) -> Response:
    with MUTATION_LOCK:
        delete_case_with_audit(
            db, audit_for(request), case_id, reviewer=reviewer or "", reason=reason or ""
        )
    return Response(status_code=204)


class PurgeRequest(Strict):
    older_than_days: int | None = Field(default=None, ge=0, le=36500)
    dry_run: bool = True


@router.post("/admin/retention/purge")
def retention_purge(
    request: Request,
    *,
    body: PurgeRequest | None = None,
    db: DbDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    body = body or PurgeRequest()
    days = settings.retention_days if body.older_than_days is None else body.older_than_days
    with MUTATION_LOCK:
        return purge(
            db,
            audit_for(request),
            older_than_days=days,
            dry_run=body.dry_run,
            now=get_clock(request)(),
        )
