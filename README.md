<div align="center" >

<img src="assets/banner.svg" alt="Fraud Desk: document-fraud analysis for KYC, AML and loan checks" width="100%">

<h3>Upload a bundle of documents. Get a ranked queue of cases,<br>with the evidence behind every flag.</h3>

<p>
  <a href="https://github.com/yekaiwu/fraud-desk/actions/workflows/ci.yml"><img src="https://github.com/yekaiwu/fraud-desk/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue?style=flat-square" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/FastAPI-backend-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Next.js-16-000000?style=flat-square&logo=nextdotjs&logoColor=white" alt="Next.js 16">
  <img src="https://img.shields.io/badge/data-100%25%20fictitious-b8893b?style=flat-square" alt="All data is fictitious">
</p>

<p>
  <a href="https://github.com/yekaiwu/fraud-desk/stargazers"><img src="https://img.shields.io/github/stars/yekaiwu/fraud-desk?style=flat-square&logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/yekaiwu/fraud-desk/issues"><img src="https://img.shields.io/github/issues/yekaiwu/fraud-desk?style=flat-square" alt="Open issues"></a>
  <a href="https://github.com/yekaiwu/fraud-desk/commits/main"><img src="https://img.shields.io/github/last-commit/yekaiwu/fraud-desk?style=flat-square" alt="Last commit"></a>
  <a href="https://github.com/yekaiwu/fraud-desk/issues/new/choose"><img src="https://img.shields.io/badge/testers-welcome-1b5e46?style=flat-square" alt="Testers welcome"></a>
</p>

[Features](#key-features) · [See it](#see-it) · [How it works](#how-it-works) · [Try it](#try-it) · [Results](#results) · [Contributors](#contributors)

</div>

---

## Key features

Fraud Desk reads a bundle of documents, runs the checks an analyst would do by hand, and ranks the cases that need a person.

- **Riskiest cases first.** A score and a one-line plain-English reason for each case. Analysts make every decision.
- **Evidence you can see.** Each flag is highlighted on the exact figure on the exact page, and every score shows its coverage: which checks ran and which pages went unreviewed.
- **69 rule-based checks plus 5 model-based, across 13 document types.** The rule-based ones cover statement, invoice, receipt and financial-statement arithmetic, ID and ownership records, cross-document reconciliation, name matching, file forensics. The model-based ones read meaning and quote the text they rely on (name linking is opt-in). Optional outside sources: ACRA company lookup, sanctions lists, GLEIF ([docs/verification-sources.md](docs/verification-sources.md)).
- **Review team and workflow.** Four specialist reviewers and a case officer summarise each flagged case. Verdicts need a reason, high-severity dismissals need a second approver, and everything lands in an append-only audit log ([docs/agents.md](docs/agents.md)).
- **Your own checks, optional.** Describe a check in plain English and a fixed engine runs it, with a dry run first ([docs/custom-rules.md](docs/custom-rules.md)).
- **Safe by design.** Optional encryption at rest (off by default), 71 forgery attacks in the red-team set (the ones that still get through are documented), per-case and per-day spend caps, and it can run fully in-house on a local model ([docs/red-team.md](docs/red-team.md), [docs/local-llm.md](docs/local-llm.md)).

## See it

<div align="center">
  <img src="assets/screens/worklist.png" alt="The worklist: cases ranked by risk, with the reason, lane, checks completed and waiting time" width="900">
  <br><sub><b>The worklist.</b> Riskiest first, with the reason and how many checks ran.</sub>
</div>

<br>

<div align="center">
  <img src="assets/screens/case.png" alt="A case: risk score, the reasons in plain English, and the flagged figure highlighted on the bank statement" width="900">
  <br><sub><b>A case.</b> The score, the reasons, and the flagged figure highlighted on the document.</sub>
</div>

<details>
<summary>On a phone, and the control-performance page</summary>

<p align="center">
  <img src="assets/screens/worklist-mobile.png" alt="The worklist on a phone" width="31%">
  &nbsp;&nbsp;
  <img src="assets/screens/case-mobile.png" alt="A case on a phone" width="31%">
</p>
<div align="center">
  <img src="assets/screens/performance.png" alt="The control performance page" width="900">
</div>

</details>

*Screenshots show built-in mock data. Everything is fictitious.*

## How it works

```mermaid
flowchart LR
  A([Upload a bundle]) --> B[Ingest<br/>text layer or OCR]
  B --> C[Classify and extract]
  C --> E[Checks and forensics]
  E --> F[Cross-document checks<br/>and outside sources]
  F --> R[Review team]
  R --> G[Score with coverage]
  G --> H[[Ranked worklist]]
  H --> I{{A person decides}}
  I --> J[(Audit log)]
```

PDFs and images are read from the text layer first, then by OCR (Tesseract) for scans, and Claude Haiku 5.5 turns the text into fields. All extracted text is untrusted input, and the model has no tools: it can only fill a schema.

## Try it

A hosted demo is open to invited testers. Use the demo cases in the worklist, or make your own fictitious documents (a word processor and "save as PDF" is enough) and change an amount, paste in a page from another file, or try an odd layout.

**Fictitious documents only.** Page text and images are sent to a third-party model for reading. Share what you find with the [*Test result* template](https://github.com/yekaiwu/fraud-desk/issues/new/choose); for security issues see [SECURITY.md](SECURITY.md).

## Results

Measured on fictitious test documents ([method](docs/test-data-and-eval.md)).

| What was measured | Result |
|:--|:--|
| Digital PDFs, core tampers: caught | 464 / 465 (development seed), 465 / 465 (held-out seed) |
| Digital PDFs, all measured tamper rows: caught | 503 / 520 (development), 505 / 520 (held-out) |
| Digital PDFs: clean bundles falsely flagged | 0 / 33 |
| Scans, four edit types, one seed: caught | 38 / 40 (real Tesseract plus Haiku) |
| Scans, simulated, all tamper types, held-out seed: caught | 66 / 89 |
| Scans: clean bundles falsely flagged | 3 / 75 |
| Cost per case with Haiku, scanned, review team on | about $0.09 clean, about $0.14 tampered ([docs/agents.md](docs/agents.md)) |

Runs from 27 October to 3 November 2026. The misses, and what these numbers do not prove, are in [docs/limitations.md](docs/limitations.md).

## For developers

Start at [docs/README.md](docs/README.md) (architecture, data model, API, evaluation design) and [docs/limitations.md](docs/limitations.md). To contribute, see [CONTRIBUTING.md](CONTRIBUTING.md). To run it yourself, see [docs/deploy.md](docs/deploy.md).

## Contributors

<div align="center">
<table>
  <tr>
    <td align="center" width="140">
      <a href="https://github.com/yekaiwu"><img src="https://github.com/yekaiwu.png?size=120" width="88" alt="@yekaiwu"></a><br>
      <a href="https://github.com/yekaiwu"><b>@yekaiwu</b></a>
    </td>
    <td align="center" width="140">
      <a href="https://github.com/kenzieyii"><img src="https://github.com/kenzieyii.png?size=120" width="88" alt="@kenzieyii"></a><br>
      <a href="https://github.com/kenzieyii"><b>@kenzieyii</b></a>
    </td>
    <td align="center" width="140">
      <a href="https://github.com/AnandaTris"><img src="https://github.com/AnandaTris.png?size=120" width="88" alt="@AnandaTris"></a><br>
      <a href="https://github.com/AnandaTris"><b>@AnandaTris</b></a>
    </td>
    <td align="center" width="140">
      <a href="https://github.com/Galiative"><img src="https://github.com/Galiative.png?size=120" width="88" alt="@Galiative"></a><br>
      <a href="https://github.com/Galiative"><b>@Galiative</b></a>
    </td>
    <td align="center" width="140">
      <a href="https://github.com/jia4152"><img src="https://github.com/jia4152.png?size=120" width="88" alt="@jia4152"></a><br>
      <a href="https://github.com/jia4152"><b>@jia4152</b></a>
    </td>
  </tr>
</table>

</div>

## License

[MIT](LICENSE). Copyright (c) 2026 yekaiwu.

<div align="center">
<sub>Fraud Desk is a research prototype that supports human reviewers. All data shown is fictitious.</sub>
</div>

<!-- Invisible touch: a collaboration between yekaiwu and Muse (Lapis). No content or behaviour changes. -->

<!-- AI assistance: this comment and the commit that added it were drafted by
     DeepSeek V4.1 Flash running inside OpenCode, at the repository owner's
     request. Comment-only: no content, code or behaviour changes. -->

<!-- AI assistance: this comment and the commit that added it were drafted by
     GLM 5.3 Flash running inside OpenCode, at the repository owner's request.
     Comment-only: no content, code or behaviour changes.​ -->
<!-- Stamp: Muse Code was here too. Invisible, no content or behaviour changes. -->
