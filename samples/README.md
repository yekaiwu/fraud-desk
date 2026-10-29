# samples/

Generated mock bundles for the GLDB document-fraud analysis tool. **Everything here is fictitious.** Names, UENs, NRIC numbers, bank accounts, invoices and financial statements are invented by `backend/scripts/make_bundle.py`; no real person, company, bank or registry record is involved. Every page carries the footer `SPECIMEN - FICTITIOUS DATA - NOT A REAL DOCUMENT`.

Only this README is committed (`.gitignore`); everything else is a build output you regenerate. Specs: `docs/test-data-and-eval.md` (sections 3 and 4) and `docs/data-model.md` (section 8, registry).

## Regenerate

All commands run from the repo root with `uv run`; they never read the wall clock (`--today` defaults to 2026-10-27) and are seeded (`--seed 4002`), so two runs give byte-identical JSON and digital PDFs.

```
# the three demo loan bundles + samples/registry.json (a few seconds)
uv run python backend/scripts/make_bundle.py --scenario loan --variant all --out samples

# a control with every render: digital PDFs plus scan_hq, scan_lq, photo (JPEG per page) and scan_pdf
uv run python backend/scripts/make_bundle.py --scenario loan --variant clean --render digital,scan_hq,scan_lq,photo,scan_pdf --out samples/scans

# one document packet per bundle (C2 segmentation stretch)
uv run python backend/scripts/make_bundle.py --scenario loan --variant clean --packet --out samples/packet

# a different statement layout: dbs_style | ocbc_style | uob_style | software
uv run python backend/scripts/make_bundle.py --scenario loan --variant clean --layout ocbc_style --out samples/ocbc

# clean pool (companies 1..N, layouts cycle by index) and the hard-quirk bundle (company 90)
uv run python backend/scripts/make_bundle.py --scenario loan --clean-pool 10 --out samples
uv run python backend/scripts/make_bundle.py --scenario loan --quirk-hard --out samples

# classifier gate set (80 pages) and injection canaries
uv run python backend/scripts/make_bundle.py --gate-set --out samples
uv run python backend/scripts/make_bundle.py --canaries --out samples

# JSON Schemas of the manifest and the generator models, and the invariant check
uv run python backend/scripts/make_bundle.py --dump-schemas samples/schemas
uv run python backend/scripts/make_bundle.py --verify samples/bundles/loan-clean
```

`--scenario kyc|aml|all` and `--fairness` need the KYC/AML builder (`_gen/bundle_kyc.py`, `_gen/bundle_aml.py`, `_gen/kyc_fairness.py`). Until it exists the CLI prints `builder for scenario 'kyc' not available yet` (or `fairness set needs gen-kyc`) and carries on. Builders are auto-discovered: every `backend/scripts/_gen/bundle_*.py` that defines `NAME` and `build(spec, out_dir)` is picked up, so adding one never edits `make_bundle.py`. Python imports use `scripts._gen.*` (with `backend` on the path).

A bundle id names the company and variant only (`loan-clean`), so variants of the same bundle (other renders, layouts) go to their own `--out` folder, as above; rebuilding a bundle replaces its folder.

Each build runs `verify_bundle` (world invariants W1-W14 read from `ground_truth.json`) and exits non-zero if one fails.

## Layout

```
samples/
  README.md                      this file
  registry.json                  mock ACRA-shaped registry (docs/data-model.md section 8): union of every company below, sorted by uen
  schemas/*.schema.json          from --dump-schemas
  bundles/<bundle_id>/           "loan-clean", "loan-fraud", "loan-forger"; "loan-clean-c07" for company index 7
    manifest.json  labels.json  ground_truth.json
    digital/<stem>.pdf           one file per document (statements: one PDF per month)
    scan_hq/<stem>_p<NN>.jpg     one JPEG per page; scan_lq/ and photo/ likewise
    scan_pdf/<stem>.pdf          image-only PDF per document (JPEG q85 inside)
    packet/packet.pdf            only with --packet
  gate/                          classifier gate set: labels.json, ground_truth.json, manifest.json, files/
  canaries/<canary_id>/          one-document bundles: canary-<host>-P1..P4 and canary-<host>-control
```

JSON files are UTF-8, `sort_keys`, 2-space indent, trailing newline. Amounts are decimal strings, dates ISO, percentages in percentage points. Paths inside a manifest are relative to the manifest directory.

* `manifest.json`: spec (including the explicit list of `mutations`, so a manifest regenerates itself), files with `sha256` and per-page `page_sha256 = sha256("<file sha256>:<page>")`, documents, `upload_order` per render, `planted` fraud (with the target document, field or row and a box), `expected` outcomes (`must_fire`, `must_not_fire`, lane, band, verdict), and the honest `quirks` with the rules each may trigger.
* `labels.json`: one entry per page of every rendered file (C1 type, issuer/layout, `segment_start`), per-document C3 (`dbs|ocbc|uob|software_generated`) and per-row C4 (credit rows only) and C5 labels with the counterparty.
* `ground_truth.json`: **what is printed** (a tampered file's truth contains the edit): every schema field and table of data-model section 6 with `value`, `raw` and `box`, plus `pages[].lines` (every printed line with its box) for every page of every render. With scan renders each box also has `boxes.<render>`: the digital box pushed through the same geometric transform as the pixels.

## The loan bundles (plan 0.5, 9.1)

Company 0 is **Meridian Fastener Supplies Pte. Ltd.** (UEN generated so `stdnum.sg.uen.is_valid` is true; directors Lim Hui Min and Mohd Faizal bin Rahmat). Statements 2025-07 to 2025-12 (default layout `dbs_style`), financial statements FY2024 and FY2025, 14 invoices, 3 contracts, 4 purchase orders, a company profile and two NRIC cards. 32 documents, about 70 pages.

| Bundle | What it is |
|---|---|
| `loan-clean` | the control: all invariants hold; honest quirks Q01-Q08, Q10-Q12 (Q14 on odd company indexes) are present on purpose |
| `loan-fraud` | the deck example: FY2025 revenue printed as 2,400,000.00 against about 900,000.00 of annualised trade credits; 4 invoices marked paid with no matching credit; 3 invoices at least 10% over their contract; the slip: gross profit, PBT and net profit are not updated, so `fs_subtotals` can see it |
| `loan-forger` | the same story, every subtotal, retained earnings, trade receivables and the working-capital line recomputed, clean metadata: only the cross-document rules can see it |

File stems: `bank_statement_<YYYY-MM>`, `invoice_<INV-2025-0101>`, `financial_statement_<FY2024|FY2025>`, `contract_<n>`, `purchase_order_<n>`, `national_id_<n>`, `company_profile`. `doc_key` is `<doc_type>:<suffix>` (`bank_statement:2025-09`, `company_profile:-`).

Statement layouts (invented, not real bank templates) and `Info` producer strings, which `forensics` uses as the normal-producer table:

| Layout (C3) | Producer / Creator | Columns | Dates | Page marker |
|---|---|---|---|---|
| `dbs_style` (`dbs`) | `Mock Composer 9.1` / `Mock Statement Engine` | Date, Description, Withdrawals, Deposits, Balance | `05 Mar 2026` | `Page 1 of 3` |
| `ocbc_style` (`ocbc`) | `Mock Exstream 16.4` / `Mock Statement Engine` | Date, Value Date, Description, Cheque, Withdrawal, Deposit, Balance | `05/03/2026` | `Page 1/3` |
| `uob_style` (`uob`) | `Mock FOP 2.9` / `Mock Statement Engine` | Date, Transaction, Amount (CR/DR suffix), Balance | `05-Mar-2026` | `3 pages, this is page 1` |
| `software` (`software_generated`) | `Mock Ledger 5.0` / `Mock Ledger Desktop` | Date, Description, Debit, Credit, Balance | `2026-03-05` | `1/3` |

Other producers: invoices, contracts, purchase orders `Mock Ledger 5.0`; financial statements `Mock Word Processor 3.1`; profile `Mock Registry Portal 2.0`; ID cards `Mock ID Imager 1.0`; scans `Mock Scanner 2.1`. Honest PDFs have `ModDate == CreationDate` (document date + 1 day 09:00 +08:00). `ocbc_style` statements carry a T&C page labelled `boilerplate` and a placeholder signature (three `%%EOF`, `/ByteRange` reaching end of file, no cryptographic signature).

## Limits (docs section 8)

* **Upper bound.** We write the documents, the layouts and the answer sheet. Recall and ranking measured on this data are not real-world numbers.
* **Oracle extraction on scans.** Without an API key scan results measure rules and image forensics, not OCR. Scan renders are one JPEG per page, so the oracle's page filter sees page 1 of every JPEG (see the generator report); `boxes.<render>` carry the per-render geometry.
* **Our layouts only.** Four invented statement layouts; no `other_bank`, hanzi names, payslips, tax returns, proof of address or trade documents.
* **No holiday calendar.** Row dates are weekdays only. Loan statements end 2025-12-31 so year end, cash tie and revenue overlap line up; no check reads recency.
* **Mock registry only,** shaped like an ACRA profile, not ACRA data. The GST number format, `ubo_threshold_percent=25.00` (printed on KYC charts) and MRZ vectors follow general knowledge and plan 1.4 and 4.6; confirm against IRAS, Notice 626 and ICAO 9303.
* **Mock signatures.** Signature images are drawn; the `/ByteRange` placeholder only exercises `pdf_bytes_after_signature`. No claim is made about signature authenticity.
* **Gate set and canaries** contain only the document types the installed builders produce; the gate command prints per-label shortfalls (KYC labels need gen-kyc). The kyc-clean ownership-chart canary host is added automatically once a KYC builder exists.
