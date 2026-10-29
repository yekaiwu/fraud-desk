# Validation report
> Our reading as students, not legal advice. Decision support only; a human decides. All data is fictitious.

Status: NOT VALIDATED. Evaluated for ruleset rs-02de835ec068 on 2026-10-27 (synthetic data; upper bound; thresholds are proposed defaults; sign-off pending)

The sections between `eval:*` markers are written by `eval.py --write-validation-report`; nothing in them is typed by hand. Everything outside the markers is fixed text. Companion documents: [ai-inventory.md](ai-inventory.md), [regulatory-mapping.md](regulatory-mapping.md), [check-catalogue.md](check-catalogue.md).

## 1 Scope and version
<!-- BEGIN eval:scope -->
Run with no network and no API key: FakeLLMClient (oracle on scans), a fresh SQLite database per experiment, fictitious data from our own generator (results are an upper bound).

- Ruleset version: `rs-02de835ec068`; app version `0.1.0`.
- Today (injected, never the wall clock): 2026-10-27; generator seed 4002.
- Extraction: oracle for photo, scan_hq, scan_lq; text_layer for digital; classifier: `rules`; language model: `fake` (no API key, no network).
- Renders requested: digital, scan_hq, scan_lq, photo; instances per tamper (digital): 5; degraded instances per scenario: 1 (scan_hq for a representative subset of tampers, scan_lq and photo for a smaller subset, plus every image-channel tamper; one instance per scenario (compute budget)).
- Mode: full. Clean pool: 10 per scenario plus the canonical company (33 bundles) and 3 hard-quirk bundles; ranking slots: 30 x 4 forms x 5 repetitions; fairness set: full set.
<!-- END eval:scope -->

## 2 Method and honest framing

- The mock bundles are produced by our own generator (`backend/scripts/make_bundle.py`) with a fixed seed; the tampers are produced by our own tamper catalogue. We wrote both the data and the checks, so every result here is an upper bound on real-world performance (plan 9.4).
- The consistent-recomputation hard case is included and its outcome is reported, caught or missed.
- Scanned and image pages are read by an oracle that answers from the generator's ground truth, not by a real vision model. Scan results therefore test the rules, not text recognition.
- The language model client is the fake client (no API key, no network). Extraction on digital PDFs uses the text layer; any model-assisted result is not measured here.
- The queue result depends on the fraud share we chose, so it is reported across a sweep of shares with repeated draws.
- Metrics are reported as measured, including misses and false alarms. A metric that could not be run says "not run" with the reason, or "rule not implemented".
- Today is injected into every run and recorded in the scope section; no result depends on the wall clock.

## 3 Fraud detection
<!-- BEGIN eval:recall -->
A tamper instance counts as caught only when an expected rule fires on a flag that is new relative to the untampered base run of the same render. `weak`, `hard` and `known miss` rows are listed apart and excluded from core recall. `localised` = the flag points at the edited row, field or box (overlap of at least 0.30), over caught instances that have a row, field or box target. The full table, one row per tamper and render, is `docs/eval-results/recall.csv`. Scan and photo rows use oracle extraction (the generator's ground truth stands in for OCR), so they measure the rules and the image forensics, not text recognition.

### Core recall (pooled over non-weak, non-hard, non-known-miss tampers)

| render | instances | caught | core recall | localised |
|---|---|---|---|---|
| digital | 465 | 462 | 0.99 [0.98-1.00] | 302/327 |
| photo | 21 | 12 | 0.57 [0.37-0.76] | 7/8 |
| scan_hq | 47 | 30 | 0.64 [0.50-0.76] | 17/23 |
| scan_lq | 21 | 11 | 0.52 [0.32-0.72] | 6/7 |

**Hard case: consistent recomputation (stated either way) and known miss**

- T02b on digital: CAUGHT in 5/5 instances, Wilson 0.57-1.00 (hard case); caught by `x_cash_at_year_end` in 5.
- T02c on digital: MISSED in 0/5 instances, Wilson 0.00-0.43 (known miss: content checks cannot see it).

**Digital rows with a miss (every other digital row caught every instance)**

| tamper | channel | render | n | caught | Wilson 95% | localised | flags | expected rule(s) | misses (ran / not run) |
|---|---|---|---|---|---|---|---|---|---|
| T08 | world | digital | 15 | 4/15 | 0.11-0.52 | 2/4 | weak | bs_round_numbers | 11 ran / 0 not run |
| T09 | world | digital | 5 | 3/5 | 0.23-0.88 | - | weak | bs_benford | 2 ran / 0 not run |
| T17 | bundle | digital | 10 | 0/10 | 0.00-0.28 | - | weak | inv_sequence | 10 ran / 0 not run |
| T35 | pdf_edit | digital | 15 | 4/15 | 0.11-0.52 | - | weak | font_mix | 11 ran / 0 not run |
| T57 | world | digital | 5 | 4/5 | 0.38-0.96 | 4/4 | - | x_declared_vs_activity | 1 ran / 0 not run |
| T59 | world | digital | 10 | 8/10 | 0.49-0.94 | 8/8 | - | x_remitter_vs_buyer | 2 ran / 0 not run |

- `photo` (9 tamper rows, one to three instances each): T01 1/3; T41 0/2; T42 1/3; T43 0/3. The other 5 rows caught every instance.
- `scan_hq` (22 tamper rows, one to three instances each): T01 1/3; T04 0/3; T19 1/2; T20a 0/1; T41 1/2; T42 0/3; T43 0/3; T58 0/1; T59 0/2. The other 13 rows caught every instance.
- `scan_lq` (9 tamper rows, one to three instances each): T01 1/3; T41 0/2; T42 0/3; T43 0/3. The other 5 rows caught every instance.
- The expected rule was already firing on the untampered base (so no catch is credited): T09 digital (2), T19 scan_hq (1), T41 photo (2), T41 scan_hq (1), T41 scan_lq (2), T42 photo (1), T42 scan_hq (1), T43 photo (3), T43 scan_hq (3), T43 scan_lq (3), T57 digital (1), T58 scan_hq (1).
- Caught but not localised on digital (the flag did not point at the edited row, field or box): T08 2/4, T19 0/10, T24 0/5, T54 0/5, T55 0/5.
- Not measured: T34 (not applicable: needs two contracts); T56b (not applicable: not built: the generator renders an ID card only for directors listed on t)
<!-- END eval:recall -->

## 4 False alarms
<!-- BEGIN eval:false_alarms -->
False alarms are high or medium flags on a clean bundle; a rule a quirk's `may_trigger` allows is listed but not counted; `info` and `low` flags are never false alarms (low flags are a secondary figure).

| render | clean bundles | high/medium flags | false alarms per 10 | bundles with a false alarm | low flags | allowed by quirk | fast lane |
|---|---|---|---|---|---|---|---|
| digital | 33 | 0 | 0.00 | 0/33 | 1 | 0 | 15/33 |
| photo | 3 | 114 | 380.00 | 3/3 | 34 | 0 | 0/3 |
| scan_hq | 3 | 114 | 380.00 | 3/3 | 39 | 0 | 0/3 |
| scan_lq | 3 | 114 | 380.00 | 3/3 | 36 | 0 | 0/3 |
| scan_pdf | 3 | 0 | 0.00 | 0/3 | 37 | 0 | 1/3 |

Rules firing on clean bundles, by render: `photo`: bs_page_chain, bs_period_continuity, bs_totals, x_invoice_vs_receipts, x_sof_support; `scan_hq`: bs_page_chain, bs_period_continuity, bs_totals, x_invoice_vs_receipts, x_sof_support; `scan_lq`: bs_page_chain, bs_period_continuity, bs_totals, x_invoice_vs_receipts, x_sof_support.

Diagnostic (not part of recall): `scan_hq` uploads every page as its own JPEG, so a statement spread over several files cannot be put back together; `scan_pdf` keeps each document in one image-only PDF. High or medium flags on the same three clean bundles: 114 on `scan_hq` against 0 on `scan_pdf` (0.0 per 10); the statement-chain rules and the image-forensics rules are both silent on clean image-only PDFs.

Hard-quirk bundles (reported apart from the headline rate): loan-clean-c90 0 false alarms, 0 allowed by quirk, score 5, lane fast; kyc-clean-c91 0 false alarms, 0 allowed by quirk, score 5, lane edd; aml-clean-c92 0 false alarms, 0 allowed by quirk, score 5, lane aml.

Scan and photo rows use oracle extraction (the generator's ground truth stands in for OCR), so they measure the rules and the image forensics, not text recognition.
<!-- END eval:false_alarms -->

## 5 Extraction
<!-- BEGIN eval:extraction -->
Row and field extraction on the clean bundles, against the generator's ground truth. Rows are aligned with `difflib.SequenceMatcher` over `(date, debit, credit, balance)`.

| extraction mode | statements | row recall | row precision | row exact (with description) | chain valid | chain valid matches truth |
|---|---|---|---|---|---|---|
| text_layer | 165 | 6592/6592 | 6592/6592 | 6592/6592 | 165/165 | 165/165 |

The extractor must never fix a tampered chain (plan 8.5): on T01 digital cases the printed chain is broken by construction and the extracted chain stayed broken in 15/15 statements.

Field accuracy (canonical-equal over fields present in the truth): 8439/8505 overall across 12 document types; document types below 1.00: national_id 396/462. Weakest fields: `national_id.nationality` 0/66.
Table row recall by key column: 2011/2011 over 8 tables; every table complete.

Digital rows are read from the text layer. Scan and photo rows use oracle extraction (the generator's ground truth stands in for OCR), so they measure the rules and the image forensics, not text recognition.
<!-- END eval:extraction -->

## 6 Classification
<!-- BEGIN eval:classification -->
Classification C1 (page type), C3 (statement issuer), C4 (credit-row type), C5 (counterparty business) measured on the clean bundles with the `rules` classifier; the local and hosted classifiers are not available in this run (see the gate result below). C4 and C5 are scored over rows aligned to the truth rows.

| render | C1 pages | C3 issuer | C4 credits | C5 rows | low-confidence page rate |
|---|---|---|---|---|---|
| digital | 1011/1033 = 0.98 (macro-F1 0.99) | 165/165 = 1.00 | 1428/1593 = 0.90 | 5950/6592 = 0.90 | 0.07 |

C1 confusions on digital (truth, predicted): boilerplate -> other

### Classifier gate (imported from `docs/eval-results/classifier-gate.json`, not re-implemented)

80 labelled pages; OCR: oracle+noise. Decision recorded by the gate: keep rules plus hosted fallback. Classifier availability: cascade: ran, llm: skipped: no API key, local: skipped: not installed, rules: ran.

| task | classifier | accuracy | macro-F1 | n |
|---|---|---|---|---|
| C1 | rules | 0.975 | 0.962 | 80 |
| C3 | rules | 0.964 | 0.964 | 28 |
| C4 | rules | 0.898 | 0.924 | 137 |
| C5 | rules | 0.903 | 0.892 | 514 |

C2 (packet boundaries) is a stretch item and is not run. `other_bank` statements are never generated, so there is no accuracy row for them.
<!-- END eval:classification -->

## 7 Injection canaries
<!-- BEGIN eval:canaries -->
Canary pass rate: 12/12 = 1.00 [0.76-1.00] (classifier `rules`). Pass means: original page labels unchanged, flags a superset of the control's (P2: the extra page labelled other or boilerplate only with a `doc_unclassified` info flag and coverage not higher), score not lower.

| host | placement | result | extra page label | failure (with the label it received) |
|---|---|---|---|---|
| statement | P1 | PASS | - | - |
| statement | P2 | PASS | other | - |
| statement | P3 | PASS | - | - |
| statement | P4 | PASS | - | - |
| invoice | P1 | PASS | - | - |
| invoice | P2 | PASS | other | - |
| invoice | P3 | PASS | - | - |
| invoice | P4 | PASS | - | - |
| chart | P1 | PASS | - | - |
| chart | P2 | PASS | other | - |
| chart | P3 | PASS | - | - |
| chart | P4 | PASS | - | - |
<!-- END eval:canaries -->

## 8 Cost and speed
<!-- BEGIN eval:cost -->
**No real model cost measured.** The language model client is the fake client, so every dollar figure is zero by construction and is not a result. Seconds below are real wall-clock times of the offline pipeline on this machine.

Digital bundles analysed: 36 (1127 pages). Seconds per bundle: mean 2.99, p50 2.92, p95 5.41 (nearest-rank).
Mean seconds per stage: ingest 0.74, classify 0.20, extract 0.30, forensics 1.44, rules 0.27, crossdoc 0.05, score 0.00 (dollars per page by stage: no real model cost measured).

Cost per correctly handled bundle: not a result (no model dollars were measured and no false flag had to be priced, so the formula gives zero) over 517 correctly handled bundles. The formula is model dollars plus analyst time on false flags at 2.0 minutes per false flag and 60.0 USD per hour; both are PLACEHOLDER assumptions until `timed-test.csv` has data (timed test: not run).

Cost experiments (`docs/eval-results/cost_experiments.json`): model_sweep = not_run: no api key, text_vs_vision = not_run: no api key, dpi_sweep = not_run: no api key.
<!-- END eval:cost -->

## 9 Prioritisation
<!-- BEGIN eval:ranking -->
30 generated cases per queue (slots), each slot in four forms (clean, deck fraud, forger, one core tamper), digital renders. Fraud share swept over 10%, 20%, 30%, 50% with 5 repetitions each (mean, min..max). Simulated minutes per case = 4.0 + 0.75 x non-info flags (**assumed (placeholder until the timed test is run)**).

**All fraud forms (deck, forger, single)**

| fraud share | fraud cases | top-5 fraud | top-10 fraud | P@5 | R@5 | lift@5 | AUROC | first fraud top-down | first fraud upload order | random expectation | sim. minutes top-down | sim. minutes upload order |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 10% | 3 | 3.0 (3.0..3.0) | 3.0 (3.0..3.0) | 0.60 (0.60..0.60) | 1.00 (1.00..1.00) | 6.00 (6.00..6.00) | 1.00 (1.00..1.00) | 1.0 (1.0..1.0) | 9.4 (1.0..21.0) | 7.8 | 8.3 (6.2..10.8) | 41.2 (4.8..87.0) |
| 20% | 6 | 5.0 (5.0..5.0) | 6.0 (6.0..6.0) | 1.00 (1.00..1.00) | 0.83 (0.83..0.83) | 5.00 (5.00..5.00) | 1.00 (1.00..1.00) | 1.0 (1.0..1.0) | 4.4 (1.0..10.0) | 4.4 | 9.4 (6.2..10.8) | 20.9 (4.8..46.0) |
| 30% | 9 | 5.0 (5.0..5.0) | 9.0 (9.0..9.0) | 1.00 (1.00..1.00) | 0.56 (0.56..0.56) | 3.33 (3.33..3.33) | 1.00 (1.00..1.00) | 1.0 (1.0..1.0) | 2.6 (1.0..9.0) | 3.1 | 9.4 (7.0..10.0) | 12.7 (4.8..37.5) |
| 50% | 15 | 5.0 (5.0..5.0) | 10.0 (10.0..10.0) | 1.00 (1.00..1.00) | 0.33 (0.33..0.33) | 2.00 (2.00..2.00) | 1.00 (1.00..1.00) | 1.0 (1.0..1.0) | 1.6 (1.0..4.0) | 1.9 | 10.0 (10.0..10.0) | 8.5 (4.8..16.8) |

**Detectable subset (forger cases removed from the queue)**

- 10%: 2.0 (2.0..2.0) fraud cases; top-5 2.0 (2.0..2.0); P@5 0.40 (0.40..0.40); AUROC 1.00 (1.00..1.00); first fraud top-down 1.0 (1.0..1.0) against 12.2 (1.0..21.0) in upload order (random 10.0).
- 20%: 4.0 (4.0..4.0) fraud cases; top-5 4.0 (4.0..4.0); P@5 0.80 (0.80..0.80); AUROC 1.00 (1.00..1.00); first fraud top-down 1.0 (1.0..1.0) against 6.2 (1.0..11.0) in upload order (random 5.8).
- 30%: 6.0 (6.0..6.0) fraud cases; top-5 5.0 (5.0..5.0); P@5 1.00 (1.00..1.00); AUROC 1.00 (1.00..1.00); first fraud top-down 1.0 (1.0..1.0) against 4.0 (1.0..9.0) in upload order (random 4.0).
- 50%: 10.0 (10.0..10.0) fraud cases; top-5 5.0 (5.0..5.0); P@5 1.00 (1.00..1.00); AUROC 1.00 (1.00..1.00); first fraud top-down 1.0 (1.0..1.0) against 3.0 (1.0..8.0) in upload order (random 2.4).


**How the result moves with the fraud share**

- At a fraud share of 10% (3 of 30 cases) the first five queue places held 3.0 fraud cases on average (an ideal queue would hold 3); the first fraud case sat at position 1.0 top-down against 9.4 in upload order (random expectation 7.8); without forger cases the top five held 2.0.
- At a fraud share of 20% (6 of 30 cases) the first five queue places held 5.0 fraud cases on average (an ideal queue would hold 5); the first fraud case sat at position 1.0 top-down against 4.4 in upload order (random expectation 4.4); without forger cases the top five held 4.0.
- At a fraud share of 30% (9 of 30 cases) the first five queue places held 5.0 fraud cases on average (an ideal queue would hold 5); the first fraud case sat at position 1.0 top-down against 2.6 in upload order (random expectation 3.1); without forger cases the top five held 5.0.
- At a fraud share of 50% (15 of 30 cases) the first five queue places held 5.0 fraud cases on average (an ideal queue would hold 5); the first fraud case sat at position 1.0 top-down against 1.6 in upload order (random expectation 1.9); without forger cases the top five held 5.0.

**Caveats**

- The generator is ours: the fraud we planted is the fraud we look for, so these numbers are an upper bound.
- `forger` cases keep every internal identity consistent and can legitimately sit low; the second column set drops them (the detectable subset).
- The queue is only as good as `SCORE_WEIGHTS` and the fast-lane thresholds, which are recalibrated from the recall and false-alarm results; this test is run after any recalibration and before the freeze.
- The fraud share is our choice (real prevalence at GLDB is unknown); the table shows four shares and five repetitions each.
- Minutes to first fraud come from an assumed model, not a measurement, unless `timed-test.csv` has data.
<!-- END eval:ranking -->

## 10 Fairness slice
<!-- BEGIN eval:fairness -->
Name-matching slice test (full set, 270 pairs) through the real pipeline. False-flag rate = same-person pairs for which a name rule (`x_directors`, `x_ownership_vs_registry`, `x_entity`) fired; wrong-match rate = different-person (near-spelled) pairs for which none fired. Last column: the public matcher in `backend/app/names.py` run directly (false-flag rate / wrong-match rate).

| name group | false-flag rate | wrong-match rate | matcher only |
|---|---|---|---|
| western_order | 0/30 = 0.00 [0.00-0.11] | 0/15 = 0.00 [0.00-0.20] | 0.00 / 0.67 |
| chinese_order | 0/30 = 0.00 [0.00-0.11] | 0/15 = 0.00 [0.00-0.20] | 0.00 / 0.00 |
| malay_patronymic | 0/30 = 0.00 [0.00-0.11] | 3/15 = 0.20 [0.07-0.45] | 0.00 / 0.20 |
| indian_patronymic | 0/30 = 0.00 [0.00-0.11] | 6/15 = 0.40 [0.20-0.64] | 0.00 / 0.40 |
| mononym | 0/30 = 0.00 [0.00-0.11] | 0/15 = 0.00 [0.00-0.20] | 0.00 / 0.73 |
| titles | 0/30 = 0.00 [0.00-0.11] | 0/15 = 0.00 [0.00-0.20] | 0.00 / 0.00 |

Disparity (max minus min false-flag rate): 0.00. Proposed pass criteria (ours, not from the plan): false-flag rate <= 0.02, wrong-match rate <= 0.05, disparity <= 0.05. Outcome: **FAIL** (criteria met: false_flag_rate yes, wrong_match_rate no, disparity yes). A FAIL is a finding, not a build failure. The groups are our categories, not a validated taxonomy; the intervals are wide.
<!-- END eval:fairness -->

## 11 Deck acceptance
<!-- BEGIN eval:acceptance -->
Deck acceptance, digital (gating): 9/9 bundles pass. The scripted analyst confirms every high flag with a reason and posts the expected verdict through the API; a row passes only when every `must_fire` is met (each any-of group, at the minimum severity), no `must_not_fire` fired, lane and band are in bounds, the case is not partial, the verdict is accepted (201, case closed) and `flags_digest` is reproducible. The tool never decides: this shows the evidence is there and the human path exists.

| bundle | result | lane | band | score | coverage | reproducible | missing, forbidden or other failure |
|---|---|---|---|---|---|---|---|
| kyc-clean | PASS | edd | low | 0 | 0.83 | yes | - |
| kyc-fraud | PASS | edd | high | 75 | 0.83 | yes | - |
| kyc-forger | PASS | edd | high | 100 | 0.83 | yes | - |
| aml-clean | PASS | aml | low | 0 | 0.76 | yes | - |
| aml-fraud | PASS | aml | high | 90 | 0.76 | yes | - |
| aml-forger | PASS | aml | high | 75 | 0.76 | yes | - |
| loan-clean | PASS | fast | low | 0 | 0.89 | yes | - |
| loan-fraud | PASS | loan | high | 100 | 0.89 | yes | - |
| loan-forger | PASS | loan | high | 75 | 0.89 | yes | - |

Reproducibility (M10): 9/9 = 1.00 [0.70-1.00] bundles gave the same `flags_digest` and flag ids on rerun.
Row A:effectiveness: effectiveness counts and the case pack are consistent with the analyst's actions in 9/9 bundles.

Scan renders (`scan_hq`, oracle extraction), reported and not gating: 2/9 pass. Scan and photo rows use oracle extraction (the generator's ground truth stands in for OCR), so they measure the rules and the image forensics, not text recognition. kyc-clean FAIL (band high, score 100; band high above low; 15 flags >= medium (max 0)) | kyc-fraud PASS (band high, score 100) | kyc-forger FAIL (band high, score 100; forbidden rule fired: bs_totals; forbidden rule fired: bs_period_continuity; forbidden rule fired: bs_page_cha) | aml-clean FAIL (band high, score 100; band high above low; 67 flags >= medium (max 0); case partial: ['some documents were only partly extracted']) | aml-fraud FAIL (band high, score 100; missing bs_pass_through (>= medium); case partial: ['some documents were only partly extracted']) | aml-forger FAIL (band high, score 100; missing bs_pass_through (>= medium); forbidden rule fired: bs_totals; forbidden rule fired: bs_period_continui) | loan-clean FAIL (band high, score 100; band high above low; 32 flags >= medium (max 0)) | loan-fraud PASS (band high, score 100) | loan-forger FAIL (band high, score 100; forbidden rule fired: bs_totals; forbidden rule fired: bs_period_continuity; forbidden rule fired: bs_page_cha)
<!-- END eval:acceptance -->

## 12 Analyst burden
<!-- BEGIN eval:burden -->
| population | bundles | mean non-info flags | median | p95 |
|---|---|---|---|---|
| clean bundles | 33 | 0.03 | 0 | 0 |
| deck-fraud bundles | 3 | 5.67 | 4 | 9 |

Timed analyst test: **not run** (timed-test.csv has a header and no rows (the timed test is a manual task)). The protocol and header are in `docs/eval-results/timed-test.csv`; the simulated minutes in the prioritisation section use an assumed model, not a measurement.
<!-- END eval:burden -->

## 13 Known misses and limits
<!-- BEGIN eval:limits -->
**Missed or weak in this run** (generated from the results above; per-row detail in `docs/eval-results/recall.csv`)

- 19 core tamper rows missed at least one instance (digital and degraded renders); the digital ones are listed in the recall section.
- T02c on digital: caught 0/5 (known miss, as expected); misses: 5 ran and missed, 0 not run
- T08 on digital: caught 4/15 (weak); misses: 11 ran and missed, 0 not run
- T09 on digital: caught 3/5 (weak); misses: 2 ran and missed, 0 not run; 2 had the expected rule already firing on the untampered base, so no catch is credited
- T17 on digital: caught 0/10 (weak); misses: 10 ran and missed, 0 not run
- T35 on digital: caught 4/15 (weak); misses: 11 ran and missed, 0 not run
- False alarms on clean bundles: photo 114 over 3 bundles (380.0 per 10); scan_hq 114 over 3 bundles (380.0 per 10); scan_lq 114 over 3 bundles (380.0 per 10) (rules per render in section 4)
- Fairness slice: FAIL against our proposed criteria

**Gaps (plan 0.6)**

- T56b: not applicable: not built: the generator renders an ID card only for directors listed on the profile, so an ID for a non-director cannot be generated (generator gap)
- plan 0.4 circle items (board resolution, proof of address, business licences, tax returns, wealth evidence, RFI supporting documents, collateral, trade documents) are meant to be classified as other and listed as received, not checked; rows O1 to O8 test it
- no layout-fingerprint rule (template comparison relies on the issuer label and the producer table)
- no dedicated reader-disagreement rule (OCR versus visual mismatch is pdf_text_vs_render plus alt_raw)
- ratio benchmarking against peers (industry profile and financial performance) is not built
- C2 packet boundary detection (stretch) is not run; `local` and `llm` classifiers and the real-model extraction modes are not run (no model, no API key)
- real model cost is not measured (fake client); cost experiments say `not_run: no api key`

**Fixed limits**

- The generator is ours (documents, layouts and the answer sheet): every number is an upper bound. Scan and photo results use oracle extraction, not OCR or a vision model.
- Four invented statement layouts; no real bank templates; `other_bank`, hanzi names and the plan 0.4 circle items are not generated. The mock registry is not ACRA data; mock signatures carry no cryptography.
- Weak and statistical tampers (T08, T09, T17, T35) have thresholds only the eval can set and are kept out of core recall. The fraud share in the prioritisation test is our choice.
- The fairness set is small and its groups are our categories. Dollar figures are zero with the fake client; real cost exists only after a keyed run.
<!-- END eval:limits -->

## 14 Change log
<!-- BEGIN eval:changelog -->
| ruleset version | summary | deltas against the baseline |
|---|---|---|
| rs-02de835ec068 | core recall digital 462/465; false alarms per 10 clean bundles 0.00 (33 bundles); deck acceptance 9/9; canaries 12/12 | baseline recorded for this ruleset version (later runs report deltas against it) |
<!-- END eval:changelog -->

## 15 Sign-off

No one has signed this report. The thresholds in it are proposed defaults, not approved ones.

| Role | Name | Date | Comment |
|---|---|---|---|
| Model owner | | | |
| Independent reviewer | | | |
| Compliance | | | |
