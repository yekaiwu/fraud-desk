// Pasted verbatim from docs/api.md section 12. Do not edit by hand; change api.md first (Contract request).
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

export interface Classification {
  label: string; probability: number; classifier: string; version: string; distribution?: Record<string, number> | null;
  /** Cues that fired (rules classifier), e.g. `Found "opening balance"`. Absent on an older backend. */
  reasons?: string[];
  /** A model's own one-sentence reason (LLM classifier). Generated text: show as plain text, never as authority. */
  rationale?: string | null;
}
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
  /** Present only when a check-catalogue override changed this flag's severity: the severity the code gave it. */
  code_severity?: Severity | null;
}
export interface Finding {
  id: string; group_key: string; title: string; severity: Severity;
  flag_ids: string[]; document_ids: string[]; rules: string[];
  weight: number; summary: string; hard_stop: boolean;
}
export interface Score { value: number; band: Band; partial: boolean; partial_reasons: string[] }
export interface CoverageSkip { rule: string; status: "skipped" | "error"; reason: string; code?: string | null }
export interface Coverage { applicable: number; ran: number; ratio: number; skipped: CoverageSkip[]; pages_total: number; pages_unchecked: number;
  /** Which pages no check looked at, and why. Absent on cases analysed before the backend listed them. */
  unchecked_pages?: UncheckedPage[];
}
export type UncheckedReason = "no_document" | "unreadable" | "not_checked" | "failed" | "unrecognised_type" | "low_confidence";
export interface UncheckedPage { file_id: string; page: number; reason: UncheckedReason }
export interface CheckRun { rule: string; stage: FlagStage; status: "ran" | "skipped" | "error"; reason?: string | null; code?: string | null; flags: number; seconds: number }
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
  catalogue?: CatalogueProvenance | null;
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
export interface RuleInfo {
  rule: string; stage: FlagStage; severity: Severity; applies_to: string[]; requires: string[];
  use_cases: UseCase[]; description: string; tamper: string | null; version: string;
  // Check-catalogue settings. Absent on a backend that predates editing: read them through lib/checkCatalogue helpers.
  default_severity?: Severity; effective_severity?: Severity; enabled?: boolean; override?: RuleOverride | null;
}

/** Settings an officer has changed for one check. A null field means "use the default". */
export interface RuleOverride {
  enabled: boolean; severity: Severity | null; name: string | null; description: string | null; note: string | null;
  updated_by: string; updated_at: IsoDateTime; reason: string; revision: number;
}
/** PUT /rules/{rule}/override. Only the fields that change are sent; null or "" clears severity, name, description and note. */
export interface RuleOverrideRequest {
  enabled?: boolean; severity?: Severity | null; name?: string | null; description?: string | null; note?: string | null;
  reviewer: string; reason: string;
}
/** One row of the permanent change log. */
export interface RuleChange {
  id: string | number; rule: string; at: IsoDateTime; reviewer: string; reason: string;
  action: "update" | "reset"; before: RuleOverride | null; after: RuleOverride | null;
}
/** Present on provenance only when the case was analysed with custom catalogue settings. */
export interface CatalogueProvenance {
  overrides: { rule: string; enabled: boolean; severity: Severity | null; revision: number }[]; hash: string;
}
export interface ApiError { error: { code: string; message: string; details?: Record<string, unknown> } }
export interface Health {
  status: "ok"; version: string; llm_backend: string; ruleset_version: string; checks_registered: number;
  settings?: { classifier_min_prob: number; max_upload_mb: number; max_files_per_case: number; require_second_approver: boolean };
}

/** GET /explain/method: how the score, coverage, fast lane and lanes work. Absent on an older backend. */
export interface ExplainMethod {
  score: {
    weights: Record<Severity, number>; cap: number;
    bands: { medium_from: number; high_from: number };
    counting: string;
  };
  coverage: { low_coverage_ratio: number; fast_lane_min_coverage: number; definition: string };
  fast_lane: { requires: string[] };
  hard_stop_rules: string[];
  aml_routing_rules?: string[];
  lanes: { lane: Lane; suggested_verdict: Verdict | null; how: string }[];
  ruleset_version: string;
}
