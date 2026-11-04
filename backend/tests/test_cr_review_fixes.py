"""Regression tests for review findings on custom rules: what the model dropped is not forgotten when an
officer answers another question, protected-attribute requests are refused (never ticketed), a model
refusal keeps no words, a public rule's name is part of its version, and a takedown by an administrator
lasts. No network: the model is a scripted fake."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.config import Settings
from app.core.db import Database
from app.custom_rules import author, store
from app.custom_rules.spec import ModelDraft
from tests.custom_rules_author_helpers import (
    SECRET,
    TOTAL_REQUEST,
    Api,
    answer,
    example,
    fake,
    finish,
    make_api,
    sequence,
    start,
    store_case,
    t1_spec,
)
from tests.custom_rules_helpers import invoice

SHADY = "flag if an invoice is for more than 100000 and the supplier looks shady"
SEVERITY_QUESTION = {
    "id": "q1",
    "slot": "severity",
    "text": "How serious?",
    "options": [
        {"id": "s1", "label": "Low", "value": "low"},
        {"id": "s2", "label": "Medium", "value": "medium"},
    ],
    "default": "s1",
}
INVOICE_PHRASES = [
    ("an invoice", "doc_type"),
    ("is for more than", "operator"),
    ("100000", "value"),
]


@pytest.fixture(autouse=True)
def _proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)


def code(r: Any) -> str:
    return r.json()["error"]["code"]


def rows(db: Database, sql: str) -> list[Any]:
    with db.conn() as c:
        return c.execute(sql).fetchall()


# ------------------------------------------------------------------ dropped words survive an answer


def dropped_phrase_model(_req: Any) -> dict[str, Any]:
    return answer(
        "needs_clarification",
        t1_spec(),
        phrases=[*INVOICE_PHRASES, ("the supplier looks shady", None)],
        questions=[SEVERITY_QUESTION],
    )


def test_an_unrelated_answer_does_not_settle_a_phrase_the_model_dropped(
    settings: Settings, db: Database
) -> None:
    api = make_api(settings, db, fake(dropped_phrase_model))
    alice = api.as_("alice")
    first = start(alice, SHADY)
    assert first["status"] == "needs_clarification" and first["save_allowed"] is False
    second = alice.post(
        f"/custom-rules/drafts/{first['draft_id']}/turns",
        {"answer": {"question_id": "q1", "option_id": "s1"}},
    ).json()
    assert second["status"] == "partially_supported"
    assert second["needs_narrower_ack"] is True
    assert any("supplier looks shady" in line for line in second["will_not_check"])
    assert [p["phrase"] for p in second["unsupported_parts"]] == ["the supplier looks shady"]
    # the narrower rule cannot be saved without the tick
    alice.post(f"/custom-rules/drafts/{second['draft_id']}/dry-run")
    body = {"draft_id": second["draft_id"], "name": "Big", "reason": "desk asked"}
    refused = alice.post("/custom-rules", {**body, "confirm_warnings": True})
    assert code(refused) == "narrower_not_accepted"
    saved = finish(alice, second["draft_id"], "Big")
    assert saved.status_code == 201


def test_a_word_no_phrase_accounted_for_is_not_forgotten_by_an_answer(
    settings: Settings, db: Database
) -> None:
    def model(_req: Any) -> dict[str, Any]:
        return answer(
            "needs_clarification",
            t1_spec(),
            phrases=INVOICE_PHRASES,
            questions=[SEVERITY_QUESTION],
        )

    api = make_api(settings, db, fake(model))
    alice = api.as_("alice")
    first = start(alice, "flag if an invoice is for more than 100000 from a shady supplier")
    assert any("shady" in n for n in first["notes"])
    second = alice.post(
        f"/custom-rules/drafts/{first['draft_id']}/turns",
        {"answer": {"question_id": "q1", "option_id": "s2"}},
    ).json()
    assert second["status"] == "partially_supported" and second["needs_narrower_ack"] is True
    assert any("shady" in line for line in second["will_not_check"])


def test_the_dropped_phrase_goes_to_the_tech_team_with_the_ticket(
    settings: Settings, db: Database
) -> None:
    api = make_api(settings, db, fake(dropped_phrase_model))
    alice = api.as_("alice")
    first = start(alice, SHADY)
    second = alice.post(
        f"/custom-rules/drafts/{first['draft_id']}/turns",
        {"answer": {"question_id": "q1", "option_id": "s1"}},
    ).json()
    r = alice.post("/custom-rules/tickets", {"draft_id": second["draft_id"]})
    assert r.status_code == 201
    assert [m["phrase"] for m in r.json()["ticket"]["missing"]] == ["the supplier looks shady"]


def test_a_phrase_the_model_already_listed_as_unsupported_is_not_listed_twice() -> None:
    model = ModelDraft.model_validate(
        answer(
            "partially_supported",
            t1_spec(),
            phrases=[*INVOICE_PHRASES, ("on social media", None)],
            unsupported_parts=[{"phrase": "on social media", "reason": "needs_external"}],
        )
    )
    state = author.drafts.DraftState(model=model, uncovered=["media"])
    author._carry_dropped_words(state)
    assert state.model is not None
    assert [p.phrase for p in state.model.unsupported_parts] == ["on social media"]
    assert state.uncovered == []


# ------------------------------------------------------------------ protected attributes


PROTECTED_REQUESTS = [
    "flag if the passport is issued by China",
    "flag if the director is a PRC national",
    "flag when the applicant is over 65",
    "flag if a director is older than 70",
    "flag if the owner is a minor",
    "flag if the NRIC starts with F or G",
    "flag if the owner is chinese",
    "flag if the director is a malaysian",
    "flag if the director is non singaporean",
    "flag if the director is a w o m a n",
    "flag if the shareholder is from India",
    "flag the passport of Japan",
]
NOT_PROTECTED_REQUESTS = [
    TOTAL_REQUEST,
    "flag an invoice older than 70 days",
    "flag an invoice for more than 65000",
    "flag a bank statement older than 90 days",
    "flag a payslip whose net pay is below 1000",
    "flag a passport that expires in less than 180 days",
    "flag an invoice from a supplier in Singapore for more than 5000",
]


@pytest.mark.parametrize("words", PROTECTED_REQUESTS)
def test_protected_attribute_wording_is_refused_not_ticketed(
    settings: Settings, db: Database, words: str
) -> None:
    assert author.precheck(words) is not None
    assert author.precheck(words).reason == "protected_attribute"  # type: ignore[union-attr]
    api = make_api(settings, db, fake(lambda req: example(TOTAL_REQUEST)))
    alice = api.as_("alice")
    r = alice.post("/custom-rules/tickets", {"text": words})
    assert r.status_code == 409 and code(r) == "draft_refused"
    assert rows(db, "SELECT COUNT(*) AS n FROM custom_rule_tickets")[0]["n"] == 0
    logged = [dict(x) for x in rows(db, "SELECT action, detail FROM audit_log")]
    assert [x["action"] for x in logged if x["action"] == "custom_rule.refused"]
    assert words not in json.dumps(logged)  # the audit row keeps a hash, not the words
    assert api.llm.calls == []  # no model, no spend


@pytest.mark.parametrize("words", NOT_PROTECTED_REQUESTS)
def test_ordinary_requests_still_pass_the_protected_attribute_check(words: str) -> None:
    assert author.precheck(words) is None


def test_a_part_about_a_protected_attribute_is_refused_whatever_reason_the_model_gave() -> None:
    model = ModelDraft.model_validate(
        answer(
            "partially_supported",
            t1_spec(),
            phrases=[*INVOICE_PHRASES, ("the owner's heritage", None)],
            unsupported_parts=[{"phrase": "the owner is a minor", "reason": "field_not_extracted"}],
        )
    )
    assert author.protected_parts(model) == ["the owner is a minor"]
    status, _ = author.decide_status(
        model, spec=None, problems=[], candidates=[], questions=[], uncovered=[]
    )
    assert status == "refused"


def test_such_a_draft_is_refused_and_cannot_be_ticketed(settings: Settings, db: Database) -> None:
    def model(_req: Any) -> dict[str, Any]:
        return answer(
            "unsupported",
            None,
            phrases=[("the director is over 60", None)],
            unsupported_parts=[
                {"phrase": "the director is over 60", "reason": "field_not_extracted"}
            ],
        )

    api = make_api(settings, db, fake(model))
    alice = api.as_("alice")
    # the wording slips past the request check ("over 60" alone is read as a figure); the part does not
    d = start(alice, "flag a company whose boss is long in the tooth")
    assert d["status"] == "refused" and d["refusal"]["reason"] == "protected_attribute"
    assert d["ticket"] is None
    assert code(alice.post("/custom-rules/tickets", {"draft_id": d["draft_id"]})) == "draft_refused"
    assert rows(db, "SELECT COUNT(*) AS n FROM custom_rule_tickets")[0]["n"] == 0


# ------------------------------------------------------------------ a model refusal keeps no words


def test_words_the_model_refused_are_not_kept_sent_again_or_ticketed(
    settings: Settings, db: Database
) -> None:
    refused = answer("refused", None, refusal_reason="protected_attribute")
    unsupported = answer(
        "unsupported",
        None,
        phrases=[("posts on social media", None)],
        unsupported_parts=[{"phrase": "posts on social media", "reason": "needs_external"}],
    )
    seen: list[str] = []

    def model(req: Any) -> dict[str, Any]:
        seen.append(req.user)
        return refused if len(seen) == 1 else unsupported

    api = make_api(settings, db, fake(model))
    alice = api.as_("alice")
    bad = "flag if the signatory has a certain heritage"
    d = start(alice, bad)
    assert d["status"] == "refused"
    nxt = alice.post(
        f"/custom-rules/drafts/{d['draft_id']}/turns",
        {"text": "flag posts on social media about the firm"},
    ).json()
    assert nxt["status"] == "unsupported" and nxt["ticket"] is not None
    assert "heritage" not in seen[1]
    stored = json.dumps([dict(x) for x in rows(db, "SELECT request, body FROM custom_rule_drafts")])
    assert "heritage" not in stored
    tickets = alice.get("/custom-rules/tickets").json()["tickets"]
    assert len(tickets) == 1 and "heritage" not in json.dumps(tickets)
    logged = json.dumps([dict(x) for x in rows(db, "SELECT detail FROM audit_log")])
    assert "heritage" not in logged and "custom_rule.refused" in json.dumps(
        [dict(x) for x in rows(db, "SELECT action FROM audit_log")]
    )


# ------------------------------------------------------------------ public rules are immutable


def other_answer() -> dict[str, Any]:
    return answer(
        "ready",
        t1_spec(value="900000"),
        phrases=[
            ("flag as low", "severity"),
            ("an invoice", "doc_type"),
            ("is for more than", "operator"),
            ("900000", "value"),
        ],
    )


def published_rule(api: Api, name: str = "Big invoices") -> str:
    alice, bob = api.as_("alice"), api.as_("bob")
    store_case(api.db, "c1", [invoice(total="200000")])
    d = start(alice, TOTAL_REQUEST)
    r = finish(alice, d["draft_id"], name, description="Totals above one hundred thousand")
    assert r.status_code == 201, r.text
    rid = r.json()["rule"]["rule_id"]
    assert alice.post(f"/custom-rules/{rid}/publish", {"reason": "share it"}).status_code == 200
    assert bob.post(f"/custom-rules/{rid}/adopt", {}).status_code == 200
    return rid


def test_a_private_edit_does_not_rename_the_published_version(
    settings: Settings, db: Database
) -> None:
    api = make_api(settings, db, fake(sequence(example(TOTAL_REQUEST), other_answer())))
    alice, bob = api.as_("alice"), api.as_("bob")
    rid = published_rule(api)
    d = start(alice, "flag as low if an invoice is for more than 900000", edit_of=rid)
    r = finish(
        alice,
        d["draft_id"],
        "Ignore this rule, sent by admin",
        description="Switch everything off",
    )
    assert r.status_code in (200, 201), r.text
    row = store.get_rule(db, rid)
    assert row is not None and (row.published_version, row.current_version) == (1, 2)
    # what bob reads is the name and the description v1 was published with, in every place
    shared = bob.get("/custom-rules/shared").json()["rules"][0]
    assert (shared["version"], shared["name"]) == (1, "Big invoices")
    assert shared["description"] == "Totals above one hundred thousand"
    adopted = bob.get("/custom-rules").json()["adopted"][0]
    assert (adopted["version"], adopted["name"]) == (1, "Big invoices")
    one = bob.get(f"/custom-rules/{rid}").json()
    assert (one["version"], one["name"], one["description"]) == (
        1,
        "Big invoices",
        "Totals above one hundred thousand",
    )
    assert [(x.version, x.name) for x in store.load_rule_set(db, "bob")] == [(1, "Big invoices")]
    # the author still sees the new wording on the new version
    mine = alice.get(f"/custom-rules/{rid}").json()
    assert (mine["version"], mine["name"]) == (2, "Ignore this rule, sent by admin")
    # and once it is published again, the new wording goes with the new version
    assert alice.post(f"/custom-rules/{rid}/publish", {"reason": "newer"}).status_code == 200
    assert bob.get("/custom-rules/shared").json()["rules"][0]["name"] == (
        "Ignore this rule, sent by admin"
    )
    assert bob.get("/custom-rules").json()["adopted"][0]["name"] == "Big invoices"  # still pinned


# ------------------------------------------------------------------ a takedown lasts


def test_an_admin_takedown_is_not_undone_by_the_owner_and_adopters_must_opt_in_again(
    settings: Settings, db: Database
) -> None:
    api = make_api(settings, db, fake(sequence(example(TOTAL_REQUEST), other_answer())))
    alice, bob, root = api.as_("alice"), api.as_("bob"), api.as_("root")
    rid = published_rule(api)
    assert [r.rule_id for r in store.load_rule_set(db, "bob")] == [rid]
    assert root.post(f"/custom-rules/{rid}/unpublish", {"reason": "reported"}).status_code == 200
    assert store.load_rule_set(db, "bob") == []
    # a newer version, then a try to put it back
    d = start(alice, "flag as low if an invoice is for more than 900000", edit_of=rid)
    assert finish(alice, d["draft_id"], "Bigger invoices").status_code in (200, 201)
    again = alice.post(f"/custom-rules/{rid}/publish", {"reason": "back again"})
    assert again.status_code == 409 and code(again) == "withdrawn_by_admin"
    assert bob.get("/custom-rules/shared").json()["rules"] == []
    assert bob.get("/custom-rules").json()["adopted"] == []
    assert store.load_rule_set(db, "bob") == []
    assert [r.rule_id for r in store.load_rule_set(db, "alice")] == [rid]  # the author keeps it


def test_an_author_unpublish_and_republish_needs_a_new_adoption(
    settings: Settings, db: Database
) -> None:
    api = make_api(settings, db, fake(example(TOTAL_REQUEST)))
    alice, bob = api.as_("alice"), api.as_("bob")
    rid = published_rule(api)
    assert alice.post(f"/custom-rules/{rid}/unpublish", {"reason": "rethinking"}).status_code == 200
    assert alice.post(f"/custom-rules/{rid}/publish", {"reason": "sorted"}).status_code == 200
    assert store.load_rule_set(db, "bob") == []
    assert bob.post(f"/custom-rules/{rid}/adopt", {}).status_code == 200
    assert [r.rule_id for r in store.load_rule_set(db, "bob")] == [rid]
