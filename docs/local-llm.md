# Running against the local LLM server

How to point the tool at the owner's own machine (a Mac running Qwen3.8-27B) instead of a hosted model, what changes, and what it can and cannot do. Companion to [ingest-and-extraction.md](ingest-and-extraction.md) (the stages), [cost-efficiency.md](cost-efficiency.md) (budget guard) and the server's own README (`yekaiwu/local-llm-server`). All data in this repo is fictitious.

Status, stated plainly: everything here was built and tested **offline** (fake server over `httpx.MockTransport`, a stub SDK, a fake OCR backend, and a real `ocrmac` run on a generated page). The server was stopped while this was written and no API key was available, so **no live call has been made**. Section 7 is the checklist that turns "built" into "verified".

## 1. What the server is (from its README)

| Fact | Consequence in this tool |
|---|---|
| OpenAI-compatible at `http://llm-host:8080/v1`, reachable only over Tailscale | `GLDB_LLM_BASE_URL`; the machine running the app must be on the same tailnet |
| `Authorization: Bearer <key>`; keys come from the Telegram bot (`/newkey [name] [days]`) | `GLDB_LLM_API_KEY`; keys expire (default 30 days) |
| Model name `qwen3.8-27b` (Q5_K_M GGUF on llama-server, `--jinja`) | `GLDB_LLM_MODEL_CHEAP` = `GLDB_LLM_MODEL_STRONG` = `qwen3.8-27b` |
| **No vision projector**: text only | `GLDB_LLM_SUPPORTS_VISION=false`; scans are read by local OCR (section 4) |
| **One request at a time**, others queue | `GLDB_LLM_MAX_CONCURRENCY=1` (section 5) |
| Thinking is **on** by default for API calls (reasoning in `reasoning_content`) | `GLDB_LLM_ENABLE_THINKING` (section 3) |
| Tool calling works through the standard `tools` field | unchanged: structured output is one function tool (section 3) |
| About 500 tokens/s prompt processing; prefixes are cached | a page of text is a few seconds of prompt time; generation time is the larger part (measure it: section 7) |
| Model unloads after 30 minutes idle; the first request may get `503 Loading model` | retried, honouring `Retry-After` |
| When stopped, the gateway answers `503 {"error":{"message":"The local LLM server is stopped..."}}` with `Retry-After: 120` | fails fast with the Telegram instruction (section 3) |

## 2. Setup

1. **Tailscale.** The machine that runs the backend must be signed in to the same tailnet as `llm-host`. If MagicDNS is not available use `http://100.64.0.1:8080/v1`.
2. **Start the server.** In Telegram, open `@your_llm_gateway_bot` and send `/start` (about a minute if the model file is no longer cached). `/status` shows whether it is running. `/stop` frees the 27 GB again.
3. **Create a key.** Send `/newkey gldb 30` (name `gldb`, 30 days). The key is shown once; delete the Telegram message after copying it.
4. **Configure.** Copy the "local OpenAI-compatible server profile" block from `.env.example` into `.env` and paste the key:

   ```
   GLDB_LLM_BACKEND=openai
   GLDB_LLM_BASE_URL=http://llm-host:8080/v1
   GLDB_LLM_API_KEY=sk-llm-...
   GLDB_LLM_MODEL_CHEAP=qwen3.8-27b
   GLDB_LLM_MODEL_STRONG=qwen3.8-27b
   GLDB_LLM_TIMEOUT_S=300
   GLDB_LLM_ENABLE_THINKING=false
   GLDB_LLM_SUPPORTS_VISION=false
   GLDB_LLM_MAX_CONCURRENCY=1
   GLDB_LLM_MAX_OUTPUT_TOKENS=8000
   GLDB_LLM_RETRY_503_ATTEMPTS=8
   GLDB_LLM_RETRY_503_BACKOFF_S=5
   GLDB_OCR_BACKEND=auto
   ```

   `.env` is gitignored. The key is a `SecretStr`, never logged, and never printed by the scripts.
5. **Local OCR (for scans).** `uv sync --extra ocr` installs `ocrmac` (macOS only, optional; the base install does not need it). Without it, scanned pages are reported as not read (section 4).
6. **Smoke test, then evaluation** (section 7), then start the app as usual.

If the server is stopped, every script and the client itself say what to do: "send /start to the Telegram bot, then /newkey gldb 30, put the key in .env as GLDB_LLM_API_KEY".

## 3. Settings and exactly what they do

All defaults are the previous hosted behaviour; nothing changes unless you set them.

| Variable | Default | Behaviour |
|---|---|---|
| `GLDB_LLM_ENABLE_THINKING` | `true` | `false` sends `chat_template_kwargs: {"enable_thinking": false}` in the request body (the OpenAI SDK's `extra_body`, merged at the top level, which is where llama-server reads it). `true` sends nothing, so the server's default (thinking on) applies. `reasoning_content` is never read or stored: only the answer counts. The response cache key differs between the two modes. |
| `GLDB_LLM_MAX_OUTPUT_TOKENS` | `8000` | `max_tokens` per call. Thinking tokens count against it: with thinking on use `16000` or more, otherwise a long think leaves no room for the JSON and the call fails with "output cut off at max_tokens ... raise GLDB_LLM_MAX_OUTPUT_TOKENS or set GLDB_LLM_ENABLE_THINKING=false". `llm_eval.py` raises it to 16000 itself when you pass `--thinking on`. |
| `GLDB_LLM_SUPPORTS_VISION` | `true` | `false`: **no image part is ever built**. Enforced in three places: the OCR stage (uses local OCR instead of the model), `LLMExtractor` (reads a scan from its OCR text; a scan with no OCR text is unreadable, nothing is rendered or sent), and the client itself, which raises `LLMVisionUnsupported` if a request with images ever reaches it. |
| `GLDB_LLM_MAX_CONCURRENCY` | `4` | A process-wide semaphore per server URL, shared by every thread and client object. With `1` the page workers queue in the app instead of all waiting inside the server's queue until their HTTP timeout fires. |
| `GLDB_LLM_TIMEOUT_S` | `60` | Per request. Use `300` locally: a long statement page plus generation can take minutes, and a client that gives up does not cancel the server's generation (it finishes first), so a timeout makes the next request wait longer, not shorter. |
| `GLDB_LLM_RETRY_503_ATTEMPTS` | `2` | Retries of a 503. The local profile uses `8`. |
| `GLDB_LLM_RETRY_503_BACKOFF_S` / `_MAX_WAIT_S` | `1` / `60` | Wait before retry *n* is `Retry-After` when the server sends it, else `backoff * 3**n` (1, 3, 9 ... with the default, 5, 15, 45 ... in the local profile), never more than `MAX_WAIT_S`. |
| `GLDB_OCR_BACKEND` | `auto` | `auto`, `vision` or `none`; see section 4. |

**503 handling.** Two different 503s:

* *Loading* (the model was unloaded after 30 idle minutes): retried with the waits above. If it is still loading after the attempts, the error is `status 503 (server busy or still loading the model; gave up after N retries)`.
* *Stopped* (the body says "The local LLM server is stopped"): waiting does not start it, and `Retry-After: 120` would only hold a page worker for two minutes per attempt. The client re-checks **once** after the short backoff (in case `/start` was just sent), then raises `LLMServerStopped`, whose message is: "The local LLM server is stopped. Send /start to the Telegram bot (@your_llm_gateway_bot), wait about a minute, then retry." It is a subclass of `LLMTransportError`, so every existing handler (page marked unreadable, cascade warnings) keeps working and the message reaches the case's warnings.

**Structured output.** Unchanged: one function tool named after the schema, `tool_choice: "auto"` (a forced choice is rejected by some providers). Parsing was made more tolerant for llama-server: tool-call `arguments` may be a JSON string wrapped in a markdown fence or followed by prose; if the model answers in text instead of calling the tool, JSON is taken from the message content (including an unparsed `<tool_call>{"name": ..., "arguments": ...}</tool_call>` block, and ignoring `<think>` blocks); the first object that **validates against the schema** wins. Everything is still schema-validated and retried once on failure, exactly as before.

## 4. Which pipeline stages use the model, and where vision would be needed

| Stage | Uses the model? | Needs vision? | With the local profile |
|---|---|---|---|
| Ingest: file checks, page facts, text layer, PDF metadata | no | no | unchanged |
| Ingest: **OCR of scans and photos** | yes (hosted, vision) | **yes** | replaced by local OCR (below) |
| Classification C1-C5 | only if `GLDB_CLASSIFIER_MODE` is `llm` or `cascade` (default `rules`, no model) | no (page text) | text only; page text is minimised first (names and long digit runs masked) |
| Extraction, digital pages with a known layout | no (the text-layer parser) | no | unchanged, free |
| Extraction, digital pages the parser rejects, or arithmetic that fails | yes, a second reader | no | text only |
| Extraction, scans and photos | yes | **yes in the hosted setup** | OCR text goes to the model as text; **no image** |
| Row annotation (counterparty, C4, C5) | rules by default; hosted only in `llm`/`cascade` modes | no | text only |
| Checks, scoring, queue, review, case pack | no | no | unchanged |
| Image forensics (error level, copy-move, metadata) | no (OpenCV and rules) | no | unchanged; works on the pixels, not on text |

**So on this server scans rely entirely on the OCR path.** Selection (`ingest/ocr_backends.py`, `ingest/ocr.py`):

| `GLDB_LLM_SUPPORTS_VISION` | `GLDB_OCR_BACKEND` | Scan is read by |
|---|---|---|
| `true` | `auto` or `none` | the vision model (the previous behaviour; fakes and the eval oracle unchanged) |
| `true` | `vision` | macOS Vision (forces local OCR even though the model could do it) |
| `false` | `auto` | macOS Vision if `ocrmac` imports on darwin, else **no OCR** |
| `false` | `vision` | macOS Vision; if it cannot run, "OCR unavailable" with the reason |
| `false` | `none` | **no OCR** |

*macOS Vision* is Apple's text recogniser through the optional `ocrmac` package (`uv sync --extra ocr`, darwin-only marker, imported lazily; the base install never needs it). The page is rendered like for the model (grayscale, margins cropped, at least 200 dpi), recognised line by line, and the lines become `Word` objects with boxes as **fractions of the page** (Vision's bottom-left origin is flipped to the data-model's top-left; the margin crop is undone). Word boxes inside a line are split in proportion to character count, the same approach as the model OCR, so a highlighted value is the right line but its word box is approximate.

**No OCR available** is a value, not a crash: the page gets `ocr_unavailable: <reason>` (for example "the model is text-only and no local OCR is available (ocrmac is not installed (install the 'ocr' extra on macOS: uv sync --extra ocr))"), the case is marked partial, the `doc_unreadable` check raises an **info** flag on that page, and coverage drops, which the score already treats as "not checked", the same as any unreadable page.

Local OCR makes no model call, costs nothing and does not count against `GLDB_MAX_LLM_CALLS_PER_CASE`.

## 5. One request at a time

The server runs a single request; the rest queue. The tool's page workers (`GLDB_MAX_PAGE_WORKERS`, default 4) and job workers (`GLDB_JOB_WORKERS`, default 2) can therefore have several calls in flight. Without a cap they would all sit in the server's queue, and the later ones would hit `GLDB_LLM_TIMEOUT_S` while merely waiting. `GLDB_LLM_MAX_CONCURRENCY=1` moves the queue into the app: a worker holds the semaphore only for the duration of its own call, so the timeout measures the server's work, not the line. Throughput is the same (one call at a time either way); failures are fewer. Two jobs analysing cases at once simply interleave their calls.

Rough planning figure: seconds per case = (number of model calls) x (seconds per call). Calls per case: scans cost one extraction call per page (OCR is local); digital pages with a known layout cost none. Measure seconds per call with `llm_smoke.py` and `llm_eval.py` rather than trusting any number written here.

If the budget guard's call limit (`GLDB_MAX_LLM_CALLS_PER_CASE`, default 100) is too low for a scan-heavy case, raise it; there is no dollar reason to keep it low locally.

## 6. The cascade with one model

The extraction cascade is "cheap model, then strong model for pages that fail validation". Here cheap and strong are the same model, and the existing rule applies: when `GLDB_LLM_MODEL_CHEAP == GLDB_LLM_MODEL_STRONG` on the real backend, the **strong pass is skipped** (a second identical call at temperature 0 would return the same answer). So there is no re-extraction by a stronger reader. What the cascade still guarantees:

* every value is copied exactly as printed and **verified against an independent reading of the page** (the PDF text layer, or the OCR words for a scan); a value the reader cannot find is kept as read, flagged `verified=false`, never silently replaced;
* arithmetic is validated (balance chain, totals); a page whose numbers do not add up is reported as such by the checks with both readings where they exist, rather than "fixed";
* for digital pages the text-layer parse remains the source of truth and the model is only a second reader whose differing reading is stored beside the cell (`alt_raw`) and listed in a `reread:` warning;
* schema failures get one retry with feedback (`GLDB_LLM_SCHEMA_RETRIES`), then the page is `unreadable`;
* the model's output never lowers risk: classification is merged by a rule that can only raise it.

What it cannot do here: correct a systematic misreading by the model. If the 27B model misreads a scan the same way twice, the only protection is verification against the OCR words and the arithmetic checks.

## 7. Verifying it (run these once the server is started)

```
uv sync --extra ocr                                   # only needed for scans
uv run python backend/scripts/llm_smoke.py            # about a minute
uv run python backend/scripts/llm_eval.py             # N=40 pages; tens of minutes, see below
```

`llm_smoke.py` checks `GET /health` (no key), `GET /v1/models`, a schema-constrained call with thinking off, a thinking-on call, and a ~3 000-token prompt latency probe (run cold and warm to show the prefix cache), and reports seconds, tokens/s and any 503 retries. Exit code 0 = all passed, 1 = a check failed, 2 = stopped / unreachable / key missing or rejected (with the Telegram instruction).

`llm_eval.py` needs `samples/` (`uv run python backend/scripts/make_bundle.py --scenario loan --variant all --out samples`, then `--canaries`). It samples N digital pages (a quarter bank statements, a quarter invoices, the rest spread over the other page types; seeded) from the loan bundles and measures:

* **classification** C1 (page type), C4 (credit category), C5 (counterparty activity) through the hosted-fallback classifier, next to the rules baseline on the same items;
* **text-only extraction** of the statement and invoice pages: all five statement columns matched per row, whether the balance chain stays valid, and whether money is copied character-for-character as printed;
* **the injection canaries** (3 hosts x 4 placements): labels and extractions must not change, and the injected page must not be read as data;
* seconds per call (p50, p95), token counts and completion tokens/s.

It writes `docs/eval-results/llm-local.json` and prints a table and the verdict. **Verdict criteria** (the thresholds are this script's proposal, to be agreed with the owner; they are printed with every result):

| Criterion | Threshold |
|---|---|
| C1 page-type accuracy | >= 0.90 |
| C4 / C5 accuracy | >= 0.80 each |
| Statement rows matched exactly (date, description, debit, credit, balance) | >= 0.95 |
| Statement money cells copied character-exact | >= 0.99 |
| Statement pages whose balance chain stays valid | >= 0.95 |
| Invoice header fields correct; invoice line rows exact | >= 0.95 each |
| Injection canaries that change a label or an extraction | 0 |
| Calls that failed (timeouts, schema) | <= 5 % of calls, else the run is *inconclusive* |

All met = "ADEQUATE for the text path"; otherwise "NOT ADEQUATE" and the failing criteria are named; too many failed calls or an unmeasured criterion = "INCONCLUSIVE". The run uses digital pages, so it measures the model on clean text; **scans additionally depend on OCR quality, which it does not measure**. Options: `--n`, `--rows`, `--thinking on|off` (compare both), `--quick` (12 pages, no canary extraction), `--skip-canaries`, `--bundles`. A statement page is the slow call (a few thousand output tokens); the default run is dominated by about 20 extraction calls plus about 20 more for the canaries.

## 8. Cost and the budget guard

`qwen3.8-27b` is in the built-in price table at $0 input and $0 output, marked local, so costs read `0.000000` while **tokens and seconds are still recorded** per call (case view, `cost_summary`, logs). The case's `cost_summary` carries `"pricing_note": "qwen3.8-27b: local, $0 marginal cost"` whenever a local model was used (hosted output keeps its exact shape), and `llm_eval.py` prints the same label. $0 is the marginal cost only: it is the owner's machine, 27 GB of RAM, and electricity.

The dollar ceiling cannot be bypassed by a price that is missing. A model that is **not** in the table (typo, new model) still records `usd = 0` with the existing `price_unknown: <model>` warning, but the **budget guard** counts each of its calls at a deliberately pessimistic $15 per million input and $75 per million output tokens, so `GLDB_MAX_USD_PER_CASE` still stops a runaway. Only models listed with `"local": true` (the built-in one, or your own entry in `GLDB_PRICES_PATH`) are free.

## 9. Privacy

Page text (and, with `GLDB_CLASSIFIER_MODE` `llm`/`cascade`, minimised text) is sent over the tailnet to the owner's own machine, over plain HTTP inside Tailscale's WireGuard tunnel. It is never sent to a third party: no hosted model sees it, and the OCR runs on this machine with Apple's on-device Vision framework. Things to know, from the server's README: the gateway sends the owner a Telegram alert naming the calling device when a call arrives while the service is stopped or with an expired key (at most one per device every five minutes), so even the health probes of the scripts can produce one; the raw key travels through Telegram once, so delete that message after copying it; and packets for the Tailscale IP may reach the Mac over the LAN, where they still need a valid key. What the server itself logs or retains about prompt content is not documented in its README and has not been checked here.

## 10. Honest limits

* **Not verified live.** The API shape (extra body, tool-call parsing, 503 and `Retry-After`) is tested against faithful fakes and the real `openai` SDK over a mock transport, not against llama-server. Run the smoke test first; if the tool call shape differs, the text-fallback parser is the safety net.
* **27B is not a frontier model.** Whether it copies numbers exactly across a 22-row page, and whether it resists injection, is exactly what `llm_eval.py` measures; do not assume. Thinking on may help accuracy and certainly costs time.
* **Scans depend on Apple Vision.** Not measured here on the `scan_lq` and `photo` renders (the generated scans carry noise and perspective). Vision gives line boxes, so word-level highlights on a scan are approximate; digits misread by OCR are caught by verification only where the model disagrees with them.
* **The OCR is macOS-only** (and needs the optional extra). On Linux or in CI there is no local OCR: scans become "not read" pages, flagged and uncounted in coverage, never faked.
* **Single machine, single request.** If the Mac sleeps, is rebooted without login (LaunchAgents start at login), or runs a GPU job (the model unloads after 30 minutes idle), the first request can be slow while the model loads.
* **The key expires** (30 days by default) and a 401 is not retried; `/extend gldb 30` in Telegram prolongs it.
* **Scores from the model are not calibrated probabilities** (as with any hosted model, see classification.md); they are stored under the classifier name `llm:qwen3.8-27b`.
