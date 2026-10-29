"""x_sof_support (checks-kyc-aml.md 4.8; tests 15, 16, 28)."""

from __future__ import annotations

import pytest

from app.checks.aml import x_sof_support
from app.checks.registry import CheckSkipped
from app.core.models import Cell, Document
from tests.test_checks_kyc_fixtures import box, ctx_for, sof_doc, statement_doc

ALPHA = "ALPHA TRADING PTE LTD"


def credits(
    amounts: list[str],
    cp: str = ALPHA,
    c4: str | None = "trade_receipt",
    start_day: int = 1,
    p: float = 0.9,
) -> list[dict]:
    return [
        {"date": f"2026-03-{start_day + i:02d}", "credit": a, "cp": cp, "c4": c4, "p": p}
        for i, a in enumerate(amounts)
    ]


def run(decl: Document, *statements: Document):  # noqa: ANN201
    return x_sof_support(ctx_for([decl, *statements], use_case="aml", registry=False))


# ------------------------------------------------------------------ test 15


def test_312k_of_1m_is_high_with_the_unsupported_part_and_unclassified_credits() -> None:
    rows = credits(["62480.00"] * 5) + credits(
        ["6000.00", "6000.00", "6200.00"], cp="MISC PAYER", c4=None, start_day=10
    )
    doc = statement_doc("d7", rows)
    amount_cell = Cell(value="1000000.00", kind="money", box=box(y=0.4))
    decl = sof_doc("d5", counterparty=None)
    decl.fields["declared_amount"] = amount_cell
    flags = run(decl, doc)
    assert len(flags) == 1
    f = flags[0]
    assert (
        f.severity == "high"
        and f.field == "declared_amount"
        and f.id == "x_sof_support.d5+d7.declared_amount"
    )
    assert f.evidence == (
        "declared 1000000.00 (sales_proceeds, 2026-01-01..2026-06-30); 5 supporting credits total "
        "312400.00; unsupported 687600.00 (68.8%); 3 credits unclassified (18200.00)"
    )
    assert f.region is not None and f.region.label == "declared_amount" and f.region.y == 0.4
    assert f.explanation.startswith("Only 312400.00 of the declared 1000000.00 is supported")


def test_995k_supported_is_inside_the_one_percent_tolerance() -> None:
    doc = statement_doc("d7", credits(["199000.00"] * 5))
    assert run(sof_doc("d5"), doc) == []


def test_tolerance_boundary() -> None:
    inside = statement_doc("d7", credits(["990000.00"]))  # unsupported 10000 == 1% of declared
    assert run(sof_doc("d5"), inside) == []
    outside = statement_doc("d7", credits(["989999.00"]))
    assert len(run(sof_doc("d5"), outside)) == 1


def test_overshoot_is_fine() -> None:
    assert run(sof_doc("d5"), statement_doc("d7", credits(["600000.00", "600000.00"]))) == []


def test_unclassified_credits_do_not_support() -> None:
    doc = statement_doc("d7", credits(["500000.00", "500000.00"], c4=None))
    f = run(sof_doc("d5"), doc)[0]
    assert (
        "0 supporting credits total 0.00" in f.evidence
        and "2 credits unclassified (1000000.00)" in f.evidence
    )
    low = statement_doc("d7", credits(["500000.00", "500000.00"], p=0.4))
    assert (
        "2 credits unclassified" in run(sof_doc("d5"), low)[0].evidence
    )  # a low-probability label is not trusted


def test_medium_when_the_unsupported_part_is_under_a_quarter() -> None:
    doc = statement_doc("d7", credits(["400000.00", "400000.00"]))
    f = run(sof_doc("d5"), doc)[0]
    assert f.severity == "medium" and "unsupported 200000.00 (20.0%)" in f.evidence
    just_over = statement_doc("d7", credits(["750000.00"]))
    assert run(sof_doc("d5"), just_over)[0].severity == "high"  # exactly 25% is high


def test_named_counterparty_must_match() -> None:
    rows = credits(["500000.00"]) + credits(["500000.00"], cp="BETA HOLDINGS PTE LTD", start_day=5)
    decl = sof_doc("d5", counterparty="Alpha Trading Pte Ltd")
    f = run(decl, statement_doc("d7", rows))[0]
    assert "1 supporting credit total 500000.00" in f.evidence
    # a truncated bank description still counts
    trunc = statement_doc("d7", credits(["1000000.00"], cp="ALPHA TRADING PTE L"))
    assert run(decl, trunc) == []


def test_window_and_declaration_date_exclude_credits() -> None:
    early = [{"date": "2025-12-30", "credit": "1000000.00", "cp": ALPHA, "c4": "trade_receipt"}]
    assert len(run(sof_doc("d5"), statement_doc("d7", early))) == 1  # before period_start
    late = [{"date": "2026-08-01", "credit": "1000000.00", "cp": ALPHA, "c4": "trade_receipt"}]
    assert (
        len(run(sof_doc("d5", end="2026-09-30", declared="2026-07-01"), statement_doc("d7", late)))
        == 1
    )
    # no explicit window: starts at the statement start, ends at the declaration date
    inside = [{"date": "2026-06-30", "credit": "1000000.00", "cp": ALPHA, "c4": "trade_receipt"}]
    assert run(sof_doc("d5", start=None, end=None), statement_doc("d7", inside)) == []


def test_loan_origin_needs_financing_proceeds() -> None:
    decl = sof_doc("d5", origin="loan", amount="100000.00")
    ok = statement_doc("d7", credits(["100000.00"], c4="financing_proceeds"))
    assert run(decl, ok) == []
    wrong = statement_doc("d7", credits(["100000.00"], c4="trade_receipt"))
    assert len(run(decl, wrong)) == 1


def test_asset_sale_with_a_named_person_uses_the_person_matcher() -> None:
    decl = sof_doc("d5", origin="asset_sale", amount="50000.00", counterparty="Mr Tan Ah Kow")
    paid = statement_doc("d7", credits(["50000.00"], cp="Kow Ah Tan", c4="owner_transfer"))
    assert run(decl, paid) == []
    refund = statement_doc("d7", credits(["50000.00"], cp="TAN AH KOW", c4="refund"))
    assert len(run(decl, refund)) == 1
    other = statement_doc("d7", credits(["50000.00"], cp="LEE AH KOW", c4="owner_transfer"))
    assert len(run(decl, other)) == 1


def test_multiple_statements_and_receiving_account() -> None:
    a = statement_doc("d7", credits(["500000.00"]), account="111-111-111")
    b = statement_doc("d8", credits(["500000.00"]), account="222-222-222")
    both = run(sof_doc("d5"), a, b)
    assert both == []
    only_a = run(sof_doc("d5", account="111 111 111"), a, b)  # account number normalised
    assert len(only_a) == 1 and only_a[0].document_ids == ["d5", "d7"]


# ------------------------------------------------------------------ test 16 and 28


def test_receiving_account_not_in_any_statement_is_a_medium_flag_not_a_skip() -> None:
    """Declaring the funds into an account that was not uploaded must not avoid the check."""
    flags = run(sof_doc("d5", account="999-999-999"), statement_doc("d7", credits(["1.00"])))
    assert len(flags) == 1
    f = flags[0]
    assert f.rule == "x_sof_support" and f.severity == "medium" and f.document_ids == ["d5"]
    assert "no statement for that account was supplied" in f.evidence
    assert "999-999-999" not in f.evidence  # the account number is masked


def test_statement_with_an_unreadable_account_number_is_still_judged() -> None:
    blank = statement_doc("d7", credits(["1000000.00"]), account="")
    assert run(sof_doc("d5", account="999-999-999"), blank) == []


def test_untestable_origin_skips() -> None:
    with pytest.raises(CheckSkipped, match="origin not testable without a named counterparty"):
        run(
            sof_doc("d5", origin="inheritance", counterparty=None),
            statement_doc("d7", credits(["1.00"])),
        )


def test_currency_mismatch_skips() -> None:
    with pytest.raises(CheckSkipped, match="currency mismatch"):
        run(sof_doc("d5", currency="USD"), statement_doc("d7", credits(["1000000.00"])))


def test_one_testable_declaration_is_enough_to_run() -> None:
    bad = sof_doc("d5", origin="inheritance")
    good = sof_doc("d6")
    flags = run(bad, good, statement_doc("d7", credits(["100.00"])))
    assert len(flags) == 1 and flags[0].document_ids == ["d6", "d7"]


def test_unreadable_amount_skips() -> None:
    decl = sof_doc("d5")
    decl.fields["declared_amount"] = Cell(value=None, kind="money")
    with pytest.raises(CheckSkipped):
        run(decl, statement_doc("d7", credits(["1.00"])))


def test_money_tolerance_floor_applies_to_small_declarations() -> None:
    decl = sof_doc("d5", amount="50.00")
    assert run(decl, statement_doc("d7", credits(["49.50"]))) == []
