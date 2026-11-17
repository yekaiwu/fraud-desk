# Officer Workbench: Master Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Read this file and plan 04 first, then the feature plan you are on (01, 02 or 03). Plan 04 takes precedence for the two-case demo wherever the earlier plans differ.

**Goal:** Turn Fraud Desk from "a list of red flags" into a review workbench: for every case the officer sees what evidence exists for each review item and where it came from (Evidence Map), what to ask for next (Follow-up Planner), what a new policy would affect (Impact Preview), what changed since the last review (What Changed), and which other cases relate (Related Cases).

**Architecture:** Everything is a read-side *view* over data the pipeline already stores (`Case`, `check_runs`, `flags`, `documents`, `case_versions`, `review_entries`, `fingerprints`). New code lives in `backend/app/workbench/` and one auto-discovered router `backend/app/api/workbench.py`. Status, gaps, diffs and links are decided by deterministic code; an LLM is used in one place only (wording of a follow-up request) and always has a template fallback.

**Tech Stack:** Python 3.12, FastAPI, Pydantic (`Strict`), SQLite, pytest (`uv run`), Next.js 16, React, Tailwind v4, SWR, Vitest.

**Written from:** four read-only code maps made on 2026-11-17, not from reading every line. Where this plan says **CONFIRM**, read the named file and let the code win; report any mismatch instead of forcing the plan.

## Global Constraints

- **No new check rules.** Do not add `@check` functions. That keeps `ruleset_version`, scoring, `docs/check-catalogue.md` and every eval number unchanged. Run `uv run python backend/scripts/gen_check_catalogue.py --check` at each cut line: it must still pass untouched.
- **Feature flag.** New setting `GLDB_WORKBENCH` (`"on"`/`"off"`, default off), in `backend/app/core/config.py` and `.env.example`. Every workbench route raises `ApiError(404, "feature_off", ...)` when off. `GET /me` gains `workbench: bool`. Do NOT add it to `result_settings()` (it does not change analysis results). Setting it to `off` and restarting the backend must hide every workbench tab: that is the demo fallback.
- **Wording (frontend).** `frontend/src/components/wording.test.ts` bans the words `authentic`, `genuine`, `verified` (any case) in every non-test file under `components/`, `app/` and most of `lib/`. Say "confirmed", never "verified". Status label for an item with no outside record is **"Not independently confirmed"**. No `dangerouslySetInnerHTML`. No `Number()` or `parseFloat()` on money. Use `lib/labels.ts` words for severity and status. Fictitious data only.
- **Wording (anything that can reach an applicant).** A follow-up request draft must be neutral. It must never contain: rule ids (snake_case), "flag", "fraud", "suspicious", "sanction", "AML", "laundering", "score", "forged/fake/tamper", "alert", "investigat", or the words "Fraud Desk". Code verifies this (see plan 01, `verify_message`); the model is never trusted to follow it. This matches `VerdictForm.tsx`: "Ask for documents in neutral terms. Do not say which checks fired."
- **No automatic customer messages.** The workbench drafts and records neutral text. As specified in plan 04 D7, it may open the officer's own mail client or webmail with a draft; only the officer can send from that provider. Fraud Desk itself does not send the message or claim it was sent. In private mode disable webmail links; Copy and the default mail client remain available.
- **Privacy of cross-case output.** Report only case id, document id, date and a same/different-applicant bit (the existing `fingerprints` rule). Identifiers in `fingerprints.fingerprint` are keyed HMACs (`GLDB_WORKBENCH_LINK_KEY`), never plain NRIC/UEN/account numbers. Names go only in sealed `meta`.
- **LLM calls.** Purpose prefix `workbench:`. Build a throwaway `CostMeter` exactly like `custom_rules/author.py:_ask` (l.1098) so spend caps apply. Wrap document-derived text with `wrap_untrusted`. Do not call `register_prompt` at import time. Tests use `FakeLLMClient` and assert `.calls` is empty when the feature is off or the model path is not taken. `backend/tests/conftest.py` blocks network sockets: keep it that way.
- **Honesty.** Add to `docs/limitations.md`: views are derived from extracted fields (unread pages are "not checked"); "similar cases" is keyword/rule overlap, not a judgement; cross-case links are single-bank leads, not proof; no real labels, so no precision/recall claim.
- **Commands.** Backend: `uv sync --dev`, `uv run ruff check .`, `uv run ruff format .`, `uv run pytest`. Frontend (in `frontend/`): `npm run lint`, `npx tsc --noEmit`, `npm test`, `npm run build`. Single tests: `uv run pytest backend/tests/test_x.py -k name`; `npx vitest run src/path/File.test.tsx`.
- **Git.** Work on branch `feature/officer-workbench`. One commit per task. Never push. Never touch `demo-data/demo-data.tar.gz`, `docs/eval-results/`, or the pptx decks. Commit messages end with the attribution lines the harness gives.
- **Style.** Match surrounding code: `Strict` models, `from __future__ import annotations`, ruff line length 100, no wall-clock reads in pure logic, `Decimal` for money, short docstrings that say why.

## File Structure

| Path | Responsibility |
|---|---|
| `backend/app/workbench/__init__.py` | package marker |
| `backend/app/workbench/models.py` | Pydantic shapes for every view (plan 01 T2) |
| `backend/app/workbench/items.py` | data-only list of review items and request options |
| `backend/app/workbench/gaps.py` | deterministic evidence-gap detection |
| `backend/app/workbench/evidence.py` | `build_evidence_map(case, previous)` |
| `backend/app/workbench/followup.py` | draft, verify, optional model polish |
| `backend/app/workbench/changes.py` | profile snapshot and diff (plan 02) |
| `backend/app/workbench/linkkeys.py` | keyed identifiers for cross-case links (plan 03) |
| `backend/app/workbench/related.py` | links, hub policy, similar reviewed cases |
| `backend/app/api/workbench.py` | all routes, thin |
| `backend/scripts/reindex_links.py`, `seed_reviewed_cases.py` | demo data helpers |
| `frontend/src/components/workbench/*` | panels and composer |
| `frontend/src/lib/workbenchTypes.ts` | types (do not edit `lib/types.ts`; `frontend/DESIGN.md` says not to) |
| `docs/workbench.md` | feature doc; also update `docs/api.md`, `docs/frontend.md`, `docs/custom-rules.md`, `docs/limitations.md`, `docs/ai-inventory.md` |

## Order, time boxes and cut lines

Pitch is 2026-11-19. Times are a guide for one agent working continuously; the order matters more than the numbers. **At every CUT LINE: run all four frontend commands, the full backend suite and the catalogue check; commit; update the demo notes in `docs/workbench.md`; only then continue.** If time runs out, stop at the last passed cut line and set `GLDB_WORKBENCH` accordingly.

| Phase | Work | Plan | Est. |
|---|---|---|---|
| 0 | Branch, baseline green, flag, `/me`, router skeleton, `docs/workbench.md` skeleton (headings: what it is, demo script, limits) | 01 T1 | 1 h |
| 1 | Evidence Map backend + tab | 01 T2-T7 | 5 h |
| 1 | Follow-up Planner: gaps, template draft, record, tracking, UI | 01 T8-T10 | 4 h |
| **CUT LINE 1** | Evidence and follow-up work ready; final demo uses GLDB and Yishun as specified in plan 04 | | |
| 2 | Impact Preview (three-way dry run) | 02 W3 | 3 h |
| 2 | Cross-case links: keys, hub policy, reindex, panel | 03 T1-T6 | 5 h |
| **CUT LINE 2** | (Tier B) | | |
| 3 | What Changed | 02 W4 | 4 h |
| 3 | Similar reviewed cases + seed script | 03 T7-T9 | 3 h |
| 3 | Optional model polish for follow-up wording | 01 T11 | 2 h |
| **CUT LINE 3** | (Tier C) | | |
| 4 | Docs, demo script, deploy to demo host, rehearsal, freeze | below | 4 h |

## Demo script

The authoritative order and acceptance checks are in **plan 04**. Present two cases: Green Link Digital Bank (date mismatch, sanctions, the separately identified Clementi Ember fictional-ID forensic specimen, model-based comparison and cross-case graph), then Yishun Anchor (Timeline, existing Ask tab, evidence gap, follow-up and mail draft, added agreement, measured score change and the low-severity GST custom check). Do not present the Clementi specimen as a GLDB person's ID. The impact preview, change comparison and similar-cases features remain requested implementation work, even though they are not all shown in the two-case demo.

## Phase 4: release checklist

- [ ] `docs/workbench.md` written (what each view does, what is code vs model, limits, demo script).
- [ ] `docs/api.md`, `docs/frontend.md`, `docs/limitations.md`, `docs/ai-inventory.md` (new purpose `workbench:followup`, if T11 done), `.env.example` updated.
- [ ] Full suites green; `gen_check_catalogue.py --check` passes; `git diff --stat main` shows no change to `check-catalogue.md`, `score_weights.default.json`, or anything under `docs/eval-results/`.
- [ ] Demo host: add `GLDB_WORKBENCH=on`, `GLDB_CUSTOM_RULES=on`, `GLDB_CASE_ASSISTANT=on`, `GLDB_WORKBENCH_LINK_KEY=<16+ chars>` to `deploy/.env.production`; `cd frontend && npm run build`; `launchctl kickstart -k gui/$(id -u)/com.jay.gldb-backend` and `...gldb-frontend`; run `deploy/status.sh`.
- [ ] On the demo host: run `backend/scripts/reindex_links.py` and `seed_reviewed_cases.py` (plan 03). Back up the data dir first (`deploy/load-demo-data.sh` replaces it; do not run that script).
- [ ] Log in as the exact personal demo account (`user1`). Confirm the existing **Ask** tab and the Evidence, Related cases and What changed tabs show, and **Create custom check** shows. Ask and custom rules each need their respective feature switch and a personal account.
- [ ] Rehearse the plan 04 two-case script twice with a stopwatch, including the ID photo-region overlay, and record actual timings. Record a screen capture as the offline fallback.
- [ ] Freeze: no code changes in the last 12 hours before the pitch. Fallback if anything misbehaves: `GLDB_WORKBENCH=off`, kickstart the backend.
