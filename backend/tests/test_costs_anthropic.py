"""Haiku 5.5 price tiers and the global daily spend cap (docs/anthropic-haiku.md, cost-efficiency.md)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import app.pipeline as pipeline
from app.core.cases import new_case
from app.core.config import Settings
from app.core.costs import (
    BUILTIN_PRICES,
    BudgetLimits,
    CostMeter,
    DailySpend,
    LLMBudgetExceeded,
    ModelPrice,
    PriceTable,
    summarise_costs,
)
from app.core.db import Database
from app.core.llm import FakeLLMClient
from app.core.models import Case, Cost, StageOutcome
from app.core.testing import FIXED_TODAY
from app.pipeline import run_pipeline

HAIKU = "claude-haiku-5-5"


# --------------------------------------------------------------------------------------
# price tiers
# --------------------------------------------------------------------------------------


def test_haiku_is_in_the_builtin_table_and_is_not_local() -> None:
    price = PriceTable.load(None).prices[HAIKU]
    assert (price.input_per_mtok, price.output_per_mtok) == (Decimal("0.10"), Decimal("0.50"))
    assert price.local is False and price.cached_input_per_mtok is None
    assert BUILTIN_PRICES[HAIKU].long_prompt_tokens == 100_000


@pytest.mark.parametrize(
    ("prompt", "completion", "usd"),
    [
        (99_994, 0, "0.009999"),  # short tier; 0.0099994 rounds down
        (99_995, 0, "0.010000"),  # 0.0099995 rounds half up
        (100_000, 0, "0.010000"),  # exactly 100K is still the short tier
        (100_000, 1_000_000, "0.510000"),  # short tier output: $0.50 per million
        (100_001, 0, "0.050001"),  # one token over: ALL tokens at $0.50
        (100_001, 1_000_000, "2.550001"),  # long tier output: $2.50 per million
        (1_000_000, 0, "0.500000"),
        (1_000, 200, "0.000200"),
        (1, 1, "0.000001"),  # 0.6e-6 rounds half up to 1e-6
        (0, 0, "0.000000"),
    ],
)
def test_haiku_tiers_and_rounding(prompt: int, completion: int, usd: str) -> None:
    got, known = PriceTable.load(None).cost(HAIKU, prompt, completion)
    assert known and got == Decimal(usd)


def test_cache_tokens_are_billed_at_the_plain_input_rate_in_the_right_tier() -> None:
    table = PriceTable.load(None)
    plain = table.cost(HAIKU, 50_000, 0)[0]
    # the cached flag changes nothing without a known cache price (no invented discount)
    assert table.cost(HAIKU, 50_000, 0, cached_prompt_tokens=50_000)[0] == plain
    long_plain = table.cost(HAIKU, 150_000, 0)[0]
    assert table.cost(HAIKU, 150_000, 0, cached_prompt_tokens=100_000)[0] == long_plain
    assert long_plain == Decimal("0.075000")


def test_a_prices_file_entry_overrides_the_builtin_haiku_price(tmp_path: Path) -> None:
    p = tmp_path / "prices.json"
    p.write_text('{"claude-haiku-5-5": {"input_per_mtok": "1", "output_per_mtok": "2"}}')
    table = PriceTable.load(p)
    assert table.cost(HAIKU, 1_000_000, 0)[0] == Decimal("1.000000")
    assert table.cost(HAIKU, 5_000_000, 0)[0] == Decimal("5.000000")  # no tier in the override


def test_tier_fields_are_validated_with_the_model_price() -> None:
    price = ModelPrice(
        input_per_mtok=Decimal(1),
        output_per_mtok=Decimal(2),
        long_prompt_tokens=10,
        long_input_per_mtok=Decimal(3),
        long_output_per_mtok=Decimal(4),
    )
    assert PriceTable({"m": price}).cost("m", 11, 1_000_000)[0] == Decimal("4.000033")


def test_unknown_model_is_still_priced_pessimistically_by_the_guard() -> None:
    meter = CostMeter(
        BudgetLimits(max_pages=5, max_calls=5, max_usd=Decimal("1")), PriceTable.load(None)
    )
    with meter.scope(stage="extract"):
        meter.reserve_call()
        cost = meter.record(
            purpose="x",
            model="claude-opus-9",
            prompt_tokens=100_000,
            completion_tokens=0,
            seconds=0.1,
        )
        assert cost.usd == 0 and meter.drain_warnings() == ["price_unknown: claude-opus-9"]
        with pytest.raises(LLMBudgetExceeded, match="max_usd"):  # 100K tokens at $15/M = $1.50
            meter.reserve_call()


def test_cost_summary_does_not_call_haiku_local() -> None:
    case = Case.model_construct(  # only the fields summarise_costs reads
        costs=[
            Cost(stage="extract", purpose="x", model=HAIKU, prompt_tokens=1, usd=Decimal("0.5")),
            Cost(stage="extract", purpose="x", model="qwen3.8-27b", prompt_tokens=1),
        ],
        stages=[],
    )
    note = summarise_costs(case)["pricing_note"]
    assert "qwen3.8-27b" in note and "claude" not in note


def test_budget_guard_counts_real_haiku_dollars_per_case() -> None:
    meter = CostMeter(
        BudgetLimits(max_pages=5, max_calls=50, max_usd=Decimal("0.60")), PriceTable.load(None)
    )
    with meter.scope(stage="extract"):
        meter.reserve_call()
        meter.record(  # 1M tokens in the long tier = $0.50
            purpose="x", model=HAIKU, prompt_tokens=1_000_000, completion_tokens=0, seconds=1
        )
        meter.reserve_call()
        meter.record(
            purpose="x", model=HAIKU, prompt_tokens=200_000, completion_tokens=0, seconds=1
        )
        with pytest.raises(LLMBudgetExceeded, match=r"max_usd \(limit 0.60, used 0.600000\)"):
            meter.reserve_call()


# --------------------------------------------------------------------------------------
# daily spend cap
# --------------------------------------------------------------------------------------


class Clock:
    def __init__(self, iso: str):
        self.now = datetime.fromisoformat(iso).astimezone(UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock("2026-03-01T10:00:00+00:00")


def paid_settings(tmp_path: Path, **kw) -> Settings:  # noqa: ANN003
    base = dict(
        _env_file=None,
        data_dir=tmp_path / "data",
        db_path=str(tmp_path / "data" / "gldb.sqlite3"),
        llm_backend="anthropic",
        anthropic_api_key="sk-ant-test-fake",
        max_usd_per_day=Decimal("1.00"),
    )
    base.update(kw)
    return Settings(**base)


def meter_with(db: Database, cap: str, clock: Clock, **kw) -> CostMeter:  # noqa: ANN003
    return CostMeter(
        BudgetLimits(max_pages=100, max_calls=100, max_usd=Decimal("100")),
        PriceTable.load(None),
        daily=DailySpend(db, Decimal(cap), now=clock),
        **kw,
    )


def spend(meter: CostMeter, usd: str, model: str = HAIKU) -> None:
    with meter.scope(stage="extract"):
        meter.reserve_call()
        meter.record(
            purpose="x",
            model=model,
            prompt_tokens=10,
            completion_tokens=1,
            seconds=0.1,
            usd=Decimal(usd),
        )


def test_under_the_cap_calls_go_through(mem_db: Database, clock: Clock) -> None:
    meter = meter_with(mem_db, "1.00", clock)
    spend(meter, "0.40")
    spend(meter, "0.50")  # 0.90 < 1.00: a third call is still allowed
    with meter.scope(stage="extract"):
        meter.reserve_call()
    assert meter.daily is not None and meter.daily.spent() == Decimal("0.9")
    assert meter.breach() is None


def test_at_the_cap_further_calls_are_refused_with_the_visible_reason(
    mem_db: Database, clock: Clock
) -> None:
    meter = meter_with(mem_db, "1.00", clock)
    spend(meter, "0.60")
    spend(meter, "0.40")  # exactly the cap
    with meter.scope(stage="extract"), pytest.raises(LLMBudgetExceeded) as err:
        meter.reserve_call()
    assert err.value.limit == "daily_cap"
    assert str(err.value) == (
        "budget_exceeded: daily spend cap reached (limit 1.00 USD per UTC day, used 1.000000)"
    )
    assert meter.breach() is err.value  # the pipeline reads this and marks the case partial


def test_the_cap_is_global_across_cases_and_survives_restarts(tmp_path: Path, clock: Clock) -> None:
    path = str(tmp_path / "d.sqlite3")
    first = Database(path)
    first.init_schema()
    spend(meter_with(first, "1.00", clock), "1.00")
    second = Database(path)  # a new process / another case's meter
    second.init_schema()
    other_case = meter_with(second, "1.00", clock)
    with other_case.scope(stage="extract"), pytest.raises(LLMBudgetExceeded, match="daily spend"):
        other_case.reserve_call()


def test_a_new_utc_day_resets_the_count(mem_db: Database, clock: Clock) -> None:
    meter = meter_with(mem_db, "1.00", clock)
    spend(meter, "1.50")
    with meter.scope(stage="extract"), pytest.raises(LLMBudgetExceeded):
        meter.reserve_call()
    clock.now = Clock("2026-03-01T23:59:59+00:00").now
    assert DailySpend(mem_db, Decimal("1.00"), now=clock).spent() == Decimal("1.5")
    clock.now = Clock("2026-03-02T00:00:00+00:00").now  # next UTC day
    fresh = meter_with(mem_db, "1.00", clock)
    assert fresh.daily is not None and fresh.daily.spent() == 0
    spend(fresh, "0.10")
    assert fresh.breach() is None


def test_the_day_is_utc_not_local(mem_db: Database) -> None:
    # 23:30 on 1 March in UTC-5 is 04:30 on 2 March UTC
    late = Clock("2026-03-01T23:30:00-05:00")
    ledger = DailySpend(mem_db, Decimal("1"), now=late)
    ledger.add(Decimal("0.25"))
    with mem_db.conn() as c:
        rows = c.execute("SELECT day, micro_usd FROM daily_spend").fetchall()
    assert [(r["day"], r["micro_usd"]) for r in rows] == [("2026-03-02", 250_000)]


def test_cache_hits_and_free_records_add_nothing(mem_db: Database, clock: Clock) -> None:
    meter = meter_with(mem_db, "1.00", clock)
    with meter.scope(stage="extract"):
        meter.record(
            purpose="x",
            model=HAIKU,
            prompt_tokens=0,
            completion_tokens=0,
            seconds=0,
            cache_hit=True,
        )
    assert meter.daily is not None and meter.daily.spent() == 0


def test_an_unpriced_paid_model_counts_at_the_pessimistic_rate(
    mem_db: Database, clock: Clock
) -> None:
    meter = meter_with(mem_db, "1.00", clock)
    with meter.scope(stage="extract"):
        meter.record(
            purpose="x", model="vendor/new", prompt_tokens=100_000, completion_tokens=0, seconds=0
        )
    assert meter.daily is not None and meter.daily.spent() == Decimal("1.5")  # 100K at $15/M


def test_daily_spend_is_a_table_created_idempotently(mem_db: Database) -> None:
    mem_db.init_schema()
    mem_db.init_schema()
    with mem_db.conn() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM daily_spend").fetchone()["n"] == 0


def test_from_settings_applies_only_to_paid_backends(tmp_path: Path, mem_db: Database) -> None:
    prices = PriceTable.load(None)

    def build(**kw) -> DailySpend | None:  # noqa: ANN003
        return DailySpend.from_settings(mem_db, paid_settings(tmp_path, **kw), prices)

    anth = build()
    assert anth is not None and anth.cap == Decimal("1.00")
    openai = build(
        llm_backend="openai",
        llm_api_key="k",
        llm_model_cheap="vendor/m",
        llm_model_strong="vendor/m",
    )
    assert openai is not None
    assert build(llm_backend="fake") is None  # the fake backend is unaffected
    assert build(llm_backend="auto") is None  # auto without a key is the fake
    assert build(max_usd_per_day=Decimal("0")) is None  # 0 disables
    local = build(
        llm_backend="openai",
        llm_api_key="k",
        llm_model_cheap="qwen3.8-27b",
        llm_model_strong="qwen3.8-27b",
    )
    assert local is None  # the local $0 model is unaffected
    mixed = build(
        llm_backend="openai",
        llm_api_key="k",
        llm_model_cheap="qwen3.8-27b",
        llm_model_strong="vendor/paid",
    )
    assert mixed is not None  # one paid role is enough to need the cap


def test_fake_backend_runs_ignore_a_full_ledger(tmp_path: Path, mem_db: Database) -> None:
    mem_db.init_schema()
    DailySpend(mem_db, Decimal("1")).add(Decimal("100"))
    settings = paid_settings(tmp_path, llm_backend="fake")
    assert DailySpend.from_settings(mem_db, settings, PriceTable.load(None)) is None


# --- through the pipeline: the case becomes partial with the visible message ---------------------


def test_pipeline_marks_the_case_partial_once_the_daily_cap_is_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = paid_settings(
        tmp_path,
        llm_backend="openai",
        llm_api_key="k",
        llm_model_cheap="vendor/m",
        llm_model_strong="vendor/m",
        anthropic_api_key="",
    )
    db = Database(settings.database_path, data_dir=settings.data_dir)
    db.init_schema()

    def paid_extract(ctx) -> StageOutcome:  # noqa: ANN001
        ctx.costs.reserve_call()
        ctx.costs.record(
            purpose="x",
            model="vendor/m",
            prompt_tokens=10,
            completion_tokens=1,
            seconds=0.1,
            usd=Decimal("1.20"),
        )
        return StageOutcome()

    monkeypatch.setitem(pipeline.STAGE_RUNNERS, "extract", paid_extract)
    first = new_case(settings, "loan", reference="D-1", today=FIXED_TODAY)
    second = new_case(settings, "loan", reference="D-2", today=FIXED_TODAY)
    db.create_case(first)
    db.create_case(second)

    one = run_pipeline(first.id, db=db, settings=settings, llm=FakeLLMClient())
    assert not any("daily" in r for r in one.partial_reasons)  # the call was allowed (check first)
    assert sum(c.usd for c in one.costs) == Decimal("1.20")

    two = run_pipeline(second.id, db=db, settings=settings, llm=FakeLLMClient())
    reason = next(r for r in two.partial_reasons if r.startswith("budget_exceeded:"))
    assert "daily spend cap reached" in reason and "limit 1.00 USD per UTC day" in reason
    assert two.partial and two.score is not None and two.score.partial
    assert two.costs == []  # nothing was spent on the second case
    assert all(s.status == "ok" for s in two.stages)  # deterministic stages still ran
