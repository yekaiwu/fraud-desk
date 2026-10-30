# Architecture

Single source of truth for stack, layout, pipeline contract, dependencies, settings, parallel-work rules and file ownership.
Companion contracts: [data-model.md](data-model.md) (every model, field and column name) and [api.md](api.md) (HTTP surface).
Plan refs ("plan 8.1") point to the master plan (not included in this repo). All data is fictitious. Do not git commit.

## 1. Stack decisions (plan 8.1)

| # | Decision | Choice | Build consequence |
|---|---|---|---|
| D1 | Stack | Python 3.12 backend (FastAPI, Pydantic v2), Next.js + Tailwind frontend | `requires-python = ">=3.12,<3.13"`, `.python-version` = `3.12` |
| D2 | Python tooling | `uv` only (`uv sync`, `uv add`, `uv run pytest`, `uv run ruff`). Commit `uv.lock`. Never `pip`, never `python -m venv`, never hand-edit `uv.lock` | See section 7 |
| D3 | Storage | SQLite (JSON columns, WAL mode) plus a content-addressed file folder under `GLDB_DATA_DIR` | See section 6 |
| D4 | Background work | Bounded `ThreadPoolExecutor` inside the API process, job state in SQLite | `GLDB_JOB_WORKERS` (default 2). Jobs left `running` at startup are marked `failed("interrupted")` |
| D5 | LLM access | One `LLMClient` protocol over an OpenAI-compatible API (OpenRouter or a provider). Model names are settings, never constants | `core/llm.py`; a `FakeLLMClient` is the default when no API key is set (section 5) |
| D6 | Repo | One monorepo: `backend/`, `frontend/`, `docs/`, `samples/`, `data/` (gitignored). CI runs `ruff` and `pytest` | `.github/workflows/ci.yml` |

Additional decisions made here (contracts, not in plan):
- **Pydantic models are strict.** Every model uses `extra="forbid"`. A typo in a field name fails loudly in tests.
- **Amounts are `Decimal` in Python and decimal strings in JSON** (rationale: data-model.md section 2).
- **Today is injected.** `CaseContext.today` is the only legitimate source of "today". A test (`test_no_wallclock_in_checks.py`, AST-based) fails if anything under `backend/app/checks/**` calls `date.today`, `datetime.now`, `datetime.utcnow` or `time.time`.
- **Review never mutates analysis.** Flags, findings and score are immutable after the `score` stage; reviewer actions are separate append-only entries.

## 2. Repo layout (plan 8.2, expanded)

```
gldb-fraud/
  pyproject.toml  uv.lock  .python-version  .gitignore  .env.example  README.md
  .github/workflows/ci.yml
  backend/
    app/
      __init__.py
      main.py                 FastAPI app; auto-includes every module in app/api/ that exposes `router`
      pipeline.py             run_pipeline(): stage runner, StageRecords, partial handling, provenance, fingerprints
      score.py                run_stage(ctx): flags -> findings -> score + coverage   (foundation basic, score-queue extends)
      findings.py             grouping of correlated flags into Findings              (score-queue)
      queue.py                lane_for(), top_finding_line(), page_counts()          (score-queue; build_queue_item() lives in api/queue.py, api-backend)
      graph.py                in-case entity graph                                    (rules-kyc)
      names.py                name normalisation + matching                           (rules-kyc)
      review.py audit.py pack.py effectiveness.py retention.py                       (api-backend)
      api/                    one module per router; each exposes `router: APIRouter`
        health.py cases.py queue.py jobs.py files.py review.py pack.py effectiveness.py rules.py
      core/
        models.py             all Pydantic models + Literal enums + label constants (data-model.md)
        schemas.py            DOC_SCHEMAS: machine-readable Document.fields / Table columns per doc type
        context.py            CaseContext dataclass + helpers
        config.py             Settings (pydantic-settings, env prefix GLDB_)
        db.py                 Database: schema DDL, case/job CRUD, conn() for others
        provenance.py         build_provenance(ctx)
        costs.py              CostMeter, price table, budget guard
        cache.py              ResponseCache (SQLite table llm_cache)
        llm.py                LLMClient protocol, OpenAICompatClient, FakeLLMClient, get_llm_client()
        money.py              parse_money(), to_decimal(), approx_eq(), quantize helpers
        textnorm.py           company_key(), company_match(), company_disc(), uen_disc() (checks-financial.md 3.4)
        textlayout.py         layout_text(words), page_marker(text) (shared by ingest, classify, extract; ingest-and-extraction.md 3.2)
        fingerprints.py       invoice_fingerprint(), invoice_key_fingerprint(), file_sha256(), FingerprintStore (SQLite)
        registry.py           MockRegistry / RegistryEntity models + load_registry()
        builtin_checks.py     info checks doc_unreadable, doc_unclassified (registered like any check)
        testing.py            test/script helpers: make_cell, make_table, make_doc, make_ctx, FIXED_TODAY
      ingest/stage.py         hashing, page info, text layer (words+boxes), render        (stub: foundation; real: ingest-classify)
      classify/               base.py rules.py local.py llm.py stage.py                    (stub stage: foundation)
      extract/                registry.py base.py text_layer.py llm_extractor.py cascade.py prompts/ <doc_type>.py kyc_*.py stage.py
      checks/                 registry.py + one module per document family (section 4)
    tests/                    __init__.py conftest.py + test_*.py ; e2e/ for integration
    scripts/                  __init__.py make_bundle.py make_tampered.py run_case.py eval.py classifier_gate.py gen_check_catalogue.py _gen/ _eval/
  frontend/                   Next.js app (queue, case page, upload, effectiveness)
  data/                       gitignored: gldb.sqlite3, files/, renders/
  samples/                    generated bundles + registry.json (fictitious only; committed only if tiny)
  docs/                       this folder
```

`import app...` must work from the repo root for `uv run pytest`, `uv run uvicorn --app-dir backend app.main:app`, and `uv run python backend/scripts/<x>.py`. Recommended mechanism: hatchling build backend with `[tool.hatch.build.targets.wheel] packages = ["backend/app"]` and `[tool.pytest.ini_options] pythonpath = ["backend"]`. The foundation agent chooses the mechanism and verifies with `uv run python -c "import app, scripts"` from repo root and from `backend/scripts/`.

## 3. Pipeline and stage contract

Stages, in order: `ingest -> classify -> extract -> forensics -> rules -> crossdoc -> score` (plan 8.3).

| Stage | Module entry | Reads | Writes (only these) |
|---|---|---|---|
| ingest | `ingest.stage.run_stage` | stored files | `case.files[*].pages`, `case.files[*].pdf_meta`, `ctx.page_words` |
| classify | `classify.stage.run_stage` | pages, words | `case.files[*].pages[*].classification`, creates `case.documents` (doc_type, classification, issuer, page range, words), C4/C5 later by extract |
| extract | `extract.stage.run_stage` | documents, words, images | `documents[*].fields`, `.tables`, `.status`, `.warnings`, `.extractor`, row annotations c4/c5/counterparty |
| forensics | registry runner | files, documents | `case.flags` (checks with `stage="forensics"`), `case.check_runs` |
| rules | registry runner | documents | `case.flags` (`stage="rules"`) |
| crossdoc | registry runner | documents | `case.flags` (`stage="crossdoc"`) |
| score | `score.run_stage` | flags, check_runs | `case.findings`, `case.score`, `case.coverage`, `case.lane`, `case.top_finding` |

```python
# every backend/app/{ingest,classify,extract}/stage.py and backend/app/score.py
def run_stage(ctx: CaseContext) -> StageOutcome: ...

class StageOutcome(BaseModel):            # core/models.py
    detail: dict[str, int | float | str] = {}   # counts for the StageRecord, e.g. {"pages": 14, "scans": 2}
    warnings: list[str] = []                    # human-readable, shown on the case page
    partial: bool = False                       # True if anything was skipped or degraded
    partial_reason: str | None = None
```

Stage rules (binding):
1. **No raise for per-page or per-document problems.** Record them in `PageInfo.error` / `Document.warnings` / `Document.status`, add to `StageOutcome.warnings`, set `partial=True`. Raise only for a fatal condition (unreadable database, programmer error); the pipeline catches it.
2. **Idempotent.** Running a stage twice on the same `ctx` replaces its previous output. It never appends duplicates.
3. **Deterministic stages** (ingest, rules, crossdoc, forensics, score, and classify/extract with `rules`/`text_layer` backends) give identical output for identical input and identical `ctx.today`.
4. **Write only what the table above lists.** Never mutate documents from a check.
5. **Cost and time are measured by the pipeline**, not the stage. Stages call model code only through `ctx.llm`, which feeds `ctx.costs`.
6. **Untrusted text.** Nothing extracted is ever executed, interpolated into SQL, a file path, a shell command or HTML (section 11).

`run_pipeline` (`pipeline.py`, foundation):

```python
def run_pipeline(case_id: str, *, db: Database, settings: Settings, llm: LLMClient | None = None,
                 registry: MockRegistry | None = None, on_stage: Callable[[StageRecord], None] | None = None) -> Case: ...
```
- Loads the case, builds `CaseContext` (`today` = `case.today`, set at case creation from `GLDB_TODAY` or the wall clock; reruns reuse it so results are reproducible).
- Runs the 7 stages in order. Each gets a `StageRecord` (`status`, `started_at`, `seconds`, `cost_usd`, `error`, `warnings`, `detail`). The case is saved after every stage so `GET /jobs/{id}` shows progress.
- Exception in a stage: record `status="failed"`, set `case.partial=True` and append the reason to `case.partial_reasons` (the score stage copies both into `case.score`), **continue with the next stage**.
- Budget guard breach (`LLMBudgetExceeded`, from `core/costs.py`): stage `status="ok"` with `partial=True`, `partial_reason="budget_exceeded: <limit>"`; later LLM calls are refused, deterministic stages still run.
- Between `extract` and `forensics`: resolve `case.applicant` (data-model.md 4.1), so every check sees it.
- After `score`: stamp `case.provenance` via `build_provenance(ctx)`, record fingerprints in the store (`file_sha256` per file and `invoice` full + key per invoice, each with `meta={"applicant_uen": ..., "doc_type": ...}`; `crop_phash` from `forensics_image.crop_fingerprints(ctx)` is added by integration-loan in wave 2 behind a guarded import), set `case.status = "open"`. Checks only look fingerprints up (with `exclude_case_id`), never record them.
- Provides `rerun` semantics: `run_pipeline(case_id)` on an existing case creates `version+1`, snapshots the old version (section 6), keeps `review` entries.

Default segmentation (classify stage; detail in classification.md): each page lands in exactly one `Document`; default = maximal runs of consecutive pages with the same C1 label inside one file, additionally split where a "page 1 of N" marker restarts. An optional C2 splitter may refine this.

## 4. Check framework (auto-discovered)

```python
# backend/app/checks/registry.py   (foundation)
Severity = Literal["high", "medium", "low", "info"]
FlagStage = Literal["forensics", "rules", "crossdoc"]

class CheckMeta(BaseModel):
    rule: str                        # stable id, e.g. "bs_running_balance"
    stage: FlagStage = "rules"
    severity: Severity               # default severity
    applies_to: frozenset[str]       # DocType values, or {"*"} for file-level/forensic checks. ANY-of: at least one such document must exist
    requires: frozenset[str] = frozenset()   # ALL-of: every listed DocType must exist (cross-document checks)
    use_cases: frozenset[str] = frozenset({"kyc", "aml", "loan"})
    description: str                 # one sentence, shown in the generated catalogue
    tamper: str | None = None        # which edit it catches (catalogue column)
    version: str = "1"               # bump when logic changes; enters provenance.ruleset_version
    func: Callable[[CaseContext], list[Flag]]   # excluded from serialisation

def check(*, rule: str, applies_to: set[str], severity: Severity, description: str, stage: FlagStage = "rules",
          requires: set[str] = frozenset(), use_cases: set[str] = frozenset({"kyc","aml","loan"}),
          tamper: str | None = None, version: str = "1") -> Callable: ...

class CheckSkipped(Exception):       # raise inside a check when it cannot evaluate (missing field, currency mismatch ...)
    def __init__(self, reason: str): ...

def discover() -> None: ...                                  # imports app.checks.* (except names starting "_" and "registry") and app.core.builtin_checks; idempotent; a module that fails to import is re-raised with its name (never silently skipped)
def all_checks() -> list[CheckMeta]: ...                     # sorted by (stage order, rule)
def applicable(ctx: CaseContext) -> list[CheckMeta]: ...     # checks whose use_cases contain ctx.case.use_case
def run_stage_checks(ctx: CaseContext, stage: FlagStage) -> list[CheckRun]: ...   # appends flags to ctx.case.flags
def make_flag(rule: str, *, docs: Sequence[Document | str], evidence: str, explanation: str,
              table: str | None = None, field: str | None = None, row: int | None = None,
              region: Region | None = None, extra_regions: Sequence[Region] = (),
              severity: Severity | None = None, discriminator: str | None = None) -> Flag: ...
```

Semantics:
- **Applicable** = checks whose `use_cases` include the case's use case, plus any check whose required documents are present and usable (a document that was uploaded and read is never left unchecked because of the use case). A bundle that lacks a document therefore *lowers* coverage; silence is never safety (plan principle 3). R0 decision on forensics CR "coverage honesty" (tasks CR-28): no `input_kinds` field is added; image-only checks on an all-digital bundle count as skipped (reason "no jpeg raster" etc.), which is shown, and `FAST_LANE_MIN_COVERAGE` is recalibrated from the eval's measured coverage of the clean bundles (integration-kyc, T-integration-kyc-07). Clean manifests therefore allow `lane_any_of = ["fast", <scenario lane>]`.
- **Discriminator charset.** A discriminator passed to `make_flag` must match `[a-z0-9_:]+` (entity keys from `names.entity_key` or `core.textnorm.company_disc`/`uen_disc`, `f1p2r1`-style region ids, hex prefixes, `store:<case_id>`); `make_flag` lower-cases it and replaces any other character with `_`, so a flag id never contains a second `.`, `~` or `#`.
- A check **ran** if its `applies_to` (any-of) and `requires` (all-of) are satisfied by the case's documents, it did not raise `CheckSkipped`, and it did not raise another exception. Otherwise it is recorded in `case.check_runs` with `status="skipped"` (reason) or `status="error"` (message, never the stack). `coverage = ran / applicable`.
- `run_stage_checks` wraps each check in try/except, times it, and sets `Flag.stage`/`Flag.rule` from the meta. A check returning `[]` still counts as ran.
- `make_flag` is the only sanctioned way to build a flag. It sets the id as `"<rule>.<doc ids joined by '+', sorted>.<locator>"`, where locator is `field`, or `"<table>:<row>"` (row is 0-based), or `"-"`; `~<discriminator>` is appended when given; a numeric suffix `#2`, `#3` is appended by the runner if an id would collide. Example: `bs_running_balance.d3.transactions:17`.
- Checks are pure: `(ctx) -> list[Flag]`, no network, no wall clock, no mutation of `ctx`. They may read the raw file via `ctx.file_path(file_id)` (forensics).
- **Rule modules** (one file per family; each agent adds only files it owns): `bank_statement.py invoice.py financial_statement.py crossdoc.py kyc.py aml.py forensics_pdf.py forensics_image.py`. `app/core/builtin_checks.py` registers two info checks emitted from pipeline warnings: `doc_unreadable` (page or document could not be read) and `doc_unclassified` (label `other`/`boilerplate` or probability below `GLDB_CLASSIFIER_MIN_PROB`; plan 5.3 guardrail).
- **Rule ids are stable.** Renaming a rule requires a contract request (section 9).

Other registries follow the same pattern (decorator + `discover()` over a package; agents add new files, never edit an index):

| Registry | Module | Registration | Added by |
|---|---|---|---|
| extractors | `extract/registry.py` | `@register_extractor(doc_types={...}, backend="text_layer" or "llm")` on a class implementing `Extractor` | extract-fin (`bank_statement.py invoice.py financial_statement.py contract.py`), extract-kyc (`kyc_*.py`) |
| classifiers | `classify/base.py` | `@register_classifier(name="rules"/"local"/"llm")` on a class implementing `Classifier` | ingest-classify, classifier-local |
| bundle builders | `scripts/_gen/bundle_*.py` | module-level `def build(spec: BundleSpec, out_dir: Path) -> BundleManifest` and `NAME: str` | gen-loan (`bundle_loan.py`), gen-kyc (`bundle_kyc.py`, `bundle_aml.py`) |
| routers | `api/*.py` | module-level `router = APIRouter(...)`; `main.py` includes each | api-backend |

```python
class Classifier(Protocol):                       # classify/base.py (plan 5.3: Classifier(text, labels) -> {label: probability})
    name: ClassVar[str]; version: ClassVar[str]
    def classify(self, text: str, labels: Sequence[str], *, question: Literal["C1","C3","C4","C5"], context: dict[str, str] | None = None) -> dict[str, float]: ...

class Extractor(Protocol):                        # extract/base.py
    doc_types: ClassVar[frozenset[str]]; backend: ClassVar[Literal["text_layer", "llm"]]; name: ClassVar[str]; version: ClassVar[str]
    def extract(self, doc: Document, ctx: CaseContext) -> ExtractionResult: ...
class ExtractionResult(BaseModel):
    fields: dict[str, Cell]; tables: dict[str, Table]; warnings: list[str] = []
```
Detailed behaviour lives in ingest-and-extraction.md and classification.md; those docs may not change these signatures (contract requests only).

## 5. Extraction has two backends

Design decision (cost levers 2, 3, 4; plan 6): extraction is a *cascade* over two interchangeable backends behind one `Extractor` protocol.

1. **`TextLayerExtractor`** (deterministic, free). Reads PyMuPDF words and boxes and parses our own digital PDFs (the generator's layouts) with layout-aware regexes. No model call. It also serves as the **cross-check against model output**: every value a model returns must be findable in the text layer words; the cascade sets `Cell.verified` (True/False, `None` when there is no text layer) and records mismatches in `Document.warnings`.
2. **`LLMExtractor`**. Text-only call when a text layer exists, vision call for scans and images. Schema-constrained output (`DOC_SCHEMAS` converted to a Pydantic response model), values copied exactly as printed, never corrected (plan 8.5).

Cascade (`extract/cascade.py`, mode `GLDB_EXTRACTOR_MODE`): `text_layer` first; if it fails its own validation (required fields missing, balance chain broken) or the page has no text layer, fall to the cheap model; if the cheap model's rows fail a balance/total check, re-extract that page with the strong model **before** raising a flag; keep both readings (`Cell.alt_raw`); if the strong model agrees with the cheap one, the mismatch stands and the check flags it (plan 6 lever 5).

**No API key is available in this project's build environment.** Therefore `get_llm_client(settings)` returns a `FakeLLMClient` unless `GLDB_LLM_API_KEY` is set:
- `FakeLLMClient(fixtures: dict[str, str | dict | Callable[[LLMRequest], dict]] | None = None, default: dict | None = None)` returns canned JSON keyed as in ingest-and-extraction.md 5.5 (lookup order: `purpose:<sha256(images[0] or user)[:12]>`, `purpose:<file_sha256[:12]>:<page>` parsed from `cache_extra`, `purpose:<model_role>`, `purpose`, `default`); records every request in `.calls` (tests assert no call happened when the text layer sufficed); reports zero cost with model `"fake"`; raises `LLMNoFixture` when nothing matches (the extractor turns that into a `Document.warnings` entry and `status="partial"`).
- Optional oracle mode: `FakeLLMClient.from_ground_truth(manifest_path)` answers `ocr:page` and `extract:<doc_type>` from the generator's `ground_truth.json` so scanned bundles can run offline (ingest-and-extraction.md 5.5). It does **not** answer `classify:*` (those requests are keyed by a text hash and fall back to the rules result); classification of scans runs the rules classifier on the oracle OCR text. The generator owns the manifest format (test-data-and-eval.md).
- Consequence, stated honestly in every doc that touches it: **scanned/image pages cannot be extracted in this environment without fixtures or a real key**; they become `doc_unreadable` info flags and a partial score.

`LLMClient` (core/llm.py, foundation):

```python
class LLMRequest(BaseModel):
    purpose: str                      # "extract:bank_statement", "classify:C1", ... (cache key + cost attribution)
    model_role: Literal["cheap", "strong"] = "cheap"
    system: str                       # fixed instructions; never contains document text
    user: str                         # document text, wrapped in explicit data delimiters
    images: list[bytes] = []          # PNG bytes
    schema_name: str; json_schema: dict  # from a Pydantic model
    cache_extra: str = ""             # file sha256 + page, appended to the cache key
class LLMResult(BaseModel):
    data: dict; model: str; cost: Cost; cache_hit: bool
class LLMClient(Protocol):
    def complete_json(self, req: LLMRequest, *, ctx_costs: CostMeter | None = None) -> LLMResult: ...
```
Rules: structured output via one function tool with `tool_choice="auto"`, then verify a tool call came back (OpenRouter rejects a forced `tool_choice` for Claude models); fall back to parsing JSON text; validate against the schema; **retry once** on parse failure, then raise `LLMSchemaError`. Usage tokens come from the API `usage` field. Cache key = sha256(purpose, model, prompt hash, cache_extra, client version); a cache hit costs `0` and is recorded with `cache_hit=True`. The model never has tools of its own and never sees other documents.

## 6. Storage (D3)

`GLDB_DATA_DIR` (default `./data`): `gldb.sqlite3`, `files/<sha[:2]>/<sha256>` (content-addressed originals, refcounted by cases), `renders/<sha256>/<page>_<width>.png` (page PNG cache). `core/db.py` creates all tables at startup (idempotent DDL), so later agents never edit it.

| Table | Columns | Owner of helpers |
|---|---|---|
| `cases` | `id` PK, `use_case`, `status`, `version`, `reference`, `applicant_name`, `created_at`, `updated_at`, `score` (int null), `band`, `lane`, `coverage_ratio` (real null), `partial` (0/1), `findings_count`, `high_count`, `decision`, `top_finding`, `body` JSON (full `Case` without `review`) | foundation (`Database`) |
| `case_versions` | `case_id`, `version`, `created_at`, `body` JSON; PK (`case_id`,`version`) | foundation |
| `jobs` | `id` PK, `case_id`, `kind` (`analyse`/`rerun`), `state`, `created_at`, `started_at`, `finished_at`, `error`, `stages` JSON | foundation |
| `review_entries` | `id` PK holding `"<case_id>/<entry id>"` (so `r1` is unique across cases; `ReviewEntry.id` stays `r<n>`), `case_id`, `case_version`, `created_at`, `body` JSON (append-only; no UPDATE except `body.status` of a pending entry) | DDL foundation; helpers api-backend (`review.py`) |
| `audit_log` | `id` INTEGER PK AUTOINCREMENT, `ts`, `actor`, `action`, `case_ref` (case id, or hash after delete), `detail` JSON (pseudonymised). DDL triggers: `BEFORE DELETE` always aborts; `BEFORE UPDATE` aborts unless `id`, `ts`, `actor`, `action`, `detail` are unchanged (only `case_ref` may change, for pseudonymisation after delete) | DDL foundation; helpers api-backend (`audit.py`) |
| `rule_overrides` | `rule` TEXT PK, `enabled` INTEGER NOT NULL DEFAULT 1, `severity`, `name`, `description`, `note` (TEXT null), `updated_by`, `updated_at`, `reason`, `revision` INTEGER NOT NULL. Check catalogue overrides: global configuration, never touched by delete-case or retention (scoring-and-queue.md "Catalogue overrides") | `core/db.py`, `catalogue.py` |
| `rule_changes` | `id` INTEGER PK AUTOINCREMENT, `rule`, `at`, `reviewer`, `reason`, `action` (`update`/`reset`), `before` JSON, `after` JSON. Permanent change log of the check catalogue; `BEFORE UPDATE` and `BEFORE DELETE` triggers always abort (append-only like `audit_log`) | `core/db.py`, `catalogue.py` |
| `fingerprints` | `kind` (`file_sha256`/`invoice`/`crop_phash`), `fingerprint`, `case_id`, `document_id`, `created_at`, `meta` JSON; index on (`kind`,`fingerprint`) | foundation (`core/fingerprints.py`) |
| `llm_cache` | `key` PK, `value` JSON, `created_at`, `model`, `purpose` | foundation (`core/cache.py`) |

`Database` public API (foundation): `Database(path)`, `init_schema()`, `conn()` (context manager yielding `sqlite3.Connection`, row factory = `sqlite3.Row`, WAL, `foreign_keys=ON`, one connection per call so threads are safe), `create_case(case)`, `save_case(case, *, snapshot_version: bool = False)` (also refreshes the denormalised columns: `findings_count` = findings with severity != info, `high_count` = findings with severity high, `partial` = `case.partial or (case.score is not None and case.score.partial)`, `coverage_ratio` = `case.coverage.ratio`), `load_case(case_id, version: int | None = None) -> Case` (merges into `Case.review` only the `review_entries` whose `case_version` equals the loaded version), `delete_case(case_id)` (removes the case rows, versions, review entries, jobs, fingerprints, unreferenced files and renders, and the `llm_cache` rows whose `value.file_sha256` belongs to a file whose reference count drops to zero; audit rows are kept by `audit.py` in pseudonymised form), `list_cases(filters: CaseFilters, sort: str, page: int, page_size: int) -> tuple[list[sqlite3.Row], int]`, job helpers `create_job`, `update_job`, `get_job`. Only parameterised SQL; no string-built queries.

`priority` sort (frozen here, used by the queue): status rank (`open`=0, `processing`=1, `awaiting_documents`=2, `closed`=3, `error`=4), then `score` DESC (a partial case ranks as at least score 30, so unreadable bundles are not buried under low-score cases), then `coverage_ratio` ASC (less coverage = less certainty), then `created_at` ASC (older first).

## 7. Dependencies (installed once by the foundation agent)

Foundation runs, in this order, then commits nothing: `uv init --no-readme` (or hand-write `pyproject.toml`), `uv add <runtime...>`, `uv add --dev <dev...>`, `uv sync`, `uv run python -c "import fastapi, fitz, cv2, imagehash, pikepdf, stdnum, rapidfuzz, reportlab, networkx, jinja2"`. Take the versions `uv add` resolves. After wave 0 **nobody edits `pyproject.toml` or `uv.lock`** (see section 9). If an agent needs a package that is missing, it files a contract request in its own doc and works around it with the standard library.

Runtime (`uv add`):

| Package | Why |
|---|---|
| `fastapi` | HTTP API (D1) |
| `uvicorn[standard]` | ASGI server |
| `pydantic` (v2) | all models and contracts |
| `pydantic-settings` | `Settings` from `GLDB_*` env and `.env` |
| `python-multipart` | multipart upload parsing for `POST /cases` |
| `httpx` | `fastapi.testclient.TestClient` backend, plus any outbound HTTP (none by default) |
| `openai` | the OpenAI-compatible SDK for `OpenAICompatClient` (OpenRouter or direct) |
| `pymupdf` | text layer words/boxes/spans (fonts, sizes), page render to PNG, PDF metadata |
| `pillow` | image I/O, EXIF, ELA re-save, scan degradation in the generator |
| `numpy` | array maths for CV, noise, Benford and statistics |
| `opencv-python-headless` | ORB copy-move, signature crop matching (headless: no GUI libs) |
| `imagehash` | perceptual hashes for `signature_reuse` (pulls scipy, PyWavelets, PyYAML transitively) |
| `reportlab` | generator renders digital PDFs and ownership charts |
| `pikepdf` | PDF structure: incremental updates, xref, signature `/ByteRange`, producer metadata, building tampered PDFs |
| `python-stdnum` | UEN validation (`stdnum.sg.uen`); never re-derive the UEN check letter. **python-stdnum has no `stdnum.sg.nric` module** (verified at R0: `stdnum.sg` contains only `uen`). The NRIC/FIN check letter is the public algorithm of plan 1.4, implemented twice on purpose (once in `checks/kyc.py`, once in the generator `_gen/ids.py`) and both pinned to the same hand-computed vectors in checks-kyc-aml.md 4.2, so a bug in one cannot hide behind the other |
| `rapidfuzz` | fuzzy name and company matching |
| `python-dateutil` | tolerant date parsing of printed dates (`dd MMM yyyy`, `dd/mm/yyyy`) |
| `networkx` | in-case entity graph and pass-through cycle/path queries |
| `jinja2` | HTML case pack template (FastAPI does not bundle it) |

Dev (`uv add --dev`): `pytest`, `pytest-timeout` (stop hung PDF/CV tests), `pytest-cov`, `ruff` (lint + format).

Local classifier packages (R0 decision): **no `local` extra is declared.** No model weights can be downloaded in the build environment and resolving `torch` for every platform makes `uv lock` slow and fragile. `classify/local.py` lazy-imports `torch`/`transformers` and raises `ClassifierUnavailable` when they are absent, which is the recorded gate outcome (plan 5.3). A human who runs the gate on a laptop (tasks H-13) adds them with `uv add --optional local torch transformers` and `uv sync --extra local`; that is the only sanctioned later change to `pyproject.toml`/`uv.lock`, and it is done by a human, not an agent.

Not used, on purpose: no local OCR binary installed by `uv sync` (the optional `GLDB_OCR_BACKEND=tesseract` drives a `tesseract` program you install yourself; by default scans go to the vision model), no pandas (plain Python plus numpy suffices), no ORM, no Celery/Redis, no Faker (the generator uses its own fictitious name lists so patronymic and Chinese-order cases are controlled).

Ruff config (in `pyproject.toml`): `line-length = 100`, `target-version = "py312"`, select `E,F,I,UP,B,SIM`; format with `ruff format`. Pytest: `testpaths = ["backend/tests"]`, `pythonpath = ["backend"]`, default `--timeout=120`.

Frontend (`frontend-scaffold` installs everything up front with npm): `next`, `react`, `react-dom`, `typescript`, `tailwindcss` (+ `postcss`, `autoprefixer` if the chosen major needs them), `eslint`, `eslint-config-next`, `@types/node`, `@types/react`, `@types/react-dom`, `swr` (polling jobs), `clsx`, `lucide-react` (icons), `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`. `frontend-app` does not edit `package.json` or the lockfile.

## 8. Settings and environment variables (prefix `GLDB_`)

`core/config.py` defines `Settings(BaseSettings)` with `env_prefix="GLDB_"`, `env_file=".env"`, `get_settings()` cached; tests build `Settings(...)` explicitly. `.env.example` lists all of these.

| Variable | Default | Meaning |
|---|---|---|
| `GLDB_DATA_DIR` | `./data` | database, files, renders |
| `GLDB_DB_PATH` | `<DATA_DIR>/gldb.sqlite3` | SQLite file (`:memory:` allowed in tests) |
| `GLDB_REGISTRY_PATH` | `./samples/registry.json` | mock ACRA-shaped registry (data-model.md section 8); missing file = registry checks skip |
| `GLDB_TODAY` | empty | ISO date; if set it is used as `case.today` for new cases (demos, tests). Empty = wall-clock date at case creation |
| `GLDB_LLM_BACKEND` | `auto` | `auto` (openai if key set, else fake), `fake`, `openai` |
| `GLDB_LLM_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint |
| `GLDB_LLM_API_KEY` | empty | secret; never logged |
| `GLDB_LLM_MODEL_CHEAP` / `GLDB_LLM_MODEL_STRONG` | empty | required when backend is `openai` (startup error otherwise) |
| `GLDB_LLM_TIMEOUT_S` | `60` | per call |
| `GLDB_LLM_SCHEMA_RETRIES` | `1` | retries after a schema parse failure |
| `GLDB_PRICES_PATH` | empty | JSON `{model: {"input_per_mtok": "0.15", "output_per_mtok": "0.60"}}`; unknown model = cost recorded as `0` with a warning |
| `GLDB_EXTRACTOR_MODE` | `cascade` | `text_layer`, `llm`, `cascade` |
| `GLDB_CLASSIFIER_MODE` | `rules` | `rules`, `local`, `llm`, `cascade` (rules, then hosted when below threshold) |
| `GLDB_CLASSIFIER_MIN_PROB` | `0.70` | below this a page is "low confidence": info flag, coverage reduced, escalated to the stronger classifier |
| `GLDB_LOCAL_CLASSIFIER_MODEL` | empty | local JEV-style model id (classification.md 4.3); empty = local classifier unavailable |
| `GLDB_LOCAL_CLASSIFIER_DEVICE` | `auto` | `auto`, `cpu`, `mps`, `cuda` |
| `GLDB_UBO_THRESHOLD_PERCENT` | `25.00` | beneficial-owner threshold read by `kyc_shareholdings` **[confirm against Notice 626]**; a chart can only lower it (min of the two) |
| `GLDB_RENDER_DPI` | `150` | page PNGs for the UI |
| `GLDB_VISION_DPI` | `150` | grayscale images sent to a model (cost lever 6) |
| `GLDB_MAX_UPLOAD_MB` | `25` | per file (413 above) |
| `GLDB_MAX_FILES_PER_CASE` | `40` | per upload request |
| `GLDB_MAX_PAGES_PER_CASE` | `200` | budget guard: pages beyond are not processed; score partial |
| `GLDB_MAX_LLM_CALLS_PER_CASE` | `100` | budget guard |
| `GLDB_MAX_USD_PER_CASE` | `2.00` | budget guard (dollar ceiling) |
| `GLDB_MAX_PAGE_WORKERS` | `4` | bounded page-level parallelism inside a case |
| `GLDB_JOB_WORKERS` | `2` | concurrent cases |
| `GLDB_CACHE_ENABLED` | `true` | LLM response cache |
| `GLDB_MONEY_TOLERANCE` | `1.00` | default absolute tolerance for amount comparisons (plan 4.1 and 4.2) |
| `GLDB_REQUIRE_SECOND_APPROVER` | `true` | dismissing a high flag needs a second reviewer |
| `GLDB_RETENTION_DAYS` | `1825` | 5 years **[confirm against Notice 626]**; used by `retention.py` |
| `GLDB_PSEUDONYMISE_LOGS` | `true` | application logs and audit `detail` carry ids and hashes, not names or NRICs |
| `GLDB_LOG_LEVEL` | `INFO` | |
| `GLDB_CORS_ORIGINS` | `http://localhost:3000` | comma-separated |

Settings attribute names are the lower-cased variable without the prefix (`llm_backend`, `llm_base_url`, `llm_model_cheap`, `llm_model_strong`, `money_tolerance`, `classifier_min_prob`, `require_second_approver`, `retention_days`, `pseudonymise_logs`, `ubo_threshold_percent`, ...); compliance tests cite them. `settings_hash` (provenance) covers every setting that changes results: tolerances, thresholds, modes, classifier mode and local model id, model names.

Frontend: `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`), `NEXT_PUBLIC_USE_MOCK` (`1` = mock-data mode, frontend.md section 11; npm script `dev:mock`). Dev ports: API 8000, UI 3000.

Commands: `uv sync`; `uv run uvicorn --app-dir backend app.main:app --reload`; `uv run pytest`; `uv run ruff check . && uv run ruff format --check .`; `uv run python backend/scripts/make_bundle.py ...`.

## 9. Parallel-work rules

1. **Read order for every agent:** this file, data-model.md, api.md (if you touch HTTP or the UI), then the feature doc for your area.
2. **Write only files you own** (roster, section 12). Reading anything is fine.
3. **Registries auto-discover** (section 4). To add a check, extractor, classifier, bundle builder or router you add a new file under the owning package and edit no shared index (`__init__.py` stays empty except where the foundation wrote it).
4. **`pyproject.toml`, `uv.lock`, `.python-version`: frozen after the foundation.** No `uv add` by anyone else. Run commands as `uv run ...` only.
5. **`core/**` is frozen after wave 0**, as are `core/schemas.py` field and column names. Changing a contract is a **Contract request**: append a bullet under the `## Contract requests` heading at the end of the feature doc for your area (what, why, which files change). That and appending to that doc's `## Known gaps and honest limits` section are the only doc edits an implementing agent makes. The Opus reviewer and `integration-kyc` resolve them; nobody edits another agent's file to "fix" a contract.
5a. **No in-wave imports of a sibling's module at test time.** Within one wave, an agent never needs another same-wave agent's file to run its own tests. Where code must call a same-wave module at runtime (extract-fin's annotation pass calling `app.classify.base.get_classifier`/`decide`; extract-fin's vision mode calling `app.ingest.render.render_for_model`; forensics' `OVERLAP_MIN` equality test against `app.findings`), the import is done inside the function, the dependency is injectable (`classifier_factory=`, `render=`), and the test injects a stub or skips with a reason when the module is absent. Shared pure helpers that several wave-1 agents need (`layout_text`, `page_marker`, `company_key`/`company_match`/`company_disc`, fingerprints) live in `core/` (foundation, wave 0). The W1 gate runs the whole suite, which proves the real wiring.
6. **Tests live beside your code** with a unique prefix: `test_<owned module>*.py`. Never edit another agent's test file. Use `app.core.testing` helpers (`make_ctx`, `make_doc`, `make_table`, `FIXED_TODAY = date(2026, 10, 27)`), never `date.today()`.
7. **No network in tests.** LLM paths use `FakeLLMClient`. No test needs an API key.
8. **Determinism:** no wall clock, no unseeded randomness (generator takes `--seed`, default 4002), no dict-order dependence in outputs.
9. **Do not git commit.** Do not touch files outside the repo.
10. **Honest gaps (plan 0.6):** anything lacking a check, a UI flag or an eval row is written under "Known gaps" in your doc, not claimed as done.
11. A wave starts only when the previous wave's exit check is green: wave 0 `uv run pytest` and `ruff` pass with stub stages and `GET /health`; wave 1 each agent's own tests pass; wave 2 loan end-to-end test passes; wave 3 full test suite plus eval run.

## 10. Error handling policy (plan 8.5)

| Situation | Policy |
|---|---|
| Unreadable page, corrupt PDF, image that will not decode | Local failure: `PageInfo.error`, `Document.status="partial"/"failed"`, warning, `doc_unreadable` info flag, score partial. Never a crash |
| Model output not parseable | Reject, retry once (`GLDB_LLM_SCHEMA_RETRIES`), then `LLMSchemaError`; the extractor records a warning and the cascade tries the next backend |
| Model returns a value not in the text layer | `Cell.verified=False`, warning; the check layer decides (never silently "corrected") |
| Forced `tool_choice` on OpenRouter Claude models | Use `auto` and assert a tool call returned (section 5) |
| Printed text that looks like an amount but is an ID (`S8222222A`) | `parse_money` returns `None` when a letter touches a digit (data-model.md section 2) |
| A check raises | Run recorded as `status="error"` (message only), coverage reduced, other checks continue, stage warning, score partial |
| A check cannot evaluate (missing field, currency mismatch) | `raise CheckSkipped(reason)`, counted as not ran |
| A stage raises | `StageRecord.status="failed"`, score partial, remaining stages still run |
| Budget guard (pages, calls, dollars) | `LLMBudgetExceeded`, stage partial with message shown on the case page, deterministic checks still run |
| API errors | Single JSON body `{"error": {"code", "message", "details"}}` (api.md section 1). No stack traces to clients; log with correlation id |
| Review rule violation | 4xx with a specific code; nothing is written |
| Fatal startup misconfiguration (backend `openai` without models) | Fail fast at startup with a clear message |

## 11. Untrusted input (plan principle 5)

- Extracted text and filenames are attacker-controlled. Never use them in file paths (stored names are `sha256`), shell, SQL (parameters only), `eval`, or HTML. The case pack HTML template uses Jinja autoescape; the frontend renders `evidence` and `explanation` as text, never `dangerouslySetInnerHTML`.
- Prompts: instructions go in `system` (fixed, hashed into provenance); document text goes in `user` between explicit delimiters and is described as data. Extractors have no tools and no access to other documents.
- Output is schema-constrained and length-capped (`Cell.raw` max 500 chars, `Flag.evidence` max 1000, `Flag.explanation` max 300).
- A classifier can only raise risk: label `other`/`boilerplate` or low probability yields an info flag and a coverage reduction (plan 5.3). Injection canaries are part of the eval.
- Resource limits: file size, page count, `Image.MAX_IMAGE_PIXELS = 50_000_000`, per-file processing timeout of 60 s in ingest.
- Upload validation checks magic bytes (`%PDF-`, JPEG `FFD8FF`, PNG `89504E47`), not just the declared content type.
- The applicant is never shown flags (tipping-off risk, plan 7.2 [confirm]); there is no applicant-facing endpoint.

## 12. Agent roster and file ownership (MUST be respected)

Paths are relative to the repo root unless they start with `docs/`, `backend/` or `frontend/`. "Wave" = start order. An agent never edits a path owned by another row.

| Agent | Wave | Role | Owns |
|---|---|---|---|
| foundation | 0 | Backend foundation, contracts frozen | `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, `.env.example`, `README.md`, `.github/workflows/**`, `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/pipeline.py`, `backend/app/score.py` (basic), `backend/app/core/**` (models, schemas, context, config, db, provenance, costs, cache, llm, money, textnorm, textlayout, fingerprints, registry, builtin_checks, testing), `backend/app/checks/__init__.py`, `backend/app/checks/registry.py`, `backend/app/checks/bank_statement.py` (only `bs_running_balance`), stub stage modules `backend/app/{ingest,classify,extract}/stage.py`, stub routers `backend/app/api/*.py`, `backend/tests/__init__.py`, `backend/tests/conftest.py`, `backend/scripts/__init__.py`, and tests for the above |
| frontend-scaffold | 0 | Next.js scaffold | `frontend/**` (scaffold only) |
| gen-loan | 1 | Mock bundle generator: loan bundle and the shared generator machinery | `backend/scripts/make_bundle.py`, `backend/scripts/_gen/**` (except files named `bundle_kyc*.py`, `bundle_aml*.py`, `kyc_*.py`), incl. the renderers every loan bundle needs: statements, invoice, FS, contract, PO, **company profile and national ID** (`render_profile.py`, `render_id.py`), `ids.py` (UEN/NRIC/MRZ/GST builders); `backend/tests/test_gen_*.py` (except `test_gen_kyc*`), `samples/README.md` |
| ingest-classify | 1 | Ingest and classification (rules) | `backend/app/ingest/**`, `backend/app/classify/**` (`base.py`, `rules.py`, `llm.py`, `stage.py`; `local.py` is only a stub raising a clear "not available" error), `backend/tests/test_ingest_*.py`, `test_classify_*.py`, `backend/tests/ingest_helpers.py` |
| extract-fin | 1 | Extraction: bank statement, invoice, FS, contract, PO | `backend/app/extract/**` (`registry.py`, `base.py`, `text_layer.py`, `llm_extractor.py`, `cascade.py`, prompts, `bank_statement.py`, `invoice.py`, `financial_statement.py`, `contract.py`, `stage.py`), tests |
| rules-bank | 1 | Bank statement rules | `backend/app/checks/bank_statement.py` (extends foundation's `bs_running_balance`), `backend/tests/test_checks_bank_statement.py` |
| rules-inv-fs-cross | 1 | Invoice, FS, cross-document rules | `backend/app/checks/invoice.py`, `financial_statement.py`, `crossdoc.py`, tests |
| rules-kyc | 1 | KYC/AML rules, name matching, entity graph | `backend/app/checks/kyc.py`, `aml.py`, `backend/app/graph.py`, `backend/app/names.py`, tests |
| forensics-pdf | 1 | PDF forensics | `backend/app/checks/forensics_pdf.py`, `backend/tests/test_forensics_pdf*.py`, `backend/tests/forensics_pdf_factory.py` (fixture builder, not collected) |
| forensics-image | 1 | Image forensics | `backend/app/checks/forensics_image.py`, `backend/tests/test_forensics_image*.py`, `backend/tests/forensics_image_factory.py` |
| score-queue | 1 | Scoring, findings, ranked queue | `backend/app/score.py` (extends foundation's), `backend/app/findings.py`, `backend/app/queue.py`, `backend/tests/test_findings.py`, `test_score*.py` (incl. foundation's `test_score_basic.py`, which it may update), `test_queue.py`, `test_scoring_properties.py` |
| api-backend | 1 | API, review workflow, audit log, case pack | `backend/app/api/**`, `backend/app/review.py`, `audit.py`, `pack.py`, `effectiveness.py`, `retention.py`, `backend/tests/test_api_*.py` |
| frontend-app | 1 | Frontend pages | `frontend/**` |
| integration-loan | 2 | Loan bundle end to end | `backend/scripts/run_case.py`, `backend/tests/e2e/**`, `backend/app/pipeline.py`; minimal bug fixes in any wave-1 file EXCEPT `backend/scripts/_gen/bundle_kyc*`, `bundle_aml*`, `kyc_*`, `backend/app/extract/kyc_*`, `backend/app/classify/local.py` |
| gen-kyc | 2 | Mock bundle generator: KYC and AML | `backend/scripts/_gen/bundle_kyc.py`, `bundle_aml.py`, `kyc_*.py` (certificate, ownership chart, register, passport, SoF renderers, `kyc_fairness.py`), `backend/tests/test_gen_kyc*.py`. Uses gen-loan's `world.py`, renderers (incl. profile and national ID), `verify.py` read-only |
| extract-kyc | 2 | Extraction: KYC and AML documents | `backend/app/extract/kyc_*.py` (new modules only, auto-discovered), `backend/tests/test_extract_kyc_*.py` |
| classifier-local | 2 | Local JEV-style classifier and eval gate | `backend/app/classify/local.py`, `backend/scripts/classifier_gate.py`, `backend/tests/test_classifier_*.py`, `docs/classifier-gate.md`, and exactly one file under eval's folder: `docs/eval-results/classifier-gate.json` |
| compliance-docs | 2 | Compliance documents, generated catalogue | `docs/ai-inventory.md`, `docs/regulatory-mapping.md`, `docs/validation-report.md` (skeleton with placeholders), `docs/check-catalogue.md`, `backend/scripts/gen_check_catalogue.py`, `backend/tests/test_check_catalogue.py`, `backend/tests/test_compliance_*.py` |
| integration-kyc | 3 | KYC and AML end to end, hardening | `backend/tests/e2e/test_kyc_*.py`, `test_aml_*.py`, `test_fairness_smoke.py`, `test_not_checked.py`; may fix any file in `backend/`, `docs/` (except eval's `validation-report.md` content between markers, `traceability.md`, `demo-script.md`, `eval-results/**`) and, because nobody else owns the UI after wave 1, `frontend/src/**` (never `package.json` or the lockfile). Works only in slot 3b, when `eval` is idle |
| eval | 3 | Tamper catalogue, eval harness, ranking and fairness tests | `backend/scripts/make_tampered.py`, `backend/scripts/eval.py`, `backend/scripts/_eval/**`, `backend/tests/test_eval_*.py`, `docs/validation-report.md` (fill in), `docs/traceability.md`, `docs/demo-script.md`, `docs/eval-results/**` |

Doc ownership: `docs/architecture.md`, `docs/data-model.md`, `docs/api.md`, `docs/README.md` belong to the contracts author. `docs/tasks.md`, `ingest-and-extraction.md`, `classification.md`, `cost-efficiency.md`, `checks-financial.md`, `checks-kyc-aml.md`, `forensics.md`, `scoring-and-queue.md`, `review-workflow-and-audit.md`, `frontend.md`, `test-data-and-eval.md`, `compliance.md` are written by the spec agents right after this one and then maintained only by the Opus reviewer or `integration-kyc`.

Notes on overlaps that look like collisions but are not:
- `backend/app/score.py`: foundation writes the basic version in wave 0 (`run_stage`, weights, bands, one finding per flag, coverage ratio). `score-queue` then owns it in wave 1.
- `backend/app/checks/bank_statement.py`: foundation writes only `bs_running_balance`; `rules-bank` extends the same file in wave 1 and keeps that function's behaviour.
- `backend/app/pipeline.py`: foundation in wave 0, `integration-loan` in wave 2.
- `frontend/**`: `frontend-scaffold` in wave 0, `frontend-app` in wave 1 (after the scaffold).
- `docs/validation-report.md`: skeleton by `compliance-docs` (wave 2), filled by `eval` (wave 3).
- `backend/app/api/*.py`: foundation stubs return `501 not_implemented` (except `/health`); `api-backend` replaces them. `main.py` calls an optional module-level `init(app, settings)` of each discovered api module at startup, so api-backend can install `audit.install_log_filter` from `api/review.py` without editing `main.py`.
- `integration-loan` (wave 2) may fix wave-1 files, including `frontend/src/**`, but must not change the public signatures of `_gen/schema.py`, `_gen/world.py` or the renderers that `gen-kyc` is building against in the same wave; such a change is reported to `integration-kyc` instead.
- `docs/check-catalogue.md`: compliance-docs regenerates it as its last step in wave 2; whoever changes a check later reruns `gen_check_catalogue.py` (CI `--check` enforces it).

## 13. Walking skeleton (wave 0 exit check)

Foundation proves the contracts with: a `bs_running_balance` check; a `make_ctx` fixture; `GET /health`; discovery tests (registry finds the check, router discovery includes every stub); `test_no_wallclock_in_checks.py`; `parse_money("S8222222A") is None`; `FakeLLMClient` unit tests; `run_pipeline` on a case with zero files finishing `open` with `score.partial=True`; CI workflow running `uv sync`, `uv run ruff check .`, `uv run pytest`.

## 14. Known gaps and honest limits

- No live registry, MyInfo or IRAS calls; the registry is a generator-written JSON file (plan 2).
- No local OCR: scans need a vision model or fixtures. Without a key, scanned pages are reported as not read.
- Deterministic extraction targets our generator's layouts; real bank layouts are only handled through the LLM path.
- The local JEV-style classifier is a stub until the gate in plan 5.3 is run.
- Single-process job runner; no auth beyond a reviewer name (multi-tenant security is out of scope, plan 2).
- Eval results are an upper bound because we generated the data (plan 9.4).

## Contract requests

**W3 closure (T-integration-kyc-08):** every request in this section has a recorded outcome in tasks.md sections 4.2 and 4.2a (accepted, decided at R0, or not built and listed under Known gaps above); none is open.

R0 (Opus review) resolved every request raised by the spec authors; the outcome of each is in tasks.md section 4.2 (CR-01 to CR-80) and the contract text above, data-model.md and api.md already reflect the accepted ones. New requests from implementing agents are appended below.

**W0 foundation (additions and deviations; none changes a frozen signature).**
- **Added modules in `core/`** (all foundation-owned): `errors.py` (the LLM error family, re-exported by `core/llm.py`, plus `ApiError` and `not_implemented()` that `main.py` renders as the single error body: api-backend raises `ApiError(status, code, message, details)`), `storage.py` (`FileStore`: content-addressed put/read/delete, same layout as `Settings.files_dir`), `cases.py` (`new_case`, `add_file`, `sniff_mime`, `sanitise_filename`, `next_file_id`), `deps.py` (`get_db`, `get_app_settings`, `get_runner` FastAPI dependencies reading `app.state`).
- **`Database`** also has `Database(path, *, data_dir=None)`, `.files` (a `FileStore`), `case_exists`, `list_versions`, `latest_job`, `recover_interrupted_jobs`; `list_cases(filters: CaseFilters | None, sort, page, page_size, *, order=None)`; `get_job`/`create_job` return a `JobRecord`; `delete_case` returns a `DeleteResult`; `save_case(case, *, snapshot_version=False, touch=True)` refreshes `updated_at`. `:memory:` uses one locked shared connection. The `fingerprints` table has a unique index on `(kind, fingerprint, case_id, COALESCE(document_id, ''))` so `record` is idempotent; `SqliteFingerprintStore` and `MemoryFingerprintStore` also have `forget_case(case_id)`.
- **Rerun and jobs live in `pipeline.py`:** `run_pipeline` bumps `version` itself when the case already looks analysed; an API that must know the new version first (add files) calls `prepare_rerun(db, case_id)` (snapshot, version+1, outputs cleared, `processing`), adds its files with `added_in_version=case.version`, saves, then `JobRunner.submit(case_id, "rerun")`; `run_pipeline` then does not bump twice. `JobRunner(db, settings, llm=None, registry=None)` is on `app.state.runner`. `STAGE_RUNNERS` lets tests override a stage runner.
- **Pipeline reasons:** a case with no documents after `extract` gets partial reason `no_documents: nothing was classified or extracted` (so a zero-file case ends `open` with `score.partial=True`); a malformed registry file gives `registry_unreadable: ...` instead of crashing; a budget breach no stage reported is appended as its `budget_exceeded:` message.
- **Checks:** `make_flag` needs `severity=` only for an unregistered rule; locators replace `.` with `_`; document ids sort naturally (`d9+d12`). The runner treats `failed` and `received_not_checked` documents as not present when testing `applies_to`/`requires`, and `"*"` needs at least one file or document. `bs_running_balance` sets **no** discriminator, so its id equals the data-model example `bs_running_balance.d3.transactions:17`.
- **`FakeLLMClient`** takes an optional `cache=` (off by default; `get_llm_client` does not pass one) and refused-by-budget requests are not recorded in `.calls`. The oracle returns `page_marker` from the ground-truth `page_chain` rows when present.
- **`Region`** rejects `x+w > 1.0001` or `y+h > 1.0001` (loud failure, not clamping): generator and extractors must clamp their own boxes.
- **Tooling:** `pymupdf` 2.0 resolves with `pymupdf-layout` and `pymupdf4llm`; `import fitz` raises a `FutureWarning`, so new code should `import pymupdf`. Ruff ignores `E501` (the formatter wraps code), treats `backend` as first-party (`src`), and excludes `docs`/`*.md` (ruff 0.17 would otherwise reformat Markdown code blocks).
- **Contract request for api-backend/score-queue (no change needed):** `Coverage.pages_unchecked` is left at `0` by the foundation scorer; score-queue fills it.
