import { describe, expect, it } from "vitest";
import { isNoisy, rulesToCsv, sortRules, splitNeverFired, stackedText } from "./effectiveness";
import type { RuleEffectiveness } from "./types";

const rule = (o: Partial<RuleEffectiveness>): RuleEffectiveness => ({
  rule: "r", stage: "rules", default_severity: "medium", description: "d", cases_fired: 1, times_fired: 1, confirmed: 0, dismissed: 0,
  pending: 0, open: 0, dismissal_rate: null, median_seconds_to_decide: null, cases_ran: 1, cases_skipped: 0, ...o,
});

describe("effectiveness helpers", () => {
  it("marks Noisy? at 5 decided and 50% dismissed", () => {
    expect(isNoisy(rule({ confirmed: 3, dismissed: 3, dismissal_rate: 0.5 }))).toBe(true);
    expect(isNoisy(rule({ confirmed: 2, dismissed: 2, dismissal_rate: 0.5 }))).toBe(false);
    expect(isNoisy(rule({ confirmed: 5, dismissed: 1, dismissal_rate: 0.17 }))).toBe(false);
    expect(isNoisy(rule({ confirmed: 0, dismissed: 0, dismissal_rate: null }))).toBe(false);
  });
  it("sorts and groups", () => {
    const rs = [rule({ rule: "b", times_fired: 1 }), rule({ rule: "a", times_fired: 5, dismissal_rate: 0.2 }), rule({ rule: "c", times_fired: 0 })];
    expect(sortRules(rs, "times_fired").map((r) => r.rule)).toEqual(["a", "b", "c"]);
    expect(sortRules(rs, "rule").map((r) => r.rule)).toEqual(["a", "b", "c"]);
    expect(sortRules(rs, "dismissal_rate")[0].rule).toBe("a");
    const g = splitNeverFired(rs);
    expect(g.never.map((r) => r.rule)).toEqual(["c"]);
    expect(g.fired).toHaveLength(2);
  });
  it("text equivalent and CSV", () => {
    expect(stackedText({ confirmed: 5, dismissed: 1, pending: 0, open: 1 })).toBe("5 confirmed, 1 dismissed, 0 pending, 1 open");
    const csv = rulesToCsv([rule({ rule: "=evil()", confirmed: 5, dismissed: 5, dismissal_rate: 0.5 }), rule({ rule: "plain" })]);
    const lines = csv.trim().split("\n");
    expect(lines).toHaveLength(3);
    expect(lines[1].startsWith("'=evil()")).toBe(true);
    expect(lines[1].endsWith(",yes")).toBe(true);
  });
});
