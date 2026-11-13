"""GET /health (api.md section 11): liveness plus the UI-relevant settings. Never any secret."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api._common import signed_in_reviewer
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


@router.get("/me")
def me(request: Request) -> dict:
    """Who the proxy says is signed in, so the screen can show the name the audit log will carry, whether
    that account may open the Spending page (``GLDB_ADMIN_USERS``), and whether it may use custom rules
    or Ask this case (each feature is on, and a personal account is signed in)."""
    who = signed_in_reviewer(request)
    settings = request.app.state.settings
    return {
        "reviewer": who,
        "admin": who is not None and who in settings.admin_user_set,
        "custom_rules": settings.custom_rules == "on" and who is not None,
        "case_assistant": settings.case_assistant == "on" and who is not None,
    }
