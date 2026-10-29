/**
 * Sample data and the arithmetic behind the /welcome demos. Everything here is fictitious.
 *
 * The page never ships a pre-baked result: the statement is stored as printed (opening balance, one
 * amount per row, the balance printed after the row), and the check below recomputes every row from
 * the previous printed balance. Money is whole cents (integers), formatted without floating point.
 */
import type { Band, Severity } from "@/lib/types";

// ---------- money ----------

/** 1157450 -> "11,574.50". Whole cents in, text out; no floating point. */
export function formatCents(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(Math.trunc(cents));
  const whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${whole}.${String(abs % 100).padStart(2, "0")}`;
}

// ---------- the sample statement ----------

export type StatementRow = {
  id: string;
  date: string;
  description: string;
  /** Money in, in cents (0 when the row is a payment out). */
  credit: number;
  /** Money out, in cents. */
  debit: number;
  /** The running balance printed on the statement after this row. */
  printed: number;
};

export type Statement = { company: string; account: string; period: string; opening: number; rows: StatementRow[] };

/** The one row that differs between the two versions of the statement. */
export const EDITED_ROW_ID = "r4";
export const ORIGINAL_CREDIT = 400_000; // 4,000.00 as the bank issued it
export const EDITED_CREDIT = 1_400_000; // 14,000.00 after someone typed an extra digit

const BASE_ROWS: StatementRow[] = [
  { id: "r1", date: "02 Sep", description: "Receipt INV-2208", credit: 620_000, debit: 0, printed: 2_465_000 },
  { id: "r2", date: "04 Sep", description: "Payroll, August", credit: 0, debit: 980_000, printed: 1_485_000 },
  { id: "r3", date: "09 Sep", description: "Harbour Fuel Pte. Ltd.", credit: 0, debit: 327_550, printed: 1_157_450 },
  // The printed balance after r4 stays as the bank issued it, whatever amount is printed on the row.
  { id: EDITED_ROW_ID, date: "12 Sep", description: "Receipt INV-2211", credit: ORIGINAL_CREDIT, debit: 0, printed: 1_557_450 },
  { id: "r5", date: "16 Sep", description: "Office rent", credit: 0, debit: 420_000, printed: 1_137_450 },
  { id: "r6", date: "23 Sep", description: "Receipt INV-2214", credit: 535_000, debit: 0, printed: 1_672_450 },
  { id: "r7", date: "29 Sep", description: "Utilities", credit: 0, debit: 61_275, printed: 1_611_175 },
];

export function sampleStatement(edited: boolean): Statement {
  return {
    company: "Kallang Freight Services Pte. Ltd.",
    account: "Current account ending 4471",
    period: "1 to 30 September",
    opening: 1_845_000,
    rows: BASE_ROWS.map((r) => (edited && r.id === EDITED_ROW_ID ? { ...r, credit: EDITED_CREDIT } : r)),
  };
}

// ---------- check: the running balance ----------

export type RowCheck = {
  rowId: string;
  /** The balance printed on the row above (the opening balance for the first row). */
  previous: number;
  /** Credit minus debit, in cents. */
  change: number;
  expected: number;
  printed: number;
  ok: boolean;
  /** printed minus expected. */
  difference: number;
};

/** Recompute each row from the previous printed balance and compare with the printed one. */
export function checkRunningBalance(statement: Statement): RowCheck[] {
  let previous = statement.opening;
  return statement.rows.map((row) => {
    const change = row.credit - row.debit;
    const expected = previous + change;
    const result: RowCheck = { rowId: row.id, previous, change, expected, printed: row.printed, ok: expected === row.printed, difference: row.printed - expected };
    previous = row.printed;
    return result;
  });
}

export const RUNNING_BALANCE_FINDING = "Running balance does not add up from this row";

export type DemoFinding = { severity: Severity; title: string; failedRowIds: string[]; blocksFastLane: boolean };

/** Related flags are grouped: however many rows fail, the statement raises one finding. */
export function findingsFor(checks: RowCheck[]): DemoFinding[] {
  const failed = checks.filter((c) => !c.ok);
  if (failed.length === 0) return [];
  return [{ severity: "high", title: RUNNING_BALANCE_FINDING, failedRowIds: failed.map((c) => c.rowId), blocksFastLane: true }];
}

/** The coverage the demo shows for a statement (fixed: it is the bundle's coverage, not a result of this check). */
export const DEMO_COVERAGE = { ran: 41, applicable: 54 } as const;

// ---------- the score ----------

/** The documented defaults. */
export const WEIGHTS: Record<Severity, number> = { high: 30, medium: 15, low: 5, info: 0 };
export const SCORE_CAP = 100;
export const MEDIUM_FROM = 30;
export const HIGH_FROM = 60;

/** Sum of the weights, one per finding, before the cap. */
export function rawScore(severities: readonly Severity[]): number {
  return severities.reduce((sum, s) => sum + WEIGHTS[s], 0);
}

export function scoreOf(severities: readonly Severity[]): number {
  return Math.min(SCORE_CAP, rawScore(severities));
}

export function bandOf(score: number): Band {
  if (score >= HIGH_FROM) return "high";
  if (score >= MEDIUM_FROM) return "medium";
  return "low";
}

/** Fictitious findings for the calculator. Each counts once, however many documents it is seen on. */
export type SampleFinding = { id: string; severity: Severity; text: string; seen: string };

export const SAMPLE_FINDINGS: SampleFinding[] = [
  { id: "f1", severity: "high", text: "The printed gross profit does not equal the sum of its lines", seen: "Seen on 1 document" },
  { id: "f2", severity: "high", text: "A director named in the application is not in the company registry", seen: "Seen on 2 documents" },
  { id: "f3", severity: "medium", text: "Bank deposits support only 37% of the revenue reported", seen: "Seen on 7 documents" },
  { id: "f4", severity: "medium", text: "The invoice customer is not the party that paid", seen: "Seen on 3 documents" },
  { id: "f5", severity: "medium", text: "Declared business is logistics, but statements show crypto counterparties", seen: "Seen on 2 documents" },
  { id: "f6", severity: "low", text: "The company address is spelled two ways", seen: "Seen on 2 documents" },
  { id: "f7", severity: "low", text: "An invoice is dated on a public holiday", seen: "Seen on 1 document" },
  { id: "f8", severity: "info", text: "The statements cover 5 of the 6 months requested", seen: "Seen on 1 document" },
];
