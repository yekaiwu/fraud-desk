/**
 * Small pure helpers for custom rules (docs/custom-rules.md): how to tell a custom rule's run from a built-in's, the
 * words for each authoring status, and error wording that is specific to the rule assistant. Everything an officer
 * or the model wrote is plain text here: nothing is parsed as markup.
 */
import { explainError, type ApiErrorBody, type ExplainedError } from "./errors";
import type { BadgeVariant } from "@/components/ui";
import type { CaseDetail, CheckRun, CustomRunInfo, CustomSeverity, DraftStatus, DryRunResult, TemplateId, UnsupportedReason } from "./types";

export const CUSTOM_RULE_PREFIX = "cus_";
export const CUSTOM_SEVERITIES: CustomSeverity[] = ["info", "low", "medium"];
/** Mirrors the backend limits (spec.py); the capabilities endpoint reports them too. */
export const REQUEST_MAX_CHARS = 1000;
export const REQUEST_MIN_CHARS = 8;
export const NAME_MIN = 3;
export const NAME_MAX = 80;
export const DESCRIPTION_MAX = 400;
export const REASON_MIN = 5;
export const REASON_MAX = 500;
export const TURN_LIMIT = 6;

export function isCustomRule(rule: string): boolean {
  return rule.startsWith(CUSTOM_RULE_PREFIX);
}

/** The check run that carries the custom stamp for this rule on this case, if the case ran it. */
export function customRunOf(c: Pick<CaseDetail, "check_runs"> | null | undefined, rule: string): CheckRun | undefined {
  if (!c || !isCustomRule(rule)) return undefined;
  return c.check_runs.find((r) => r.rule === rule && r.custom);
}

/** "Custom, by alice, v2, unreviewed" */
export function customLabel(info: Pick<CustomRunInfo, "author" | "version">): string {
  return `Custom, by ${info.author}, v${info.version}, unreviewed`;
}

/** One sentence for a flag of a custom rule, shown where a built-in check's name would be. */
export function customFlagLine(info: Pick<CustomRunInfo, "author">): string {
  return `Custom rule by ${info.author} (unreviewed)`;
}

export const STATUS_TITLE: Record<DraftStatus, string> = {
  ready: "Ready to save",
  needs_clarification: "A few questions first",
  ambiguous: "Which of these do you mean?",
  partially_supported: "Part of this can be checked",
  unsupported: "This is beyond what a custom rule can do",
  refused: "This request cannot be turned into a rule",
};

export function statusTone(s: DraftStatus): BadgeVariant {
  switch (s) {
    case "ready":
      return "low";
    case "needs_clarification":
    case "ambiguous":
      return "attention";
    case "partially_supported":
      return "medium";
    case "unsupported":
      return "info";
    case "refused":
      return "high";
  }
}

export const TEMPLATE_LABEL: Record<TemplateId, string> = {
  field_vs_constant: "A field against a fixed value",
  date_window: "A date against the case date",
  field_missing: "A field that is missing",
  fs_ratio: "Financial statement lines against each other",
};

/** Mirrors UNSUPPORTED_REASON_TEXT in the backend (spec.py), for tickets, which carry only the reason code. */
export const UNSUPPORTED_TEXT: Record<UnsupportedReason, string> = {
  field_not_extracted: "the system does not read this from the documents",
  doc_not_extracted: "the system does not read this kind of document",
  needs_external: "it needs outside data or a lookup, which custom rules cannot use",
  needs_trend: "it needs a trend over more than this year and last year",
  needs_image: "it needs the picture of the page (images, logos, layout, signatures)",
  needs_judgement: "it needs a judgement that cannot be written as a fixed condition",
  needs_cross_case: "it compares with other cases, which custom rules cannot see",
  needs_cross_document: "it compares two documents; a custom rule reads one document type",
  needs_algorithm: "it needs counting, grouping or matching that the templates do not do",
  needs_several_conditions: "it needs more than one condition; a custom rule has exactly one",
  policy_forbidden: "custom rules are not allowed to do this",
};

export const TICKET_STATUS_TEXT: Record<string, string> = {
  submitted: "Submitted to the tech team",
  acknowledged: "Seen by the tech team",
  done: "Done",
  declined: "Declined",
};

export const ORIGIN_TEXT: Record<string, string> = {
  unsupported: "Could not be built as a rule",
  partial: "The part a rule could not check",
  officer: "Sent by you",
};

export function percent(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}

/** The dry run in one sentence. */
export function dryRunSentence(d: DryRunResult): string {
  const skipped = d.skipped_missing > 0 ? `, skipped ${d.skipped_missing} because a field was missing` : "";
  return `Evaluated ${d.evaluated} stored ${d.evaluated === 1 ? "case" : "cases"}, fired on ${d.fired}${skipped}.`;
}

/**
 * Error wording for the rule assistant. Most codes are explained in errors.ts; the daily budget is worded for authoring
 * here, because the generic text points the officer to open cases.
 */
export function explainCustomError(e: ApiErrorBody): ExplainedError {
  if (e.code === "user_budget_used") {
    return {
      title: "Your model budget for today is used up",
      detail: "The rule assistant is unavailable until tomorrow. Listing, adopting and deleting rules and sending an idea to the tech team still work, and so does casework.",
      retryable: false,
    };
  }
  return explainError(e);
}

/** Authoring cannot be used right now (budget, backend): the idea can still go to the tech team. */
export function authoringDown(code: string): boolean {
  return code === "user_budget_used" || code === "site_budget_used" || code === "authoring_unavailable";
}

/** The custom rules that ran on a case, and how they went. They sit outside the coverage figures. */
export function customRunTally(c: Pick<CaseDetail, "check_runs">): { total: number; ran: number; skipped: number; flags: number } {
  const runs = c.check_runs.filter((r) => r.custom);
  return {
    total: runs.length,
    ran: runs.filter((r) => r.status === "ran").length,
    skipped: runs.filter((r) => r.status !== "ran").length,
    flags: runs.reduce((n, r) => n + r.flags, 0),
  };
}

/** "Custom checks: 3 ran, 1 skipped. Not counted in coverage." */
export function customCoverageText(t: { ran: number; skipped: number }): string {
  return `Custom checks: ${t.ran} ran, ${t.skipped} skipped. Not counted in coverage.`;
}
