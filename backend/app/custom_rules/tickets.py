"""Requests routed to the tech team (table ``custom_rule_tickets``; docs/custom-rules.md).

When an idea is too complex for the four templates, the officer is told plainly that it "will be submitted
to the tech team" and a structured ticket is stored: intent, wanted severity, the documents involved, what
is missing, the officer's own words, who asked and when. The thin slice stops at status ``submitted``: the
"tech team" is whoever reads the list (the accounts in ``GLDB_ADMIN_USERS`` see every ticket, an officer
sees their own). A ticket never needs the model, so it works with the budget used up.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.audit import AuditLog
from app.core.config import Settings
from app.core.db import Database, ts
from app.core.models import clean_text

INTENT_MAX = 300
ORIGINAL_WORDS_MAX = 1000
PHRASE_MAX = 120
MAX_MISSING = 6
MAX_DOC_TYPES = 6


@dataclass(frozen=True)
class TicketRow:
    id: str
    account: str
    created_at: str
    status: str
    origin: str  # unsupported | partial | officer
    intent: str
    desired_severity: str | None
    doc_types: list[str]
    missing: list[dict[str, Any]]  # {"phrase", "reason"}
    original_words: str
    draft_id: str | None
    weaker_rule_id: str | None

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account": self.account,
            "created_at": self.created_at,
            "status": self.status,
            "origin": self.origin,
            "intent": self.intent,
            "desired_severity": self.desired_severity,
            "doc_types": list(self.doc_types),
            "missing": list(self.missing),
            "original_words": self.original_words,
            "draft_id": self.draft_id,
            "weaker_rule_id": self.weaker_rule_id,
        }


def new_ticket_id() -> str:
    return "crt_" + uuid.uuid4().hex[:12]


def ticket_message(ticket_id: str) -> str:
    return f"This will be submitted to the tech team (ticket {ticket_id})."


def _row(r: Any) -> TicketRow:
    return TicketRow(
        id=r["id"],
        account=r["account"],
        created_at=r["created_at"],
        status=r["status"],
        origin=r["origin"],
        intent=r["intent"],
        desired_severity=r["desired_severity"],
        doc_types=json.loads(r["doc_types"]),
        missing=json.loads(r["missing"]),
        original_words=r["original_words"],
        draft_id=r["draft_id"],
        weaker_rule_id=r["weaker_rule_id"],
    )


def get_ticket(db: Database, ticket_id: str) -> TicketRow | None:
    with db.conn() as c:
        row = c.execute("SELECT * FROM custom_rule_tickets WHERE id = ?", (ticket_id,)).fetchone()
    return None if row is None else _row(row)


def ticket_for_draft(db: Database, draft_id: str) -> TicketRow | None:
    with db.conn() as c:
        row = c.execute(
            "SELECT * FROM custom_rule_tickets WHERE draft_id = ?", (draft_id,)
        ).fetchone()
    return None if row is None else _row(row)


def create_ticket(
    db: Database,
    settings: Settings,
    *,
    account: str,
    origin: str,
    intent: str,
    desired_severity: str | None,
    doc_types: Sequence[str],
    missing: Sequence[dict[str, str]],
    original_words: str,
    draft_id: str | None,
    weaker_rule_id: str | None,
    now: datetime,
) -> TicketRow:
    """Store a ticket. One per draft: a second call for the same draft returns the first. Text is
    cleaned (the officer's words and the model's phrases are untrusted plain text)."""
    if draft_id is not None:
        existing = ticket_for_draft(db, draft_id)
        if existing is not None:
            return existing
    ticket_id = new_ticket_id()
    cleaned_missing = [
        {"phrase": clean_text(str(m.get("phrase", "")), PHRASE_MAX), "reason": str(m["reason"])}
        for m in list(missing)[:MAX_MISSING]
    ]
    try:
        with db.conn() as c:
            c.execute(
                "INSERT INTO custom_rule_tickets (id, account, created_at, status, origin, "
                "intent, desired_severity, doc_types, missing, original_words, draft_id, "
                "weaker_rule_id) VALUES (?,?,?,'submitted',?,?,?,?,?,?,?,?)",
                (
                    ticket_id,
                    account,
                    ts(now),
                    origin,
                    clean_text(intent, INTENT_MAX),
                    desired_severity,
                    json.dumps(sorted(set(doc_types))[:MAX_DOC_TYPES]),
                    json.dumps(cleaned_missing, ensure_ascii=False),
                    clean_text(original_words, ORIGINAL_WORDS_MAX),
                    draft_id,
                    weaker_rule_id,
                ),
            )
            AuditLog(db, settings, clock=lambda: now).write(
                actor=account,
                action="custom_rule.ticket",
                case_id=None,
                detail={"ticket_id": ticket_id, "origin": origin, "draft_id": draft_id},
                conn=c,
            )
    except sqlite3.IntegrityError:
        # two requests for one draft at once: the other one won (UNIQUE draft_id); anything else is a bug
        existing = ticket_for_draft(db, draft_id) if draft_id else None
        if existing is None:
            raise
        return existing
    row = get_ticket(db, ticket_id)
    assert row is not None
    return row


def list_tickets(db: Database, *, viewer: str, admin: bool, limit: int = 100) -> list[TicketRow]:
    """Newest first: an officer sees their own requests, an administrator sees all."""
    limit = max(1, min(limit, 500))
    sql = "SELECT * FROM custom_rule_tickets"
    args: list[Any] = []
    if not admin:
        sql += " WHERE account = ?"
        args.append(viewer)
    sql += " ORDER BY created_at DESC, id LIMIT ?"
    args.append(limit)
    with db.conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row(r) for r in rows]
