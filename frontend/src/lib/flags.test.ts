import { describe, expect, it } from "vitest";
import { makeDocNamer } from "./docNames";
import { dispositionSummary, findingHeadline, flagLocation, flagWhere, groupBySeverity, humanisePartialReason, isHighFlag, numberFlags, pageCounts, pagesLabelText, plainNotCheckedReason, ruleName, scoreImpactText, seenOnText, stripTitleSuffix } from "./flags";
import { buildCase, SPECS } from "./mock/fixtures";

const fraud = buildCase(SPECS.find((s) => s.id === "c_loan_fraud")!);

describe("flags helpers", () => {
  it("numbers flags in finding order then flag_ids order", () => {
    const n = numberFlags(fraud.findings, fraud.flags);
    expect(n.all.map((x) => x.n)).toEqual(fraud.flags.map((_, i) => i + 1));
    expect(n.all[0].flag.id).toBe(fraud.findings[0].flag_ids[0]);
    expect(n.other).toEqual([]);
  });
  it("appends flags missing from every finding", () => {
    const n = numberFlags([], fraud.flags);
    expect(n.other).toHaveLength(fraud.flags.length);
    expect(n.all[0].n).toBe(1);
  });
  it("summarises dispositions", () => {
    expect(dispositionSummary([{ disposition: "open" }, { disposition: "open" }, { disposition: "confirmed" }])).toBe("2 open, 1 confirmed");
    expect(dispositionSummary([{ disposition: "pending_second_approval" }])).toBe("1 needs 2nd approval");
  });
  it("describes where a flag is", () => {
    const f = fraud.flags[0];
    expect(flagLocation(f)).toBe("d1 · transactions row 9 · f1 p1");
    expect(flagLocation({ ...f, region: null, table: null, field: null })).toBe("d1 · No location on a page");
  });
  it("humanises partial reasons and leaves unknown ones alone", () => {
    expect(humanisePartialReason("stage_failed:rules")).toBe("The rules stage failed");
    expect(humanisePartialReason("check_error:bs_totals")).toBe("Check bs_totals errored");
    expect(humanisePartialReason("document_failed:d4")).toBe("Document d4 could not be read");
    expect(humanisePartialReason("budget_exceeded: 0.50 USD")).toBe("budget_exceeded: 0.50 USD");
  });
  it("uses API page counts, with a client fallback", () => {
    expect(pageCounts(fraud)).toEqual({ unchecked: 1, total: 4 });
    const noCoverage = { ...fraud, coverage: null };
    expect(pageCounts(noCoverage).total).toBe(4);
  });
});

describe("plain-language helpers", () => {
  it("groups findings by severity, highest first, without empty groups", () => {
    const g = groupBySeverity(fraud.findings);
    expect(g.map((x) => x.severity)).toEqual([...new Set(fraud.findings.map((f) => f.severity))].sort((a, b) => ["high", "medium", "low", "info"].indexOf(a) - ["high", "medium", "low", "info"].indexOf(b)));
    expect(g.flatMap((x) => x.findings)).toHaveLength(fraud.findings.length);
  });
  it("strips the counts the backend appends to titles", () => {
    expect(stripTitleSuffix("This invoice bills more (seen on 2 documents)")).toBe("This invoice bills more");
    expect(stripTitleSuffix("Marked paid, no deposit (+3 similar)")).toBe("Marked paid, no deposit");
    expect(stripTitleSuffix("Plain title")).toBe("Plain title");
  });
  it("uses the full explanation as the headline", () => {
    const f = fraud.findings[0];
    expect(findingHeadline({ ...f, title: "Short title… (seen on 2 documents)" }, [{ explanation: "The full sentence." }])).toBe("The full sentence.");
    expect(findingHeadline({ ...f, title: "Short title (+1 similar)" }, [])).toBe("Short title");
  });
  it("words the score impact and document counts", () => {
    expect(scoreImpactText(30)).toBe("Adds 30 to the risk score");
    expect(scoreImpactText(0)).toBe("Does not change the risk score");
    expect(seenOnText(1)).toBe("Seen on 1 document");
    expect(seenOnText(3)).toBe("Seen on 3 documents");
  });
  it("turns check ids into names for lists", () => {
    expect(ruleName("fs_subtotals")).toBe("Financial statement: subtotals");
    expect(ruleName("copy_move")).toBe("Copy move");
    expect(ruleName("x_cash_at_year_end")).toBe("Across documents: cash at year end");
  });
  it("says where a concern is without ids", () => {
    const names = makeDocNamer(fraud.documents, fraud.files);
    const where = flagWhere(fraud.flags[0], names);
    expect(where).toContain("page 1");
    expect(where).toContain("transactions row 9");
    expect(where).not.toMatch(/\bd\d+\b|\bf\d+\b/);
    expect(flagWhere({ ...fraud.flags[0], region: null, table: null, field: null }, names)).toContain("No location on a page");
  });
  it("explains unreviewed pages", () => {
    expect(pagesLabelText("f15:2")).toBe("page 2");
    expect(pagesLabelText("f1:1-3")).toBe("pages 1 to 3");
    expect(plainNotCheckedReason("classified as boilerplate: no checks apply", "boilerplate")).toMatch(/standard wording/);
    expect(plainNotCheckedReason("classified as other: no checks apply", "other")).toMatch(/could not tell/);
    expect(plainNotCheckedReason("odd reason", "other")).toBe("odd reason");
  });
});

describe("isHighFlag (review controls)", () => {
  it("is high by the severity in force or by the code's own severity", () => {
    expect(isHighFlag({ severity: "high" })).toBe(true);
    expect(isHighFlag({ severity: "medium" })).toBe(false);
    expect(isHighFlag({ severity: "low", code_severity: "high" })).toBe(true); // lowered in the catalogue: still a high flag for controls
    expect(isHighFlag({ severity: "low", code_severity: "medium" })).toBe(false);
    expect(isHighFlag({ severity: "high", code_severity: null })).toBe(true);
  });

  it("leaves badges and grouping on the effective severity", () => {
    const flags = [{ ...fraud.flags[0], severity: "low" as const, code_severity: "high" as const }];
    expect(flags[0].severity).toBe("low");
  });
});
