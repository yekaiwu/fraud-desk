"""Bank statement rules (docs/checks-financial.md section 4; tests T-BS-*).

Documents are built by hand with ``make_doc`` / ``make_ctx`` so nothing depends on extraction.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

import pytest

from app.checks import bank_statement as bs
from app.checks.registry import CheckSkipped, all_checks, discover, run_stage_checks
from app.core.config import Settings
from app.core.models import Applicant, Document, Flag, Region
from app.core.testing import FIXED_TODAY, make_ctx, make_doc

ACCT = "0012345678"
HOLDER = "EXAMPLE LOGISTICS PTE LTD"
APPLICANT = Applicant(name="Example Logistics Pte. Ltd.", uen="201912345R", source="upload")


def tx(d, desc="X", debit=None, credit=None, balance=None) -> dict:  # noqa: ANN001
    return {"date": d, "description": desc, "debit": debit, "credit": credit, "balance": balance}


def f_bs_1_rows() -> list[dict]:
    return [
        tx(
            "2026-03-02",
            "FAST PAYMENT FROM ACME TRADING PTE LTD",
            credit="12500.00",
            balance="22500.00",
        ),
        tx("2026-03-05", "GIRO PAYMENT TO SUPPLIER ALPHA", debit="4200.00", balance="18300.00"),
        tx("2026-03-15", "MONTHLY ACCOUNT FEE", debit="12.50", balance="18287.50"),
        tx("2026-03-20", "FAST PAYMENT FROM BETA PTE LTD", credit="8000.00", balance="26287.50"),
        tx("2026-03-31", "INTEREST CREDIT", credit="3.21", balance="26290.71"),
    ]


def statement(
    rows: list[dict] | None = None,
    *,
    id: str = "d1",  # noqa: A002
    chain: list[dict] | None = None,
    boxes: bool = False,
    **fields: str | None,
) -> Document:
    """F-BS-1 by default; keyword arguments override fields."""
    base: dict[str, str | None] = {
        "bank_name": "EXAMPLE BANK",
        "account_holder": HOLDER,
        "uen": "201912345R",
        "account_number": ACCT,
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "opening_balance": "10000.00",
        "closing_balance": "26290.71",
        "currency": "SGD",
    }
    base.update(fields)
    if chain is None:
        chain = [{"page": "1", "page_x": "1", "page_y": "1", "account_number": ACCT}]
    doc = make_doc(
        "bank_statement",
        id=id,
        fields=base,
        tables={"transactions": f_bs_1_rows() if rows is None else rows, "page_chain": chain},
    )
    if boxes:
        cells = list(doc.fields.values())
        for table in doc.tables.values():
            for row in table.rows:
                cells.extend(row.cells.values())
        for n, cell in enumerate(cells):
            cell.box = Region(
                file_id="f1", page=1, x=0.05 + 0.01 * (n % 50), y=0.05 + 0.002 * n, w=0.1, h=0.01
            )
    return doc


def april(id: str = "d2", boxes: bool = False, **fields: str | None) -> Document:  # noqa: A002
    base = {
        "period_start": "2026-04-01",
        "period_end": "2026-04-30",
        "opening_balance": "26290.71",
        "closing_balance": "31290.71",
    }
    base.update(fields)
    rows = [
        tx(
            "2026-04-10",
            "FAST PAYMENT FROM ACME TRADING PTE LTD",
            credit="5000.00",
            balance="31290.71",
        )
    ]
    return statement(rows, id=id, boxes=boxes, **base)


def same_box(region: Region | None, cell) -> bool:  # noqa: ANN001
    return (
        region is not None
        and cell.box is not None
        and (region.page, region.x, region.y, region.w, region.h)
        == (cell.box.page, cell.box.x, cell.box.y, cell.box.w, cell.box.h)
    )


def set_row(doc: Document, i: int, **cells: str | None) -> None:
    for name, value in cells.items():
        doc.tables["transactions"].rows[i].cells[name].value = value


def cell(doc: Document, i: int, name: str):  # noqa: ANN201
    return doc.tables["transactions"].rows[i].cells[name]


def ctx_of(*docs: Document, **kw):  # noqa: ANN002, ANN003, ANN201
    return make_ctx(list(docs), **kw)


# ----------------------------------------------------------------------------- registration


def test_registration_metadata() -> None:
    discover()
    metas = {m.rule: m for m in all_checks()}
    expected = {
        "bs_totals": "high",
        "bs_running_balance": "high",
        "bs_period_continuity": "high",
        "bs_dates": "medium",
        "bs_holder": "high",
        "bs_page_chain": "medium",
        "bs_duplicates": "low",
        "bs_round_numbers": "low",
        "bs_benford": "info",
    }
    for rule, severity in expected.items():
        meta = metas[rule]
        assert meta.stage == "rules" and meta.severity == severity, rule
        assert meta.applies_to == frozenset({"bank_statement"}) and not meta.requires, rule
        assert meta.use_cases == frozenset({"kyc", "aml", "loan"}), rule
        assert meta.version == "1", rule
        assert meta.description and meta.tamper, rule


def test_clean_bundle_is_silent_through_the_runner() -> None:
    ctx = ctx_of(statement(id="d1"), april(id="d2"), applicant=APPLICANT)
    runs = run_stage_checks(ctx, "rules")
    bank = [r for r in runs if r.rule.startswith("bs_")]
    # the 9 plan 4.1 rules plus bs_pass_through (an AML rule that also applies to bank statements)
    assert len(bank) == 10
    assert all(r.status != "error" for r in bank), [(r.rule, r.message) for r in bank]
    assert [f for f in ctx.case.flags if f.severity != "info"] == []
    skipped = {r.rule for r in bank if r.status == "skipped"}
    assert skipped == {"bs_round_numbers", "bs_benford"}  # not enough data: skipped, not passed


# ----------------------------------------------------------------------------- bs_totals


def test_totals_clean() -> None:
    doc = statement()
    assert bs.bs_totals(ctx_of(doc)) == []
    # printed totals that equal the column sums also pass
    doc = statement(total_debits="4212.50", total_credits="20503.21")
    assert bs.bs_totals(ctx_of(doc)) == []


def test_totals_tampered_credit_golden_evidence() -> None:
    doc = statement(boxes=True)
    set_row(doc, 3, credit="18000.00")  # balances untouched
    (flag,) = bs.bs_totals(ctx_of(doc))
    assert flag.rule == "bs_totals" and flag.severity == "high"
    assert flag.field == "closing_balance" and flag.row is None
    assert flag.evidence == (
        "opening 10000.00 + credits 30503.21 - debits 4212.50 = 36290.71; "
        "printed closing 26290.71; diff 10000.00"
    )
    assert flag.explanation == (
        "Opening balance plus deposits minus withdrawals is 36290.71, but the statement "
        "prints a closing balance of 26290.71."
    )
    assert same_box(flag.region, doc.fields["closing_balance"])
    assert same_box(flag.extra_regions[0], doc.fields["opening_balance"])
    assert flag.id == "bs_totals.d1.closing_balance"


def test_totals_printed_totals() -> None:
    doc = statement(total_debits="4300.00", total_credits="20503.21", boxes=True)
    (flag,) = bs.bs_totals(ctx_of(doc))
    assert flag.field == "total_debits"
    assert flag.id == "bs_totals.d1.total_debits~printed_debits"
    assert flag.evidence == "printed total_debits 4300.00; sum of 2 debit rows 4212.50; diff 87.50"
    assert same_box(flag.region, doc.fields["total_debits"])
    doc = statement(total_credits="20000.00")
    (flag,) = bs.bs_totals(ctx_of(doc))
    assert flag.field == "total_credits" and flag.id.endswith("~printed_credits")
    assert "sum of 3 credit rows 20503.21" in flag.evidence


def test_totals_tolerance_boundary_is_inclusive() -> None:
    assert bs.bs_totals(ctx_of(statement(closing_balance="26291.71"))) == []  # exactly 1.00 off
    assert bs.bs_totals(ctx_of(statement(closing_balance="26289.71"))) == []
    assert len(bs.bs_totals(ctx_of(statement(closing_balance="26291.72")))) == 1  # 1.01 off
    wide = Settings(_env_file=None, money_tolerance=Decimal("5.00"), llm_backend="fake")
    assert bs.bs_totals(ctx_of(statement(closing_balance="26295.00"), settings=wide)) == []


def test_totals_invalid_row_is_excluded_and_noted() -> None:
    rows = f_bs_1_rows()
    rows.append(tx("2026-03-31", debit="9.00", credit="9.00"))
    doc = statement(rows, closing_balance="30000.00")
    (flag,) = bs.bs_totals(ctx_of(doc))
    assert "; 1 invalid row(s) excluded" in flag.evidence
    assert "credits 20503.21 - debits 4212.50 = 26290.71" in flag.evidence


def test_totals_skip_semantics() -> None:
    with pytest.raises(CheckSkipped):
        bs.bs_totals(ctx_of(statement([])))
    with pytest.raises(CheckSkipped):
        bs.bs_totals(ctx_of(statement(closing_balance=None)))
    with pytest.raises(CheckSkipped):
        bs.bs_totals(ctx_of(statement(opening_balance=None)))
    # one evaluable document is enough: the other is silently not evaluated
    assert bs.bs_totals(ctx_of(statement(id="d1"), statement([], id="d2"))) == []


# ----------------------------------------------------------------------------- bs_running_balance


def run_rb(*docs: Document):  # noqa: ANN201
    return bs.bs_running_balance(ctx_of(*docs))


def test_rb_clean() -> None:
    assert run_rb(statement()) == []


def test_rb_tampered_credit_balances_unchanged() -> None:
    doc = statement(boxes=True)
    set_row(doc, 3, credit="18000.00")
    (flag,) = run_rb(doc)
    assert flag.row == 3 and flag.table == "transactions" and flag.severity == "high"
    assert flag.evidence == (
        "previous 18287.50 + credit 18000.00 = 36287.50, printed 26287.50; diff 10000.00"
    )
    assert flag.region is not None and flag.region.label == "balance"
    assert same_box(flag.region, cell(doc, 3, "balance"))
    assert same_box(flag.extra_regions[0], cell(doc, 3, "credit"))
    assert flag.id == "bs_running_balance.d1.transactions:3"


def test_rb_edited_balance_reports_first_break_and_later_count() -> None:
    doc = statement()
    set_row(doc, 1, balance="19300.00")  # 22500 - 4200 = 18300 printed 19300
    (flag,) = run_rb(doc)
    assert flag.row == 1
    assert flag.evidence.startswith(
        "previous 22500.00 - debit 4200.00 = 18300.00, printed 19300.00"
    )
    assert "1 later row(s) also break" in flag.evidence  # row 3 now breaks against 19300


def test_rb_honest_quirk_balance_only_at_day_end() -> None:
    rows = f_bs_1_rows()
    rows[1] = tx("2026-03-05", "GIRO", debit="4200.00", balance=None)
    rows[2] = tx("2026-03-05", "FEE", debit="12.50", balance="18287.50")
    assert run_rb(statement(rows)) == []
    rows[2]["balance"] = "18387.50"
    (flag,) = run_rb(statement(rows))
    assert flag.row == 2
    assert "since row 2" in flag.evidence
    assert flag.evidence.startswith("anchor row 1 balance 22500.00 + net -4212.50 since row 2")


def test_rb_honest_quirk_brought_forward_row_and_overdraft() -> None:
    rows = [
        tx("2026-03-01", "BALANCE B/F", balance="10000.00"),
        tx("2026-03-02", "BIG PAYMENT", debit="10120.50", balance="-120.50"),
        tx("2026-03-03", "RECEIPT", credit="20.50", balance="-100.00"),
    ]
    assert run_rb(statement(rows, closing_balance="-100.00")) == []


def test_rb_hard_case_consistent_recomputation_is_a_known_miss() -> None:
    """The forger edits a deposit and recomputes every later balance and the closing balance."""
    rows = f_bs_1_rows()
    rows[3] = tx(
        "2026-03-20", "FAST PAYMENT FROM BETA PTE LTD", credit="18000.00", balance="36287.50"
    )
    rows[4] = tx("2026-03-31", "INTEREST CREDIT", credit="3.21", balance="36290.71")
    march = statement(rows, closing_balance="36290.71")
    # bs_running_balance and bs_totals are consistent: nothing to see inside the one document ...
    assert run_rb(march) == []
    assert bs.bs_totals(ctx_of(march)) == []
    # ... the net is bs_period_continuity against the untouched neighbour month (and the
    # cross-document rules x_revenue_vs_credits / x_cash_at_year_end, tested in their own files).
    (flag,) = bs.bs_period_continuity(ctx_of(march, april()))
    assert flag.id.endswith("~balance") and "diff 10000.00" in flag.evidence


def test_rb_skip_when_nothing_evaluable() -> None:
    for doc in (
        statement([]),
        statement(opening_balance=None),
        statement([tx("2026-03-01", credit="1.00")]),
    ):
        with pytest.raises(CheckSkipped):
            run_rb(doc)


# ----------------------------------------------------------------------------- bs_period_continuity


def test_pc_clean_and_same_day_boundary() -> None:
    assert bs.bs_period_continuity(ctx_of(statement(id="d1"), april(id="d2"))) == []
    same_day = april(period_start="2026-03-31")  # B starts the day A ends: contiguous
    assert bs.bs_period_continuity(ctx_of(statement(), same_day)) == []


def test_pc_tampered_removed_month_gap() -> None:
    may = april(id="d3", period_start="2026-05-01", period_end="2026-05-31")
    (flag,) = bs.bs_period_continuity(ctx_of(statement(id="d1"), may))
    assert flag.severity == "high" and flag.document_ids == ["d1", "d3"]
    assert flag.evidence == "gap: d1 ends 2026-03-31, d3 starts 2026-05-01; 30 day(s) not covered"
    assert flag.explanation == "No statement covers 30 day(s) between these two statements."
    assert flag.field == "period_start" and flag.id.endswith("~gap")


def test_pc_overlap_and_same_period_uploaded_twice() -> None:
    twice = statement(id="d2")
    (flag,) = bs.bs_period_continuity(ctx_of(statement(id="d1"), twice))
    assert flag.severity == "medium" and flag.id.endswith("~overlap")
    assert flag.evidence == (
        "overlap: d1 ends 2026-03-31, d2 starts 2026-03-01 (31 days; same period uploaded twice)"
    )
    partial = april(period_start="2026-03-15", period_end="2026-04-14")
    (flag,) = bs.bs_period_continuity(ctx_of(statement(), partial))
    assert flag.evidence == "overlap: d1 ends 2026-03-31, d2 starts 2026-03-15 (17 days)"
    assert flag.explanation == "These two statements cover some of the same days."


def test_pc_tampered_hard_case_balance_break_with_pointer() -> None:
    rows = f_bs_1_rows()
    rows[3] = tx("2026-03-20", "BETA", credit="18000.00", balance="36287.50")
    rows[4] = tx("2026-03-31", "INTEREST", credit="3.21", balance="36290.71")
    march = statement(rows, id="d1", closing_balance="36290.71", boxes=True)
    apr = april(id="d2", boxes=True)
    (flag,) = bs.bs_period_continuity(ctx_of(march, apr))
    assert flag.severity == "high" and flag.field == "opening_balance"
    assert flag.evidence == "balance: d1 closing 36290.71, d2 opening 26290.71; diff 10000.00"
    assert flag.explanation == (
        "The closing balance of the earlier statement does not match the opening balance of the next."
    )
    assert flag.id == "bs_period_continuity.d1+d2.opening_balance~balance"
    assert flag.region is not None and flag.region.label == "opening balance"
    assert flag.extra_regions and flag.extra_regions[0].label == "previous closing"


def test_pc_balance_tolerance_boundary() -> None:
    assert bs.bs_period_continuity(ctx_of(statement(), april(opening_balance="26291.71"))) == []
    assert len(bs.bs_period_continuity(ctx_of(statement(), april(opening_balance="26291.72")))) == 1


def test_pc_honest_quirks_non_calendar_cycles_and_two_accounts() -> None:
    a = statement(
        id="d1", period_start="2026-03-15", period_end="2026-04-14", closing_balance="500.00"
    )
    b = statement(
        id="d2", period_start="2026-04-15", period_end="2026-05-14", opening_balance="500.00"
    )
    assert bs.bs_period_continuity(ctx_of(a, b)) == []
    # a second account with different periods and balances is evaluated separately
    other = statement(
        id="d3",
        account_number="0098765432",
        period_start="2026-06-01",
        period_end="2026-06-30",
        opening_balance="1.00",
    )
    other2 = statement(
        id="d4",
        account_number="0098765432",
        period_start="2026-07-01",
        period_end="2026-07-31",
        opening_balance="1.00",
        closing_balance="1.00",
    )
    other.fields["closing_balance"].value = "1.00"
    assert bs.bs_period_continuity(ctx_of(a, b, other, other2)) == []


def test_pc_different_currency_is_a_different_account() -> None:
    usd = april(currency="USD", opening_balance="1.00")
    assert bs.bs_period_continuity(ctx_of(statement(), usd, april(id="d3"))) == []


def test_pc_single_statement_and_missing_account_skip() -> None:
    with pytest.raises(CheckSkipped):
        bs.bs_period_continuity(ctx_of(statement()))
    with pytest.raises(CheckSkipped):
        bs.bs_period_continuity(ctx_of(statement(account_number=None), april(account_number=None)))


def test_pc_account_number_formatting_is_normalised() -> None:
    dashed = april(account_number="001-234 5678")
    assert bs.bs_period_continuity(ctx_of(statement(account_number="0012345678"), dashed)) == []


# ----------------------------------------------------------------------------- bs_dates


def test_dates_clean_with_period_end_day_and_blank_date() -> None:
    rows = f_bs_1_rows()
    rows[2] = tx(None, "MONTHLY ACCOUNT FEE", debit="12.50", balance="18287.50")  # same day
    assert bs.bs_dates(ctx_of(statement(rows))) == []
    # a row on the first and the last day of the period is fine
    rows[0]["date"] = "2026-03-01"
    rows[4]["date"] = "2026-03-31"
    assert bs.bs_dates(ctx_of(statement(rows))) == []


def test_dates_moved_row_flags_only_that_row() -> None:
    doc = statement(boxes=True)
    set_row(doc, 3, date="2026-03-03")  # after the 03-15 row
    (flag,) = bs.bs_dates(ctx_of(doc))
    assert flag.row == 3 and flag.severity == "medium" and flag.id.endswith("~order")
    assert flag.evidence == "row 4 dated 2026-03-03 follows row 3 dated 2026-03-15 (out of order)"
    assert flag.explanation == "The date on row 4 is out of order."
    assert same_box(flag.region, cell(doc, 3, "date"))


def test_dates_first_row_moved_later_is_the_one_flagged() -> None:
    doc = statement()
    set_row(doc, 0, date="2026-03-25")
    (flag,) = bs.bs_dates(ctx_of(doc))
    assert flag.row == 0 and "precedes row 2" in flag.evidence


def test_dates_inserted_row_after_period_is_range() -> None:
    rows = [*f_bs_1_rows(), tx("2026-04-02", "EXTRA", credit="5.00", balance="26295.71")]
    (flag,) = bs.bs_dates(ctx_of(statement(rows)))
    assert flag.row == 5 and flag.id.endswith("~range")
    assert flag.evidence == "row 6 dated 2026-04-02 outside period 2026-03-01..2026-03-31"
    assert flag.explanation == "The date on row 6 is outside the statement period."
    before = [tx("2026-02-28", "EARLY", credit="5.00", balance="1.00"), *f_bs_1_rows()]
    (flag,) = bs.bs_dates(ctx_of(statement(before)))
    assert flag.row == 0 and flag.id.endswith("~range")


def test_dates_grace_days_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [*f_bs_1_rows(), tx("2026-04-02", "EXTRA", credit="5.00", balance="26295.71")]
    monkeypatch.setattr(bs, "BS_DATE_GRACE_DAYS", 2)
    assert bs.bs_dates(ctx_of(statement(rows))) == []


def test_dates_calendar_date_that_does_not_exist() -> None:
    doc = statement(boxes=True)
    cell(doc, 1, "date").value = None
    cell(doc, 1, "date").raw = "31 FEB 2026"
    (flag,) = bs.bs_dates(ctx_of(doc))
    assert flag.row == 1 and flag.id.endswith("~calendar")
    assert flag.evidence == 'row 2 date "31 FEB 2026" is not a calendar date'
    assert flag.explanation == "The date on row 2 is not a real date."
    assert same_box(flag.region, cell(doc, 1, "date"))


def test_dates_future_uses_the_injected_today() -> None:
    rows = [tx("2026-03-02", "A", credit="1.00", balance="10001.00")]
    doc = statement(
        rows, period_start="2026-11-01", period_end="2026-11-30", closing_balance="10001.00"
    )
    set_row(doc, 0, date="2026-11-02")
    flags = bs.bs_dates(ctx_of(doc, today=FIXED_TODAY))
    kinds = sorted(f.id.rsplit("~", 1)[1] for f in flags)
    assert kinds == ["future", "future"]
    by_field = {f.field: f for f in flags}
    assert by_field["period_end"].evidence == "period_end 2026-11-30 is after today 2026-10-27"
    assert by_field[None].evidence == "row 1 dated 2026-11-02 is after today 2026-10-27"
    assert by_field[None].explanation == "The date on row 1 is in the future."
    assert bs.bs_dates(ctx_of(doc, today=date(2026, 12, 1))) == []  # today is injected


def test_dates_period_start_after_end() -> None:
    doc = statement([], period_start="2026-03-31", period_end="2026-03-01")
    (flag,) = bs.bs_dates(ctx_of(doc))
    assert flag.field == "period_start" and "after period_end" in flag.evidence


def test_dates_cap_is_three_per_condition_with_more_note() -> None:
    rows = [tx(f"2026-05-0{k}", "Z", credit="1.00", balance=None) for k in range(1, 9)]
    flags = bs.bs_dates(ctx_of(statement(rows)))
    assert len(flags) == 3 and all(f.id.endswith("~range") for f in flags)
    assert flags[-1].evidence.endswith("; +5 more")


def test_dates_skip_when_nothing_to_evaluate() -> None:
    doc = statement([tx(None, "x", credit="1.00")], period_start=None, period_end=None)
    with pytest.raises(CheckSkipped):
        bs.bs_dates(ctx_of(doc))


# ----------------------------------------------------------------------------- bs_holder


def test_holder_clean_punctuation_and_case() -> None:
    assert bs.bs_holder(ctx_of(statement(uen=None), applicant=APPLICANT)) == []
    assert bs.bs_holder(ctx_of(statement(), applicant=APPLICANT)) == []


def test_holder_name_mismatch() -> None:
    doc = statement(uen=None, account_holder="TAN WEI MING", boxes=True)
    (flag,) = bs.bs_holder(ctx_of(doc, applicant=APPLICANT))
    assert flag.field == "account_holder" and flag.severity == "high"
    assert flag.evidence.startswith(
        'account_holder "TAN WEI MING" vs applicant "Example Logistics Pte. Ltd."; match 0.'
    )
    assert flag.explanation == "The account holder is not the applicant company."
    assert flag.id == "bs_holder.d1.account_holder~ming_tan_wei"
    assert same_box(flag.region, doc.fields["account_holder"])


def test_holder_uen_decides_over_names() -> None:
    renamed = statement(account_holder="EXAMPLE LOGISTICS GROUP", uen="201912345R")
    assert bs.bs_holder(ctx_of(renamed, applicant=APPLICANT)) == []  # same UEN, different name
    other = statement(uen="202012345H", account_holder=HOLDER)
    (flag,) = bs.bs_holder(ctx_of(other, applicant=APPLICANT))  # same name, different UEN
    assert flag.field == "uen" and flag.id.endswith("~uen_202012345h")
    assert flag.evidence == "statement uen 202012345H vs applicant uen 201912345R"


def test_holder_uen_whitespace_is_normalised() -> None:
    doc = statement(uen="2019 12345 r")
    assert bs.bs_holder(ctx_of(doc, applicant=APPLICANT)) == []


def test_holder_trading_as_suffix_matches() -> None:
    doc = statement(uen=None, account_holder="EXAMPLE LOGISTICS PTE LTD T/A EXPRESS FREIGHT")
    assert bs.bs_holder(ctx_of(doc, applicant=APPLICANT)) == []


def test_holder_skips_without_applicant_or_names() -> None:
    with pytest.raises(CheckSkipped):
        bs.bs_holder(ctx_of(statement()))
    with pytest.raises(CheckSkipped):
        bs.bs_holder(ctx_of(statement(uen=None, account_holder=None), applicant=APPLICANT))


# ----------------------------------------------------------------------------- bs_page_chain


def chain_rows(pairs: list[tuple[int | None, int | None]], acct: str = ACCT) -> list[dict]:
    return [
        {
            "page": str(i + 1),
            "page_x": None if x is None else str(x),
            "page_y": None if y is None else str(y),
            "account_number": acct,
        }
        for i, (x, y) in enumerate(pairs)
    ]


def paged(pairs, pages: int, **kw) -> Document:  # noqa: ANN001, ANN003
    doc = statement(chain=chain_rows(pairs), **kw)
    doc.page_end = pages
    return doc


def test_page_chain_clean_and_single_page() -> None:
    assert bs.bs_page_chain(ctx_of(paged([(1, 3), (2, 3), (3, 3)], 3))) == []
    assert bs.bs_page_chain(ctx_of(statement())) == []  # 1/1, one-page document


def test_page_chain_missing_page() -> None:
    doc = paged([(1, 3), (3, 3)], 3, boxes=True)
    flags = bs.bs_page_chain(ctx_of(doc))
    (seq,) = [f for f in flags if f.id.endswith("~sequence")]
    assert seq.severity == "medium" and seq.table == "page_chain" and seq.row == 1
    assert seq.evidence == "page markers 1/3, 3/3; expected 1/3, 2/3, 3/3; missing page 2"
    assert seq.explanation == 'The page numbers do not form a complete "page x of y" sequence.'
    assert same_box(seq.region, doc.tables["page_chain"].rows[1].cells["page_x"])
    (count,) = [f for f in flags if f.id.endswith("~count")]  # the document spans 3 pages
    assert count.evidence == "chain lists 2 pages; document has 3"
    # a document of 2 pages gets the sequence flag only
    assert [
        f.id.rsplit("~", 1)[1] for f in bs.bs_page_chain(ctx_of(paged([(1, 3), (3, 3)], 2)))
    ] == ["sequence"]


def test_page_chain_repeated_and_out_of_order_pages() -> None:
    (flag,) = bs.bs_page_chain(ctx_of(paged([(1, 3), (2, 3), (2, 3)], 3)))
    assert "repeated page 2" in flag.evidence and "missing page 3" in flag.evidence
    (flag,) = bs.bs_page_chain(ctx_of(paged([(1, 3), (3, 3), (2, 3)], 3)))
    assert flag.row == 1 and "pages out of order" in flag.evidence


def test_page_chain_total_disagreement() -> None:
    flags = bs.bs_page_chain(ctx_of(paged([(1, 3), (2, 4), (3, 3)], 3)))
    (flag,) = flags
    assert flag.id.endswith("~total") and flag.row == 1
    assert flag.evidence == "row 2 says 2/4 but other pages say /3"


def test_page_chain_different_account_on_a_page() -> None:
    doc = statement(chain=chain_rows([(1, 2), (2, 2)]), boxes=True)
    doc.page_end = 2
    doc.tables["page_chain"].rows[1].cells["account_number"].value = "0098765432"
    (flag,) = bs.bs_page_chain(ctx_of(doc))
    assert flag.id == "bs_page_chain.d1.page_chain:1~account" and flag.row == 1
    assert flag.evidence == "page 2 account 0098765432 vs 0012345678 on the statement"
    assert flag.explanation == (
        "This page carries a different account number from the rest of the statement."
    )
    assert same_box(flag.region, doc.tables["page_chain"].rows[1].cells["account_number"])


def test_page_chain_honest_quirk_no_total_printed() -> None:
    assert bs.bs_page_chain(ctx_of(paged([(1, None), (2, None)], 2))) == []
    (flag,) = bs.bs_page_chain(ctx_of(paged([(1, None), (3, None)], 2)))
    assert "missing page 2" in flag.evidence and "expected 1, 2" in flag.evidence


def test_page_chain_skip_without_markers() -> None:
    with pytest.raises(CheckSkipped, match="no page markers"):
        bs.bs_page_chain(ctx_of(statement(chain=[])))
    with pytest.raises(CheckSkipped):
        bs.bs_page_chain(ctx_of(statement(chain=chain_rows([(None, None)]))))


# ----------------------------------------------------------------------------- bs_duplicates


def test_duplicates_clean_and_small_fees() -> None:
    assert bs.bs_duplicates(ctx_of(statement())) == []
    rows = f_bs_1_rows()
    rows.insert(3, dict(rows[2]))  # second MONTHLY ACCOUNT FEE of 12.50 on the same date
    assert bs.bs_duplicates(ctx_of(statement(rows))) == []


def test_duplicates_copied_row() -> None:
    rows = f_bs_1_rows()
    rows.insert(2, dict(rows[1]))
    doc = statement(rows, boxes=True)
    (flag,) = bs.bs_duplicates(ctx_of(doc))
    assert flag.row == 2 and flag.severity == "low" and flag.id.endswith(":2")
    assert flag.evidence == (
        'rows 2 and 3 identical: 2026-03-05 "GIRO PAYMENT TO SUPPLIER ALPHA" debit 4200.00'
    )
    assert flag.explanation == (
        "Row 3 repeats an earlier row with the same date, description and amount."
    )
    assert same_box(flag.region, cell(doc, 2, "description"))
    assert same_box(flag.extra_regions[0], cell(doc, 1, "description"))


def test_duplicates_balance_is_not_part_of_the_key_and_text_is_normalised() -> None:
    rows = f_bs_1_rows()
    copy = dict(rows[0])
    copy["balance"] = "99999.00"  # a copy-paste that recomputed the balance
    copy["description"] = "  fast payment from acme   trading pte ltd "
    rows.append(copy)
    (flag,) = bs.bs_duplicates(ctx_of(statement(rows)))
    assert flag.row == 5 and "rows 1 and 6 identical" in flag.evidence


def test_duplicates_three_copies_one_flag_on_second_and_boundary_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [tx("2026-03-02", "RENT", debit="20.00", balance=None) for _ in range(3)]
    (flag,) = bs.bs_duplicates(ctx_of(statement(rows)))  # exactly 20.00 counts
    assert flag.row == 1 and "rows 1, 2 and 3 identical" in flag.evidence
    monkeypatch.setattr(bs, "BS_DUP_MIN_AMOUNT", Decimal("20.01"))
    assert bs.bs_duplicates(ctx_of(statement(rows))) == []


def test_duplicates_cap_five_groups() -> None:
    rows = []
    for k in range(7):
        rows += [tx("2026-03-02", f"PAY {k}", debit="100.00")] * 2
    flags = bs.bs_duplicates(ctx_of(statement(rows)))
    assert len(flags) == 5 and flags[-1].evidence.endswith("; +2 more")


def test_duplicates_skip_without_rows() -> None:
    with pytest.raises(CheckSkipped):
        bs.bs_duplicates(ctx_of(statement([])))


# ----------------------------------------------------------------------------- bs_round_numbers


def credit_doc(id: str, credits: list[str], month: int) -> Document:  # noqa: A002
    rows = [tx(f"2026-{month:02d}-{1 + i:02d}", f"IN {i}", credit=c) for i, c in enumerate(credits)]
    return statement(
        rows,
        id=id,
        period_start=f"2026-{month:02d}-01",
        period_end=f"2026-{month:02d}-28",
    )


ODD = ["1234.56", "877.10", "4321.09", "250.75", "9123.40", "655.30", "7012.80", "321.65"]
ROUND_8 = ["5000.00", "2000.00", "1000.00", "3000.00", "4000.00", "6000.00", "777.10", "310.55"]


def odd(n: int, round_count: int = 0) -> list[str]:
    values = ["2000.00"] * round_count + [ODD[i % len(ODD)] for i in range(n - round_count)]
    return values


def test_round_numbers_clean() -> None:
    docs = [
        credit_doc("d1", odd(10, 1), 1),
        credit_doc("d2", odd(10, 2), 2),
        credit_doc("d3", odd(10, 1), 3),
    ]
    assert bs.bs_round_numbers(ctx_of(*docs)) == []


def test_round_numbers_tampered_month() -> None:
    docs = [
        credit_doc("d1", odd(10, 1), 1),
        credit_doc("d2", odd(10, 1), 2),
        credit_doc("d3", ROUND_8, 3),
    ]
    ctx = ctx_of(*docs)
    (flag,) = bs.bs_round_numbers(ctx)
    assert flag.document_ids == ["d3"] and flag.severity == "low" and flag.row == 0
    assert flag.evidence == (
        "6 of 8 credits are round multiples of 1000 (75%); other statements of this account: "
        "2 of 20 (10%); uplift 65 points"
    )
    assert flag.explanation == (
        "Unusually many round-sum deposits compared with the other months of this account. "
        "A hint only, not proof."
    )
    assert flag.table == "transactions" and len(flag.extra_regions) <= 4


def test_round_numbers_pointer_is_the_first_round_credit() -> None:
    docs = [
        credit_doc("d1", odd(10, 1), 1),
        credit_doc("d2", ["777.10", *ROUND_8[:6], "310.55"], 2),
    ]
    doc = docs[1]
    c = doc.tables["transactions"].rows[1].cells["credit"]
    c.box = Region(file_id="f1", page=1, x=0.2, y=0.2, w=0.1, h=0.01)
    (flag,) = bs.bs_round_numbers(ctx_of(*docs))
    assert flag.row == 1 and same_box(flag.region, c)


def test_round_numbers_honest_retainer_business_is_silent() -> None:
    # 70% round credits in every month: no uplift against the other months
    month = ["5000.00"] * 7 + ["123.45", "88.10", "9.99"]
    docs = [credit_doc(f"d{k}", month, k) for k in (1, 2, 3)]
    assert bs.bs_round_numbers(ctx_of(*docs)) == []


def test_round_numbers_boundary_exactly_at_thresholds() -> None:
    base = [credit_doc("d1", odd(10, 2), 1)]  # baseline 2 of 10 = 0.20
    exactly = credit_doc("d2", odd(10, 5), 2)  # share 0.50, uplift 0.30: flags
    assert len(bs.bs_round_numbers(ctx_of(*base, exactly))) == 1
    below = credit_doc("d2", odd(9, 4), 2)  # 4 of 9 = 0.44: no flag
    assert bs.bs_round_numbers(ctx_of(*base, below)) == []


def test_round_numbers_needs_enough_credits_and_baseline() -> None:
    with pytest.raises(CheckSkipped):
        bs.bs_round_numbers(ctx_of(credit_doc("d1", ROUND_8, 1)))  # single statement
    short_baseline = [credit_doc("d1", odd(5, 0), 1), credit_doc("d2", ROUND_8, 2)]
    with pytest.raises(CheckSkipped):
        bs.bs_round_numbers(ctx_of(*short_baseline))  # only 5 baseline credits (< 10)


def test_round_numbers_round_must_reach_the_modulus() -> None:
    # 0 is not a round credit and neither is 500: the modulus floor is 1000
    docs = [credit_doc("d1", odd(10, 1), 1), credit_doc("d2", ["500.00"] * 8, 2)]
    assert bs.bs_round_numbers(ctx_of(*docs)) == []


# ----------------------------------------------------------------------------- bs_benford


def benford_doc(amounts: list[str], id: str = "d1") -> Document:  # noqa: A002
    rows = [tx("2026-03-02", f"T{i}", credit=a) for i, a in enumerate(amounts)]
    return statement(rows, id=id)


def test_benford_clean_synthetic_amounts() -> None:
    rng = random.Random(4002)
    amounts = [f"{int(10 ** rng.uniform(0, 5))}.00" for _ in range(120)]
    assert bs.bs_benford(ctx_of(benford_doc(amounts))) == []


def test_benford_all_leading_five_flags_as_info() -> None:
    amounts = [("5.00", "50.00", "5000.00", "55.55")[i % 4] for i in range(120)]
    (flag,) = bs.bs_benford(ctx_of(benford_doc(amounts)))
    assert flag.severity == "info" and flag.row is None and flag.region is None
    assert flag.evidence.startswith("n 120; first-digit % 1:0 2:0 3:0 4:0 5:100 6:0 7:0 8:0 9:0;")
    assert "(critical 20.09)" in flag.evidence
    assert flag.explanation.startswith("The leading digits of the amounts depart")


def test_benford_needs_100_amounts() -> None:
    with pytest.raises(CheckSkipped):
        bs.bs_benford(ctx_of(benford_doc(["5.00"] * 99)))
    assert len(bs.bs_benford(ctx_of(benford_doc(["5.00"] * 100)))) == 1  # exactly 100 runs


def test_benford_honest_false_positive_fixed_salaries_is_info_only() -> None:
    amounts = ["2500.00"] * 90 + ["12.50"] * 30
    (flag,) = bs.bs_benford(ctx_of(benford_doc(amounts)))
    assert flag.severity == "info"


def test_benford_pools_statements_of_one_account_only() -> None:
    a = benford_doc(["5.00"] * 60, id="d1")
    b = benford_doc(["5.00"] * 60, id="d2")
    (flag,) = bs.bs_benford(ctx_of(a, b))
    assert flag.document_ids == ["d1", "d2"]
    other = statement(
        [tx("2026-03-02", "X", credit="5.00")] * 60, id="d3", account_number="0098765432"
    )
    with pytest.raises(CheckSkipped):
        bs.bs_benford(ctx_of(benford_doc(["5.00"] * 60, id="d1"), other))


def test_benford_first_digit_helper() -> None:
    assert bs._first_digit(Decimal("0.05")) == 5
    assert bs._first_digit(Decimal("1234.00")) == 1
    assert bs._first_digit(Decimal("0.00")) is None


# ----------------------------------------------------------------------------- cross-cutting

RULES = [
    bs.bs_totals,
    bs.bs_running_balance,
    bs.bs_period_continuity,
    bs.bs_dates,
    bs.bs_holder,
    bs.bs_page_chain,
    bs.bs_duplicates,
    bs.bs_round_numbers,
    bs.bs_benford,
]


def tampered_bundle() -> list[Document]:
    docs = [
        statement(id="d1", boxes=True),
        april(id="d2"),
        april(id="d3", period_start="2026-05-01", period_end="2026-05-31"),
    ]
    set_row(docs[0], 3, credit="18000.00")
    set_row(docs[0], 1, date="2026-03-30")
    docs[0].tables["transactions"].rows.append(
        docs[0].tables["transactions"].rows[1].model_copy(deep=True)
    )
    return docs


def outcome(rule, docs) -> list[tuple[str, str]]:  # noqa: ANN001
    try:
        return [(f.id, f.evidence) for f in rule(make_ctx(docs, applicant=APPLICANT))]
    except CheckSkipped as exc:
        return [("skipped", str(exc))]


def test_determinism_and_document_order_independence() -> None:
    docs = tampered_bundle()
    for rule in RULES:
        first = outcome(rule, docs)
        assert first == outcome(rule, docs), rule.__name__
        assert first == outcome(rule, list(reversed(docs))), rule.__name__
    assert any(outcome(rule, docs)[0][0] != "skipped" for rule in RULES)


def test_untrusted_text_is_truncated_and_inert() -> None:
    nasty = '"); DROP TABLE cases; --\n' + "A" * 5000
    rows = f_bs_1_rows()
    rows[1]["description"] = nasty
    rows.insert(2, dict(rows[1]))
    doc = statement(rows, account_holder=nasty, uen=None)
    flags = bs.bs_duplicates(ctx_of(doc))
    assert flags and "\n" not in flags[0].evidence and len(flags[0].evidence) <= 200
    assert "DROP TABLE" in flags[0].evidence  # kept as inert text, just truncated
    assert "A" * 60 not in flags[0].evidence
    (holder,) = bs.bs_holder(ctx_of(doc, applicant=APPLICANT))
    assert "\n" not in holder.evidence and len(holder.evidence) < 300
    assert holder.id.count(".") == 2 and "~" in holder.id  # discriminator stayed clean
    doc2 = statement()
    doc2.tables["transactions"].rows[0].cells["date"].value = None
    doc2.tables["transactions"].rows[0].cells["date"].raw = nasty
    (cal,) = bs.bs_dates(ctx_of(doc2))
    assert "\n" not in cal.evidence and len(cal.evidence) < 140


def test_skip_semantics_for_a_case_without_the_document_type() -> None:
    for rule in RULES:
        with pytest.raises(CheckSkipped):
            rule(make_ctx([], applicant=APPLICANT))


def test_unverified_reading_suffix_does_not_change_severity() -> None:
    doc = statement()
    set_row(doc, 3, credit="18000.00")
    doc.tables["transactions"].rows[3].cells["credit"].alt_raw = "8000.00"
    doc.fields["closing_balance"].verified = False
    (rb,) = bs.bs_running_balance(ctx_of(doc))
    assert rb.evidence.endswith("; unverified reading: credit") and rb.severity == "high"
    (totals,) = bs.bs_totals(ctx_of(doc))
    assert totals.evidence.endswith("; unverified reading: closing_balance, credit row 4")
    assert totals.severity == "high"


def test_failed_and_unread_documents_are_not_evaluated() -> None:
    failed = statement(id="d9")
    failed.status = "failed"
    set_row(failed, 3, credit="18000.00")
    ok = statement(id="d1")
    assert bs.bs_totals(ctx_of(ok, failed)) == []
    assert bs.bs_running_balance(ctx_of(ok, failed)) == []


def test_pointer_localisation_for_each_row_level_rule() -> None:
    """The tampered cell's table/row/region equal the flag's (localisation metric)."""
    # running balance: edited credit
    doc = statement(boxes=True)
    set_row(doc, 3, credit="18000.00")
    (f,) = bs.bs_running_balance(ctx_of(doc))
    assert (f.table, f.row) == ("transactions", 3) and same_box(f.region, cell(doc, 3, "balance"))
    # dates: moved row
    doc = statement(boxes=True)
    set_row(doc, 3, date="2026-03-03")
    (f,) = bs.bs_dates(ctx_of(doc))
    assert (f.table, f.row) == ("transactions", 3) and same_box(f.region, cell(doc, 3, "date"))
    # duplicates: copied row
    rows = f_bs_1_rows()
    rows.insert(2, dict(rows[1]))
    doc = statement(rows, boxes=True)
    (f,) = bs.bs_duplicates(ctx_of(doc))
    assert (f.table, f.row) == ("transactions", 2) and same_box(
        f.region, cell(doc, 2, "description")
    )
    # every flag the module produces is a valid Flag with a 0-based row inside the table
    for flag in [*bs.bs_totals(ctx_of(statement(closing_balance="1.00")))]:
        assert isinstance(flag, Flag) and flag.row is None
