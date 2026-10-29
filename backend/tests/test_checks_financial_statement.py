"""Financial statement rules (docs/checks-financial.md section 6): T-FS-* and cross-cutting tests."""

from __future__ import annotations

import pytest

from app.checks.registry import all_checks, run_stage_checks
from app.core.models import Applicant
from tests.checks_fin_factory import (
    APPLICANT,
    UEN_A,
    UEN_B,
    box,
    ctx_of,
    make_fs,
    put_box,
    put_row_box,
    row_index,
    run_rule,
    skipped,
)

FS_RULES = {
    "fs_balance": "high",
    "fs_subtotals": "high",
    "fs_cash_tie": "high",
    "fs_comparatives": "high",
    "fs_entity": "medium",
}


def one(rule: str, *docs, **kw):  # noqa: ANN001, ANN201
    flags = run_rule(rule, ctx_of(*docs, **kw))
    assert len(flags) == 1, [f.evidence for f in flags]
    return flags[0]


def drop(doc, table: str, *lines: str) -> None:  # noqa: ANN001
    doc.tables[table].rows = [
        r for r in doc.tables[table].rows if r.cells["line"].value not in lines
    ]


# ---------------------------------------------------------------- registration / cross-cutting


def test_financial_statement_rules_are_registered_with_the_documented_metadata() -> None:
    by_rule = {m.rule: m for m in all_checks()}
    for rule, severity in FS_RULES.items():
        meta = by_rule[rule]
        assert (meta.severity, meta.stage) == (severity, "rules"), rule
        assert meta.applies_to == {"financial_statement"} and meta.requires == frozenset()
        assert meta.use_cases == {"aml", "loan"} and meta.version == "1"
        assert meta.tamper and meta.description


def test_clean_statements_are_silent_for_every_rule() -> None:
    ctx = ctx_of(make_fs("d5", year=1), make_fs("d6", year=0))
    for rule in FS_RULES:
        assert run_rule(rule, ctx) == [], rule


def test_runner_runs_the_financial_statement_rules() -> None:
    ctx = ctx_of(make_fs("d5", year=1), make_fs("d6", year=0))
    runs = {r.rule: r for r in run_stage_checks(ctx, "rules")}
    for rule in FS_RULES:
        assert runs[rule].status == "ran", (rule, runs[rule].reason)


def test_rules_need_a_financial_statement_in_the_bundle() -> None:
    ctx = ctx_of()
    runs = {r.rule: r for r in run_stage_checks(ctx, "rules")}
    assert all(runs[rule].status == "skipped" for rule in FS_RULES)


def test_determinism_and_document_order() -> None:
    bad = make_fs("d5", edits={("balance_sheet", "total_assets"): {"current": "1150000"}})
    old = make_fs("d6", year=0)

    def snapshot(*docs):  # noqa: ANN001, ANN202
        return [(f.id, f.evidence) for r in FS_RULES for f in _safe(r, ctx_of(*docs))]

    assert snapshot(bad, old) and snapshot(bad, old) == snapshot(old, bad)


def _safe(rule, ctx):  # noqa: ANN001, ANN202
    try:
        return run_rule(rule, ctx)
    except Exception as exc:  # noqa: BLE001
        assert type(exc).__name__ == "CheckSkipped"
        return []


def test_untrusted_label_is_truncated() -> None:
    nasty = '"); DROP TABLE cases; --\n' + "B" * 5000
    doc = make_fs(
        edits={("income_statement", "net_profit"): {"current": "1", "label": nasty}},
        fields={"company_name": nasty, "uen": None},
    )
    flags = run_rule("fs_subtotals", ctx_of(doc)) + run_rule("fs_entity", ctx_of(doc))
    assert flags
    for f in flags:
        assert len(f.evidence) <= 1000 and len(f.explanation) <= 300
        assert "B" * 61 not in f.evidence + f.explanation
        assert "\n" not in f.evidence


def test_unverified_reading_is_appended_without_changing_severity() -> None:
    doc = make_fs(edits={("income_statement", "net_profit"): {"current": "124500"}})
    cell = (
        doc.tables["income_statement"]
        .rows[row_index(doc, "income_statement", "net_profit")]
        .cells["current"]
    )
    doc.tables["income_statement"].rows[row_index(doc, "income_statement", "net_profit")].cells[
        "current"
    ] = cell.model_copy(update={"alt_raw": "149,400"})
    flag = one("fs_subtotals", doc)
    assert flag.evidence.endswith("; unverified reading: net_profit")
    assert flag.severity == "high"


# ---------------------------------------------------------------- fs_balance


def test_fs_balance_tampered_flags_total_assets() -> None:  # T-FS-BA-2
    doc = make_fs(
        edits={
            ("balance_sheet", "trade_receivables"): {"current": "350000"},
            ("balance_sheet", "total_current_assets"): {"current": "750000"},
            ("balance_sheet", "total_assets"): {"current": "1150000"},
        }
    )
    put_row_box(
        doc,
        "balance_sheet",
        row_index(doc, "balance_sheet", "total_assets"),
        "current",
        box(0.7, 0.4),
    )
    flag = one("fs_balance", doc)
    assert flag.evidence == (
        "total_liabilities_and_equity 1100000.00; printed total_assets 1150000.00; diff 50000.00"
    )
    assert flag.explanation == "Total assets do not equal total liabilities plus equity."
    assert (flag.table, flag.row) == (
        "balance_sheet",
        row_index(doc, "balance_sheet", "total_assets"),
    )
    assert (flag.region.x, flag.region.y) == (0.7, 0.4)
    assert flag.id.endswith(f"balance_sheet:{flag.row}")


def test_fs_balance_uses_liabilities_plus_equity_when_the_total_is_not_printed() -> None:
    doc = make_fs(
        edits={
            ("balance_sheet", "total_liabilities_and_equity"): {"current": None},
            ("balance_sheet", "total_equity"): {"current": "700000"},
        }
    )
    flag = one("fs_balance", doc)
    assert flag.evidence.startswith(
        "total_liabilities 500000.00 + total_equity 700000.00 = 1200000.00; printed total_assets"
    )


def _thousands(total_equity: str):  # noqa: ANN202
    return make_fs(
        fields={"scale_printed": "1000"},
        prior=False,
        edits={
            ("balance_sheet", "total_assets"): {"current": "10500000"},
            ("balance_sheet", "total_liabilities"): {"current": "6200000"},
            ("balance_sheet", "total_equity"): {"current": total_equity},
            ("balance_sheet", "total_liabilities_and_equity"): {"current": None},
        },
    )


def test_fs_balance_statement_printed_in_thousands_tolerates_rounding() -> None:  # T-FS-BA-3
    assert run_rule("fs_balance", ctx_of(_thousands("4301000"))) == []  # diff 1000 == tolerance
    assert one("fs_balance", _thousands("4302000"))  # diff 2000 > tolerance


def test_fs_balance_accumulated_deficit_balances() -> None:  # T-FS-BA-4
    doc = make_fs(
        prior=False,
        edits={
            ("balance_sheet", "retained_earnings"): {"current": "-800000"},
            ("balance_sheet", "total_equity"): {"current": "-700000"},
            ("balance_sheet", "borrowings_non_current"): {"current": "1500000"},
            ("balance_sheet", "total_non_current_liabilities"): {"current": "1500000"},
            ("balance_sheet", "total_liabilities"): {"current": "1800000"},
        },
    )
    assert run_rule("fs_balance", ctx_of(doc)) == []


def test_fs_balance_prior_column_is_checked_separately() -> None:
    doc = make_fs(edits={("balance_sheet", "total_assets"): {"prior": "999999"}})
    flag = one("fs_balance", doc)
    assert flag.id.endswith("~prior")


def test_fs_balance_derives_totals_from_section_lines_and_skips_without_data() -> None:
    doc = make_fs(prior=False)
    drop(doc, "balance_sheet", "total_assets", "total_current_assets", "total_non_current_assets")
    assert run_rule("fs_balance", ctx_of(doc)) == []  # derived 1,100,000
    empty = make_fs()
    empty.tables["balance_sheet"].rows = []
    assert skipped("fs_balance", ctx_of(empty)) is not None


# ---------------------------------------------------------------- fs_subtotals


def test_fs_subtotals_operating_expenses_edit_hits_profit_before_tax() -> None:  # T-FS-ST-2
    doc = make_fs(edits={("income_statement", "operating_expenses"): {"current": "240000"}})
    flag = one("fs_subtotals", doc)
    assert flag.table == "income_statement"
    assert flag.row == row_index(doc, "income_statement", "profit_before_tax")
    assert flag.evidence == (
        "profit_before_tax: printed 180000.00; gross_profit 500000.00 - operating_expenses "
        "240000.00 - finance_costs 20000.00 = 240000.00; diff 60000.00"
    )
    assert flag.explanation == "The printed Profit Before Tax does not equal the sum of its lines."


def test_fs_subtotals_golden_net_profit_evidence() -> None:
    doc = make_fs(edits={("income_statement", "net_profit"): {"current": "124500"}})
    put_row_box(
        doc,
        "income_statement",
        row_index(doc, "income_statement", "net_profit"),
        "current",
        box(0.8, 0.6),
    )
    flag = one("fs_subtotals", doc)
    assert flag.evidence == (
        "net_profit: printed 124500.00; profit_before_tax 180000.00 - income_tax 30600.00 = "
        "149400.00; diff 24900.00"
    )
    assert (flag.region.x, flag.region.y) == (0.8, 0.6)
    assert flag.extra_regions == [] and flag.severity == "high"


def test_fs_subtotals_balance_sheet_line_edit() -> None:
    doc = make_fs(
        prior=False, edits={("balance_sheet", "trade_receivables"): {"current": "350000"}}
    )
    flags = run_rule("fs_subtotals", ctx_of(doc))
    first = flags[0]
    assert first.evidence == (
        "total_current_assets: printed 700000.00; sum of 3 lines 750000.00; diff 50000.00"
    )
    assert first.table == "balance_sheet"


def test_fs_subtotals_cash_flow_and_closing_cash() -> None:
    doc = make_fs(prior=False, edits={("cash_flow", "net_change_in_cash"): {"current": "70000"}})
    ids = {f.id.split(".")[2] for f in run_rule("fs_subtotals", ctx_of(doc))}
    assert any(i.startswith("cash_flow:") for i in ids)
    flags = run_rule("fs_subtotals", ctx_of(doc))
    assert {f.evidence.split(":")[0] for f in flags} == {"net_change_in_cash", "cash_closing"}


def test_fs_subtotals_unmapped_line_gives_an_info_note_not_a_flag() -> None:  # T-FS-ST-3
    doc = make_fs(
        prior=False,
        extra=[("income_statement", {"label": "Impairment loss", "current": "5000"})],
    )
    flag = one("fs_subtotals", doc)
    assert flag.severity == "info" and flag.id.endswith("~unmapped")
    assert flag.evidence == (
        'subtotal profit_before_tax not verified: unmapped line(s) "Impairment loss"'
    )


def test_fs_subtotals_company_without_cost_of_sales_is_fine() -> None:  # T-FS-ST-4
    doc = make_fs(
        prior=False,
        edits={
            ("income_statement", "revenue"): {"current": "500000"},
            ("income_statement", "profit_before_tax"): {"current": "200000"},
            ("income_statement", "net_profit"): {"current": "200000"},
        },
    )
    drop(doc, "income_statement", "cost_of_sales", "gross_profit", "finance_costs", "income_tax")
    assert run_rule("fs_subtotals", ctx_of(doc)) == []


def test_fs_subtotals_tamper_only_in_the_prior_column() -> None:  # T-FS-ST-5
    doc = make_fs(edits={("income_statement", "net_profit"): {"prior": "100000"}})
    flag = one("fs_subtotals", doc)
    assert flag.id.endswith("~prior") and flag.evidence.endswith("(prior year)")


def test_fs_subtotals_thousands_scale_widens_tolerance() -> None:
    doc = make_fs(
        prior=False,
        fields={"scale_printed": "1000"},
        edits={("income_statement", "net_profit"): {"current": "150400"}},  # diff 1000
    )
    assert run_rule("fs_subtotals", ctx_of(doc)) == []
    doc2 = make_fs(
        prior=False,
        fields={"scale_printed": "1000"},
        edits={("income_statement", "net_profit"): {"current": "151400"}},
    )
    assert one("fs_subtotals", doc2)


def test_fs_subtotals_skips_when_nothing_is_printed() -> None:
    doc = make_fs()
    for table in doc.tables.values():
        table.rows = [r for r in table.rows if r.cells["is_subtotal"].value != "true"]
    assert skipped("fs_subtotals", ctx_of(doc)) is not None


# ---------------------------------------------------------------- fs_cash_tie


def test_fs_cash_tie_tampered_balance_sheet_cash() -> None:  # T-FS-CT-2
    doc = make_fs(edits={("balance_sheet", "cash_and_equivalents"): {"current": "400000"}})
    flag = one("fs_cash_tie", doc)
    assert flag.evidence == (
        "cash_closing 250000.00 (cash flow) vs cash_and_equivalents 400000.00 (balance sheet); "
        "diff 150000.00"
    )
    assert flag.table == "cash_flow" and flag.row == row_index(doc, "cash_flow", "cash_closing")
    assert flag.severity == "high"


def test_fs_cash_tie_overdraft_definition_is_a_documented_false_positive() -> None:  # T-FS-CT-3
    doc = make_fs(edits={("cash_flow", "cash_closing"): {"current": "240000"}})
    assert one("fs_cash_tie", doc).severity == "high"


def test_fs_cash_tie_opening_cash_is_medium() -> None:
    doc = make_fs(edits={("cash_flow", "cash_opening"): {"current": "180000"}})
    flag = one("fs_cash_tie", doc)
    assert flag.severity == "medium" and flag.id.endswith("~opening")


def test_fs_cash_tie_skip() -> None:
    doc = make_fs()
    drop(doc, "cash_flow", "cash_closing", "cash_opening")
    assert skipped("fs_cash_tie", ctx_of(doc)) is not None


# ---------------------------------------------------------------- fs_comparatives


def test_fs_comparatives_tampered_prior_year() -> None:  # T-FS-CO-2
    newer = make_fs(
        "d5",
        edits={
            ("income_statement", "revenue"): {"prior": "1100000"},
            ("income_statement", "gross_profit"): {"prior": "500000"},
        },
    )
    older = make_fs("d6", year=0)
    flags = run_rule("fs_comparatives", ctx_of(older, newer))
    assert [f.evidence.split(":")[0] for f in flags] == ["revenue", "gross_profit"]
    first = flags[0]
    assert first.evidence == (
        "revenue: 2026 prior column 1100000.00 vs 2025 reported 1000000.00; diff 100000.00"
    )
    assert first.document_ids == ["d5", "d6"] and first.severity == "high"
    assert first.explanation == (
        "The prior-year figure for Revenue differs from what last year's statement reported."
    )


def test_fs_comparatives_restated_is_info() -> None:  # T-FS-CO-3
    newer = make_fs(
        "d5",
        edits={
            ("income_statement", "revenue"): {"prior": "1100000", "label": "Revenue (restated)"}
        },
    )
    flags = run_rule("fs_comparatives", ctx_of(newer, make_fs("d6", year=0)))
    revenue = [f for f in flags if f.evidence.startswith("revenue:")]
    assert len(revenue) == 1 and revenue[0].severity == "info"


def test_fs_comparatives_reclassification_inside_a_section_is_silent() -> None:  # T-FS-CO-4
    newer = make_fs(
        "d5",
        edits={
            ("balance_sheet", "trade_receivables"): {"prior": "200000"},
            ("balance_sheet", "inventories"): {"prior": "170000"},
        },
    )
    assert run_rule("fs_comparatives", ctx_of(newer, make_fs("d6", year=0))) == []


def test_fs_comparatives_needs_two_years() -> None:  # T-FS-CO-5
    assert skipped("fs_comparatives", ctx_of(make_fs())) == "needs two years"
    other_entity = make_fs("d6", year=0, fields={"uen": UEN_B, "company_name": "OTHER CO PTE LTD"})
    assert skipped("fs_comparatives", ctx_of(make_fs("d5"), other_entity)) is not None
    far = make_fs("d6", year=0, fields={"period_end": "2024-03-31"})
    assert skipped("fs_comparatives", ctx_of(make_fs("d5"), far)) is not None


def test_fs_comparatives_thousands_rounding_across_years_is_silent() -> None:  # Q06
    older = make_fs("d6", year=0, fields={"scale_printed": "1000"})
    newer = make_fs("d5", edits={("income_statement", "revenue"): {"prior": "1000400"}})
    assert run_rule("fs_comparatives", ctx_of(older, newer)) == []
    far = make_fs("d5", edits={("income_statement", "revenue"): {"prior": "1002000"}})
    assert run_rule("fs_comparatives", ctx_of(older, far))


def test_fs_comparatives_pointer_is_the_prior_cell() -> None:
    newer = make_fs("d5", edits={("income_statement", "revenue"): {"prior": "1100000"}})
    older = make_fs("d6", year=0)
    r = row_index(newer, "income_statement", "revenue")
    cell = newer.tables["income_statement"].rows[r].cells["prior"]
    newer.tables["income_statement"].rows[r].cells["prior"] = cell.model_copy(
        update={"box": box(0.66, 0.2)}
    )
    put_row_box(older, "income_statement", r, "current", box(0.3, 0.2, file_id="f2"))
    flag = run_rule("fs_comparatives", ctx_of(older, newer))[0]
    assert (flag.table, flag.row, flag.region.x) == ("income_statement", r, 0.66)
    assert flag.extra_regions[0].x == 0.3


# ---------------------------------------------------------------- fs_entity


def test_fs_entity_clean_with_punctuation_variants() -> None:  # T-FS-EN-1
    app = Applicant(name="Example Logistics Pte. Ltd.", uen=None, source="upload")
    doc = make_fs(fields={"uen": None, "company_name": "EXAMPLE LOGISTICS PRIVATE LIMITED"})
    assert run_rule("fs_entity", ctx_of(doc, applicant=app)) == []
    assert run_rule("fs_entity", ctx_of(make_fs(), applicant=APPLICANT)) == []


def test_fs_entity_uen_takes_precedence_and_gives_one_flag() -> None:  # T-FS-EN-2
    doc = make_fs(fields={"uen": UEN_B, "company_name": "OTHER HOLDINGS PTE LTD"})
    put_box(doc, "uen", box(0.4, 0.05))
    flag = one("fs_entity", doc)
    assert flag.field == "uen" and flag.severity == "medium"
    assert flag.evidence == f"uen {UEN_B} vs applicant uen {UEN_A}"
    assert flag.id.endswith("~uen_202012345h")
    assert flag.region.x == 0.4


def test_fs_entity_equal_uens_skip_the_name_test() -> None:
    doc = make_fs(fields={"company_name": "A COMPLETELY RENAMED PTE LTD"})
    assert run_rule("fs_entity", ctx_of(doc)) == []


def test_fs_entity_name_flag_when_the_uen_is_unknown() -> None:
    doc = make_fs(fields={"uen": None, "company_name": "OTHER HOLDINGS PTE LTD"})
    flag = one("fs_entity", doc)
    assert flag.field == "company_name"
    assert (
        flag.evidence
        == 'company_name "OTHER HOLDINGS PTE LTD" vs applicant "Example Logistics Pte. Ltd."'
    )
    assert flag.id.endswith("~holdings_other")


def test_fs_entity_skip_without_applicant() -> None:  # T-FS-EN-3
    assert skipped("fs_entity", ctx_of(make_fs(), applicant=Applicant())) == "no applicant"


@pytest.mark.parametrize("rule", sorted(FS_RULES))
def test_documents_that_failed_extraction_are_ignored(rule: str) -> None:
    doc = make_fs(status="failed")
    assert skipped(rule, ctx_of(doc)) is not None
