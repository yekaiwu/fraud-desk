# Scoring, findings and the ranked queue

Build spec for the `score-queue` agent. Contracts come from [architecture.md](architecture.md) (sections 3, 4, 6), [data-model.md](data-model.md) (sections 3, 4) and [api.md](api.md) (section 4). Plan refs: 0.2 and 0.3 (alert overload is the stated pain), 2 principles 1 and 3, 8.3 (score), 8.4 (queue), 9.3 (prioritisation metric). Fictitious data only. Do not git commit.

## 1. Purpose

Turn the flat list of `Flag`s into what an analyst can act on: a few `Finding`s (correlated flags counted once), one `Score` with a band, a `Coverage` figure that makes silence visible, an outcome lane, a one-line reason, and a queue order that puts likely fraud at the top. The product is a triage tool first and a detector second (plan 0.2), so this is the part that is judged on ranking quality.

## 2. Scope

| In | Plan ref |
|---|---|
| Flag to finding grouping (`findings.py`) | 8.4 |
| Weights, cap, bands, partial score, coverage (`score.py`) | 8.3, principle 3 |
| Outcome lane incl. fast lane, one-line reason, `QueueItem` assembly (`queue.py`) | 8.4, 0.4 themes 1 and 4 |
| Priority ordering (as frozen in architecture.md section 6) | 8.4 |

| Out | Where |
|---|---|
| Review verdicts, dispositions, audit | [review-workflow-and-audit.md](review-workflow-and-audit.md) |
| HTTP filters, pagination, `GET /cases` | [api.md](api.md) section 4, api-backend |
| Queue and score UI | [frontend.md](frontend.md) |
| Recalibrating weights from the eval | eval agent proposes; a change is a Contract request on `SCORE_WEIGHTS` / `BAND_THRESHOLDS` |
| Cross-case entity graph, ML ranking | not built (section 9) |

## 3. Interfaces

```python
# backend/app/findings.py
SEVERITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2, "info": 3}
TITLE_MAX_CHARS = 140
REGION_OVERLAP_MIN = 0.5            # see 4.2 (region clustering)

def flag_discriminator(flag: Flag) -> str | None: ...
    # id = "<rule>.<docs>.<locator>[~<disc>][#<n>"]; strip a trailing "#<digits>", then return the text after the FIRST "~", lower-cased and stripped; None if absent/empty
def group_key(flag: Flag, case: Case) -> str: ...                    # 4.1; never raises (unknown document ids fall back to "case")
def group_flags(case: Case, flags: Sequence[Flag]) -> list[Finding]: ...   # 4.1-4.4: pure, deterministic, input order irrelevant, ids "fnd_1".. assigned in rank order
FAMILIES: tuple[GroupFamily, ...]                                       # the table in 4.1, data not code, so the catalogue test can read it
class GroupFamily(NamedTuple): prefix: str; kind: Literal["per_doc", "per_file", "shared", "bundle", "anchor", "region"]; rules: frozenset[str]
HARD_STOP_RULES: frozenset[str]                                         # 4.5
AML_ROUTING_RULES: frozenset[str] = frozenset({"x_sof_support", "x_remitter_vs_buyer", "bs_pass_through"})

# backend/app/score.py   (foundation wrote the basic version; extend, keep run_stage's signature)
def run_stage(ctx: CaseContext) -> StageOutcome: ...                    # writes ONLY case.findings, case.score, case.coverage, case.lane, case.top_finding
def band_for(value: int) -> Band: ...                                   # uses BAND_THRESHOLDS from core.models
def compute_coverage(case: Case) -> Coverage: ...                       # 4.6
def collect_partial_reasons(case: Case) -> list[str]: ...               # 4.7
def compute_score(findings: Sequence[Finding], *, partial: bool, reasons: Sequence[str]) -> Score: ...   # 4.3

# backend/app/queue.py
FAST_LANE_MAX_SCORE = 20            # starting guess, recalibrate from the eval
FAST_LANE_MIN_COVERAGE = 0.80       # starting guess, recalibrate from the eval
TOP_FINDING_MAX_CHARS = 160
def page_counts(case: Case, *, min_prob: float = 0.70) -> tuple[int, int]: ...      # (total_pages, unchecked_pages), 4.6
def effective_coverage(case: Case, *, min_prob: float = 0.70) -> float: ...         # 4.6
def lane_for(case: Case, *, min_prob: float = 0.70) -> Lane | None: ...             # 4.5
def top_finding_line(case: Case) -> str | None: ...                                 # 4.8

# backend/app/api/queue.py  (api-backend; R0 moved it there so api-backend does not depend on a same-wave module)
def build_queue_item(row: sqlite3.Row, *, now: datetime, pending_second_approvals: int = 0) -> QueueItem: ...   # 4.9
```
`run_stage` also fills `Coverage.pages_total` and `Coverage.pages_unchecked` from `page_counts` (data-model.md 4).

`run_stage` reads `ctx.case.flags`, `ctx.case.check_runs`, `ctx.case.documents`, `ctx.case.files`, `ctx.case.stages`, `ctx.settings.classifier_min_prob`. It returns `StageOutcome(detail={"flags": n, "findings": m, "score": v, "applicable": a, "ran": r}, warnings=[...], partial=False)`. It never raises for odd input; a flag whose document id is unknown only produces a warning.

## 4. Behaviour spec

### 4.1 Grouping: flag to finding

Principle: **one underlying cause is one finding, and a finding is counted once** (plan 8.4). Every flag belongs to exactly one finding (invariant, tested). Info flags are grouped too (weight 0) so the analyst sees them.

Helpers:
- `subject(flag)` = sorted `flag.document_ids` joined with `+`; if empty, `flag.region.file_id` when a region exists, else `"case"`.
- `file_key(flag, case)` = sorted set of `Document.file_id` for `flag.document_ids` (looked up in `case.documents`), joined with `+`; if none resolve, `region.file_id`, else `"case"`.
- `disc` = `flag_discriminator(flag)`. Checks set `make_flag(discriminator=...)` to a normalised entity key (lower-case, punctuation removed, tokens sorted: the form from `app/names.py`); the scorer lower-cases and strips again, never fuzzy-matches.

Algorithm (`group_key`), first match wins:

| # | Rule set (family prefix, kind) | Key | Why |
|---|---|---|---|
| 0 | `ela_region`, `copy_move` flag with severity `low` (a lone, uncorroborated raster region) | `noisy:<rule>` (whole bundle) | a clean scan or photo makes these fire page after page; one noisy check counts once (plan 4.5). A region a second detector corroborates is `high` and goes to row 13 |
| 1 | `stmt_arith` per_doc: `bs_totals`, `bs_running_balance` | `stmt_arith:<subject>` | one edited amount breaks both identities |
| 2 | `inv_arith` per_doc: `inv_lines`, `inv_gst`, `inv_total` | `inv_arith:<subject>` | one edited line breaks several totals |
| 3 | `fs_arith` per_doc: `fs_balance`, `fs_subtotals`, `fs_cash_tie` | `fs_arith:<subject>` | same figure seen by several identities |
| 4 | `id_doc` per_doc: `id_format`, `id_checksum`, `passport_mrz`, `date_valid` | `id_doc:<subject>` | one altered ID document |
| 5 | `meta` per_file: `pdf_editor_software`, `pdf_modified`, `pdf_producer_unexpected`, `image_editor_software`, `image_modified` | `meta:<file_key>` | metadata of one file is one observation |
| 6 | `holder` shared: `x_ownership_vs_registry`, `kyc_shareholdings` | `holder:<disc>` if disc else default | the same missing or wrong shareholder seen on chart, register and profile (plan 8.4 example) |
| 7 | `director` shared: `x_directors` | `director:<disc>` if disc else default | same director absent from the registry seen on several documents |
| 8 | `entity` shared: `x_entity`, `fs_entity`, `bs_holder` | `entity:<disc>` if disc else default (disc = `company_disc`/`uen_disc` of the foreign company, checks-financial.md 3.4) | the bundle names a different company: one fact |
| 9 | `remitter` shared: `x_remitter_vs_buyer` | `remitter:<disc>` if disc else default | the same paying party across several credits |
| 10 | `dup` shared: `inv_duplicate`, `signature_reuse`, `file_reuse` | `dup:<disc>` if disc else default | both copies of a duplicate are flagged; disc = fingerprint or file hash (first 12 hex), crop hash (8 hex), or `store:<case_id>` |
| 11 | `pattern` bundle: `x_invoice_vs_receipts`, `inv_sequence`, `bs_round_numbers`, `bs_benford`, `font_mix` | `pattern:<rule>` | a bundle-wide pattern, not N independent frauds; stops 10 unpaid invoices capping the score alone |
| 12 | `anchor` anchor: `x_invoice_vs_contract` | `anchor:<rule>:<id of the first document in flag.document_ids whose doc_type is contract or purchase_order>`, else default | one disputed contract, many invoices |
| 13 | `region` region: `pdf_text_vs_render`, `font_span_outlier`, `ela_region`, `copy_move`, `noise_mismatch` | `region:<file_id>:p<page>:<n>` from 4.2; default when the flag has no `region` | two detectors pointing at the same pixels are one finding |
| 14 | anything else | **default** `rule:<rule>:<subject>` | the same rule on the same documents (for example several `bs_dates` rows) is one finding |

Consequences to state in the UI help text: the *rows* behind a finding are all listed under it; two separate row-level breaks of the same rule on one document count once (honest limit, section 9).

### 4.2 Region clustering (family 13)

For flags in family 13 that carry a `region`: bucket by `(region.file_id, region.page)`. Inside a bucket two flags are linked when `overlap(a, b) >= REGION_OVERLAP_MIN`, where `overlap = intersection_area / min(area_a, area_b)` (containment counts as 1.0). Connected components are clusters; number clusters per bucket `n = 1, 2, ...` ordered by (min y, min x) of the union box. `extra_regions` are ignored. Boxes that only touch have overlap 0.

### 4.3 Finding fields and ordering

For each group, `members` = flags sorted by `(SEVERITY_RANK[severity], flag.id)`; `rep = members[0]`.

| Field | Value |
|---|---|
| `group_key` | from 4.1 |
| `severity` | `rep.severity` (max over members). **No corroboration bump here:** raising severity when two forensic checks agree is the forensics rules' job (plan 4.5), the scorer only takes the max |
| `weight` | `SCORE_WEIGHTS[severity]` (import the dict from `core.models`; never copy the numbers) |
| `flag_ids` | member ids in `members` order |
| `document_ids`, `rules` | sorted unique unions |
| `title` | `sentence(rep.explanation)` plus a suffix, capped at `TITLE_MAX_CHARS` with `…` at a word boundary: `" (seen on N documents)"` when `N = len(document_ids) > 1`; for `pattern` groups with more than one member `" (+k similar)"` with `k = len(members) - 1` instead |
| `summary` | `f"{rep.explanation} Evidence: {rep.evidence}"`, whitespace collapsed, capped at 300 |
| `hard_stop` | 4.5 |

`sentence(text)`: collapse whitespace (`" ".join(text.split())`), cut at the first match of `(?<=[.!?])\s+(?=[A-Z])` (so "2.4M" is not split), drop a trailing `.`. Extracted text is untrusted: the result is plain text and the UI renders it as text only (architecture.md section 11).

Ranking and ids: sort findings by `(SEVERITY_RANK, not hard_stop, -len(document_ids), -len(flag_ids), group_key)`, then assign `id = "fnd_1"`, `"fnd_2"`, ... in that order, so `fnd_1` is always the top finding and ids are stable for identical flags. A rerun with different flags may renumber; review entries are tied to `case_version` for that reason.

### 4.4 Weights, cap, bands (plan 8.3)

`SCORE_WEIGHTS = {high: 30, medium: 15, low: 5, info: 0}`, `BAND_THRESHOLDS = {medium: 30, high: 60}` (frozen in `core/models.py`). `score.value = min(100, sum(f.weight))`. `band_for`: `value >= 60` high, `>= 30` medium, else low. No score point exists without a flag. Review entries never change any of it.

| Findings | value | band |
|---|---|---|
| none | 0 | low |
| 1 high | 30 | medium (one high flag alone is not "High") |
| 1 high + 1 medium | 45 | medium |
| 2 high | 60 | high |
| 4 medium | 60 | high |
| 1 medium + 3 low | 30 | medium |
| 29 vs 30 vs 59 vs 60 | band low / medium / medium / high | boundaries |
| 4 high | 100 (cap, raw 120) | high |

### 4.5 Hard stops and outcome lanes

`HARD_STOP_RULES = {pdf_bytes_after_signature, pdf_text_vs_render, pdf_editor_software, signature_reuse, copy_move, inv_duplicate, file_reuse, bs_pass_through, x_declared_vs_activity, x_remitter_vs_buyer, x_sof_support, x_ownership_vs_registry, x_directors, id_checksum, passport_mrz}` (15 rules; `file_reuse` added at R0; only its medium cross-applicant flags make it a hard stop, low/info never do because the rule below needs medium or high).
`Finding.hard_stop = severity == "high" or (severity == "medium" and set(rules) & HARD_STOP_RULES)`. A hard stop has no effect on the score; it only blocks the fast lane (so a future weight recalibration cannot let a forged ID into the fast lane). With check catalogue overrides (section 10a) the formula is applied to the original (code) severity as well as the effective one, so an override can never remove a hard stop.

`lane_for(case)`:
1. `case.score is None` (still processing or errored before scoring) gives `None`.
2. `base` = `{"kyc": "edd", "aml": "aml", "loan": "loan"}[case.use_case]`. If any finding with `weight > 0` has a rule in `AML_ROUTING_RULES`, `base = "aml"` (suspected laundering routes to AML whatever the bundle type; plan 7.3).
3. If `fast_lane_eligible(case)` return `"fast"`, else `base`.

`fast_lane_eligible` (all must hold): `case.status != "error"`; `case.score.partial` is False; `case.score.value <= FAST_LANE_MAX_SCORE` (20); no finding has `hard_stop`; `effective_coverage(case) >= FAST_LANE_MIN_COVERAGE` (0.80). The fast lane is a **label** for "look at this last": a person still decides, `approve` still needs a reason, nothing is auto-approved (plan 0.4 theme 1, principle 2).

Lane to deck outcome (plan 0.5, 7.3): `edd` = refer for EDD review, `aml` = escalate for AML investigation (STR assessment), `loan` = reject or escalate, `fast` = quick confirmation. Lanes are a recommendation shown beside the verdict form; they never preselect a verdict.

### 4.6 Coverage (plan principle 3)

Definition (frozen in architecture.md section 4): `applicable` = checks whose `use_cases` contain the case's use case; a check *ran* if its document requirements were met, it did not raise `CheckSkipped` or another error. A missing document therefore lowers coverage. `compute_coverage`:
- `applicable = len(case.check_runs)`, `ran = count(status == "ran")`, `ratio = round(ran / applicable, 4)`.
- `skipped = [{"rule", "status", "reason"} for each run with status != "ran"]`, `reason = run.reason or ""`, sorted by `rule`. A check switched off in the check catalogue adds a fourth key `code: "switched_off"` (section 10a); no other skip has it.
- Empty `check_runs`: `ratio = 1.0` if `not case.partial`, else `0.0` (a crashed rules stage must never read as full coverage). See Contract requests.

Page-level silence (plan 5.3: pages labelled `other` or `boilerplate`, or with low probability, reduce coverage): `page_counts` returns `(total, unchecked)`. `total = sum(len(f.pages))`. A page is *unchecked* if any of: it lies in a `Document` with status `received_not_checked` or `failed`; `PageInfo.error` is set; its page classification probability is below `min_prob` (`GLDB_CLASSIFIER_MIN_PROB`, 0.70); its classification label is `boilerplate` or `other`; it belongs to no document, or it is a piece of a bank statement that arrived page by page and could not be assembled (reason `statement_incomplete`, ingest-and-extraction.md 4.11; the score is also partial with reason `statement_incomplete:N`). `effective_coverage = ratio * (1 - unchecked / total)` (`ratio` when `total == 0`). `Coverage.ratio` itself stays pure `ran / applicable`; `effective_coverage` feeds the fast-lane gate only. The UI shows both numbers ("18 of 21 checks ran, 3 of 14 pages not checked"). Coverage is unweighted: it counts checks, not their importance.

### 4.7 Partial score

`collect_partial_reasons(case)`: start with `case.partial_reasons`, then append in this order, de-duplicating and keeping first occurrence, capped at 20 entries:
`stage_failed:<stage>` for each `StageRecord.status == "failed"`; `check_error:<rule>` for each check run with status `error`; `document_failed:<id>` and `document_partial:<id>` for documents with those statuses; `pages_unreadable:<n>` when `n > 0` pages have `PageInfo.error`. If `case.partial` is true and the list is empty, use `["partial: reason not recorded"]`.
`score.partial = bool(reasons) or case.partial`. Meaning: **the value is a floor**. Checks that did not run can only add flags, so a partial score of 0 is never evidence of a clean case. Consequences: partial cases are never fast lane; UI prefixes the score with `≥`; the band chip reads "Low (partial)".

### 4.8 One-line reason (`Case.top_finding`)

`top_finding_line(case)`; `None` when `case.score is None`. Let `nonzero = [f for f in case.findings if f.severity != "info"]` (already in rank order).
1. `nonzero` empty and `score.partial`: `f"No flags so far, analysis incomplete: {first partial reason}"`.
2. `nonzero` empty: `f"No flags raised; {ran} of {applicable} checks ran"` plus `f" ({k} informational note{'s' if k != 1 else ''})"` when `k > 0` info findings exist. Coverage is in the sentence on purpose: silence is not safety.
3. otherwise `title = nonzero[0].title`; `extra = len(nonzero) - 1`; line = `title` plus `f" (+{extra} more)"` when `extra > 0`; truncate the *title* (with `…`) so the whole line is at most `TOP_FINDING_MAX_CHARS` (160).
A full stop after a company-form or title abbreviation (`Pte.`, `Ltd.`, `Co.`, `Mr.`, `No.` and similar) does not end the sentence, so a title keeps `Kestrel Capital Holdings Pte. Ltd.` whole (integration-kyc seam fix, `findings.sentence`). Examples: `Revenue 2.4M on the financial statements but only 0.9M of trade credits in the bank statements (+3 more)`; `No flags raised; 21 of 21 checks ran`. The text comes from rule authors' explanations, so each rule's first explanation sentence must stand alone with its key numbers (see Contract requests).

### 4.9 Queue item and ordering

`build_queue_item(row, now, pending_second_approvals)`: `row` is a `cases` row (`Database.list_cases`). Scalar fields come from the denormalised columns; `coverage` (applicable, ran, ratio, `skipped=[]`) and `file_count = len(files)` come from parsing `row["body"]`; `age_seconds = max(0, int((now - created_at).total_seconds()))` (`now` is passed in by the API: this is the only wall-clock use). `score`, `band`, `coverage`, `lane`, `top_finding` are `None` while `status == "processing"`. `findings_count` = findings with severity != info, `high_count` = findings with severity high (definition for `Database.save_case`, see Contract requests). `partial` = `row["partial"]`.

Order (`sort=priority`), frozen in architecture.md section 6, restated for the SQL author (`id ASC` is added only to make pagination stable):

```sql
ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'processing' THEN 1 WHEN 'awaiting_documents' THEN 2
                     WHEN 'closed' THEN 3 ELSE 4 END,
         score DESC NULLS LAST, coverage_ratio ASC NULLS LAST, created_at ASC, id ASC
```
Reading it: open work first; highest score first; at equal score the case we know *less* about first; then oldest first. Fast-lane cases fall to the bottom of the open block by construction (low score). Partial cases get no boost: their score is a floor, and the pipeline's job is to make partial rare. The eval measures this exact order (plan 9.3): the harness either reads it from `Database.list_cases(sort="priority")` or sorts its cached results by the same key, and a test proves the two orders equal on a fixture (test-data-and-eval.md 4.5).

## 5. Files owned

`backend/app/findings.py`, `backend/app/score.py` (extends foundation's), `backend/app/queue.py`, tests `backend/tests/test_findings.py`, `test_score.py`, `test_score_basic.py` (may update foundation's), `test_queue.py`, `test_scoring_properties.py`. Reads `core/**`, never edits it. `build_queue_item` (4.9) and its test 21 belong to api-backend (`api/queue.py`, `backend/tests/test_api_queue.py`).

## 6. Test plan

All tests use `app.core.testing` (`make_ctx`, `make_doc`, `FIXED_TODAY`); flags are built with `make_flag` or small helpers; no network, no wall clock.

| # | Case | Expect |
|---|---|---|
| 1 | **Clean case**: no flags, 21 of 21 checks ran, all pages checked | `value 0`, band `low`, `findings == []`, `lane == "fast"`, `top_finding == "No flags raised; 21 of 21 checks ran"` |
| 2 | Same missing shareholder on chart, register and profile (3 `x_ownership_vs_registry` flags, same discriminator) | 1 finding, `weight 30`, 3 `flag_ids`, `document_ids` has 3 ids, title ends `(seen on 3 documents)` |
| 3 | Same three flags but no discriminator | 3 findings (graceful degradation), score 90 |
| 4 | `bs_totals` + `bs_running_balance` on d3; then the same two rules on d3 and on d4 | 1 finding `stmt_arith:d3`, 30; then 2 findings (`stmt_arith:d3`, `stmt_arith:d4`), 60 |
| 5 | 10 `x_invoice_vs_receipts` flags (medium) on 10 invoices | 1 finding `pattern:x_invoice_vs_receipts`, 15, title ends `(+9 similar)` |
| 6 | `x_invoice_vs_contract` on 3 invoices citing contract d7, plus 1 citing d8 | 2 findings (`anchor:...:d7`, `...:d8`) |
| 7 | `ela_region` and `copy_move` boxes on one page with overlap 0.6, 0.5, 0.49 and 0.0 | merged, merged, separate, separate (1, 1, 2, 2 findings) |
| 8 | Cap: 4 independent high findings | `value 100`, band high |
| 9 | Boundaries: 29, 30, 59, 60 (built from weights) | low, medium, medium, high |
| 10 | Info only (`doc_unclassified`) | `value 0`, `findings_count` column 0, top line `No flags raised; ... (1 informational note)` |
| 11 | **Partial**: one `check_runs` entry `status="error"` and one failed stage | `score.partial`, reasons `stage_failed:rules` and `check_error:<rule>` in that order, `lane != "fast"`, band label unchanged but `partial` true |
| 12 | Empty `check_runs` with `case.partial=True`, then with `partial=False` | coverage `ratio 0.0`, then `1.0` |
| 13 | Hard stop: one medium `pdf_editor_software` only (score 15) | `hard_stop True`, lane is the use-case lane, not `fast` |
| 14 | Low score with check coverage 0.75; low score with check coverage 0.95 and 3 of 14 pages unchecked (effective 0.747) | neither is `fast` |
| 15 | `x_remitter_vs_buyer` finding in a `kyc` case; then a `kyc` case with only a medium `x_entity` finding | `lane == "aml"`; then `"edd"` |
| 16 | Determinism: shuffle flags 20 times | identical findings, ids, `top_finding` |
| 17 | Idempotence: `run_stage` twice on one ctx | equal case outputs, no duplicates |
| 18 | Property: for random flag sets, every flag is in exactly one finding; `min(100, sum(finding.weight)) == score.value`; no finding with weight > 0 lacks flags | holds |
| 19 | `test_families_reference_registered_rules`: after `discover()`, every rule named in `FAMILIES`/`HARD_STOP_RULES` exists in `all_checks()` | catches renamed rules (skip-with-warning for rules not yet merged in an early wave) |
| 20 | Top line: title of 200 chars, 3 other findings | line ≤ 160 chars, ends `(+3 more)`; title with "2.4M" is not cut at the dot |
| 21 | `build_queue_item`: processing row | `score None`, `lane None`, `coverage None`; `age_seconds` never negative |
| 22 | Priority fixture (6 rows via `Database.list_cases`): open 80, open 80 with lower coverage, open 40, processing, closed 99, awaiting 90 | order: open80-lowcov, open80, open40, processing, awaiting, closed |
| 23 | AST test (like `test_no_wallclock_in_checks.py`) on `findings.py`, `score.py`, `queue.py` | none calls `date.today`, `datetime.now`, `datetime.utcnow`, `time.time` |
| 24 | **Honest quirk**: careful-forger bundle (consistent recomputation, no rule fires) | scores 0 and may be `fast`; recorded as a miss in the eval, not hidden; the test asserts the code path, the eval row reports it |

## 7. Acceptance criteria

1. Tests above pass with `uv run pytest backend/tests/test_findings.py backend/tests/test_score.py backend/tests/test_queue.py backend/tests/test_scoring_properties.py` and `uv run ruff check .`.
2. On the three demo bundles (plan 0.5) the findings named there each appear as their own finding; scores land in the high band for the fraud variants and at least medium for the forger variants (loan-forger has three medium findings, 45 points, by design); clean controls get band low and lane `fast` or their use-case lane (fast depends on the coverage threshold recalibrated from the eval). Verified by integration agents, reported by the eval.
3. `score.value`, `band`, `lane`, `top_finding` are identical across two runs of the same case with the same `today`.
4. No number in `score.py` duplicates `SCORE_WEIGHTS` or `BAND_THRESHOLDS`.
5. Every plan 0.6 item here has a test or a line in section 9.

## 8. Recalibration (plan 8.3, 9)

Weights, thresholds, `FAST_LANE_*` and `REGION_OVERLAP_MIN` are starting guesses. The eval reports ranking (fraud cases in top 5 and top 10 of about 30 generated cases, with the fraud share stated), false alarms per 10 clean bundles and the fast-lane miss count; changes go through a Contract request.

## 9. Known gaps and honest limits

- Weights and bands are guesses until the eval runs; the score is an ordering signal, not a probability.
- Coverage counts checks, unweighted; 18 of 21 can still hide the one check that mattered. The skipped list is shown for that reason.
- `Coverage.ratio` is not reduced by unchecked pages; only the fast-lane gate uses `effective_coverage`. The queue and case page show page silence separately from `Coverage.pages_total`/`pages_unchecked`.
- On an all-digital bundle the image-only forensic checks (`ela_region`, `copy_move`, `noise_mismatch`, `image_*`) count as skipped, so check coverage of a clean digital bundle is below 1.0 by design; `FAST_LANE_MIN_COVERAGE` is recalibrated from the eval's measured clean coverage (R0 decision, architecture.md 4).
- Grouping by rule list needs maintenance when rules are added; unlisted rules default to one finding per rule and document set, which can under-count two real row-level edits in one document and over-count nothing.
- Pattern grouping caps a large unpaid-invoice problem at one medium finding unless the rule raises its own severity.
- No corroboration credit between a row-level rule and a pixel-forensics flag on the same cell (they stay separate findings by design).
- Fast lane can contain a careful forger whose arithmetic is consistent (plan 9.4 hard case). It is a label for human confirmation, never a decision.
- No cross-case signals (the same applicant across cases, shared directors) beyond the duplicate-fingerprint store.
- Ties are broken by coverage and age only; severity mix and hard stops do not reorder equal scores.
- **Calibration decision (T-integration-kyc-07, CR-28).** Recorded: no `CheckMeta.input_kinds`; `SCORE_WEIGHTS`, `FAST_LANE_MAX_SCORE = 20` and `FAST_LANE_MIN_COVERAGE = 0.80` are unchanged after the first full eval. Evidence: on the 33 clean digital bundles check coverage is 0.76 to 0.89 (loan 0.89, KYC 0.83, AML 0.76) and 15 of 33 reach the fast lane (loan 8 of 11, KYC 7 of 11, AML 0 of 11: its lane is `aml` by routing). The three loan and four KYC bundles that stay in `loan` or `edd` have score 0; they are held back by `effective_coverage` (check ratio times the share of pages that were not left unchecked, e.g. the bank's terms-and-conditions pages) falling under 0.80, which is the intended behaviour of that gate (page silence is not safety). Deck acceptance 9 of 9 and the ranking test (fraud first top-down at every share) do not depend on the lane threshold. Lowering the 0.80 floor would move clean bundles into the fast lane without any recall evidence for it, so it stays a documented starting guess; revisit with real data (H-21).

## 10a. Catalogue overrides

Checks are defined in code. An officer can override a check in the check catalogue (api.md section 9a): switch it on or off, change its severity, and edit its display name, description and note. Every edit is logged with the reviewer, a reason, and the values before and after (`rule_changes`, append-only), and can be reset to the code defaults. The change log is the evidence for "change control" and "validation before use" of such changes (regulatory-mapping.md O5 and O13). A second approver is not required. The engine rules:

1. **Timing.** `run_pipeline` reads the overrides once when an analysis starts (`CaseContext.overrides`; one query per run). A change applies to analyses run after it. A stored result keeps the settings it was analysed under until it is re-run, and a re-run uses the current settings. `ruleset_version` is about code identity and does not change.
2. **Severity.** After a check returns its flags, `run_stage_checks` sets every flag's severity to the override severity (if one is set); the severity the check produced is kept in `Flag.code_severity` (only when it differs). Findings, `SCORE_WEIGHTS`, the score, severity counts (`high_count`), the queue and the effectiveness view then follow the effective severity with no other change.
3. **Hard stops never loosen.** The rule of 4.5 is evaluated on both severities: `Finding.hard_stop = default_hard_stop(original) or default_hard_stop(effective)` where `default_hard_stop(sev)` is `sev == "high" or (sev == "medium" and set(rules) & HARD_STOP_RULES)` and `original` is the best (`high` first) `code_severity or severity` of the finding's flags, i.e. what the defaults would have produced. Lowering a severity reduces points but never removes a block, so it cannot let a case into the fast lane that the default configuration would have shut. Raising a severity can add a hard stop, by the default logic on the effective severity (a plain rule raised to `high`). Example: `x_directors` (medium, hard-stop rule) lowered to `low` keeps `hard_stop = true` and counts 5 points (`low`) instead of 15 (`medium`).
4. **Switched off.** A disabled check is not executed. Its `CheckRun` is `skipped` with `code = "switched_off"` and `reason = "Switched off in the check catalogue by <reviewer> on <date>: <reason>"`. It is still applicable, so `coverage.applicable` is unchanged, `ran` and `ratio` drop, and it is listed in `coverage.skipped` with the extra key `code: "switched_off"` (no other skip has that key): silence is never mistaken for safety. A check that could not run anyway (missing document) keeps its own reason. To stop a switch from loosening the fast lane, `fast_lane_eligible` also requires that no hard-stop check (a rule of `HARD_STOP_RULES`) is switched off, even when `effective_coverage` is still 0.80 or more.
5. **Provenance.** With at least one override row at analysis time `provenance.catalogue = {"overrides": [{"rule", "enabled", "severity", "revision"}...sorted by rule], "hash": sha256}`; without overrides the key is absent and every hash is byte-identical to before.
6. **Display text.** Name, description and note are display-only: stored and returned by the API, never fed to a model or interpolated into prompts, flags or evidence.
7. **Statistics.** The effectiveness endpoint and rule statistics still key by rule id; a switched-off rule shows no run (`cases_ran` stops growing, `cases_skipped` grows) after the switch. `default_severity` there is the code default.

8. **Review controls.** Lowering a severity reduces points only; it never relaxes a review control or the fast lane. Wherever the review logic asks "is this a high flag" (`approve` blocked by `high_flags_open` while a high flag is undecided, a high dismissal needing a second approver and so entering `pending_second_approval`, the pending approvals list) it uses `is_high_flag(flag) = flag.severity == "high" or flag.code_severity == "high"` (`findings.py`). A flag that was high under the code defaults keeps those controls after an override lowers it; raising a check to `high` adds them. Scoring weights, severity counts and the queue still follow the effective severity. A UI that decides "high flag" itself must use the same test.

An override applies to the flags of the registered check runner; nothing else changes.

## 10. Explainability data

What the interface reads to explain a score, a lane and a classification. Nothing here is computed in the browser; every number comes from the engine and the explanation endpoint reads the same constants the engine uses.

| Question | Where it is answered |
|---|---|
| How is the score made? | `GET /explain/method` `score` (weights, cap, bands, counting rule); `findings[].weight` and `findings[].severity` give each finding's contribution; `score.value` is `min(cap, sum of finding weights)` |
| Why is a finding there? | `findings[].summary`, `flag_ids`, `rules`, `document_ids`; the flags carry `evidence` and `explanation` |
| What blocks the fast lane? | `findings[].hard_stop` and `GET /explain/method` `hard_stop_rules`, `hard_stop_effect`, `fast_lane` |
| Why this lane? | `case.lane`, `GET /explain/method` `lanes[]` (`how`, `suggested_verdict`), `aml_routing_rules`, `fast_lane.requires` |
| What was not looked at? | `coverage` (`applicable`, `ran`, `ratio`, `skipped[]`, `pages_total`, `pages_unchecked`) and `GET /explain/method` `coverage` (`definition`, `low_coverage_ratio`, `fast_lane_min_coverage`); `not_checked_documents` |
| What did each check do? | `check_runs[]` (`rule`, `status`, `reason`) |
| Why was a document typed this way? | `documents[].classification` (`label`, `probability`, `classifier`, `version`, `distribution`) plus `reasons` (rules cues, deterministic) and `rationale` (hosted model's own sentence, untrusted text shown as plain data; it never affects label, score, flags or lane) |
| What happened at each step? | `stages[]` (`stage`, `status`, `detail`, `warnings`) |
| Can the result be reproduced? | `provenance` (`ruleset_version`, `prompt_hash`, `classifier_versions`, `settings_hash`, `flags_digest`, `input_hashes`) |

`GET /explain/method` (api.md section 11a) returns `score`, `coverage`, `fast_lane`, `hard_stop_rules`, `aml_routing_rules`, `lanes` and `ruleset_version`. The constants behind it are `SCORE_WEIGHTS`, `SCORE_CAP`, `BAND_THRESHOLDS` (`core/models.py`), `FAST_LANE_MAX_SCORE`, `FAST_LANE_MIN_COVERAGE`, `LOW_COVERAGE_RATIO` (a display marker only), `LANE_SUGGESTED_VERDICT` (`queue.py`) and `HARD_STOP_RULES`, `AML_ROUTING_RULES` (`findings.py`). `reasons` and `rationale` are explanation only; the scoring path never reads them.

## Contract requests

**W3 closure (T-integration-kyc-08):** every request in this section has a recorded outcome in tasks.md sections 4.2 and 4.2a (accepted, decided at R0, or not built and listed under Known gaps above); none is open.

**R0 outcomes:** discriminators accepted (entity family uses `core.textnorm.company_disc`/`uen_disc`; `inv_duplicate` uses `fp[:12]` or `store:<case_id>`; `file_reuse` added to family 10); explanations accepted; `save_case` columns accepted (architecture.md 6); coverage 0.0 for a partial case with no runs accepted (data-model.md 4); `Coverage.pages_total/pages_unchecked` accepted; foundation tests: score-queue owns `test_score_basic.py`; settings: no, constants; `build_queue_item` moved to api-backend.

- **Discriminators**: rule authors set `make_flag(discriminator=<normalised key>)` as follows so grouping works: `x_ownership_vs_registry`, `kyc_shareholdings` (holder name), `x_directors` (director name), `x_entity`, `fs_entity`, `bs_holder` (the mismatching name), `x_remitter_vs_buyer` (remitter name), `inv_duplicate` (fingerprint[:12]), `signature_reuse` (crop hash[:8]). Owners: rules-kyc, rules-inv-fs-cross, rules-bank, forensics-pdf, forensics-image. Without them the scorer falls back to per-document findings (tested).
- **Explanations**: the first sentence of `Flag.explanation` must stand alone and carry the key numbers; it becomes the finding title and the queue reason (section 4.3, 4.8). Applies to every rules and forensics agent.
- **Database.save_case** (foundation): `findings_count` = findings with severity != info; `high_count` = findings with severity `high`; `partial` column = `case.partial or (case.score is not None and case.score.partial)`; `coverage_ratio` = `case.coverage.ratio`.
- **Coverage**: when `applicable == 0` and the case is partial, `ratio = 0.0` (data-model.md says `1.0` when `applicable == 0`; keep `1.0` only for a non-partial case).
- **Coverage fields** (optional, data-model.md): add `pages_total: int = 0` and `pages_unchecked: int = 0` to `Coverage` so the API and queue can show page-level silence without recomputing; `effective_ratio` stays derived.
- **Foundation tests**: foundation's basic-scorer tests that assume one finding per flag must only use single-flag cases (or `score-queue` may update `backend/tests/test_score_basic.py`), because grouping merges flags (section 4.1).
- **Settings** (optional): `GLDB_FAST_LANE_MAX_SCORE`, `GLDB_FAST_LANE_MIN_COVERAGE` if the sponsor wants them tunable; constants until then.
