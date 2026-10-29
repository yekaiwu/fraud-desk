"""Page and call budgets in ingest (cost-efficiency.md 4, test 9)."""

from __future__ import annotations

from pathlib import Path

from app.core.llm import FakeLLMClient
from app.ingest.stage import run_stage
from tests.ingest_helpers import ctx_with_files, image_pdf, lines_page, make_pdf

BODY = ["Statement of Account for Example Logistics Pte Ltd", "Opening Balance 1,000.00 forward"]
OCR = {"lines": [{"text": "Statement of Account", "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.02}]}


def test_page_cap_processes_first_pages_only(tmp_path: Path) -> None:
    two = make_pdf([lines_page(BODY)] * 2)
    four = make_pdf([lines_page(BODY + ["more words on this page"])] * 4)
    ctx = ctx_with_files(tmp_path, [two, four], max_pages_per_case=3)
    outcome = run_stage(ctx)
    f1, f2 = ctx.case.files
    assert [p.error for p in f1.pages] == [None, None]
    assert f2.pages[0].error is None
    assert [p.error for p in f2.pages[1:]] == ["not_processed: budget max_pages"] * 3
    assert all(p.width_pt > 0 for p in f2.pages)  # dimensions are still known
    assert ("f2", 2) not in ctx.page_words
    assert outcome.partial is True
    assert outcome.partial_reason == (
        "budget_exceeded: max_pages (limit 3, used 6); 3 pages not processed"
    )
    assert outcome.detail["not_processed_pages"] == 3 and outcome.detail["unreadable_pages"] == 0


def test_page_cap_message_form_from_the_spec(tmp_path: Path) -> None:
    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(BODY)] * 5)], max_pages_per_case=3)
    outcome = run_stage(ctx)
    assert outcome.partial_reason == (
        "budget_exceeded: max_pages (limit 3, used 5); 2 pages not processed"
    )


def test_rerun_with_fresh_meter_has_a_fresh_page_budget(tmp_path: Path) -> None:
    from app.core.costs import BudgetLimits, CostMeter

    ctx = ctx_with_files(tmp_path, [make_pdf([lines_page(BODY)] * 4)], max_pages_per_case=3)
    run_stage(ctx)
    ctx.costs = CostMeter(BudgetLimits.from_settings(ctx.settings))
    ctx.settings = ctx.settings.model_copy(update={"max_pages_per_case": 10})
    ctx.costs = CostMeter(BudgetLimits.from_settings(ctx.settings))
    outcome = run_stage(ctx)
    assert outcome.partial is False
    assert all(p.error is None for p in ctx.case.files[0].pages)


def test_call_cap_leaves_later_scan_pages_unread(tmp_path: Path) -> None:
    llm = FakeLLMClient(fixtures={"ocr:page": OCR})
    ctx = ctx_with_files(tmp_path, [image_pdf(pages=4)], llm=llm, max_llm_calls_per_case=2)
    outcome = run_stage(ctx)
    pages = ctx.case.files[0].pages
    assert [p.error for p in pages[:2]] == [None, None]
    assert [p.error for p in pages[2:]] == ["not_processed: budget max_calls"] * 2
    assert len(llm.calls) == 2
    assert outcome.partial is True
    assert outcome.partial_reason.startswith("budget_exceeded: max_calls (limit 2, used 2)")


def test_budget_trip_is_deterministic_with_many_workers(tmp_path: Path) -> None:
    results = []
    for workers in (1, 4):
        llm = FakeLLMClient(fixtures={"ocr:page": OCR})
        ctx = ctx_with_files(
            tmp_path / f"w{workers}",
            [image_pdf(pages=6)],
            llm=llm,
            max_llm_calls_per_case=3,
            max_page_workers=workers,
        )
        run_stage(ctx)
        results.append([p.error for p in ctx.case.files[0].pages])
    assert results[0] == results[1]
    assert results[0][:3] == [None, None, None]


def test_concurrency_does_not_change_output(tmp_path: Path) -> None:
    outs = []
    for workers in (1, 4):
        llm = FakeLLMClient(fixtures={"ocr:page": OCR})
        ctx = ctx_with_files(
            tmp_path / f"w{workers}", [image_pdf(pages=5)], llm=llm, max_page_workers=workers
        )
        run_stage(ctx)
        outs.append(([p.model_dump() for p in ctx.case.files[0].pages], dict(ctx.page_words)))
    assert outs[0] == outs[1]
