# Checks: financial documents (bank statement, invoice, financial statement, cross-document)

Build spec for plan 4.1 to 4.4. Builds against [architecture.md](architecture.md) (check framework, section 4) and [data-model.md](data-model.md) (field and column names, sections 2 and 6). Plan refs ("plan 4.2") point to the master plan (not included in this repo). All data is fictitious. Today is `ctx.today`; never the wall clock.

## 1. Purpose

Deterministic, explainable consistency checks over structured extraction output. Every rule takes a `CaseContext`, reads `Document.fields` / `Table` rows, and returns `Flag`s that carry a rule id, evidence (numbers) and a one-sentence explanation. These rules are the high-yield core of the tool (plan 1.3): arithmetic identities, reconciliation across documents, duplicate detection and a few statistical hints. They never judge authenticity, only consistency.

## 2. Scope

**In** (27 plan rules plus `file_reuse`, added at R0 to give plan 0.4 "duplicate document detection: file hash" a rule id; section 7.6):

| Family | Rules | Plan |
|---|---|---|
| Bank statement | `bs_totals`, `bs_running_balance`, `bs_period_continuity`, `bs_dates`, `bs_holder`, `bs_page_chain`, `bs_duplicates`, `bs_round_numbers`, `bs_benford` | 4.1 |
| Invoice | `inv_lines`, `inv_gst`, `inv_total`, `inv_ids`, `inv_dates`, `inv_parties`, `inv_duplicate`, `inv_sequence` | 4.2 |
| Financial statement | `fs_balance`, `fs_subtotals`, `fs_cash_tie`, `fs_comparatives`, `fs_entity` | 4.3 |
| Cross-document | `x_entity`, `x_cash_at_year_end`, `x_revenue_vs_credits`, `x_invoice_vs_receipts`, `x_invoice_vs_contract` | 4.4 |
| Duplicate files | `file_reuse` (not a plan 4.x id; listed under "Registered but not in the plan" in the catalogue) | 0.4, 1.2 |

Also in: the invoice fingerprint definition and store usage (4.2), UEN validation with `python-stdnum`, the GST number format caveat, the company-name matching algorithm used by these rules (section 3.4), and the explicit list of statistical-hint rules (section 7).

**Out** (owned elsewhere; do not build here):
- `bs_pass_through`, `x_declared_vs_activity`, `x_sof_support`, `x_remitter_vs_buyer`, `x_ownership_vs_registry`, `x_directors`, `kyc_*`, `id_*`, `passport_mrz`, `date_valid`: [checks-kyc-aml.md](checks-kyc-aml.md) (plan 4.6; `aml.py` / `kyc.py`).
- All PDF/image forensics (plan 4.5): [forensics.md](forensics.md).
- Extraction, C4/C5 annotation producers, applicant resolution: [ingest-and-extraction.md](ingest-and-extraction.md), architecture.md section 3, data-model.md section 4.1.
- Grouping flags into findings, hard-stop list, scoring weights, coverage arithmetic: [scoring-and-queue.md](scoring-and-queue.md). Several rules below fire together on one edit (for example `bs_running_balance` and `bs_totals`); counting them once is the scorer's job.
- (Resolved at R0) repeated `file_sha256` uploads are now `file_reuse`, section 7.6.

## 3. Interfaces

### 3.1 Modules and registration

Each module is auto-discovered (architecture.md section 4). Every function has the signature `def <name>(ctx: CaseContext) -> list[Flag]` and is decorated with `@check(...)`. All rules use `version="1"`.

| Module (owner) | Function = rule id | stage | severity | applies_to (any-of) | requires (all-of) | use_cases |
|---|---|---|---|---|---|---|
| `bank_statement.py` (rules-bank; foundation owns `bs_running_balance` first) | `bs_totals` | rules | high | `bank_statement` | | kyc, aml, loan |
| | `bs_running_balance` | rules | high | `bank_statement` | | kyc, aml, loan |
| | `bs_period_continuity` | rules | high | `bank_statement` | | kyc, aml, loan |
| | `bs_dates` | rules | medium | `bank_statement` | | kyc, aml, loan |
| | `bs_holder` | rules | high | `bank_statement` | | kyc, aml, loan |
| | `bs_page_chain` | rules | medium | `bank_statement` | | kyc, aml, loan |
| | `bs_duplicates` | rules | low | `bank_statement` | | kyc, aml, loan |
| | `bs_round_numbers` | rules | low | `bank_statement` | | kyc, aml, loan |
| | `bs_benford` | rules | info | `bank_statement` | | kyc, aml, loan |
| `invoice.py` (rules-inv-fs-cross) | `inv_lines`, `inv_gst`, `inv_total`, `inv_ids` | rules | high | `invoice` | | aml, loan |
| | `inv_dates`, `inv_parties` | rules | medium | `invoice` | | aml, loan |
| | `inv_duplicate` | rules | high | `invoice` | | aml, loan |
| | `inv_sequence` | rules | low | `invoice` | | aml, loan |
| `financial_statement.py` | `fs_balance`, `fs_subtotals`, `fs_cash_tie`, `fs_comparatives` | rules | high | `financial_statement` | | aml, loan |
| | `fs_entity` | rules | medium | `financial_statement` | | aml, loan |
| `crossdoc.py` | `x_entity` | crossdoc | high | `bank_statement, invoice, financial_statement, contract, purchase_order, company_profile, certificate_of_incorporation, ownership_chart, shareholder_register, source_of_funds_declaration` | | kyc, aml, loan |
| | `x_cash_at_year_end` | crossdoc | medium | `financial_statement` | `bank_statement` | aml, loan |
| | `x_revenue_vs_credits` | crossdoc | medium | `financial_statement` | `bank_statement` | aml, loan |
| | `x_invoice_vs_receipts` | crossdoc | medium | `invoice` | `bank_statement` | aml, loan |
| | `x_invoice_vs_contract` | crossdoc | medium | `contract, purchase_order` | `invoice` | aml, loan |
| | `file_reuse` | crossdoc | medium | `*` | | kyc, aml, loan |

Rationale for `use_cases`: plan 0.4 document families put bank statements in all three bundles, invoices and financial statements in AML and loan only. Listing a rule under a use case that never carries the document would lower coverage for no reason.

The `tamper` text in each `@check` is the "Tamper" line of the rule's subsection. The `description` is the first sentence of its "Logic".

### 3.2 Shared helper contracts

`company_key`, `company_match`, `company_disc`, `uen_disc` are imported from `app.core.textnorm` (foundation, wave 0; R0 accepted Contract request 1), never copied. The small helpers below are private to each module (about 15 lines each); their signatures and behaviour are normative.

```python
def _tol(ctx: CaseContext) -> Decimal:             # ctx.settings.money_tolerance (default Decimal("1.00"))
def _cell_money(cell: Cell) -> Decimal | None:     # cell.money(); None when value is None
def _unverified(cells: Mapping[str, Cell]) -> str: # "" if every cell has verified is not False and alt_raw is None;
    # else "; unverified reading: " + ", ".join(sorted(names)). Cells whose verified is False or alt_raw is not None.
def _fs_tol(ctx: CaseContext, doc: Document, terms: int) -> Decimal:
    # scale = int(doc.fields["scale_printed"].value or "1")
    # scale == 1 -> _tol(ctx); else Decimal(scale) * max(1, terms // 2)     (rounding units, section 6.0)
```

### 3.3 Conventions that apply to every rule

1. **Tolerance.** Amount equality is `approx_eq(a, b, tol)` with `tol = ctx.settings.money_tolerance` unless the rule says otherwise. Comparisons are inclusive (`abs(a-b) <= tol`). A difference of exactly 1.00 passes.
2. **Rounding.** Expected values are computed in `Decimal` and quantized to `0.01` with `ROUND_HALF_UP` before comparison. No `float` for money (data-model.md section 2).
3. **Missing inputs.** A rule loops over its documents. A document with a needed value missing (`Cell.value is None`) is silently not evaluated. The rule raises `CheckSkipped(reason)` only when **no** document (or pair, group) was evaluable. At least one evaluated document means the check "ran" (architecture.md section 4). Rules that must tell the analyst about unevaluable items emit one `info` flag (stated per rule); silence is never the only record.
4. **Row numbering.** `Flag.row` is 0-based. Evidence strings and explanations print 1-based row numbers (`row 4`) to match the UI (data-model.md section 1).
5. **Pointers.** Field-level flag: `field=<name>`, `region=<that Cell.box>`. Row-level flag: `table`, `row`, `region=<offending cell box>`; related cells go in `extra_regions` (max 4). A `None` box gives `region=None`; the flag is still emitted.
6. **Unverified readings.** If any cell used in a flag has `verified is False` or `alt_raw is not None`, append `_unverified(...)` to the evidence. Severity is unchanged: the extraction cascade already tried the strong model (architecture.md section 5); the analyst decides.
7. **Caps.** At most 5 flags per rule per document unless stated; when capped, the last flag's evidence ends `; +N more`. Evidence max 1000 chars, explanation max 300 (the model truncates; templates below are shorter).
8. **Evidence format.** `key value` pairs separated by `; `, amounts as `fmt_money`, dates ISO, no prose padding. Explanation: one plain sentence, no jargon beyond the field names, never accusing ("does not match", never "forged").
9. **Severity overrides** per flag are allowed through `make_flag(severity=...)`; the meta severity is the default. Overrides are listed per rule.
10. **Determinism.** Iterate documents in id order (`ctx.docs(...)`), rows in printed order, groups sorted by key. No clock, no randomness, no network, no mutation of `ctx`.
11. **Untrusted text.** Names and descriptions from documents appear in evidence only after whitespace-collapse and truncation to 60 chars. They are never used as paths, SQL, regex patterns or format strings.
12. **Currency.** A rule comparing amounts across two documents requires equal `currency`; otherwise that pair is not evaluated (convention 3), or the rule raises `CheckSkipped("currency mismatch")` when it was the only candidate.

### 3.4 Company-name matching (normative; used by `bs_holder`, `inv_parties`, `fs_entity`, `x_*`)

```python
# backend/app/core/textnorm.py (foundation)
def company_key(name: str) -> str: ...
def company_match(a: str, b: str) -> bool: ...
def company_disc(name: str) -> str: ...   # discriminator form: tokens of company_key(name), lower-cased, sorted, joined "_", [a-z0-9_] only
def uen_disc(uen: str) -> str: ...        # "uen_" + lower-cased alphanumerics
COMPANY_MATCH_MIN = 0.92
```
**Entity discriminators (R0, so the scorer's `entity` family groups one foreign company once):** `bs_holder`, `fs_entity` and `x_entity` set `discriminator = uen_disc(<the document's UEN>)` when the mismatch is decided by UEN, else `company_disc(<the document's mismatching name>)`. `x_entity` part (B) uses `uen_disc(<conflicting uen>)` for a UEN conflict and `company_disc(<name>)` for a name conflict. The same foreign company on a statement and an FS therefore becomes one finding; two different foreign companies stay two.
`company_key`: NFKC, upper-case; `&` becomes `AND`; characters `.,'"()-/` become a space (but keep the literal tokens `T/A` before this step, see below); collapse whitespace; strip trailing legal-form tokens repeatedly while the last token is in `{PTE, LTD, PRIVATE, LIMITED, LLP, LP, CO, COMPANY, INC, CORP, CORPORATION, SDN, BHD}`. A trading-as name (`X PTE LTD T/A Y`) yields keys for `X` and for `Y`; the name matches if any key of one side matches any key of the other.
`company_match(a, b)`: an empty name (either side) never matches; otherwise true when the keys are equal, or `rapidfuzz.fuzz.token_sort_ratio(ka, kb) / 100 >= 0.92` **and** the multiset of digit-bearing tokens is equal (so `ACME 1` and `ACME 2` differ).
Reference outputs (must be unit-tested identically in every module that copies it): `("Example Logistics Pte. Ltd.", "EXAMPLE LOGISTICS PTE LTD")` true; `("ACME LOGISTICS PTE LTD", "ACME LOGISTIC PTE. LTD.")` true (ratio 96.3); `("ACME TRADING PTE LTD", "ACME TRADERS PTE LTD")` false (75.0); `("Tom & Jerry Pte Ltd", "TOM AND JERRY PRIVATE LIMITED")` true; `("ACME 1 PTE LTD", "ACME 2 PTE LTD")` false; `("", "X")` false.
This is company matching only. Person-name matching (patronymics, Chinese order) is `names.py`, owned by rules-kyc, and is not used here.

### 3.5 Fingerprint definition and store interface (`inv_duplicate`)

From data-model.md section 4.1 (not redefined, only used):

```python
def invoice_fingerprint(supplier_uen: str, invoice_number: str, invoice_date: str, total: str) -> str: ...
# sha256 hex of "inv|" + "|".join([UEN upper/no spaces, number upper/no whitespace (leading zeros kept, hyphens kept),
#                                  ISO date, total quantized to 2 dp as a plain string])
class FingerprintStore(Protocol):
    def lookup(self, kind: str, fingerprint: str, *, exclude_case_id: str | None = None) -> list[FingerprintHit]: ...
    def record(self, kind: str, fingerprint: str, *, case_id: str, document_id: str | None, meta: dict | None = None) -> None: ...
```
- **Inputs are the canonical `Cell.value` strings** of `supplier_uen`, `invoice_number`, `invoice_date`, `total`. If any of the four is `None`, no full fingerprint exists for that invoice (the key fingerprint below may).
- **Excluded on purpose:** buyer, currency, description. Re-issuing the same invoice to a second lender keeps all four values.
- **Second key (Contract request 2):** `invoice_key_fingerprint(supplier_uen: str, invoice_number: str) -> str` = sha256 hex of `"invkey|" + uen + "|" + number` with the same normalisation. Catches the same number resubmitted with an edited total or date (the "same supplier + invoice number" clause of plan 4.2). Both fingerprints are stored under `kind="invoice"`; their hex strings cannot collide because the preimage prefixes differ. Meta is `{"role": "full"}` or `{"role": "key"}`.
- **Recording** is done by the pipeline after `score` (architecture.md section 3), never by a check (checks are pure). The check only calls `ctx.fingerprints.lookup("invoice", fp, exclude_case_id=ctx.case.id)`. Excluding the current case makes reruns idempotent: a rerun does not flag the case against itself.
- **Within the case**, duplicates are found by grouping the case's own invoice documents on both keys; the store is only for other cases.
- **Privacy:** a store hit is reported by `case_id`, `document_id` and `created_at.date()` only. Never print another case's applicant, names or amounts.

### 3.6 Eval-tunable constants (module-level, upper case, so the eval can read and sweep them)

| Constant | Default | Rule |
|---|---|---|
| `BS_DATE_GRACE_DAYS` | `0` | `bs_dates` |
| `BS_DUP_MIN_AMOUNT` | `Decimal("20.00")` | `bs_duplicates` |
| `RN_ROUND_MODULUS`, `RN_MIN_CREDITS`, `RN_MIN_ROUND`, `RN_MIN_SHARE`, `RN_MIN_UPLIFT`, `RN_MIN_BASELINE_CREDITS` | `1000`, `5`, `3`, `0.50`, `0.30`, `10` | `bs_round_numbers` |
| `RN_COUNT_ALPHA`, `RN_COUNT_MIN_UPLIFT`, `RN_COUNT_LAMBDA_FLOOR` | `0.005`, `0.15`, `0.5` | `bs_round_numbers` (count trigger) |
| `BENFORD_MIN_N`, `BENFORD_MAD_MIN`, `BENFORD_CHI2_CRIT` | `100`, `0.015`, `20.09` | `bs_benford` |
| `GST_RATE_TABLE` | `[(date(2024,1,1), "9.00"), (date(2023,1,1), "8.00"), (date(2007,7,1), "7.00")]` | `inv_gst` (**[confirm on IRAS]**, plan 1.4) |
| `FS_YEAR_GAP_DAYS` | `(350, 380)` | `fs_comparatives` |
| `X_CASH_REL_TOL` | `Decimal("0.05")` | `x_cash_at_year_end` |
| `X_REV_LOW_RATIO`, `X_REV_HIGH_RATIO`, `X_REV_MIN_COVER_DAYS` | `0.60`, `1.50`, `30` | `x_revenue_vs_credits` |
| `X_RECEIPT_EARLY_DAYS`, `X_RECEIPT_AFTER_PAID_DAYS`, `X_RECEIPT_UNPAID_WINDOW_DAYS`, `X_RECEIPT_FEE_ALLOW` | `7`, `7`, `90`, `Decimal("35.00")` | `x_invoice_vs_receipts` |
| `X_CONTRACT_POST_END_DAYS` | `90` | `x_invoice_vs_contract` |

Plan 4.1 says thresholds for the statistical hints "come from the eval": the defaults above are starting points, recorded in the validation report when changed (bump the rule `version`).

## 4. Behaviour spec: bank statement (plan 4.1)

Data read: `bank_statement.fields` and tables `transactions` (`date, description, debit, credit, balance`) and `page_chain` (data-model.md section 6.1). Debits and credits are positive magnitudes; balances are signed. For a row, `amount_signed = credit - debit` (an empty cell is 0).

**Row anchoring (shared by `bs_running_balance`, `bs_totals`, `x_cash_at_year_end`).** A row with both `debit` and `credit` empty and `balance` present is a *balance-only row* (for example "BALANCE B/F"); it carries amount 0 and is checked like any other. A row with both empty and no balance is ignored. A row with both `debit` and `credit` non-empty is *invalid*: it is skipped for arithmetic, and the row's own `balance` (if present) becomes the new anchor.

Fixtures used by the tests (section 9.1): `F-BS-1` (March 2026 statement, 5 rows), `F-BS-2` (April, continues it).

### 4.1 `bs_totals`

- **Logic:** opening + Σcredits − Σdebits equals closing (±tol). Also, when the layout prints `total_debits` / `total_credits`, each equals the Σ of the corresponding column (±tol). Σ runs over all `transactions` rows (invalid rows excluded; if any were excluded, add `; N invalid row(s) excluded` to evidence).
- **Skip:** no `transactions` rows, or `opening_balance` / `closing_balance` missing.
- **Severity:** high. **Tamper:** edited amount or balance; removed or inserted rows.
- **Flags:** (a) chain mismatch: `field="closing_balance"`, region = closing cell, `extra_regions=[opening cell]`, no discriminator. (b) printed total mismatch: `field="total_debits"` or `"total_credits"`, region = that cell, `discriminator="printed_debits"` / `"printed_credits"`.
- **Evidence:** (a) `opening 10000.00 + credits 30503.21 - debits 4212.50 = 36290.71; printed closing 26290.71; diff 10000.00`. (b) `printed total_debits 4300.00; sum of 3 debit rows 4212.50; diff 87.50`.
- **Explanation (a):** `Opening balance plus deposits minus withdrawals is {computed}, but the statement prints a closing balance of {closing}.` (b) `The printed total of {debits|credits} does not equal the sum of the listed rows.`
- **Tests** (file `test_checks_bank_statement.py`):
  - `T-BS-TOT-1` clean: `F-BS-1` gives `[]`. Includes a monthly fee row (12.50) and an interest credit (3.21): bank fees are ordinary debits, not anomalies.
  - `T-BS-TOT-2` tampered: `F-BS-1` with row 4 credit changed to 18,000.00 and balances untouched: one flag (a), computed `36290.71`, diff `10000.00`.
  - `T-BS-TOT-3` printed totals: `total_debits=4212.50`, `total_credits=20503.21` pass; `total_debits=4300.00` gives flag (b) `printed_debits`, diff `87.50`.
  - `T-BS-TOT-4` boundary: closing off by exactly 1.00 passes; by 1.01 flags.
  - `T-BS-TOT-5` skip: empty `transactions` raises `CheckSkipped`.

### 4.2 `bs_running_balance` (first written by foundation; behaviour frozen by this spec)

- **Logic:** each row's printed `balance` equals the previous anchor plus the amounts since that anchor, ±tol. The first anchor is `opening_balance`. Compare **against the previous printed balance, not a recomputed running total**, so one edit produces one break and tolerance never accumulates. Report the **first broken row per document** only; evidence states how many further rows also break.
- **Rows without a printed balance** (some banks print a balance only on the last row of a day): carry the amounts forward; the expected value is `anchor + Σ(credit-debit)` of the rows since the anchor, compared at the next row that has a balance. The flag points at that balance row and the evidence says `since row K`.
- **Skip:** no rows, or `opening_balance` missing.
- **Severity:** high. **Tamper:** single edited transaction; edited balance.
- **Flag:** `table="transactions"`, `row=i`, `region=` the row's `balance` cell box, `extra_regions=[` the amount cell (debit or credit) of the same row, previous row's balance cell`]`.
- **Evidence:** `previous 35710.75 + credit 12500.00 = 48210.75, printed 58210.75; diff 10000.00; 1 later row(s) also break` (matches data-model.md section 7). Debit rows read `previous 18300.00 - debit 12.50 = 18287.50, printed ...`. Carried rows: `anchor row 2 balance 18300.00 + net 3787.50 since row 3 = 22087.50, printed ...`.
- **Explanation:** `The balance on row {n} is {abs diff} {higher|lower} than the previous balance plus the transaction amounts.`
- **Tests:**
  - `T-BS-RB-1` clean: `F-BS-1` gives `[]`.
  - `T-BS-RB-2` tampered: `F-BS-1` row 4 credit 8,000.00 changed to 18,000.00, balances unchanged: flag on `row=3`, evidence `previous 18287.50 + credit 18000.00 = 36287.50, printed 26287.50; diff 10000.00`, `region.label == "balance"`.
  - `T-BS-RB-3` balance-only edit: row 2 balance 18,300.00 changed to 19,300.00: the first break is row 2 (index 1), evidence says `1 later row(s) also break`.
  - `T-BS-RB-4` honest quirk (balance only at day end): rows 2 and 3 on the same day, row 2 balance empty, row 3 balance equals anchor plus both amounts: `[]`; same data with row 3 balance wrong flags `row=2` with `since row 2`.
  - `T-BS-RB-5` honest quirk (brought-forward row and overdraft): first row is a balance-only row equal to opening; later balances negative (`-120.50`): `[]`.
  - `T-BS-RB-6` hard case: the forger recomputes every later balance and the closing balance: `[]` (a known miss, caught only by `bs_period_continuity` or the cross-document rules; see section 11).

### 4.3 `bs_period_continuity`

- **Logic:** group `bank_statement` documents by normalised `account_number` and `currency` (documents without `account_number`, `period_start` or `period_end` are not evaluated). Sort each group by `period_start`. For every consecutive pair (A, B): (1) **gap**: `(B.period_start - A.period_end).days > 1` means a missing period; (2) **overlap**: `(B.period_start - A.period_end).days < 0`, which also covers the same month uploaded twice; (3) **balance**: `approx_eq(A.closing_balance, B.opening_balance, tol)` fails. A difference of 0 or 1 day between the end and the next start is contiguous (banks differ on whether a cycle ends and the next begins on the same day).
- **Skip:** no group has two evaluable statements.
- **Severity:** gap and balance high; overlap medium (`severity="medium"`). **Tamper:** swapped or removed month; a month edited with recomputed balances while its neighbours are untouched.
- **Flag:** anchored on B: `docs=[A, B]`, `field="opening_balance"` (balance, overlap) or `"period_start"` (gap, overlap), `region=` that cell, `extra_regions=[A.closing_balance cell]`; discriminators `gap`, `overlap`, `balance`.
- **Evidence:** `gap: A ends 2026-03-31, B starts 2026-05-01; 30 day(s) not covered`; `overlap: A ends 2026-03-31, B starts 2026-03-15 (17 days; same period uploaded twice)` (the parenthetical only when both periods are identical); `balance: A closing 26290.71, B opening 31290.71; diff 5000.00`.
- **Explanation:** gap `No statement covers {n} day(s) between these two statements.`; overlap `These two statements cover some of the same days.`; balance `The closing balance of the earlier statement does not match the opening balance of the next.`
- **Tests:**
  - `T-BS-PC-1` clean: `F-BS-1` then `F-BS-2` (closing 26290.71 = opening 26290.71, periods 03-01..03-31 and 04-01..04-30): `[]`. Also clean when B starts on the same day A ends.
  - `T-BS-PC-2` tampered (removed month): March and May: gap flag, `30 day(s) not covered`.
  - `T-BS-PC-3` tampered (hard case): March with all later balances recomputed (closing 36290.71) against an untouched April (opening 26290.71): balance flag, diff `10000.00`.
  - `T-BS-PC-4` honest quirk: non-calendar cycles (15 Mar to 14 Apr, 15 Apr to 14 May): `[]`. Two accounts (different `account_number`) with different periods: each group evaluated separately, `[]`.
  - `T-BS-PC-5` single statement: `CheckSkipped`.

### 4.4 `bs_dates`

- **Logic** per transaction row; an empty date cell (`value None`, `raw None`) inherits the previous row's date (the date is printed once per day on many layouts); an empty first-row date is ignored.
  1. **calendar:** `value is None` and `raw is not None` (the extractor could not parse it, for example `31 FEB 2026`). Medium.
  2. **order:** compute a longest non-decreasing subsequence of the dated rows (patience algorithm, `bisect_right`); every dated row outside it is out of order. This flags the moved row, not everything after it. Medium.
  3. **range:** date `< period_start - grace` or `> period_end + grace` (`BS_DATE_GRACE_DAYS = 0`, so a row on the first or last day is fine). Medium.
  4. **future:** date `> ctx.today`, or `period_end > ctx.today`. Medium; `period_start > period_end` is reported here as `field="period_start"`.
- **Skip:** no document has rows with parseable dates.
- **Severity:** medium. **Tamper:** inserted or moved rows; statement dated in the future.
- **Flag:** `table="transactions"`, `row=i`, `region=` the row's `date` cell, discriminator = the condition name (`calendar`, `order`, `range`, `future`). Cap 3 per condition per document; evidence of the last carries `+N more`.
- **Evidence:** `row 4 dated 2026-03-03 follows row 3 dated 2026-03-15 (out of order)`; `row 6 dated 2026-04-02 outside period 2026-03-01..2026-03-31`; `row 2 date "31 FEB 2026" is not a calendar date`; `row 5 dated 2026-11-02 is after today 2026-10-27`.
- **Explanation:** `The date on row {n} is out of order|outside the statement period|not a real date|in the future.`
- **Tests:**
  - `T-BS-DT-1` clean: `F-BS-1`, with row 4 dated exactly `2026-03-31` and a blank date on one row: `[]`.
  - `T-BS-DT-2` tampered (moved row): row 4's date set to `2026-03-03` after a 03-15 row: one `order` flag on `row=3` only (rows after it are not flagged).
  - `T-BS-DT-3` tampered (inserted row after period): extra row dated `2026-04-02`: one `range` flag.
  - `T-BS-DT-4` tampered: raw `31 FEB 2026`, value `None`: `calendar` flag.
  - `T-BS-DT-5` future: `period_end 2026-11-30` with `FIXED_TODAY 2026-10-27`: `future` flag; same data with `today=date(2026,12,1)` gives `[]` (proves `today` is injected).

### 4.5 `bs_holder`

- **Logic:** needs `ctx.applicant` (`name` or `uen`); otherwise `CheckSkipped("no applicant")`. Per statement:
  1. `uen` (statement) and `applicant.uen` both present and different: flag `uen`.
  2. else if both UENs present and equal: pass regardless of name differences (the name check is not run; a renamed company still owns the account).
  3. else (a UEN missing on either side): `account_holder` and `applicant.name` both present and `not company_match(...)`: flag `name`.
- **Severity:** high. **Tamper:** someone else's statement.
- **Flag:** `field="account_holder"` (name) or `"uen"` (uen), `region=` that cell; discriminator `company_disc(account_holder)` / `uen_disc(statement uen)` (section 3.4).
- **Evidence:** `account_holder "TAN WEI MING" vs applicant "EXAMPLE LOGISTICS PTE LTD"; match 0.31` (rapidfuzz ratio, 2 dp, or `no match`); `statement uen 202012345H vs applicant uen 201912345R`.
- **Explanation:** `The account holder is not the applicant company.`
- **Honest quirks handled:** `Pte. Ltd.` vs `PRIVATE LIMITED`, case, `&` vs `AND`, a trading-as suffix (section 3.4). Not handled (flagged, analyst dismisses): the account is genuinely held by a director or a related company; a legal name change.
- **Tests:**
  - `T-BS-HO-1` clean: applicant `Example Logistics Pte. Ltd.`, holder `EXAMPLE LOGISTICS PTE LTD`: `[]`.
  - `T-BS-HO-2` tampered: holder `TAN WEI MING`: `name` flag.
  - `T-BS-HO-3` UEN: names differ (`EXAMPLE LOGISTICS PTE LTD` vs `EXAMPLE LOGISTICS GROUP`) but UENs equal: `[]`; UENs differ: `uen` flag.
  - `T-BS-HO-4` skip: `applicant` empty raises `CheckSkipped`.

### 4.6 `bs_page_chain`

- **Logic** per statement with at least one `page_chain` row. Let `Y` be the most common non-empty `page_y` (ties: the larger). Conditions:
  1. **sequence:** the `page_x` values in printed order must be exactly `1, 2, ..., Y` (missing, repeated or out-of-order pages). If every `page_y` is empty (layout prints only "Page 2"), `Y = number of rows` and only consecutiveness is tested.
  2. **total:** any row with `page_y` different from `Y`.
  3. **count:** the document spans `page_end - page_start + 1` pages but the chain has a different number of rows.
  4. **account:** a row whose `account_number` differs from `fields.account_number` (per row, cap 5).
- **Skip:** no statement has `page_chain` rows (`CheckSkipped("no page markers")`).
- **Severity:** medium. **Tamper:** spliced pages.
- **Flag:** `table="page_chain"`, `row=` the first offending row (for `sequence` the row where the sequence first breaks; for `count`, the last row), `region=` that row's `page_x` cell box; discriminators `sequence`, `total`, `count`, `account`.
- **Evidence:** `page markers 1/3, 3/3; expected 1/3, 2/3, 3/3; missing page 2`; `row 2 says 2/4 but other pages say /3`; `chain lists 2 pages; document has 3`; `page 2 account 0098765432 vs 0012345678 on page 1`.
- **Explanation:** `The page numbers do not form a complete "page x of y" sequence.` / `This page carries a different account number from the rest of the statement.`
- **Tests:**
  - `T-BS-PG-1` clean: chain `1/3, 2/3, 3/3`, same account on all rows, document has 3 pages: `[]`.
  - `T-BS-PG-2` tampered: chain `1/3, 3/3`: `sequence` flag naming page 2 missing; a `count` flag only if the document spans 3 pages.
  - `T-BS-PG-3` tampered: page 2 carries another account number: one `account` flag at `row=1`.
  - `T-BS-PG-4` honest quirk: single-page statement `1/1`: `[]`; layout printing "Page 1", "Page 2" without a total (`page_y` empty): `[]`.

### 4.7 `bs_duplicates` (heuristic hint, see section 7)

- **Logic:** within one statement, rows whose key `(date, upper/collapsed description, debit, credit)` is identical, with amount (`debit` or `credit`) `>= BS_DUP_MIN_AMOUNT` (20.00). Balance is not part of the key (a copy-paste that recomputed the balance is still a duplicate). One flag per group, on the **second** occurrence.
- **Severity:** low. **Tamper:** copy-pasted rows.
- **Flag:** `table="transactions"`, `row=` second occurrence, `region=` its `description` cell, `extra_regions=[` first occurrence description (and up to 2 more)`]`. Cap 5 groups.
- **Evidence:** `rows 2 and 5 identical: 2026-03-05 "GIRO PAYMENT TO SUPPLIER ALPHA" debit 4200.00`.
- **Explanation:** `Row {n} repeats an earlier row with the same date, description and amount.`
- **Honest quirks:** small repeated charges (fees, ATM withdrawals) are legitimate: the 20.00 floor. Identical large repeats (rent, loan instalments on one day) can be legitimate, hence low severity.
- **Tests:**
  - `T-BS-DU-1` clean: `F-BS-1`: `[]`.
  - `T-BS-DU-2` tampered: row 2 copied as row 3 (same date, text, debit 4,200.00): one flag at `row=2`, evidence `rows 2 and 3 identical`.
  - `T-BS-DU-3` honest quirk: two `MONTHLY ACCOUNT FEE` debits of 12.50 on one date: `[]` (below floor).

### 4.8 `bs_round_numbers` (statistical hint, see section 7)

- **Logic:** for each statement S with at least `RN_MIN_CREDITS` (5) credit rows: round credit = `credit >= RN_ROUND_MODULUS and credit % RN_ROUND_MODULUS == 0` (1000). `share_S = round credits / credit rows`. Baseline = pooled share over **other statements of the same account** (normalised `account_number`, same currency), requiring at least `RN_MIN_BASELINE_CREDITS` (10) pooled credit rows. Flag S when `share_S >= RN_MIN_SHARE` (0.50), `share_S - baseline >= RN_MIN_UPLIFT` (0.30) and at least `RN_MIN_ROUND` (3) round credits. The uplift against the file's own other months is what separates an invented-credit month from a business that simply bills in round sums.
- **Second trigger, count test (hand-off change).** The share rule misses a month where invented round credits are diluted by many ordinary credits (the share stays under 0.50). A statement S is therefore also flagged when it has at least `RN_MIN_ROUND` (3) round credits, and **both** hold: (a) the Poisson tail `P(X >= k) < RN_COUNT_ALPHA` (0.005), where `k` is the number of round credits in S and the expected count is `max(RN_COUNT_LAMBDA_FLOOR (0.5), round credits in the other statements / number of other statements)`; (b) `share_S - baseline >= RN_COUNT_MIN_UPLIFT` (0.15). Condition (b) guards against a statement that is simply busier than the others with the same mix. The flag fires when either trigger holds; the original share rule is unchanged. The three constants were fixed before any tampered data was looked at. When only the count test fired, the evidence ends with `; 7 round credits against about 1.5 a month in the other statements (chance 0.0004)`.
- **Skip:** fewer than two statements of one account, or no statement has enough credits.
- **Known exposure, unmeasured.** The count trigger fires on a month with, for example, 4 round-thousand credits among 25 (16%) when the account's other months have none. Owner top-ups, rent receipts and cheque deposits in round thousands can look like that. There is no false-alarm estimate for it: the evaluation's clean generator has almost no credits that are multiples of 1000, and its false-alarm rate counts medium and high flags only (this rule is low). The evaluation report now adds a separate line for clean cases with any low flag, but that line cannot show this exposure until the clean generator draws round-thousand credits at a realistic rate. The expected count is estimated from the other months with no allowance for that estimate being noisy when only two or three statements exist.
- **Severity:** low (triage), for both triggers. **Tamper:** invented credits.
- **Flag:** `table="transactions"`, `row=` first round credit, `region=` its `credit` cell, `extra_regions=` up to 4 further round credits; discriminator none.
- **Evidence:** `6 of 8 credits are round multiples of 1000 (75%); other statements of this account: 3 of 25 (12%); uplift 63 points`.
- **Explanation:** `Unusually many round-sum deposits compared with the other months of this account. A hint only, not proof.`
- **Tests:**
  - `T-BS-RN-1` clean: three months at 10% to 20% round credits: `[]`.
  - `T-BS-RN-2` tampered: one month 6 of 8 round, others 12%: one flag on that month.
  - `T-BS-RN-3` honest quirk: a retainer business with 70% round credits in every month: `[]` (no uplift).
  - `T-BS-RN-4` boundary: share exactly 0.50 and uplift exactly 0.30 flags; 4 round credits of 9 (0.44) does not.
  - `T-BS-RN-5` skip: a single statement raises `CheckSkipped`.
  - Count test (added): a diluted month (round credits well above the Poisson expectation, share below 0.50) flags; a busier month with the same round mix does not (uplift under 0.15); a small surplus that does not reach the tail probability does not.
  - **Dev-set result (generator data, not real documents).** Tamper T08 (5 invented round credits in one month, expected rule `bs_round_numbers`), development seed 9101, digital, 5 instances per scenario (15 instances): 1/15 before this change, 15/15 after. The change was fixed before the tampered data was measured, but the tamper shape of our own generator was known when the count test was designed. No held-out figure is claimed in this section.

### 4.9 `bs_benford` (statistical hint, see section 7)

- **Logic:** pool all debit and credit amounts `> 0` of all statements of one account. **Recurring identical amounts count once** (hand-off change): an amount value already seen in the pool (same `Decimal`, debit or credit) is skipped, so a rent or payroll line repeated every month adds one observation, not twelve. Run only when the pool has at least `BENFORD_MIN_N` (100) such different amounts. First significant digit `d` of each amount (leading zeros and the decimal point skipped). Expected `p(d) = log10(1 + 1/d)`. Flag when **both** `MAD = mean(|obs_d - p(d)|) > BENFORD_MAD_MIN` (0.015) **and** `chi2 = sum((O_d - n*p(d))^2 / (n*p(d))) > BENFORD_CHI2_CRIT` (20.09, df 8, p = 0.01).
- **Skip:** no account reaches 100 different amounts.
- **Severity:** info (weight 0; never changes the score), unchanged. **Tamper:** fabricated activity.
- **Flag:** `docs=` every statement in the pool, no row or field, region `None`.
- **Evidence:** `n 142; first-digit % 1:41 2:12 3:9 4:8 5:14 6:6 7:4 8:3 9:3; MAD 0.021; chi2 25.3 (critical 20.09)`.
- **Explanation:** `The leading digits of the amounts depart from the usual pattern. Bank amounts often do (fees, fixed salaries), so this is only a hint.`
- **Tests:**
  - `T-BS-BF-1` clean: 120 amounts generated by `floor(10**uniform)` with a seeded `random.Random(4002)`: `[]`.
  - `T-BS-BF-2` tampered: 120 amounts all starting with 5: one `info` flag.
  - `T-BS-BF-3` insufficient data: 99 amounts all starting with 5: `CheckSkipped` (not a flag).
  - `T-BS-BF-4` honest quirk: 120 amounts that are fixed 2,500.00 salaries plus fees fires (documented false positive) when written as distinct amounts; fixed repeats now count once, which is meant to stop that quirk firing (checked on the generator's quirk only). The severity stays `info` so the score is unaffected either way.
  - Added: 100 different amounts are needed (`test_benford_needs_100_different_amounts`), and recurring amounts count once (`test_benford_recurring_amounts_count_once`).
  - **Dev-set result (generator data).** The change was made so that the clean loan base stops firing on its repeated amounts (rent, payroll). T09 (amounts of 3 statements replaced by uniform draws, expected rule `bs_benford`), development seed 9101, 5 instances: 4/5 before, 5/5 after. The hand-off report gives no separate dev count of clean-base firings before and after. A clean generator base can still reach the thresholds on other seeds, in which case a T09 catch cannot be credited; Benford on bank amounts stays an unreliable hint.

## 5. Behaviour spec: invoice (plan 4.2)

Data read: `invoice.fields.*` and table `lines` (`description, quantity, unit_price, gst_amount, amount`; data-model.md section 6.2). `amount` is the line amount before GST.

Fixture `F-INV-1` (section 9.1): `INV-0042`, issued 2026-03-10, due 2026-04-09, supplier `EXAMPLE LOGISTICS PTE LTD` (UEN `201912345R`, GST no `M21234567K`), buyer `ACME TRADING PTE LTD` (UEN `202012345H`), lines `Freight Mar` 4 x 2000.00 = 8000.00 and `Handling` 20 x 100.00 = 2000.00, subtotal 10000.00, `gst_rate 9.00`, `gst_amount 900.00`, total 10900.00. The UENs are valid under `python-stdnum` (the earlier data-model example `201912345K` was not; fixed at R0).

### 5.1 `inv_lines`

- **Logic:** (a) for each line with `quantity`, `unit_price`, `amount` all present: `expected = quantity * unit_price` quantized to 0.01 half-up; flag if `abs(amount - expected) > line_tol`, where `line_tol = 0.005 * abs(quantity) + 0.005` (the largest honest rounding error when the printed unit price is rounded to 2 dp; it is far tighter than the money tolerance on purpose). Lines without a quantity (for example `Delivery`) are not product-checked. Negative lines (discounts) are checked the same way. (b) `sum(amount over lines)` equals `subtotal` (±tol), or, for tax-inclusive layouts, equals `total` while `subtotal + gst_amount = total`.
- **Skip:** no invoice has a `lines` table with rows.
- **Severity:** high. **Tamper:** edited line.
- **Flag:** (a) `table="lines"`, `row=i`, `region=` the `amount` cell, `extra_regions=[quantity, unit_price cells]`; (b) `field="subtotal"`, region = subtotal cell, `discriminator="sum"`.
- **Evidence:** (a) `row 2: quantity 20 x unit price 100.00 = 2000.00; printed 2600.00; diff 600.00`. (b) `lines sum 10000.00; printed subtotal 12000.00; diff 2000.00`.
- **Explanation:** (a) `The amount on line {n} is not quantity times unit price.` (b) `The invoice lines do not add up to the printed subtotal.`
- **Tests** (file `test_checks_invoice.py`):
  - `T-INV-LN-1` clean: `F-INV-1`: `[]`.
  - `T-INV-LN-2` tampered: line 2 amount edited to 2600.00 with the total left alone: (a) flag at `row=1` and (b) flag (sum 10600.00 vs 10000.00).
  - `T-INV-LN-3` honest quirk (rounded unit price): quantity 1000, unit price printed `0.33` (true 0.3333), amount 333.30: `|333.30 - 330.00| = 3.30 <= 0.005*1000+0.005 = 5.005`, so `[]`.
  - `T-INV-LN-4` honest quirk (tax-inclusive lines): lines sum to 10900.00, `subtotal 10000.00`, `gst_amount 900.00`, `total 10900.00`: (b) passes through the inclusive branch.
  - `T-INV-LN-5` boundary: unit price 12.5, quantity 3, amount 37.50 passes; 37.52 flags.

### 5.2 `inv_gst`

- **Logic:** accept both GST methods (plan 1.4). Let `rate` = `gst_rate` if printed else the statutory rate for `invoice_date` from `GST_RATE_TABLE` (9% from 2024-01-01, 8% in 2023, 7% from 2007-07-01; **[confirm on IRAS before shipping]**), or 9.00 if `invoice_date` is missing. Candidate expected GST:
  - `C1` (net): `round(subtotal * rate / 100, 2)`.
  - `C2` (printed per line): `sum(line.gst_amount)`, only when every line that has an `amount` also has a `gst_amount`.
  - `C3` (computed per line): `sum(round(line.amount * rate / 100, 2))`.
  The invoice **passes** when `gst_amount` is within tol of **any** candidate. Special cases: `gst_rate` printed as `0.00` (zero-rated) expects 0.00 (`C1` only); `gst_amount` missing: not evaluated.
  - **rate sub-check:** printed `gst_rate` not equal to the statutory rate for `invoice_date` and not `0.00`: separate flag, `discriminator="rate"`, severity medium.
- **Skip:** no invoice has `subtotal` and `gst_amount`.
- **Severity:** high (`rate`: medium). **Tamper:** edited total or tax.
- **Flag:** `field="gst_amount"`, `region=` that cell, `extra_regions=[subtotal cell]`.
- **Evidence:** `gst_amount 990.00; expected 900.00 (net 9.00% of 10000.00), 900.00 (sum of line GST), 900.00 (per-line computed); diff 90.00`. `rate`: `printed gst_rate 7.00 on 2026-03-10; statutory 9.00`. Only candidates that exist are listed.
- **Explanation:** `The GST amount is not {rate}% of the subtotal, computed on the total or line by line.` `rate`: `The GST rate printed is not the rate in force on the invoice date.`
- **Tests:**
  - `T-INV-GST-1` clean (net method): `F-INV-1`: `[]`.
  - `T-INV-GST-2` tampered: `gst_amount 990.00`: flag, diff `90.00`.
  - `T-INV-GST-3` honest quirk (per-line vs net): 250 lines of 1.50, printed per-line GST 0.14 (half-up of 0.135), `subtotal 375.00`, `gst_amount 35.00`, `total 410.00`: `C1 = 33.75` fails (diff 1.25) but `C2 = 35.00` passes, so `[]`. The same invoice with `gst_amount 33.75` also passes (via `C1`).
  - `T-INV-GST-4` honest quirk (rate by date): invoice dated 2023-06-01, `gst_rate 8.00`, GST 8% of subtotal: `[]`; dated 2026-03-10 with `gst_rate 8.00`: `rate` flag.
  - `T-INV-GST-5` zero-rated: `gst_rate 0.00`, `gst_amount 0.00`: `[]`; `gst_amount 50.00` with `gst_rate 0.00`: flag.

### 5.3 `inv_total`

- **Logic:** `subtotal + gst_amount = total` (±tol). If `gst_amount` is missing, treat it as 0.00 and add `(no GST printed)` to the evidence. Cash-rounding adjustments of a few cents are inside tol.
- **Skip:** `subtotal` or `total` missing on every invoice.
- **Severity:** high. **Tamper:** edited total.
- **Flag:** `field="total"`, `region=` total cell, `extra_regions=[subtotal, gst_amount cells]`.
- **Evidence:** `subtotal 10000.00 + gst 900.00 = 10900.00; printed total 19900.00; diff 9000.00`.
- **Explanation:** `Subtotal plus GST is {computed}, but the invoice prints a total of {total}.`
- **Tests:**
  - `T-INV-TO-1` clean: `F-INV-1`: `[]`.
  - `T-INV-TO-2` tampered: total `19900.00`: flag, diff `9000.00`.
  - `T-INV-TO-3` honest quirk: total 10899.95 after cash rounding: `[]`; no GST printed and `subtotal == total`: `[]`.

### 5.4 `inv_ids`

- **Logic (UEN, `python-stdnum`):** for `supplier_uen` (and, when present, `buyer_uen`): `from stdnum.sg import uen`; call `uen.validate(value)` inside `try/except stdnum.exceptions.ValidationError`; the exception class name (`InvalidChecksum`, `InvalidFormat`, `InvalidLength`, `InvalidComponent`) goes into the evidence. **Never re-derive the check letter** (plan 1.4). `uen.validate` compacts the input itself; cell values are already upper-case without spaces. A missing `supplier_uen` is not flagged (a tax invoice must show the GST number, not the UEN).
- **GST number format (caveat):** plan 1.4 says to check IRAS before hard-coding. Until confirmed, accept a compact value (spaces and hyphens removed, upper-case) matching `^M\d{8}[A-Z]$` (the plan's example `M21234567K`) **or** a UEN that passes `uen.validate` (companies commonly quote their UEN as GST registration number). Anything else is a format flag. Other historical formats are **unconfirmed** and would be false positives: this is why the GST-format flag is `medium`, not the plan's `high`; the rule stays at `high` for an invalid supplier UEN. The accepted patterns live in one constant `GST_NO_PATTERNS` so IRAS confirmation is a one-line change plus a `version` bump.
- **GST missing:** `gst_amount > 0` and `supplier_gst_no` missing: flag `gst_no_missing` (medium); a tax invoice must show the supplier's GST number.
- **Skip:** no invoice has a `supplier_uen`, `supplier_gst_no` or `buyer_uen` to test.
- **Severity:** high (`supplier_uen`); medium (`supplier_gst_no`, `gst_no_missing`, `buyer_uen`). **Tamper:** invented supplier. Honest limit: a valid check letter proves the number is well-formed, not registered, and a careful forger copies a real UEN; no registry lookup is done here (the mock registry only lists the demo entities, so absence from it proves nothing).
- **Flag:** `field=` the field name, `region=` its cell; discriminator = the field name for the non-supplier-UEN cases.
- **Evidence:** `supplier_uen 201912345K fails UEN validation (InvalidChecksum)`; `supplier_gst_no M2123456K does not match M + 8 digits + letter and is not a valid UEN`; `GST 900.00 charged but no supplier GST number printed`.
- **Explanation:** `The supplier UEN is not a valid Singapore UEN.` / `The GST registration number is not in a recognised format.` / `GST is charged but no GST registration number is shown.`
- **Tests:**
  - `T-INV-ID-1` clean: `F-INV-1` (`201912345R`, `M21234567K`, buyer `202012345H`): `[]`.
  - `T-INV-ID-2` tampered: supplier UEN `201912345K` (bad check letter): `supplier_uen` flag with `InvalidChecksum`; `201912345` (no letter): `InvalidFormat`.
  - `T-INV-ID-3` GST format: `M2123456K` flags (medium); the UEN `201912345R` used as GST number passes; `M-2123-4567-K` passes after compacting.
  - `T-INV-ID-4` honest quirk: a sole-proprietor UEN of the business format `53012345B` passes (this is why the library is used, not a regex).
  - `T-INV-ID-5` GST charged, no GST number: `gst_no_missing`; no GST charged and no GST number: `[]` (not a tax invoice).

### 5.5 `inv_dates`

- **Logic:** (1) `due_date < invoice_date`; (2) `invoice_date > ctx.today` (not in the future; the same day is fine); (3) `paid_date < invoice_date`; (4) `paid_date > ctx.today`. Missing dates skip that condition. "Due on receipt" (`due_date == invoice_date`) is fine.
- **Skip:** no invoice has `invoice_date`.
- **Severity:** medium. **Tamper:** back- or forward-dated invoice.
- **Flag:** `field=` the offending date field (the later-dated one for ordering conditions), `region=` its cell, `extra_regions=[invoice_date cell]`; discriminators `due`, `future`, `paid_before`, `paid_future`.
- **Evidence:** `due_date 2026-02-01 is before invoice_date 2026-03-10`; `invoice_date 2026-11-15 is after today 2026-10-27`; `paid_date 2026-03-01 is before invoice_date 2026-03-10`.
- **Explanation:** `The invoice has a {due|paid} date before it was issued.` / `The invoice is dated in the future.`
- **Tests:**
  - `T-INV-DA-1` clean: `F-INV-1` and an invoice dated exactly `2026-10-27` (today): `[]`.
  - `T-INV-DA-2` tampered: `due_date 2026-02-01`: `due` flag; `invoice_date 2026-11-15`: `future` flag.
  - `T-INV-DA-3` rerun determinism: the same invoice dated `2026-11-15` with `today=date(2026,12,1)`: `[]`.

### 5.6 `inv_parties`

- **Logic:** (1) **self-dealing:** `supplier_uen == buyer_uen` (both present) or `company_match(supplier_name, buyer_name)`: flag `self`. (2) **applicant on the invoice:** when `ctx.applicant` has a name or UEN, the applicant must be one of the two parties (UEN equality if both present, else `company_match`). If not, pass only if the **buyer** matches a *named counterparty*: a `contract.buyer_name`, `purchase_order.buyer_name`, or `source_of_funds_declaration.counterparty_name` of this case; otherwise flag `applicant_absent`. Plan 4.2 words this as "buyer is the applicant (or a named counterparty)"; in a receivables bundle the applicant is the **supplier**, so this spec accepts the applicant on either side (see Contract request 3).
- **Skip:** (2) needs an applicant; (1) always runs; `CheckSkipped` only if no invoice has any party data.
- **Severity:** medium. **Tamper:** round-tripping; an invoice between two unrelated companies.
- **Flag:** `field="buyer_name"` (self) or `"supplier_name"` (applicant_absent), `region=` its cell, `extra_regions=[the other party cell]`.
- **Evidence:** `supplier "EXAMPLE LOGISTICS PTE LTD" (201912345R) and buyer "EXAMPLE LOGISTICS PTE. LTD." (201912345R) are the same company`; `supplier "X" and buyer "Y" are neither applicant "Z" nor a named counterparty`.
- **Explanation:** `Seller and buyer on this invoice are the same company.` / `Neither party on this invoice is the applicant.`
- **Tests:**
  - `T-INV-PA-1` clean: `F-INV-1` with applicant `EXAMPLE LOGISTICS PTE LTD`: `[]`; same invoice with the applicant as the buyer: `[]`.
  - `T-INV-PA-2` tampered: supplier and buyer share UEN: `self` flag.
  - `T-INV-PA-3` tampered: applicant `OTHER CO PTE LTD`, no counterparty: `applicant_absent`; with a contract whose `buyer_name` matches the invoice buyer: `[]`.
  - `T-INV-PA-4` honest quirk: suffix differences `Pte. Ltd.` vs `PTE LTD` do not create an `applicant_absent` flag.

### 5.7 `inv_duplicate`

- **Logic** (fingerprint and store: section 3.5):
  1. **In-case:** group the case's invoices by full fingerprint and by key (`supplier_uen`, `invoice_number`). Within a group, keep the lowest document id as the original; every other member is flagged (`docs=[original, duplicate]`). If a key group has differing totals or dates, evidence says `same number, different total` (the stronger signal: reused invoice number).
  2. **Store:** for each invoice with a full fingerprint and for each with a key fingerprint, `ctx.fingerprints.lookup("invoice", fp, exclude_case_id=ctx.case.id)`; any hit gives a flag (`docs=[the invoice]`, `discriminator="store:" + hit.case_id`), one flag per distinct hit case. If `ctx.fingerprints` has no store backing or the lookup raises, `CheckSkipped("fingerprint store unavailable")` only if no in-case group could be evaluated.
  3. An invoice with neither fingerprint nor key (missing `supplier_uen` or `invoice_number`) is not evaluated.
- **Severity:** high. **Tamper:** same invoice submitted twice (double financing).
- **Flag:** `field="invoice_number"`, `region=` that cell on the duplicate; `extra_regions=[` the original's invoice_number cell`]` for in-case hits. Discriminator: in-case flags `fp[:12]` of the full fingerprint (or of the key fingerprint when only the key matched); store flags `"store:" + hit.case_id` (one per distinct earlier case).
- **Evidence:** in-case: `invoice INV-0042 of 201912345R on 2026-03-10 total 10900.00 also appears as d2 (same fingerprint a1b2c3d4)`; reused number: `invoice INV-0042 of 201912345R also appears as d2 with total 12900.00 (same number, different total)`; store: `fingerprint a1b2c3d4 seen in case c_3f9a1b2c4d5e (document d2) on 2026-09-30`. The fingerprint is shown as its first 8 hex characters.
- **Explanation:** `This invoice was already submitted {in this case|in case c_...}.`
- **Honest limits:** a forger who changes the number format (`INV-0042` vs `INV-042`, `INV 0042` vs `INV-0042`) evades the key because leading zeros and hyphens are kept (data-model.md section 4.1); changing the date and total evades the full fingerprint. A genuine re-upload of the same applicant's own invoice in a later case is flagged too and the analyst dismisses it with a reason. No near-duplicate (same supplier, date, total, different number) rule is built.
- **Tests:**
  - `T-INV-DP-1` clean: two different invoices from the same supplier (`INV-0042`, `INV-0043`): `[]`; the same invoice number from two different supplier UENs: `[]`.
  - `T-INV-DP-2` tampered (in-case): `F-INV-1` as `d1` and again as `d2`: one flag on `d2`, `extra_regions` pointing at `d1`.
  - `T-INV-DP-3` tampered (reused number): same supplier and number, total edited to 12900.00: key-group flag, evidence `same number, different total`.
  - `T-INV-DP-4` store: a fake store with `lookup` returning a hit for the fingerprint in case `c_other`: flag with discriminator `store:c_other`; the same hit with `case_id == ctx.case.id` is excluded by `exclude_case_id` (rerun idempotence): `[]`.
  - `T-INV-DP-5` evidence privacy: store-hit evidence contains the case id and no names or amounts.

### 5.8 `inv_sequence` (heuristic hint, see section 7)

- **Logic:** group invoices by supplier (`supplier_uen`, else `company_key(supplier_name)`) and by number prefix, using `^(.*?)(\d+)$` on `invoice_number` (the numeric tail; prefix and zero-padded width form the group key). Groups with at least 3 parseable numbers are evaluated:
  1. **reuse:** a number appearing twice with different `invoice_date` or `total` (identical content is `inv_duplicate`): low.
  2. **order:** sorted by number, an invoice dated earlier than a lower-numbered invoice of the same group (back-dating): low.
  3. **gap:** missing numbers between the minimum and maximum: **info**, one flag per group listing the gaps. Gaps are normal when the bundle is only a selection of invoices, so they are never scored.
- **Skip:** no group has 3 parseable numbers.
- **Severity:** low (`gap`: info). **Tamper:** fabricated invoices (reused or back-dated numbering).
- **Flag:** `field="invoice_number"`, `region=` that cell; discriminators `reuse`, `order`, `gap`; the `gap` flag is on the first invoice of the group, `docs=` all members.
- **Evidence:** `INV-0043 dated 2026-02-20 precedes INV-0041 dated 2026-02-25`; `numbers INV-0044 to INV-0046 absent between INV-0043 and INV-0047`.
- **Explanation:** `The invoice numbering from this supplier looks out of sequence.` / `Some invoice numbers in this range are not in the bundle.`
- **Tests:**
  - `T-INV-SQ-1` clean: `INV-0041..0043` dated ascending: `[]`.
  - `T-INV-SQ-2` tampered (order): `INV-0043` dated before `INV-0041`: `order` flag, low.
  - `T-INV-SQ-3` honest quirk (selection): `INV-0041, INV-0045, INV-0050`: one `info` `gap` flag, no low/medium flag, so the score is unchanged.
  - `T-INV-SQ-4` two suppliers with overlapping numbers: grouped separately, `[]`.

## 6. Behaviour spec: financial statement (plan 4.3)

Data read: `financial_statement.fields` and the tables `income_statement`, `balance_sheet`, `cash_flow` (columns `line, label, section, current, prior, is_subtotal`; line keys, sections and sign conventions: data-model.md section 6.3). Values are already normalised to whole currency units.

**6.0 Common definitions.**
- `L(table, key, col="current", section=None)` is the sum of `col` over rows with `line == key` (first row only when `is_subtotal`; documents with two printed rows for one subtotal use the first and add `; duplicate subtotal row ignored`). `None` when no row has a value.
- Checks run on the `current` column and, when the `prior` column has values for every line used, again on `prior` with `discriminator="prior"`.
- **Tolerance:** `_fs_tol(ctx, doc, terms)`: `tol` when `scale_printed` is `1`; otherwise `scale * max(1, terms // 2)` (statements printed in thousands carry rounding of about one printed unit per two terms; the converse limit, that a one-unit edit is invisible, is stated in section 11). `terms` is the number of printed figures in the comparison.
- **Unmapped lines:** a row with `line == "other"` in the `income_statement` makes the affected subtotal unverifiable (the sign is unknown); instead of a flag, one `info` flag per document says `subtotal profit_before_tax not verified: unmapped line(s) "..."`. In the balance sheet and cash flow, `other` rows are summed as extracted (balance sheet values positive, cash flow values signed).
- **Optional lines:** in formulas below, `cost_of_sales`, `other_income`, `operating_expenses`, `finance_costs`, `income_tax` count as 0 when absent; `revenue` and `net_profit` do not.
- Fixture `F-FS-1` is in section 9.1.

### 6.1 `fs_balance`

- **Logic:** `total_assets = total_liabilities_and_equity` when the latter is printed, else `total_assets = total_liabilities + total_equity`; where a total is not printed it is derived from its section subtotals (`total_current_liabilities + total_non_current_liabilities`) or lines. Tolerance `_fs_tol(ctx, doc, 3)`. (Whether `total_liabilities_and_equity` equals `total_liabilities + total_equity` is a subtotal question, `fs_subtotals`.)
- **Skip:** `total_assets` not derivable or neither side derivable.
- **Severity:** high. **Tamper:** balance sheet figure changed.
- **Flag:** `table="balance_sheet"`, `row=` the `total_assets` row, `region=` its `current` (or `prior`) cell, `extra_regions=[` the liabilities-and-equity total row cell`]`.
- **Evidence:** `total_assets 1100000.00; total_liabilities 500000.00 + total_equity 600000.00 = 1100000.00` or the failing `... = 1100000.00; printed assets 1150000.00; diff 50000.00`.
- **Explanation:** `Total assets do not equal total liabilities plus equity.`
- **Tests** (file `test_checks_financial_statement.py`):
  - `T-FS-BA-1` clean: `F-FS-1`: `[]`.
  - `T-FS-BA-2` tampered: `trade_receivables` and `total_current_assets` raised by 50,000 and `total_assets` to 1,150,000: flag, diff `50000.00`.
  - `T-FS-BA-3` honest quirk (statement in thousands): `scale_printed "1000"`, `total_assets 10500000`, liabilities 6200000 + equity 4301000 (rounding): tol = 1000 x 1 = 1000, diff 1000: `[]`.
  - `T-FS-BA-4` deficit: `retained_earnings -800000`, `share_capital 100000`, `total_equity -700000`, balances: `[]`.

### 6.2 `fs_subtotals`

Each printed subtotal (`is_subtotal` true) must equal its formula. Tolerance `_fs_tol(ctx, doc, n)` with `n` the number of terms.

| Table | Subtotal line | Formula (current or prior column) |
|---|---|---|
| `income_statement` | `gross_profit` | `revenue - cost_of_sales` |
| | `profit_before_tax` | `gross_profit` (printed, else `revenue - cost_of_sales`) `+ other_income - operating_expenses - finance_costs` |
| | `net_profit` | `profit_before_tax - income_tax` |
| `balance_sheet` | `total_current_assets` | sum of non-subtotal rows in section `current_assets` |
| | `total_non_current_assets` | same, `non_current_assets` |
| | `total_assets` | `total_current_assets + total_non_current_assets` (printed, else section sums) |
| | `total_current_liabilities`, `total_non_current_liabilities` | sum of non-subtotal rows of the section |
| | `total_liabilities` | `total_current_liabilities + total_non_current_liabilities` |
| | `total_equity` | sum of non-subtotal rows in `equity` (retained earnings signed) |
| | `total_liabilities_and_equity` | `total_liabilities + total_equity` |
| `cash_flow` | `net_cash_from_operating`, `net_cash_from_investing`, `net_cash_from_financing` | sum of non-subtotal rows of the section |
| | `net_change_in_cash` | operating + investing + financing nets (printed, else section sums) |
| | `cash_closing` | `cash_opening + net_change_in_cash` |

- **Skip:** no document has a printed subtotal with a derivable formula.
- **Severity:** high. **Tamper:** edited line or edited subtotal; consistent edits of line and subtotal are the hard case (caught only by `fs_balance`, `fs_comparatives` or the cross-document rules).
- **Flag:** `table=`, `row=` the subtotal row, `region=` its cell; `extra_regions=` up to 4 component cells; `discriminator=` `prior` for the prior column.
- **Evidence:** `net_profit: printed 124500.00; profit_before_tax 180000.00 - income_tax 30600.00 = 149400.00; diff 24900.00`; `total_current_assets: printed 700000.00; sum of 3 lines 650000.00; diff 50000.00`.
- **Explanation:** `The printed {label} does not equal the sum of its lines.`
- **Tests:**
  - `T-FS-ST-1` clean: `F-FS-1` (all three statements): `[]`.
  - `T-FS-ST-2` tampered: `operating_expenses` edited from 300,000 to 240,000, PBT and net left alone: flag on `profit_before_tax` (computed 240000 vs printed 180000).
  - `T-FS-ST-3` honest quirk (unmapped line): an `other` row in the income statement: no flag, one `info` `not verified` flag.
  - `T-FS-ST-4` honest quirk (no cost of sales): a services company with revenue 500,000, operating_expenses 300,000, `gross_profit` absent, PBT 200,000: `[]`.
  - `T-FS-ST-5` prior column: a tamper only in `prior` gives a flag with discriminator `prior`.

### 6.3 `fs_cash_tie`

- **Logic:** (1) `cash_closing` (cash flow, `current`) equals `cash_and_equivalents` (balance sheet, `current`), tol `_fs_tol(ctx, doc, 2)`; (2) `cash_opening` (current) equals balance sheet `cash_and_equivalents` `prior`, when both exist; this sub-check is severity medium, discriminator `opening`.
- **Skip:** both statements are not present on the same document, or the two figures are missing.
- **Severity:** high (opening: medium). **Tamper:** cash figure changed on one statement.
- **Flag:** `table="cash_flow"`, `row=` the `cash_closing` row, `region=` its cell, `extra_regions=[` the balance sheet cash cell`]`.
- **Evidence:** `cash_closing 250000.00 (cash flow) vs cash_and_equivalents 400000.00 (balance sheet); diff 150000.00`.
- **Explanation:** `Closing cash on the cash-flow statement does not equal cash on the balance sheet.`
- **Tests:**
  - `T-FS-CT-1` clean: `F-FS-1` (250000 on both): `[]`.
  - `T-FS-CT-2` tampered: balance sheet cash 400000 (with `total_current_assets` and `total_assets` adjusted so `fs_balance` still ties on the asset side): flag, diff `150000.00`.
  - `T-FS-CT-3` honest limit: closing cash defined net of an overdraft (cash flow 240000, balance sheet 250000, `borrowings_current` includes the overdraft): the rule flags; the analyst dismisses. The test documents this known false positive.

### 6.4 `fs_comparatives`

- **Logic:** needs two `financial_statement` documents of the same entity (UEN equal, else `company_match`) whose `period_end` dates differ by `FS_YEAR_GAP_DAYS` (350 to 380) days. For the later document N and the earlier N-1: for every `(table, line)` that is not `other` and appears exactly once in both, `N.prior` equals `N-1.current` (tol `max(_fs_tol(ctx, N, 2), _fs_tol(ctx, N-1, 2))`: the coarser printing scale of the two decides, so a year printed in S$'000 compared with the next year's prior column in whole dollars, generator quirk Q06, differs by up to 500 without a flag). If a section's subtotals all agree between the two, mismatches on individual lines of that section are suppressed (a reclassification inside a section, not a restatement). A row whose `label` contains `restated` (case-insensitive) produces an **info** flag instead.
- **Skip:** fewer than two financial statements of one entity a year apart (`CheckSkipped("needs two years")`).
- **Severity:** high (restated: info). **Tamper:** prior-year figures rewritten to flatter the trend.
- **Flag:** `docs=[N-1, N]`, `table=`, `row=` the N row, `region=` its `prior` cell, `extra_regions=[` the N-1 `current` cell`]`; cap 5 per pair, ordered by table then row.
- **Evidence:** `revenue: 2026 prior column 1000000.00 vs 2025 reported 900000.00; diff 100000.00`.
- **Explanation:** `The prior-year figure for {label} differs from what last year's statement reported.`
- **Tests:**
  - `T-FS-CO-1` clean: N-1 `current` equals N `prior` for every line: `[]`.
  - `T-FS-CO-2` tampered: N `prior` revenue 1,000,000 vs N-1 `current` 900,000, `gross_profit` also changed: flags on revenue and gross profit.
  - `T-FS-CO-3` honest quirk (restated): label `Revenue (restated)`: `info` flag, not high.
  - `T-FS-CO-4` honest quirk (reclassification): `trade_receivables` 300,000 to 250,000 with `other_current_assets` 100,000 to 150,000 and `total_current_assets` equal: `[]`.
  - `T-FS-CO-5` skip: one statement: `CheckSkipped`.

### 6.5 `fs_entity`

- **Logic:** against `ctx.applicant`: `fs.uen` and `applicant.uen` both present and different: flag `uen`; otherwise `fs.company_name` and `applicant.name` both present and not `company_match`: flag `name`. If `applicant.uen == fs.uen`, the name is not tested (a renamed company).
- **Skip:** no applicant, or no FS has a name or UEN.
- **Severity:** medium. **Tamper:** statements of another company.
- **Flag:** `field="company_name"` or `"uen"`, `region=` that cell; discriminator `company_disc(company_name)` / `uen_disc(fs uen)` (section 3.4).
- **Evidence:** `company_name "OTHER HOLDINGS PTE LTD" vs applicant "EXAMPLE LOGISTICS PTE LTD"` / `uen 202012345H vs applicant uen 201912345R`.
- **Explanation:** `These financial statements are for a different company from the applicant.`
- **Tests:**
  - `T-FS-EN-1` clean: matching name with `Pte. Ltd.` punctuation: `[]`.
  - `T-FS-EN-2` tampered: different name and UEN: `uen` flag (UEN takes precedence; a single flag).
  - `T-FS-EN-3` skip: applicant empty.

## 7. Behaviour spec: cross-document (plan 4.4), and which rules are hints

All cross-document rules are `stage="crossdoc"` and run after all `rules` flags exist. They compare only equal currencies (convention 12).

### 7.0 Evidence strength (the honest classification)

| Class | Rules | Meaning |
|---|---|---|
| Identity and arithmetic (no honest explanation other than extraction error or rounding) | `bs_totals`, `bs_running_balance`, `bs_period_continuity`, `bs_page_chain`, `inv_lines`, `inv_gst`, `inv_total`, `inv_ids`, `inv_duplicate`, `fs_balance`, `fs_subtotals`, `fs_cash_tie`, `fs_comparatives`, `x_entity` | Fires on facts printed in the documents |
| Reconciliation with a stated tolerance (honest timing and scope differences exist) | `bs_dates`, `bs_holder`, `inv_dates`, `inv_parties`, `fs_entity`, `x_cash_at_year_end`, `x_revenue_vs_credits`, `x_invoice_vs_receipts`, `x_invoice_vs_contract` | Medium severity; explanations name the usual honest cause |
| **Statistical or heuristic hints only** | **`bs_round_numbers` (low), `bs_benford` (info), `bs_duplicates` (low), `inv_sequence` (low; gap sub-flag info)** | Shown to the analyst, weighted low, **never a hard stop**, never sufficient alone for a verdict. Thresholds come from the eval (plan 9) and are listed in section 3.6 |

`scoring-and-queue.md` owns the hard-stop list; this doc recommends that none of the hint rules is a hard stop, and that `bs_benford` (info, weight 0) never appears as a queue "top finding".

### 7.1 `x_entity`

- **Logic:** build *claims* `(document, role, name, uen)` from: `bank_statement` (`account_holder`, `uen`); `invoice` (`supplier_name`, `supplier_uen`) and (`buyer_name`, `buyer_uen`); `financial_statement`, `company_profile`, `certificate_of_incorporation`, `ownership_chart`, `shareholder_register`, `source_of_funds_declaration` (`company_name`, `uen`); `contract` (`seller_*`, `buyer_*`); `purchase_order` (`buyer_*`, `supplier_*`). Part **(B) conflict inside the bundle:** group claims by UEN; a UEN with two names that do not `company_match` is a conflict; group claims by `company_key`; a name with two different UENs is a conflict. One flag per conflicting UEN or name, `docs=` all involved documents (cap 4 in the id; evidence lists up to 3), severity high. Part **(A) applicant identity on documents that have no dedicated rule:** for `company_profile`, `certificate_of_incorporation`, `ownership_chart`, `shareholder_register`, `source_of_funds_declaration`: UEN differs from `applicant.uen`, or (UEN absent on either side) name does not `company_match` `applicant.name`: flag high; for `contract` and `purchase_order` the applicant must be one of the two parties, else flag `applicant_absent`, medium. Bank statements, invoices and financial statements are covered by `bs_holder`, `inv_parties`, `fs_entity` for part (A) (flags correlate; the scorer groups them), but still take part in (B).
- **Skip:** fewer than 2 claims, or part (A) without an applicant and no conflicts found: `CheckSkipped` only when neither part could run.
- **Severity:** high (`applicant_absent`: medium). **Tamper:** documents belonging to different companies spliced into one bundle; invoice or statement showing the applicant's UEN under another name.
- **Flag:** `field=` the name or UEN field of the first involved document, `region=` its cell, `extra_regions=` the other involved cells (max 4); discriminator (section 3.4) `uen_disc(<uen>)` or `company_disc(<name>)` for (B); for (A) `uen_disc(<document uen>)` when UENs decide, else `company_disc(<document name>)`. Truncate discriminators to 40 characters.
- **Evidence:** `UEN 201912345R is "EXAMPLE LOGISTICS PTE LTD" on d2 and "ACME TRADING PTE LTD" on d5`; `name "ACME TRADING" has UEN 202012345H on d4 and 53012345B on d7`; `d6 uen 202012345H vs applicant uen 201912345R`.
- **Explanation:** `The same UEN appears with different company names in this bundle.` / `This document names a different company from the applicant.`
- **Honest limits:** an ACRA name change gives two names for one UEN and flags; the analyst dismisses with a reason. Person identity on ID documents (plan 4.4 lists "ID documents") is `x_directors` / `id_*` in checks-kyc-aml.md.
- **Tests** (file `test_checks_crossdoc.py`):
  - `T-X-EN-1` clean: profile, certificate, FS, statement and invoice all `EXAMPLE LOGISTICS PTE LTD` / `201912345R` with punctuation variants: `[]`.
  - `T-X-EN-2` tampered: invoice supplier UEN `201912345R` with name `ACME TRADING PTE LTD`: (B) flag with evidence naming both documents.
  - `T-X-EN-3` tampered: certificate of incorporation for `OTHER HOLDINGS PTE LTD` / `202012345H` while the applicant is `201912345R`: (A) flag.
  - `T-X-EN-4` honest quirk: `Tom & Jerry Pte Ltd` vs `TOM AND JERRY PRIVATE LIMITED`: `[]`.

### 7.2 `x_cash_at_year_end`

- **Logic:** per financial statement F with `period_end = E` and `balance_sheet` `cash_and_equivalents` (current) `C` (sum of rows with that line): for each bank account (group by `account_number`) find the statement covering E (`period_start <= E <= period_end`, same currency). Balance at E = `balance` of the last row with date `<= E` (dates inherited as in `bs_dates`), or `opening_balance` if E precedes the first row. `B` = sum over covered accounts. Let `tol_x = max(tol, X_CASH_REL_TOL * max(|C|, |B|))` (5%, to allow cheques in transit and other book-versus-bank timing). Flag if `abs(B - C) > tol_x`: `discriminator="short"` when `B < C` (bank below the reported cash; other accounts not supplied may explain it), `"excess"` when `B > C`.
- **Skip:** no statement covers E for any F (`CheckSkipped("no statement covers <E>")`); currency mismatch.
- **Severity:** medium. **Tamper:** inflated cash on the balance sheet.
- **Flag:** `docs=[F, covering statements...]`, `table="balance_sheet"`, `row=` cash row, `region=` its `current` cell, `extra_regions=[` the balance cell of the row used (max 4)`]`.
- **Evidence:** `FS cash at 2026-03-31: 400000.00; bank balance at 2026-03-31: 26290.71 (1 account, statement d3); diff 373709.29; tolerance 20000.00`.
- **Explanation:** short `Cash on the balance sheet is higher than the balance in the supplied bank statements on that date; accounts not supplied would explain this.` excess `The bank statements show more cash on the year-end date than the balance sheet.`
- **Tests:**
  - `T-X-CY-1` clean: FS cash 152,000; bank at 2026-03-31 150,000 (diff 2,000 < 5%): `[]`. Year-end on a Sunday: the Friday's balance is used.
  - `T-X-CY-2` tampered: FS cash 400,000 vs bank 150,000: `short` flag.
  - `T-X-CY-3` honest quirk (cheque timing): FS 148,000 vs bank 152,500 (diff 3.0%): `[]`.
  - `T-X-CY-4` skip: statements end before E: `CheckSkipped`.

### 7.3 `x_revenue_vs_credits`

- **Logic:** per financial statement F (period `[S, E]`, revenue `R` = sum of `income_statement` rows with `line == "revenue"` `current`), over statements of the same currency:
  1. `period_days = (E - S).days + 1`. `covered` = the set of days of `[S, E]` covered by the union of the statements' periods. `covered_days = |covered|`. Require `covered_days >= X_REV_MIN_COVER_DAYS` (30).
  2. `expected = R * covered_days / period_days` (even-revenue assumption; seasonality is a stated limit).
  3. Credits = all `credit` cells of rows dated inside `covered`, split by annotation `c4`: **qualifying low** = `trade_receipt`, `customer_payment`; **unclassified** = `other` or no annotation; **excluded** = `financing_proceeds`, `owner_transfer`, `inter_company`, `refund`. `qualifying_high = low + unclassified` (the generous reading).
  4. `r = qualifying_high / expected`. Flag `shortfall` when `r < X_REV_LOW_RATIO` (0.60, medium: slack for receivables and timing). Flag `excess` when `r > X_REV_HIGH_RATIO` (1.50, severity **info**: credits far above revenue is a note about undeclared income, not evidence against the borrower).
- **Skip:** no F has `revenue`, or none has `covered_days >= 30` (the usual case when the statements cover recent months and the FS cover last year: stated in section 11).
- **Severity:** medium (`excess`: info). **Tamper:** inflated revenue on the financial statements (deck loan example).
- **Flag:** `docs=[F, statements...]`, `table="income_statement"`, `row=` the revenue row, `region=` its `current` cell; `extra_regions=[` closing balance cells of covering statements, max 4`]`.
- **Evidence:** `FS revenue 1800000.00 for 2025-01-01..2025-12-31 (365 d); statements cover 181 d; prorated 892602.74; qualifying credits 310000.00 (trade_receipt 250000.00, customer_payment 60000.00, unclassified 0.00); excluded 410000.00 (financing_proceeds 400000.00, owner_transfer 10000.00); ratio 0.35 < 0.60`.
- **Explanation:** `Bank deposits that look like sales support only {pct}% of the revenue reported for the days the statements cover; sales banked in accounts not supplied would explain this.`
- **Tests:**
  - `T-X-RV-1` clean: revenue 1,200,000 for a full year, statements cover the year, trade credits 1,140,000: `r = 0.95`, `[]`.
  - `T-X-RV-2` tampered: revenue 1,800,000, qualifying credits 310,000, 400,000 of financing excluded: `r = 0.35`, `shortfall` flag. Without the exclusion logic the credits would be 710,000 (`r = 0.80`) and the tamper would pass: the test fixes that the exclusion is applied.
  - `T-X-RV-3` honest quirk (partial coverage and receivables): revenue 1,200,000 for the year, statements cover 6 months with trade credits 470,000 (`expected` about 595,000, `r = 0.79`): `[]`.
  - `T-X-RV-4` honest quirk (unclassified credits): credits unannotated (C4 missing) count toward `qualifying_high`: no false alarm when the classifier was unavailable.
  - `T-X-RV-5` skip: statements cover 12 days of the FS year: `CheckSkipped`.

### 7.4 `x_invoice_vs_receipts`

- **Logic:** for every invoice with `payment_status == "paid"` and a `total`: window start `invoice_date - X_RECEIPT_EARLY_DAYS` (7); window end `paid_date + X_RECEIPT_AFTER_PAID_DAYS` (7) when `paid_date` is present, else `invoice_date + X_RECEIPT_UNPAID_WINDOW_DAYS` (90).
  1. **Evaluable** only if the window lies entirely inside the union of the statements' periods (any account, same currency as the invoice). Otherwise the invoice goes into one `info` flag per case `uncovered` (`document_ids` = first 5, evidence lists all; reasons `outside statement coverage` or `currency`). `payment_status` `unpaid` / `partial` or missing: not evaluated.
  2. **Matching** (greedy; invoices ordered by `(invoice_date, document id)`, credits by `(date, account, row)`; each credit used once). A credit matches when its date is in the window and `-tol <= total - credit <= X_RECEIPT_FEE_ALLOW` (credit may be lower by up to 35.00, absorbing bank and remittance charges). Preference among several candidates: the one whose `counterparty` annotation `company_match`es the buyer, then the earliest.
  3. **Batch:** a still-unmatched invoice may match one credit equal (same rule) to the sum of 2 to 4 unmatched paid invoices of the **same buyer** whose windows contain the credit date (subsets in document-id order, first match wins).
  4. **Instalments (R0, generator quirk Q04):** a still-unmatched invoice may match 2 or 3 unused credits in its window whose `counterparty` annotations all `company_match` the buyer and whose sum satisfies `-tol <= total - sum <= X_RECEIPT_FEE_ALLOW` (combinations in credit order, first match wins; credits of 1% of the total or less are ignored).
  5. No match: flag medium.
- **Skip:** no paid invoice is evaluable (`CheckSkipped("no statements cover invoice payment windows")`); the `uncovered` info flag is emitted before the skip only if at least one invoice was evaluable.
- **Severity:** medium (`uncovered`: info). **Tamper:** fake or unpaid invoices presented as paid.
- **Flag:** `docs=[invoice]` (`+` statements not listed), `field="total"`, `region=` the total cell; `extra_regions=` none.
- **Evidence:** `invoice INV-0042 total 10900.00 marked paid (paid_date 2026-04-02); no credit between 2026-03-03 and 2026-04-09 of 10900.00 (tolerance -1.00/+35.00) in 2 statements; nearest 10450.00 on 2026-04-03`. Nearest = the window credit with the smallest absolute amount difference, omitted if none.
- **Explanation:** `This invoice is marked paid, but no matching deposit appears in the bank statements.`
- **Tests:**
  - `T-X-IR-1` clean: invoice total 10,900.00 paid 2026-04-02, credit 10,900.00 on 2026-04-03: `[]`.
  - `T-X-IR-2` tampered: no credit in the window: flag; nearest-credit evidence present when one exists.
  - `T-X-IR-3` honest quirk (fees): credit 10,875.00 (25.00 remittance fee): `[]`; credit 10,850.00 (50.00 short): flag.
  - `T-X-IR-4` honest quirk (batch): invoices 10,900.00 and 5,450.00 from the same buyer paid by one credit of 16,350.00: `[]`.
  - `T-X-IR-5` honest quirk (coverage): paid invoice dated before the first statement: no medium flag; one `info` `uncovered` flag.
  - `T-X-IR-6` each credit used once: two identical invoices of 10,900.00 (different numbers, both paid) and one credit: one flag.
  - `T-X-IR-7` honest quirk (instalments, Q04): invoice 10,900.00 paid by 5,000.00 and 5,900.00 from the buyer: `[]`; the same two credits from an unrelated counterparty: flag.
- **Window difference from `x_remitter_vs_buyer` (R0 decision on CR-21):** the two rules keep different windows on purpose. This rule asks "is a paid invoice backed by money?" and tolerates fees (credit up to 35.00 lower) and instalments from the buyer; `x_remitter_vs_buyer` asks "who paid?" and matches exact amounts (±tol) or exact instalment sums (checks-kyc-aml.md 4.9). Both state their window in evidence.

### 7.5 `x_invoice_vs_contract`

- **Logic:** for each invoice with `contract_reference` (match `contract.contract_number`) or `po_reference` (match `purchase_order.po_number`), ids compared as canonical `id` values. A cited document not in the bundle goes into one `info` flag per case (`cited document not supplied`; a missing document lowers coverage, it is not evidence). When found:
  1. **party:** `invoice.supplier` matches contract `seller_*` / PO `supplier_*` and `invoice.buyer` matches contract `buyer_*` / PO `buyer_*` (UEN equality when both present, else `company_match`). Mismatch: flag `party`.
  2. **amount (contract):** invoice passes if `subtotal <= contract_value + tol` **or** `total <= contract_value + tol` (contract values may be quoted with or without GST). Cumulative over-billing: invoices citing the same contract, ordered by `(invoice_date, id)`, running sum of `subtotal` (or `total` when only that passes); the first invoice that pushes the running sum over `contract_value + tol` is flagged `cumulative`. Partial billing (less than the contract value) is expected and never flagged. If `milestones` exist and the invoice subtotal equals a milestone `amount`, the amount check passes.
  3. **amount (PO):** `subtotal <= po.subtotal + tol` or `total <= po.total + tol`; a lower invoice is partial delivery.
  4. **date:** `invoice_date < effective_date` (else `contract_date`, or `po_date`) or `invoice_date > end_date + X_CONTRACT_POST_END_DAYS` (90): flag `date`.
  5. A comparison whose currencies differ is not evaluated.
- **Skip:** no invoice cites a document that is in the bundle (`CheckSkipped("no cited contract or PO in the bundle")`).
- **Severity:** medium (`info` for not-supplied). **Tamper:** forged or altered contract/PO values; invoice amounts that disagree with the contract (deck loan example).
- **Flag:** `docs=[invoice, contract|PO]`, `field="total"` (or `"subtotal"`, whichever failed; `"invoice_date"` for date; `"buyer_name"` / `"supplier_name"` for party), `region=` that invoice cell, `extra_regions=[` the contract `contract_value` / PO total / party cell`]`; discriminators `party`, `amount`, `cumulative`, `date`.
- **Evidence:** `invoice INV-0042 subtotal 650000.00 / total 708500.00 exceeds contract C-2026-007 value 500000.00`; `running total of 3 invoices citing C-2026-007 is 560000.00 > 500000.00 at INV-0044`; `invoice buyer "ACME TRADING PTE LTD" vs contract buyer "BETA PTE LTD"`; `invoice_date 2026-01-05 before contract effective_date 2026-02-01`.
- **Explanation:** `This invoice bills more than the contract or purchase order it cites.` / `The parties on this invoice differ from the contract.` / `The invoice date is outside the contract term.`
- **Tests:**
  - `T-X-IC-1` clean: contract value 500,000.00; invoice subtotal 100,000.00 (partial billing); parties match; date inside the term: `[]`.
  - `T-X-IC-2` tampered: invoice subtotal 650,000.00 vs contract 500,000.00: `amount` flag; buyer differs: `party` flag.
  - `T-X-IC-3` honest quirk (GST-inclusive contract): contract value 109,000.00, invoice subtotal 100,000.00, total 109,000.00: `[]`.
  - `T-X-IC-4` honest quirk (final invoice after the term): `end_date 2026-06-30`, invoice dated `2026-08-15` (46 days after): `[]`; dated `2026-11-15`: `date` flag.
  - `T-X-IC-5` cumulative: three invoices of 200,000.00 against a 500,000.00 contract: flag only on the third (`cumulative`).
  - `T-X-IC-6` not supplied: invoice cites `C-2026-099` not in the bundle: `info` flag only.

### 7.6 `file_reuse` (added at R0; plan 0.4 "duplicate document detection: file hash", 1.2)

- **Logic:** (1) **in-case:** two `FileRef`s of the case with the same `sha256` (the API accepts duplicate uploads on purpose): one flag on the later file, severity **low** (often an accidental double upload). (2) **cross-case:** for each file, `ctx.fingerprints.lookup("file_sha256", sha256, exclude_case_id=ctx.case.id)`; each hit case gives one flag, severity **medium** when `hit.meta.get("applicant_uen")` is present and differs from `ctx.applicant.uen` (the same file submitted by a different applicant), else **info** (a resubmission by the same applicant). Privacy as `inv_duplicate`: evidence names the other case id, document id and date only.
- **Skip:** never (a case has at least one file once it reaches this stage); with zero files, `CheckSkipped("no files")`.
- **Severity:** medium (overrides: low, info as above). **Tamper:** the same statement, invoice or ID file reused across applicants (double financing at file level).
- **Flag:** `docs=` the documents of the file, `field=None`, `region=None`; discriminator `sha256[:12]` (in-case) or `"store:" + hit.case_id` (cross-case).
- **Evidence:** `file f3 (sha256 9f3a21c0d4e5...) is identical to f1 in this case`; `file f2 (sha256 9f3a21c0d4e5...) seen in case c_3f9a1b2c4d5e on 2026-09-30 for a different applicant`.
- **Explanation:** `This exact file was already submitted {in this case|in another case}.`
- **Honest limits:** any byte change (re-save, re-scan) evades it; that is what `inv_duplicate` and the content rules are for. Deleting a case removes its fingerprints (review-workflow-and-audit.md 4.11).
- **Tests** (`test_checks_crossdoc.py`): `T-X-FR-1` clean, two different files: `[]`; `T-X-FR-2` same bytes twice in one case: one low flag; `T-X-FR-3` store hit with a different `applicant_uen`: medium with `store:` discriminator; `T-X-FR-4` store hit for the same applicant: info; `T-X-FR-5` `exclude_case_id` makes a rerun silent.

## 8. Files owned

| Path | Owner | Content |
|---|---|---|
| `backend/app/checks/bank_statement.py` | foundation (`bs_running_balance` only), then rules-bank | rules in section 4 |
| `backend/app/checks/invoice.py` | rules-inv-fs-cross | rules in section 5 |
| `backend/app/checks/financial_statement.py` | rules-inv-fs-cross | rules in section 6 |
| `backend/app/checks/crossdoc.py` | rules-inv-fs-cross | rules in section 7 |
| `backend/tests/test_checks_bank_statement.py` | rules-bank | tests `T-BS-*` |
| `backend/tests/test_checks_invoice.py` | rules-inv-fs-cross | tests `T-INV-*` |
| `backend/tests/test_checks_financial_statement.py` | rules-inv-fs-cross | tests `T-FS-*` |
| `backend/tests/test_checks_crossdoc.py` | rules-inv-fs-cross | tests `T-X-*` |

No new top-level or shared files. Not touched: `core/**`, `checks/registry.py`, `pyproject.toml`, `uv.lock`, any other family's module or test. Needs from others: `core/models.py`, `core/testing.py`, `core/money.py`, `core/fingerprints.py` (foundation), `checks/registry.py` (foundation), C4 annotations on `transactions` rows (extract-fin, fed by the rules classifier in classify), applicant resolution (pipeline). Needs nothing from `names.py` (section 3.4).

## 9. Test plan

Run with `uv run pytest backend/tests/test_checks_bank_statement.py backend/tests/test_checks_invoice.py backend/tests/test_checks_financial_statement.py backend/tests/test_checks_crossdoc.py`. No network, no model call, no wall clock; `FIXED_TODAY = date(2026, 10, 27)` through `make_ctx`.

### 9.1 Shared fixtures (build with `make_doc`, `make_table`, `make_ctx`; values are the canonical strings)

- **`F-BS-1`** account `0012345678`, holder `EXAMPLE LOGISTICS PTE LTD`, `uen 201912345R`, period `2026-03-01..2026-03-31`, `opening_balance 10000.00`, `closing_balance 26290.71`, currency `SGD`, `page_chain` rows `1/1`. Rows (`date | description | debit | credit | balance`):
  1. `2026-03-02 | FAST PAYMENT FROM ACME TRADING PTE LTD | - | 12500.00 | 22500.00`
  2. `2026-03-05 | GIRO PAYMENT TO SUPPLIER ALPHA | 4200.00 | - | 18300.00`
  3. `2026-03-15 | MONTHLY ACCOUNT FEE | 12.50 | - | 18287.50`
  4. `2026-03-20 | FAST PAYMENT FROM BETA PTE LTD | - | 8000.00 | 26287.50`
  5. `2026-03-31 | INTEREST CREDIT | - | 3.21 | 26290.71`
  (credits 20503.21, debits 4212.50; C4 annotations `trade_receipt` on rows 1 and 4, `other` on row 5.)
- **`F-BS-2`** same account, period `2026-04-01..2026-04-30`, `opening_balance 26290.71`, one row `2026-04-10 | FAST PAYMENT FROM ACME TRADING PTE LTD | - | 5000.00 | 31290.71`, `closing_balance 31290.71`.
- **`F-INV-1`** as in section 5.
- **`F-FS-1`** (current year to 2026-03-31, scale `1`, `unaudited` or `audited`):
  - income statement: `revenue 1200000`, `cost_of_sales 700000`, `gross_profit* 500000`, `operating_expenses 300000`, `finance_costs 20000`, `profit_before_tax* 180000`, `income_tax 30600`, `net_profit* 149400`.
  - balance sheet: `cash_and_equivalents 250000`, `trade_receivables 300000`, `inventories 150000`, `total_current_assets* 700000`, `property_plant_equipment 400000`, `total_non_current_assets* 400000`, `total_assets* 1100000`, `trade_payables 200000`, `borrowings_current 100000`, `total_current_liabilities* 300000`, `borrowings_non_current 200000`, `total_non_current_liabilities* 200000`, `total_liabilities* 500000`, `share_capital 100000`, `retained_earnings 500000`, `total_equity* 600000`, `total_liabilities_and_equity* 1100000`.
  - cash flow: `profit_before_tax 180000`, `adjustments 30000`, `working_capital_changes -40000`, `net_cash_from_operating* 170000`, `capex -60000`, `net_cash_from_investing* -60000`, `loan_repayments -50000`, `net_cash_from_financing* -50000`, `net_change_in_cash* 60000`, `cash_opening 190000`, `cash_closing* 250000`.
  (`*` = `is_subtotal`.) The prior-year fixture `F-FS-0` has `current` columns equal to `F-FS-1`'s `prior` columns; build `prior` columns by copying.
- **Applicant** for rule tests: `Applicant(name="Example Logistics Pte. Ltd.", uen="201912345R", source="upload")`.
- **Valid UENs for tests:** `201912345R`, `202012345H`, `201934567C`, `53012345B` (computed with `stdnum.sg.uen`; the tests assert `uen.is_valid` on them so a library change is caught).

### 9.2 Cross-cutting tests (each module's file)

1. **Clean bundle is silent:** the clean bundle (`F-BS-1`, `F-BS-2`, `F-INV-1`, `F-FS-1`, `F-FS-0`, matching contract and paid-invoice credit) run through `run_stage_checks` for `rules` and `crossdoc` yields no flag above `info` and no `CheckRun.status == "error"`.
2. **Registration:** `discover()`; every rule id in section 3.1 appears in `all_checks()` with the stage, severity, `applies_to`, `requires` and `use_cases` stated there.
3. **No wall clock:** `test_no_wallclock_in_checks.py` (foundation) passes over these modules; `bs_dates` future test (`T-BS-DT-5`) and `T-INV-DA-3` prove `today` is injected.
4. **Determinism:** the same context run twice gives identical flag ids and evidence; documents supplied in a different order give identical ids.
5. **Untrusted text:** a description or name containing `"); DROP TABLE cases; --`, a 5000-character name and a newline stays within evidence limits, and appears truncated to 60 characters.
6. **Skip semantics:** a case without the required document yields `CheckSkipped` (rule recorded `skipped`); per-document gaps do not skip the rule.
7. **Unverified readings:** a cell with `verified=False` appends `; unverified reading: ...` to the evidence and does not change the severity.
8. **Pointer test:** for every row-level rule the flag's `table`, `row` and `region` equal the tampered cell's (localisation metric, plan 9.3).

### 9.3 Tamper catalogue mapping (plan 9.2, this doc's rows)

| Edit | Rule expected here |
|---|---|
| Change one deposit, balances left alone | `bs_running_balance` (+ `bs_totals`) |
| Change one deposit and recompute later balances | `bs_period_continuity` (if a neighbouring month exists) or `x_revenue_vs_credits` (hard case; `T-BS-RB-6`) |
| Remove a month | `bs_period_continuity` |
| Edit an invoice line, total left alone | `inv_lines` and `inv_total` |
| Same invoice submitted twice | `inv_duplicate` |
| Balance sheet figure changed | `fs_balance` |
| Revenue inflated on the financial statements | `x_revenue_vs_credits` (+ `fs_subtotals` if the subtotal was not rebuilt) |
| Invoices marked paid with no payment | `x_invoice_vs_receipts` |
| Contract value changed | `x_invoice_vs_contract` |

## 10. Acceptance criteria

1. All 28 rules (27 plan rules plus `file_reuse`) are registered with the metadata in section 3.1 and appear in the generated check catalogue with their `tamper` text.
2. Each rule has at least the unit tests listed in its subsection, in the four test files named in section 8, all passing under `uv run pytest`.
3. The clean bundle of 9.2 yields no flag above `info` and the three deck bundles' clean controls add no false `high` flag from these rules.
4. Every tampered fixture in section 9.3 produces its expected rule, with the flag's `row`/`field` and `region` pointing at the edited cell (localisation).
5. Evidence and explanation strings follow the templates (golden-string tests for at least `bs_running_balance`, `inv_gst`, `fs_subtotals`, `x_invoice_vs_receipts`).
6. Statistical hints (`bs_round_numbers`, `bs_benford`, `bs_duplicates`, `inv_sequence`) never exceed `low` severity, and `bs_benford` and `inv_sequence` gaps are `info`.
7. `uv run ruff check .` and `uv run ruff format --check .` are clean; no `float` used for money; no import of `datetime.now`, `date.today`, `time.time`.
8. The doc's "Known gaps" are reflected in the validation report, not hidden (plan 0.6).

## 11. Known gaps and honest limits (plan 0.6)

- **Hard case is a known miss for single-document rules:** a forger who edits a deposit and recomputes every later balance and the closing balance defeats `bs_running_balance` and `bs_totals`. Only `bs_period_continuity` (neighbouring month untouched), `x_revenue_vs_credits` and `x_cash_at_year_end` can catch it, and only when their inputs exist. The eval must report this (plan 9.4).
- **Sub-tolerance edits are invisible:** a change of 1.00 or less passes all money rules; on statements printed in thousands a one-unit edit passes `fs_*`.
- **No authenticity claims:** these rules prove inconsistency, never forgery; a consistent forgery passes. UEN validity proves the check letter, not registration; no registry lookup is done for suppliers.
- **GST rates and GST number formats** are from plan 1.4 and public IRAS facts and are marked **[confirm on IRAS]**; the GST number accepted formats are deliberately narrow and medium severity.
- **Coverage mismatch skips rules:** real bundles often hold recent statements and last year's financial statements. Then `x_revenue_vs_credits`, `x_cash_at_year_end` and part of `x_invoice_vs_receipts` skip, and coverage drops; the tool says so instead of passing silently.
- **Revenue proration assumes even revenue;** seasonal businesses, sales banked in accounts not supplied, cash sales and GST in credits move `r`. The thresholds are starting guesses to be set from the eval.
- **`fs_cash_tie` false positive** when closing cash is defined net of overdrafts; `fs_comparatives` suppresses reclassifications only inside a section; restatements are recognised only by the word `restated` in the label.
- **Signs on the balance sheet:** negative equity components other than retained earnings (for example treasury shares) are extracted as positive magnitudes (data-model.md section 6.3) and can break `fs_subtotals` for `total_equity` (false positive).
- **Statistical hints** (section 7.0) are weak by construction; Benford on bank amounts is unreliable.
- **Not built:** near-duplicate invoices (same supplier, date and total with a different number), invoice line-level match to PO lines, cross-account transfer netting, withholding tax, foreign-currency conversion (currency mismatch means not evaluated), peer-ratio benchmarking (plan 0.4). `file_reuse` catches only byte-identical files.
- **Name matching** is a string heuristic: transliteration variants of company names (Chinese-to-English), abbreviations (`INTL`) and renamed companies are not handled and appear as flags for the analyst to dismiss; the fairness slice test (plan 9.3) covers person names, not company names.
- **Dependence on extraction quality:** every rule inherits extraction errors; the `verified` / `alt_raw` suffix (convention 6) is the only mitigation here. Scanned pages cannot be read without fixtures or a vision key (architecture.md section 5), so on those bundles these rules are skipped, not passed.
- **W3 eval triage (T-integration-kyc-07).** (1) `bs_totals`, `bs_page_chain` and `bs_period_continuity` misfire on a statement split into one image per page (ingest-and-extraction.md section 11); digital and one-PDF-per-document scans are silent on clean bundles (0 false alarms in 33 digital bundles). (2) `x_remitter_vs_buyer` missed 2 of 10 T59 instances (loan company 1 and AML company 3): the swapped remitter was not matched as a candidate credit, while `x_invoice_vs_receipts` still raised a medium flag on the same bundle, so the tampered bundle is caught but the named rule is not. The candidate-credit window of the two rules differs on purpose (CR-21); no threshold was changed because the two misses give no basis for a new one. (3) T57 caught 4 of 5: on the fifth the clean base already carried a low `x_declared_vs_activity` flag, so no new flag is credited. (4) Weak tampers T08, T09, T17, T35 stay out of core recall; T08 and T35 catch 4 of 15, T17 0 of 10, T09 3 of 5.
- **Rule changes after the W3 triage (hand-off).** Four rules changed: `bs_round_numbers` (count trigger, 4.8), `bs_benford` (recurring amounts count once, 4.9), `x_remitter_vs_buyer` (mixed instalments and wider window, full spec in checks-kyc-aml.md 4.9: an instalment set of 2 to 4 credits, each at least 20% of the invoice, where the buyer paid some parts and another payer paid a part of at least 20% is flagged at **medium** instead of being dropped; if no set is found around the paid date and the invoice has a paid date, the search is repeated over invoice date to paid date + 5 days and kept only when the set is mixed) and `font_mix` (sibling-family trigger, forensics.md 4.4). Development seed 9101, digital, generator data only, thresholds fixed before the tampered cases were measured: T08 1/15 -> 15/15, T35 4/15 -> 15/15, T59 7/10 -> 10/10, T09 4/5 -> 5/5; the core digital recall on the dev seed went from 462/465 to 465/465 with no previously caught case lost, and digital false alarms at medium or higher stayed 0/33 clean bundles. These are the cases the changes were built for, on the generator whose tamper shapes were known, so they show the rules fire on those shapes, not that they generalise to real documents. T17 (removed invoices) stays 0/10: `inv_sequence` fires as an info hint on 10/10 but info flags do not count, and real invoice sets have gaps, so its severity was not raised. T02c (every figure recomputed consistently) stays 0/5; only external confirmation can detect it. No held-out figures are given here.

## Contract requests

**W3 closure (T-integration-kyc-08):** every request in this section has a recorded outcome in tasks.md sections 4.2 and 4.2a (accepted, decided at R0, or not built and listed under Known gaps above); none is open.

**R0 outcomes:** 1 accepted (plus `company_disc`/`uen_disc`); 2 accepted; 3 resolved as written; 4 accepted as `file_reuse` in `crossdoc.py` (section 7.6); 5 fixed in data-model.md and checks-kyc-aml.md; 6 accepted (applicant resolved between `extract` and `forensics`); 7 confirmed (the extract stage writes `c4`, `c5`, `counterparty`; ingest-and-extraction.md 6.7). Also at R0: `fs_comparatives` uses the coarser scale of the two years; `x_invoice_vs_receipts` accepts instalments; discriminators fixed in 3.4 and 5.7.

1. **`backend/app/core/textnorm.py` (foundation, wave 0).** Add `company_key(name) -> str`, `company_match(a, b) -> bool` and `COMPANY_MATCH_MIN` exactly as in section 3.4, with tests for the six reference pairs. Why: `bs_holder` (rules-bank) and `inv_parties`, `fs_entity`, `x_entity` (rules-inv-fs-cross) are built in parallel and must agree. Until it exists each module keeps an identical private copy.
2. **`core/fingerprints.py`: `invoice_key_fingerprint(supplier_uen: str, invoice_number: str) -> str`** (section 3.5) and a pipeline change (integration-loan, `pipeline.py`) to record both `invoice_fingerprint` and `invoice_key_fingerprint` under `kind="invoice"` (`meta={"role": "full"|"key"}`) for every invoice that has the inputs. Without it, `inv_duplicate` only finds the key match inside one case.
3. **Plan wording (resolved here, record in scoring/eval docs):** plan 4.2 `inv_parties` says "buyer is the applicant"; in receivables bundles the applicant is the supplier. This doc accepts the applicant on either side of the invoice.
4. **File-hash duplicate rule has no owner.** Plan 0.4 ("Duplicate document detection: file hash and invoice fingerprint store") and architecture.md section 6 (`fingerprints.kind = file_sha256`) record file hashes, but no rule id in plan 4.1 to 4.5 reads them. Needs an owner and a rule id (for example `file_reuse`) in forensics.md or here.
5. **data-model.md section 8 example UEN `201912345K` fails `stdnum.sg.uen.validate` (`InvalidChecksum`; the valid check letter for `201912345` is `R`).** Docs, generator and tests must use computed valid UENs (`201912345R`, `202012345H`, `201934567C`, `53012345B`), or `inv_ids` fires on every generated bundle.
6. **Applicant resolution is a hard dependency** for `bs_holder`, `inv_parties`, `fs_entity` and part (A) of `x_entity`: the pipeline must resolve `case.applicant` before the `rules` stage (data-model.md section 4.1 says "after extract"; confirm it runs before `forensics`/`rules`).
7. **C4 annotations on credit rows** (`Row.annotations["c4"]`) are an input to `x_revenue_vs_credits`; when the classify/extract stages are skipped the rule treats credits as unclassified (conservative). `ingest-and-extraction.md` should state which stage writes `c4` and `counterparty` (data-model.md section 6.1 says extract; the counterparty-parser is deterministic).
