"""Requests routed to the tech team (app/custom_rules/tickets.py)."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.core.db import Database
from app.core.testing import FIXED_NOW
from app.custom_rules import tickets


def new(
    db: Database,
    settings: Settings,
    *,
    account: str = "alice",
    draft_id: str | None = "crd_0123456789ab",
    now=FIXED_NOW,  # noqa: ANN001
    **kw,  # noqa: ANN003
) -> tickets.TicketRow:
    args = {
        "origin": "unsupported",
        "intent": "Flag companies that post about money problems on social media.",
        "desired_severity": "low",
        "doc_types": [],
        "missing": [{"phrase": "posts on social media", "reason": "doc_not_extracted"}],
        "original_words": "flag if the company posts about money problems on social media",
        "weaker_rule_id": None,
        **kw,
    }
    return tickets.create_ticket(db, settings, account=account, draft_id=draft_id, now=now, **args)


def test_the_message_names_the_tech_team_and_the_ticket() -> None:
    assert (
        tickets.ticket_message("crt_0123456789ab")
        == "This will be submitted to the tech team (ticket crt_0123456789ab)."
    )


def test_a_ticket_has_the_structured_request(db: Database, settings: Settings) -> None:
    t = new(db, settings, doc_types=["invoice", "invoice", "receipt"])
    assert re.fullmatch(r"crt_[0-9a-f]{12}", t.id)
    assert t.status == "submitted" and t.origin == "unsupported" and t.account == "alice"
    assert t.intent.startswith("Flag companies") and t.desired_severity == "low"
    assert t.doc_types == ["invoice", "receipt"]
    assert t.missing == [{"phrase": "posts on social media", "reason": "doc_not_extracted"}]
    assert t.original_words.startswith("flag if the company posts")
    assert t.draft_id == "crd_0123456789ab" and t.weaker_rule_id is None
    assert t.created_at.endswith("Z")
    assert tickets.get_ticket(db, t.id) == t
    assert set(t.view()) == {
        "id",
        "account",
        "created_at",
        "status",
        "origin",
        "intent",
        "desired_severity",
        "doc_types",
        "missing",
        "original_words",
        "draft_id",
        "weaker_rule_id",
    }


def test_one_ticket_per_draft(db: Database, settings: Settings) -> None:
    first = new(db, settings)
    second = new(db, settings, intent="a different intent")
    assert second == first
    assert tickets.ticket_for_draft(db, "crd_0123456789ab") == first
    assert len(tickets.list_tickets(db, viewer="alice", admin=False)) == 1


def test_tickets_without_a_draft_are_all_kept(db: Database, settings: Settings) -> None:
    a = new(db, settings, draft_id=None, origin="officer")
    b = new(db, settings, draft_id=None, origin="officer")
    assert a.id != b.id and a.origin == "officer"


def test_the_officers_words_and_the_phrases_are_cleaned_plain_text(
    db: Database, settings: Settings
) -> None:
    t = new(
        db,
        settings,
        intent="  Flag‮ this\x00\n\nplease  " + "x" * 400,
        original_words="a​b\x07c " + "y" * 1200,
        missing=[{"phrase": "bad‮phrase\n" + "z" * 300, "reason": "needs_external"}],
    )
    assert "‮" not in t.intent and "\x00" not in t.intent and "\n" not in t.intent
    assert len(t.intent) <= 300 and len(t.original_words) <= 1000
    assert "​" not in t.original_words and "\x07" not in t.original_words
    assert len(t.missing[0]["phrase"]) <= 120 and "‮" not in t.missing[0]["phrase"]


def test_at_most_six_missing_parts_and_six_documents(db: Database, settings: Settings) -> None:
    t = new(
        db,
        settings,
        missing=[{"phrase": f"p{i}", "reason": "needs_judgement"} for i in range(10)],
        doc_types=[f"doc{i}" for i in range(10)],
    )
    assert len(t.missing) == 6 and len(t.doc_types) == 6


def test_creating_a_ticket_writes_one_audit_row_without_the_words(
    db: Database, settings: Settings
) -> None:
    t = new(db, settings)
    new(db, settings)  # the same draft again: no second row
    with db.conn() as c:
        rows = c.execute("SELECT * FROM audit_log WHERE action = 'custom_rule.ticket'").fetchall()
    assert len(rows) == 1
    assert rows[0]["actor"] == "alice" and rows[0]["case_ref"] is None
    assert json.loads(rows[0]["detail"]) == {
        "ticket_id": t.id,
        "origin": "unsupported",
        "draft_id": "crd_0123456789ab",
    }
    assert "social media" not in rows[0]["detail"]


def test_an_officer_sees_their_own_and_an_administrator_sees_all(
    db: Database, settings: Settings
) -> None:
    mine = new(db, settings, account="alice", draft_id="crd_aaaaaaaaaaaa", now=FIXED_NOW)
    theirs = new(
        db,
        settings,
        account="bob",
        draft_id="crd_bbbbbbbbbbbb",
        now=FIXED_NOW + timedelta(minutes=1),
    )
    assert [t.id for t in tickets.list_tickets(db, viewer="alice", admin=False)] == [mine.id]
    assert [t.id for t in tickets.list_tickets(db, viewer="bob", admin=False)] == [theirs.id]
    assert [t.id for t in tickets.list_tickets(db, viewer="root", admin=True)] == [
        theirs.id,
        mine.id,
    ]
    assert tickets.list_tickets(db, viewer="carol", admin=False) == []


def test_the_list_is_bounded(db: Database, settings: Settings) -> None:
    for i in range(5):
        new(db, settings, draft_id=None, origin="officer", now=FIXED_NOW + timedelta(seconds=i))
    assert len(tickets.list_tickets(db, viewer="alice", admin=False, limit=3)) == 3
    assert len(tickets.list_tickets(db, viewer="alice", admin=False, limit=0)) == 1


def test_only_the_known_origins_and_statuses_are_stored(db: Database, settings: Settings) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        new(db, settings, origin="surprise")
