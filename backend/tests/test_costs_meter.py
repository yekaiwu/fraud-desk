import json
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.costs import (
    BUILTIN_PRICES,
    BudgetLimits,
    CostMeter,
    LLMBudgetExceeded,
    ModelPrice,
    PriceTable,
    summarise_costs,
)
from app.core.llm import LLMError
from app.core.models import Case, StageRecord
from app.core.testing import FIXED_NOW, FIXED_TODAY


def prices(**kw) -> PriceTable:  # noqa: ANN003
    return PriceTable(
        {"m": ModelPrice(input_per_mtok=Decimal("0.15"), output_per_mtok=Decimal("0.60"), **kw)}
    )


def meter(*, pages=200, calls=100, usd="2.00", table=None) -> CostMeter:  # noqa: ANN001
    return CostMeter(
        BudgetLimits(max_pages=pages, max_calls=calls, max_usd=Decimal(usd)), table or prices()
    )


def test_price_table() -> None:
    t = prices()
    assert t.cost("m", 1_000_000, 500_000) == (Decimal("0.450000"), True)
    assert t.cost("unknown", 10, 10) == (Decimal("0"), False)
    assert t.cost("fake", 10**9, 10**9) == (Decimal("0"), True)
    assert t.cost("m", 1, 1)[0] == Decimal("0.000001")  # 0.75e-6 rounds half up to 1e-6


def test_cached_input_price() -> None:
    t = prices(cached_input_per_mtok=Decimal("0.015"))
    full = t.cost("m", 1_000_000, 0)[0]
    cached = t.cost("m", 1_000_000, 0, cached_prompt_tokens=1_000_000)[0]
    assert full == Decimal("0.150000") and cached == Decimal("0.015000")
    # without a cached price, cached tokens cost as input
    assert prices().cost("m", 1_000_000, 0, cached_prompt_tokens=500_000)[0] == full


def test_price_table_load(tmp_path: Path) -> None:
    # load() always carries the built-in local models (docs/local-llm.md): a self-hosted model is
    # the only thing priced at 0 without an entry in the user's file
    assert PriceTable.load(None).prices == BUILTIN_PRICES == PriceTable.load("").prices
    p = tmp_path / "prices.json"
    p.write_text(
        json.dumps(
            {
                "x/y": {
                    "input_per_mtok": "0.15",
                    "output_per_mtok": "0.60",
                    "cached_input_per_mtok": "0.01",
                }
            }
        )
    )
    t = PriceTable.load(p)
    assert t.prices["x/y"].cached_input_per_mtok == Decimal("0.01")
    p.write_text("{bad")
    with pytest.raises(ValueError):
        PriceTable.load(p)
    p.write_text(json.dumps({"x": {"input_per_mtok": "1"}}))
    with pytest.raises(ValueError):
        PriceTable.load(p)


def test_unknown_model_records_zero_with_one_warning() -> None:
    m = meter()
    with m.scope(stage="extract"):
        m.record(purpose="p", model="mystery", prompt_tokens=100, completion_tokens=10, seconds=0.1)
        m.record(purpose="p", model="mystery", prompt_tokens=100, completion_tokens=10, seconds=0.1)
        m.record(purpose="p", model="fake", prompt_tokens=100, completion_tokens=10, seconds=0.1)
    assert m.total() == Decimal("0")
    assert m.drain_warnings() == ["price_unknown: mystery"]
    assert m.drain_warnings() == []


def test_limits_must_be_positive_and_from_settings() -> None:
    with pytest.raises(ValueError):
        BudgetLimits(max_pages=0, max_calls=1, max_usd=Decimal("1"))
    with pytest.raises(ValueError):
        BudgetLimits(max_pages=1, max_calls=1, max_usd=Decimal("0"))
    lim = BudgetLimits.from_settings(Settings(_env_file=None))
    assert (lim.max_pages, lim.max_calls, lim.max_usd) == (200, 100, Decimal("2.00"))


def test_reserve_call_is_exact_under_threads_and_sticky() -> None:
    m = meter(calls=3)
    results: list[object] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            m.reserve_call()
            outcome: object = "ok"
        except LLMBudgetExceeded as exc:
            outcome = exc
        with lock:
            results.append(outcome)

    with ThreadPoolExecutor(8) as pool:
        for _ in range(8):
            pool.submit(attempt)
    assert results.count("ok") == 3
    errors = [r for r in results if isinstance(r, LLMBudgetExceeded)]
    assert len(errors) == 5 and all(e.limit == "max_calls" for e in errors)
    assert m.calls_used == 3
    with m.scope(stage="extract"):
        m.record(purpose="p", model="fake", prompt_tokens=1, completion_tokens=1, seconds=0)
    with pytest.raises(LLMBudgetExceeded) as again:
        m.reserve_call()
    assert again.value is errors[0] or again.value.limit == "max_calls"  # sticky
    breach = m.breach()
    assert breach is not None and breach.limit == "max_calls"
    assert str(breach) == "budget_exceeded: max_calls (limit 3, used 3)"
    assert isinstance(breach, LLMError)


def test_dollar_limit_message_and_stickiness() -> None:
    m = meter(usd="0.01")
    with m.scope(stage="extract"):
        m.reserve_call()
        m.record(
            purpose="p",
            model="m",
            prompt_tokens=0,
            completion_tokens=0,
            seconds=0,
            usd=Decimal("0.006"),
        )
        m.reserve_call()  # 0.006 < 0.01: still allowed
        m.record(
            purpose="p",
            model="m",
            prompt_tokens=0,
            completion_tokens=0,
            seconds=0,
            usd=Decimal("0.005"),
        )
    with pytest.raises(LLMBudgetExceeded) as err:
        m.reserve_call()
    assert err.value.limit == "max_usd" and err.value.used == "0.011000"
    assert str(err.value) == "budget_exceeded: max_usd (limit 0.01, used 0.011000)"
    with pytest.raises(LLMBudgetExceeded):
        m.reserve_call()  # sticky


def test_claim_pages_never_raises() -> None:
    m = meter(pages=3)
    assert m.claim_pages(2) == 2
    assert m.claim_pages(4) == 1
    assert m.claim_pages(1) == 0
    b = m.breach()
    assert b is not None and b.limit == "max_pages" and m.pages_used == 7
    assert str(b) == "budget_exceeded: max_pages (limit 3, used 7)"
    m.reserve_call()  # a page breach does not block calls for accepted pages


def test_scope_nesting_wrap_and_missing_stage() -> None:
    m = meter()
    with pytest.raises(ValueError):
        m.record(purpose="p", model="fake", prompt_tokens=1, completion_tokens=1, seconds=0)
    with m.scope(stage="extract"), m.scope(document_id="d1"), m.scope(page=2):
        with m.scope(page=3):
            m.record(purpose="p", model="fake", prompt_tokens=1, completion_tokens=1, seconds=0)
        assert m.current_scope() == {"stage": "extract", "document_id": "d1", "page": 2}

        def work(page: int) -> None:
            with m.scope(page=page):
                m.record(purpose="w", model="fake", prompt_tokens=1, completion_tokens=1, seconds=0)

        with ThreadPoolExecutor(4) as pool:
            list(pool.map(m.wrap(work), range(10, 14)))
    costs = m.costs()
    assert (costs[0].stage, costs[0].document_id, costs[0].page) == ("extract", "d1", 3)
    worker = [c for c in costs if c.purpose == "w"]
    assert sorted(c.page for c in worker if c.page is not None) == [10, 11, 12, 13]
    assert all(c.stage == "extract" and c.document_id == "d1" for c in worker)
    assert m.current_scope() == {}  # nothing leaks out


def test_cache_hit_costs_nothing_and_is_not_a_call() -> None:
    m = meter()
    with m.scope(stage="classify"):
        c = m.record(
            purpose="p",
            model="m",
            prompt_tokens=500,
            completion_tokens=50,
            seconds=0.01,
            cache_hit=True,
        )
    assert c.cache_hit and c.usd == 0 and c.prompt_tokens == 0 and c.completion_tokens == 0
    assert m.calls_used == 0 and m.total("classify") == 0


def test_summarise_costs() -> None:
    m = meter()
    with m.scope(stage="extract"):
        m.record(purpose="a", model="m", prompt_tokens=1_000_000, completion_tokens=0, seconds=1)
        m.record(
            purpose="a", model="m", prompt_tokens=0, completion_tokens=0, seconds=0, cache_hit=True
        )
    with m.scope(stage="ingest"):
        m.record(
            purpose="ocr:page", model="m", prompt_tokens=0, completion_tokens=1_000_000, seconds=1
        )
    case = Case(
        id="c_1",
        use_case="loan",
        today=FIXED_TODAY,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        costs=m.costs(),
        stages=[StageRecord(stage="extract", seconds=2.5)],
    )
    s = summarise_costs(case)
    assert s["total_usd"] == "0.750000" and s["calls"] == 2 and s["cache_hits"] == 1
    assert s["usd_by_stage"] == {"extract": "0.150000", "ingest": "0.600000"}
    assert list(s["seconds_by_stage"]) == [
        "ingest",
        "classify",
        "extract",
        "forensics",
        "rules",
        "crossdoc",
        "score",
    ]
    assert s["seconds_by_stage"]["extract"] == 2.5 and s["seconds_by_stage"]["score"] == 0.0
    empty = summarise_costs(
        Case(
            id="c_2", use_case="loan", today=FIXED_TODAY, created_at=FIXED_NOW, updated_at=FIXED_NOW
        )
    )
    assert empty["total_usd"] == "0.000000" and empty["usd_by_stage"] == {}
