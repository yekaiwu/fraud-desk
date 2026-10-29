"""x_declared_vs_activity (checks-kyc-aml.md 4.6; test 14)."""

from __future__ import annotations

import pytest

from app.checks.kyc import declared_categories, x_declared_vs_activity
from app.checks.registry import CheckSkipped
from app.core.models import Cell, Document
from tests.test_checks_kyc_fixtures import company_profile_doc, ctx_for, statement_doc

LOGISTICS = "Freight forwarding and logistics"


def row(date: str, amount: str, cp: str, c5: str, *, credit: bool = True, p: float = 0.9) -> dict:
    return {"date": date, ("credit" if credit else "debit"): amount, "cp": cp, "c5": c5, "p": p}


def run(profile: Document, *statements: Document):  # noqa: ANN201
    return x_declared_vs_activity(ctx_for([profile, *statements], registry=False))


def base_rows(total_other: str = "100000.00") -> list[dict]:
    return [row("2026-01-05", total_other, "SEA FREIGHT PTE LTD", "logistics")]


def test_declared_categories_keyword_mapping() -> None:
    assert declared_categories(LOGISTICS) == {"logistics"}
    assert declared_categories("Wholesale trading and consultancy") == {
        "trading",
        "professional_services",
    }
    assert declared_categories("Crypto asset exchange and blockchain") == {"crypto_virtual_assets"}
    assert declared_categories("Payment services and remittance") == {"money_services"}
    assert declared_categories("Sells flowers") == set()
    assert declared_categories(None) == set()


def test_six_crypto_rows_at_31_percent_is_medium() -> None:
    # turnover 585,000: crypto 182,400 = 31.2%
    crypto = [
        row(f"2026-02-0{i + 1}", "30400.00", "COINHARBOUR PTE LTD", "crypto_virtual_assets")
        for i in range(6)
    ]
    other = [row("2026-03-01", "402600.00", "SEA FREIGHT PTE LTD", "logistics")]
    flags = run(company_profile_doc("d1", activity=LOGISTICS), statement_doc("d7", crypto + other))
    assert len(flags) == 1
    f = flags[0]
    assert f.severity == "medium" and f.table == "transactions" and f.row == 0
    assert f.evidence == (
        "declared: logistics; crypto_virtual_assets: 6 rows, 182400.00 (31.2% of turnover); "
        "top counterparties: COINHARBOUR PTE LTD x6"
    )
    assert (
        f.document_ids == ["d7"]
        and f.id == "x_declared_vs_activity.d7.transactions:0~crypto_virtual_assets"
    )
    assert f.explanation.startswith(
        "The statements show crypto virtual assets activity (31.2% of turnover)"
    )


def test_a_single_small_crypto_row_is_not_flagged() -> None:
    rows = [
        row("2026-02-01", "1000.00", "COINHARBOUR PTE LTD", "crypto_virtual_assets"),
        *base_rows("99000.00"),
    ]
    assert run(company_profile_doc("d1", activity=LOGISTICS), statement_doc("d7", rows)) == []


def test_a_single_large_row_over_five_percent_is_flagged() -> None:
    rows = [
        row("2026-02-01", "10000.00", "COINHARBOUR PTE LTD", "crypto_virtual_assets"),
        *base_rows("90000.00"),
    ]
    assert len(run(company_profile_doc("d1", activity=LOGISTICS), statement_doc("d7", rows))) == 1


def test_money_services_counts_credits_only() -> None:
    debits = [
        row(f"2026-02-0{i + 1}", "5000.00", "STAFF REMIT PTE LTD", "money_services", credit=False)
        for i in range(3)
    ]
    assert (
        run(
            company_profile_doc("d1", activity=LOGISTICS), statement_doc("d7", debits + base_rows())
        )
        == []
    )
    credits = [
        row(f"2026-02-0{i + 1}", "5000.00", "QUICK REMIT PTE LTD", "money_services")
        for i in range(3)
    ]
    flags = run(
        company_profile_doc("d1", activity=LOGISTICS), statement_doc("d7", credits + base_rows())
    )
    assert (
        len(flags) == 1
        and flags[0].severity == "medium"
        and "money_services: 3 rows" in flags[0].evidence
    )


def test_declared_crypto_business_is_not_a_mismatch() -> None:
    crypto = [
        row(f"2026-02-0{i + 1}", "30400.00", "COINHARBOUR PTE LTD", "crypto_virtual_assets")
        for i in range(6)
    ]
    assert (
        run(
            company_profile_doc("d1", activity="Crypto trading platform"),
            statement_doc("d7", crypto),
        )
        == []
    )


def test_dominant_undeclared_activity_is_low() -> None:
    rows = [
        row("2026-02-01", "62000.00", "ALPHA TRADING PTE LTD", "trading"),
        row("2026-02-02", "38000.00", "SEA FREIGHT PTE LTD", "logistics"),
    ]
    flags = run(company_profile_doc("d1", activity="Freight forwarding"), statement_doc("d7", rows))
    assert len(flags) == 1 and flags[0].severity == "low"
    assert (
        flags[0].evidence
        == "62.0% of turnover goes to/comes from 'trading' counterparties; declared: logistics"
    )
    # declared trading as well: no mismatch
    assert (
        run(company_profile_doc("d1", activity="Freight and trading"), statement_doc("d7", rows))
        == []
    )


def test_low_probability_and_other_labels_do_not_count() -> None:
    crypto = [
        row(f"2026-02-0{i + 1}", "30400.00", "COINHARBOUR PTE LTD", "crypto_virtual_assets", p=0.5)
        for i in range(6)
    ]
    assert (
        run(
            company_profile_doc("d1", activity=LOGISTICS), statement_doc("d7", crypto + base_rows())
        )
        == []
    )


def test_multiple_statements_are_combined_and_listed() -> None:
    a = statement_doc(
        "d7",
        [
            row("2026-02-01", "10000.00", "COINHARBOUR PTE LTD", "crypto_virtual_assets"),
            *base_rows("30000.00"),
        ],
    )
    b = statement_doc(
        "d8",
        [
            row("2026-03-01", "10000.00", "COINHARBOUR PTE LTD", "crypto_virtual_assets"),
            *base_rows("30000.00"),
        ],
    )
    flags = run(company_profile_doc("d1", activity=LOGISTICS), a, b)
    assert len(flags) == 1 and flags[0].document_ids == ["d7", "d8"]
    assert "2 rows" in flags[0].evidence and len(flags[0].extra_regions) <= 5


def test_skips() -> None:
    profile = company_profile_doc("d1", activity=LOGISTICS)
    with pytest.raises(CheckSkipped, match="no c5 annotations"):
        run(profile, statement_doc("d7", [{"date": "2026-02-01", "credit": "100.00", "cp": "X"}]))
    with pytest.raises(CheckSkipped, match="declared activity not mappable"):
        run(company_profile_doc("d1", activity="Sells flowers"), statement_doc("d7", base_rows()))
    usd = statement_doc("d8", base_rows(), currency="USD")
    with pytest.raises(CheckSkipped, match="currency mismatch"):
        run(profile, statement_doc("d7", base_rows()), usd)


def test_row_region_is_the_amount_cell() -> None:
    from tests.test_checks_kyc_fixtures import box

    rows = [
        row("2026-02-01", "30000.00", "COINHARBOUR PTE LTD", "crypto_virtual_assets")
        for _ in range(2)
    ]
    doc = statement_doc("d7", [*rows, *base_rows()])
    for r in doc.tables["transactions"].rows[:2]:
        r.cells["credit"] = Cell(
            value=r.cells["credit"].value, kind="money", box=box(y=0.2 + r.index * 0.1)
        )
    f = run(company_profile_doc("d1", activity=LOGISTICS), doc)[0]
    assert f.region is not None and f.region.label == "crypto_virtual_assets" and f.region.y == 0.2
    assert len(f.extra_regions) == 1
