<div align="center">

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

<div align="center">
  <img src="https://img.shields.io/badge/held--out%20digital%3A%20tampered%20caught-465%20%2F%20465-1b5e46?style=for-the-badge" alt="465 of 465 tampered digital cases caught on the held-out seed">
  <img src="https://img.shields.io/badge/digital%3A%20clean%20falsely%20flagged-0%20%2F%2033-1b5e46?style=for-the-badge" alt="0 of 33 clean digital bundles falsely flagged">
  <img src="https://img.shields.io/badge/checks-69%20rule--based%20%2B%205%20model--based-2f4858?style=for-the-badge" alt="69 rule-based and 5 model-based checks">
  <img src="https://img.shields.io/badge/forgery%20attacks%20red--teamed-71-b8893b?style=for-the-badge" alt="71 forgery attacks red-teamed">
  <img src="https://img.shields.io/badge/automated%20tests-6%2C800%2B-3776AB?style=for-the-badge" alt="More than 6,800 automated tests">
</div>

<br>

<table>
  <tr>
    <td valign="top" width="50%">
      <h4>🎯 Riskiest cases first</h4>
      A score and a one-line plain-English reason for every case, so the analyst opens the right one next. People make every decision.
    </td>
    <td valign="top" width="50%">
      <h4>🔎 Evidence you can see</h4>
      Each flag is drawn on the exact figure on the exact page. Every score shows its coverage: which checks ran and which pages went unreviewed.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <h4>🧪 69 rule-based checks plus 5 model-based</h4>
      Across 13 document types: statement, invoice, receipt and financial-statement arithmetic, ID and ownership records, cross-document reconciliation, name matching, file forensics. The model-based checks read meaning and quote the text they rely on. Optional outside sources: ACRA, sanctions lists, GLEIF (<a href="docs/verification-sources.md">details</a>).
    </td>
    <td valign="top">
      <h4>🧩 Write your own check</h4>
      In the Check catalogue, describe a check in plain English. A fixed engine runs it, shows exactly what it will and will not check, and tests it on stored cases first. Anything it cannot do goes to the tech team as a request (<a href="docs/custom-rules.md">details</a>).
    </td>
  </tr>
  <tr>
    <td valign="top">
      <h4>👥 Review team and audit trail</h4>
      Four specialist reviewers and a case officer summarise each flagged case. Verdicts need a reason, high-severity dismissals need a second approver, and everything lands in an append-only log (<a href="docs/agents.md">details</a>).
    </td>
    <td valign="top">
      <h4>🔒 Safe by design</h4>
      Encrypted at rest, red-teamed with 71 forgery attacks, per-case and per-day spend caps, and it can run fully in-house on a local model (<a href="docs/red-team.md">red team</a>, <a href="docs/local-llm.md">local model</a>).
    </td>
  </tr>
</table>

<sub>Numbers are measured on fictitious test documents; see [Results](#results) for the method and the misses.</sub>

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
| Digital PDFs: tampered cases caught | 464 / 465 (development seed), 465 / 465 (held-out seed) |
| Digital PDFs: clean bundles falsely flagged | 0 / 33 |
| Scans: tampered bundles caught | 38 / 40 |
| Scans: clean bundles falsely flagged | 3 / 75 |
| Cost per case with Haiku | about 1 cent digital, about 10 cents scanned |

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
