import { describe, expect, it } from "vitest";
import {
  alternatives,
  amlRoutedFindings,
  checksAreReproducible,
  costSummaryText,
  costText,
  decider,
  describeExtractor,
  effectiveCoverage,
  evaluateRequirements,
  readSummary,
  scoreBreakdown,
  secondReadings,
  secondsText,
  totalText,
  unreadableNotes,
} from "./explain";
import { buildCase, EXPLAIN_METHOD, finding, flag, SPECS, statementDoc, FILE_STATEMENT } from "./mock/fixtures";
import type { CaseDetail } from "./types";

const byId = (id: string): CaseDetail => buildCase(SPECS.find((s) => s.id === id)!);

/** A case with four high findings: 120 points before the limit. */
function cappedCase(): CaseDetail {
  const flags = [1, 2, 3, 4].map((n) => flag({ id: `f${n}`, rule: `bs_rule_${n}`, severity: "high", documents: ["d1"], evidence: "e", explanation: "x" }));
  return buildCase({
    id: "c_cap", reference: null, use_case: "loan", status: "open", ageSeconds: 10, applicantName: "Cap Co", files: [FILE_STATEMENT()], documents: [statementDoc()],
    flags, findings: flags.map((f, i) => finding(`fnd_${i + 1}`, `Finding ${i + 1}`, "high", [f], "s")), lane: "loan",
  });
}

describe("scoreBreakdown", () => {
  it("adds up this case's findings by severity using their own weights", () => {
    const c = byId("c_loan_fraud"); // 1 high (30) + 3 medium (15 each) = 75
    const b = scoreBreakdown(c, EXPLAIN_METHOD)!;
    expect(b.rows).toEqual([
      { severity: "high", findings: 1, pointsEach: 30, subtotal: 30 },
      { severity: "medium", findings: 3, pointsEach: 15, subtotal: 45 },
    ]);
    expect(b.rawTotal).toBe(75);
    expect(b.value).toBe(75);
    expect(b.capped).toBe(false);
    expect(b.band).toBe("high");
    expect(b.bands).toEqual({ mediumFrom: 30, highFrom: 60 });
    expect(b.consistent).toBe(true);
    expect(totalText(b)).toBe("Total 75");
  });

  it("says when the total is capped", () => {
    const b = scoreBreakdown(cappedCase(), EXPLAIN_METHOD)!;
    expect(b.rawTotal).toBe(120);
    expect(b.value).toBe(100);
    expect(b.capped).toBe(true);
    expect(b.cap).toBe(100);
    expect(totalText(b)).toBe("Total 120, capped at 100");
  });

  it("works out a limit from the case alone when the method is missing, and leaves bands out", () => {
    const b = scoreBreakdown(cappedCase(), null)!;
    expect(b.capped).toBe(true);
    expect(b.cap).toBe(100);
    expect(b.bands).toBeNull();
    expect(b.consistent).toBe(true);
  });

  it("marks a partial score as a floor", () => {
    const b = scoreBreakdown(byId("c_loan_partial"), EXPLAIN_METHOD)!;
    expect(b.partial).toBe(true);
    expect(b.value).toBe(20);
    expect(b.rows.map((r) => [r.severity, r.subtotal])).toEqual([["medium", 15], ["low", 5]]);
  });

  it("a clean case has no rows and a score of 0; a case without a score has no breakdown", () => {
    const b = scoreBreakdown(byId("c_loan_clean"), EXPLAIN_METHOD)!;
    expect(b.rows).toEqual([]);
    expect(b.value).toBe(0);
    expect(b.rawTotal).toBe(0);
    expect(scoreBreakdown(byId("c_kyc_processing"), EXPLAIN_METHOD)).toBeNull();
  });

  it("flags a recorded score the findings cannot reproduce", () => {
    const c = byId("c_loan_fraud");
    c.score!.value = 90;
    expect(scoreBreakdown(c, EXPLAIN_METHOD)!.consistent).toBe(false);
  });
});

describe("fast lane requirements", () => {
  it("ticks what holds for a clean case in the fast lane", () => {
    const reqs = evaluateRequirements(byId("c_loan_clean"), EXPLAIN_METHOD);
    expect(reqs).toHaveLength(EXPLAIN_METHOD.fast_lane.requires.length);
    expect(reqs.map((r) => r.met)).toEqual([true, true, true, true]);
  });

  it("crosses the partial, score, blocking and coverage requirements that fail", () => {
    const c = byId("c_loan_hardstop"); // one blocking finding, score 15, 21 of 21 checks
    expect(evaluateRequirements(c, EXPLAIN_METHOD).map((r) => r.met)).toEqual([true, true, false, true]);
    const p = byId("c_loan_partial"); // partial, score 20, 11 of 21 checks, 3 of 5 pages unreviewed
    const got = evaluateRequirements(p, EXPLAIN_METHOD).map((r) => r.met);
    expect(got[0]).toBe(false);
    expect(got[3]).toBe(false);
  });

  it("lists a requirement it cannot match without a verdict", () => {
    const method = { ...EXPLAIN_METHOD, fast_lane: { requires: ["Something the tool does not recognise"] } };
    expect(evaluateRequirements(byId("c_loan_clean"), method)[0].met).toBeNull();
  });

  it("reduces coverage by the pages nobody reviewed", () => {
    expect(effectiveCoverage({ applicable: 10, ran: 10, ratio: 1, skipped: [], pages_total: 10, pages_unchecked: 2 })).toBeCloseTo(0.8);
    expect(effectiveCoverage(null)).toBeNull();
  });
});

describe("aml routing", () => {
  it("finds the findings that come from a routing check, only with the method", () => {
    const c = byId("c_aml_graph");
    expect(amlRoutedFindings(c, EXPLAIN_METHOD).length).toBeGreaterThan(0);
    expect(amlRoutedFindings(c, null)).toEqual([]);
  });
});

describe("how it was analysed", () => {
  it("summarises the files from the files themselves", () => {
    const r = readSummary(byId("c_loan_fraud"));
    expect(r).toMatchObject({ files: 2, pages: 4, textPages: 4, scanPages: 0, unreadable: 0, seconds: 0.8 });
  });

  it("names who decided and lists the alternatives", () => {
    const cl = { label: "invoice", probability: 0.7, classifier: "rules", version: "1", distribution: { invoice: 0.7, contract: 0.04, purchase_order: 0.2, other: 0.02, receipt: 0.01 } };
    expect(decider(cl)).toEqual({ kind: "rules", text: "Rules classifier, version 1" });
    expect(decider({ ...cl, classifier: "llm", version: "claude-haiku-4-5", rationale: "x" }).kind).toBe("model");
    expect(alternatives(cl).map((a) => a.label)).toEqual(["purchase_order", "contract", "other"]);
    expect(alternatives({ label: "x", distribution: null })).toEqual([]);
  });

  it("describes each way figures can be read, without inventing any", () => {
    expect(describeExtractor("text_layer@1").kind).toBe("text_layer");
    expect(describeExtractor("llm:claude-haiku-4-5")).toMatchObject({ kind: "model" });
    expect(describeExtractor("cascade(text_layer>llm_cheap>llm_strong)").text).toBe("Read in steps: the text layer, then a smaller model, then a stronger model.");
    expect(describeExtractor(null).kind).toBe("none");
    expect(describeExtractor("mystery").kind).toBe("other");
  });

  it("reads second readings and unreadable pages from the document warnings", () => {
    const doc = { warnings: ["escalated: page=2 reason=balance_mismatch result=cheap_model_kept", "unreadable: page 3 could not be read", "no_extractor: x"] };
    expect(secondReadings(doc)).toEqual([{ page: 2, reason: "balance_mismatch", result: "cheap_model_kept" }]);
    expect(unreadableNotes(doc)).toEqual(["unreadable: page 3 could not be read"]);
  });

  it("formats time and cost without turning an amount into a float", () => {
    expect(secondsText(0.44)).toBe("0.4 s");
    expect(secondsText(12.4)).toBe("12 s");
    expect(secondsText(150)).toBe("3 min");
    expect(costText("0.004200")).toBe("0.0042");
    expect(costText("0.000000")).toBe("0.00");
    expect(costSummaryText(byId("c_loan_clean"))).toBe("$0.00, no paid model used");
    expect(costSummaryText(byId("c_loan_partial"))).toBe("$0.0042 across 3 model calls");
  });

  it("only claims the checks repeat when the record supports it", () => {
    expect(checksAreReproducible(byId("c_loan_clean"))).toBe(true);
    const noRecord = byId("c_loan_clean");
    noRecord.provenance = null;
    expect(checksAreReproducible(noRecord)).toBe(false);
    const failed = byId("c_loan_clean");
    failed.stages = failed.stages.map((s) => (s.stage === "rules" ? { ...s, status: "failed" as const } : s));
    expect(checksAreReproducible(failed)).toBe(false);
    const paid = byId("c_loan_clean");
    paid.cost_summary.usd_by_stage = { crossdoc: "0.001000" };
    expect(checksAreReproducible(paid)).toBe(false);
  });
});
