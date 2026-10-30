# Data model

Exact contracts for `backend/app/core/models.py`, `core/schemas.py`, `core/context.py` and `core/registry.py`. Check authors, extractors, the generator, scoring, the API and the frontend all use these names verbatim. See [architecture.md](architecture.md) for where each file lives and [api.md](api.md) for the JSON over HTTP. Plan refs point to the master plan (not included in this repo) (8.3 is the base contract).

Changes are Contract requests (architecture.md section 9). Fictitious data only.

## 1. Conventions

| Topic | Rule |
|---|---|
| Base class | `class Strict(BaseModel): model_config = ConfigDict(extra="forbid")`. Every model below derives from `Strict` |
| Ids | `case_id = "c_" + uuid4().hex[:12]`; `file_id = "f<n>"` and `document_id = "d<n>"`, `n` = 1-based ordinal assigned in upload / document order within the case and never reused or renumbered within a case version; `finding_id = "fnd_<n>"`; `ReviewEntry.id = "r<n>"` (per case); `job_id = "j_" + uuid4().hex[:12]`. Ids contain no `.` (flag ids use `.` as separator) |
| Pages | 1-based page number **within its file**. A `Region` always carries `file_id` + `page` |
| Rows | 0-based index within the table, in printed order. The UI shows `row + 1` |
| Coordinates | Fractions of the page, origin top-left, `x,y,w,h` in `[0,1]`. Valid when `x+w <= 1.0001` and `y+h <= 1.0001` |
| Dates | `datetime.date`; JSON and `Cell.value` use ISO `YYYY-MM-DD`. Extractors normalise printed forms (`05 Mar 2026`, `05/03/2026`) with day-first parsing; an unparseable date gives `value=None`, `raw` kept, never a guess |
| Timestamps | `datetime` UTC, JSON ISO 8601 with `Z` (`2026-10-27T09:15:00Z`) |
| Enums | Plain lowercase snake_case strings (`Literal` types), same strings in JSON |
| Strings | `Cell.raw` max 500 chars, `Flag.evidence` max 1000, `Flag.explanation` max 300 (validators truncate with `...`) |
| Currency | ISO 4217 code in `currency` fields, default `"SGD"` when the document prints `S$`/`$` in Singapore context. Cross-document amount checks compare only equal currencies; otherwise `raise CheckSkipped("currency mismatch")` |

## 2. Amounts: Decimal in Python, decimal string in JSON (decision)

- **Python**: `decimal.Decimal`. **JSON and `Cell.value`**: a plain decimal string, no thousands separator, no currency symbol, `.` decimal point, leading `-` for negatives, e.g. `"12345.67"`, `"-120.50"`, `"0.00"`.
- Money has exactly 2 decimal places (`unit_price` up to 4, `quantity` up to 4, `percent` up to 4).
- **Why not float:** binary floats cannot represent cents exactly, so a running-balance chain of 300 rows can drift past the 1.00 tolerance and a tamper of `0.01` can hide or appear spuriously. **Why not integer cents:** it forces every reader, the UI and the LLM schema to know the scale, and `unit_price` and percentages need more places. **Why not a JSON number:** JavaScript parses it as a double and silently loses precision. A string round-trips exactly in both languages.
- Equality uses tolerance, never `==`: `approx_eq(a, b, tol=settings.money_tolerance)` in `core/money.py`. Default tolerance `Decimal("1.00")` (plan 4.1, 4.2).
- USD cost figures (`Cost.usd`, `StageRecord.cost_usd`) are `Decimal` serialised as strings with up to 6 decimal places.
- Statement and invoice amounts are **magnitudes** (debit and credit columns are both positive). Balances are signed (overdraft `-120.50`). Financial statement sign rules are in section 6.

```python
# core/money.py
MONEY = Decimal("0.01")
def parse_money(text: str | None) -> Decimal | None: ...
    # accepts "1,234.56", "S$1,234.56", "SGD 1,234.56", "$ 1234.5", "(1,234.56)" -> negative,
    # trailing " DR" -> negative, trailing " CR" -> positive. Returns None for "", "-", "N/A".
    # Returns None whenever a letter touches a digit outside a recognised currency prefix or
    # a space-separated CR/DR suffix, so "S8222222A", "T08LL1234D", "12A" are NOT amounts (plan 8.5).
def to_decimal(value: str | None) -> Decimal | None: ...     # canonical string -> Decimal
def fmt_money(d: Decimal) -> str: ...                         # quantize to 0.01, format(d, "f")
def approx_eq(a: Decimal, b: Decimal, tol: Decimal = Decimal("1.00")) -> bool: ...
```

## 3. Literal types and label sets (`core/models.py`)

```python
UseCase     = Literal["kyc", "aml", "loan"]
Severity    = Literal["high", "medium", "low", "info"]
FlagStage   = Literal["forensics", "rules", "crossdoc"]
StageName   = Literal["ingest", "classify", "extract", "forensics", "rules", "crossdoc", "score"]
StageStatus = Literal["pending", "running", "ok", "failed", "skipped"]
DocType     = Literal["bank_statement", "invoice", "financial_statement", "contract", "purchase_order",
                      "company_profile", "certificate_of_incorporation", "ownership_chart",
                      "shareholder_register", "source_of_funds_declaration", "national_id", "passport",
                      "boilerplate", "other"]
DocStatus   = Literal["ok", "partial", "failed", "received_not_checked"]
CaseStatus  = Literal["processing", "open", "awaiting_documents", "closed", "error"]
Verdict     = Literal["approve", "request_documents", "refer_edd", "escalate_aml", "reject_or_escalate"]
Lane        = Literal["edd", "aml", "loan", "fast"]
Band        = Literal["low", "medium", "high"]
CellKind    = Literal["text", "money", "decimal", "percent", "int", "date", "id", "bool", "enum"]
CellSource  = Literal["text_layer", "llm_cheap", "llm_strong", "derived"]
Disposition = Literal["open", "confirmed", "dismissed", "pending_second_approval"]

SCORE_WEIGHTS = {"high": 30, "medium": 15, "low": 5, "info": 0}      # plan 8.3; recalibrated from the eval
BAND_THRESHOLDS = {"medium": 30, "high": 60}                          # low < 30, medium 30-59, high >= 60

C1_LABELS = tuple(DocType.__args__)       # classifier labels = DocType values (plan's coarse "id_document" is split into national_id and passport)
C3_LABELS = ("dbs", "ocbc", "uob", "other_bank", "software_generated")            # issuer
C4_LABELS = ("trade_receipt", "customer_payment", "financing_proceeds", "owner_transfer",
             "inter_company", "refund", "other")                                   # credit category
C5_LABELS = ("logistics", "trading", "manufacturing", "professional_services",
             "crypto_virtual_assets", "gambling", "money_services", "other")      # counterparty activity
```

`doc_type="boilerplate"` and `"other"` documents get `status="received_not_checked"`, and so do recognised types without an extractor (plan 0.4 "○ items"). They are listed in the case view.

## 4. Models

```python
UsdDecimal = Annotated[Decimal, PlainSerializer(lambda d: format(d, "f"), return_type=str, when_used="json")]

class Region(Strict):
    file_id: str
    page: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    label: str | None = None             # short caption shown on the page overlay

class Cell(Strict):                      # one value with provenance; also used for Document.fields
    value: str | None                    # CANONICAL string per section 2 and the kind (None = not found / empty)
    raw: str | None = None               # exactly as printed (max 500 chars), None if not read
    kind: CellKind = "text"
    box: Region | None = None            # where it sits on the page (feeds highlights)
    confidence: float | None = None      # 0..1 from model or parser; None for text-layer parse
    source: CellSource = "text_layer"
    verified: bool | None = None         # value found in the text layer words; None = no text layer to compare
    alt_raw: str | None = None           # the other reading when cheap and strong model disagree (plan 6 lever 5)
    # helpers (methods, not fields)
    def money(self) -> Decimal | None: ...      # kind money/decimal/percent -> Decimal
    def date_(self) -> date | None: ...
    def int_(self) -> int | None: ...
    def text(self) -> str: ...                  # value or ""

class Annotation(Strict):                # per-row model or parser label (C4, C5, counterparty)
    label: str
    probability: float = Field(ge=0, le=1)
    classifier: str
    version: str

class Row(Strict):
    index: int                           # 0-based, printed order
    cells: dict[str, Cell]               # EVERY column of the table is present; empty cell = Cell(value=None)
    annotations: dict[str, Annotation] = {}
    box: Region | None = None            # union box of the row on one page

class Table(Strict):
    name: str                            # key in Document.tables
    columns: list[str]                   # exact column names, section 6
    rows: list[Row] = []
    pages: list[int] = []                # file pages the table spans
    def col(self, name: str) -> list[Cell]: ...           # same order as rows
    def values(self, name: str) -> list[str | None]: ...

class Word(Strict):                      # text-layer word (PyMuPDF), also fills OCR output when a vision model returns words
    page: int
    text: str
    x: float; y: float; w: float; h: float   # fractions of the page, origin top-left (same unit as Region)
    font: str | None = None
    size: float | None = None            # points
    line: int = 0; block: int = 0

class Classification(Strict):
    label: str
    probability: float = Field(ge=0, le=1)
    classifier: str                      # "rules", "openjev-0.8b", "llm:<model>" ...
    version: str
    distribution: dict[str, float] | None = None
    # explanation only: nothing in scoring, checks or routing reads these two
    reasons: list[str] = []              # rules cues that fired for the winning label: at most 5 items, 120 chars each, control characters removed; [] when a model decided
    rationale: str | None = None         # a hosted model's own one-sentence reason, at most 240 chars; untrusted data, shown verbatim as text

class PageInfo(Strict):
    page: int
    width_pt: float; height_pt: float
    rotation: int = 0
    has_text_layer: bool
    is_scan: bool                        # no usable text layer, content is an image
    word_count: int = 0
    classification: Classification | None = None     # C1 for this page
    error: str | None = None             # set when the page could not be read

class FileRef(Strict):
    id: str                              # "f1"
    name: str                            # sanitised original filename (display only; never used as a path)
    sha256: str
    size_bytes: int
    mime: Literal["application/pdf", "image/jpeg", "image/png"]
    pages: list[PageInfo] = []           # filled by ingest
    pdf_meta: dict[str, str] = {}        # raw PDF Info dict: Producer, Creator, CreationDate, ModDate, ... (PDF only; filled by ingest, keys without "/")
    added_in_version: int = 1

class Document(Strict):
    id: str                              # "d3"
    file_id: str
    page_start: int; page_end: int       # inclusive, 1-based, within the file
    doc_type: DocType
    classification: Classification       # C1 for the segment (aggregate of its pages)
    issuer: Classification | None = None # C3, bank statements only
    status: DocStatus = "ok"
    extractor: str | None = None         # "text_layer@1", "llm:<model>", "cascade(text_layer>llm_strong)"
    fields: dict[str, Cell] = {}         # EVERY key of DOC_SCHEMAS[doc_type].fields present (value None when not found)
    tables: dict[str, Table] = {}        # EVERY table of the schema present (rows may be empty)
    words: list[Word] = []               # text-layer words for the document's pages; excluded from default API output
    warnings: list[str] = []
    error: str | None = None
    statement: StatementLink | None = None  # only for a bank statement page that arrived as its own image and is part of a longer statement: group, state "assembled" | "incomplete" | "ambiguous", members, reason, parts, missing (ingest-and-extraction.md 4.11); left out of the JSON when None

class Flag(Strict):                      # plan 8.3
    id: str                              # "<rule>.<document_ids joined '+'>.<locator>" built only by make_flag()
    rule: str
    stage: FlagStage
    severity: Severity
    document_ids: list[str]
    table: str | None = None             # added: names the table when `row` is set
    field: str | None = None
    row: int | None = None
    region: Region | None = None         # primary highlight
    extra_regions: list[Region] = []     # added: related evidence (e.g. the other document in a cross-check)
    evidence: str                        # numbers and values, no prose padding
    explanation: str                     # one plain sentence for the analyst
    disposition: Disposition = "open"    # NEVER set by checks; materialised from review entries when a case view is built
    code_severity: Severity | None = None  # severity the check produced before a catalogue override changed it; absent from the JSON unless an override did

class Finding(Strict):                   # a group of correlated flags, counted once in the score (plan 8.4)
    id: str                              # "fnd_1"
    group_key: str                       # e.g. "missing_shareholder:<normalised name>" or "flag:<flag id>" (basic scorer)
    title: str                           # one line, shown in the queue as "top finding"
    severity: Severity                   # max over member flags
    flag_ids: list[str]
    document_ids: list[str]
    rules: list[str]
    weight: int                          # SCORE_WEIGHTS[severity], counted once
    summary: str                         # one plain sentence with the key evidence
    hard_stop: bool = False              # rule in scoring-and-queue.md; basic scorer always False

class Score(Strict):
    value: int = Field(ge=0, le=100)     # min(100, sum(finding.weight))
    band: Band                           # per BAND_THRESHOLDS
    partial: bool = False
    partial_reasons: list[str] = []

class Coverage(Strict):
    applicable: int
    ran: int
    ratio: float                         # ran / applicable; when applicable == 0: 1.0 if the case is not partial, else 0.0
    skipped: list[dict[str, str]] = []   # [{"rule": "...", "status": "skipped|error", "reason": "..."}]
    pages_total: int = 0                 # page-level silence (scoring-and-queue.md 4.6 page_counts), filled by the score stage
    pages_unchecked: int = 0             # pages in received_not_checked/failed documents, with PageInfo.error, low C1 probability, labelled other/boilerplate, or in no document

class CheckRun(Strict):
    rule: str
    stage: FlagStage
    status: Literal["ran", "skipped", "error"]
    reason: str | None = None
    code: str | None = None              # machine-readable skip reason, today only "switched_off" (check catalogue); absent from the JSON when None
    flags: int = 0
    seconds: float = 0.0

class Cost(Strict):                      # one model call (or cache hit)
    stage: StageName
    purpose: str                         # "extract:bank_statement", "classify:C1"
    model: str                           # "fake" for FakeLLMClient
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: UsdDecimal = Decimal("0")
    seconds: float = 0.0
    cache_hit: bool = False
    document_id: str | None = None
    page: int | None = None

class StageRecord(Strict):
    stage: StageName
    status: StageStatus = "pending"
    started_at: datetime | None = None
    seconds: float = 0.0
    cost_usd: UsdDecimal = Decimal("0")
    error: str | None = None             # message only, no stack
    warnings: list[str] = []
    detail: dict[str, int | float | str] = {}

class Provenance(Strict):                # plan 7.2 reproducibility
    app_version: str
    ruleset_version: str                 # "rs-" + sha256(sorted "rule@version")[:12]
    models: dict[str, str]               # {"extract_cheap": "...", "extract_strong": "...", "classifier_c1": "rules"}
    prompt_hash: str                     # sha256 over all registered system prompts (sorted by purpose)
    prompt_hashes: dict[str, str]        # per purpose
    classifier_versions: dict[str, str]  # {"C1": "rules@1", ...}
    extractor_versions: dict[str, str]   # {"bank_statement": "text_layer@1", ...}
    input_hashes: dict[str, str]         # file_id -> sha256
    settings_hash: str                   # sha256 of the Settings values that change results (tolerances, thresholds, modes)
    today: date
    flags_digest: str                    # sha256 of sorted (flag.id, severity, evidence); equal across deterministic reruns
    created_at: datetime
    catalogue: dict | None = None        # {"overrides": [{"rule","enabled","severity","revision"}...sorted by rule], "hash": sha256 of that list}; present only when check catalogue overrides existed at analysis time, absent from the JSON otherwise

class RuleOverride(Strict):              # one row of rule_overrides (section "Catalogue overrides" below)
    enabled: bool = True
    severity: Severity | None = None     # None = keep the severity the check produces
    name: str | None = None              # display-only plain text, max 80
    description: str | None = None       # display-only plain text, max 400 ("what it checks")
    note: str | None = None              # display-only plain text, max 600, internal
    updated_by: str                      # reviewer name, 2-80
    updated_at: str                      # "...Z", UTC, microseconds
    reason: str                          # 5-500
    revision: int                        # number of changes ever logged for the rule

class ReviewEntry(Strict):               # plan 7.2, 7.3; append-only
    id: str                              # "r1"
    kind: Literal["flag_decision", "verdict", "second_approval"]
    reviewer: str                        # 2-64 chars, trimmed
    decision: str                        # flag_decision: "confirm"|"dismiss"; verdict: a Verdict; second_approval: "approve"|"decline"
    reason: str                          # mandatory, trimmed length >= 5, on EVERY entry
    flag_ids: list[str] = []             # flag_decision targets (finding_id requests are expanded to flag ids)
    finding_id: str | None = None
    target_entry_id: str | None = None   # second_approval: the pending entry it countersigns
    status: Literal["effective", "pending_second_approval", "declined"] = "effective"
    case_version: int
    created_at: datetime

class Applicant(Strict):
    name: str | None = None
    uen: str | None = None               # normalised upper-case
    source: Literal["upload", "derived"] = "derived"   # "upload" = typed on the upload form

class Case(Strict):
    id: str
    reference: str | None = None         # free text from the analyst, max 80
    use_case: UseCase
    status: CaseStatus = "processing"
    version: int = 1                     # increments on rerun or added documents; older versions are snapshots
    today: date                          # injected "today" for every check (GLDB_TODAY or creation date)
    created_at: datetime; updated_at: datetime
    applicant: Applicant = Applicant()
    files: list[FileRef] = []
    documents: list[Document] = []
    flags: list[Flag] = []
    findings: list[Finding] = []
    score: Score | None = None
    coverage: Coverage | None = None
    check_runs: list[CheckRun] = []
    lane: Lane | None = None
    top_finding: str | None = None       # one-line reason for the queue
    partial: bool = False
    partial_reasons: list[str] = []
    stages: list[StageRecord] = []
    provenance: Provenance | None = None
    costs: list[Cost] = []
    review: list[ReviewEntry] = []       # loaded from review_entries
    decision: Verdict | None = None      # latest effective terminal verdict
    escalated_at: datetime | None = None # set when an escalate_aml verdict is recorded: "suspicion formed" clock (plan 7.3)

class QueueItem(Strict):                 # ranked queue row (api.md section 4)
    case_id: str; reference: str | None; use_case: UseCase; status: CaseStatus
    applicant_name: str | None; version: int
    created_at: datetime; updated_at: datetime; age_seconds: int
    score: int | None; band: Band | None
    coverage: Coverage | None            # `skipped` omitted (empty) in queue rows
    lane: Lane | None; top_finding: str | None
    findings_count: int; high_count: int
    partial: bool; decision: Verdict | None
    pending_second_approvals: int
    file_count: int
```

Scoring contract (basic scorer in `score.py`, extended by score-queue): `finding.weight = SCORE_WEIGHTS[finding.severity]`; `score.value = min(100, sum(finding.weight))`; no score point exists without a flag; `info` weighs 0; the basic scorer makes one finding per flag with `group_key = "flag:<flag.id>"`; `score.partial = case.partial`; `coverage.ratio` from `check_runs` (architecture.md section 4). Review entries never change flags, findings or score.

### 4.1 CaseContext (`core/context.py`)

```python
@dataclass(kw_only=True)
class CaseContext:
    case: Case                         # mutable only by stages (architecture.md section 3); checks treat it as read-only
    today: date                        # == case.today. THE only source of "today"
    settings: Settings
    llm: LLMClient
    registry: MockRegistry | None      # None when the registry file is absent
    fingerprints: FingerprintStore
    costs: CostMeter                   # thread-safe; budget guard
    cache: ResponseCache
    page_words: dict[tuple[str, int], list[Word]]   # (file_id, page) -> words; runtime only, never serialised
    overrides: dict[str, RuleOverride]  # check catalogue overrides read once when the analysis starts (rule -> override); empty = code defaults

    @property
    def use_case(self) -> UseCase: ...
    @property
    def applicant(self) -> Applicant: ...
    def docs(self, *types: str) -> list[Document]: ...         # documents of those doc types, in document-id order
    def doc(self, document_id: str) -> Document: ...           # KeyError if absent
    def file(self, file_id: str) -> FileRef: ...
    def file_path(self, file_id: str) -> Path: ...             # content-addressed original on disk
    def page_text(self, file_id: str, page: int) -> str: ...   # words joined in reading order

class FingerprintStore(Protocol):                              # core/fingerprints.py (SQLite-backed)
    def lookup(self, kind: str, fingerprint: str, *, exclude_case_id: str | None = None) -> list["FingerprintHit"]: ...
    def record(self, kind: str, fingerprint: str, *, case_id: str, document_id: str | None, meta: dict | None = None) -> None: ...
class FingerprintHit(Strict):
    kind: str; fingerprint: str; case_id: str; document_id: str | None; created_at: datetime; meta: dict

def invoice_fingerprint(supplier_uen: str, invoice_number: str, invoice_date: str, total: str) -> str: ...
    # sha256 hex of "inv|" + "|".join([upper(strip(uen)) with spaces removed,
    #                                  upper(number) with whitespace removed and leading zeros KEPT,
    #                                  ISO date, total quantized to 2 dp as plain string])
def invoice_key_fingerprint(supplier_uen: str, invoice_number: str) -> str: ...
    # sha256 hex of "invkey|" + uen + "|" + number, same normalisation; stored under kind "invoice" with meta {"role": "key"}
def file_sha256(path: Path) -> str: ...
# Fingerprint kinds: "file_sha256" (per file), "invoice" (full: meta role "full"; key: meta role "key"), "crop_phash".
# The pipeline records them after `score` with meta {"applicant_uen", "doc_type", "role"?}; checks only call lookup().

# core/textnorm.py (company names; checks-financial.md 3.4 is normative)
def company_key(name: str) -> str: ...
def company_match(a: str, b: str) -> bool: ...
def company_disc(name: str) -> str: ...      # "_".join(sorted(tokens of company_key(name).lower())), chars [a-z0-9_] only; "" for empty
def uen_disc(uen: str) -> str: ...           # "uen_" + lower-case alphanumerics of the UEN
COMPANY_MATCH_MIN = 0.92

# core/textlayout.py (shared by ingest, classify and extract; ingest-and-extraction.md 3.2)
def layout_text(words: Sequence[Word]) -> str: ...
def page_marker(text: str) -> tuple[int, int] | None: ...
```

`applicant` resolution (pipeline, after `extract` and before `forensics`, so every check sees it): if the upload gave `applicant_name`/`applicant_uen`, `source="upload"`; otherwise take the most frequent normalised `(company_name, uen)` among `company_profile`, `certificate_of_incorporation`, `financial_statement`; if none, the `bank_statement.account_holder` of the earliest statement; otherwise leave `None` (checks needing it `CheckSkipped`).

### 4.2 Test helpers (`core/testing.py`)

```python
FIXED_TODAY = date(2026, 10, 27)
def make_cell(value: str | None, *, kind: CellKind = "text", raw: str | None = None, box: Region | None = None, **kw) -> Cell: ...
def make_table(name: str, rows: list[dict[str, str | None]], *, annotations: list[dict[str, Annotation]] | None = None) -> Table: ...  # columns from DOC_SCHEMAS
def make_doc(doc_type: DocType, *, id: str = "d1", fields: dict[str, str | None] | None = None,
             tables: dict[str, list[dict[str, str | None]]] | None = None, file_id: str = "f1", **kw) -> Document: ...  # fills every schema key
def make_ctx(documents: Sequence[Document] = (), *, use_case: UseCase = "loan", today: date = FIXED_TODAY,
             registry: MockRegistry | None = None, llm: LLMClient | None = None, applicant: Applicant | None = None,
             files: Sequence[tuple[FileRef, bytes]] = (), fingerprints: FingerprintStore | None = None, **kw) -> CaseContext: ...
    # files: each (FileRef, bytes) is written to a temporary content-addressed folder, appended to ctx.case.files,
    # and ctx.file_path resolves it (forensics tests need real bytes). fingerprints defaults to an empty in-memory store.
TEST_UENS = ("201912345R", "202012345H", "201934567C", "53012345B")   # verified valid with stdnum.sg.uen at R0
```

### 4.3 Catalogue overrides (`rule_overrides`, `rule_changes`)

Checks are defined in code (`@check`). Officers can lay an override on top of a check (api.md section 9a): `RuleOverride` above is one row of `rule_overrides` (`enabled` stored as 0/1; no row means the code defaults). Every edit is also appended to `rule_changes` (`id` AUTOINCREMENT, `rule`, `at`, `reviewer`, `reason`, `action` in `update`/`reset`, `before` and `after` as JSON, the override object or `null`). The change log is permanent: `BEFORE UPDATE` and `BEFORE DELETE` triggers abort, as for `audit_log`; a reset deletes the `rule_overrides` row but adds a log entry. Both tables are global configuration (no case id): `delete_case` and the retention purge never touch them. `CaseContext.overrides` carries the rows read once at the start of an analysis; `Flag.code_severity`, `CheckRun.code` and `Provenance.catalogue` record what an override did to a case and are absent from the JSON when no override applied, so a case analysed without overrides serialises exactly as before. Name, description and note are display-only plain text (control and bidirectional characters stripped, whitespace collapsed); they are never given to a model or put into flags, evidence or prompts.

## 5. Document schemas (`core/schemas.py`)

```python
class FieldSpec(Strict):  kind: CellKind; required: bool = False; values: list[str] | None = None   # values for kind "enum"
class TableSpec(Strict):  columns: dict[str, FieldSpec]; row_annotations: list[str] = []; required: bool = False
class DocSchema(Strict):  fields: dict[str, FieldSpec]; tables: dict[str, TableSpec] = {}
DOC_SCHEMAS: dict[DocType, DocSchema]      # exactly the contents of section 6; boilerplate/other have empty schemas
```
`required` means "the extractor must try and a missing value is a warning", never "must be present". Every key is always present in `Document.fields` (value `None` if not found), and every table is always present in `Document.tables`.

Canonical value forms by kind: `text` trimmed, internal whitespace collapsed; `money` per section 2; `decimal` plain decimal string; `percent` percentage points as a decimal string, `"33.33"` means 33.33% (never a fraction); `int` base-10 digits; `date` ISO; `id` upper-cased with spaces removed (UEN, NRIC, GST number, passport number, account number, invoice number, PO number); `bool` `"true"`/`"false"`; `enum` one of the listed values, lower-case unless noted.

## 6. Exact keys and columns per document type

Key notation: `name (kind)`; `?` = optional/often absent. Table columns list the exact column names; every row carries every column.

### 6.1 `bank_statement`

Fields: `bank_name (text)`, `account_holder (text)`, `uen (id)?`, `account_number (id)`, `period_start (date)`, `period_end (date)`, `opening_balance (money)`, `closing_balance (money)`, `currency (enum ISO4217)`, `page_x_of_y (text)`, `total_debits (money)?`, `total_credits (money)?`.
- `page_x_of_y`: comma-separated markers for each page of the document in order, e.g. `"1/3,2/3,3/3"`; the extractor derives it from `page_chain` (if they disagree `page_chain` wins).
- `total_debits` / `total_credits`: the printed totals, if the layout prints them.

Tables:
- `transactions`: `date (date)`, `description (text)`, `debit (money)`, `credit (money)`, `balance (money)`. Exactly one of debit/credit is non-empty per row (an empty cell is `Cell(value=None)`); both are positive magnitudes; `balance` is the running balance after the row, signed. **Each of the five cells carries its own page `box`**; `Row.box` is the union. Row annotations: `c4` (credit category, **only on rows with a credit**, label in `C4_LABELS`), `c5` (counterparty activity, **on every row**, label in `C5_LABELS`), `counterparty` (label = the counterparty name parsed from `description`, `probability` 1.0 and `classifier="counterparty-parser"` when deterministic). `pages` lists the pages spanned.
- `page_chain`: `page (int)`, `page_x (int)`, `page_y (int)`, `account_number (id)`; one row per page of the document; boxes point at the "page x of y" marker.

### 6.2 `invoice`

Fields: `invoice_number (id)`, `invoice_date (date)`, `due_date (date)?`, `supplier_name (text)`, `supplier_uen (id)`, `supplier_gst_no (id)?` (format like `M21234567K`; check the IRAS format before hard-coding, plan 1.4), `buyer_name (text)`, `buyer_uen (id)?`, `currency (enum)`, `subtotal (money)`, `gst_rate (percent)?` (default `"9.00"` when GST is shown), `gst_amount (money)`, `total (money)`, `po_reference (id)?`, `contract_reference (id)?`, `payment_status (enum: paid|unpaid|partial)?`, `paid_date (date)?`.

Table `lines`: `description (text)`, `quantity (decimal)`, `unit_price (decimal)`, `gst_amount (money)?` (per-line GST, when the layout prints it; plan 1.4 requires accepting both methods), `amount (money)` (line amount before GST).

### 6.3 `financial_statement`

Fields: `company_name (text)`, `uen (id)`, `period_start (date)`, `period_end (date)` (financial year end), `currency (enum)`, `basis (enum: audited|unaudited|unknown)` (management accounts are `unaudited`, plan 0.4), `scale_printed (enum: "1"|"1000"|"1000000")` (units printed on the page; **all table values are already normalised to whole currency units**), `auditor (text)?`.

Tables `income_statement`, `balance_sheet`, `cash_flow` share columns: `line (enum)`, `label (text)` (as printed), `section (enum)`, `current (money)`, `prior (money)?` (comparative year), `is_subtotal (bool)` (printed subtotal or total, to be checked against its section's lines). One row per printed line.

Canonical `line` keys (`other` for any unmapped line; keep the printed text in `label`). `*` marks a subtotal/total (`is_subtotal=true`):

| Table | `section` values | `line` keys |
|---|---|---|
| `income_statement` | `pnl` | `revenue`, `cost_of_sales`, `gross_profit*`, `other_income`, `operating_expenses`, `finance_costs`, `profit_before_tax*`, `income_tax`, `net_profit*`, `other` |
| `balance_sheet` | `current_assets`, `non_current_assets`, `current_liabilities`, `non_current_liabilities`, `equity`, `totals` | current_assets: `cash_and_equivalents`, `trade_receivables`, `inventories`, `other_current_assets`, `total_current_assets*`. non_current_assets: `property_plant_equipment`, `other_non_current_assets`, `total_non_current_assets*`. `totals`: `total_assets*`, `total_liabilities*`, `total_liabilities_and_equity*`. current_liabilities: `trade_payables`, `borrowings_current`, `other_current_liabilities`, `total_current_liabilities*`. non_current_liabilities: `borrowings_non_current`, `other_non_current_liabilities`, `total_non_current_liabilities*`. equity: `share_capital`, `retained_earnings`, `total_equity*`. Any section may contain `other` |
| `cash_flow` | `operating`, `investing`, `financing`, `cash` | operating: `profit_before_tax`, `adjustments`, `working_capital_changes`, `net_cash_from_operating*`. investing: `capex`, `net_cash_from_investing*`. financing: `loan_proceeds`, `loan_repayments`, `net_cash_from_financing*`. `cash`: `net_change_in_cash*`, `cash_opening`, `cash_closing*`. Any section may contain `other` |

Sign conventions (extractor normalises, checks rely on them): income statement expense lines (`cost_of_sales`, `operating_expenses`, `finance_costs`, `income_tax`) are **positive magnitudes** regardless of printed brackets; `other_income` and profits are signed; balance sheet values are positive magnitudes except `retained_earnings` (signed; accumulated deficit is negative); cash flow values are signed as printed (outflows negative, brackets become `-`). The arithmetic of each identity is defined in checks-financial.md.

### 6.4 `contract`

Fields: `contract_number (id)?`, `title (text)`, `contract_date (date)`, `effective_date (date)?`, `end_date (date)?`, `seller_name (text)`, `seller_uen (id)?`, `buyer_name (text)`, `buyer_uen (id)?`, `contract_value (money)`, `currency (enum)`, `payment_terms (text)?`, `signatory_seller (text)?`, `signatory_buyer (text)?`. (Seller = invoice `supplier_*`; buyer = invoice `buyer_*`. No claim about signature authenticity, plan 0.4.)

Table `milestones` (optional): `description (text)`, `due_date (date)`, `amount (money)`.

### 6.5 `purchase_order`

Fields: `po_number (id)`, `po_date (date)`, `buyer_name (text)`, `buyer_uen (id)?`, `supplier_name (text)`, `supplier_uen (id)?`, `currency (enum)`, `subtotal (money)`, `gst_amount (money)?`, `total (money)`, `delivery_date (date)?`, `contract_reference (id)?`.
Table `lines`: same columns as invoice `lines`.

### 6.6 `company_profile` (ACRA BizFile-style)

Fields: `company_name (text)`, `uen (id)`, `entity_type (text)`, `status (text)` (e.g. `Live`), `incorporation_date (date)`, `registered_address (text)?`, `principal_activity (text)` (the declared business; input to `x_declared_vs_activity`), `ssic_code (id)?`, `paid_up_capital (money)?`, `currency (enum)?`, `retrieved_date (date)?`.
Tables: `directors`: `director_name (text)`, `id_number (id)?`, `nationality (text)?`, `position (text)?`, `appointment_date (date)?`. `shareholders`: `holder_name (text)`, `id_or_uen (id)?`, `shares (int)`, `percent (percent)`.

### 6.7 `certificate_of_incorporation`

Fields: `company_name (text)`, `uen (id)`, `entity_type (text)?`, `incorporation_date (date)`, `certificate_number (id)?`, `issue_date (date)?`, `issuer (text)?`. No tables.

### 6.8 `ownership_chart`

Fields: `company_name (text)`, `uen (id)`, `chart_date (date)?`, `ubo_threshold_percent (percent)?` (as printed on the chart).
Table `holders`: `holder_name (text)`, `holder_type (enum: individual|company)`, `id_or_uen (id)?`, `percent (percent)` (as printed on the chart), `parent_name (text)?` (empty = holds directly in the applicant; otherwise the intermediate holder it sits under), `is_ubo (bool)?` (marked UBO on the chart).
**Percent semantics (R0 decision):** for a row with `parent_name`, `percent` is the percentage held **in the parent** (not the effective stake in the applicant); effective stake = parent's effective stake x percent / 100. The generator draws charts this way and the extractor copies what is printed; `kyc_shareholdings` computes effective stakes.

### 6.9 `shareholder_register`

Fields: `company_name (text)`, `uen (id)`, `register_date (date)` (as-at date), `total_shares (int)`, `issued_capital (money)?`, `currency (enum)?`.
Table `shareholders`: `holder_name (text)`, `id_or_uen (id)?`, `shares (int)`, `percent (percent)`, `share_class (text)?`, `date_acquired (date)?`.

### 6.10 `source_of_funds_declaration`

Fields: `declarant_name (text)`, `company_name (text)?`, `uen (id)?`, `declaration_date (date)`, `declared_amount (money)`, `currency (enum)`, `origin_category (enum: sales_proceeds|salary|investment_returns|inheritance|loan|asset_sale|other)`, `origin_description (text)`, `counterparty_name (text)?` (buyer or source named), `invoice_reference (id)?`, `period_start (date)?`, `period_end (date)?` (window in which the funds were received), `receiving_account (id)?`. No tables.

### 6.11 `national_id` (NRIC / FIN)

Fields: `holder_name (text)`, `id_number (id)` (NRIC/FIN, e.g. `S1234567D`), `date_of_birth (date)`, `sex (enum: M|F)?`, `nationality (text)?`, `date_of_issue (date)?`, `expiry_date (date)?`, `address (text)?`. No tables.

### 6.12 `passport`

Fields: `holder_name (text)`, `surname (text)`, `given_names (text)`, `passport_number (id)`, `nationality (text)` (ICAO 3-letter), `issuing_country (text)` (3-letter), `date_of_birth (date)`, `sex (enum: M|F|X)`, `date_of_issue (date)?`, `date_of_expiry (date)`, `mrz_line1 (text)`, `mrz_line2 (text)` (44 characters each, exactly as printed, `<` preserved). No tables.

### 6.13 `boilerplate`, `other`

No fields, no tables. `status="received_not_checked"`.

### 6.14 Which model reads what

| Consumer | Reads |
|---|---|
| `bs_*` rules | `bank_statement.fields.*`, `transactions`, `page_chain` |
| `inv_*`, `x_invoice_*` | `invoice.fields.*`, `invoice.lines`, `contract.fields`, `purchase_order.*` |
| `fs_*`, `x_revenue_vs_credits`, `x_cash_at_year_end` | `financial_statement` tables, `transactions` + annotation `c4` |
| `x_declared_vs_activity` | `company_profile.fields.principal_activity`, `transactions` annotation `c5` and `counterparty` |
| `x_ownership_vs_registry`, `kyc_shareholdings`, `x_directors` | `ownership_chart.holders`, `shareholder_register.shareholders`, `company_profile.directors/shareholders`, `ctx.registry` |
| `x_sof_support`, `x_remitter_vs_buyer`, `bs_pass_through` | `source_of_funds_declaration.fields`, `transactions` credits and debits with `counterparty`, `invoice.fields.buyer_name` |
| `id_*`, `passport_mrz`, `date_valid` | `national_id.fields`, `passport.fields` |
| forensics | `FileRef.pdf_meta`, raw file via `ctx.file_path`, `Document.words` (fonts, sizes), `Document.issuer` |

## 7. Example (bank statement row and a flag)

```json
{"index": 17,
 "cells": {"date": {"value": "2026-03-05", "raw": "05 Mar 2026", "kind": "date", "box": {"file_id": "f1", "page": 2, "x": 0.06, "y": 0.41, "w": 0.1, "h": 0.015}, "source": "text_layer", "verified": true},
           "description": {"value": "FAST PAYMENT FROM ACME TRADING PTE LTD", "kind": "text", "source": "text_layer"},
           "debit": {"value": null, "kind": "money"},
           "credit": {"value": "12500.00", "raw": "12,500.00", "kind": "money"},
           "balance": {"value": "48210.75", "raw": "48,210.75", "kind": "money"}},
 "annotations": {"c4": {"label": "trade_receipt", "probability": 0.94, "classifier": "rules", "version": "1"},
                 "c5": {"label": "trading", "probability": 0.81, "classifier": "rules", "version": "1"},
                 "counterparty": {"label": "ACME TRADING PTE LTD", "probability": 1.0, "classifier": "counterparty-parser", "version": "1"}}}
```
```json
{"id": "bs_running_balance.d3.transactions:17", "rule": "bs_running_balance", "stage": "rules", "severity": "high",
 "document_ids": ["d3"], "table": "transactions", "field": null, "row": 17,
 "region": {"file_id": "f1", "page": 2, "x": 0.62, "y": 0.41, "w": 0.12, "h": 0.015, "label": "balance"},
 "extra_regions": [], "evidence": "previous 35710.75 + credit 12500.00 = 48210.75, printed 58210.75",
 "explanation": "The balance on this row is 10000.00 higher than the previous balance plus the deposit.", "disposition": "open"}
```

## 8. Mock registry JSON shape (`GLDB_REGISTRY_PATH`; `core/registry.py`)

One file for the whole demo, written by the generator (`samples/registry.json`), shaped like an ACRA profile so a real lookup can replace it (plan 4.6). The registry is the "truth"; fraudulent documents disagree with it.

```json
{
  "schema_version": "1",
  "generated_by": "make_bundle.py",
  "as_of": "2026-09-30",
  "entities": [
    {
      "uen": "201912345R",
      "entity_name": "Example Logistics Pte. Ltd.",
      "entity_type": "Private Company Limited by Shares",
      "status": "Live",
      "incorporation_date": "2019-04-12",
      "registered_address": "1 Fictitious Road, Singapore 000001",
      "principal_activity": "Freight forwarding and logistics",
      "ssic_code": "52291",
      "paid_up_capital": "500000.00",
      "currency": "SGD",
      "officers": [
        {"name": "Tan Wei Ming", "id_number": "S1234567D", "nationality": "SG", "position": "Director",
         "appointment_date": "2019-04-12", "cessation_date": null}
      ],
      "shareholders": [
        {"name": "Tan Wei Ming", "id_or_uen": "S1234567D", "holder_type": "individual",
         "shares": 300000, "percent": "60.00", "share_class": "Ordinary"}
      ]
    }
  ]
}
```
All monetary and percent values are strings (section 2); dates ISO; `cessation_date: null` = current officer. Models and API:

```python
class RegistryOfficer(Strict): name: str; id_number: str | None; nationality: str | None; position: str; appointment_date: date | None; cessation_date: date | None
class RegistryShareholder(Strict): name: str; id_or_uen: str | None; holder_type: Literal["individual", "company"]; shares: int; percent: str; share_class: str | None
class RegistryEntity(Strict): uen: str; entity_name: str; entity_type: str; status: str; incorporation_date: date; registered_address: str | None
    principal_activity: str; ssic_code: str | None; paid_up_capital: str | None; currency: str | None; officers: list[RegistryOfficer]; shareholders: list[RegistryShareholder]
class MockRegistry(Strict):
    schema_version: str; generated_by: str; as_of: date; entities: list[RegistryEntity]
    def lookup(self, uen: str | None) -> RegistryEntity | None: ...        # normalised exact match on uen
    def find_by_name(self, name: str) -> list[RegistryEntity]: ...         # normalised exact name match only (fuzzy matching lives in names.py)
def load_registry(path: Path) -> MockRegistry | None: ...                  # None if the file does not exist; ValueError if malformed
```
Checks that need the registry `raise CheckSkipped("registry unavailable")` when `ctx.registry is None`. When a registry **is** loaded but lacks the applicant's UEN, `x_ownership_vs_registry` emits exactly one high flag ("applicant UEN not found in registry") and `x_directors` raises `CheckSkipped("entity absent from registry")`, so the fact is counted once (checks-kyc-aml.md 4.4). Other registry lookups (an intermediate holder's UEN) that miss are simply not compared (a stated gap).
All UENs in docs, fixtures and the generator must pass `stdnum.sg.uen.is_valid` (the earlier example `201912345K` fails with `InvalidChecksum`; verified at R0).

## 9. Known gaps and honest limits

- Cells carry one box; a value that wraps over two lines has the box of its first line.
- `Document.words` exist only for text-layer pages. Scanned pages have no words, so `font_*` and `pdf_text_vs_render` do not run on them.
- Percent values are percentage points with up to 4 decimals; rounding differences of 0.01 are a tolerance question for the rules, not the model.
- No model for payslips, tax notices, board resolutions, proof of address, business licences, wealth evidence, collateral or trade documents: they classify to `other` and are listed as "received, not checked" (plan 0.4 ○ items).

## Contract requests

**W3 closure (T-integration-kyc-08):** every request in this section has a recorded outcome in tasks.md sections 4.2 and 4.2a (accepted, decided at R0, or not built and listed under Known gaps above); none is open.

R0 accepted and folded in: `Word` unit, `pdf_meta` filled by ingest, `Coverage.pages_total/pages_unchecked`, coverage 0.0 for a partial case with no runs, `invoice_key_fingerprint`, `core/textnorm.py`, `core/textlayout.py`, `make_ctx(files=..., fingerprints=...)`, chart percent-in-parent, registry-absent applicant, valid UENs, applicant resolved before `forensics`. Outcomes: tasks.md section 4.2.
