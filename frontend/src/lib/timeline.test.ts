import { describe, expect, it } from "vitest";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import { buildTimeline, humaniseKey, offsetText, tallyOf, tallyText, timelineExport } from "./timeline";
import type { AgentReport, CaseDetail } from "./types";

const fraud = (): CaseDetail => buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);

const team: AgentReport[] = [
  { agent: "finance", role: "Arithmetic", status: "ok", checks_run: 22, flag_ids: ["f1"], summary: "Ran 22 checks.", verdicts: [], next_steps: [], handoff: "", cost_usd: "0", seconds: 0 },
  {
    agent: "challenger", role: "Argues the other side", status: "ok", checks_run: 0, flag_ids: ["f1", "f2"], summary: "Ruled on 2 flag(s); lowered 1, kept 1.", next_steps: [], handoff: "", cost_usd: "0.0017", seconds: 3.7,
    verdicts: [
      { flag_id: "f1", verdict: "upheld", action: "kept", reason: "No passage explains it." },
      { flag_id: "f2", verdict: "doubtful", action: "lowered", reason: "The register shows the same name.", document_id: "d1", quote: "same name" },
    ],
  },
  { agent: "case_officer", role: "Briefs the reviewer", status: "ok", checks_run: 0, flag_ids: [], summary: "Two things stand out.", verdicts: [], next_steps: ["Ask for the original statement."], handoff: "", cost_usd: "0.0008", seconds: 5.7 },
];

describe("buildTimeline", () => {
  it("starts with the case being received and follows the stages in order", () => {
    const steps = buildTimeline(fraud());
    expect(steps[0]).toMatchObject({ key: "received", offsetSeconds: 0 });
    const keys = steps.map((s) => s.key);
    const order = ["ingest", "classify", "extract", "forensics", "rules", "crossdoc", "score"].filter((k) => keys.includes(k));
    expect(keys.filter((k) => order.includes(k))).toEqual(order);
    expect(order.length).toBeGreaterThan(3);
  });

  it("gives each step the flags its stage raised, the check runs of that stage and a running tally", () => {
    const c = fraud();
    const steps = buildTimeline(c);
    for (const s of steps.filter((x) => ["forensics", "rules", "crossdoc"].includes(x.key))) {
      expect(s.flags.every((f) => f.stage === s.key)).toBe(true);
      expect(s.checks.every((r) => r.stage === s.key)).toBe(true);
    }
    const last = steps[steps.length - 1].cumulative;
    const total = tallyOf(c.flags.filter((f) => ["forensics", "rules", "crossdoc"].includes(f.stage)));
    expect(last.high + last.medium + last.low + last.info).toBe(total.high + total.medium + total.low + total.info);
    // the tally never goes down from one step to the next
    const sums = steps.map((s) => s.cumulative.high + s.cumulative.medium + s.cumulative.low + s.cumulative.info);
    expect([...sums].sort((a, b) => a - b)).toEqual(sums);
  });

  it("puts the review team after the cross-document step and before the score, with all its rulings", () => {
    const c = { ...fraud(), agents: team };
    const steps = buildTimeline(c);
    const keys = steps.map((s) => s.key);
    expect(keys.indexOf("team")).toBe(keys.indexOf("crossdoc") + 1);
    expect(keys.indexOf("team")).toBeLessThan(keys.indexOf("score"));
    const t = steps[keys.indexOf("team")];
    expect(t.agents).toHaveLength(3);
    expect(t.headline).toContain("ruled on 2 flags (lowered 1)");
    expect(t.costUsd).toBeCloseTo(0.0025, 4);
  });

  it("has no team step when the team did not run, and no review step without decisions", () => {
    const steps = buildTimeline({ ...fraud(), agents: [], review: [] });
    expect(steps.some((s) => s.key === "team" || s.key === "review")).toBe(false);
  });

  it("adds the reviewers' decisions as the last step, oldest first", () => {
    const c = fraud();
    c.review = [
      { id: "r1", kind: "flag_decision", reviewer: "officer", decision: "dismiss", reason: "old draft", flag_ids: [], status: "pending_second_approval", case_version: 1, created_at: "2026-11-03T10:00:00Z" },
      { id: "r2", kind: "second_approval", reviewer: "boss", decision: "approve", reason: "agreed", flag_ids: [], target_entry_id: "r1", status: "effective", case_version: 1, created_at: "2026-11-03T10:05:00Z" },
    ];
    const steps = buildTimeline(c);
    const last = steps[steps.length - 1];
    expect(last.key).toBe("review");
    expect(last.entries.map((e) => e.reviewer)).toEqual(["officer", "boss"]);
  });

  it("copes with a stage that recorded no detail", () => {
    const c = fraud();
    c.stages = c.stages.map((s) => ({ ...s, detail: {} }));
    expect(() => buildTimeline(c)).not.toThrow();
    expect(buildTimeline(c).every((s) => s.headline.length > 0)).toBe(true);
  });
});

describe("helpers", () => {
  it("humanises detail keys and the tally", () => {
    expect(humaniseKey("text_layer_docs")).toBe("text layer docs");
    expect(tallyText({ high: 2, medium: 0, low: 1, info: 0 })).toBe("2 high, 1 low");
    expect(tallyText({ high: 0, medium: 0, low: 0, info: 0 })).toBe("none");
  });
  it("writes a step's start as an offset from receipt", () => {
    expect(offsetText({ offsetSeconds: 4.24 })).toBe("+4.2 s");
    expect(offsetText({ offsetSeconds: 125 })).toBe("+2 min");
    expect(offsetText({ offsetSeconds: null })).toBe("");
  });
  it("exports every step as plain data with the facts, checks, flags, calls, team and decisions", () => {
    const c = { ...fraud(), agents: team };
    const out = timelineExport(c, buildTimeline(c)) as { case_id: string; steps: { step: string; review_team: unknown[]; flags_so_far: object }[] };
    expect(out.case_id).toBe(c.id);
    expect(out.steps.map((s) => s.step)).toContain("team");
    expect(out.steps.find((s) => s.step === "team")!.review_team).toHaveLength(3);
    expect(JSON.stringify(out)).not.toContain("undefined");
  });
});

describe("model calls", () => {
  it("lists the team's calls on the team step only, not again under the cross-document step", () => {
    const c = { ...fraud(), agents: team };
    const call = (purpose: string, stage: "rules" | "crossdoc") => ({ stage, purpose, model: "m", prompt_tokens: 1, completion_tokens: 1, usd: "0.001", seconds: 1, cache_hit: false });
    c.costs = [call("semantic:txn_labels", "rules"), call("semantic:coherence_fit", "crossdoc"), call("agent:challenger", "crossdoc"), call("agent:case_officer", "crossdoc")];
    const steps = buildTimeline(c);
    expect(steps.find((s) => s.key === "crossdoc")!.calls.map((x) => x.purpose)).toEqual(["semantic:coherence_fit"]);
    expect(steps.find((s) => s.key === "team")!.calls.map((x) => x.purpose)).toEqual(["agent:challenger", "agent:case_officer"]);
    expect(steps.find((s) => s.key === "rules")!.calls).toHaveLength(1);
  });
});
