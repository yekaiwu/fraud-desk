# Anthropic backend (Claude Haiku 5.5 only)

How to run the tool against Anthropic's API with the official `anthropic` Python SDK, what the one allowed model is, what a request looks like and why, what it costs, and how the spend is capped. This is the second real LLM backend; the OpenAI-compatible client (OpenRouter, a local server, see [local-llm.md](local-llm.md)) is unchanged. Companion to [cost-efficiency.md](cost-efficiency.md) (budget guard) and [ai-inventory.md](ai-inventory.md) (third-party register). All data in this repo is fictitious.

Status, stated plainly: everything here was built and tested **offline** (the real SDK over an `httpx2.MockTransport`; no network call, no live key). **No live call has been made.** Section 10 is the checklist that turns "built" into "verified".

## 1. What it is

| Item | Value |
|---|---|
| Backend switch | `GLDB_LLM_BACKEND=anthropic` (explicit opt-in; `auto` never selects it) |
| Code | `backend/app/core/llm_anthropic.py` (`AnthropicLLMClient`), created by `get_llm_client` |
| SDK | the official `anthropic` package (2.x, built on `httpx2`), not the OpenAI-compatibility shim |
| Model | `claude-haiku-5-5`, nothing else (section 3) |
| Roles | `cheap` and `strong` both resolve to the same model, so the cascade does not escalate to a second, identical call |
| Behaviour shared with the other client | response cache, cost telemetry, the process-wide concurrency gate (`GLDB_LLM_MAX_CONCURRENCY`), schema validation with `GLDB_LLM_SCHEMA_RETRIES` (default one) retry, the per-case budget guard, vision |

## 2. Setup

1. The key lives in the repo-root `.env` (gitignored) as `GLDB_ANTHROPIC_API_KEY`. It is a pydantic `SecretStr`: it is not in any repr, log line, error message, doc or test. Never paste it into a chat, a ticket or `.env.example`.
2. **Workspace id.** The owner's key is user-scoped, and for such keys the API requires the header `anthropic-workspace-id`. Find the id in the Anthropic Console, in the settings of the workspace the key belongs to (it starts with `wrkspc_` `[confirm]`), and put it in `.env` as `GLDB_ANTHROPIC_WORKSPACE_ID`. The client then sends it on every request as a default header. If it is missing and the API wants it, the call fails with an error that names `GLDB_ANTHROPIC_WORKSPACE_ID` (and `anthropic_smoke.py` exits 2).
3. Keep `GLDB_ANTHROPIC_MODEL=claude-haiku-5-5` (it is also the default).
4. Switch the backend on: `GLDB_LLM_BACKEND=anthropic`. Until then the key does nothing (the backend stays `fake`).
5. Verify with the smoke script (section 10), then use **fictitious documents only** (section 8).

| Variable | Default | Meaning |
|---|---|---|
| `GLDB_LLM_BACKEND` | `auto` | `anthropic` selects this backend |
| `GLDB_ANTHROPIC_API_KEY` | empty | secret; required for the backend |
| `GLDB_ANTHROPIC_MODEL` | `claude-haiku-5-5` | anything else stops the application at start-up |
| `GLDB_ANTHROPIC_WORKSPACE_ID` | empty | sent as the `anthropic-workspace-id` header when non-empty |
| `GLDB_ANTHROPIC_TIMEOUT_S` | `120` | per request, seconds |
| `GLDB_LLM_MAX_OUTPUT_TOKENS` | `8000` | `max_tokens`; if a page is cut off the error names this setting |
| `GLDB_LLM_MAX_CONCURRENCY` | `4` | simultaneous requests, all threads together |
| `GLDB_MAX_USD_PER_CASE` | `2.00` | per-case dollar guard; **use `0.25` or less with this backend** (section 7) |
| `GLDB_MAX_USD_PER_DAY` | `5.00` | global daily dollar cap, paid backends only; `0` disables |

Under this backend `GLDB_LLM_MODEL_CHEAP` and `_STRONG` are ignored (both become the Haiku id, which is what provenance records) and `GLDB_LLM_SUPPORTS_VISION` is forced to `true`, so a leftover local-model profile in the same `.env` cannot change them.

## 3. The Haiku-only rule

The owner pays for this key and allowed exactly one model. It is enforced in four places, so no code path can send another model name:

1. `Settings.validate_startup()` raises `SettingsError` when the backend is `anthropic` and the key is empty or `GLDB_ANTHROPIC_MODEL` is not in `ALLOWED_ANTHROPIC_MODELS` (`{"claude-haiku-5-5"}`, in `core/config.py`). The application will not start.
2. `get_llm_client` and `AnthropicLLMClient.__init__` run the same check.
3. `complete_json` checks the resolved model again before the cache lookup.
4. `_create` checks the model name in the final request one more time, immediately before the SDK call. A violation raises `LLMModelNotAllowed` and nothing is sent.

Adding a model means editing `ALLOWED_ANTHROPIC_MODELS` and `BUILTIN_PRICES` on purpose, with the owner's agreement.

## 4. The request, and why

One `messages.create` call per model call, with exactly these fields:

| Field | Value | Why |
|---|---|---|
| `model` | `claude-haiku-5-5` | section 3 |
| `max_tokens` | `GLDB_LLM_MAX_OUTPUT_TOKENS` | a cap on output dollars; `stop_reason: max_tokens` is an error naming the setting |
| `system` | the fixed instructions plus "Reply only by calling the tool `<name>` once." | document text never goes in `system` (untrusted-data rule) |
| `messages` | one `user` turn: image blocks first, then the wrapped document text; on the retry a second text block carries the validation error | never an assistant prefill (a 400 on this model) |
| `tools` | one tool whose `input_schema` is the JSON schema; no `strict` | our schemas may not meet the requirements of strict mode; we validate ourselves |
| `tool_choice` | `{"type": "auto"}` | forced tool choice is not used; the prompt instructs the call |
| `thinking` | `{"type": "disabled"}` | extraction copies printed values; reasoning tokens would only cost money. Thinking blocks are never read or stored |
| `output_config` | `{"effort": "low"}` | disabled thinking is only accepted at effort `high` or below, and the default effort is `medium` |

Never sent: `temperature`, `top_p`, `top_k` (non-default values are a 400 on this model), `stream`, `cache_control` (so no cache writes exist to be billed).

Reading the answer: the `tool_use` block's `input` (already a dict); if the model answered in text instead, JSON is parsed from the text blocks as the OpenAI-compatible client does (fences, prose around it, a printed `{"name": ..., "arguments": ...}` wrapper). The result is validated against the schema by our own validator; invalid output is retried once with the error as feedback (`GLDB_LLM_SCHEMA_RETRIES`), then `LLMSchemaError`.

Retries on the network are the SDK's own (`max_retries=2` for 408, 409, 429, 5xx and connection errors, honouring `retry-after`). There is no second retry layer on top, so one logical call is at most three HTTP attempts and counts once against `GLDB_MAX_LLM_CALLS_PER_CASE`.

## 5. Vision

Haiku 5.5 accepts images, so `GLDB_LLM_SUPPORTS_VISION` is true for this backend and scans are read by the model (`ocr:page`, and vision extraction), unless `GLDB_OCR_BACKEND=vision` forces on-device OCR. An image is sent as `{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ...}}`, grayscale at `GLDB_VISION_DPI`, before the text block. Sending images costs input tokens; if cost matters more than model-quality OCR, set `GLDB_OCR_BACKEND=vision` (macOS only, `uv sync --extra ocr`) so only text leaves the machine.

## 6. Pricing

Haiku 5.5 is billed per request by the size of the prompt:

| Prompt size | Input per million tokens | Output per million tokens |
|---|---|---|
| up to 100,000 tokens | $0.10 | $0.50 |
| above 100,000 tokens | $0.50 | $2.50 |

The tier is decided per request and applies to all of that request's tokens. The prompt size is `usage.input_tokens + cache_read_input_tokens + cache_creation_input_tokens`; exactly 100,000 is the lower tier. Dollars are quantised to six places, half up, like every other price (`core/costs.py`, `BUILTIN_PRICES`; a `GLDB_PRICES_PATH` entry for the same model overrides it).

**Cache prices are unknown to us, so cache reads and cache writes are billed at the plain input rate** (conservative for reads; no discount is invented). The client sends no `cache_control`, so cache writes do not occur and the cost shown is the cost incurred. If a future change turns caching on, add the real prices to the price table first.

The per-case guard `GLDB_MAX_USD_PER_CASE` counts these real dollars. A model that is not in the price table is still priced pessimistically by the guard ($15 / $75 per million), as before.

## 7. Spend caps (the site may be public)

* **Per case**: `GLDB_MAX_USD_PER_CASE` (code default unchanged at `2.00`). A page costs a few thousandths of a dollar on this model, so a case rarely needs more than a few cents; set `0.25` (or less) in `.env` so a runaway case stops early. Together with `GLDB_MAX_LLM_CALLS_PER_CASE` and `GLDB_MAX_PAGES_PER_CASE` it bounds what one upload can spend.
* **Per UTC day, all cases together**: `GLDB_MAX_USD_PER_DAY` (default `5.00`, `0` disables). It applies only to paid backends (`anthropic`, `openai`), never to the fake backend or to a local model priced at `$0`. Before each paid call the guard reads the day's total from table `daily_spend` (integer micro-dollars per UTC date, created idempotently by `init_schema`); after each paid call it adds the call's dollars (an unpriced model counts at the pessimistic rate). When the total has reached the cap the call is refused with `LLMBudgetExceeded`, and the case becomes partial with the visible reason `budget_exceeded: daily spend cap reached (limit 5.00 USD per UTC day, used 5.012300)`, through the same path as the per-case limits: deterministic checks still run, the score says it is partial. The day rolls over at 00:00 UTC.
* The cap is a **soft** limit: it is checked before a call and charged after it, so calls already in flight when it is reached can overshoot by their own cost (bounded by `GLDB_LLM_MAX_CONCURRENCY` and `GLDB_JOB_WORKERS`). Cache hits cost nothing and are never refused.
* On a public site the other protections are the shared-password gate, `GLDB_RATE_CASES_PER_HOUR` and `GLDB_MAX_ACTIVE_JOBS` ([deploy.md](deploy.md)). Also set a monthly spend limit in the Anthropic Console: it is the only cap that survives a bug here.

## 8. Privacy

Page text, and for scans the page image, are sent to Anthropic, a third party outside the bank's control (location, retention and training terms `[confirm]`). **Use fictitious documents only. Never upload real customer data** while this backend is on. Pseudonymisation of outbound text is not implemented (one page at a time, no other document, no tools is the only minimisation), see [ai-inventory.md](ai-inventory.md) section 2. The key stays in `.env` / `deploy/.env.production`; error messages, logs and reprs contain ids, counts and dollars, never the key (a test formats the settings, the client and a raised API error and asserts the key text is absent, even when the server echoes it back).

## 9. Failures

| What happens | What the tool does |
|---|---|
| `stop_reason: refusal` (`stop_details.category` such as `cyber`, `bio`, `general_harms`) | not retried, not a crash: `LLMRefusal` (a kind of `LLMSchemaError`) with the category in the message; the page is unread (`PageReadError("schema")`), the case is partial and the existing `doc_unreadable` info flag appears. The call is still billed and recorded. |
| `stop_reason: max_tokens` | `LLMSchemaError` naming `GLDB_LLM_MAX_OUTPUT_TOKENS`; not retried |
| output that does not match the schema | one retry with the error as feedback, then `LLMSchemaError` |
| 401 | `LLMAuthError`: check `GLDB_ANTHROPIC_API_KEY` |
| 403 | `LLMAuthError`: the key may not use the model or workspace |
| 404 | `LLMTransportError`: model not found for this key |
| 400 mentioning "workspace" | `LLMWorkspaceRequired`: set `GLDB_ANTHROPIC_WORKSPACE_ID` |
| other 400, 413, 422 | `LLMTransportError` with the API's one-line message (key-shaped text removed) |
| 429, 5xx, connection error, timeout | the SDK retries twice, then `LLMTransportError` |

Every message carries the `request-id` when the API sent one, for support requests.

## 10. Which stages call the model

| Stage | Calls the model? | Image? |
|---|---|---|
| Ingest: file checks, page facts, text layer, PDF metadata | no | no |
| Ingest: OCR of scans and photos (`ocr:page`) | yes, unless `GLDB_OCR_BACKEND=vision` | yes, one page |
| Classification C1-C5 | only when `GLDB_CLASSIFIER_MODE` is `llm` or `cascade` (default `rules`, no model) | no (minimised text) |
| Extraction, digital pages with a known layout | no (text-layer parser) | no |
| Extraction, digital pages the parser rejects or whose arithmetic fails | yes (`extract:<doc_type>`) | no |
| Extraction, scans and photos | yes | yes, one page |
| Row annotation (counterparty, C4, C5) | only in `llm` / `cascade` classifier modes | no |
| Cascade "strong" re-read | skipped: both roles are the same model | |
| Checks, scoring, queue, review, case pack, image forensics | no | no |

## 11. Verifying it (the owner runs these)

```
uv run python backend/scripts/anthropic_smoke.py --dry-run   # settings only, sends nothing
uv run python backend/scripts/anthropic_smoke.py             # one call, max_tokens 256, a fraction of a cent
```

The smoke script prints the model, input and output tokens, the dollars from the price table, the seconds and the request id; it never prints the key. Exit code 0 = worked, 1 = the call failed or the answer was wrong, 2 = not usable: key missing or rejected, model not `claude-haiku-5-5`, or the API wants a workspace id (set `GLDB_ANTHROPIC_WORKSPACE_ID`). It does not need `GLDB_LLM_BACKEND=anthropic` yet.

Then set `GLDB_LLM_BACKEND=anthropic`, generate fictitious bundles (`backend/scripts/make_bundle.py`), upload one, and read `cost_summary` on the case (calls, dollars, `usd_by_stage`). Compare a digital bundle (few or no calls) with a scanned one.

`backend/scripts/llm_eval.py` is built around the local text-only server (it pre-flights `/health` and `/v1/models` and forces `supports_vision=false`), so it was **not** retargeted at this backend; a Haiku evaluation would need that script generalised and the owner's sign-off on its spend. Until then the accuracy of this backend on real layouts is unmeasured.

## 12. Honest limits

* Not run live: request fields were taken from Anthropic's documentation for Haiku 5.5 and tested against a mock transport, not against the API.
* Cache prices and the exact workspace-id location in the Console are unconfirmed `[confirm]`.
* The daily cap is soft (section 7) and counts only what this application records; the Anthropic Console limit is the hard stop.
* Refusals are false positives for benign documents sometimes; the tool shows the page as unread rather than guessing.
