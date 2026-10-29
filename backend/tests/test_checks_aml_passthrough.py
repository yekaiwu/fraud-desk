"""bs_pass_through (checks-kyc-aml.md 4.10; tests 18, 19, 20, 28)."""

from __future__ import annotations

import pytest

from app.checks.aml import bs_pass_through
from app.checks.registry import CheckSkipped
from app.core.models import Cell, Document
from tests.test_checks_kyc_fixtures import box, ctx_for, statement_doc


def run(*docs: Document):  # noqa: ANN201
    return bs_pass_through(ctx_for(docs, use_case="aml", registry=False))


def pair(
    day_in: str, amount_in: str, src: str, day_out: str, amount_out: str, dst: str
) -> list[dict]:
    return [
        {"date": day_in, "credit": amount_in, "cp": src, "c4": "trade_receipt"},
        {"date": day_out, "debit": amount_out, "cp": dst},
    ]


def three_pairs(margin: float = 0.015, gap: int = 1) -> list[dict]:
    rows: list[dict] = []
    for i, (src, dst, amount) in enumerate(
        [
            ("ALPHA TRADING PTE LTD", "BETA ENTERPRISE", 100000),
            ("CHARLIE PTE LTD", "DELTA PTE LTD", 80000),
            ("ECHO PTE LTD", "FOXTROT PTE LTD", 60000),
        ]
    ):
        out = f"{amount * (1 - margin):.2f}"
        rows += pair(
            f"2026-02-{3 + i * 5:02d}",
            f"{amount}.00",
            src,
            f"2026-02-{3 + i * 5 + gap:02d}",
            out,
            dst,
        )
    return rows


# ------------------------------------------------------------------ test 18


def test_three_pass_through_pairs_are_one_medium_flag() -> None:
    flags = run(statement_doc("d1", three_pairs()))
    assert len(flags) == 1
    f = flags[0]
    assert f.severity == "medium" and f.table == "transactions" and f.row == 0
    assert f.id == "bs_pass_through.d1.transactions:0"
    assert f.evidence.startswith(
        "3 credits (240000.00, 100.0% of credits) each followed within 5 days by a near-equal debit to another entity; 6 entities; first: "
    )
    assert (
        "2026-02-03 +100000.00 ALPHA TRADING PTE LTD -> 2026-02-04 -98500.00 BETA ENTERPRISE"
        in f.evidence
    )
    assert "circular" not in f.evidence
    assert f.explanation.startswith("3 credits totalling 240000.00 were passed on")


def test_extra_regions_alternate_credit_and_debit_up_to_six() -> None:
    doc = statement_doc("d1", three_pairs())
    for r in doc.tables["transactions"].rows:
        col = "credit" if r.cells["credit"].value else "debit"
        r.cells[col] = Cell(value=r.cells[col].value, kind="money", box=box(y=0.1 + r.index * 0.05))
    f = run(doc)[0]
    assert f.region is not None and f.region.label == "credit" and f.region.y == 0.1
    assert [round(r.y, 2) for r in f.extra_regions] == [
        0.15,
        0.2,
        0.25,
        0.3,
        0.35,
    ]  # d1, c2, d2, c3, d3


def test_circular_entities_are_listed() -> None:
    rows = three_pairs()
    rows += pair(
        "2026-03-01",
        "50000.00",
        "BETA ENTERPRISE",
        "2026-03-02",
        "49000.00",
        "ALPHA TRADING PTE LTD",
    )
    f = run(statement_doc("d1", rows))[0]
    assert (
        "circular: " in f.evidence and "ALPHA TRADING PTE LTD" in f.evidence.split("circular: ")[1]
    )


def test_pairs_can_span_several_statements() -> None:
    rows = three_pairs()
    a = statement_doc("d1", rows[:2] + rows[4:])
    b = statement_doc("d2", rows[2:4], account="999")
    f = run(a, b)
    assert len(f) == 1 and f[0].document_ids == ["d1", "d2"]


# ------------------------------------------------------------------ test 19


def test_ten_percent_margin_is_not_pass_through() -> None:
    assert run(statement_doc("d1", three_pairs(margin=0.10))) == []


def test_two_pairs_only() -> None:
    assert run(statement_doc("d1", three_pairs()[:4])) == []


def test_three_pairs_between_one_source_and_one_destination() -> None:
    rows: list[dict] = []
    for day in (3, 10, 17):
        rows += pair(
            f"2026-02-{day:02d}",
            "50000.00",
            "ALPHA TRADING PTE LTD",
            f"2026-02-{day + 1:02d}",
            "49000.00",
            "BETA ENTERPRISE",
        )
    assert run(statement_doc("d1", rows)) == []  # only 2 entities


def test_gap_of_six_days_is_outside_the_window() -> None:
    assert run(statement_doc("d1", three_pairs(gap=6))) == []
    assert len(run(statement_doc("d1", three_pairs(gap=5)))) == 1  # 5 days is inside
    assert len(run(statement_doc("d1", three_pairs(gap=0)))) == 1  # same day counts


def test_debit_before_the_credit_is_not_a_hop() -> None:
    rows: list[dict] = []
    for i, (src, dst) in enumerate(
        [
            ("A CORP PTE LTD", "B CORP PTE LTD"),
            ("C CORP PTE LTD", "D CORP PTE LTD"),
            ("E CORP PTE LTD", "F CORP PTE LTD"),
        ]
    ):
        d = 3 + i * 5
        rows += pair(f"2026-02-{d:02d}", "50000.00", src, f"2026-02-{d - 1:02d}", "49000.00", dst)
    assert run(statement_doc("d1", rows)) == []


def test_amounts_below_the_minimum_are_ignored() -> None:
    rows: list[dict] = []
    for i, (src, dst) in enumerate(
        [
            ("A CORP PTE LTD", "B CORP PTE LTD"),
            ("C CORP PTE LTD", "D CORP PTE LTD"),
            ("E CORP PTE LTD", "F CORP PTE LTD"),
        ]
    ):
        d = 3 + i * 5
        rows += pair(f"2026-02-{d:02d}", "4999.00", src, f"2026-02-{d + 1:02d}", "4900.00", dst)
    assert run(statement_doc("d1", rows)) == []


def test_matched_volume_under_twenty_percent_of_credits_is_not_flagged() -> None:
    rows = three_pairs()
    rows = [
        dict(r, **({"credit": "10000.00"} if r.get("credit") else {"debit": "9850.00"}))
        for r in rows
    ]
    rows.append(
        {
            "date": "2026-01-02",
            "credit": "170000.00",
            "cp": "BIG CUSTOMER PTE LTD",
            "c4": "trade_receipt",
        }
    )
    # matched 30,000 of 200,000 credits = 15%
    assert run(statement_doc("d1", rows)) == []
    rows[-1]["credit"] = "100000.00"  # 30,000 of 130,000 = 23%
    assert len(run(statement_doc("d1", rows))) == 1


def test_refund_credits_are_not_sources() -> None:
    rows = three_pairs()
    for r in rows:
        if r.get("credit"):
            r["c4"] = "refund"
    assert run(statement_doc("d1", rows)) == []
    for r in rows:
        r.pop("c4", None)
    assert len(run(statement_doc("d1", rows))) == 1  # a missing c4 is allowed


def test_own_account_transfers_are_dropped() -> None:
    rows = three_pairs()
    rows[1]["cp"] = "EXAMPLE LOGISTICS PTE LTD"
    rows[3]["cp"] = "EXAMPLE LOGISTICS PTE LTD"
    assert run(statement_doc("d1", rows)) == []  # two real pairs remain


def test_each_debit_matches_one_credit_only() -> None:
    rows = [
        {"date": "2026-02-03", "credit": "100000.00", "cp": "A CORP PTE LTD"},
        {"date": "2026-02-03", "credit": "100000.00", "cp": "C CORP PTE LTD"},
        {"date": "2026-02-04", "debit": "99000.00", "cp": "B CORP PTE LTD"},
        {"date": "2026-02-10", "credit": "100000.00", "cp": "E CORP PTE LTD"},
        {"date": "2026-02-11", "debit": "99000.00", "cp": "F CORP PTE LTD"},
    ]
    assert (
        run(statement_doc("d1", rows)) == []
    )  # 2 hops: the second credit of day 3 has no debit left


def test_the_closest_debit_wins_and_the_credit_entity_is_excluded() -> None:
    rows = three_pairs()
    # a debit back to the credit's own counterparty is not a hop
    rows.insert(1, {"date": "2026-02-03", "debit": "100000.00", "cp": "ALPHA TRADING PTE LTD"})
    f = run(statement_doc("d1", rows))[0]
    assert "-98500.00 BETA ENTERPRISE" in f.evidence


# ------------------------------------------------------------------ test 20


def test_back_to_back_trader_at_two_percent_is_the_documented_false_alarm() -> None:
    rows: list[dict] = []
    for i, (cust, supp) in enumerate(
        [
            ("CUST ONE PTE LTD", "SUPP ONE PTE LTD"),
            ("CUST TWO PTE LTD", "SUPP TWO PTE LTD"),
            ("CUST THREE PTE LTD", "SUPP THREE PTE LTD"),
        ]
    ):
        d = 3 + i * 4
        rows += pair(f"2026-02-{d:02d}", "50000.00", cust, f"2026-02-{d + 2:02d}", "49000.00", supp)
    flags = run(statement_doc("d1", rows))
    assert len(flags) == 1 and flags[0].severity == "medium"  # honest false alarm, dismissable
    five = [dict(r, **({"debit": "47500.00"} if r.get("debit") else {})) for r in rows]
    assert run(statement_doc("d1", five)) == []


# ------------------------------------------------------------------ skips (test 28)


def test_mixed_currencies_skip() -> None:
    with pytest.raises(CheckSkipped, match="currency mismatch"):
        run(statement_doc("d1", three_pairs()), statement_doc("d2", three_pairs(), currency="USD"))


def test_no_counterparty_rows_skip() -> None:
    doc = statement_doc("d1", [{"date": "2026-02-03", "credit": "50000.00", "desc": " "}])
    with pytest.raises(CheckSkipped, match="no transactions"):
        run(doc)


def test_description_fallback_is_enough_for_the_graph() -> None:
    rows = three_pairs()
    for r in rows:
        r["desc"] = ("FAST PAYMENT FROM " if r.get("credit") else "FAST PAYMENT TO ") + r.pop("cp")
    assert len(run(statement_doc("d1", rows))) == 1
