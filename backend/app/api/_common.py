"""Shared helpers for the routers (underscore-prefixed, so ``main.py`` does not treat it as a
router module). Routers stay thin: parse, call the domain module, map errors to the one body."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.audit import AuditLog
from app.core.cases import sanitise_filename, sniff_mime
from app.core.config import Settings
from app.core.db import CaseNotFound, Database
from app.core.deps import get_app_settings, get_db, get_runner
from app.core.errors import ApiError
from app.core.models import Case, utcnow
from app.review import ReviewService

DbDep = Annotated[Database, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RunnerDep = Annotated[Any, Depends(get_runner)]

# Serialises "mutate a case and queue its job" (rerun, add files, create) with the audit row that
# records it, so `case.created` always precedes `case.analysis_finished` in the log.
MUTATION_LOCK = threading.RLock()

ANONYMOUS = "anonymous"


class ApiRoute(APIRoute):
    """A body that is not parseable JSON is ``400 bad_request`` (api.md section 1), not a 422;
    schema and enum violations of well-formed JSON stay ``422 validation_error``."""

    def get_route_handler(self) -> Callable[[Request], Any]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                if any(e.get("type") == "json_invalid" for e in exc.errors()):
                    raise ApiError(
                        400, "bad_request", "The request body is not valid JSON."
                    ) from exc
                raise

        return handler


def get_clock(request: Request) -> Callable[[], datetime]:
    """The application clock (``app.state.clock`` when a test or deployment installed one)."""

    def clock() -> datetime:
        return getattr(request.app.state, "clock", utcnow)()

    return clock


def audit_for(request: Request) -> AuditLog:
    return AuditLog(request.app.state.db, request.app.state.settings, clock=get_clock(request))


def review_service(request: Request) -> ReviewService:
    return ReviewService(
        request.app.state.db,
        audit_for(request),
        request.app.state.settings,
        clock=get_clock(request),
    )


def not_found(kind: str, **details: Any) -> ApiError:
    return ApiError(404, "not_found", f"The {kind} was not found.", {"kind": kind, **details})


def load_case_or_404(db: Database, case_id: str, version: int | None = None) -> Case:
    try:
        return db.load_case(case_id, version)
    except CaseNotFound as exc:
        if version is not None and db.case_exists(case_id):
            raise not_found("version", id=case_id, version=version) from exc
        raise not_found("case", id=case_id) from exc


def require_state(case: Case, allowed: Sequence[str]) -> None:
    """409 ``case_processing`` / ``case_not_open`` unless the case status is in ``allowed``."""
    if case.status in allowed:
        return
    if case.status == "processing":
        raise ApiError(
            409, "case_processing", "The case is still being analysed.", {"status": case.status}
        )
    raise ApiError(
        409,
        "case_not_open",
        f"This action needs a case with status {' or '.join(allowed)}.",
        {"status": case.status},
    )


def parse_csv(value: str | None, allowed: Sequence[str], field: str) -> list[str] | None:
    """Comma-separated enum list (``None`` when absent). Unknown values are a 422."""
    if value is None or not value.strip():
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in allowed]
    if bad:
        raise ApiError(
            422,
            "validation_error",
            f"Unknown value(s) for {field}: {', '.join(bad)}.",
            {
                "errors": [
                    {"field": field, "message": f"allowed: {', '.join(allowed)}", "type": "enum"}
                ]
            },
        )
    return items


Upload = tuple[str, bytes]


def read_uploads(files: Sequence[UploadFile], settings: Settings) -> list[Upload]:
    """Validate every uploaded file before anything is stored (all-or-nothing, api.md section 3):
    422 ``no_files`` / ``too_many_files``, 413 ``file_too_large``, 415 ``unsupported_media_type``
    (magic bytes, not the declared content type)."""
    if not files:
        raise ApiError(422, "no_files", "Upload at least one file.", {"field": "files"})
    if len(files) > settings.max_files_per_case:
        raise ApiError(
            422,
            "too_many_files",
            f"At most {settings.max_files_per_case} files per upload.",
            {"max": settings.max_files_per_case, "received": len(files)},
        )
    limit = settings.max_upload_mb * 1024 * 1024
    out: list[Upload] = []
    for f in files:
        name = sanitise_filename(f.filename)
        data = f.file.read(limit + 1)
        if len(data) > limit:
            raise ApiError(
                413,
                "file_too_large",
                f"A file exceeds the {settings.max_upload_mb} MB limit.",
                {"max_mb": settings.max_upload_mb, "filename": name},
            )
        if sniff_mime(data) is None:
            raise ApiError(
                415,
                "unsupported_media_type",
                "Only PDF, JPEG and PNG files are accepted (checked by content).",
                {"filename": name},
            )
        out.append((name, data))
    return out


def accepted(body: dict[str, Any], job_id: str) -> JSONResponse:
    return JSONResponse(body, status_code=202, headers={"Location": f"/jobs/{job_id}"})
