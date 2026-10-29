import { describe, expect, it } from "vitest";
import type { RuleOverride } from "@/lib/types";
import { describeChange } from "./changeText";

const ov = (over: Partial<RuleOverride> = {}): RuleOverride => ({
  enabled: true, severity: null, name: null, description: null, note: null, updated_by: "a.tan", updated_at: "2026-10-27T10:00:00Z", reason: "because", revision: 1, ...over,
});

describe("describeChange", () => {
  it("describes each kind of change in plain words", () => {
    expect(describeChange({ before: null, after: ov({ enabled: false }) })).toEqual(["Switched off"]);
    expect(describeChange({ before: ov({ enabled: false }), after: null })).toEqual(["Switched on"]);
    expect(describeChange({ before: null, after: ov({ severity: "low" }) }, "high")).toEqual(["Severity changed from High concern (default) to Low concern"]);
    expect(describeChange({ before: ov({ severity: "low" }), after: ov({ severity: "info" }) })).toEqual(["Severity changed from Low concern to For information"]);
    expect(describeChange({ before: null, after: ov({ name: "Mine" }) })).toEqual(["Name set to “Mine”"]);
    expect(describeChange({ before: ov({ name: "Mine" }), after: ov({ name: "Ours" }) })).toEqual(["Name changed from “Mine” to “Ours”"]);
    expect(describeChange({ before: ov({ note: "n" }), after: ov() })).toEqual(["Internal note back to the default wording"]);
  });

  it("reads a reset as the values being put back", () => {
    const lines = describeChange({ before: ov({ enabled: false, severity: "low", description: "Ours" }), after: null }, "high");
    expect(lines).toEqual(["Switched on", "Severity changed from Low concern to High concern (default)", "Description back to the default wording"]);
  });

  it("shortens long wording and keeps typed markup as text", () => {
    const [line] = describeChange({ before: null, after: ov({ name: `<img src=x onerror=alert(1)> ${"a".repeat(300)}` }) });
    expect(line.startsWith("Name set to “<img src=x onerror=alert(1)>")).toBe(true);
    expect(line.length).toBeLessThan(200);
    expect(line).toContain("…");
  });

  it("returns nothing when nothing visible changed", () => {
    expect(describeChange({ before: ov(), after: ov({ revision: 2 }) })).toEqual([]);
  });
});
