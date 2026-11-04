import { describe, expect, it } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { runInfo } from "@/test/customRulesFixtures";
import {
  authoringDown,
  customCoverageText,
  customFlagLine,
  customLabel,
  customRunOf,
  customRunTally,
  dryRunSentence,
  explainCustomError,
  isCustomRule,
  percent,
  STATUS_TITLE,
  statusTone,
  TEMPLATE_LABEL,
  UNSUPPORTED_TEXT,
} from "./customRules";
import type { CaseDetail, DraftStatus } from "./types";

const withRuns = (): CaseDetail => {
  const c = buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);
  c.check_runs = [
    ...c.check_runs,
    { rule: "cus_3f9a1c2b7d", stage: "rules", status: "ran", flags: 1, seconds: 0, note: "Checked 1 statement: 1 matched.", custom: runInfo() },
    { rule: "cus_77aa00bb11", stage: "rules", status: "skipped", flags: 0, seconds: 0, reason: "no passport", code: "custom_no_document", custom: runInfo({ rule_id: "cus_77aa00bb11", author: "bob", origin: "adopted" }) },
  ];
  return c;
};

describe("custom rule helpers", () => {
  it("tells custom rule ids from built-in ones", () => {
    expect(isCustomRule("cus_3f9a1c2b7d")).toBe(true);
    expect(isCustomRule("inv_dates")).toBe(false);
    expect(isCustomRule("sem_entity")).toBe(false);
  });

  it("finds the stamped run of a custom rule on a case, and nothing for a built-in or an unknown rule", () => {
    const c = withRuns();
    expect(customRunOf(c, "cus_3f9a1c2b7d")?.custom?.author).toBe("alice");
    expect(customRunOf(c, "cus_ffffffffff")).toBeUndefined();
    expect(customRunOf(c, "inv_dates")).toBeUndefined();
    expect(customRunOf(null, "cus_3f9a1c2b7d")).toBeUndefined();
  });

  it("labels a custom rule with its author and version, always as unreviewed", () => {
    expect(customLabel({ author: "alice", version: 2 })).toBe("Custom, by alice, v2, unreviewed");
    expect(customFlagLine({ author: "alice" })).toBe("Custom rule by alice (unreviewed)");
  });

  it("has a title and a tone for every draft status", () => {
    const all: DraftStatus[] = ["ready", "needs_clarification", "ambiguous", "partially_supported", "unsupported", "refused"];
    for (const s of all) {
      expect(STATUS_TITLE[s]).toBeTruthy();
      expect(statusTone(s)).toBeTruthy();
    }
    expect(Object.keys(STATUS_TITLE).sort()).toEqual([...all].sort());
  });

  it("words every reason an idea cannot be built, and every template", () => {
    expect(Object.keys(UNSUPPORTED_TEXT)).toHaveLength(11);
    expect(Object.values(UNSUPPORTED_TEXT).every((t) => t.length > 10)).toBe(true);
    expect(Object.keys(TEMPLATE_LABEL).sort()).toEqual(["date_window", "field_missing", "field_vs_constant", "fs_ratio"]);
  });

  it("tallies the custom runs of a case apart from the built-in ones", () => {
    expect(customRunTally(withRuns())).toEqual({ total: 2, ran: 1, skipped: 1, flags: 1 });
    expect(customRunTally(buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!)).total).toBe(0);
    expect(customCoverageText({ ran: 1, skipped: 1 })).toBe("Custom rules: 1 ran, 1 skipped. Not counted in coverage.");
  });

  it("states the dry run in one sentence", () => {
    expect(dryRunSentence({ evaluated: 41, fired: 6, skipped_missing: 3 } as never)).toBe("Evaluated 41 stored cases, fired on 6, skipped 3 because a field was missing.");
    expect(dryRunSentence({ evaluated: 1, fired: 0, skipped_missing: 0 } as never)).toBe("Evaluated 1 stored case, fired on 0.");
    expect(percent(0.1463)).toBe("15%");
  });

  it("words a used-up budget for authoring, not for casework", () => {
    const e = explainCustomError({ code: "user_budget_used", message: "x" });
    expect(e.title).toMatch(/model budget/);
    expect(e.detail).toMatch(/tech team/);
    expect(e.detail).not.toMatch(/existing case/);
    expect(explainCustomError({ code: "name_required", message: "x" }).title).toBe("Name required");
  });

  it("knows when authoring is down so the tech-team route is offered", () => {
    expect(authoringDown("user_budget_used")).toBe(true);
    expect(authoringDown("site_budget_used")).toBe(true);
    expect(authoringDown("authoring_unavailable")).toBe(true);
    expect(authoringDown("bad_answer")).toBe(false);
  });
});
