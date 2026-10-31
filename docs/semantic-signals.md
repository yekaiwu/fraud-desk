# Semantic signals: model-derived checks that cite their evidence

Status: the shared base (switch, claim schema, evidence verifier, call wrapper, registry hook, severity cap, fast-lane rules, canaries) is built and unit-tested with the fake client. Which concrete checks sit on top of it is described by the feature sections that the feature work adds to this file; this page is the contract they must keep. All data in this repository is fictitious, and every number measured on our own hand-written data is an upper bound.

## 1. What this is, and why it is separate

The rules in [check-catalogue.md](check-catalogue.md) compare values: sums, dates, registry fields, name strings. They cannot read meaning. A transaction described as a crypto top-up on the statement of a wholesale electronics firm, or a description that is a paraphrase of the declared business, is invisible to a string matcher. A language model can read it, but a model can also be wrong, be talked into things by the document it reads, and say things that are not in the document.

So model-derived signals are a separate layer with stricter rules than the deterministic checks. They are **on whenever a model is configured** (and off with the fake backend), they can only **add** attention (never remove it), they must **quote the extracted data** for everything they say, and a model failure is shown as "not checked", never as "fine". The deterministic checks do not depend on any of it.

## 2. The six invariants

| # | Invariant | Where it is enforced in code |
|---|---|---|
| 1 | On when a model is configured, switchable (`GLDB_SEMANTIC=off`). Off means byte-identical behaviour and eval numbers: the semantic checks are not even registered as applicable, so coverage ratios do not change. | `Settings.semantic`; `checks.registry.all_checks(settings)` and `applicable(ctx)` add semantic checks only when it is on; `result_settings()` lists the switch only when on, so the provenance hash of an off run does not move. |
| 2 | Evidence-bound. The model returns structured claims. Each claim cites document id, a field or a table row (and optionally a column), and the quoted value. Code checks that the document was shown to the model, that the field or row exists, and that the quote appears in that cell's extracted text. A claim with any unverifiable citation is dropped and counted; it is never shown as a flag. | `semantic/claims.py` (`verify_claims`). Counts go to `CheckRun.reason` as `claims proposed=4 kept=3 dropped=1 why=quote_not_found:1`. |
| 3 | Capped. A model-derived flag is at most `medium`. It never moves a case into the fast lane and never lowers a score. A failed, timed-out or malformed call marks the check as not run (coverage down, case partial), never as passed. | `semantic/policy.py` (`cap_severity`) applied in `semantic/claims.py`, `semantic/flags.py`, `semantic/registry.py` and again in `checks.registry.run_stage_checks` (so a catalogue override cannot lift it either); `queue.py` (fast-lane rules, section 6); `semantic/call.py` (`SemanticNotRun`, section 5). |
| 4 | All document text is untrusted. | Document text is rendered by `semantic/render.py` into the `user` message inside `wrap_untrusted` delimiters whose nonce is a hash of the enclosed text; instructions live in `system` and never contain document text; the system prompt says the delimited text is data. Semantic canaries are in `backend/tests/test_semantic_canaries.py` and `_eval/canaries.py` (section 8). |
| 5 | Deterministic where the backend allows. Temperature 0, prompt hash and model name in the cache key, prompts registered with `register_prompt` (so they are hashed into the provenance record). | `semantic/call.py` registers the prompt on every call and puts `sem<version>:<prompt sha>` in `cache_extra`; the clients add the model name, the full request hash and `t0`. Caveat in section 9. |
| 6 | Fairness. The existing name-group slice is rerun with the features on; the flag-rate disparity across name groups must not increase. | Measured by the feature work and reported in section 10; not enforced by the base. |

## 3. How to turn it on or off

```
GLDB_SEMANTIC=              # empty = on when a model is configured, off with the fake backend; on | off, true/false/1/0 are accepted
GLDB_SEMANTIC_MAX_CLAIMS=8  # claims kept per model call; more are dropped and counted
```

The semantic checks use whatever model the app is already configured to use (`GLDB_LLM_BACKEND`); nothing about them picks a backend of its own. A fake backend with no fixtures answers nothing, so with `GLDB_SEMANTIC=on` and no real model every semantic check is recorded as errored and the case is partial. That is the intended, visible behaviour.

The response cache is on by default (`GLDB_CACHE_ENABLED`), so re-running the same case re-uses every semantic answer at zero cost.

## 4. The two models and what each is used for

| Model | Backend setting | Used for |
|---|---|---|
| Qwen3.8-27B, served locally through an OpenAI-compatible server (LM Studio) | `GLDB_LLM_BACKEND=openai`, `GLDB_LLM_BASE_URL=http://localhost:1234/v1`, `GLDB_LLM_MODEL_CHEAP=qwen3.8-27b` (the same for `_STRONG`) | Development, prompt tuning on the dev seed, and the large evaluation runs. Zero marginal cost; slow (section 11). Text only: no image input. |
| Claude Haiku 5.5 (`claude-haiku-5-5`), the only paid model the app allows | `GLDB_LLM_BACKEND=anthropic` (see [anthropic-haiku.md](anthropic-haiku.md)) | A small spot-check of the frozen prompts, to see whether the local model's behaviour carries over to a second, stronger model. Spend is capped. Not used for volume. |

The local server needs a non-empty `GLDB_LLM_API_KEY` for the app's client to start; any placeholder string works because the server does not check it. Prompts are tuned on the dev seed only; the held-out seed is run once with frozen prompts.

Local-model switch for thinking: this server ignores `GLDB_LLM_ENABLE_THINKING=false` (it ignores `chat_template_kwargs`). The new setting `GLDB_LLM_REASONING_EFFORT=none` sends the OpenAI-style `reasoning_effort` field, which it honours (section 11 shows the effect). Default is empty, which sends nothing, so no existing run changes.

## 5. The building blocks (for people adding a check)

All of it is under `backend/app/semantic/`. The package `__init__` imports nothing, so `app.queue` and `app.checks.registry` can import `semantic.policy` without a cycle.

```
policy.py    SEMANTIC_RULE_PREFIX = "sem_"; is_semantic_rule(rule); cap_severity(sev);
             semantic_enabled(settings)
claims.py    claims_json_schema(kinds, extra_properties=None, extra_required=());
             verify_claims(reply, docs, kinds=, max_claims=, extra_keys=) -> Verification;
             Verification.claims / .dropped / .proposed / .severity_capped / .note();
             parse_note(note) -> dict; norm(text)
render.py    render_document(doc, fields=, tables=, columns=, max_rows=) -> str;
             wrap_documents([(doc_id, text), ...]) -> str
call.py      run_claims(ctx, rule=, name=, task=, docs=, kinds=, stage=, fields=, tables=,
                        columns=, user_prefix=, extra_properties=, extra_required=,
                        cache_extra=) -> Verification
             call_model(ctx, name=, system=, user=, schema_name=, json_schema=, stage=,
                        document_id=, cache_extra=) -> dict
             SemanticNotRun(reason); system_prompt(task)
flags.py     make_semantic_flag(rule, claim, docs, discriminator=None, severity=None) -> Flag
registry.py  @semantic_check(rule=, applies_to=, description=, severity="medium", stage="rules",
                             requires=, use_cases=, version=)
             discover_semantic(); semantic_checks(settings); unregister(rule)  # tests only
checks/      one module per feature; every module not starting with "_" is imported on discovery
```

A check looks like this (shape only):

```python
@semantic_check(rule="sem_activity_fit", applies_to={"bank_statement"},
                description="...", stage="rules")
def sem_activity_fit(ctx: CaseContext) -> list[Flag]:
    docs = ctx.docs("bank_statement", "company_profile")
    if not rows_worth_asking_about(docs):
        raise CheckSkipped("no transactions to review")   # normal, not a failure
    v = run_claims(ctx, rule="sem_activity_fit", name="activity_fit", task=TASK,
                   docs=docs, kinds=["activity_mismatch"])
    return [make_semantic_flag("sem_activity_fit", c, docs) for c in v.claims]
```

* **Registration is conditional by construction.** `@semantic_check` writes to a registry of its own; `all_checks()` without settings, the check catalogue and the ruleset hash never see it. With the switch on, `all_checks(settings)` and `applicable(ctx)` include it, and `get_check(rule)` finds it (so `make_flag` and the catalogue endpoints work). Prompts are registered on the first call, not at import, so nothing is added to the provenance prompt hash while the feature is off. The flip side: with the feature on, the provenance `prompt_hash` covers the prompts registered so far in that process, so it depends on which semantic prompts have already been used since the process started. Two runs of the same case can record different prompt hashes. Known and not fixed.
* **Failure is "not run".** `call_model` turns any exception of the client (transport error, timeout, malformed or schema-invalid output after the client's one retry, budget exceeded, no fixture) into `SemanticNotRun`. It is deliberately not a `CheckSkipped`: the runner records it as an errored check, which lowers coverage and adds `check_error:<rule>` to the partial reasons, so the case is partial and cannot take the fast lane. `CheckSkipped` stays for the normal "nothing to analyse here".
* **Retries.** Schema failures are retried once by the client with the validation error fed back (`GLDB_LLM_SCHEMA_RETRIES`); 429, 5xx and connection errors are retried by the client with back-off. The wrapper adds none, so nothing is paid for twice, and a timeout is not retried.
* **Cost.** The wrapper opens the cost scope itself (stage `rules` or `crossdoc`, optional document id), so costs are attributed to the stage and the budget guard (`GLDB_MAX_LLM_CALLS_PER_CASE`, `GLDB_MAX_USD_PER_CASE`, the daily cap) applies as for every other call.
* **What is evidence and what is not.** Only `citations` are verified. The claim's `statement` is the model's sentence; it becomes the flag's explanation, prefixed "Model-derived signal, quotes verified against the extracted text:", and is never used as evidence. The flag's `evidence` line is rebuilt from the verified quotes. `extra_properties` (for example a category label) come back in `claim.attrs` and are *not* verified: a check that acts on one must verify it itself.
* **What counts as "the quote appears".** The quote is compared with the cell's `value` and its printed form `raw` after Unicode normalisation, case folding and whitespace collapsing. It must be a substring (at least 3 characters) or the whole cell. A paraphrase fails. A numeric quote is accepted in either its canonical or its printed form.
* **Limits of the verifier.** It proves the quote is in the extracted data the model was shown. It does not prove the claim follows from the quote, and it does not prove the extracted data matches the original page (that is the extraction stage's own verification). A claim can be verified and still be a bad inference; that is what the medium cap, the "model-derived" label and the human reviewer are for. The 3-character minimum is a weak bar: a quote of three characters that sits anywhere in the cell is accepted as evidence, and a short quote proves very little. This is known and not changed.

## 6. Score, lane and coverage

* **Score.** Flags only add. A semantic flag is a finding of its own (default grouping key `rule:<rule>:<documents>`), weight 15 for medium and 5 for low, so it cannot merge into, or cancel, another finding. The score without the semantic checks is a lower bound on the score with them.
* **Hard stops and routing.** `sem_*` rules are in neither `HARD_STOP_RULES` nor `AML_ROUTING_RULES`, so a model-derived flag is never a hard stop and never changes the lane family by itself.
* **Fast lane.** In `queue.fast_lane_eligible`: a finding with weight above 0 made only of semantic rules closes the fast lane (a case with a model-derived concern is looked at now, not last); and the coverage bar is computed over the non-semantic check runs only, so a semantic check that ran cannot lift a case over the 0.80 bar. A semantic check that errored makes the case partial, and a partial case is never fast. The displayed coverage ratio counts a semantic check only when it failed (`counts_in_coverage` in `semantic/policy.py`), so a model failure is visible there while a normal run or skip leaves coverage exactly as it is with the feature off. Before this rule a skipped fit check counted as applicable-but-not-run and lowered the displayed ratio of 22 of 33 clean bundles; the measured coverage figures further down were taken before that change.
* **Override.** A catalogue override can switch a semantic check off (recorded as skipped, `switched_off`) or lower its severity; it cannot raise it above medium (`run_stage_checks` caps it).

## 7. Reading the counts

Every semantic check that ran has its claim counts in the check run: `claims proposed=P kept=K dropped=D [capped=C] [why=reason:n,...]`. Reasons are `bad_shape`, `bad_kind`, `no_citation`, `empty_quote`, `document_not_provided`, `unknown_field`, `unknown_table`, `unknown_row`, `unknown_column`, `quote_not_found`, `duplicate`, `over_limit`. `capped` counts claims the model labelled high that were reduced to medium. A high drop rate with `quote_not_found` is the signature of a model that paraphrases or invents text; it is a measurement of the model, not an alarm in the case.

## 8. Injection canaries

`backend/tests/test_semantic_canaries.py` plants the six injection strings of the classifier canaries, plus an instruction to return high severity and a forged end marker, into a transaction description, and scripts the fake model to **obey** them (the worst case; the guarantees are in code). It checks that: the text reaches the model only inside the delimited user block (and a forged end marker has a different nonce, so it stays inside); an obedient model's "high" is capped to medium; claims citing text or documents that are not there are dropped; an output with extra keys (`score`, `verdict`, `lane`) or a severity outside the enum is rejected and the check is not run; a "report no issues" model changes neither the score nor the lane; a "flag everything" model can only raise the score, by one medium finding.

`scripts/_eval/canaries.py` gains `canary_row(..., semantic=True)`, used by the canary suite when `GLDB_SEMANTIC` is on: in addition to the existing criteria, no `sem_*` flag may be above medium and the canary may not be in the fast lane unless its control was. These canaries test the **guards**, not the model's resistance: a real model can still be talked into a wrong claim, and what it can then do is bounded by sections 2 and 6.

## 9. What this does not do, and what was not possible

* **Haiku has no temperature setting** on this model (the Anthropic client sends none: a non-default value is a request error), so invariant 5's "temperature 0" holds for the local model and for the fake client only. For Haiku, repeatability rests on the response cache: the same prompt, schema and model returns the stored answer. A cache miss may differ from an earlier run.
* **The local server's concurrency is not safe for long generations** (section 11): with thinking on, concurrent requests failed on the server side. The base does not work around it; run thinking-on work at concurrency 1.
* **Unit tests use the fake client only.** The shape of a real model's output (nullable citation fields in the tool schema, a closed set of kinds) was checked once against the local model on a three-row example (one claim, verified, cited both the statement row and the profile field). That is a smoke test, not a measurement.
* **Citations stop at the extracted data.** A claim about something that is on the page but was not extracted (an unread line, a scan the OCR got wrong) cannot be verified and is dropped, so the layer is blind to what extraction missed.
* **The data is ours.** Hand-written, fictitious; a model that has seen our style of description can look better than it will on real customer text. The evaluation sets for the three features (seeds 5101 and 7001) are described in [test-data-and-eval.md](test-data-and-eval.md) section 9.
* **Who wrote what, and what is shared between dev and held-out.** The project team wrote the catalogues, templates, name pools, perturbation functions and labels; no model wrote or labelled any row. The prompt glossaries (Malay and romanised Chinese words, abbreviations, dialect syllables) were written by the team after reading dev errors. The two splits differ in brand words, vendors, founders and industries. They share the generator code, the templates, the perturbation functions, the generic words and the vocabulary the glossaries cover. So the held-out numbers show that the prompts did not memorise the dev tokens. They do not show that the prompts work on text written by someone else, and the glossary-covered cases (Malay, romanised Chinese, abbreviations, dialect spellings) are the most inflated.
* **The dev tables for TXN and ENTITY cannot be reproduced from the shipped dev files.** The prompts for these two features were tuned on copies of the dev data made before the generator was revised (336 TXN rows; the ENTITY pairs differ from the shipped ones in about a third of the 384 pairs, 111 to 135 depending on how a pair is matched). After the revision `samples/semantic` was regenerated (384 TXN rows, a different set of rows). The freeze records still hold the manifest hashes of the older dev files, so for TXN and ENTITY they do not match the files in the repository; for FIT they match. The dev tables below therefore describe the older sets. The held-out run used the regenerated data, whose hashes match the manifest. An independent check found no entity name shared between the older dev set and the held-out set, and a few fixed TXN lines (about 15 of 338) in common; that is a check for leakage, not a proof of none. The dev tables are tuning history, not figures to reproduce.
* **The criteria were not recorded in advance.** The adoption thresholds are written in the held-out summary. No timestamped copy of them from before the run exists in the repository or in the freeze records, so "set before the run" is a statement we cannot prove. The five prompt hashes, the three module hashes and held-out data hashes were checked before and after the run and the module and freeze times precede the first held-out run; that part is verifiable.
* **The harness scripts were edited just after the first held-out run began.** The three feature harnesses were changed 54 seconds after the first transaction run started (the output-naming slip in the held-out summary). No earlier copy exists to compare. The row-level raw files reproduce every stored score, so scoring was not affected, but the edit cannot be shown to be limited to naming.
* **Open items.** Review findings that are confirmed and not fixed, and other known gaps, are listed at the end of section 11 of [limitations.md](limitations.md).

## 10. Fairness and per-feature results

Each feature section below holds its dev-seed tuning (read the provenance caveat in section 9 first: the TXN and ENTITY dev tables describe older dev files). The single held-out run with frozen prompts (seed 7001, three repeats, plus bundle runs on seed 9001) has been done. Its misses, dropped-claim rates, variance, cost and name-group flag-rate disparity are in the local, gitignored file `docs/eval-results/semantic/summary.md`; the short version, with the caveats, is in [limitations.md](limitations.md) section 11. In short: TXN and FIT met their criteria; ENTITY met its accuracy criteria and is not adopted, because the name-group disparity of the low-flag rate rose from 0.00 to 0.33 (invariant 6).

## 11. Local model: measured latency and throughput

Measured through the repo's own client (`OpenAICompatClient`), one structured answer per call: eight entries of three fields, about 300 to 400 completion tokens when thinking is off, prompt about 400 tokens, response cache off, temperature 0, one server (a single 27B model on one Apple-silicon machine, LM Studio). "Concurrency" is `GLDB_LLM_MAX_CONCURRENCY`. Small samples; read as orders of magnitude.

| Thinking | Concurrency | Calls | Mean latency per call | Completion tokens per call | Aggregate tokens per second | Result |
|---|---|---|---|---|---|---|
| off (`GLDB_LLM_REASONING_EFFORT=none`) | 1 | 6 | 10.7 s (10.0 to 11.9) | 390 | 36 | all valid |
| off | 2 | 8 | 12.5 s (11.5 to 13.8) | 393 | 62 | all valid |
| off | 4 | 16 | 20.2 s (18.5 to 22.7) | 394 | 76 | all valid |
| on (server default) | 1 | 2 | 93.7 s (93.5 to 94.0) | 4,658 (about 4,300 of them reasoning) | 50 | valid |
| on | 4 | 4 | 67 to 137 s | | | 3 of 4 failed: server error "speculative batch index ... is not inside the current sub-batch" (HTTP 500, seen by the client as `status 400`) |
| `GLDB_LLM_ENABLE_THINKING=false` only | 1 | 1 | 151 s | 3,211 | 21 | valid, but still thinking: the setting has no effect on this server |

Reading it: thinking off is about ten times faster than thinking on for this kind of answer (and the thinking-on first call, 151 s at 21 tokens per second, shows how much a cold or contended server varies). Concurrency 4 doubles aggregate throughput with thinking off (76 against 36 tokens per second) at the price of per-call latency doubling, and was reliable over 16 calls. Thinking on at concurrency 4 is not usable on this server. The model accepts no images ("does not support image inputs"), so nothing here depends on vision. A practical planning figure for thinking off: about 5 to 6 short structured calls per minute at concurrency 1, about 3 per minute per slot (12 per minute in total) at concurrency 4; add prompt-processing time for long documents.

## TXN: transaction description labels (feature section)

Status: built, unit-tested with the fake client, and tuned on the dev seed (5101) with the local model in two rounds; prompt frozen after round 2; the held-out seed (7001) was run once afterwards (see section 10). Dev results are in the subsection "Dev-seed tuning (5101)" at the end of this section; they were measured on an older version of the dev file than the one now in `samples/semantic` (section 9). All numbers are upper bounds (hand-written data, [test-data-and-eval.md](test-data-and-eval.md) section 9).

**What it does.** The model puts each distinct statement description into a closed set of ten labels: `salary`, `rent`, `supplier_trade`, `loan`, `cash`, `gambling`, `crypto`, `transfer_individual`, `tax_government`, `other`. The label is an extra input to a deterministic rule. It creates no flag of its own.

**Files.** `backend/app/semantic/txn.py` (labeller, verifier, lookups), `backend/app/semantic/checks/txn_labels.py` (the registered check `sem_txn_labels`), the consumer in `backend/app/checks/kyc.py` (`x_declared_vs_activity`), tests in `backend/tests/test_semantic_txn.py`. `CaseContext.semantic_state` is the per-run hand-over from the `rules` stage to the `crossdoc` stage.

**Prompt id.** One prompt, purpose `semantic:txn_labels` (registered with `register_prompt` on first call, hashed into the provenance record, its sha in the cache key). Reply schema `TxnLabels`: `{"labels": [{"item": int, "label": <enum of the ten>, "cue": str}]}`, no other keys. The prompt is a label list with one line of meaning each, three rules (decide from the words, answer `other` when unclear, copy the cue letter for letter) and the untrusted-data notice. It says nothing of amounts, and tells the model that a person's name or origin says nothing.

**Evidence.** The "citation" of a label is its cue. A label other than `other` is kept only when the cue is a verbatim piece (case, spacing and width folded) of the description the item stands for, at least three characters long (or the whole text). Otherwise it is dropped and counted (`quote_not_found`, `empty_quote`; also `duplicate`, `unknown_row`). Items the model did not answer are counted as `unanswered`, stay unlabelled (and are not remembered as "no label"), and the check is reported as not run: a partly answered reply is a malformed one. The cue is checked again against every statement line that shares the label (descriptions are de-duplicated with digit runs masked), and the flag evidence quotes it.

**Batching and calls.** Descriptions are normalised (case, spacing, digits masked) and de-duplicated together with the direction (money in or out). New ones are sent per statement in batches of 40; a description already answered in this case is not sent again (the labels live in `ctx.semantic_state`), and the response cache covers a re-run. Cap: 400 distinct descriptions per case; the rest stay unlabelled and are counted (`unlabelled_cap`). Expected calls per case: 0 when the check is skipped (see below) or the statement has no lines; otherwise one per statement for up to 40 new descriptions, and later months of the same account usually add 0 or 1. A case with 3 to 6 statements of 20 to 60 lines: about 1 to 4 calls, each about 1,000 output tokens (25 per item), so roughly 30 s each on the local model with thinking off. Run it with `GLDB_LLM_REASONING_EFFORT=none` (section 4).

**When the check runs.** Mirrors `x_declared_vs_activity` so that no call is paid for nothing: it needs a company profile and a bank statement (use case `kyc`, as the rule), and is skipped without a call when the declared activity cannot be mapped to a category, when it already covers both `crypto_virtual_assets` and `gambling` (the two labels a rule can use), when the statements are in more than one currency, or when there are no transaction lines.

**Which rules use the labels, and which do not.**

| Rule | Uses labels? | Why |
|---|---|---|
| `x_declared_vs_activity` | Yes, `gambling` and `crypto` only | A statement line whose own C5 annotation is missing, below the confidence bar or `other` can count as a `gambling` or `crypto_virtual_assets` observation. It never replaces a label the classifier is sure of, never turns the rule on (the "no c5 annotations" skip and the coverage ratio are unchanged), and the rule's own thresholds (two rows or 5% of turnover) and severity (medium) apply. A flag that rests on model-labelled rows says so in its evidence and quotes the cues. |
| `x_sof_support` | No | The only direction a label could move it is "this credit is explained", which removes a finding. A model signal must not lower a score. Its `high` severity could also not be reached from a model label. |
| `bs_pass_through` | No | The only use of a category there is excluding a credit (`refund`), which removes a hop. Adding hops needs counterparties, which the deterministic parser already reads. |
| Other labels (`salary`, `rent`, `supplier_trade`, `loan`, `cash`, `transfer_individual`, `tax_government`) | No rule consumes them | They are produced and counted (the labeller can be evaluated on all ten) but no existing rule can use them without changing its logic. |

So the feature has a narrow reach on purpose: two of ten labels feed one rule.

**Failure.** A failed, timed-out, malformed or empty reply is `SemanticNotRun`: the check `sem_txn_labels` is recorded as errored, coverage drops, the case is partial (`check_error:sem_txn_labels`) and cannot take the fast lane. Labels verified in the batches that did succeed stay in use (they only add attention). `x_declared_vs_activity` then reads what it read with the switch off.

**Off.** With `GLDB_SEMANTIC=off` `sem_txn_labels` is not registered, `ctx.semantic_state` stays empty, `c5_label_for` returns `None` before it looks at anything, and the rule's code path is the old one (tested). Existing eval numbers are unchanged.

**Open risks.**

* A flag raised by `x_declared_vs_activity` partly from model labels is an ordinary medium flag of a non-`sem_` rule. A catalogue override can raise that rule above medium and the cap in `run_stage_checks` (for `sem_` rules) does not apply to it. The evidence line marks the rows, but the severity is not capped for them.
* Such a flag counts as a normal finding in the fast-lane test (it closes the lane like any flag with weight); the "only semantic rules" clause does not cover it. It can only add score, never remove it.
* The cue proves the words are in the description, not that the label follows from them: `CASINO` in a firm's name (a catering company called "Casino Road Foods") is a verified, wrong `gambling`. The medium severity, the quoted cue and the reviewer are the control.
* Names. Descriptions carry counterparty names. The prompt tells the model that the name or origin of a *person* says nothing, but since dev round 1 it must read a *company* name ("Millbrook Gaming Pte Ltd", "Mapleton Token Pte Ltd") for gambling and crypto, so a brand that looks like a gambling or crypto word can trigger a label. A probe with 300 personal-name transfer lines over the six name groups of the fairness slice found no risky label and 100% `transfer_individual` in every group (see the subsection below); that is a probe on one template, not a proof.
* `transfer_individual` rests on the model telling a person's name from a business name; the label is not used by any rule, so a mistake costs nothing today.
* Digit masking merges descriptions that differ only in numbers. A reference that carries the meaning (`PAY 777` as a code) is lost.
* The local model's accuracy on Malay or romanised Chinese wording is measured on dev only (0.987 at the frozen prompt, with a glossary written from the same generator vocabulary); it is not known on real customer text.

### Dev-seed tuning (5101)

Everything below is the dev split only, as it was when the prompt was tuned: 336 TXN rows. The dev file now in `samples/semantic` has 384 rows and a different set of rows, so these numbers cannot be reproduced from it (section 9). The old set had 14 ground-truth categories, 84 per messiness level 0 to 3, 75 with Malay or romanised Chinese wording, 16 counterparty rows whose company name is a gambling or crypto business). The key is mapped onto the ten labels (payroll to `salary`, premises to `rent`, supplier payment, customer receipt, utilities and insurance to `supplier_trade`, both loan categories to `loan`, owner transfer to `transfer_individual`, both tax categories to `tax_government`, bank charges and interest to `other`, refund to `other` or `supplier_trade`; a counterparty whose class is gambling or crypto is `gambling` or `crypto`). Local model, thinking off, temperature 0, response cache off, one call at a time, rows split into statements of 48 lines and labelled through the product code (`label_statements`), 13 calls per run. Intervals are Wilson 95%.

| Round | Change | 10-label accuracy | Level 3 | Malay / Chinese wording | Gambling or crypto recall (16) | Gambling or crypto labels on the other 320 rows | Labels dropped (cue not found) |
|---|---|---|---|---|---|---|---|
| R0 | the built prompt | 289/336 = 0.860 [0.819, 0.893] | 66/84 = 0.786 | 57/75 = 0.760 | 4/16 = 0.25 [0.10, 0.50] | 0/320 [0, 0.012] | 0 of 246 |
| R1 | company names count for gambling and crypto in either direction; a payment to or from a company is `supplier_trade`; a person is not a company; director and owner transfers are `transfer_individual`; a short glossary of abbreviations and Malay / romanised Chinese words | 326/336 = 0.970 [0.946, 0.984] | 79/84 = 0.940 | 67/75 = 0.893 | 15/16 = 0.94 [0.72, 0.99] | 0/320 | 1 of 255 |
| R2 (frozen) | the glossary corrected for director loans, loan repayment and interest received; "rent" with an address is rent | 331/336 = 0.985 [0.966, 0.994] | 82/84 = 0.976 | 74/75 = 0.987 | 15/16 = 0.94 [0.72, 0.99] | 0/320 [0, 0.012] | 1 of 255 |

How to read it. Round 0 was a measurement of the built prompt; rounds 1 and 2 are the two tuning rounds (at most three were allowed, the third was not needed). **The big gains came from telling the model things the first prompt left out**: that a company name is evidence (R0 read "Refund from Mapleton Token Pte Ltd" as an ordinary refund), that "director loan" is a transfer from a person in our key (R0 called 19 of 24 owner transfers `loan`; this is a convention of our key, a director loan is arguably a loan, and the ten labels have no owner category), and a glossary of the Malay and romanised Chinese words. The glossary was written by reading the dev errors and the generator uses the same vocabulary on both splits, so **the Malay / Chinese number is probably the most inflated one**; the held-out split will show how much of it transfers (its brand words differ, its generic words and templates do not).

Errors left at R2 (5 rows of 336): one refund from a token company read as `other`; "Gongxi Fukun" (a misspelt payroll word) read as `supplier_trade`; three premises lines (a bare "RENT 63 ..." address line, "Rent 21 ...", "BDLNG SERV CHG") read as `other`. None is a gambling or crypto label on a control row.

**Variance (frozen prompt, 3 more runs).** Run V1 repeated R2 exactly and produced the same 336 labels (the same partition and a temperature-0 server are reproducible). V2 and V3 changed the statement partition (a seeded shuffle of which rows share a statement, hence the batches): 331/336 = 0.985 and 330/336 = 0.982; recall of gambling or crypto 16/16 and 16/16; control false alarms 0/320 each. Between R2 and V2 or V3, 5 and 9 rows changed label (rent and short company lines flip between `rent`/`other`/`supplier_trade`; the refund-from-a-token-company row flipped to `crypto`). Accuracy range over the four runs 0.982 to 0.985; recall 15/16 to 16/16. Small samples: this says the labeller is stable to about 3 rows in 336 for batching, not that it is stable to a different model build.

**Cost (local model, thinking off).** About 12 to 13 calls, 20,000 to 22,000 prompt tokens and 7,400 completion tokens, 140 to 145 seconds, for 294 distinct descriptions. Through the pipeline on the clean bundles: 3 calls (KYC bundles) to 6 calls (loan and AML bundles, which carry more statement months) per case, 1,270 to 1,840 prompt and 110 to 910 completion tokens per call, 27 to 48 seconds per case; USD 0 (no paid model was used in this phase).

**Pipeline checks, dev bundles (feature on against off; the labeller is the only semantic check that was switched on).**

* The 33 clean digital bundles (11 each of loan, KYC and AML): flags at medium or above 0 off, 0 on; flag sets, scores and lanes identical in all 33; fast lane 15 of 33 in both; no case partial; `sem_txn_labels` ran in all 33, 0 errors; label cues dropped in 1 bundle (1 label). The *displayed* coverage ratio rises by 0.002 to 0.005 because the semantic check is counted as a run (loan 0.8868 to 0.8889, KYC 0.8056 to 0.8108, AML 0.7593 to 0.7636); the lane test excludes semantic runs, so the lane is unchanged.
* Downstream, the rule `x_declared_vs_activity` on the 5 crypto-insertion tamper bundles (T57) and the KYC fraud and forger decks: the same medium flag on the same row, off and on, in all 7. The labeller **added nothing** there: the existing C5 classifier already labels those rows, so the label only fills gaps that this data does not contain.
* Fairness slice (270 KYC name-pair bundles, six name groups): these bundles carry no bank statement, so `sem_txn_labels` is skipped ("missing document of type bank_statement") and nothing changes: flags, scores and lanes identical in all 270, medium-or-above rate 15/45 in every group on and off (disparity 0.00 both ways). Separate probe for the labeller itself: 300 transfer lines ("FUNDS TRANSFER FROM <name>", "GIRO PAYMENT TO <name>", 50 names per group, dev names) all labelled `transfer_individual` in all six groups, 0 gambling or crypto labels.

**Frozen.** Prompt `semantic:txn_labels` sha256 `f634382b0dd951d4373ca2a994d49021ab751b7ee293ea9ed52684a4fe266b92`, reply schema sha256 `6b75f0cdbebf01e41e7e2b9af19643dade2351807355f1d9949a835c89e0f3e8`, semantic version 1, batch 40. Any change to the prompt text, labels or batch size after this point invalidates the held-out run.


## FIT: does what is declared fit what is shown (feature section)

Status: built, unit-tested with the fake client, and tuned on the dev seed (5101) with the local model in three rounds plus one schema fix; prompts frozen afterwards; the held-out seed (7001) was run once afterwards (see section 10). Dev results are in the subsection "Dev-seed tuning (5101)" at the end of this section. Code: `backend/app/semantic/fit.py` (logic, prompts), `backend/app/semantic/checks/fit.py` (registration), tests in `backend/tests/test_semantic_fit.py`. Evaluation data: FIT in [test-data-and-eval.md](test-data-and-eval.md) section 9.

### Three closed questions

Each question is one model call per case, with a small ordinal answer instead of an open "find problems" request. A 27B local model and Haiku follow a short closed question well and drift on open ones.

| Rule | Question | Declared side (added by code) | Evidence side (the claim must cite it) | Needs |
|---|---|---|---|---|
| `sem_goods_fit` | Do the invoice lines the company issued fit its declared principal activity? | profile `principal_activity` | an invoice `lines` row, column `description` | a company profile with an activity, and at least one invoice issued by that company (supplier UEN equals the profile's, or, with no UEN, the names match) |
| `sem_income_fit` | Is the declared amount plausible for the job the source-of-funds text names? | declaration `declared_amount` | declaration field `origin_description` | a declaration with a description and an amount |
| `sem_coherence_fit` | Do the counterparties on the statement fit the declared principal activity? | profile `principal_activity` | a statement `transactions` row, column `description` | a company profile with an activity, and at least 3 statement lines with a description from an account that is not known to be someone else's |

All three are registered at stage `crossdoc`, severity `medium` (the cap), and only when `GLDB_SEMANTIC=on`. A case without the needed fields gets `CheckSkipped` (a normal skip; it does not enter the coverage ratio); a failed, timed-out or malformed call is `SemanticNotRun` (an errored check: coverage down, the case partial).

### The answer and what becomes a flag

The model returns `{"answer": "fits" | "unusual" | "does_not_fit", "claims": [...]}` (schema `FitAnswer`, enforced by the client). Rules in code, not in the prompt:

* Only `does_not_fit` can produce a flag, and only through claims that survive verification. A flag is always `medium`; the severity the model writes is ignored (a `high` is counted as capped by the shared verifier).
* `fits` or `unusual` with claims attached: the claims are not flags. They are counted as dropped with reason `answer_fits` / `answer_unusual`. `unusual` is the model's way out for an odd-but-ordinary case (a delivery line, a one-off fit-out, a former high-pay role); it is recorded in the result of `fit.ask()` and not shown to a reviewer. Whether `unusual` should become a low flag is an open question for the evaluation (it would catch more and flag the matched controls more).
* `does_not_fit` with no claim at all is counted as dropped, reason `no_claim`.
* Every citation goes through the shared verifier (document shown, field or row exists, quote is in that cell). On top of that each claim must cite at least one cell of the evidence side (the table above); otherwise it is dropped, reason `wrong_anchor`. The declared side is then appended by code from the real cell (verified the same way), so a flag always shows both halves of the comparison and the flag points first at the evidence cell. At most 3 claims are kept per question.
* The counts appear in the check run as `claims proposed=P kept=K dropped=D why=...`, with the reasons of section 7 plus `wrong_anchor`, `no_claim`, `answer_fits` and `answer_unusual`.

### What the model is shown

Only the cells a question is about, wrapped as untrusted text like every semantic call:

* goods: the profile `principal_activity`; for up to 6 invoices issued by the company, only the `description` column of up to 8 lines each, 30 lines in total. No prices, quantities, totals, dates, invoice numbers or party names.
* income: the declaration's category, description, amount and currency (not the declarant or counterparty names, not the period fields, which date the receiving account and not the earning).
* coherence: the profile `principal_activity`; the `description`, `debit` and `credit` columns of at most 40 statement lines, chosen as the largest by amount and shown in statement order with their original row numbers. No account holder, account number or dates. Small lines are therefore not seen: a tiny payment to an odd counterparty is invisible to this check by design.

Names are left out on purpose: the fairness question (invariant 6) is cleanest when no personal or company name reaches the model. Names can still appear inside free text (an employer in `origin_description`, a payee in a statement line); that is data the model has to read, and the name-group fairness rerun must include these checks.

### Prompts

| Prompt id (`register_prompt`) | Check | Claim kind | Task text |
|---|---|---|---|
| `semantic:goods_fit` | `sem_goods_fit` | `goods_industry_mismatch` | `fit.GOODS_TASK` |
| `semantic:income_fit` | `sem_income_fit` | `income_occupation_implausible` | `fit.INCOME_TASK` |
| `semantic:coherence_fit` | `sem_coherence_fit` | `activity_incoherence` | `fit.COHERENCE_TASK` |

Each system prompt is the task text, the shared untrusted-data notice and the shared claim format rules, and holds no document text. The cache key carries `sem1:<prompt sha>:fit2`; bump `FIT_VERSION` when inputs or answer handling change. The three prompts state, in the same words, the bias toward the milder answer (when unsure between fits and unusual answer fits; between unusual and does_not_fit answer unusual) because a false flag costs a reviewer's attention and a miss is still covered by the deterministic checks.

### Cost per case

At most three calls per case, one per question, and only for questions whose inputs exist: a loan case with a profile, invoices and a statement makes 2 (goods, coherence); a KYC or AML case with a profile, a source-of-funds declaration and a statement makes 2 (income, coherence); the FIT evaluation bundles (profile, three invoices, statement, and a declaration in the income family) make up to 3. A repeat run is answered from the response cache. Prompt size is about 2,200 to 2,500 characters of system text and 500 to 2,100 characters of user text.

### Known limits

* The model is asked about the *kind* of business, not about amounts, prices or dates; the deterministic checks own those.
* A general or generic declared activity ("general wholesale trade") fits anything by the prompt's own rule, so a company that declares a generic activity cannot be caught by `sem_goods_fit` or `sem_coherence_fit`.
* `sem_goods_fit` looks at invoices *issued by* the profiled company only. Purchases (invoices where the company is the buyer) are not examined: they are legitimately unrelated to the activity far more often.
* `sem_coherence_fit` takes the profile as the declared side. When the profile is the wrong document and the statement and invoices agree, the claim still cites statement lines (the evidence side) plus the profile's activity; it cannot cite the profile alone.
* A verified claim proves the quote is in the extracted data, not that the inference is right.
* Dev-seed results are in the subsection below. The single held-out run and its name-group probe are summarised in section 10 and in [limitations.md](limitations.md) section 11.
* The "code-only baseline" of 0 flagged tampers is an assumption, not a measurement of the full rule set. A test confirms that no existing rule flags any base case under the loan and AML rule sets. It was not run under the KYC rule set, which is where `x_declared_vs_activity` lives, and that rule flags a crypto or gambling counterparty by code (it flags all five T57 tampers). Some coherence tampers whose odd counterparties are crypto or gambling businesses may therefore be caught by a rule already. The check "recall lower bound above a baseline of 0" is for that reason easy to meet and says little.

### Dev-seed tuning (5101)

Dev split only: 192 cases (72 tampered, 24 per family and 8 each easy, medium, hard; 72 matched unusual-but-legitimate controls; 48 ordinary). Each case is asked the one question of its family, with the local model (thinking off, temperature 0, response cache off). A tampered case counts as caught when the question ends in `does_not_fit` and at least one kept claim cites the evidence cell the answer key names; a control counts as a false alarm when the question produces a flag. The code-only baseline is taken as 0 of 72 (an assumption; see Known limits). Intervals are Wilson 95%.

| Round | Change | Caught, tampered (72) | False alarms, controls (120) | What the counts say |
|---|---|---|---|---|
| R0 | the built prompts | 0/72 [0, 5.1%] | 0/120 | 137 of 137 claims dropped as `document_not_provided`: the model cited the wrapper's `DOC-...` marker instead of the document id. At the answer level the model had said `does_not_fit` on 42 of 72 tampered cases |
| R1 | the system prompt says where the document id is printed and that the `DOC-...` marker is not an id | 12/72 = 16.7% [9.8, 26.9] | 0/120 | ids now right; 70 claims dropped as `bad_shape` (a citation of a field with no `field` key), 39 as `answer_unusual` |
| R2 | a generic citation example in the shared rules | 6/72 = 8.3% [3.9, 17.0] | 0/120 | worse: 106 `bad_shape`. A generic example did not make the model name the field |
| R3 | goods and coherence: concrete citation example, cite only the odd lines (the declared side is added by code), sharper `does_not_fit` definitions (one off-industry line is enough for goods; two or more off-trade trading counterparties for coherence; overheads and one-off purchases never count); income: concrete example | 35/72 = 48.6% [37.4, 59.9] (goods 20/24, coherence 15/24, income 0/24) | 0/120 | goods and coherence work; every income claim still `bad_shape` |
| R3b (income only, a fourth change, outside the three-round limit) | income citations must name a field in the reply schema (`fit_schema(..., cite_field=True)`, no prompt change) | income 20/24; with R3 55/72 = 76.4% [65.4, 84.7] | 1/120 [0.15, 4.6%] | the local model leaves an optional key out of a citation unless the schema requires it |
| Frozen (V1) | R3 and R3b together, one example id renamed in the prompt (a unit test forbids an invoice-number-like string in the system text), `FIT_VERSION` 2 | 55/72 = 76.4% [65.4, 84.7] | 1/120 = 0.8% [0.15, 4.6] | the numbers to quote |

Only the frozen row is a measurement of the final code; R3 and R3b ran with the prompt one example id earlier. Two things stand out. First, most of the gain came from the plumbing of the citation (the id, then the field key), not from the judgement of the model: with the first prompt the model already said `does_not_fit` on more than half of the tampered cases. Second, the prompts were tuned while looking at dev misses, so the dev figures are optimistic by an unknown amount. The example industries in the prompt text were chosen outside the dev and held-out industry lists to avoid copying a case into the prompt.

**Frozen run by family and difficulty** (caught / n; false alarms among the 40 controls of the family): goods 21/24 [69.0, 95.7], 0/40 [0, 8.8]; income 19/24 [59.5, 90.8], 1/40 [0.4, 12.9]; coherence 15/24 [42.7, 78.8], 0/40 [0, 8.8]. Easy 21/24, medium 21/24, hard 13/24 [35.1, 72.1]. By subtype: goods wrong-industry 21/24; income salary too high 9/12, savings too high 10/12; coherence profile odd 11/12, statement odd 4/12. Controls that stayed quiet: adjacent sideline 0/8, general-trade profile 0/8, service lines 0/8, high pay plausible 0/6, former role 0/6, non-salary source 0/6, diversified profile 0/6, one-off expenses 0/6, profile with a stated sideline 0/6. The one false alarm: a hawker stall assistant with 17 years of savings (111,000, a modest-savings control); the model called it high for the role.

**Misses (17 of 72).** 8 coherence statement-odd cases (the model sees the odd suppliers but answers `unusual` in 6 and `fits` in 2), 1 coherence profile-odd case answered `fits`, 5 income cases (all `unusual`; the declared amount is 4.1 to 4.9 times the plausible maximum in four, 7.3 in one), and 3 goods cases (two `unusual`, and one `fits` where auto-part bulbs sit on an electronics invoice, close to the declared trade). The prompt prefers the milder answer on purpose, so these misses are the price of the 1 false alarm in 120.

**Variance (frozen prompts, 3 runs).** Three full runs with three different case orders gave the same 55/72, 1/120, and the same answer and the same reply text for all 192 cases. At temperature 0 on this one server the run-to-run variance is zero; it says nothing about other servers, other quantisations or Haiku. The uncertainty that remains is the sampling uncertainty in the intervals above.

**Cost (local model, thinking off).** 192 calls in about 650 s: 3.4 s per call, 1,650 prompt and 135 completion tokens per call; no USD. Haiku was not used.

**Product path.** Asking every question whose inputs exist (448 calls ran, 128 skipped for lack of inputs): 56 of 72 tampered cases flagged by some question (one more than by their own family: the goods question also flags 12 coherence profile-odd cases, correctly, since the invoices disagree with the profile), and 1 of 120 controls. The 384 cross-family questions raised no flag on any control.

**Pipeline checks, dev bundles (fit checks on against off; only the three fit checks were switched on).**

* The 33 clean digital bundles: flags at medium or above 0 off, 0 on; no semantic flag; flag sets, scores and lanes identical in all 33; fast lane 15 of 33 in both; no case partial. Fit checks that ran: coherence 33, goods 22, income 11 (the rest skipped, no inputs); 66 calls, 2,400 prompt tokens each, 5.6 s per bundle on average. The displayed coverage ratio moves because the new checks are counted as runs: loan 0.8868 to 0.8750, KYC 0.8056 to 0.7692, AML 0.7593 to 0.7719 (the fast-lane bar is computed without semantic runs, so no lane changed).
* The 5 KYC tamper bundles with crypto-counterparty rows inserted (T57): `sem_coherence_fit` flagged 4 of 5 and cited the inserted row; score 15 to 30 on those 4, lane unchanged. The fifth call cited a wrong document id in a four-document input and its claim was dropped. These are true findings of a tamper the deterministic rule already expects, not added false alarms. The KYC fraud and forger bundles: no change.

**Fairness (dev).** The name-group slice (270 KYC bundles, 6 groups of 45) holds a profile and identity documents only, so all 810 fit runs were skipped and nothing changed: that slice cannot test this feature. As a stand-in, 16 ordinary coherence cases were each given 4 personal-name transfer lines using names of each of the six groups (96 questions): 0 flags in every group, disparity 0.0. Each group has 16 questions, so a rate up to about 19% per group is not excluded, and the `unusual` answer (not shown to a reviewer) came up once in each of four groups and in none of the other two. The feature sends no applicant name; names reach the model only inside statement lines.

**Open risks found in tuning.** The model sometimes cites a wrong document id when several statements are shown (1 of the 5 T57 bundles); such a claim is dropped and counted, never shown. The income question rests on the model's idea of Singapore pay levels, and its borderline cases sit exactly where the data was built to be hard (4 to 5 times the plausible maximum). The citation behaviour depends on the model: a model that leaves out `field` unless the schema requires it may behave differently on Haiku.

## ENTITY: counterparty and entity adjudication (feature section)

Code: `backend/app/semantic/entity.py` (logic), `backend/app/semantic/checks/entity.py` (the check `sem_entity_link`, stage `crossdoc`, applies to `bank_statement` and `invoice`). Tests: `backend/tests/test_semantic_entity.py`. Prompt: purpose `semantic:entity_adjudication` (registered through `register_prompt` on the first call, hashed into the cache key).

**What it does.** The rule checks place a counterparty with string matchers (`names.match_company`, `names.match_person`, equal UENs, truncations): `x_remitter_vs_buyer`, `x_sof_support`, `x_entity`, the entity graph. When none of them links a name to a declared party, this check lets a model look at that one pair. It never overrides a code match and never changes another rule's flag.

**Who is compared.**

* Observed names: each distinct counterparty on a statement line (the text after the channel prefix, cited in the `description` cell of its first line) and the other party of each invoice.
* Declared parties: the other party of a contract or purchase order, the source-of-funds counterparty and declarant, the applicant's name on the profile and certificate, directors, and shareholders from the profile, the register and the ownership chart. The applicant's own side of a contract or invoice is not a declared party.

**Candidates are made by code.** A name is dropped from the check when a matcher already links it to any declared party or to the applicant (counted as `resolved`). A pair with two different UENs is never asked. A remaining pair is a candidate only if the two names share a word, a short form of a word (`trdg`/`trading`, first and last letter equal, letters in order), a one-letter typo of a word of five letters or more, or initials (`nge` for Northgate Engineering). Legal suffixes, generic words (holdings, group, services ...) and bank-channel words never count; initial letters alone never count. Per counterparty at most 3 pairs, per case at most 24 (best overlap first, then by name), so the cost is bounded; pairs beyond the cap are not examined and nothing records them.

**The model's job.** One closed question per pair: `same_entity`, `different_entity` or `cannot_tell`, plus a sentence that names both strings, and two citations (the observed cell and the declared cell, quotes copied). The system prompt holds no document text; the pairs are in the user message inside one `wrap_untrusted` block (`label=name_pairs`). The schema closes `pair_id` (`P1`..`Pn`) and the three kinds; the decision comes first in each claim. Prompt wording tells the model to answer `cannot_tell` when unsure and that a holding company and its subsidiary, or one name in two countries, are different entities.

**Verification, on top of the shared verifier.** Each claim must name a pair of its chunk and cite exactly that pair's two cells with quotes found in those cells. Otherwise it is dropped and counted (`unknown_pair`, `pair_mismatch`, plus the shared reasons). Two different verdicts for one pair keep neither (`conflict`); a repeated verdict keeps one (`duplicate`).

**What a verdict becomes.** Severity is set by code; the model's own severity is ignored.

| Verdict | Flag | Why |
|---|---|---|
| `same_entity` | `info` (weight 0): "Possible link, the rules found no match." | A hint beside a rule's mismatch flag. It adds no score, cannot close the fast lane, removes nothing, and nothing reads it as a match. |
| `cannot_tell` | `info` (weight 0): "Needs ID." | The names do not settle it: ask for a UEN or ID. Never a match. It was a `low` flag until the clean-bundle false alarms below; as a note it moves neither the score nor the lane. |
| `different_entity` | none | Counted in the claim counts only. |

**Calls per case.** 0 when every counterparty is matched by code or shares nothing with a declared party (the check runs with no flags); otherwise `ceil(pairs / 8)`, 1 for most cases and at most 3. Output is about 150 to 200 tokens per pair with thinking off. A failure of any batch makes the whole check not run (error, case partial); so does a batch in which no pair got a verified answer (an empty reply, or every claim dropped), because the prompt asks for one claim per pair; earlier batches of the same case are discarded, and the response cache makes the retry cheap. `kept` in the claim counts includes `different_entity` answers; the number of flags is in the check run.

**For evaluation.** `adjudicate_name_pairs(ctx, [NamePair(id, a, b)])` runs bare name pairs (the ENTITY set) through the same prompt, schema and verification, as a statement line (A) and a director row (B) of two small synthetic documents. It does not apply the code matchers first; the caller decides which pairs to send, and should report the code matchers' own accuracy on the same pairs next to the model's.

**Limits.**

* Candidates need a shared token. A transliterated name in another script (for example a Chinese-character counterparty against a Latin declared name) shares none, so it is never asked; the model could do it, the generator cannot propose it.
* The model sees two strings and nothing else (no addresses, no amounts, no dates), by design. Many genuine variants are `cannot_tell`.
* `cannot_tell` flags fall on names that are ambiguous, which can differ by naming convention; the name-group disparity of the flag rate with the feature on is measured in section 10, not assumed.
* A verified claim proves the quotes are in the cited cells, not that the verdict is right.
* The labels of our ENTITY set treat a holding company and its subsidiary as different entities; a bank may treat related entities as one customer. Dev-seed results are in the subsection "Dev-seed tuning (5101)" below; the held-out run was done once afterwards (section 10). The ENTITY check is not adopted: its name-group flag-rate disparity rose from 0.00 to 0.33 (invariant 6).

### Dev-seed tuning (5101)

Dev split only, as it was when the prompt was tuned: 384 company-name pairs, 192 same entity and 192 different (24 per class). The pairs now in `samples/semantic/dev/entity.json` differ from these in about a third of the pairs (111 to 135 of 384, depending on how a pair is matched), so these tables cannot be reproduced from the shipped file (section 9). Every pair is sent to the adjudicator in batches of 8 through the product path (`adjudicate_name_pairs`: same prompt, schema and verification as the check), local model, thinking off, temperature 0, response cache off, 48 calls per run. A pair without a verified answer counts as not merged (the check would raise nothing for it). Intervals are Wilson 95%. The labels are ours and the data is hand-written, so every number is an upper bound.

For comparison, the string matcher the rules use (`match_company`, verdict `same` or `probable` counted as a match) on the same 384 pairs: recall 88/192 = 0.458 [0.389, 0.529], false merges 32/192 = 0.167 [0.121, 0.226] (12 jurisdiction/legal-form pairs, 13 initial conflicts, 7 holding/subsidiary pairs).

| Round | Change | Recall (same_entity on same pairs) | False merges (same_entity on different pairs) | Claims dropped | cannot_tell (same / different) |
|---|---|---|---|---|---|
| R0 | the built prompt and schema | 84/192 = 0.438 [0.369, 0.508] | 13/192 = 0.068 [0.040, 0.112] | 192 of 384 `bad_shape` | 0 / 2 |
| R1 | citation locator keys (document, table, row, column, field) required in the pair schema; the prompt lists what counts as the same entity (punctuation, suffix spelling or missing suffix, word order, abbreviations, place written two ways, a one-letter slip that leaves a non-word) and what does not (other brand, other line of business, holding or group company, similar real words, other given name, any differing initial, two different legal forms or places) | 169/192 = 0.880 [0.827, 0.919] | 1/192 = 0.005 [0.001, 0.029] | 1 (`quote_not_found`) | 1 / 0 |
| R2 | Malay descriptor word list; dialect-to-Pinyin syllable list; "Pte Ltd and Corp" is two forms; a one-letter difference in a person's name is `cannot_tell` | 174/192 = 0.906 [0.857, 0.940] | 0/192 [0, 0.020] | 1 | 3 / 0 |
| R3 (frozen) | one-letter slip rule widened to any dropped, added, doubled, swapped or wrong letter in a brand word; more abbreviations; "Pengurusan" | 178/192 = 0.927 [0.881, 0.956] | 0/192 [0, 0.020] | 2 | 0 / 0 |

How to read it. R0 was a measurement of the built code, not of the idea: the shared claim schema leaves the citation locator optional, and with 8 pairs per call the model left it out in half the claims (192 of 384 dropped as `bad_shape`), so only the half that kept it was scored; the first attempt at R0 was stopped by the per-case money guard (the local model has no price table, so the guard uses a high placeholder rate) and was repeated with the guard raised. The pair schema now requires every locator key (null when it does not apply). Most of the R0 to R1 gain is that fix, the rest is the written-out convention. R2 and R3 are the glossary rounds (three were allowed). **The glossaries were written after reading dev errors and the generator uses the same vocabulary on both splits, so the transliteration and abbreviation numbers are probably the most inflated; the held-out split will show how much transfers.** R3 is the last tuning round.

Per class at R3 (24 each): same pairs `abbreviation` 24, `initialism` 24, `legal_suffix` 24, `region_marker` 24, `word_order` 23, `combined` 23, `transliteration` 19, `typo` 17; different pairs all 24 `different_entity` except `given_name_differs` 23 (one pair without a verified answer). Left at R3: 14 same pairs judged `different_entity` (six typos such as a doubled letter in the brand word, five dialect/Pinyin or Malay-descriptor pairs, one reordered partner pair, one combined, one without answer).

**Variance (frozen prompt, 3 more runs).** V1 repeated R3 exactly and produced the same 384 verdicts (temperature 0, same batches). V2 and V3 changed which pairs share a call (seeded shuffle): recall 174/192 and 181/192; false merges 0 and 1 (a "Limited" against "Pte Ltd" pair). Across the four runs 21 pairs changed verdict at least once (mostly typo, transliteration and one-off legal-form pairs), recall 174 to 181 of 192, false merges 0 to 1. Pooled over the three distinct batchings: recall 0.925, false merges 1/576 = 0.002 [0.000, 0.010]. The model's one-sentence reason is sometimes unrelated to its verdict (a "doubled letter" reason for a legal-suffix pair), which is why the sentence is never used as evidence.

**Product path on the same pairs** (only pairs no code matcher links and that pass the candidate generator are asked): 232 of 384 are asked (all 104 same pairs the matcher misses reach the model; 32 different pairs share no word and are never asked); on those, recall 96/104 = 0.923, false merges 0/128, `cannot_tell` on a different pair 0/128. Code recall OR model recall: 184/192 = 0.958. In the product a `same_entity` verdict is an info flag only, so the recall above is a measure of the reading, not a match that any rule uses.

**Cost.** 48 calls, 111,288 prompt and 57,166 completion tokens, about 1,070 s for 384 pairs on the local model (about 22 s a call; roughly 2,300 prompt and 1,190 completion tokens per call); USD 0. Through the pipeline on the 33 clean digital bundles: 1.09 calls per case on average (0 to 2), about 2,270 prompt and 840 completion tokens, 16 s extra per case. No paid model was used.

**Pipeline checks, dev bundles (feature on against off; the entity check is the only semantic check switched on).**

* 33 clean digital bundles: flags at medium or above 0 off, 0 on; no non-semantic flag changed in any bundle; no case partial; the check ran in all 33. It raised a flag in 10 bundles (34 `low` "needs ID" flags, 2 `info`), added 5 to 15 points to 9 scores and took 2 bundles out of the fast lane (15 of 33 fast off, 13 on). All come from three causes seen in the pairs: (1) the candidate generator pairs a person's short name token with a company descriptor that contains it as a letter subsequence (`ming` with `manufacturing`, `ting` with `trading`) and the model answers `cannot_tell` for company against person; (2) a statement line such as `RENT 74 SIN MING ROAD` is treated as a counterparty name; (3) a truncated payer such as `SHEELA D` against `Sheela d/o Subramaniam` is a real ambiguity (the model said `cannot_tell`, once `same_entity`). None is a model error about a close pair; they are candidate-generation defects. They were found after the prompt was frozen and are not fixed in this round (three rounds were used); fixing the generator does not change the prompt hash.

  **Update: candidate-generator fixes (code only, prompt hash unchanged), evaluated on dev only.** A later diagnosis traced all 10 clean bundles to input defects, not model errors: (a) `extract/annotate.py` cut the counterparty at the `/` of `S/O`, `D/O`, `A/L`, `A/P` (`RAJESH S/O KUMAR` became `RAJESH S`; 7 of the 10 bundles, all with an Indian-style patronymic name); (b) the letter-subsequence rule paired companies with persons (3 of the 10); (c) a line with no payment channel and no legal form was treated as a name; (d) one person who is director, shareholder and declarant was asked about up to three times, which tripled the flags and the score. Fixes: the slash stays in the patronymic markers; a company-shaped name (legal form or UEN) is not paired with a natural person; a line needs a payment channel or a legal form to count as a name; one pair per distinct declared name; `cannot_tell` is `info`. On the 33 dev clean bundles (Qwen, 27 calls) bundles with a low flag went from 9 to 0 and the fast lane stayed at 15. That is a dev result on bundles that were already used to find the defects: it is not a confirmation. The held-out bundles of this feature were read during the diagnosis and are spent for entity-linking design; a one-shot run on a fresh seed, balanced by name group, is the test. Also fixed, found by the Haiku run below: the answer rules say "field null", which is wrong for a declared name that lives in a field (a contract's buyer, a declared customer). Qwen filled the field in anyway; Haiku obeyed literally, cited those cells with no locator, and every claim of 12 of 60 bundles was dropped as `bad_shape` (the check errored and 6 bundles lost the fast lane through lower coverage). `verify_pairs` now gives a citation that names one of the pair's documents and has no field and no table the field of that side, which is our own locator printed in the sheet; the quote is still checked against the cell, and the prompt (and its hash) is unchanged. Not fixed: `_cites` accepts a 3-character quote.

  **One-shot run on a fresh seed (8801, never used before; 60 clean digital bundles, 20 each of KYC, AML and loan; entity check only, everything else as in the default evaluation).** Scores, lanes and every non-semantic flag were identical with the check on and off in all 60 bundles, for both models, and none was partial.

  | | Qwen 3.8 27B (local) | Claude Haiku 5.5 |
  |---|---|---|
  | check ran | 60/60 | 60/60 (48/60 before the locator fix) |
  | pairs asked / model calls | 215 / 51 | 215 / 51 |
  | `different_entity` | 209 | 205 |
  | `same_entity` | 4 | 4 |
  | `cannot_tell` | 2 | 6 |
  | flags | 6, all `info` | 10, all `info` |
  | `low` or higher flags | 0 | 0 |

  The 4 `same_entity` pairs are the same in both: a director-loan line (`GANESH S/O NAIR DIRECTOR LOAN`, `KARTHIK S/O IYER ...`, `DEEPA D/O IYER ...`) and the declared director; they are correct, and they are the lines that caused the false alarms before the fixes. The `cannot_tell` notes are pairs of different companies that share a brand word (`ZENITH SUPPLIES PTE LTD` and `Woodlands Zenith Pte. Ltd.`, `Silverline Imports` and `Silverline Supplies`); a human would also want an ID there. Haiku spent $0.043 on the final run and about $0.13 on the three runs it took to find and check the locator defect (about $0.126; computed from the repo price table, not an invoice).

  **Haiku canaries for the other two features (dev data, one run, not an evaluation).** Transaction labels, through the product code on 96 dev rows (87 ordinary, 9 hard counterparties): 4 calls, 0 failed, 0 claims dropped, every row labelled. Against the dev truth mapped onto the ten labels, 74 of 87 ordinary rows matched; 8 more are `gambling` or `crypto` labels on counterparties whose names say bet, lottery, crypto or blockchain, which the dataset's own rule makes correct (a judgement made after seeing the output, so read it as about 0.94 and not as a clean figure). The real misses are the Malay `kredit faedah` (interest credit), read as `loan` in 5 rows, and one property company read as `rent`. The 9 hard-counterparty rows (company names that give nothing away) were labelled `supplier_trade` or `transfer_individual`, as expected. Fit, through the product code on 24 dev cases (8 per question family, clean and tampered): 0 errors, 12 of 12 clean cases quiet, 10 of 12 tampered cases flagged (income 4/4, goods 3/4, coherence 3/4). The cost meter read 0 for these runs (the calls are not charged to a stage in this harness), so the spend is not measured; it was 28 calls on a model that costs a few thousandths of a dollar per call in the runs above.

  What this does not show: (1) it is not a fairness result, because the generator's clean pool barely varies the name style (4 of the 60 bundles carry an Indian patronymic name and none a Malay one), so a balanced six-group comparison still needs bundles built for it; (2) no tampered bundle was run, so it says nothing about whether a `same_entity` or `cannot_tell` note helps a reviewer catch anything, and no rule reads these notes; (3) one run per model, 60 bundles that share a small set of declared names, so the 4 `same_entity` pairs are the same four strings repeated; (4) real statements may print counterparties in formats the parser does not know.
* The 42 downstream bundles (kyc, loan and aml deck examples and the T05, T13, T15, T59 tampers): every non-semantic flag identical off and on, the same 112 flags at medium or above in the same 42 bundles, 15 bundles with an added low or info flag, no lane change. The entity check detected none of these tampers by itself; they are caught by the existing rules as before.
* Fairness slice (270 KYC name-pair bundles, six name groups): these bundles carry no statement or invoice, so the check is skipped ("no usable document of type bank_statement/invoice") and nothing changes: flags, scores and lanes identical in all 270, medium-or-above rate 15/45 in every group on and off (disparity 0.00 both ways). Separate probe of the adjudicator on the 270 name pairs of the slice, taking the product path (a pair is asked only if `match_person` does not link it and the two names share a word): 56 pairs reach the model, all near-spelled *different* people; 54 are `cannot_tell` (low flag) and 2 are `same_entity` (both Chinese-order names that differ by one vowel: `Lim Jian Hui`/`Lim Jian Huy`, `Chan Cheng Wei`/`Chan Cheng Wai`), so the instruction to answer `cannot_tell` for a one-letter difference in a person's name is not followed every time. The low-flag rate per group (per 45 pairs) is chinese_order 13, indian_patronymic 9, malay_patronymic 12, titles 15, western_order 5, mononym 0 (max minus min 0.33); per asked pair it is 87% to 100% in every group, so the spread comes from how many near-spelled pairs the generator draws for each group and reach the model, not from the model treating a group differently. These pairs are already flagged by the name matcher, so the added low flag duplicates a flag that exists. Measured against off (0 added flags), the disparity of added flags is higher with the feature on; it is reported, not argued away.

**Frozen.** Prompt `semantic:entity_adjudication` sha256 `718803f357edd496aa87e13b50c49edad388a98a7e07bbe01fd88e3bbbff0b09` (cache key part `sem1:718803f357edd496:`), semantic version 1, batch 8, 3 batches a case, 3 declared parties per counterparty. Any change to the prompt text, the answer rules, the schema or the batch size after this point invalidates the held-out run.
