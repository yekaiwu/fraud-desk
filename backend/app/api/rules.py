"""Check catalogue as JSON and its change control (api.md sections 9 and 9a).

``GET /rules`` lists every registered check, unfiltered, with the code defaults and the effective
settings after the officers' overrides. ``PUT``/``DELETE /rules/{rule}/override`` edit or reset an
override (reviewer and reason required) and ``GET /rules/changes`` is the permanent change log.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Request

from app.api._common import ApiRoute, DbDep, get_clock
from app.catalogue import (
    CHANGES_DEFAULT_LIMIT,
    CHANGES_MAX_LIMIT,
    OverrideRequest,
    ResetRequest,
    put_override,
    reset_override,
    rule_item,
)
from app.checks.registry import all_checks

router = APIRouter(tags=["rules"], route_class=ApiRoute)


@router.get("/rules")
def list_rules(db: DbDep) -> dict[str, Any]:
    overrides = db.rule_overrides()
    return {"rules": [rule_item(m, overrides.get(m.rule)) for m in all_checks()]}


@router.get("/rules/changes")
def list_rule_changes(
    db: DbDep,
    rule: str | None = None,
    limit: Annotated[int, Query(ge=1, le=CHANGES_MAX_LIMIT)] = CHANGES_DEFAULT_LIMIT,
) -> dict[str, Any]:
    return {"changes": db.rule_changes(rule, limit)}


@router.put("/rules/{rule}/override")
def put_rule_override(
    rule: str, body: OverrideRequest, request: Request, db: DbDep
) -> dict[str, Any]:
    return put_override(db, rule, body, now=get_clock(request)())


@router.delete("/rules/{rule}/override")
def delete_rule_override(
    rule: str,
    request: Request,
    db: DbDep,
    body: Annotated[ResetRequest | None, Body()] = None,
    reviewer: Annotated[str | None, Query()] = None,
    reason: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Body values win; the query parameters are for clients that cannot send a DELETE body."""
    given = body or ResetRequest()
    merged = ResetRequest(
        reviewer=given.reviewer if given.reviewer is not None else reviewer,
        reason=given.reason if given.reason is not None else reason,
    )
    return reset_override(db, rule, merged, now=get_clock(request)())
