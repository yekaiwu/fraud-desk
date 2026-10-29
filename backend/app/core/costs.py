"""Cost telemetry and the budget guard (docs/cost-efficiency.md sections 3.1, 4, 6).

One ``CostMeter`` per pipeline run (a rerun starts a fresh budget). Thread-safe (one lock).
Attribution (stage, document, page) travels in a ``ContextVar``; use ``meter.wrap(fn)`` when
submitting work to a ``ThreadPoolExecutor``.
"""

from __future__ import annotations

import contextvars
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from pydantic import field_validator

from app.core.config import ANTHROPIC_HAIKU_MODEL, Settings
from app.core.errors import LLMError
from app.core.models import STAGE_ORDER, Case, Cost, StageName, Strict, utcnow

if TYPE_CHECKING:
    from app.core.db import Database

T = TypeVar("T")
SIX_DP = Decimal("0.000001")

_scope_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "gldb_cost_scope", default=None
)


def _current() -> dict[str, Any]:
    return dict(_scope_var.get() or {})


def usd6(value: Decimal) -> Decimal:
    return value.quantize(SIX_DP, rounding=ROUND_HALF_UP)


class BudgetLimits(Strict):
    max_pages: int
    max_calls: int
    max_usd: Decimal

    @field_validator("max_pages", "max_calls", "max_usd")
    @classmethod
    def _positive(cls, v):  # noqa: ANN001, ANN202
        if v <= 0:
            raise ValueError("budget limits must be > 0 (there is no 'unlimited')")
        return v

    @classmethod
    def from_settings(cls, s: Settings) -> BudgetLimits:
        return cls(
            max_pages=s.max_pages_per_case,
            max_calls=s.max_llm_calls_per_case,
            max_usd=s.max_usd_per_case,
        )


# A model that is not in the price table is NOT free. The recorded ``usd`` stays 0 (we do not invent
# a bill) with a ``price_unknown`` warning, but the budget guard counts every call of an unpriced
# model at this deliberately pessimistic rate, so a mistyped model id cannot bypass the dollar
# ceiling. Add the model to ``GLDB_PRICES_PATH`` (``"local": true`` for a self-hosted one).
UNPRICED_INPUT_PER_MTOK = Decimal("15")
UNPRICED_OUTPUT_PER_MTOK = Decimal("75")
LOCAL_COST_NOTE = "local, $0 marginal cost"


class ModelPrice(Strict):
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None = None
    local: bool = False  # self-hosted: priced at 0 on purpose; tokens and seconds are still logged
    # Tiered pricing: a request whose whole prompt is above ``long_prompt_tokens`` is billed at the
    # ``long_*`` rates for ALL of its tokens (the tier is per request, not per token).
    long_prompt_tokens: int | None = None
    long_input_per_mtok: Decimal | None = None
    long_output_per_mtok: Decimal | None = None


# Models served by the owner's own machine (docs/local-llm.md). They are the only ones that are
# priced at 0 without an entry in the user's price file.
BUILTIN_PRICES: dict[str, ModelPrice] = {
    "qwen3.8-27b": ModelPrice(
        input_per_mtok=Decimal("0"), output_per_mtok=Decimal("0"), local=True
    ),
    # Claude Haiku 5.5 (docs/anthropic-haiku.md): $0.10 / $0.50 per million tokens for prompts up to
    # 100K tokens, $0.50 / $2.50 above. We do not know the cache-read and cache-write prices, so
    # ``cached_input_per_mtok`` stays unset: cache reads and writes are billed at the plain input rate
    # (a deliberate over-estimate for reads; the client sends no cache_control, so writes do not occur).
    ANTHROPIC_HAIKU_MODEL: ModelPrice(
        input_per_mtok=Decimal("0.10"),
        output_per_mtok=Decimal("0.50"),
        long_prompt_tokens=100_000,
        long_input_per_mtok=Decimal("0.50"),
        long_output_per_mtok=Decimal("2.50"),
    ),
}


class PriceTable:
    def __init__(self, prices: dict[str, ModelPrice] | None = None):
        self.prices: dict[str, ModelPrice] = dict(prices or {})

    @classmethod
    def load(cls, path: Path | str | None) -> PriceTable:
        """Load ``GLDB_PRICES_PATH`` on top of the built-in local models (``BUILTIN_PRICES``);
        ``None``/empty gives just those, malformed raises. A file entry overrides a built-in one."""
        if path is None or str(path) == "":
            return cls(dict(BUILTIN_PRICES))
        p = Path(path)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("top level must be an object")
            parsed = {model: ModelPrice.model_validate(v) for model, v in raw.items()}
            return cls({**BUILTIN_PRICES, **parsed})
        except (OSError, ValueError, InvalidOperation) as exc:
            raise ValueError(f"malformed prices file {p.name}: {str(exc)[:200]}") from exc

    def is_local(self, model: str) -> bool:
        price = self.prices.get(model)
        return price is not None and price.local

    def cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        cached_prompt_tokens: int = 0,
    ) -> tuple[Decimal, bool]:
        """(usd quantised to 6 dp half up, known). Unknown model is ``(0, False)``; ``"fake"`` is
        ``(0, True)``."""
        if model == "fake":
            return Decimal("0"), True
        price = self.prices.get(model)
        if price is None:
            return Decimal("0"), False
        million = Decimal(1_000_000)
        cached = max(0, min(cached_prompt_tokens, prompt_tokens))
        fresh = prompt_tokens - cached
        input_rate, output_rate = price.input_per_mtok, price.output_per_mtok
        if (
            price.long_prompt_tokens is not None
            and price.long_input_per_mtok is not None
            and price.long_output_per_mtok is not None
            and prompt_tokens > price.long_prompt_tokens
        ):
            input_rate, output_rate = price.long_input_per_mtok, price.long_output_per_mtok
        cached_rate = (
            price.cached_input_per_mtok if price.cached_input_per_mtok is not None else input_rate
        )
        usd = (
            Decimal(fresh) * input_rate
            + Decimal(cached) * cached_rate
            + Decimal(completion_tokens) * output_rate
        ) / million
        return usd6(usd), True


class LLMBudgetExceeded(LLMError):  # noqa: N818
    def __init__(
        self,
        limit: Literal["max_pages", "max_calls", "max_usd", "daily_cap"],
        limit_value: str,
        used: str,
    ):
        self.limit = limit
        self.limit_value = limit_value
        self.used = used
        super().__init__(str(self))

    def __str__(self) -> str:
        if self.limit == "daily_cap":  # shown to the analyst as the partial-score reason
            return (
                "budget_exceeded: daily spend cap reached "
                f"(limit {self.limit_value} USD per UTC day, used {self.used})"
            )
        return f"budget_exceeded: {self.limit} (limit {self.limit_value}, used {self.used})"


class DailySpend:
    """Global per-UTC-day spend on paid backends, across all cases (``GLDB_MAX_USD_PER_DAY``).

    Persisted in table ``daily_spend`` (integer micro-dollars per day, one atomic upsert per paid
    call) because per-call costs live inside each case's JSON body and carry no timestamp. The check
    runs before a call and the spend is added after it, so concurrent calls can overshoot the cap
    by what is in flight (the same soft bound as the per-case dollar limit)."""

    def __init__(self, db: Database, cap: Decimal, *, now: Callable[[], datetime] = utcnow):
        self.db = db
        self.cap = cap
        self._now = now

    @classmethod
    def from_settings(
        cls,
        db: Database,
        settings: Settings,
        prices: PriceTable,
        *,
        now: Callable[[], datetime] = utcnow,
    ) -> DailySpend | None:
        """``None`` when the cap is off, the backend is fake, or every configured model is local
        and priced at 0 (nothing paid can happen)."""
        backend = settings.effective_llm_backend()
        if settings.max_usd_per_day <= 0 or backend == "fake":
            return None
        models = {settings.llm_model_cheap, settings.llm_model_strong}
        if backend == "openai" and all(prices.is_local(m) for m in models):
            return None
        return cls(db, settings.max_usd_per_day, now=now)

    def _day(self) -> str:
        return self._now().astimezone(UTC).strftime("%Y-%m-%d")

    def spent(self) -> Decimal:
        with self.db.conn() as c:
            row = c.execute(
                "SELECT micro_usd FROM daily_spend WHERE day = ?", (self._day(),)
            ).fetchone()
        return Decimal(row["micro_usd"] if row else 0) / Decimal(1_000_000)

    def check(self) -> None:
        """Raise ``LLMBudgetExceeded("daily_cap")`` once today's spend has reached the cap."""
        spent = self.spent()
        if spent >= self.cap:
            raise LLMBudgetExceeded("daily_cap", format(self.cap, "f"), format(usd6(spent), "f"))

    def add(self, usd: Decimal) -> None:
        micros = int(usd6(usd) * 1_000_000)
        if micros <= 0:
            return
        with self.db.conn() as c:
            c.execute(
                "INSERT INTO daily_spend (day, micro_usd) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET micro_usd = micro_usd + excluded.micro_usd",
                (self._day(), micros),
            )


class CostMeter:
    def __init__(
        self,
        limits: BudgetLimits,
        prices: PriceTable | None = None,
        daily: DailySpend | None = None,
    ):
        self.limits = limits
        self.prices = prices or PriceTable()
        self.daily = daily
        self._lock = threading.RLock()
        self._costs: list[Cost] = []
        self._calls = 0
        self._pages_requested = 0
        self._pages_granted = 0
        self._page_breach = False
        self._call_breach: LLMBudgetExceeded | None = None
        self._first_breach: Literal["pages", "calls"] | None = None
        self._warnings: list[str] = []
        self._warned: set[str] = set()
        self._unpriced_estimate = Decimal("0")  # guard-only; never shown as a cost

    # ---------------------------------------------------------------- attribution

    @contextmanager
    def scope(
        self,
        *,
        stage: StageName | None = None,
        document_id: str | None = None,
        page: int | None = None,
    ) -> Iterator[None]:
        """Nested scopes merge: an inner value overrides the same key, others are inherited."""
        merged = _current()
        for key, value in (("stage", stage), ("document_id", document_id), ("page", page)):
            if value is not None:
                merged[key] = value
        token = _scope_var.set(merged)
        try:
            yield
        finally:
            _scope_var.reset(token)

    def wrap(self, fn: Callable[..., T]) -> Callable[..., T]:
        """Capture the current scope; use for ``ThreadPoolExecutor.submit(meter.wrap(fn), ...)``."""
        captured = _current()

        @wraps(fn)
        def runner(*args: Any, **kwargs: Any) -> T:
            token = _scope_var.set(dict(captured))
            try:
                return fn(*args, **kwargs)
            finally:
                _scope_var.reset(token)

        return runner

    @staticmethod
    def current_scope() -> dict[str, Any]:
        return _current()

    # ---------------------------------------------------------------- budget

    def claim_pages(self, n: int = 1) -> int:
        """Pages granted (0..n). A shortfall records the sticky max_pages breach; never raises."""
        with self._lock:
            self._pages_requested += n
            room = max(0, self.limits.max_pages - self._pages_granted)
            granted = min(n, room)
            self._pages_granted += granted
            if granted < n:
                self._page_breach = True
                if self._first_breach is None:
                    self._first_breach = "pages"
            return granted

    def _usd_total(self) -> Decimal:
        """Dollars the budget guard counts: recorded usd plus the pessimistic estimate for calls
        of models that have no price (a mistyped model id must not bypass the ceiling)."""
        return sum((c.usd for c in self._costs), Decimal("0")) + self._unpriced_estimate

    def reserve_call(self) -> None:
        """Atomically reserve one provider call; raises ``LLMBudgetExceeded`` (sticky)."""
        with self._lock:
            if self._call_breach is not None:
                raise self._call_breach
            if self._calls >= self.limits.max_calls:
                self._set_call_breach(
                    LLMBudgetExceeded("max_calls", str(self.limits.max_calls), str(self._calls))
                )
            else:
                total = self._usd_total()
                if total >= self.limits.max_usd:
                    self._set_call_breach(
                        LLMBudgetExceeded(
                            "max_usd", format(self.limits.max_usd, "f"), format(usd6(total), "f")
                        )
                    )
            if self._call_breach is None and self.daily is not None:
                try:
                    self.daily.check()
                except LLMBudgetExceeded as exc:
                    self._set_call_breach(exc)
            if self._call_breach is not None:
                raise self._call_breach
            self._calls += 1

    def _set_call_breach(self, exc: LLMBudgetExceeded) -> None:
        self._call_breach = exc
        if self._first_breach is None:
            self._first_breach = "calls"

    def breach(self) -> LLMBudgetExceeded | None:
        """The first breach recorded in this run, or None."""
        with self._lock:
            if self._first_breach == "pages" or (self._first_breach is None and self._page_breach):
                return self.pages_breach()
            return self._call_breach

    def pages_breach(self) -> LLMBudgetExceeded | None:
        with self._lock:
            if not self._page_breach:
                return None
            return LLMBudgetExceeded(
                "max_pages", str(self.limits.max_pages), str(self._pages_requested)
            )

    # ---------------------------------------------------------------- recording

    def add_warning(self, message: str) -> None:
        """Queue a run-level warning (de-duplicated); drained into the current StageRecord."""
        with self._lock:
            if message not in self._warned:
                self._warned.add(message)
                self._warnings.append(message)

    def record(
        self,
        *,
        purpose: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        seconds: float,
        cache_hit: bool = False,
        cached_prompt_tokens: int = 0,
        usd: Decimal | None = None,
    ) -> Cost:
        """Record one call or cache hit. Does NOT count a call: only ``reserve_call`` does."""
        scope = _current()
        stage = scope.get("stage")
        if stage is None:
            raise ValueError("CostMeter.record needs a stage in scope (use meter.scope(stage=...))")
        estimate = Decimal("0")
        if cache_hit:
            usd_value = Decimal("0")
            prompt_tokens = completion_tokens = 0
        elif usd is not None:
            usd_value = usd6(usd)
        else:
            usd_value, known = self.prices.cost(
                model, prompt_tokens, completion_tokens, cached_prompt_tokens=cached_prompt_tokens
            )
            if not known:
                self.add_warning(f"price_unknown: {model}")
                estimate = (
                    Decimal(prompt_tokens) * UNPRICED_INPUT_PER_MTOK
                    + Decimal(completion_tokens) * UNPRICED_OUTPUT_PER_MTOK
                ) / Decimal(1_000_000)
                with self._lock:
                    self._unpriced_estimate += estimate
        cost = Cost(
            stage=stage,
            purpose=purpose,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            usd=usd_value,
            seconds=seconds,
            cache_hit=cache_hit,
            document_id=scope.get("document_id"),
            page=scope.get("page"),
        )
        with self._lock:
            self._costs.append(cost)
        if self.daily is not None and not cache_hit:
            self.daily.add(usd_value + estimate)  # an unpriced model counts at the pessimistic rate
        return cost

    def costs(self) -> list[Cost]:
        with self._lock:
            return list(self._costs)

    def total(self, stage: StageName | None = None) -> Decimal:
        with self._lock:
            return usd6(
                sum((c.usd for c in self._costs if stage is None or c.stage == stage), Decimal("0"))
            )

    @property
    def calls_used(self) -> int:
        with self._lock:
            return self._calls

    @property
    def pages_used(self) -> int:
        with self._lock:
            return self._pages_requested

    def drain_warnings(self) -> list[str]:
        """Pending ``price_unknown: <model>`` / ``usage_missing`` warnings (each once per run)."""
        with self._lock:
            out, self._warnings = self._warnings, []
            return out


def summarise_costs(case: Case) -> dict[str, Any]:
    """The ``cost_summary`` of api.md section 5 (cost-efficiency.md 6.2)."""
    total = sum((c.usd for c in case.costs), Decimal("0"))
    by_stage: dict[str, Decimal] = {}
    for c in case.costs:
        by_stage[c.stage] = by_stage.get(c.stage, Decimal("0")) + c.usd
    seconds = {s: 0.0 for s in STAGE_ORDER}
    for rec in case.stages:
        seconds[rec.stage] = rec.seconds
    summary: dict[str, Any] = {
        "total_usd": format(usd6(total), "f"),
        "calls": sum(1 for c in case.costs if not c.cache_hit),
        "cache_hits": sum(1 for c in case.costs if c.cache_hit),
        "seconds_by_stage": seconds,
        "usd_by_stage": {s: format(usd6(v), "f") for s, v in by_stage.items()},
    }
    local = sorted(
        {
            c.model
            for c in case.costs
            if not c.cache_hit and c.model in BUILTIN_PRICES and BUILTIN_PRICES[c.model].local
        }
    )
    if local:  # only present for local models, so hosted output keeps its exact shape
        summary["pricing_note"] = f"{', '.join(local)}: {LOCAL_COST_NOTE}"
    return summary
