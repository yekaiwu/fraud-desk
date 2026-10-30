# Anthropic backend (Claude Haiku 5.5 only)

How to run the tool against Anthropic's API with the official `anthropic` Python SDK, what the one allowed model is, what a request looks like and why, what it costs, and how the spend is capped. This is the second real LLM backend; the OpenAI-compatible client (OpenRouter, a local server, see [local-llm.md](local-llm.md)) is unchanged. Companion to [cost-efficiency.md](cost-efficiency.md) (budget guard) and [ai-inventory.md](ai-inventory.md) (third-party register). All data in this repo is fictitious.

Status, stated plainly: the backend was built and tested **offline** (the real SDK over an `httpx2.MockTransport`; no network call), and has since had **one small live sanity check**: 9 billed calls on 3 fictitious pages, about $0.0079 in all (section 11). That check is not an evaluation: three clean generated pages, no tampered documents, so the accuracy of this backend on real layouts and its effect on fraud detection remain unmeasured. Section 11 has the numbers and their limits.

## 1. What it is

| Item | Value |
|---|---|
| Backend switch | the default: `auto` picks this backend whenever `GLDB_ANTHROPIC_API_KEY` is set (it wins over `GLDB_LLM_API_KEY`); `GLDB_LLM_BACKEND=anthropic` forces it; `GLDB_LLM_BACKEND=openai` selects the local Qwen or another OpenAI-compatible server instead |
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
5. Verify with the smoke script (section 11), then use **fictitious documents only** (section 8).

| Variable | Default | Meaning |
|---|---|---|
| `GLDB_LLM_BACKEND` | `auto` | `auto` selects this backend when the key is set; `anthropic` forces it; `openai` or `fake` override it |
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

Haiku 5.5 accepts images, so `GLDB_LLM_SUPPORTS_VISION` is true for this backend and scans are read by the model (`ocr:page`, and vision extraction), unless `GLDB_OCR_BACKEND` names an on-device OCR (`tesseract`, `rapidocr` or `vision`, section 5a). An image is sent as `{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ...}}`, grayscale at `GLDB_VISION_DPI`, before the text block. Sending images costs input tokens (about 3,600 per page at the default grayscale render in the live check, section 11).

**The page image is sent for extraction whenever the model has vision, even when local OCR words exist.** For a scan, `extract/llm_extractor.py` builds the extraction request from the image unless the model is text-only (`GLDB_LLM_SUPPORTS_VISION=false`); the local OCR words are then used only to verify the model's values. With the default `GLDB_OCR_BACKEND=auto`, vision is forced on under this backend. Naming an on-device OCR changes that, see section 5a.

### 5a. On-device OCR, Haiku on text

Set `GLDB_OCR_BACKEND=tesseract` (needs `brew install tesseract`; nothing to add to `uv sync`), `rapidocr` or `vision` (Apple Vision, `uv sync --extra ocr`) and the page is read on the machine. Haiku then receives the OCR text with the table columns kept in place (`core/textlayout.py:aligned_text`) and no image, which costs a fraction of the vision path (section 5b). One exception, `GLDB_LLM_VISION_FALLBACK` (on by default in this setup): a scanned page whose arithmetic does not add up (a running balance or a total) is read a second time from its image, and on a difference that image reading wins and the OCR-text reading is kept in `alt_raw`. Set it to `false` to keep every image on the machine; pages the OCR misread then stay flagged for a person.

### 5b. Measured

Same 13 scanned cases for each reader (clean AML and KYC bundles plus 11 tampered ones, 8 tamper types, `scan_hq`, one pass, `backend/scripts/ext_eval/real_ocr_eval.py` with the OCR job swapped for the real pipeline). Spend is read from the case costs.

| Seed | Reader | Tamper recall | Clean bundles flagged (medium or above) | Statement rows read exactly | Spend, 13 cases | Seconds a case |
|---|---|---|---|---|---|---|
| held-out 9001 | Tesseract, Haiku on the text | 11 of 11 | 1 of 2 (`bs_holder`, `x_entity`, `x_ownership_vs_registry`) | 1.000 | $0.337 | 115 |
| held-out 9001 | Haiku on the page image | 11 of 11 | 1 of 2 (`passport_mrz`) | 1.000 | $0.872 | 170 |
| dev 4002 | Tesseract, Haiku on the text | 11 of 11 | 0 of 2 | 1.000 | $0.368 | 127 |
| dev 4002 | Haiku on the page image | 10 of 11 | 1 of 2 (`passport_mrz`) | 0.99 (598 of 604, 3 bundles) | $0.875 | 100 to 225 |
| dev 4002 | Apple Vision, Haiku on the text (no cell split, no column alignment) | 3 of 11 | 2 of 2 | 0.72 | $0.261 | 99 |
| dev 4002 | RapidOCR, Haiku on the text | 0 of 11 | 2 of 2 | 0.01 | $0.031 | 32 |

How to read it. The Tesseract settings and its three text repairs (a stray comma after an amount, CR/DR glued to the amount, `S` read as `$` or `§` in an ID number) were tuned on the dev-seed bundles, so the dev rows flatter it; the held-out rows were run once after the code was fixed and nothing was changed afterwards. The held-out Tesseract false alarm is a name-matching one on a clean bundle, from words read wrongly out of the text, and it is left as it is. Samples are 11 tampered cases and 2 clean bundles per row, so every figure has a wide interval (11 of 11 is 0.74 to 1.00). Recall counts a new flag against the same scan of the clean bundle. The loan scenario was not run. Apple Vision and RapidOCR failed for causes that were fixed in the Tesseract path but not re-run for them: Apple Vision's lines were not split into cells and its text had no alignment; RapidOCR drops the spaces between words.

What made Tesseract work, in order of effect: one box per table cell instead of per line (the pipeline spreads a box's words evenly, which erased the debit and credit columns), keeping column positions in the text sent to Haiku, reading the page at about 1.7 times the scan resolution, and the vision fallback for the pages whose arithmetic still failed.

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

Page text, and for scans the page image (also when local OCR is on, section 5), are sent to Anthropic, a third party outside the bank's control (location, retention and training terms `[confirm]`). **Use fictitious documents only. Never upload real customer data** while this backend is on. Pseudonymisation of outbound text is not implemented (one page at a time, no other document, no tools is the only minimisation), see [ai-inventory.md](ai-inventory.md) section 2. The key stays in `.env` / `deploy/.env.production`; error messages, logs and reprs contain ids, counts and dollars, never the key (a test formats the settings, the client and a raised API error and asserts the key text is absent, even when the server echoes it back).

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
| Extraction, scans and photos | yes | yes, one page, also when `GLDB_OCR_BACKEND=vision` supplies OCR words (section 5) |
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

`backend/scripts/llm_eval.py` is built around the local text-only server (it pre-flights `/health` and `/v1/models` and forces `supports_vision=false`), so it was **not** retargeted at this backend; a Haiku evaluation would need that script generalised and the owner's sign-off on its spend. The only measurement of this backend so far is the sanity check below.

### 11.1 Live sanity check (measured)

One billed run through the real pipeline (`scripts.run_case.analyse_files`, the same path as `POST /cases`), response cache off so every call was billed and measured, per-case cap $0.20 and daily cap $0.45 on a temporary database. The run script was a one-off and is not in the repo; the write-up is local (`docs/eval-results/` is gitignored). Fictitious data from our own generator: bundle `loan-clean`, the `photo` render (one JPEG per page with perspective, shadow and noise), three single-page documents (company profile, invoice, national ID), each read in two modes, so 6 page-reads.

| Mode | OCR | Model calls per page | Tokens in / out (3 pages) | Dollars (3 pages) | Dollars per page |
|---|---|---|---|---|---|
| Haiku OCR | `GLDB_OCR_BACKEND=auto`: Haiku transcribes (`ocr:page`), then extracts | 2 | 26,057 / 6,119 | 0.005666 | about $0.0019 |
| Apple Vision OCR | `GLDB_OCR_BACKEND=vision`: local OCR words, Haiku extracts | 1 | 15,227 / 1,290 | 0.002168 | about $0.0007 |

* **Calls and spend:** 9 live calls in the two modes plus one smoke call (451 in, 75 out, $0.000083); total about **$0.0079**. Dollars are computed from the repo price table (section 6), not from an Anthropic invoice; the Console was not checked.
* **Field accuracy** against the generator's ground truth, using the repo's own field scorer: **33 of 35 fields (94.3%)** in both modes (company profile 10/11, invoice 17/17, national ID 6/7; Wilson 95% interval about 81% to 98%). All 3 pages were classified correctly and none was unreadable. Table rows were all recalled (directors 2/2, shareholders 2/2, invoice lines 3/3).
* **The two misses are scoring artefacts, not misreads:** the company profile prints "S$" but no currency code, so the model returned null where the truth is `SGD`; the ID card prints `SINGAPORE CITIZEN` where the truth is `Singaporean`. The scoring was not loosened after seeing this (it would read 35/35).
* **Per call:** mean 4,587 tokens in and 823 out, about $0.00087; about 4 s per call and 9 s per page in the two-call mode. Output tokens are uneven (transcription 820 to 2,181, extraction 203 to 548), so a dense page, such as a statement page with many rows, will cost more than these three; none was measured.
* **Finding:** the extraction requests were identical in both modes (same token counts), because the image is sent whenever the model has vision (section 5). Local OCR therefore saved the transcription call (about 62% of per-page cost here) and not the image, with the same accuracy on this sample.

Limits, stated plainly: n = 3 pages and 35 fields, one run per mode (no run-to-run variance), clean generator output with a mild photo render. No bank statements, multi-page documents, heavy degradation, handwriting, stamps or **tampered documents** were sent, so nothing was learned about accuracy on real scans or about tamper detection (a model that reads an altered page faithfully passes the alteration on to the checks and adds no forensic power of its own). This is a sanity check that the live path works and a first measurement of cost, **not an evaluation**. Suggested follow-ups, each needing the owner's sign-off on spend: a 30 to 60 page run across statements, invoices and IDs on the simulated scan renders; one dense statement page and a two-page PDF; and measuring the accuracy cost of not sending the image when good local OCR exists.

## 12. Honest limits

* Live evidence is thin: the request fields were taken from Anthropic's documentation and tested against a mock transport, and the only live run is the 9-call sanity check in section 11.1 (3 clean fictitious pages). Accuracy on real scans, on long statements and on tampered documents is unmeasured, and the dollar figures come from the repo price table, not an invoice.
* Cache prices and the exact workspace-id location in the Console are unconfirmed `[confirm]`.
* The image is always sent for extraction under this backend (section 5), so local OCR is a cost lever only, not a privacy lever.
* The daily cap is soft (section 7) and counts only what this application records; the Anthropic Console limit is the hard stop.
* Refusals are false positives for benign documents sometimes; the tool shows the page as unread rather than guessing.
