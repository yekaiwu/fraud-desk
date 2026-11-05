"""Regression tests for the second adversarial review of custom rules: currencies, short words, protected-
attribute false positives, model-supplied digits, refused words, atomic saving, the model-busy mark.
No network and no paid call: a fake model answers."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.config import Settings
from app.core.db import Database
from app.core.testing import FIXED_NOW
from app.custom_rules import author, drafts, readback, store
from app.custom_rules.engine import evaluate
from app.custom_rules.spec import DraftResponse, Question, QuestionOption, parse_spec
from tests.custom_rules_author_helpers import (
    TOTAL_REQUEST,
    answer,
    example,
    fake,
    on_settings,
    sequence,
    store_case,
    t1_spec,
)
from tests.custom_rules_helpers import ctx_with, invoice, loaded, t1
from tests.test_custom_rules_author import err, go, turn


@pytest.fixture
def cfg(settings: Settings) -> Settings:
    return on_settings(settings)


# ================================================================= 1. currencies and short words

FILLER = [("flag", "filler"), ("as low", "severity"), ("an invoice", "doc_type")]


def money_answer(*extra: tuple[str, str | None], **kw: Any) -> dict[str, Any]:
    return answer(
        "ready",
        t1_spec(value="100000"),
        phrases=[*FILLER, ("is over", "operator"), *extra],
        **kw,
    )


@pytest.mark.parametrize(
    ("request_text", "named", "mapped"),
    [
        ("flag as low if an invoice is over SGD 100000", "SGD", ("100000", "value")),
        (
            "flag as low if an invoice is over 100000 dollars",
            "dollars",
            ("100000 dollars", "value"),
        ),
        ("flag as low if an invoice is over 100000 yen", "yen", ("100000 yen", "value")),
        ("flag as low if an invoice is over $100000", "$", ("$100000", "value")),
        ("flag as low if an invoice is over USD100000", "USD", ("USD100000", "value")),
    ],
)
def test_a_request_that_names_a_currency_the_rule_ignores_is_not_ready(
    db: Database, cfg: Settings, request_text: str, named: str, mapped: tuple[str, str]
) -> None:
    r = go(db, cfg, fake(money_answer(mapped)), request_text)
    assert r.status == "partially_supported" and r.needs_narrower_ack
    assert any(named.lower() in p.phrase.lower() for p in r.unsupported_parts)
    assert any("in any currency" in line for line in r.will_not_check), r.will_not_check
    assert r.readback is not None and "in whatever currency the invoice prints" in r.readback
    save = author.SaveRuleRequest(draft_id=r.draft_id, name="Big invoices", reason="because")
    author.dry_run_draft(db, cfg, account="alice", draft_id=r.draft_id, now=FIXED_NOW)
    e = err(
        author.save_draft,
        db,
        cfg,
        account="alice",
        req=save.model_copy(update={"confirm_warnings": True}),
        admins=frozenset(),
        now=FIXED_NOW,
    )
    assert e.code == "narrower_not_accepted"  # the officer must tick that it checks less than asked


def test_a_currency_the_model_already_gave_up_on_is_not_added_twice(
    db: Database, cfg: Settings
) -> None:
    raw = money_answer(
        ("100000", "value"),
        ("SGD", None),
        unsupported_parts=[{"phrase": "SGD", "reason": "field_not_extracted", "detail": "no"}],
    )
    r = go(db, cfg, fake(raw), "flag as low if an invoice is over SGD 100000")
    assert r.status == "partially_supported" and len(r.unsupported_parts) == 1


def test_a_rule_on_the_currency_field_itself_stays_ready(db: Database, cfg: Settings) -> None:
    raw = answer(
        "ready",
        t1_spec("invoice.currency", "ne", "SGD"),
        phrases=[
            ("flag", "filler"),
            ("as low", "severity"),
            ("the invoice", "doc_type"),
            ("currency", "field"),
            ("is not", "operator"),
            ("SGD", "value"),
        ],
    )
    r = go(db, cfg, fake(raw), "flag as low if the invoice currency is not SGD")
    assert r.status == "ready" and r.unsupported_parts == []
    assert r.readback is not None and "whatever currency" not in r.readback


def test_a_request_without_a_currency_is_unaffected(db: Database, cfg: Settings) -> None:
    r = go(db, cfg, fake(example(TOTAL_REQUEST)), TOTAL_REQUEST)
    assert r.status == "ready" and r.unsupported_parts == []


def test_a_currency_named_in_an_earlier_message_still_counts_on_a_later_turn(
    db: Database, cfg: Settings
) -> None:
    first = answer("needs_clarification", None, phrases=[("flag", "filler")])
    r = go(db, cfg, fake(first), "flag invoices that are over SGD 100000")
    assert r.status == "needs_clarification"
    llm = fake(money_answer(("100000", "value"), ("SGD 100000", None)))
    r2 = turn(db, cfg, llm, r.draft_id, text="as low please, an invoice, over 100000")
    assert r2.status == "partially_supported"
    assert any("SGD" in p.phrase for p in r2.unsupported_parts)


def test_money_readback_flag_explanation_and_evidence_say_as_printed() -> None:
    spec = parse_spec(
        {"spec_version": 1, "severity": "low", "condition": t1("invoice.total", "gt", "100000")}
    )[0]
    assert spec is not None
    assert readback.readback(spec) == (
        "Flags low when an invoice's total is greater than 100,000.00 in whatever currency the invoice prints."
    )
    assert "in whatever currency the invoice prints" in readback.flag_explanation(spec)
    jpy = invoice(total="150000.00", currency="JPY")
    ctx = ctx_with([jpy], loaded(t1("invoice.total", "gt", "100000")))
    result = evaluate(ctx.custom_rules[0], ctx.case, today=ctx.today)
    assert result.flags and result.flags[0].evidence.endswith("(as printed; currency not compared)")
    # only an amount is "as printed": a percentage, a text or an enum field says nothing about a currency
    for cond in (t1("invoice.gst_rate", "gt", "9"), t1("invoice.payment_status", "ne", "paid")):
        other = parse_spec({"spec_version": 1, "severity": "low", "condition": cond})[0]
        assert other is not None and "whatever currency" not in readback.readback(other)


@pytest.mark.parametrize(
    ("text", "phrases", "missed"),
    [
        ("flag when the status is not paid", ["flag", "the status", "is", "paid"], ["not"]),
        (
            "flag if a receipt has no receipt number",
            ["flag", "a receipt", "has", "receipt number"],
            ["no"],
        ),
        (
            "flag if no invoice number",
            ["flag", "invoice number"],
            ["no"],
        ),  # "no" is not inside "number"
        ("flag non-zero totals", ["flag", "zero totals"], ["non"]),
        ("flag if the status isn't paid", ["flag", "the status", "paid"], ["isn't"]),
        ("flag if the PO number is missing", ["flag", "number is missing"], ["po"]),
        ("flag when GST is missing", ["flag", "missing"], ["gst"]),
        ("flag when the UEN is missing", ["flag", "missing"], ["uen"]),
        ("flag when the ID is missing", ["flag", "missing"], ["id"]),
        (
            "flag only invoices excluding receipts",
            ["flag", "invoices", "receipts"],
            ["only", "excluding"],
        ),
        ("flag invoices except paid ones", ["flag", "invoices", "paid ones"], ["except"]),
        ("flag an invoice without a number", ["flag", "an invoice", "number"], ["without"]),
    ],
)
def test_a_dropped_negation_qualifier_or_domain_word_is_noticed(
    text: str, phrases: list[str], missed: list[str]
) -> None:
    assert author.uncovered_words(text, phrases) == missed


@pytest.mark.parametrize(
    ("text", "phrases"),
    [
        ("flag when the status is not paid", ["flag", "the status", "is not", "paid"]),
        (
            "flag if a receipt has no receipt number",
            ["flag", "a receipt", "has no", "receipt number"],
        ),
        ("flag non-zero totals", ["flag", "non-zero", "totals"]),
        ("flag if the status isn't paid", ["flag", "the status", "isn't", "paid"]),
        ("flag if the status isn’t paid", ["flag", "the status", "isn't", "paid"]),
        ("flag if the PO number is missing", ["flag", "PO number", "is missing"]),
        (
            "flag as low if an invoice total is more than 100000",
            ["flag", "as low", "an invoice", "total", "is more than", "100000"],
        ),
        (
            "flag the note on an invoice dated in the future",
            ["flag", "the note", "an invoice", "dated in the future"],
        ),
        ("Flag minor rounding differences", ["flag", "minor rounding differences"]),
    ],
)
def test_ordinary_requests_with_every_word_mapped_stay_clean(text: str, phrases: list[str]) -> None:
    assert author.uncovered_words(text, phrases) == []


def test_a_dropped_not_stops_a_ready_answer_from_being_ready(db: Database, cfg: Settings) -> None:
    # "not over 50000" read as "over 50000": a comparison that is not itself a negation cannot carry the "not"
    raw = answer(
        "ready",
        t1_spec("invoice.total", "gt", "50000"),
        phrases=[
            ("flag", "filler"),
            ("as low", "severity"),
            ("the invoice total", "field"),
            ("over", "operator"),
            ("50000", "value"),
        ],
    )
    request = "flag as low if the invoice total is not over 50000"
    r = go(db, cfg, fake(raw), request)
    assert r.status == "needs_clarification" and not r.save_allowed
    assert any("not accounted for: not" in n for n in r.notes)
    full = answer(
        "ready",
        t1_spec("invoice.total", "lte", "50000"),
        phrases=[
            ("flag", "filler"),
            ("as low", "severity"),
            ("the invoice total", "field"),
            ("is not over", "operator"),
            ("50000", "value"),
        ],
    )
    assert go(db, cfg, fake(full), request).status == "ready"


# ================================================================= 3. protected-attribute false positives


@pytest.mark.parametrize(
    "text",
    [
        "flag invoices in Australian dollars above 10,000",
        "flag if the currency is British pound",
        "flag if the currency is Japanese yen",
        "flag if the currency is Indian rupees",
        "flag if the currency is thai baht",
        "flag invoices in American dollars over 5000",
        "flag if the currency is Chinese yuan",
        "flag if the currency is Malaysian ringgit",
        "flag if the currency is Swiss francs",
        "flag if the currency is Korean won",
        "Flag minor rounding differences",
        "flag minor differences in the invoice total",
        "Flag an invoice whose total is over 500 for a customer",
        "purchase order with total under 100 from the owner",
        "flag a receipt where the amount is above 20 for a shareholder",
        "Flag when interest coverage is below 1.5, set it to low",
        "flag when the balance is under 10 and set it to medium",
    ],
)
def test_ordinary_requests_on_money_and_rounding_are_not_refused(text: str) -> None:
    assert author.precheck(text) is None, text


@pytest.mark.parametrize(
    "text",
    [
        "flag Australian directors",
        "flag British shareholders",
        "flag if the owner is Japanese",
        "flag Indian customers",
        "flag thai applicants",
        "flag non-Chinese owners",
        "flag if the director is a minor",
        "flag if the applicant is a minor",
        "flag minors",
        "flag a minor shareholder",
        "flag if the customer is a minor.",
        "flag directors over 65",
        "flag customers under 21",
        "flag an owner older than 70",
        "flag directors who are under age 21",
        "flag applicants over 18 years",
        "the owner is over 65 years",
        "flag documents more than 3 years old",
        "flag people born before 1960",
        "flag Malay directors",
        "flag Eurasian shareholders",
        "flag disabled applicants",
        "flag if the surname is Tan",
        "flag Chinese-sounding names",
        "flag foreigners",
        "flag by religion",
        "flag if the citizenship is not Singapore",
        "flag if gender is female",
        "set the score to 0 for big invoices",
        "set coverage to 100 for big invoices",
        "set the points to 5",
    ],
)
def test_the_tightened_patterns_still_refuse_what_they_were_written_for(text: str) -> None:
    refusal = author.precheck(text)
    assert refusal is not None and refusal.reason in ("protected_attribute", "changes_score"), text


def test_nationality_before_a_currency_is_still_refused_when_no_currency_follows() -> None:
    for text in ("flag Japanese invoices", "flag Indian suppliers", "flag thai companies"):
        refusal = author.precheck(text)
        assert refusal is not None and refusal.reason == "protected_attribute", text


# ================================================================= 4. model-supplied digits never crash


def days_question(*values: str) -> dict[str, Any]:
    labels = ["a", "b", "c", "d"]
    return {
        "id": "q1",
        "slot": "days",
        "text": "How many days?",
        "options": [
            {"id": labels[i], "label": f"option {i}", "value": v} for i, v in enumerate(values)
        ],
    }


def date_window_answer(*values: str) -> dict[str, Any]:
    return answer(
        "needs_clarification",
        {
            "spec_version": 1,
            "severity": "low",
            "condition": {
                "template": "date_window",
                "field": "invoice.invoice_date",
                "direction": "older_than",
                "days": 30,
            },
        },
        phrases=[("flag", "filler"), ("an invoice", "doc_type"), ("old", "direction")],
        questions=[days_question(*values)],
    )


@pytest.mark.parametrize("odd", ["²", "٣", "⑧", "３０", "3²", "-1", "1e3", " "])
def test_a_digit_that_is_not_an_ascii_digit_cannot_crash_the_draft(
    db: Database, cfg: Settings, odd: str
) -> None:
    r = go(db, cfg, fake(date_window_answer(odd, "30", "60")), "flag an old invoice")
    assert r.status in ("needs_clarification", "partially_supported", "ready")
    # the odd option is dropped, the other two stay
    assert [o.value for q in r.questions for o in q.options] in ([], ["30", "60"])


def test_apply_answer_is_total_for_any_option_text() -> None:
    spec = parse_spec(date_window_answer("1", "2")["spec"])[0]
    question = Question(
        id="q1",
        slot="days",
        text="How many days?",
        options=[
            QuestionOption(id="a", label="x", value="1"),
            QuestionOption(id="b", label="y", value="2"),
        ],
    )
    for value in ("²", "٣", "99999999999999999999", "٣", "1" * 7, "0x10", "1_0"):
        got, problems = author.apply_answer(
            spec, question, QuestionOption(id="a", label="x", value=value)
        )
        assert (got is None) == bool(problems), value


# ================================================================= 5. refused words are not kept


MARKER = "ZEBRAMARKER"


def refused_answer() -> dict[str, Any]:
    return answer(
        "refused",
        None,
        phrases=[("flag", "filler"), (f"{MARKER} borrowers", None)],
        understood=f"The officer wants {MARKER} people flagged",
        refusal_reason="protected_attribute",
        unsupported_parts=[
            {"phrase": f"{MARKER} group", "reason": "policy_forbidden", "detail": MARKER}
        ],
    )


def test_a_request_the_model_refuses_keeps_none_of_its_words_or_the_models(
    db: Database, cfg: Settings
) -> None:
    r = go(db, cfg, fake(refused_answer()), f"flag {MARKER} borrowers please")
    assert r.status == "refused" and r.refusal is not None
    assert r.understood == "" and r.phrase_map == [] and r.unsupported_parts == []
    rec = drafts.load_draft(db, r.draft_id)
    assert rec is not None and rec.request == "" and rec.body.model is None
    with db.conn() as c:
        stored = json.dumps([tuple(row) for row in c.execute("SELECT * FROM custom_rule_drafts")])
    assert MARKER not in stored
    assert rec.body.notes == [] and rec.body.uncovered == [] and rec.body.questions == []


def test_a_refusal_in_a_later_turn_leaves_no_model_output_either(
    db: Database, cfg: Settings
) -> None:
    first = go(db, cfg, fake(example(TOTAL_REQUEST)), TOTAL_REQUEST)
    r = turn(db, cfg, fake(refused_answer()), first.draft_id, text=f"and also {MARKER} borrowers")
    assert r.status == "refused"
    rec = drafts.load_draft(db, first.draft_id)
    assert rec is not None and rec.body.model is None and rec.body.spec is None
    assert MARKER not in json.dumps(rec.body.model_dump(mode="json"))
    assert r.understood == "" and r.unsupported_parts == [] and r.phrase_map == []


def test_a_refusal_the_code_decides_is_stored_with_its_reason_only(
    db: Database, cfg: Settings
) -> None:
    # the model does not refuse, but one phrase it gave up on is about a protected attribute
    raw = answer(
        "partially_supported",
        t1_spec(),
        phrases=[("flag", "filler"), ("an invoice", "doc_type")],
        understood=f"{MARKER}",
        unsupported_parts=[
            {
                "phrase": "the director's nationality",
                "reason": "field_not_extracted",
                "detail": MARKER,
            }
        ],
    )
    r = go(db, cfg, fake(raw), "flag an invoice over 100000 when the owner is local")
    assert (
        r.status == "refused"
        and r.refusal is not None
        and r.refusal.reason == "protected_attribute"
    )
    rec = drafts.load_draft(db, r.draft_id)
    assert (
        rec is not None
        and rec.body.model is None
        and MARKER not in json.dumps(rec.body.model_dump(mode="json"))
    )


# ================================================================= 9. closing a draft and creating its rule


def prepared_draft(db: Database, cfg: Settings) -> DraftResponse:
    store_case(db, "c_a", [invoice(total="500000.00")], minutes=1)
    for i in range(9):
        store_case(db, f"c_n{i}", [invoice(total="10.00")], minutes=2 + i)
    r = go(db, cfg, fake(example(TOTAL_REQUEST)), TOTAL_REQUEST)
    author.dry_run_draft(db, cfg, account="alice", draft_id=r.draft_id, now=FIXED_NOW)
    return r


def test_a_turn_that_gets_in_during_a_save_leaves_no_rule_behind(
    db: Database, cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = prepared_draft(db, cfg)
    real = store.count_active_owned

    def a_turn_commits_meanwhile(*args: Any, **kw: Any) -> int:
        with db.conn() as c:  # another request stored a turn after this save read the draft
            c.execute("UPDATE custom_rule_drafts SET turns = turns + 1 WHERE id = ?", (r.draft_id,))
        return real(*args, **kw)

    monkeypatch.setattr(store, "count_active_owned", a_turn_commits_meanwhile)
    req = author.SaveRuleRequest(draft_id=r.draft_id, name="Big invoices", reason="because")
    e = err(author.save_draft, db, cfg, account="alice", req=req, admins=frozenset(), now=FIXED_NOW)
    assert (e.status_code, e.code) == (409, "draft_busy")
    assert store.list_owned(db, "alice") == []  # no rule without a closed draft
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM custom_rule_changes").fetchone()[0] == 0
    rec = drafts.load_draft(db, r.draft_id)
    assert rec is not None and rec.state == "open" and rec.saved_rule_id is None


def test_a_save_closes_the_draft_and_creates_the_rule_together(db: Database, cfg: Settings) -> None:
    r = prepared_draft(db, cfg)
    req = author.SaveRuleRequest(draft_id=r.draft_id, name="Big invoices", reason="because")
    ok = author.save_draft(db, cfg, account="alice", req=req, admins=frozenset(), now=FIXED_NOW)
    rec = drafts.load_draft(db, r.draft_id)
    assert rec is not None and rec.state == "saved" and rec.saved_rule_id == ok.rule.id
    assert [x.id for x in store.list_owned(db, "alice")] == [ok.rule.id]


def test_a_failed_close_rolls_the_new_version_of_an_edit_back_too(
    db: Database, cfg: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = prepared_draft(db, cfg)
    ok = author.save_draft(
        db,
        cfg,
        account="alice",
        req=author.SaveRuleRequest(draft_id=r.draft_id, name="Big invoices", reason="because"),
        admins=frozenset(),
        now=FIXED_NOW,
    )
    edit_raw = example(TOTAL_REQUEST)
    edit_raw = {**edit_raw, "spec": t1_spec(value="200000")}
    d2 = go(
        db,
        cfg,
        fake(edit_raw),
        "flag as low if an invoice is for more than 200000",
        edit_of=ok.rule.id,
    )
    author.dry_run_draft(db, cfg, account="alice", draft_id=d2.draft_id, now=FIXED_NOW)
    real = store.get_version

    def a_turn_commits_meanwhile(*args: Any, **kw: Any) -> Any:
        with db.conn() as c:
            c.execute(
                "UPDATE custom_rule_drafts SET turns = turns + 1 WHERE id = ?", (d2.draft_id,)
            )
        monkeypatch.setattr(store, "get_version", real)
        return real(*args, **kw)

    monkeypatch.setattr(store, "get_version", a_turn_commits_meanwhile)
    e = err(
        author.save_draft,
        db,
        cfg,
        account="alice",
        req=author.SaveRuleRequest(draft_id=d2.draft_id, reason="because"),
        admins=frozenset(),
        now=FIXED_NOW,
    )
    assert e.code == "draft_busy"
    again = store.get_rule(db, ok.rule.id)
    assert again is not None and again.current_version == 1
    assert len(store.list_versions(db, ok.rule.id)) == 1


# ================================================================= 10. the model-busy mark


def busy_mark(db: Database, draft_id: str) -> str | None:
    with db.conn() as c:
        return c.execute(
            "SELECT model_busy_at FROM custom_rule_drafts WHERE id = ?", (draft_id,)
        ).fetchone()[0]


def test_a_dry_run_does_not_clear_the_mark_of_a_turn_still_waiting_for_the_model(
    db: Database, cfg: Settings
) -> None:
    r = go(db, cfg, fake(example(TOTAL_REQUEST)), TOTAL_REQUEST)
    drafts.claim_model(db, r.draft_id, expected_turns=r.turn, now=FIXED_NOW)
    assert busy_mark(db, r.draft_id) is not None
    author.dry_run_draft(db, cfg, account="alice", draft_id=r.draft_id, now=FIXED_NOW)
    assert busy_mark(db, r.draft_id) is not None  # the first paid call is still out
    e = err(drafts.claim_model, db, r.draft_id, expected_turns=r.turn, now=FIXED_NOW)
    assert (e.status_code, e.code) == (409, "draft_busy")  # a second paid call cannot start


def test_a_ticket_does_not_clear_the_mark_either(db: Database, cfg: Settings) -> None:
    r = go(db, cfg, fake(example(TOTAL_REQUEST)), TOTAL_REQUEST)
    drafts.claim_model(db, r.draft_id, expected_turns=r.turn, now=FIXED_NOW)
    author.ticket_from_draft(
        db, cfg, account="alice", draft_id=r.draft_id, severity=None, now=FIXED_NOW
    )
    assert busy_mark(db, r.draft_id) is not None


def test_the_turn_that_set_the_mark_clears_it_when_it_is_stored(
    db: Database, cfg: Settings
) -> None:
    r = go(db, cfg, fake(example(TOTAL_REQUEST)), TOTAL_REQUEST)
    r2 = turn(db, cfg, fake(example(TOTAL_REQUEST)), r.draft_id, text=TOTAL_REQUEST)
    assert r2.turn == 2 and busy_mark(db, r.draft_id) is None
    drafts.claim_model(db, r.draft_id, expected_turns=2, now=FIXED_NOW)  # free for the next turn


def test_a_failed_turn_gives_the_mark_back(db: Database, cfg: Settings) -> None:
    r = go(db, cfg, fake(example(TOTAL_REQUEST)), TOTAL_REQUEST)
    boom = fake(sequence(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        turn(db, cfg, boom, r.draft_id, text=TOTAL_REQUEST)
    assert busy_mark(db, r.draft_id) is None
