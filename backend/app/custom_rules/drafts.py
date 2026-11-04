"""The authoring conversation, stored (table ``custom_rule_drafts``; docs/custom-rules.md).

A draft is one officer's bounded conversation with the rule assistant: at most ``DRAFT_MAX_TURNS`` turns
and ``MODEL_CALLS_PER_TURN`` model calls per turn. It belongs to the account that started it and is
invisible to every other account (a lookup of someone else's draft answers 404). The body is the whole
state needed to render a turn again and to continue it; only code-built data and the officer's own words
are kept, never document content.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.db import Database, ts
from app.core.errors import ApiError
from app.core.models import Strict
from app.custom_rules.spec import (
    DraftStatus,
    DryRunResult,
    ModelDraft,
    Question,
    Refusal,
    SpecProblem,
)


class DraftState(Strict):
    """The JSON in ``custom_rule_drafts.body``."""

    model: ModelDraft | None = None  # the last answer of the model, as sanitised and validated
    # the status the model's answer stands for now; an officer's answer or choice settles a question
    # or a choice between readings, so ``decide_status`` is not held back by it any longer
    model_status: DraftStatus = "needs_clarification"
    status: DraftStatus = "needs_clarification"  # the final status code decided
    spec: dict[str, Any] | None = None  # the current spec (after answers)
    candidates: list[dict[str, Any]] = []  # valid readings of an ambiguous request
    weaker: dict[str, Any] | None = None  # a smaller rule the officer could save instead (valid)
    questions: list[Question] = []  # still open
    problems: list[SpecProblem] = []  # why the model's spec was not usable (code text)
    uncovered: list[str] = []  # words of the request that no phrase accounted for
    notes: list[str] = []  # code-written: why the status was lowered
    refusal: Refusal | None = None
    desired_severity: str | None = None
    dry_run: DryRunResult | None = None
    ticket_id: str | None = None
    history: list[
        dict[str, Any]
    ] = []  # {"turn", "kind": text|answer|candidate, "text", "status", "at"}
    last_calls: int = 0  # model calls of the last turn


@dataclass
class DraftRecord:
    id: str
    owner: str
    state: str  # open | saved | closed
    edit_of: str | None
    turns: int
    model_calls: int
    micro_usd: int
    prompt_sha256: str | None
    request: str
    body: DraftState
    saved_rule_id: str | None
    created_at: str
    updated_at: str


def new_draft_id() -> str:
    return "crd_" + uuid.uuid4().hex[:12]


def _record(row: Any) -> DraftRecord:
    return DraftRecord(
        id=row["id"],
        owner=row["owner"],
        state=row["state"],
        edit_of=row["edit_of"],
        turns=row["turns"],
        model_calls=row["model_calls"],
        micro_usd=row["micro_usd"],
        prompt_sha256=row["prompt_sha256"],
        request=row["request"],
        body=DraftState.model_validate_json(row["body"]),
        saved_rule_id=row["saved_rule_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _body_text(state: DraftState) -> str:
    return json.dumps(state.model_dump(mode="json"), ensure_ascii=False)


def create_draft(
    db: Database,
    *,
    owner: str,
    request: str,
    edit_of: str | None,
    state: DraftState,
    prompt_sha256: str | None,
    now: datetime,
    model_calls: int = 0,
    micro_usd: int = 0,
) -> DraftRecord:
    """A new open draft at turn 1 (the first request is turn 1)."""
    draft_id = new_draft_id()
    stamp = ts(now)
    with db.conn() as c:
        c.execute(
            "INSERT INTO custom_rule_drafts (id, owner, state, edit_of, turns, model_calls, micro_usd, "
            "prompt_sha256, request, body, saved_rule_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?)",
            (
                draft_id,
                owner,
                "open",
                edit_of,
                1,
                model_calls,
                micro_usd,
                prompt_sha256,
                request,
                _body_text(state),
                stamp,
                stamp,
            ),
        )
        row = c.execute("SELECT * FROM custom_rule_drafts WHERE id = ?", (draft_id,)).fetchone()
    return _record(row)


def load_draft(db: Database, draft_id: str) -> DraftRecord | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM custom_rule_drafts WHERE id = ?", (draft_id,)).fetchone()
    return None if row is None else _record(row)


def update_draft(
    db: Database, rec: DraftRecord, *, expected_turns: int, now: datetime
) -> DraftRecord:
    """Write ``rec`` (body, turns, state, saved rule) if no other turn got in first: the row must still
    be at ``expected_turns`` and open or the one being closed here. 409 ``draft_busy`` on a lost race."""
    with db.conn() as c:
        cur = c.execute(
            "UPDATE custom_rule_drafts SET state = ?, turns = ?, request = ?, body = ?, "
            "saved_rule_id = ?, updated_at = ?, model_busy_at = NULL "
            "WHERE id = ? AND turns = ? AND state = 'open'",
            (
                rec.state,
                rec.turns,
                rec.request,
                _body_text(rec.body),
                rec.saved_rule_id,
                ts(now),
                rec.id,
                expected_turns,
            ),
        )
        if cur.rowcount != 1:
            raise ApiError(
                409,
                "draft_busy",
                "Another turn of this draft is in progress or it was closed. Reload it and try again.",
                {"draft_id": rec.id},
            )
        row = c.execute("SELECT * FROM custom_rule_drafts WHERE id = ?", (rec.id,)).fetchone()
    return _record(row)


MODEL_BUSY_SECONDS = (
    120  # a paid call that never came back stops blocking the draft after this long
)


def claim_model(db: Database, draft_id: str, *, expected_turns: int, now: datetime) -> None:
    """Mark the draft as waiting for the model BEFORE the paid call, so that of several parallel text
    turns on one draft only one reaches the model. 409 ``draft_busy`` for the others (no money spent).
    The mark is cleared when the turn is stored (``update_draft``) or given up (``release_model``)."""
    stale = ts(now - timedelta(seconds=MODEL_BUSY_SECONDS))
    with db.conn() as c:
        cur = c.execute(
            "UPDATE custom_rule_drafts SET model_busy_at = ? WHERE id = ? AND turns = ? "
            "AND state = 'open' AND (model_busy_at IS NULL OR model_busy_at < ?)",
            (ts(now), draft_id, expected_turns, stale),
        )
    if cur.rowcount != 1:
        raise ApiError(
            409,
            "draft_busy",
            "Another turn of this draft is in progress or it was closed. Reload it and try again.",
            {"draft_id": draft_id},
        )


def release_model(db: Database, draft_id: str) -> None:
    """Clear the mark of a turn that failed before anything was stored."""
    with db.conn() as c:
        c.execute("UPDATE custom_rule_drafts SET model_busy_at = NULL WHERE id = ?", (draft_id,))


def add_spend(db: Database, draft_id: str, *, calls: int, micro_usd: int) -> None:
    """Add the model calls and spend of a turn (also of a turn that failed: the money was spent)."""
    if calls <= 0 and micro_usd <= 0:
        return
    with db.conn() as c:
        c.execute(
            "UPDATE custom_rule_drafts SET model_calls = model_calls + ?, micro_usd = micro_usd + ? "
            "WHERE id = ?",
            (max(calls, 0), max(micro_usd, 0), draft_id),
        )
