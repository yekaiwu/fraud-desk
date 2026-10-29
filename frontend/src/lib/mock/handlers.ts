/**
 * Mock API (frontend.md section 11). Mirrors the shapes and error codes of docs/api.md for the
 * calls the UI makes. In-memory; resets on reload. Not a second source of truth: it implements
 * only the review rules the demo needs and no audit, no retention, no real pack export.
 */
import { MAX_FILES_PER_CASE, MAX_UPLOAD_MB, PAGE_SIZE } from "../constants";
import { ApiErrorException } from "../errors";
import type { NewVersionResponse, VersionsResponse } from "../api";
import { isHighFlag } from "../flags";
import { DEFAULT_QUEUE_STATUS } from "../queueQuery";
import type {
  Band,
  CaseDetail,
  CaseStatus,
  CreateCaseResponse,
  EffectivenessResponse,
  ExplainMethod,
  Flag,
  Health,
  Job,
  Page,
  QueueItem,
  QueueQuery,
  ReviewEntry,
  ReviewRequest,
  ReviewResponse,
  RuleEffectiveness,
  RuleChange,
  RuleInfo,
  RuleOverride,
  RuleOverrideRequest,
  Severity,
  UseCase,
  Verdict,
} from "../types";
import {
  bandFor,
  coverage,
  EXPLAIN_METHOD,
  fileRef,
  finding,
  flag,
  jobFor,
  MOCK_NOW,
  ownershipDoc,
  provenanceFor,
  RULES,
  stageRecords,
  statementDoc,
  stmtBalance,
  stmtCredit,
  STAGES,
  ownHolder,
} from "./fixtures";
import { MOCK_ANALYSIS_MS, state } from "./state";

// ------------------------------------------------------------------ helpers

const err = (status: number, code: string, message: string, details?: Record<string, unknown>) =>
  new ApiErrorException({ code, message, details }, status);

const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

async function latency(): Promise<void> {
  if (state.latencyMs > 0) await new Promise((r) => setTimeout(r, state.latencyMs));
}

const VERDICTS_BY_USE_CASE: Record<UseCase, Verdict[]> = {
  kyc: ["approve", "request_documents", "refer_edd", "escalate_aml"],
  aml: ["approve", "request_documents", "escalate_aml"],
  loan: ["approve", "request_documents", "escalate_aml", "reject_or_escalate"],
};
const ALL_VERDICTS: Verdict[] = ["approve", "request_documents", "refer_edd", "escalate_aml", "reject_or_escalate"];

/** Canned results for a case created in mock mode, chosen by use case. */
function cannedFor(useCase: UseCase): Pick<CaseDetail, "flags" | "findings" | "lane"> {
  if (useCase === "kyc") {
    const f = flag({
      id: "x_ownership_vs_registry.d1.-", rule: "x_ownership_vs_registry", stage: "crossdoc", severity: "high", documents: ["d1"],
      region: ownHolder(1, "Holder"), evidence: "Chart lists a 25.00% holder that the register omits", explanation: "A holder on the chart does not appear in the register.",
    });
    return { flags: [f], findings: [finding("fnd_1", "Ownership chart lists a holder that the registry omits", "high", [f], "Chart vs register mismatch.")], lane: "edd" };
  }
  if (useCase === "aml") {
    const f = flag({
      id: "x_remitter_vs_buyer.d1.3", rule: "x_remitter_vs_buyer", stage: "crossdoc", severity: "high", documents: ["d1"],
      table: "transactions", row: 3, region: stmtCredit(3, 1, "Remitter"), evidence: "Remitter differs from the invoiced party", explanation: "The payer is not the invoiced party.",
    });
    return { flags: [f], findings: [finding("fnd_1", "Remitter differs from the invoice customer", "high", [f], "Payer and invoiced party differ.")], lane: "aml" };
  }
  const a = flag({
    id: "bs_running_balance.d1.transactions.8", rule: "bs_running_balance", severity: "high", documents: ["d1"],
    table: "transactions", field: "balance", row: 8, region: stmtBalance(8, 1, "Balance"),
    evidence: "Printed balance 84560.00; computed 74560.00", explanation: "The running balance does not match the movement.",
  });
  const b = flag({
    id: "x_invoice_vs_receipts.d1.total", rule: "x_invoice_vs_receipts", stage: "crossdoc", severity: "medium", documents: ["d1"],
    region: stmtCredit(1, 1, "Closest credit"), evidence: "No credit matches the invoice total", explanation: "No bank credit matches the invoice.",
  });
  return {
    flags: [a, b],
    findings: [
      finding("fnd_1", "Running balance does not add up on a bank statement row", "high", [a], "Balance mismatch."),
      finding("fnd_2", "Invoice has no matching receipt", "medium", [b], "No matching credit."),
    ],
    lane: "loan",
  };
}

/** Moves a running case to `open` once its clock has run out. Called by every read. */
function advance(): void {
  const now = Date.now();
  for (const [caseId, readyAt] of [...state.readyAt]) {
    if (now < readyAt) continue;
    state.readyAt.delete(caseId);
    const c = state.cases.get(caseId);
    if (!c || c.status !== "processing") continue;
    const canned = cannedFor(c.use_case);
    // Checks switched off in the catalogue do not run: no findings from them, and they are listed as skipped.
    const off = [...state.overrides].filter(([, o]) => !o.enabled).map(([rule]) => rule);
    canned.flags = canned.flags.filter((f) => !off.includes(f.rule));
    canned.findings = canned.findings.filter((f) => !f.rules.every((r) => off.includes(r)));
    const value = Math.min(100, canned.findings.reduce((n, f) => n + f.weight, 0));
    const cov = coverage(21, 21 - off.length, c.files.reduce((n, f) => n + f.pages.length, 0), 0, off.map((rule) => {
      const o = state.overrides.get(rule)!;
      return { rule, status: "skipped" as const, code: "switched_off", reason: `Switched off in the check catalogue by ${o.updated_by} on ${o.updated_at.slice(0, 10)}: ${o.reason}` };
    }));
    c.status = "open";
    // The mock "analysis" ignores file contents: it always attaches the canned documents and findings.
    c.documents = c.use_case === "kyc" ? [ownershipDoc("d1")] : [statementDoc("d1")];
    c.flags = canned.flags;
    c.findings = canned.findings;
    c.lane = canned.lane;
    c.score = { value, band: bandFor(value), partial: false, partial_reasons: [] };
    c.coverage = cov;
    c.stages = stageRecords([], c.files);
    c.provenance = provenanceFor(nowIso(), false);
    if (state.overrides.size > 0) {
      c.provenance.catalogue = {
        overrides: [...state.overrides].map(([rule, o]) => ({ rule, enabled: o.enabled, severity: o.severity, revision: o.revision })),
        hash: "c0".repeat(32),
      };
    }
    c.top_finding = c.findings[0]?.title ?? `No flags raised; ${cov.ran} of ${cov.applicable} checks ran`;
    c.updated_at = nowIso();
    recompute(c);
  }
}

/** Re-derives dispositions, pending approvals and allowed verdicts from the review log. */
function recompute(c: CaseDetail): void {
  const disp = new Map<string, Flag["disposition"]>();
  for (const e of c.review) {
    if (e.case_version !== c.version) continue;
    if (e.kind === "flag_decision") {
      if (e.status === "declined") continue;
      const d: Flag["disposition"] =
        e.decision === "confirm" ? "confirmed" : e.status === "pending_second_approval" ? "pending_second_approval" : "dismissed";
      for (const id of e.flag_ids) disp.set(id, d);
    }
  }
  for (const f of c.flags) f.disposition = disp.get(f.id) ?? "open";
  c.pending_second_approvals = c.review
    .filter((e) => e.status === "pending_second_approval" && e.case_version === c.version)
    .map((e) => ({ entry_id: e.id, flag_ids: e.flag_ids, reviewer: e.reviewer, created_at: e.created_at }));
  c.allowed_verdicts = c.status === "open" ? VERDICTS_BY_USE_CASE[c.use_case] : [];
}

function getOrThrow(id: string): CaseDetail {
  advance();
  const c = state.cases.get(id);
  if (!c) throw err(404, "not_found", "No such case.", { kind: "case" });
  return c;
}

/** Fixture cases age from the fixed MOCK_NOW plus time since load; cases made in this session age from real time. */
function ageSeconds(c: CaseDetail, created: number): number {
  if (c.id.startsWith("c_mock")) return (Date.now() - created) / 1000;
  return (Date.parse(MOCK_NOW) - created) / 1000 + (Date.now() - state.loadedAt) / 1000;
}

function deriveItem(c: CaseDetail): QueueItem {
  recompute(c);
  const created = Date.parse(c.created_at);
  return {
    case_id: c.id, reference: c.reference ?? null, use_case: c.use_case, status: c.status,
    applicant_name: c.applicant.name ?? null, version: c.version, created_at: c.created_at, updated_at: c.updated_at,
    age_seconds: Math.max(0, Math.round(ageSeconds(c, created))),
    score: c.score?.value ?? null, band: c.score?.band ?? null, coverage: c.coverage ?? null,
    lane: c.lane ?? null, top_finding: c.top_finding ?? null,
    findings_count: c.findings.filter((f) => f.severity !== "info").length,
    high_count: c.findings.filter((f) => f.severity === "high").length,
    partial: c.partial, decision: c.decision ?? null,
    pending_second_approvals: c.pending_second_approvals.length, file_count: c.files.length,
  };
}

const STATUS_RANK: Record<CaseStatus, number> = { open: 0, processing: 1, awaiting_documents: 2, closed: 3, error: 4 };

function sortItems(items: QueueItem[], q: QueueQuery): QueueItem[] {
  const sort = q.sort ?? "priority";
  const dir = (q.order ?? "desc") === "asc" ? 1 : -1;
  const cmp = (a: QueueItem, b: QueueItem): number => {
    switch (sort) {
      case "score": return dir * ((a.score ?? -1) - (b.score ?? -1));
      case "coverage": return dir * ((a.coverage?.ratio ?? 2) - (b.coverage?.ratio ?? 2));
      case "created": return dir * a.created_at.localeCompare(b.created_at);
      case "age": return -dir * a.created_at.localeCompare(b.created_at);
      default:
        return (
          STATUS_RANK[a.status] - STATUS_RANK[b.status] ||
          (b.score ?? -1) - (a.score ?? -1) ||
          (a.coverage?.ratio ?? 2) - (b.coverage?.ratio ?? 2) ||
          a.created_at.localeCompare(b.created_at)
        );
    }
  };
  return [...items].sort(cmp);
}

function checkReviewer(reviewer: unknown): string {
  const r = typeof reviewer === "string" ? reviewer.trim() : "";
  if (r.length < 2 || r.length > 64) throw err(422, "reviewer_required", "A reviewer name of 2 to 64 characters is required.", { field: "reviewer" });
  return r;
}
function checkReason(reason: unknown): string {
  const r = typeof reason === "string" ? reason.trim() : "";
  if (r.length < 5) throw err(422, "reason_required", "A reason of at least 5 characters is required.", { field: "reason" });
  if (r.length > 1000) throw err(422, "validation_error", "The reason is too long.", { errors: [{ field: "reason", message: "at most 1000 characters" }] });
  return r;
}

const MAGIC_OK = (f: File): boolean => /^application\/pdf$|^image\/(jpeg|png)$/.test(f.type) || /\.(pdf|jpe?g|png)$/i.test(f.name);

function checkFiles(files: File[]): void {
  if (files.length === 0) throw err(422, "no_files", "At least one file is required.");
  if (files.length > MAX_FILES_PER_CASE) throw err(422, "too_many_files", `At most ${MAX_FILES_PER_CASE} files are allowed.`);
  for (const f of files) {
    if (f.size > MAX_UPLOAD_MB * 1024 * 1024) throw err(413, "file_too_large", `${f.name} is larger than ${MAX_UPLOAD_MB} MB.`);
    if (!MAGIC_OK(f)) throw err(415, "unsupported_media_type", `${f.name} is not a PDF, JPEG or PNG.`);
  }
}

function nextFileId(existing: { id: string }[], useCase: UseCase): string {
  const order = useCase === "kyc" ? ["f3", "f1", "f2"] : ["f1", "f2", "f3"];
  const used = new Set(existing.map((f) => f.id));
  const free = order.find((id) => !used.has(id));
  if (free) return free;
  let n = 4;
  while (used.has(`f${n}`)) n++;
  return `f${n}`;
}

function startJob(caseId: string, kind: Job["kind"]): string {
  const id = `j_mock${state.nextJob++}`;
  state.jobs.set(id, { id, caseId, kind, startedAt: Date.now() });
  state.readyAt.set(caseId, Date.now() + MOCK_ANALYSIS_MS);
  return id;
}

// ------------------------------------------------------------------ handlers

export async function getHealth(): Promise<Health> {
  await latency();
  return {
    status: "ok", version: "0.1.0-mock", llm_backend: "mock", ruleset_version: "rs-mock", checks_registered: RULES.length,
    settings: { classifier_min_prob: 0.7, max_upload_mb: MAX_UPLOAD_MB, max_files_per_case: MAX_FILES_PER_CASE, require_second_approver: true },
  };
}

export async function getQueue(q: QueueQuery = {}): Promise<Page<QueueItem>> {
  await latency();
  advance();
  const statuses = q.status?.length ? q.status : DEFAULT_QUEUE_STATUS;
  const needle = q.q?.trim().toLowerCase();
  let items = [...state.cases.values()].map(deriveItem).filter((i) => {
    if (!statuses.includes(i.status)) return false;
    if (q.use_case?.length && !q.use_case.includes(i.use_case)) return false;
    if (q.lane?.length && (!i.lane || !q.lane.includes(i.lane))) return false;
    if (q.band?.length && (!i.band || !q.band.includes(i.band as Band))) return false;
    if (q.min_score !== undefined && (i.score ?? -1) < q.min_score) return false;
    if (q.partial !== undefined && i.partial !== q.partial) return false;
    if (needle && !`${i.reference ?? ""} ${i.applicant_name ?? ""}`.toLowerCase().includes(needle)) return false;
    return true;
  });
  items = sortItems(items, q);
  const page = Math.max(1, q.page ?? 1);
  const size = Math.min(100, q.page_size ?? PAGE_SIZE);
  const start = (page - 1) * size;
  return { items: items.slice(start, start + size), total: items.length, page, page_size: size, has_next: start + size < items.length };
}

export async function createCase(form: FormData): Promise<CreateCaseResponse> {
  await latency();
  const use_case = form.get("use_case");
  if (use_case !== "kyc" && use_case !== "aml" && use_case !== "loan") {
    throw err(422, "validation_error", "use_case must be kyc, aml or loan.", { errors: [{ field: "use_case", message: "must be kyc, aml or loan" }] });
  }
  const files = form.getAll("files").filter((x): x is File => typeof x !== "string");
  checkFiles(files);
  const text = (k: string, max: number) => {
    const v = form.get(k);
    return typeof v === "string" && v.trim() ? v.trim().slice(0, max) : null;
  };
  const id = `c_mock${state.nextCase++}`;
  // File ids pick the bundled page image (f1 statement, f2 invoice, f3 ownership chart), so a KYC
  // case puts its first file on f3 and the canned region lands on the right drawing.
  const refs: CaseDetail["files"] = [];
  files.forEach((f) => refs.push(fileRef(nextFileId(refs, use_case), f.name.slice(0, 120), 1, f.type === "image/png" ? "image/png" : f.type === "image/jpeg" ? "image/jpeg" : "application/pdf")));
  const created = nowIso();
  state.cases.set(id, {
    id, reference: text("reference", 80), use_case, status: "processing", version: 1, today: created.slice(0, 10), created_at: created, updated_at: created,
    applicant: { name: text("applicant_name", 120), uen: text("applicant_uen", 20)?.toUpperCase() ?? null, source: "upload" },
    files: refs, documents: [], flags: [], findings: [], score: null, coverage: null, check_runs: [], lane: null, top_finding: null,
    partial: false, partial_reasons: [], stages: [], provenance: null, costs: [], review: [], decision: null, escalated_at: null,
    pending_second_approvals: [], allowed_verdicts: [], not_checked_documents: [],
    cost_summary: { total_usd: "0.000000", calls: 0, cache_hits: 0, seconds_by_stage: {}, usd_by_stage: {} },
  });
  const job = startJob(id, "analyse");
  return { case_id: id, job_id: job, status_url: `/jobs/${job}`, case_url: `/cases/${id}` };
}

export async function getCase(id: string, version?: number): Promise<CaseDetail> {
  await latency();
  const c = getOrThrow(id);
  if (version !== undefined && version !== c.version) {
    const snap = state.history.get(id)?.find((s) => s.version === version);
    if (!snap) throw err(404, "not_found", "No such version.", { kind: "version" });
    return structuredClone({ ...snap.detail, allowed_verdicts: [], pending_second_approvals: [] });
  }
  recompute(c);
  return structuredClone(c);
}

export async function getVersions(id: string): Promise<VersionsResponse> {
  await latency();
  const c = getOrThrow(id);
  const old = (state.history.get(id) ?? []).map((s) => ({
    version: s.version, created_at: s.detail.created_at, score: s.detail.score?.value ?? null, band: s.detail.score?.band ?? null,
    reason: s.reason, current: false,
  }));
  return {
    versions: [
      ...old,
      { version: c.version, created_at: c.updated_at, score: c.score?.value ?? null, band: c.score?.band ?? null, reason: state.currentReason.get(id) ?? "created", current: true },
    ].sort((a, b) => b.version - a.version),
  };
}

export async function getJob(id: string): Promise<Job> {
  await latency();
  advance();
  const meta = state.jobs.get(id);
  if (!meta) throw err(404, "not_found", "No such job.", { kind: "job" });
  if (meta.fixed) return structuredClone(meta.fixed);
  if (meta.startedAt === null) {
    // the fixture "processing" case: its clock starts the first time somebody looks at the job
    meta.startedAt = Date.now();
    state.readyAt.set(meta.caseId, meta.startedAt + MOCK_ANALYSIS_MS);
  }
  const elapsed = Date.now() - meta.startedAt;
  if (elapsed >= MOCK_ANALYSIS_MS) return jobFor(id, meta.caseId, "succeeded", STAGES.length, null, meta.kind);
  const stage = Math.min(STAGES.length - 1, Math.floor((elapsed / MOCK_ANALYSIS_MS) * STAGES.length));
  return jobFor(id, meta.caseId, elapsed < 100 ? "queued" : "running", stage, null, meta.kind);
}

export async function getReviewLog(id: string): Promise<{ entries: ReviewEntry[] }> {
  await latency();
  return { entries: structuredClone(getOrThrow(id).review) };
}

export async function postReview(id: string, req: ReviewRequest): Promise<ReviewResponse> {
  await latency();
  const c = getOrThrow(id);
  const reviewer = checkReviewer(req.reviewer);
  const reason = checkReason(req.reason);
  if (c.status === "processing") throw err(409, "case_processing", "The analysis is still running.");
  if (c.status !== "open") throw err(409, "case_not_open", "Decisions are only possible while a case is open.");
  if (req.case_version !== c.version) throw err(409, "stale_version", `The case is now at version ${c.version}.`, { current_version: c.version });
  recompute(c);

  const nextId = (state.nextEntry.get(c.id) ?? c.review.length + 1);
  state.nextEntry.set(c.id, nextId + 1);
  const entry: ReviewEntry = {
    id: `r${nextId}`, kind: req.kind, reviewer, decision: req.decision, reason, flag_ids: [], finding_id: null, target_entry_id: null,
    status: "effective", case_version: c.version, created_at: nowIso(),
  };

  if (req.kind === "verdict") {
    if (!ALL_VERDICTS.includes(req.decision) || !VERDICTS_BY_USE_CASE[c.use_case].includes(req.decision)) {
      throw err(422, "verdict_not_allowed", "That verdict is not allowed for this use case.", { allowed: VERDICTS_BY_USE_CASE[c.use_case] });
    }
    if (req.decision === "approve") {
      const open = c.flags.filter((f) => isHighFlag(f) && (f.disposition === "open" || f.disposition === "pending_second_approval"));
      if (open.length) throw err(409, "high_flags_open", "Every high flag must be confirmed or dismissed before approving.", { flag_ids: open.map((f) => f.id) });
    }
    if (req.decision === "request_documents") c.status = "awaiting_documents";
    else {
      c.status = "closed";
      c.decision = req.decision;
      if (req.decision === "escalate_aml") c.escalated_at = entry.created_at;
    }
  } else if (req.kind === "flag_decision") {
    if ((req.flag_ids === undefined) === (req.finding_id === undefined)) {
      throw err(422, "validation_error", "Give exactly one of flag_ids or finding_id.", { errors: [{ field: "flag_ids", message: "exactly one of flag_ids, finding_id" }] });
    }
    let ids = req.flag_ids ?? [];
    if (req.finding_id !== undefined) {
      const fnd = c.findings.find((f) => f.id === req.finding_id);
      if (!fnd) throw err(404, "not_found", "No such finding.", { kind: "finding" });
      ids = fnd.flag_ids;
      entry.finding_id = fnd.id;
    }
    if (ids.length < 1 || ids.length > 50) throw err(422, "validation_error", "Between 1 and 50 flags per decision.", { errors: [{ field: "flag_ids", message: "1 to 50 items" }] });
    const targets = ids.map((fid) => {
      const f = c.flags.find((x) => x.id === fid);
      if (!f) throw err(404, "not_found", "No such flag.", { kind: "flag", flag_id: fid });
      return f;
    });
    const pending = targets.filter((f) => f.disposition === "pending_second_approval");
    if (pending.length) throw err(409, "flag_decision_pending", "A dismissal of this flag is waiting for a second reviewer.", { flag_ids: pending.map((f) => f.id) });
    entry.flag_ids = targets.map((f) => f.id);
    if (req.decision === "dismiss" && targets.some(isHighFlag)) entry.status = "pending_second_approval";
  } else {
    const target = c.review.find((e) => e.id === req.target_entry_id);
    if (!target || target.status !== "pending_second_approval") throw err(409, "entry_not_pending", "That entry is not waiting for a second approval.");
    if (target.reviewer.trim().toLowerCase() === reviewer.toLowerCase()) throw err(409, "second_approver_must_differ", "A different reviewer must countersign.");
    entry.target_entry_id = target.id;
    target.status = req.decision === "approve" ? "effective" : "declined";
  }

  c.review.push(entry);
  c.updated_at = entry.created_at;
  recompute(c);
  return {
    entry: structuredClone(entry),
    case: { id: c.id, status: c.status, decision: c.decision ?? null, escalated_at: c.escalated_at ?? null, version: c.version, pending_second_approvals: c.pending_second_approvals.length },
  };
}

function newVersion(c: CaseDetail, kind: Job["kind"], reason: "rerun" | "documents_added" = "rerun"): NewVersionResponse {
  // snapshot the version being replaced so it stays readable (read-only) through ?version=
  const snaps = state.history.get(c.id) ?? [];
  snaps.push({ version: c.version, reason: state.currentReason.get(c.id) ?? "created", detail: structuredClone(c) });
  state.history.set(c.id, snaps);
  state.currentReason.set(c.id, reason);
  c.version += 1;
  c.status = "processing";
  c.score = null;
  c.coverage = null;
  c.lane = null;
  c.top_finding = null;
  c.decision = null;
  c.updated_at = nowIso();
  const job_id = startJob(c.id, kind);
  return { case_id: c.id, job_id, version: c.version };
}

export async function rerunCase(id: string): Promise<NewVersionResponse> {
  await latency();
  const c = getOrThrow(id);
  if (c.status === "processing") throw err(409, "case_processing", "The analysis is still running.");
  if (c.status === "closed") throw err(409, "case_not_open", "A closed case cannot be re-run.");
  return newVersion(c, "rerun");
}

export async function addFiles(id: string, files: File[]): Promise<NewVersionResponse> {
  await latency();
  const c = getOrThrow(id);
  if (c.status === "processing") throw err(409, "case_processing", "The analysis is still running.");
  if (c.status !== "open" && c.status !== "awaiting_documents") throw err(409, "case_not_open", "Documents can only be added while a case is open or awaiting documents.");
  checkFiles(files);
  files.forEach((f) => c.files.push({ ...fileRef(nextFileId(c.files, c.use_case), f.name.slice(0, 120), 1), added_in_version: c.version + 1 }));
  return newVersion(c, "rerun", "documents_added");
}

export async function deleteCase(id: string, reviewer: string, reason: string): Promise<void> {
  await latency();
  checkReviewer(reviewer);
  checkReason(reason);
  const c = getOrThrow(id);
  if (c.status === "processing") throw err(409, "case_processing", "The analysis is still running.");
  state.cases.delete(id);
}

// ------------------------------------------------------------------ check catalogue (editable)

const SEVERITY_VALUES: Severity[] = ["high", "medium", "low", "info"];
const WORDING_MAX = { name: 80, description: 400, note: 600 } as const;
type Wording = keyof typeof WORDING_MAX;

function withSettings(r: RuleInfo): RuleInfo {
  const o = state.overrides.get(r.rule) ?? null;
  return { ...structuredClone(r), default_severity: r.severity, effective_severity: o?.severity ?? r.severity, enabled: o ? o.enabled : true, override: o ? structuredClone(o) : null };
}

export async function getRules(): Promise<{ rules: RuleInfo[] }> {
  await latency();
  return { rules: RULES.map(withSettings) };
}

const sameSettings = (a: RuleOverride | null, b: RuleOverride | null) => {
  const f = (o: RuleOverride | null) => [o?.enabled ?? true, o?.severity ?? null, o?.name ?? null, o?.description ?? null, o?.note ?? null];
  return JSON.stringify(f(a)) === JSON.stringify(f(b));
};
const isDefault = (o: RuleOverride) => o.enabled && !o.severity && !o.name && !o.description && !o.note;

function record(rule: string, reviewer: string, reason: string, action: RuleChange["action"], before: RuleOverride | null, after: RuleOverride | null): void {
  state.changes.push({ id: `chg_${state.changes.length + 1}`, rule, at: nowIso(), reviewer, reason, action, before: before && structuredClone(before), after: after && structuredClone(after) });
}

/** The catalogue allows 5 to 500 characters (the review forms allow more). */
function catalogueReason(reason: unknown): string {
  const r = checkReason(reason);
  if (r.length > 500) throw err(422, "validation_error", "The reason is too long.", { errors: [{ field: "reason", message: "at most 500 characters" }] });
  return r;
}

function knownRule(rule: string): RuleInfo {
  const r = RULES.find((x) => x.rule === rule);
  if (!r) throw err(404, "unknown_rule", `There is no check called ${rule}.`, { rule });
  return r;
}

export async function updateRuleOverride(rule: string, req: RuleOverrideRequest): Promise<RuleInfo> {
  await latency();
  const info = knownRule(rule);
  const reviewer = checkReviewer(req.reviewer);
  const reason = catalogueReason(req.reason);
  const errors: { field: string; message: string }[] = [];
  if (req.enabled !== undefined && typeof req.enabled !== "boolean") errors.push({ field: "enabled", message: "must be true or false" });
  if (req.severity != null && !SEVERITY_VALUES.includes(req.severity)) errors.push({ field: "severity", message: "must be high, medium, low or info" });
  const text: Partial<Record<Wording, string | null>> = {};
  for (const k of Object.keys(WORDING_MAX) as Wording[]) {
    const v = req[k];
    if (v === undefined) continue;
    const t = typeof v === "string" ? v.trim() : "";
    if (t.length > WORDING_MAX[k]) errors.push({ field: k, message: `at most ${WORDING_MAX[k]} characters` });
    text[k] = t || null;
  }
  if (errors.length) throw err(422, "validation_error", "Some settings are not valid.", { errors });

  const before = state.overrides.get(rule) ?? null;
  const next: RuleOverride = {
    enabled: req.enabled ?? before?.enabled ?? true,
    severity: req.severity !== undefined ? req.severity : (before?.severity ?? null),
    name: text.name !== undefined ? text.name : (before?.name ?? null),
    description: text.description !== undefined ? text.description : (before?.description ?? null),
    note: text.note !== undefined ? text.note : (before?.note ?? null),
    updated_by: reviewer, updated_at: nowIso(), reason, revision: (before?.revision ?? 0) + 1,
  };
  if (sameSettings(before, next)) throw err(422, "no_change", "These settings are already in place.");
  if (isDefault(next)) state.overrides.delete(rule);
  else state.overrides.set(rule, next);
  record(rule, reviewer, reason, "update", before, isDefault(next) ? null : next);
  return withSettings(info);
}

export async function resetRuleOverride(rule: string, req: { reviewer: string; reason: string }): Promise<RuleInfo> {
  await latency();
  const info = knownRule(rule);
  const reviewer = checkReviewer(req.reviewer);
  const reason = catalogueReason(req.reason);
  const before = state.overrides.get(rule);
  if (!before) throw err(404, "no_override", "This check is already at its defaults.", { rule });
  state.overrides.delete(rule);
  record(rule, reviewer, reason, "reset", before, null);
  return withSettings(info);
}

export async function getRuleChanges(q: { rule?: string; limit?: number } = {}): Promise<RuleChange[]> {
  await latency();
  const all = state.changes.filter((c) => !q.rule || c.rule === q.rule).reverse();
  return structuredClone(q.limit ? all.slice(0, q.limit) : all);
}

export async function getEffectiveness(q: { use_case?: UseCase; since?: string } = {}): Promise<EffectivenessResponse> {
  await latency();
  advance();
  const cases = [...state.cases.values()].filter((c) => !q.use_case || c.use_case === q.use_case).filter((c) => !q.since || c.created_at.slice(0, 10) >= q.since);
  const rules: RuleEffectiveness[] = RULES.map((r) => {
    const flags = cases.flatMap((c) => c.flags.filter((f) => f.rule === r.rule));
    const count = (d: Flag["disposition"]) => flags.filter((f) => f.disposition === d).length;
    const confirmed = count("confirmed");
    const dismissed = count("dismissed");
    return {
      rule: r.rule, stage: r.stage, default_severity: r.severity, description: r.description,
      cases_fired: new Set(cases.filter((c) => c.flags.some((f) => f.rule === r.rule)).map((c) => c.id)).size,
      times_fired: flags.length, confirmed, dismissed, pending: count("pending_second_approval"), open: count("open"),
      dismissal_rate: confirmed + dismissed ? Math.round((dismissed / (confirmed + dismissed)) * 10000) / 10000 : null,
      median_seconds_to_decide: null, cases_ran: cases.filter((c) => c.coverage).length, cases_skipped: 0,
    };
  });
  return {
    generated_for: { use_case: q.use_case ?? null, since: q.since ?? null },
    cases_total: cases.length, cases_decided: cases.filter((c) => c.status === "closed").length, median_seconds_to_verdict: null, rules,
  };
}

export async function getExplainMethod(): Promise<ExplainMethod> {
  await latency();
  return structuredClone(EXPLAIN_METHOD);
}
