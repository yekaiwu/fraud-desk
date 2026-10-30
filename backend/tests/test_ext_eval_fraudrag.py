"""FraudRAG-style local generator (backend/scripts/ext_eval/fraudrag_local.py): statements, the
five manipulation families, the declared expectations, scoring and one end-to-end pass through the
real pipeline."""

from __future__ import annotations

import random
from decimal import Decimal

import pymupdf
import pytest

from scripts.ext_eval import fraudrag_local as fl


def stmt(seed: int = 3, **kw) -> fl.Stmt:  # noqa: ANN003
    return fl.make_statement(random.Random(seed), "s0001", **kw)


def arithmetic_ok(st: fl.Stmt) -> bool:
    bal = st.opening
    for r in st.rows:
        bal = bal + (r.credit or 0) - (r.debit or 0)
        if fl.q2(bal) != r.balance:
            return False
    td = sum((r.debit or Decimal(0) for r in st.rows), Decimal(0))
    tc = sum((r.credit or Decimal(0) for r in st.rows), Decimal(0))
    return st.closing == fl.q2(bal) and st.total_debits == td and st.total_credits == tc


# ------------------------------------------------------------------------------ generator


def test_statement_is_deterministic_and_arithmetically_clean() -> None:
    a, b = stmt(5), stmt(5)
    assert a == b
    assert stmt(5) != stmt(6)
    assert arithmetic_ok(a)
    assert all(r.balance > 0 for r in a.rows)
    dates = [r.date for r in a.rows if r.date]
    assert dates == sorted(dates) and a.start <= dates[0] and dates[-1] <= a.end


def test_pdf_is_byte_deterministic_and_has_a_text_layer() -> None:
    st = stmt(7, layout="two_col")
    pdf = fl.render_pdf(st)
    assert pdf == fl.render_pdf(st)
    text = "".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))
    assert st.holder in text and st.account in text and "Page 1 of" in text
    one = fl.render_pdf(stmt(7, layout="one_col"))
    assert " CR" in "".join(p.get_text() for p in pymupdf.open(stream=one, filetype="pdf"))


def test_long_statement_has_two_pages() -> None:
    st = stmt(8, n_rows=30)
    assert len(pymupdf.open(stream=fl.render_pdf(st), filetype="pdf")) == 2


# ------------------------------------------------------------------------------ manipulations


def test_naive_amount_edit_breaks_arithmetic_and_consistent_edit_does_not() -> None:
    base = stmt(9)
    naive, t1 = fl.amount_digit(random.Random(1), base, consistent=False)
    cons, t2 = fl.amount_digit(random.Random(1), base, consistent=True)
    assert not arithmetic_ok(naive)
    assert arithmetic_ok(cons)
    assert Decimal(t1.detail["diff"]) > 0 and t1.detail == t2.detail
    assert naive.closing == base.closing  # nothing downstream was touched
    assert t1.rows == t2.rows and len(t1.rows) == 1


def test_balance_variants() -> None:
    base = stmt(10)
    header, _ = fl.balance_closing_header(random.Random(2), base, Decimal("50.00"))
    assert abs(header.closing - base.closing) == Decimal("50.00") and not arithmetic_ok(header)
    row, t = fl.balance_row(random.Random(2), base, Decimal("50.00"))
    assert not arithmetic_ok(row) and row.closing == base.closing and len(t.rows) == 1
    shifted, _ = fl.balance_shifted(random.Random(2), base, Decimal("1000.00"))
    assert arithmetic_ok(shifted) and shifted.closing == base.closing + Decimal("1000.00")


def test_date_variants() -> None:
    base = stmt(11)
    out, _ = fl.date_outside_period(random.Random(3), base)
    assert any(r.date and not (base.start <= r.date <= base.end) for r in out.rows)
    inv, t = fl.date_invalid(random.Random(3), base)
    assert inv.rows[t.rows[0]].date is None
    assert inv.rows[t.rows[0]].date_raw.split()[0] in {"30", "31"}
    order, t = fl.date_out_of_order(random.Random(3), base)
    dates = [r.date for r in order.rows if r.date]
    assert dates != sorted(dates)
    moved, t = fl.date_shift_in_order(random.Random(3), base)
    dates = [r.date for r in moved.rows if r.date]
    assert dates == sorted(dates) and all(base.start <= d <= base.end for d in dates)
    assert arithmetic_ok(moved)


@pytest.mark.parametrize(
    "kind",
    ["unrelated_company", "similar_name", "one_char_typo", "person_name", "suffix_only_benign"],
)
def test_entity_variants_change_only_the_holder(kind: str) -> None:
    base = stmt(12)
    out, t = fl.entity_change(random.Random(4), base, kind)
    assert out.holder != base.holder
    assert out.rows == base.rows and t.variant == kind
    if kind == "suffix_only_benign":
        assert out.holder.replace(".", "") == base.holder


def test_insertions() -> None:
    base = stmt(13)
    naive, t = fl.fabricate_insert(random.Random(5), base, consistent=False)
    assert len(naive.rows) == len(base.rows) + t.detail["inserted"] and not arithmetic_ok(naive)
    cons, _ = fl.fabricate_insert(random.Random(5), base, consistent=True)
    assert arithmetic_ok(cons) and len(cons.rows) == len(naive.rows)
    rnd, t = fl.fabricate_insert(random.Random(5), base, consistent=True, round_sum=True)
    assert t.variant == "insert_consistent_round" and arithmetic_ok(rnd)
    assert any(r.credit and r.credit % 1000 == 0 for r in rnd.rows)
    for i in t.rows:
        assert rnd.rows[i].credit is not None


# ------------------------------------------------------------------------------ the suite


def test_suite_is_deterministic_and_matches_the_declared_expectations() -> None:
    a, b = fl.build_cases(1), fl.build_cases(1)
    assert [c.case_id for c in a] == [c.case_id for c in b]
    assert [c.statements for c in a][:5] == [c.statements for c in b][:5]
    assert len({c.case_id for c in a}) == len(a)
    variants = {f"{c.category}/{c.variant}" for c in a if c.category != "clean"}
    assert variants == set(fl.EXPECTED)
    assert {c.category for c in a if c.category != "clean"} == set(fl.CATEGORIES)
    assert set(fl.EXPECTED) >= fl.BENIGN
    # the applicant is always the TRUE holder, so a changed holder on the page is a mismatch
    for c in a:
        if c.category == "entity_name_mismatch":
            assert c.applicant != c.statements[0].holder
        else:
            assert c.applicant == c.statements[0].holder
    seqs = [c for c in a if c.variant in {"clean_continuity", "continuity_consistent"}]
    assert all(len(c.statements) == 3 for c in seqs)
    assert fl.build_cases(1) != fl.build_cases(2)


def test_sequences_chain_months_and_tamper_only_the_intended_month() -> None:
    cases = {c.case_id: c for c in fl.build_cases(1)}
    clean = cases["clean__clean_continuity__000"]
    for a, b in zip(clean.statements, clean.statements[1:], strict=False):
        assert b.opening == a.closing and a.account == b.account
    many = cases["fabricated_transactions__insert_round_in_sequence_many__000"]
    assert len(many.statements[2].rows) >= len(clean.statements[2].rows) + 5
    rnd = cases["fabricated_transactions__insert_round_in_sequence__000"]
    # same bases as the clean sequence: first two months identical, last one has extra rows
    assert rnd.statements[:2] == clean.statements[:2]
    assert len(rnd.statements[2].rows) > len(clean.statements[2].rows)
    assert arithmetic_ok(rnd.statements[2])
    shift = cases["balance_manipulation__continuity_consistent__000"]
    assert shift.statements[0] == clean.statements[0]
    assert shift.statements[1].opening != clean.statements[1].opening
    assert arithmetic_ok(shift.statements[1])


# ------------------------------------------------------------------------------ scoring


def res(category: str, variant: str, flags: list[dict], rows: list[int] | None = None) -> dict:
    return {
        "ok": True,
        "case_id": "x",
        "category": category,
        "variant": variant,
        "flags": flags,
        "detail": {},
        "tampered_rows": rows or [],
        "rows_expected": 1,
        "rows_parsed": 1,
        "rows_amounts_exact": 1,
        "n_bank_statement": 1,
    }


def flag(rule: str, severity: str = "high", stage: str = "rules", row: int | None = None) -> dict:
    return {"rule": rule, "severity": severity, "stage": stage, "row": row, "table": None}


def test_verdict_counts_only_expected_rules_and_ignores_forensics() -> None:
    r = res(
        "amount_digit_substitution",
        "naive",
        [flag("bs_totals", row=None), flag("pdf_producer_unexpected", "medium", "forensics")],
        [4],
    )
    v = fl.verdict(r)
    assert v["detected"] and v["any_severe"] and v["localised"] is None  # no row on the flag
    r = res("amount_digit_substitution", "naive", [flag("bs_running_balance", row=5)], [4])
    assert fl.verdict(r)["localised"] is True  # next row counts: that is where a break shows
    r = res("amount_digit_substitution", "naive", [flag("bs_running_balance", row=9)], [4])
    assert fl.verdict(r)["localised"] is False
    only_forensics = res(
        "date_forgery", "outside_period", [flag("pdf_producer_unexpected", "medium", "forensics")]
    )
    v = fl.verdict(only_forensics)
    assert not v["detected"] and not v["any_severe"]
    low = res(
        "fabricated_transactions", "insert_round_in_sequence", [flag("bs_round_numbers", "low")]
    )
    v = fl.verdict(low)
    assert not v["detected"] and v["detected_any_severity"]
    assert not fl.verdict(res("date_forgery", "shift_in_order", [flag("bs_dates", "medium")]))[
        "detected"
    ]  # no expectation


def test_aggregate_rates_and_bands() -> None:
    results = [res("clean", "clean", []) for _ in range(4)]
    results += [res("clean", "clean_continuity", [flag("bs_totals")])]
    for diff, hit in (("0.30", False), ("0.40", False), ("20.00", True), ("500.00", True)):
        r = res("amount_digit_substitution", "naive", [flag("bs_totals")] if hit else [], [3])
        r["detail"] = {"diff": diff}
        results.append(r)
    for delta, hit in (("0.01", False), ("5.00", True)):
        r = res("balance_manipulation", "closing_header", [flag("bs_totals")] if hit else [])
        r["detail"] = {"delta": delta}
        results.append(r)
    out = fl.aggregate(results, 1)
    assert out["clean"]["single_statement"]["k"] == 0 and out["clean"]["single_statement"]["n"] == 4
    assert out["clean"]["three_month_sequence"]["k"] == 1
    assert out["clean"]["any_low_flag"]["k"] == 0 and out["clean"]["any_low_flag"]["n"] == 5
    v = {x["variant"]: x for x in out["variants"]}
    assert v["amount_digit_substitution/naive"]["detected"]["k"] == 2
    bands = out["bands"]["amount digit substitution, naive (difference of the edited amount)"]
    assert bands["up to 1.00 (inside the tolerance)"]["n"] == 2
    assert bands["100.00 or more"]["k"] == 1
    assert out["deltas"]["balance_manipulation/closing_header"]["5.00"]["k"] == 1
    md = fl.render_markdown(out)
    assert "Re-implemented from the published description" in md
    assert "amount_digit_substitution/naive" in md


# ------------------------------------------------------------------------------ real pipeline


def test_pipeline_end_to_end_on_a_clean_and_a_naively_edited_statement() -> None:
    base = stmt(21, layout="two_col", n_rows=18)
    edited, truth = fl.amount_digit(random.Random(8), base, consistent=False)
    specs = [
        fl.CaseSpec("clean__t", "clean", "clean", [base], fl.Truth(), base.holder, 0),
        fl.CaseSpec(
            "amount__t",
            "amount_digit_substitution",
            "naive",
            [edited],
            truth,
            base.holder,
            0,
        ),
    ]
    clean, bad = (fl.run_job({"spec": s}) for s in specs)
    assert clean["ok"] and bad["ok"], (clean.get("error"), bad.get("error"))
    assert clean["rows_parsed"] == clean["rows_expected"] == clean["rows_amounts_exact"]
    assert not fl.verdict(clean)["any_severe"]
    diff = Decimal(truth.detail["diff"])
    if diff > 1:
        assert fl.verdict(bad)["detected"]


def test_a_low_flag_on_a_clean_case_is_counted_apart() -> None:
    results = [res("clean", "clean", []), res("clean", "clean", [flag("bs_round_numbers", "low")])]
    out = fl.aggregate(results, 1)
    assert out["clean"]["single_statement"]["k"] == 0  # medium and high only
    assert out["clean"]["any_low_flag"]["k"] == 1 and out["clean"]["any_low_flag"]["n"] == 2
    assert "any low flag" in fl.render_markdown(out)
