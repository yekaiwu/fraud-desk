"""GET /monitoring and the AI incident log (docs/ai-incidents.md)."""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Path, Request
from pydantic import Field

from app.alerts import (
    AlertService,
    get_json_meta,
    list_alert_state,
    recent_deliveries,
    scheduler_status,
)
from app.api._common import ApiRoute, DbDep, SettingsDep, audit_for, get_clock, signed_in_reviewer
from app.audit import scrub
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.models import Strict
from app.core.privacy import describe as describe_privacy
from app.monitoring import (
    INCIDENT_ACK_ACTION,
    INCIDENT_ACTION,
    incidents,
    open_incident,
    snapshot,
)
from app.readiness import approve_thresholds, readiness, readiness_summary
from app.review import clean_reason, clean_reviewer

router = APIRouter(tags=["monitoring"], route_class=ApiRoute)


class IncidentRequest(Strict):
    severity: Literal["low", "medium", "high"]
    summary: str = Field(min_length=10, max_length=500)
    case_id: str | None = Field(default=None, max_length=64)
    resolves: int | None = Field(default=None, ge=1)  # the id of the incident this report closes
    reviewer: str | None = None


AlertKey = Annotated[str, Path(pattern=r"^[a-z0-9_:.-]{1,64}$")]


class AcknowledgeRequest(Strict):
    reason: str | None = Field(default=None, max_length=4000)
    reviewer: str | None = None


class ApproveThresholdsRequest(Strict):
    approver: str = Field(min_length=2, max_length=80)
    role: str = Field(min_length=2, max_length=80)
    note: str = Field(min_length=5, max_length=500)


class IncidentAckRequest(Strict):
    note: str = Field(min_length=5, max_length=500)
    assigned_to: str | None = Field(default=None, min_length=2, max_length=80)
    reviewer: str | None = None


def alert_service(request: Request) -> AlertService:
    """The service for this request. A test may install ``app.state.alert_sinks`` and ``alert_sleep``."""
    state = request.app.state
    return AlertService(
        state.db,
        state.settings,
        clock=get_clock(request),
        sinks=getattr(state, "alert_sinks", None),
        sleep=getattr(state, "alert_sleep", time.sleep),
    )


def acting_account(request: Request, settings: Settings, typed: str | None) -> str:
    """Who is doing it. The signed-in account wins over any typed name; with personal accounts configured
    (GLDB_PERSONAL_ACCOUNTS) a request without one is refused rather than recorded under a typed name."""
    who = signed_in_reviewer(request)
    if who:
        return who
    if settings.personal_accounts:
        raise ApiError(
            403,
            "account_required",
            "Sign in with your own account to do this; a typed name is not accepted.",
        )
    return clean_reviewer(typed)


@router.get("/monitoring")
def get_monitoring(request: Request, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    now = get_clock(request)()
    service = alert_service(request)
    snap = snapshot(db, settings, now=now)
    sched = scheduler_status(db, settings, now=now)
    full = readiness(db, settings, service.sinks, now=now)
    open_alerts = [a["key"] for a in list_alert_state(db) if a["status"] == "open"]
    return {
        **snap,
        **sched,
        "alert_sinks": [s.name for s in service.sinks],
        "open_alerts": open_alerts,
        "readiness": readiness_summary(full),
        **describe_privacy(settings),
    }


@router.get("/monitoring/readiness")
def get_readiness(request: Request, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    return readiness(db, settings, alert_service(request).sinks, now=get_clock(request)())


@router.get("/monitoring/alerts")
def get_alerts(request: Request, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    service = alert_service(request)
    test = get_json_meta(db, "last_test")
    return {
        "alerts": list_alert_state(db),
        "deliveries": recent_deliveries(db),
        "sinks": [s.name for s in service.sinks],
        "last_test": None
        if test is None
        else {k: test[k] for k in ("at", "by", "results", "delivered_to") if k in test},
        **scheduler_status(db, settings, now=get_clock(request)()),
    }


@router.post("/monitoring/alerts/test")
def post_test_alert(request: Request, settings: SettingsDep) -> dict[str, Any]:
    who = signed_in_reviewer(request) or "operator"
    return alert_service(request).send_test(who=who)


@router.post("/monitoring/alerts/{key}/acknowledge")
def acknowledge_alert(
    key: AlertKey, body: AcknowledgeRequest, request: Request, settings: SettingsDep
) -> dict[str, Any]:
    who = acting_account(request, settings, body.reviewer)
    reason = clean_reason(body.reason)
    return {"alert": alert_service(request).acknowledge(key, who=who, reason=reason)}


@router.post("/monitoring/thresholds/approve", status_code=201)
def approve(
    body: ApproveThresholdsRequest, request: Request, db: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    who = acting_account(request, settings, body.approver)
    clean_reason(body.note)
    return approve_thresholds(
        db,
        audit_for(request),
        settings,
        actor=who,
        approver=body.approver,
        role=body.role,
        note=body.note,
    )


@router.get("/monitoring/incidents")
def list_incidents(db: DbDep) -> dict[str, Any]:
    return {"incidents": incidents(db)}


@router.post("/monitoring/incidents", status_code=201)
def report_incident(
    body: IncidentRequest, request: Request, background: BackgroundTasks
) -> dict[str, Any]:
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
    if body.severity in ("medium", "high") and not body.resolves:
        service = alert_service(request)
        background.add_task(
            service.notify_incident, incident_id=row_id, severity=body.severity, reported_by=who
        )
    return {"id": row_id, "reported_by": who, "severity": body.severity}


@router.post("/monitoring/incidents/{incident_id}/acknowledge", status_code=201)
def acknowledge_incident(
    incident_id: Annotated[int, Path(ge=1)], body: IncidentAckRequest, request: Request
) -> dict[str, Any]:
    """Say that someone has seen the incident, and optionally who it is assigned to. Open or resolved is
    unchanged: only a report that names the incident in ``resolves`` closes it."""
    who = acting_account(request, request.app.state.settings, body.reviewer)
    db = request.app.state.db
    if open_incident(db, incident_id) is None:
        raise ApiError(
            404,
            "not_found",
            "No open incident with this id.",
            {"kind": "incident", "id": incident_id},
        )
    row_id = audit_for(request).write(
        actor=who,
        action=INCIDENT_ACK_ACTION,
        case_id=None,
        detail={
            "incident": incident_id,
            "note": scrub(body.note, max_len=300),
            "assigned_to": scrub(body.assigned_to or "", max_len=80) or None,
        },
    )
    return {"id": row_id, "incident": incident_id, "acknowledged_by": who}
