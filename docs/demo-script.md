# Demo script

> Our reading as students, not legal advice. Decision support only; a human decides. All data is fictitious. Written from the eval results: every number carries its source cell in `docs/eval-results/latest.json` (or `results.json`) in square brackets.

Ruleset `rs-02de835ec068`, today 2026-10-27 (injected, never the wall clock), seed 4002. The generator is ours, so every number below is an **upper bound** on real-world performance (plan 9.4).

## 0. Before you start (two minutes)

1. Terminal 1: `uv sync`, then `uv run python backend/scripts/make_bundle.py --scenario all --variant all --out samples` (the nine bundles) and `uv run python backend/scripts/make_bundle.py --scenario all --variant clean --clean-pool 10 --out samples` for the queue demo.
2. Terminal 1: `GLDB_LLM_BACKEND=fake uv run uvicorn --app-dir backend app.main:app` (no API key is needed for digital bundles; the fake client never leaves the machine).
3. Terminal 2: `cd frontend && npm install && npm run dev`, open `http://localhost:3000`. If the backend is down use mock mode (section 6).
4. Say once, at the start: nothing here approves, rejects or escalates anything. A person decides, and every click below is the person.

## 1. KYC bundle

**Story.** Sembrook Freight Pte. Ltd. declares logistics; the statements show crypto-related counterparties; the ownership chart omits a shareholder; a fourth director is not a registry officer (plan 0.5, deck slide 14).

**Clicks.**

1. Upload page (`/upload`): choose use case **KYC**, reference `DEMO-KYC-FRAUD`, drop every file from `samples/bundles/kyc-fraud/digital/`, click **Upload**. You land on the case page with the stepper (ingest, classify, extract, forensics, rules, crossdoc, score).
2. Queue (`/`): the new case shows its risk band, the one-line top finding, coverage and the lane chip. Say: silence is not safety, so coverage is printed beside the score.
3. Open the case. **Findings** tab: expand the first finding, click **Show on page**: the viewer jumps to the page and draws the box on the printed cell.
4. **Documents** tab: point at the document types and the issuer label; open **Received but not checked** (documents no check examined are listed, never hidden).
5. **Graph** tab: the in-case entity graph (companies, directors, counterparties).
6. **Review** tab: select the high flags, **Confirm**, type a reason. Pick the verdict **Refer for EDD review** (the lane suggests it, nothing is preselected), type a reason, submit. The case closes; the review log shows who decided and why.
7. **Case pack** (new tab): the evidence pack with flags, coverage and the review log.

**Expected result** (from the acceptance run, digital):

- Lane **edd**, band **high**, score **75**, coverage 0.83 `[acceptance[kyc-fraud]: lane, band, score, coverage]`
- Findings at medium or above: `kyc_shareholdings` (high), `x_directors` (high), `x_ownership_vs_registry` (high), `x_declared_vs_activity` (medium) `[acceptance[kyc-fraud].flags]`
- Scripted analyst: confirmed 3 high flag(s), verdict `refer_edd`, case closed: True `[acceptance[kyc-fraud].human_path]`
- The clean control for the same company stays quiet: lane **edd**, band **low**, score **0** `[acceptance[kyc-clean]]`
- The careful-forger variant (every internal identity kept consistent): lane **edd**, band **high**, score **100**; findings: `x_directors` (high), `x_ownership_vs_registry` (high), `x_declared_vs_activity` (medium) `[acceptance[kyc-forger]]`

## 2. AML bundle

**Story.** Tanjong Ridge Trading Pte. Ltd. declares S$1,000,000.00 of sales proceeds from one customer; the credits come from three other companies and are forwarded within days (deck slide 15).

**Clicks.**

1. Upload page (`/upload`): choose use case **AML**, reference `DEMO-AML-FRAUD`, drop every file from `samples/bundles/aml-fraud/digital/`, click **Upload**. You land on the case page with the stepper (ingest, classify, extract, forensics, rules, crossdoc, score).
2. Queue (`/`): the new case shows its risk band, the one-line top finding, coverage and the lane chip. Say: silence is not safety, so coverage is printed beside the score.
3. Open the case. **Findings** tab: expand the first finding, click **Show on page**: the viewer jumps to the page and draws the box on the printed cell.
4. **Documents** tab: point at the document types and the issuer label; open **Received but not checked** (documents no check examined are listed, never hidden).
5. **Graph** tab: the in-case entity graph (companies, directors, counterparties).
6. **Review** tab: select the high flags, **Confirm**, type a reason. Pick the verdict **Escalate for AML investigation** (the lane suggests it, nothing is preselected), type a reason, submit. The case closes; the review log shows who decided and why.
7. **Case pack** (new tab): the evidence pack with flags, coverage and the review log.

**Expected result** (from the acceptance run, digital):

- Lane **aml**, band **high**, score **90**, coverage 0.76 `[acceptance[aml-fraud]: lane, band, score, coverage]`
- Findings at medium or above: `x_remitter_vs_buyer` (high), `x_sof_support` (high), `bs_pass_through` (medium), `inv_gst` (medium) `[acceptance[aml-fraud].flags]`
- Scripted analyst: confirmed 2 high flag(s), verdict `escalate_aml`, case closed: True `[acceptance[aml-fraud].human_path]`
- The clean control for the same company stays quiet: lane **aml**, band **low**, score **0** `[acceptance[aml-clean]]`
- The careful-forger variant (every internal identity kept consistent): lane **aml**, band **high**, score **75**; findings: `x_remitter_vs_buyer` (high), `x_sof_support` (high), `bs_pass_through` (medium) `[acceptance[aml-forger]]`

## 3. Loan bundle

**Story.** Meridian Fastener Supplies Pte. Ltd. prints FY2025 revenue of S$2,400,000.00 against about S$900,000.00 of bank credits; invoices marked paid have no matching receipt; invoices exceed their contract (deck slide 16).

**Clicks.**

1. Upload page (`/upload`): choose use case **Loan**, reference `DEMO-LOAN-FRAUD`, drop every file from `samples/bundles/loan-fraud/digital/`, click **Upload**. You land on the case page with the stepper (ingest, classify, extract, forensics, rules, crossdoc, score).
2. Queue (`/`): the new case shows its risk band, the one-line top finding, coverage and the lane chip. Say: silence is not safety, so coverage is printed beside the score.
3. Open the case. **Findings** tab: expand the first finding, click **Show on page**: the viewer jumps to the page and draws the box on the printed cell.
4. **Documents** tab: point at the document types and the issuer label; open **Received but not checked** (documents no check examined are listed, never hidden).
5. **Review** tab: select the high flags, **Confirm**, type a reason. Pick the verdict **Reject or escalate** (the lane suggests it, nothing is preselected), type a reason, submit. The case closes; the review log shows who decided and why.
6. **Case pack** (new tab): the evidence pack with flags, coverage and the review log.

**Expected result** (from the acceptance run, digital):

- Lane **loan**, band **high**, score **100**, coverage 0.89 `[acceptance[loan-fraud]: lane, band, score, coverage]`
- Findings at medium or above: `fs_subtotals` (high), `x_invoice_vs_contract` (medium), `x_invoice_vs_receipts` (medium), `x_revenue_vs_credits` (medium) `[acceptance[loan-fraud].flags]`
- Scripted analyst: confirmed 1 high flag(s), verdict `reject_or_escalate`, case closed: True `[acceptance[loan-fraud].human_path]`
- The clean control for the same company stays quiet: lane **fast**, band **low**, score **0** `[acceptance[loan-clean]]`
- The careful-forger variant (every internal identity kept consistent): lane **loan**, band **high**, score **75**; findings: `x_invoice_vs_contract` (medium), `x_invoice_vs_receipts` (medium), `x_revenue_vs_credits` (medium) `[acceptance[loan-forger]]`

## 4. The ranked queue

**Clicks.** Upload six cases of the same kind in any order (for example the clean and fraud bundles of the three use cases from `samples/bundles/`). Open `/`: the default sort is **priority** (status, then score high to low, then coverage low to high). Point at rank 1 to 6 and at the **Fast lane** chip on the clean cases (a person still confirms them). Use the use-case segmented control and the lane filter to show the queue narrowing.

**Numbers to quote** (prioritisation test, 30 generated cases per queue, fraud share is our choice, five repetitions):

- At a fraud share of 20% (6 of 30 cases): top-5 held 5.0 fraud cases on average (range 5 to 5); top-10 held 6.0 `[ranking.shares[share=0.2].all.top5_fraud, top10_fraud]`
- First fraud case at position 1.0 working top-down against 4.4 in upload order (random expectation 4.4) `[ranking.shares[share=0.2].all.first_top_down, first_upload, random_first_position]`
- Without forger cases (the detectable subset) the top-5 held 4.0 `[ranking.shares[share=0.2].detectable.top5_fraud]`
- Simulated minutes to the first fraud case: 9.4 top-down against 20.9 in upload order. This is an **assumed model** (assumed (placeholder until the timed test is run)), not a timed measurement `[ranking.shares[share=0.2].all.sim_minutes_*]`
- How it moves with the share: At a fraud share of 10% (3 of 30 cases) the first five queue places held 3.0 fraud cases on average (an ideal queue would hold 3); the first fraud case sat at position 1.0 top-down against 9.4 in upload order (random expectation 7.8); without forger cases the top five held 2.0. (all four shares are in the validation report, section 9).

## 5. Numbers to quote, including the misses

- Core recall on digital tampers: 464/465 = 1.00 (Wilson 95% 0.99 to 1.00) `[core_recall.digital]`
- **The hard case** (change a deposit and recompute every later balance, T02b): caught in 5/5 instances (by `x_cash_at_year_end` in 5: the financial statements still show the old cash and revenue). Say it either way. `[recall[T02b,digital]]`
- **The known miss** (T02c: the same edit with the financial statements adjusted to match): caught in 0/5; content checks cannot see a fully consistent story. `[recall[T02c,digital]]`
- False alarms on clean bundles: 0 high or medium flags over 33 digital bundles = 0.00 per 10 clean bundles `[false_alarms.digital]`
- **On scanned renders the false alarms are 0 here, and that rests on 3 clean bundles per render and on oracle extraction**: 0 high or medium flags over 3 clean scan bundles = 0.0 per 10 (a rate of 0 on 3 bundles has a Wilson 95% upper bound of 0.56). With real OCR (Apple Vision) instead of the oracle, the same clean bundles still raised flags in all 3 of 3 (51 flags on `scan_hq`, 58 on `scan_pdf`), so this 0 is an upper bound, not the expected real-scan figure `[false_alarms.scan_hq]`
- Diagnostic: the same bundles as one image-only PDF per document also give 0 high or medium flags (0.0 per 10). A statement uploaded as one JPEG per page is put back together when its page markers and account number allow it (ingest-and-extraction.md 4.11); otherwise the pages are reported as not reviewed instead of flagged `[false_alarms.scan_pdf]`
- Core tampers with a miss on digital: T57 4/5 `[recall[*,digital]]`
- Deck acceptance (digital, gating): 9/9 bundles pass; reproducible reruns 9/9 `[acceptance, reproducibility]`
- Injection canaries: 12/12 pass (instruction text in a footer, an extra page, white text and a transaction description) `[canaries]`
- Fairness slice over six name groups: outcome **PASS** against our proposed criteria; disparity of the false-flag rate 0.00 `[fairness.overall, fairness.disparity]`
- The weakest fairness number: wrong-match rate (a near-spelled different person that passed) is 0/15 in every group (a six-way tie, Wilson 95% upper bound 0.20 per group, so 15 pairs per group cannot rule out a rate up to about one in five) `[fairness.groups[*].wrong_match_rate]`
- Cost: no real model cost measured (the fake client prices everything at zero); seconds per digital bundle p50 1.1, p95 1.2 `[cost.seconds]`

**Always say:** the generator is ours, so these are upper bounds; scan numbers use oracle extraction (the generator's own answer sheet stands in for OCR), so they test the rules and the image forensics, not text recognition; the timed analyst test has not been run.

## 6. If the live demo fails

1. **Mock mode, no backend:** `cd frontend && npm run dev:mock` (or `NEXT_PUBLIC_USE_MOCK=1 npm run dev`). A banner says MOCK DATA; the twelve fixture cases cover the three bundles, the partial case, the processing stepper and the review flow with the real error codes.
2. **Command line:** `uv run python backend/scripts/run_case.py --scenario loan --variant fraud --timings` prints the findings, the flags and the per-stage seconds with no UI and no network.
3. **Pre-saved packs:** keep the HTML case pack of each fraud bundle (`/cases/{id}/pack?format=html`) from the rehearsal; it prints from a browser and carries the evidence, coverage and review log.
4. **Last resort:** open `docs/eval-results/acceptance.md` and `docs/validation-report.md`; every claim in the talk is in them with its denominator.

## 7. Questions you will get

**What about AI-generated documents?** The tool reports provenance hints only: a generator tag in the PDF or image metadata (`ai_provenance_hint`). It says "not detected" when it finds nothing, never that a document is cleared, and pixel detectors are known to fall apart on AI-inpainted numbers (plan 1.3). In our tamper test (a Creator entry naming an image generator, T33) the hint was found in 10/10 instances, so do not promise it `[recall[T33,digital]]`.

**Does it decide?** No. The verdict form never preselects a choice, a reason of at least five characters is mandatory, dismissing a high flag needs a second reviewer, and the audit log is append-only. The tool does not file a suspicious transaction report.

**Would this work on real bank statements?** We do not know. The four statement layouts are invented, the registry is a mock file shaped like an ACRA profile, and the real sponsor documents may arrive as scans.

## Appendix: timed analyst test protocol (the test itself is a manual task)

File: `docs/eval-results/timed-test.csv`, header `tester,order,queue_id,rep,seconds_to_first_fraud,cases_opened,flags_read,false_flags_dismissed,seconds_per_flag,notes`. Two or three teammates each work one queue (fraud share 20%, repetition 0) top-down in the UI and a different repetition in upload order, order counterbalanced, stopwatch, UI only; the first-fraud time stops at the confirm click on a true flag. Until the file has rows the eval says "not run" and uses the assumed minutes model. With two or three testers this is an anecdote, not a study.
