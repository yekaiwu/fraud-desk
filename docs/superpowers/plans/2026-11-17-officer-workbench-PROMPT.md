# Instruction prompt: build the Officer Workbench

Paste everything below the line into a fresh Claude Code session started at the Fraud Desk repository root.

---

You are implementing the "Officer Workbench" for Fraud Desk, a Singapore bank document-fraud tool (FastAPI + Next.js). It is judged in a pitch on 2026-11-19, so the work is ordered so that stopping at any cut line leaves a working, demo-able product.

**Read first, in order:** `docs/superpowers/plans/2026-11-17-officer-workbench-00-master.md`, `...-04-demo-scope.md`, then `...-01-evidence-and-followup.md`; read `...-02-policy-and-changes.md` and `...-03-related-cases.md` when you reach them. Plan 04 is authoritative wherever the earlier plans conflict about the demo. Also read `CONTRIBUTING.md`, `frontend/AGENTS.md` and `frontend/DESIGN.md`.

**Two-case demo:** Present Green Link Digital Bank and Yishun Anchor. GLDB shows the incorporation-date comparison, sanctions, a forensic region, model-based cross-document evidence and the cross-case graph. For the forensic beat use the user's fictional Clementi Ember `1-clementi-ember/national_id_2.jpg` specimen: preserve the JPEG bytes, show the low-severity `ela_region` box over the swapped photo, and explain that it is an illustrative specimen from another fictitious case, not a GLDB person's ID. The second title-strip region can be a benign artefact; an ELA hint alone does not prove fraud. Plan 04 D5 specifies portable fixture handling and acceptance. Yishun shows Timeline, the existing **Ask** tab, suggested follow-up and evidence, a neutral email draft opened in the officer's selected provider, the supplied agreement and measured score change, then the single low-severity GST custom finding. Do not fabricate results or suppress other findings to make the demonstration fit.

**How to work**
1. Use `superpowers:subagent-driven-development` (or `superpowers:executing-plans`) and follow each task's steps in order: failing test, see it fail, minimal code, see it pass, ruff, commit. One commit per task, on branch `feature/officer-workbench`. Never push. Use `uv` for Python (`uv run pytest`, `uv run ruff`), `npm` inside `frontend/`.
2. The plans were written from code maps, not from reading every line. Where a plan says **CONFIRM**, open the named file and let the code win. If the plan and the code disagree, do not force the plan: make the smallest change that honours the plan's intent, and list the mismatch in your report.
3. Obey the master plan's Global Constraints exactly. The ones people forget: no new `@check` rules (the catalogue check must stay green and unchanged); the frontend must never contain the words "verified", "authentic" or "genuine" (use "confirmed"); anything that could reach an applicant must pass `verify_message`; workbench routes are 404 `feature_off` unless `GLDB_WORKBENCH=on`; cross-case identifiers are keyed HMACs. Fraud Desk does not send email itself: plan 04 D7 opens a draft in the officer's own mail client or webmail for their separate review and send action, and must never claim it was sent.
4. Do not touch: `demo-data/demo-data.tar.gz`, `docs/eval-results/`, any `.pptx`, `deploy/.env.production`, or `docs/check-catalogue.md`. Do not run `deploy/load-demo-data.sh`.
5. Before any claim that something works, run the command and show its output (`superpowers:verification-before-completion`). A passing unit test is not a working UI: for every frontend task, run the app locally with `GLDB_WORKBENCH=on` and look at it at 390 px and 1440 px.

**Verify and report at each CUT LINE**, following the revised order in plan 04. Each report must contain: tasks completed, the exact commands run with pass counts (backend `uv run pytest`, `uv run ruff check .`, `uv run python backend/scripts/gen_check_catalogue.py --check`, and in `frontend/`: `npm run lint`, `npx tsc --noEmit`, `npm test`, `npm run build`), screenshots, every mismatch between plan and code, and anything you chose not to do. Continue through the requested impact preview, similar cases and change comparison after the demo-critical slice unless a real blocker or the deadline prevents it; report any unfinished work rather than silently dropping it.

**Decisions that are mine, not yours.** Plan 03 Task 3 defines `link_strength` (what counts as a strong link versus a hub). Implement the stated defaults, and in your report ask me to confirm or change the numbers. Do not invent a UI for editing them.

**If time is short:** finish and verify plan 04's two-case demo-critical slice first. Continue with the requested impact preview, similar cases and What changed in the plan's order. Model rewording is optional. Never cut the tests, the feature flag, or the catalogue check; report every item not completed.

Begin with plan 04 Task D1 and the feature-flag foundation in plan 01 Task 1. Recheck the actual `origin/main` implementation before following older code maps: **Ask this case already exists**, is controlled by `GLDB_CASE_ASSISTANT`, and requires a personal account. Announce each task as you start it.
