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
| GET | `/spending` | model spend today and over 7 days, per signed-in account and for the site; administrator accounts only (403 otherwise) | demo |
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
| GET | `/me` | the signed-in account the proxy vouched for, or null, whether it is an administrator (`GLDB_ADMIN_USERS`), and whether custom rules are available to it (`custom_rules`) | 5.9 |
| GET | `/monitoring` | metrics against thresholds, spend, owner, disk encryption, application encryption at rest | 5.23 |
| GET | `/monitoring/incidents` | the AI incident log, newest first | 5.23 |
| POST | `/monitoring/incidents` | report (or resolve) an AI incident; severity medium or high is also announced through the alert sinks | 5.23 |
| POST | `/monitoring/incidents/{id}/acknowledge` | note that someone has seen an open incident, optionally assigning it; open / resolved is unchanged | 5.23 |
| GET | `/monitoring/alerts` | alert state (open, acknowledged, resolved), recent deliveries per sink, last test alert, scheduler | 5.23 |
| POST | `/monitoring/alerts/test` | send a labelled test alert through every configured sink and record the per-sink result | 5.23(c) |
| POST | `/monitoring/alerts/{key}/acknowledge` | acknowledge an open alert (account or name, and a reason; audited) | 5.23(c) |
| GET | `/monitoring/readiness` | the 5.23 (a) to (c) checklist: named owner, approved thresholds, alerts that reach a watched place | 5.23 |
| GET | `/monitoring/notifications` | the in-app notification feed (alerts raised and resolved, medium and high incidents, test alerts, the monitor's silence), newest first, paged, with the unread count | 5.23(c) |
| GET | `/monitoring/notifications/summary` | counts for the navigation badge: unread notifications, open alerts, and the badge number | 5.23(c) |
| POST | `/monitoring/notifications/read` | mark notifications read (ids, or all) under the signed-in account or a typed name; audited | 5.23(c) |
| POST | `/monitoring/thresholds/approve` | record, in the audit log, that the bank approved the current threshold values (approver, role, note) | 5.23(b) |
| GET | `/custom-rules/capabilities` | what a custom rule can and cannot do: templates, operators, every field per document, the financial statement lines, what is impossible, limits | custom rules |
| POST | `/custom-rules/drafts` | start the rule assistant with a plain-English request: classify, draft, ideate or refuse (the one route that calls the model, with the turns below) | custom rules |
| GET | `/custom-rules/drafts/{id}` | the draft as of its last turn (own drafts only) | custom rules |
| POST | `/custom-rules/drafts/{id}/turns` | one more turn: more words (model), an answer to a question, or a choice between readings (no model) | custom rules |
| POST | `/custom-rules/drafts/{id}/dry-run` | run the draft's rule over stored cases (counts only); saving needs it | custom rules |
| POST | `/custom-rules` | save a draft as a private rule, or as a new version of one (reason required) | custom rules |
| GET | `/custom-rules` | the signed-in account's own rules and the public rules it adopted | custom rules |
| GET | `/custom-rules/shared` | the public library: every public rule, author attributed, labelled custom and unreviewed | custom rules |
| GET | `/custom-rules/changes` | change log of the account's own rules and adoptions (all of it for an administrator) | custom rules |
| GET | `/custom-rules/tickets` | requests sent to the tech team (own; all for an administrator) | custom rules |
| POST | `/custom-rules/tickets` | send a draft, or the officer's own words, to the tech team (no model) | custom rules |
| GET | `/custom-rules/{rule_id}` | one rule (the owner also gets every version) | custom rules |
| POST | `/custom-rules/{rule_id}/enabled` | switch an own rule on or off (reason required) | custom rules |
| POST | `/custom-rules/{rule_id}/publish` | make the current version public (reason required) | custom rules |
| POST | `/custom-rules/{rule_id}/unpublish` | take a public rule back; the author, or an administrator (reason required) | custom rules |
| POST | `/custom-rules/{rule_id}/adopt` | opt in to a public rule so it runs on analyses this account starts | custom rules |
| POST | `/custom-rules/{rule_id}/unadopt` | stop using an adopted rule | custom rules |
| POST | `/custom-rules/{rule_id}/retire` | retire an own rule; every version is kept (reason required) | custom rules |

Routers: `health.py`, `cases.py` (create, get, delete, rerun, files, versions, documents), `queue.py` (list), `jobs.py`, `files.py` (pages, file download), `review.py` (+audit), `pack.py`, `effectiveness.py`, `spending.py`, `rules.py` (catalogue, overrides and change log), `explain.py`, `monitoring.py` (monitoring, alerts, readiness and the incident log; `/me` is in `health.py`), `custom_rules.py` (custom rules, section 9c).

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

## 9b. Monitoring, alerts and readiness: `/monitoring`

Design and operator guide: [ai-incidents.md](ai-incidents.md). These routes are about the monitor, never about a case or an applicant. `POST /monitoring/thresholds/approve` is the one route with "approve" in its path; it records a person's decision about the monitoring thresholds and decides nothing about a case.

`GET /monitoring` keeps its earlier keys (`alerts`, `alerting`, `spend`, `owner_named`, `disk_encryption`, ...) and adds: `scheduler` (`"off"`, `"in_process"` or `"external"`), `scheduler_note`, `interval_minutes`, `last_evaluated_at`, `last_evaluated_by` (`scheduler` or `cli`), `monitor_stale` (true when a schedule is on and nothing has evaluated for more than two intervals), `alert_sinks` (names only), `open_alerts` (keys) and `readiness` (`ready` plus the status of each item). It also carries `score_model`: `{"name": "legacy"|"calibrated", "version", "ok", "ordering_signal_only": true, "note"}` and, for the calibrated model, `weights_version`, `weights_sha256`, `weights_source` (`synthetic` or `review_log`) and `fitted_on_synthetic`. A weights file that cannot be read shows `ok: false` with an `error`; scoring then stops rather than falling back (scoring-and-queue.md 4.4a).

`GET /monitoring/alerts` returns `{"alerts": [{"key", "status": "open"|"acknowledged"|"resolved", "first_seen", "last_seen", "last_notified", "notify_count", "notified", "value", "threshold", "meaning", "action", "acknowledged_by", "acknowledged_at", "note", "resolved_at"}], "deliveries": [{"ts", "sink", "kind", "alert_key", "ok", "attempts", "error"}], "sinks": [...], "last_test": {"at", "by", "results", "delivered_to"} | null, ...scheduler fields}`. `error` is an exception class or `HTTP_<status>`, never a message or a URL. Keys are the metric names plus `monitor_stale`.

`POST /monitoring/alerts/{key}/acknowledge` body `{"reason": "...", "reviewer": "name"}`. The signed-in account replaces a typed `reviewer`; with `GLDB_PERSONAL_ACCOUNTS=true` a request without an account is `403 account_required`. `reason` follows the review rules (`422 reason_required`). `404 not_found` for an unknown key, `409 alert_not_open` when the alert is already acknowledged or resolved. Writes `monitoring.alert_acknowledged` to the audit log.

`POST /monitoring/alerts/test` sends a test alert and returns `{"sent_at", "sinks_configured", "results": [{"sink", "ok", "attempts", "error", "at"}], "delivered", "message"}`. At most one every 30 seconds (`429 rate_limited`).

`GET /monitoring/readiness` returns `{"as_of", "ready", "statement", "items": [{"id": "5.23(a)"|"5.23(b)"|"5.23(c)", "name", "status": "met"|"not_met", "detail", "missing": [...]}]}`; `ready` is true only when all three are met. `POST /monitoring/thresholds/approve` body `{"approver", "role", "note"}` (2-80, 2-80, 5-500 characters) returns `201` with the stored hash; the approval counts only while the thresholds hash to the same value.

`POST /monitoring/incidents/{id}/acknowledge` body `{"note", "assigned_to"?, "reviewer"?}` returns `201`; `404` unless the incident is an open report. `GET /monitoring/incidents` then shows `acknowledged_by`, `acknowledged_at`, `ack_note` and `assigned_to`; `status` stays `open` until a report names it in `resolves`.

### The in-app notification feed (the Notifications page)

The in-app sink is always on: every message the alert service announces (an alert raised, a reminder, a resolution, a medium or high incident, a test alert, the `monitor_stale` alert) is also written to a `notifications` table, whether or not an outside sink is configured. It holds metric names, numbers, thresholds, the meaning and action text written by the code, an incident id and severity; never case content or an analyst's free text. Delivery to the page is not the same as someone watching it.

`GET /monitoring/notifications?limit=50&before=<id>&since=<iso>&category=alerts|incidents|system&unread=true` returns `{"as_of", "notifications": [{"id", "ts", "kind": "alert"|"reminder"|"resolved"|"incident"|"test", "category", "key", "severity": "info"|"attention"|"high", "title", "text", "data": {"metric"?, "value"?, "threshold"?, "incident_id"?, "severity"?, "reported_by"?}, "read", "read_at", "read_by"}], "unread", "next_before"}`, newest first. `limit` is 1 to 200; pass `next_before` as `before` for the next page (null on the last page); `since` keeps entries newer than that ISO date or time (`422 validation_error` if unparseable); `unread` is the count over the whole feed whatever the filter. `monitor_stale` and test entries are in category `system`.

`GET /monitoring/notifications/summary` returns `{"unread", "open_alerts", "badge"}` (cheap, no snapshot). `badge` is what the navigation shows: open alerts plus unread notifications, counting an open alert and its own unread "raised" notification once.

`POST /monitoring/notifications/read` body `{"ids": [1, 2]}` (1 to 200 ids) or `{"all": true}`, plus `"reviewer"`; exactly one of `ids` and `all`. The signed-in account replaces a typed name; with `GLDB_PERSONAL_ACCOUNTS=true` a request without one is `403 account_required`; no name is `422 reviewer_required`. Returns `{"marked", "unread"}`; unknown or already-read ids change nothing. Writes `monitoring.notifications_read` (count, up to 50 ids) to the audit log when something changed. Acknowledging an alert or an incident also marks its own notifications read. Read state is shared: it says that someone looked, not who in particular must.

The in-app sink counts as a configured sink in `GET /monitoring/readiness` item (c) and `alert_sinks`; the readiness text says plainly that delivery to the page is not the same as someone watching it (`page_only: true`, `watching_confirmed: false`).

## 9c. Custom rules: `/custom-rules`

Officers describe a new rule-based check in plain English and a severity (`info`, `low` or `medium`, never `high`, never a hard stop). The model (Claude Haiku) fills a closed JSON schema for one of four templates and a fixed engine runs it; no generated code ever runs. Plain-language guide: [custom-rules.md](custom-rules.md).

- **Gate.** Every route answers 404 `feature_off` unless `GLDB_CUSTOM_RULES=on`, and 403 `account_required` unless a personal account is signed in (`/me` reports `custom_rules: true` only then). A private rule, draft or ticket of another account answers 404, as if it did not exist.
- **Only two routes cost money:** `POST /custom-rules/drafts` and a `text` turn. They are charged to the signed-in account's daily model spend (purpose `custom_rule:draft`, and `custom_rule:repair` for the one optional second call), never filed on a case, and share the review rate limiter. Everything else (listing, dry runs, saving, publishing, adopting, retiring, tickets) works with the budget used up. Casework is never affected.
- All writes are `POST`. Bodies are strict (extra fields are a 422). Free text is cleaned to one line of plain text and must be rendered as text.

**Classification.** Every request starts with cheap checks in code (empty, injection markers, protected attributes, high severity, hard stop, changing built-in checks or the score), then one model call. Code, not the model, decides the final `status`, and only ever lowers the model's claim:

| `status` | Meaning | What the officer sees |
|---|---|---|
| `ready` | one valid rule, nothing left out | readback, dry run, save |
| `needs_clarification` | a question must be answered first | questions with options; the proposed `default` is never applied without a click |
| `ambiguous` | two or three plausible rules | `candidates` to choose from |
| `partially_supported` | part of the request can be a rule | `will_check` and `will_not_check`; saving needs `accept_narrower` |
| `unsupported` | nothing useful is possible | ideas, and a ticket for the tech team is created at once |
| `refused` | high severity, hard stop, protected attribute, change to built-in checks or the score, injection, nonsense | the reason; no ticket; the attempt is logged (a hash of the text) |

A draft is a bounded conversation: at most 6 turns, at most 2 model calls per turn. The 7th turn is 409 `draft_turn_limit`.

**`GET /custom-rules/capabilities`** returns what the model is told (generated from the document schemas, so it cannot drift): `templates`, `operators` (by field kind), `operator_text`, `documents[].fields[]` (`path`, `label`, `kind`, `required`, `values`, `meaning`, `templates`), `fs_lines`, `severities`, `impossible[]` (`reason`, `text`, `examples`) and `limits`.

**`POST /custom-rules/drafts`** body `{"text": string (3-1000), "severity"?: "info"|"low"|"medium", "edit_of"?: "cus_..."}` returns a `DraftResponse` (200). Example for `flag as medium if the firm has a lot of debt from different places`:

```json
{"draft_id": "crd_5b2e9f0a1c3d", "turn": 1, "turns_left": 5, "status": "partially_supported",
 "understood": "Flag a company whose borrowings are large compared with its equity, and whose debt comes from several lenders.",
 "spec": {"spec_version": 1, "severity": "medium", "condition": {"template": "fs_ratio",
   "left": ["balance_sheet.borrowings_current", "balance_sheet.borrowings_non_current"], "operator": "gt",
   "multiplier": "2", "right": "balance_sheet.total_equity", "column": "current"}},
 "spec_hash": "2f1676f0...", "readback": "Flags medium when, on a financial statement's current-year column, borrowings (current) + borrowings (non-current) is greater than 2 times total equity.",
 "candidates": [], "questions": [{"id": "q1", "slot": "multiplier", "text": "What counts as a lot of debt?", "default": "x2",
   "options": [{"id": "x1", "label": "More than equity", "value": "1"}, {"id": "x2", "label": "More than 2 times equity", "value": "2"}, {"id": "x3", "label": "More than 3 times equity", "value": "3"}]}],
 "assumptions": ["Debt is read as current plus non-current borrowings, compared with total equity."],
 "phrase_map": [{"phrase": "flag as medium", "slot": "severity"}, {"phrase": "the firm", "slot": "filler"}, {"phrase": "a lot of", "slot": "multiplier"}, {"phrase": "debt", "slot": "left"}, {"phrase": "from different places", "slot": null}],
 "unsupported_parts": [{"phrase": "from different places", "reason": "field_not_extracted", "detail": "Lenders are not read from financial statements."}],
 "closest_templates": ["fs_ratio"],
 "will_check": ["Flags medium when, on a financial statement's current-year column, ..."],
 "will_not_check": ["\"from different places\": the system does not read this from the documents"],
 "refusal": null, "duplicates": [], "ideas": [{"kind": "weaker_rule", "text": "...", "spec": {}, "rule": null, "template": null}],
 "ticket": null, "notes": ["There is a question to answer before this can be saved."], "dry_run": null,
 "save_allowed": false, "needs_narrower_ack": true, "edit_of": null, "model_calls": 1}
```
Other statuses use the same envelope. `unsupported` adds `"ticket": {"id": "crt_9c0d1e2f3a4b", "status": "submitted", "message": "This will be submitted to the tech team (ticket crt_9c0d1e2f3a4b)."}`; `refused` has `"refusal": {"reason": "protected_attribute", "message": "..."}`, `"spec": null` and `"model_calls": 0`.

**`POST /custom-rules/drafts/{id}/turns`** body: exactly one of `{"text": string}` (classified again, one or two model calls), `{"answer": {"question_id": "q1", "option_id": "x2"}}` or `{"choose_candidate": 0}` (no model call). An answer that gives an invalid rule is 422 `bad_answer`. After the `x2` answer above: `turn: 2`, `questions: []`, `save_allowed: true`, `model_calls: 0`. A refused message empties the draft and counts as a turn.

**`POST /custom-rules/drafts/{id}/dry-run`** returns `{"spec_hash", "cases_considered", "evaluated", "fired", "skipped_missing", "scanned_evaluated", "scanned_fired", "fire_rate", "warnings": [{"code": "no_cases"|"fires_on_none"|"fires_too_often", "message"}], "examples": [case ids], "needs_confirmation"}`. Firing on none, or on more than 30% of the cases evaluated, is a warning that needs `confirm_warnings` to save. 409 `no_spec` without a valid rule. Counts and a few case ids only; nothing is written to any case.

**`POST /custom-rules`** (201) body `{"draft_id", "name"?, "description"?, "reason", "accept_narrower"?, "confirm_warnings"?, "submit_ticket"?}` returns `{"rule": RuleView, "ticket": TicketRef | null}`. The gates, in order: the draft is the account's (404) and open (409 `draft_closed`); 422 `draft_not_ready`; 422 `narrower_not_accepted`; 409 `duplicate_builtin` (details `rule`, `what`, `action`: use the severity override of that check instead); 409 `duplicate_custom` (details `rule_id`, `name`); 422 `no_change` (an edit equal to the current version); 409 `dry_run_required` (a dry run of exactly this spec); 422 `warnings_not_confirmed`; 422 `name_required` (3-80); 422 `reason_required` (5-500); 409 `rule_limit` (`GLDB_CUSTOM_RULES_MAX_PER_ACCOUNT`). With `edit_of` the save is a new version of that rule. `submit_ticket` files a ticket (origin `partial`) for the part that cannot be checked.

**Rules.** `GET /custom-rules` returns `{"mine": [RuleView], "adopted": [AdoptedView]}`; `GET /custom-rules/shared` returns `{"rules": [SharedView]}`; `GET /custom-rules/{rule_id}` returns a `RuleView` (the owner also gets `versions` and `last_dry_run`; another account sees a public rule at its published version). `POST /custom-rules/{rule_id}/enabled` body `{"enabled": bool, "reason"}`; `/publish`, `/unpublish`, `/retire` body `{"reason"}`; `/adopt` and `/unadopt` body `{}`. A rule is private to its author until published; a public rule is immutable (a change is a new version that stays private until published again); nothing public changes anyone's results until they adopt it; adopting pins the version. An administrator can unpublish any public rule (change log action `admin_unpublish`). Unpublishing ends every adoption (logged `unadopt`), and after an `admin_unpublish` publishing again answers 409 `withdrawn_by_admin`. `GET /custom-rules/changes?rule_id=&limit=` returns `{"changes": [{"id", "rule_id", "at", "actor", "reason", "action", "version", "before", "after"}]}`, newest first.

**Tickets.** `GET /custom-rules/tickets` returns `{"tickets": [Ticket]}`. `POST /custom-rules/tickets` body `{"draft_id"}` or `{"text"}` (exactly one) plus optional `"severity"` returns `{"ticket": Ticket, "message": "This will be submitted to the tech team (ticket crt_...).", "existing": bool}` (201, or 200 when the draft already had one). A refused draft or text is 409 `draft_refused`. No model is called. A ticket holds the intent, the wanted severity, the documents involved, what is missing, the officer's own words, who asked and when, status `submitted`.

```ts
type CustomSeverity = "info" | "low" | "medium";
type DraftStatus = "ready" | "needs_clarification" | "ambiguous" | "partially_supported" | "unsupported" | "refused";
type TemplateId = "field_vs_constant" | "date_window" | "field_missing" | "fs_ratio";
interface DraftResponse { draft_id: string; turn: number; turns_left: number; status: DraftStatus; understood: string;
  spec: CustomRuleSpec | null; spec_hash: string | null; readback: string | null; candidates: CandidateView[];
  questions: Question[]; assumptions: string[]; phrase_map: { phrase: string; slot: string | null }[];
  unsupported_parts: { phrase: string; reason: UnsupportedReason; detail?: string }[]; closest_templates: TemplateId[];
  will_check: string[]; will_not_check: string[]; refusal: { reason: RefusalReason; message: string } | null;
  duplicates: DuplicateNote[]; ideas: Idea[]; ticket: TicketRef | null; notes: string[]; dry_run: DryRunResult | null;
  save_allowed: boolean; needs_narrower_ack: boolean; edit_of: string | null; model_calls: number }
```
The full set of types (`CustomRuleSpec`, `Question`, `DuplicateNote`, `Idea`, `DryRunResult`, `CustomRuleView`, `AdoptedRuleView`, `SharedRuleView`, `CustomTicket`) is in `frontend/src/lib/types.ts`.

**Error codes** (besides the common ones in section 1): 404 `feature_off`; 403 `account_required`; 404 `not_found` (rule, draft or ticket not visible); 403 `forbidden` (a visible rule of another account: publish, retire, enable); 422 `bad_answer`; 409 `draft_closed`, `draft_turn_limit`, `draft_busy`, `no_spec`, `dry_run_required`, `duplicate_builtin`, `duplicate_custom`, `rule_limit`, `rule_retired`, `already_public`, `not_public`, `own_rule`, `already_adopted`, `not_adopted`, `draft_refused`; 422 `draft_not_ready`, `narrower_not_accepted`, `warnings_not_confirmed`, `no_change`, `name_required`, `reason_required`; 429 `user_budget_used` (this account's daily model budget is used; authoring only), `site_budget_used`, `rate_limited`; 503 `authoring_unavailable` (no usable model: the message says the idea can still go to the tech team).

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
    "counting": "Each finding counts once, however many flags or documents it covers.",
    "model": "legacy",
    "model_version": "1",
    "combination": "sum",
    "note": "Points per finding by severity (high 30, medium 15, low 5), added up and capped at 100. The weights are starting guesses, not measured. The score orders the queue; it is not a probability of fraud.",
    "ordering_signal_only": true
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
Score keys: `weights`, `cap`, `bands` and `counting` are as before. `model` (`legacy` or `calibrated`), `model_version`, `combination` (`sum` or `noisy_or`), `note` and `ordering_signal_only` (always true) are additive. With `GLDB_SCORE_MODEL=calibrated`, `combination` is `noisy_or`, `counting` describes how findings are combined, `weights` stays the per-severity default used for a rule the weights file does not name, and `family_discount`, `family_cap`, `weights_version`, `weights_source` and `fitted_on_synthetic` are added; `note` then says the weights were fitted on synthetic data and that the score is an ordering signal, not a probability. A finding's `weight` is then the points it would score alone (at least 1 for anything but `info`), so finding weights no longer add up to `score.value`. `score.value` stays an integer 0 to 100 with the same bands.

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
  | "bank_statement" | "invoice" | "receipt" | "financial_statement" | "contract" | "purchase_order"
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
