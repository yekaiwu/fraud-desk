"""Invoice rules (docs/checks-financial.md section 5): T-INV-* plus the cross-cutting tests of 9.2."""

from __future__ import annotations

from datetime import date

import pytest
from stdnum.sg import uen as sg_uen

from app.checks.registry import all_checks, run_stage_checks
from app.core.fingerprints import (
    MemoryFingerprintStore,
    invoice_fingerprint,
    invoice_key_fingerprint,
)
from app.core.models import Applicant
from app.core.testing import TEST_UENS
from tests.checks_fin_factory import (
    APPLICANT,
    BUYER,
    SELLER,
    UEN_A,
    UEN_B,
    UEN_C,
    UEN_D,
    box,
    ctx_of,
    make_contract,
    make_invoice,
    make_statement,
    put_box,
    put_row_box,
    run_rule,
    skipped,
)

INVOICE_RULES = {
    "inv_lines": ("high", "rules"),
    "inv_gst": ("high", "rules"),
    "inv_total": ("high", "rules"),
    "inv_ids": ("high", "rules"),
    "inv_dates": ("medium", "rules"),
    "inv_parties": ("medium", "rules"),
    "inv_duplicate": ("high", "rules"),
    "inv_sequence": ("low", "rules"),
}


def one(rule: str, *docs, **kw):  # noqa: ANN001, ANN201
    flags = run_rule(rule, ctx_of(*docs, **kw))
    assert len(flags) == 1, [f.evidence for f in flags]
    return flags[0]


# ---------------------------------------------------------------- registration / cross-cutting


def test_valid_test_uens_are_valid_under_stdnum() -> None:
    assert set(TEST_UENS) == {UEN_A, UEN_B, UEN_C, UEN_D}
    for value in TEST_UENS:
        assert sg_uen.is_valid(value), value


def test_invoice_rules_are_registered_with_the_documented_metadata() -> None:
    by_rule = {m.rule: m for m in all_checks()}
    for rule, (severity, stage) in INVOICE_RULES.items():
        meta = by_rule[rule]
        assert (meta.severity, meta.stage) == (severity, stage), rule
        assert meta.applies_to == {"invoice"} and meta.requires == frozenset()
        assert meta.use_cases == {"aml", "loan"} and meta.version == "1"
        assert meta.tamper and meta.description


def test_clean_invoice_is_silent_for_every_invoice_rule() -> None:
    ctx = ctx_of(make_invoice())
    for rule in INVOICE_RULES:
        if rule in ("inv_sequence",):
            assert skipped(rule, ctx) is not None  # one invoice: nothing to sequence
            continue
        assert run_rule(rule, ctx) == [], rule


def test_runner_records_invoice_rules_without_errors() -> None:
    ctx = ctx_of(make_invoice())
    runs = {r.rule: r for r in run_stage_checks(ctx, "rules")}
    for rule in INVOICE_RULES:
        assert runs[rule].status in ("ran", "skipped"), (rule, runs[rule].reason)
    assert runs["inv_sequence"].status == "skipped"
    assert runs["inv_gst"].status == "ran"


def test_rules_are_not_applicable_to_kyc_cases_without_an_invoice() -> None:
    # Applicable = use case matches OR the documents are present (docs/architecture.md section 4),
    # so a KYC case that carries no invoice does not count the invoice rules.
    ctx = ctx_of(make_statement(), use_case="kyc")
    assert not [r for r in run_stage_checks(ctx, "rules") if r.rule.startswith("inv_")]


def test_invoice_rules_run_on_a_kyc_case_that_carries_an_invoice() -> None:
    ctx = ctx_of(make_invoice(), use_case="kyc")
    ran = {r.rule for r in run_stage_checks(ctx, "rules") if r.rule.startswith("inv_")}
    assert "inv_dates" in ran


def test_determinism_same_flags_for_any_document_order() -> None:
    a = make_invoice("d1", fields={"gst_amount": "990.00", "total": "19900.00"})
    b = make_invoice("d2", fields={"invoice_number": "INV-0050", "due_date": "2026-01-01"})
    first = [(f.id, f.evidence) for r in INVOICE_RULES for f in _safe(r, ctx_of(a, b))]
    second = [(f.id, f.evidence) for r in INVOICE_RULES for f in _safe(r, ctx_of(b, a))]
    assert first and first == second


def _safe(rule, ctx):  # noqa: ANN001, ANN202
    try:
        return run_rule(rule, ctx)
    except Exception as exc:  # noqa: BLE001 - CheckSkipped
        assert type(exc).__name__ == "CheckSkipped"
        return []


def test_untrusted_text_is_truncated_and_stays_within_limits() -> None:
    nasty = '"); DROP TABLE cases; --\n' + "A" * 5000
    inv = make_invoice(
        fields={
            "supplier_name": nasty,
            "buyer_name": nasty,
            "supplier_uen": nasty[:30],
            "supplier_gst_no": nasty,
        }
    )
    flags = [f for r in INVOICE_RULES for f in _safe(r, ctx_of(inv))]
    assert flags
    for f in flags:
        assert len(f.evidence) <= 1000 and len(f.explanation) <= 300
        assert "A" * 61 not in f.evidence
        assert "\n" not in f.evidence


def test_unverified_reading_is_appended_without_changing_severity() -> None:
    inv = make_invoice(fields={"total": "19900.00"})
    inv.fields["total"] = inv.fields["total"].model_copy(update={"verified": False})
    flag = one("inv_total", inv)
    assert flag.evidence.endswith("; unverified reading: total")
    assert flag.severity == "high"


# ---------------------------------------------------------------- inv_lines


def test_inv_lines_clean() -> None:  # T-INV-LN-1
    assert run_rule("inv_lines", ctx_of(make_invoice())) == []


def test_inv_lines_tampered_line_flags_row_and_sum() -> None:  # T-INV-LN-2
    inv = make_invoice()
    inv.tables["lines"].rows[1].cells["amount"] = (
        inv.tables["lines"].rows[1].cells["amount"].model_copy(update={"value": "2600.00"})
    )
    region = put_row_box(inv, "lines", 1, "amount", box(0.6, 0.5))
    put_row_box(inv, "lines", 1, "quantity", box(0.3, 0.5))
    put_row_box(inv, "lines", 1, "unit_price", box(0.45, 0.5))
    flags = run_rule("inv_lines", ctx_of(inv))
    assert [f.id for f in flags] == [
        "inv_lines.d1.lines:1",
        "inv_lines.d1.subtotal~sum",
    ]
    line, total = flags
    assert (line.table, line.row, line.severity) == ("lines", 1, "high")
    assert (line.region.x, line.region.y, line.region.label) == (region.x, region.y, "amount")
    assert {r.label for r in line.extra_regions} == {"quantity", "unit_price"}
    assert (
        line.evidence
        == "row 2: quantity 20 x unit price 100.00 = 2000.00; printed 2600.00; diff 600.00"
    )
    assert line.explanation == "The amount on line 2 is not quantity times unit price."
    assert total.evidence == "lines sum 10600.00; printed subtotal 10000.00; diff 600.00"


def test_inv_lines_rounded_unit_price_is_an_honest_quirk() -> None:  # T-INV-LN-3
    inv = make_invoice(
        fields={"subtotal": "333.30", "gst_amount": "30.00", "total": "363.30"},
        lines=[
            {"description": "Bulk", "quantity": "1000", "unit_price": "0.33", "amount": "333.30"}
        ],
    )
    assert run_rule("inv_lines", ctx_of(inv)) == []


def test_inv_lines_tax_inclusive_lines_pass_through_the_total() -> None:  # T-INV-LN-4
    inv = make_invoice(
        lines=[
            {
                "description": "Freight Mar",
                "quantity": "4",
                "unit_price": "2000.00",
                "amount": "8000.00",
            },
            {
                "description": "Handling",
                "quantity": "20",
                "unit_price": "145.00",
                "amount": "2900.00",
            },
        ]
    )
    assert run_rule("inv_lines", ctx_of(inv)) == []


@pytest.mark.parametrize(("amount", "flagged"), [("37.50", False), ("37.52", True)])
def test_inv_lines_boundary(amount: str, flagged: bool) -> None:  # T-INV-LN-5
    inv = make_invoice(
        fields={"subtotal": amount},
        lines=[{"description": "Part", "quantity": "3", "unit_price": "12.5", "amount": amount}],
    )
    assert bool(run_rule("inv_lines", ctx_of(inv))) is flagged


def test_inv_lines_skips_without_lines() -> None:
    assert skipped("inv_lines", ctx_of(make_invoice(lines=[]))) is not None


def test_inv_lines_line_without_quantity_is_not_product_checked() -> None:
    inv = make_invoice(
        fields={"subtotal": "150.00", "gst_amount": "13.50", "total": "163.50"},
        lines=[
            {"description": "Delivery", "quantity": None, "unit_price": None, "amount": "150.00"}
        ],
    )
    assert run_rule("inv_lines", ctx_of(inv)) == []


# ---------------------------------------------------------------- inv_gst


def test_inv_gst_clean_net_method() -> None:  # T-INV-GST-1
    assert run_rule("inv_gst", ctx_of(make_invoice())) == []


def test_inv_gst_tampered_has_golden_evidence() -> None:  # T-INV-GST-2
    inv = make_invoice(fields={"gst_amount": "990.00"})
    put_box(inv, "gst_amount", box(0.7, 0.8))
    put_box(inv, "subtotal", box(0.7, 0.7))
    flag = one("inv_gst", inv)
    assert flag.evidence == (
        "gst_amount 990.00; expected 900.00 (net 9.00% of 10000.00), "
        "900.00 (per-line computed); diff 90.00"
    )
    assert flag.explanation == (
        "The GST amount is not 9.00% of the subtotal, computed on the total or line by line."
    )
    assert (flag.field, flag.severity, flag.region.label) == ("gst_amount", "high", "gst_amount")
    assert [r.label for r in flag.extra_regions] == ["subtotal"]


def _per_line_gst_invoice(gst: str) -> object:
    lines = [
        {
            "description": "Item",
            "quantity": "1",
            "unit_price": "1.50",
            "gst_amount": "0.14",
            "amount": "1.50",
        }
        for _ in range(250)
    ]
    return make_invoice(
        fields={"subtotal": "375.00", "gst_amount": gst, "total": "410.00", "gst_rate": "9.00"},
        lines=lines,
    )


def test_inv_gst_accepts_both_net_and_per_line_methods() -> None:  # T-INV-GST-3
    assert run_rule("inv_gst", ctx_of(_per_line_gst_invoice("35.00"))) == []  # per-line sum
    assert run_rule("inv_gst", ctx_of(_per_line_gst_invoice("33.75"))) == []  # net method


def test_inv_gst_per_line_difference_beyond_tolerance_flags() -> None:
    flags = run_rule("inv_gst", ctx_of(_per_line_gst_invoice("37.00")))
    assert len(flags) == 1 and "sum of line GST" in flags[0].evidence


def test_inv_gst_rate_follows_the_invoice_date() -> None:  # T-INV-GST-4
    old = make_invoice(
        fields={
            "invoice_date": "2023-06-01",
            "gst_rate": "8.00",
            "gst_amount": "800.00",
            "total": "10800.00",
        }
    )
    assert run_rule("inv_gst", ctx_of(old)) == []
    new = make_invoice(fields={"gst_rate": "8.00", "gst_amount": "800.00", "total": "10800.00"})
    flag = one("inv_gst", new)
    assert flag.id.endswith("gst_rate~rate") and flag.severity == "medium"
    assert flag.evidence == "printed gst_rate 8.00 on 2026-03-10; statutory 9.00"


def test_inv_gst_seven_percent_edit_is_flagged() -> None:
    flag = one(
        "inv_gst",
        make_invoice(fields={"gst_rate": "7.00", "gst_amount": "700.00", "total": "10700.00"}),
    )
    assert flag.field == "gst_rate" and flag.severity == "medium"


def test_inv_gst_missing_rate_uses_the_statutory_rate() -> None:
    assert run_rule("inv_gst", ctx_of(make_invoice(fields={"gst_rate": None}))) == []
    flag = one("inv_gst", make_invoice(fields={"gst_rate": None, "gst_amount": "700.00"}))
    assert flag.field == "gst_amount"


def test_inv_gst_zero_rated() -> None:  # T-INV-GST-5
    ok = make_invoice(fields={"gst_rate": "0.00", "gst_amount": "0.00", "total": "10000.00"})
    assert run_rule("inv_gst", ctx_of(ok)) == []
    bad = make_invoice(fields={"gst_rate": "0.00", "gst_amount": "50.00", "total": "10050.00"})
    assert one("inv_gst", bad).field == "gst_amount"


def test_inv_gst_skip_and_unevaluated_documents() -> None:
    assert skipped("inv_gst", ctx_of(make_invoice(fields={"gst_amount": None}))) is not None
    good, no_gst = make_invoice("d1"), make_invoice("d2", fields={"gst_amount": None})
    assert run_rule("inv_gst", ctx_of(good, no_gst)) == []


# ---------------------------------------------------------------- inv_total


def test_inv_total_clean() -> None:  # T-INV-TO-1
    assert run_rule("inv_total", ctx_of(make_invoice())) == []


def test_inv_total_tampered() -> None:  # T-INV-TO-2
    inv = make_invoice(fields={"total": "19900.00"})
    region = put_box(inv, "total", box(0.8, 0.9))
    flag = one("inv_total", inv)
    assert (
        flag.evidence
        == "subtotal 10000.00 + gst 900.00 = 10900.00; printed total 19900.00; diff 9000.00"
    )
    assert (
        flag.explanation
        == "Subtotal plus GST is 10900.00, but the invoice prints a total of 19900.00."
    )
    assert (flag.field, flag.region.x, flag.region.y) == ("total", region.x, region.y)


def test_inv_total_honest_quirks() -> None:  # T-INV-TO-3
    assert run_rule("inv_total", ctx_of(make_invoice(fields={"total": "10899.95"}))) == []
    no_gst = make_invoice(fields={"gst_amount": None, "total": "10000.00"})
    assert run_rule("inv_total", ctx_of(no_gst)) == []
    flag = one("inv_total", make_invoice(fields={"gst_amount": None, "total": "11000.00"}))
    assert "(no GST printed)" in flag.evidence


def test_inv_total_boundary_is_inclusive() -> None:
    assert run_rule("inv_total", ctx_of(make_invoice(fields={"total": "10901.00"}))) == []
    assert run_rule("inv_total", ctx_of(make_invoice(fields={"total": "10901.01"})))


def test_inv_total_skip() -> None:
    assert skipped("inv_total", ctx_of(make_invoice(fields={"total": None}))) is not None


# ---------------------------------------------------------------- inv_ids


def test_inv_ids_clean() -> None:  # T-INV-ID-1
    assert run_rule("inv_ids", ctx_of(make_invoice())) == []


@pytest.mark.parametrize(
    ("value", "error"), [("201912345K", "InvalidChecksum"), ("201912345", "InvalidFormat")]
)
def test_inv_ids_bad_supplier_uen(value: str, error: str) -> None:  # T-INV-ID-2
    flag = one("inv_ids", make_invoice(fields={"supplier_uen": value}))
    assert flag.evidence == f"supplier_uen {value} fails UEN validation ({error})"
    assert (flag.field, flag.severity) == ("supplier_uen", "high")


def test_inv_ids_bad_buyer_uen_is_medium() -> None:
    flag = one("inv_ids", make_invoice(fields={"buyer_uen": "202012345K"}))
    assert (flag.field, flag.severity) == ("buyer_uen", "medium")
    assert "InvalidChecksum" in flag.evidence


def test_inv_ids_gst_number_format() -> None:  # T-INV-ID-3
    flag = one("inv_ids", make_invoice(fields={"supplier_gst_no": "M2123456K"}))
    assert (flag.field, flag.severity) == ("supplier_gst_no", "medium")
    assert flag.evidence.startswith(
        "supplier_gst_no M2123456K does not match M + 8 digits + letter"
    )
    assert run_rule("inv_ids", ctx_of(make_invoice(fields={"supplier_gst_no": UEN_A}))) == []
    assert (
        run_rule("inv_ids", ctx_of(make_invoice(fields={"supplier_gst_no": "M-2123-4567-K"}))) == []
    )


def test_inv_ids_sole_proprietor_uen_passes_because_the_library_is_used() -> None:  # T-INV-ID-4
    assert run_rule("inv_ids", ctx_of(make_invoice(fields={"supplier_uen": UEN_D}))) == []


def test_inv_ids_gst_charged_without_a_gst_number() -> None:  # T-INV-ID-5
    flag = one("inv_ids", make_invoice(fields={"supplier_gst_no": None}))
    assert flag.id.endswith("~gst_no_missing") and flag.severity == "medium"
    assert flag.evidence == "GST 900.00 charged but no supplier GST number printed"
    not_tax = make_invoice(
        fields={
            "supplier_gst_no": None,
            "gst_amount": "0.00",
            "gst_rate": "0.00",
            "total": "10000.00",
        }
    )
    assert run_rule("inv_ids", ctx_of(not_tax)) == []


def test_inv_ids_skip() -> None:
    bare = make_invoice(
        fields={
            "supplier_uen": None,
            "supplier_gst_no": None,
            "buyer_uen": None,
            "gst_amount": None,
        }
    )
    assert skipped("inv_ids", ctx_of(bare)) is not None


# ---------------------------------------------------------------- inv_dates


def test_inv_dates_clean_including_dated_today() -> None:  # T-INV-DA-1
    assert run_rule("inv_dates", ctx_of(make_invoice())) == []
    today_inv = make_invoice(fields={"invoice_date": "2026-10-27", "due_date": "2026-11-26"})
    assert run_rule("inv_dates", ctx_of(today_inv)) == []
    on_receipt = make_invoice(fields={"due_date": "2026-03-10"})
    assert run_rule("inv_dates", ctx_of(on_receipt)) == []


def test_inv_dates_tampered() -> None:  # T-INV-DA-2
    due = one("inv_dates", make_invoice(fields={"due_date": "2026-02-01"}))
    assert due.id.endswith("due_date~due") and due.severity == "medium"
    assert due.evidence == "due_date 2026-02-01 is before invoice_date 2026-03-10"
    future = one("inv_dates", make_invoice(fields={"invoice_date": "2026-11-15", "due_date": None}))
    assert future.id.endswith("invoice_date~future")
    assert future.evidence == "invoice_date 2026-11-15 is after today 2026-10-27"


def test_inv_dates_paid_conditions() -> None:
    before = one("inv_dates", make_invoice(fields={"paid_date": "2026-03-01"}))
    assert before.evidence == "paid_date 2026-03-01 is before invoice_date 2026-03-10"
    ahead = one("inv_dates", make_invoice(fields={"paid_date": "2026-12-01"}))
    assert ahead.id.endswith("paid_date~paid_future")


def test_inv_dates_uses_the_injected_today() -> None:  # T-INV-DA-3
    inv = make_invoice(fields={"invoice_date": "2026-11-15", "due_date": None})
    assert run_rule("inv_dates", ctx_of(inv, today=date(2026, 12, 1))) == []
    assert run_rule("inv_dates", ctx_of(inv))


def test_inv_dates_skip() -> None:
    assert skipped("inv_dates", ctx_of(make_invoice(fields={"invoice_date": None}))) is not None


# ---------------------------------------------------------------- inv_parties


def test_inv_parties_clean_applicant_on_either_side() -> None:  # T-INV-PA-1
    assert run_rule("inv_parties", ctx_of(make_invoice())) == []
    buyer_app = Applicant(name="ACME TRADING PTE LTD", uen=UEN_B, source="upload")
    assert run_rule("inv_parties", ctx_of(make_invoice(), applicant=buyer_app)) == []


def test_inv_parties_self_dealing() -> None:  # T-INV-PA-2
    flag = one("inv_parties", make_invoice(fields={"buyer_uen": UEN_A}))
    assert flag.id.endswith("buyer_name~self") and flag.severity == "medium"
    assert "are the same company" in flag.evidence
    by_name = make_invoice(fields={"buyer_name": "Example Logistics Pte. Ltd.", "buyer_uen": None})
    assert one("inv_parties", by_name).id.endswith("~self")


def test_inv_parties_applicant_absent_unless_a_named_counterparty() -> None:  # T-INV-PA-3
    stranger = Applicant(name="OTHER CO PTE LTD", uen=UEN_C, source="upload")
    flag = one("inv_parties", make_invoice(), applicant=stranger)
    assert flag.id.endswith("supplier_name~applicant_absent")
    assert flag.evidence == (
        'supplier "EXAMPLE LOGISTICS PTE LTD" and buyer "ACME TRADING PTE LTD" are neither '
        'applicant "OTHER CO PTE LTD" nor a named counterparty'
    )
    ctx = ctx_of(make_invoice(), make_contract("d7"), applicant=stranger)
    assert run_rule("inv_parties", ctx) == []


def test_inv_parties_suffix_differences_do_not_flag() -> None:  # T-INV-PA-4
    app = Applicant(name="Example Logistics Private Limited", uen=None, source="upload")
    inv = make_invoice(fields={"supplier_name": "Example Logistics Pte. Ltd."})
    assert run_rule("inv_parties", ctx_of(inv, applicant=app)) == []


def test_inv_parties_self_check_runs_without_an_applicant() -> None:
    inv = make_invoice(fields={"buyer_uen": UEN_A})
    assert one("inv_parties", inv, applicant=Applicant()).id.endswith("~self")
    assert (
        skipped(
            "inv_parties",
            ctx_of(
                make_invoice(
                    fields={
                        k: None
                        for k in ("supplier_name", "supplier_uen", "buyer_name", "buyer_uen")
                    }
                )
            ),
        )
        is not None
    )


# ---------------------------------------------------------------- inv_duplicate


def test_inv_duplicate_clean() -> None:  # T-INV-DP-1
    a, b = make_invoice("d1"), make_invoice("d2", fields={"invoice_number": "INV-0043"})
    assert run_rule("inv_duplicate", ctx_of(a, b)) == []
    other_supplier = make_invoice("d2", fields={"supplier_uen": UEN_C})
    assert run_rule("inv_duplicate", ctx_of(make_invoice("d1"), other_supplier)) == []


def test_inv_duplicate_same_invoice_twice_in_the_case() -> None:  # T-INV-DP-2
    d1, d2 = make_invoice("d1"), make_invoice("d2")
    original = put_box(d1, "invoice_number", box(0.2, 0.1))
    put_box(d2, "invoice_number", box(0.2, 0.15))
    fp = invoice_fingerprint(UEN_A, "INV-0042", "2026-03-10", "10900.00")
    flag = one("inv_duplicate", d1, d2)
    assert flag.id == f"inv_duplicate.d1+d2.invoice_number~{fp[:12]}"
    assert flag.evidence == (
        f"invoice INV-0042 of {UEN_A} on 2026-03-10 total 10900.00 also appears as d1 "
        f"(same fingerprint {fp[:8]})"
    )
    assert flag.region.y == 0.15 and flag.extra_regions[0].y == original.y
    assert flag.explanation == "This invoice was already submitted in this case."


def test_inv_duplicate_reused_number_with_a_different_total() -> None:  # T-INV-DP-3
    d1 = make_invoice("d1")
    d2 = make_invoice(
        "d2", fields={"subtotal": "12000.00", "gst_amount": "1080.00", "total": "13080.00"}
    )
    flag = one("inv_duplicate", d1, d2)
    assert "same number, different total" in flag.evidence
    assert flag.id.endswith("~" + invoice_key_fingerprint(UEN_A, "INV-0042")[:12])


def test_inv_duplicate_number_format_change_evades_the_key() -> None:
    d2 = make_invoice("d2", fields={"invoice_number": "INV-042"})
    assert run_rule("inv_duplicate", ctx_of(make_invoice("d1"), d2)) == []


def _store_with(case_id: str, doc_id: str = "d9") -> MemoryFingerprintStore:
    store = MemoryFingerprintStore()
    fp = invoice_fingerprint(UEN_A, "INV-0042", "2026-03-10", "10900.00")
    store.record("invoice", fp, case_id=case_id, document_id=doc_id, meta={"role": "full"})
    return store


def test_inv_duplicate_store_hit_in_another_case() -> None:  # T-INV-DP-4
    flag = one("inv_duplicate", make_invoice(), fingerprints=_store_with("c_other"))
    assert flag.id.endswith("~store:c_other") and flag.severity == "high"
    assert flag.document_ids == ["d1"]


def test_inv_duplicate_rerun_does_not_match_itself() -> None:  # T-INV-DP-4
    store = _store_with("c_test00000000")
    assert run_rule("inv_duplicate", ctx_of(make_invoice(), fingerprints=store)) == []


def test_inv_duplicate_key_store_hit_catches_an_edited_total() -> None:
    store = MemoryFingerprintStore()
    store.record(
        "invoice",
        invoice_key_fingerprint(UEN_A, "INV-0042"),
        case_id="c_old",
        document_id="d2",
        meta={"role": "key"},
    )
    inv = make_invoice(
        fields={"total": "12900.00", "subtotal": "11834.86", "gst_amount": "1065.14"}
    )
    assert one("inv_duplicate", inv, fingerprints=store).id.endswith("~store:c_old")


def test_inv_duplicate_store_evidence_is_private() -> None:  # T-INV-DP-5
    flag = one("inv_duplicate", make_invoice(), fingerprints=_store_with("c_other", "d2"))
    assert "c_other" in flag.evidence and "d2" in flag.evidence
    for private in ("ACME", "EXAMPLE", "10900", "10,900", UEN_A, UEN_B):
        assert private not in flag.evidence
    assert "10900" not in flag.explanation


def test_inv_duplicate_one_flag_per_distinct_earlier_case() -> None:
    store = _store_with("c_a")
    store.record(
        "invoice", invoice_key_fingerprint(UEN_A, "INV-0042"), case_id="c_a", document_id="d3"
    )
    store.record(
        "invoice", invoice_key_fingerprint(UEN_A, "INV-0042"), case_id="c_b", document_id="d1"
    )
    flags = run_rule("inv_duplicate", ctx_of(make_invoice(), fingerprints=store))
    assert sorted(f.id.rsplit("~", 1)[1] for f in flags) == ["store:c_a", "store:c_b"]


def test_inv_duplicate_skips_invoices_without_identity() -> None:
    inv = make_invoice(fields={"supplier_uen": None})
    assert skipped("inv_duplicate", ctx_of(inv)) is not None


# ---------------------------------------------------------------- inv_sequence


def _seq(numbers: list[tuple[str, str]], uen: str = UEN_A, first_id: int = 1) -> list:
    return [
        make_invoice(
            f"d{first_id + i}", fields={"invoice_number": n, "invoice_date": d, "supplier_uen": uen}
        )
        for i, (n, d) in enumerate(numbers)
    ]


def test_inv_sequence_clean() -> None:  # T-INV-SQ-1
    docs = _seq(
        [("INV-0041", "2026-02-20"), ("INV-0042", "2026-02-24"), ("INV-0043", "2026-02-28")]
    )
    assert run_rule("inv_sequence", ctx_of(*docs)) == []


def test_inv_sequence_back_dating_is_low() -> None:  # T-INV-SQ-2
    docs = _seq(
        [("INV-0041", "2026-02-25"), ("INV-0042", "2026-02-24"), ("INV-0043", "2026-02-20")]
    )
    flags = run_rule("inv_sequence", ctx_of(*docs))
    order = [f for f in flags if f.id.endswith("~order")]
    assert order and all(f.severity == "low" for f in order)
    assert any(
        f.evidence == "INV-0043 dated 2026-02-20 precedes INV-0041 dated 2026-02-25" for f in order
    )


def test_inv_sequence_reused_number_with_other_content() -> None:
    docs = _seq(
        [("INV-0041", "2026-02-20"), ("INV-0042", "2026-02-24"), ("INV-0042", "2026-02-26")]
    )
    flags = run_rule("inv_sequence", ctx_of(*docs))
    reuse = [f for f in flags if f.id.endswith("~reuse")]
    assert len(reuse) == 1 and reuse[0].severity == "low" and reuse[0].document_ids == ["d2", "d3"]


def test_inv_sequence_selection_only_gives_an_info_gap() -> None:  # T-INV-SQ-3
    docs = _seq(
        [("INV-0041", "2026-02-10"), ("INV-0045", "2026-02-20"), ("INV-0050", "2026-02-28")]
    )
    flags = run_rule("inv_sequence", ctx_of(*docs))
    assert [f.severity for f in flags] == ["info"] and flags[0].id.endswith("invoice_number~gap")
    assert flags[0].evidence == (
        "numbers INV-0042 to INV-0044 absent between INV-0041 and INV-0045; "
        "numbers INV-0046 to INV-0049 absent between INV-0045 and INV-0050"
    )
    assert flags[0].document_ids == ["d1", "d2", "d3"]


def test_inv_sequence_suppliers_are_grouped_separately() -> None:  # T-INV-SQ-4
    a = _seq([("INV-0041", "2026-02-10"), ("INV-0042", "2026-02-12"), ("INV-0043", "2026-02-14")])
    b = _seq(
        [("INV-0042", "2026-01-10"), ("INV-0043", "2026-01-12"), ("INV-0044", "2026-01-14")],
        uen=UEN_C,
        first_id=4,
    )
    assert run_rule("inv_sequence", ctx_of(*a, *b)) == []
    short = _seq([("INV-0041", "2026-02-10"), ("INV-0042", "2026-02-12")])
    assert skipped("inv_sequence", ctx_of(*short)) is not None


def test_inv_sequence_hint_never_exceeds_low() -> None:
    docs = _seq(
        [("INV-0041", "2026-02-25"), ("INV-0050", "2026-02-24"), ("INV-0043", "2026-02-20")]
    )
    assert {f.severity for f in run_rule("inv_sequence", ctx_of(*docs))} <= {"low", "info"}


# ---------------------------------------------------------------- pointers (9.2 test 8)


def test_row_level_flags_point_at_the_tampered_cell() -> None:
    inv = make_invoice()
    inv.tables["lines"].rows[0].cells["amount"] = (
        inv.tables["lines"].rows[0].cells["amount"].model_copy(update={"value": "9000.00"})
    )
    expected = put_row_box(inv, "lines", 0, "amount", box(0.55, 0.33))
    flag = run_rule("inv_lines", ctx_of(inv))[0]
    assert (flag.table, flag.row) == ("lines", 0)
    assert (flag.region.page, flag.region.x, flag.region.y) == (
        expected.page,
        expected.x,
        expected.y,
    )


def test_applicant_fixture_is_the_documented_one() -> None:
    assert (APPLICANT.name, APPLICANT.uen) == ("Example Logistics Pte. Ltd.", UEN_A)
    assert (SELLER, BUYER) == ("EXAMPLE LOGISTICS PTE LTD", "ACME TRADING PTE LTD")


def test_line_flags_are_capped_with_a_count_of_the_rest() -> None:
    lines = [
        {"description": f"L{i}", "quantity": "2", "unit_price": "10.00", "amount": "99.00"}
        for i in range(7)
    ]
    inv = make_invoice(
        fields={"subtotal": "693.00", "gst_amount": "62.37", "total": "755.37"}, lines=lines
    )
    flags = run_rule("inv_lines", ctx_of(inv))
    assert len(flags) == 5
    assert flags[-1].evidence.endswith("; +2 more")
