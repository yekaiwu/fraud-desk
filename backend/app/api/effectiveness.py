"""Rule effectiveness (api.md section 9)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter

from app.api._common import DbDep
from app.effectiveness import compute_effectiveness

router = APIRouter(tags=["rules"])


@router.get("/rules/effectiveness")
def rule_effectiveness(
    *,
    use_case: Literal["kyc", "aml", "loan"] | None = None,
    since: date | None = None,
    db: DbDep,
) -> dict[str, Any]:
    return compute_effectiveness(db, use_case=use_case, since=since)
