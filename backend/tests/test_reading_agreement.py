"""Reading unreliable (docs/ingest-and-extraction.md 4.8): disagreement between two OCR readers may
lower a flag, agreement never does, and nothing is ever cleared silently.

The tamper in these tests is a figure printed on the page: both readers print it the same way."""

from __future__ import annotations

from app.checks.registry import run_stage_checks
from app.core.config import Settings
from app.core.models import Cell, Document, Flag, Region
from app.core.testing import make_ctx
from app.reading_agreement import NOTE, apply_reading_agreement, disagreeing_cells
from app.score import collect_partial_reasons, compute_coverage
from tests.test_checks_bank_statement import APPLICANT, cell, statement


def settings(**kw) -> Settings:  # noqa: ANN003
    return Settings(_env_file=None, db_path=":memory:", llm_backend="fake", **kw)


def ctx_for(*docs: Document, on: bool = True):  # noqa: ANN202
    ctx = make_ctx(list(docs), applicant=APPLICANT, settings=settings(multi_reader=on))
    run_stage_checks(ctx, "rules")
    run_stage_checks(ctx, "crossdoc")
    return ctx


def mark(c: Cell, state: str, other: str | None = None) -> None:
    c.agreement = state  # type: ignore[assignment]
    c.other_raw = other
    if state == "differ":
        c.verified = False


def place(doc: Document) -> Document:
    """A box of its own for every cell of the transactions table (one row per line, one column per
    slot), so that "which figures does this flag cite" can be answered."""
    cols = ["date", "description", "debit", "credit", "balance"]
    for i, row in enumerate(doc.tables["transactions"].rows):
        for j, name in enumerate(cols):
            row.cells[name].box = Region(
                file_id="f1", page=1, x=0.05 + 0.18 * j, y=0.20 + 0.03 * i, w=0.15, h=0.02
            )
    for j, c in enumerate(doc.fields.values()):
        c.box = Region(file_id="f1", page=1, x=0.05 + 0.02 * (j % 40), y=0.05, w=0.01, h=0.01)
    return doc


def flags_of(ctx, rule: str) -> list[Flag]:  # noqa: ANN001
    return [f for f in ctx.case.flags if f.rule == rule]


def misread_statement() -> Document:
    """Row 2 prints a debit of 4,200.00; the first reader took it for 4,700.00 (the page is clean)."""
    doc = statement()
    cell(doc, 1, "debit").value = "4700.00"
    cell(doc, 1, "debit").raw = "4,700.00"
    return doc


def test_a_misread_amount_the_readers_disagree_on_is_lowered_with_both_readings() -> None:
    doc = misread_statement()
    mark(cell(doc, 1, "debit"), "differ", "4,200.00")
    ctx = ctx_for(doc)
    before = flags_of(ctx, "bs_running_balance")
    assert before and before[0].severity == "high"
    summary = apply_reading_agreement(ctx)
    after = flags_of(ctx, "bs_running_balance")
    assert summary["reading_unreliable_flags"] >= 1
    assert after[0].severity == "low"
    assert after[0].explanation.startswith(NOTE)
    assert "4,700.00 / 4,200.00" in after[0].evidence
    assert after[0].reading is not None and after[0].reading.severity == "high"


def test_the_same_break_with_agreeing_readers_keeps_its_severity() -> None:
    doc = misread_statement()
    mark(cell(doc, 1, "debit"), "agree")  # both readers print 4,700.00: nothing to doubt
    ctx = ctx_for(doc)
    assert apply_reading_agreement(ctx) == {}
    assert [f.severity for f in flags_of(ctx, "bs_running_balance")] == ["high"]
    assert all(f.reading is None for f in ctx.case.flags)


def test_a_tampered_amount_read_identically_by_both_readers_still_flags_high() -> None:
    doc = statement()
    cell(doc, 2, "debit").value = "212.50"  # edited on the page, so both readers print 212.50
    mark(cell(doc, 2, "debit"), "agree")
    ctx = ctx_for(doc)
    apply_reading_agreement(ctx)
    flags = flags_of(ctx, "bs_running_balance")
    assert flags and flags[0].severity == "high" and flags[0].reading is None


def test_a_real_edit_hidden_behind_a_misread_is_not_lowered_with_it() -> None:
    """A check that reports the first break hides the next one. The misread on row 1 is lowered; the
    edit on row 3, whose figures both readers print alike, is shown at its full severity."""
    doc = place(statement())
    cell(doc, 2, "debit").value = "212.50"  # a real edit, read the same by both
    mark(cell(doc, 2, "debit"), "agree")
    cell(doc, 0, "credit").value = "12600.00"  # an unrelated slip the readers differ on
    mark(cell(doc, 0, "credit"), "differ", "12,500.00")
    ctx = ctx_for(doc)
    first = flags_of(ctx, "bs_running_balance")
    assert [f.row for f in first] == [0]  # the edit on row 3 is hidden behind the first break
    apply_reading_agreement(ctx)
    by_row = {f.row: f for f in flags_of(ctx, "bs_running_balance")}
    assert by_row[0].severity == "low" and by_row[0].reading is not None
    assert by_row[2].severity == "high" and by_row[2].reading is None


def test_a_break_next_to_a_figure_the_readers_differ_on_is_not_trusted_as_independent() -> None:
    doc = place(statement())
    cell(doc, 2, "debit").value = "212.50"
    mark(cell(doc, 2, "debit"), "differ", "12.50")  # the edited cell itself is in doubt
    ctx = ctx_for(doc)
    apply_reading_agreement(ctx)
    assert all(f.severity == "low" for f in flags_of(ctx, "bs_running_balance"))


def test_disagreement_on_a_figure_the_flag_does_not_rest_on_changes_nothing() -> None:
    doc = place(statement())
    cell(doc, 2, "debit").value = "212.50"
    mark(cell(doc, 2, "debit"), "agree")
    mark(
        cell(doc, 4, "credit"), "differ", "3.21"
    )  # a slip on another row, already right in the sum
    ctx = ctx_for(doc)
    apply_reading_agreement(ctx)
    assert any(
        f.severity == "high" and f.reading is None for f in flags_of(ctx, "bs_running_balance")
    )


def test_a_second_reader_that_found_nothing_is_not_a_disagreement() -> None:
    doc = misread_statement()
    mark(cell(doc, 1, "debit"), "unread")
    ctx = ctx_for(doc)
    assert disagreeing_cells(ctx.case) == {}
    assert apply_reading_agreement(ctx) == {}
    assert [f.severity for f in flags_of(ctx, "bs_running_balance")] == ["high"]


def test_off_by_default_and_nothing_is_touched_when_the_switch_is_off() -> None:
    doc = misread_statement()
    mark(cell(doc, 1, "debit"), "differ", "4,200.00")
    ctx = ctx_for(doc, on=False)
    assert apply_reading_agreement(ctx) == {}
    assert [f.severity for f in flags_of(ctx, "bs_running_balance")] == ["high"]
    assert Settings(_env_file=None, llm_backend="fake").multi_reader is False


def test_nothing_is_removed_only_lowered_and_it_is_counted_in_coverage_and_partial_reasons() -> (
    None
):
    doc = misread_statement()
    mark(cell(doc, 1, "debit"), "differ", "4,200.00")
    ctx = ctx_for(doc)
    n = len(ctx.case.flags)
    ids = {f.id for f in ctx.case.flags}
    ratio_before = compute_coverage(ctx.case).ratio
    apply_reading_agreement(ctx)
    assert ids <= {f.id for f in ctx.case.flags}, "no flag was dropped"
    assert len(ctx.case.flags) >= n
    lowered = [f for f in ctx.case.flags if f.reading is not None]
    assert lowered and all(f.severity == "low" for f in lowered)
    assert any(r.startswith("reading_limited:") for r in collect_partial_reasons(ctx.case))
    cov = compute_coverage(ctx.case)
    assert cov.ratio < ratio_before
    assert any(s.get("code") == "reading_limited" for s in cov.skipped)


def test_a_break_that_exists_only_on_the_second_readers_figures_is_not_a_flag_of_its_own() -> None:
    doc = place(statement())
    # the first reader's 18,287.50 is right; the second reader's figure would make the row break
    mark(cell(doc, 2, "balance"), "differ", "18,297.50")
    ctx = ctx_for(doc)
    assert not flags_of(ctx, "bs_running_balance")
    n = len(ctx.case.flags)
    apply_reading_agreement(ctx)
    assert len(ctx.case.flags) == n, "nothing is added for a failure that depends on the dispute"


def test_a_figure_no_ocr_reader_prints_is_dropped_from_the_retry_and_the_flag_is_lowered() -> None:
    """The model read the amount off the page image; neither OCR prints it. The check that fails on it
    is lowered, with the reason; it is not cleared."""
    doc = misread_statement()
    c = cell(doc, 1, "debit")
    c.agreement = "unsupported"
    c.verified = False
    ctx = ctx_for(doc)
    assert flags_of(ctx, "bs_running_balance")[0].severity == "high"
    apply_reading_agreement(ctx)
    flag = flags_of(ctx, "bs_running_balance")[0]
    assert flag.severity == "low" and flag.reading is not None
    assert "printed by neither OCR reader" in flag.evidence


def test_a_figure_printed_by_a_reader_is_not_unsupported() -> None:
    doc = misread_statement()
    cell(doc, 1, "debit").agreement = "agree"
    ctx = ctx_for(doc)
    assert disagreeing_cells(ctx.case) == {}


def test_info_and_low_flags_are_never_touched_and_semantic_flags_are_left_alone() -> None:
    doc = misread_statement()
    mark(cell(doc, 1, "debit"), "differ", "4,200.00")
    ctx = ctx_for(doc)
    low_before = [f for f in ctx.case.flags if f.severity in ("low", "info")]
    apply_reading_agreement(ctx)
    for f in low_before:
        now = next(g for g in ctx.case.flags if g.id == f.id)
        assert now.severity == f.severity and now.reading is None


def test_a_check_that_did_not_run_on_the_retried_figures_has_not_passed() -> None:
    from app.reading_agreement import _passes

    doc = misread_statement()
    ctx = ctx_for(doc)
    flag = flags_of(ctx, "bs_running_balance")[0]
    assert _passes(flag, ({}, {"bs_running_balance"})) is True  # ran, and no longer raised
    assert _passes(flag, ({}, set())) is False  # skipped on the changed figures: not a pass
    assert _passes(flag, ({flag.id: flag}, {"bs_running_balance"})) is False  # raised again
