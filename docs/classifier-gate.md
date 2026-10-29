# Classifier gate

Result and how-to for the eval gate in plan 5.3. Spec: [classification.md](classification.md) sections 4.3, 4.10, 4.11. Code: `backend/app/classify/local.py` (adapter), `backend/scripts/classifier_gate.py` (gate), tests `backend/tests/test_classifier_*.py`. Raw numbers: `docs/eval-results/classifier-gate.json`. All data is fictitious.

## 1. Outcome of the first run

**The local model was not run.** This build has no `torch`, no `transformers` and no weights, and nothing is downloaded (architecture.md section 7). The gate recorded the local classifier as `skipped: not installed` in every section, and the decision is:

```
adopt_local = false
reasons     = ["local classifier not available"]
recommendation = "keep rules plus hosted fallback"
```

The hosted fallback was also not run: there is no API key, so the client is the `FakeLLMClient` without fixtures and the gate reports `skipped: no API key`. Adopting nothing is a valid outcome (plan 5.3); running the local model on a team laptop with weights is task **H-13**. Until then no claim is made about the local or hosted models. The numbers below are for the **rules** classifier only (the `cascade` row equals rules because both of its model tiers were unavailable, and is marked `degraded` in the JSON).

| Item | Value |
|---|---|
| Machine | macOS arm64 (Apple silicon), Python 3.12.11 |
| Model name and revision | none run (`GLDB_LOCAL_CLASSIFIER_MODEL` is empty) |
| Licences read | **none**. The model repos' licences and the Qwen base-weight licence are **[confirm]**: they must be read by the person who runs H-13 before any weights are used |
| Gate set | `samples/gate/`, 80 pages: 40 digital, 40 scanned (20 `scan_hq`, 10 `scan_lq`, 10 `photo`); 28 bank-statement pages, 12 invoices, 6 each of financial statement, contract, purchase order, national id, boilerplate, other, 4 company profiles; 137 credit rows (C4), 514 rows (C5); C3 on 28 statement pages |
| Scan text | oracle OCR (printed lines from `ground_truth.json`) plus seeded noise, `--ocr-noise 0.02`, seed 4002, scaled x1 / x2 / x3 for `scan_hq` / `scan_lq` / `photo` |
| Canaries | all 12 exist (statement, invoice and ownership-chart hosts x P1 to P4) plus a control per host; rebuilt in W3 by `make_bundle.py --canaries` (T-integration-kyc-04) |

### Rules results

| Task | n | Accuracy | Macro-F1 | Digital | Scan (union) | Low-confidence share |
|---|---|---|---|---|---|---|
| C1 page type | 80 | 0.975 | 0.962 | 0.975 | 0.975 | 0.200 |
| C3 issuer | 28 | 0.964 | 0.964 | 1.000 | 0.929 | 0.000 |
| C4 credit category | 137 | 0.898 | 0.924 | 0.934 | 0.868 | 0.117 |
| C5 counterparty activity | 514 | 0.903 | 0.892 | 0.910 | 0.896 | 0.344 |

C1 by render: `digital` 0.975, `scan_hq` 0.950, `scan_lq` 1.000, `photo` 1.000. The two C1 misses are `boilerplate` pages labelled `other` (both non-specific, both still visible documents). Per-class tables and confusion matrices are in the JSON.

| Classifier | p50 s/page | p95 s/page | Dollars / 100 pages | Canaries |
|---|---|---|---|---|
| rules | 0.003 | 0.018 | 0.0000 (no model dollars) | 12 / 12 |
| local | skipped: not installed | skipped: not installed | skipped | skipped |
| llm (hosted) | skipped: no API key | skipped: no API key | skipped | skipped |
| cascade | 0.004 | 0.018 | 0.0000 | 12 / 12, degraded to rules |

Seconds per page are the total classification time of one page: its C1 call, its C3 call (statement pages) and the C4 and C5 rows on it. Dollars are `0.0000` for rules and local because neither is priced (local compute is not dollars), and for the hosted classifier with the Fake client the report prints "no real model cost measured".

### What the C4 number means (finding for ingest-classify, resolved in W3)

The first run measured C4 at 0.511: 57 of 87 `customer_payment` rows came back `other` because the generator's phrasings `RETAINER FEE <client>`, `GIRO CREDIT <client>` and `FUNDS TRANSFER FROM <client>` were not in the rules' cues. The rules classifier has since been extended (CR-57) and the W3 rerun on the same 80 pages gives **0.898** (digital 0.934, scan 0.868; macro-F1 0.924), the other three tasks unchanged within rounding. The gate still passes only the counterparty as context (no applicant name, no related parties, no amount), so `owner_transfer` and `inter_company` by name overlap are not exercised either (1 `owner_transfer` row exists).

**Not resolved (T-integration-kyc-04, recorded).** `make_bundle.py --gate-set` still samples only the loan bundle builder (`_gen/gate.py` `build_gate_set`), so the gate set has no certificate, ownership-chart, shareholder-register, source-of-funds or passport pages, and the "every C1 label has at least 3 pages" clause of CR-71 does not hold for those five labels (the command prints the shortfall). The 12 canaries and their controls are complete and pass 12 / 12 with the rules and cascade classifiers. Extending `build_gate_set` to sample the KYC and AML builders (and the `80` page counts in `test_gen_gate_canaries.py`) is the remaining work; KYC page types are meanwhile classified correctly in the end-to-end KYC bundles (`test_kyc_bundles.py`: every KYC page type reaches the 0.70 threshold), which is a different and weaker evidence than the labelled gate.

## 2. How to run it

Prerequisites: the gate set and canaries (generated, fictitious):

```
uv run python backend/scripts/make_bundle.py --gate-set --out samples
uv run python backend/scripts/make_bundle.py --canaries --out samples
```

Run (defaults shown):

```
uv run python backend/scripts/classifier_gate.py \
    --gate-dir samples/gate --canary-dir samples/canaries \
    --classifiers rules,local,llm,cascade --ocr-noise 0.02 --seed 4002 \
    --out docs/eval-results/classifier-gate.json
```

| Flag | Meaning |
|---|---|
| `--classifiers` | any of `rules,local,llm,cascade`. If `local` is named without `rules`, rules are added (the criteria compare against them) |
| `--ocr oracle` (default) | scan text from `ground_truth.json` lines plus seeded noise |
| `--ocr real` | actually OCR the scans through the vision model; needs `GLDB_LLM_API_KEY` and model names, refuses to run with the Fake client |
| `--ocr-noise` | base character-garble rate (O/0, l/1, S/5, `rn`/`m`), times 1, 2 or 3 by render |
| `--max-usd` | budget for the hosted classifier during the gate (default 5.00) |

Digital pages use the real text layer through the ingest word builder. The script exits 2 with a hint when `samples/gate/labels.json` is missing. It makes no network call unless a real LLM client is configured, and never reads the date.

### Running the local model (H-13, by a human, on a laptop with weights)

1. Read each model repo's licence and the Qwen base-weight licence; write them into section 1 above.
2. Add the optional dependencies (this is a human step; agents never edit `pyproject.toml`): `uv add --optional local torch transformers`, then `uv sync --extra local`.
3. Download the weights **outside** the project (for example with `huggingface-cli download`) so the Hugging Face cache holds them. The adapter loads with `local_files_only=True` and never downloads.
4. Set `GLDB_LOCAL_CLASSIFIER_MODEL` to the model id or local path. A comma-separated list is tried in order (plan 5.3: openjev 0.8B, then 2B; Kev-0.5B as the alternative). Set `GLDB_LOCAL_CLASSIFIER_DEVICE` to `auto`, `cpu`, `mps` or `cuda`.
5. Re-run the gate and replace section 1 with the real numbers, the model name and revision (the adapter reports the commit hash as the version).

The adapter's prompt and yes/no read-out are this project's assumption of the openjev method: plan 5.2's JEV facts are third-party and unverified. If the real checkpoint expects another format, change `build_prompt` / `TransformersScorer` in `classify/local.py`; the rest of the gate does not care.

## 3. What the gate measures

For every classifier and each of C1, C3, C4, C5: `n`, `n_errors` (an item the classifier failed on counts as wrong, never dropped), `accuracy`, `macro_f1` (over classes present in the truth), `per_class` precision and recall, `confusion`, `by_render` (`digital`, `scan_hq`, `scan_lq`, `photo`, and `scan` = the union of the three), and `low_conf_rate` (answers below `GLDB_CLASSIFIER_MIN_PROB`). Then latency (`p50_s`, `p95_s`, nearest-rank, per page), `usd_per_100_pages` with a `usd_note`, `canaries`, and `decision`.

Classifier modes mirror the pipeline: `rules`; `local` (C1 label set pruned to the rules' top 3, `other` and anything scored at least 0.35 via `prune_labels`); `llm` (hosted, rows batched 50 per call); `cascade` (rules first; unsure items, meaning low probability or `other`/`boilerplate`, go to local when available; then to hosted when still unsure or when rules and local disagree on a specific label; combined with `base.decide`, so a non-specific answer can never override a confident specific one).

**Status values** (`classifiers.<name>.status`, and `skipped` in each section): `ran`; `skipped: not installed` (no `torch`/`transformers`, no weights, or no model configured); `skipped: no API key` (the hosted client is the Fake without fixtures). A skipped classifier is never omitted from the JSON.

**Canaries** (classification.md 4.10): for each injected canary the gate classifies every page of the untouched control and of the injected file with the same classifier. For placements P1 (footer line), P3 (white 1 pt text) and P4 (inside a description) every original page must keep the control's C1 label; for P2 (extra last page) the original pages must keep it and the extra page is reported (`extra_page_label`; `other`/`boilerplate` is acceptable only as a visible document, and the `doc_unclassified` flag and coverage rule are proved end to end by the eval, not here). `max_abs_delta` is the largest change in the control label's probability, informational. The pass rate needs all 12 canaries to be present; fewer present means "incomplete" and blocks adoption.

## 4. Adoption rule

Adopt the local model as a tier only if **all four** hold (thresholds 5.0 s and 2 points are this spec's proposal; the plan gives none):

1. local C1 accuracy on `scan` is **strictly greater** than rules';
2. local passes **12 / 12** canaries (so a set with fewer than 12 canaries cannot adopt);
3. local p95 is at most **5.0 s per page** on the machine that ran the gate;
4. no task (C1, C3, C4, C5) is more than **2 percentage points worse** than rules.

In short: adopt only if it beats rules on scans and passes the canaries (and is fast enough and does not regress). Otherwise keep rules plus the hosted fallback and write the negative result here. The `decision` object lists each criterion with `met` (`true`, `false`, or `null` when not evaluated) and a detail string. When the local model is unavailable the decision is `adopt_local=false` with the single reason `local classifier not available`.

Even when adopted, the local model sits under the decision rule of classification.md 4.9: it can confirm or add risk, never suppress it.

## 5. Limits

- Oracle OCR plus synthetic noise is **not** a real scan measurement; no OCR exists in this build without an API key. Scan accuracy is therefore an optimistic proxy (the oracle text is clean apart from the injected noise). Use `--ocr real` with a key for the real figure.
- The generator never produces `other_bank` statements: C3 has no `other_bank` row.
- Gate set has loan-only labels (CR-71 still open, see section 1): no passport, certificate of incorporation, ownership chart, shareholder register or source-of-funds pages. The canary set is complete (12 / 12).
- C4 and C5 context is the counterparty only; related-party and applicant-name logic is not exercised.
- 80 pages is a small sample; differences of a few points are within noise. Latency is for one machine and one process (no batching on the local model: one forward pass per label).
- Hosted scores are uncalibrated and the hosted classifier receives counterparty names (open privacy item, plan 7.1).
- The tests use fake models (`backend/tests/test_classifier_*.py`): they prove the gate's arithmetic, skip handling and decision logic, not the quality of any real model.
