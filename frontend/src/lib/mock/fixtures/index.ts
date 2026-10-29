/**
 * Mock-mode fixtures. Everything here is fictitious.
 *
 * Typed against lib/types.ts, so the build fails when a fixture drifts from the contract.
 * Scores follow the real formulae (scoring-and-queue.md 4.4): score = min(100, sum of finding
 * weights 30/15/5/0), bands 30/60, coverage ratio = ran / applicable. fixtures.test.ts pins this.
 *
 * Page images are public/mock/pages/{statement,invoice,ownership}.svg (A4 ratio). Region helpers
 * below compute fractions from the same row geometry the SVGs are drawn with, so boxes land on
 * the intended row:
 *   statement  table row i: baseline y = 200 + 24 i, balance column x 480..560, credit column x 415..485
 *   invoice    item row i: baseline y = 300 + 24 i, amount column x 480..560, total baseline y = 460
 *   ownership  holder row i: baseline y = 220 + 40 i
 */
import type {
  Annotation,
  Band,
  CaseDetail,
  CaseStatus,
  CellKind,
  CheckRun,
  Classification,
  Coverage,
  CoverageSkip,
  Document,
  DocStatus,
  ExplainMethod,
  Provenance,
  DocType,
  FileRef,
  Finding,
  Flag,
  FlagStage,
  Job,
  Lane,
  ReviewEntry,
  Region,
  RuleInfo,
  Score,
  Severity,
  StageName,
  StageRecord,
  UseCase,
  Verdict,
} from "../../types";

export const MOCK_NOW = "2026-10-27T10:30:00Z";
export const MOCK_TODAY = "2026-10-27";

export const SCORE_WEIGHTS: Record<Severity, number> = { high: 30, medium: 15, low: 5, info: 0 };
export const BAND_THRESHOLDS = { medium: 30, high: 60 } as const;
/** The same shape as GET /explain/method (the mock serves it so the explainers work offline). */
export const EXPLAIN_METHOD: ExplainMethod = {
  score: {
    weights: SCORE_WEIGHTS, cap: 100, bands: { medium_from: BAND_THRESHOLDS.medium, high_from: BAND_THRESHOLDS.high },
    counting: "Findings are counted once each: flags that come from one underlying cause are grouped into one finding, and a finding adds its weight once, whatever the number of flags behind it.",
  },
  coverage: {
    low_coverage_ratio: 0.7, fast_lane_min_coverage: 0.8,
    definition: "Checks completed is the share of applicable checks that could run. A check could not run when the document it needs is missing or unreadable. Pages that no check looked at lower it further for the fast lane.",
  },
  fast_lane: {
    requires: [
      "The analysis is complete (not a partial score)",
      "The risk score is 20 or lower",
      "No finding blocks the fast lane",
      "At least 80% of the applicable checks completed, counting pages we could not review",
    ],
  },
  hard_stop_rules: [
    "pdf_bytes_after_signature", "pdf_text_vs_render", "pdf_editor_software", "signature_reuse", "copy_move", "inv_duplicate", "file_reuse",
    "bs_pass_through", "x_declared_vs_activity", "x_remitter_vs_buyer", "x_sof_support", "x_ownership_vs_registry", "x_directors", "id_checksum", "passport_mrz",
  ],
  aml_routing_rules: ["x_sof_support", "x_remitter_vs_buyer", "bs_pass_through"],
  lanes: [
    { lane: "loan", suggested_verdict: "reject_or_escalate", how: "Loan applications go here by default. The tool suggests rejecting or escalating when the risk supports it." },
    { lane: "edd", suggested_verdict: "refer_edd", how: "Onboarding (KYC) cases go here by default. The tool suggests referring for enhanced due diligence when the risk supports it." },
    { lane: "aml", suggested_verdict: "escalate_aml", how: "AML reviews go here, and so does any case with a concern about where money came from or went to, whatever its type." },
    { lane: "fast", suggested_verdict: "approve", how: "A low-risk case that was checked thoroughly. A person still confirms before anything is approved." },
  ],
  ruleset_version: "2026.10.1",
};

export const STAGES: StageName[] = ["ingest", "classify", "extract", "forensics", "rules", "crossdoc", "score"];

export function bandFor(value: number): Band {
  return value >= BAND_THRESHOLDS.high ? "high" : value >= BAND_THRESHOLDS.medium ? "medium" : "low";
}

export function isoMinus(seconds: number): string {
  const t = Date.parse(MOCK_NOW) - seconds * 1000;
  return new Date(t).toISOString().replace(/\.\d{3}Z$/, "Z");
}

// ---------------------------------------------------------------- regions

const PAGE_W = 595.28;
const PAGE_H = 841.89;
const r3 = (n: number) => Math.round(n * 1000) / 1000;

function box(file_id: string, page: number, x0: number, y0: number, x1: number, y1: number, label?: string): Region {
  return { file_id, page, x: r3(x0 / 595), y: r3(y0 / 842), w: r3((x1 - x0) / 595), h: r3((y1 - y0) / 842), label: label ?? null };
}
export const stmtBalance = (row: number, page = 1, label?: string) => box("f1", page, 480, 188 + 24 * row, 560, 204 + 24 * row, label);
export const stmtCredit = (row: number, page = 1, label?: string) => box("f1", page, 415, 188 + 24 * row, 485, 204 + 24 * row, label);
export const invAmount = (row: number, label?: string) => box("f2", 1, 480, 288 + 24 * row, 560, 304 + 24 * row, label);
export const invTotal = (label?: string) => box("f2", 1, 480, 448, 560, 464, label);
export const ownHolder = (row: number, label?: string) => box("f3", 1, 40, 200 + 40 * row, 555, 232 + 40 * row, label);

// ---------------------------------------------------------------- files and documents

function pages(n: number): FileRef["pages"] {
  return Array.from({ length: n }, (_, i) => ({
    page: i + 1, width_pt: PAGE_W, height_pt: PAGE_H, rotation: 0,
    has_text_layer: true, is_scan: false, word_count: 120,
  }));
}
export function fileRef(id: string, name: string, nPages: number, mime: FileRef["mime"] = "application/pdf"): FileRef {
  return { id, name, sha256: id.padEnd(64, "0"), size_bytes: 48_000 + nPages * 1000, mime, pages: pages(nPages), pdf_meta: {}, added_in_version: 1 };
}
export const FILE_STATEMENT = (): FileRef => fileRef("f1", "bank-statement-sep-2026.pdf", 3);
export const FILE_INVOICE = (): FileRef => fileRef("f2", "invoice-INV-0042.pdf", 1);
export const FILE_OWNERSHIP = (): FileRef => fileRef("f3", "ownership-chart.pdf", 1);

const REASONS: Record<string, string[]> = {
  bank_statement: ['Found "opening balance"', 'Found "closing balance"', "A table with date, debit, credit and balance columns"],
  invoice: ['Found "invoice number"', 'Found "total due"', "A table of line items with quantity and unit price"],
  ownership_chart: ['Found "shareholders"', "Boxes joined by lines with percentages"],
  company_profile: ['Found "registered office"', 'Found "directors"'],
  shareholder_register: ['Found "register of members"', "A table of holders with share counts"],
};
const ALTERNATIVES: Record<string, Record<string, number>> = {
  bank_statement: { invoice: 0.02, financial_statement: 0.007, other: 0.003 },
  invoice: { purchase_order: 0.15, contract: 0.04, other: 0.02 },
  ownership_chart: { shareholder_register: 0.03, company_profile: 0.01 },
};
const classification = (label: string, probability = 0.97): Classification => ({
  label, probability, classifier: "rules", version: "1",
  reasons: REASONS[label] ?? [], distribution: { [label]: probability, ...(ALTERNATIVES[label] ?? {}) },
});

export function doc(id: string, file_id: string, page_start: number, page_end: number, doc_type: DocType, extra: Partial<Document> = {}): Document {
  const status: DocStatus = extra.status ?? "ok";
  return {
    id, file_id, page_start, page_end, doc_type, classification: classification(doc_type), issuer: null,
    status, extractor: "text_layer", fields: {}, tables: {}, warnings: [], error: null, ...extra,
  };
}

const cell = (value: string, kind: CellKind = "text") => ({ value, kind, source: "text_layer" as const });
const STMT_ROWS: { desc: string; debit?: string; credit?: string; balance: string; counterparty?: string }[] = [
  { desc: "Opening balance", balance: "50000.00" },
  { desc: "Transfer from Alpha Supplies Pte Ltd", credit: "12000.00", balance: "62000.00", counterparty: "Alpha Supplies Pte Ltd" },
  { desc: "Payment to Beta Logistics", debit: "3500.00", balance: "58500.00", counterparty: "Beta Logistics" },
  { desc: "Transfer from Gamma Holdings", credit: "20000.00", balance: "78500.00", counterparty: "Gamma Holdings" },
  { desc: "Payment to Gamma Holdings", debit: "19500.00", balance: "59000.00", counterparty: "Gamma Holdings" },
  { desc: "Payroll Sep", debit: "8200.00", balance: "50800.00" },
  { desc: "Transfer from Alpha Supplies Pte Ltd", credit: "15000.00", balance: "65800.00", counterparty: "Alpha Supplies Pte Ltd" },
  { desc: "Utilities", debit: "640.00", balance: "65160.00" },
  { desc: "Transfer from Delta Customer", credit: "9400.00", balance: "84560.00", counterparty: "Delta Customer" },
  { desc: "Closing balance", balance: "84560.00" },
];
export function statementDoc(id = "d1", status: DocStatus = "ok"): Document {
  return doc(id, "f1", 1, 3, "bank_statement", {
    classification: classification("bank_statement", 0.97),
    issuer: classification("example_bank", 0.9),
    status,
    tables: {
      transactions: {
        name: "transactions", columns: ["date", "description", "debit", "credit", "balance"], pages: [1],
        rows: STMT_ROWS.map((r, index) => ({
          index,
          cells: {
            description: cell(r.desc),
            ...(r.debit ? { debit: cell(r.debit, "money") } : {}),
            ...(r.credit ? { credit: cell(r.credit, "money") } : {}),
            balance: { ...cell(r.balance, "money"), box: stmtBalance(index) },
          },
          annotations: (r.counterparty ? { counterparty: { label: r.counterparty, probability: 0.9, classifier: "rules", version: "1" } } : {}) as Record<string, Annotation>,
          box: null,
        })),
      },
    },
  });
}
export const invoiceDoc = (id = "d2", cls?: Partial<Classification>): Document =>
  doc(id, "f2", 1, 1, "invoice", {
    ...(cls ? { classification: { ...classification("invoice"), ...cls } } : {}),
    fields: { supplier_name: cell("Alpha Supplies Pte Ltd"), buyer_name: cell("Example Trading Pte. Ltd."), total: cell("13000.00", "money") },
  });
export const ownershipDoc = (id = "d3"): Document =>
  doc(id, "f3", 1, 1, "ownership_chart", { fields: { company_name: cell("Example Trading Pte. Ltd.") } });

// ---------------------------------------------------------------- flags, findings

export function flag(f: {
  id: string; rule: string; stage?: FlagStage; severity: Severity; documents: string[];
  table?: string; field?: string; row?: number; region?: Region | null; extra?: Region[];
  evidence: string; explanation: string; disposition?: Flag["disposition"];
}): Flag {
  return {
    id: f.id, rule: f.rule, stage: f.stage ?? "rules", severity: f.severity, document_ids: f.documents,
    table: f.table ?? null, field: f.field ?? null, row: f.row ?? null,
    region: f.region ?? null, extra_regions: f.extra ?? [],
    evidence: f.evidence, explanation: f.explanation, disposition: f.disposition ?? "open",
  };
}
export function finding(id: string, title: string, severity: Severity, flags: Flag[], summary: string, hardStop = false): Finding {
  return {
    id, group_key: `${flags[0]?.rule ?? id}:${flags[0]?.document_ids.join("+") ?? ""}`, title, severity,
    flag_ids: flags.map((x) => x.id), document_ids: [...new Set(flags.flatMap((x) => x.document_ids))],
    rules: [...new Set(flags.map((x) => x.rule))], weight: SCORE_WEIGHTS[severity], summary, hard_stop: hardStop,
  };
}

// ---------------------------------------------------------------- case builder

export type CaseSpec = {
  id: string; reference: string | null; use_case: UseCase; status: CaseStatus; ageSeconds: number;
  applicantName: string | null; version?: number; files: FileRef[]; documents: Document[];
  flags?: Flag[]; findings?: Finding[]; lane: Lane | null;
  applicable?: number; ran?: number; skipped?: CoverageSkip[]; pagesUnchecked?: number;
  partialReasons?: string[]; topFinding?: string | null;
  decision?: Verdict | null; escalated_at?: string | null; review?: ReviewEntry[];
  notChecked?: CaseDetail["not_checked_documents"];
  /** A model read or classified something in this case: records models and a small cost. */
  usesModel?: boolean;
  /** earlier versions of this case (oldest first); each is a full spec rendered with its own version number. */
  history?: { reason: "created" | "rerun" | "documents_added"; spec: Omit<CaseSpec, "history"> }[];
};

export function coverage(applicable: number, ran: number, pagesTotal: number, pagesUnchecked: number, skipped: CoverageSkip[] = []): Coverage {
  return { applicable, ran, ratio: applicable ? r3(ran / applicable) : 0, skipped, pages_total: pagesTotal, pages_unchecked: pagesUnchecked };
}

export function stageRecords(failed: StageName[] = [], files: FileRef[] = []): StageRecord[] {
  const allPages = files.flatMap((f) => f.pages);
  const detail = (stage: StageName): StageRecord["detail"] => {
    if (stage === "ingest") {
      return {
        files: files.length, pages: allPages.length, text_pages: allPages.filter((p) => p.has_text_layer).length,
        scan_pages: allPages.filter((p) => p.is_scan).length, ocr_calls: 0, words: allPages.reduce((n, p) => n + p.word_count, 0),
        unreadable_pages: allPages.filter((p) => p.error).length, not_processed_pages: 0,
      };
    }
    return {};
  };
  return STAGES.map((stage, i) => ({
    stage, status: failed.includes(stage) ? "failed" : "ok", started_at: MOCK_NOW, seconds: [0.8, 0.1, 3.2, 1.4, 0.2, 0.1, 0][i] ?? 0.4, cost_usd: "0",
    error: failed.includes(stage) ? "stage failed (mock)" : null, warnings: [], detail: detail(stage),
  }));
}

const HEX = (seed: string) => seed.repeat(64).slice(0, 64);
export function provenanceFor(created: string, usesModel: boolean): Provenance {
  return {
    app_version: "0.1.0", ruleset_version: EXPLAIN_METHOD.ruleset_version,
    models: usesModel ? { classify: "claude-haiku-4-5", extract: "claude-haiku-4-5" } : {},
    prompt_hash: HEX("9f2c4b7a1e0d"), prompt_hashes: usesModel ? { classify: HEX("3a91"), extract: HEX("c47e") } : {},
    classifier_versions: { rules: "1" }, extractor_versions: { text_layer: "1" }, input_hashes: {}, settings_hash: HEX("5d10"),
    today: MOCK_TODAY, flags_digest: HEX("e8b2"), created_at: created,
  };
}

export function buildCase(s: CaseSpec): CaseDetail {
  // Flags are mutated by the mock review handlers (dispositions), so every built case owns its own copies.
  const flags = structuredClone(s.flags ?? []);
  const findings = structuredClone(s.findings ?? []);
  const processing = s.status === "processing";
  const errored = s.status === "error";
  const noResult = processing || errored;
  const pagesTotal = s.files.reduce((n, f) => n + f.pages.length, 0);
  const partialReasons = s.partialReasons ?? [];
  const partial = partialReasons.length > 0;
  const value = Math.min(100, findings.reduce((n, f) => n + f.weight, 0));
  const score: Score | null = noResult ? null : { value, band: bandFor(value), partial, partial_reasons: partialReasons };
  const cov = noResult ? null : coverage(s.applicable ?? 21, s.ran ?? s.applicable ?? 21, pagesTotal, s.pagesUnchecked ?? 0, s.skipped ?? []);
  const topFinding =
    s.topFinding !== undefined ? s.topFinding
    : noResult ? null
    : findings[0]?.title ?? `No flags raised; ${cov?.ran} of ${cov?.applicable} checks ran`;
  const checkRuns: CheckRun[] = noResult ? [] : [
    ...[...new Set(flags.map((f) => f.rule))].map((rule): CheckRun => ({
      rule, stage: flags.find((f) => f.rule === rule)!.stage, status: "ran", flags: flags.filter((f) => f.rule === rule).length, seconds: 0.01,
    })),
    // forensic checks that ran and found nothing (frontend.md 5.3: "No signal found")
    ...["pdf_editor_software", "ai_provenance_hint"]
      .filter((rule) => !flags.some((f) => f.rule === rule) && !(s.skipped ?? []).some((k) => k.rule === rule))
      .map((rule): CheckRun => ({ rule, stage: "forensics", status: "ran", flags: 0, seconds: 0.02 })),
    ...(s.skipped ?? []).map((k): CheckRun => ({ rule: k.rule, stage: "rules", status: k.status === "error" ? "error" : "skipped", reason: k.reason, flags: 0, seconds: 0 })),
  ];
  const created = isoMinus(s.ageSeconds);
  return {
    id: s.id, reference: s.reference, use_case: s.use_case, status: s.status, version: s.version ?? 1,
    today: MOCK_TODAY, created_at: created, updated_at: isoMinus(Math.max(0, s.ageSeconds - 8)),
    applicant: { name: s.applicantName, uen: null, source: "upload" },
    files: s.files, documents: s.documents, flags, findings, score, coverage: cov, check_runs: checkRuns,
    lane: noResult ? null : s.lane, top_finding: topFinding,
    partial: !noResult && partial, partial_reasons: noResult ? [] : partialReasons,
    stages: processing ? [] : stageRecords(errored ? ["rules"] : partialReasons.some((r) => r.startsWith("stage_failed:")) ? ["extract"] : [], s.files),
    provenance: processing ? null : provenanceFor(created, !!s.usesModel), costs: [],
    review: structuredClone(s.review ?? []), decision: s.decision ?? null, escalated_at: s.escalated_at ?? null,
    pending_second_approvals: [], allowed_verdicts: [],
    not_checked_documents: s.notChecked ?? [],
    cost_summary: s.usesModel
      ? { total_usd: "0.004200", calls: 3, cache_hits: 1, seconds_by_stage: { ingest: 0.8, classify: 0.1, extract: 3.2, forensics: 1.4, rules: 0.2, crossdoc: 0.1, score: 0 }, usd_by_stage: { classify: "0.000900", extract: "0.003300" } }
      : { total_usd: "0.000000", calls: 0, cache_hits: 0, seconds_by_stage: {}, usd_by_stage: {} },
  };
}

// ---------------------------------------------------------------- the seven mock cases

const fBal = flag({
  id: "bs_running_balance.d1.transactions.8", rule: "bs_running_balance", severity: "high", documents: ["d1"],
  table: "transactions", field: "balance", row: 8, region: stmtBalance(8, 1, "Balance"), extra: [stmtCredit(8, 1, "Credit")],
  evidence: "Printed balance 84560.00; previous 65160.00 + credit 9400.00 = 74560.00",
  explanation: "The running balance on this row is 10,000.00 higher than the previous balance plus the movement.",
});
const fRev = flag({
  id: "x_revenue_vs_credits.d1+d3.-", rule: "x_revenue_vs_credits", stage: "crossdoc", severity: "medium", documents: ["d1"],
  region: stmtCredit(1, 1, "Trade credit"),
  evidence: "Revenue 2400000.00 declared; trade credits in statements total 56400.00",
  explanation: "Credits in the bank statements cover about 2% of the revenue on the financial statements.",
});
const fInv1 = flag({
  id: "x_invoice_vs_receipts.d2.total", rule: "x_invoice_vs_receipts", stage: "crossdoc", severity: "medium", documents: ["d2", "d1"],
  field: "total", region: invTotal("Invoice total"), extra: [stmtCredit(6, 1, "Closest credit")],
  evidence: "Invoice INV-0042 total 13000.00; no credit within 1% on or after 12 Sep",
  explanation: "No bank credit matches the invoice total within the expected window.",
});
const fInv2 = flag({
  id: "x_invoice_vs_receipts.d2.line1", rule: "x_invoice_vs_receipts", stage: "crossdoc", severity: "medium", documents: ["d2", "d1"],
  region: invAmount(0, "Line 1"), evidence: "Line amount 8000.00 has no matching receipt",
  explanation: "Part of a pattern: several invoices have no matching receipts.",
});
const fCon = flag({
  id: "x_invoice_vs_contract.d2.unit", rule: "x_invoice_vs_contract", stage: "crossdoc", severity: "medium", documents: ["d2"],
  field: "unit_price", region: invAmount(1, "Unit price"),
  evidence: "Contract unit price 85.00; invoice 90.00",
  explanation: "The invoiced unit price differs from the contract.",
});

const fraudFlags = [fBal, fRev, fInv1, fInv2, fCon];
const fraudFindings = [
  finding("fnd_1", "Running balance does not add up on a bank statement row", "high", [fBal], "Balance on row 9 is 10,000.00 above the arithmetic result."),
  finding("fnd_2", "Revenue 2.4M on the financial statements but only 56K of trade credits in the bank statements", "medium", [fRev], "Declared revenue is not reflected in the account."),
  finding("fnd_3", "Invoices have no matching receipts in the bank statements", "medium", [fInv1, fInv2], "Pattern across two invoice checks."),
  finding("fnd_4", "Invoice price differs from the contract", "medium", [fCon], "Unit price mismatch."),
];

const skipped: CoverageSkip[] = [
  { rule: "fs_arith", status: "skipped", reason: "requires financial_statement" },
  { rule: "kyc_shareholdings", status: "skipped", reason: "requires ownership_chart" },
  { rule: "x_directors", status: "skipped", reason: "requires company_profile" },
];

const fSof = flag({
  id: "x_sof_support.d3.-", rule: "x_sof_support", stage: "crossdoc", severity: "high", documents: ["d3", "d1"],
  region: ownHolder(0, "Declared owner"), evidence: "Source of funds declared as sale of property; no matching credit",
  explanation: "The declared source of funds is not supported by any document.",
});
const fRem = flag({
  id: "x_remitter_vs_buyer.d1.4", rule: "x_remitter_vs_buyer", stage: "crossdoc", severity: "high", documents: ["d1", "d2"],
  table: "transactions", row: 3, region: stmtCredit(3, 1, "Remitter"),
  evidence: "Remitter Gamma Holdings; invoice buyer Example Trading Pte. Ltd.",
  explanation: "The party that paid is not the party invoiced.",
});
const fPass = flag({
  id: "bs_pass_through.d1.5", rule: "bs_pass_through", severity: "medium", documents: ["d1"],
  table: "transactions", row: 4, region: stmtBalance(4, 1, "Pass-through"),
  evidence: "Gamma Holdings: 20000.00 in on 06 Sep, 19500.00 out on 09 Sep",
  explanation: "Funds in and out within 3 days with 97.5% retained by neither party.",
});
const amlFindings = [
  finding("fnd_1", "Declared source of funds is not supported by any document", "high", [fSof], "No supporting document."),
  finding("fnd_2", "Remitter differs from the invoice customer", "high", [fRem], "Payer and invoiced party differ."),
  finding("fnd_3", "Funds pass through the account within days", "medium", [fPass], "Pass-through pattern."),
];

const fOwn = flag({
  id: "x_ownership_vs_registry.d3.-", rule: "x_ownership_vs_registry", stage: "crossdoc", severity: "high", documents: ["d3"],
  region: ownHolder(1, "Holder"), evidence: "Chart lists Beta Holdings Pte Ltd 25.00%; register omits it",
  explanation: "A holder on the chart does not appear in the register.", disposition: "pending_second_approval",
});
const fDecl = flag({
  id: "x_declared_vs_activity.d3.-", rule: "x_declared_vs_activity", stage: "crossdoc", severity: "medium", documents: ["d3"],
  region: ownHolder(2, "Declared activity"), evidence: "Declared dormant; activity found", explanation: "Declared activity does not match the account.",
});
const kycReview: ReviewEntry = {
  id: "r1", kind: "flag_decision", reviewer: "a.tan", decision: "dismiss", reason: "Chart is an old draft; register is the source of truth.",
  flag_ids: [fOwn.id], finding_id: null, target_entry_id: null, status: "pending_second_approval", case_version: 1, created_at: isoMinus(1800),
};

const fPartial = flag({
  id: "bs_running_balance.d1.transactions.2", rule: "bs_running_balance", severity: "medium", documents: ["d1"],
  table: "transactions", field: "balance", row: 2, region: stmtBalance(2), evidence: "Printed 58500.00; computed 58500.01",
  explanation: "Balance differs from the arithmetic result by 0.01.",
});
const fPartialLow = flag({
  id: "doc_unreadable.d4.-", rule: "doc_unreadable", stage: "forensics", severity: "low", documents: ["d1"],
  region: null, evidence: "Page 3 could not be read", explanation: "A scanned page could not be read without a model key.",
});


// ---- KYC fraud: one finding seen on three documents, plus a high dismissal waiting for a second reviewer
const fOwn3 = flag({
  id: "x_ownership_vs_registry.d1+d2+d3.-", rule: "x_ownership_vs_registry", stage: "crossdoc", severity: "high", documents: ["d1", "d2", "d3"],
  region: ownHolder(1, "Chart: Beta Holdings 40%"), extra: [box("f1", 1, 40, 200, 555, 232, "Register: no Beta Holdings"), box("f2", 1, 40, 200, 555, 232, "Profile: not listed")],
  evidence: "Chart lists Beta Holdings Pte Ltd 40.00%; the register and the profile omit it",
  explanation: "A 40% holder on the ownership chart appears in neither the shareholder register nor the company profile.",
});
const fDir = flag({
  id: "x_directors.d2+d3.-", rule: "x_directors", stage: "crossdoc", severity: "high", documents: ["d2", "d3"],
  region: box("f2", 1, 40, 240, 555, 272, "Director"), evidence: "Director Tan Wei Ming on the profile is absent from the register",
  explanation: "A director named on the company profile does not appear on the register of directors.", disposition: "pending_second_approval",
});
const fAct = flag({
  id: "x_declared_vs_activity.d3.-", rule: "x_declared_vs_activity", stage: "crossdoc", severity: "medium", documents: ["d3"],
  region: box("f2", 1, 40, 280, 555, 312, "Declared activity"), evidence: "Declared: consulting; account shows 14 inbound cash deposits",
  explanation: "Declared activity does not match account activity.",
});
const tblRow = (index: number, cells: Record<string, string>) => ({
  index, cells: Object.fromEntries(Object.entries(cells).map(([k, v]) => [k, cell(v)])), annotations: {} as Record<string, Annotation>, box: null,
});
const kycDocs = (): Document[] => [
  doc("d1", "f3", 1, 1, "ownership_chart", {
    tables: { holders: { name: "holders", columns: ["holder_name", "holder_type", "percent"], pages: [1], rows: [
      tblRow(0, { holder_name: "Jessica Wong", holder_type: "person", percent: "60.00" }),
      tblRow(1, { holder_name: "Beta Holdings Pte Ltd", holder_type: "company", percent: "40.00" }),
    ] } },
  }),
  doc("d2", "f2", 1, 1, "company_profile", {
    fields: { company_name: cell("Lion City Foods Pte. Ltd.") },
    tables: { directors: { name: "directors", columns: ["director_name"], pages: [1], rows: [
      tblRow(0, { director_name: "Jessica Wong" }), tblRow(1, { director_name: "Tan Wei Ming" }),
    ] } },
  }),
  doc("d3", "f1", 1, 1, "shareholder_register", {
    tables: { shareholders: { name: "shareholders", columns: ["holder_name", "percent"], pages: [1], rows: [
      tblRow(0, { holder_name: "Wong Jessica", percent: "100.00" }),
    ] } },
  }),
];
const kycFraudReview: ReviewEntry = {
  id: "r1", kind: "flag_decision", reviewer: "a.tan", decision: "dismiss", reason: "Tan Wei Ming resigned last month; the register is newer.",
  flag_ids: [fDir.id], finding_id: null, target_entry_id: null, status: "pending_second_approval", case_version: 1, created_at: isoMinus(900),
};

// ---- AML with an entity graph: Gamma Holdings pays in and out
const amlGraphDocs = (): Document[] => [statementDoc("d1"), invoiceDoc("d2"), ownershipDoc("d3")];

// ---- hard stop: PDF editor software
const fEditor = flag({
  id: "pdf_editor_software.d1.-", rule: "pdf_editor_software", stage: "forensics", severity: "medium", documents: ["d1"],
  region: null, evidence: "Producer: Example PDF Editor Pro 9.1", explanation: "The statement was last saved by a general-purpose PDF editor, not by a bank system.",
});

// ---- awaiting documents
const fAwait = flag({
  id: "x_invoice_vs_receipts.d2.total", rule: "x_invoice_vs_receipts", stage: "crossdoc", severity: "medium", documents: ["d2", "d1"],
  field: "total", region: invTotal("Invoice total"), evidence: "Invoice total 13000.00; no matching credit", explanation: "No bank credit matches the invoice total.",
});

// ---- version 2
const fV2 = flag({
  id: "bs_running_balance.d1.transactions.8", rule: "bs_running_balance", severity: "medium", documents: ["d1"],
  table: "transactions", field: "balance", row: 8, region: stmtBalance(8, 1, "Balance"),
  evidence: "Printed balance 84560.00; computed 84559.00", explanation: "The running balance differs from the arithmetic result by 1.00.",
});
const v2Spec: Omit<CaseSpec, "history"> = {
  id: "c_loan_v2", reference: "ACME-2026-050", use_case: "loan", status: "open", ageSeconds: 7200,
  applicantName: "Bluewater Marine Pte. Ltd.", files: [FILE_STATEMENT(), FILE_INVOICE()], documents: [statementDoc(), invoiceDoc()],
  flags: [fV2], findings: [finding("fnd_1", "Running balance differs by 1.00 on a bank statement row", "medium", [fV2], "Small difference.")],
  lane: "loan", applicable: 21, ran: 21,
};

export const SPECS: CaseSpec[] = [
  {
    id: "c_loan_fraud", reference: "ACME-2026-014", use_case: "loan", status: "open", ageSeconds: 3600,
    applicantName: "Example Trading Pte. Ltd.", files: [FILE_STATEMENT(), FILE_INVOICE()], documents: [statementDoc(), invoiceDoc()],
    flags: fraudFlags, findings: fraudFindings, lane: "loan", applicable: 21, ran: 18, skipped, pagesUnchecked: 1,
  },
  {
    id: "c_kyc_review", reference: "ACME-2026-021", use_case: "kyc", status: "open", ageSeconds: 5400,
    applicantName: "Lion City Foods Pte. Ltd.", files: [FILE_OWNERSHIP()], documents: [ownershipDoc()],
    flags: [fOwn, fDecl],
    findings: [
      finding("fnd_1", "Ownership chart lists a holder that the registry omits", "high", [fOwn], "Chart vs register mismatch."),
      finding("fnd_2", "Declared activity does not match account activity", "medium", [fDecl], "Dormant company with activity."),
    ],
    lane: "edd", applicable: 15, ran: 9, skipped: [{ rule: "x_directors", status: "skipped", reason: "requires company_profile" }], review: [kycReview],
  },
  {
    id: "c_aml_closed", reference: "ACME-2026-009", use_case: "aml", status: "closed", ageSeconds: 86400,
    applicantName: "Harbour Imports Pte. Ltd.", files: [FILE_STATEMENT(), FILE_INVOICE(), FILE_OWNERSHIP()],
    documents: [statementDoc(), invoiceDoc(), ownershipDoc()],
    flags: [fSof, fRem, fPass], findings: amlFindings, lane: "aml", applicable: 18, ran: 17,
    decision: "escalate_aml", escalated_at: isoMinus(7200),
    review: [{
      id: "r1", kind: "verdict", reviewer: "a.tan", decision: "escalate_aml", reason: "Remitter differs from invoice customer; funds routed through three companies.",
      flag_ids: [], finding_id: null, target_entry_id: null, status: "effective", case_version: 1, created_at: isoMinus(7200),
    }],
  },
  {
    id: "c_loan_clean", reference: "ACME-2026-030", use_case: "loan", status: "open", ageSeconds: 1200,
    applicantName: "Sunrise Bakery Pte. Ltd.", files: [FILE_STATEMENT()], documents: [{ ...statementDoc(), tables: {} }],
    lane: "fast", applicable: 21, ran: 21,
  },
  {
    id: "c_loan_partial", reference: "ACME-2026-033", use_case: "loan", status: "open", ageSeconds: 2400,
    applicantName: "Riverside Cafe Pte. Ltd.", files: [FILE_STATEMENT(), FILE_INVOICE()],
    usesModel: true,
    documents: [
      {
        ...statementDoc(), tables: {}, status: "partial", extractor: "cascade(text_layer>llm_cheap)",
        warnings: ["unreadable: page 3 could not be read", "escalated: page=2 reason=balance_mismatch result=cheap_model_kept"],
      },
      invoiceDoc("d2", {
        classifier: "llm", version: "claude-haiku-4-5", probability: 0.74,
        rationale: "Has an invoice number, a list of items with prices and a total due.", reasons: [],
        distribution: { invoice: 0.74, purchase_order: 0.2, contract: 0.04, other: 0.02 },
      }),
    ],
    flags: [fPartial, fPartialLow],
    findings: [
      finding("fnd_1", "Running balance differs by 0.01 on a bank statement row", "medium", [fPartial], "Rounding-sized difference."),
      finding("fnd_2", "A scanned page could not be read", "low", [fPartialLow], "No model key."),
    ],
    lane: "loan", applicable: 21, ran: 11, partialReasons: ["stage_failed:extract", "document_failed:d4"], pagesUnchecked: 3,
    skipped: [{ rule: "fs_arith", status: "error", reason: "extract stage failed" }],
    notChecked: [{ document_id: "d4", doc_type: "other", pages: "f1:3-3", reason: "no checks for this document type" }],
  },
  {
    id: "c_kyc_processing", reference: "ACME-2026-040", use_case: "kyc", status: "processing", ageSeconds: 20,
    applicantName: "Orchid Logistics Pte. Ltd.", files: [FILE_OWNERSHIP()], documents: [], lane: null,
  },
  {
    id: "c_loan_error", reference: "ACME-2026-041", use_case: "loan", status: "error", ageSeconds: 900,
    applicantName: "Maple Works Pte. Ltd.", files: [FILE_STATEMENT()], documents: [], lane: null,
  },
  {
    id: "c_kyc_fraud", reference: "ACME-2026-022", use_case: "kyc", status: "open", ageSeconds: 4800,
    applicantName: "Lion City Foods Pte. Ltd.", files: [FILE_OWNERSHIP(), fileRef("f1", "shareholder-register.pdf", 1), fileRef("f2", "company-profile.pdf", 1)],
    documents: kycDocs(), flags: [fOwn3, fDir, fAct],
    findings: [
      finding("fnd_1", "Ownership chart lists a 40% holder that the register and the profile omit", "high", [fOwn3], "Seen on three documents."),
      finding("fnd_2", "A director on the profile is absent from the register", "high", [fDir], "Director mismatch."),
      finding("fnd_3", "Declared activity does not match account activity", "medium", [fAct], "Consulting vs cash deposits."),
    ],
    lane: "edd", applicable: 15, ran: 14, review: [kycFraudReview],
  },
  {
    id: "c_aml_graph", reference: "ACME-2026-017", use_case: "aml", status: "open", ageSeconds: 7000,
    applicantName: "Example Trading Pte. Ltd.", files: [FILE_STATEMENT(), FILE_INVOICE(), FILE_OWNERSHIP()], documents: amlGraphDocs(),
    flags: [fSof, fRem, fPass], findings: amlFindings, lane: "aml", applicable: 18, ran: 17,
  },
  {
    id: "c_loan_awaiting", reference: "ACME-2026-025", use_case: "loan", status: "awaiting_documents", ageSeconds: 30000,
    applicantName: "Coral Print Works Pte. Ltd.", files: [FILE_STATEMENT(), FILE_INVOICE()], documents: [statementDoc(), invoiceDoc()],
    flags: [fAwait], findings: [finding("fnd_1", "Invoice has no matching receipt in the bank statement", "medium", [fAwait], "No matching credit.")],
    lane: "loan", applicable: 21, ran: 21,
    review: [{
      id: "r1", kind: "verdict", reviewer: "b.lim", decision: "request_documents", reason: "Need the October statement to find the receipt.",
      flag_ids: [], finding_id: null, target_entry_id: null, status: "effective", case_version: 1, created_at: isoMinus(20000),
    }],
  },
  {
    id: "c_loan_hardstop", reference: "ACME-2026-027", use_case: "loan", status: "open", ageSeconds: 2000,
    applicantName: "Quayside Bikes Pte. Ltd.", files: [FILE_STATEMENT()], documents: [{ ...statementDoc(), tables: {} }],
    flags: [fEditor], findings: [finding("fnd_1", "Statement was last saved by a PDF editor", "medium", [fEditor], "Producer is an editor.", true)],
    lane: "loan", applicable: 21, ran: 21,
  },
  {
    ...v2Spec,
    version: 2,
    history: [
      {
        reason: "created",
        spec: { ...v2Spec, version: 1, flags: [], findings: [], files: [FILE_STATEMENT()], documents: [statementDoc()], ageSeconds: 14400 },
      },
    ],
  },
];

// ---------------------------------------------------------------- rules catalogue (subset)

function rule(r: string, stage: FlagStage, severity: Severity, description: string, applies_to: string[], use_cases: UseCase[], tamper: string | null = null): RuleInfo {
  return { rule: r, stage, severity, applies_to, requires: applies_to, use_cases, description, tamper, version: "1" };
}
export const RULES: RuleInfo[] = [
  rule("bs_running_balance", "rules", "high", "Each balance equals the previous balance plus credit minus debit.", ["bank_statement"], ["loan", "aml", "kyc"], "edited balance"),
  rule("bs_pass_through", "rules", "medium", "Funds in and out of the account within days.", ["bank_statement"], ["aml"]),
  rule("fs_arith", "rules", "high", "Financial statement totals add up.", ["financial_statement"], ["loan"], "edited total"),
  rule("inv_dates", "rules", "medium", "Invoice dates are plausible and ordered.", ["invoice"], ["loan", "aml"]),
  rule("pdf_editor_software", "forensics", "medium", "PDF producer is an editor rather than a bank or ERP system.", ["bank_statement", "invoice"], ["loan", "aml", "kyc"]),
  rule("x_revenue_vs_credits", "crossdoc", "medium", "Declared revenue is supported by credits in statements.", ["financial_statement", "bank_statement"], ["loan"]),
  rule("x_invoice_vs_receipts", "crossdoc", "medium", "Invoices have matching receipts.", ["invoice", "bank_statement"], ["loan", "aml"]),
  rule("x_invoice_vs_contract", "crossdoc", "medium", "Invoice terms match the contract.", ["invoice", "contract"], ["loan"]),
  rule("x_ownership_vs_registry", "crossdoc", "high", "Ownership chart matches the shareholder register.", ["ownership_chart", "shareholder_register"], ["kyc"]),
  rule("x_directors", "crossdoc", "high", "Directors agree across documents.", ["company_profile"], ["kyc"]),
  rule("x_sof_support", "crossdoc", "high", "Declared source of funds is supported.", ["source_of_funds_declaration", "bank_statement"], ["aml"]),
  rule("x_remitter_vs_buyer", "crossdoc", "high", "Remitter equals the invoiced party.", ["bank_statement", "invoice"], ["aml"]),
  rule("x_declared_vs_activity", "crossdoc", "medium", "Declared activity matches account activity.", ["company_profile", "bank_statement"], ["kyc"]),
];

// ---------------------------------------------------------------- job for a case

export function jobFor(id: string, caseId: string, state: Job["state"], stageIndex: number, error: string | null = null, kind: Job["kind"] = "analyse"): Job {
  const stages: StageRecord[] = STAGES.map((stage, i) => ({
    stage, status: state === "failed" && i === stageIndex ? "failed" : i < stageIndex || state === "succeeded" ? "ok" : i === stageIndex && state === "running" ? "running" : "pending",
    started_at: i <= stageIndex ? MOCK_NOW : null, seconds: i < stageIndex || state === "succeeded" ? 0.8 : 0, cost_usd: "0", error: null, warnings: [], detail: {},
  }));
  return {
    id, case_id: caseId, kind, state, created_at: MOCK_NOW, started_at: state === "queued" ? null : MOCK_NOW,
    finished_at: state === "succeeded" || state === "failed" ? MOCK_NOW : null,
    progress: { stage_index: Math.min(stageIndex + 1, STAGES.length), stage_count: STAGES.length, current_stage: state === "succeeded" ? null : STAGES[Math.min(stageIndex, STAGES.length - 1)] },
    stages, error,
  };
}
