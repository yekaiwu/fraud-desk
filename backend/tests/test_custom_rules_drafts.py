"""The stored authoring conversation (app/custom_rules/drafts.py)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.db import Database
from app.core.errors import ApiError
from app.core.testing import FIXED_NOW
from app.custom_rules import drafts
from app.custom_rules.spec import ModelDraft, Refusal
from tests.custom_rules_author_helpers import debt_answer


def make(db: Database, owner: str = "alice", **state: object) -> drafts.DraftRecord:
    return drafts.create_draft(
        db,
        owner=owner,
        request="flag big invoices",
        edit_of=None,
        state=drafts.DraftState(**state),  # type: ignore[arg-type]
        prompt_sha256="a" * 64,
        now=FIXED_NOW,
    )


def test_ids_look_like_crd_and_twelve_hex() -> None:
    import re

    assert re.fullmatch(r"crd_[0-9a-f]{12}", drafts.new_draft_id())
    assert drafts.new_draft_id() != drafts.new_draft_id()


def test_a_new_draft_is_open_at_turn_one(db: Database) -> None:
    rec = make(db)
    assert (rec.state, rec.turns, rec.owner, rec.model_calls, rec.micro_usd) == (
        "open",
        1,
        "alice",
        0,
        0,
    )
    assert rec.prompt_sha256 == "a" * 64 and rec.request == "flag big invoices"
    again = drafts.load_draft(db, rec.id)
    assert again is not None and again.body == rec.body and again.created_at == rec.created_at


def test_an_unknown_draft_is_none(db: Database) -> None:
    assert drafts.load_draft(db, "crd_000000000000") is None


def test_the_whole_state_survives_storage(db: Database) -> None:
    model = ModelDraft.model_validate(debt_answer())
    state = drafts.DraftState(
        model=model,
        model_status="partially_supported",
        status="partially_supported",
        spec=debt_answer()["spec"],
        questions=list(model.questions),
        refusal=Refusal(reason="nonsense", message="x"),
        desired_severity="medium",
        history=[{"turn": 1, "kind": "text", "text": "hello", "status": "ready"}],
        last_calls=2,
        weaker=debt_answer()["spec"],
    )
    rec = make(db, **state.model_dump())
    loaded = drafts.load_draft(db, rec.id)
    assert loaded is not None
    assert loaded.body.model == model
    assert loaded.body.questions[0].options[1].value == "2"
    assert loaded.body.history[0]["text"] == "hello"
    assert loaded.body.refusal is not None and loaded.body.refusal.reason == "nonsense"
    assert loaded.body.weaker == debt_answer()["spec"]


def test_an_update_moves_the_turn_and_the_body(db: Database) -> None:
    rec = make(db)
    rec.turns = 2
    rec.body.status = "ready"
    saved = drafts.update_draft(db, rec, expected_turns=1, now=FIXED_NOW + timedelta(minutes=1))
    assert saved.turns == 2 and saved.body.status == "ready"
    assert saved.updated_at > saved.created_at


def test_two_turns_at_once_the_second_loses(db: Database) -> None:
    first = make(db)
    second = drafts.load_draft(db, first.id)
    assert second is not None
    first.turns = 2
    drafts.update_draft(db, first, expected_turns=1, now=FIXED_NOW)
    second.turns = 2
    with pytest.raises(ApiError) as err:
        drafts.update_draft(db, second, expected_turns=1, now=FIXED_NOW)
    assert (err.value.status_code, err.value.code) == (409, "draft_busy")
    loaded = drafts.load_draft(db, first.id)
    assert loaded is not None and loaded.turns == 2


def test_a_closed_draft_cannot_be_updated(db: Database) -> None:
    rec = make(db)
    rec.state, rec.saved_rule_id = "saved", "cus_0123456789"
    closed = drafts.update_draft(db, rec, expected_turns=1, now=FIXED_NOW)
    assert closed.state == "saved" and closed.saved_rule_id == "cus_0123456789"
    closed.turns = 2
    with pytest.raises(ApiError) as err:
        drafts.update_draft(db, closed, expected_turns=1, now=FIXED_NOW)
    assert err.value.code == "draft_busy"


def test_spend_adds_up_even_for_a_failed_turn(db: Database) -> None:
    rec = make(db)
    drafts.add_spend(db, rec.id, calls=1, micro_usd=1200)
    drafts.add_spend(db, rec.id, calls=2, micro_usd=800)
    drafts.add_spend(db, rec.id, calls=0, micro_usd=0)
    loaded = drafts.load_draft(db, rec.id)
    assert loaded is not None and (loaded.model_calls, loaded.micro_usd) == (3, 2000)


def test_a_draft_belongs_to_one_owner(db: Database) -> None:
    mine, theirs = make(db, "alice"), make(db, "bob")
    assert drafts.load_draft(db, mine.id).owner == "alice"  # type: ignore[union-attr]
    assert drafts.load_draft(db, theirs.id).owner == "bob"  # type: ignore[union-attr]
