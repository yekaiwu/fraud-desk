"""All Pydantic models, Literal enums and label constants (docs/data-model.md sections 1 to 4).

Every model derives from ``Strict`` (``extra="forbid"``): a typo in a field name fails loudly.
Amounts are ``Decimal`` in Python and plain decimal strings in JSON (data-model.md section 2).
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------------------------
# Literal types and label sets (data-model.md section 3)
# --------------------------------------------------------------------------------------

UseCase = Literal["kyc", "aml", "loan"]
Severity = Literal["high", "medium", "low", "info"]
FlagStage = Literal["forensics", "rules", "crossdoc"]
StageName = Literal["ingest", "classify", "extract", "forensics", "rules", "crossdoc", "score"]
StageStatus = Literal["pending", "running", "ok", "failed", "skipped"]
DocType = Literal[
    "bank_statement",
    "invoice",
    "financial_statement",
    "contract",
    "purchase_order",
    "company_profile",
    "certificate_of_incorporation",
    "ownership_chart",
    "shareholder_register",
    "source_of_funds_declaration",
    "national_id",
    "passport",
    "boilerplate",
    "other",
]
DocStatus = Literal["ok", "partial", "failed", "received_not_checked"]
CaseStatus = Literal["processing", "open", "awaiting_documents", "closed", "error"]
Verdict = Literal["approve", "request_documents", "refer_edd", "escalate_aml", "reject_or_escalate"]
Lane = Literal["edd", "aml", "loan", "fast"]
Band = Literal["low", "medium", "high"]
CellKind = Literal["text", "money", "decimal", "percent", "int", "date", "id", "bool", "enum"]
CellSource = Literal["text_layer", "llm_cheap", "llm_strong", "derived"]
Disposition = Literal["open", "confirmed", "dismissed", "pending_second_approval"]

STAGE_ORDER: tuple[str, ...] = get_args(StageName)
FLAG_STAGE_ORDER: tuple[str, ...] = get_args(FlagStage)

SCORE_WEIGHTS: dict[str, int] = {"high": 30, "medium": 15, "low": 5, "info": 0}
BAND_THRESHOLDS: dict[str, int] = {"medium": 30, "high": 60}  # low < 30, medium 30-59, high >= 60
SCORE_CAP = 100  # compute_score returns min(SCORE_CAP, sum of finding weights)

C1_LABELS: tuple[str, ...] = get_args(DocType)
C3_LABELS = ("dbs", "ocbc", "uob", "other_bank", "software_generated")
C4_LABELS = (
    "trade_receipt",
    "customer_payment",
    "financing_proceeds",
    "owner_transfer",
    "inter_company",
    "refund",
    "other",
)
C5_LABELS = (
    "logistics",
    "trading",
    "manufacturing",
    "professional_services",
    "crypto_virtual_assets",
    "gambling",
    "money_services",
    "other",
)

RAW_MAX = 500
EVIDENCE_MAX = 1000
EXPLANATION_MAX = 300
REASONS_MAX_ITEMS = 5
REASON_MAX = 120
RATIONALE_MAX = 240


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def truncate(text: str, limit: int) -> str:
    """Truncate to ``limit`` characters, ending with ``...`` when something was cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def clean_text(text: str, limit: int) -> str:
    """One line of plain text for display: control, format (bidi, zero-width) and line-separator
    characters become spaces, whitespace is collapsed, then ``truncate``. Used for text that came
    from a document or a model, which is only ever stored and shown as data."""
    cleaned = "".join(
        " " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch for ch in text
    )
    return truncate(" ".join(cleaned.split()), limit)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _fmt_utc(value: datetime) -> str:
    value = _to_utc(value)
    if value.microsecond:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


UtcDatetime = Annotated[
    datetime,
    AfterValidator(_to_utc),
    PlainSerializer(_fmt_utc, return_type=str, when_used="json"),
]
UsdDecimal = Annotated[
    Decimal,
    PlainSerializer(lambda d: format(d, "f"), return_type=str, when_used="json"),
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------
# Geometry, cells, tables
# --------------------------------------------------------------------------------------


class Region(Strict):
    file_id: str
    page: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)
    label: str | None = None

    @model_validator(mode="after")
    def _inside_page(self) -> Region:
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("region extends beyond the page (x+w or y+h > 1.0001)")
        return self


class Cell(Strict):
    value: str | None
    raw: str | None = None
    kind: CellKind = "text"
    box: Region | None = None
    confidence: float | None = None
    source: CellSource = "text_layer"
    verified: bool | None = None
    alt_raw: str | None = None

    @field_validator("raw", "alt_raw")
    @classmethod
    def _cap_raw(cls, v: str | None) -> str | None:
        return None if v is None else truncate(v, RAW_MAX)

    def money(self) -> Decimal | None:
        """Decimal for kind money/decimal/percent (canonical string), else None."""
        if self.value is None or self.kind not in ("money", "decimal", "percent"):
            return None
        try:
            return Decimal(self.value)
        except InvalidOperation:
            return None

    def date_(self) -> date | None:
        if self.value is None or self.kind != "date":
            return None
        try:
            return date.fromisoformat(self.value)
        except ValueError:
            return None

    def int_(self) -> int | None:
        if self.value is None:
            return None
        try:
            return int(self.value)
        except ValueError:
            return None

    def text(self) -> str:
        return self.value or ""


class Annotation(Strict):
    label: str
    probability: float = Field(ge=0, le=1)
    classifier: str
    version: str


class Row(Strict):
    index: int
    cells: dict[str, Cell]
    annotations: dict[str, Annotation] = {}
    box: Region | None = None


class Table(Strict):
    name: str
    columns: list[str]
    rows: list[Row] = []
    pages: list[int] = []

    def col(self, name: str) -> list[Cell]:
        return [r.cells[name] for r in self.rows]

    def values(self, name: str) -> list[str | None]:
        return [r.cells[name].value for r in self.rows]


class Word(Strict):
    page: int
    text: str
    x: float
    y: float
    w: float
    h: float
    font: str | None = None
    size: float | None = None
    line: int = 0
    block: int = 0


# --------------------------------------------------------------------------------------
# Classification, files, documents
# --------------------------------------------------------------------------------------


class Classification(Strict):
    label: str
    probability: float = Field(ge=0, le=1)
    classifier: str
    version: str
    distribution: dict[str, float] | None = None
    # Explainability only (never read by scoring, checks or routing). ``reasons`` are the cues the
    # rules classifier saw for the winning label; ``rationale`` is a hosted model's own one-line
    # reason, stored and shown verbatim as untrusted data.
    reasons: list[str] = []
    rationale: str | None = None

    @field_validator("reasons")
    @classmethod
    def _clean_reasons(cls, v: list[str]) -> list[str]:
        cleaned = (clean_text(r, REASON_MAX) for r in v)
        return [r for r in cleaned if r][:REASONS_MAX_ITEMS]

    @field_validator("rationale")
    @classmethod
    def _clean_rationale(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return clean_text(v, RATIONALE_MAX) or None


class PageInfo(Strict):
    page: int
    width_pt: float
    height_pt: float
    rotation: int = 0
    has_text_layer: bool
    is_scan: bool
    word_count: int = 0
    classification: Classification | None = None
    error: str | None = None


class FileRef(Strict):
    id: str
    name: str
    sha256: str
    size_bytes: int
    mime: Literal["application/pdf", "image/jpeg", "image/png"]
    pages: list[PageInfo] = []
    pdf_meta: dict[str, str] = {}
    added_in_version: int = 1


class Document(Strict):
    id: str
    file_id: str
    page_start: int
    page_end: int
    doc_type: DocType
    classification: Classification
    issuer: Classification | None = None
    status: DocStatus = "ok"
    extractor: str | None = None
    fields: dict[str, Cell] = {}
    tables: dict[str, Table] = {}
    words: list[Word] = []
    warnings: list[str] = []
    error: str | None = None


# --------------------------------------------------------------------------------------
# Flags, findings, score
# --------------------------------------------------------------------------------------


class Flag(Strict):
    id: str
    rule: str
    stage: FlagStage
    severity: Severity
    document_ids: list[str]
    table: str | None = None
    field: str | None = None
    row: int | None = None
    region: Region | None = None
    extra_regions: list[Region] = []
    evidence: str
    explanation: str
    disposition: Disposition = "open"
    # The severity the check produced before a catalogue override changed it (docs/scoring-and-
    # queue.md "Catalogue overrides"). Absent (None, and left out of the JSON) unless an override
    # changed this flag's severity, so every result without overrides is byte-identical.
    code_severity: Severity | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_validator("evidence")
    @classmethod
    def _cap_evidence(cls, v: str) -> str:
        return truncate(v, EVIDENCE_MAX)

    @field_validator("explanation")
    @classmethod
    def _cap_explanation(cls, v: str) -> str:
        return truncate(v, EXPLANATION_MAX)


class Finding(Strict):
    id: str
    group_key: str
    title: str
    severity: Severity
    flag_ids: list[str]
    document_ids: list[str]
    rules: list[str]
    weight: int
    summary: str
    hard_stop: bool = False


class Score(Strict):
    value: int = Field(ge=0, le=100)
    band: Band
    partial: bool = False
    partial_reasons: list[str] = []


UncheckedReason = Literal[
    "no_document", "unreadable", "not_checked", "failed", "unrecognised_type", "low_confidence"
]


class UncheckedPage(Strict):
    """A page no check looked at, and why (``Coverage.unchecked_pages``)."""

    file_id: str
    page: int
    reason: UncheckedReason


class Coverage(Strict):
    applicable: int
    ran: int
    ratio: float
    skipped: list[dict[str, str]] = []
    pages_total: int = 0
    pages_unchecked: int = 0
    unchecked_pages: list[UncheckedPage] = []


class CheckRun(Strict):
    rule: str
    stage: FlagStage
    status: Literal["ran", "skipped", "error"]
    reason: str | None = None
    # Machine-readable skip reason; today only "switched_off" (the check was switched off in the
    # check catalogue). Left out of the JSON when None.
    code: str | None = Field(default=None, exclude_if=lambda v: v is None)
    flags: int = 0
    seconds: float = 0.0


# --------------------------------------------------------------------------------------
# Cost, stages, provenance
# --------------------------------------------------------------------------------------


class Cost(Strict):
    stage: StageName
    purpose: str
    model: str
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
    started_at: UtcDatetime | None = None
    seconds: float = 0.0
    cost_usd: UsdDecimal = Decimal("0")
    error: str | None = None
    warnings: list[str] = []
    detail: dict[str, int | float | str] = {}


class StageOutcome(Strict):
    detail: dict[str, int | float | str] = {}
    warnings: list[str] = []
    partial: bool = False
    partial_reason: str | None = None


class Provenance(Strict):
    app_version: str
    ruleset_version: str
    models: dict[str, str]
    prompt_hash: str
    prompt_hashes: dict[str, str]
    classifier_versions: dict[str, str]
    extractor_versions: dict[str, str]
    input_hashes: dict[str, str]
    settings_hash: str
    today: date
    flags_digest: str
    created_at: UtcDatetime
    # {"overrides": [{"rule", "enabled", "severity", "revision"}...], "hash": sha256} when the
    # check catalogue had overrides at analysis time; absent otherwise (docs/data-model.md).
    catalogue: dict[str, Any] | None = Field(default=None, exclude_if=lambda v: v is None)


# --------------------------------------------------------------------------------------
# Check catalogue overrides (docs/data-model.md "Catalogue overrides")
# --------------------------------------------------------------------------------------


class RuleOverride(Strict):
    """An officer's override of one check (a row of ``rule_overrides``). Checks stay defined in
    code; this layer only switches a check off, changes its severity or its display text."""

    enabled: bool = True
    severity: Severity | None = None
    name: str | None = None
    description: str | None = None
    note: str | None = None
    updated_by: str
    updated_at: str
    reason: str
    revision: int


# --------------------------------------------------------------------------------------
# Review, applicant, case, queue
# --------------------------------------------------------------------------------------


class ReviewEntry(Strict):
    id: str
    kind: Literal["flag_decision", "verdict", "second_approval"]
    reviewer: str
    decision: str
    reason: str
    flag_ids: list[str] = []
    finding_id: str | None = None
    target_entry_id: str | None = None
    status: Literal["effective", "pending_second_approval", "declined"] = "effective"
    case_version: int
    created_at: UtcDatetime


class Applicant(Strict):
    name: str | None = None
    uen: str | None = None
    source: Literal["upload", "derived"] = "derived"


class Case(Strict):
    id: str
    reference: str | None = None
    use_case: UseCase
    status: CaseStatus = "processing"
    version: int = 1
    today: date
    created_at: UtcDatetime
    updated_at: UtcDatetime
    applicant: Applicant = Applicant()
    files: list[FileRef] = []
    documents: list[Document] = []
    flags: list[Flag] = []
    findings: list[Finding] = []
    score: Score | None = None
    coverage: Coverage | None = None
    check_runs: list[CheckRun] = []
    lane: Lane | None = None
    top_finding: str | None = None
    partial: bool = False
    partial_reasons: list[str] = []
    stages: list[StageRecord] = []
    provenance: Provenance | None = None
    costs: list[Cost] = []
    review: list[ReviewEntry] = []
    decision: Verdict | None = None
    escalated_at: UtcDatetime | None = None


class QueueItem(Strict):
    case_id: str
    reference: str | None
    use_case: UseCase
    status: CaseStatus
    applicant_name: str | None
    version: int
    created_at: UtcDatetime
    updated_at: UtcDatetime
    age_seconds: int
    score: int | None
    band: Band | None
    coverage: Coverage | None
    lane: Lane | None
    top_finding: str | None
    findings_count: int
    high_count: int
    partial: bool
    decision: Verdict | None
    pending_second_approvals: int
    file_count: int


def utcnow() -> datetime:
    """Wall-clock UTC timestamp for bookkeeping (created_at, audit). Never used by checks."""
    return datetime.now(UTC)


def case_dump(case: Case) -> dict[str, Any]:
    """JSON-ready dict of a case (strings for decimals and datetimes)."""
    return case.model_dump(mode="json")
