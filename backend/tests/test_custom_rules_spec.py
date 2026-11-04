"""The shared custom-rule spec (app/custom_rules/spec.py): templates, operators, ranges, hashing."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.schemas import DOC_SCHEMAS, EXTRACTABLE_TYPES, FS_LINES
from app.custom_rules import spec as sp
from tests.custom_rules_helpers import DEBT, EQUITY, spec_of, t1, t2, t3, t4


def problems(condition: dict[str, Any], severity: str = "low") -> list[str]:
    got, found = sp.parse_spec({"spec_version": 1, "severity": severity, "condition": condition})
    assert got is None
    assert found
    return [p.message for p in found]


# ------------------------------------------------------------------ valid specs


@pytest.mark.parametrize(
    "condition",
    [
        t1("invoice.total", "gt", "100000"),
        t1("invoice.total", "lte", "0.5"),
        t1("invoice.gst_rate", "eq", "9"),
        t1("shareholder_register.total_shares", "gte", "1000"),
        t1("invoice.invoice_date", "lt", "2025-01-31"),
        t1("invoice.payment_status", "eq", "unpaid"),
        t1("invoice.payment_status", "in", None, values=["unpaid", "partial"]),
        t1("invoice.payment_status", "not_in", None, values=["paid"]),
        t1("invoice.currency", "ne", "SGD"),
        t2("passport.date_of_expiry", "older_than", 0),
        t2("invoice.invoice_date", "older_than", 90),
        t2("invoice.invoice_date", "newer_than", 30),
        t2("invoice.invoice_date", "in_future"),
        t3("receipt.receipt_number"),
        t3("invoice.buyer_name"),
        t4(DEBT, EQUITY),
        t4(["income_statement.revenue"], "balance_sheet.total_assets", "lt", "0.1", "prior"),
    ],
)
def test_valid_conditions_parse_and_round_trip(condition: dict[str, Any]) -> None:
    spec = spec_of(condition)
    again, found = sp.parse_spec(json.loads(spec.model_dump_json()))
    assert found == [] and again == spec
    assert spec.spec_hash() == again.spec_hash()
    assert spec.doc_types()
    assert spec.template == condition["template"]


def test_doc_types_and_paths() -> None:
    assert spec_of(t1("invoice.total", "gt", "1")).doc_types() == ("invoice",)
    t4_spec = spec_of(t4(DEBT, EQUITY))
    assert t4_spec.doc_types() == ("financial_statement",)
    assert set(t4_spec.paths()) == {*DEBT, EQUITY}


# ------------------------------------------------------------------ closed schema


def test_unknown_field_is_not_addressable() -> None:
    assert "not a field" in problems(t1("invoice.lender_name", "eq", "x"))[0]
    assert "not a field" in problems(t3("invoice.nothing"))[0]


def test_unknown_document_type_is_not_addressable() -> None:
    assert problems(t1("tax_return.total", "gt", "1"))


@pytest.mark.parametrize(
    "path",
    [
        "passport.nationality",
        "passport.sex",
        "passport.date_of_birth",
        "passport.holder_name",
        "passport.surname",
        "passport.given_names",
        "passport.mrz_line1",
        "national_id.holder_name",
        "national_id.address",
    ],
)
@pytest.mark.parametrize("make", [lambda p: t3(p), lambda p: t1(p, "eq", "x")])
def test_protected_attributes_are_refused_by_every_template(path: str, make: Any) -> None:
    msg = problems(make(path))[0]
    assert "protected attribute" in msg


def test_protected_paths_are_not_addressable_at_all() -> None:
    fields = sp.addressable_fields()
    assert not [p for p in fields if p.partition(".")[2] in sp.PROTECTED_FIELD_NAMES]
    assert sp.is_protected_path("passport.nationality")
    assert not sp.is_protected_path("invoice.total")
    assert sp.is_protected_path("anything.date_of_birth")


def test_addressable_fields_come_from_the_schemas() -> None:
    fields = sp.addressable_fields()
    expected = {
        f"{dt}.{name}"
        for dt in EXTRACTABLE_TYPES
        for name in DOC_SCHEMAS[dt].fields
        if name not in sp.PROTECTED_FIELD_NAMES
    }
    assert set(fields) == expected
    assert set(sp.fs_lines()) == {
        f"{t}.{ln}"
        for t in sp.FS_RULE_TABLES
        for ln in FS_LINES[t]
        if ln not in sp.FS_EXCLUDED_LINES
    }


# ------------------------------------------------------------------ operators suit the field kind


@pytest.mark.parametrize(
    ("field", "operator"),
    [
        ("invoice.supplier_name", "gt"),  # no "greater than" on a name
        ("invoice.invoice_number", "eq"),  # ids cannot be compared either
        ("invoice.payment_status", "gt"),
        ("invoice.total", "in"),
        ("invoice.invoice_date", "not_in"),
    ],
)
def test_operator_must_suit_the_kind(field: str, operator: str) -> None:
    kw = {"values": ["x"]} if operator in sp.LIST_OPS else {}
    value = None if kw else "1"
    assert problems(t1(field, operator, value, **kw))


def test_text_and_id_fields_can_only_be_checked_for_missing() -> None:
    assert (
        "only be checked for being missing" in problems(t1("invoice.supplier_name", "eq", "x"))[0]
    )
    assert spec_of(t3("invoice.supplier_name")).template == "field_missing"


def test_every_kind_has_the_operator_table_it_should() -> None:
    for info in sp.addressable_fields().values():
        if info.kind in ("text", "id", "bool"):
            assert info.operators == ()
        elif info.kind == "enum":
            assert info.operators == ("eq", "ne", "in", "not_in")
        else:
            assert info.operators == sp.COMPARE_OPS


# ------------------------------------------------------------------ number, date and enum ranges


@pytest.mark.parametrize(
    "value", ["abc", "1e5", "1,000", "10.12345", "", "  5", "5 ", "+5", "1000000000000001", "--1"]
)
def test_numbers_must_be_plain_and_inside_the_range(value: str) -> None:
    assert problems(t1("invoice.total", "gt", value))


@pytest.mark.parametrize("value", ["0", "-5", "12.5", "1000000000000000", "-1000000000000000"])
def test_numbers_at_the_edges_are_fine(value: str) -> None:
    assert spec_of(t1("invoice.total", "gt", value))


@pytest.mark.parametrize(
    "value", ["2025-13-01", "25-01-01", "2025/01/01", "1899-12-31", "2101-01-01"]
)
def test_dates_must_be_iso_within_1900_to_2100(value: str) -> None:
    assert problems(t1("invoice.invoice_date", "lt", value))


def test_enum_values_come_from_the_schema_and_currency_is_three_letters() -> None:
    assert "not one of" in problems(t1("invoice.payment_status", "eq", "cancelled"))[0]
    assert "currency" in problems(t1("invoice.currency", "eq", "dollars"))[0]
    assert problems(t1("invoice.payment_status", "in", None, values=["paid", "cancelled"]))


def test_list_operators_need_a_list_and_value_operators_need_a_value() -> None:
    assert problems(t1("invoice.payment_status", "in", "paid"))
    assert problems(t1("invoice.payment_status", "in", None, values=[]))
    assert problems(t1("invoice.payment_status", "eq", None, values=["paid"]))
    assert problems(t1("invoice.payment_status", "eq"))
    assert problems(t1("invoice.payment_status", "in", None, values=["paid", "paid"]))
    assert problems(
        t1("invoice.currency", "in", None, values=[f"{c}{c}{c}" for c in "ABCDEFGHIJKLMNOPQRSTU"])
    )


@pytest.mark.parametrize("days", [-1, 36501, 10**9])
def test_days_are_bounded(days: int) -> None:
    assert problems(t2("invoice.invoice_date", "older_than", days))


def test_days_rules_per_direction() -> None:
    assert problems(t2("invoice.invoice_date", "older_than"))  # needs days
    assert problems(t2("invoice.invoice_date", "newer_than"))
    assert problems(t2("invoice.invoice_date", "in_future", 5))  # takes none
    assert spec_of(t2("invoice.invoice_date", "older_than", 36500))


def test_date_window_needs_a_date_field() -> None:
    assert "not a date" in problems(t2("invoice.total", "older_than", 5))[0]


def test_ratio_multiplier_range_and_lines() -> None:
    for bad in ("0", "-1", "1001", "abc", "1e2", "0.00001"):
        assert problems(t4(DEBT, EQUITY, multiplier=bad)), bad
    assert spec_of(t4(DEBT, EQUITY, multiplier="1000"))
    assert spec_of(t4(DEBT, EQUITY, multiplier="0.0001"))
    assert problems(t4([], EQUITY))
    assert problems(
        t4(
            [
                *DEBT,
                "balance_sheet.trade_payables",
                "balance_sheet.inventories",
                "balance_sheet.share_capital",
            ],
            EQUITY,
        )
    )
    assert "repeats" in problems(t4([DEBT[0], DEBT[0]], EQUITY))[0]
    assert "also on the left" in problems(t4([EQUITY], EQUITY))[0]
    assert problems(t4(["balance_sheet.lenders"], EQUITY))
    assert problems(t4(["cash_flow.capex"], EQUITY))  # cash flow is not addressable
    assert problems(t4(["balance_sheet.other"], EQUITY))  # the catch-all line is not addressable
    assert problems(t4(DEBT, EQUITY, operator="eq"))  # only gt gte lt lte


# ------------------------------------------------------------------ severity policy


@pytest.mark.parametrize("severity", ["high", "critical", "", "HIGH"])
def test_high_and_unknown_severities_are_rejected(severity: str) -> None:
    got, found = sp.parse_spec(
        {"spec_version": 1, "severity": severity, "condition": t3("receipt.receipt_number")}
    )
    assert got is None and found


@pytest.mark.parametrize("severity", ["info", "low", "medium"])
def test_allowed_severities(severity: str) -> None:
    assert spec_of(t3("receipt.receipt_number"), severity).severity == severity


def test_cap_custom_severity_never_returns_high() -> None:
    assert sp.cap_custom_severity("high") == "medium"
    assert sp.cap_custom_severity("medium") == "medium"
    assert sp.cap_custom_severity("info") == "info"
    assert sp.cap_custom_severity("nonsense") == "low"
    assert sp.CUSTOM_MAX_SEVERITY == "medium"


def test_rule_ids_never_collide_with_a_builtin() -> None:
    from app.checks.registry import all_checks

    builtin = {m.rule for m in all_checks()}
    assert not [r for r in builtin if sp.is_custom_rule(r)]
    for _ in range(50):
        rule_id = sp.new_rule_id()
        assert sp.RULE_ID_RE.match(rule_id) and sp.is_custom_rule(rule_id)
        assert rule_id not in builtin


# ------------------------------------------------------------------ strictness


def test_extra_keys_and_loose_types_are_rejected() -> None:
    base = t1("invoice.total", "gt", "5")
    assert problems({**base, "code": "import os"})
    assert problems(t1("invoice.total", "gt", 5))  # type: ignore[arg-type]  # a number, not a string
    assert problems(t2("invoice.invoice_date", "older_than", "30"))  # type: ignore[arg-type]
    assert problems({**base, "template": "python"})
    got, found = sp.parse_spec({"spec_version": 2, "severity": "low", "condition": base})
    assert got is None and found
    got, found = sp.parse_spec({"spec_version": 1, "severity": "low", "condition": base, "x": 1})
    assert got is None and found


@pytest.mark.parametrize("raw", [None, 5, "text", [], {}, {"condition": 1}, object()])
def test_parse_spec_never_raises(raw: object) -> None:
    got, found = sp.parse_spec(raw)
    assert got is None
    if raw is not None:
        assert found


def test_parse_spec_limits_the_number_of_problems() -> None:
    _, found = sp.parse_spec({"spec_version": 9, "severity": "x", "condition": {"template": "?"}})
    assert 1 <= len(found) <= 5


# ------------------------------------------------------------------ canonical form and hashes


def test_numbers_are_canonicalised() -> None:
    a = spec_of(t1("invoice.total", "gt", "100000"))
    b = spec_of(t1("invoice.total", "gt", "100000.00"))
    c = spec_of(t1("invoice.total", "gt", "100000.0"))
    assert a.spec_hash() == b.spec_hash() == c.spec_hash()
    assert a.condition_hash() == b.condition_hash()
    assert sp.normal_number("-0") == "0" and sp.normal_number("2.50") == "2.5"
    assert sp.normal_number("100") == "100" and sp.normal_number("1000") == "1000"


def test_ratio_hash_ignores_left_order_and_multiplier_spelling() -> None:
    a = spec_of(t4(DEBT, EQUITY, multiplier="2"))
    b = spec_of(t4(list(reversed(DEBT)), EQUITY, multiplier="2.0"))
    assert a.spec_hash() == b.spec_hash()
    assert a.spec_hash() != spec_of(t4(DEBT, EQUITY, multiplier="3")).spec_hash()
    assert a.spec_hash() != spec_of(t4(DEBT, EQUITY, column="prior")).spec_hash()


def test_list_values_are_sorted_in_the_hash() -> None:
    a = spec_of(t1("invoice.payment_status", "in", None, values=["unpaid", "partial"]))
    b = spec_of(t1("invoice.payment_status", "in", None, values=["partial", "unpaid"]))
    assert a.spec_hash() == b.spec_hash()


def test_condition_hash_ignores_severity_but_spec_hash_does_not() -> None:
    low = spec_of(t1("invoice.total", "gt", "5"), "low")
    medium = spec_of(t1("invoice.total", "gt", "5"), "medium")
    assert low.condition_hash() == medium.condition_hash()
    assert low.spec_hash() != medium.spec_hash()


def test_a_different_condition_changes_both_hashes() -> None:
    a = spec_of(t1("invoice.total", "gt", "5"))
    assert a.condition_hash() != spec_of(t1("invoice.total", "gte", "5")).condition_hash()
    assert a.condition_hash() != spec_of(t1("invoice.subtotal", "gt", "5")).condition_hash()
    assert a.condition_hash() != spec_of(t1("invoice.total", "gt", "6")).condition_hash()


def test_specs_are_immutable() -> None:
    spec = spec_of(t3("receipt.receipt_number"))
    with pytest.raises(Exception):  # noqa: B017, PT011 - pydantic's frozen error
        spec.severity = "medium"  # type: ignore[misc]


# ------------------------------------------------------------------ the draft models


def test_question_needs_two_options_and_a_default_that_is_one_of_them() -> None:
    opt = [
        {"id": "a", "label": "A", "value": "1"},
        {"id": "b", "label": "B", "value": "2"},
    ]
    q = sp.Question.model_validate(
        {"id": "q1", "slot": "multiplier", "text": "?", "options": opt, "default": "a"}
    )
    assert q.default == "a"
    with pytest.raises(Exception):  # noqa: B017, PT011
        sp.Question.model_validate(
            {"id": "q1", "slot": "multiplier", "text": "?", "options": opt, "default": "zz"}
        )
    with pytest.raises(Exception):  # noqa: B017, PT011
        sp.Question.model_validate(
            {"id": "q1", "slot": "multiplier", "text": "?", "options": opt[:1]}
        )
    with pytest.raises(Exception):  # noqa: B017, PT011
        sp.Question.model_validate(
            {"id": "q1", "slot": "multiplier", "text": "?", "options": [opt[0], opt[0]]}
        )


def test_model_draft_keeps_specs_raw_so_one_bad_spec_does_not_lose_the_answer() -> None:
    draft = sp.ModelDraft.model_validate(
        {"status": "ready", "understood": "x", "spec": {"condition": "garbage"}}
    )
    assert sp.parse_spec(draft.spec)[0] is None


def test_every_unsupported_and_refusal_reason_has_text() -> None:
    assert set(sp.UNSUPPORTED_REASON_TEXT) == set(sp.UNSUPPORTED_REASONS)
    assert set(sp.REFUSAL_TEXT) == set(sp.REFUSAL_REASONS)
    assert set(sp.TEMPLATE_TEXT) == set(sp.TEMPLATES)
    assert set(sp.OPERATOR_TEXT) >= set(sp.COMPARE_OPS) | set(sp.LIST_OPS)


def test_draft_limits_match_the_design() -> None:
    assert sp.DRAFT_MAX_TURNS == 6 and sp.MODEL_CALLS_PER_TURN == 2
    assert sp.REQUEST_MAX_CHARS == 1000 and sp.DRY_RUN_MAX_FIRE_RATE == 0.30
    assert (sp.RULE_NAME_MIN, sp.RULE_NAME_MAX, sp.RULE_DESCRIPTION_MAX) == (3, 80, 400)
