import { describe, expect, it } from "vitest";
import { checkName } from "./checkCatalogue";
import { catalogueCounts, filterChecks, isFiltered, NO_FILTERS, orderChecks } from "./checkFilters";
import type { RuleInfo, RuleOverride } from "./types";

const ov = (over: Partial<RuleOverride>): RuleOverride => ({
  enabled: true, severity: null, name: null, description: null, note: null, updated_by: "a.tan", updated_at: "2026-10-27T10:00:00Z", reason: "because", revision: 1, ...over,
});
const r = (rule: string, severity: RuleInfo["severity"], override: RuleOverride | null = null, extra: Partial<RuleInfo> = {}): RuleInfo => ({
  rule, stage: "rules", severity, applies_to: ["bank_statement"], requires: [], use_cases: ["loan"], description: `desc of ${rule}`, tamper: null, version: "1",
  default_severity: severity, effective_severity: override?.severity ?? severity, enabled: override ? override.enabled : true, override, ...extra,
});

const rules = [
  r("bs_running_balance", "high", ov({ enabled: false })),
  r("inv_dates", "medium", ov({ severity: "low", name: "Dates in order", note: "ask credit" })),
  r("fs_arith", "high"),
  r("x_directors", "high", ov({})),
];

describe("settings filter and counts", () => {
  it("counts customised and switched off checks", () => {
    expect(catalogueCounts(rules)).toEqual({ total: 4, customised: 2, off: 1 });
  });

  it("filters to customised checks (a bare override does not count)", () => {
    const out = filterChecks(rules, { ...NO_FILTERS, settings: "customised" }, checkName);
    expect(out.map((x) => x.rule).sort()).toEqual(["bs_running_balance", "inv_dates"]);
  });

  it("filters to switched-off checks", () => {
    expect(filterChecks(rules, { ...NO_FILTERS, settings: "off" }, checkName).map((x) => x.rule)).toEqual(["bs_running_balance"]);
  });

  it("treats the settings filter as an active filter", () => {
    expect(isFiltered(NO_FILTERS)).toBe(false);
    expect(isFiltered({ ...NO_FILTERS, settings: "off" })).toBe(true);
  });

  it("filters by the severity in force now, not the default", () => {
    expect(filterChecks(rules, { ...NO_FILTERS, severity: "low" }, checkName).map((x) => x.rule)).toEqual(["inv_dates"]);
    expect(filterChecks(rules, { ...NO_FILTERS, severity: "medium" }, checkName)).toHaveLength(0);
  });

  it("searches the custom name, the note and the plain name", () => {
    expect(filterChecks(rules, { ...NO_FILTERS, q: "dates in order" }, checkName).map((x) => x.rule)).toEqual(["inv_dates"]);
    expect(filterChecks(rules, { ...NO_FILTERS, q: "ask credit" }, checkName).map((x) => x.rule)).toEqual(["inv_dates"]);
    expect(filterChecks(rules, { ...NO_FILTERS, q: "invoice: dates" }, checkName).map((x) => x.rule)).toEqual(["inv_dates"]);
  });

  it("orders by the severity in force now", () => {
    const sorted = orderChecks([r("a_one", "high", ov({ severity: "info" })), r("b_two", "low")]);
    expect(sorted.map((x) => x.rule)).toEqual(["b_two", "a_one"]);
  });
});
