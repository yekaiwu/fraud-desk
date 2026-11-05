"""Custom rules over HTTP (api.md section 9c): the gate, privacy between accounts, the authoring
conversation, saving, publishing and adopting, tickets and the budget. Offline: a fake model answers."""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.core.config import Settings
from app.core.costs import LLMBudgetExceeded
from app.core.db import Database
from app.core.errors import LLMNoFixture
from app.core.llm import LLMRequest
from app.custom_rules import store
from tests.custom_rules_author_helpers import (
    DEBT_REQUEST,
    SECRET,
    TOTAL_REQUEST,
    Api,
    As,
    answer,
    debt_answer,
    example,
    fake,
    finish,
    make_api,
    sequence,
    start,
    store_case,
    t1_spec,
    t3_spec,
)
from tests.custom_rules_helpers import invoice

SOCIAL = "flag if the company posts about money problems on social media"
NATIONALITY = "flag as medium when the director's nationality is not Singapore"
PASSPORT = "flag if the passport expires before the case date"
RECEIPT = "flag a receipt when the merchant name is missing"

PASSPORT_ANSWER = answer(
    "ready",
    {
        "spec_version": 1,
        "severity": "low",
        "condition": {
            "template": "date_window",
            "field": "passport.date_of_expiry",
            "direction": "older_than",
            "days": 0,
        },
    },
    phrases=[
        ("flag", "filler"),
        ("the passport", "doc_type"),
        ("expires", "field"),
        ("before the case date", "direction"),
    ],
)
RECEIPT_ANSWER = answer(
    "ready",
    t3_spec("receipt.merchant_name"),
    phrases=[("a receipt", "doc_type"), ("merchant name", "field"), ("is missing", "operator")],
)
ANSWERS: dict[str, dict[str, Any]] = {
    DEBT_REQUEST: debt_answer(),
    TOTAL_REQUEST: example(TOTAL_REQUEST),
    SOCIAL: example(SOCIAL),
    NATIONALITY: example(NATIONALITY),
    PASSPORT: PASSPORT_ANSWER,
    RECEIPT: RECEIPT_ANSWER,
}


def by_request(req: LLMRequest) -> dict[str, Any]:
    """The fake model answers the request it finds in the officer's wrapped words."""
    for text, ans in ANSWERS.items():
        if f"\n{text}\n" in req.user:
            return ans
    raise LLMNoFixture(req.purpose)


@pytest.fixture(autouse=True)
def _proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLDB_PROXY_SECRET", SECRET)


@pytest.fixture
def api(settings: Settings, db: Database) -> Api:
    return make_api(settings, db, fake(by_request))


@pytest.fixture
def alice(api: Api) -> As:
    return api.as_("alice")


@pytest.fixture
def bob(api: Api) -> As:
    return api.as_("bob")


@pytest.fixture
def root(api: Api) -> As:
    return api.as_("root")  # an administrator (GLDB_ADMIN_USERS=root)


def code(response: Any) -> str:
    return response.json()["error"]["code"]


def mk_rule(who: As, text: str = TOTAL_REQUEST, name: str = "Big invoices") -> dict[str, Any]:
    draft = start(who, text)
    r = finish(who, draft["draft_id"], name)
    assert r.status_code == 201, r.text
    return r.json()["rule"]


ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/custom-rules/capabilities", None),
    ("POST", "/custom-rules/drafts", {"text": TOTAL_REQUEST}),
    ("GET", "/custom-rules/drafts/crd_0123456789ab", None),
    ("POST", "/custom-rules/drafts/crd_0123456789ab/turns", {"text": "hello again"}),
    ("POST", "/custom-rules/drafts/crd_0123456789ab/dry-run", {}),
    ("POST", "/custom-rules", {"draft_id": "crd_0123456789ab"}),
    ("GET", "/custom-rules", None),
    ("GET", "/custom-rules/shared", None),
    ("GET", "/custom-rules/changes", None),
    ("GET", "/custom-rules/tickets", None),
    ("POST", "/custom-rules/tickets", {"text": "flag something"}),
    ("GET", "/custom-rules/cus_0123456789", None),
    ("POST", "/custom-rules/cus_0123456789/enabled", {"enabled": False, "reason": "testing it"}),
    ("POST", "/custom-rules/cus_0123456789/publish", {"reason": "share it"}),
    ("POST", "/custom-rules/cus_0123456789/unpublish", {"reason": "take back"}),
    ("POST", "/custom-rules/cus_0123456789/adopt", {}),
    ("POST", "/custom-rules/cus_0123456789/unadopt", {}),
    ("POST", "/custom-rules/cus_0123456789/retire", {"reason": "not needed"}),
]


# ================================================================================== the gate


def test_the_route_list_here_is_the_whole_surface(api: Api) -> None:
    schema = api.app.openapi()
    registered = {
        (method.upper(), re.sub(r"\{[^}]+\}", "{}", path))
        for path, item in schema["paths"].items()
        if path.startswith("/custom-rules")
        for method in item
    }
    listed = {
        (method, path.replace("crd_0123456789ab", "{}").replace("cus_0123456789", "{}"))
        for method, path, _ in ROUTES
    }
    assert len(ROUTES) == 18 and registered == listed


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_says_feature_off_when_it_is_switched_off(
    settings: Settings, db: Database, method: str, path: str, body: Any
) -> None:
    off = make_api(settings, db, fake(by_request), custom_rules="off")
    r = off.as_("alice").request(method, path, json=body)
    assert r.status_code == 404 and code(r) == "feature_off"
    assert "GLDB_CUSTOM_RULES" in r.json()["error"]["message"]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_needs_a_personal_sign_in(api: Api, method: str, path: str, body: Any) -> None:
    r = api.as_(None).request(method, path, json=body)
    assert r.status_code == 403 and code(r) == "account_required"
    assert "personal sign-in" in r.json()["error"]["message"]


def test_without_the_proxy_secret_the_account_header_is_ignored(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GLDB_PROXY_SECRET", raising=False)
    open_mode = make_api(settings, db, fake(by_request))
    r = open_mode.client.get("/custom-rules/capabilities", headers={"X-Gldb-Reviewer": "alice"})
    assert r.status_code == 403 and code(r) == "account_required"
    assert (
        open_mode.client.get("/me", headers={"X-Gldb-Reviewer": "alice"}).json()["custom_rules"]
        is False
    )


def test_me_reports_whether_custom_rules_are_available(
    settings: Settings, db: Database, api: Api
) -> None:
    assert api.as_("alice").get("/me").json() == {
        "reviewer": "alice",
        "admin": False,
        "custom_rules": True,
    }
    assert api.as_("root").get("/me").json() == {
        "reviewer": "root",
        "admin": True,
        "custom_rules": True,
    }
    assert api.as_(None).get("/me").json() == {
        "reviewer": None,
        "admin": False,
        "custom_rules": False,
    }
    off = make_api(settings, db, custom_rules="off")
    assert off.as_("alice").get("/me").json()["custom_rules"] is False


def test_a_malformed_id_is_a_not_found(alice: As) -> None:
    for path in ("/custom-rules/not-a-rule", "/custom-rules/cus_XYZ", "/custom-rules/drafts/nope"):
        r = alice.get(path)
        assert r.status_code == 404 and code(r) == "not_found", path
    bodies: dict[str, dict[str, Any]] = {
        "enabled": {"enabled": True, "reason": "because"},
        "publish": {"reason": "because"},
        "unpublish": {"reason": "because"},
        "adopt": {},
        "unadopt": {},
        "retire": {"reason": "because"},
    }
    for suffix, body in bodies.items():
        assert alice.post(f"/custom-rules/nope/{suffix}", body).status_code == 404, suffix
    assert alice.get("/custom-rules/changes?rule_id=nope").status_code == 404


# ========================================================================= what is possible


def test_capabilities(alice: As) -> None:
    r = alice.get("/custom-rules/capabilities")
    assert r.status_code == 200
    cat = r.json()
    assert [t["id"] for t in cat["templates"]] == [
        "field_vs_constant",
        "date_window",
        "field_missing",
        "fs_ratio",
    ]
    assert cat["severities"] == ["info", "low", "medium"]
    assert cat["limits"]["max_turns"] == 6 and cat["limits"]["max_rules"] == 50
    assert {i["reason"] for i in cat["impossible"]} >= {
        "field_not_extracted",
        "needs_cross_case",
        "policy_forbidden",
    }
    fields = {f["path"] for d in cat["documents"] for f in d["fields"]}
    assert "invoice.total" in fields and "passport.nationality" not in fields


# ==================================================================== the assistant over HTTP


def test_the_worked_example_over_http(api: Api, alice: As) -> None:
    d = start(alice)
    assert d["status"] == "partially_supported" and d["save_allowed"] is False
    assert (
        d["will_not_check"][0]
        == '"from different places": the system does not read this from the documents'
    )
    assert d["ticket"] is None and d["needs_narrower_ack"] is True and d["model_calls"] == 1
    assert d["questions"][0]["default"] == "x2" and d["draft_id"].startswith("crd_")
    got = alice.get(f"/custom-rules/drafts/{d['draft_id']}")
    assert got.status_code == 200 and got.json() == {**d, "model_calls": d["model_calls"]}
    calls = len(api.llm.calls)
    r = alice.post(
        f"/custom-rules/drafts/{d['draft_id']}/turns",
        {"answer": {"question_id": "q1", "option_id": "x2"}},
    )
    assert r.status_code == 200
    d2 = r.json()
    assert (d2["turn"], d2["questions"], d2["save_allowed"], d2["model_calls"]) == (2, [], True, 0)
    assert len(api.llm.calls) == calls
    dry = alice.post(f"/custom-rules/drafts/{d['draft_id']}/dry-run")
    assert dry.status_code == 200 and dry.json()["warnings"][0]["code"] == "no_cases"
    assert alice.get(f"/custom-rules/drafts/{d['draft_id']}").json()["dry_run"] == dry.json()
    body = {
        "draft_id": d["draft_id"],
        "name": "Heavy borrowing",
        "reason": "Loan desk asked for it",
    }
    assert code(alice.post("/custom-rules", body)) == "narrower_not_accepted"
    body["accept_narrower"] = True
    assert code(alice.post("/custom-rules", body)) == "warnings_not_confirmed"
    body["confirm_warnings"] = True
    body["submit_ticket"] = True
    r = alice.post("/custom-rules", body)
    assert r.status_code == 201, r.text
    saved = r.json()
    rule = saved["rule"]
    assert rule["rule_id"].startswith("cus_") and rule["owner"] == "alice" and rule["own"] is True
    assert (rule["name"], rule["version"], rule["severity"], rule["template"]) == (
        "Heavy borrowing",
        1,
        "medium",
        "fs_ratio",
    )
    assert (
        rule["visibility"] == "private" and rule["enabled"] is True and rule["status"] == "active"
    )
    assert (
        rule["label"] == "custom, unreviewed"
        and rule["adoptions"] == 0
        and rule["published_version"] is None
    )
    assert rule["readback"] == d["readback"] and rule["spec_hash"] == d["spec_hash"]
    ticket = saved["ticket"]
    assert (
        ticket["status"] == "submitted"
        and ticket["message"] == f"This will be submitted to the tech team (ticket {ticket['id']})."
    )
    tickets = alice.get("/custom-rules/tickets").json()["tickets"]
    assert [t["id"] for t in tickets] == [ticket["id"]]
    assert tickets[0]["origin"] == "partial" and tickets[0]["weaker_rule_id"] == rule["rule_id"]
    assert tickets[0]["missing"] == [
        {"phrase": "from different places", "reason": "field_not_extracted"}
    ]
    listed = alice.get("/custom-rules").json()
    assert [r["rule_id"] for r in listed["mine"]] == [rule["rule_id"]] and listed["adopted"] == []
    assert (
        code(alice.post(f"/custom-rules/drafts/{d['draft_id']}/turns", {"text": "more"}))
        == "draft_closed"
    )
    assert code(alice.post("/custom-rules", body)) == "draft_closed"


def test_a_saved_rule_runs_for_its_owner_only(api: Api, alice: As, bob: As) -> None:
    rule = mk_rule(alice)
    assert [r.rule_id for r in store.load_rule_set(api.db, "alice")] == [rule["rule_id"]]
    assert store.load_rule_set(api.db, "bob") == []


def test_unsupported_is_ticketed_and_listed(alice: As) -> None:
    d = start(alice, SOCIAL)
    assert d["status"] == "unsupported" and d["spec"] is None and d["save_allowed"] is False
    t = d["ticket"]
    assert t["message"] == f"This will be submitted to the tech team (ticket {t['id']})."
    assert [x["id"] for x in alice.get("/custom-rules/tickets").json()["tickets"]] == [t["id"]]
    r = alice.post("/custom-rules/tickets", {"draft_id": d["draft_id"]})
    assert (
        r.status_code == 200
        and r.json()["existing"] is True
        and r.json()["ticket"]["id"] == t["id"]
    )
    assert r.json()["message"] == t["message"]


def test_a_refusal_is_not_ticketed_and_is_logged(api: Api, alice: As) -> None:
    d = start(alice, NATIONALITY)
    assert d["status"] == "refused" and d["refusal"]["reason"] == "protected_attribute"
    assert (
        d["ticket"] is None and d["spec"] is None and d["model_calls"] == 0 and api.llm.calls == []
    )
    r = alice.post("/custom-rules/tickets", {"draft_id": d["draft_id"]})
    assert r.status_code == 409 and code(r) == "draft_refused"
    assert alice.post("/custom-rules/tickets", {"text": NATIONALITY}).status_code == 409
    assert alice.get("/custom-rules/tickets").json() == {"tickets": []}
    with api.db.conn() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM audit_log WHERE action = 'custom_rule.refused'"
            ).fetchone()[0]
            == 2  # the draft, and the attempt to send the words straight to the tech team
        )


def test_high_severity_and_hard_stop_are_refused_with_the_reason(alice: As) -> None:
    assert (
        start(alice, "flag as high if an invoice total is over 100000")["refusal"]["reason"]
        == "high_severity"
    )
    assert start(alice, "hard stop when the receipt is missing")["refusal"]["reason"] == "hard_stop"


def test_a_ticket_from_words_needs_no_model(api: Api, alice: As) -> None:
    r = alice.post(
        "/custom-rules/tickets", {"text": "  flag lenders on the statements  ", "severity": "low"}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["existing"] is False and body["message"].startswith(
        "This will be submitted to the tech team (ticket crt_"
    )
    t = body["ticket"]
    assert t["origin"] == "officer" and t["desired_severity"] == "low" and t["draft_id"] is None
    assert t["original_words"] == "flag lenders on the statements" and t["account"] == "alice"
    assert api.llm.calls == []


def test_turn_bodies_are_checked(alice: As) -> None:
    d = start(alice, TOTAL_REQUEST)
    path = f"/custom-rules/drafts/{d['draft_id']}/turns"
    for body in (
        {},
        {"text": "a longer message", "choose_candidate": 0},
        {"text": "x", "extra": 1},
        {"answer": {"question_id": "q1"}},
    ):
        r = alice.post(path, body)
        assert r.status_code == 422 and code(r) == "validation_error", body
    assert alice.post(path, {"text": "ab"}).status_code == 422
    assert alice.post(path, {"text": "z" * 1001}).status_code == 422
    assert (
        alice.post("/custom-rules/drafts", {"text": TOTAL_REQUEST, "severity": "high"}).status_code
        == 422
    )
    assert (
        alice.post("/custom-rules/drafts", {"text": TOTAL_REQUEST, "extra": 1}).status_code == 422
    )
    assert alice.post("/custom-rules/drafts", {"text": ""}).status_code == 422
    assert alice.post("/custom-rules/drafts", {"text": "z" * 5001}).status_code == 422
    assert alice.post("/custom-rules/tickets", {"text": "z" * 5001}).status_code == 422
    assert (
        alice.post("/custom-rules", {"draft_id": d["draft_id"], "reason": "r" * 5001}).status_code
        == 422
    )
    assert alice.post("/custom-rules/tickets", {}).status_code == 422
    assert (
        alice.post(
            "/custom-rules/tickets", {"text": "flag it please", "draft_id": d["draft_id"]}
        ).status_code
        == 422
    )
    assert alice.get("/custom-rules/changes?limit=0").status_code == 422
    assert alice.get("/custom-rules/changes?limit=201").status_code == 422


def test_a_draft_has_six_turns_over_http(alice: As) -> None:
    d = start(alice, TOTAL_REQUEST)
    path = f"/custom-rules/drafts/{d['draft_id']}/turns"
    for n in range(2, 7):
        assert alice.post(path, {"text": TOTAL_REQUEST}).json()["turn"] == n
    r = alice.post(path, {"text": TOTAL_REQUEST})
    assert r.status_code == 409 and code(r) == "draft_turn_limit"


# =========================================================================== privacy


def test_a_private_rule_draft_and_ticket_are_invisible_to_others(
    api: Api, alice: As, bob: As, root: As
) -> None:
    d = start(alice, SOCIAL)
    draft = start(alice, TOTAL_REQUEST)
    rule = mk_rule(alice)
    rid = rule["rule_id"]
    for who in (bob, root):
        assert who.get(f"/custom-rules/{rid}").status_code == 404
        for suffix, body in [
            ("enabled", {"enabled": False, "reason": "testing it"}),
            ("publish", {"reason": "share it"}),
            ("unpublish", {"reason": "take back"}),
            ("retire", {"reason": "not needed"}),
            ("adopt", {}),
            ("unadopt", {}),
        ]:
            r = who.post(f"/custom-rules/{rid}/{suffix}", body)
            assert r.status_code == 404 and code(r) == "not_found", (suffix, r.text)
        assert who.get(f"/custom-rules/drafts/{d['draft_id']}").status_code == 404
        assert (
            who.post(
                f"/custom-rules/drafts/{draft['draft_id']}/turns", {"text": "more words"}
            ).status_code
            == 404
        )
        assert who.post(f"/custom-rules/drafts/{draft['draft_id']}/dry-run").status_code == 404
        assert who.post("/custom-rules/tickets", {"draft_id": draft["draft_id"]}).status_code == 404
        assert (
            who.post(
                "/custom-rules",
                {"draft_id": draft["draft_id"], "name": "Mine now", "reason": "because"},
            ).status_code
            == 404
        )
        assert who.get("/custom-rules").json() == {"mine": [], "adopted": []}
        assert who.get("/custom-rules/shared").json() == {"rules": []}
        if who is bob:  # the change log of an administrator is the whole log (api.md 9c)
            assert who.get(f"/custom-rules/changes?rule_id={rid}").json() == {"changes": []}
    assert bob.get("/custom-rules/tickets").json() == {"tickets": []}
    assert store.load_rule_set(api.db, "bob") == [] and store.load_rule_set(api.db, "root") == []
    # the owner still sees everything
    assert alice.get(f"/custom-rules/{rid}").status_code == 200
    assert len(alice.get("/custom-rules/tickets").json()["tickets"]) == 1
    # an administrator sees every ticket (the tech team) but still no private rule
    assert [t["account"] for t in root.get("/custom-rules/tickets").json()["tickets"]] == ["alice"]


def test_a_404_does_not_reveal_whether_a_private_rule_exists(alice: As, bob: As) -> None:
    rid = mk_rule(alice)["rule_id"]
    real = bob.post(f"/custom-rules/{rid}/adopt")
    ghost = bob.post("/custom-rules/cus_ffffffffff/adopt")
    assert (real.status_code, real.json()["error"]["code"], real.json()["error"]["message"]) == (
        ghost.status_code,
        ghost.json()["error"]["code"],
        ghost.json()["error"]["message"],
    )
    assert real.json()["error"]["details"]["kind"] == "custom_rule"


def test_an_identical_idea_of_another_account_is_neither_blocked_nor_revealed(
    alice: As, bob: As
) -> None:
    mk_rule(alice)
    d = start(bob, TOTAL_REQUEST)
    assert d["duplicates"] == [] and d["save_allowed"] is True
    assert finish(bob, d["draft_id"], "My big invoices").status_code == 201


def test_the_change_log_shows_own_rules_and_own_adoptions_only(
    alice: As, bob: As, root: As
) -> None:
    rid = mk_rule(alice)["rule_id"]
    assert alice.post(f"/custom-rules/{rid}/publish", {"reason": "share it"}).status_code == 200
    assert bob.post(f"/custom-rules/{rid}/adopt").status_code == 200
    mine = alice.get("/custom-rules/changes").json()["changes"]
    assert [c["action"] for c in mine] == ["publish", "create"]  # alice does not see who adopted
    theirs = bob.get("/custom-rules/changes").json()["changes"]
    assert [(c["action"], c["actor"]) for c in theirs] == [("adopt", "bob")]
    everything = root.get("/custom-rules/changes").json()["changes"]
    assert [c["action"] for c in everything] == ["adopt", "publish", "create"]
    assert (
        alice.get(f"/custom-rules/changes?rule_id={rid}&limit=1").json()["changes"][0]["action"]
        == "publish"
    )


# ============================================================ publish, adopt, unpublish, retire


def test_publish_adopt_and_run(api: Api, alice: As, bob: As, root: As) -> None:
    rule = mk_rule(alice)
    rid = rule["rule_id"]
    assert code(bob.post(f"/custom-rules/{rid}/adopt")) == "not_found"  # private
    r = alice.post(f"/custom-rules/{rid}/publish", {})
    assert r.status_code == 422 and code(r) == "reason_required"
    r = alice.post(f"/custom-rules/{rid}/publish", {"reason": "Useful to the loan desk"})
    assert r.status_code == 200 and r.json()["rule"]["visibility"] == "public"
    assert r.json()["rule"]["published_version"] == 1
    assert (
        code(alice.post(f"/custom-rules/{rid}/publish", {"reason": "again please"}))
        == "already_public"
    )
    shared = bob.get("/custom-rules/shared").json()["rules"]
    assert len(shared) == 1
    s = shared[0]
    assert (
        s["rule_id"],
        s["author"],
        s["own"],
        s["adopted"],
        s["adopted_version"],
        s["adoptions"],
    ) == (rid, "alice", False, False, None, 0)
    assert s["label"] == "custom, unreviewed" and s["name"] == "Big invoices" and s["version"] == 1
    assert (
        s["readback"] == rule["readback"]
        and s["severity"] == "low"
        and s["template"] == "field_vs_constant"
    )
    assert alice.get("/custom-rules/shared").json()["rules"][0]["own"] is True
    view = bob.get(f"/custom-rules/{rid}").json()
    assert (
        view["own"] is False
        and view["owner"] == "alice"
        and "versions" not in view
        and view["version"] == 1
    )
    # others cannot change it
    for suffix, body in [
        ("enabled", {"enabled": False, "reason": "testing it"}),
        ("publish", {"reason": "again please"}),
        ("retire", {"reason": "not needed"}),
    ]:
        r = bob.post(f"/custom-rules/{rid}/{suffix}", body)
        assert r.status_code == 403 and code(r) == "forbidden", suffix
    assert code(bob.post(f"/custom-rules/{rid}/unpublish", {"reason": "take back"})) == "forbidden"
    # nothing runs for bob until he adopts
    assert store.load_rule_set(api.db, "bob") == []
    assert code(alice.post(f"/custom-rules/{rid}/adopt")) == "own_rule"
    r = bob.post(f"/custom-rules/{rid}/adopt")
    assert r.status_code == 200
    ad = r.json()["adoption"]
    assert (
        ad["rule_id"],
        ad["author"],
        ad["version"],
        ad["latest_version"],
        ad["update_available"],
        ad["active"],
    ) == (rid, "alice", 1, 1, False, True)
    assert ad["label"] == "custom, unreviewed" and ad["readback"] == rule["readback"]
    assert code(bob.post(f"/custom-rules/{rid}/adopt")) == "already_adopted"
    loaded = store.load_rule_set(api.db, "bob")
    assert [(r.rule_id, r.origin, r.author) for r in loaded] == [(rid, "adopted", "alice")]
    assert bob.get("/custom-rules").json()["adopted"][0]["rule_id"] == rid
    assert bob.get("/custom-rules/shared").json()["rules"][0]["adopted"] is True
    assert alice.get(f"/custom-rules/{rid}").json()["adoptions"] == 1
    # an administrator who is not the author can unpublish; adopters stop running it
    r = root.post(f"/custom-rules/{rid}/unpublish", {"reason": "reported by the desk"})
    assert r.status_code == 200 and r.json()["rule"]["visibility"] == "private"
    assert (
        "spec" not in r.json()["rule"]
    )  # the administrator learns the outcome, not the private rule
    assert store.load_rule_set(api.db, "bob") == []
    assert (
        bob.get("/custom-rules").json()["adopted"] == []
    )  # every adoption ended with the takedown
    assert bob.get("/custom-rules/shared").json()["rules"] == []
    assert bob.get(f"/custom-rules/{rid}").status_code == 404
    assert (
        code(root.post(f"/custom-rules/{rid}/unpublish", {"reason": "twice over"})) == "not_found"
    )
    actions = [
        c["action"] for c in alice.get(f"/custom-rules/changes?rule_id={rid}").json()["changes"]
    ]
    assert actions == ["admin_unpublish", "publish", "create"]
    assert code(bob.post(f"/custom-rules/{rid}/unadopt")) == "not_found"  # private and not held now
    # the author cannot undo it, and nobody runs the rule again unless they adopt it again
    assert code(alice.post(f"/custom-rules/{rid}/publish", {"reason": "back again"})) == (
        "withdrawn_by_admin"
    )
    assert alice.get("/custom-rules").json()["mine"][0]["visibility"] == "private"
    assert store.load_rule_set(api.db, "bob") == []


def test_unpublish_by_the_author_and_by_a_stranger(alice: As, bob: As, root: As) -> None:
    rid = mk_rule(alice)["rule_id"]
    assert (
        code(alice.post(f"/custom-rules/{rid}/unpublish", {"reason": "take back"})) == "not_public"
    )
    alice.post(f"/custom-rules/{rid}/publish", {"reason": "share it"})
    r = alice.post(f"/custom-rules/{rid}/unpublish", {"reason": "changed my mind"})
    assert r.status_code == 200 and r.json()["rule"]["visibility"] == "private"
    assert r.json()["rule"]["published_version"] is None
    assert [
        c["action"] for c in alice.get(f"/custom-rules/changes?rule_id={rid}").json()["changes"]
    ][:1] == ["unpublish"]
    assert code(bob.post(f"/custom-rules/{rid}/unadopt")) == "not_found"


def test_a_new_version_stays_private_until_published_and_adopters_stay_pinned(
    api: Api, alice: As, bob: As
) -> None:
    rule = mk_rule(alice)
    rid = rule["rule_id"]
    alice.post(f"/custom-rules/{rid}/publish", {"reason": "share it"})
    bob.post(f"/custom-rules/{rid}/adopt")
    ANSWERS["flag as low if an invoice is for more than 50000"] = answer(
        "ready",
        t1_spec(value="50000"),
        phrases=[
            ("flag as low", "severity"),
            ("an invoice", "doc_type"),
            ("is for more than", "operator"),
            ("50000", "value"),
        ],
    )
    try:
        d = start(alice, "flag as low if an invoice is for more than 50000", edit_of=rid)
        assert d["edit_of"] == rid and d["status"] == "ready" and d["duplicates"] == []
        r = finish(alice, d["draft_id"], reason="Lower the limit")
        assert r.status_code == 201, r.text
        assert r.json()["rule"]["version"] == 2 and r.json()["rule"]["rule_id"] == rid
        assert r.json()["rule"]["published_version"] == 1  # the public version did not change
    finally:
        ANSWERS.pop("flag as low if an invoice is for more than 50000")
    owner_view = alice.get(f"/custom-rules/{rid}").json()
    assert owner_view["version"] == 2 and [v["version"] for v in owner_view["versions"]] == [1, 2]
    assert owner_view["versions"][0]["spec"]["condition"]["value"] == "100000"
    assert owner_view["last_dry_run"]["spec_hash"] == owner_view["spec_hash"]
    assert [r.version for r in store.load_rule_set(api.db, "alice")] == [
        2
    ]  # the owner runs the newest
    assert [r.version for r in store.load_rule_set(api.db, "bob")] == [
        1
    ]  # the adopter stays pinned
    assert bob.get(f"/custom-rules/{rid}").json()["version"] == 1
    # publishing the new version offers an update; adopting again moves the pin
    alice.post(f"/custom-rules/{rid}/publish", {"reason": "publish v2"})
    ad = bob.get("/custom-rules").json()["adopted"][0]
    assert (ad["version"], ad["latest_version"], ad["update_available"]) == (1, 2, True)
    assert bob.post(f"/custom-rules/{rid}/adopt").status_code == 200
    assert [r.version for r in store.load_rule_set(api.db, "bob")] == [2]


def test_disable_and_retire(api: Api, alice: As, bob: As) -> None:
    rid = mk_rule(alice)["rule_id"]
    alice.post(f"/custom-rules/{rid}/publish", {"reason": "share it"})
    bob.post(f"/custom-rules/{rid}/adopt")
    assert code(alice.post(f"/custom-rules/{rid}/enabled", {"enabled": False})) == "reason_required"
    assert (
        code(alice.post(f"/custom-rules/{rid}/enabled", {"enabled": "no", "reason": "testing it"}))
        == "validation_error"
    )
    r = alice.post(f"/custom-rules/{rid}/enabled", {"enabled": False, "reason": "pausing it"})
    assert r.status_code == 200 and r.json()["rule"]["enabled"] is False
    assert store.load_rule_set(api.db, "alice") == []
    assert (
        len(store.load_rule_set(api.db, "bob")) == 1
    )  # the adopter's copy has its own switch: the author's
    alice.post(f"/custom-rules/{rid}/enabled", {"enabled": True, "reason": "resuming it"})
    assert len(store.load_rule_set(api.db, "alice")) == 1
    r = alice.post(f"/custom-rules/{rid}/retire", {"reason": "no longer needed"})
    assert (
        r.status_code == 200
        and r.json()["rule"]["status"] == "retired"
        and r.json()["rule"]["enabled"] is False
    )
    assert store.load_rule_set(api.db, "alice") == [] and store.load_rule_set(api.db, "bob") == []
    for suffix, body in [
        ("enabled", {"enabled": True, "reason": "resuming it"}),
        ("publish", {"reason": "share it"}),
        ("retire", {"reason": "no longer needed"}),
    ]:
        r = alice.post(f"/custom-rules/{rid}/{suffix}", body)
        assert r.status_code == 409 and code(r) == "rule_retired", suffix
    mine = alice.get("/custom-rules").json()["mine"]
    assert [(r["rule_id"], r["status"]) for r in mine] == [
        (rid, "retired")
    ]  # still listed, every version kept
    assert alice.get(f"/custom-rules/{rid}").json()["versions"]
    assert bob.get("/custom-rules/shared").json()["rules"] == []
    assert bob.get("/custom-rules").json()["adopted"][0]["active"] is False
    assert code(start_raw(alice, TOTAL_REQUEST, edit_of=rid)) == "rule_retired"


def start_raw(who: As, text: str, **extra: Any):  # noqa: ANN201
    return who.post("/custom-rules/drafts", {"text": text, **extra})


# ========================================================================= duplicates


def test_a_built_in_that_already_does_it_blocks_the_save(alice: As) -> None:
    d = start(alice, RECEIPT)
    assert d["status"] == "ready" and d["save_allowed"] is False
    assert (
        d["duplicates"][0]["rule"] == "rcpt_merchant" and d["duplicates"][0]["level"] == "identical"
    )
    alice.post(f"/custom-rules/drafts/{d['draft_id']}/dry-run")
    r = alice.post(
        "/custom-rules",
        {
            "draft_id": d["draft_id"],
            "name": "Merchant",
            "reason": "because",
            "confirm_warnings": True,
        },
    )
    assert r.status_code == 409 and code(r) == "duplicate_builtin"
    details = r.json()["error"]["details"]
    assert (
        details["rule"] == "rcpt_merchant"
        and "check catalogue" in details["action"]
        and details["what"]
    )


def test_an_identical_own_rule_is_refused_with_a_pointer(alice: As) -> None:
    rule = mk_rule(alice)
    d = start(alice, TOTAL_REQUEST)
    assert d["save_allowed"] is False and d["duplicates"][0]["rule"] == rule["rule_id"]
    alice.post(f"/custom-rules/drafts/{d['draft_id']}/dry-run")
    r = alice.post(
        "/custom-rules",
        {"draft_id": d["draft_id"], "name": "Again", "reason": "because", "confirm_warnings": True},
    )
    assert r.status_code == 409 and code(r) == "duplicate_custom"
    assert r.json()["error"]["details"] == {"rule_id": rule["rule_id"], "name": "Big invoices"}


def test_the_same_rule_can_be_created_again_after_it_was_deleted(alice: As) -> None:
    first = mk_rule(alice)
    blocked = start(alice, TOTAL_REQUEST)
    assert blocked["save_allowed"] is False and blocked["duplicates"][0]["rule"] == first["rule_id"]
    deleted = alice.post(f"/custom-rules/{first['rule_id']}/retire", {"reason": "deleting it"})
    assert deleted.status_code == 200
    again = start(alice, TOTAL_REQUEST)
    assert again["duplicates"] == [] and again["save_allowed"] is True
    saved = finish(alice, again["draft_id"], "Big invoices")
    assert saved.status_code == 201, saved.text
    new = saved.json()["rule"]
    assert new["rule_id"] != first["rule_id"] and new["status"] == "active"
    mine = alice.get("/custom-rules").json()["mine"]
    states = {m["rule_id"]: m["status"] for m in mine}
    assert states == {first["rule_id"]: "retired", new["rule_id"]: "active"}


def test_a_public_rule_blocks_an_identical_one_and_publishing_a_twin(alice: As, bob: As) -> None:
    mine = mk_rule(alice)
    theirs_draft = start(bob, TOTAL_REQUEST)
    theirs = finish(bob, theirs_draft["draft_id"], "Bob's big invoices").json()["rule"]
    alice.post(f"/custom-rules/{mine['rule_id']}/publish", {"reason": "share it"})
    r = bob.post(f"/custom-rules/{theirs['rule_id']}/publish", {"reason": "share mine too"})
    assert r.status_code == 409 and code(r) == "duplicate_custom"
    assert r.json()["error"]["details"]["rule_id"] == mine["rule_id"]
    again = start(bob, TOTAL_REQUEST)
    assert (
        again["duplicates"][0]["rule"] in (mine["rule_id"], theirs["rule_id"])
        and again["save_allowed"] is False
    )


# ======================================================================= saving, limits


def test_saving_needs_a_dry_run_a_name_and_a_reason(alice: As) -> None:
    d = start(alice, TOTAL_REQUEST)["draft_id"]

    def save(**extra: Any):  # noqa: ANN202
        return alice.post("/custom-rules", {"draft_id": d, **extra})

    assert code(save()) == "dry_run_required"
    alice.post(f"/custom-rules/drafts/{d}/dry-run")
    assert code(save()) == "warnings_not_confirmed"
    assert code(save(confirm_warnings=True)) == "name_required"
    assert code(save(confirm_warnings=True, name="A good rule")) == "reason_required"
    assert code(save(confirm_warnings=True, name="A good rule", reason="four")) == "reason_required"
    assert save(confirm_warnings=True, name="A good rule", reason="five+ chars").status_code == 201
    assert alice.post("/custom-rules", {"draft_id": d, "extra": 1}).status_code == 422


def test_a_dry_run_that_fires_on_few_cases_needs_no_confirmation(api: Api, alice: As) -> None:
    store_case(api.db, "c_big", [invoice(total="500000.00")], minutes=1)
    for i in range(9):
        store_case(api.db, f"c_small{i}", [invoice(total="10.00")], minutes=2 + i)
    d = start(alice, TOTAL_REQUEST)["draft_id"]
    dry = alice.post(f"/custom-rules/drafts/{d}/dry-run").json()
    assert (dry["evaluated"], dry["fired"], dry["warnings"], dry["needs_confirmation"]) == (
        10,
        1,
        [],
        False,
    )
    assert dry["examples"] == ["c_big"]
    r = alice.post(
        "/custom-rules", {"draft_id": d, "name": "Big invoices", "reason": "tested on stored cases"}
    )
    assert r.status_code == 201, r.text


def test_the_rule_limit(settings: Settings, db: Database) -> None:
    api = make_api(settings, db, fake(by_request), custom_rules_max_per_account=1)
    who = api.as_("alice")
    mk_rule(who)
    d = start(who, PASSPORT)["draft_id"]
    who.post(f"/custom-rules/drafts/{d}/dry-run")
    r = who.post(
        "/custom-rules",
        {"draft_id": d, "name": "Passports", "reason": "because", "confirm_warnings": True},
    )
    assert r.status_code == 409 and code(r) == "rule_limit"
    assert api.as_("bob").get("/custom-rules/capabilities").json()["limits"]["max_rules"] == 1


def test_dry_run_without_a_rule_is_refused(alice: As) -> None:
    d = start(alice, SOCIAL)["draft_id"]
    r = alice.post(f"/custom-rules/drafts/{d}/dry-run")
    assert r.status_code == 409 and code(r) == "no_spec"


# ============================================================================= the budget


def test_a_used_up_account_budget_stops_authoring_and_nothing_else(
    alice: As, bob: As, api: Api
) -> None:
    rule = mk_rule(alice)
    alice.post(f"/custom-rules/{rule['rule_id']}/publish", {"reason": "share it"})
    d = start(alice, SOCIAL)  # a ticket exists already
    broken = make_api(
        api.settings, api.db, fake(sequence(LLMBudgetExceeded("user_daily_cap", "1.00", "1.00")))
    )
    a, b = broken.as_("alice"), broken.as_("bob")
    r = a.post("/custom-rules/drafts", {"text": TOTAL_REQUEST})
    assert r.status_code == 429 and code(r) == "user_budget_used"
    assert (
        "$1.00" in r.json()["error"]["message"] and "midnight UTC" in r.json()["error"]["message"]
    )
    assert r.json()["error"]["details"] == {"limit_usd": "1.00", "used_usd": "1.00"}
    r = a.post(f"/custom-rules/drafts/{d['draft_id']}/turns", {"text": "a different idea entirely"})
    assert code(r) == "user_budget_used"
    # everything that needs no model still answers
    assert a.get("/custom-rules").status_code == 200
    assert a.get("/custom-rules/shared").status_code == 200
    assert a.get("/custom-rules/tickets").status_code == 200
    assert a.get(f"/custom-rules/drafts/{d['draft_id']}").status_code == 200
    assert a.get("/custom-rules/capabilities").status_code == 200
    assert (
        a.post("/custom-rules/tickets", {"text": "flag lenders on the statements"}).status_code
        == 201
    )
    assert b.post(f"/custom-rules/{rule['rule_id']}/adopt").status_code == 200
    assert b.post(f"/custom-rules/{rule['rule_id']}/unadopt").status_code == 200
    assert (
        a.post(
            f"/custom-rules/{rule['rule_id']}/enabled", {"enabled": False, "reason": "pausing it"}
        ).status_code
        == 200
    )
    assert (
        a.post(
            f"/custom-rules/{rule['rule_id']}/retire", {"reason": "no longer needed"}
        ).status_code
        == 200
    )


def test_the_sites_daily_budget_and_an_unavailable_model(settings: Settings, db: Database) -> None:
    site = make_api(
        settings, db, fake(sequence(LLMBudgetExceeded("daily_cap", "5.00", "5.00")))
    ).as_("alice")
    r = site.post("/custom-rules/drafts", {"text": TOTAL_REQUEST})
    assert r.status_code == 429 and code(r) == "site_budget_used"
    none = make_api(settings, db, fake()).as_("alice")  # no fixture: the fake backend cannot answer
    r = none.post("/custom-rules/drafts", {"text": TOTAL_REQUEST})
    assert r.status_code == 503 and code(r) == "authoring_unavailable"
    assert "tech team" in r.json()["error"]["message"]
    # a refused request never needed the model
    assert none.post("/custom-rules/drafts", {"text": NATIONALITY}).status_code == 200
    assert (
        none.post("/custom-rules/tickets", {"text": "flag lenders on the statements"}).status_code
        == 201
    )


def test_answers_need_no_model_at_all(settings: Settings, db: Database) -> None:
    api = make_api(settings, db, fake(debt_answer()))
    who = api.as_("alice")
    d = start(who)
    api.llm.fixtures.clear()  # from here on the model would fail
    r = who.post(
        f"/custom-rules/drafts/{d['draft_id']}/turns",
        {"answer": {"question_id": "q1", "option_id": "x3"}},
    )
    assert r.status_code == 200 and r.json()["spec"]["condition"]["multiplier"] == "3"
    assert who.post(f"/custom-rules/drafts/{d['draft_id']}/dry-run").status_code == 200


# =========================================================================== rate limit


def test_starting_drafts_shares_the_review_limiter(
    settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLDB_RATE_REVIEWS_PER_MIN", "2")
    api = make_api(settings, db, fake(by_request))
    who, other = api.as_("alice"), api.as_("bob")
    assert who.post("/custom-rules/drafts", {"text": TOTAL_REQUEST}).status_code == 200
    d = who.post("/custom-rules/drafts", {"text": TOTAL_REQUEST}).json()["draft_id"]
    r = who.post(f"/custom-rules/drafts/{d}/turns", {"text": TOTAL_REQUEST})
    assert r.status_code == 429 and code(r) == "rate_limited"
    assert r.json()["error"]["details"]["scope"] == "reviews"
    assert who.get("/custom-rules").status_code == 200  # reads are not limited
    assert (
        who.post("/custom-rules/tickets", {"text": "flag lenders on the statements"}).status_code
        == 201
    )
    assert (
        other.post("/custom-rules/drafts", {"text": TOTAL_REQUEST}).status_code == 200
    )  # per account


# ===================================================================== routes and ids


def test_no_operation_id_or_path_names_an_automatic_decision(api: Api) -> None:
    schema = api.app.openapi()
    paths = [p for p in schema["paths"] if p.startswith("/custom-rules")]
    assert len(paths) >= 12
    for path in paths:
        for op in schema["paths"][path].values():
            assert "auto" not in op["operationId"].lower() and "auto" not in path.lower()
            assert op["tags"] == ["custom-rules"]


def test_every_write_is_a_post(api: Api) -> None:
    schema = api.app.openapi()
    methods = {
        m for p, item in schema["paths"].items() if p.startswith("/custom-rules") for m in item
    }
    assert methods == {"get", "post"}


def test_a_draft_id_in_the_wrong_place_is_not_a_rule(alice: As) -> None:
    d = start(alice, TOTAL_REQUEST)["draft_id"]
    assert alice.get(f"/custom-rules/{d}").status_code == 404
    assert alice.get(f"/custom-rules/drafts/{mk_rule(alice)['rule_id']}").status_code == 404
