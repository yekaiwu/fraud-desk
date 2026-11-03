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

Fraud Desk reads a document bundle, runs the checks an analyst would do by hand, and ranks the cases that need a person, with the figure on the page that raised each flag.

> [!IMPORTANT]
> - **Riskiest cases first.** Each case gets a score and a one-line plain-English reason. Analysts make every decision.
> - **Evidence you can see.** Every flag is highlighted on the exact figure on the exact page.
> - **Coverage on every score.** It shows how many applicable checks ran and which pages went unreviewed.
> - **73 checks across 13 document types.** Statement, invoice, receipt and financial-statement arithmetic, ID and ownership records, cross-document reconciliation, name matching across transliterations, file forensics, and a live company-register lookup (Singapore's open ACRA data). Opt-in outside sources cover sanctions lists, GLEIF, OneMap and the IRAS GST register ([docs/verification-sources.md](docs/verification-sources.md)). Five model-based checks read meaning, such as an invoice whose goods do not fit the business, and each quotes the text it relies on ([docs/semantic-signals.md](docs/semantic-signals.md)).
> - **Review team.** Four specialists group the checks by what they examine, and a case officer writes a plain-English summary and next steps for each flagged case ([docs/agents.md](docs/agents.md)).
> - **Review workflow.** Reasons on every verdict, a second approver for high-severity dismissals, an append-only audit log and an exportable case pack.
> - **Encrypted at rest.** Uploads, page images, case data and review entries are encrypted with AES-256-GCM ([docs/encryption-at-rest.md](docs/encryption-at-rest.md)).
> - **Runs in-house.** The whole reading path also runs on a local model, and private mode blocks every outbound call ([docs/local-llm.md](docs/local-llm.md)).
> - **Red-teamed.** An independent attacker tried 71 forgery attacks in 10 families, and every gap found became a permanent test ([docs/red-team.md](docs/red-team.md)).
> - **Reads scans.** Tesseract reads the page and Claude Haiku 5.5 turns the text into fields, at about $0.10 per scanned case ([docs/anthropic-haiku.md](docs/anthropic-haiku.md)).
> - **Predictable spend.** Per-case and per-day dollar caps.
> - **Notifications bell.** Monitoring alerts and incidents appear on the page itself, each with what to do about it ([docs/ai-incidents.md](docs/ai-incidents.md)).

## See it

<div align="center">
  <img src="assets/screens/worklist.png" alt="The worklist: cases ranked by risk, with the reason, lane, checks completed and waiting time" width="900">
  <br><sub><b>The worklist.</b> Riskiest first, with the reason, how many checks ran and how long each case has waited.</sub>
</div>

<br>

<div align="center">
  <img src="assets/screens/case.png" alt="A case: risk score, the reasons in plain English, and the flagged figure highlighted on the bank statement" width="900">
  <br><sub><b>A case.</b> The score, the reasons in plain English, and the flagged figure highlighted on the document.</sub>
</div>

<br>

<details>
<summary><b>On a phone</b> · the same flow at 390 px</summary>

<p align="center">
  <img src="assets/screens/worklist-mobile.png" alt="The worklist on a phone" width="31%">
  &nbsp;&nbsp;
  <img src="assets/screens/case-mobile.png" alt="A case on a phone" width="31%">
</p>

</details>

<details>
<summary><b>Control performance</b> · which checks earn their keep</summary>

<div align="center">
  <img src="assets/screens/performance.png" alt="The control performance page" width="900">
</div>

</details>

*Screenshots show the built-in mock data ("Example Trading Pte. Ltd." and friends). Everything is fictitious.*

## How it works

```mermaid
flowchart LR
  A([Upload a bundle]) --> B[Ingest<br/>text layer or OCR]
  B --> C[Classify each page]
  C --> D[Extract the fields<br/>with a completeness check]
  D --> E[Checks and forensics]
  E --> F[Cross-document checks<br/>and opt-in outside sources]
  F --> K[Second reading<br/>of scan flags]
  K --> R[Review team]
  R --> G[Score with coverage]
  G --> H[[Ranked worklist]]
  H --> I{{A person decides}}
  I --> J[(Audit log)]
  G -.-> N[Notifications bell]
```

Fraud Desk reads PDFs and images: the text layer first, then OCR for scans. A flag on scanned pages is kept only if a different reading of the same pages reproduces it. All extracted text is treated as untrusted input, and the model has no tools, so all it can do is fill a schema.

## Try it

A hosted demo is open to invited testers. Use the demo cases already in the worklist, or make your own fictitious documents (a word processor and "save as PDF" is enough) and see what it catches: change an amount, paste in a page from another file, or try an unusual layout.

**Use fictitious documents only.** Page text and images are sent to a third-party model for reading, so never upload anything real.

**Share what you find** with the [*Test result* issue template](https://github.com/yekaiwu/fraud-desk/issues/new/choose). To report a security issue, see [SECURITY.md](SECURITY.md).

## Results

Measured on fictitious documents built for testing, with the checks run offline and Claude Haiku 5.5 reading scans.

| What was measured | Result |
|:--|:--|
| Digital PDFs: tampered cases caught | 464 / 465 on the development seed, 465 / 465 on a held-out seed |
| Digital PDFs: clean bundles falsely flagged | 0 / 33 |
| Scans: tampered bundles caught | 38 / 40 on a fresh seed, four edit types |
| Scans: clean bundles falsely flagged | 3 / 75 |
| Public invoice set: totals read | 48 / 48 labelled invoices |
| Cost per case with Haiku | about $0.006 digital, about $0.098 scanned |

The data, the tamper catalogue and the metrics are defined in [docs/test-data-and-eval.md](docs/test-data-and-eval.md).

## For developers

Source, specs and the evaluation design are in this repository: start at [docs/README.md](docs/README.md) (`architecture.md`, `data-model.md`, `api.md`, and `test-data-and-eval.md`). More: [scoring-and-queue.md](docs/scoring-and-queue.md), [forensics.md](docs/forensics.md), [limitations.md](docs/limitations.md). Code contributions: [CONTRIBUTING.md](CONTRIBUTING.md).

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
