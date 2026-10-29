"""Cross-document rules and file_reuse (docs/checks-financial.md section 7): T-X-* plus 9.2 tests."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from app.checks.registry import all_checks, run_stage_checks
from app.core.fingerprints import MemoryFingerprintStore
from app.core.models import Annotation, Applicant, Document, FileRef
from app.core.testing import make_doc
from tests.checks_fin_factory import (
    APPLICANT,
    BUYER,
    SELLER,
    UEN_A,
    UEN_B,
    UEN_C,
    R,
    april_statement,
    box,
    clean_bundle,
    ctx_of,
    make_contract,
    make_entity_doc,
    make_fs,
    make_invoice,
    make_po,
    make_statement,
    march_statement,
    put_box,
    put_row_box,
    row_index,
    run_rule,
    skipped,
)

# rule -> (stage, severity, applies_to, requires, use_cases)  -- checks-financial.md section 3.1
ALL_FIN_RULES: dict[str, tuple[str, str, set[str], set[str], set[str]]] = {
    "inv_lines": ("rules", "high", {"invoice"}, set(), {"aml", "loan"}),
    "inv_gst": ("rules", "high", {"invoice"}, set(), {"aml", "loan"}),
    "inv_total": ("rules", "high", {"invoice"}, set(), {"aml", "loan"}),
    "inv_ids": ("rules", "high", {"invoice"}, set(), {"aml", "loan"}),
    "inv_dates": ("rules", "medium", {"invoice"}, set(), {"aml", "loan"}),
    "inv_parties": ("rules", "medium", {"invoice"}, set(), {"aml", "loan"}),
    "inv_duplicate": ("rules", "high", {"invoice"}, set(), {"aml", "loan"}),
    "inv_sequence": ("rules", "low", {"invoice"}, set(), {"aml", "loan"}),
    "fs_balance": ("rules", "high", {"financial_statement"}, set(), {"aml", "loan"}),
    "fs_subtotals": ("rules", "high", {"financial_statement"}, set(), {"aml", "loan"}),
    "fs_cash_tie": ("rules", "high", {"financial_statement"}, set(), {"aml", "loan"}),
    "fs_comparatives": ("rules", "high", {"financial_statement"}, set(), {"aml", "loan"}),
    "fs_entity": ("rules", "medium", {"financial_statement"}, set(), {"aml", "loan"}),
    "x_entity": (
        "crossdoc",
        "high",
        {
            "bank_statement",
            "invoice",
            "financial_statement",
            "contract",
            "purchase_order",
            "company_profile",
            "certificate_of_incorporation",
            "ownership_chart",
            "shareholder_register",
            "source_of_funds_declaration",
        },
        set(),
        {"kyc", "aml", "loan"},
    ),
    "x_cash_at_year_end": (
        "crossdoc",
        "medium",
        {"financial_statement"},
        {"bank_statement"},
        {"aml", "loan"},
    ),
    "x_revenue_vs_credits": (
        "crossdoc",
        "medium",
        {"financial_statement"},
        {"bank_statement"},
        {"aml", "loan"},
    ),
    "x_invoice_vs_receipts": (
        "crossdoc",
        "medium",
        {"invoice"},
        {"bank_statement"},
        {"aml", "loan"},
    ),
    "x_invoice_vs_contract": (
        "crossdoc",
        "medium",
        {"contract", "purchase_order"},
        {"invoice"},
        {"aml", "loan"},
    ),
    "file_reuse": ("crossdoc", "medium", {"*"}, set(), {"kyc", "aml", "loan"}),
}
CROSSDOC_RULES = [r for r, v in ALL_FIN_RULES.items() if v[0] == "crossdoc"]


def one(rule: str, *docs, **kw):  # noqa: ANN001, ANN201
    flags = run_rule(rule, ctx_of(*docs, **kw))
    assert len(flags) == 1, [f.evidence for f in flags]
    return flags[0]


def _safe(rule, ctx):  # noqa: ANN001, ANN202
    try:
        return run_rule(rule, ctx)
    except Exception as exc:  # noqa: BLE001
        assert type(exc).__name__ == "CheckSkipped"
        return []


# ---------------------------------------------------------------- registration and the clean bundle


def test_every_financial_rule_has_the_documented_metadata() -> None:
    by_rule = {m.rule: m for m in all_checks()}
    for rule, (stage, severity, applies_to, requires, use_cases) in ALL_FIN_RULES.items():
        meta = by_rule[rule]
        assert (meta.stage, meta.severity) == (stage, severity), rule
        assert set(meta.applies_to) == applies_to, rule
        assert set(meta.requires) == requires, rule
        assert set(meta.use_cases) == use_cases, rule
        assert meta.version == "1" and meta.tamper and meta.description, rule


def test_clean_bundle_yields_no_flag_above_info_and_no_errors() -> None:
    ctx = ctx_of(*clean_bundle())
    runs = run_stage_checks(ctx, "rules") + run_stage_checks(ctx, "crossdoc")
    mine = set(ALL_FIN_RULES)
    errors = [(r.rule, r.reason) for r in runs if r.rule in mine and r.status == "error"]
    assert errors == []
    loud = [
        (f.id, f.severity, f.evidence)
        for f in ctx.case.flags
        if f.rule in mine and f.severity != "info"
    ]
    assert loud == []
    ran = {r.rule for r in runs if r.status == "ran"}
    assert {
        "x_entity",
        "x_cash_at_year_end",
        "x_revenue_vs_credits",
        "x_invoice_vs_receipts",
        "x_invoice_vs_contract",
        "fs_comparatives",
        "inv_gst",
        "fs_balance",
    } <= ran


def test_kyc_cases_run_only_the_documented_crossdoc_rules() -> None:
    ctx = ctx_of(make_statement(rows=[R("2026-03-02", "X", None, "1.00")]), use_case="kyc")
    runs = {r.rule: r for r in run_stage_checks(ctx, "crossdoc")}
    assert "x_entity" in runs and "file_reuse" in runs
    assert "x_revenue_vs_credits" not in runs and "x_invoice_vs_receipts" not in runs


def test_crossdoc_rules_skip_when_required_documents_are_missing() -> None:
    ctx = ctx_of(make_fs())
    runs = {r.rule: r for r in run_stage_checks(ctx, "crossdoc")}
    assert runs["x_cash_at_year_end"].status == "skipped"
    assert runs["x_revenue_vs_credits"].status == "skipped"
    assert runs["x_invoice_vs_receipts"].status == "skipped"


def test_determinism_and_document_order() -> None:
    bad_fs = make_fs("d5", edits={("balance_sheet", "cash_and_equivalents"): {"current": "400000"}})
    inv = make_invoice(
        "d1", fields={"payment_status": "paid", "paid_date": "2026-03-21", "supplier_name": BUYER}
    )
    docs = [
        inv,
        march_statement("d3"),
        bad_fs,
        make_contract("d7", fields={"buyer_name": "BETA PTE LTD"}),
    ]

    def snapshot(order):  # noqa: ANN001, ANN202
        ctx = ctx_of(*order)
        return [(f.id, f.evidence) for r in CROSSDOC_RULES for f in _safe(r, ctx)]

    assert snapshot(docs) and snapshot(docs) == snapshot(list(reversed(docs)))


def test_untrusted_names_are_truncated_in_evidence() -> None:
    nasty = '"); DROP TABLE cases; --\n' + "C" * 5000
    docs = [
        make_invoice("d1", fields={"supplier_name": nasty}),
        make_entity_doc("certificate_of_incorporation", "d2", name=nasty, uen=UEN_B),
        march_statement("d3"),
    ]
    flags = [f for r in CROSSDOC_RULES for f in _safe(r, ctx_of(*docs))]
    assert flags
    for f in flags:
        assert len(f.evidence) <= 1000 and len(f.explanation) <= 300
        assert "C" * 61 not in f.evidence
        assert "\n" not in f.evidence


# ---------------------------------------------------------------- x_entity


def test_x_entity_clean_with_punctuation_variants() -> None:  # T-X-EN-1
    docs = [
        make_entity_doc("company_profile", "d1", name="Example Logistics Pte. Ltd."),
        make_entity_doc(
            "certificate_of_incorporation", "d2", name="EXAMPLE LOGISTICS PRIVATE LIMITED"
        ),
        make_fs("d3"),
        march_statement("d4"),
        make_invoice("d5"),
    ]
    assert run_rule("x_entity", ctx_of(*docs)) == []


def test_x_entity_same_uen_with_two_names_is_a_conflict() -> None:  # T-X-EN-2
    inv = make_invoice(
        "d1", fields={"supplier_name": BUYER, "buyer_name": "BETA PTE LTD", "buyer_uen": UEN_C}
    )
    put_box(inv, "supplier_uen", box(0.5, 0.2))
    flag = one("x_entity", inv, march_statement("d3"))
    assert flag.severity == "high" and flag.document_ids == ["d1", "d3"]
    assert flag.field == "supplier_uen" and flag.region.x == 0.5
    assert flag.evidence == (f'UEN {UEN_A} is "{BUYER}" on d1 and "{SELLER}" on d3')
    assert flag.id.endswith("~uen_201912345r")
    assert flag.explanation == "The same UEN appears with different company names in this bundle."


def test_x_entity_same_name_with_two_uens_is_a_conflict() -> None:
    flag = one(
        "x_entity",
        make_entity_doc("company_profile", "d1", uen=UEN_A),
        make_entity_doc("certificate_of_incorporation", "d2", uen=UEN_B),
        applicant=Applicant(),
    )
    assert flag.field == "company_name" and flag.id.endswith("~example_logistics")
    assert flag.evidence == f'name "EXAMPLE LOGISTICS" has UEN {UEN_A} on d1 and {UEN_B} on d2'


def test_x_entity_foreign_certificate_against_the_applicant() -> None:  # T-X-EN-3
    cert = make_entity_doc(
        "certificate_of_incorporation", "d8", name="OTHER HOLDINGS PTE LTD", uen=UEN_B
    )
    flag = one("x_entity", cert)
    assert flag.id.endswith("uen~uen_202012345h") and flag.severity == "high"
    assert flag.evidence == f"d8 uen {UEN_B} vs applicant uen {UEN_A}"
    no_uen = make_entity_doc("company_profile", "d9", name="OTHER HOLDINGS PTE LTD", uen=None)
    assert one("x_entity", no_uen).id.endswith("company_name~holdings_other")


def test_x_entity_company_forms_match_across_documents() -> None:  # T-X-EN-4
    app = Applicant(name="Tom & Jerry Pte Ltd", uen=UEN_A, source="upload")
    docs = [
        make_entity_doc("company_profile", "d1", name="Tom & Jerry Pte Ltd"),
        make_entity_doc("certificate_of_incorporation", "d2", name="TOM AND JERRY PRIVATE LIMITED"),
    ]
    assert run_rule("x_entity", ctx_of(*docs, applicant=app)) == []


def test_x_entity_contract_between_two_strangers_is_medium() -> None:
    contract = make_contract(
        "d7",
        fields={
            "seller_name": "ALPHA PTE LTD",
            "seller_uen": UEN_C,
            "buyer_name": "BETA PTE LTD",
            "buyer_uen": UEN_B,
        },
    )
    flag = one("x_entity", contract)
    assert flag.severity == "medium" and flag.id.endswith("seller_name~applicant_absent")
    assert run_rule("x_entity", ctx_of(make_contract("d7"))) == []


def test_x_entity_skips_when_nothing_can_be_compared() -> None:
    assert skipped("x_entity", ctx_of(march_statement("d3"), applicant=Applicant())) is not None


# ---------------------------------------------------------------- x_cash_at_year_end


def _fs_with_cash(cash: str, *, end: str = "2026-03-31") -> Document:
    return make_fs(
        "d5",
        prior=False,
        fields={"period_end": end},
        edits={("balance_sheet", "cash_and_equivalents"): {"current": cash}},
    )


def _bank(rows, **kw) -> Document:  # noqa: ANN001
    return make_statement("d3", rows=rows, opening="100000.00", **kw)


def test_x_cash_year_end_within_five_percent() -> None:  # T-X-CY-1
    bank = _bank([R("2026-03-10", "FAST PAYMENT FROM X", None, "50000.00")])
    assert run_rule("x_cash_at_year_end", ctx_of(_fs_with_cash("152000"), bank)) == []


def test_x_cash_year_end_on_a_sunday_uses_the_friday_balance() -> None:  # T-X-CY-1
    rows = [
        R("2026-03-27", "FAST PAYMENT FROM X", None, "50000.00"),
        R("2026-03-30", "GIRO OUT", "20000.00"),
    ]
    bank = _bank(rows, end="2026-03-31")
    fs = _fs_with_cash("152000", end="2026-03-29")
    assert run_rule("x_cash_at_year_end", ctx_of(fs, bank)) == []
    assert run_rule("x_cash_at_year_end", ctx_of(_fs_with_cash("130000", end="2026-03-29"), bank))


def test_x_cash_year_end_short_flag_has_golden_evidence() -> None:  # T-X-CY-2
    fs = _fs_with_cash("400000")
    put_row_box(
        fs,
        "balance_sheet",
        row_index(fs, "balance_sheet", "cash_and_equivalents"),
        "current",
        box(0.7, 0.3),
    )
    bank = _bank([R("2026-03-10", "FAST PAYMENT FROM X", None, "50000.00")])
    flag = one("x_cash_at_year_end", fs, bank)
    assert flag.evidence == (
        "FS cash at 2026-03-31: 400000.00; bank balance at 2026-03-31: 150000.00 "
        "(1 account, statement d3); diff 250000.00; tolerance 20000.00"
    )
    assert flag.id.endswith("~short") and flag.severity == "medium"
    assert flag.document_ids == ["d3", "d5"]
    assert (flag.table, flag.region.x) == ("balance_sheet", 0.7)
    assert flag.row == row_index(fs, "balance_sheet", "cash_and_equivalents")


def test_x_cash_year_end_excess_when_the_bank_holds_more() -> None:
    bank = _bank([R("2026-03-10", "FAST PAYMENT FROM X", None, "50000.00")])
    flag = one("x_cash_at_year_end", _fs_with_cash("100000"), bank)
    assert flag.id.endswith("~excess")
    assert (
        flag.explanation
        == "The bank statements show more cash on the year-end date than the balance sheet."
    )


def test_x_cash_year_end_cheque_timing_is_an_honest_quirk() -> None:  # T-X-CY-3
    bank = _bank([R("2026-03-10", "FAST PAYMENT FROM X", None, "52500.00")])
    assert run_rule("x_cash_at_year_end", ctx_of(_fs_with_cash("148000"), bank)) == []


def test_x_cash_year_end_sums_accounts() -> None:
    a = _bank([R("2026-03-10", "IN", None, "50000.00")])
    b = make_statement(
        "d4", rows=[R("2026-03-10", "IN", None, "1.00")], opening="99999.00", account="0099999999"
    )
    assert run_rule("x_cash_at_year_end", ctx_of(_fs_with_cash("250000"), a, b)) == []


def test_x_cash_year_end_skips_without_coverage_or_with_other_currency() -> None:  # T-X-CY-4
    april = april_statement("d4")
    assert "2026-03-31" in skipped("x_cash_at_year_end", ctx_of(_fs_with_cash("250000"), april))
    usd = _bank([R("2026-03-10", "IN", None, "50000.00")], currency="USD")
    assert (
        skipped("x_cash_at_year_end", ctx_of(_fs_with_cash("250000"), usd)) == "currency mismatch"
    )


# ---------------------------------------------------------------- x_revenue_vs_credits

H2_START, H2_END = "2025-10-01", "2026-03-31"


def _h2_statement(rows, id: str = "d3") -> Document:  # noqa: A002, ANN001
    return make_statement(id, rows=rows, opening="0.00", start=H2_START, end=H2_END)


def _rv2_rows(annotated: bool = True):  # noqa: ANN202
    def tag(c4: str) -> str | None:
        return c4 if annotated else None

    return [
        R("2025-11-05", "SALES RECEIPT", None, "250000.00", BUYER, tag("trade_receipt")),
        R(
            "2025-12-05",
            "CUSTOMER PAYMENT",
            None,
            "60000.00",
            "BETA PTE LTD",
            tag("customer_payment"),
        ),
        R("2026-01-05", "LOAN DRAWDOWN", None, "400000.00", "A BANK", tag("financing_proceeds")),
        R(
            "2026-02-05",
            "DIRECTOR TRANSFER",
            None,
            "10000.00",
            "TAN WEI MING",
            tag("owner_transfer"),
        ),
    ]


def _fs_revenue(revenue: str, **kw) -> Document:  # noqa: ANN003
    return make_fs(
        "d5", prior=False, edits={("income_statement", "revenue"): {"current": revenue}}, **kw
    )


def test_x_revenue_clean_full_year_coverage() -> None:  # T-X-RV-1
    bank = make_statement(
        "d3",
        opening="0.00",
        start="2025-04-01",
        end="2026-03-31",
        rows=[
            R("2025-06-30", "SALES", None, "570000.00", BUYER, "trade_receipt"),
            R("2026-01-31", "SALES", None, "570000.00", BUYER, "trade_receipt"),
        ],
    )
    assert run_rule("x_revenue_vs_credits", ctx_of(_fs_revenue("1200000"), bank)) == []


def test_x_revenue_shortfall_excludes_non_trade_credits() -> None:  # T-X-RV-2
    fs = _fs_revenue("1800000")
    put_row_box(
        fs,
        "income_statement",
        row_index(fs, "income_statement", "revenue"),
        "current",
        box(0.7, 0.2),
    )
    bank = _h2_statement(_rv2_rows())
    flag = one("x_revenue_vs_credits", fs, bank)
    assert flag.evidence == (
        "FS revenue 1800000.00 for 2025-04-01..2026-03-31 (365 d); statements cover 182 d; "
        "prorated 897534.25; qualifying credits 310000.00 (trade_receipt 250000.00, "
        "customer_payment 60000.00, unclassified 0.00); excluded 410000.00 "
        "(financing_proceeds 400000.00, owner_transfer 10000.00); ratio 0.35 < 0.60"
    )
    assert flag.id.endswith("~shortfall") and flag.severity == "medium"
    assert (flag.table, flag.region.x) == ("income_statement", 0.7)
    assert flag.document_ids == ["d3", "d5"]
    assert "35%" in flag.explanation


def test_x_revenue_without_exclusions_the_tamper_would_pass() -> None:  # T-X-RV-2 / T-X-RV-4
    unclassified = _h2_statement(_rv2_rows(annotated=False))
    assert run_rule("x_revenue_vs_credits", ctx_of(_fs_revenue("1800000"), unclassified)) == []


def test_x_revenue_partial_coverage_and_receivables_are_honest_quirks() -> None:  # T-X-RV-3
    bank = _h2_statement([R("2025-12-05", "SALES", None, "470000.00", BUYER, "trade_receipt")])
    assert run_rule("x_revenue_vs_credits", ctx_of(_fs_revenue("1200000"), bank)) == []


def test_x_revenue_skips_when_coverage_is_too_short() -> None:  # T-X-RV-5
    bank = make_statement(
        "d3", rows=[R("2026-03-05", "IN", None, "10.00")], start="2026-03-01", end="2026-03-12"
    )
    assert skipped("x_revenue_vs_credits", ctx_of(_fs_revenue("1800000"), bank)) is not None
    assert (
        skipped("x_revenue_vs_credits", ctx_of(make_fs("d5"), bank, applicant=APPLICANT))
        is not None
    )


def test_x_revenue_credits_far_above_revenue_are_info_only() -> None:
    bank = _h2_statement([R("2025-12-05", "SALES", None, "3000000.00", BUYER, "trade_receipt")])
    flag = one("x_revenue_vs_credits", _fs_revenue("1200000"), bank)
    assert flag.severity == "info" and flag.id.endswith("~excess") and "> 1.50" in flag.evidence


def test_x_revenue_the_same_statement_uploaded_twice_is_not_double_counted() -> None:
    twice = [
        _h2_statement(_rv2_rows(annotated=True), "d3"),
        _h2_statement(_rv2_rows(annotated=True), "d4"),
    ]
    flag = one("x_revenue_vs_credits", _fs_revenue("1800000"), *twice)
    assert "ratio 0.35" in flag.evidence


def test_x_revenue_other_currency_statements_are_not_used() -> None:
    bank = make_statement(
        "d3", rows=_rv2_rows(), opening="0.00", start=H2_START, end=H2_END, currency="USD"
    )
    assert skipped("x_revenue_vs_credits", ctx_of(_fs_revenue("1800000"), bank)) is not None


# ---------------------------------------------------------------- x_invoice_vs_receipts


def _paid(id: str = "d1", **fields):  # noqa: A002, ANN202, ANN003
    base = {"payment_status": "paid", "paid_date": "2026-04-02"}
    base.update(fields)
    return make_invoice(id, fields=base)


def _march_april(credits, **kw):  # noqa: ANN001, ANN202, ANN003
    march = make_statement(
        "d3", rows=[R("2026-03-02", "SEED", None, "100.00", None, "other")], opening="1000.00"
    )
    april = make_statement(
        "d4", rows=credits, opening="1100.00", start="2026-04-01", end="2026-04-30", **kw
    )
    return march, april


def test_x_receipts_clean_exact_credit() -> None:  # T-X-IR-1
    march, april = _march_april(
        [R("2026-04-03", "FAST PAYMENT FROM ACME", None, "10900.00", BUYER, "trade_receipt")]
    )
    assert run_rule("x_invoice_vs_receipts", ctx_of(_paid(), march, april)) == []


def test_x_receipts_missing_credit_flags_with_golden_evidence() -> None:  # T-X-IR-2
    march, april = _march_april(
        [R("2026-04-03", "FAST PAYMENT", None, "10450.00", BUYER, "trade_receipt")]
    )
    inv = _paid()
    put_box(inv, "total", box(0.8, 0.9))
    flag = one("x_invoice_vs_receipts", inv, march, april)
    assert flag.evidence == (
        "invoice INV-0042 total 10900.00 marked paid (paid_date 2026-04-02); no credit between "
        "2026-03-03 and 2026-04-09 of 10900.00 (tolerance -1.00/+35.00) in 2 statements; "
        "nearest 10450.00 on 2026-04-03"
    )
    assert (flag.field, flag.document_ids, flag.severity) == ("total", ["d1"], "medium")
    assert flag.region.x == 0.8
    assert flag.explanation == (
        "This invoice is marked paid, but no matching deposit appears in the bank statements."
    )


@pytest.mark.parametrize(
    ("credit", "flagged"), [("10875.00", False), ("10850.00", True), ("10905.00", True)]
)
def test_x_receipts_remittance_fees_are_allowed_up_to_35(
    credit: str, flagged: bool
) -> None:  # T-X-IR-3
    march, april = _march_april(
        [R("2026-04-03", "FAST PAYMENT", None, credit, BUYER, "trade_receipt")]
    )
    assert bool(run_rule("x_invoice_vs_receipts", ctx_of(_paid(), march, april))) is flagged


def test_x_receipts_batch_payment_of_two_invoices() -> None:  # T-X-IR-4
    march, april = _march_april(
        [R("2026-04-03", "FAST PAYMENT", None, "16350.00", BUYER, "trade_receipt")]
    )
    one_inv = _paid("d1")
    two_inv = _paid(
        "d2", invoice_number="INV-0043", subtotal="5000.00", gst_amount="450.00", total="5450.00"
    )
    assert run_rule("x_invoice_vs_receipts", ctx_of(one_inv, two_inv, march, april)) == []


def test_x_receipts_outside_coverage_gives_one_info_note() -> None:  # T-X-IR-5
    march, april = _march_april(
        [R("2026-04-03", "FAST PAYMENT", None, "10900.00", BUYER, "trade_receipt")]
    )
    early = _paid(
        "d2", invoice_number="INV-0001", invoice_date="2026-02-10", paid_date="2026-02-12"
    )
    flags = run_rule("x_invoice_vs_receipts", ctx_of(_paid("d1"), early, march, april))
    assert [(f.severity, f.id.rsplit("~", 1)[-1]) for f in flags] == [("info", "uncovered")]
    assert "INV-0001 (outside statement coverage)" in flags[0].evidence
    assert skipped("x_invoice_vs_receipts", ctx_of(early, march, april)) is not None


def test_x_receipts_each_credit_is_used_once() -> None:  # T-X-IR-6
    march, april = _march_april(
        [R("2026-04-03", "FAST PAYMENT", None, "10900.00", BUYER, "trade_receipt")]
    )
    first, second = _paid("d1"), _paid("d2", invoice_number="INV-0043")
    flags = run_rule("x_invoice_vs_receipts", ctx_of(first, second, march, april))
    assert [f.document_ids for f in flags] == [["d2"]]


def test_x_receipts_instalments_from_the_buyer() -> None:  # T-X-IR-7
    credits = [
        R("2026-03-28", "FAST PAYMENT", None, "5000.00", BUYER, "trade_receipt"),
        R("2026-04-03", "FAST PAYMENT", None, "5900.00", BUYER, "trade_receipt"),
    ]
    march, april = _march_april(credits[1:])
    march = make_statement(
        "d3",
        rows=[R("2026-03-28", "FAST PAYMENT", None, "5000.00", BUYER, "trade_receipt")],
        opening="1000.00",
    )
    assert run_rule("x_invoice_vs_receipts", ctx_of(_paid(), march, april)) == []
    stranger = make_statement(
        "d3",
        rows=[R("2026-03-28", "FAST PAYMENT", None, "5000.00", "UNRELATED CO", "trade_receipt")],
        opening="1000.00",
    )
    april2 = make_statement(
        "d4",
        rows=[R("2026-04-03", "FAST PAYMENT", None, "5900.00", "UNRELATED CO", "trade_receipt")],
        opening="6000.00",
        start="2026-04-01",
        end="2026-04-30",
    )
    assert len(run_rule("x_invoice_vs_receipts", ctx_of(_paid(), stranger, april2))) == 1


def test_x_receipts_unpaid_invoices_are_not_evaluated() -> None:
    march, april = _march_april([R("2026-04-03", "FAST PAYMENT", None, "1.00")])
    unpaid = make_invoice("d1", fields={"payment_status": "unpaid"})
    assert skipped("x_invoice_vs_receipts", ctx_of(unpaid, march, april)) is not None


def test_x_receipts_unverified_total_is_noted() -> None:
    march, april = _march_april([R("2026-04-03", "FAST PAYMENT", None, "1.00")])
    inv = _paid()
    inv.fields["total"] = inv.fields["total"].model_copy(update={"verified": False})
    assert one("x_invoice_vs_receipts", inv, march, april).evidence.endswith(
        "; unverified reading: total"
    )


def test_x_receipts_currency_mismatch_is_not_evaluated() -> None:
    march, april = _march_april([R("2026-04-03", "FAST PAYMENT", None, "10900.00")], currency="USD")
    usd_march = make_statement("d3", rows=[], opening="1000.00", currency="USD")
    assert skipped("x_invoice_vs_receipts", ctx_of(_paid(), usd_march, april)) is not None


# ---------------------------------------------------------------- x_invoice_vs_contract


def _cited(id: str = "d1", **fields):  # noqa: A002, ANN202, ANN003
    base = {
        "contract_reference": "C-2026-007",
        "subtotal": "100000.00",
        "gst_amount": "9000.00",
        "total": "109000.00",
    }
    base.update(fields)
    return make_invoice(id, fields=base)


def test_x_contract_partial_billing_inside_the_term_is_clean() -> None:  # T-X-IC-1
    assert run_rule("x_invoice_vs_contract", ctx_of(_cited(), make_contract())) == []


def test_x_contract_overbilling_and_party_mismatch() -> None:  # T-X-IC-2
    inv = _cited(subtotal="650000.00", gst_amount="58500.00", total="708500.00")
    put_box(inv, "total", box(0.75, 0.85))
    flag = one("x_invoice_vs_contract", inv, make_contract())
    assert flag.evidence == (
        "invoice INV-0042 subtotal 650000.00 / total 708500.00 exceeds contract C-2026-007 "
        "value 500000.00"
    )
    assert flag.id.endswith("total~amount") and flag.severity == "medium"
    assert flag.document_ids == ["d1", "d7"] and flag.region.x == 0.75
    party = one(
        "x_invoice_vs_contract",
        _cited(),
        make_contract(fields={"buyer_name": "BETA PTE LTD", "buyer_uen": UEN_C}),
    )
    assert party.id.endswith("buyer_name~party")
    assert party.evidence == f'invoice buyer "{BUYER}" vs contract buyer "BETA PTE LTD"'


def test_x_contract_value_may_include_gst() -> None:  # T-X-IC-3
    contract = make_contract(fields={"contract_value": "109000.00"})
    assert run_rule("x_invoice_vs_contract", ctx_of(_cited(), contract)) == []


def test_x_contract_dates_final_invoice_after_the_term_is_tolerated_for_90_days() -> (
    None
):  # T-X-IC-4
    contract = make_contract(fields={"end_date": "2026-06-30"})
    assert (
        run_rule(
            "x_invoice_vs_contract",
            ctx_of(_cited(invoice_date="2026-08-15", due_date=None), contract),
        )
        == []
    )
    late = one("x_invoice_vs_contract", _cited(invoice_date="2026-11-15", due_date=None), contract)
    assert late.id.endswith("invoice_date~date")
    early = one(
        "x_invoice_vs_contract", _cited(invoice_date="2025-12-20", due_date=None), make_contract()
    )
    assert early.evidence == "invoice_date 2025-12-20 before contract effective_date 2026-01-01"


def test_x_contract_cumulative_overbilling_flags_only_the_third_invoice() -> None:  # T-X-IC-5
    invoices = [
        _cited(
            f"d{i}",
            invoice_number=f"INV-004{i}",
            invoice_date=f"2026-0{i + 2}-10",
            due_date=None,
            subtotal="200000.00",
            gst_amount="18000.00",
            total="218000.00",
        )
        for i in (1, 2, 3)
    ]
    flags = run_rule("x_invoice_vs_contract", ctx_of(*invoices, make_contract("d7")))
    assert [f.id.rsplit("~", 1)[-1] for f in flags] == ["cumulative"]
    assert flags[0].document_ids == ["d3", "d7"]
    assert flags[0].evidence == (
        "running total of 3 invoices citing C-2026-007 is 600000.00 > 500000.00 at INV-0043"
    )


def test_x_contract_missing_cited_document_is_an_info_note() -> None:  # T-X-IC-6
    flags = run_rule(
        "x_invoice_vs_contract", ctx_of(_cited(contract_reference="C-2026-099"), make_contract())
    )
    assert [f.severity for f in flags] == ["info"]
    assert "cites contract C-2026-099" in flags[0].evidence


def test_x_contract_purchase_order_amount_allows_partial_delivery() -> None:
    po = make_po()
    inv = make_invoice(
        "d1",
        fields={
            "po_reference": "PO-7781",
            "subtotal": "60000.00",
            "gst_amount": "5400.00",
            "total": "65400.00",
        },
    )
    assert run_rule("x_invoice_vs_contract", ctx_of(inv, po)) == []
    over = make_invoice(
        "d1",
        fields={
            "po_reference": "PO-7781",
            "subtotal": "150000.00",
            "gst_amount": "13500.00",
            "total": "163500.00",
        },
    )
    flag = one("x_invoice_vs_contract", over, po)
    assert flag.id.endswith("total~amount") and "exceeds PO PO-7781" in flag.evidence
    wrong_buyer = make_po(fields={"buyer_name": "BETA PTE LTD", "buyer_uen": UEN_C})
    assert one("x_invoice_vs_contract", inv, wrong_buyer).id.endswith("~party")


def test_x_contract_milestone_amount_and_currency_rules() -> None:
    contract = make_contract(
        fields={"contract_value": "100000.00"},
        milestones=[{"description": "Phase 2", "due_date": "2026-05-01", "amount": "120000.00"}],
    )
    inv = _cited(subtotal="120000.00", gst_amount="10800.00", total="130800.00")
    assert run_rule("x_invoice_vs_contract", ctx_of(inv, contract)) == []
    usd = make_contract(fields={"contract_value": "1.00", "currency": "USD"})
    assert run_rule("x_invoice_vs_contract", ctx_of(_cited(), usd)) == []


def test_x_contract_skips_when_no_invoice_cites_anything() -> None:
    assert skipped("x_invoice_vs_contract", ctx_of(make_invoice(), make_contract())) is not None


# ---------------------------------------------------------------- file_reuse


def _fileref(file_id: str, name: str = "a.pdf") -> FileRef:
    return FileRef(id=file_id, name=name, sha256="0" * 64, size_bytes=0, mime="application/pdf")


def _file_ctx(contents: list[bytes], *, fingerprints=None, applicant=APPLICANT):  # noqa: ANN001, ANN202
    docs = [make_doc("other", id=f"d{i + 1}", file_id=f"f{i + 1}") for i in range(len(contents))]
    files = [(_fileref(f"f{i + 1}"), c) for i, c in enumerate(contents)]
    return ctx_of(*docs, files=files, fingerprints=fingerprints, applicant=applicant)


def test_file_reuse_clean() -> None:  # T-X-FR-1
    assert run_rule("file_reuse", _file_ctx([b"%PDF-one", b"%PDF-two"])) == []


def test_file_reuse_same_bytes_twice_in_a_case_is_low() -> None:  # T-X-FR-2
    flags = run_rule("file_reuse", _file_ctx([b"%PDF-one", b"%PDF-one"]))
    sha = hashlib.sha256(b"%PDF-one").hexdigest()
    assert len(flags) == 1 and flags[0].severity == "low"
    assert flags[0].id == f"file_reuse.d2.-~{sha[:12]}"
    assert flags[0].evidence == f"file f2 (sha256 {sha[:12]}...) is identical to f1 in this case"


def _store(case_id: str, sha: str, meta: dict) -> MemoryFingerprintStore:
    store = MemoryFingerprintStore()
    store.record("file_sha256", sha, case_id=case_id, document_id="d2", meta=meta)
    return store


def test_file_reuse_store_hit_for_a_different_applicant_is_medium() -> None:  # T-X-FR-3
    sha = hashlib.sha256(b"%PDF-one").hexdigest()
    ctx = _file_ctx([b"%PDF-one"], fingerprints=_store("c_other", sha, {"applicant_uen": UEN_C}))
    (flag,) = run_rule("file_reuse", ctx)
    assert flag.severity == "medium" and flag.id.endswith("~store:c_other")
    assert flag.evidence.startswith(f"file f1 (sha256 {sha[:12]}...) seen in case c_other on ")
    assert flag.evidence.endswith("for a different applicant")
    for private in (UEN_C, UEN_A, "EXAMPLE"):
        assert private not in flag.evidence


def test_file_reuse_store_hit_for_the_same_applicant_is_info() -> None:  # T-X-FR-4
    sha = hashlib.sha256(b"%PDF-one").hexdigest()
    ctx = _file_ctx([b"%PDF-one"], fingerprints=_store("c_old", sha, {"applicant_uen": UEN_A}))
    (flag,) = run_rule("file_reuse", ctx)
    assert flag.severity == "info" and flag.evidence.endswith("for the same applicant")
    no_meta = _file_ctx([b"%PDF-one"], fingerprints=_store("c_old", sha, {}))
    assert run_rule("file_reuse", no_meta)[0].severity == "info"


def test_file_reuse_rerun_does_not_match_itself() -> None:  # T-X-FR-5
    sha = hashlib.sha256(b"%PDF-one").hexdigest()
    ctx = _file_ctx(
        [b"%PDF-one"], fingerprints=_store("c_test00000000", sha, {"applicant_uen": UEN_C})
    )
    assert run_rule("file_reuse", ctx) == []


def test_file_reuse_skips_without_files() -> None:
    assert skipped("file_reuse", ctx_of(make_invoice())) == "no files"


def test_annotation_import_is_used_for_c4_fixtures() -> None:
    ann = Annotation(label="trade_receipt", probability=0.9, classifier="rules", version="1")
    doc = march_statement("d3")
    assert doc.tables["transactions"].rows[0].annotations["c4"].label == ann.label
    assert date.fromisoformat(doc.fields["period_end"].value) > date(2026, 3, 1)
