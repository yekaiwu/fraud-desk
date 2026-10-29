import { describe, expect, it } from "vitest";
import {
  checkName,
  defaultSeverity,
  documentTypeLabel,
  effectiveDescription,
  effectiveName,
  effectiveSeverity,
  isCustomised,
  isEnabled,
  isSwitchedOffReason,
  stageHint,
  stageLabel,
} from "./checkCatalogue";
import type { RuleInfo, RuleOverride } from "./types";

describe("check catalogue wording", () => {
  it("names document types and humanises unknown ones", () => {
    expect(documentTypeLabel("bank_statement")).toBe("Bank statement");
    expect(documentTypeLabel("source_of_funds_declaration")).toBe("Source-of-funds declaration");
    expect(documentTypeLabel("any file")).toBe("Any file");
    expect(documentTypeLabel("lease_agreement")).toBe("Lease agreement");
  });

  it("derives readable check names from rule ids", () => {
    expect(checkName("bs_running_balance")).toBe("Statement: running balance");
    expect(checkName("x_invoice_vs_receipts")).toBe("Invoice vs receipts");
    expect(checkName("x_sof_support")).toBe("Source of funds support");
    expect(checkName("pdf_editor_software")).toBe("PDF: editor software");
    expect(checkName("copy_move")).toBe("Copy move");
    expect(checkName("")).toBe("");
  });

  it("labels the three stages", () => {
    expect(stageLabel("crossdoc")).toBe("Across documents");
    expect(stageHint("forensics")).toMatch(/file itself/);
  });
});

const rule = (over: Partial<RuleInfo> = {}): RuleInfo => ({
  rule: "bs_running_balance", stage: "rules", severity: "high", applies_to: ["bank_statement"], requires: [], use_cases: ["loan"],
  description: "Each balance equals the previous one.", tamper: null, version: "1", ...over,
});
const ov = (over: Partial<RuleOverride> = {}): RuleOverride => ({
  enabled: true, severity: null, name: null, description: null, note: null, updated_by: "a.tan", updated_at: "2026-10-27T10:00:00Z", reason: "because", revision: 1, ...over,
});

describe("effective wording and settings", () => {
  it("prefers the officer's name, else the derived one", () => {
    expect(effectiveName("bs_running_balance", ov({ name: "Balance walk" }))).toBe("Balance walk");
    expect(effectiveName("bs_running_balance", ov({ name: "   " }))).toBe("Statement: running balance");
    expect(effectiveName("bs_running_balance", null)).toBe("Statement: running balance");
    expect(effectiveName("bs_running_balance", undefined, () => "Own fallback")).toBe("Own fallback");
    expect(effectiveName("bs_running_balance", ov({ name: " Trimmed " }), () => "Own fallback")).toBe("Trimmed");
  });

  it("prefers the officer's description", () => {
    expect(effectiveDescription(rule({ override: ov({ description: "Our words" }) }))).toBe("Our words");
    expect(effectiveDescription(rule({ override: ov() }))).toBe("Each balance equals the previous one.");
  });

  it("reads severity and switch with older backends in mind", () => {
    expect(effectiveSeverity(rule())).toBe("high");
    expect(defaultSeverity(rule())).toBe("high");
    const custom = rule({ default_severity: "high", effective_severity: "low", severity: "high", override: ov({ severity: "low" }) });
    expect(effectiveSeverity(custom)).toBe("low");
    expect(defaultSeverity(custom)).toBe("high");
    expect(isEnabled(rule())).toBe(true);
    expect(isEnabled(rule({ enabled: false }))).toBe(false);
    expect(isEnabled(rule({ override: ov({ enabled: false }) }))).toBe(false);
  });

  it("calls a check customised only when something differs from the defaults", () => {
    expect(isCustomised(rule())).toBe(false);
    expect(isCustomised(rule({ override: null, enabled: true }))).toBe(false);
    expect(isCustomised(rule({ override: ov() }))).toBe(false);
    expect(isCustomised(rule({ override: ov({ note: "n" }) }))).toBe(true);
    expect(isCustomised(rule({ override: ov({ enabled: false }) }))).toBe(true);
    expect(isCustomised(rule({ override: ov({ severity: "low" }) }))).toBe(true);
  });

  it("recognises the switched-off skip reason", () => {
    expect(isSwitchedOffReason("switched_off")).toBe(true);
    expect(isSwitchedOffReason(" Switched off in catalogue")).toBe(true);
    expect(isSwitchedOffReason("no raster source")).toBe(false);
  });
});
