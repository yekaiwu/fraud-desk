import type { Lane, UseCase } from "./types";

/**
 * The documents an officer usually needs for each kind of case, and a rough, honest guess at which of
 * them a set of files covers. The guess uses FILE NAMES AND TYPES ONLY: the system reads the real content
 * after upload, and a missing item never blocks the upload.
 */

export type CaseType = {
  id: UseCase;
  /** Card title and summary wording. */
  title: string;
  /** One line on what this kind of case is. */
  blurb: string;
  /** The lane the analysis normally routes to. */
  lane: Lane;
};

export const CASE_TYPES: readonly CaseType[] = [
  { id: "loan", title: "Loan application", blurb: "Check the documents support the figures a business gave to get credit.", lane: "loan" },
  { id: "kyc", title: "Corporate onboarding (KYC)", blurb: "Know a new company customer: who owns it, who runs it, and that its papers agree.", lane: "edd" },
  { id: "aml", title: "Source of funds (AML)", blurb: "Check where the money came from and that the paperwork supports it.", lane: "aml" },
];

export function caseTypeOf(id: UseCase): CaseType {
  return CASE_TYPES.find((c) => c.id === id) ?? CASE_TYPES[0];
}

export type ChecklistItem = {
  id: string;
  label: string;
  /** What to look for or how many; shown under the label. */
  hint: string;
  /** Matched against the file name with separators turned into spaces, lower-case. */
  match: RegExp;
};

const FINANCIALS: ChecklistItem = {
  id: "financial_statements",
  label: "Financial statements",
  hint: "Usually the last 2 years",
  match: /\b(financials?|financial statements?|balance sheets?|income statements?|profit (and|&) loss|p ?& ?l|annual reports?|audited|afs|fs ?(fy)?\d{2,4})\b/,
};
const BANK: ChecklistItem = {
  id: "bank_statements",
  label: "Bank statements",
  hint: "Usually 3 to 6 months",
  match: /\b(bank|bs|stmt|(?<!financial )statements?|passbook|dbs|ocbc|uob|posb|maybank|citi|hsbc|account|acct)\b/,
};
const INVOICES: ChecklistItem = {
  id: "invoices",
  label: "Invoices",
  hint: "Those that back the figures or the transactions",
  match: /\b(invoices?|inv ?\d*|tax invoices?|bills?|receipts?)\b/,
};
const CONTRACTS: ChecklistItem = {
  id: "contracts",
  label: "Contracts or purchase orders",
  hint: "Agreements the invoices relate to",
  match: /\b(contracts?|agreements?|purchase orders?|po ?\d*|lease|loa|loi|sale and purchase|spa)\b/,
};
const PROFILE: ChecklistItem = {
  id: "company_profile",
  label: "Company profile",
  hint: "A registry extract, such as a business profile",
  match: /\b(company|business|acra|bizfile)? ?(profile|extract|info(rmation)? sheet)\b/,
};
const CERT: ChecklistItem = {
  id: "certificate",
  label: "Certificate of incorporation",
  hint: "Proof the company was formed",
  match: /\b(incorporation|certificate|cert|coi)\b/,
};
const OWNERSHIP: ChecklistItem = {
  id: "ownership_chart",
  label: "Ownership chart",
  hint: "Who owns the company, including through other companies",
  match: /\b(ownership|org(anisation|anization)? chart|structure chart|group structure|structure|ubo)\b/,
};
const REGISTER: ChecklistItem = {
  id: "shareholder_register",
  label: "Shareholder register",
  hint: "The list of shareholders and their holdings",
  match: /\b(registers?|shareholders?|shareholding|members)\b/,
};
const IDS: ChecklistItem = {
  id: "ids",
  label: "Directors' and owners' IDs",
  hint: "NRIC, FIN or passport for each director and owner",
  match: /\b(nric|fin|passport|identity|id|ic|national id|driver'?s? licen[sc]e|licen[sc]e)\b/,
};
const SOF: ChecklistItem = {
  id: "sof_declaration",
  label: "Source-of-funds declaration",
  hint: "The customer's own statement of where the money came from",
  match: /\b(source of funds|sof|source of wealth|sow|declaration|funds)\b/,
};
const ID_DOCS: ChecklistItem = { ...IDS, label: "ID documents", hint: "NRIC, FIN or passport for the people involved" };

// Order matters: a file is credited to the FIRST item it matches, so the narrow ones come before "bank".
export const CHECKLISTS: Record<UseCase, readonly ChecklistItem[]> = {
  loan: [FINANCIALS, INVOICES, CONTRACTS, BANK],
  kyc: [CERT, PROFILE, OWNERSHIP, REGISTER, IDS, BANK],
  aml: [SOF, INVOICES, CONTRACTS, ID_DOCS, BANK],
};

/** Display order differs from match order: show financials first, bank statements second for a loan. */
const DISPLAY_ORDER: Record<UseCase, readonly string[]> = {
  loan: ["financial_statements", "bank_statements", "invoices", "contracts"],
  kyc: ["company_profile", "certificate", "ownership_chart", "shareholder_register", "ids", "bank_statements"],
  aml: ["sof_declaration", "bank_statements", "invoices", "contracts", "ids"],
};

export type ChecklistRow = {
  id: string;
  label: string;
  hint: string;
  /** Names of the files that look like this kind of document. */
  files: string[];
  found: boolean;
};

export type Recognition = {
  rows: ChecklistRow[];
  /** file entry id -> checklist item label, for the per-file "Looks like" text. */
  byFile: Map<number, string>;
  /** Names the heuristic could not place. They are still uploaded and read. */
  unplaced: string[];
  foundCount: number;
  missing: ChecklistRow[];
};

const normaliseName = (name: string) =>
  name
    .replace(/\.[a-z0-9]{2,5}$/i, "")
    .replace(/[_\-.()[\]+]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .toLowerCase();

/**
 * Which usual documents do these files probably cover? Names and types only, so treat the result as a
 * helpful guess. A photo (JPEG or PNG) with no recognisable name is credited to the ID item if the case type has one.
 */
export function recogniseFiles(useCase: UseCase, files: { id: number; name: string; type?: string }[]): Recognition {
  const items = CHECKLISTS[useCase];
  const hit = new Map<string, string[]>(items.map((i) => [i.id, []]));
  const byFile = new Map<number, string>();
  const unplaced: string[] = [];
  const idItem = items.find((i) => i.id === "ids");
  for (const f of files) {
    const n = normaliseName(f.name);
    let item = items.find((i) => i.match.test(n));
    if (!item && idItem && /\.(jpe?g|png)$/i.test(f.name)) item = idItem;
    if (item) {
      hit.get(item.id)!.push(f.name);
      byFile.set(f.id, item.label);
    } else unplaced.push(f.name);
  }
  const rows = DISPLAY_ORDER[useCase].map((id) => {
    const item = items.find((i) => i.id === id)!;
    const names = hit.get(id) ?? [];
    return { id, label: item.label, hint: item.hint, files: names, found: names.length > 0 };
  });
  return { rows, byFile, unplaced, foundCount: rows.filter((r) => r.found).length, missing: rows.filter((r) => !r.found) };
}
