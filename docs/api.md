# HTTP API

Contract for `backend/app/api/*.py` (owner: api-backend) and the frontend client (`frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`). Models are defined in [data-model.md](data-model.md); layout and rules in [architecture.md](architecture.md). Plan refs: 8.3 (endpoints), 8.4 (queue), 7.2 and 7.3 (review), 6 (cost), 0.3 (embedded in existing processes: API first).

Base URL `http://localhost:8000`. JSON UTF-8 except uploads (multipart) and page images (PNG). No authentication (multi-tenant security is out of scope, plan 2); reviewer identity is a name sent in the body. The API is analyst-facing only: the applicant never sees flags (tipping-off, plan 7.2 [confirm]).

## 1. Conventions

- Datetimes ISO 8601 UTC `...Z`; dates `YYYY-MM-DD`; amounts are decimal strings (data-model.md section 2).
- Enums are lowercase snake_case strings, identical to the Python `Literal`s.
- CORS allows `GLDB_CORS_ORIGINS`.
- **Errors.** Every non-2xx response uses one body (FastAPI's default 422 body is replaced by an exception handler):

```json
{"error": {"code": "reason_required", "message": "A reason of at least 5 characters is required.", "details": {"field": "reason"}}}
```

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | malformed multipart or JSON |
| 404 | `not_found` | case, job, file, document, page or flag id unknown (`details.kind` says which) |
| 404 | `unknown_rule` | `/rules/{rule}/override` names a rule id that no registered check has (`details.rule`) |
| 404 | `no_override` | `DELETE /rules/{rule}/override` on a check that has no override |
| 409 | `case_processing` | action needs a finished analysis |
| 409 | `case_not_open` | review action on a case whose status is not `open` |
| 409 | `high_flags_open` | `approve` while a high flag has no effective disposition |
| 409 | `second_approver_must_differ` | countersigner equals the first reviewer (case-insensitive, trimmed) |
| 409 | `entry_not_pending` | countersigning an entry that is not `pending_second_approval` |
| 409 | `stale_version` | `case_version` in the request differs from the case's current version |
| 409 | `flag_decision_pending` | a `flag_decision` targets a flag whose dismissal is waiting for a second approver (`details.flag_ids`); approve or decline it first |
| 413 | `file_too_large` | a file exceeds `GLDB_MAX_UPLOAD_MB` |
| 415 | `unsupported_media_type` | not PDF/JPEG/PNG by magic bytes |
| 422 | `validation_error` | schema or enum violation (`details.errors` lists fields) |
| 422 | `reason_required` | missing or shorter than 5 characters after trimming (catalogue writes: at most 500) |
| 422 | `reviewer_required` | missing or outside 2-64 characters (catalogue writes: 2-80) |
| 422 | `no_change` | `PUT /rules/{rule}/override` would leave the check exactly as it is (the values already in effect) |
| 422 | `verdict_not_allowed` | verdict not allowed for the use case (section 6) |
| 422 | `too_many_files` / `no_files` | more than `GLDB_MAX_FILES_PER_CASE`, or zero |
| 500 | `internal_error` | unexpected; generic message, details logged with a correlation id (`X-Request-Id` header) |
| 501 | `not_implemented` | foundation stubs only |

- **Pagination** (list endpoints): query `page` (1-based, default 1), `page_size` (default 25, max 100). Response `{"items": [...], "total": n, "page": p, "page_size": s, "has_next": bool}`.
- Responses are never cached except page images.

## 2. Endpoint summary

| Method | Path | Purpose | Plan |
|---|---|---|---|
| GET | `/health` | liveness, version, llm backend | |
| POST | `/cases` | upload + create case, start analysis job | 8.3 |
| GET | `/cases` | ranked queue | 8.3, 8.4 |
| GET | `/cases/{id}` | full case view | 8.3 |
| GET | `/cases/{id}/pages/{n}` | page image (PNG) | 8.3 |
| POST | `/cases/{id}/review` | verdict, flag decision, second approval | 8.3, 7.3 |
| GET | `/cases/{id}/review` | review log | 7.2 |
| GET | `/cases/{id}/pack` | evidence pack, JSON or readable HTML | 8.3 |
| GET | `/rules/effectiveness` | rule effectiveness | 8.3, 0.4 theme 6 |
| GET | `/jobs/{job_id}` | poll background processing | D4 |
| POST | `/cases/{id}/files` | add documents (new version, re-analysis) | 7.3 |
| POST | `/cases/{id}/rerun` | re-run analysis on the same inputs (new version) | 7.2 reproducibility |
| GET | `/cases/{id}/versions` | version list | 0.4 theme 2 |
| GET | `/cases/{id}/files/{file_id}` | download original file | |
| GET | `/cases/{id}/documents/{doc_id}/words` | text-layer words of a document | |
| GET | `/cases/{id}/audit` | audit log for the case | 7.2 |
| GET | `/rules` | check catalogue (registered checks as JSON, code defaults plus the effective settings) | 7.2 |
| PUT | `/rules/{rule}/override` | switch a check on or off, change its severity, edit its name, description and note (reviewer and reason required) | 7.2 |
| DELETE | `/rules/{rule}/override` | reset a check to its code defaults (reviewer and reason required) | 7.2 |
| GET | `/rules/changes` | permanent change log of the check catalogue, newest first | 7.2 |
| GET | `/explain/method` | scoring and routing method, read from the engine's constants | explainability |
| DELETE | `/cases/{id}` | delete a case and its data | 7.2 |
| POST | `/admin/retention/purge` | purge cases older than retention | 7.2 |

Routers: `health.py`, `cases.py` (create, get, delete, rerun, files, versions, documents), `queue.py` (list), `jobs.py`, `files.py` (pages, file download), `review.py` (+audit), `pack.py`, `effectiveness.py`, `rules.py` (catalogue, overrides and change log), `explain.py`.

## 3. Create case: `POST /cases`

`multipart/form-data`:

| Part | Type | Required | Rule |
|---|---|---|---|
| `use_case` | text | yes | enum `kyc` \| `aml` \| `loan` |
| `files` | file, repeated | yes | 1..`GLDB_MAX_FILES_PER_CASE` (40); each `<= GLDB_MAX_UPLOAD_MB` (25); PDF, JPEG or PNG verified by magic bytes (`%PDF-`, `FFD8FF`, `89504E47`); filename sanitised (basename only, control chars stripped, max 120 chars) and stored for display only |
| `reference` | text | no | free text, max 80 |
| `applicant_name` | text | no | max 120. If given, it is authoritative (`applicant.source="upload"`) |
| `applicant_uen` | text | no | normalised upper-case, max 20; format is *not* a precondition (an invalid UEN is a finding, not an upload error) |

Behaviour: files are stored content-addressed (`sha256`); the case is created with `status="processing"`, `version=1`, `today` = `GLDB_TODAY` or the server date; a job is queued. The same file uploaded twice is accepted (duplicate detection is a check, not a gate).

`202 Accepted` response and `Location: /jobs/{job_id}`:

```json
{"case_id": "c_3fa91b2c0d4e", "job_id": "j_81c0a7d2e9b4", "status_url": "/jobs/j_81c0a7d2e9b4", "case_url": "/cases/c_3fa91b2c0d4e"}
```
Errors: 413, 415, 422 (`validation_error`, `no_files`, `too_many_files`). Nothing is written when any file is rejected (all-or-nothing).

## 4. Ranked queue: `GET /cases`

Query parameters (all optional):

| Param | Values | Default |
|---|---|---|
| `use_case` | `kyc`,`aml`,`loan` (comma-separated allowed) | all |
| `status` | comma-separated `CaseStatus` | `processing,open,awaiting_documents` (closed and error hidden unless asked) |
| `lane` | `edd`,`aml`,`loan`,`fast` (comma-separated) | all |
| `band` | `low`,`medium`,`high` | all |
| `min_score` | int 0-100 | none |
| `q` | substring of `reference` or `applicant_name` (case-insensitive) | none |
| `partial` | `true` \| `false` | none |
| `sort` | `priority` \| `score` \| `age` \| `coverage` \| `created` | `priority` |
| `order` | `desc` \| `asc` | `desc` for `score`/`created`/`age`, per rule for `priority` |
| `page`, `page_size` | pagination | 1, 25 |

`priority` order is frozen in architecture.md section 6 (status rank, score desc with partial cases floored at 30, coverage asc, created asc). `age` sorts by `created_at` ascending when `order=desc` (oldest first). Filters are applied in SQL via `Database.list_cases`.

Response: `Page<QueueItem>` (`QueueItem` in data-model.md), built row by row by `build_queue_item(row, *, now, pending_second_approvals)` in `api/queue.py` (api-backend; behaviour in scoring-and-queue.md 4.9). Example item:

```json
{"case_id": "c_3fa91b2c0d4e", "reference": "ACME-2026-014", "use_case": "loan", "status": "open",
 "applicant_name": "Example Trading Pte. Ltd.", "version": 1,
 "created_at": "2026-10-27T09:15:00Z", "updated_at": "2026-10-27T09:15:07Z", "age_seconds": 3600,
 "score": 75, "band": "high", "coverage": {"applicable": 21, "ran": 18, "ratio": 0.857, "skipped": [], "pages_total": 31, "pages_unchecked": 2},
 "lane": "loan", "top_finding": "Revenue 2.4M on the financial statements but only 0.9M of trade credits in the bank statements",
 "findings_count": 4, "high_count": 2, "partial": false, "decision": null, "pending_second_approvals": 0, "file_count": 14}
```
`age_seconds` is computed on the server from `created_at` to the request time (the queue is the only place the server's wall clock is used; checks never use it). Cases still `processing` appear with `score: null`.

## 5. Case view: `GET /cases/{id}`

Query: `version` (int, optional; default current), `include` (comma list; allowed: `words`, default none). Response `CaseDetail`: the `Case` model with `documents[*].words` omitted (unless `include=words`), `flags[*].disposition` materialised from the review log, and these extra top-level keys:

```json
{"...Case fields...": "...",
 "pending_second_approvals": [{"entry_id": "r3", "flag_ids": ["x_directors.d9+d12.-"], "reviewer": "a.tan", "created_at": "..."}],
 "allowed_verdicts": ["approve", "request_documents", "escalate_aml", "reject_or_escalate"],
 "not_checked_documents": [{"document_id": "d14", "doc_type": "other", "pages": "f9:1-2", "reason": "no checks for this document type"}],
 "cost_summary": {"total_usd": "0.012300", "calls": 14, "cache_hits": 6, "seconds_by_stage": {"ingest": 0.8, "classify": 0.1, "extract": 3.2, "forensics": 1.4, "rules": 0.2, "crossdoc": 0.1, "score": 0.0}, "usd_by_stage": {"extract": "0.012300"}}}
```
`allowed_verdicts` follows section 6 and is empty unless `status="open"`. `not_checked_documents` lists every document with `status="received_not_checked"` and every page the classifier labelled `boilerplate` (plan 5.3: pages treated as boilerplate are visible). `404 not_found` for an unknown id or version.

Other reads:
- `GET /cases/{id}/documents/{doc_id}/words` returns `{"document_id": "d3", "words": Word[]}` (large; fetched on demand).
- `GET /cases/{id}/files/{file_id}` returns the original bytes with `Content-Type` of the stored mime and `Content-Disposition: attachment; filename="<sanitised>"`.
- `GET /cases/{id}/versions` returns `{"versions": [{"version": 2, "created_at": "...", "score": 40, "band": "medium", "reason": "rerun" | "documents_added" | "created", "current": true}]}`.

### Page image: `GET /cases/{id}/pages/{n}`

`n` is the 1-based page within the file; the file is chosen with the **required** query `file_id`. Optional `w` (pixel width, 200-2000, default 1000). Returns `image/png`, rendered by PyMuPDF (PDF) or re-encoded after `ImageOps.exif_transpose` (images, so region fractions line up with the oriented image) and cached under `data/renders/`; headers `ETag: "<sha256>-<n>-<w>"` and `Cache-Control: private, max-age=31536000, immutable` (content-addressed, so a version bump with the same file reuses it). Region overlays are drawn by the frontend from `Flag.region` fractions (origin top-left). 404 if the file or page does not exist; 422 for a bad `w`.

## 6. Review: `POST /cases/{id}/review`

One endpoint, a discriminated union on `kind`. Common fields: `reviewer` (2-64 chars), `reason` (trimmed length >= 5, **mandatory on every entry**, max 1000), `case_version` (int; must equal the case's current version, else `409 stale_version`).

```json
{"kind": "verdict", "decision": "escalate_aml", "reviewer": "a.tan", "reason": "Remitter differs from invoice customer; funds routed through three companies.", "case_version": 1}
```
```json
{"kind": "flag_decision", "decision": "dismiss", "flag_ids": ["inv_dates.d5.invoice_date"], "reviewer": "a.tan", "reason": "Due date typo confirmed with the PO; not an alteration.", "case_version": 1}
```
```json
{"kind": "flag_decision", "decision": "confirm", "finding_id": "fnd_2", "reviewer": "a.tan", "reason": "Both documents omit the same shareholder.", "case_version": 1}
```
```json
{"kind": "second_approval", "decision": "approve", "target_entry_id": "r3", "reviewer": "b.lim", "reason": "Agree: the balance row was rounded by the bank.", "case_version": 1}
```

Rules (enforced by `review.py`, specified in review-workflow-and-audit.md):

| Rule | Detail |
|---|---|
| Open only | Any entry requires `status="open"`; else `409 case_not_open` (or `409 case_processing`) |
| `flag_decision` | exactly one of `flag_ids` (1..50, all must exist, else `404 not_found` with `details.kind="flag"`) or `finding_id` (expanded to its flags). `decision` is `confirm` or `dismiss`. A targeted flag with a pending dismissal gives `409 flag_decision_pending` |
| Dismissing a high flag | If any targeted flag has severity `high` and `GLDB_REQUIRE_SECOND_APPROVER` is true, the entry is stored with `status="pending_second_approval"` and the flags show `disposition="pending_second_approval"`. Low, medium and info dismissals are `effective` immediately. Confirming is always effective |
| `second_approval` | `target_entry_id` must be a pending entry (`409 entry_not_pending` otherwise) and `reviewer` must differ from the target's reviewer (`409 second_approver_must_differ`). `approve` makes the target `effective`; `decline` marks it `declined` (the flags return to `open`). The second-approval entry itself is `effective` |
| `verdict` | one of `approve`, `request_documents`, `refer_edd`, `escalate_aml`, `reject_or_escalate`, allowed per use case below, else `422 verdict_not_allowed` |
| `approve` gate | every `high` flag must be `confirmed` or effectively `dismissed`; a pending dismissal counts as open. Otherwise `409 high_flags_open` with `details.flag_ids` |
| Effects | `approve`, `refer_edd`, `escalate_aml`, `reject_or_escalate` set `status="closed"` and `case.decision`. `request_documents` sets `status="awaiting_documents"` (analysis kept). `escalate_aml` also sets `case.escalated_at` (the "suspicion formed" clock, plan 7.3; the tool does not file an STR) |
| No score change | Entries never change flags, findings or score |
| Audit | every accepted and every rejected-by-rule attempt is written to `audit_log` (pseudonymised, `GLDB_PSEUDONYMISE_LOGS`) |

Verdicts by use case (plan 7.2, 7.3): `approve` and `request_documents`: all use cases. `refer_edd`: `kyc` only. `escalate_aml`: all use cases (false documents at onboarding are an STR red flag, plan 7.1; the clock timestamp matters wherever suspicion forms). `reject_or_escalate`: `loan` only. No automatic approval or rejection exists anywhere (plan principle 2).

Response `201 Created`:

```json
{"entry": {"id": "r4", "kind": "verdict", "reviewer": "a.tan", "decision": "escalate_aml", "reason": "...", "flag_ids": [], "finding_id": null, "target_entry_id": null, "status": "effective", "case_version": 1, "created_at": "2026-10-27T10:02:11Z"},
 "case": {"id": "c_3fa91b2c0d4e", "status": "closed", "decision": "escalate_aml", "escalated_at": "2026-10-27T10:02:11Z", "version": 1, "pending_second_approvals": 0}}
```

`GET /cases/{id}/review` returns `{"entries": ReviewEntry[]}` oldest first. `GET /cases/{id}/audit` returns `{"entries": [{"id": 12, "ts": "...", "actor": "a.tan", "action": "review.verdict", "detail": {...}}]}`; `detail` carries ids and hashes, not names or NRICs.

Adding documents after `request_documents`: `POST /cases/{id}/files` (multipart `files`, same validation as section 3, only when `status` is `awaiting_documents` or `open`, else `409 case_not_open`). It stores the files, snapshots the current version to `case_versions`, creates `version+1` with `status="processing"`, keeps all review entries, queues a job, returns `202` with `{case_id, job_id, status_url, version}`. `POST /cases/{id}/rerun` does the same without new files and is allowed from `open`, `awaiting_documents` and `error` (`closed` gives `409 case_not_open`, `processing` gives `409 case_processing`); reproducibility test: deterministic stages give identical `provenance.flags_digest`. A case in `awaiting_documents` whose documents never arrive is closed by rerunning it (back to `open`) and posting a verdict; no direct close from `awaiting_documents` exists (review-workflow-and-audit.md 4.1).

## 7. Background jobs: `GET /jobs/{job_id}`

Processing runs in a bounded thread pool (D4). Poll every 1 s until `state` is `succeeded` or `failed`; the frontend backs off to 3 s after 30 s.

```json
{"id": "j_81c0a7d2e9b4", "case_id": "c_3fa91b2c0d4e", "kind": "analyse", "state": "running",
 "created_at": "2026-10-27T09:15:00Z", "started_at": "2026-10-27T09:15:01Z", "finished_at": null,
 "progress": {"stage_index": 3, "stage_count": 7, "current_stage": "extract"},
 "stages": [{"stage": "ingest", "status": "ok", "started_at": "...", "seconds": 0.8, "cost_usd": "0", "error": null, "warnings": [], "detail": {"pages": 14}}],
 "error": null}
```
`state`: `queued` | `running` | `succeeded` | `failed`. `succeeded` also covers a *partial* analysis (look at `case.partial`); `failed` only when the runner itself crashed (case then `status="error"`). On server restart, jobs left `running` become `failed` with `error="interrupted"` and their case `status="error"`; `POST /cases/{id}/rerun` recovers. `404` for an unknown job.

## 8. Case pack: `GET /cases/{id}/pack`

Query `format` = `json` (default) | `html`. Both include: case metadata, applicant, files with sha256, documents with status, flags with evidence and regions and dispositions, findings, score, coverage and skipped checks, stage records and costs, provenance, the full review log, escalation timestamp, and the not-checked documents list, plus a fixed disclaimer ("Decision support only; a human decides; not legal advice"). `html` is a single self-contained printable page (Jinja autoescape, inline CSS, no external requests, page images embedded as `data:` URIs capped at 800 px wide) with `Content-Type: text/html; charset=utf-8`; `json` is `application/json` with `Content-Disposition: attachment; filename="case-<id>-v<version>.json"`. Available when status is not `processing` (else `409 case_processing`).

## 9. Rule effectiveness: `GET /rules/effectiveness`

Query: `use_case` (optional), `since` (date, optional, on `Case.created_at`). Response:

```json
{"generated_for": {"use_case": null, "since": null},
 "cases_total": 42, "cases_decided": 30, "median_seconds_to_verdict": 540,
 "rules": [{"rule": "bs_running_balance", "stage": "rules", "default_severity": "high", "description": "...",
            "cases_fired": 6, "times_fired": 7, "confirmed": 5, "dismissed": 1, "pending": 0, "open": 1,
            "dismissal_rate": 0.1667, "median_seconds_to_decide": 210, "cases_ran": 40, "cases_skipped": 2}]}
```
`dismissal_rate = dismissed / (confirmed + dismissed)` (null if the denominator is 0). Counts use effective dispositions on the latest version of each case. Rules registered but never fired still appear with zeros. `GET /rules` is described in section 9a.

## 9a. Check catalogue and its change control: `/rules`

Checks are defined in code (`@check`, see `docs/check-catalogue.md`, which is generated and shows the **code defaults** only). An officer can lay an **override** on top of a check: switch it on or off, change its severity (`high`, `medium`, `low`, `info`), and edit its plain-language name, description ("what it checks") and an internal note. There is no second approver for these edits, but every edit needs the reviewer's name and a written reason and is kept in a permanent, append-only change log with the values before and after (tables `rule_overrides` and `rule_changes`, data-model.md). Overrides are global configuration; deleting a case or purging by retention never touches them.

**`GET /rules`** returns `{"rules": [RuleInfo]}`, one item per registered check (unfiltered, same order as `docs/check-catalogue.md`). Each item keeps its code-definition fields (`severity` is **always the code default**) and gains `default_severity` (same value), `effective_severity` (the override severity, else the default), `enabled` (`false` when switched off) and `override` (`null`, or a `RuleOverride`). Before an override, for `bs_running_balance`:

```json
{"rule": "bs_running_balance", "stage": "rules", "severity": "high", "applies_to": ["bank_statement"], "requires": [],
 "use_cases": ["aml", "kyc", "loan"], "description": "...", "tamper": "...", "version": "1",
 "default_severity": "high", "effective_severity": "high", "enabled": true, "override": null}
```
After `PUT /rules/bs_running_balance/override` with `{"enabled": false, "severity": "low", "name": "Balance chain", "reviewer": "Ann Officer", "reason": "Calibrated on last quarter"}`:

```json
{"rule": "bs_running_balance", "stage": "rules", "severity": "high", "applies_to": ["bank_statement"], "requires": [],
 "use_cases": ["aml", "kyc", "loan"], "description": "...", "tamper": "...", "version": "1",
 "default_severity": "high", "effective_severity": "low", "enabled": false,
 "override": {"enabled": false, "severity": "low", "name": "Balance chain", "description": null, "note": null,
              "updated_by": "Ann Officer", "updated_at": "2026-10-27T10:00:00.000000Z",
              "reason": "Calibrated on last quarter", "revision": 1}}
```

**`PUT /rules/{rule}/override`**, JSON body `OverrideRequest`: `{"enabled"?: bool, "severity"?: "high"|"medium"|"low"|"info"|null, "name"?: string|null, "description"?: string|null, "note"?: string|null, "reviewer": string, "reason": string}`. Partial update: an omitted field keeps its value; `null` clears that field's override (for the text fields an empty or blank string also clears it); `enabled` is true or false and cannot be null. Returns **200** with the updated rule item (the same shape as a `GET /rules` item).

- Text is **plain text**: control, bidirectional and zero-width characters become spaces and runs of whitespace collapse (the same cleaning as other officer and document text). Limits after cleaning: `name` 80, `description` 400, `note` 600, `reviewer` 2-80, `reason` 5-500 characters. Longer text is rejected (422 `validation_error`), never cut silently. Name, description and note are display-only: the backend stores and returns them and never feeds them to a model or into flags, evidence or prompts; the UI must render them as text.
- Errors: 404 `unknown_rule`; 422 `validation_error` (extra fields, wrong types, invalid severity, too long), `reviewer_required`, `reason_required`, and `no_change` when the request would leave the check exactly as it is. Nothing is written for a rejected request.
- **Back to the defaults.** If the new state equals the defaults (enabled, no severity, no name, no description, no note) the override row is deleted and the change is logged with `action: "reset"` and `after: null`. Otherwise the row is written and the log entry has `action: "update"`. An explicit severity equal to the code default is kept as an override (it flattens rules that emit mixed severities).
- `revision` counts every change ever logged for the rule (it keeps rising across resets).

**`DELETE /rules/{rule}/override`** resets a check to its code defaults. Body `{"reviewer": string, "reason": string}`; because some clients cannot send a body on DELETE, the same two values are also accepted as query parameters (`?reviewer=...&reason=...`; a value in the body wins). Returns **200** with the rule item (`override: null`), logged as `action: "reset"`. 404 `unknown_rule`, 404 `no_override` when there is nothing to reset, 422 `reviewer_required` / `reason_required`.

**`GET /rules/changes?rule=<id>&limit=<n>`** returns `{"changes": [RuleChange]}`, newest first; `rule` filters to one check, `limit` defaults to 50 (1-200; outside that is a 422). `RuleChange` is `{"id", "rule", "at", "reviewer", "reason", "action": "update"|"reset", "before": RuleOverride|null, "after": RuleOverride|null}`; `before` is `null` for the first change, `after` is `null` for a reset.

The two writes are rate limited with the same limiter as review POSTs (`GLDB_RATE_REVIEWS_PER_MIN`, 429 `rate_limited`, `details.scope = "reviews"`) and, like every route, sit behind the proxy secret.

**What an override does to an analysis** (the engine rules are in scoring-and-queue.md "Catalogue overrides"):

- **When.** Overrides are read once when an analysis starts and apply to analyses run **after** the change. A stored result keeps the settings it was analysed under until it is re-run; a re-run uses the current settings.
- **Severity.** Every flag the check returns gets the override severity (the severity the check produced is kept in `Flag.code_severity`). Findings, weights, the score, severity counts, the queue and effectiveness views follow the effective severity.
- **Hard stops never loosen.** `Finding.hard_stop` is computed from the original (code) severity and the rule id as well as the effective severity: lowering a check's severity reduces its points but cannot let a case into the fast lane that the defaults would have blocked.
- **Switched off** (reason code `switched_off`). The check is not executed. Its `CheckRun` is `status: "skipped"` with `code: "switched_off"` and `reason: "Switched off in the check catalogue by <reviewer> on <YYYY-MM-DD>: <reason>"`. It stays applicable, so `coverage.ratio` drops and the check appears in `coverage.skipped` as `{"rule", "status": "skipped", "reason", "code": "switched_off"}` (the `code` key exists only for this reason). The UI groups skipped checks by `code` when present. If the switched-off check is one of the hard-stop rules the fast lane is not offered.
- **Provenance.** When at least one override exists at analysis time, `provenance.catalogue = {"overrides": [{"rule", "enabled", "severity", "revision"}...sorted by rule], "hash": sha256}` records which custom settings the case was analysed under. Without overrides the key is absent and every existing hash is unchanged. `ruleset_version` is about code identity and does not change.

## 10. Delete and retention

`DELETE /cases/{id}?reviewer=<name>&reason=<text>` (both required, same validation as review; 422 `reviewer_required` / `reason_required`). `409 case_processing` while a job runs. Removes the case, versions, review entries, fingerprints, unreferenced stored files and renders (`Database.delete_case`); writes one audit row `case.deleted` whose `case_ref` is a hash of the id and whose `detail` has the reason (no applicant data). `204 No Content`; `404` if unknown. Idempotent re-delete is `404`.

`POST /admin/retention/purge` body `{"older_than_days": 1825, "dry_run": true}` (defaults: `GLDB_RETENTION_DAYS`, `dry_run=true`) returns `{"dry_run": true, "case_ids": ["c_..."], "count": 3}`; with `dry_run=false` it deletes them like above with `reviewer="system"`, `reason="retention"`.

## 11. `GET /health`

```json
{"status": "ok", "version": "0.1.0", "llm_backend": "fake", "ruleset_version": "rs-1a2b3c4d5e6f", "checks_registered": 55,
 "settings": {"classifier_min_prob": 0.7, "max_upload_mb": 25, "max_files_per_case": 40, "require_second_approver": true}}
```
The foundation stub returns the first five keys; api-backend adds `settings` (UI-relevant values, so the frontend does not hard-code them). Never includes secrets.

## 11a. `GET /explain/method`

The scoring and routing method as data, for the "?" explanations and the analysis trail. Read-only, no database, same access rules as every other route. Every value is read from the constants and functions the engine itself uses (`SCORE_WEIGHTS`, `SCORE_CAP`, `BAND_THRESHOLDS`, `FAST_LANE_MAX_SCORE`, `FAST_LANE_MIN_COVERAGE`, `LOW_COVERAGE_RATIO`, `HARD_STOP_RULES`, `AML_ROUTING_RULES`, `BASE_LANE`, `LANE_SUGGESTED_VERDICT`, `ruleset_version`), so the interface cannot drift from the engine. Key names are stable; new keys may be added, none removed.

```json
{
  "score": {
    "weights": {"high": 30, "medium": 15, "low": 5, "info": 0},
    "cap": 100,
    "bands": {"medium_from": 30, "high_from": 60},
    "counting": "Each finding counts once, however many flags or documents it covers."
  },
  "coverage": {
    "low_coverage_ratio": 0.7,
    "fast_lane_min_coverage": 0.8,
    "definition": "Checks that ran divided by checks that apply to the documents received."
  },
  "fast_lane": {
    "max_score": 20,
    "min_coverage": 0.8,
    "requires": [
      "The analysis is complete, not partial",
      "Score of 20 or less",
      "At least 80% of applicable checks completed, counting pages that could not be checked as not completed",
      "No finding that blocks the fast lane"
    ]
  },
  "hard_stop_rules": ["bs_pass_through", "copy_move", "file_reuse", "id_checksum", "inv_duplicate", "passport_mrz", "pdf_bytes_after_signature", "pdf_editor_software", "pdf_text_vs_render", "signature_reuse", "x_declared_vs_activity", "x_directors", "x_ownership_vs_registry", "x_remitter_vs_buyer", "x_sof_support"],
  "hard_stop_effect": "A hard stop never changes the score; it only blocks the fast lane.",
  "aml_routing_rules": ["bs_pass_through", "x_remitter_vs_buyer", "x_sof_support"],
  "lanes": [
    {"lane": "edd", "suggested_verdict": "refer_edd", "how": "KYC cases are routed here unless a scoring finding comes from an AML routing rule or the fast lane applies."},
    {"lane": "aml", "suggested_verdict": "escalate_aml", "how": "AML cases are routed here, and so is any other case with a scoring finding from an AML routing rule (bs_pass_through, x_remitter_vs_buyer, x_sof_support), unless the fast lane applies."},
    {"lane": "loan", "suggested_verdict": "reject_or_escalate", "how": "Loan cases are routed here unless a scoring finding comes from an AML routing rule or the fast lane applies."},
    {"lane": "fast", "suggested_verdict": "approve", "how": "Cases that meet every fast lane requirement are routed here. It means look at this last: a person still decides and nothing is approved automatically."}
  ],
  "ruleset_version": "rs-02de835ec068"
}
```
Notes: `low_coverage_ratio` is the point below which the UI calls coverage "low" (a display marker; it routes nothing). `fast_lane.max_score` and `min_coverage` are the numbers behind the `requires` sentences. The fast lane tests the score (at most `max_score`), not the band. `suggested_verdict` is what a lane suggests to the person deciding; it never preselects a verdict. `ruleset_version` equals `/health` and `provenance.ruleset_version`.

## 11b. Why a document was classified that way

`documents[].classification` (also each `files[].pages[].classification`, `documents[].issuer`, and the classification in the pack's `documents[]`) carries two optional explanation fields next to `label`, `probability`, `classifier`, `version` and `distribution`:

```json
{"label": "bank_statement", "probability": 1.0, "classifier": "rules", "version": "3",
 "distribution": {"bank_statement": 1.0, "invoice": 0.0, "...": 0.0},
 "reasons": ["Found \"statement of account\", \"closing balance\", \"balance brought forward\" and a transaction table header",
             "Also found \"withdrawals\", \"deposits\", \"account number\" and \"statement period\"",
             "Weaker hints \"page N of N\""],
 "rationale": null}
```
```json
{"label": "invoice", "probability": 0.95, "classifier": "llm:<model>", "version": "1",
 "reasons": [],
 "rationale": "The words Tax Invoice and Invoice Number point to an invoice."}
```
* `reasons: string[]` (default `[]`): deterministic cues the rules classifier saw for the winning label, strongest first; at most 5 items of at most 120 characters, control characters removed. Empty when a model decided. Literal cue phrases or plain descriptions of a pattern, never raw page text.
* `rationale: string | null` (default `null`): the hosted fallback model's own one-sentence reason, at most 240 characters, control characters removed. **Untrusted data** (a model read an attacker-controlled page): show it as plain text, never interpret or execute it. It never influences the label, score, flags or routing. `null` when rules or a local model decided, or the model gave none.
* Cases stored before these fields existed load with the defaults.

## 12. TypeScript types (paste into `frontend/src/lib/types.ts`)

```ts
export type UseCase = "kyc" | "aml" | "loan";
export type Severity = "high" | "medium" | "low" | "info";
export type FlagStage = "forensics" | "rules" | "crossdoc";
export type StageName = "ingest" | "classify" | "extract" | "forensics" | "rules" | "crossdoc" | "score";
export type StageStatus = "pending" | "running" | "ok" | "failed" | "skipped";
export type DocType =
  | "bank_statement" | "invoice" | "financial_statement" | "contract" | "purchase_order"
  | "company_profile" | "certificate_of_incorporation" | "ownership_chart" | "shareholder_register"
  | "source_of_funds_declaration" | "national_id" | "passport" | "boilerplate" | "other";
export type DocStatus = "ok" | "partial" | "failed" | "received_not_checked";
export type CaseStatus = "processing" | "open" | "awaiting_documents" | "closed" | "error";
export type Verdict = "approve" | "request_documents" | "refer_edd" | "escalate_aml" | "reject_or_escalate";
export type Lane = "edd" | "aml" | "loan" | "fast";
export type Band = "low" | "medium" | "high";
export type CellKind = "text" | "money" | "decimal" | "percent" | "int" | "date" | "id" | "bool" | "enum";
export type CellSource = "text_layer" | "llm_cheap" | "llm_strong" | "derived";
export type Disposition = "open" | "confirmed" | "dismissed" | "pending_second_approval";
export type JobState = "queued" | "running" | "succeeded" | "failed";

/** Decimal string, e.g. "12345.67". Never parse with Number() for arithmetic; use a decimal lib or compare as strings for display. */
export type DecimalString = string;
/** ISO date "YYYY-MM-DD" and ISO datetime "...Z". */
export type IsoDate = string;
export type IsoDateTime = string;

export interface Region {
  file_id: string;
  page: number;      // 1-based within the file
  x: number; y: number; w: number; h: number;   // fractions of the page, origin top-left
  label?: string | null;
}

export interface Cell {
  value: string | null;
  raw?: string | null;
  kind: CellKind;
  box?: Region | null;
  confidence?: number | null;
  source: CellSource;
  verified?: boolean | null;
  alt_raw?: string | null;
}
export interface Annotation { label: string; probability: number; classifier: string; version: string }
export interface Row { index: number; cells: Record<string, Cell>; annotations: Record<string, Annotation>; box?: Region | null }
export interface Table { name: string; columns: string[]; rows: Row[]; pages: number[] }
export interface Word { page: number; text: string; x: number; y: number; w: number; h: number; font?: string | null; size?: number | null; line: number; block: number }

export interface Classification { label: string; probability: number; classifier: string; version: string; distribution?: Record<string, number> | null; reasons?: string[]; rationale?: string | null }
export interface PageInfo {
  page: number; width_pt: number; height_pt: number; rotation: number;
  has_text_layer: boolean; is_scan: boolean; word_count: number;
  classification?: Classification | null; error?: string | null;
}
export interface FileRef {
  id: string; name: string; sha256: string; size_bytes: number;
  mime: "application/pdf" | "image/jpeg" | "image/png";
  pages: PageInfo[]; pdf_meta: Record<string, string>; added_in_version: number;
}
export interface Document {
  id: string; file_id: string; page_start: number; page_end: number;
  doc_type: DocType; classification: Classification; issuer?: Classification | null;
  status: DocStatus; extractor?: string | null;
  fields: Record<string, Cell>; tables: Record<string, Table>;
  words?: Word[];            // present only with ?include=words
  warnings: string[]; error?: string | null;
}

export interface Flag {
  id: string; rule: string; stage: FlagStage; severity: Severity;
  document_ids: string[];
  table?: string | null; field?: string | null; row?: number | null;
  region?: Region | null; extra_regions: Region[];
  evidence: string; explanation: string;
  disposition: Disposition;
  /** Severity the check produced before a catalogue override changed it; absent unless an override did (section 9a). */
  code_severity?: Severity;
}
export interface Finding {
  id: string; group_key: string; title: string; severity: Severity;
  flag_ids: string[]; document_ids: string[]; rules: string[];
  weight: number; summary: string; hard_stop: boolean;
}
export interface Score { value: number; band: Band; partial: boolean; partial_reasons: string[] }
export interface CoverageSkip { rule: string; status: "skipped" | "error"; reason: string; code?: "switched_off" }
export interface Coverage { applicable: number; ran: number; ratio: number; skipped: CoverageSkip[]; pages_total: number; pages_unchecked: number }
export interface CheckRun { rule: string; stage: FlagStage; status: "ran" | "skipped" | "error"; reason?: string | null; code?: "switched_off"; flags: number; seconds: number }
export interface Cost {
  stage: StageName; purpose: string; model: string;
  prompt_tokens: number; completion_tokens: number; usd: DecimalString;
  seconds: number; cache_hit: boolean; document_id?: string | null; page?: number | null;
}
export interface StageRecord {
  stage: StageName; status: StageStatus; started_at?: IsoDateTime | null; seconds: number;
  cost_usd: DecimalString; error?: string | null; warnings: string[]; detail: Record<string, number | string>;
}
export interface Provenance {
  app_version: string; ruleset_version: string; models: Record<string, string>;
  prompt_hash: string; prompt_hashes: Record<string, string>;
  classifier_versions: Record<string, string>; extractor_versions: Record<string, string>;
  input_hashes: Record<string, string>; settings_hash: string;
  today: IsoDate; flags_digest: string; created_at: IsoDateTime;
  /** Present only when check catalogue overrides existed at analysis time (section 9a). */
  catalogue?: { overrides: { rule: string; enabled: boolean; severity: Severity | null; revision: number }[]; hash: string };
}
export interface ReviewEntry {
  id: string; kind: "flag_decision" | "verdict" | "second_approval";
  reviewer: string; decision: string; reason: string;
  flag_ids: string[]; finding_id?: string | null; target_entry_id?: string | null;
  status: "effective" | "pending_second_approval" | "declined";
  case_version: number; created_at: IsoDateTime;
}
export interface Applicant { name?: string | null; uen?: string | null; source: "upload" | "derived" }

export interface CaseDetail {
  id: string; reference?: string | null; use_case: UseCase; status: CaseStatus; version: number;
  today: IsoDate; created_at: IsoDateTime; updated_at: IsoDateTime;
  applicant: Applicant; files: FileRef[]; documents: Document[];
  flags: Flag[]; findings: Finding[];
  score?: Score | null; coverage?: Coverage | null; check_runs: CheckRun[];
  lane?: Lane | null; top_finding?: string | null;
  partial: boolean; partial_reasons: string[];
  stages: StageRecord[]; provenance?: Provenance | null; costs: Cost[];
  review: ReviewEntry[]; decision?: Verdict | null; escalated_at?: IsoDateTime | null;
  // API-only extras
  pending_second_approvals: { entry_id: string; flag_ids: string[]; reviewer: string; created_at: IsoDateTime }[];
  allowed_verdicts: Verdict[];
  not_checked_documents: { document_id: string; doc_type: DocType; pages: string; reason: string }[];
  cost_summary: {
    total_usd: DecimalString; calls: number; cache_hits: number;
    seconds_by_stage: Partial<Record<StageName, number>>; usd_by_stage: Partial<Record<StageName, DecimalString>>;
  };
}

export interface QueueItem {
  case_id: string; reference: string | null; use_case: UseCase; status: CaseStatus;
  applicant_name: string | null; version: number;
  created_at: IsoDateTime; updated_at: IsoDateTime; age_seconds: number;
  score: number | null; band: Band | null; coverage: Coverage | null;
  lane: Lane | null; top_finding: string | null;
  findings_count: number; high_count: number; partial: boolean;
  decision: Verdict | null; pending_second_approvals: number; file_count: number;
}
export interface Page<T> { items: T[]; total: number; page: number; page_size: number; has_next: boolean }

export interface QueueQuery {
  use_case?: UseCase[]; status?: CaseStatus[]; lane?: Lane[]; band?: Band[];
  min_score?: number; q?: string; partial?: boolean;
  sort?: "priority" | "score" | "age" | "coverage" | "created"; order?: "asc" | "desc";
  page?: number; page_size?: number;
}

export interface CreateCaseResponse { case_id: string; job_id: string; status_url: string; case_url: string }
export interface Job {
  id: string; case_id: string; kind: "analyse" | "rerun"; state: JobState;
  created_at: IsoDateTime; started_at: IsoDateTime | null; finished_at: IsoDateTime | null;
  progress: { stage_index: number; stage_count: number; current_stage: StageName | null };
  stages: StageRecord[]; error: string | null;
}

interface ReviewBase { reviewer: string; reason: string; case_version: number }
export type ReviewRequest =
  | (ReviewBase & { kind: "verdict"; decision: Verdict })
  | (ReviewBase & { kind: "flag_decision"; decision: "confirm" | "dismiss"; flag_ids?: string[]; finding_id?: string })
  | (ReviewBase & { kind: "second_approval"; decision: "approve" | "decline"; target_entry_id: string });
export interface ReviewResponse {
  entry: ReviewEntry;
  case: { id: string; status: CaseStatus; decision: Verdict | null; escalated_at: IsoDateTime | null; version: number; pending_second_approvals: number };
}

export interface RuleEffectiveness {
  rule: string; stage: FlagStage; default_severity: Severity; description: string;
  cases_fired: number; times_fired: number; confirmed: number; dismissed: number; pending: number; open: number;
  dismissal_rate: number | null; median_seconds_to_decide: number | null; cases_ran: number; cases_skipped: number;
}
export interface EffectivenessResponse {
  generated_for: { use_case: UseCase | null; since: IsoDate | null };
  cases_total: number; cases_decided: number; median_seconds_to_verdict: number | null;
  rules: RuleEffectiveness[];
}
export interface RuleOverride {
  enabled: boolean; severity: Severity | null; name: string | null; description: string | null; note: string | null;
  updated_by: string; updated_at: IsoDateTime; reason: string; revision: number;
}
export interface RuleInfo {
  rule: string; stage: FlagStage; severity: Severity /* always the code default */; applies_to: string[]; requires: string[];
  use_cases: UseCase[]; description: string; tamper: string | null; version: string;
  default_severity: Severity; effective_severity: Severity; enabled: boolean; override: RuleOverride | null;
}
export interface OverrideRequest {
  enabled?: boolean; severity?: Severity | null; name?: string | null; description?: string | null; note?: string | null;
  reviewer: string; reason: string;   // omitted field keeps its value; null (or "" for text) clears it
}
export interface ResetRequest { reviewer: string; reason: string }
export interface RuleChange {
  id: number; rule: string; at: IsoDateTime; reviewer: string; reason: string;
  action: "update" | "reset"; before: RuleOverride | null; after: RuleOverride | null;
}
export interface ApiError { error: { code: string; message: string; details?: Record<string, unknown> } }
export interface Health {
  status: "ok"; version: string; llm_backend: string; ruleset_version: string; checks_registered: number;
  settings?: { classifier_min_prob: number; max_upload_mb: number; max_files_per_case: number; require_second_approver: boolean };
}
```

Client helpers: `api.ts` exposes `getQueue(q)`, `createCase(form)`, `getCase(id, version?)`, `pageUrl(id, fileId, n, w?)`, `pollJob(id)`, `postReview(id, req)`, `getEffectiveness()`; it throws an `ApiErrorException` carrying `ApiError.error`. Regions render as absolutely positioned boxes at `left = x*100%`, `top = y*100%` over the page image.

## 13. Test plan

- Contract tests (api-backend): upload happy path (2 files, 202, job reaches `succeeded`, case `open`); magic-byte rejection (a `.pdf` containing text gives 415); oversize 413; zero files 422; all-or-nothing on a bad third file; queue filters and pagination (`has_next`), default status filter hides `closed`; `sort=priority` ordering fixture; page image PNG signature and ETag; review: missing reason 422, 4-character reason 422, dismiss high flag becomes `pending_second_approval`, same reviewer countersign 409, different reviewer 201 and flag `dismissed`, `decline` returns flag to `open`, `approve` with an open high flag 409, `refer_edd` on a loan case 422, `escalate_aml` sets `escalated_at`, stale `case_version` 409, review on a closed case 409; delete returns 204 and `GET` 404, audit row exists without applicant name; pack JSON and HTML (autoescape: a flag evidence of `<script>` appears escaped); jobs 404; `X-Request-Id` on errors.
- Clean-case behaviour: a case with no flags returns `score.value=0`, `band="low"`, `findings=[]`, `allowed_verdicts` includes `approve` (no `high_flags_open`).
- Honest quirk: a case whose scanned pages cannot be read (no LLM key) returns `partial=true`, `partial_reasons` non-empty, a `doc_unreadable` info flag, and is still reviewable.

## 14. Acceptance criteria

1. Every endpoint in section 2 exists, returns the shapes above, and uses the single error body.
2. `uv run pytest backend/tests/test_api_*.py` passes without network or API key.
3. The TypeScript block type-checks against the real responses (frontend contract test fetches fixtures produced by the API tests).
4. No endpoint can approve, reject or escalate without a human `reviewer` and `reason`.

## 15. Known gaps and honest limits

- No authentication or roles: "second approver" is a different free-text name, not a proven identity.
- Single process; jobs are not durable across restarts (they fail and can be rerun).
- Pack PDF output is not provided (HTML is print-ready); STR filing is out of scope.
- Rule effectiveness measures reviewer agreement, not ground truth; ground truth comes from the eval.
- **Stretch endpoints not built** (CR-48, CR-49, T-api-backend-12): there is no `GET /cases/{id}/graph` (the UI derives the entity graph from the case body, frontend.md section 9.2) and `GET /cases/{id}/audit` has no `limit`/`after` cursor. The case pack does not include `EntityGraph.summary()` (CR-23).

## Contract requests

**W3 closure (T-integration-kyc-08):** every request in this section has a recorded outcome in tasks.md sections 4.2 and 4.2a (accepted, decided at R0, or not built and listed under Known gaps above); none is open.

R0 folded in: `flag_decision_pending`, rerun statuses, `/health` settings echo, `Coverage.pages_*`, image pages transposed, `build_queue_item` in `api/queue.py`. Not built (stretch, tasks CR-48/CR-49): audit cursor, server-side entity graph endpoint.
