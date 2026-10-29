# Frontend

> **Visual design and wording.** The look (tokens, type, UI kit) and the plain-language glossary used on screen are in `frontend/DESIGN.md`. Nav labels on screen are Worklist, New case, Control performance and Check catalogue; the routes (`/`, `/upload`, `/effectiveness`, `/rules`) and every behaviour below are unchanged.


Build spec for the `frontend-app` agent (after `frontend-scaffold` has created the Next.js project). Contracts: [api.md](api.md) (every endpoint, error code and the TypeScript types in section 12 are binding), [data-model.md](data-model.md), [scoring-and-queue.md](scoring-and-queue.md) (what score, coverage, lane and top finding mean), [review-workflow-and-audit.md](review-workflow-and-audit.md) (verdict rules, error behaviour). Plan refs: 0.2 (UX and investigation workflow is judged), 0.3 (ranked queue, explainable, human decides), 0.4 themes 4 to 6, 5.3 (boilerplate pages are visible), 8.3 (frontend list), 8.4, 9.3 (analyst burden). Fictitious data only. Do not git commit.

## 1. Purpose

The analyst's working surface: pick the riskiest case first, see why in one line, open it, see each flag drawn on the page it came from, decide with a reason, and leave an audit trail. It must also make *silence* visible (coverage, partial score, unchecked pages) and must run offline in a **mock-data mode** so the UI can be built, demoed and tested without the backend.

## 2. Scope

| In | Plan ref |
|---|---|
| Queue page, case page (page viewer with highlight overlay, findings panel, coverage, documents and boilerplate list, entity graph, review log, verdict form, second approval, versions, rerun, add documents), upload page, effectiveness page | 8.3, 8.4, 0.4 |
| Optional last: read-only check catalogue page `/rules` | 7.2 |
| Loading, empty, error and partial states; phone width; accessibility; mock-data mode | 8.3 "test at phone width" |

| Out | Why |
|---|---|
| Any applicant-facing view | tipping-off (review-workflow-and-audit.md 4.12) |
| Login, roles, multi-tenant | out of scope (plan 2) |
| Dark theme, i18n, offline PWA | gap (section 13) |
| Editing documents or extracted values | review never mutates analysis |

## 3. Stack and conventions

Next.js (App Router) + TypeScript + Tailwind, `swr` for fetching and polling, `clsx`, `lucide-react` icons, `vitest` + `@testing-library/react` + `jsdom` (all installed by the scaffold; `package.json` and the lockfile are not edited by this agent). Pages that fetch are client components. Use the dynamic-route parameter style of the Next major the scaffold installed.

- **No `dangerouslySetInnerHTML` anywhere** (ESLint `react/no-danger: error`). `evidence`, `explanation`, `title`, `top_finding`, file names, party names and `reason` are untrusted text (architecture.md section 11): render as React text; evidence in `<code class="whitespace-pre-wrap break-words">`.
- Amounts are decimal strings. **Never `Number()` an amount.** Display formatting is string-based (`formatDecimalString("12345.67") -> "12,345.67"`); aggregation uses BigInt cents (section 9.2).
- Times: server ISO `Z` strings. Show absolute as `2026-10-27 10:02 UTC` and relative beside it; the UI wall clock is allowed here (only checks are forbidden from it).
- Env: `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`), `NEXT_PUBLIC_USE_MOCK` (`1` = mock mode, section 11). Both listed in `frontend/.env.example`.
- `localStorage` only for the reviewer name (`gldb.reviewer`); every access in try/catch (`lib/storage.ts`); the app works with it blocked.

### 3.1 Files owned

`frontend/**` (scaffold files may be edited, `package.json`/lockfile may not). Target layout under `frontend/src/`:

```
app/ layout.tsx globals.css page.tsx (queue) upload/page.tsx cases/[id]/page.tsx effectiveness/page.tsx rules/page.tsx error.tsx not-found.tsx
components/ AppShell MockBanner StateViews(Loading|Empty|ErrorState) BandChip LaneChip SeverityBadge ScoreBadge CoverageMeter
  queue/ QueueFilters QueueTable QueueCard
  case/ CaseHeader ProcessingStepper VersionSelect PageViewer RegionOverlay FindingsPanel FindingCard FlagRow CoveragePanel
        DocumentsPanel NotCheckedList EntityGraph ReviewPanel VerdictForm FlagDecisionDialog PendingApprovals ReviewLog AddDocuments
  upload/ UploadForm FileDropzone
  effectiveness/ RuleTable StackedBar
lib/ types.ts api.ts errors.ts format.ts decimal.ts regions.ts queueQuery.ts graph.ts constants.ts storage.ts
lib/mock/ index.ts handlers.ts state.ts fixtures/*.json        public/mock/pages/*.svg
```
Tests are colocated as `*.test.ts(x)`. `lib/types.ts` is the api.md section 12 block pasted verbatim (plus the `flag_decision_pending` code in `errors.ts`).

## 4. Interfaces

```ts
// lib/api.ts  (throws ApiErrorException carrying ApiError["error"]; network failure throws ApiErrorException with code "network_error")
getQueue(q: QueueQuery): Promise<Page<QueueItem>>
createCase(form: FormData): Promise<CreateCaseResponse>
getCase(id: string, version?: number): Promise<CaseDetail>
getVersions(id: string): Promise<{ versions: { version: number; created_at: string; score: number | null; band: Band | null; reason: "created" | "rerun" | "documents_added"; current: boolean }[] }>
pageUrl(caseId: string, fileId: string, page: number, w?: number): string          // /cases/{id}/pages/{n}?file_id=&w=
getJob(id: string): Promise<Job>;  pollJob(id: string, onUpdate: (j: Job) => void, signal: AbortSignal): Promise<Job>   // 1 s, then 3 s after 30 s (api.md section 7)
postReview(id: string, req: ReviewRequest): Promise<ReviewResponse>
rerunCase(id: string): Promise<{ case_id: string; job_id: string; version: number }>
addFiles(id: string, files: File[]): Promise<{ case_id: string; job_id: string; version: number }>
packUrl(id: string, format: "json" | "html", version?: number): string
deleteCase(id: string, reviewer: string, reason: string): Promise<void>
getEffectiveness(q?: { use_case?: UseCase; since?: string }): Promise<EffectivenessResponse>
getRules(): Promise<{ rules: RuleInfo[] }>

// lib/errors.ts
explainError(e: ApiError["error"]): { title: string; detail: string; retryable: boolean }   // table in 9.4; unknown codes -> message verbatim

// lib/regions.ts  (section 6)
regionStyle(r: Region, opts?: { minPx?: number }): { left: string; top: string; width: string; height: string } | null
clampRegion(r: Region): Region | null
pickImageWidth(containerCssPx: number, dpr: number): number     // multiple of 200 in [600, 2000]

// lib/queueQuery.ts
queueQueryToSearchParams(q: QueueQuery): URLSearchParams;  searchParamsToQueueQuery(p: URLSearchParams): QueueQuery

// lib/format.ts
formatAge(seconds: number): string            // "45 s" | "12 min" | "3 h" | "2 d"
formatUtc(iso: string): string                // "2026-10-27 10:02 UTC"
formatDecimalString(s: string): string        // string-based grouping, keeps all decimals
formatRatio(r: number): string                // 0.857 -> "86%"
formatSeconds(s: number | null): string       // 540 -> "9 min", null -> "-"

// lib/graph.ts   (section 9.2)
deriveEntityGraph(c: CaseDetail): EntityGraph
type EntityGraph = { nodes: GNode[]; edges: GEdge[]; truncated: number }
type GNode = { id: string; label: string; kind: "applicant" | "person" | "company" | "counterparty"; roles: ("owner"|"director"|"payer"|"payee"|"supplier"|"buyer")[]; flagged: boolean }
type GEdge = { id: string; source: string; target: string; kind: "owns" | "director" | "paid" | "paid_out" | "invoiced"; label: string; amountCents?: bigint; count?: number; docIds: string[]; rows: { docId: string; row: number }[]; flagged: boolean }
```

Key component props (all typed from `lib/types.ts`):

```ts
<PageViewer caseId files flags selectedFlagId onSelectFlag current={{ fileId, page }} onNavigate />
<FindingsPanel findings flags dispositions selectedFlagId onSelectFlag onDecide />       // flags numbered 1..n in finding order
<VerdictForm caseDetail viewingVersion onDone />                                          // renders only when allowed (section 8)
<EntityGraph graph onSelectEdge />                                                       // SVG + table alternative
```

## 5. Pages

Global shell: header with product name, nav (Queue, Upload, Effectiveness, Rules), a mock banner in mock mode, and a footer with the disclaimer and student caveat (`Decision support only; a human decides; not legal advice. Our mapping to MAS and STR obligations is a student reading.`).

### 5.1 Queue (`/`)

Data: `GET /cases` with the URL query as state (`searchParamsToQueueQuery`; changing a filter replaces the URL, so views are shareable and back-button safe). SWR `refreshInterval: 10000`, paused when the tab is hidden. Default sort `priority` (api.md section 4).

Controls: use case (segmented: All, KYC, AML, Loan), status (multi-select; default `processing, open, awaiting_documents`; "Show closed" toggle), lane (multi-select), band (multi-select), search `q` (debounced 300 ms; matches reference or applicant name), "Fast lane only" chip, sort select (Priority, Score, Age, Coverage, Created), page size fixed 25. Header: "`total` cases".

Row content (table at `md` and up, cards below):

| Column | Content |
|---|---|
| Rank | `(page-1)*25 + index + 1`, shown only when `sort=priority` and `order` default |
| Risk | score number, thin bar, band chip (text "High/Medium/Low", never colour alone). Partial: `≥ 45` and chip "Medium (partial)". Processing: "Analysing" with spinner and no number |
| Case | `reference` (or case id), applicant name, use-case chip, version `v2` if > 1, `file_count` files |
| Top finding | `top_finding`, 2-line clamp, full text in `title` attribute; `findings_count` and `high_count` as "4 findings, 2 high" |
| Lane | chip: `edd` "EDD review", `aml` "AML investigation", `loan` "Loan decision", `fast` "Fast lane" (tooltip: "Low risk, high coverage, no hard stops. A person still confirms.") |
| Coverage | `ran/applicable` + `formatRatio`; below 0.70 amber with "low coverage" text; "+ N pages not checked" when `coverage.pages_unchecked > 0` |
| Age | `formatAge(age_seconds + seconds since fetch)` |
| Status | status chip; `pending_second_approvals > 0` badge "2nd approval pending"; `decision` shown for closed |

Whole row/card is one link to `/cases/{case_id}` (single focusable element per row; no nested links). Sortable headers (Risk, Coverage, Age) are buttons with `aria-sort`.

States: **loading** skeleton of 6 rows (`aria-busy="true"` on the list); **empty** (no cases at all): "No cases yet. Upload a bundle." with a link to Upload; **filtered empty**: "No cases match these filters" + "Clear filters"; **error**: `ErrorState` with `explainError`, "Retry" (SWR `mutate`), and for `network_error` "Cannot reach the API at `<base url>`. Is the backend running?"; **stale refresh failure** keeps old rows and shows a small "Last refresh failed" line (`role="status"`).

### 5.2 Upload (`/upload`)

Form (all labels visible, 16 px inputs): use case (radio group with a one-line description of the document family per use case: KYC = registry-style profile, ownership chart, register, IDs; AML = statements, invoices, source-of-funds declaration; Loan = financial statements, statements, invoices, contracts), reference (max 80), applicant name (max 120), applicant UEN (max 20, hint "an invalid UEN is accepted and checked later"), files. Dropzone plus a real `<input type=file multiple accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png">` (keyboard operable; the dropzone is only an enhancement).

Client checks (advisory; the API is authoritative): 1 to 40 files (`GLDB_MAX_FILES_PER_CASE`), each at most 25 MB (`GLDB_MAX_UPLOAD_MB`), magic bytes read from the first 8 bytes (`%PDF-`, `FFD8FF`, `89504E47`); a bad file is listed with its reason and blocks submit; same name and size twice shows a warning only (duplicates are a check, not a gate). File list with name, size, remove button. Fictitious-data notice: "Use fictitious documents only in demos."

Submit: button shows "Uploading N files..." and is disabled; success redirects to `/cases/{case_id}?job={job_id}`. Errors: `413 file_too_large`, `415 unsupported_media_type`, `422 no_files|too_many_files|validation_error` mapped by `explainError`; all-or-nothing, so the form keeps its content for retry. Focus moves to an error summary (`role="alert"`) on failure.

### 5.3 Case page (`/cases/[id]`)

Query params: `v` (version), `tab` (`findings|documents|graph|review`), `flag` (selected flag id), `job`.

**Header**: reference and applicant (text), use-case chip, status chip, version select (`GET /versions`; non-current shows banner "Viewing version 1, read-only. Go to current"), lane chip, score + band + coverage (as in the queue), `top_finding`, actions: "Decide" (opens the Review tab), "Case pack" (new tab, `?format=html`; also JSON download), "Re-run analysis", "Add documents" (the last two only for `open`/`awaiting_documents` on the current version; re-run asks for confirmation: "Creates version N+1 and keeps all decisions as history; decisions do not carry over to the new version."). Delete is under a "More" menu (a confirmation dialog with reviewer, reason and the warning "Removes all data and duplicate-detection fingerprints; do not type names or ID numbers in the reason"; a case with `escalated_at` shows an extra warning).

**Banners** (stack, each `role="status"` unless noted): `partial` (section 7.3); tipping-off line; pending second approvals count; viewing an older version; `status=error` (`role="alert"`, "Analysis failed: <job.error>", button Re-run).

**Layout** `>= lg`: left column (about 58%) page viewer; right column tabs: **Findings** (default), **Documents**, **Graph** (only when `use_case` is `kyc` or `aml`, or the case has any `bs_pass_through`, `x_remitter_vs_buyer` or `x_sof_support` flag), **Review**. Below `lg`: single column with a segmented tab bar `Findings | Page | Docs | Review (| Graph)` (section 10).

**Processing state** (`status="processing"`): `ProcessingStepper` shows the 7 stages (ingest to score) from `GET /jobs/{id}` (`aria-live="polite"` text "Stage 3 of 7: extract"), elapsed seconds, per-stage status icon plus word; on `succeeded` refetch the case and replace the stepper; on `failed` show the error state with Re-run. Findings, viewer and form are not rendered until the case is `open` (show the stepper only; if the case already has partial results from an earlier version, still wait).

**Page viewer** (`PageViewer`): file selector (if more than one file) and page navigator (prev/next buttons, "Page 2 of 3", "pages with flags" chips `f1 p2 (3)`), zoom (Fit, 150%, 200%; the container scrolls, the overlay does not drift because it is percent-positioned inside the image wrapper), the image wrapper and `RegionOverlay` (maths in section 6). Selecting a flag, finding row, document row or graph edge navigates to its region's `file_id` and page and scrolls the box into view (`scrollIntoView({block:"center"})`, `behavior:"auto"` when reduced motion is requested). Image `alt`: `Page 2 of statement.pdf, 3 flagged regions` (file name as text). Loading: aspect-ratio skeleton; error: "Page image unavailable" + Retry.

**Findings panel** (`FindingsPanel`): findings in API order (rank order, `fnd_1` first). Info findings sit in a collapsed "Informational (n)" group. Each `FindingCard`: severity badge (icon + word), title, weight `+30`, "Hard stop" badge with tooltip "Not eligible for the fast lane", rule chips, document chips (click navigates to the first region), derived state "2 open, 1 confirmed", expand/collapse (`aria-expanded`), finding-level buttons "Confirm finding" and "Dismiss finding..." (open `FlagDecisionDialog` with `finding_id`). Expanded: `FlagRow`s: number badge (matches the overlay label), rule id, `explanation`, `evidence` (monospace), location text `d3 · transactions row 18 · f1 p2` (row is `row + 1`), disposition chip (`open`, `confirmed`, `dismissed`, `pending_second_approval` shown as "Needs 2nd approval"), "Show on page", checkbox for multi-select, per-flag Confirm / Dismiss. Dismissing a flag whose severity is `high` shows the notice "A second reviewer must approve this dismissal" before submit. Multi-select bar: "Confirm n" / "Dismiss n..." (a `flag_decision` with `flag_ids`, max 50; if any selected flag is high the same notice appears and the whole entry will be pending).
Empty (`findings` empty and not partial): "No flags raised. 21 of 21 checks ran." with the note "No flags is not proof of authenticity; see coverage." Empty and partial: "No flags so far; the analysis is incomplete" + the reasons.

**Coverage panel** (inside Findings tab, above the list, collapsible): "18 of 21 checks ran (86%)" and "3 of 14 pages not checked" (from `coverage.pages_unchecked`/`pages_total`; section 9.3 is only the fallback for mock fixtures that lack them); a table of `coverage.skipped` (rule, status, reason) with plain-language reason text; link to the Rules page. **Forensic wording (plan 4.5, forensics.md 8):** a forensic check that ran with zero flags is listed as "No signal found (not a guarantee of authenticity)"; `ai_provenance_hint` with zero flags reads "No AI-generator provenance hint found. This does not mean the document is authentic." The UI never uses the words "authentic", "verified" or "genuine" about a document or signature.

**Documents tab** (`DocumentsPanel` + `NotCheckedList`): all documents: type, pages `f1:1-3`, status chip, classification `bank_statement 0.97 (rules@1)`, issuer, extractor, warnings (text). Rows with probability below `CLASSIFIER_LOW_PROB = 0.70` or status `partial`/`failed` get a "low confidence"/"not fully read" badge. Row click shows the first page. **Boilerplate / not-checked list** (plan 5.3): section "Received but not checked (n)" built from `not_checked_documents` (document, type, pages, reason, "View page"); always visible when `n > 0`, with the sentence "These pages were not examined by any check."

**Graph tab** (`EntityGraph`): section 9.2.

**Review tab** (`ReviewPanel`): in order: tipping-off line; escalation clock if `escalated_at` ("Suspicion-formed clock started 2026-10-27 10:02 UTC, 2 h ago. The tool does not file an STR; compliance decides outside the tool."); `PendingApprovals`; the form (section 8) or the reason it is unavailable; `ReviewLog`.

**Closed state**: form replaced by a summary ("Closed: Escalate to AML investigation by a.tan, 2026-10-27 10:02 UTC. Reason: ..."), log kept, actions limited to Pack. **Awaiting documents**: summary plus `AddDocuments` (same validation as Upload) and Re-run.

### 5.4 Effectiveness (`/effectiveness`)

Data `GET /rules/effectiveness` (+ optional `use_case`, `since` filters). Top KPIs: cases total, cases decided, median time to verdict (`formatSeconds`). Table (`RuleTable`, desktop table, phone cards): rule (monospace) with description, stage, default severity, times fired / cases fired, a `StackedBar` of confirmed / dismissed / pending / open with a text equivalent ("5 confirmed, 1 dismissed, 0 pending, 1 open"), dismissal rate (`-` when null), median seconds to decide, ran / skipped. Sorts: times fired (default), dismissal rate, rule. "Never fired" rules are grouped in a collapsed section. A rule is marked "Noisy?" (text, not colour) when `confirmed + dismissed >= 5` and `dismissal_rate >= 0.5` (constants `NOISY_MIN_DECIDED`, `NOISY_RATE`). "Download CSV" (client-side, current sort). Always-visible caveat: "Measures reviewer agreement, not ground truth. Ground truth is in the validation report." States: loading skeleton; **empty** (`cases_decided == 0`): "No decided cases yet. Rule statistics appear after reviewers confirm or dismiss flags." (rules table still shows zeros); error as elsewhere.

### 5.5 Rules (`/rules`, optional, build last)

`GET /rules` as a filterable table (stage, severity, use case, search): rule, description, applies to, requires, tamper caught, severity, version. Mirrors `docs/check-catalogue.md`.

**Editing the catalogue.** Each check has an Edit button (the name opens the same editor): a right-hand sheet (`components/catalogue/CheckEditor.tsx`) to switch the check on or off, change its severity, and reword its name, "What it checks" and an internal note. Every save needs the reviewer's name and a reason of at least 5 characters, lands in the permanent change log (header button "Change log", also "History for this check" in the editor) and can be reset to default; there is no second approver. The list shows the effective values everywhere (effective severity with "default: Medium" in small text, custom name and description, "Customised" and "Switched off" badges, filter "Settings", count line "N of M checks, K customised, J switched off"). Calls: `PUT /rules/{rule}/override`, `DELETE /rules/{rule}/override` (JSON body), `GET /rules/changes?rule=&limit=` (`{items}` or a bare array). The editor says, from the pending change, what will happen: a switched-off check does not run and shows as not completed (coverage drops, never a clearance); a custom severity changes the points findings add and never unblocks the fast lane; wording is display only; edits apply to analyses run after saving. Name, description and note limits are 80, 400 and 600 characters. `lib/checkCatalogue.ts` has `effectiveName`, `effectiveDescription`, `effectiveSeverity`, `isCustomised`; skipped checks with reason `switched_off` read "Switched off in the check catalogue"; the analysis trail shows "Custom catalogue settings were used: N checks changed" when `provenance.catalogue` exists. In mock mode edits persist for the session (`lib/mock/state.ts`).

### 5.6 Product tour (`/welcome`, public)

A public page that explains the product before anyone signs in: hero, the five steps from upload to decision, two interactive demos (spot the edited deposit; tick findings and watch the score), coverage ("silence is not safety"), the three case types, the officer's decision step, the "?" explainer, honest limits and a closing sign-in band. Source: `src/app/welcome/` (route, `welcome.css` for the scroll reveal) and `src/components/welcome/`.

- **Access.** `/welcome` is in `PUBLIC_PATHS` (`src/lib/auth/gate.ts`). **Behaviour change:** an unauthenticated visit to `/` now redirects (303) to `/welcome` instead of `/login`; every other protected page still redirects to `/login?next=...`, `/api/*` still answers 401, and a misconfigured production server still answers 503 on every path, `/welcome` included. A signed-in user can open `/welcome`; its buttons then read "Open the worklist" (to `/`) instead of "Staff sign-in" (to `/login`). The page reads the session cookie on the server only to pick that label and grants nothing. `/login` links back with "How it works".
- **Layout.** `AppShell` renders `/welcome` bare (skip link only); the page brings its own sticky top bar, `<main id="main">` and footer, so there is no staff nav and no logout.
- **No calls, no externals.** No API request, no remote font, image or script; the CSP is unchanged. All sample data is fictitious and lives in `src/components/welcome/demo.ts`.
- **Real arithmetic.** The statement demo recomputes every running balance from integer cents (`checkRunningBalance`) and the calculator uses the documented defaults (High 30, Medium 15, Low 5, For information 0; each finding once; cap 100; Low under 30, Medium 30 to 59, High 60 and over). Nothing is pre-baked; `demo.test.ts` covers both.
- **Motion.** Scroll reveal is pure CSS (`animation-timeline: view()`), applied only where supported and when reduced motion is not requested; without it everything is simply visible. Client islands: the two demos and the score explainer; the rest is server-rendered.

## 6. Highlight overlay maths

Inputs: `Region {file_id, page, x, y, w, h}` as **fractions of the page, origin top-left** (data-model.md section 1); the page image is the whole page rendered by `GET /cases/{id}/pages/{n}`.

Structure: an image wrapper `div.relative` whose width is the display width and whose height follows the image: `aspect-ratio: W / H`, with `W/H` taken from `PageInfo.width_pt/height_pt` before load and from `img.naturalWidth/naturalHeight` after load. The `<img>` is `w-full h-full block`, `draggable=false`. Each region is an absolutely positioned child of the wrapper:

```
left   = x * 100 %          top    = y * 100 %
width  = w * 100 %          height = h * 100 %          (toFixed(3), as strings with "%")
```
Percent of the wrapper means the box tracks every display size and zoom with no JavaScript measuring and no per-resize recompute. Never multiply by the PNG pixel width.

`clampRegion`: reject (return `null`, `console.warn` once per flag id) if any of `x,y,w,h` is not finite, `w <= 0` or `h <= 0`, `x < 0` or `y < 0` beyond -0.0001, or `x >= 1` or `y >= 1`; otherwise clamp `x,y` to `[0,1]` and `w = min(w, 1 - x)`, `h = min(h, 1 - y)` (tolerance `1.0001` per data-model.md). Regions with a different `file_id`/`page` than the one shown are not drawn.

Worked example (data-model.md section 7 flag): `{page 2, x .62, y .41, w .12, h .015}`, A4 page (`595.28 x 841.89`, aspect 1.4142).
- Desktop, wrapper 700 px wide, 989.9 px tall: box at left 434.0, top 405.9, 84.0 x 14.8 px.
- Phone 360 px viewport, wrapper 328 px wide (16 px gutters), 463.9 px tall: left 203.4, top 190.2, 39.4 x 7.0 px. Same CSS, no recomputation.

Visual rules: the drawn box is `outline: 2px solid <severity colour>; outline-offset: 2px; background: <colour at 12% alpha>` so the geometry stays exact (do not pad the box itself). Severity is also encoded by line style (high solid, medium dashed 6/3, low dotted, info dash-dot) and by a number label, so it never relies on colour. Minimum touch target: each box is a `<button>` with `::before { content:""; position:absolute; inset:-10px }` so a 7 px tall box is still tappable (at least 24 px). The number chip (flag number, e.g. `3`) sits above the top-left corner; if `y < 0.03` it sits below the box. `extra_regions` (related evidence on the same file and page) draw dashed grey with the region `label`; extra regions on another page are listed in the flag row as "Also on f2 p3" links. Stacking order: info, low, medium, high (high on top); the selected flag gets `z-index` above all and a 2 px white inner ring. Selecting pulses once (`prefers-reduced-motion: reduce` disables it). `pointer-events: auto` only on the boxes.

`pickImageWidth(containerCssPx, dpr)` = `clamp(ceil(containerCssPx * dpr / 200) * 200, 600, 2000)` (multiples of 200 maximise the server's render cache; api.md allows 200 to 2000). Rotation: regions are in the same rendered orientation as `page.rect` (post-rotation), so no extra transform; mismatch would be a backend bug (section 13).

Unit tests (section 12) pin this table: `(.62,.41,.12,.015)` -> `left 62.000% top 41.000% width 12.000% height 1.500%`; `(0,0,1,1)` full page; `(.95,.5,.1,.1)` -> width clamped to `5.000%`; `(.2,.2,0,.1)` -> `null`; `(1,.2,.1,.1)` -> `null`; `(NaN,...)` -> `null`.

## 7. States and partial score

### 7.1 State matrix

| Page / area | Loading | Empty | Error | Partial |
|---|---|---|---|---|
| Queue | 6-row skeleton, `aria-busy` | no cases / filtered empty (5.1) | `ErrorState` + retry; keep stale rows on refresh failure | row shows `≥ n` and "(partial)" chip |
| Case | header + viewer skeletons | no flags (5.3) | `404` -> not-found page "No such case or version"; `network_error`/5xx -> `ErrorState` | banner + `≥` score |
| Page image | aspect skeleton | n/a | "Page image unavailable" + Retry | pages with `PageInfo.error` show "This page could not be read" |
| Findings | skeleton cards | "No flags raised..." | inherits case | note on empty |
| Review log | skeleton | "No decisions yet" | inline error | n/a |
| Effectiveness | skeleton | no decided cases | `ErrorState` | n/a |
| Upload | n/a | n/a | error summary, form kept | n/a |

Skeletons never block interaction with already-loaded areas. Spinners are accompanied by text.

### 7.2 Error display

Every failure uses `explainError` (table 9.4). Show `title` (bold), `detail`, and, for 5xx, the `X-Request-Id` when present ("Reference: ..."). Retry only when `retryable`.

### 7.3 Partial score

When `score.partial` (or `partial` in a queue row): the score renders as `≥ 45`, the band chip reads "Medium (partial)", and the case page banner says: "Partial analysis. Some checks or pages did not run, so the real score may be higher. A partial case is never fast lane." followed by `partial_reasons` humanised (`stage_failed:rules` -> "The rules stage failed", `check_error:bs_totals` -> "Check bs_totals errored", `document_failed:d4` -> "Document d4 could not be read", `budget_exceeded: ...` -> verbatim) and a link to the Coverage panel. A partial score of `≥ 0` is never labelled "clean" or "low risk" in any wording. Unknown reason strings are shown verbatim.

## 8. Verdict form and review UI

Rendered only when `status="open"`, `allowed_verdicts` is non-empty and the viewed version is current. Otherwise show why ("Analysis running", "Waiting for documents", "Closed", "Viewing an old version").

Fields:
- **Reviewer** text (2 to 64, trimmed), prefilled from `gldb.reviewer`, saved on success.
- **Verdict** radio group of `allowed_verdicts` with `VERDICT_LABELS` (review-workflow-and-audit.md section 3) and a one-line consequence under each (`approve`: "Closes the case; every high flag must be decided first"; `request_documents`: "Case waits for more documents; nothing is sent to the applicant"; `refer_edd`: "Closes the case for the EDD team"; `escalate_aml`: "Closes the case and records the suspicion-formed time now; compliance decides on an STR outside this tool"; `reject_or_escalate`: "Closes the case; the credit decision stays with the bank"). The verdict matching the lane (`edd`->`refer_edd`, `aml`->`escalate_aml`, `loan`->`reject_or_escalate`, `fast`->`approve`) is marked "Suggested by lane" but **never preselected**: no default choice.
- **Reason** textarea (labelled "Reason (required, at least 5 characters)"), live counter `n/1000`, trimmed length validated on blur and submit; `aria-describedby` points to the counter and error.
- Helper text under the form: the student caveat and, for `request_documents`, "Ask for documents in neutral terms. Do not say which checks fired."
- Submit button label is the chosen verdict label (no generic "Submit"). A confirmation dialog (native `<dialog>`) appears for: `escalate_aml` (shows the timestamp that will be recorded, "The tool does not file an STR"), and `approve` when `score.partial`, coverage ratio below 0.80, `band != "low"` or any `not_checked_documents` ("Approving with incomplete or elevated-risk analysis: <list>. Continue?").
- Client validation mirrors the server (reviewer 2 to 64, reason at least 5 after trim); the server remains authoritative.

Flag decisions use `FlagDecisionDialog` (decision, reviewer, reason; same validation). Second approval: each pending entry is a card ("a.tan dismissed 2 flags: <rule ids>. Reason: ... Needs a different reviewer") with its own reviewer, reason and Approve / Decline; submitting with the same name (casefold, trimmed) is blocked client-side with "A different reviewer must countersign" (the API's `409` still handled). Review log: oldest first, columns reviewer, kind and decision, status chip, reason (text), version tag, absolute UTC time; entries from older versions are visually grouped "Version 1".

After success: refetch the case (SWR `mutate`), toast-free confirmation line (`role="status"`) "Recorded as r4"; closing verdicts re-render the closed state. Failures: `high_flags_open` lists the blocking flags as links that select them; `stale_version` shows "This case changed (now version N). Reload" and reloads on click; `flag_decision_pending` explains the pending approval; every field error focuses the first invalid control.

## 9. Supporting algorithms

### 9.1 Flag numbering and disposition summary

Number flags `1..n` in the order: findings (API order), then each finding's `flag_ids` order. The overlay label, the flag row and the keyboard focus order all use that number. A finding's derived state is the multiset of its flags' dispositions. Flags missing from every finding (should not happen) are appended under "Other flags".

### 9.2 Entity graph (client-side, plan 0.4 theme 4)

`deriveEntityGraph(c)` builds a small in-case graph from `CaseDetail.documents` only (no endpoint exists; Contract request for a server graph). Normalisation `norm(name)`: Unicode NFKC, lower-case, `&` -> `and`, remove `. , ' " ( )`, drop tokens `pte ltd limited llp inc co`, sort the remaining tokens, join with a space. Matching is exact on `norm`; this is *not* the backend's fuzzy matcher and the tab says so ("Names matched exactly after normalising; the backend matcher may differ").

Nodes: `applicant` (applicant name, else `company_profile.company_name`, else "Applicant"); every director (`company_profile.directors.director_name`); every holder (`ownership_chart.holders`, `shareholder_register.shareholders`, `company_profile.shareholders`; kind `company` if `holder_type=company` or the name had a dropped company token, else `person`); every counterparty from `bank_statement.transactions` rows via `annotations.counterparty.label` (rows without it are counted but not drawn); invoice `supplier_name` / `buyer_name`. Nodes merge on `norm`. Edges: `owns` holder -> applicant (label = chart percent if present, else register percent, as `"60.00%"`); `director` person -> applicant; `paid` counterparty -> applicant (credits, sum, count); `paid_out` applicant -> counterparty (debits); `invoiced` supplier -> buyer (sum of `total`). Sums use BigInt cents from the decimal strings (`toCents("12345.67") = 1234567n`; rejects strings that are not `-?\d+(\.\d{1,2})?`; formats back with `fmtCents`). Each edge keeps `docIds` and `rows` (`{docId, row}`).
Flagged: an edge is flagged when any flag has `table=="transactions"`, a `row` in the edge's `rows` for the same document; a node is flagged when it touches a flagged edge, or when a flag of rule `x_directors`, `x_ownership_vs_registry`, `kyc_shareholdings`, `x_entity` or `bs_pass_through` contains the node's display name (case-insensitive substring) in `evidence` or `explanation` (heuristic, labelled as such). A counterparty that is both payer and payee gets both roles and the badge "in and out".
Limits: at most 8 counterparties per side by amount (the rest collapsed into one "+n more" node, `truncated = n`), 40 nodes total.

Rendering: SVG `viewBox="0 0 1000 600"`, `width:100%`. Row of people (owners and directors) at y 90; applicant centre (500, 300); payers column x 120, payees column x 880, evenly spaced between y 120 and 520; edge stroke width `1.5 + min(4.5, log10(1 + cents/100) / 1.5)`; flagged edges red with a dash pattern and a "flagged" text label; labels truncated to 24 chars with `<title>` for the full name. Selecting an edge selects its first flagged row's flag (navigates the viewer). **Alternative text view** (a real `<table>`: from, to, kind, amount, count, flagged) is always rendered and is the default below `sm`; a toggle switches views. `role="img"` on the SVG with `aria-label` "Entity graph, N parties, M relationships, K flagged" and `aria-describedby` pointing at the table.

### 9.3 Unchecked page counts (fallback only)

The API sends `coverage.pages_total` and `coverage.pages_unchecked` (R0); the queue row and case page use them. This client computation is only for mock fixtures that omit them: `unchecked = pages in documents with status received_not_checked|failed + pages with PageInfo.error + pages whose classification probability < 0.70 or label boilerplate|other`; `total = sum(files[*].pages.length)`. Show "`unchecked` of `total` pages not checked". Mirrors `page_counts` in scoring-and-queue.md section 4.6.

### 9.4 Error map (`explainError`)

| `code` | Title | Detail |
|---|---|---|
| `network_error` | Cannot reach the API | "Is the backend running at <base url>?" (retryable) |
| `bad_request`, `validation_error` | Check the form | field messages from `details.errors` |
| `not_found` | Not found | by `details.kind` |
| `case_processing` | Analysis still running | "Try again when it finishes." |
| `case_not_open` | Case is not open | "Decisions are only possible while a case is open." |
| `high_flags_open` | High flags still open | "Confirm or dismiss every high flag (a pending dismissal does not count)." + flag links |
| `second_approver_must_differ` | A different reviewer is needed | |
| `entry_not_pending` | Nothing to approve | "This entry was already decided." |
| `flag_decision_pending` | Decision pending | "A dismissal of this flag is waiting for a second reviewer." |
| `stale_version` | Case changed | "Reload to see version N." |
| `file_too_large` | File too large | max 25 MB |
| `unsupported_media_type` | Unsupported file | "Only PDF, JPEG and PNG." |
| `too_many_files`, `no_files` | Wrong number of files | 1 to 40 |
| `reason_required` | Reason required | "Enter at least 5 characters." |
| `reviewer_required` | Reviewer name required | "2 to 64 characters." |
| `verdict_not_allowed` | Verdict not allowed here | list `details.allowed` |
| `internal_error`, 5xx | Something went wrong | request reference; retryable |
| unknown | `error.message` verbatim | |

## 10. Phone width, responsiveness, accessibility

**Breakpoints** (Tailwind): base 360 to 639, `sm` 640, `md` 768, `lg` 1024, `xl` 1280. Verify at 360, 390, 768 and 1280 px wide.

Phone rules (below `md` unless stated):
- Page gutter 16 px; no horizontal page scroll at 360 px (only the zoomed page viewer and wide tables inside `overflow-x-auto` regions scroll, each labelled and keyboard focusable).
- Queue: cards, not a table. Each card: score + band + lane on the first line, applicant and reference, 2-line top finding, coverage and age on the last line. Filters collapse behind a "Filters (n)" button opening a bottom sheet (`<dialog>`).
- Case page below `lg`: tab bar sticky under the header (`Findings | Page | Docs | Review`, plus `Graph`), the viewer is its own tab (full width, zoom buttons, overlay unchanged); selecting a flag in Findings offers "View on page" and switches tab. "Decide" is a sticky bottom action bar button that opens the Review tab at the form. Findings are accordions.
- Verdict form full width; radio options at least 48 px tall; the confirmation dialogs are full-screen sheets.
- Graph defaults to the table view below `sm`.
- Upload: single column; the dropzone is a large button; the file list rows wrap.
- Touch targets at least 44 x 44 CSS px (overlay hit area via `::before` inset, section 6); inputs 16 px font minimum (prevents iOS zoom); no hover-only information (tooltips duplicated as visible text or `aria-describedby`).

**Accessibility** (target WCAG 2.2 AA, manual checklist since no axe dependency):
- Landmarks: `header`, `nav`, `main`, `footer`; one `h1` per page; a "Skip to main content" link and, on the case page, "Skip to findings".
- Queue table: `<caption class="sr-only">`, header cells with `scope`, sortable headers `aria-sort`; one link per row.
- Colour is never the only signal: severity and band always carry text; lane chips carry text; status chips carry text; dispositions carry text.
- Contrast at least 4.5:1 for text and 3:1 for graphical objects: tokens in `globals.css` (`--sev-high #b42318`, `--sev-medium #b54708`, `--sev-low #475467`, `--sev-info #175cd3`, text `#101828` on `#ffffff`; verify each with a contrast calculator when changing).
- Focus: visible 2 px ring on every interactive element; focus order follows reading order; overlay boxes are tabbable in flag-number order and have `aria-label="Flag 3, bs_running_balance, high severity, page 2"`, `aria-pressed` for the selected one.
- Disclosure widgets use `aria-expanded` and `aria-controls`; tabs use the ARIA tabs pattern (arrow keys); dialogs are native `<dialog>` with `showModal()` (focus trap, Escape), focus returns to the opener.
- Live regions: job progress `role="status" aria-live="polite"`; form error summaries and failed loads `role="alert"`; polling updates in the queue do not steal focus or announce every refresh.
- Forms: every control has a visible `<label>`; required fields say so in text; errors are linked with `aria-describedby` and `aria-invalid`.
- Keyboard: everything works without a mouse. Shortcuts (disabled while typing in a field, listed behind a "Keyboard help" dialog): `j`/`k` next/previous flag, `[`/`]` previous/next page, `Esc` clear selection.
- Motion: respect `prefers-reduced-motion` (no pulse, no smooth scroll). Text resizes to 200% without loss; layout reflows at 320 px.
- Images: page image has meaningful `alt`; decorative icons `aria-hidden`; the graph has the table alternative (9.2).

## 11. Mock-data mode

`NEXT_PUBLIC_USE_MOCK=1 npm run dev` serves the whole UI with no backend. `lib/api.ts` delegates to `lib/mock/handlers.ts` when the flag is set (checked once at module load); the production bundle without the flag contains no fixture imports used at runtime (dynamic `import()` of the mock module). A persistent banner "MOCK DATA. Not connected to the API. Everything here is fictitious." (`role="note"`) shows on every page.

Fixtures (`lib/mock/fixtures/*.json`) are typed against `lib/types.ts` (the build fails on a mismatch) and **must be consistent with the scoring formulae** in scoring-and-queue.md (score = min(100, sum of finding weights 30/15/5/0), bands 30/60, coverage = ran/applicable). Minimum set, 12 queue rows:

| # | Case | Shape |
|---|---|---|
| 1 | Loan, fraud variant | findings: `fs_arith` high (30), `x_revenue_vs_credits` medium (15), `x_invoice_vs_receipts` pattern medium (15, 4 flags), `x_invoice_vs_contract` medium (15) -> 75, band high, coverage 18/21, lane `loan`, 14 files; regions on 3 pages |
| 2 | KYC, fraud variant | `x_ownership_vs_registry` high (30, seen on 3 documents), `x_directors` high (30), `x_declared_vs_activity` medium (15) -> 75, lane `edd`; includes a high flag already `pending_second_approval` |
| 3 | AML, fraud variant | `x_sof_support` high, `x_remitter_vs_buyer` high, `bs_pass_through` medium -> 75, lane `aml`; entity graph data (counterparties both in and out) |
| 4 | Loan, clean control | no findings, 21/21, score 0, lane `fast`, top line "No flags raised; 21 of 21 checks ran" |
| 5 | Loan, partial | 2 findings (15 + 5), `partial` with reasons (`stage_failed:extract`, `document_failed:d4`), coverage 11/21, 3 unchecked pages, shown `≥ 20` |
| 6 | KYC, processing | `status=processing`, no score; the job fixture steps through stages |
| 7 | Loan, awaiting documents | verdict `request_documents` recorded |
| 8 | AML, closed | `escalate_aml` with `escalated_at` |
| 9 | Loan, hard stop | one medium `pdf_editor_software` (score 15) -> lane `loan`, not fast |
| 10 | Loan, error | `status=error`, job failed |
| 11 | KYC, medium | 45 points, coverage 0.6 (amber) |
| 12 | Loan, version 2 | `version: 2` with `/versions` returning 2 entries |

Page images: `public/mock/pages/*.svg` (at least 3, fictitious bank statement, invoice and ownership chart drawn with text rows); `pageUrl` returns these in mock mode; fixture region coordinates must land on the matching row (checked manually and by a test that asserts each fixture region is valid under `clampRegion`).

Stateful behaviour (in-memory, resets on reload): `POST /cases` creates a case that advances through the 7 stages over about 6 s then becomes `open` with a canned finding set chosen by use case; `postReview` implements, with the real error codes and bodies, exactly: reviewer and reason validation, `case_not_open`, `stale_version`, `verdict_not_allowed` by use case, dismissing a high flag -> `pending_second_approval`, `second_approver_must_differ`, `high_flags_open` on approve, `escalate_aml` setting `escalated_at`. It does not implement audit, versions beyond the fixture, pack export (opens a static sample HTML) or retention.

## 12. Test plan

Vitest + Testing Library; run with `npm test` (the scaffold's script) and `npm run lint`, `npx tsc --noEmit`, `npm run build` (all must pass). No network; mock handlers are used for component tests.

| # | Case | Expect |
|---|---|---|
| 1 | `regionStyle` table in section 6 (six inputs incl. clamp and null cases) | exact strings / `null` |
| 2 | Overlay renders for the section 6 flag in a 700 px and a 328 px wrapper (set wrapper width) | CSS percentages identical in both; computed px proportional |
| 3 | `pickImageWidth(328, 3)` / `(700, 1)` / `(2000, 2)` | 1000 / 800 / 2000 |
| 4 | `queueQueryToSearchParams` <-> `searchParamsToQueueQuery` round trip with arrays, `partial=false`, `min_score=0` | lossless; default status not serialised when untouched |
| 5 | Queue with 12 mock rows | priority order preserved; rank column only for priority sort; fast-lane chip text present; processing row has no score and says "Analysing"; partial row shows `≥` and "(partial)"; each row has exactly one link |
| 6 | Queue states: pending promise, `[]` items, filtered empty, rejected `network_error`, refresh failure with stale rows | skeleton with `aria-busy`, empty text, "Clear filters", `ErrorState` with retry, old rows + status line |
| 7 | **Clean case page** (fixture 4) | "No flags raised. 21 of 21 checks ran.", band Low, Fast lane chip, Decide enabled, `approve` listed first, no partial banner |
| 8 | Partial case page (fixture 5) | banner text and humanised reasons, `≥ 20`, "Low (partial)"-style chip, no Fast lane chip, "3 of N pages not checked" |
| 9 | Selecting a flag | viewer navigates to its page, the box gets `aria-pressed="true"`, URL gets `flag=`; `j`/`k` move selection; typing in the reason field does not trigger shortcuts |
| 10 | Untrusted text: flag with `evidence="<script>alert(1)</script>"`, applicant name `"<img src=x onerror=alert(1)>"`, reason `"</td>"` | rendered as literal text; `document.querySelector("script")` count unchanged; no `img[src=x]` |
| 11 | Verdict form validation: reason `"abcd"`, `"    "`, 5 chars; reviewer `"a"` | errors on blur/submit, no request for invalid; valid sends the exact `ReviewRequest` JSON with `case_version` |
| 12 | Verdict options follow `allowed_verdicts`; no option preselected; lane-suggested badge present | `refer_edd` absent for loan |
| 13 | Dismiss a high flag | notice "A second reviewer must approve this dismissal"; after mock response the flag shows "Needs 2nd approval"; pending card appears |
| 14 | Countersign with same name in different case/whitespace | blocked client-side; with a different name resolves, flag shows dismissed |
| 15 | `approve` with an open high flag (mock returns 409) | error lists blocking flags as links; focus on the alert |
| 16 | `escalate_aml` | confirmation dialog shows the timestamp and the "does not file an STR" sentence; after success the closed summary and clock appear |
| 17 | Closed, awaiting_documents, processing, error, old-version cases | no verdict form; each shows its explanation; awaiting shows `AddDocuments` |
| 18 | `explainError` for every code in 9.4 and an unknown code | titles as listed; unknown returns `message` verbatim |
| 19 | `formatDecimalString("1234567.8")`, `"-0.50"`, `"0.00"`; `toCents`/`fmtCents` round trip; `toCents("12.345")` | `1,234,567.8`, `-0.50`, `0.00`; lossless; throws |
| 20 | `deriveEntityGraph` on fixture 3 | node merge by `norm` ("Wong Jessica" vs "Jessica Wong" merge because tokens are sorted; "Acme Trading Pte. Ltd." vs "ACME TRADING" merge); sums exact; edge flagged when a flag's `transactions` row matches; 8-per-side cap yields `truncated`; table view lists every edge |
| 21 | Upload: 41 files; a 26 MB file; a `.pdf` whose bytes are text; duplicate name+size | blocked with specific messages; blocked; blocked; warning only. Valid submit builds `FormData` with `use_case`, repeated `files`, optional fields |
| 22 | Effectiveness: empty (`cases_decided=0`), rule with 5 decided and 0.5 dismissal, never-fired group, CSV | empty text; "Noisy?" text; collapsed group; CSV rows match table |
| 23 | Mock mode: banner present on every page; upload creates a case that reaches `open`; review rules from section 11 return real codes | passes |
| 24 | Accessibility assertions (Testing Library roles): queue `table` has `caption`, sortable headers expose `aria-sort`; tabs have role `tab`/`tabpanel`; dialogs open with focus inside and return focus; overlay boxes are `button`s with `aria-label`; error summaries have role `alert` | pass |
| 25 | Manual phone checklist at 360 and 390 px (DevTools): queue cards, case tabs, viewer overlay aligned on tap, verdict sheet, upload, no horizontal scroll, 44 px targets, graph defaults to table | recorded in the PR description |
| 26 | Fixture validity: every fixture region passes `clampRegion`; every fixture satisfies the score formula (`min(100, sum weights)`, band thresholds) | pass |
| 27 | **Honest quirk**: a flag whose region is `null` (document-level check) | listed in the findings panel with "No location on a page"; clicking it navigates to the document's first page without an overlay |

## 13. Acceptance criteria

1. `npm run lint`, `npx tsc --noEmit`, `npm test`, `npm run build` pass; `NEXT_PUBLIC_USE_MOCK=1 npm run dev` shows the queue, a case with highlighted regions, the upload flow, and the effectiveness page without a backend.
2. Against the real API (backend running): upload a generated bundle, watch the stepper, open the case, click a flag and see the box on the right row, record a verdict with a reason, see the case close; a high-flag dismissal needs a second reviewer.
3. All api.md error codes render a specific message; unknown codes render the server message.
4. Partial and low-coverage cases are visibly different from clean ones in the queue and on the case page; a partial score is never described as safe.
5. The three demo-bundle expectations (plan 0.5) are readable on screen: lane label, top finding, grouped findings.
6. The phone checklist (test 25) passes at 360 px; keyboard-only walkthrough of queue -> case -> verdict passes.
7. No `dangerouslySetInnerHTML`, no `Number()` on amounts, no secrets in the bundle.

## 14. Known gaps and honest limits

- Light theme only; no i18n; English only.
- Highlights are box-level (first line of a value, `Cell` has one box); wrapped values and word-level evidence are not marked. Regions assume the rendered orientation of the page.
- The entity graph is derived in the browser with exact normalised-name matching; it can disagree with the backend's fuzzy matcher and it only knows what extraction found. It is a small in-case view, not a cross-case network (plan 0.4 theme 4 is minimal).
- Page counts come from the API's `Coverage.pages_*`; mock fixtures may fall back to the client computation (9.3).
- No authentication UI: reviewer is a typed name remembered in the browser.
- Polling, not push; a long-idle tab shows stale data until the next refresh.
- Accessibility is checked by role-based tests and a manual checklist, not by an automated audit tool (none in the dependency list).
- Mock mode re-implements a subset of the review rules for demos; it is not a second source of truth.
- Charts are plain bars with text equivalents; no charting library.
- Pack and effectiveness exports are server HTML/JSON and client CSV only; no PDF.

## Contract requests

**W3 closure (T-integration-kyc-08):** every request in this section has a recorded outcome in tasks.md sections 4.2 and 4.2a (accepted, decided at R0, or not built and listed under Known gaps above); none is open.

**R0 outcomes:** server-side graph is stretch (client derivation stays the default); page-level coverage accepted (`Coverage.pages_*`, api.md 12); `flag_decision_pending` added to api.md; `NEXT_PUBLIC_USE_MOCK` added to architecture.md 8 and `dev:mock` is a frontend-scaffold task; API fixtures accepted (`backend/tests/_out/` is gitignored); `/health` settings echo accepted (`Health` type in api.md 12). After wave 1 nobody else owns the UI: integration-loan (W2) and integration-kyc (W3) may make minimal fixes in `frontend/src/**`.

- **Server-side entity graph** (rules-kyc `graph.py`, api-backend): add `graph: {nodes: [{id, label, kind, roles}], edges: [{id, source, target, kind, label, amount?, count?, document_ids, rows, flagged}], truncated}` to `CaseDetail` (or `GET /cases/{id}/graph`) so the UI uses the same name matching as the rules; the client derivation in 9.2 stays as fallback.
- **Page-level coverage**: `Coverage.pages_total` / `pages_unchecked` (also requested in scoring-and-queue.md) so the queue can show page silence.
- **Error code** `flag_decision_pending` (review-workflow-and-audit.md): add to api.md section 1 and the TypeScript doc.
- **Frontend env**: document `NEXT_PUBLIC_USE_MOCK` in architecture.md section 8 (frontend-only variable), and have `frontend-scaffold` add an npm script `dev:mock` (`NEXT_PUBLIC_USE_MOCK=1 next dev`) if it has not already; this agent cannot edit `package.json`.
- **API fixtures for contract tests**: api-backend tests write JSON responses (queue page, one case, review response, effectiveness) to `backend/tests/_out/api_fixtures/*.json`; the frontend test `fixtures.contract.test.ts` validates them against `types.ts` when the folder exists and skips otherwise.
- **Settings echo** (optional): `GET /health` includes `classifier_min_prob`, `max_upload_mb`, `max_files_per_case`, `require_second_approver` so the UI need not hard-code 0.70 / 25 / 40 / true.
- **Explainers** (`GET /explain/method`, optional `documents[].classification.reasons` / `rationale`): the "?" explainers (`frontend/src/components/explain/`) and the "How this was analysed" sheet take the scoring method (weights, cap, bands, fast-lane list, hard-stop and AML-routing rules, lanes, ruleset version) from `GET /explain/method`, fetched the first time one opens and cached for the session; the mock serves the same shape. A 404 or network failure shows "The method details are not available right now" and the case's own numbers still appear. `rationale` is model-written text: always labelled as generated, rendered as plain text, never used for any figure.
