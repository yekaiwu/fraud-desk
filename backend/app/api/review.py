"""Review log, review entries and the audit trail (api.md section 6, review-workflow-and-audit.md).

``init(app, settings)`` is called by ``main.py`` at startup and installs the application log
filter (pseudonymised logs) without editing ``main.py``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.api._common import (
    MUTATION_LOCK,
    ApiRoute,
    DbDep,
    audit_for,
    load_case_or_404,
    review_service,
)
from app.audit import install_log_filter
from app.review import ReviewRequest

router = APIRouter(tags=["review"], route_class=ApiRoute)


def init(app: Any, settings: Any) -> None:
    install_log_filter(settings)


@router.post("/cases/{case_id}/review", status_code=201)
def post_review(case_id: str, body: ReviewRequest, request: Request) -> JSONResponse:
    with MUTATION_LOCK:  # a review never interleaves with a rerun or add-documents in this process
        result = review_service(request).submit(case_id, body)
    return JSONResponse(result.model_dump(mode="json"), status_code=201)


@router.get("/cases/{case_id}/review")
def get_review(
    case_id: str,
    request: Request,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    entries = review_service(request).entries(case_id, version=version)
    return {"entries": [e.model_dump(mode="json") for e in entries]}


@router.get("/cases/{case_id}/audit")
def get_audit(case_id: str, request: Request, db: DbDep) -> dict[str, Any]:
    load_case_or_404(db, case_id)
    return {"entries": audit_for(request).for_case(case_id)}
