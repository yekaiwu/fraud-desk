import { describe, expect, it } from "vitest";
import type { RuleInfo, RuleOverride } from "@/lib/types";
import { buildPatch, describeEffects, draftFrom, hasChanges, soleCoverage, validateWording, type Draft } from "./editModel";

const ov = (over: Partial<RuleOverride> = {}): RuleOverride => ({
  enabled: true, severity: null, name: null, description: null, note: null, updated_by: "a.tan", updated_at: "2026-10-27T10:00:00Z", reason: "because", revision: 1, ...over,
});
const rule = (id: string, over: Partial<RuleInfo> = {}): RuleInfo => ({
  rule: id, stage: "rules", severity: "high", applies_to: ["bank_statement"], requires: [], use_cases: ["loan"], description: `Default description of ${id}.`, tamper: null, version: "1",
  default_severity: "high", effective_severity: "high", enabled: true, override: null, ...over,
});
const weights = { high: 30, medium: 15, low: 5, info: 0 };

describe("draft and patch", () => {
  const r = rule("bs_running_balance");

  it("starts from what is saved and sends nothing when nothing changed", () => {
    expect(hasChanges(buildPatch(r, draftFrom(r)))).toBe(false);
    const custom = rule("bs_running_balance", { enabled: false, effective_severity: "low", override: ov({ enabled: false, severity: "low", name: "Mine", note: "n" }) });
    const d = draftFrom(custom);
    expect(d).toEqual({ enabled: false, severity: "low", name: "Mine", description: "", note: "n" });
    expect(buildPatch(custom, d)).toEqual({});
  });

  it("sends only the fields that changed", () => {
    const d: Draft = { ...draftFrom(r), enabled: false, name: "  Walk the balance  " };
    expect(buildPatch(r, d)).toEqual({ enabled: false, name: "Walk the balance" });
  });

  it("sends null when the severity goes back to the default, nothing when it already is", () => {
    const low = rule("x", { effective_severity: "low", override: ov({ severity: "low" }) });
    expect(buildPatch(low, { ...draftFrom(low), severity: "high" })).toEqual({ severity: null });
    expect(buildPatch(r, { ...draftFrom(r), severity: "high" })).toEqual({});
    expect(buildPatch(r, { ...draftFrom(r), severity: "info" })).toEqual({ severity: "info" });
  });

  it("clears wording that is emptied and treats the default text as 'use the default'", () => {
    const worded = rule("bs_running_balance", { override: ov({ name: "Mine", description: "Ours", note: "n" }) });
    expect(buildPatch(worded, { ...draftFrom(worded), name: "", description: " ", note: "" })).toEqual({ name: null, description: null, note: null });
    expect(buildPatch(r, { ...draftFrom(r), description: "Default description of bs_running_balance." })).toEqual({});
    expect(buildPatch(r, { ...draftFrom(r), name: "Statement: running balance" })).toEqual({});
  });
});

describe("wording validation", () => {
  const base: Draft = { enabled: true, severity: "high", name: "", description: "", note: "" };
  it("accepts the limits and rejects one over", () => {
    expect(validateWording({ ...base, name: "x".repeat(80), description: "y".repeat(400), note: "z".repeat(600) })).toEqual({});
    const e = validateWording({ ...base, name: "x".repeat(81), description: "y".repeat(401), note: "z".repeat(601) });
    expect(e.name).toMatch(/at most 80 characters \(now 81\)/);
    expect(e.description).toMatch(/at most 400/);
    expect(e.note).toMatch(/at most 600/);
  });
  it("counts trimmed text", () => {
    expect(validateWording({ ...base, name: `  ${"x".repeat(80)}  ` })).toEqual({});
  });
});

describe("what will happen", () => {
  const r = rule("bs_running_balance");
  const catalogue = [r, rule("pdf_editor_software", { stage: "forensics", applies_to: ["any file"] }), rule("fs_arith", { applies_to: ["financial_statement"] })];
  const ids = (r0: RuleInfo, patch: Parameters<typeof describeEffects>[1], ctx = {}) => describeEffects(r0, patch, { catalogue, weights, ...ctx }).map((e) => e.id);

  it("says nothing before anything changes", () => {
    expect(ids(r, {})).toEqual([]);
  });

  it("switching off: coverage drops, never a clearance, applies after saving", () => {
    const fx = describeEffects(r, { enabled: false }, { catalogue, weights });
    const off = fx.find((e) => e.id === "off")!;
    expect(off.tone).toBe("warning");
    expect(off.text).toMatch(/not completed/);
    expect(off.text).toMatch(/coverage drops/);
    expect(off.text).toMatch(/never a clearance/);
    expect(fx.at(-1)!.id).toBe("timing");
    expect(fx.at(-1)!.text).toMatch(/after you save/);
    expect(fx.at(-1)!.text).toMatch(/keep their results until they are re-run/);
  });

  it("switching back on says it will run again", () => {
    expect(describeEffects(r, { enabled: true }, { catalogue, weights })[0].text).toMatch(/run again/);
  });

  it("lowering severity: fewer points, does not open the fast lane, with the real points", () => {
    const fx = describeEffects(r, { severity: "medium" }, { catalogue, weights });
    const lower = fx.find((e) => e.id === "lower")!;
    expect(lower.text).toMatch(/fewer points/);
    expect(lower.text).toMatch(/adds 15 instead of 30 points/);
    expect(lower.text).toMatch(/does not open the fast lane/);
    expect(lower.text).toMatch(/never relaxes a review control/);
    expect(lower.text).toMatch(/second approver for high flags/);
    expect(fx.find((e) => e.id === "higher")).toBeUndefined();
  });

  it("raising severity: more points", () => {
    const low = rule("x_one", { severity: "high", effective_severity: "low", override: ov({ severity: "low" }) });
    const fx = describeEffects(low, { severity: null }, { catalogue: [low], weights });
    const higher = fx.find((e) => e.id === "higher")!;
    expect(higher.text).toMatch(/more points/);
    expect(higher.text).toMatch(/adds 30 instead of 5 points/);
  });

  it("works without the method (no point numbers)", () => {
    const lower = describeEffects(r, { severity: "low" }, { catalogue }).find((e) => e.id === "lower")!;
    expect(lower.text).not.toMatch(/instead of/);
  });

  it("wording is display only", () => {
    const w = describeEffects(r, { name: "New" }, { catalogue, weights });
    expect(w.map((e) => e.id)).toEqual(["wording", "timing"]);
    expect(w[0].text).toMatch(/only changes how the check is described on screen/);
  });

  it("stronger checks get an extra warning when switched off or lowered", () => {
    const hard = { hardStops: ["bs_running_balance"] };
    expect(ids(r, { enabled: false }, hard)).toContain("stronger-off");
    expect(describeEffects(r, { enabled: false }, { catalogue, weights, ...hard }).find((e) => e.id === "stronger-off")!.text).toMatch(/fast lane is not offered at all for new analyses/);
    expect(ids(r, { severity: "low" }, hard)).toContain("stronger-lower");
    expect(ids(r, { name: "x" }, hard)).not.toContain("stronger-off");
    expect(ids(r, { enabled: false })).not.toContain("stronger-off");
  });

  it("warns when it is the only switched-on check for a document type or in its group", () => {
    const narrow = [r, rule("fs_arith", { stage: "crossdoc", applies_to: ["financial_statement"] })];
    const fx = describeEffects(r, { enabled: false }, { catalogue: narrow, weights });
    const sole = fx.find((e) => e.id === "sole-doc")!;
    expect(sole.text).toMatch(/only switched-on check for bank statement/);
    expect(fx.find((e) => e.id === "sole-stage")).toBeDefined();
    const alone = describeEffects(r, { enabled: false }, { catalogue: [r, rule("fs_arith", { applies_to: ["bank_statement"] })], weights });
    expect(alone.find((e) => e.id === "sole-doc")).toBeUndefined();
  });

  it("a check that applies to any file covers every type; already-off checks do not count", () => {
    expect(soleCoverage(r, [r, rule("pdf", { applies_to: ["any file"] })]).docTypes).toEqual([]);
    expect(soleCoverage(r, [r, rule("fs_arith", { applies_to: ["bank_statement"], enabled: false })]).docTypes).toEqual(["bank_statement"]);
  });
});
