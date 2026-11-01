"""GET /monitoring and the AI incident log (docs/ai-incidents.md)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import Field

from app.api._common import ApiRoute, DbDep, SettingsDep, audit_for, get_clock, signed_in_reviewer
from app.audit import scrub
from app.core.models import Strict
from app.monitoring import INCIDENT_ACTION, incidents, snapshot
from app.review import clean_reviewer

router = APIRouter(tags=["monitoring"], route_class=ApiRoute)


class IncidentRequest(Strict):
    severity: Literal["low", "medium", "high"]
    summary: str = Field(min_length=10, max_length=500)
    case_id: str | None = Field(default=None, max_length=64)
    resolves: int | None = Field(default=None, ge=1)  # the id of the incident this report closes
    reviewer: str | None = None


@router.get("/monitoring")
def get_monitoring(request: Request, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    return snapshot(db, settings, now=get_clock(request)())


@router.get("/monitoring/incidents")
def list_incidents(db: DbDep) -> dict[str, Any]:
    return {"incidents": incidents(db)}


@router.post("/monitoring/incidents", status_code=201)
def report_incident(body: IncidentRequest, request: Request) -> dict[str, Any]:
    who = clean_reviewer(signed_in_reviewer(request) or body.reviewer)
    row_id = audit_for(request).write(
        actor=who,
        action=INCIDENT_ACTION,
        case_id=None,
        detail={
            "severity": body.severity,
            "summary": scrub(body.summary, max_len=500),
            "case_ref": scrub(body.case_id or "", max_len=64) or None,
            "status": "resolution" if body.resolves else "open",
            "resolves": body.resolves,
        },
    )
    return {"id": row_id, "reported_by": who, "severity": body.severity}
