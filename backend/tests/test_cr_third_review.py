"""Regression tests for the third review of custom rules: a currency caveat made by code never makes a request
unsupported, negations carried by the rule itself, what counts as a currency, one tokenising for both sides
of the word check, order-insensitive age and score wording. No network and no paid call: a fake model answers."""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.core.config import Settings
from app.core.db import Database
from app.custom_rules import author, tickets
from app.custom_rules.spec import DraftResponse, ModelDraft, parse_spec
from tests.custom_rules_author_helpers import answer, fake, on_settings, sequence, t1_spec
from tests.test_custom_rules_author import go, turn


@pytest.fixture
def cfg(settings: Settings) -> Settings:
    return on_settings(settings)


def all_tickets(db: Database) -> list[Any]:
    return tickets.list_tickets(db, viewer="alice", admin=True)


# ================================================================= 1. an ambiguous request that names a currency

AMBIGUOUS_PHRASES = [
    ("flag", "filler"),
    ("an invoice", "doc_type"),
    ("amount", "field"),
    ("over", "operator"),
    ("50000", "value"),
]
AMBIGUOUS_TWO = [t1_spec(value="50000"), t1_spec(field="invoice.subtotal", value="50000")]


def ambiguous_answer() -> dict[str, Any]:
    return answer("ambiguous", None, phrases=AMBIGUOUS_PHRASES, candidates=AMBIGUOUS_TWO)


def test_an_ambiguous_request_that_names_a_currency_keeps_its_readings_and_files_no_ticket(
    db: Database, cfg: Settings
) -> None:
    r = go(db, cfg, fake(ambiguous_answer()), "flag an invoice amount over $50000")
    assert r.status == "ambiguous" and r.spec is None
    assert [c.index for c in r.candidates] == [0, 1]
    assert r.ticket is None and r.unsupported_parts == []
    assert all_tickets(db) == []


def test_choosing_a_reading_of_a_currency_request_brings_the_caveat(
    db: Database, cfg: Settings
) -> None:
    llm = fake(ambiguous_answer())
    r = go(db, cfg, llm, "flag an invoice amount over SGD 50000")
    assert r.status == "ambiguous"
    chosen = turn(db, cfg, llm, r.draft_id, choose_candidate=1)
    assert chosen.status == "partially_supported" and chosen.needs_narrower_ack
    assert any("sgd" in p.phrase.lower() for p in chosen.unsupported_parts)
    assert any("in any currency" in line for line in chosen.will_not_check)
    assert chosen.spec is not None and chosen.spec.condition.field == "invoice.subtotal"  # type: ignore[union-attr]
    assert (
        chosen.ticket is None and all_tickets(db) == []
    )  # the caveat alone never goes to the tech team
    assert len(llm.calls) == 1


def test_choosing_a_reading_without_a_currency_in_the_request_stays_ready(
    db: Database, cfg: Settings
) -> None:
    llm = fake(ambiguous_answer())
    r = go(db, cfg, llm, "flag an invoice amount over 50000")
    assert turn(db, cfg, llm, r.draft_id, choose_candidate=0).status == "ready"


def test_choosing_a_reading_on_the_currency_field_stays_ready(db: Database, cfg: Settings) -> None:
    raw = answer(
        "ambiguous",
        None,
        phrases=[("flag", "filler"), ("an invoice", "doc_type"), ("currency", "field")],
        candidates=[
            t1_spec("invoice.currency", "ne", "SGD"),
            t1_spec("bank_statement.currency", "ne", "SGD"),
        ],
    )
    llm = fake(raw)
    r = go(db, cfg, llm, "flag an invoice currency that is not SGD")
    assert turn(db, cfg, llm, r.draft_id, choose_candidate=0).status == "ready"


QUESTION = {
    "id": "q1",
    "slot": "field",
    "text": "Which date do you mean?",
    "options": [
        {"id": "inv", "label": "Invoice date", "value": "invoice.invoice_date"},
        {"id": "due", "label": "Due date", "value": "invoice.due_date"},
    ],
    "default": "inv",
}
DATE_SPEC = {
    "spec_version": 1,
    "severity": "low",
    "condition": {
        "template": "date_window",
        "field": "invoice.invoice_date",
        "direction": "older_than",
        "days": 90,
    },
}


def test_answering_a_question_of_a_currency_request_keeps_the_caveat(
    db: Database, cfg: Settings
) -> None:
    ask = answer(
        "needs_clarification",
        DATE_SPEC,
        phrases=[("flag", "filler"), ("old invoices", "field")],
        questions=[QUESTION],
    )
    llm = fake(ask)
    r = go(db, cfg, llm, "flag old invoices in SGD")
    assert r.status == "partially_supported" and r.questions
    done = turn(db, cfg, llm, r.draft_id, answer={"question_id": "q1", "option_id": "due"})
    assert done.status == "partially_supported" and done.needs_narrower_ack
    assert any("sgd" in p.phrase.lower() for p in done.unsupported_parts)
    assert done.ticket is None and all_tickets(db) == []


def test_answering_a_question_that_the_model_reads_with_the_request_keeps_the_caveat(
    db: Database, cfg: Settings
) -> None:
    first = answer(
        "needs_clarification",
        None,
        phrases=[("flag", "filler"), ("invoices", "doc_type")],
        questions=[QUESTION],
    )
    second = answer(
        "ready",
        DATE_SPEC,
        phrases=[
            ("flag", "filler"),
            ("old invoices", "field"),
            ("older", "operator"),
            ("SGD", "filler"),
        ],
    )
    llm = fake(sequence(first, second))
    r = go(db, cfg, llm, "flag old invoices in SGD")
    assert r.status == "needs_clarification" and r.ticket is None
    done = turn(db, cfg, llm, r.draft_id, answer={"question_id": "q1", "option_id": "due"})
    assert done.status == "partially_supported"
    assert any("sgd" in p.phrase.lower() for p in done.unsupported_parts)
    assert all_tickets(db) == []


def test_the_currency_part_needs_a_rule_to_attach_to() -> None:
    model = ModelDraft.model_validate(answer("ambiguous", None, phrases=AMBIGUOUS_PHRASES))
    text = "flag an invoice over $50000"
    assert author._with_currency_part(model, None, text) is model
    spec = parse_spec(t1_spec())[0]
    assert author._with_currency_part(model, spec, text) is not model


# ================================================================= 2. negations carried by the rule

PO = "invoice.po_reference"


def vs(field: str, op: str, value: str) -> dict[str, Any]:
    return t1_spec(field, op, value)


def miss(field: str) -> dict[str, Any]:
    return {
        "spec_version": 1,
        "severity": "low",
        "condition": {"template": "field_missing", "field": field},
    }


def date(field: str, direction: str, days: int | None = None) -> dict[str, Any]:
    cond: dict[str, Any] = {"template": "date_window", "field": field, "direction": direction}
    if days is not None:
        cond["days"] = days
    return {"spec_version": 1, "severity": "low", "condition": cond}


# '|' separates the chunks the model quotes. A chunk starting with @ is filed under the field slot, ~ operator,
# = value, # doc_type, ^ direction; no mark is filler. A lean model leaves out the filler words that LEAN_OUT
# lists ("has no", "without", "does not have": the rule is the negation); a chunk starting with ! is left out
# by every model (the dropped negation).
ORDINARY: list[tuple[str, dict[str, Any]]] = [
    ("flag|if|#an invoice|has no|@PO reference", miss(PO)),
    ("flag|when|#an invoice|does not have|@a PO reference", miss(PO)),
    ("flag|#invoices|without|@a GST number", miss("invoice.supplier_gst_no")),
    ("flag|if|@the supplier UEN is missing", miss("invoice.supplier_uen")),
    ("flag|when|#an invoice|has no|@buyer UEN", miss("invoice.buyer_uen")),
    ("flag|#invoices|that don't have|@a contract reference", miss("invoice.contract_reference")),
    ("flag|if|#the statement|has no|@opening balance", miss("bank_statement.opening_balance")),
    ("flag|if|#the invoice|has no|@invoice number", miss("invoice.invoice_number")),
    ("flag|if|#the invoice|has no|@GST amount", miss("invoice.gst_amount")),
    ("flag|if|@the PO reference is empty", miss(PO)),
    ("flag|if|@the supplier name is missing", miss("invoice.supplier_name")),
    ("flag|when|@the paid date is missing", miss("invoice.paid_date")),
    ("flag|#receipts|without|@a merchant name", miss("receipt.merchant_name")),
    ("flag|if|@the buyer name is missing", miss("invoice.buyer_name")),
    ("flag|if|#the invoice|~is not|=paid", vs("invoice.payment_status", "ne", "paid")),
    ("flag|if|#the invoice|~isn't|=paid", vs("invoice.payment_status", "ne", "paid")),
    ("flag|if|#the invoice|~hasn't been|=paid", vs("invoice.payment_status", "ne", "paid")),
    ("flag|if|#the invoice|~was not|=paid", vs("invoice.payment_status", "ne", "paid")),
    ("flag|if|#the invoice|~is not|in|=SGD", vs("invoice.currency", "ne", "SGD")),
    ("flag|#invoices|~not|in|=Singapore dollars", vs("invoice.currency", "ne", "SGD")),
    (
        "flag|#statements|where|@the currency|~is not|=SGD",
        vs("bank_statement.currency", "ne", "SGD"),
    ),
    ("flag|if|@the GST rate|~is not|=9", vs("invoice.gst_rate", "ne", "9")),
    ("flag|if|@the GST amount|~is|=nil", vs("invoice.gst_amount", "eq", "0")),
    ("flag|#invoices|~over|=50000", vs("invoice.total", "gt", "50000")),
    ("flag|if|@the invoice total|~is above|=50000", vs("invoice.total", "gt", "50000")),
    ("flag|#invoices|@with a subtotal|~above|=20000", vs("invoice.subtotal", "gt", "20000")),
    ("flag|all #invoices|~over|=100000", vs("invoice.total", "gt", "100000")),
    (
        "flag|#invoices|where|@the dollar amount|~is over|=10000",
        vs("invoice.total", "gt", "10000"),
    ),
    (
        "flag|#statements|@with total debits|~over|=1,000,000",
        vs("bank_statement.total_debits", "gt", "1000000"),
    ),
    (
        "flag|when|#the bank statement|@closing balance|~is below|=0",
        vs("bank_statement.closing_balance", "lt", "0"),
    ),
    (
        "flag|#invoices|~above|=5000|so I can try calling the buyer",
        vs("invoice.total", "gt", "5000"),
    ),
    ("flag|#invoices|@due|^in the future", date("invoice.due_date", "in_future")),
    ("flag|if|#the invoice|@is dated|^in the future", date("invoice.invoice_date", "in_future")),
    ("flag|#an invoice|^older than|=90 days", date("invoice.invoice_date", "older_than", 90)),
    (
        "flag|if|#a receipt|^is older than|=365 days",
        date("receipt.receipt_date", "older_than", 365),
    ),
    ("flag|if|@the invoice total|~is below|=100", vs("invoice.total", "lt", "100")),
    (
        "flag|when|#the invoice|@payment status|~is not|=paid|and set it to low",
        vs("invoice.payment_status", "ne", "paid"),
    ),
]

# a model that dropped the negation (!) or read the currency away: whatever else it got right, never ready
NEVER_READY: list[tuple[str, dict[str, Any]]] = [
    ("flag|if|@the invoice total|!is not|~over|=50000", vs("invoice.total", "gt", "50000")),
    ("flag|#invoices|!that are not|~above|=5000", vs("invoice.total", "gt", "5000")),
    ("flag|an #invoice|!not|^older than|=90 days", date("invoice.invoice_date", "older_than", 90)),
    ("flag|if|@the invoice total|is|!non|=zero", vs("invoice.total", "eq", "0")),
    ("flag|if|@the GST rate|!is not|~is|=9", vs("invoice.gst_rate", "eq", "9")),
    ("flag|#invoices|~over|=$50,000", vs("invoice.total", "gt", "50000")),
    ("flag|#invoices|@with a total|~over|=1000 USD", vs("invoice.total", "gt", "1000")),
    ("flag|#invoices|~above|=RM 50000", vs("invoice.total", "gt", "50000")),
]
SLOT = {"@": "field", "~": "operator", "=": "value", "#": "doc_type", "^": "direction"}
LEAN_OUT = {"has no", "does not have", "without", "that don't have"}


def build(chunks: str, *, lean: bool) -> tuple[str, list[tuple[str, str | None]]]:
    request: list[str] = []
    phrases: list[tuple[str, str | None]] = []
    for chunk in chunks.split("|"):
        dropped = chunk.startswith("!")
        slot = SLOT.get(chunk[0], "filler")
        text = re.sub(r"[@=#]", "", chunk.lstrip("!@~=#^"))
        request.append(text)
        if dropped or (slot == "filler" and lean and text in LEAN_OUT):
            continue
        phrases.append((text, slot))
    return " ".join(request), phrases


def run_row(db: Database, cfg: Settings, n: int, row: tuple[str, dict[str, Any]], lean: bool):
    request, phrases = build(row[0], lean=lean)
    assert author.precheck(request) is None, request
    return request, go(
        db, cfg, fake(answer("ready", row[1], phrases=phrases)), request, account=f"u{n}"
    )


def test_the_table_has_forty_five_requests() -> None:
    assert (len(ORDINARY), len(NEVER_READY)) == (37, 8)


@pytest.mark.parametrize("lean", [True, False], ids=["lean", "full"])
def test_ordinary_requests_reach_ready_with_a_correct_model_answer(
    db: Database, cfg: Settings, lean: bool
) -> None:
    not_ready: list[tuple[str, str, list[str]]] = []
    for n, row in enumerate(ORDINARY):
        request, r = run_row(db, cfg, n, row, lean)
        if r.status != "ready":
            not_ready.append((request, r.status, r.notes))
    assert not_ready == []


@pytest.mark.parametrize("lean", [True, False], ids=["lean", "full"])
def test_a_dropped_negation_or_dropped_currency_is_never_ready(
    db: Database, cfg: Settings, lean: bool
) -> None:
    ready: list[str] = []
    for n, row in enumerate(NEVER_READY, start=100):
        request, r = run_row(db, cfg, n, row, lean)
        if r.status == "ready":
            ready.append(request)
    assert ready == []


def test_a_comparison_that_is_not_a_negation_does_not_carry_the_word_not() -> None:
    def one(spec: dict[str, Any]) -> Any:
        return parse_spec(spec)[0]

    gt, ne, gone = (
        one(t1_spec("invoice.total", "gt", "5")),
        one(t1_spec("invoice.payment_status", "ne", "paid")),
        one(miss(PO)),
    )
    text, mapped = "flag if the invoice total is not over 5", ["invoice total", "over", "5"]
    assert author.uncovered_words(text, mapped, [gt]) == ["not"]
    assert author.uncovered_words(text, mapped, [ne]) == []
    assert author.uncovered_words(text, mapped, [gone]) == []
    assert author.uncovered_words(text, mapped) == ["not"]
    # a short word of a field the rule reads is covered by the field; another one is not
    assert author.uncovered_words("flag if the PO is missing", ["missing"], [gone]) == []
    assert author.uncovered_words("flag if the UEN is missing", ["missing"], [gone]) == ["uen"]


# ================================================================= 3. what counts as a currency


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("flag invoices in USD above 10000", ["USD"]),
        ("flag invoices over SGD 100000", ["SGD"]),
        ("flag invoices over USD 5,000", ["USD"]),
        ("flag invoices over 50000 SGD", ["SGD"]),
        ("flag invoices over 50000SGD", ["SGD"]),
        ("flag invoices over USD100000", ["USD"]),
        ("flag if the currency is SGD", ["SGD"]),
        ("flag if the currency JPY", ["JPY"]),
        ("flag invoices over 1000 dollars", ["dollars"]),
        ("flag invoices over 1000 Singapore dollars", ["dollars"]),
        ("flag invoices in Singapore dollars", ["dollars"]),
        ("flag if the currency is British pound", ["pound"]),
        ("flag invoices over 100000 yen", ["yen"]),
        ("flag invoices over RM 50000", ["rm"]),
        ("flag invoices over $50,000", ["$"]),
        ("flag invoices over €500", ["€"]),
        ("flag invoices in USD or over 5 euros", ["USD", "euros"]),
    ],
)
def test_a_currency_next_to_a_figure_or_after_in_is_a_currency(text: str, named: list[str]) -> None:
    assert author.currencies_named(text) == named


@pytest.mark.parametrize(
    "text",
    [
        "flag invoices where a SAR was filed",
        "flag if a suspicious activity report (SAR) exists",
        "flag if a CAD file is attached",
        "flag if the PHP or SEK note is there",
        "flag invoices from Euros Trading Pte Ltd",
        "flag invoices in Euros Trading Pte Ltd",
        "flag invoices from Euros Holdings over 5000",
        "flag invoices where the dollar amount is over 10000",
        "flag invoices with a large dollar value",
        "flag invoices over 5000",
    ],
)
def test_words_that_only_look_like_a_currency_are_not_one(text: str) -> None:
    assert author.currencies_named(text) == []


def test_a_suspicious_activity_report_request_is_not_cut_back(db: Database, cfg: Settings) -> None:
    raw = answer(
        "ready",
        t1_spec("invoice.total", "gt", "5000"),
        phrases=[
            ("flag", "filler"),
            ("invoices", "doc_type"),
            ("total", "field"),
            ("over", "operator"),
            ("5000", "value"),
            ("SAR", "filler"),
        ],
    )
    r: DraftResponse = go(db, cfg, fake(raw), "flag invoices over 5000 that have a SAR")
    assert r.status == "ready" and r.unsupported_parts == []


# ================================================================= 4. the same tokens on both sides


def test_a_code_with_digits_is_split_the_same_way_on_both_sides() -> None:
    text = "flag invoices with PO123 as the reference"
    assert author.uncovered_words(text, ["flag", "invoices", "with PO123", "reference"]) == []
    assert author.uncovered_words(text, ["flag", "invoices", "with PO 123", "reference"]) == []
    assert author.uncovered_words(text, ["flag", "invoices", "with 123", "reference"]) == ["po"]
    assert author.uncovered_words("flag when PO123 is missing", ["missing"]) == ["po"]


# ================================================================= 5. ages and scores in any word order


@pytest.mark.parametrize(
    "text",
    [
        "flag a balance of customers above 60",
        "flag the total of directors over 65",
        "flag customers above 60",
        "flag directors over 65",
        "flag above 60 customers",
        "flag customers under 21",
        "flag an owner older than 70",
        "flag applicants over 18 years",
        "flag the amount of owners below 25",
    ],
)
def test_an_age_is_refused_in_either_word_order(text: str) -> None:
    refusal = author.precheck(text)
    assert refusal is not None and refusal.reason == "protected_attribute", text


@pytest.mark.parametrize(
    "text",
    [
        "Flag an invoice whose total is over 500 for a customer",
        "flag invoices with a total over 500 for a customer",
        "flag when the customer balance is over 500",
        "flag a customer total over 65",
        "purchase order with total under 100 from the owner",
        "flag a receipt where the amount is above 20 for a shareholder",
        "flag when the balance is under 10 and set it to medium",
        "Flag when interest coverage is below 1.5, set it to low",
        "flag invoices in Australian dollars above 10,000",
        "flag if the currency is British pound",
        "Flag minor rounding differences",
    ],
)
def test_an_amount_next_to_a_person_word_is_still_let_through(text: str) -> None:
    assert author.precheck(text) is None, text


@pytest.mark.parametrize(
    "text",
    [
        "set to zero score for big invoices",
        "set it to zero score",
        "score set to 0 for big invoices",
        "flag big invoices, the score is set to 0",
        "set the score to 0 for big invoices",
        "set coverage to 100 for big invoices",
        "set the points to 5",
    ],
)
def test_setting_the_score_is_refused_in_either_word_order(text: str) -> None:
    refusal = author.precheck(text)
    assert refusal is not None and refusal.reason == "changes_score", text


@pytest.mark.parametrize(
    "text",
    [
        "flag invoices over 5000 and set it to low",
        "flag invoices over 5000, set the severity to medium",
        "flag if the credit score is lower than 500",
        "flag invoices over 5000, set it to low severity",
    ],
)
def test_setting_the_severity_is_not_setting_the_score(text: str) -> None:
    assert author.precheck(text) is None, text
