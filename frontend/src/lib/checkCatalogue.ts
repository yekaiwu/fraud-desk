import type { FlagStage, RuleInfo, RuleOverride, Severity } from "./types";

/**
 * Plain-language names for the check catalogue and the performance page. The API sends rule ids
 * ("bs_running_balance") and document-type ids ("bank_statement"); the officer reads these instead.
 * The id stays visible as small secondary text so an auditor can still quote it.
 */

const DOC_TYPE_LABEL: Record<string, string> = {
  bank_statement: "Bank statement",
  financial_statement: "Financial statements",
  invoice: "Invoice",
  contract: "Contract",
  purchase_order: "Purchase order",
  company_profile: "Company profile",
  certificate_of_incorporation: "Certificate of incorporation",
  ownership_chart: "Ownership chart",
  shareholder_register: "Shareholder register",
  source_of_funds_declaration: "Source-of-funds declaration",
  national_id: "National ID",
  passport: "Passport",
  boilerplate: "Boilerplate pages",
  other: "Other",
};

const ANY_FILE = new Set(["any file", "any", "*", "all"]);

function sentence(s: string): string {
  const t = s.replace(/_/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

/** "bank_statement" -> "Bank statement"; unknown ids are humanised, never hidden. */
export function documentTypeLabel(id: string): string {
  if (ANY_FILE.has(id.toLowerCase())) return "Any file";
  return DOC_TYPE_LABEL[id] ?? sentence(id);
}

const STAGE_LABEL: Record<FlagStage, string> = {
  forensics: "File forensics",
  rules: "Document rules",
  crossdoc: "Across documents",
};

const STAGE_HINT: Record<FlagStage, string> = {
  forensics: "Looks at how the file itself was made or changed.",
  rules: "Checks the numbers and details inside one document.",
  crossdoc: "Compares what different documents say about the same thing.",
};

export const STAGES: readonly FlagStage[] = ["forensics", "rules", "crossdoc"];

export function stageLabel(stage: FlagStage): string {
  return STAGE_LABEL[stage] ?? sentence(String(stage));
}

export function stageHint(stage: FlagStage): string {
  return STAGE_HINT[stage] ?? "";
}

const PREFIX: Record<string, string> = {
  bs: "Statement",
  fs: "Financials",
  inv: "Invoice",
  id: "ID",
  pdf: "PDF",
  kyc: "KYC",
  doc: "Document",
  x: "",
};

const WORDS: Record<string, string> = {
  bs: "bank statement",
  vs: "vs",
  sof: "source of funds",
  gst: "GST",
  uen: "UEN",
  ids: "IDs",
  id: "ID",
  pdf: "PDF",
  ai: "AI",
  ela: "error-level analysis",
  mrz: "passport machine-readable zone",
  kyc: "KYC",
  aml: "AML",
  nric: "NRIC",
};

/**
 * A readable name for a check id: "bs_running_balance" -> "Statement: running balance",
 * "x_invoice_vs_receipts" -> "Invoice vs receipts". Derived, so a new check never shows as a blank.
 */
export function checkName(rule: string): string {
  const parts = rule.split("_").filter(Boolean);
  if (parts.length === 0) return rule;
  const head = PREFIX[parts[0]];
  const body = (head === undefined ? parts : parts.slice(1)).map((p) => WORDS[p] ?? p).join(" ");
  if (head) return `${head}: ${body}`;
  return sentence(body);
}

/** Sort order for severity columns: high first. */
export const SEVERITY_ORDER = { high: 0, medium: 1, low: 2, info: 3 } as const;

/* ---- Officer-edited settings (the check catalogue can be customised; the code defaults stay underneath) ---- */

/** Limits of the free-text settings. The server checks them too. */
export const NAME_MAX = 80;
export const DESCRIPTION_MAX = 400;
export const NOTE_MAX = 600;

const clean = (v: string | null | undefined): string | null => {
  const t = v?.trim();
  return t ? t : null;
};

/** The name to show: the officer's wording when set, else the derived plain name. `fallback` lets a caller keep its own derived name. */
export function effectiveName(rule: string, override?: Pick<RuleOverride, "name"> | null, fallback: (id: string) => string = checkName): string {
  return clean(override?.name) ?? fallback(rule);
}

/** "What it checks": the officer's wording when set, else the code's description. */
export function effectiveDescription(r: Pick<RuleInfo, "description" | "override">): string {
  return clean(r.override?.description) ?? r.description;
}

/** The code's own severity, whatever the officer has set. */
export function defaultSeverity(r: Pick<RuleInfo, "severity" | "default_severity">): Severity {
  return r.default_severity ?? r.severity;
}

/** The severity analyses use now. An older backend only sends `severity`. */
export function effectiveSeverity(r: Pick<RuleInfo, "severity" | "default_severity" | "effective_severity" | "override">): Severity {
  return r.effective_severity ?? r.override?.severity ?? r.severity;
}

/** Switched on unless the catalogue says otherwise. */
export function isEnabled(r: Pick<RuleInfo, "enabled" | "override">): boolean {
  return r.enabled ?? r.override?.enabled ?? true;
}

/** True when anything differs from the defaults (an override with every field at its default does not count). */
export function isCustomised(r: Pick<RuleInfo, "override" | "enabled">): boolean {
  const o = r.override;
  if (!o) return r.enabled === false;
  return o.enabled === false || !!o.severity || !!clean(o.name) || !!clean(o.description) || !!clean(o.note);
}

export const isSwitchedOff = (r: Pick<RuleInfo, "enabled" | "override">): boolean => !isEnabled(r);

/** Skip reason the engine uses for a check that was switched off. */
export const SWITCHED_OFF_REASON = "switched_off";
export const SWITCHED_OFF_TEXT = "Switched off in the check catalogue";
export const isSwitchedOffReason = (reason: string): boolean => /^switched[_ ]off\b/i.test(reason.trim());
