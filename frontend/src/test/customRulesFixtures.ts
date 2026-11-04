/** Builders for the custom-rules API shapes, so each test states only what it cares about. */
import type {
  AdoptedRuleView,
  CaseDetail,
  CheckRun,
  CustomRuleSpec,
  CustomRuleView,
  CustomRunInfo,
  CustomTicket,
  DraftResponse,
  DryRunResult,
  Finding,
  Flag,
  SharedRuleView,
} from "@/lib/types";

export const DEBT_SPEC: CustomRuleSpec = {
  spec_version: 1,
  severity: "medium",
  condition: {
    template: "fs_ratio",
    left: ["balance_sheet.borrowings_current", "balance_sheet.borrowings_non_current"],
    operator: "gt",
    multiplier: "2",
    right: "balance_sheet.total_equity",
    column: "current",
  },
};
export const DEBT_READBACK =
  "Flags medium when, on a financial statement's current-year column, borrowings (current) + borrowings (non-current) is greater than 2 times total equity.";

export const INVOICE_SPEC: CustomRuleSpec = {
  spec_version: 1,
  severity: "low",
  condition: { template: "field_vs_constant", field: "invoice.total", operator: "gt", value: "100000" },
};
export const INVOICE_READBACK = "Flags low when an invoice's total is greater than 100,000.00.";

export function draft(over: Partial<DraftResponse> = {}): DraftResponse {
  return {
    draft_id: "crd_5b2e9f0a1c3d", turn: 1, turns_left: 5, status: "ready", understood: "Flag an invoice with a large total.",
    spec: INVOICE_SPEC, spec_hash: "hash-invoice", readback: INVOICE_READBACK,
    candidates: [], questions: [], assumptions: [], phrase_map: [], unsupported_parts: [], closest_templates: [],
    will_check: [INVOICE_READBACK, "Only invoices in the case are read."], will_not_check: [], refusal: null,
    duplicates: [], ideas: [], ticket: null, notes: [], dry_run: null, save_allowed: true, needs_narrower_ack: false,
    edit_of: null, model_calls: 1,
    ...over,
  };
}

/** Turn 1 of the worked example: "flag as medium if the firm has a lot of debt from different places". */
export function debtDraft(over: Partial<DraftResponse> = {}): DraftResponse {
  return draft({
    status: "partially_supported",
    understood: "Flag a company whose borrowings are large compared with its equity, and whose debt comes from several lenders.",
    spec: DEBT_SPEC, spec_hash: "hash-debt", readback: DEBT_READBACK,
    questions: [
      {
        id: "q1", slot: "multiplier", text: "What counts as a lot of debt?", default: "x2",
        options: [
          { id: "x1", label: "More than equity", value: "1" },
          { id: "x2", label: "More than 2 times equity", value: "2" },
          { id: "x3", label: "More than 3 times equity", value: "3" },
        ],
      },
    ],
    assumptions: ["Debt is read as current plus non-current borrowings, compared with total equity."],
    phrase_map: [
      { phrase: "flag as medium", slot: "severity" }, { phrase: "the firm", slot: "filler" }, { phrase: "a lot of", slot: "multiplier" },
      { phrase: "debt", slot: "left" }, { phrase: "from different places", slot: null },
    ],
    unsupported_parts: [{ phrase: "from different places", reason: "field_not_extracted", detail: "Lenders are not read from financial statements." }],
    closest_templates: ["fs_ratio"],
    will_check: [DEBT_READBACK, "Only financial statements in the case are read."],
    will_not_check: ['"from different places": the system does not read this from the documents'],
    save_allowed: false, needs_narrower_ack: true,
    ...over,
  });
}

export function dryRun(over: Partial<DryRunResult> = {}): DryRunResult {
  return {
    spec_hash: "hash-invoice", cases_considered: 200, evaluated: 41, fired: 6, skipped_missing: 3, scanned_evaluated: 9, scanned_fired: 2,
    fire_rate: 0.1463, warnings: [], examples: ["c_1a2b3c4d5e6f"], needs_confirmation: false,
    ...over,
  };
}

export function ruleView(over: Partial<CustomRuleView> = {}): CustomRuleView {
  return {
    rule_id: "cus_3f9a1c2b7d", name: "Heavy borrowing", description: "", owner: "alice", own: true, version: 1, severity: "medium",
    template: "fs_ratio", spec: DEBT_SPEC, spec_hash: "hash-debt", readback: DEBT_READBACK, enabled: true, visibility: "private",
    published_version: null, status: "active", adoptions: 0, label: "custom, unreviewed",
    created_at: "2026-11-04T09:00:00Z", updated_at: "2026-11-04T09:00:00Z",
    ...over,
  };
}

export function adopted(over: Partial<AdoptedRuleView> = {}): AdoptedRuleView {
  return {
    rule_id: "cus_77aa00bb11", name: "Expired passport", author: "bob", version: 1, latest_version: 1, update_available: false, active: true,
    severity: "low", template: "date_window", readback: "Flags low when a passport's date of expiry is before the case date.",
    adopted_at: "2026-11-03T10:00:00Z", label: "custom, unreviewed",
    ...over,
  };
}

export function shared(over: Partial<SharedRuleView> = {}): SharedRuleView {
  return {
    rule_id: "cus_77aa00bb11", name: "Expired passport", author: "bob", description: "Passports must be in date.", version: 1, severity: "low",
    template: "date_window", readback: "Flags low when a passport's date of expiry is before the case date.", published_at: "2026-11-03T09:00:00Z",
    own: false, adopted: false, adopted_version: null, adoptions: 3, label: "custom, unreviewed",
    ...over,
  };
}

export function ticket(over: Partial<CustomTicket> = {}): CustomTicket {
  return {
    id: "crt_9c0d1e2f3a4b", account: "alice", created_at: "2026-11-04T09:01:00Z", status: "submitted", origin: "unsupported",
    intent: "Flag companies that post about money problems on social media.", desired_severity: "low", doc_types: [],
    missing: [{ phrase: "posts on social media", reason: "doc_not_extracted" }],
    original_words: "flag if the company posts about money problems on social media", draft_id: "crd_5b2e9f0a1c3d", weaker_rule_id: null,
    ...over,
  };
}

export function runInfo(over: Partial<CustomRunInfo> = {}): CustomRunInfo {
  return {
    rule_id: "cus_3f9a1c2b7d", version: 2, spec_hash: "hash-debt", template: "fs_ratio", name: "Heavy borrowing", author: "alice",
    origin: "own", readback: DEBT_READBACK,
    ...over,
  };
}

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
export const errorBody = (code: string, message: string, status: number, details?: Record<string, unknown>) =>
  json({ error: { code, message, details } }, status);

export type Handler = (body: unknown, url: URL) => Response | Promise<Response>;
export interface FetchRecord { method: string; path: string; body: unknown }

/**
 * A fetch stand-in that answers by "METHOD /path" and records every request, so a test can show what the real API
 * client sent. An unknown route answers 500 with a message naming it (and fails the test through `unhandled`).
 */
export function routedFetch(routes: Record<string, Handler>) {
  const calls: FetchRecord[] = [];
  const unhandled: string[] = [];
  const fn = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input));
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path: url.pathname, body });
    const h = routes[`${method} ${url.pathname}`];
    if (!h) {
      unhandled.push(`${method} ${url.pathname}`);
      return errorBody("internal_error", `no route ${method} ${url.pathname}`, 500);
    }
    return h(body, url);
  };
  return { fn, calls, unhandled, count: (key: string) => calls.filter((c) => `${c.method} ${c.path}` === key).length };
}

/**
 * A case with two custom rules on it: "Heavy borrowing" (alice's own, raised one medium flag) and "Expired passport"
 * (adopted from bob, ran and found nothing), the way the backend stores them: ordinary rules-stage check runs and flags
 * with a `custom` stamp. Built on a case the caller passes in, so it fits whichever fixture the test needs.
 */
export function withCustomRuns(c: CaseDetail): CaseDetail {
  const flag: Flag = {
    id: "flag-custom-1", rule: "cus_3f9a1c2b7d", stage: "rules", severity: "medium", document_ids: c.documents[0] ? [c.documents[0].id] : [],
    extra_regions: [], evidence: "borrowings 1700000.00 is greater than 2 x total equity 1200000.00",
    explanation: "Custom rule (unreviewed): borrowings is greater than 2 times total equity.", disposition: "open",
  };
  const finding: Finding = {
    id: "finding-custom-1", group_key: "cus_3f9a1c2b7d", title: "Custom rule (unreviewed): borrowings is greater than 2 times total equity.",
    severity: "medium", flag_ids: [flag.id], document_ids: flag.document_ids, rules: ["cus_3f9a1c2b7d"], weight: 5, summary: "", hard_stop: false,
  };
  const runs: CheckRun[] = [
    { rule: "cus_3f9a1c2b7d", stage: "rules", status: "ran", flags: 1, seconds: 0, note: "Checked 1 financial statement: 1 matched.", custom: runInfo() },
    {
      rule: "cus_77aa00bb11", stage: "rules", status: "ran", flags: 0, seconds: 0, note: "Checked 1 passport: 0 matched.",
      custom: runInfo({ rule_id: "cus_77aa00bb11", name: "Expired passport", author: "bob", version: 1, origin: "adopted", template: "date_window", readback: "Flags low when a passport's date of expiry is before the case date." }),
    },
    {
      rule: "cus_5555555555", stage: "rules", status: "skipped", flags: 0, seconds: 0, reason: "no usable document of the rule's type", code: "custom_no_document",
      custom: runInfo({ rule_id: "cus_5555555555", name: "Big receipts", author: "alice", version: 1, template: "field_vs_constant", readback: "Flags low when a receipt's total is greater than 5,000.00." }),
    },
  ];
  return {
    ...c,
    flags: [...c.flags, flag],
    findings: [...c.findings, finding],
    check_runs: [...c.check_runs, ...runs],
    stages: c.stages.map((s) => (s.stage === "rules" ? { ...s, detail: { ...s.detail, custom_checks: 3, custom_flags: 1 } } : s)),
  };
}
