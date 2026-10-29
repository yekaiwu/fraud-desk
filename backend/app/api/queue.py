"""Ranked queue (api.md section 4, scoring-and-queue.md 4.9).

``GET /cases`` filters and sorts in SQL through ``Database.list_cases`` (the frozen ``priority``
order is never re-sorted in Python). ``build_queue_item`` assembles one ``QueueItem`` from a
``cases`` row; ``now`` is passed in, so this is the only place where the server clock matters.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request

from app.api._common import DbDep, get_clock, parse_csv
from app.core.db import SORTS, CaseFilters
from app.core.models import Coverage, QueueItem
from app.review import pending_counts

router = APIRouter(tags=["queue"])

USE_CASES = ("kyc", "aml", "loan")
STATUSES = ("processing", "open", "awaiting_documents", "closed", "error")
LANES = ("edd", "aml", "loan", "fast")
BANDS = ("low", "medium", "high")
DEFAULT_STATUSES = ["processing", "open", "awaiting_documents"]
MAX_PAGE_SIZE = 100


def build_queue_item(
    row: sqlite3.Row, *, now: datetime, pending_second_approvals: int = 0
) -> QueueItem:
    """One queue row. Scalars come from the denormalised columns; coverage and the file count come
    from the stored body. A case still ``processing`` has no score, lane, coverage or top line."""
    body = json.loads(row["body"])
    processing = row["status"] == "processing"
    cov = body.get("coverage")
    coverage = None
    if cov is not None and not processing:
        coverage = Coverage.model_validate({**cov, "skipped": [], "unchecked_pages": []})
    created = datetime.fromisoformat(row["created_at"])
    return QueueItem(
        case_id=row["id"],
        reference=row["reference"],
        use_case=row["use_case"],
        status=row["status"],
        applicant_name=row["applicant_name"],
        version=row["version"],
        created_at=created,
        updated_at=datetime.fromisoformat(row["updated_at"]),
        age_seconds=max(0, int((now - created).total_seconds())),
        score=None if processing else row["score"],
        band=None if processing else row["band"],
        coverage=coverage,
        lane=None if processing else row["lane"],
        top_finding=None if processing else row["top_finding"],
        findings_count=row["findings_count"],
        high_count=row["high_count"],
        partial=bool(row["partial"]),
        decision=row["decision"],
        pending_second_approvals=pending_second_approvals,
        file_count=len(body.get("files", [])),
    )


@router.get("/cases")
def list_cases(
    request: Request,
    *,
    use_case: str | None = None,
    status: str | None = None,
    lane: str | None = None,
    band: str | None = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    partial: bool | None = None,
    sort: Literal["priority", "score", "age", "coverage", "created"] = "priority",
    order: Literal["asc", "desc"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 25,
    db: DbDep,
) -> dict[str, Any]:
    assert sort in SORTS
    filters = CaseFilters(
        use_case=parse_csv(use_case, USE_CASES, "use_case"),
        status=parse_csv(status, STATUSES, "status") or DEFAULT_STATUSES,
        lane=parse_csv(lane, LANES, "lane"),
        band=parse_csv(band, BANDS, "band"),
        min_score=min_score,
        q=(q or "").strip() or None,
        partial=partial,
    )
    rows, total = db.list_cases(filters, sort, page, page_size, order=order)
    now = get_clock(request)()
    pending = pending_counts(db, rows)
    items = [
        build_queue_item(r, now=now, pending_second_approvals=pending.get(r["id"], 0)).model_dump(
            mode="json"
        )
        for r in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_next": page * page_size < total,
    }
