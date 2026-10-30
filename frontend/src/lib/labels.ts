import type { Band, CaseStatus, Lane, Severity, UseCase } from "./types";

/**
 * Plain-language wording for the officer-facing UI. Pure functions and constants only: the API enum
 * values (lane "edd", severity "info", ...) never change, only what the officer reads. Every page should
 * take its visible text for these concepts from here so the tool speaks with one voice.
 * (Words the UI must never use about a document are kept in wording.ts.)
 */

export const LANES: readonly Lane[] = ["loan", "edd", "aml", "fast"];

const LANE_LABEL: Record<Lane, string> = {
  loan: "Loan review",
  edd: "Enhanced due diligence",
  aml: "AML investigation",
  fast: "Fast lane",
};

const LANE_DESCRIPTION: Record<Lane, string> = {
  loan: "A credit decision: check the documents support the figures in the application.",
  edd: "A customer who needs deeper checks before onboarding or continued service.",
  aml: "Signs of possible money laundering or fraud; consider escalating to the AML team.",
  fast: "Low risk and well checked; a person still confirms before anything is approved.",
};

/** Short lane name, e.g. "Enhanced due diligence". */
export function laneLabel(lane: Lane): string {
  return LANE_LABEL[lane];
}

/** One sentence saying what the lane means for the officer. */
export function laneDescription(lane: Lane): string {
  return LANE_DESCRIPTION[lane];
}

const SEVERITY_LABEL: Record<Severity, string> = {
  high: "High concern",
  medium: "Medium concern",
  low: "Low concern",
  info: "For information",
};

/** Label for one finding or flag. */
export function severityLabel(severity: Severity): string {
  return SEVERITY_LABEL[severity];
}

const BAND_WORD: Record<Band, string> = { high: "High", medium: "Medium", low: "Low" };

/** The bare band word under the risk gauge: "High". */
export function bandWord(band: Band): string {
  return BAND_WORD[band];
}

/** The band for a whole case: "High risk". A partial score is a floor, so say so. */
export function bandLabel(band: Band, partial = false): string {
  return `${BAND_WORD[band]} risk${partial ? " (partial score)" : ""}`;
}

/** Spoken form of a score: "Risk score 90 of 100, high" or "Risk score at least 20 of 100, low". */
export function scoreSummary(score: number, band: Band, partial = false): string {
  return `Risk score ${partial ? "at least " : ""}${Math.round(score)} of 100, ${band}`;
}

const STATUS_LABEL: Record<CaseStatus, string> = {
  processing: "Being analysed",
  open: "Ready for review",
  awaiting_documents: "Waiting for documents",
  closed: "Closed",
  error: "Could not be analysed",
};

const STATUS_DESCRIPTION: Record<CaseStatus, string> = {
  processing: "The documents are still being read and checked. The score will appear when this finishes.",
  open: "Analysis is done. A reviewer has not yet closed this case.",
  awaiting_documents: "More documents were requested from the customer. Nothing to decide until they arrive.",
  closed: "A decision has been recorded.",
  error: "Something went wrong while checking this case. Try again or ask for help.",
};

export function caseStatusLabel(status: CaseStatus): string {
  return STATUS_LABEL[status];
}

export function caseStatusDescription(status: CaseStatus): string {
  return STATUS_DESCRIPTION[status];
}

const USE_CASE_LABEL: Record<UseCase, string> = {
  kyc: "Onboarding (KYC)",
  aml: "AML review",
  loan: "Loan application",
};

export function useCaseLabel(useCase: UseCase): string {
  return USE_CASE_LABEL[useCase];
}

// ---- jargon replacements (use these exact strings) ----

/** Was "hard stop". */
export const HARD_STOP_LABEL = "Blocks the fast lane";
/** Was "Received but not checked". */
export const UNCHECKED_PAGES_LABEL = "Pages we could not review";
/** Was "Dismiss". */
export const DISMISS_LABEL = "Mark as not a concern";
/** Was "Confirm". */
export const CONFIRM_LABEL = "Mark as a real concern";

export function plural(n: number, one: string, many = `${one}s`): string {
  return n === 1 ? one : many;
}

/** Was "41/54 checks". */
export function coverageText(ran: number, applicable: number): string {
  return `${ran} of ${applicable} ${plural(applicable, "check")} completed`;
}

/** "3 pages we could not review" (never "unchecked"). */
export function unreviewedPagesText(n: number): string {
  return `${n} ${plural(n, "page")} we could not review`;
}

/** Why no check looked at a page, in plain words. */
export const UNCHECKED_REASON_TEXT: Record<string, string> = {
  no_document: "It does not belong to any document we recognised.",
  unreadable: "The page could not be read.",
  not_checked: "Its document was received but not checked.",
  failed: "Its document could not be processed.",
  unrecognised_type: "It was not recognised as a document type we check.",
  low_confidence: "We were not confident what kind of page this is.",
  statement_incomplete: "It is part of a scanned statement we could not put back together, so it was not checked.",
};

/** Rule ids are secondary text only: "Check: bs_running_balance". */
export function ruleIdText(id: string): string {
  return `Check: ${id}`;
}
