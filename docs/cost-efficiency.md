# Cost efficiency

Build spec for plan section 6 (all 12 levers, telemetry, metrics, budget guard, cache). Implemented in `backend/app/core/costs.py`, `core/cache.py`, the LLM client in `core/llm.py`, the page budget in `ingest/`, the routing in `classify/` and `extract/`, the CV caps in `checks/forensics_image.py`, and measured by `backend/scripts/eval.py`. Contracts: [architecture.md](architecture.md) (sections 3, 5, 6, 8, 10), [data-model.md](data-model.md) (`Cost`, `StageRecord`, `Case.costs`, `Score`), [api.md](api.md) (`cost_summary`). Neighbours: [ingest-and-extraction.md](ingest-and-extraction.md), [classification.md](classification.md), [forensics.md](forensics.md) (4.7), [test-data-and-eval.md](test-data-and-eval.md) (M8, baseline). Plan refs point to the master plan (not included in this repo). All data is fictitious. Do not git commit.

## 1. Purpose

Principle (plan 6): **spend money only where there is uncertainty, and let the free deterministic checks decide when to spend more.** Cost is measured from the first run, built into the pipeline shape (text layer first, classify before extract, cheap before strong), cached, and capped. A cap that trips is shown on screen as a partial score, never silent (plan principle 4, lever 11). Nothing is claimed that the telemetry cannot show: with no API key every dollar figure is `0` and the reports say so.

## 2. Scope

| In | Plan ref |
|---|---|
| Cost telemetry: model, tokens, dollars, seconds per call, per stage, per case | 6 lever 1 |
| `CostMeter`, price table, budget guard (pages, calls, dollars), partial score and on-screen message | 6 lever 11, 8.3, principle 3 |
| Response cache: key, hit semantics, invalidation, retention | 6 lever 7 |
| How each of the 12 levers is realised, where it lives, how it is measured | 6 table |
| Metrics definitions and the baseline-then-ratio targets | 6 "Metrics" |

| Out | Where |
|---|---|
| The cascade's escalation logic and the classifier decision rule | ingest-and-extraction.md 6.6, classification.md 4.9 |
| Forensics rules themselves (only their cost caps are mirrored here) | forensics.md |
| Eval harness code and report layout | test-data-and-eval.md |
| Invented dollar targets (none exist before the baseline run) | plan 6 |

## 3. Interfaces

### 3.1 `core/costs.py` (owner foundation, wave 0)

```python
class BudgetLimits(Strict):
    max_pages: int; max_calls: int; max_usd: Decimal            # all > 0, from Settings; invalid values fail startup
    @classmethod
    def from_settings(cls, s: Settings) -> "BudgetLimits": ...  # GLDB_MAX_PAGES_PER_CASE, GLDB_MAX_LLM_CALLS_PER_CASE, GLDB_MAX_USD_PER_CASE

class ModelPrice(Strict):
    input_per_mtok: Decimal; output_per_mtok: Decimal
    cached_input_per_mtok: Decimal | None = None                # optional key in the prices JSON; absent = cached tokens priced as input

class PriceTable:
    def __init__(self, prices: dict[str, ModelPrice] = {}): ...
    @classmethod
    def load(cls, path: Path | None) -> "PriceTable": ...       # GLDB_PRICES_PATH; None or empty = empty table; malformed file = ValueError at startup
    def cost(self, model: str, prompt_tokens: int, completion_tokens: int, *, cached_prompt_tokens: int = 0) -> tuple[Decimal, bool]: ...
        # (usd quantised to 6 dp ROUND_HALF_UP, known). Unknown model -> (Decimal("0"), False). model "fake" -> (0, True)

class LLMBudgetExceeded(LLMError):                              # LLMError is defined in core/llm.py (ingest-and-extraction.md 3.4)
    limit: Literal["max_pages", "max_calls", "max_usd"]; limit_value: str; used: str
    def __str__(self) -> str: ...                               # "budget_exceeded: max_usd (limit 2.00, used 2.003412)"

class CostMeter:                                                # one per pipeline run; thread-safe (one lock)
    def __init__(self, limits: BudgetLimits, prices: PriceTable): ...
    # attribution (contextvars): stage, document_id, page of the code currently running
    def scope(self, *, stage: StageName | None = None, document_id: str | None = None, page: int | None = None) -> ContextManager[None]: ...
        # nested scopes merge: an inner value overrides the same key, others are inherited
    def wrap(self, fn: Callable[..., T]) -> Callable[..., T]: ...   # captures the current scope; use for ThreadPoolExecutor.submit(meter.wrap(fn), ...)
    # budget
    def claim_pages(self, n: int = 1) -> int: ...               # pages granted (0..n); a shortfall records the sticky max_pages breach; never raises
    def reserve_call(self) -> None: ...                         # atomic; raises LLMBudgetExceeded (max_calls or max_usd); sticky once any breach is recorded
    def breach(self) -> LLMBudgetExceeded | None: ...           # first breach recorded in this run, or None
    # recording
    def record(self, *, purpose: str, model: str, prompt_tokens: int, completion_tokens: int, seconds: float,
               cache_hit: bool = False, cached_prompt_tokens: int = 0, usd: Decimal | None = None) -> Cost: ...
        # stage/document_id/page from the current scope (ValueError if no stage in scope); usd from PriceTable unless given;
        # cache_hit forces usd=0 and tokens=0. Does NOT count a call: only reserve_call() does.
    def costs(self) -> list[Cost]: ...                           # in record order (Case.costs)
    def total(self, stage: StageName | None = None) -> Decimal: ...
    @property
    def calls_used(self) -> int: ...                            # reserve_call() successes
    @property
    def pages_used(self) -> int: ...                            # claim_pages() requested total (granted + refused)
    def drain_warnings(self) -> list[str]: ...                  # "price_unknown: <model>", "usage_missing" (each once per run); pipeline appends to the current StageRecord.warnings

def summarise_costs(case: Case) -> dict: ...                    # api.md cost_summary shape, section 6.2
```

`CaseContext.costs: CostMeter`. The pipeline builds a fresh `CostMeter` per `run_pipeline` call (a rerun starts a new budget), wraps every stage in `with ctx.costs.scope(stage=<name>)`, sets `StageRecord.cost_usd = ctx.costs.total(stage)`, `Case.costs = ctx.costs.costs()` after each stage, and appends `drain_warnings()` to the stage record.

### 3.2 `core/cache.py` (owner foundation)

```python
LLM_CACHE_SCHEMA = 1
def cache_key(req: LLMRequest, model: str, client_version: str) -> str: ...      # section 5.1
class CachedResponse(Strict): data: dict; model: str; purpose: str; prompt_tokens: int; completion_tokens: int; usd: Decimal; created_at: datetime
class CacheStats(Strict): enabled: bool; entries: int; hits: int; misses: int                 # hits/misses are in-process counters
class ResponseCache:
    def __init__(self, db: Database, *, enabled: bool = True): ...                # enabled = settings.cache_enabled
    def get(self, key: str) -> CachedResponse | None: ...                         # None when disabled
    def put(self, key: str, data: dict, *, model: str, purpose: str, prompt_tokens: int, completion_tokens: int,
            usd: Decimal, file_sha256: str | None) -> None: ...                    # INSERT OR REPLACE; no-op when disabled
    def clear(self, *, purpose: str | None = None, model: str | None = None) -> int: ...   # rows deleted
    def prune(self, *, older_than: timedelta) -> int: ...                          # by created_at; called by retention.py
    def stats(self) -> CacheStats: ...
```
Stored in table `llm_cache(key, value JSON, created_at, model, purpose)` (architecture.md section 6). `value` is `{"v": 1, "data": {...}, "prompt_tokens": n, "completion_tokens": n, "usd": "0.000123", "file_sha256": "<64 hex>" | null}`.

### 3.3 Settings used (architecture.md section 8, unchanged)

`GLDB_MAX_PAGES_PER_CASE` (200), `GLDB_MAX_LLM_CALLS_PER_CASE` (100), `GLDB_MAX_USD_PER_CASE` (2.00), `GLDB_MAX_PAGE_WORKERS` (4), `GLDB_JOB_WORKERS` (2), `GLDB_CACHE_ENABLED` (true), `GLDB_PRICES_PATH`, `GLDB_LLM_MODEL_CHEAP`/`STRONG`, `GLDB_VISION_DPI` (150), `GLDB_RENDER_DPI` (150), `GLDB_EXTRACTOR_MODE`, `GLDB_CLASSIFIER_MODE`, `GLDB_CLASSIFIER_MIN_PROB`, `GLDB_LLM_BACKEND`. Optional extra key in the prices JSON: `cached_input_per_mtok`.

## 4. Behaviour: budget guard (lever 11)

### 4.1 Limits and where each is enforced

| Limit | Default | Counted by | Enforced in | On breach |
|---|---|---|---|---|
| Pages per case | 200 | `claim_pages(1)` for every page, in file-id then page order | `ingest.stage` (before touching the page) | the remaining pages are not processed: `PageInfo.error="not_processed: budget max_pages"`, no words; stage partial |
| Model calls per case | 100 | `reserve_call()` before every provider call (retries after a schema failure are separate calls); cache hits and local-classifier work do **not** reserve | `OpenAICompatClient` and `FakeLLMClient`, so every caller is covered | `LLMBudgetExceeded("max_calls")` |
| Dollars per case | 2.00 | `total_usd >= max_usd` checked inside `reserve_call()` | same | `LLMBudgetExceeded("max_usd")` |

Semantics:
- **Exact for pages and calls** (atomic reservation under the meter lock, so parallel workers cannot exceed `max_calls`).
- **Dollars can overshoot** by the cost of calls already in flight when the ceiling is crossed (at most `GLDB_MAX_PAGE_WORKERS` calls) plus the call that crossed it; cost is known only after a response. Stated, not hidden.
- **Sticky**: after the first call/dollar breach every later `reserve_call()` raises the same exception for the rest of the run, including cheaper calls. A page breach does not block calls for pages that were accepted.
- **Per run**: a rerun (version + 1) builds a new meter and a new budget. Cache hits cost nothing and are always allowed, so a rerun of a case that tripped the guard can finish from cache.
- `max_*` values are `> 0`; `0` or negative fails startup. There is no "unlimited".

### 4.2 What each stage does when the guard trips

| Stage | Behaviour | Document/page effect |
|---|---|---|
| ingest | pages refused by `claim_pages`; OCR calls refused by the guard | `PageInfo.error` starts `not_processed` |
| classify | the item keeps its rules result; no more hosted calls | stage warning `classify: escalation_unavailable: budget` |
| extract | no more model calls; text-layer documents still finish; model-dependent pages not read | page warning `unreadable: page <n>: budget_exceeded`, document `partial` |
| forensics, rules, crossdoc, score | **run normally** (deterministic, no model) | checks whose input documents are missing are `skipped`, so coverage drops |

The stage that hit the limit returns `StageOutcome(partial=True, partial_reason=<message>)` with `StageRecord.status="ok"` (architecture.md section 3). The pipeline appends the message to `case.partial_reasons` (de-duplicated) and sets `case.partial=True`, which the score stage copies into `Score.partial` / `Score.partial_reasons`.

### 4.3 The on-screen message (never silent)

`partial_reason` is `str(LLMBudgetExceeded)` plus a stage suffix, exactly:

| Breach | Text |
|---|---|
| pages | `budget_exceeded: max_pages (limit 200, used 237); 37 pages not processed` |
| calls | `budget_exceeded: max_calls (limit 100, used 100); 6 pages not extracted` (classify adds `; N items kept the rules result`) |
| dollars | `budget_exceeded: max_usd (limit 2.00, used 2.003412); 6 pages not extracted` |

Rules: always starts with `budget_exceeded:` so consumers can match the prefix; `used` for pages counts pages requested; dollars print 6 decimal places; the limit prints as configured. The case page banner shows it **verbatim** and the score as a lower bound (`>= N`, "Partial analysis", never fast lane): that is already specified in frontend.md section on partial scores and scoring-and-queue.md (`partial_reasons`). The queue row shows the partial chip. A partial score is never labelled "clean" or "low risk".

### 4.4 Interaction with coverage and the score

No score point exists without a flag, so a tripped guard cannot *add* risk; it removes evidence. Coverage falls because the checks that needed the unread documents are `skipped`, which is the "silence is not safety" display. The score is a lower bound.

## 5. Behaviour: response cache (lever 7)

### 5.1 Cache key

```
def field(x: str) -> str: return f"{len(x)}:{x}"                     # length-prefixed, so fields cannot run together
prompt_hash = sha256(field(req.system) + field(req.user) + field(canonical_json(req.json_schema))
                     + "".join(field(sha256(img).hex) for img in req.images)).hex
key = sha256("gldb-llm-cache" "|" LLM_CACHE_SCHEMA "|" req.purpose "|" model "|" req.schema_name "|" prompt_hash
             "|" req.cache_extra "|" client_version "|" "t0" "|" MAX_OUTPUT_TOKENS).hex
canonical_json = json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```
`model` is the resolved model name for the role (not the role). `cache_extra` is `"<file_sha256>:<page>:<mode>"` for OCR and extraction (mode `text` or `vision:<dpi>`), the text hash for classification. **Not in the key:** case id, applicant, reviewer, `today`, time. So the same file re-uploaded in another case, a demo replay or an eval rerun hits the cache (plan lever 7).

### 5.2 Hit and miss semantics
- Lookup happens **before** `reserve_call()`; a hit returns the stored `data`, records `Cost(cache_hit=True, usd=0, tokens=0, seconds=<lookup time>)` and consumes no call budget.
- Only validated results are stored. Failures (`LLMSchemaError`, transport, budget) are never cached.
- `GLDB_CACHE_ENABLED=false`: no read and no write; `stats().enabled == False`.
- Two workers missing the same key at once both call the provider (no single-flight); the second `put` overwrites with an equivalent value.
- A cached extraction reproduces the earlier output exactly, so reruns are byte-identical on model paths too. Provenance still names the models used.

### 5.3 Invalidation (by construction, plus manual)

| Change | Why the old entry stops matching |
|---|---|
| any system prompt text edited | `prompt_hash` |
| model setting changed (`GLDB_LLM_MODEL_*`) | `model` |
| text layout, `wrap_untrusted`, truncation, page text changed | `req.user` in `prompt_hash` |
| render DPI, grayscale, margin crop, resize code changed | image bytes in `prompt_hash`; `vision:<dpi>` in `cache_extra` |
| `DOC_SCHEMAS` or a response model changed | `json_schema` in `prompt_hash` |
| client message construction or parsing changed | bump `LLM_CLIENT_VERSION` (core/llm.py) |
| cache value format changed | bump `LLM_CACHE_SCHEMA` |
| manual | `ResponseCache.clear(purpose=..., model=...)` |

No TTL: correctness rests on key purity. Entries for models no longer configured stay until pruned.

### 5.4 Retention and delete-case (data minimisation, plan 7.2)
- `value.file_sha256` is set for `ocr:*` and `extract:*` purposes only (those values contain page text and personal data); it is null for `classify:*` (values are label scores; the key is a text hash).
- `Database.delete_case` must also run, for each file whose reference count drops to zero, `DELETE FROM llm_cache WHERE json_extract(value, '$.file_sha256') = ?` (parameterised), so deleting a case removes cached copies of its text.
- `retention.py` calls `ResponseCache.prune(older_than=timedelta(days=settings.retention_days))` with the rest of the retention job.
- Cache rows live in the same SQLite file and obey the same access model as cases.

### 5.5 Provider prompt caching
For models whose id starts with `anthropic/` (OpenRouter pass-through) the client sends the system message as a content list with `cache_control: {"type": "ephemeral"}`; other providers cache a long prefix automatically. When `usage.prompt_tokens_details.cached_tokens` is present it is passed to `PriceTable.cost(..., cached_prompt_tokens=...)`. The fixed system prompts are short, so the saving may be small; it is only claimed after measurement (section 11).

## 6. Behaviour: telemetry and metrics (lever 1)

### 6.1 What is recorded

| Level | Fields | Source |
|---|---|---|
| Per model call (`Cost`, data-model.md) | `stage`, `purpose` (`ocr:page`, `extract:<doc_type>`, `classify:C1\|C3\|C4\|C5`), `model` (`"fake"` for the Fake), `prompt_tokens`, `completion_tokens` (API `usage`; estimated `ceil(chars/4)` with warning `usage_missing` when absent), `usd`, `seconds` (wall time of the provider round trip including transport retries), `cache_hit`, `document_id`, `page` | `CostMeter.record`, called by the client |
| Per stage (`StageRecord`) | `seconds` (perf_counter around the stage, measured by the pipeline), `cost_usd` (`meter.total(stage)`), `warnings`, `detail` counters | pipeline |
| Stage counters that evidence the levers | ingest: `pages`, `text_pages`, `scan_pages`, `ocr_calls`; classify: `hosted_calls`, `escalation_unavailable`; extract: `text_layer_docs`, `llm_cheap_pages`, `llm_strong_pages`, `cache_hits`, `received_not_checked` | stages (ingest-and-extraction.md, classification.md) |
| Per case | `Case.costs` (all calls), `Case.stages`; derived summary below | pipeline, `summarise_costs` |
| Log line per call | `llm_call purpose=.. model=.. role=.. prompt_tokens=.. completion_tokens=.. usd=.. seconds=.. cache_hit=.. case=<id> doc=<id> page=<n>`; no text, no key, no filenames | client, INFO |

Local classifier work is recorded with `record(...)` (real seconds, `usd=0`) and never calls `reserve_call()`, so it is timed but not counted against `max_calls`.

### 6.2 `summarise_costs(case)` (the `cost_summary` of api.md)
`{"total_usd": "<sum of Cost.usd, 6 dp>", "calls": <count of entries with cache_hit false>, "cache_hits": <count with cache_hit true>, "seconds_by_stage": {<every stage in StageName>: <StageRecord.seconds>}, "usd_by_stage": {<only stages with at least one Cost entry>: "<6 dp>"}}`.

### 6.3 Metrics (definitions; formulas for the eval are authoritative in test-data-and-eval.md M8 and repeated here)
| Metric | Definition |
|---|---|
| Dollars per bundle | sum of `Cost.usd` for the case |
| Dollars per page by stage | stage usd divided by pages processed (`PageInfo` without error) |
| p50 / p95 seconds per bundle | nearest-rank over bundles, `sorted[ceil(q x n) - 1]`, of the sum of `StageRecord.seconds` |
| Cost per correctly handled bundle | `(sum model usd + analyst_minutes_on_false_flags / 60 x analyst_rate_usd_per_hour) / n_correct`; correctly handled = clean bundle with no false-alarm flag, or a tampered/fraud bundle whose expectation was met; `minutes_per_false_flag` from the timed test, placeholder 2.0 and rate placeholder 60.0 until measured, printed as placeholders |
| Cache hit rate | `cache_hits / (calls + cache_hits)` |
| Escalation rate | `llm_strong_pages / llm_cheap_pages` (extract) and `hosted_calls / pages` (classify) |
| Model-free page share | `(text_layer pages + boilerplate pages skipped) / total pages`; reported as a count, not as dollars saved (no counterfactual is invented) |

### 6.4 Targets
No target exists before the first baseline. `eval.py --write-baseline` writes `docs/eval-results/baseline.json` from the first full run (per-bundle usd and seconds mean/p50/p95, usd per page by stage, core recall, false alarms per 10, P@5 at fraud share 0.2). Later runs report each as a **ratio to that baseline**, including misses (test-data-and-eval.md 4.6). Forensics has its own starting figures (median <= 3 s, p95 <= 10 s per file; forensics.md 4.7), also reported even if missed. With `FakeLLMClient` every usd is `0`: the tables then print "no real model cost measured" instead of presenting zero as a result.

## 7. Behaviour: the twelve levers

| # | Lever | Concrete feature | Implemented in | Config | Measured by |
|---|---|---|---|---|---|
| 1 | Measure from the first run | `Cost` per call, `StageRecord` per stage, `cost_summary`, log line | `core/costs.py`, `core/llm.py`, `pipeline.py`, `api` | `GLDB_PRICES_PATH` | M8, `baseline.json` |
| 2 | Text layer first | PyMuPDF words and boxes; text-layer extractor runs first and finishes the document when its validation passes | `ingest/words.py`, `extract/text_layer.py`, `extract/cascade.py` | `GLDB_EXTRACTOR_MODE` | `text_layer_docs`, `ocr_calls`, model-free page share |
| 3 | Classify before extracting | page type routes work (path table below); boilerplate/other skipped | `classify/stage.py`, `extract/stage.py` | `GLDB_CLASSIFIER_MODE` | `received_not_checked`, `boilerplate_pages` |
| 4 | Text-only extraction call | `LLMExtractor` sends `layout_text` for pages with words; image only for scans | `extract/llm_extractor.py` | none | experiment `text_vs_vision` (needs key) |
| 5 | Cheap model first, escalate on failed validation | cascade: cheap, then strong per failing page, before any flag; both readings kept in `alt_raw`; if they agree the mismatch stands | `extract/cascade.py` | `GLDB_LLM_MODEL_CHEAP/STRONG` | escalation rate |
| 6 | Shrink the image | grayscale, margin crop, DPI cap, longest side <= 2200 px | `ingest/render.render_for_model` | `GLDB_VISION_DPI` | experiment `dpi_sweep` (needs key) |
| 7 | Cache | response cache on content-addressed key; provider prompt caching for the fixed system prompt | `core/cache.py`, `core/llm.py` | `GLDB_CACHE_ENABLED` | cache hit rate; rerun cost 0 |
| 8 | Local models for C1-C5 | rules classifier is the free local path; JEV-style local model behind the gate | `classify/rules.py`, `classify/local.py` | `GLDB_CLASSIFIER_MODE` | gate output (classification.md 4.11) |
| 9 | Concurrency and batching | bounded page workers; row classification batched 50 per call | `extract/llm_extractor.py`, `ingest/ocr.py`, `classify/llm.py` | `GLDB_MAX_PAGE_WORKERS`, `GLDB_JOB_WORKERS` | p50/p95 seconds |
| 10 | Expensive CV on candidates only | deterministic gates and caps in the forensics image checks | `checks/forensics_image.py`, `forensics_pdf.py` | none | forensics stage seconds, limit notices |
| 11 | Budget guard | pages, calls, dollars; partial score and message | `core/costs.py`, `ingest/stage.py`, clients | `GLDB_MAX_*` | section 4 tests |
| 12 | Choose the model by eval | models are settings; sweep harness plots accuracy against dollars per 100 pages | `core/config.py`, `scripts/_eval/` | `GLDB_LLM_MODEL_*` | experiment `model_sweep` (needs key) |

### 7.1 Lever detail

**L2/L3 routing (path table).** A page's state decides the work; no model is called for a page that does not need one.

| Page state | Classify input | Extract path | Model calls |
|---|---|---|---|
| digital, document type with a text-layer extractor, validation passes | text layer | text layer | 0 |
| digital, text-layer structure fails or arithmetic fails on this page | text layer | cheap text call on the failing page(s), strong only if still failing and readers disagree (ingest-and-extraction.md 6.6) | 1-2 per failing page |
| digital, no text-layer extractor (type or layout) | text layer | cheap text call per page, strong on failing pages | 1-2 per page |
| scan | OCR (1 cheap vision call) | cheap vision call, strong on failing pages | 2-3 per page |
| `boilerplate` / `other` (non-blank) | rules; hosted only in cascade mode, cached by text hash | none (`received_not_checked`, listed in the case view) | 0, or 1 cached in cascade |
| blank | none | none | 0 |
Guardrails from classification.md 4.9 still apply: skipped pages are visible, never silent.

**L4 text-only.** Prompts carry `layout_text`, not images, whenever words exist. Whether this is cheaper is a hypothesis to confirm by measurement (plan 6): experiment `text_vs_vision` runs the same 20 digital statement pages both ways and records `prompt_tokens`, usd per 100 pages and cell accuracy. Word boxes are always kept for highlights (verification in 6.5 of the extraction doc supplies them).

**L5 escalation economics.** Escalation is per page and only after cheap output fails validation or verification, so a clean scan costs `ocr + cheap` and a tampered digital page costs at most one cheap call. "Strong" is skipped when the cheap reading already equals an independent reader, and when `GLDB_LLM_MODEL_STRONG` equals the cheap model on the real backend.

**L6 image shrink.** `render_for_model`: grayscale, `GLDB_VISION_DPI` (default 150), longest side <= 2200 px, margin crop only when it removes >= 10 % of the page area, PNG `optimize`. Experiment `dpi_sweep` runs 100/150/200 DPI and reports cell accuracy against usd per 100 pages; fine print and small digits are the stated risk. `GLDB_VISION_DPI` is the only knob.

**L9 concurrency and batching.** Pages of one document run in a pool of `GLDB_MAX_PAGE_WORKERS` (default 4), documents run one after another, cases run `GLDB_JOB_WORKERS` (default 2) at a time, so at most `JOB_WORKERS x PAGE_WORKERS` (8) provider calls are in flight. Outputs are merged in page order, so results never depend on scheduling. C4/C5 rows go in batches of 50 per hosted call. **Provider batch pricing is not built**: OpenAI-compatible routes such as OpenRouter do not generally offer it (plan: "confirm the provider offers it"); an offline eval run would use the same synchronous path (section 11).

**L10 CV on candidates only.** The numbers live in forensics.md 4.7 and must stay deterministic caps, not timers (so reruns give identical flags): ELA only on JPEG rasters (ELA also skipped above 12 MP); `copy_move` and `noise_mismatch` only on rasters above the size gate and on documents with a schema; `MAX_TEXT_PAGES = 30`, `MAX_RASTER_PAGES = 8` per file; analysis long side 1600 px; ORB `nfeatures = 1500`; 3 regions per page per rule; hitting a cap emits a limit notice. This doc only requires that those caps exist, are logged in `check_runs.seconds`, and are not model calls. Cut order if time is short: `noise_mismatch` first (plan 10).

**L12 model choice by eval.** Models are settings only (`GLDB_LLM_MODEL_CHEAP`, `_STRONG`); no model name appears in code. Experiment `model_sweep` runs extraction on N labelled statement pages (default 100, mixed layouts and scan renders) for each listed model and writes `docs/eval-results/cost_experiments.json`:
```json
{"status": "measured" | "not_run: no api key",
 "model_sweep": [{"model": "<id>", "pages": 100, "cell_accuracy": 0.0, "chain_valid_rate": 0.0,
                  "usd_per_100_pages": "0.0000", "p95_s_per_page": 0.0, "on_frontier": false}],
 "text_vs_vision": {"pages": 20, "text": {"prompt_tokens_mean": 0, "usd_per_100_pages": "0.0000", "cell_accuracy": 0.0},
                    "vision": {"prompt_tokens_mean": 0, "usd_per_100_pages": "0.0000", "cell_accuracy": 0.0}},
 "dpi_sweep": [{"dpi": 100, "cell_accuracy": 0.0, "usd_per_100_pages": "0.0000"}]}
```
`cell_accuracy` = share of ground-truth cells whose canonical value equals the extracted value; `chain_valid_rate` = share of statements whose extracted balance chain validates. A model is **on the frontier** when no other model has accuracy >= its own at a strictly lower dollar cost. The cheap and strong roles are chosen from the frontier and recorded in the validation report. Without a key the file says `not_run` and the report prints that.

## 8. Files owned

| Path | Agent |
|---|---|
| `backend/app/core/costs.py`, `core/cache.py`, cost recording in `core/llm.py`, cost wiring in `pipeline.py` (scope per stage, drain warnings, fresh meter per run), `StageRecord.cost_usd`, tests `backend/tests/test_costs_*.py`, `test_cache_*.py` | foundation (wave 0); `pipeline.py` then integration-loan (wave 2) |
| Page budget in `backend/app/ingest/stage.py`, tests `test_ingest_budget*.py` | ingest-classify |
| Routing and stage counters in `backend/app/extract/stage.py`, `cascade.py` | extract-fin |
| `backend/app/checks/forensics_image.py` caps (forensics.md 4.7) | forensics-image |
| `cost_summary` in the case view | api-backend (uses `summarise_costs`) |
| `docs/eval-results/baseline.json`, `cost_experiments.json`, cost table in the eval | eval (wave 3) |

## 9. Test plan

Unit tests use `FakeLLMClient`, a hand-built `PriceTable`, `make_ctx`, a temp SQLite file and `FIXED_TODAY`; no network.

**Meter and budget** (`test_costs_*.py`)
1. `PriceTable.cost("m", 1_000_000, 500_000)` with `input 0.15`, `output 0.60` equals `0.450000`; unknown model gives `(0, False)` and one warning `price_unknown: m` in `drain_warnings()`; `"fake"` gives `(0, True)`.
2. `reserve_call()` with `max_calls=3` from 8 threads: exactly 3 succeed, 5 raise `LLMBudgetExceeded` with `limit == "max_calls"`; `calls_used == 3`; the next call raises the same exception (sticky) even after `record()`.
3. Dollar limit: `max_usd=Decimal("0.01")`; record a `0.006000` call then a `0.005000` call: third `reserve_call()` raises `max_usd` with `used == "0.011000"`; the message equals `"budget_exceeded: max_usd (limit 0.01, used 0.011000)"` form (limit printed as configured).
4. `claim_pages` with `max_pages=3`: `claim_pages(2)==2`, `claim_pages(4)==1`; `breach().limit=="max_pages"`; `pages_used==6`; it never raises.
5. `scope` nesting and `wrap` across a `ThreadPoolExecutor`: Costs recorded in workers carry the right stage/document/page; recording with no stage in scope raises `ValueError`.
6. `record(cache_hit=True)` stores `usd=0`, tokens 0, and does not change `calls_used`.
7. `summarise_costs`: two calls, one hit, stages with and without cost: `calls=2`, `cache_hits=1`, `usd_by_stage` only lists stages with entries, `seconds_by_stage` has all seven stages.

**Pipeline behaviour** (extract/ingest/e2e fixtures)
8. **Calls cap**: 4 scan pages, `max_calls=2`, Fake with fixtures: `Fake.calls` length exactly 2; pages 3-4 `unreadable: page N: budget_exceeded`; document `partial`; `StageOutcome.partial_reason` starts `budget_exceeded: max_calls (limit 2, used 2)`; a text-layer document in the same case is fully extracted; forensics/rules/crossdoc/score stages ran (`StageRecord.status == "ok"`); `Case.partial` and `Score.partial` true; `Score.partial_reasons` contains the message verbatim; coverage ratio below 1.0.
9. **Pages cap**: 5 pages with `max_pages=3`: 3 processed, 2 `not_processed`, message `budget_exceeded: max_pages (limit 3, used 5); 2 pages not processed`; the rest of the pipeline runs.
10. **Dollar cap** with a price table that makes each call cost `0.60`, `max_usd=1.00`: calls 1-2 succeed (second crosses), call 3 refused; overshoot bound asserted (`total <= limit + max_workers x per_call`).
11. **Cache bypass of the cap**: after a tripped run, rerun with the same cache and a fresh meter: all earlier pages are hits (`calls_used == 0`), the remaining pages still hit the cap; message again present.
12. **Never silent**: with the guard tripped, `case.partial_reasons` is non-empty and `GET /cases/{id}` carries it (api-backend test uses this fixture).

**Cache** (`test_cache_*.py`)
13. Key stability: same request twice gives the same key; changing any one of purpose, model, system, user, schema, an image byte, `cache_extra`, `client_version`, `LLM_CACHE_SCHEMA` changes it; changing the case id or `today` does not (they are not inputs).
14. Hit path through the client: first call recorded with `cache_hit=False`, second with `True`, `usd=0`, identical `data`, provider stub called once; `GLDB_CACHE_ENABLED=false`: provider called twice, `stats().enabled is False`.
15. Failures are not cached (schema error twice, then a valid fixture: provider called again).
16. Re-upload: analyse the same fixture file in two cases with one cache: second case `calls == 0`, `cache_hits == n`, `Case.costs` all `usd == 0`, flags identical (`flags_digest` equal).
17. Invalidation: edit the registered system prompt text of one purpose: only that purpose misses.
18. `delete_case` removes `llm_cache` rows whose `file_sha256` belongs to the deleted file and keeps rows of a file still referenced by another case; `prune(older_than=...)` removes only old rows.
19. Provider prompt caching: with an `anthropic/...` model id the stub sees the system message as a content list with `cache_control`; with another id a plain string; `cached_tokens` in usage lowers the cost when `cached_input_per_mtok` is set.

**Telemetry and levers**
20. A digital clean bundle: `Case.costs == []`, `llm_cheap_pages == 0`, `text_layer_docs == documents` (lever 2).
21. Boilerplate page in `rules` mode: no extract call, listed in not-checked (lever 3).
22. Tampered digital page: exactly one cheap call, no strong call; strong used for a scan whose OCR words disagree with the cheap reading (lever 5).
23. `render_for_model`: grayscale and smaller than RGB at the same DPI; longest side <= 2200; the same input gives the same bytes (cache stability) (lever 6).
24. Local-classifier work is recorded (`usd == 0`, `seconds > 0`) and does not change `calls_used` (lever 8).
25. Concurrency: results with `GLDB_MAX_PAGE_WORKERS=1` and `=4` are identical (lever 9).
26. Forensics caps present and limit-noticed (test owned by forensics-image; this doc only lists it, lever 10).
27. Log line contains no document text, no API key; `GLDB_PSEUDONYMISE_LOGS` leaves ids and hashes only.
28. Eval: `cost_experiments.json` is written with `status="not_run: no api key"` under the Fake; the cost table prints "no real model cost measured".

## 10. Acceptance criteria

- `uv run pytest backend/tests/test_costs_*.py test_cache_*.py` and `ruff` green; no network.
- Every provider call and cache hit is a `Cost` entry with model, tokens, usd, seconds, stage, and (where known) document and page; `cost_summary` matches the entries.
- A clean digital bundle makes **zero** model calls; a rerun of any bundle through a warm cache makes zero provider calls and reports `cache_hits`.
- Each of pages, calls and dollars caps is enforced; after a breach the deterministic stages still run, the score is partial, `case.partial_reasons` carries a message starting `budget_exceeded:`, and the case page shows it verbatim.
- `calls_used` never exceeds `max_calls` under 8 threads; dollar overshoot stays within the stated bound.
- No model name is hard-coded outside settings and tests.
- Rerun determinism: same inputs and `ctx.today` give identical `flags_digest` with a cold and a warm cache.

## 11. Known gaps and honest limits (plan 0.6)

- **No dollars measured here.** With no API key every `usd` is `0`; scans cannot be read; levers 4, 5 (cost side), 6 and 12 and the cost of the strong tier are mechanically built and tested with fixtures but **not measured**. `cost_experiments.json` says `not_run`; the final presentation must say so. Targets are not set until a keyed baseline exists.
- Price table is user supplied (`GLDB_PRICES_PATH`) plus the built-in local model (section 12); an unknown model still records `0` with a warning, so a mis-typed model id under-reports the *reported* spend until noticed, but since the local-LLM work the budget guard counts its calls at a pessimistic rate (section 12), so the dollar ceiling no longer depends on the price file being complete.
- Dollar overshoot bound (in-flight calls) is real; the guard is a ceiling, not an estimate, and does not pre-estimate a call's cost.
- No single-flight on identical concurrent cache misses; no cross-process cache locking beyond SQLite.
- Provider prompt caching and provider batch pricing are unmeasured / not built; system prompts are short, so the prompt-cache saving may be negligible.
- The cache stores page text and extracted values (personal data) next to the case database; protection is deletion on case delete and retention pruning, not encryption.
- Budget is per run, not per customer or per day; there is no org-wide spend control.
- The calls limit counts attempts, including schema retries and calls whose response failed, which is conservative; cache hits and local classification are not counted.
- `cost per correctly handled bundle` depends on placeholder analyst minutes and rate until the timed test supplies them (test-data-and-eval.md M8).
- Latency figures are laptop-specific; no SLA is claimed.

## 12. Local model: $0 marginal cost, and a guard that an unknown price cannot bypass

Added with the local LLM server (details and setup in [local-llm.md](local-llm.md)).

- `ModelPrice` has an optional `local: bool = False`. `PriceTable.load()` always starts from `BUILTIN_PRICES`, which lists `qwen3.8-27b` at `0` input and `0` output with `local=True`; a model in `GLDB_PRICES_PATH` overrides a built-in one, and `"local": true` marks another self-hosted model. `PriceTable()` built directly stays empty (tests).
- A local model's calls are **known** (no `price_unknown` warning) and record `usd = 0` while `prompt_tokens`, `completion_tokens` and `seconds` are recorded exactly as for a hosted model. `summarise_costs` adds `"pricing_note": "qwen3.8-27b: local, $0 marginal cost"` when such a call is in the case; without one the dict keeps its previous shape. $0 is marginal cost only (own hardware and power).
- **Unknown model:** the recorded `usd` is still `0` with the single `price_unknown: <model>` warning (no invented bill), but `CostMeter` accumulates a guard-only estimate for each such call at `UNPRICED_INPUT_PER_MTOK = 15` and `UNPRICED_OUTPUT_PER_MTOK = 75` dollars per million tokens and `reserve_call` compares `recorded + estimate` with `GLDB_MAX_USD_PER_CASE`. A mistyped model id therefore trips the dollar ceiling after a realistic number of calls instead of running unbounded; the fix is to add the model to the price file. Only listed models (including `local`) are exempt.
- The call limit counts model calls only. Local OCR (macOS Vision) is not a model call and does not count.
- With one model for both roles the strong pass is skipped (section 7.1, L5), so a local run costs one call per page that needs the model.

## Contract requests

**W3 closure (T-integration-kyc-08):** every request in this section has a recorded outcome in tasks.md sections 4.2 and 4.2a (accepted, decided at R0, or not built and listed under Known gaps above); none is open.

**R0 outcomes:** all accepted (tasks.md CR-02, CR-05, CR-09, CR-10, CR-11); `delete_case` cache cleanup is in architecture.md 6.

- **foundation (core/costs.py, core/cache.py)**: implement the signatures in 3.1 and 3.2, including `summarise_costs`, `claim_pages`, `reserve_call`, `breach`, `scope`, `wrap`, `drain_warnings`; `CaseContext.costs` must be this `CostMeter`. `FakeLLMClient` and `OpenAICompatClient` must call `reserve_call()` before a provider call and `record()` after, and look up the cache before reserving (ingest-and-extraction.md 5.2).
- **pipeline**: fresh meter per run; wrap each stage in `scope(stage=...)`; set `StageRecord.cost_usd`; append `drain_warnings()`; copy `StageOutcome.partial_reason` into `case.partial_reasons` (de-duplicated) and set `case.partial`; the page-count of `claim_pages` is per run.
- **`Database.delete_case`**: also delete `llm_cache` rows by `json_extract(value, '$.file_sha256')` for files whose reference count reaches zero (5.4). **`retention.py`**: call `ResponseCache.prune`.
- **Prices JSON**: accept the optional key `cached_input_per_mtok` (3.1); architecture.md section 8 documents only the two required keys.
- **eval (test-data-and-eval.md)**: add a suite `cost_experiments` (`model_sweep`, `text_vs_vision`, `dpi_sweep`) writing `docs/eval-results/cost_experiments.json` in the shape of 7.1; it must run end to end under the Fake and write `status="not_run: no api key"`. The M8 table must print "no real model cost measured" when every `usd` is zero (already stated there).
- **classification.md** (aligned): the local classifier records through `CostMeter.record` and does not call `reserve_call()`; there is no `counts_as_call` parameter.
- **forensics.md 4.7** (already aligned): keep the caps deterministic and listed in `check_runs`.
- **frontend.md / scoring-and-queue.md** (already aligned): `partial_reasons` entries beginning `budget_exceeded:` are shown verbatim.
