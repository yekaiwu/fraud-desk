# Officer Workbench 04: Two-case demo scope (authoritative over plans 00-03 where they differ)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Read plan 00 first. This plan reorders the work around the demo the user will actually give: **two cases, Green Link Digital Bank (GLDB) and Yishun Anchor**. Where this file and plans 00-03 disagree, this file wins.

**Goal:** Every beat of the two-case demo below works on the demo host, survives a bad network, and is backed by a test or a recorded measurement.

**Findings from the code (2026-11-17) that shape this plan**
- **GLDB case** = `docs/demo-script.md` section 3b item 3: the Green Link Digital Bank KYC bundle (real name, UEN `202115950G`, real incorporation date from the sponsor; documents print a different date) plus an invoice from a shipping company on a sanctions list (`Chonmyong Shipping Co`). Built by `backend/scripts/make_showcase_bundles.py --out DIR --variant 1 --extras` (folder `gldb`; `sb.demo_extras`, `sb.GLDB_NAME`). The date check is `x_entity_vs_registry` (high) and needs the live ACRA lookup, which rate-limits (429) and then marks the case partial. The sanctions flag is `x_sanctions_screen` (medium).
- **Clementi Ember forensic specimen.** The user's existing demo folder contains `1-clementi-ember/national_id_2.jpg`: a fictional, specimen-marked national ID with a swapped photo. On 17 Nov, an isolated offline run of the existing `ela_region` rule on this exact JPEG returned two **low** flags: one box over the photo and a second, thin box across the card title. This is a single-file check on the then-local checkout, not a full-case or current-host acceptance test. The original PDF is in `spare/clementi-ember-original/`; use the JPEG for error-level analysis and preserve its bytes. The ID belongs to a separate fictional demo, so identify it on stage as an illustrative specimen, not as an ID of a GLDB director or customer.
- **Yishun Anchor** = `sb.challenger_demo` (`CHALLENGER_COMPANY = 6`, seed 4002, director Nadia Hartmann). v1 score about 90, High: `x_remitter_vs_buyer` (high), `x_sof_support` (high), `bs_pass_through` (medium), `inv_gst` (medium). v2 (add `supply_agreement_C-2026-002.pdf`) is about 60: the challenger lowers only `x_remitter_vs_buyer` to info and keeps `x_sof_support`, because that check is deterministic (`checks/aml.py:_sof_one`: credits count only if the counterparty name matches the declared counterparty, here Orchid Bay Holdings; the money came from three affiliates).
- **"Jensen Huang" was not found in the older local checkout, generated Yishun bundle, or shipped demo database.** Search the current implementation and accessible demo data again before asking the user where it appears (Task D2); do not claim a location without evidence.
- **"Ask this case" exists on current `origin/main`.** Use the existing read-only Ask tab (`frontend/src/components/case/AskPanel.tsx`, `backend/app/api/ask.py` and `docs/case-assistant.md`), not the separate custom-check authoring assistant. It requires `GLDB_CASE_ASSISTANT=on` and a personal sign-in. It cites case evidence or abstains and never changes the case score. The case officer's summary and suggested next steps are a separate existing card.
- **No email feature exists.** The only email-related text is in `backend/app/alerts.py` (webhook alerts). `VerdictForm.tsx` already tells officers to request documents in neutral terms.
- Custom checks are already able to say "GST rate below 9%": the invoice field `invoice.gst_rate` ("the GST rate printed on the invoice, as a percentage") is in `custom_rules/labels.py:60`. Custom findings are capped (`score.py`: custom family adds at most one point short of High) and the custom-rule maximum severity is medium; the officer picks low.
- Singapore GST has been 9% since 1 January 2024, so an invoice dated 2026 that prints 8% uses an out-of-date rate. That is the story for the low-risk custom finding.

## Demo script (replaces the one in plan 00)

**Case 1: GLDB (KYC), about 2 minutes. Upload it BEFORE the pitch; open the finished case on stage.**
1. Findings list. Open the incorporation-date flag (`x_entity_vs_registry`, rule-based, outside register): the document date and the register date side by side. Say it is a plain comparison, no model.
2. Sanctions flag (`x_sanctions_screen`): the list, entry and matched fields; "a person must adjudicate".
3. Open the Clementi Ember fictional ID specimen added for illustration, select the `ela_region` finding over the swapped photo, and use **Show on page**. Explain that the image is a separate synthetic specimen, not a GLDB person's ID. A second low region over the title can be an innocent scan artefact. Error-level analysis shows a compression difference: it is a hint requiring review, not proof of forgery. Never describe GLDB or a real person as implicated by the specimen.
4. A cross-document model-based flag: an invoice and bank flows that do not fit the company's declared main line of business, with the quoted text from both documents.
5. **Related cases** tab: the cross-case graph with this case in the centre and the other cases that share an identifier (the sanctioned supplier appears on other applicants' invoices).

**Case 2: Yishun Anchor (AML), about 3 minutes.**
1. Upload `1-upload-first` earlier; open v1: score about 90, High.
2. **Timeline** tab: the review team's trail; the challenger ruled on both judgement flags and kept both.
3. Open **Ask this case** and ask a question about the payers and the evidence available in v1; show a cited answer or honest abstention. Then show the separate **Case officer's summary** and **Suggested next steps** card. After adding v2, the same question can show the agreement citation if the model supports it.
4. **Evidence** tab: the gap "Payer is not the invoice buyer; no document links them". Select it, **Draft request**, tweak one line, **Record request**; the case moves to awaiting documents.
5. **Send email**: choose Gmail or Outlook (or the default mail app); the neutral request opens prefilled in the officer's own mail. (Fraud Desk does not send it.)
6. Customer replies: **Add documents** `supply_agreement_C-2026-002.pdf`. v2: the score falls sharply (target below), the Evidence tab shows the gap closed.
7. **Custom check**: type "Flag an invoice with a GST rate below 9%", severity low; show the readback; save; run it on the case: exactly one new **low** finding, and it is the only low-risk finding in the case.

## Task order for the demo (new tiers)

| Tier | Work | Where |
|---|---|---|
| A0 | Baseline measurement of both cases; find "Jensen Huang" | D1, D2 below |
| A1 | Yishun data: bigger drop, 8% GST invoice, one low finding | D3, D4 |
| A2 | Evidence tab + Follow-up Planner + Send email | plan 01 T1-T10 + D7 |
| A3 | GLDB demo upload, the Clementi Ember ID forensic specimen, cross-case links, **graph view**, seeded linked cases | D5, plan 03 T1-T5, D8, D9 |
| **CUT LINE A** | both cases demo-ready end to end | D10 |
| B | Impact preview (plan 02 W3), similar reviewed cases (plan 03 T6-T9) | requested work after the demo-critical slice |
| C | What changed (plan 02 W4); model wording (plan 01 T11) if used | change comparison is requested work, even though it is not in the two-case demo |

Do A0 first: its measurements decide D3-D5.

---

### Task D1: Baseline both demo cases (measure before changing anything)

**Files:** Create `docs/workbench-demo-baseline.md` (fictitious data only; no keys)

- [ ] **Step 1:** Generate the bundles (`make_showcase_bundles.py --challenger --out DIR` and `--out DIR2 --variant 1 --extras`). Run each case through the real pipeline with the model backend the demo host uses (ask the user to run the commands if no API key is configured in your session; each case costs a few cents). Use the CLI or API upload, not the fake backend: the challenger and the model-based checks only behave like the demo with a real model.
- [ ] **Step 2:** Record in the baseline file, for GLDB and for Yishun v1 and v2: score and band, and a table of every flag (`rule`, `severity`, `stage`, one-line evidence), plus which checks were skipped and why. Note any `low` flags and where `inv_gst` comes from (which invoice, why).
- [ ] **Step 3:** Record which semantic rule id carries "invoice or statements do not fit the declared business" (`docs/semantic-signals.md`; Sembrook's fraud flags include `x_declared_vs_activity`) and whether the GLDB bundle already raises it.
- [ ] **Step 4: Commit** `docs: demo baseline`. Report the table to the user before D3.

### Task D2: Find and remove "Jensen Huang"

- [ ] **Step 1:** Search the current checkout, generated Yishun bundle, existing screenshots and any already-accessible demo data first: use `rg` (excluding `node_modules`, `.venv`, `.next`), PDF text extraction and read-only database copies, including review entries and model cache where accessible. Do not alter a live database on speculation. If the text still cannot be located, ask the user which screen and case shows it; report the unresolved finding without claiming where it must be.
- [ ] **Step 2:** If it is generated text (a model wrote it into a summary or an agent answer), find the prompt or fixture that produced it and fix the cause. If it is data on the hosted instance, give the user the exact rows and the delete or edit command; do not edit `demo-data/demo-data.tar.gz`.
- [ ] **Step 3 (guard):** Create `backend/tests/test_demo_names.py` with a targeted `DENYLIST = ["Jensen Huang"]`; build the relevant challenger and showcase bundles in `tmp_path` and check their demo-facing text and PDF text for that name. Do not assert that all real names are absent: the agreed sponsor identity and a public sanctions-list entity are intentional in the GLDB showcase. Run it; commit `test: remove unwanted name from demo bundles`.

### Task D3: Make the Yishun score fall sharply in v2

**Files:** Modify `backend/scripts/make_showcase_bundles.py` (the agreement text in `challenger_demo`); Test `backend/tests/test_showcase_bundles.py` (keep every existing assertion)

**Target (measured on a real run, written into the baseline file):** v1 score >= 85 and band High, unchanged. v2 score at most 40, band not High, with `x_remitter_vs_buyer` lowered to info and `x_sof_support` lowered or absent. A fall of at least 45 points.

**Rule: do not change a check, a weight, a threshold or `score_weights.default.json`.** That would change every eval number and would be tuning the score to look good. Change only the demo document.

- [ ] **Step 1:** From D1, read exactly why the challenger kept `x_sof_support` in v2 (its quoted reason in the Timeline). The check itself stays deterministic and will still say "unsupported"; the target is that the challenger, which may lower a flag only by quoting the case's own documents, can rule on it.
- [ ] **Step 2:** Rewrite the agreement text in `challenger_demo` so that it states, in plain contract language, (a) the contract value is the declared S$1,000,000, (b) the buyer may discharge it by payment from the named affiliates, and (c) payment by an affiliate is payment by the buyer for the contract. Keep the three payer names and the specimen watermark; keep the "signed by a director of the applicant" property (the existing tests assert `Nadia Hartmann`, the payer names and "specimen").
- [ ] **Step 3:** Run the real pipeline three times on v1 then v2 (fresh cases each time). Record scores and the challenger's quoted reasons. If the challenger does not lower `x_sof_support` in at least 2 of 3 runs, revise the wording once (still honest contract text) and repeat. If it still does not, stop and tell the user: the demo will show the smaller drop, and the target cannot be reached without changing a check, which we will not do.
- [ ] **Step 4:** Keep the honesty line in `docs/demo-script.md` 3c and in the Q&A notes: the agreement was written to explain the flags, so the demo shows the mechanism, not a hit rate; the challenger lowered nothing in the measured runs.
- [ ] **Step 5:** Update `docs/demo-script.md` 3c with the measured v1/v2 scores. Commit `demo: agreement explains the declared amount`.

### Task D4: 8% GST invoice and exactly one low finding

**Files:** Modify `backend/scripts/make_showcase_bundles.py` (and the generator it calls, `backend/scripts/_gen/`, only through a parameter, CONFIRM); Test `backend/tests/test_showcase_bundles.py`

- [ ] **Step 1:** From D1, find why `inv_gst` fires on the Yishun upload and which invoice. Decide with the data, not by assumption: if it is the main invoice `INV-2026-0117`, the story is that the invoice prints GST at 8% (the rate that ended on 31 December 2023) with consistent arithmetic.
- [ ] **Step 2:** Make `INV-2026-0117` print `GST 8%` with `gst_amount = subtotal * 0.08` and the total consistent, dated in 2026. All other invoices print 9% consistently. If `inv_gst` should no longer fire, that is intended: the arithmetic is right and the rate is what the custom check tests. If the existing `inv_gst` flag is wanted in v1 for the score, keep a different arithmetic fault on another invoice; do not leave a `low` finding in the case.
- [ ] **Step 3: Test.** The built invoice text contains `8%` and the extracted `gst_rate` is 8 (use the extraction helper the invoice tests use). Add an engine-level test: `engine.evaluate` on a spec `field_vs_constant` (`invoice.gst_rate`, `lt`, 9) against the extracted Yishun v2 case returns one flag; against any other invoice in the bundle returns none.
- [ ] **Step 4: Acceptance on a real run (record in the baseline file):** v2 plus the saved custom check has exactly one flag with severity `low`, its rule id begins `cus_`, and no other flag in the case is `low`. If another `low` flag exists, report its rule and evidence; do not suppress it.
- [ ] **Step 5:** Custom-check demo prerequisites: `GLDB_CUSTOM_RULES=on`, the demo login is a personal account, the draft is saved with severity low, and the saved check runs on the open case (CONFIRM how: `components/case/CustomRuns.tsx`; the check may need a rerun to apply). Write the exact clicks into `docs/workbench.md`.
- [ ] **Step 6: Commit** `demo: invoice prints the retired 8% GST rate`.

### Task D5: Assemble the GLDB upload so all five beats appear

**Files:** Modify `backend/scripts/make_showcase_bundles.py` (`demo_extras` entry `gldb`); Test `backend/tests/test_showcase_bundles.py`

- [ ] **Step 1:** Using D1, list which of the five beats the current GLDB upload already shows. Expected: date mismatch and sanctions yes; forensic and the "does not fit the main business" semantic flag probably no.
- [ ] **Step 2:** Add the user's exact `national_id_2.jpg` from `~/Downloads/fraud-desk-demo/1-clementi-ember/` as a separately labelled fictional forensic specimen alongside the GLDB upload. Accept an explicit fixture path in the showcase builder; copy its bytes without re-encoding and fail clearly if it is unavailable. Do not make CI depend on the user's Downloads directory: unit tests use a generated synthetic JPEG and a temporary path. Do not imply the separate specimen identifies a GLDB director or customer. Also add one invoice, billed to `sb.GLDB_NAME`, for goods or services plainly unrelated to GLDB's declared business, so the semantic check (`docs/semantic-signals.md`) has something to quote. Keep the sanctions invoice.
- [ ] **Step 3: Tests:** the builder includes the selected JPEG byte-for-byte, labels it as a separate specimen, and includes the new invoice naming `sb.GLDB_NAME` as buyer; the targeted D2 name is absent. Exercise `ela_region` on the provided sample when available and assert a low-severity region overlaps the known swapped-photo box. Do not demand that every ELA region is a real edit; the title strip is an expected possible false lead. The portable test suite uses only synthetic fixtures and skips no essential generic JPEG checks.
- [ ] **Step 4: Acceptance on a real run (baseline file):** the case raises `x_entity_vs_registry`, `x_sanctions_screen`, an `ela_region` finding for the specimen with a clickable region over its photo, and the semantic rule from D1 Step 3. Verify **Show on page** in the browser and record the actual flag severity and any additional flags, including possible file-reuse flags if the same specimen was uploaded before. If the specimen or region cannot be reproduced, say so and use a clearly labelled captured demonstration as fallback; do not fabricate a flag or substitute a different detector without disclosure. If the semantic flag does not fire with the real model on three runs, report the invoice text you tried and stop.
- [ ] **Step 5 (reliability):** the live ACRA lookup rate-limits. Pre-upload and open the GLDB case on the demo host before the pitch and do not upload it live on stage. Add to `docs/workbench.md`: if the case shows "partial" because of a 429, wait a minute and use **Rerun**; and a screenshot fallback saved in the demo folder.
- [ ] **Step 6: Commit** `demo: GLDB upload shows forensic and model-based flags`.

### Task D6: reserved (no work)

### Task D7: Send email (open the officer's own mail; the server sends nothing)

**Files:**
- Create: `frontend/src/lib/mailLinks.ts`, `frontend/src/lib/mailLinks.test.ts`, `frontend/src/components/workbench/SendMenu.tsx`, `SendMenu.test.tsx`
- Modify: `backend/app/workbench/models.py` (`FollowUp.opened_via`, see below), `backend/app/api/workbench.py`, `backend/app/api/health.py` (`/me` key `webmail`), `FollowUpComposer.tsx`, `backend/tests/test_api_workbench_followups.py`

**Design decisions (do not change without asking the user):**
- Fraud Desk never sends email. The server holds no SMTP or mailbox credentials. The composer builds a link that opens the officer's own mail client or webmail with the text prefilled; the officer presses Send there. The UI says "Opens in your mail app. Fraud Desk does not send email itself."
- Providers: default mail app (`mailto:`), Gmail, Outlook (work or school), Outlook.com (personal), Yahoo Mail, and Copy.
- Webmail links carry the request text to Google, Microsoft or Yahoo through the officer's own browser. When `GLDB_PRIVATE_MODE=on`, offer only the default mail app and Copy: `/me` returns `webmail: false`. Private mode only guards the application: a separate mail client or copied text may still leave the private environment, so use only channels approved for the bank's data.
- The message (and subject) must have passed `verify_message` on the server before any link is shown. The record stores which target was used (`opened_via`), never the recipient address, and the UI says "Opened in Gmail", never "Sent".
- Recipient: an optional "To" field the officer types; it is not stored.

**Interfaces:** `mailHref(target: MailTarget, draft: MailDraft): string`, `MAIL_TARGETS`, `MAX_URL = 1900`; backend `FollowUp.opened_via: Literal["mailto","gmail","outlook","outlook_live","yahoo","copy"] | None = None`; the draft route also returns `subject: str`.

- [ ] **Step 1: Failing frontend tests**

```ts
// frontend/src/lib/mailLinks.test.ts
import { describe, expect, it } from "vitest";
import { mailHref, MAX_URL } from "./mailLinks";

const d = { to: "ops@example.com", subject: "Documents needed", body: "Dear A,\n\n1. Ownership chart\n\nThank you." };

describe("mailHref", () => {
  it("builds a mailto link with CRLF newlines", () => {
    const u = mailHref("default", d);
    expect(u.startsWith("mailto:ops%40example.com?subject=Documents%20needed&body=")).toBe(true);
    expect(u).toContain("%0D%0A");
  });
  it("builds Gmail, Outlook, Outlook.com and Yahoo compose links", () => {
    expect(mailHref("gmail", d)).toMatch(/^https:\/\/mail\.google\.com\/mail\/\?view=cm&fs=1&to=ops%40example\.com&su=Documents%20needed&body=/);
    expect(mailHref("outlook", d)).toMatch(/^https:\/\/outlook\.office\.com\/mail\/deeplink\/compose\?to=ops%40example\.com&subject=/);
    expect(mailHref("outlook_live", d)).toMatch(/^https:\/\/outlook\.live\.com\/mail\/0\/deeplink\/compose\?to=/);
    expect(mailHref("yahoo", d)).toMatch(/^https:\/\/compose\.mail\.yahoo\.com\/\?to=ops%40example\.com&subj=/);
  });
  it("allows an empty recipient", () => {
    expect(mailHref("gmail", { ...d, to: "" })).toContain("&to=&su=");
  });
  it("keeps every link under the length limit for the longest allowed message", () => {
    const long = { ...d, body: "x".repeat(900) };
    for (const t of ["default", "gmail", "outlook", "outlook_live", "yahoo"] as const) {
      expect(mailHref(t, long).length).toBeLessThan(MAX_URL);
    }
  });
});
```
Component test (`SendMenu.test.tsx`): renders one anchor per provider with `target="_blank"` and `rel="noopener noreferrer"` and the right `href`; clicking one calls `onOpened("gmail")`; with `webmail={false}` only "Default mail app" and "Copy" remain; the visible note says Fraud Desk does not send email itself; no element says "Sent".
- [ ] **Step 2: Run FAIL. Step 3: Implement**

```ts
// frontend/src/lib/mailLinks.ts
export type MailTarget = "default" | "gmail" | "outlook" | "outlook_live" | "yahoo";
export type MailDraft = { to: string; subject: string; body: string };
export const MAX_URL = 1900;
export const MAIL_TARGETS: { id: MailTarget; label: string; webmail: boolean }[] = [
  { id: "default", label: "Default mail app", webmail: false },
  { id: "gmail", label: "Gmail", webmail: true },
  { id: "outlook", label: "Outlook (work or school)", webmail: true },
  { id: "outlook_live", label: "Outlook.com (personal)", webmail: true },
  { id: "yahoo", label: "Yahoo Mail", webmail: true },
];
const e = encodeURIComponent;

export function mailHref(target: MailTarget, d: MailDraft): string {
  switch (target) {
    case "default":
      return `mailto:${e(d.to)}?subject=${e(d.subject)}&body=${e(d.body.replace(/\r?\n/g, "\r\n"))}`;
    case "gmail":
      return `https://mail.google.com/mail/?view=cm&fs=1&to=${e(d.to)}&su=${e(d.subject)}&body=${e(d.body)}`;
    case "outlook":
      return `https://outlook.office.com/mail/deeplink/compose?to=${e(d.to)}&subject=${e(d.subject)}&body=${e(d.body)}`;
    case "outlook_live":
      return `https://outlook.live.com/mail/0/deeplink/compose?to=${e(d.to)}&subject=${e(d.subject)}&body=${e(d.body)}`;
    case "yahoo":
      return `https://compose.mail.yahoo.com/?to=${e(d.to)}&subj=${e(d.subject)}&body=${e(d.body)}`;
  }
}
```
`SendMenu` renders anchors (not `window.open`, so popup blockers do not interfere), and a Copy button using `navigator.clipboard.writeText`. If a link would exceed `MAX_URL`, disable it and show "Too long for a mail link: use Copy." Add the component after a successful **Record request**, with the optional "To" field above it.
- [ ] **Step 4: Backend.** Add `opened_via` to `FollowUp`; add `POST /cases/{id}/follow-ups/{follow_up_id}/opened` with body `{"via": "gmail"}` that sets the field once (an append-only note: store a second small row or update the sealed body through the same helper `add_follow_up` uses; CONFIRM the pattern) and writes audit `workbench.follow_up_opened` with the target name only. The draft route returns `subject` (fixed neutral text `"Additional documents needed for your application"`) and `verify_message` is run on the subject as well. `/me` gains `webmail: settings.private_mode != "on"`. Tests: 422 for an unknown `via`; audit row has no message text; private mode -> `webmail False`.
- [ ] **Step 5:** CSP and navigation: check `frontend/next.config.*` and `src/proxy.ts` for any `Content-Security-Policy` or `navigate-to` rule that would block these links, and test in the real browser against the built app.
- [ ] **Step 6: Four frontend commands, backend tests. Commit** `feat(workbench): open the request in the officer's own mail`.

### Task D8: Cross-case graph view

**Files:** Create `frontend/src/components/workbench/RelatedGraph.tsx` + test; Modify `RelatedCasesPanel.tsx` (plan 03 T5)

**Interfaces:** Consumes the `Link[]` from `GET /cases/{id}/related`. Produces `<RelatedGraph centre={{caseId, label}} links={Link[]} />`.

- [ ] **Step 1: Read** `frontend/src/components/case/EntityGraph.tsx` and copy its conventions: SVG with a fixed `viewBox`, node and edge components, Graph/Table toggle (phones default to the table), `useIsSm()`, accessible table alternative, tooltips.
- [ ] **Step 2: Failing test.** Given a centre and three links (two cases, one hub summary): the table lists three rows with kind label, strength text, other case id and date, and "Same applicant"/"Different applicant"; the graph has one centre node and three outer nodes; the hub node is dashed and labelled "Common identifier, 21 other cases"; edges carry the kind label; no node shows an applicant name or an amount (assert the rendered text contains neither).
- [ ] **Step 3: Implement.** Deterministic radial layout: centre at the middle of the viewBox, `n` outer nodes at angle `2*PI*i/n - PI/2`, radius 220, at most 8 nodes plus one hub node; neutral tokens only (no red, amber or green); strong links drawn with a thicker stroke and the word "Strong" in the edge label (text, not colour). Each outer node links to `/cases/<id>`. Show above it: "Cases that share an identifier with this one. A link is a lead, not evidence of wrongdoing."
- [ ] **Step 4: Four frontend commands. Commit** `feat(workbench): cross-case graph view`.

### Task D9: Seed linked cases for the GLDB graph

**Files:** Create `backend/scripts/seed_linked_cases.py`; Modify `backend/scripts/make_showcase_bundles.py` (`DEMO_BUYERS` if needed); Test `backend/tests/test_seed_linked_cases.py`

- [ ] **Step 1:** Decide which identifiers the GLDB case can honestly share with other cases, using real extracted data from D1: the sanctioned supplier's UEN on the invoice (CONFIRM it has one; if the invoice prints only a name, add a `party_name` link kind (keyed HMAC of `company_key(name)`, always `weak`) in plan 03 T1 rather than inventing a UEN), and optionally a director NRIC or an account number if the generator can be given the same value.
- [ ] **Step 2:** The script creates three fictitious cases (references `SEED-LINK-1..3`) for existing demo companies (for example Jurong Kiln, Clementi Ember, Kallang Orbit), each carrying an invoice from the same sanctioned supplier, built from `sb.demo_extras` and the existing bundles. It records link keys (reuse the reindex function from plan 03 T4), refuses to run on a database with more than 50 cases, is idempotent by reference, and prints the three case ids.
- [ ] **Step 3: Test** with the fake LLM backend: after the script and `reindex`, `find_links` for the GLDB case returns at least three links, none `hub`; running twice adds nothing.
- [ ] **Step 4 (acceptance, real host):** the GLDB case's Related tab shows the graph with three outer nodes. Commit `demo: seed linked cases`.

### Task D10: Rehearsal, fallback and freeze (cut line A)

- [ ] Rewrite the demo section of `docs/workbench.md` from the script above, with the exact sentence to type for the custom check, the expected scores from D3, and the case ids to open.
- [ ] Back up the data directory, then on the demo host: set `GLDB_WORKBENCH=on`, `GLDB_CUSTOM_RULES=on`, `GLDB_CASE_ASSISTANT=on`, `GLDB_WORKBENCH_LINK_KEY`, rebuild the frontend, restart both agents (plan 00 checklist), sign in with a personal account, run `reindex_links.py` and `seed_linked_cases.py`, upload GLDB with the labelled fictional ID specimen and Yishun v1 BEFORE the pitch, and leave Yishun v2 and the custom check to be done live or pre-made as a fallback if the live step stalls. Measure their actual runtime; do not promise they take under a minute without a rehearsal.
- [ ] Rehearse both cases twice with a stopwatch (target 5 minutes in total). Record a screen capture of the complete flow as the offline fallback. Screenshots of every beat go in the demo folder.
- [ ] Final verification (show output): all four frontend commands, `uv run pytest`, `uv run ruff check .`, `gen_check_catalogue.py --check`, `git diff --stat main` shows no change to `docs/check-catalogue.md`, `score_weights.default.json` or `docs/eval-results/`.
- [ ] Freeze. If anything misbehaves on the day: `GLDB_WORKBENCH=off`, kickstart the backend; the original demo still works.

**CUT LINE A.** Report: baseline file, measured v1/v2 scores and flags (before and after D3/D4), the three real-model runs, screenshots of every beat on the demo host, the answer about "Jensen Huang", and anything that could not be made to work.
