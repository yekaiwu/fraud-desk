"""Duplicate checks (app/custom_rules/duplicates.py): built-ins, own, public and adopted rules."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.checks.registry import all_checks
from app.core.db import Database
from app.core.models import RuleOverride
from app.custom_rules import duplicates as dup
from app.custom_rules import store
from app.custom_rules.readback import readback
from app.custom_rules.spec import CustomRuleSpec
from tests.custom_rules_helpers import DEBT, EQUITY, spec_of, t1, t2, t3, t4

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def save(
    db: Database, owner: str, spec: CustomRuleSpec, name: str = "A rule", publish: bool = False
):  # noqa: ANN201
    rule = store.create_rule(
        db,
        owner=owner,
        name=name,
        description="",
        spec=spec,
        readback=readback(spec),
        reason="because",
        at=NOW,
        draft_id=None,
    )
    if publish:
        store.publish(db, rule.id, actor=owner, reason="share it", at=NOW)
    return rule


def override(severity: str) -> RuleOverride:
    return RuleOverride(
        severity=severity, updated_by="x", updated_at="t", reason="reason", revision=1
    )  # type: ignore[arg-type]


# ------------------------------------------------------------------ built-ins


def test_every_footprint_names_a_registered_check() -> None:
    registered = {m.rule for m in all_checks()}
    assert {fp.rule for fp in dup.BUILTIN_FOOTPRINTS} <= registered
    assert {
        "rcpt_merchant",
        "rcpt_dates",
        "inv_dates",
        "date_valid",
        "bs_dates",
        "fs_balance",
        "inv_ids",
        "fs_entity",
        "x_entity",
    } <= {fp.rule for fp in dup.BUILTIN_FOOTPRINTS}


def test_every_footprint_path_is_addressable_and_matches_its_template() -> None:
    from app.custom_rules.spec import addressable_fields, fs_lines

    for fp in dup.BUILTIN_FOOTPRINTS:
        for path in fp.paths:
            assert path in addressable_fields() or path in fs_lines(), (fp.rule, path)
        assert fp.template in ("field_missing", "date_window", "fs_ratio")


@pytest.mark.parametrize(
    ("condition", "rule"),
    [
        (t3("receipt.merchant_name"), "rcpt_merchant"),
        (t2("receipt.receipt_date", "in_future"), "rcpt_dates"),
        (
            t2("receipt.receipt_date", "newer_than", 0),
            "rcpt_dates",
        ),  # later than the case date is "in the future"
        (t2("invoice.invoice_date", "in_future"), "inv_dates"),
        (t2("passport.date_of_issue", "in_future"), "date_valid"),
        (t2("national_id.date_of_issue", "in_future"), "date_valid"),
    ],
)
def test_a_rule_that_does_what_a_builtin_does_is_identical(condition: dict, rule: str) -> None:
    notes = dup.builtin_duplicates(spec_of(condition), {})
    identical = [n for n in notes if n.level == "identical"]
    assert [n.rule for n in identical] == [rule]
    note = identical[0]
    assert note.kind == "builtin" and note.what
    assert f"already checked by {rule}" in note.action
    assert f"Check catalogue > {rule}" in note.action
    assert dup.blocking(notes) == note


def test_the_note_names_the_effective_severity_from_the_catalogue() -> None:
    default = next(m for m in all_checks() if m.rule == "rcpt_merchant").severity
    spec = spec_of(t3("receipt.merchant_name"))
    assert f"flags at {default} now" in dup.builtin_duplicates(spec, {})[0].action
    assert (
        "flags at high now"
        in dup.builtin_duplicates(spec, {"rcpt_merchant": override("high")})[0].action
    )


def test_same_fields_with_another_relation_is_only_similar() -> None:
    notes = dup.builtin_duplicates(spec_of(t2("receipt.receipt_date", "older_than", 3650)), {})
    assert [(n.rule, n.level) for n in notes] == [("rcpt_dates", "similar")]
    assert dup.blocking(notes) is None


def named(spec: CustomRuleSpec) -> list[tuple[str, str]]:
    """The built-in notes of a spec as (rule, level), leaving out the generic required-field note."""
    return [
        (n.rule, n.level) for n in dup.builtin_duplicates(spec, {}) if n.rule != "required_field"
    ]


def test_non_exact_footprints_are_warnings_never_blockers() -> None:
    assert named(spec_of(t2("bank_statement.period_end", "in_future"))) == [("bs_dates", "similar")]
    assert named(spec_of(t3("invoice.supplier_uen"))) == [("inv_ids", "similar")]
    assert named(spec_of(t3("invoice.supplier_gst_no"))) == [("inv_ids", "similar")]
    fs = spec_of(
        t4(["balance_sheet.total_liabilities", EQUITY], "balance_sheet.total_assets", "lt", "1")
    )
    assert named(fs) == [("fs_balance", "similar")]
    assert sorted(named(spec_of(t3("financial_statement.uen")))) == [
        ("fs_entity", "similar"),
        ("x_entity", "similar"),
    ]
    assert named(spec_of(t3("company_profile.company_name"))) == [("x_entity", "similar")]
    assert dup.blocking(dup.builtin_duplicates(spec_of(t3("financial_statement.uen")), {})) is None


def test_a_required_field_gets_the_read_only_in_part_note() -> None:
    notes = dup.builtin_duplicates(spec_of(t3("invoice.buyer_name")), {})  # required in the schema
    assert [n.rule for n in notes] == ["required_field"]
    assert "read only in part" in notes[0].what and notes[0].level == "similar"
    assert (
        dup.builtin_duplicates(spec_of(t3("invoice.due_date")), {}) == []
    )  # optional: nothing to say


def test_unrelated_rules_have_no_builtin_duplicate() -> None:
    assert dup.builtin_duplicates(spec_of(t1("invoice.total", "gt", "100000")), {}) == []
    assert dup.builtin_duplicates(spec_of(t4(DEBT, EQUITY)), {}) == []
    assert dup.builtin_duplicates(spec_of(t3("receipt.receipt_number")), {}) == []


def test_relation_of() -> None:
    cases = [
        (t1("invoice.total", "gt", "1"), "upper"),
        (t1("invoice.total", "gte", "1"), "upper"),
        (t1("invoice.total", "lt", "1"), "lower"),
        (t1("invoice.total", "lte", "1"), "lower"),
        (t1("invoice.total", "eq", "1"), "equal"),
        (t1("invoice.total", "ne", "1"), "not_equal"),
        (t1("invoice.payment_status", "in", None, values=["paid"]), "equal"),
        (t1("invoice.payment_status", "not_in", None, values=["paid"]), "not_equal"),
        (t2("invoice.invoice_date", "older_than", 5), "older_than"),
        (t2("invoice.invoice_date", "newer_than", 5), "newer_than"),
        (t2("invoice.invoice_date", "newer_than", 0), "in_future"),
        (t2("invoice.invoice_date", "in_future"), "in_future"),
        (t3("invoice.due_date"), "missing"),
        (t4(DEBT, EQUITY, "gte"), "upper"),
        (t4(DEBT, EQUITY, "lt"), "lower"),
    ]
    for condition, expected in cases:
        assert dup.relation_of(spec_of(condition)) == expected, condition


# ------------------------------------------------------------------ custom rules


def test_identical_own_rule_is_refused_with_a_pointer(mem_db: Database) -> None:
    rule = save(
        mem_db, "alice", spec_of(t1("invoice.total", "gt", "100000"), "low"), name="Big invoices"
    )
    # same condition, other severity and spelling: still the same rule
    again = spec_of(t1("invoice.total", "gt", "100000.00"), "medium")
    notes = dup.custom_duplicates(mem_db, again, viewer="alice")
    assert [(n.kind, n.level, n.rule, n.name) for n in notes] == [
        ("custom", "identical", rule.id, "Big invoices")
    ]
    assert notes[0].action == "Change the severity of that rule instead."
    assert notes[0].what == readback(spec_of(t1("invoice.total", "gt", "100000")))
    assert dup.blocking(dup.find_duplicates(mem_db, again, viewer="alice")) is not None


def test_editing_a_rule_is_not_a_duplicate_of_itself(mem_db: Database) -> None:
    rule = save(mem_db, "alice", spec_of(t1("invoice.total", "gt", "5")))
    same = spec_of(t1("invoice.total", "gt", "5"))
    assert dup.custom_duplicates(mem_db, same, viewer="alice", exclude_rule=rule.id) == []


def test_same_template_and_fields_with_another_limit_is_similar(mem_db: Database) -> None:
    rule = save(mem_db, "alice", spec_of(t1("invoice.total", "gt", "100000")))
    notes = dup.custom_duplicates(mem_db, spec_of(t1("invoice.total", "gt", "5")), viewer="alice")
    assert [(n.level, n.rule) for n in notes] == [("similar", rule.id)]
    assert dup.blocking(notes) is None
    other_field = dup.custom_duplicates(
        mem_db, spec_of(t1("invoice.subtotal", "gt", "100000")), viewer="alice"
    )
    assert other_field == []


def test_public_rules_are_visible_duplicates_with_the_right_pointer(mem_db: Database) -> None:
    rule = save(
        mem_db, "alice", spec_of(t1("invoice.total", "gt", "100000")), name="Big", publish=True
    )
    spec = spec_of(t1("invoice.total", "gt", "100000"))
    first = dup.custom_duplicates(mem_db, spec, viewer="bob")
    assert [(n.level, n.rule, n.action) for n in first] == [
        ("identical", rule.id, "Adopt it from the public rules.")
    ]
    store.adopt(mem_db, rule.id, "bob", at=NOW)
    second = dup.custom_duplicates(mem_db, spec, viewer="bob")
    assert [(n.level, n.action) for n in second] == [("identical", "You already adopted it.")]


def test_an_adopted_pinned_version_counts_even_after_the_owner_moved_on(mem_db: Database) -> None:
    rule = save(mem_db, "alice", spec_of(t1("invoice.total", "gt", "100000")), publish=True)
    store.adopt(mem_db, rule.id, "bob", at=NOW)
    store.add_version(
        mem_db,
        rule.id,
        actor="alice",
        spec=spec_of(t1("invoice.total", "gt", "5")),
        readback="r",
        reason="edit it",
        at=NOW,
        draft_id=None,
    )
    store.publish(mem_db, rule.id, actor="alice", reason="v2", at=NOW)
    old = dup.custom_duplicates(mem_db, spec_of(t1("invoice.total", "gt", "100000")), viewer="bob")
    assert [(n.level, n.action) for n in old] == [("identical", "You already adopted it.")]
    new = dup.custom_duplicates(mem_db, spec_of(t1("invoice.total", "gt", "5")), viewer="bob")
    assert [(n.level, n.action) for n in new] == [("identical", "Adopt it from the public rules.")]


def test_another_accounts_private_rule_is_never_reported(mem_db: Database) -> None:
    save(mem_db, "alice", spec_of(t1("invoice.total", "gt", "100000")), name="Secret idea")
    identical = dup.custom_duplicates(
        mem_db, spec_of(t1("invoice.total", "gt", "100000")), viewer="bob"
    )
    similar = dup.custom_duplicates(mem_db, spec_of(t1("invoice.total", "gt", "7")), viewer="bob")
    assert identical == [] and similar == []
    assert (
        dup.blocking(
            dup.find_duplicates(mem_db, spec_of(t1("invoice.total", "gt", "100000")), viewer="bob")
        )
        is None
    )


def test_retired_and_unpublished_rules_are_not_duplicates(mem_db: Database) -> None:
    gone = save(mem_db, "alice", spec_of(t1("invoice.total", "gt", "100000")))
    store.retire(mem_db, gone.id, actor="alice", reason="obsolete now", at=NOW)
    shared = save(mem_db, "alice", spec_of(t1("invoice.subtotal", "gt", "100000")), publish=True)
    store.unpublish(mem_db, shared.id, actor="alice", reason="private now", at=NOW, as_admin=False)
    for viewer in ("alice", "bob"):
        assert (
            dup.custom_duplicates(
                mem_db, spec_of(t1("invoice.total", "gt", "100000")), viewer=viewer
            )
            == []
        )
    assert (
        dup.custom_duplicates(mem_db, spec_of(t1("invoice.subtotal", "gt", "100000")), viewer="bob")
        == []
    )
    assert [
        n.level
        for n in dup.custom_duplicates(
            mem_db, spec_of(t1("invoice.subtotal", "gt", "100000")), viewer="alice"
        )
    ] == ["identical"]


def test_find_duplicates_lists_builtins_first_and_uses_the_catalogue_overrides(
    mem_db: Database,
) -> None:
    spec = spec_of(t3("receipt.merchant_name"))
    notes = dup.find_duplicates(mem_db, spec, viewer="alice")
    assert [(n.kind, n.rule) for n in notes][:1] == [("builtin", "rcpt_merchant")]
    mem_db.write_rule_override(
        "rcpt_merchant",
        {"enabled": True, "severity": "medium"},
        reviewer="x",
        reason="because",
        at=NOW,
    )
    assert "flags at medium now" in dup.find_duplicates(mem_db, spec, viewer="alice")[0].action


def test_note_text_is_plain_and_bounded(mem_db: Database) -> None:
    save(mem_db, "alice", spec_of(t1("invoice.total", "gt", "1")), name="x‮" + "y" * 200)
    note = dup.custom_duplicates(mem_db, spec_of(t1("invoice.total", "gt", "1")), viewer="alice")[0]
    assert note.name is not None and "‮" not in note.name and len(note.name) <= 80


# ------------------------------------------------------------------ related built-ins


def test_related_builtins_for_financial_statements() -> None:
    ideas = dup.related_builtins(["financial_statement"])
    assert 1 <= len(ideas) <= 5
    assert all(i.kind == "related_builtin" and i.rule and i.text for i in ideas)
    rules = [i.rule for i in ideas]
    assert "fs_balance" in rules and "fs_entity" in rules
    registered = {m.rule: m for m in all_checks()}
    assert all("financial_statement" in registered[r].applies_to for r in rules if r)  # type: ignore[index]


def test_related_builtins_edge_cases() -> None:
    assert dup.related_builtins([]) == []
    assert dup.related_builtins(["boilerplate"]) == []
    assert len(dup.related_builtins(["invoice", "receipt"], limit=2)) == 2
    first = dup.related_builtins(["invoice"])
    assert first == dup.related_builtins(["invoice"])  # stable order
