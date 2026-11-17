# Officer Workbench 02: Impact Preview and What Changed

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Read the master plan first. Plan 01 Task 1 (flag, router skeleton) must be done; nothing else from plan 01 is required.

**Goal:** (W3) Before an officer saves a new plain-English check, show what it would do on past cases in three groups: would flag, would not flag, could not be checked, with the case lists. (W4) Compare an earlier company profile with a newer one and list what changed (directors, owners, address, business activity), each linked to the passage it came from.

**Architecture:** W3 extends the existing dry run (`backend/app/custom_rules/dryrun.py`), which already runs the real engine over stored cases with no model call; it only loses information today (it keeps counts and five example ids). W4 is a deterministic diff of two `Case` objects. Neither uses a model: extraction already happened in the pipeline, and the comparison must be reproducible. Say so honestly in the pitch ("AI reads the documents, code compares them").

## Facts relied on

- `dry_run(db, spec, *, limit) -> DryRunResult` (`custom_rules/dryrun.py:34`) selects `cases WHERE status != 'processing' ORDER BY created_at DESC LIMIT ?`, loads each with `db.load_case`, calls `engine.evaluate(rule, case, today=case.today)`. `Evaluation(flags, outcomes, skip_code, skip_reason, note)`; skip codes `SKIP_NO_DOCUMENT, SKIP_FIELD_MISSING, SKIP_SPEC_INVALID, SKIP_FAILED, SKIP_NOT_LOADED` (`custom_rules/engine.py:64-70`). `SKIP_FIELD_MISSING` is set only when ALL documents of the type are missing the field. Today `SKIP_NO_DOCUMENT` and `SKIP_FAILED` cases are silently skipped and appear only in `considered`.
- `DryRunResult` lives in `custom_rules/spec.py:803`, is a `_Frozen` model (`extra="forbid"`), is stored inside `DraftState` JSON, and older stored results must still validate: every new field needs a default (precedent: `readable`). `examples` has `max_length=5` and must keep working for old tests (`tests/test_custom_rules_dryrun.py`, `tests/test_api_custom_rules.py`).
- Endpoint: `POST /custom-rules/drafts/{id}/dry-run` (`api/custom_rules.py:258`). It needs `GLDB_CUSTOM_RULES=on` and a personal account (`require_account`, 404 `feature_off` / 403 `account_required`). Frontend: `components/custom-rules/DryRunPanel.tsx`, `lib/api.ts:431 dryRunCustomDraft`, types `DryRunResult` at `lib/types.ts:315-320`.
- Case versions: `case_versions` snapshots; `GET /cases/{id}?version=N`, `GET /cases/{id}/versions`; no diff exists. Document ids (`d1`...) and flag ids are regenerated each run, so never diff by id. `FileRef.added_in_version` tells which files are new. Reruns accumulate files, so a v2 built by adding a newer profile contains BOTH profiles: to compare an old and a new profile use two cases, or pick the profile from the newest `FileRef` of that type.
- KYC document fields: `company_profile` has table `directors` (`director_name, id_number, nationality, position, appointment_date`) and table `shareholders`; `ownership_chart` has table `holders` (`holder_name, holder_type, id_or_uen, percent, parent_name, is_ubo`); `shareholder_register` has table `shareholders` (`holder_name, id_or_uen, shares, percent, share_class, date_acquired`). **CONFIRM the company-level field names** (address, business activity, share capital) in `backend/app/extract/kyc_company_profile.py`.
- Helpers: `app.names.entity_key(name, kind)`, `app.names.normalise_id`, `app.core.textnorm.company_key`, `normalise_uen`.

---

## W3: Impact Preview

### Task 1: Three-way dry run

**Files:**
- Modify: `backend/app/custom_rules/spec.py` (`DryRunResult` near l.803), `backend/app/custom_rules/dryrun.py`
- Test: extend `backend/tests/test_custom_rules_dryrun.py`

**Interfaces:**
- Produces new `DryRunResult` fields, all defaulted:

```python
relevant: int = 0                       # cases with at least one document of a type the check reads
not_relevant: int = 0                   # cases with none (SKIP_NO_DOCUMENT)
checkable: int = 0                      # relevant cases where the check could read what it needs
would_flag_ids: list[str] = []
would_not_flag_ids: list[str] = []
could_not_check_ids: list[str] = []
could_not_check_reasons: dict[str, int] = {}   # {"field_missing": 3, "unreadable": 1}
summary: str = ""                       # written by code, see impact_sentence
```
and `impact_sentence(result) -> str` in `dryrun.py`.

- [ ] **Step 1: Failing tests.** Seed cases with the helper the existing dry-run tests use (CONFIRM), policy `field_vs_constant`: invoice `total` greater than 100000.

| Case | Content | Expected group |
|---|---|---|
| A | invoice total 150000 | would flag |
| B | invoice total 500 | would not flag |
| C | no invoice (only a bank statement) | not relevant |
| D | invoice whose `total` cell is empty | could not check, reason `field_missing` |
| E | invoice document `status="failed"` | could not check, reason `unreadable` |

Assert: `relevant == 4`, `not_relevant == 1`, `checkable == 2`, `would_flag_ids == [A]`, `would_not_flag_ids == [B]`, `set(could_not_check_ids) == {D, E}`, `could_not_check_reasons == {"field_missing": 1, "unreadable": 1}`, `summary == "Out of 4 relevant cases, 2 could be checked. 1 would be flagged. 2 could not be checked (1 field missing, 1 unreadable)."`. Also: a result JSON stored before this change (copy a fixture dict without the new keys) still validates via `DryRunResult.model_validate`; `examples` still holds at most 5 ids.
- [ ] **Step 2: Run FAIL. Step 3: Implement** inside the per-case loop, right after `result = engine.evaluate(...)`:

```python
if result.flags:
    outcome, reason = "would_flag", None
elif result.skip_code is None:
    outcome, reason = "would_not_flag", None
elif result.skip_code == SKIP_NO_DOCUMENT:
    outcome, reason = "not_relevant", None
elif result.skip_code == SKIP_FIELD_MISSING:
    outcome, reason = "could_not_check", "field_missing"
elif result.skip_code == SKIP_FAILED:
    outcome, reason = "could_not_check", "unreadable"
else:  # SKIP_SPEC_INVALID / SKIP_NOT_LOADED: not a statement about this case, leave it uncounted
    continue
```
then update the lists and counters. The existing counters (`considered`, `evaluated`, `fired`, `skipped_missing`, `readable`, `fire_rate`, warnings) must keep their exact current meaning: do not change any existing assertion in the old tests. Keep the id lists bounded by `limit`. `impact_sentence`:

```python
def impact_sentence(r) -> str:
    if r.relevant == 0:
        return "None of the cases checked contain a document this check reads."
    unchecked = len(r.could_not_check_ids)
    text = f"Out of {r.relevant} relevant cases, {r.checkable} could be checked. {len(r.would_flag_ids)} would be flagged."
    if unchecked:
        names = {"field_missing": "field missing", "unreadable": "unreadable"}
        why = ", ".join(f"{n} {names[k]}" for k, n in r.could_not_check_reasons.items())
        text += f" {unchecked} could not be checked ({why})."
    return text
```
`DryRunResult` is frozen: build the final instance once at the end with all fields. Check the persisted `DraftState` and `custom_rule_versions` JSON paths still round-trip (run `uv run pytest backend/tests -k custom_rules -q`).
- [ ] **Step 4: PASS. Step 5: Commit** `feat(custom-rules): three-way dry run with case lists`.

### Task 2: Impact Preview UI

**Files:** Modify `frontend/src/lib/types.ts` (add the new `DryRunResult` fields: this is the one necessary edit to that file; mention it in your report because `frontend/DESIGN.md` says not to edit it), `frontend/src/components/custom-rules/DryRunPanel.tsx`; Test: `DryRunPanel.test.tsx` (CONFIRM whether one exists; extend it)

- [ ] **Step 1: Failing test.** Given a result with the numbers above, the panel shows the `summary` text verbatim, three collapsible groups titled "Would be flagged (1)", "Would not be flagged (1)", "Could not be checked (2)", each case id a link to `/cases/<id>`, and the reasons next to unchecked cases. An old result without the new fields renders the previous view without error.
- [ ] **Step 2: Implement.** Rename the panel heading to "Impact preview" and add the line "Before you adopt this check, here is what it would have done on past cases." Use `<details>` for the three groups (keyboard accessible, no new dependency). Keep every existing control and warning.
- [ ] **Step 3: Run four frontend commands. Step 4: Commit** `feat(custom-rules): impact preview panel`.

### Task 3: Pick the demo sentence and document it

- [ ] Run a dry run on the loaded demo data for at least three candidate policy sentences (for example "Flag a receipt with no receipt number", "Flag an invoice with a total over 100,000", "Flag a passport that expired before the case date"). Choose the one whose preview shows all three groups non-empty. Write the sentence and the expected sentence of the preview into `docs/workbench.md` and `docs/custom-rules.md` (new section "Impact preview").
- [ ] Verify the demo login (`user1`) is a personal account and sees **Create custom check** with `GLDB_CUSTOM_RULES=on`. If not, report it: it is a known exposure.
- [ ] Commit `docs: impact preview demo notes`.

---

## W4: What Changed

### Task 4: Profile snapshot and diff

**Files:** Create `backend/app/workbench/changes.py`; Test `backend/tests/test_workbench_changes.py`

**Interfaces:**
- Consumes: `EvidenceRef` (plan 01 T2), `Case`.
- Produces: `PROFILE_FIELDS: dict[str, str]` (field name to label), `snapshot(case: Case) -> Snapshot`, `diff(before: Snapshot, after: Snapshot) -> list[Change]`.

```python
class Party(Strict):
    key: str        # normalised id/UEN if present, else entity_key(name)
    name: str
    detail: str     # "60%" for an owner, "Director" for a director
    ref: EvidenceRef | None = None

class Snapshot(Strict):
    case_id: str
    version: int
    reference: str | None
    fields: dict[str, tuple[str, EvidenceRef | None]]   # PROFILE_FIELDS key -> (text, ref)
    directors: list[Party]
    holders: list[Party]

class Change(Strict):
    group: Literal["company", "directors", "holders"]
    kind: Literal["added", "removed", "changed"]
    subject: str
    field: str | None
    before: str | None
    after: str | None
    before_ref: EvidenceRef | None = None
    after_ref: EvidenceRef | None = None
    text: str       # one sentence written by code
```

- [ ] **Step 1: Failing tests** (build two small cases with `make_ctx`/`make_doc`; CONFIRM kwargs):

| Test | Earlier | Newer | Expected changes |
|---|---|---|---|
| `test_no_change_is_empty` | profile with director Alice Tan | identical | `[]` |
| `test_director_replaced` | director Alice Tan | director Benjamin Lim | one `removed` (subject Alice Tan, text "Alice Tan is no longer listed as a director.") and one `added` (Benjamin Lim) |
| `test_main_shareholder_changed` | chart: Alice Tan 60%, XYZ Holdings Pte Ltd 40% | chart: XYZ Holdings Pte Ltd 60%, Alice Tan 40% | two `changed` rows; text "Alice Tan: stake changed from 60% to 40%." |
| `test_percent_formats_compare_equal` | `60%` | `60.00 %` | no change |
| `test_name_spelling_variants_are_not_changes` | `XYZ Holdings Pte. Ltd.` | `XYZ HOLDINGS PTE LTD` | none |
| `test_name_order_variant_person` | `Tan Alice` | `Alice Tan` | none |
| `test_business_activity_changed` | activity `Logistics` | `General trading` | one `changed`, group `company`, before `Logistics`, after `General trading` (use the confirmed field name) |
| `test_unreadable_document_is_skipped` | profile `status="failed"` on one side | | no spurious "removed" for every director: when a side has no readable profile, `diff` returns `[]` and the route says "no readable profile in <case>" (assert in the API test) |
| `test_newest_profile_wins` | case with two profiles from different `FileRef.added_in_version` | | `snapshot` uses the one from the newest file |
| `test_refs_point_into_each_side` | any change | | `before_ref.document_id` is a document of the earlier case, `after_ref` of the newer |
- [ ] **Step 2: Run FAIL. Step 3: Implement.** `snapshot`: pick the readable `company_profile` from the newest file (`FileRef.added_in_version` of `doc.file_id`; ties by document order); fields from `PROFILE_FIELDS` that exist; directors from its `directors` table (`key = normalise_id(id_number) or entity_key(name, "person")`, `detail = position or "Director"`); holders from the newest readable `ownership_chart` (`key = normalise_uen(id_or_uen) or normalise_id(id_or_uen) or entity_key(name, kind)`, `detail = percent normalised`), else from `shareholder_register`, else the profile's `shareholders` table. Percent normalisation: strip `%` and spaces and compare as `Decimal`; if it will not parse, compare the folded text. Text comparison folds case, collapses whitespace and strips `.,`. `diff`: companies fields in `PROFILE_FIELDS` order, then directors, then holders, each by key (removed = keys only before, added = keys only after, changed = same key with a different `detail`). Sentences are fixed templates, never from a model. Return `[]` if either snapshot has no profile, directors and holders all empty and no fields.
- [ ] **Step 4: PASS. Step 5: Commit** `feat(workbench): profile snapshot and diff`.

### Task 5: Route

**Files:** Modify `backend/app/api/workbench.py`; Test `backend/tests/test_api_workbench_changes.py`

**Interfaces:** `GET /cases/{case_id}/changes?version=M&against=<case_id>&against_version=N`. Defaults: `version` = current; `against` = same case; `against_version` = `version - 1`. Response:

```json
{"before": {"case_id": "...", "version": 1, "reference": "..."},
 "after":  {"case_id": "...", "version": 2, "reference": "..."},
 "changes": [ ... ], "readable": true,
 "note": "A change is a prompt to review, not evidence of wrongdoing."}
```
- [ ] **Step 1: Failing tests:** two cases with different directors give the expected `changes`; default compares `version-1`; `version=1` with no `against` -> 422 `validation_error` ("nothing earlier to compare with"); same case and same version -> 422; unknown `against` -> 404 `not_found`; flag off -> 404 `feature_off`; a side with no readable profile -> `readable: false`, `changes: []`.
- [ ] **Step 2: Implement** with `load_case_or_404` for both sides and `ApiError(422, "validation_error", ...)`. **Step 3: PASS. Step 4: Commit** `feat(workbench): changes route`.

### Task 6: What changed tab

**Files:** Create `frontend/src/components/workbench/ChangesPanel.tsx` + test; Modify `lib/api.ts` (`getChanges`), `lib/workbenchTypes.ts`, `app/cases/[id]/page.tsx` (tab "What changed", shown when `me.workbench` and (`version > 1` or the officer opens it to compare with another case))

- [ ] **Step 1: Failing test.** Given a response with a removed director, an added director and a changed stake: the table shows columns "Information", "Earlier record", "New record"; removed rows show the words "Not listed" in the new column; the note "A change is a prompt to review, not evidence of wrongdoing." is visible; a **Copy summary for the decision note** button copies one line per change (assert via a mocked `navigator.clipboard`). Empty `changes` shows `EmptyState` "No differences found between these two records." Unreadable shows "One of the records has no readable company profile."
- [ ] **Step 2: Implement.** Comparison picker: a `<select>` of this case's earlier versions (from `getVersions`) plus a text field "Compare with another case" (a case id; show the API error inline). Links: the earlier/new cells link to `/cases/<id>?v=<n>&tab=documents` (CONFIRM the query param the documents tab uses for a selected document and use it). No model call, no status colours: use neutral tokens.
- [ ] **Step 3: Four frontend commands. Step 4: Commit** `feat(workbench): What changed tab`.

### Task 7: Demo pair

- [ ] Generate the Sembrook bundles for both variants with the same company index and seed (`uv run python backend/scripts/make_bundle.py --scenario kyc --variant clean ...` and `--variant fraud ...`; CONFIRM flags in `docs/demo-script.md` section 1). Upload both as two cases. Open the clean case, **What changed**, compare with the fraud case. If the diff shows director or owner differences, that pair is the demo ("same company, two submissions").
- [ ] If they do not differ in profile data, add `backend/scripts/make_refresh_bundle.py` that calls the existing generator with changed director and shareholder inputs (change generator inputs; do not edit PDFs) and writes `samples/bundles/kyc-refresh/`. Add a test that the script runs and the diff against kyc-clean contains a director change and a stake change.
- [ ] Record the exact upload steps and expected rows in `docs/workbench.md`. Commit.

**CUT LINE 2 (with plan 03 T1-T6) / CUT LINE 3 (after T4-T7).** Report as in plan 01, plus screenshots of the Impact preview with all three groups and of What changed.
