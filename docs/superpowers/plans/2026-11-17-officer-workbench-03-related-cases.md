# Officer Workbench 03: Related Cases (cross-case links and similar reviewed cases)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Read the master plan first. Plan 01 Task 1 (flag, router skeleton) must be done.

**Goal:** A **Related cases** tab with two sections. (1) *Linked by shared identifiers*: other cases in this bank's own store that share a director/owner ID, a bank account number or a company UEN with this case, weighted so that common identifiers (registered-agent style hubs) do not produce noise. (2) *Similar reviewed cases*: earlier cases that raised the same findings, with the earlier officer's own recorded reason and what that case had that this one lacks.

**Architecture:** (1) extends the existing `fingerprints` table (it already powers `file_reuse`, `inv_duplicate`, `rcpt_duplicate`, `signature_reuse`) with three new keyed-HMAC kinds, recorded by the pipeline and read by a new route. (2) is deterministic retrieval over stored cases and review entries; the officer's words are shown verbatim. **No model is used in this plan.** An optional model summary is deliberately not planned: it is the riskiest, lowest-value piece for the deadline.

**Scope limits to repeat in the docs and the pitch:** single bank only; this is not COSMIC and shares nothing with other banks; a link is a lead for a person, never a score input and never grounds to decline.

## Facts relied on

- `fingerprints(kind, fingerprint, case_id, document_id, created_at, meta)`; `KINDS = ("file_sha256","invoice","receipt","crop_phash")` in `backend/app/core/fingerprints.py:22` is a constant nothing enforces; `SqliteFingerprintStore.lookup(kind, fp, *, exclude_case_id=None) -> list[FingerprintHit]`, `record(..., meta)` is `INSERT OR IGNORE`, `forget_case(case_id)`; `meta` is sealed, the hash is plaintext and searchable. A unique index on `(kind, fingerprint, case_id, COALESCE(document_id,''))` makes re-recording safe.
- `pipeline.record_fingerprints(ctx)` (`backend/app/pipeline.py:158`) calls `forget_case` first, then records file/invoice/receipt fingerprints with `meta = {applicant_uen, applicant_key, doc_type, ...}`, then `_record_crop_fingerprints` guarded by try/except (l.223-233): copy that guard. It runs after the stages, before status `open` (l.438). Failed runs call `forget_case` again (l.450).
- Deleting a case or the retention purge (`backend/app/retention.py`) removes its fingerprints: links disappear with the case. That is the expiry mechanism; add an age cut-off too (Task 3).
- `checks/_dedupe.py::same_applicant(ctx, meta)` shows how "same applicant" is decided (UEN, else `company_key` of the name). It takes a `CaseContext`; write a small equivalent that takes a `Case`.
- There is no per-case access control: any signed-in reviewer can open every case (`access.py`, `signed_in_reviewer`). So "cases the officer is allowed to access" means all cases; document it. Review text: `review_entries.body` -> `ReviewEntry.reason`, sealed when encryption is on, so read it through `app.review.load_entries(db, case_id)` (review.py:281, CONFIRM signature) and never with SQL. Final decision: `cases.decision` plaintext column or the last `kind="verdict"` entry.
- No search, embeddings or similarity exist anywhere; `GET /cases?q=` is `LIKE` on reference and applicant name only.
- Document fields to read (CONFIRM names in `backend/app/extract/*.py`): bank statement account number (`extract/bank_statement.py:61`, field `account_number`); company profile `directors.id_number`; ownership chart `holders.id_or_uen` and `holder_type`; shareholder register `shareholders.id_or_uen`; invoice `supplier_uen`; ID documents' number field (`national_id`, `passport`). Helpers: `app.names.normalise_id`, `app.names.looks_like_nric`, `app.core.textnorm.normalise_uen`.

---

### Task 1: Keyed link identifiers (pure)

**Files:** Create `backend/app/workbench/linkkeys.py`; Test `backend/tests/test_workbench_linkkeys.py`

**Interfaces:**
- Produces: `LINK_KINDS = ("person_id", "account_no", "party_uen")`, `LinkKey(NamedTuple: kind, fingerprint, document_id, role)`, `fingerprint(key: bytes, kind: str, value: str) -> str`, `link_keys(case: Case, key: bytes) -> list[LinkKey]`.

- [ ] **Step 1: Failing tests** (build cases with `make_ctx`/`make_doc`; CONFIRM kwargs)

| Test | Given | Expect |
|---|---|---|
| `test_same_value_same_fingerprint_across_cases` | two cases whose profile lists director `S1234567D` (one writes `s1234567d`) | the same `person_id` fingerprint |
| `test_fingerprint_is_keyed` | same value, two different keys | different fingerprints; neither contains the raw value |
| `test_kinds_do_not_collide` | account `12345678` vs a UEN `12345678` | different fingerprints (kind is inside the HMAC input) |
| `test_account_digits_only_and_minimum_length` | `"012-345-678"` -> digits `012345678`; `"123"` | first recorded, second ignored |
| `test_own_uen_is_not_a_party_link` | applicant UEN also appears as a holder | no `party_uen` for it |
| `test_corporate_holder_and_supplier_become_party_uen` | chart holder company with UEN, invoice `supplier_uen` | two `party_uen` keys with roles `holder` and `supplier` |
| `test_individual_holder_becomes_person_id` | chart holder individual with NRIC | `person_id` role `owner` |
| `test_unreadable_documents_give_nothing` | `status="failed"` documents | `[]` |
| `test_deduplicated` | same director in profile and ID document | one key per (kind, fingerprint, document) |

- [ ] **Step 2: Run FAIL. Step 3: Implement**

```python
# backend/app/workbench/linkkeys.py
"""Identifiers that can link one case to another, as keyed HMACs. The key lives in settings, never
in the database, so the plaintext fingerprint column cannot be reversed by trying every NRIC."""

from __future__ import annotations

import hashlib
import hmac
import re
from typing import NamedTuple

from app.core.models import Case, Document
from app.core.textnorm import normalise_uen
from app.names import looks_like_nric, normalise_id

LINK_KINDS = ("person_id", "account_no", "party_uen")
MIN_ACCOUNT_DIGITS = 6
READABLE = ("ok", "partial")


class LinkKey(NamedTuple):
    kind: str
    fingerprint: str
    document_id: str
    role: str


def fingerprint(key: bytes, kind: str, value: str) -> str:
    return hmac.new(key, f"{kind}|{value}".encode(), hashlib.sha256).hexdigest()


def _cell(row, column: str) -> str:
    cell = row.cells.get(column)
    return cell.text().strip() if cell is not None else ""


def _rows(doc: Document, table: str):
    t = doc.tables.get(table)
    return t.rows if t is not None else []


def link_keys(case: Case, key: bytes) -> list[LinkKey]:
    own_uen = normalise_uen(case.applicant.uen)
    found: dict[tuple[str, str, str], LinkKey] = {}

    def add(kind: str, value: str, doc: Document, role: str) -> None:
        if value:
            fp = fingerprint(key, kind, value)
            found.setdefault((kind, fp, doc.id), LinkKey(kind, fp, doc.id, role))

    def person_or_company(raw: str, doc: Document, role: str, holder_type: str = "") -> None:
        ident = normalise_id(raw)
        if not ident:
            return
        if holder_type != "company" and looks_like_nric(ident):
            add("person_id", ident, doc, role)
        else:
            uen = normalise_uen(raw)
            if uen and uen != own_uen:
                add("party_uen", uen, doc, role)

    for doc in case.documents:
        if doc.status not in READABLE:
            continue
        if doc.doc_type == "bank_statement" and "account_number" in doc.fields:
            digits = re.sub(r"\D", "", doc.fields["account_number"].text())
            if len(digits) >= MIN_ACCOUNT_DIGITS:
                add("account_no", digits, doc, "account")
        elif doc.doc_type == "company_profile":
            for r in _rows(doc, "directors"):
                person_or_company(_cell(r, "id_number"), doc, "director")
            for r in _rows(doc, "shareholders"):
                person_or_company(_cell(r, "id_or_uen"), doc, "owner")
        elif doc.doc_type == "ownership_chart":
            for r in _rows(doc, "holders"):
                htype = _cell(r, "holder_type")
                person_or_company(_cell(r, "id_or_uen"), doc, "holder" if htype == "company" else "owner", htype)
        elif doc.doc_type == "shareholder_register":
            for r in _rows(doc, "shareholders"):
                person_or_company(_cell(r, "id_or_uen"), doc, "owner")
        elif doc.doc_type == "invoice" and "supplier_uen" in doc.fields:
            uen = normalise_uen(doc.fields["supplier_uen"].text())
            if uen and uen != own_uen:
                add("party_uen", uen, doc, "supplier")
        elif doc.doc_type in ("national_id", "passport"):
            ident_field = next((f for f in ("id_number", "nric", "passport_number") if f in doc.fields), None)  # CONFIRM
            if ident_field:
                person_or_company(doc.fields[ident_field].text(), doc, "id_document")
    return list(found.values())
```
- [ ] **Step 4: PASS. Step 5: Commit** `feat(workbench): keyed link identifiers`.

---

### Task 2: Settings and recording in the pipeline

**Files:** Modify `backend/app/core/fingerprints.py` (`KINDS` gains the three kinds), `backend/app/core/config.py` (`workbench_link_max_age_days: int = 730`; `workbench_link_key` already added in plan 01 T1), `backend/app/pipeline.py`; Test `backend/tests/test_workbench_record_links.py`

- [ ] **Step 1: Failing tests** (use `MemoryFingerprintStore` from `core/testing.py` and the pipeline helper the existing fingerprint tests use; CONFIRM by reading `tests/test_pipeline*.py`):
  - flag off -> no link kinds recorded; flag on but key empty or shorter than 16 chars -> none.
  - flag on, key set -> the keys from `link_keys` are recorded with `meta` containing `applicant_uen`, `applicant_key`, `doc_type`, `role`.
  - `link_keys` raising does not stop `record_fingerprints` (monkeypatch it to raise; file/invoice fingerprints are still recorded; an exception is logged).
  - rerun replaces: after `forget_case`, only the latest version's keys remain.
  - existing tests that assert the exact `KINDS` tuple are updated, nothing else in them changes.
- [ ] **Step 2: Implement.** Extract the "who" meta into `_who(case)` (the dict built at the top of `record_fingerprints`) so both paths share it, then add at the end of `record_fingerprints`:

```python
count += _record_link_fingerprints(ctx, who)
```
```python
def _record_link_fingerprints(ctx: CaseContext, who: dict[str, Any]) -> int:
    """Cross-case link keys (workbench). Off unless GLDB_WORKBENCH=on and a link key is set; a failure
    here must never lose the fingerprints recorded before it."""
    settings = ctx.settings
    secret = settings.workbench_link_key.get_secret_value()
    if settings.workbench != "on" or len(secret) < 16:
        return 0
    try:
        keys = link_keys(ctx.case, secret.encode())
    except Exception:  # noqa: BLE001
        log.exception("link keys failed for %s", ctx.case.id)
        return 0
    docs = {d.id: d for d in ctx.case.documents}
    for k in keys:
        ctx.fingerprints.record(k.kind, k.fingerprint, case_id=ctx.case.id, document_id=k.document_id,
                                meta={**who, "doc_type": docs[k.document_id].doc_type, "role": k.role})
    return len(keys)
```
- [ ] **Step 3: PASS; run the whole `-k fingerprint` and `-k pipeline` suites. Step 4: Commit** `feat(workbench): record link keys in the pipeline`.

---

### Task 3: Finding links and the hub policy

**Files:** Create `backend/app/workbench/related.py`; Test `backend/tests/test_workbench_related.py`

**Interfaces:**
- Consumes: `link_keys`, `FingerprintStore.lookup`.
- Produces: `Strength = Literal["strong","weak","hub"]`, `HUB_CAP = 20`, `link_strength(kind: str, other_cases: int) -> Strength`, `Link`, `find_links(store, case, key, *, max_age_days, now) -> list[Link]`.

```python
class Link(Strict):
    kind: str                     # person_id | account_no | party_uen
    label: str                    # "Same director or owner ID", "Same bank account number", "Same company as an owner or supplier"
    role: str                     # how it appears in THIS case: director, owner, holder, supplier, account, id_document
    strength: Strength
    other_cases: int              # distinct other cases holding this key
    other_case_id: str | None     # None for hubs: they are summarised, not listed
    other_document_id: str | None
    other_created_at: UtcDatetime | None
    same_applicant: bool | None   # True/False/None, from the earlier case's meta only
```

> **OWNER DECISION (ask the user to confirm the numbers in your report; keep the defaults below until they answer).** `link_strength` decides what the officer sees. Defaults: a key shared by 20 or more other cases is a `hub` (listed as one summary line, never as individual cases); person IDs and account numbers shared by up to 3 other cases are `strong`; everything else `weak`. Company UENs (party_uen) are never `strong` on their own, because shared suppliers and registered agents are normal. A manual allowlist is NOT built: if the user wants one, it is `GLDB_WORKBENCH_LINK_ALLOWLIST` of `kind:value` pairs hashed at comparison time.

- [ ] **Step 1: Failing tests** (`MemoryFingerprintStore`; record keys for several cases by hand):

| Test | Expect |
|---|---|
| `test_shared_account_across_applicants_is_strong` | two cases, same account, different `applicant_uen` in meta -> one `strong` link, `same_applicant=False`, other case id, doc id |
| `test_same_applicant_resubmission_marked` | meta same applicant -> `same_applicant=True` |
| `test_hub_key_is_summarised` | key held by 21 other cases -> exactly one link, `strength="hub"`, `other_cases=21`, `other_case_id is None` |
| `test_company_uen_never_strong` | `party_uen` shared by 2 cases -> `weak` |
| `test_old_hits_ignored` | hit older than `max_age_days` -> no link |
| `test_excludes_self` | the case's own keys do not link to itself |
| `test_sorted_strong_first_then_weak_then_hub` | order as stated |
| `test_privacy_only_ids_and_dates` | `model_dump` contains no names, amounts or the raw identifier (assert the raw NRIC/account string is absent) |
| `test_link_strength_table` | `(person_id,1)->strong`, `(person_id,4)->weak`, `(party_uen,1)->weak`, `(account_no,20)->hub` |

- [ ] **Step 2: Run FAIL. Step 3: Implement.** For each `LinkKey` of the case: `hits = store.lookup(k.kind, k.fingerprint, exclude_case_id=case.id)`; drop hits older than `max_age_days`; `n = len({h.case_id for h in hits})`; skip if `n == 0`; `strength = link_strength(k.kind, n)`; hubs produce one summary `Link`; others produce one `Link` per other case (newest first, at most 5 per key). `same_applicant` is computed from `h.meta` (`applicant_uen` via `normalise_uen`, else `applicant_key == company_key(case.applicant.name)`, else `None`). Cap the final list at 20.
- [ ] **Step 4: PASS. Step 5: Commit** `feat(workbench): cross-case links with hub policy`.

---

### Task 4: Reindex existing cases (demo data)

**Files:** Create `backend/scripts/reindex_links.py`; Test `backend/tests/test_reindex_links.py`

Pipeline recording only runs on new runs; the demo database has ten older cases, so a script must index them without rerunning any model.

- [ ] **Step 1: Failing test:** an in-memory database with two saved cases sharing an account number; run `reindex(db, settings)` (the script's function); `find_links` for one returns the other; running it twice records nothing new (the unique index).
- [ ] **Step 2: Implement.** Open the database the same way `backend/scripts/run_case.py` does (CONFIRM), iterate `SELECT id FROM cases WHERE status != 'processing'`, `case = db.load_case(id)`, compute `link_keys` and call `SqliteFingerprintStore(db).record(...)` with the same `meta` as the pipeline (reuse `_who`). Print one line per case and a total. `--dry-run` prints counts only. It must refuse to run unless `GLDB_WORKBENCH=on` and the key is set.
- [ ] **Step 3: PASS. Step 4: Commit** `feat(workbench): reindex script for existing cases`.

---

### Task 5: Route and Related cases tab (links)

**Files:** Modify `backend/app/api/workbench.py`; Create `frontend/src/components/workbench/RelatedCasesPanel.tsx` + test; Modify `lib/api.ts` (`getRelated`), `lib/workbenchTypes.ts`, `app/cases/[id]/page.tsx` (tab "Related cases"); Test `backend/tests/test_api_workbench_related.py`

**Interfaces:** `GET /cases/{case_id}/related` returns `{"links_enabled": bool, "links": [Link...], "similar": [Similar...], "notice": "..."}`. `links_enabled` is false when no key is configured; `similar` stays `[]` until Task 6.

- [ ] **Step 1: Failing backend test:** two cases sharing an account via recorded keys -> `links` has the strong link; with no key -> `links_enabled False, links []`; flag off -> 404.
- [ ] **Step 2: Implement the route** (`require_workbench`; `load_case_or_404`; build `SqliteFingerprintStore(db)`).
- [ ] **Step 3: Failing frontend test:** shows "Same bank account number", a "Strong link" tag with text, "Different applicant" or "Same applicant", a link to `/cases/<id>`, and always the line "A shared identifier is a lead for the officer, not evidence of wrongdoing. Companies share addresses, accountants and suppliers." A hub renders as "A common identifier shared by 21 other cases. Not listed." When `links_enabled` is false: `Notice` "Cross-case links are off: no link key is configured."
- [ ] **Step 4: Implement** the panel (neutral tokens only; strength is text plus a small icon; no red/amber/green). **Step 5: Four frontend commands and the backend tests. Step 6: Commit** `feat(workbench): Related cases tab with shared-identifier links`.

**CUT LINE 2 (together with plan 02 W3).**

---

### Task 6: Similar reviewed cases (deterministic)

**Files:** Create `backend/app/workbench/similar.py`; Test `backend/tests/test_workbench_similar.py`

**Interfaces:**
- Produces: `SCAN_LIMIT = 100`, `Similar` (model below), `similar_reviewed(db, case, *, top: int = 3) -> list[Similar]`.

```python
class Similar(Strict):
    other_case_id: str
    other_created_at: UtcDatetime
    outcome: str                  # the earlier verdict's decision value, e.g. "approve", "refer_edd"
    shared: list[dict[str, str]]  # [{"rule": "x_remitter_vs_buyer", "title": "<CheckMeta.description>"}]
    officer_reason: str           # the earlier officer's own words, clipped to 300 characters
    differences: list[str]        # sentences written by code
    score: float                  # |shared| / |union|, shown rounded or not at all
```
Rules: candidates are the latest `SCAN_LIMIT` cases other than this one, `status != 'processing'`, same `use_case`, with at least one effective `kind="verdict"` review entry (read with `load_entries`). Each candidate is loaded at its latest snapshot. Rule sets are the rules of flags with severity `high|medium|low`. A candidate qualifies only if it shares at least one rule that is `medium` or `high` in the candidate. Rank by `score` then recency; return `top`. `officer_reason` and `outcome` come from the candidate's LAST effective verdict entry. `differences`: for each document type in `{contract, purchase_order, ownership_chart, shareholder_register, source_of_funds_declaration, national_id, passport}` that the candidate has readable and this case does not, one sentence from `DOC_LABEL` (for example `"The earlier case also had a contract or agreement; this case has none."`); and the reverse is NOT reported.

- [ ] **Step 1: Failing tests** (seed with `env.seed(...)` and `env.review(...)` from `tests/test_api_support.py`):

| Test | Expect |
|---|---|
| `test_returns_reviewed_case_with_shared_rule_and_reason` | candidate with `x_remitter_vs_buyer` medium and a verdict reason `"Paid by the buyer's group treasury; agreement received."` -> one `Similar`, `outcome`, `officer_reason` equal to the reason (<= 300), `shared[0]["rule"] == "x_remitter_vs_buyer"` |
| `test_unreviewed_cases_are_excluded` | candidate without verdict entries -> `[]` |
| `test_other_use_case_excluded` | kyc candidate for an aml case -> `[]` |
| `test_info_only_overlap_does_not_qualify` | shared rule only `info` -> `[]` |
| `test_difference_sentence` | candidate has a `contract` document, this case none -> the sentence appears |
| `test_ranking_and_top` | three candidates -> highest overlap first, at most `top` |
| `test_never_returns_self` | the case itself never appears |
| `test_reason_is_clipped` | 900-char reason -> 300 chars |
| `test_decryption_path` | works with encryption at rest on (reuse the fixture the review tests use for sealed entries; CONFIRM it exists) |

- [ ] **Step 2: Run FAIL. Step 3: Implement** with `load_entries(db, case_id)` for review text and `app.checks.registry.get_check(rule).description` for titles (fall back to the rule id when a custom rule has no entry). Define `DOC_LABEL = {"contract": "contract or agreement", "purchase_order": "purchase order", "ownership_chart": "ownership chart", "shareholder_register": "shareholder register", "source_of_funds_declaration": "source of funds declaration", "national_id": "national ID", "passport": "passport"}`. The sentence template is exactly `f"The earlier case also had a {label}; this case has none."`.
- [ ] **Step 4: PASS. Step 5: Commit** `feat(workbench): similar reviewed cases`.

### Task 7: Wire similar cases into the route and tab

**Files:** Modify `backend/app/api/workbench.py`, `RelatedCasesPanel.tsx` and tests, `lib/workbenchTypes.ts`

- [ ] **Step 1: Failing tests.** Backend: `/related` returns `similar` with the seeded earlier case. Frontend: section "Similar reviewed cases" shows the shared finding titles, the outcome word via `lib/labels.ts` (never the raw enum), the earlier officer's words in a quote block inside a collapsed `<details>` titled "How the earlier officer decided", the difference sentences, and the permanent notice: "Context from earlier reviews. It is not a ruling: past decisions can be wrong and this case may differ." The panel never offers an "apply this decision" control.
- [ ] **Step 2: Implement. Step 3: Run all suites. Step 4: Commit** `feat(workbench): similar reviewed cases in the Related tab`.

### Task 8: Seed a small fictitious reviewed corpus

**Files:** Create `backend/scripts/seed_reviewed_cases.py`; Test `backend/tests/test_seed_reviewed_cases.py`

- [ ] **Step 1: Failing test:** running the script's function against a temporary database with the fake LLM backend creates N cases, each with at least one verdict entry, using only fictitious data; running it twice does not duplicate (idempotent by reference prefix `SEED-`).
- [ ] **Step 2: Implement.** Build three AML bundles with `make_bundle.main(["--scenario","aml","--variant","fraud","--seed","4002","--company-index", i, "--render","digital"])` for indices other than 6 (the Yishun company; CONFIRM which indices exist and raise `x_remitter_vs_buyer`), create each case through the same code path `run_case.py` uses, then record verdicts through the review module with scripted fictitious reasons. Use at least two contrasting outcomes so it does not look like a rubber stamp:
  - `"Payment came from the buyer's group treasury entity. Supply agreement received and read. Approved."` (approve)
  - `"Payer has no stated link to the buyer and no agreement was supplied. Referred for enhanced due diligence."` (refer_edd)
  - `"Documents requested to explain the payer. Awaiting reply."` (request_documents)
  Mark the cases `reference="SEED-<n>"`. The script refuses to run against a database that already holds more than 50 cases, and prints a reminder to back up the data directory first.
- [ ] **Step 3: PASS. Step 4: Commit** `feat(workbench): seed script for reviewed demo cases`.

### Task 9: Docs and limits

- [ ] Write the "Cross-case links" section of `docs/workbench.md` and add to `docs/limitations.md`:
  - Single bank only. Not COSMIC and not a substitute for it; nothing is shared with any other institution.
  - A link is a lead, not evidence; shared registered-agent addresses, accountants, suppliers and common surnames are normal. Hub identifiers are summarised, not listed. The thresholds are a proposal (`link_strength`), not validated.
  - Identifiers are stored as keyed HMACs; hashing is not anonymisation and the hashes are still personal data. Links expire with the case (deletion and retention) and after `GLDB_WORKBENCH_LINK_MAX_AGE_DAYS`.
  - Not assessed: the PDPA legitimate-interests assessment a bank would need before running this (the research notes say PDPC's key-concepts guidance gives a bank network-analysis example with documented-assessment conditions) and the Notice 626 retention figure. Mark both `[confirm]`; do not claim compliance.
  - No labels exist, so no precision or recall is claimed; tests use seeded rings and hub traps only.
  - "Similar reviewed cases" is rule overlap plus the earlier officer's words. It can anchor an officer; the panel says so.
- [ ] Add one row to the check/feature docs if `tests/test_compliance_docs.py` requires it; run it.
- [ ] Commit `docs: cross-case links and similar cases`.

**CUT LINE 3.** Report with screenshots of the Related cases tab on the Yishun v1 case (links and similar section), the reindex output, and the full test totals.
