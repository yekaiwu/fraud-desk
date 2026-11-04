"""The custom-rule engine (app/custom_rules/engine.py): four templates, pure, total, never an error."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.core.models import Cell, StatementLink
from app.core.testing import FIXED_TODAY, make_doc
from app.custom_rules import engine
from app.custom_rules.engine import (
    CUSTOM_FLAG_CAP,
    SKIP_FAILED,
    SKIP_FIELD_MISSING,
    SKIP_NO_DOCUMENT,
    evaluate,
    run_custom_rules,
)
from tests.custom_rules_helpers import (
    BOX,
    DEBT,
    EQUITY,
    ctx_with,
    invoice,
    loaded,
    scan_file,
    statement_doc,
    t1,
    t2,
    t3,
    t4,
    with_cell,
)
from tests.test_no_wallclock_in_checks import wallclock_calls

TODAY = FIXED_TODAY  # 2026-10-27


def run(rule, *docs, files=()):  # noqa: ANN001, ANN201
    ctx = ctx_with(list(docs), rule)
    ctx.case.files = list(files)
    return evaluate(rule, ctx.case, today=TODAY)


# ------------------------------------------------------------------ T1 field vs constant


@pytest.mark.parametrize(
    ("operator", "limit", "total", "fires"),
    [
        ("gt", "100000", "100000.01", True),
        ("gt", "100000", "100000.00", False),
        ("gte", "100000", "100000.00", True),
        ("gte", "100000", "99999.99", False),
        ("lt", "100", "99.99", True),
        ("lt", "100", "100.00", False),
        ("lte", "100", "100.00", True),
        ("lte", "100", "100.01", False),
        ("eq", "100", "100.00", True),
        ("eq", "100", "100.01", False),
        ("ne", "100", "100.01", True),
        ("ne", "100", "100", False),
    ],
)
def test_t1_numeric_operators(operator: str, limit: str, total: str, fires: bool) -> None:
    result = run(loaded(t1("invoice.total", operator, limit)), invoice(total=total))
    assert bool(result.flags) is fires
    assert result.outcomes[0].status == ("fired" if fires else "passed")
    assert result.skip_code is None


def test_t1_evidence_quotes_both_numbers() -> None:
    flag = run(loaded(t1("invoice.total", "gt", "100000")), invoice()).flags[0]
    assert flag.evidence == "invoice total 120000.00 is greater than 100000"
    assert flag.rule == "cus_0123456789" and flag.stage == "rules" and flag.severity == "low"
    assert flag.field == "total" and flag.document_ids == ["d1"]


def test_t1_percent_and_int_kinds() -> None:
    doc = make_doc("invoice", id="d1", fields={"gst_rate": "9.00"})
    assert run(loaded(t1("invoice.gst_rate", "gt", "8")), doc).flags
    assert not run(loaded(t1("invoice.gst_rate", "gt", "9")), doc).flags
    register = make_doc("shareholder_register", id="d1", fields={"total_shares": "2000000"})
    assert run(loaded(t1("shareholder_register.total_shares", "gt", "1500000")), register).flags


def test_t1_dates() -> None:
    rule = loaded(t1("invoice.invoice_date", "lt", "2026-10-02"))
    flag = run(rule, invoice(invoice_date="2026-10-01")).flags[0]
    assert flag.evidence == "invoice date 2026-10-01 is before 2026-10-02"
    assert not run(rule, invoice(invoice_date="2026-10-02")).flags
    assert run(
        loaded(t1("invoice.invoice_date", "eq", "2026-10-02")), invoice(invoice_date="2026-10-02")
    ).flags


@pytest.mark.parametrize(
    ("condition", "status", "fires"),
    [
        (t1("invoice.payment_status", "eq", "unpaid"), "unpaid", True),
        (t1("invoice.payment_status", "eq", "unpaid"), "paid", False),
        (t1("invoice.payment_status", "ne", "paid"), "unpaid", True),
        (t1("invoice.payment_status", "ne", "paid"), "paid", False),
        (t1("invoice.payment_status", "in", None, values=["unpaid", "partial"]), "partial", True),
        (t1("invoice.payment_status", "in", None, values=["unpaid", "partial"]), "paid", False),
        (t1("invoice.payment_status", "not_in", None, values=["paid"]), "unpaid", True),
        (t1("invoice.payment_status", "not_in", None, values=["paid"]), "paid", False),
    ],
)
def test_t1_enum_operators(condition: dict, status: str, fires: bool) -> None:
    result = run(loaded(condition), invoice(payment_status=status))
    assert bool(result.flags) is fires


def test_t1_enum_evidence_and_case_insensitive_currency() -> None:
    rule = loaded(t1("invoice.payment_status", "in", None, values=["partial", "unpaid"]))
    assert run(rule, invoice(payment_status="unpaid")).flags[0].evidence == (
        "invoice payment status 'unpaid' is one of: partial, unpaid"
    )
    currency = loaded(t1("invoice.currency", "ne", "SGD"))
    assert not run(currency, invoice(currency="sgd")).flags
    assert run(currency, invoice(currency="USD")).flags


def test_t1_missing_value_is_skipped_not_flagged() -> None:
    rule = loaded(t1("invoice.total", "gt", "5"))
    result = run(rule, invoice(total=None))
    assert result.flags == [] and result.outcomes[0].status == "missing"
    assert result.skip_code == SKIP_FIELD_MISSING
    unparsable = run(rule, invoice(total="n/a"))
    assert unparsable.outcomes[0].status == "missing"
    bad_date = loaded(t1("invoice.invoice_date", "lt", "2026-01-01"))
    assert run(bad_date, invoice(invoice_date="someday")).outcomes[0].status == "missing"


def test_t1_is_total_over_odd_cell_values() -> None:
    rule = loaded(t1("invoice.total", "gt", "5"))
    for odd in ["", "   ", "NaN", "Infinity", "1e999", "١٢٣", "\x00", "9" * 400]:
        evaluate(rule, ctx_with([invoice(total=odd)]).case, today=TODAY)  # must not raise


# ------------------------------------------------------------------ T2 date window


@pytest.mark.parametrize(
    ("direction", "days", "invoice_date", "fires"),
    [
        ("older_than", 90, "2026-07-27", True),  # 92 days before 2026-10-27
        ("older_than", 90, "2026-07-29", False),  # exactly 90 days is not "more than 90"
        ("older_than", 90, "2026-07-30", False),
        ("older_than", 0, "2026-10-26", True),
        ("older_than", 0, "2026-10-27", False),
        ("newer_than", 30, "2026-10-01", True),
        ("newer_than", 30, "2026-10-28", True),  # later than the case date is newer too
        ("newer_than", 30, "2026-09-28", True),
        ("newer_than", 30, "2026-09-27", False),  # exactly 30 days ago is not newer
        ("newer_than", 30, "2026-09-26", False),
        ("newer_than", 0, "2026-10-28", True),
        ("newer_than", 0, "2026-10-27", False),
        ("in_future", None, "2026-10-28", True),
        ("in_future", None, "2026-10-27", False),
    ],
)
def test_t2_boundaries(direction: str, days: int | None, invoice_date: str, fires: bool) -> None:
    rule = loaded(t2("invoice.invoice_date", direction, days))
    assert bool(run(rule, invoice(invoice_date=invoice_date)).flags) is fires


def test_t2_evidence_counts_days_from_the_case_date_not_the_clock() -> None:
    rule = loaded(t2("invoice.invoice_date", "older_than", 90))
    flag = run(rule, invoice(invoice_date="2026-01-02")).flags[0]
    assert flag.evidence == (
        "invoice date 2026-01-02 is 298 days before the case date 2026-10-27 (more than 90)"
    )
    future = loaded(t2("invoice.invoice_date", "in_future"))
    assert run(future, invoice(invoice_date="2026-10-28")).flags[0].evidence == (
        "invoice date 2026-10-28 is 1 day after the case date 2026-10-27"
    )
    newer = loaded(t2("invoice.invoice_date", "newer_than", 30))
    assert run(newer, invoice(invoice_date="2026-09-20")).flags == []
    assert (
        run(newer, invoice(invoice_date="2026-10-20")).flags[0].evidence.endswith("(less than 30)")
    )


def test_t2_a_different_case_date_changes_the_answer() -> None:
    rule = loaded(t2("invoice.invoice_date", "older_than", 30))
    ctx = ctx_with([invoice(invoice_date="2026-10-01")], rule)
    assert evaluate(rule, ctx.case, today=date(2026, 10, 20)).flags == []
    assert evaluate(rule, ctx.case, today=date(2026, 12, 1)).flags


def test_t2_missing_or_unparsable_date_is_missing() -> None:
    rule = loaded(t2("invoice.invoice_date", "older_than", 30))
    assert run(rule, invoice(invoice_date=None)).outcomes[0].status == "missing"
    assert run(rule, invoice(invoice_date="soon")).outcomes[0].status == "missing"


# ------------------------------------------------------------------ T3 field missing


def test_t3_fires_when_the_field_is_absent_blank_or_empty() -> None:
    rule = loaded(t3("receipt.receipt_number"), "info")
    for value in (None, "", "   "):
        doc = make_doc("receipt", id="d1", fields={"receipt_number": value})
        result = run(rule, doc)
        assert len(result.flags) == 1, repr(value)
        assert result.flags[0].evidence == "receipt number is missing on this receipt"
        assert result.flags[0].severity == "info"
    bare = make_doc("receipt", id="d1")
    del bare.fields["receipt_number"]  # the key itself is absent
    assert len(run(rule, bare).flags) == 1


def test_t3_passes_when_the_field_is_read() -> None:
    doc = make_doc("receipt", id="d1", fields={"receipt_number": "R-1"})
    result = run(loaded(t3("receipt.receipt_number")), doc)
    assert result.flags == [] and result.outcomes[0].status == "passed" and result.skip_code is None


def test_t3_printed_but_not_understood_is_not_missing() -> None:
    doc = make_doc("receipt", id="d1")
    doc.fields["receipt_number"] = Cell(value=None, raw="no. 77?", kind="id")
    assert run(loaded(t3("receipt.receipt_number")), doc).flags == []


def test_t3_on_a_document_read_only_in_part_is_low_and_says_so() -> None:
    doc = make_doc("invoice", id="d1", status="partial", fields={"invoice_number": "1"})
    flag = run(loaded(t3("invoice.buyer_name"), "medium"), doc).flags[0]
    assert flag.severity == "low"
    assert flag.evidence.endswith("; the document was read only in part")


def test_t3_needs_a_document_of_that_type() -> None:
    result = run(loaded(t3("receipt.receipt_number")), invoice())
    assert result.skip_code == SKIP_NO_DOCUMENT and result.flags == [] and result.outcomes == []


# ------------------------------------------------------------------ T4 financial statement line vs line


def test_t4_sums_the_left_lines_and_compares_with_a_multiple_of_the_right() -> None:
    doc = statement_doc(
        borrowings_current="500000.00",
        borrowings_non_current="1200000.00",
        total_equity_sub="600000.00",
    )
    flag = run(loaded(t4(DEBT, EQUITY), "medium"), doc).flags[0]
    assert flag.severity == "medium"
    assert flag.evidence == (
        "borrowings (current) 500000.00 + borrowings (non-current) 1200000.00 = 1700000.00 is "
        "greater than 2 x total equity 600000.00 = 1200000.00 (current year)"
    )
    assert flag.table == "balance_sheet" and flag.row is not None and flag.field is None


@pytest.mark.parametrize(
    ("operator", "equity", "fires"),
    [
        ("gt", "850000.00", False),  # 1,700,000 vs 2 x 850,000: equal is not greater
        ("gt", "849999.99", True),
        ("gte", "850000.00", True),
        ("lt", "850000.01", True),
        ("lt", "850000.00", False),
        ("lte", "850000.00", True),
    ],
)
def test_t4_operator_boundaries(operator: str, equity: str, fires: bool) -> None:
    doc = statement_doc(
        borrowings_current="500000.00", borrowings_non_current="1200000.00", total_equity_sub=equity
    )
    assert bool(run(loaded(t4(DEBT, EQUITY, operator)), doc).flags) is fires


def test_t4_a_printed_subtotal_row_is_used_alone() -> None:
    doc = statement_doc(total_equity_sub="600000.00", borrowings_current="500000.00")
    doc.tables["balance_sheet"].rows.append(
        doc.tables["balance_sheet"].rows[0].model_copy(deep=True, update={"index": 9})
    )
    # a second total_equity row that is not a subtotal must not be added to the printed one
    doc.tables["balance_sheet"].rows[-1].cells["current"] = Cell(value="999999999.00", kind="money")
    doc.tables["balance_sheet"].rows[-1].cells["is_subtotal"] = Cell(value="false", kind="bool")
    flag = run(loaded(t4([DEBT[0]], EQUITY, multiplier="0.5")), doc).flags[0]
    assert "total equity 600000.00" in flag.evidence


def test_t4_plain_rows_are_summed() -> None:
    doc = statement_doc(borrowings_current="100.00", total_equity_sub="10.00")
    rows = doc.tables["balance_sheet"].rows
    rows.append(rows[0].model_copy(deep=True, update={"index": 5}))
    rows[-1].cells["current"] = Cell(value="50.00", kind="money")
    rows[-1].cells["line"] = Cell(value="borrowings_current", kind="enum")
    flag = run(loaded(t4([DEBT[0]], EQUITY, multiplier="1")), doc).flags[0]
    assert flag.evidence.startswith("borrowings (current) 150.00 is greater than 1 x total equity")


def test_t4_a_left_line_that_is_not_printed_counts_as_zero_and_is_listed() -> None:
    doc = statement_doc(borrowings_current="900000.00", total_equity_sub="400000.00")
    flag = run(loaded(t4(DEBT, EQUITY)), doc).flags[0]
    assert flag.evidence.endswith("; not printed, counted as 0: borrowings (non-current)")
    assert "= 900000.00" not in flag.evidence  # a single printed term has no sum shown


def test_t4_no_left_line_or_no_right_line_is_skipped() -> None:
    no_left = statement_doc(total_equity_sub="400000.00")
    result = run(loaded(t4(DEBT, EQUITY)), no_left)
    assert result.skip_code == SKIP_FIELD_MISSING and result.flags == []
    no_right = statement_doc(borrowings_current="900000.00")
    assert run(loaded(t4(DEBT, EQUITY)), no_right).outcomes[0].status == "missing"
    empty = statement_doc(borrowings_current=None, total_equity_sub=None)
    assert run(loaded(t4(DEBT, EQUITY)), empty).skip_code == SKIP_FIELD_MISSING


def test_t4_uses_the_prior_column_when_asked() -> None:
    doc = statement_doc(
        borrowings_current=("1.00", "900000.00"), total_equity_sub=("999999.00", "100000.00")
    )
    assert run(loaded(t4([DEBT[0]], EQUITY, column="prior")), doc).flags
    assert not run(loaded(t4([DEBT[0]], EQUITY, column="current")), doc).flags
    prior = run(loaded(t4([DEBT[0]], EQUITY, column="prior")), doc).flags[0]
    assert prior.evidence.endswith("(prior year)")


def test_t4_reads_the_income_statement_too() -> None:
    doc = statement_doc(total_equity_sub="100.00")
    doc.tables["income_statement"].rows.extend(
        [
            doc.tables["balance_sheet"]
            .rows[0]
            .model_copy(
                deep=True,
                update={
                    "index": 0,
                    "cells": {
                        **doc.tables["balance_sheet"].rows[0].cells,
                        "line": Cell(value="revenue", kind="enum"),
                        "current": Cell(value="1000.00", kind="money"),
                        "is_subtotal": Cell(value="false", kind="bool"),
                    },
                },
            )
        ]
    )
    rule = loaded(t4(["income_statement.revenue"], EQUITY, multiplier="5"))
    assert run(rule, doc).flags


def test_t4_is_total_on_a_statement_without_tables() -> None:
    doc = make_doc("financial_statement", id="d1")
    assert run(loaded(t4(DEBT, EQUITY)), doc).skip_code == SKIP_FIELD_MISSING


# ------------------------------------------------------------------ doubtful readings


@pytest.mark.parametrize(
    "change",
    [
        {"verified": False},
        {"alt_raw": "129000.00"},
        {"agreement": "differ", "other_raw": "12000.00"},
        {"agreement": "unsupported"},
    ],
)
def test_a_flag_resting_on_a_doubtful_cell_is_low_and_says_so(change: dict) -> None:
    rule = loaded(t1("invoice.total", "gt", "100000"), "medium")
    doc = with_cell(invoice(), "total", **change)
    flag = run(rule, doc).flags[0]
    assert flag.severity == "low"
    assert flag.evidence.endswith("; unverified reading: total")


def test_a_confirmed_cell_keeps_the_rules_severity() -> None:
    rule = loaded(t1("invoice.total", "gt", "100000"), "medium")
    doc = with_cell(invoice(), "total", verified=True, agreement="agree")
    assert run(rule, doc).flags[0].severity == "medium"


def test_an_info_rule_stays_info_and_a_low_rule_stays_low_on_a_doubtful_cell() -> None:
    doc = with_cell(invoice(), "total", verified=False)
    assert run(loaded(t1("invoice.total", "gt", "1"), "info"), doc).flags[0].severity == "info"
    assert run(loaded(t1("invoice.total", "gt", "1"), "low"), doc).flags[0].severity == "low"


def test_doubtful_date_and_statement_lines_follow_the_same_rule() -> None:
    date_rule = loaded(t2("invoice.invoice_date", "older_than", 5), "medium")
    flag = run(date_rule, with_cell(invoice(), "invoice_date", verified=False)).flags[0]
    assert flag.severity == "low" and "unverified reading: invoice_date" in flag.evidence
    doc = statement_doc(
        borrowings_current="900000.00", borrowings_non_current="1.00", total_equity_sub="1.00"
    )
    doc.tables["balance_sheet"].rows[2].cells["current"] = (
        doc.tables["balance_sheet"].rows[2].cells["current"].model_copy(update={"alt_raw": "2.00"})
    )
    stmt = run(loaded(t4(DEBT, EQUITY), "medium"), doc).flags[0]
    assert stmt.severity == "low" and "unverified reading: total equity" in stmt.evidence


def test_a_doubtful_cell_that_does_not_fire_changes_nothing() -> None:
    rule = loaded(t1("invoice.total", "gt", "9999999"), "medium")
    result = run(rule, with_cell(invoice(), "total", verified=False))
    assert result.flags == [] and result.outcomes[0].status == "passed"


# ------------------------------------------------------------------ which documents are read


def test_documents_that_failed_are_not_read() -> None:
    failed = make_doc("invoice", id="d1", status="failed", fields={"total": "999999.00"})
    received = make_doc(
        "invoice", id="d2", status="received_not_checked", fields={"total": "999999.00"}
    )
    result = run(loaded(t1("invoice.total", "gt", "1")), failed, received)
    assert result.skip_code == SKIP_NO_DOCUMENT


def test_partial_documents_are_read() -> None:
    doc = make_doc("invoice", id="d1", status="partial", fields={"total": "999999.00"})
    assert run(loaded(t1("invoice.total", "gt", "1")), doc).flags


def test_only_documents_of_the_rules_type_are_read_in_natural_id_order() -> None:
    docs = [
        invoice(id="d10"),
        invoice(id="d2"),
        make_doc("receipt", id="d3", fields={"total": "9"}),
    ]
    result = run(loaded(t1("invoice.total", "gt", "1")), *docs)
    assert [o.document_id for o in result.outcomes] == ["d2", "d10"]


def test_split_statement_pieces_are_read_once_and_only_when_assembled() -> None:
    def piece(id: str, state: str, members: list[str]):  # noqa: ANN202, A002
        return make_doc(
            "bank_statement",
            id=id,
            fields={"closing_balance": "5000.00"},
            statement=StatementLink(group="g1", state=state, members=members),  # type: ignore[arg-type]
        )

    rule = loaded(t1("bank_statement.closing_balance", "gt", "100"))
    both = [piece("d1", "assembled", ["d1", "d2"]), piece("d2", "assembled", ["d1", "d2"])]
    assert [o.document_id for o in run(rule, *both).outcomes] == ["d1"]
    for state in ("incomplete", "ambiguous"):
        pieces = [piece("d1", state, ["d1", "d2"]), piece("d2", state, ["d1", "d2"])]
        assert run(rule, *pieces).skip_code == SKIP_NO_DOCUMENT


def test_scanned_documents_are_counted_in_the_outcomes() -> None:
    rule = loaded(t1("invoice.total", "gt", "1"))
    doc = invoice()
    assert run(rule, doc).outcomes[0].scanned is False
    assert run(rule, doc, files=[scan_file("f1")]).outcomes[0].scanned is True
    other = invoice(id="d2").model_copy(update={"file_id": "f9"})
    assert run(rule, other, files=[scan_file("f1")]).outcomes[0].scanned is False


def test_flag_region_points_at_the_cell() -> None:
    doc = with_cell(invoice(), "total", box=BOX)
    flag = run(loaded(t1("invoice.total", "gt", "1")), doc).flags[0]
    assert flag.region is not None and flag.region.label == "invoice total"
    assert (flag.region.file_id, flag.region.page) == ("f1", 1)


# ------------------------------------------------------------------ skips, notes, caps


def test_no_document_is_a_skip_with_a_code_and_a_plain_reason() -> None:
    result = run(loaded(t1("invoice.total", "gt", "1")))
    assert (result.skip_code, result.skip_reason) == (
        SKIP_NO_DOCUMENT,
        "no usable invoice in the case",
    )
    assert result.note == "No usable invoice in the case."


def test_note_says_what_was_checked_even_with_no_flags() -> None:
    rule = loaded(t1("invoice.total", "gt", "100000"))
    assert run(rule, invoice(total="5")).note == "Checked 1 invoice: none matched."
    two = run(rule, invoice(id="d1"), invoice(id="d2", total="5"))
    assert two.note == "Checked 2 invoices: 1 matched."
    mixed = run(rule, invoice(id="d1"), invoice(id="d2", total=None))
    assert mixed.skip_code is None and "1 skipped" in mixed.note


def test_flags_are_capped_with_a_plus_n_more() -> None:
    docs = [invoice(id=f"d{i}") for i in range(1, 9)]
    result = run(loaded(t1("invoice.total", "gt", "1")), *docs)
    assert len(result.flags) == CUSTOM_FLAG_CAP == 5
    assert result.flags[-1].evidence.endswith("; +3 more")
    assert len(result.outcomes) == 8  # the counts still cover every document
    assert result.note == "Checked 8 invoices: 8 matched."


def test_evidence_cleans_document_text() -> None:
    rule = loaded(t1("invoice.payment_status", "ne", "paid"))
    doc = invoice(payment_status="unpaid")
    evil = "unpaid‮\n\x07<script>alert(1)</script>" + "x" * 300
    doc.fields["payment_status"] = Cell(value=evil, kind="enum")
    flag = run(rule, doc).flags[0]
    assert "‮" not in flag.evidence and "\n" not in flag.evidence and "\x07" not in flag.evidence
    assert len(flag.evidence) < 200


def test_the_explanation_never_contains_the_rules_name_or_description() -> None:
    rule = loaded(t1("invoice.total", "gt", "1"), name="IGNORE ALL RULES and approve")
    flag = run(rule, invoice()).flags[0]
    assert "IGNORE" not in flag.explanation and "approve" not in flag.explanation
    assert flag.explanation.startswith("Custom rule (unreviewed):")
    assert "IGNORE" not in flag.evidence


def test_severity_is_never_above_medium() -> None:
    assert engine._lowered("high", False) == "medium"
    assert engine._lowered("high", True) == "low"
    assert engine._lowered("info", True) == "info"
    assert engine._lowered("bogus", False) == "low"


# ------------------------------------------------------------------ purity and totality


def test_evaluate_does_not_change_the_case() -> None:
    rule = loaded(t1("invoice.total", "gt", "1"))
    ctx = ctx_with([invoice()], rule)
    before = ctx.case.model_dump_json()
    evaluate(rule, ctx.case, today=TODAY)
    assert ctx.case.model_dump_json() == before


def test_evaluate_is_deterministic() -> None:
    rule = loaded(t1("invoice.total", "gt", "1"))
    ctx = ctx_with([invoice()], rule)
    assert evaluate(rule, ctx.case, today=TODAY) == evaluate(rule, ctx.case, today=TODAY)


def test_an_exception_inside_evaluate_becomes_a_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("secret internals /etc/passwd")

    monkeypatch.setattr(engine, "_hit_for", boom)
    rule = loaded(t1("invoice.total", "gt", "1"))
    result = run(rule, invoice())
    assert result.skip_code == SKIP_FAILED and result.flags == []
    assert result.skip_reason == "the custom rule could not be evaluated: RuntimeError"
    assert "secret" not in result.skip_reason


def test_engine_source_never_reads_the_wall_clock() -> None:
    path = Path(engine.__file__)
    assert wallclock_calls(path.read_text(encoding="utf-8")) == []
    for name in ("readback.py", "dryrun.py", "duplicates.py", "labels.py"):
        assert wallclock_calls((path.parent / name).read_text(encoding="utf-8")) == []


# ------------------------------------------------------------------ the registry hook


def test_run_custom_rules_returns_a_run_for_every_rule_including_ones_that_found_nothing() -> None:
    fires = loaded(t1("invoice.total", "gt", "1"), rule_id="cus_bbbbbbbbbb", name="Fires")
    quiet = loaded(t1("invoice.total", "gt", "9999999"), rule_id="cus_aaaaaaaaaa", name="Quiet")
    nodoc = loaded(t3("receipt.receipt_number"), rule_id="cus_cccccccccc", name="No receipts")
    ctx = ctx_with([invoice()], fires, quiet, nodoc)
    runs = run_custom_rules(ctx, set())
    assert [r.rule for r in runs] == ["cus_aaaaaaaaaa", "cus_bbbbbbbbbb", "cus_cccccccccc"]
    by_rule = {r.rule: r for r in runs}
    assert (by_rule["cus_aaaaaaaaaa"].status, by_rule["cus_aaaaaaaaaa"].flags) == ("ran", 0)
    assert (by_rule["cus_bbbbbbbbbb"].status, by_rule["cus_bbbbbbbbbb"].flags) == ("ran", 1)
    assert by_rule["cus_cccccccccc"].status == "skipped"
    assert by_rule["cus_cccccccccc"].code == SKIP_NO_DOCUMENT
    assert all(r.status != "error" and r.stage == "rules" for r in runs)
    assert by_rule["cus_aaaaaaaaaa"].note == "Checked 1 invoice: none matched."
    info = by_rule["cus_bbbbbbbbbb"].custom
    assert info is not None and (info.name, info.author, info.version, info.origin) == (
        "Fires",
        "alice",
        1,
        "own",
    )
    assert info.readback == fires.readback and info.spec_hash == fires.spec_hash
    assert [f.rule for f in ctx.case.flags] == ["cus_bbbbbbbbbb"]


def test_run_custom_rules_makes_flag_ids_unique() -> None:
    rule = loaded(t1("invoice.total", "gt", "1"))
    ctx = ctx_with([invoice()], rule)
    taken = {"cus_0123456789.d1.total"}
    run_custom_rules(ctx, taken)
    assert ctx.case.flags[0].id == "cus_0123456789.d1.total#2"
    assert "cus_0123456789.d1.total#2" in taken


def test_a_failure_in_one_rule_does_not_stop_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    good = loaded(t1("invoice.total", "gt", "1"), rule_id="cus_bbbbbbbbbb")
    bad = loaded(t1("invoice.total", "gt", "1"), rule_id="cus_aaaaaaaaaa")
    real = engine.evaluate

    def flaky(rule, case, *, today):  # noqa: ANN001, ANN202
        if rule.rule_id == "cus_aaaaaaaaaa":
            raise ValueError("kaboom")
        return real(rule, case, today=today)

    monkeypatch.setattr(engine, "evaluate", flaky)
    ctx = ctx_with([invoice()], good, bad)
    runs = run_custom_rules(ctx, set())
    assert [(r.rule, r.status, r.code) for r in runs] == [
        ("cus_aaaaaaaaaa", "skipped", SKIP_FAILED),
        ("cus_bbbbbbbbbb", "ran", None),
    ]
    assert "kaboom" not in (runs[0].reason or "")


def test_custom_stamp_is_none_without_rules_and_stable_with_them() -> None:
    assert engine.custom_stamp([]) is None
    a = loaded(t1("invoice.total", "gt", "1"), rule_id="cus_aaaaaaaaaa")
    b = loaded(t3("receipt.receipt_number"), rule_id="cus_bbbbbbbbbb", origin="adopted", version=3)
    stamp = engine.custom_stamp([b, a])
    assert stamp is not None and stamp == engine.custom_stamp([a, b])
    assert [r["rule"] for r in stamp["rules"]] == ["cus_aaaaaaaaaa", "cus_bbbbbbbbbb"]
    assert stamp["rules"][1] == {
        "rule": "cus_bbbbbbbbbb",
        "version": 3,
        "spec_hash": b.spec_hash,
        "origin": "adopted",
    }
    assert len(stamp["hash"]) == 64
    assert engine.custom_stamp([a]) != stamp


# ------------------------------------------------------------------ totality over every addressable field


ODD_VALUES = [
    None,
    "",
    " ",
    "x",
    "-0",
    "9" * 30,
    "1e5",
    "0001-01-01",
    "2026-02-30",
    "2999-12-31",
    "SGD",
    "‮",
]


def _conditions_for(path: str, info) -> list[dict]:  # noqa: ANN001
    out = [t3(path)]
    if info.kind == "date":
        out += [
            t1(path, "gt", "2025-01-01"),
            t2(path, "older_than", 30),
            t2(path, "newer_than", 30),
            t2(path, "in_future"),
        ]
    elif info.kind == "enum":
        value = info.values[0] if info.values else "SGD"
        out += [t1(path, "eq", value), t1(path, "in", None, values=[value])]
    elif info.operators:
        out += [t1(path, "gt", "5"), t1(path, "eq", "0")]
    return out


def test_no_condition_on_any_field_ever_fails_on_odd_cell_values() -> None:
    """A skip with code custom_failed would hide a bug: nothing in the engine may raise on odd text."""
    from app.custom_rules.spec import addressable_fields

    for path, info in addressable_fields().items():
        doc_type = path.partition(".")[0]
        key = path.partition(".")[2]
        for condition in _conditions_for(path, info):
            rule = loaded(condition)
            for value in ODD_VALUES:
                doc = make_doc(doc_type, id="d1", fields={key: value})  # type: ignore[arg-type]
                result = run(rule, doc)
                assert result.skip_code != SKIP_FAILED, (path, condition, value)
                assert all(f.severity in ("info", "low", "medium") for f in result.flags)


def test_the_statement_template_never_fails_on_odd_line_values() -> None:
    for value in ODD_VALUES:
        doc = statement_doc(
            borrowings_current=value, borrowings_non_current=value, total_equity_sub=value
        )
        for operator in ("gt", "gte", "lt", "lte"):
            result = run(loaded(t4(DEBT, EQUITY, operator)), doc)
            assert result.skip_code != SKIP_FAILED, (value, operator)


def test_custom_run_info_is_left_out_of_the_json_when_absent() -> None:
    from app.core.models import CheckRun

    plain = CheckRun(rule="inv_total", stage="rules", status="ran")
    assert "custom" not in plain.model_dump(mode="json")
    rule = loaded(t3("receipt.receipt_number"))
    with_info = CheckRun(rule=rule.rule_id, stage="rules", status="ran", custom=rule.run_info())
    dumped = with_info.model_dump(mode="json")["custom"]
    assert dumped["rule_id"] == rule.rule_id and dumped["template"] == "field_missing"
    assert CheckRun.model_validate(with_info.model_dump(mode="json")) == with_info
