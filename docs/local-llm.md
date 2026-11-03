# Running against the local LLM server

How to point the tool at the owner's own machine (a Mac running Qwen3.8-27B) instead of a hosted model, what changes, and what it can and cannot do. Companion to [ingest-and-extraction.md](ingest-and-extraction.md) (the stages), [cost-efficiency.md](cost-efficiency.md) (budget guard) and the server's own README (`yekaiwu/local-llm-server`). All data in this repo is fictitious.

Status, stated plainly: this page was first written and tested **offline** (fake server over `httpx.MockTransport`, a stub SDK, a fake OCR backend, and a real `ocrmac` run on a generated page). Since then the model has been run live; section 11 records what was checked on 2 November 2026 (where the weights came from, what the servers log, and live runs of the reading path), and **section 12 (3 November 2026) records why that first scan run was slow and wrong, what was changed, eight scanned bundles measured one at a time, the comparison with Claude Haiku, and the private mode**.

## 1. What the server is (from its README)

| Fact | Consequence in this tool |
|---|---|
| OpenAI-compatible at `http://llm-host:8080/v1`, reachable only over Tailscale | `GLDB_LLM_BASE_URL`; the machine running the app must be on the same tailnet |
| `Authorization: Bearer <key>`; keys come from the Telegram bot (`/newkey [name] [days]`) | `GLDB_LLM_API_KEY`; keys expire (default 30 days) |
| Model name `qwen3.8-27b` (`Qwen3.8-27B-UD-Q5_K_M.gguf` from Unsloth, a mixed-precision "dynamic" quantisation, on llama-server, `--jinja`; section 11) | `GLDB_LLM_MODEL_CHEAP` = `GLDB_LLM_MODEL_STRONG` = `qwen3.8-27b` |
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
   GLDB_LLM_ENABLE_THINKING=false      # also sends reasoning_effort=none (LM Studio ignores the template switch; section 12.1)
   GLDB_LLM_SUPPORTS_VISION=false
   GLDB_LLM_MAX_CONCURRENCY=1
   GLDB_LLM_MAX_OUTPUT_TOKENS=8000
   GLDB_LLM_RETRY_503_ATTEMPTS=8
   GLDB_LLM_RETRY_503_BACKOFF_S=5
   GLDB_OCR_BACKEND=tesseract
   GLDB_SCAN_RULES_FIRST=true          # scans: deterministic parser first, model only when it cannot read the page (12.2)
   GLDB_LLM_COMPACT_TABLES=true        # tables as lists of rows: fewer output tokens (12.2)
   ```

   `.env` is gitignored. The key is a `SecretStr`, never logged, and never printed by the scripts.
5. **Local OCR (for scans).** `uv sync --extra ocr` installs `ocrmac` (macOS only, optional; the base install does not need it). Without it, scanned pages are reported as not read (section 4).
6. **Smoke test, then evaluation** (section 7), then start the app as usual.

If the server is stopped, every script and the client itself say what to do: "send /start to the Telegram bot, then /newkey gldb 30, put the key in .env as GLDB_LLM_API_KEY".

## 3. Settings and exactly what they do

All defaults are the previous hosted behaviour; nothing changes unless you set them.

| Variable | Default | Behaviour |
|---|---|---|
| `GLDB_LLM_ENABLE_THINKING` | `true` | `false` sends `chat_template_kwargs: {"enable_thinking": false}` in the request body (the OpenAI SDK's `extra_body`, merged at the top level, which is where llama-server reads it) **and, unless `GLDB_LLM_REASONING_EFFORT` is set, `reasoning_effort: "none"`, because LM Studio ignores the template switch and kept thinking (section 12.1)**. `true` sends nothing, so the server's default (thinking on) applies. `reasoning_content` is never read or stored: only the answer counts. The response cache key differs between the two modes. |
| `GLDB_LLM_MAX_OUTPUT_TOKENS` | `8000` | `max_tokens` per call. Thinking tokens count against it: with thinking on use `16000` or more, otherwise a long think leaves no room for the JSON and the call fails with "output cut off at max_tokens ... raise GLDB_LLM_MAX_OUTPUT_TOKENS or set GLDB_LLM_ENABLE_THINKING=false". `llm_eval.py` raises it to 16000 itself when you pass `--thinking on`. |
| `GLDB_SCAN_RULES_FIRST` | `false` | `true`: a scanned document is first read by the deterministic parser of its type from the OCR words; it is accepted only when the layout is recognised, every validator check holds and no required field is missing, else the model reads it (`scan_rules:` / `scan_rules_declined:` warnings say which). On a scan an untagged ownership-chart box stays unknown instead of "not a UBO". |
| `GLDB_LLM_COMPACT_TABLES` | `false` | `true`: the model returns each table as a list of rows (a list of strings or nulls in a stated column order), not an object per row; the answer is expanded to the same row objects before validation. About a fifth fewer output tokens on a statement page. The financial statement keeps objects (its `line` and `section` columns are enums). |
| `GLDB_LLM_SUPPORTS_VISION` | `true` | `false`: **no image part is ever built**. Enforced in three places: the OCR stage (uses local OCR instead of the model), `LLMExtractor` (reads a scan from its OCR text; a scan with no OCR text is unreadable, nothing is rendered or sent), and the client itself, which raises `LLMVisionUnsupported` if a request with images ever reaches it. |
| `GLDB_LLM_MAX_CONCURRENCY` | `4` | A process-wide semaphore per server URL, shared by every thread and client object. With `1` the page workers queue in the app instead of all waiting inside the server's queue until their HTTP timeout fires. |
| `GLDB_LLM_TIMEOUT_S` | `60` | Per request. Use `300` locally: a long statement page plus generation can take minutes, and a client that gives up does not cancel the server's generation (it finishes first), so a timeout makes the next request wait longer, not shorter. |
| `GLDB_LLM_RETRY_503_ATTEMPTS` | `2` | Retries of a 503. The local profile uses `8`. |
| `GLDB_LLM_RETRY_503_BACKOFF_S` / `_MAX_WAIT_S` | `1` / `60` | Wait before retry *n* is `Retry-After` when the server sends it, else `backoff * 3**n` (1, 3, 9 ... with the default, 5, 15, 45 ... in the local profile), never more than `MAX_WAIT_S`. |
| `GLDB_OCR_BACKEND` | `auto` | `auto`, `vision`, `tesseract` or `none`; see section 4. |

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
| `true` | `tesseract` | the `tesseract` program (forces local OCR; `brew install tesseract`, no Python package; not measured against Haiku or Apple Vision yet) |
| `false` | `tesseract` | the `tesseract` program; if it is not installed, "OCR unavailable" with the install hint |
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

Page text (and, with `GLDB_CLASSIFIER_MODE` `llm`/`cascade`, minimised text) is sent over the tailnet to the owner's own machine, over plain HTTP inside Tailscale's WireGuard tunnel. It is never sent to a third party: no hosted model sees it, and the OCR runs on this machine with Apple's on-device Vision framework. Things to know, from the server's README: the gateway sends the owner a Telegram alert naming the calling device when a call arrives while the service is stopped or with an expired key (at most one per device every five minutes), so even the health probes of the scripts can produce one; the raw key travels through Telegram once, so delete that message after copying it; and packets for the Tailscale IP may reach the Mac over the LAN, where they still need a valid key. What the servers log about prompt content was checked on 2 November 2026 and differs by server: the gateway and llama-server keep no prompt text, but LM Studio's own server writes every request and response in full (section 11.2).

## 10. Honest limits

* **Verified live only in part.** The API shape (extra body, tool-call parsing, 503 and `Retry-After`) was first tested against faithful fakes. It has since been exercised live through LM Studio's server (section 11.3), but not through the gateway and llama-server pair with its API key. Run the smoke test against that pair before relying on it.
* **27B is not a frontier model.** Whether it copies numbers exactly across a 22-row page, and whether it resists injection, is exactly what `llm_eval.py` measures; do not assume. Thinking on may help accuracy and certainly costs time.
* **Scans depend on Apple Vision.** Not measured here on the `scan_lq` and `photo` renders (the generated scans carry noise and perspective). Vision gives line boxes, so word-level highlights on a scan are approximate; digits misread by OCR are caught by verification only where the model disagrees with them.
* **The OCR is macOS-only** (and needs the optional extra). On Linux or in CI there is no local OCR: scans become "not read" pages, flagged and uncounted in coverage, never faked.
* **Single machine, single request.** If the Mac sleeps, is rebooted without login (LaunchAgents start at login), or runs a GPU job (the model unloads after 30 minutes idle), the first request can be slow while the model loads.
* **The key expires** (30 days by default) and a 401 is not retried; `/extend gldb 30` in Telegram prolongs it.
* **Scores from the model are not calibrated probabilities** (as with any hosted model, see classification.md); they are stored under the classifier name `llm:qwen3.8-27b`.

## 11. Verification record, 2 November 2026

What was checked on the owner's Mac, by whom and how. Everything below used fictitious documents.

### 11.1 Where the model weights came from

| Question | Answer |
|---|---|
| Which file runs? | `~/.lmstudio/models/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q5_K_M.gguf` (19,771,509,664 bytes, downloaded 21 October 2026), used by LM Studio and by `start-llama.sh` |
| Who made it? | Unsloth (`unsloth/Qwen3.8-27B-GGUF` on Hugging Face), a third-party quantiser of the official `Qwen/Qwen3.8-27B`. Qwen publishes the BF16 safetensors only; there is no official GGUF, so the file cannot be matched to the release by hash |
| Licence | Apache 2.0, on the official model card, on the Unsloth card and in the GGUF header |
| Is the download intact? | The SHA-256 of the local file, `2de73110cb254cbf09b54b717578dadff12ef1194e7271527e68202f39ba4bfd`, equals the hash Hugging Face publishes for that file, and the size matches |
| Is it the same model as the official release? | The header matches the official `config.json` (64 layers plus 1 prediction layer, hidden size 5120, feed-forward 17408, 24 attention and 4 key-value heads, head size 256, vocabulary 248,320, context 262,144, full attention every 4th layer). Sampled weights match too: rows of 8 tensors (embeddings, output head, feed-forward and attention-output matrices in layers 0, 3, 40 and 63) were fetched from the official shards by HTTP range request and compared with the dequantised GGUF. Cosine similarity was 0.996 to 0.9998, with relative error about 2% (Q6_K), 4% (Q5_K) and 7 to 9% (Q4_K), as those quantisations produce. A deliberate mismatch (a different layer's tensor) gave cosine about 0 and error about 140%, so the test can tell. |
| What this cannot show | It samples 8 of 866 tensors. A change confined to other tensors would not be seen. Full equivalence would need a re-quantisation from the official weights with the same recipe, which cannot be reproduced because the importance-matrix file (`imatrix_unsloth.gguf`) is Unsloth's and its calibration text is not inspected here |
| Is it "Q5_K_M"? | Only in the file-type field. The tensors are a mix (Q4_K, IQ4_XS, Q5_K, Q6_K, Q8_0 and F32), Unsloth's "UD" recipe, not the standard llama.cpp Q5_K_M recipe. Quote it as "UD-Q5_K_M" |
| The chat template | Not byte-identical to the official `chat_template.jinja` (9,993 against 8,952 characters). The whole difference was read: it is Unsloth's declared "developer role, merged system messages, tool calling" fixes (system and developer messages merged, stricter checks on tool-call arguments, the effort level "high" mapped to "xhigh"). No added instruction text and no URL. The template is code that shapes every prompt, so pin it with the weights |
| Vision | The official model has a vision tower and Unsloth publishes `mmproj-BF16.gguf` and `mmproj-F16.gguf`; the GGUF that runs here is text-only, so no page image is ever sent to it |

Re-run the check after any re-download: `shasum -a 256` of the file against the hash above, and the Hugging Face file listing for the repository.

### 11.2 What the servers log about prompts

| Server | What is written | Where | Note |
|---|---|---|---|
| Gateway, port 8080 (`gateway.py`) | Nothing: its access log method is empty | none | Bound to loopback and the tailnet address; calls need an API key |
| llama-server, port 8081 (behind the gateway) | Start-up messages and per-call timings and token counts. No prompt or response text (no match for document words, `messages` or `content` in the log) | `local-llm/server/logs/llama-server.log`, mode 600 | |
| LM Studio's server, port 1234 | **Every request body (system prompt and document text) and every generated response, in full**, at DEBUG level | `~/.lmstudio/server-logs/YYYY-MM/`, about 34 MB since 21 October, none deleted | Files are mode 644 inside a home folder readable by the group `staff`. All data so far was fictitious |

Consequence: use the gateway and llama-server pair for anything that is not fictitious, or turn request logging off in LM Studio and delete its server logs first. Do not point the application at port 1234 with real customer data.

### 11.3 Live runs of the reading path

Settings for both runs: Tesseract reads the page images on the machine, `qwen3.8-27b` turns the text into fields through LM Studio's OpenAI-compatible server, thinking off, one request at a time, the model-based checks off, no cache. Cost $0.

| Run | Result |
|---|---|
| 20 invoice images from a public set made by someone else (single pages) | About 46 seconds an image. Document type the same as with Claude Haiku on 20 of 20; the same medium or high flag outcome on 20 of 20; invoice number read correctly on 4 of the 8 read as invoices, as with Haiku |
| One scanned KYC bundle (`kyc-clean`, 17 pages, high-quality scans, seed 9201) | 22 minutes 25 seconds in all, of which extraction 22 minutes; 18 model calls, 45,081 prompt tokens and 59,346 completion tokens (about 45 tokens a second). Page types 17 of 17. Table cells 13 of 13. Fields 60 of 84 (71%). The case was marked partial and raised two high `x_ownership_vs_registry` flags on a clean bundle: false alarms from misread ownership data |

So the reading path works end to end and costs nothing, but on this bundle it is slow and clearly less accurate than the hosted model, and it is one bundle. The earlier claim in section 6 that the text path is adequate rests on digital pages only. Section 12 explains the 22 minutes (the model was still thinking) and re-measures the path after the fix. Run `llm_eval.py` and a larger local sweep before treating the local model as a replacement for Haiku on scans.

## 12. Making the local path measured and enforceable, 3 November 2026

Everything here used fictitious bundles (generator seed 9201, `scan_hq` renders: page images at the quality of a good office scanner), Tesseract for OCR, `qwen3.8-27b` through LM Studio at 127.0.0.1:1234, one request at a time, no cache, no semantic or agent calls, `backend/scripts/local_scan_bench.py` (field by field against the generator's ground truth; it writes every prompt and answer next to the score). One Claude Haiku 5.5 run per bundle on three bundles gave the comparison; they cost USD 0.046 in all.

### 12.1 Why the first scan run was slow and wrong (60 of 84 fields)

| Cause | Evidence | Share of the damage |
|---|---|---|
| **The model kept thinking.** LM Studio ignores `chat_template_kwargs.enable_thinking=false` (section 3), so every call produced thousands of hidden reasoning tokens. A test call with the template switch only returned 63 reasoning tokens for `17*23`; with `reasoning_effort: "none"` it returned none | 59,346 completion tokens for 18 calls on `kyc-clean`; about 88 seconds a page | **All of the time**, and the accuracy loss below |
| The reasoning used up `max_tokens` (8000) on the first page of each bank statement; the call was cut off twice and the page became "unreadable" | three statement pages unreadable: 17 of the 24 missing fields (opening and closing balance, period, UEN, currency of each statement); on `kyc-clean-c01` the same run read 77 of 121 statement rows | 17 fields |
| The passport `holder_name` was read as `VASQUEZ ELENA` (the order printed on the page) where the checked value is `Elena Vasquez`; in the first run the page also ran out of budget | 1 field | 1 field |
| Not model errors, shared with Haiku: `nationality` is printed `SINGAPORE CITIZEN` where the generator's value is `Singaporean` (2 fields), `sex` is not recognised by Tesseract on one card (1), and `page_x_of_y` is a per-document value that a scanned statement, one document per page, cannot hold in full (3) | the same six misses in the Haiku run (which also missed one currency) | 6 fields, the ceiling for this OCR |

So the 71 percent was mostly a configuration fault, not a limit of the model. With thinking really off the same bundle gives **77 of 84 in 289 seconds**, equal to Haiku's 77.

### 12.2 What changed

| Change | Setting | Effect measured on `kyc-clean` (17 pages) |
|---|---|---|
| `reasoning_effort: "none"` is sent whenever `GLDB_LLM_ENABLE_THINKING=false` (an explicit `GLDB_LLM_REASONING_EFFORT` still wins) | automatic | 1,500 s to 289 s; 59,346 to 11,088 completion tokens; 60 to 77 fields; no page cut off |
| A scan is read by the deterministic parser first (the KYC, certificate, register, chart and statement parsers already read OCR words) and goes to the model only when the layout is not recognised, a validator check fails or a required field is missing. On a scan the parser leaves an untagged ownership-chart box unknown instead of deriving "not a UBO", because the OCR misses the small boxed UBO badge | `GLDB_SCAN_RULES_FIRST=true` | 14 model calls to 6 (only the statement pages); 289 s to 182 s; 78 of 84 fields; statement rows 128 of 128; the three false `kyc_shareholdings` highs this setting first caused are gone after the UBO change |
| Tables returned as lists of rows, without a key name per cell | `GLDB_LLM_COMPACT_TABLES=true` | longest statement page 1,628 to 1,298 output tokens; 182 s to 133-140 s |
| Passport hint: `holder_name` is given names then surname | prompt | 1 field |

Not done, with reasons: merging small pages (a continuation page costs 5 seconds, output dominates and merging would put two pages' rows in one answer), and speeding generation with the server's draft-model (MTP) option, which gave 35.8 s against 28.1 s on the same statement page, so no gain. The hosted path is unchanged unless these settings are turned on (`reasoning_effort` is only sent when thinking is switched off, which a hosted setup does not do); the settings hash and provenance move only when they are on.

### 12.3 Per bundle (final configuration)

Seconds are wall time for the whole case (OCR 1.1 s a page, extraction about 6 s a page on average, the rest checks and forensics). Calls and tokens are model calls only. "Fields" and "cells" are the generator's checked fields and table keys; "rows" are statement rows aligned to the truth; "flags" are medium or high flags on a bundle that should have none ("false alarms"). Every case was marked partial ("some documents were only partly extracted": the continuation pages of a scanned statement are documents of their own; Haiku's cases are partial for the same reason).

| Bundle | Pages | Seconds | s/page | Calls | Prompt / completion tokens | Fields | Cells | Page types | Statement rows | False alarms |
|---|---|---|---|---|---|---|---|---|---|---|
| kyc-clean | 17 | 140 | 8.2 | 6 | 18,084 / 6,041 | 78/84 | 13/13 | 17/17 | 128/128 | 0 |
| kyc-clean-c01 | 17 | 132 | 7.8 | 6 | 17,645 / 5,669 | 77/84 | 9/12 | 17/17 | 121/121 | 3 (OCR reads `Iyer` as `lyer`: `x_directors`, `x_ownership_vs_registry` x2) |
| kyc-clean-c02 | 13 | 71 | 5.5 | 3 | 8,135 / 2,837 | 84/90 | 12/12 | 13/13 | 118/118 | 0 |
| kyc-clean-c03 | 14 | 84 | 6.0 | 5 | 11,931 / 3,353 | 75/81 | 12/12 | 14/14 | 122/122 | 2 (`x_ownership_vs_registry`: an ID read as `92`) |
| aml-clean | 30 | 228 | 7.6 | 14 | 33,743 / 9,196 | 271/282 | 36/36 | 29/30 | 247/247 | 0 |
| aml-clean-c02 | 30 | 191 | 6.4 | 11 | 26,896 / 7,677 | 269/281 | 40/40 | 29/30 | 243/243 | 0 |
| aml-clean-c01 | 35 | 277 | 7.9 | 13 | 36,414 / 11,382 | 256/268 | 37/37 | 34/35 | 239/240 | 9 (UENs misread by the OCR, `2018703596` for `201870359E`: `x_entity`, `x_invoice_vs_contract` x6; one date read `27/07/2026 =.`: `bs_dates`) |
| loan-clean | 49 | 353 | 7.2 | 26 | 60,005 / 15,316 | 390/417 | 133/133 | 47/49 | 260/260 | 6 (UEN `202067664Z` read `2020676642`, a subtotal line lost, an NRIC read `S****751I` for `S****875I`) |

All eight finished, one at a time, in about 27 minutes of model time (total 1,476 s for 205 pages, 7.2 s a page). Averages per call: bank statement page 18 s (about 940 output tokens, 51 tokens a second), invoice 8.7 s, financial statement 15 s, contract 4 s. Every remaining miss or false alarm that was inspected traced to the OCR text (a digit, a letter case, a boxed badge, a missed word), not to the model: the model copied what it was given.

Before and after on a second bundle, `kyc-clean-c01`, same machine: thinking on (the old behaviour) **71 of 84 fields, 1,556 s, 20 calls, 69,057 completion tokens, statement rows 76 of 121 (two statement pages cut off), five high flags including two bank-statement arithmetic flags that were extraction damage**; final configuration **77 of 84, 132 s, 6 calls, 5,669 completion tokens, rows 121 of 121, three high flags (OCR)**. On `kyc-clean`: 60/84 in 1,500 s before, 78/84 in 140 s after. Rules first against the model alone on `loan-clean` (both with compact tables): 353 s, 26 calls, 6 false alarms, statement rows 260/260 against 609 s, 47 calls, 10 false alarms, rows 253/260, so the parser first is faster and no less accurate there.

### 12.4 Against Claude Haiku 5.5 (same OCR text, same bundles)

| Bundle | Haiku fields | Local fields | Haiku seconds | Local seconds | Haiku cost | Haiku false alarms | Local false alarms |
|---|---|---|---|---|---|---|---|
| kyc-clean | 77/84 | 78/84 | 67 | 140 | USD 0.0107 | 2 (`x_ownership_vs_registry`) | 0 |
| kyc-clean-c01 | 76/84 | 77/84 | 67 | 132 | USD 0.0110 | 3 | 3 (same) |
| aml-clean | 269/282 | 271/282 | 136 | 228 | USD 0.0242 | 0 | 0 |

Haiku's runs used no rules first and normal output; its seconds come from four parallel page workers. A fourth comparison on `loan-clean` was not run (the paid-spend limit for this work). On these three bundles both models read the same OCR text to the same answers within one or two fields, so **the OCR, not the model, sets the ceiling**; the local model is about twice as slow and free. The difference in false alarms on `kyc-clean` comes from the parser-first path, which repairs an ID's shape and leaves the unread UBO badge unknown; it is not evidence that the 27B model reads better.

### 12.5 Private mode

`GLDB_PRIVATE_MODE=on` (default `off`; `GLDB_PRIVATE_ALLOWED_HOSTS` lists extra hosts). Three layers, in `backend/app/core/privacy.py`:

1. **Start-up refuses** (`Settings.validate_startup`, so `main.create_app` and every script): the anthropic backend or any anthropic key, even with another backend selected; an OpenAI-compatible base URL whose host is not loopback, private (RFC 1918, link-local, unique-local), tailnet (100.64.0.0/10), a single-label name, `localhost`, `.local`, `.ts.net`, `.internal` or listed (an OpenRouter-style URL is refused by name in the message); `GLDB_REGISTRY_LIVE=true`; a Telegram alert sink; an alert webhook or SMTP host outside the private network. Names are judged by their text; the application does no DNS lookup to decide.
2. **Call sites** check again: the OpenAI-compatible client refuses a hosted URL, the anthropic client is never built, the live ACRA lookup raises "private mode: outbound lookups are disabled" (a cached answer is still used) and `attach_live_entity` returns before building a client, `build_sinks` drops a sink that points outside.
3. **A socket guard** installed by `create_app`: every connection the process opens and every DNS lookup of a name that is not allowed raises `PrivateModeViolation`, whatever library asks (httpx, the OpenAI and anthropic SDKs, smtplib, urllib). An allowed name's resolved addresses are accepted for the connection that follows. Unix sockets and listening are untouched. The code was searched for network use: the only outbound paths are the model clients, the ACRA lookup and the three alert sinks (webhook, SMTP, Telegram); the frontend loads nothing from another host and the local classifier uses `local_files_only`.

Reporting: `GET /monitoring` carries `private_mode` (`on` or `off`) and, when on, `llm_backend`, `llm_host`, `allowed_hosts`, `outbound_lookups: "disabled"` and `network_guard`; every case analysed while it is on has `provenance.private_mode = "on"` (absent when off, so older cases and their hashes are unchanged) and the setting is part of the settings hash. Tests: `backend/tests/test_private_mode.py` (59 tests: hosted configurations refused, no outbound call attempted with the HTTP layer and the sockets patched, a whole case analysed in private mode with no connection made, the guard's allow and deny rules).

**LM Studio logs prompts.** LM Studio's own server (port 1234) writes every request body, that is the page text, and every answer in full to `~/.lmstudio/server-logs/` (section 11.2). Private mode cannot see that and accepts `http://127.0.0.1:1234/v1` because it is loopback. This whole section's measurements used it **only because every document was fictitious**. For real customer data use the gateway and llama-server pair (section 11.2: no prompt text is kept), or turn LM Studio's request logging off and delete its server logs first. Private mode also does not cover: the host and its backups, the tailnet, the analyst's browser, and anything installed by hand.

### 12.6 What the local path is still weaker at

* **OCR is the ceiling.** Tesseract misses small boxed text (the UBO badges on an ownership chart), confuses `I`/`l`, `E`/`6`, `Z`/`2` in IDs and UENs, and loses single characters such as a card's sex letter. Both models copy what they are given, so these become false `x_entity`, `x_directors`, `x_invoice_vs_contract` and `x_ownership_vs_registry` flags on clean bundles (0 to 9 per bundle above). Haiku shows the same flags on the same text. They need a better OCR or a vision pass, not a better text model.
* **Slow.** About 7 seconds a page, 18 seconds for a statement page, a 49-page loan case in 6 minutes; one request at a time. Fine for a queue, not for a person waiting.
* **Only clean generated scans were measured**: `scan_hq` renders of the generator's documents, with the checks that are known to be quiet on clean bundles. Not measured: `scan_lq`, photos, a real bank's scans, tampered cases (recall), `llm_eval.py`'s canaries on the new settings. The fraud-detection side of the local path is therefore untested here.
* **Parser first reads what the parser understands.** A scan whose layout is new goes to the model, so there is no loss, but a parser that accepts a wrong reading of a known layout is not caught by the model. The validators and the verification against the OCR words are the protection.
* **Eight bundles, one seed.** Percentages above are counts on single bundles.

### 12.7 The model's own vision (mmproj), measured on three pages

Unsloth publishes `mmproj-F16.gguf` for `Qwen3.8-27B`. Provenance: downloaded from `https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/main/mmproj-F16.gguf` on 3 November 2026; 927,607,488 bytes; SHA-256 `cbb841a9ee0636b2ec172f5bb8df2ea8dfeb01e90fe7c6126581d662a0b4e43e`, equal to the value the Hugging Face file listing publishes (`lfs.oid`) and the size matches. It was fetched into an empty folder, verified there, and then copied to `~/.lmstudio/models/unsloth/Qwen3.8-27B-GGUF/mmproj-F16.gguf`, next to the weights, where LM Studio picks it up (`lms ls` then reports `vision: true`; the model loaded in 2.3 seconds, 20.70 GB with the projector). It is left in place; remove it to return to text only. The model was reloaded for the test with context 16,384 and one slot and afterwards with the previous settings (context 8,192, 4 slots).

| Page (`kyc-clean`, `scan_hq`, 150 dpi grayscale as the application renders it) | Seconds | Tokens (prompt / completion) | Result |
|---|---|---|---|
| Ownership chart, image only through the repo's own client and prompt | 13.7 | 3,468 / 353 | Reads the three UBO badges (`UBO`) and the NRIC `S2096803F` that Tesseract returned as `$§2096803F`; holder rows and the parent of the 100% holder correct. `is_ubo` comes back as the printed word `UBO`, which the parser's value (`true`) would need mapping |
| Bank statement page 1 (22 rows) | 28.1 | 3,931 / 1,597 | The 22 rows equal the OCR-text reading cell for cell and match the 22 truth balances |

So vision reads graphics and badly OCRed IDs that text cannot, and costs the same as the text route on a dense table, but this is two pages. It is not wired in: the application sends an image only on a vision backend or as the Haiku fallback; making the local model do that for failing pages (the vision fallback exists but is off for non-hosted models) is the natural next step and was not built or measured.
