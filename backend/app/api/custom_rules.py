"""Custom rules (api.md section 9c, docs/custom-rules.md): officers describe a check in plain English, the
assistant turns it into one of four fixed templates, a fixed engine runs it.

Every route needs ``GLDB_CUSTOM_RULES=on`` (404 ``feature_off`` otherwise) and a personal sign-in (403
``account_required``): a custom rule belongs to the account that wrote it. A private rule, draft or ticket of
another account answers 404, as if it did not exist. Only the two routes that call the model (starting a
draft and sending words in a turn) cost money; listing, adopting, retiring, tickets, dry runs and saving never
touch the model, so they work when the account's budget is used up. All writes are POST.
"""

from __future__ import annotations

import threading
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool

from app.api._common import ApiRoute, DbDep, SettingsDep, get_clock, signed_in_reviewer
from app.catalogue import clean_reason
from app.core.config import SettingsError
from app.core.db import Database
from app.core.errors import ApiError
from app.core.llm import LLMClient, get_llm_client
from app.core.models import Strict
from app.custom_rules import author, capabilities, store, tickets
from app.custom_rules.spec import RULE_ID_RE

LABEL = "custom, unreviewed"
WITHDRAWN_NAME = "Withdrawn by its author"
router = APIRouter(prefix="/custom-rules", tags=["custom-rules"], route_class=ApiRoute)
_llm_lock = threading.Lock()


# --------------------------------------------------------------------------------------
# Gate, bodies, shared helpers
# --------------------------------------------------------------------------------------


def require_account(request: Request, settings: SettingsDep) -> str:
    """Feature off -> 404 ``feature_off``; nobody signed in by account -> 403 ``account_required``."""
    if settings.custom_rules != "on":
        raise ApiError(
            404,
            "feature_off",
            "Custom rules are switched off on this deployment (GLDB_CUSTOM_RULES).",
        )
    who = signed_in_reviewer(request)
    if who is None:
        raise ApiError(403, "account_required", "Custom rules need a personal sign-in.")
    return who


AccountDep = Annotated[str, Depends(require_account)]


class ReasonBody(Strict):
    reason: str | None = Field(default=None, max_length=author.BODY_TEXT_MAX)


class EnabledBody(Strict):
    enabled: StrictBool
    reason: str | None = Field(default=None, max_length=author.BODY_TEXT_MAX)


class EmptyBody(Strict):
    pass


def _llm(request: Request) -> LLMClient:
    """The runner's client when one was handed to the app (tests), else one built once for authoring: no
    response cache, because a cached answer would be another account's turn."""
    runner_llm = getattr(getattr(request.app.state, "runner", None), "llm", None)
    if runner_llm is not None:
        return runner_llm
    with _llm_lock:
        client = getattr(request.app.state, "custom_rules_llm", None)
        if client is None:
            try:
                client = get_llm_client(request.app.state.settings, cache=None)
            except SettingsError as exc:
                raise ApiError(503, "authoring_unavailable", author.UNAVAILABLE_MESSAGE) from exc
            request.app.state.custom_rules_llm = client
    return client


def _not_found(rule_id: str) -> ApiError:
    return ApiError(
        404, "not_found", "Custom rule not found.", {"kind": "custom_rule", "id": rule_id}
    )


def _forbidden(message: str) -> ApiError:
    return ApiError(403, "forbidden", message)


def _visible(db: Database, rule_id: str, viewer: str) -> store.RuleRow:
    """The rule if the viewer may see it (their own, or any public active one), else 404: a private rule
    of someone else is indistinguishable from a rule that does not exist."""
    rule = store.get_rule(db, rule_id) if RULE_ID_RE.match(rule_id) else None
    if rule is None or not store.can_view(rule, viewer):
        raise _not_found(rule_id)
    return rule


def _owned(db: Database, rule_id: str, viewer: str) -> store.RuleRow:
    rule = _visible(db, rule_id, viewer)
    if rule.owner != viewer:
        raise _forbidden("Only the author of a custom rule can do this.")
    return rule


def _version(db: Database, rule: store.RuleRow, number: int) -> store.VersionRow:
    row = store.get_version(db, rule.id, number)
    if row is None:  # unreachable while versions are append-only
        raise _not_found(rule.id)
    return row


def _spec_fields(row: store.VersionRow) -> dict[str, Any]:
    spec = row.spec
    return {
        "severity": spec.severity if spec else None,
        "template": spec.template if spec else None,
        "spec": spec.model_dump(mode="json", exclude_none=True) if spec else None,
        "spec_hash": row.spec_hash,
        "readback": row.readback,
    }


def rule_view(
    rule: store.RuleRow, row: store.VersionRow, viewer: str, adoptions: int
) -> dict[str, Any]:
    return {
        "rule_id": rule.id,
        "name": store.shown_name(rule, row),
        "description": store.shown_description(rule, row),
        "owner": rule.owner,
        "own": rule.owner == viewer,
        "version": row.version,
        **_spec_fields(row),
        "enabled": rule.enabled,
        "visibility": rule.visibility,
        "published_version": rule.published_version,
        "status": rule.status,
        "adoptions": adoptions,
        "label": LABEL,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _own_view(db: Database, rule: store.RuleRow, viewer: str) -> dict[str, Any]:
    count = store.adoption_counts(db, [rule.id])[rule.id]
    return rule_view(rule, _version(db, rule, rule.current_version), viewer, count)


def _adopted_view(db: Database, adoption: store.AdoptionRow, rule: store.RuleRow) -> dict[str, Any]:
    if not rule.public:
        # taken back (unpublished, by an administrator, or retired): private again, so nothing the
        # owner wrote (name, readback, severity) is shown; the adopter can still stop using it
        return {
            "rule_id": rule.id,
            "name": WITHDRAWN_NAME,
            "author": rule.owner,
            "version": adoption.version,
            "latest_version": adoption.version,
            "update_available": False,
            "active": False,
            "severity": None,
            "template": None,
            "readback": "",
            "adopted_at": adoption.adopted_at,
            "label": LABEL,
        }
    pinned = _version(db, rule, adoption.version)
    latest = rule.published_version if rule.public and rule.published_version else adoption.version
    return {
        "rule_id": rule.id,
        "name": store.shown_name(rule, pinned),
        "author": rule.owner,
        "version": adoption.version,
        "latest_version": latest,
        "update_available": latest > adoption.version,
        "active": rule.public,
        "severity": pinned.spec.severity if pinned.spec else None,
        "template": pinned.spec.template if pinned.spec else None,
        "readback": pinned.readback,
        "adopted_at": adoption.adopted_at,
        "label": LABEL,
    }


def _ticket_ref(row: tickets.TicketRow) -> dict[str, Any]:
    return {"id": row.id, "status": "submitted", "message": tickets.ticket_message(row.id)}


# --------------------------------------------------------------------------------------
# What is possible, and the authoring conversation
# --------------------------------------------------------------------------------------


@router.get("/capabilities")
def get_custom_capabilities(_: AccountDep, settings: SettingsDep) -> dict[str, Any]:
    return capabilities.catalogue(settings)


@router.post("/drafts")
def start_custom_draft(
    body: author.StartDraftRequest,
    request: Request,
    account: AccountDep,
    db: DbDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    return author.start_draft(
        db, settings, _LazyLLM(request), account=account, req=body, now=get_clock(request)()
    ).model_dump(mode="json")


@router.get("/drafts/{draft_id}")
def get_custom_draft(draft_id: str, account: AccountDep, db: DbDep) -> dict[str, Any]:
    return author.get_draft(db, account=account, draft_id=draft_id).model_dump(mode="json")


@router.post("/drafts/{draft_id}/turns")
def send_custom_turn(
    draft_id: str,
    body: author.TurnRequest,
    request: Request,
    account: AccountDep,
    db: DbDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    return author.continue_draft(
        db,
        settings,
        _LazyLLM(request),
        account=account,
        draft_id=draft_id,
        req=body,
        now=get_clock(request)(),
    ).model_dump(mode="json")


class _LazyLLM:
    """The model client, built only if the turn calls it: an answer or a choice never does, so those
    work even when the model backend is not configured."""

    def __init__(self, request: Request):
        self._request = request

    def complete_json(self, req: Any, *, ctx_costs: Any = None) -> Any:
        return _llm(self._request).complete_json(req, ctx_costs=ctx_costs)


@router.post("/drafts/{draft_id}/dry-run")
def dry_run_custom_draft(
    draft_id: str, request: Request, account: AccountDep, db: DbDep, settings: SettingsDep
) -> dict[str, Any]:
    return author.dry_run_draft(
        db, settings, account=account, draft_id=draft_id, now=get_clock(request)()
    ).model_dump(mode="json")


# --------------------------------------------------------------------------------------
# Saving and listing
# --------------------------------------------------------------------------------------


@router.post("", status_code=201)
def save_custom_rule(
    body: author.SaveRuleRequest,
    request: Request,
    account: AccountDep,
    db: DbDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    result = author.save_draft(
        db,
        settings,
        account=account,
        req=body,
        admins=settings.admin_user_set,
        now=get_clock(request)(),
    )
    return {
        "rule": _own_view(db, result.rule, account),
        "ticket": _ticket_ref(result.ticket) if result.ticket else None,
    }


@router.get("")
def list_custom_rules(account: AccountDep, db: DbDep) -> dict[str, Any]:
    mine = store.list_owned(db, account)
    counts = store.adoption_counts(db, [r.id for r in mine])
    adopted: list[dict[str, Any]] = []
    for adoption in store.list_adoptions(db, account):
        rule = store.get_rule(db, adoption.rule_id)
        if rule is not None:
            adopted.append(_adopted_view(db, adoption, rule))
    return {
        "mine": [
            rule_view(r, _version(db, r, r.current_version), account, counts[r.id]) for r in mine
        ],
        "adopted": adopted,
    }


@router.get("/shared")
def list_shared_custom_rules(account: AccountDep, db: DbDep) -> dict[str, Any]:
    """The public library: every public active rule, author attributed, labelled custom and unreviewed."""
    public = store.list_public(db)
    counts = store.adoption_counts(db, [r.id for r in public])
    pins = {a.rule_id: a.version for a in store.list_adoptions(db, account)}
    out = []
    for rule in public:
        if rule.published_version is None:
            continue
        row = _version(db, rule, rule.published_version)
        out.append(
            {
                "rule_id": rule.id,
                "name": store.shown_name(rule, row),
                "author": rule.owner,
                "description": store.shown_description(rule, row),
                "version": row.version,
                "severity": row.spec.severity if row.spec else None,
                "template": row.spec.template if row.spec else None,
                "readback": row.readback,
                "published_at": rule.published_at,
                "own": rule.owner == account,
                "adopted": rule.id in pins,
                "adopted_version": pins.get(rule.id),
                "adoptions": counts[rule.id],
                "label": LABEL,
            }
        )
    return {"rules": out}


@router.get("/changes")
def list_custom_rule_changes(
    account: AccountDep,
    db: DbDep,
    settings: SettingsDep,
    rule_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=store.CHANGES_MAX_LIMIT)] = store.CHANGES_DEFAULT_LIMIT,
) -> dict[str, Any]:
    if rule_id is not None and not RULE_ID_RE.match(rule_id):
        raise _not_found(rule_id)
    admin = account in settings.admin_user_set
    return {"changes": store.changes(db, viewer=account, admin=admin, rule_id=rule_id, limit=limit)}


# --------------------------------------------------------------------------------------
# Requests to the tech team (no model)
# --------------------------------------------------------------------------------------


@router.get("/tickets")
def list_custom_tickets(account: AccountDep, db: DbDep, settings: SettingsDep) -> dict[str, Any]:
    admin = account in settings.admin_user_set
    return {"tickets": [t.view() for t in tickets.list_tickets(db, viewer=account, admin=admin)]}


@router.post("/tickets")
def submit_custom_ticket(
    body: author.TicketRequest,
    request: Request,
    account: AccountDep,
    db: DbDep,
    settings: SettingsDep,
) -> JSONResponse:
    now = get_clock(request)()
    if body.draft_id is not None:
        ticket, existing = author.ticket_from_draft(
            db, settings, account=account, draft_id=body.draft_id, severity=body.severity, now=now
        )
    else:
        assert body.text is not None
        ticket = author.ticket_from_text(
            db, settings, account=account, text=body.text, severity=body.severity, now=now
        )
        existing = False
    return JSONResponse(
        {
            "ticket": ticket.view(),
            "message": tickets.ticket_message(ticket.id),
            "existing": existing,
        },
        status_code=200 if existing else 201,
    )


# --------------------------------------------------------------------------------------
# One rule (declared after the static paths above)
# --------------------------------------------------------------------------------------


@router.get("/{rule_id}")
def get_custom_rule(rule_id: str, account: AccountDep, db: DbDep) -> dict[str, Any]:
    rule = _visible(db, rule_id, account)
    count = store.adoption_counts(db, [rule.id])[rule.id]
    if rule.owner != account:  # another account sees a public rule at its published version only
        assert rule.published_version is not None
        return rule_view(rule, _version(db, rule, rule.published_version), account, count)
    view = rule_view(rule, _version(db, rule, rule.current_version), account, count)
    versions = store.list_versions(db, rule.id)
    view["versions"] = [
        {
            "version": v.version,
            "spec": v.spec.model_dump(mode="json", exclude_none=True) if v.spec else None,
            "spec_hash": v.spec_hash,
            "readback": v.readback,
            "created_at": v.created_at,
            "created_by": v.created_by,
            "reason": v.reason,
        }
        for v in versions
    ]
    view["last_dry_run"] = author.saved_dry_run(
        db, account, versions[-1].draft_id, versions[-1].spec_hash
    )
    return view


@router.post("/{rule_id}/enabled")
def set_custom_rule_enabled(
    rule_id: str,
    body: EnabledBody,
    request: Request,
    account: AccountDep,
    db: DbDep,
) -> dict[str, Any]:
    _owned(db, rule_id, account)
    reason = clean_reason(body.reason)
    rule = store.set_enabled(
        db, rule_id, body.enabled, actor=account, reason=reason, at=get_clock(request)()
    )
    return {"rule": _own_view(db, rule, account)}


@router.post("/{rule_id}/publish")
def publish_custom_rule(
    rule_id: str, body: ReasonBody, request: Request, account: AccountDep, db: DbDep
) -> dict[str, Any]:
    rule = _owned(db, rule_id, account)
    reason = clean_reason(body.reason)
    if rule.status == "active":
        row = _version(db, rule, rule.current_version)
        if row.spec is not None:
            for other, number in store.rules_with_condition(db, row.condition_hash):
                if other.id != rule.id and other.public and other.published_version == number:
                    shown = _version(db, other, number)
                    raise ApiError(
                        409,
                        "duplicate_custom",
                        "A public custom rule that does the same already exists. Adopt it instead.",
                        {"rule_id": other.id, "name": store.shown_name(other, shown)},
                    )
    rule = store.publish(db, rule_id, actor=account, reason=reason, at=get_clock(request)())
    return {"rule": _own_view(db, rule, account)}


@router.post("/{rule_id}/unpublish")
def unpublish_custom_rule(
    rule_id: str,
    body: ReasonBody,
    request: Request,
    account: AccountDep,
    db: DbDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    rule = _visible(db, rule_id, account)
    admins = settings.admin_user_set
    if rule.owner != account and not store.can_unpublish(rule, account, admins):
        raise _forbidden("Only the author, or an administrator, can take a public rule back.")
    reason = clean_reason(body.reason)
    shown_as = (
        store.shown_name(rule, _version(db, rule, rule.published_version))
        if rule.published_version
        else rule.name
    )
    rule = store.unpublish(
        db,
        rule_id,
        actor=account,
        reason=reason,
        at=get_clock(request)(),
        as_admin=account in admins,
    )
    if rule.owner != account:  # an administrator learns the outcome, not the private rule's content
        return {
            "rule": {
                "rule_id": rule.id,
                "name": shown_as,
                "owner": rule.owner,
                "own": False,
                "visibility": rule.visibility,
                "published_version": rule.published_version,
                "status": rule.status,
                "label": LABEL,
            }
        }
    return {"rule": _own_view(db, rule, account)}


@router.post("/{rule_id}/adopt")
def adopt_custom_rule(
    rule_id: str,
    request: Request,
    account: AccountDep,
    db: DbDep,
    body: Annotated[EmptyBody | None, Body()] = None,
) -> dict[str, Any]:
    rule = _visible(db, rule_id, account)
    adoption = store.adopt(db, rule.id, account, at=get_clock(request)())
    return {"adoption": _adopted_view(db, adoption, store.get_rule(db, rule.id) or rule)}


@router.post("/{rule_id}/unadopt")
def unadopt_custom_rule(
    rule_id: str,
    request: Request,
    account: AccountDep,
    db: DbDep,
    body: Annotated[EmptyBody | None, Body()] = None,
) -> dict[str, Any]:
    rule = store.get_rule(db, rule_id) if RULE_ID_RE.match(rule_id) else None
    held = any(a.rule_id == rule_id for a in store.list_adoptions(db, account))
    if rule is None or (not store.can_view(rule, account) and not held):
        raise _not_found(rule_id)
    store.unadopt(db, rule_id, account, at=get_clock(request)())
    return {"rule_id": rule_id, "adopted": False}


@router.post("/{rule_id}/retire")
def retire_custom_rule(
    rule_id: str, body: ReasonBody, request: Request, account: AccountDep, db: DbDep
) -> dict[str, Any]:
    _owned(db, rule_id, account)
    reason = clean_reason(body.reason)
    rule = store.retire(db, rule_id, actor=account, reason=reason, at=get_clock(request)())
    return {"rule": _own_view(db, rule, account)}
