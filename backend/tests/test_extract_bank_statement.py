"""Bank statement text-layer extraction: four layout families and the honest quirks."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.llm import FakeLLMClient
from app.extract.bank_statement import validate_bank_statement
from app.extract.base import LayoutNotRecognised
from app.extract.registry import discover, get_extractors
from tests.extract_helpers import (
    StatementSpec,
    Txn,
    default_txns,
    make_case,
    render_statement,
    statement_truth,
)

discover()
TL = get_extractors("bank_statement")["text_layer"]
LAYOUTS = ["dbs", "ocbc", "uob", "software"]


def run(spec: StatementSpec):
    pdf = render_statement(spec)
    llm = FakeLLMClient()
    ctx, doc, _ = make_case(pdf, "bank_statement", llm=llm)
    return TL.extract(doc, ctx), ctx, doc, llm


def rows_of(result) -> list[dict]:
    return [
        {k: r.cells[k].value for k in ("date", "description", "debit", "credit", "balance")}
        for r in result.tables["transactions"].rows
    ]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_all_four_layouts_parse_to_the_same_truth_rows(layout: str) -> None:
    spec = StatementSpec(layout=layout, txns=default_txns(50))
    result, ctx, doc, llm = run(spec)
    assert rows_of(result) == statement_truth(spec)
    assert llm.calls == []  # the text layer needs no model
    f = result.fields
    assert f["account_holder"].value == spec.holder
    assert f["account_number"].value == spec.account
    assert f["uen"].value == spec.uen
    assert f["period_start"].value == "2026-03-01"
    assert f["period_end"].value == "2026-03-31"
    assert f["opening_balance"].value == "10000.00"
    assert f["closing_balance"].value == statement_truth(spec)[-1]["balance"]
    assert f["currency"].value == "SGD"
    assert f["page_x_of_y"].value == "1/3,2/3,3/3"
    assert validate_bank_statement(result, ctx, doc).failing == {}


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_cell_has_a_box_and_is_verified(layout: str) -> None:
    result, _, doc, _ = run(StatementSpec(layout=layout, txns=default_txns(30)))
    for row in result.tables["transactions"].rows:
        for name in ("date", "description", "balance"):
            cell = row.cells[name]
            assert cell.box is not None, (layout, row.index, name)
            assert cell.verified is True
            assert cell.source == "text_layer"
            assert cell.box.x >= 0 and cell.box.x + cell.box.w <= 1.0001
        amount = row.cells["debit"] if row.cells["debit"].value else row.cells["credit"]
        assert amount.box is not None
        assert row.box is not None
        assert row.box.page == row.cells["date"].box.page
        assert (row.cells["debit"].value is None) != (row.cells["credit"].value is None)
    pages = {r.box.page for r in result.tables["transactions"].rows}
    assert pages == {1, 2}
    assert result.tables["transactions"].pages == [1, 2]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_page_chain_has_one_row_per_page_with_marker_boxes(layout: str) -> None:
    result, *_ = run(StatementSpec(layout=layout, txns=default_txns(50)))
    chain = result.tables["page_chain"].rows
    assert [(r.cells["page_x"].value, r.cells["page_y"].value) for r in chain] == [
        ("1", "3"),
        ("2", "3"),
        ("3", "3"),
    ]
    assert {r.cells["account_number"].value for r in chain} == {"123-456-789-0"}
    assert [r.cells["page"].value for r in chain] == ["1", "2", "3"]
    assert all(r.cells["page_x"].box is not None for r in chain)  # points at the marker


def test_ocbc_appended_terms_page_is_not_part_of_the_statement() -> None:
    result, *_ = run(StatementSpec(layout="ocbc", txns=default_txns(50)))
    # 3 statement pages + 1 boilerplate page: no rows and no page-chain row for the T&C page
    assert len(result.tables["page_chain"].rows) == 3
    assert len(result.tables["transactions"].rows) == 50


def test_wrapped_description_is_joined_and_balance_forward_and_totals_are_not_rows() -> None:
    txns = default_txns(10)
    txns[2] = Txn(
        txns[2].day,
        "GIRO PAYMENT TO A VERY LONG SUPPLIER NAME TRADING PTE LTD",
        debit=Decimal("20.00"),
    )
    spec = StatementSpec(layout="dbs", txns=txns, wrap_at=30)
    result, *_ = run(spec)
    rows = rows_of(result)
    assert rows[2]["description"] == "GIRO PAYMENT TO A VERY LONG SUPPLIER NAME TRADING PTE LTD"
    assert len(rows) == 10  # b/f row and Total row are not rows
    assert result.fields["opening_balance"].value == "10000.00"
    assert result.fields["total_debits"].value is not None
    assert result.fields["total_credits"].value is not None
    desc = result.tables["transactions"].rows[2].cells["description"]
    assert desc.box is not None
    assert desc.box.h > 0.015  # the box covers both printed lines


def test_wrapped_description_in_every_layout_matches_truth() -> None:
    txns = default_txns(8)
    txns[1] = Txn(
        txns[1].day,
        "FAST PAYMENT FROM ACME TRADING PTE LTD REF INV-1234567890",
        credit=Decimal("500.00"),
    )
    for layout in LAYOUTS:
        spec = StatementSpec(layout=layout, txns=txns, wrap_at=26, wrap_by_layout=False)
        result, *_ = run(spec)
        assert rows_of(result) == statement_truth(spec), layout


def test_id_in_a_description_is_text_never_an_amount() -> None:
    txns = default_txns(6)
    txns[3] = Txn(txns[3].day, "FAST PAYMENT FROM S8222222A REF 5000", credit=Decimal("100.00"))
    result, *_ = run(StatementSpec(layout="dbs", txns=txns))
    row = result.tables["transactions"].rows[3]
    assert row.cells["description"].value == "FAST PAYMENT FROM S8222222A REF 5000"
    assert row.cells["credit"].value == "100.00"
    assert row.cells["debit"].value is None


def test_bracket_negative_balance_keeps_its_sign() -> None:
    txns = [
        Txn(date(2026, 3, 2), "GIRO PAYMENT TO BIG SUPPLIER", debit=Decimal("12000.00")),
        Txn(date(2026, 3, 3), "FAST PAYMENT FROM ACME REF 1", credit=Decimal("500.00")),
    ]
    spec = StatementSpec(layout="dbs", txns=txns)
    result, ctx, doc, _ = run(spec)
    balances = [r.cells["balance"].value for r in result.tables["transactions"].rows]
    assert balances == ["-2000.00", "-1500.00"]
    assert result.tables["transactions"].rows[0].cells["balance"].raw == "(2,000.00)"
    assert validate_bank_statement(result, ctx, doc).failing == {}


def test_invalid_printed_date_keeps_raw_and_is_not_guessed() -> None:
    spec = StatementSpec(layout="dbs", txns=default_txns(6), date_edits={2: "31 Feb 2026"})
    result, *_ = run(spec)
    cell = result.tables["transactions"].rows[2].cells["date"]
    assert cell.value is None
    assert cell.raw == "31 Feb 2026"
    assert any(w.startswith("unparsed: transactions[2].date") for w in result.warnings)
    assert len(result.tables["transactions"].rows) == 6  # the row still starts on the date shape


def test_statement_without_a_balance_column_leaves_balance_empty() -> None:
    spec = StatementSpec(layout="dbs", txns=default_txns(8), drop_balance_column=True)
    result, ctx, doc, _ = run(spec)
    rows = result.tables["transactions"].rows
    assert len(rows) == 8
    assert all(r.cells["balance"].value is None and r.cells["balance"].raw is None for r in rows)
    assert (
        validate_bank_statement(result, ctx, doc).failing == {}
    )  # nothing to test, nothing invented
    assert "no_balance_column" in result.warnings


def test_uob_amount_column_sets_the_side_from_the_printed_suffix() -> None:
    result, *_ = run(StatementSpec(layout="uob", txns=default_txns(10)))
    for row in result.tables["transactions"].rows:
        side = row.cells["credit"] if row.cells["credit"].value else row.cells["debit"]
        assert side.raw is not None
        assert side.raw.endswith(("CR", "DR"))
    credit_rows = [r for r in result.tables["transactions"].rows if r.cells["credit"].value]
    assert credit_rows
    assert all(r.cells["credit"].raw.endswith("CR") for r in credit_rows)


def test_tampered_balance_is_reported_as_printed_and_chain_validation_fails_on_that_page() -> None:
    spec = StatementSpec(layout="dbs", txns=default_txns(30), balance_edits={5: "58,210.75"})
    result, ctx, doc, _ = run(spec)
    assert result.tables["transactions"].rows[5].cells["balance"].value == "58210.75"
    assert validate_bank_statement(result, ctx, doc).failing == {1: "balance_chain"}


def test_tampered_amount_on_page_two_attributes_the_failure_to_page_two() -> None:
    spec = StatementSpec(layout="software", txns=default_txns(40), amount_edits={30: "9,999.00"})
    result, ctx, doc, _ = run(spec)
    assert validate_bank_statement(result, ctx, doc).failing == {2: "balance_chain"}


def test_layout_not_recognised_without_a_table_header() -> None:
    from tests.extract_helpers import render_contract

    ctx, doc, _ = make_case(render_contract(), "bank_statement")
    with pytest.raises(LayoutNotRecognised):
        TL.extract(doc, ctx)


def test_page_without_words_is_not_a_text_layer_document() -> None:
    ctx, doc, _ = make_case(
        render_statement(StatementSpec(txns=default_txns(5))), "bank_statement", keep_words=False
    )
    with pytest.raises(LayoutNotRecognised, match="no text layer"):
        TL.extract(doc, ctx)


def test_rerun_gives_identical_cells() -> None:
    spec = StatementSpec(layout="ocbc", txns=default_txns(30))
    a, *_ = run(spec)
    b, *_ = run(spec)
    assert a.model_dump_json() == b.model_dump_json()
