import { documentTypeLabel, effectiveDescription, effectiveName, effectiveSeverity, isCustomised, isSwitchedOff, SEVERITY_ORDER, STAGES } from "./checkCatalogue";
import type { FlagStage, RuleInfo, Severity, UseCase } from "./types";

/** "customised" = anything differs from the defaults; "off" = switched off. */
export type SettingsFilter = "" | "customised" | "off";
export type CheckFilters = { q: string; docType: string; severity: Severity | ""; stage: FlagStage | ""; useCase: UseCase | ""; settings: SettingsFilter };

export const NO_FILTERS: CheckFilters = { q: "", docType: "", severity: "", stage: "", useCase: "", settings: "" };

/** How many checks there are, how many differ from the defaults and how many are switched off. */
export function catalogueCounts(rules: RuleInfo[]): { total: number; customised: number; off: number } {
  return { total: rules.length, customised: rules.filter(isCustomised).length, off: rules.filter(isSwitchedOff).length };
}

export const isFiltered = (f: CheckFilters) => Object.values(f).some((v) => v.trim() !== "");

/** Every document type any check names, for the filter list, readable name first. */
export function documentTypesOf(rules: RuleInfo[]): { id: string; label: string }[] {
  const ids = new Set<string>();
  for (const r of rules) for (const t of r.applies_to) ids.add(t);
  return [...ids].map((id) => ({ id, label: documentTypeLabel(id) })).sort((a, b) => a.label.localeCompare(b.label));
}

/** Search looks at the name shown (the officer's wording when set), the id, what it checks, the internal note and the tampering it catches. Severity means the one in force now. Checks for "any file" match every document type. */
export function filterChecks(rules: RuleInfo[], f: CheckFilters, nameOf: (id: string) => string): RuleInfo[] {
  const needle = f.q.trim().toLowerCase();
  return rules.filter(
    (r) =>
      (!f.stage || r.stage === f.stage) &&
      (!f.severity || effectiveSeverity(r) === f.severity) &&
      (!f.settings || (f.settings === "customised" ? isCustomised(r) : isSwitchedOff(r))) &&
      (!f.useCase || r.use_cases.includes(f.useCase)) &&
      (!f.docType || r.applies_to.includes(f.docType) || r.applies_to.some((t) => documentTypeLabel(t) === "Any file")) &&
      (!needle || `${effectiveName(r.rule, r.override, nameOf)} ${nameOf(r.rule)} ${r.rule} ${effectiveDescription(r)} ${r.override?.note ?? ""} ${r.tamper ?? ""}`.toLowerCase().includes(needle)),
  );
}

/** Stage order, then high concern first, then name. */
export function orderChecks(rules: RuleInfo[]): RuleInfo[] {
  const stageIx = (s: FlagStage) => STAGES.indexOf(s);
  return [...rules].sort((a, b) => stageIx(a.stage) - stageIx(b.stage) || SEVERITY_ORDER[effectiveSeverity(a)] - SEVERITY_ORDER[effectiveSeverity(b)] || a.rule.localeCompare(b.rule));
}
