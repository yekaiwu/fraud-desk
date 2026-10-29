"""Background jobs (api.md section 7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api._common import DbDep, not_found
from app.core.db import JobRecord
from app.core.models import STAGE_ORDER

router = APIRouter(tags=["jobs"])


def job_progress(job: JobRecord) -> dict[str, Any]:
    """1-based position of the running stage; the last stage when the job succeeded."""
    count = len(STAGE_ORDER)
    running = next((s for s in job.stages if s.status == "running"), None)
    if running is not None:
        return {
            "stage_index": STAGE_ORDER.index(running.stage) + 1,
            "stage_count": count,
            "current_stage": running.stage,
        }
    if job.state == "succeeded":
        return {"stage_index": count, "stage_count": count, "current_stage": None}
    finished = [STAGE_ORDER.index(s.stage) + 1 for s in job.stages]
    return {"stage_index": max(finished, default=0), "stage_count": count, "current_stage": None}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: DbDep) -> dict[str, Any]:
    job = db.get_job(job_id)
    if job is None:
        raise not_found("job", id=job_id)
    data = job.model_dump(mode="json")
    data["progress"] = job_progress(job)
    return data
