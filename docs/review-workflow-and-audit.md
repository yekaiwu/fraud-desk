# Review workflow, audit log, case pack, retention

Build spec for the `api-backend` agent (and the review parts of the UI in [frontend.md](frontend.md)). Contracts: [architecture.md](architecture.md) (sections 1, 6, 8, 11), [data-model.md](data-model.md) (`ReviewEntry`, `Case`, `Disposition`) and [api.md](api.md) (sections 6, 8, 9, 10; error codes in section 1). This doc is the build spec for plan 7.2 and 7.3 and must not contradict them. Plan refs: 0.3 and 0.4 theme 5 and 6, 2 principle 2, 7.1 (Notice 626, STR guidance), 7.2, 7.3. Fictitious data only. Do not git commit.

**Caveat, stated once and shown in the product (section 4.13):** the mapping of verdicts to MAS and STR obligations in this document is our reading as students and is not legal advice. Items marked **[confirm]** (retention period, STR timing, UBO threshold, tipping-off) must be confirmed with a bank compliance function before any real use.

## 1. Purpose

The human decision step. The tool recommends and ranks; people approve, refer or escalate (plan principle 2). This spec makes that step enforceable and provable: who decided what and why, a second pair of eyes before a high-severity flag is waved through, an append-only audit trail, a re-run that never destroys history, an evidence pack for investigators, and data-minimisation controls (retention, delete-case, pseudonymised logs).

## 2. Scope

| In | Plan ref |
|---|---|
| Verdicts per use case, case status state machine, flag dispositions | 7.2, 7.3 |
| Mandatory reasons, second approver for dismissing high flags | 7.2 |
| Escalation timestamp ("suspicion formed" clock) | 7.3 |
| Append-only audit log, pseudonymisation | 7.2 |
| Re-run and add-documents versioning | 0.4 theme 2, 7.2 |
| Case pack (JSON and HTML), tipping-off rule | 7.2, 8.3 |
| Retention, purge, delete-case | 7.2 |
| Rule-effectiveness computation | 0.4 theme 6, 8.3 |

| Out | Why |
|---|---|
| Filing an STR, deciding legal duties | the tool never files (plan 2, 7.1); compliance decides outside the tool |
| Authentication, roles, real identity | out of scope (plan 2); reviewer is a typed name |
| Credit decision | stays with the bank (plan 7.3) |
| Applicant-facing anything | there is no applicant endpoint (section 4.12) |

## 3. Interfaces

HTTP shapes are in api.md and are not repeated; this section fixes the Python seams.

```python
# backend/app/review.py
Clock = Callable[[], datetime]                       # UTC-aware; default utc_now(); injected in tests. No datetime.now() elsewhere in this module
VERDICT_ORDER: tuple[Verdict, ...] = ("approve", "request_documents", "refer_edd", "escalate_aml", "reject_or_escalate")
VERDICTS_BY_USE_CASE: dict[UseCase, frozenset[Verdict]] = {
    "kyc":  frozenset({"approve", "request_documents", "refer_edd", "escalate_aml"}),
    "aml":  frozenset({"approve", "request_documents", "escalate_aml"}),
    "loan": frozenset({"approve", "request_documents", "escalate_aml", "reject_or_escalate"})}
VERDICT_LABELS: dict[Verdict, str] = {
    "approve": "Approve (close, reasons logged)",
    "request_documents": "Request more documents",
    "refer_edd": "Refer for EDD review",
    "escalate_aml": "Escalate to AML investigation (STR assessment)",
    "reject_or_escalate": "Reject or escalate (credit decision stays with the bank)"}
REASON_MIN, REASON_MAX = 5, 1000                     # trimmed length
REVIEWER_MIN, REVIEWER_MAX = 2, 64

class ReviewError(Exception):                        # mapped by the API to {"error": {...}}
    status: int; code: str; message: str; details: dict[str, Any]

class ReviewRequest(BaseModel):                      # discriminated union on `kind`, extra="forbid"; mirrors api.md section 6 and TypeScript ReviewRequest
    ...
class ReviewResult(BaseModel):                       # api.md "201 Created" body
    entry: ReviewEntry; case: dict[str, Any]

def allowed_verdicts(case: Case) -> list[Verdict]: ...                       # [] unless status == "open" and the viewed version is current; VERDICT_ORDER filtered by use case
def flag_dispositions(case: Case, entries: Sequence[ReviewEntry]) -> dict[str, Disposition]: ...   # pure, 4.3
def open_high_flags(case: Case, dispositions: Mapping[str, Disposition]) -> list[str]: ...         # high flags whose disposition is "open" or "pending_second_approval"
def pending_entries(entries: Sequence[ReviewEntry], version: int) -> list[ReviewEntry]: ...
def materialise(case: Case, entries: Sequence[ReviewEntry]) -> Case: ...    # copy with flags[*].disposition set and case.review = entries of case.version; never writes to the DB

class ReviewService:
    def __init__(self, db: Database, audit: "AuditLog", settings: Settings, *, clock: Clock = utc_now): ...
    def submit(self, case_id: str, req: ReviewRequest) -> ReviewResult: ...  # 4.2
    def entries(self, case_id: str, *, version: int | None = None) -> list[ReviewEntry]: ...   # oldest first; version=None = all versions

# backend/app/audit.py
def case_ref_hash(case_id: str) -> str: ...          # "h_" + sha256(("gldb-audit|" + case_id).encode()).hexdigest()[:16]
def scrub(text: str, *, max_len: int = 200) -> str: ...        # replaces NRIC/FIN [STFGM]\d{7}[A-Z], UEN-like tokens, e-mail addresses with "[id]"; collapses whitespace; truncates
def redact_flag_id(flag_id: str) -> str: ...         # "x_directors.d9.-~tan_wei_ming#2" -> "x_directors.d9.-~#<sha256(disc)[:8]>"; ids without "~" unchanged
class AuditLog:
    def __init__(self, db: Database, settings: Settings, *, clock: Clock = utc_now): ...
    def write(self, *, actor: str, action: str, case_id: str | None, detail: Mapping[str, Any], conn: sqlite3.Connection | None = None) -> int: ...  # INSERT only; pass `conn` to join the caller's transaction; returns row id
    def for_case(self, case_id: str) -> list[dict]: ...      # [{"id","ts","actor","action","detail"}] oldest first; matches case_ref == case_id OR case_ref_hash(case_id)
    def pseudonymise_case(self, case_id: str, *, conn: sqlite3.Connection | None = None) -> int: ...   # the ONLY UPDATE: case_ref id -> hash, returns rows changed
def install_log_filter(settings: Settings) -> None: ...      # logging.Filter that drops/redacts names, NRICs, evidence text from records when GLDB_PSEUDONYMISE_LOGS

# backend/app/pack.py
DISCLAIMER = "Decision support only; a human decides; not legal advice"      # exact string from api.md section 8
STUDENT_CAVEAT = ("The mapping of verdicts to MAS and STR obligations is our reading as students and is not legal advice. "
                  "Retention, STR timing, UBO threshold and tipping-off points are marked [confirm] and need compliance sign-off.")
def build_pack(db: Database, case_id: str, *, version: int | None = None, clock: Clock = utc_now) -> dict[str, Any]: ...   # 4.9 JSON shape
def render_pack_html(pack: Mapping[str, Any], *, page_images: Mapping[tuple[str, int], bytes] | None = None) -> str: ...  # Jinja, autoescape on, inline CSS, no external requests

# backend/app/retention.py
def expired_case_ids(db: Database, *, older_than_days: int, now: datetime) -> list[str]: ...        # 4.10
def delete_case_with_audit(db: Database, audit: AuditLog, case_id: str, *, reviewer: str, reason: str) -> None: ...
def purge(db: Database, audit: AuditLog, *, older_than_days: int, dry_run: bool, now: datetime) -> dict[str, Any]: ...   # {"dry_run","case_ids","count"}

# backend/app/effectiveness.py
def compute_effectiveness(db: Database, *, use_case: UseCase | None = None, since: date | None = None) -> dict[str, Any]: ...   # api.md section 9 body
```

Routers (`api/review.py`, `pack.py`, `effectiveness.py`, plus the delete and retention routes in `cases.py` / an `admin` section) stay thin: parse, call the module, map `ReviewError` to the single error body.

## 4. Behaviour spec

### 4.1 Case status state machine

Statuses: `processing`, `open`, `awaiting_documents`, `closed`, `error` (data-model.md).

| From | Event | Guard | To | Side effects |
|---|---|---|---|---|
| (none) | `POST /cases` | files valid | `processing` | version 1, job queued, audit `case.created` |
| `processing` | job `succeeded` | | `open` | audit `case.analysis_finished` (partial is a flag on the case, not a status) |
| `processing` | job `failed` or restart while running | | `error` | audit `case.analysis_failed` |
| `open` | verdict `approve`, `refer_edd`, `escalate_aml`, `reject_or_escalate` | allowed for use case, gates 4.4 | `closed` | `case.decision` set; `escalate_aml` also sets `escalated_at` |
| `open` | verdict `request_documents` | | `awaiting_documents` | decision stays `null`; analysis kept |
| `open`, `awaiting_documents` | `POST /cases/{id}/files` | valid files | `processing` | snapshot, `version + 1`, audit `case.documents_added` |
| `open`, `awaiting_documents`, `error` | `POST /cases/{id}/rerun` | not `processing`, not `closed` | `processing` | snapshot, `version + 1`, audit `case.rerun` |
| `closed` | anything | | stays `closed` | `409 case_not_open` for review, files, rerun. A closed case is final; start a new case to re-examine |
| `processing` | review, delete | | unchanged | `409 case_processing` |

Review entries are accepted only when `status == "open"` (`409 case_not_open`, or `409 case_processing`). No automatic transitions exist: nothing is auto-approved, auto-rejected or auto-closed (plan principle 2).

### 4.2 `ReviewService.submit` order of checks

Deterministic order so tests can rely on the error code (each failure is audited as `review.rejected`, 4.7):
1. Body validation (FastAPI/Pydantic): shape, enums, `kind`-specific fields: `422 validation_error`. Not audited.
2. `reviewer` trimmed length 2 to 64: `422 reviewer_required`. `reason` trimmed length 5 to 1000: `422 reason_required` (missing, whitespace only, shorter than 5; longer than 1000 is `validation_error`). **Applies to every kind, including `second_approval` and the delete route.**
3. Load the case: `404 not_found` (`details.kind="case"`). Open a `BEGIN IMMEDIATE` transaction here and re-read inside it (two reviewers racing).
4. `status == "processing"`: `409 case_processing`; any other status but `open`: `409 case_not_open`.
5. `req.case_version != case.version`: `409 stale_version` (`details.current_version`).
6. Kind-specific rules 4.3 to 4.6.
7. Allocate `ReviewEntry.id = "r<n>"`, `n = 1 + count(entries for the case, all versions)`, never reused. Stored in `review_entries` with column `id = "<case_id>/<entry id>"` (the architecture.md DDL has `id` as sole primary key, so the case id prefix keeps `r1` unique across cases), `case_version`, `created_at = clock()`, `body = entry JSON`.
8. Write the audit row **in the same transaction** as the entry (and the status or `escalated_at` change). Commit. If the commit fails nothing is visible; no entry exists without its audit row.

Rejected attempts roll back the review transaction, then write their audit row in a second short transaction.

### 4.3 Flag decisions and dispositions

`flag_decision` request: exactly one of `flag_ids` (1 to 50; every id must exist in the case, else `404 not_found` with `details.kind="flag"`) or `finding_id` (expanded to that finding's `flag_ids` at write time; both the expanded `flag_ids` and `finding_id` are stored). `decision` is `confirm` or `dismiss`.

Status of the stored entry:
- `confirm`: always `effective`.
- `dismiss`: `pending_second_approval` if any targeted flag has severity `high` and `settings.require_second_approver` is true (**the whole entry** is pending, including any lower-severity flags in it, so the UI should offer high flags separately); otherwise `effective`.
- Any `flag_decision` that targets a flag whose current disposition is `pending_second_approval`: `409 flag_decision_pending` (`details.flag_ids`). The pending dismissal must be approved or declined first (avoids half-superseded entries). New code, see Contract requests.

`flag_dispositions(case, entries)` (pure): consider only entries with `case_version == case.version` and `kind == "flag_decision"`, in creation order. For each flag the **latest** entry that targets it decides:

| Latest entry | Disposition |
|---|---|
| none | `open` |
| `confirm`, effective | `confirmed` |
| `dismiss`, effective | `dismissed` |
| `dismiss`, `pending_second_approval` | `pending_second_approval` |
| `dismiss`, `declined` | `open` (a declined dismissal returns the flag to `open`, not to an earlier state: simple and conservative, matches api.md) |

Dispositions never carry over to a new case version: after a rerun every flag starts `open` and decisions from older versions stay visible in the log tagged with their version. `Flag.disposition` is never written by checks; `GET /cases/{id}` materialises it (`materialise`). Finding-level state is derived by the UI from member flags.

### 4.4 Verdicts and gates

`verdict` request: `decision` must be in `VERDICTS_BY_USE_CASE[case.use_case]`, else `422 verdict_not_allowed` (`details.allowed`). Effects:

| Verdict | Gate | Result |
|---|---|---|
| `approve` | every `high` flag is `confirmed` or `dismissed`; a pending dismissal counts as open. Else `409 high_flags_open` with `details.flag_ids` | `closed`, `decision="approve"` |
| `request_documents` | none | `awaiting_documents`; the case can then take `POST /cases/{id}/files` |
| `refer_edd` | `kyc` only | `closed`, `decision`, lane for the EDD team (plan 7.3) |
| `escalate_aml` | none (all use cases: false documents at onboarding are an STR red flag, plan 7.1) | `closed`, `decision`, `escalated_at = entry.created_at` |
| `reject_or_escalate` | `loan` only | `closed`, `decision`; the credit decision stays with the bank (plan 7.3) |

Not gated on purpose: `refer_edd`, `escalate_aml` and `reject_or_escalate` are allowed with open flags or pending dismissals (an escalation must never be blocked by paperwork). On any closing verdict the pending dismissals stay `pending_second_approval`, are shown as "unresolved at close" in the pack and count as `pending` in effectiveness. The verdict never changes flags, findings or score. The audit detail records the case's `score`, `band`, `partial` and `coverage_ratio` at decision time, so approving a partial case is visible afterwards. The UI warns about it (frontend.md section 8) but the API allows it with a reason.

### 4.5 Second approver

- Applies only to dismissing a `high` flag, when `GLDB_REQUIRE_SECOND_APPROVER` is true (default). With the setting false dismissals are `effective` at once and `second_approval` entries are rejected (`409 entry_not_pending`).
- `second_approval` request: `target_entry_id` must name an entry of this case and version with `status="pending_second_approval"` (`404 not_found` `details.kind="entry"` if unknown; `409 entry_not_pending` if not pending). `reviewer` must differ from the target's reviewer after `strip().casefold()`: `409 second_approver_must_differ`.
- `approve`: target becomes `effective` (flags `dismissed`). `decline`: target becomes `declined` (flags `open`). The second-approval entry itself is stored `effective`. The only permitted UPDATE on `review_entries` is this `status` change of a pending entry (architecture.md section 6).
- A `reason` is mandatory for the countersigner too.
- Honest limit: "different reviewer" means a different typed name (no authentication, api.md section 15). The control proves a second *decision was recorded*, not a second *person*.

### 4.6 Escalation timestamp (plan 7.3, 7.1)

`case.escalated_at` is the `created_at` of the `escalate_aml` entry, taken from the injected clock in UTC, set in the same transaction as the verdict, set once, never edited. It is the "suspicion formed" clock. Display (case page, pack): the timestamp and elapsed time since it, nothing more. The tool does **not** compute a filing deadline or file anything. Context for the reader, **[confirm]**: Ministry of Law guidance cites five business days from forming suspicion (one for high-risk cases) and MAS has proposed a five-business-day cap (plan 7.1); the compliance function decides about an STR outside the tool. `reject_or_escalate` does not stamp the clock (it is a credit outcome; an analyst who suspects fraud uses `escalate_aml`).

### 4.7 Audit log (append-only)

Table `audit_log` (architecture.md section 6): `id INTEGER PRIMARY KEY AUTOINCREMENT`, `ts` (ISO 8601 UTC `Z`, from the clock), `actor` (reviewer name, or `system`, or `anonymous` for uploads), `action`, `case_ref`, `detail` (JSON).

- **Append-only** at two levels. Code: `audit.py` contains only `INSERT` and `SELECT`, plus the single `UPDATE audit_log SET case_ref = ? WHERE case_ref = ?` inside `pseudonymise_case`; a test greps the source for other `UPDATE audit_log` / `DELETE FROM audit_log`. Database: triggers `BEFORE DELETE` (always abort) and `BEFORE UPDATE` (abort unless only `case_ref` changes) in the DDL, see Contract requests.
- `case_ref` is the case id while the case exists; `delete_case_with_audit` rewrites it to `case_ref_hash(case_id)` for all earlier rows (4.11). `for_case` matches both forms.

| `action` | Written when | `detail` keys (ids, hashes, enums, counts only) |
|---|---|---|
| `case.created` | upload accepted | `version`, `use_case`, `file_count`, `file_sha256s` (list), `job_id` |
| `case.documents_added` | files added | `version_from`, `version_to`, `file_sha256s`, `job_id` |
| `case.rerun` | rerun | `version_from`, `version_to`, `job_id` |
| `case.analysis_finished` | job succeeded | `version`, `score`, `band`, `partial`, `coverage_ratio`, `lane`, `ruleset_version`, `flags_digest` |
| `case.analysis_failed` | job failed | `version`, `error` (scrubbed, 200) |
| `review.flag_decision` | entry stored | `entry_id`, `decision`, `status`, `flag_refs` (`redact_flag_id` of each), `finding_id`, `severities` (counts by severity), `case_version`, `reason_sha256`, `reason_len` |
| `review.second_approval` | entry stored | `entry_id`, `decision`, `target_entry_id`, `target_status_after`, `case_version`, `reason_sha256`, `reason_len` |
| `review.verdict` | entry stored | `entry_id`, `decision`, `status_before`, `status_after`, `case_version`, `score`, `band`, `partial`, `coverage_ratio`, `open_high_flags`, `escalated_at` (only for `escalate_aml`), `reason_sha256`, `reason_len` |
| `review.rejected` | a rule rejected an attempt | `kind`, `decision`, `code`, `http_status`, `case_version` (as sent), `reason_len`. Written for every 409, for `reason_required`, `reviewer_required`, `verdict_not_allowed` and the flag/entry `404`; not for generic `validation_error` and not for an unknown case |
| `pack.exported` | `GET /cases/{id}/pack` | `format`, `version` (actor `anonymous`) |
| `file.downloaded` | `GET .../files/{file_id}` | `file_sha256` (actor `anonymous`) |
| `case.deleted` | delete-case | `reason` (scrubbed, 200), `reason_sha256`, `versions`, `files`, `decision`, `had_escalation`, `trigger` (`manual`/`retention`) |
| `case.delete_failed` | delete raised after the audit row was written | `error` (scrubbed) |
| `retention.purge` | purge with `dry_run=false` | `older_than_days`, `count`, `case_ref_hashes` |

No read-access logging of case views (no identities exist to log; gap, section 11). Application logs follow the same rule via `install_log_filter` (4.8).

### 4.8 Pseudonymised logs (plan 7.2 data minimisation)

`GLDB_PSEUDONYMISE_LOGS` (default true):
- **Audit `detail` and application logs carry ids and hashes, never customer names, NRIC/FIN numbers, free-text evidence, extracted values or file names.** The `actor` is a staff name and stays (accountability).
- The free-text `reason` lives only in `review_entries` (case data, removed with the case). Audit stores `reason_sha256` and `reason_len`. With the setting `false`, audit also stores `reason` (scrubbed, up to 1000) for demos.
- Flag ids can embed a discriminator that is a person's name (`x_directors.d9.-~tan wei ming`), so audit and logs use `redact_flag_id`. Finding `group_key`, `Flag.evidence`, `Cell.raw` and applicant fields are never logged.
- The only clear-text reason written to the audit log is the delete-case reason (needed after the case is gone); it goes through `scrub` and the UI tells the reviewer not to type customer names or ID numbers there. Names cannot be scrubbed reliably (section 11).
- Log records: key=value with `case_id`, `rule`, counts, `X-Request-Id`; never the request body. A logging filter (`install_log_filter`) redacts anything matching the NRIC/FIN or UEN patterns as a backstop.

### 4.9 Re-run and add-documents versioning (plan 0.4 theme 2, 7.2)

- `POST /cases/{id}/rerun` and `POST /cases/{id}/files` call `Database.save_case(case, snapshot_version=True)`: the **current** version is copied to `case_versions` (`case_id`, `version`, `created_at`, `body`), then the live case becomes `version + 1`, `status="processing"`, `partial_reasons` cleared, analysis outputs (documents, flags, findings, score, coverage, check runs, stages, costs, provenance) are recomputed by the pipeline; `files` are kept (new ones get `added_in_version = new version`); `review_entries` are never touched; `today` is kept (reproducible).
- Review entries keep their original `case_version`. `GET /cases/{id}?version=N` returns that snapshot with `review` filtered to `case_version == N` and dispositions materialised from those entries; non-current versions are read-only (`allowed_verdicts == []`).
- `GET /cases/{id}/versions` reason: version 1 `created`; version `v > 1` is `documents_added` if any file has `added_in_version == v`, else `rerun`. `score` and `band` come from each snapshot body; the list marks `current`.
- Reproducibility (plan 7.2): a rerun with identical inputs and settings yields identical `provenance.flags_digest` for deterministic stages; the version list lets an analyst compare. Fingerprints of the new version are recorded with `exclude_case_id`, so a case never duplicates itself.
- Request `request_documents` then `POST files` is the intended loop: `open` -> `awaiting_documents` -> `processing` -> `open`.

### 4.10 Case pack (plan 8.3; api.md section 8)

`build_pack` returns this JSON (all amounts decimal strings, datetimes `Z`, dates ISO):

```json
{"pack_version": "1", "generated_at": "...", "disclaimer": "<DISCLAIMER>", "caveat": "<STUDENT_CAVEAT>",
 "confidentiality": "Internal. Do not disclose flags or this pack to the applicant (tipping-off risk) [confirm].",
 "case": {"id", "reference", "use_case", "status", "version", "today", "created_at", "updated_at", "decision", "lane", "top_finding"},
 "applicant": {"name", "uen", "source"},
 "files": [{"id", "name", "sha256", "size_bytes", "mime", "pages": 3, "added_in_version"}],
 "documents": [{"id", "file_id", "pages": "f1:1-3", "doc_type", "status", "classification": {"label", "probability", "classifier", "version"}, "issuer", "extractor", "warnings"}],
 "not_checked_documents": [{"document_id", "doc_type", "pages", "reason"}],
 "score": {"value", "band", "partial", "partial_reasons"}, "coverage": {"applicable", "ran", "ratio", "skipped": []},
 "findings": [{"id", "title", "severity", "weight", "hard_stop", "rules", "document_ids", "flag_ids", "summary"}],
 "flags": [{"id", "rule", "stage", "severity", "document_ids", "table", "field", "row", "region", "extra_regions", "evidence", "explanation", "disposition"}],
 "check_runs": [{"rule", "status", "reason", "flags"}],
 "stages": [], "cost_summary": {}, "provenance": {},
 "review": [ReviewEntry], "unresolved_at_close": ["<entry ids still pending>"],
 "escalation": {"escalated_at": null, "note": "Suspicion-formed clock. The tool does not file an STR; compliance decides outside the tool."},
 "versions": [{"version", "created_at", "score", "band", "reason", "current"}],
 "audit": [{"id", "ts", "actor", "action", "detail"}]}
```
- Excludes `Document.words`, `Cell` tables and any raw document text (size and minimisation); includes evidence and region boxes. Available unless `processing` (`409 case_processing`). `version` query reads a snapshot.
- `format=html`: one self-contained page: header with case id, version, the disclaimer, the caveat and the confidentiality line; score, band, partial reasons, coverage; findings with their flags (severity icon and word, rule, explanation, evidence as text, location, disposition); not-checked documents; review log; escalation line; provenance table; audit list. Page images: only pages that carry at least one flag region, rendered to at most 800 px wide as PNG `data:` URIs with the flag boxes drawn by Pillow (red high, amber medium, grey low/info, number labels matching the flag list); at most 20 pages, the rest listed as "image omitted". No external requests, no script. Jinja2 environment `autoescape=True`; no `|safe` anywhere (a flag evidence of `<script>alert(1)</script>` must appear as text). Print CSS: A4, one finding per block, no page break inside a flag.
- `json` response: `Content-Disposition: attachment; filename="case-<id>-v<version>.json"`. Each export writes audit `pack.exported`.

### 4.11 Retention, purge, delete-case (plan 7.2)

- `GLDB_RETENTION_DAYS` default 1825 (5 years) **[confirm against Notice 626, plan 7.1]**.
- `expired_case_ids`: cases with `status in {"closed", "error"}`, `updated_at < now - older_than_days`, and `escalated_at is None`. Live work (`open`, `awaiting_documents`, `processing`) and escalated cases are never purged automatically (suspicion records may need longer retention **[confirm]**); an escalated case can still be deleted by hand.
- `purge(dry_run=True)` (default) deletes nothing and returns ids and count. `dry_run=False` deletes each case via `delete_case_with_audit(..., reviewer="system", reason="retention")` and writes one `retention.purge` row.
- `delete_case_with_audit` (route `DELETE /cases/{id}?reviewer=&reason=`): validate reviewer and reason (same rules, `422`), `404` unknown, `409 case_processing`. Then: (1) write `case.deleted` with `case_ref = case_ref_hash(id)`; (2) `Database.delete_case` (case, versions, review entries, jobs, fingerprints, unreferenced files and renders); (3) `AuditLog.pseudonymise_case(id)` rewrites earlier rows' `case_ref` to the hash. If step 2 raises, write `case.delete_failed` and return `500 internal_error` (the case remains). Returns `204`; a second delete is `404`.
- Trade-off to state in the UI: deleting a case removes its invoice and file fingerprints, so duplicate financing against that case is no longer detected.
- Audit rows are not purged by the application in this build (gap, section 11).

### 4.12 Tipping-off rule (plan 7.2 **[confirm]**)

The applicant must never learn which checks fired or that a case is escalated. Build consequences: (1) there is no applicant-facing endpoint, e-mail, notification or status page; (2) the app sends nothing outside the process; (3) pack and case pages carry the confidentiality line; (4) the `request_documents` form shows the hint "Ask for documents in neutral terms. Do not say which checks fired."; (5) analysts' `reason` text is internal; (6) the API is meant to be bound to an internal network (no auth, api.md). Honest limit: the tool cannot stop an analyst phoning the applicant.

### 4.13 Where the caveat appears

`STUDENT_CAVEAT` and `DISCLAIMER` appear in: pack JSON and HTML (header and footer), the verdict form help text, the case page footer, the effectiveness page footer, `README.md`'s compliance paragraph. It is wording-checked by a test (string present).

### 4.14 Rule effectiveness (`compute_effectiveness`, api.md section 9)

Population: the **latest version** of each case whose `created_at` date is `>= since` (if given) and whose `use_case` matches (if given). Dispositions come from `flag_dispositions` for that version.
- `cases_total` = cases in the population; `cases_decided` = those with `decision` not null; `median_seconds_to_verdict` = median over decided cases of `(verdict entry created_at - provenance.created_at)` in seconds (null if none).
- Per rule (every registered check from `checks.registry.all_checks()`, with zeros if never fired; a fired rule that is no longer registered appears with `description="(rule no longer registered)"`): `times_fired` = flags of the rule; `cases_fired` = cases with at least one; `confirmed`/`dismissed`/`pending`/`open` = flag counts by disposition; `dismissal_rate = dismissed / (confirmed + dismissed)` (null if denominator 0, rounded to 4 dp); `median_seconds_to_decide` = median over confirmed and dismissed flags of `(deciding entry created_at - provenance.created_at)`; `cases_ran`/`cases_skipped` = count of cases whose `check_runs` have that rule `ran` / (`skipped` or `error`).
- Sorted by `times_fired` desc, then `rule`. Cost: loads case bodies in one pass; fine at demo scale.

### 4.15 Check catalogue change log (api.md section 9a)

Edits of the check catalogue (switch a check on or off, change its severity, edit its name, description and note) are not case events, so they are not in `audit_log`. They go to `rule_changes`: reviewer (2-80), reason (5-500) and the values before and after, append-only by the same kind of triggers as `audit_log`, with `GET /rules/changes` to read it. A second approver is not required. Lowering a severity reduces points only; it never relaxes a review control or the fast lane: a flag that is high now or was high under the code defaults (`Flag.code_severity == "high"`) still blocks `approve` until decided and still needs a second approver to dismiss (`findings.is_high_flag`). Raising a check to high adds both controls. Rule effectiveness (4.14) is unchanged: it still keys by rule id, `default_severity` is the code default, and a switched-off rule simply stops accumulating `cases_ran`.

## 5. Files owned

`backend/app/review.py`, `audit.py`, `pack.py`, `effectiveness.py`, `retention.py`; routers `backend/app/api/review.py`, `pack.py`, `effectiveness.py` and the delete / retention / audit routes inside `backend/app/api/cases.py` and `backend/app/api/review.py` (api-backend owns `api/**`); tests `backend/tests/test_api_review.py`, `test_api_review_state.py`, `test_api_audit.py`, `test_api_pack.py`, `test_api_retention.py`, `test_api_effectiveness.py`, `test_api_versions.py`. The pack template is an inline string constant in `pack.py` (no template file). `Database` methods are foundation's; call them, do not edit.

## 6. Test plan

All with `TestClient`, a temp SQLite file, `FakeLLMClient`, an injected fixed clock, no network. Fixture: a case built with `make_ctx` flags saved via `Database` in `status="open"` (a `high` flag `bs_running_balance`, a `medium` `inv_dates`, a `low` `bs_dates`, an `info`).

| # | Case | Expect |
|---|---|---|
| 1 | **Clean case**: open case with zero flags, `loan` | `allowed_verdicts` includes `approve`; `approve` with a 5-char reason `201`, status `closed`, `decision="approve"`; audit has `review.verdict` |
| 2 | Missing reason; reason `"abcd"`; reason `"   ok   "`; reason of exactly 5 chars | `422 reason_required` x3; the 5-char one accepted. Nothing written on failures; `review.rejected` audit row exists |
| 3 | Reviewer `"a"` and 65 chars | `422 reviewer_required` |
| 4 | Dismiss the high flag | entry `pending_second_approval`; flag `disposition` is `pending_second_approval`; `GET` shows `pending_second_approvals` has it |
| 5 | Same reviewer countersigns (`"A.Tan "` vs `"a.tan"`) | `409 second_approver_must_differ` |
| 6 | Different reviewer `approve` | `201`; target `effective`; flag `dismissed`; no verdict side effects |
| 7 | `decline` | target `declined`; flag `open` (even if it had been confirmed before) |
| 8 | Dismiss medium and low flags | immediate `effective`, no second approver needed |
| 9 | Entry with a high and a low flag, dismiss | whole entry pending; both flags pending |
| 10 | `confirm` on a flag with a pending dismissal | `409 flag_decision_pending` |
| 11 | `approve` with an open high flag; with a pending dismissal; after confirming it; after an effective dismissal | `409 high_flags_open` (with `details.flag_ids`), `409`, `201`, `201` |
| 12 | `refer_edd` on `loan`; `reject_or_escalate` on `kyc` and `aml`; `refer_edd` on `aml` | `422 verdict_not_allowed` each; `refer_edd` on `kyc` `201` |
| 13 | `escalate_aml` | `201`; `case.escalated_at` equals the entry `created_at` (fixed clock); status `closed`; `reject_or_escalate` does not set it |
| 14 | `refer_edd` with open high flags and a pending dismissal | `201` (not gated); pack lists `unresolved_at_close` |
| 15 | `request_documents`, then `POST files` with one PDF | status `awaiting_documents`, then `processing`, version 2, old snapshot in versions, review entries kept with `case_version=1`, flags of v2 all `open` |
| 16 | Stale `case_version` (1 sent, current 2) | `409 stale_version` |
| 17 | Review on `closed`, `processing` | `409 case_not_open`, `409 case_processing`; rerun on `closed` `409 case_not_open` |
| 18 | `flag_decision` with unknown flag id; both `flag_ids` and `finding_id`; neither; 51 ids | `404 not_found` (`kind=flag`); `422 validation_error` x3 |
| 19 | `finding_id` decision | expanded `flag_ids` stored; entry shows both |
| 20 | Second approver required `false` via settings | dismiss high is `effective`; `second_approval` gives `409 entry_not_pending` |
| 21 | Concurrency: two threads countersign the same pending entry | exactly one `201`, one `409 entry_not_pending` |
| 22 | Atomicity: force the audit insert to raise | no entry, status unchanged |
| 23 | Audit append-only: after a sequence of actions, attempt `UPDATE`/`DELETE` of an audit row through the connection | aborted by the triggers (skip with a note if foundation DDL lacks them); source grep test finds no forbidden SQL in `audit.py` |
| 24 | Pseudonymisation: case with applicant `"Tan Wei Ming"`, a flag id with discriminator `tan_wei_ming`, reason `"S1234567D is the director"` | no audit `detail`, log record or pack `audit` contains the name, `S1234567D` or the reason text; `reason_sha256` present; flag refs end `~#<8 hex>` |
| 25 | Delete: `DELETE` with reason `"duplicate upload S1234567D"` | `204`; `GET` `404`; second delete `404`; `case.deleted` row has `case_ref = case_ref_hash(id)`, reason scrubbed to `duplicate upload [id]`; earlier rows' `case_ref` rewritten; fingerprints and files removed; no applicant name anywhere |
| 26 | Delete while processing; missing reviewer; short reason | `409`, `422`, `422` |
| 27 | Retention: closed case 2000 days old, closed 100 days, open 2000 days, closed escalated 2000 days | dry run returns only the first; `dry_run=false` deletes it and writes `retention.purge` |
| 28 | Pack JSON keys exactly as 4.10; `processing` case | all keys present; `409 case_processing` |
| 29 | Pack HTML with evidence `<script>alert(1)</script>` and a name `"</td><b>"` | escaped in output; no `<script`; no `http://`/`https://` resource references; contains `DISCLAIMER`, `STUDENT_CAVEAT`, the confidentiality line; images are `data:` URIs of at most 800 px |
| 30 | Versions: v1 created, v2 rerun, v3 documents added | reasons `created`, `rerun`, `documents_added`; `?version=1` shows v1 review entries only |
| 31 | Rerun determinism: rerun an analysed case with identical inputs | `provenance.flags_digest` equal across versions (plan 7.2 re-run test) |
| 32 | Effectiveness: 3 cases with known confirm and dismiss counts and fixed times | counts, `dismissal_rate` (1 of 4 = `0.25`), medians, zero rows for unfired rules; null rate when 0 decided |
| 33 | Error body shape and `X-Request-Id` on every code above | single `{"error": {...}}` body |
| 34 | **Honest quirks**: a reason of `"xxxxx"` is accepted; a second approver typed as `"b.lim"` and `"B.Lim"` is the same person (rejected); a partial case can be approved with a reason (audit shows `partial: true`) | tests document current behaviour, not an ideal |

## 7. Acceptance criteria

1. `uv run pytest backend/tests/test_api_*.py` passes offline; `uv run ruff check .` clean.
2. No code path closes, approves, rejects or escalates a case without a `reviewer`, a trimmed `reason` of at least 5 characters and an audit row written in the same transaction.
3. Every error in section 4.2 to 4.6 has a stable code from api.md section 1 (plus `flag_decision_pending`) and a test.
4. Audit rows never contain names, NRIC/FIN numbers, evidence or reason text when `GLDB_PSEUDONYMISE_LOGS` is true (test 24).
5. Delete-case leaves no applicant data in the database or on disk and a hashed audit trail (test 25).
6. Pack HTML is self-contained, escaped, and carries the disclaimer and the student caveat.
7. Rerun keeps all review entries, snapshots the old version, and old versions stay viewable.

## 8. Frontend consequences (see [frontend.md](frontend.md))

The UI must show: reason counters and 5-character rule, `high` dismissals as "needs second approval", pending cards with a countersign form, the `escalated_at` clock, the verdict labels from `VERDICT_LABELS`, the tipping-off banner, the student caveat, version selector with read-only older versions, and the error-code messages of api.md section 1 plus `flag_decision_pending`.

## 9. Standards cross-check (plan 7.2 table)

| Expectation | Where enforced |
|---|---|
| Human oversight, accountability | 4.1, 4.4 (no auto outcome), 4.7 (audit) |
| Mandatory reason on every dismissal | 4.2 step 2 (all entries) |
| Second approver for dismissing high flags | 4.3, 4.5 |
| Reproducibility | 4.9, provenance (data-model.md) |
| Data minimisation, retention, delete-case, pseudonymised logs | 4.8, 4.11 |
| Tipping-off | 4.12 **[confirm]** |
| Explainability | pack (4.10), flags carry rule, evidence, explanation |

## 10. Verdict to deck outcome (plan 0.5, 7.3)

KYC bundle: `refer_edd`. AML bundle: `escalate_aml` (STR assessment outside the tool). Loan bundle: `reject_or_escalate`. These are the expected outcomes the eval checks against the lane in [scoring-and-queue.md](scoring-and-queue.md) section 4.5, not rules the tool applies.

## 11. Known gaps and honest limits

- No authentication: reviewer and second approver are free-text names; one person can type two names (api.md section 15).
- A reason's quality is not judged; five characters pass.
- No read-access logging (views, searches) because there is no identity to log.
- Audit rows are not purged and are not tamper-evident beyond the triggers (no hash chain, no external sink).
- A case in `awaiting_documents` cannot be closed directly; it must be re-opened by adding files or a rerun first (R0: the rerun is the sanctioned workaround; no direct close is built).
- Dispositions do not carry over to a new version; the analyst re-decides, even for unchanged flags.
- Pending dismissals left at close stay unresolved; the pack says so.
- Names in a delete reason or in free text cannot be scrubbed reliably; the UI warns.
- Deleting a case removes its fingerprints, weakening cross-case duplicate detection.
- Retention, 25% UBO threshold, STR timing and tipping-off are secondary-source readings **[confirm]**; this is a student reading, not legal advice.
- Rule effectiveness measures reviewer agreement, not ground truth; ground truth comes from the eval.
- No PDF case pack (HTML is print-ready); no STR filing; no scheduled periodic review.
- Review, rerun, add-documents and delete serialise on one in-process lock plus a SQLite `BEGIN IMMEDIATE` for the review transaction; `Database.save_case` of the pipeline and of `prepare_rerun` is not transactional with a concurrent review in another process (single-process build, api.md section 15).
- `GET /cases/{id}/audit?limit=&after=` (cursor) and `GET /cases/{id}/graph` (T-api-backend-12, stretch) are not built.

## Contract requests

**W3 closure (T-integration-kyc-08):** every request in this section has a recorded outcome in tasks.md sections 4.2 and 4.2a (accepted, decided at R0, or not built and listed under Known gaps above); none is open.

**R0 outcomes:** `flag_decision_pending` added to api.md; audit triggers added to the foundation DDL (architecture.md 6); log filter: `main.py` calls an optional `init(app, settings)` of each api module, and api-backend's `api/review.py::init` calls `install_log_filter` (no edit to `main.py`); rerun statuses accepted (api.md 6); closing from `awaiting_documents`: not built, rerun is the workaround; `review_entries.id` keeps the sole key holding `"<case_id>/<entry id>"`; version-filtered `load_case` accepted; audit cursor is stretch.

- **New error code** `409 flag_decision_pending` (`details.flag_ids`): a flag with a pending dismissal cannot receive another decision. Add to api.md section 1 and the frontend error map; unknown codes must still render `error.message`.
- **Audit triggers** (foundation, `core/db.py` DDL): `BEFORE DELETE ON audit_log` aborts; `BEFORE UPDATE ON audit_log` aborts unless `id`, `ts`, `actor`, `action`, `detail` are unchanged (only `case_ref` may change).
- **Log filter**: `main.py` calls `app.audit.install_log_filter(settings)` at startup (foundation owns `main.py`).
- **Rerun statuses** (api.md section 6): allow `open`, `awaiting_documents`, `error`; `closed` and `processing` give `409`.
- **Closing from `awaiting_documents`**: permit `escalate_aml` and `reject_or_escalate` (and a `withdraw`-style close) while `awaiting_documents`, or the case can only be closed after another upload cycle. Not built until accepted.
- **`review_entries.id`**: the DDL keeps `id` as sole PK, so this spec stores `"<case_id>/<entry id>"` in that column; the foundation may instead use a composite PK `(case_id, id)`; either is fine if `ReviewEntry.id` stays `r<n>`.
- **Version-filtered review**: `Database.load_case(case_id, version)` merges only entries with `case_version == version` into `Case.review` (data-model.md says "merges `review_entries`").
- **Optional**: a `GET /cases/{id}/audit?limit=&after=` cursor if audit lists grow.

**W1 api-backend (additions; none changes a frozen signature).**
- `review.ReviewError` subclasses `core.errors.ApiError` so `main.py` renders it with no extra handler; `ReviewRequest` is the discriminated-union alias (`parse_review_request()` validates a dict); `allowed_verdicts(case, *, current=True)` takes a `current` flag because a snapshot of an old version still carries `status="open"`.
- Inside `ReviewService.submit` the transaction uses `Database._denormalised` and `Database._body` (static helpers of `core/db.py`) on its own connection, because `save_case` would open a second connection (or commit a shared in-memory one) and break atomicity. If those helpers are renamed, `review._write_case` must follow.
- `audit.AuditLog.write(..., hashed_ref=True)` stores `case_ref_hash(case_id)` directly (used for `case.deleted`); `audit.read_audit(db, case_id)` is the SELECT used by the pack.
- `case.analysis_finished` / `case.analysis_failed` audit rows are written by `api/cases.py::init`, which wraps `JobRunner._run` on the application's runner (no edit to `pipeline.py`). A runner that is not the application's would not audit.
- Body that is not parseable JSON on the review and case routes is `400 bad_request` (api.md section 1) via `api/_common.py::ApiRoute`; schema violations stay `422 validation_error`.
- The audit `review.verdict` detail key `open_high_flags` is a count, not a list (flag ids can embed names).
- Effectiveness: a dismissal that needed a second approver is timed from the approving countersignature (`median_seconds_to_decide`).
- `foundation` test `backend/tests/test_main_app.py` asserts that every route answers `501`; those parametrised cases (`test_every_stub_route_answers_501_with_the_error_body`) are obsolete now that the routes exist and must be updated or removed by whoever owns that file.
