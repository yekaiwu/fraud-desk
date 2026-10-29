import { describe, expect, it } from "vitest";
import {
  CONFIRM_LABEL,
  DISMISS_LABEL,
  HARD_STOP_LABEL,
  LANES,
  UNCHECKED_PAGES_LABEL,
  bandLabel,
  bandWord,
  caseStatusDescription,
  caseStatusLabel,
  coverageText,
  laneDescription,
  laneLabel,
  plural,
  ruleIdText,
  scoreSummary,
  severityLabel,
  unreviewedPagesText,
  useCaseLabel,
} from "./labels";
import type { CaseStatus, Severity, UseCase } from "./types";

describe("labels", () => {
  it("names every lane in plain English and describes it in one sentence", () => {
    expect(LANES.map(laneLabel)).toEqual(["Loan review", "Enhanced due diligence", "AML investigation", "Fast lane"]);
    for (const lane of LANES) {
      const d = laneDescription(lane);
      expect(d.endsWith(".")).toBe(true);
      expect(d.split(". ").length).toBe(1);
    }
    expect(laneDescription("fast")).toMatch(/person still confirms/);
  });

  it("labels severities as concerns", () => {
    const all: Severity[] = ["high", "medium", "low", "info"];
    expect(all.map(severityLabel)).toEqual(["High concern", "Medium concern", "Low concern", "For information"]);
  });

  it("labels bands and spells the score out, flagging a partial score as a floor", () => {
    expect(bandWord("high")).toBe("High");
    expect(bandLabel("medium")).toBe("Medium risk");
    expect(bandLabel("low", true)).toBe("Low risk (partial score)");
    expect(scoreSummary(90, "high")).toBe("Risk score 90 of 100, high");
    expect(scoreSummary(19.6, "low", true)).toBe("Risk score at least 20 of 100, low");
  });

  it("labels every case status and gives a description", () => {
    const all: CaseStatus[] = ["processing", "open", "awaiting_documents", "closed", "error"];
    expect(all.map(caseStatusLabel)).toEqual(["Being analysed", "Ready for review", "Waiting for documents", "Closed", "Could not be analysed"]);
    for (const s of all) expect(caseStatusDescription(s).length).toBeGreaterThan(10);
  });

  it("labels use cases", () => {
    const all: UseCase[] = ["kyc", "aml", "loan"];
    expect(all.map(useCaseLabel)).toEqual(["Onboarding (KYC)", "AML review", "Loan application"]);
  });

  it("replaces developer jargon with the agreed phrases", () => {
    expect(HARD_STOP_LABEL).toBe("Blocks the fast lane");
    expect(UNCHECKED_PAGES_LABEL).toBe("Pages we could not review");
    expect(DISMISS_LABEL).toBe("Mark as not a concern");
    expect(CONFIRM_LABEL).toBe("Mark as a real concern");
    expect(coverageText(41, 54)).toBe("41 of 54 checks completed");
    expect(coverageText(0, 1)).toBe("0 of 1 check completed");
    expect(unreviewedPagesText(1)).toBe("1 page we could not review");
    expect(unreviewedPagesText(3)).toBe("3 pages we could not review");
    expect(ruleIdText("bs_running_balance")).toBe("Check: bs_running_balance");
  });

  it("pluralises", () => {
    expect(plural(1, "case")).toBe("case");
    expect(plural(2, "case")).toBe("cases");
    expect(plural(0, "entry", "entries")).toBe("entries");
  });
});
