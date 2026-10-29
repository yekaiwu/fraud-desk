import { NOISY_MIN_DECIDED, NOISY_RATE } from "./constants";
import type { RuleEffectiveness } from "./types";

export type RuleSort = "times_fired" | "dismissal_rate" | "rule" | "median_time";

export type SortDir = "asc" | "desc";

/** Numbers read best biggest first; names and times read best A to Z and quickest first. */
export const DEFAULT_SORT_DIR: Record<RuleSort, SortDir> = { times_fired: "desc", dismissal_rate: "desc", rule: "asc", median_time: "asc" };

/** "Noisy?" hint: enough decisions and most were dismissed (frontend.md 5.4). */
export function isNoisy(r: RuleEffectiveness): boolean {
  return r.confirmed + r.dismissed >= NOISY_MIN_DECIDED && r.dismissal_rate !== null && r.dismissal_rate >= NOISY_RATE;
}

/** Missing values (no decisions yet) always sort last, whichever way round. */
function cmpNullable(a: number | null, b: number | null, dir: SortDir): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return dir === "asc" ? a - b : b - a;
}

export function sortRules(rules: RuleEffectiveness[], sort: RuleSort, dir: SortDir = DEFAULT_SORT_DIR[sort]): RuleEffectiveness[] {
  const out = [...rules];
  out.sort((x, y) => {
    let c = 0;
    if (sort === "rule") c = dir === "asc" ? x.rule.localeCompare(y.rule) : y.rule.localeCompare(x.rule);
    else if (sort === "dismissal_rate") c = cmpNullable(x.dismissal_rate, y.dismissal_rate, dir);
    else if (sort === "median_time") c = cmpNullable(x.median_seconds_to_decide, y.median_seconds_to_decide, dir);
    else c = dir === "asc" ? x.times_fired - y.times_fired : y.times_fired - x.times_fired;
    return c || y.times_fired - x.times_fired || x.rule.localeCompare(y.rule);
  });
  return out;
}

/** Headline numbers for the summary tiles, computed only from what the API already returned. */
export function summarise(rules: RuleEffectiveness[]) {
  const confirmed = rules.reduce((n, r) => n + r.confirmed, 0);
  const dismissed = rules.reduce((n, r) => n + r.dismissed, 0);
  const decisions = confirmed + dismissed;
  return {
    registered: rules.length,
    fired: rules.filter((r) => r.times_fired > 0).length,
    flagsRaised: rules.reduce((n, r) => n + r.times_fired, 0),
    confirmed,
    dismissed,
    decisions,
    confirmedShare: decisions > 0 ? confirmed / decisions : null,
    noisy: rules.filter(isNoisy),
  };
}

export function splitNeverFired(rules: RuleEffectiveness[]): { fired: RuleEffectiveness[]; never: RuleEffectiveness[] } {
  return { fired: rules.filter((r) => r.times_fired > 0), never: rules.filter((r) => r.times_fired === 0) };
}

export function stackedText(r: Pick<RuleEffectiveness, "confirmed" | "dismissed" | "pending" | "open">): string {
  return `${r.confirmed} confirmed, ${r.dismissed} dismissed, ${r.pending} pending, ${r.open} open`;
}

function csvCell(v: string | number | null): string {
  const s = v === null ? "" : String(v);
  // Quote everything that could break a row; a leading = + - @ is neutralised against spreadsheet formulas.
  const safe = /^[=+\-@]/.test(s) && Number.isNaN(Number(s)) ? `'${s}` : s;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export const CSV_HEADER = [
  "rule", "stage", "default_severity", "times_fired", "cases_fired", "confirmed", "dismissed", "pending", "open",
  "dismissal_rate", "median_seconds_to_decide", "cases_ran", "cases_skipped", "noisy",
];

export function rulesToCsv(rules: RuleEffectiveness[]): string {
  const rows = rules.map((r) =>
    [r.rule, r.stage, r.default_severity, r.times_fired, r.cases_fired, r.confirmed, r.dismissed, r.pending, r.open,
      r.dismissal_rate, r.median_seconds_to_decide, r.cases_ran, r.cases_skipped, isNoisy(r) ? "yes" : "no"].map(csvCell).join(","),
  );
  return [CSV_HEADER.join(","), ...rows].join("\n") + "\n";
}
