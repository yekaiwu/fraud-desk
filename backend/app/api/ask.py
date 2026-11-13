"""Ask this case (docs/case-assistant.md, api.md section 9d).

``POST /cases/{id}/ask`` answers a question about documents already extracted. It needs
``GLDB_CASE_ASSISTANT=on`` (404 ``feature_off`` otherwise) and a personal sign-in (403
``account_required``). A ``case_version`` that is not the current one is 409 ``stale_version``.
The route never writes a flag, a score, a lane or a decision.
"""

from __future__ import annotations

import threading
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.api._common import (
    ApiRoute,
    DbDep,
    SettingsDep,
    audit_for,
    get_clock,
    load_case_or_404,
    signed_in_reviewer,
)
from app.assistant import prompts as _prompts  # noqa: F401 - registers assistant:ask
from app.assistant.ask import answer, audit_detail
from app.core.config import SettingsError
from app.core.errors import ApiError
from app.core.llm import LLMClient, get_llm_client
from app.core.models import Strict

router = APIRouter(tags=["ask"], route_class=ApiRoute)
_llm_lock = threading.Lock()
QUESTION_MAX = 500


def require_account(request: Request, settings: SettingsDep) -> str:
    """Feature off -> 404 ``feature_off``; nobody signed in by account -> 403 ``account_required``."""
    if settings.case_assistant != "on":
        raise ApiError(
            404,
            "feature_off",
            "Ask this case is switched off on this deployment (GLDB_CASE_ASSISTANT).",
        )
    who = signed_in_reviewer(request)
    if who is None:
        raise ApiError(403, "account_required", "Ask this case needs a personal sign-in.")
    return who


AccountDep = Annotated[str, Depends(require_account)]


class AskBody(Strict):
    question: str = Field(min_length=1, max_length=QUESTION_MAX)
    scope: Literal["document", "case"]
    file_id: str | None = Field(default=None, max_length=80)
    page: int | None = Field(default=None, ge=1)
    case_version: int = Field(ge=1)


def _client(request: Request) -> LLMClient:
    """The runner's client when a test handed one in, else one built for questions (no shared cache)."""
    runner_llm = getattr(getattr(request.app.state, "runner", None), "llm", None)
    if runner_llm is not None:
        return runner_llm
    with _llm_lock:
        client = getattr(request.app.state, "case_assistant_llm", None)
        if client is None:
            try:
                client = get_llm_client(request.app.state.settings, cache=None)
            except SettingsError as exc:
                from app.assistant.prompts import UNAVAILABLE

                raise ApiError(503, "assistant_unavailable", UNAVAILABLE) from exc
            request.app.state.case_assistant_llm = client
    return client


def _audit(
    request: Request,
    *,
    actor: str,
    case_id: str,
    question: str,
    scope: str,
    version: int,
    status: str,
    citations_kept: int = 0,
    citations_dropped: int = 0,
    dropped: dict | None = None,
    model: str = "",
    usd: str = "0.000000",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    seconds: float = 0.0,
) -> None:
    audit_for(request).write(
        actor=actor,
        action="assistant.asked",
        case_id=case_id,
        detail=audit_detail(
            version=version,
            question=question,
            scope=scope,
            status=status,
            citations_kept=citations_kept,
            citations_dropped=citations_dropped,
            dropped=dropped or {},
            model=model,
            usd=usd,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            seconds=seconds,
        ),
    )


@router.post("/cases/{case_id}/ask")
def ask_case(
    case_id: str,
    body: AskBody,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    account: AccountDep,
) -> dict:
    question = " ".join(body.question.split())
    if not question:
        raise ApiError(422, "validation_error", "Type a question.", {"field": "question"})
    if len(question) > QUESTION_MAX:
        raise ApiError(
            422,
            "validation_error",
            f"A question is at most {QUESTION_MAX} characters.",
            {"field": "question", "max": QUESTION_MAX},
        )
    if body.scope == "document" and not (body.file_id and body.file_id.strip()):
        raise ApiError(
            422,
            "validation_error",
            "Say which document to ask about.",
            {"field": "file_id"},
        )
    case = load_case_or_404(db, case_id)
    if body.case_version != case.version:
        raise ApiError(
            409,
            "stale_version",
            "The case has a newer version than the one you are looking at.",
            {"current_version": case.version, "sent_version": body.case_version},
        )
    try:
        outcome = answer(
            db,
            settings,
            _client(request),
            case,
            account=account,
            question=question,
            scope=body.scope,
            file_id=body.file_id,
            page=body.page,
            now=get_clock(request)(),
        )
    except ApiError as exc:
        if exc.code in {
            "user_budget_used",
            "site_budget_used",
            "case_budget_used",
            "assistant_unavailable",
        }:
            _audit(
                request,
                actor=account,
                case_id=case_id,
                question=question,
                scope=body.scope,
                version=case.version,
                status=exc.code,
            )
        raise
    call = outcome["call"]
    _audit(
        request,
        actor=account,
        case_id=case_id,
        question=question,
        scope=body.scope,
        version=case.version,
        status=call["status"],
        citations_kept=call["citations_kept"],
        citations_dropped=call["citations_dropped"],
        dropped=call["dropped"],
        model=call["model"],
        usd=call["usd"],
        prompt_tokens=call["prompt_tokens"],
        completion_tokens=call["completion_tokens"],
        seconds=call["seconds"],
    )
    return outcome["body"]
