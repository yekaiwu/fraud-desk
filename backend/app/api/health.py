"""GET /health (api.md section 11): liveness plus the UI-relevant settings. Never any secret."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.checks.registry import all_checks
from app.core.config import APP_VERSION
from app.core.provenance import ruleset_version

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    settings = request.app.state.settings
    checks = all_checks()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "llm_backend": settings.effective_llm_backend(),
        "ruleset_version": ruleset_version(checks),
        "checks_registered": len(checks),
        "settings": {
            "classifier_min_prob": settings.classifier_min_prob,
            "max_upload_mb": settings.max_upload_mb,
            "max_files_per_case": settings.max_files_per_case,
            "require_second_approver": settings.require_second_approver,
        },
    }
