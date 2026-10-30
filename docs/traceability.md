# Traceability: plan 0.4 re-scored from the eval

> Our reading as students, not legal advice. Decision support only; a human decides. All data is fictitious.

Run with no network and no API key: FakeLLMClient (oracle on scans), a fresh SQLite database per experiment, fictitious data from our own generator (results are an upper bound). Ruleset rs-02de835ec068, today 2026-10-27.

Status legend (plan 0.6). Planned status comes from plan 0.4; measured status is decided here from the eval: **●** = a check ran on generated digital data, the flag carried evidence and a region or row, and core recall for the item is at least 0.8 with at least one catch; **◐** = caught at least once but a limit applies; **○** = never caught, not built, or rule not implemented. Measured status never exceeds the planned status. A row where measured is below planned says "downgraded from X".

## Process themes

| item | planned | measured | evidence | limits or reason |
|---|---|---|---|---|
| 1 Digital architecture and straight-through processing | ◐ | ◐ | acceptance rows passing: 3/3 | -. API-first backend and a fast-lane label; a human still confirms, no auto-approval. Proof: the clean bundles pass acceptance (lane in fast or the scenario lane) and the ranked queue exists. |
| 2 Onboarding, risk classification, ongoing KYC | ◐ | ◐ | acceptance rows passing: 3/3 | -. KYC bundle type with a risk band; a rerun keeps the old version. No scheduled periodic review. |
| 3 Transaction monitoring and behavioural analytics | ◐ | ◐ | digital caught 14/15; scans 3/3 | -. Only inside uploaded statements; no live monitoring. |
| 4 Network analysis, alert prioritisation, investigation support | ◐ | ◐ | acceptance rows passing: 1/1 | -. Ranked queue, grouped findings, a small in-case entity graph. Proof: the prioritisation sweep and the AML bundle. |
| 5 Manual review, override, escalation | ● | ● | acceptance rows passing: 9/9; A:effectiveness consistent: True | -. Review step with audit log, mandatory reasons and outcome-specific verdicts. Proof: all nine acceptance rows close through the API, and row A:effectiveness. |
| 6 Control effectiveness and regulatory reporting | ◐ | ◐ | acceptance rows passing: 1/1; A:effectiveness consistent: True | -. Rule-effectiveness page and an exportable case pack. Proof: effectiveness counts and the pack are consistent after the acceptance run (E1). |

## Fraud scenarios

| item | planned | measured | evidence | limits or reason |
|---|---|---|---|---|
| Edited bank statement balances | ● | ● | digital caught 45/45; scans 15/15 | -. Hard case T02b (consistent recomputation) is reported apart. |
| Manipulated revenue figures | ◐ | ◐ | digital caught 25/25; scans 2/2 | -. |
| Fake invoices | ● | ● | digital caught 110/110; scans 12/12 | -. T17 (sequence gap) is weak and listed apart. |
| Forged contracts | ◐ | ◐ | digital caught 50/50 | -. No judgement about who signed. |
| Altered shareholder records | ◐ | ◐ | digital caught 15/15; scans 3/3 | -. |
| Modified passports and IDs | ◐ | ◐ | digital caught 50/50; scans 22/27 | scan misses (22/27 caught on degraded renders). T56b (an ID for a non-director) is not built: the generator renders an ID card only for profile directors. |
| False source-of-funds evidence | ◐ | ◐ | digital caught 15/15; scans 3/3 | -. |

## Detection techniques

| item | planned | measured | evidence | limits or reason |
|---|---|---|---|---|
| PDF metadata analysis | ● | ● | digital caught 60/60 | -. |
| Font inconsistency detection | ◐ | ◐ | digital caught 15/15 | -. T35 (font_mix) is weak and reported apart. |
| Image manipulation detection | ◐ | ◐ | JPEG renders caught 2/24 | core recall 0.08 is below 0.8; scan misses (2/24 caught on degraded renders). `noise_mismatch` is a stretch check. JPEG renders only. |
| Signature / stamp anomalies | ◐ | ◐ | digital caught 5/5 | -. No claim about who signed. |
| OCR vs visual mismatch | ◐ | ◐ | digital caught 15/15 | -. Reader disagreement is kept as `alt_raw`; no dedicated rule (gap). |
| Template comparison | ◐ | ◐ | digital caught 15/15 | -. Issuer classification C3 and the producer table for the mock layouts; there is no layout-fingerprint rule (gap). |
| Duplicate document detection | ● | ● | digital caught 30/30 | -. |
| AI-generated image detection | ◐ | ◐ | digital caught 10/10 | -. Provenance hints only, reported as not detected and never as a clearance. |

## Cross-document

| item | planned | measured | evidence | limits or reason |
|---|---|---|---|---|
| Invoice and contract amount | ◐ | ◐ | digital caught 15/15 | -. |
| Invoice and bank payment | ● | ● | digital caught 10/10; scans 2/2 | -. |
| Revenue and bank credits | ● | ● | digital caught 5/5; scans 1/1 | -. Hard case T02b is reported apart. |
| Ownership chart and registry | ◐ | ◐ | digital caught 5/5; scans 3/3 | -. |
| Industry profile and financial performance | ◐ | ◐ | digital caught 4/5; scans 1/1 | -. Ratio benchmarking against peers is not built. |
| Source of funds and transaction evidence | ◐ | ◐ | digital caught 5/5; scans 1/1 | -. |

## Family A: KYC

| item | planned | measured | evidence | limits or reason |
|---|---|---|---|---|
| ACRA / Biz profile | ◐ | ◐ | digital caught 5/5 | -. |
| Certificate of incorporation | ◐ | ◐ | acceptance rows passing: 1/1 | no tamper row; measured through the acceptance bundles only. No tamper row: only silence on the clean bundle is measured. |
| Ownership chart | ◐ | ◐ | digital caught 15/15; scans 3/3 | -. |
| Shareholder register | ◐ | ◐ | digital caught 10/10 | -. |
| Directors' and UBO IDs | ● | ● | digital caught 50/50; scans 22/27 | scan misses (22/27 caught on degraded renders). |
| Board resolution | ○ | ○ | O1: passed | Classified as other and listed as received, not checked. |
| Proof of address | ○ | ○ | O2: passed | Classified as other and listed as received, not checked. |
| Business licences | ○ | ○ | O3: passed | Classified as other and listed as received, not checked. |

## Family B: AML

| item | planned | measured | evidence | limits or reason |
|---|---|---|---|---|
| Bank statements | ● | ● | digital caught 105/105; scans 21/21 | -. |
| Invoices | ● | ● | digital caught 100/100; scans 10/10 | -. |
| Contracts | ◐ | ◐ | digital caught 15/15 | -. |
| Tax returns | ○ | ○ | O4: passed | Corporate returns not built; classified as other and listed as received, not checked. |
| Financial statements | ◐ | ◐ | digital caught 25/25; scans 1/1 | -. |
| Sale / purchase agreements | ◐ | ◐ | digital caught 15/15 | -. |
| Wealth evidence | ○ | ○ | O5: passed | Classified as other and listed as received, not checked. |
| RFI supporting documents | ○ | ○ | O6: passed | Classified as other and listed as received, not checked. |

## Family C: Loan

| item | planned | measured | evidence | limits or reason |
|---|---|---|---|---|
| Financial statements | ◐ | ◐ | digital caught 20/20; scans 1/1 | -. |
| Management accounts | ◐ | ◐ | acceptance rows passing: 1/1 | no tamper row; measured through the acceptance bundles only. Same parser, marked unaudited (generator quirk Q14); measured as silence on the odd-index clean bundles. |
| Bank statements | ● | ● | digital caught 45/45; scans 15/15 | -. |
| Contracts | ◐ | ◐ | digital caught 15/15 | -. |
| Invoices | ● | ● | digital caught 40/40; scans 10/10 | -. |
| Purchase orders | ◐ | ◐ | digital caught 5/5 | -. PO to invoice amount. |
| Collateral documents | ○ | ○ | O7: passed | Classified as other and listed as received, not checked. |
| Trade documents | ○ | ○ | O8: passed | Classified as other and listed as received, not checked. |

## Summary

Items: 51; measured ● 12, ◐ 31, ○ 8; downgraded: 0.

## Gaps (plan 0.6)

- T56b: not applicable: not built: the generator renders an ID card only for directors listed on the profile, so an ID for a non-director cannot be generated (generator gap)
- plan 0.4 circle items (board resolution, proof of address, business licences, tax returns, wealth evidence, RFI supporting documents, collateral, trade documents) are meant to be classified as other and listed as received, not checked; rows O1 to O8 test it
- no layout-fingerprint rule (template comparison relies on the issuer label and the producer table)
- no dedicated reader-disagreement rule (OCR versus visual mismatch is pdf_text_vs_render plus alt_raw)
- ratio benchmarking against peers (industry profile and financial performance) is not built
- C2 packet boundary detection (stretch) is not run; `local` and `llm` classifiers and the real-model extraction modes are not run (no model, no API key)
- real model cost is not measured (fake client); cost experiments say `not_run: no api key`
