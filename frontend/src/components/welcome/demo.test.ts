import { describe, expect, it } from "vitest";
import { HIGH_FROM as GAUGE_HIGH_FROM, MEDIUM_FROM as GAUGE_MEDIUM_FROM } from "@/components/ui/RiskGauge";
import {
  EDITED_ROW_ID,
  HIGH_FROM,
  MEDIUM_FROM,
  RUNNING_BALANCE_FINDING,
  SAMPLE_FINDINGS,
  SCORE_CAP,
  WEIGHTS,
  bandOf,
  checkRunningBalance,
  findingsFor,
  formatCents,
  rawScore,
  sampleStatement,
  scoreOf,
} from "./demo";

describe("formatCents", () => {
  it("formats whole cents with thousands separators and two decimals", () => {
    expect(formatCents(0)).toBe("0.00");
    expect(formatCents(5)).toBe("0.05");
    expect(formatCents(61_275)).toBe("612.75");
    expect(formatCents(1_157_450)).toBe("11,574.50");
    expect(formatCents(123_456_789)).toBe("1,234,567.89");
    expect(formatCents(-1_000_000)).toBe("-10,000.00");
  });
});

describe("running-balance check on the sample statement", () => {
  it("fails the edited statement at exactly one row, with the expected value worked out from the previous printed balance", () => {
    const checks = checkRunningBalance(sampleStatement(true));
    const failed = checks.filter((c) => !c.ok);
    expect(failed.map((c) => c.rowId)).toEqual([EDITED_ROW_ID]);
    const row = failed[0];
    expect(formatCents(row.previous)).toBe("11,574.50");
    expect(formatCents(row.change)).toBe("14,000.00");
    expect(formatCents(row.expected)).toBe("25,574.50");
    expect(formatCents(row.printed)).toBe("15,574.50");
    expect(formatCents(row.difference)).toBe("-10,000.00");
    expect(row.previous + row.change).toBe(row.expected);
  });

  it("passes the clean statement on every row", () => {
    const checks = checkRunningBalance(sampleStatement(false));
    expect(checks).toHaveLength(7);
    expect(checks.every((c) => c.ok)).toBe(true);
    expect(findingsFor(checks)).toEqual([]);
  });

  it("is real arithmetic: changing any other amount also fails, at that row only", () => {
    const s = sampleStatement(false);
    s.rows[1] = { ...s.rows[1], debit: s.rows[1].debit + 100 };
    expect(checkRunningBalance(s).filter((c) => !c.ok).map((c) => c.rowId)).toEqual(["r2"]);
  });

  it("starts from the opening balance and chains each row from the previous printed balance", () => {
    const s = sampleStatement(true);
    const checks = checkRunningBalance(s);
    expect(checks[0].previous).toBe(s.opening);
    for (let i = 1; i < checks.length; i++) expect(checks[i].previous).toBe(s.rows[i - 1].printed);
  });

  it("changes only one figure between the two versions", () => {
    const a = sampleStatement(false).rows;
    const b = sampleStatement(true).rows;
    expect(b.filter((r, i) => r.credit !== a[i].credit || r.debit !== a[i].debit || r.printed !== a[i].printed).map((r) => r.id)).toEqual([EDITED_ROW_ID]);
  });

  it("raises one high finding that blocks the fast lane and scores 30, medium", () => {
    const findings = findingsFor(checkRunningBalance(sampleStatement(true)));
    expect(findings).toHaveLength(1);
    expect(findings[0]).toMatchObject({ severity: "high", title: RUNNING_BALANCE_FINDING, failedRowIds: [EDITED_ROW_ID], blocksFastLane: true });
    expect(scoreOf(findings.map((f) => f.severity))).toBe(30);
    expect(bandOf(30)).toBe("medium");
  });

  it("groups several failing rows into one finding", () => {
    const s = sampleStatement(true);
    s.rows[5] = { ...s.rows[5], credit: s.rows[5].credit + 1 };
    const findings = findingsFor(checkRunningBalance(s));
    expect(findings).toHaveLength(1);
    expect(findings[0].failedRowIds).toEqual([EDITED_ROW_ID, "r6"]);
  });
});

describe("score calculator", () => {
  it("uses the documented weights", () => {
    expect(WEIGHTS).toEqual({ high: 30, medium: 15, low: 5, info: 0 });
    expect(SCORE_CAP).toBe(100);
  });

  it("matches the gauge's band marks", () => {
    expect([MEDIUM_FROM, HIGH_FROM]).toEqual([GAUGE_MEDIUM_FROM, GAUGE_HIGH_FROM]);
  });

  it("sums one weight per finding", () => {
    expect(scoreOf([])).toBe(0);
    expect(scoreOf(["info", "info"])).toBe(0);
    expect(scoreOf(["low", "low", "medium"])).toBe(25);
    expect(scoreOf(["high", "medium", "low", "low"])).toBe(55);
  });

  it("caps at 100 and keeps the raw sum for explaining the cap", () => {
    const all = SAMPLE_FINDINGS.map((f) => f.severity);
    expect(rawScore(all)).toBe(115);
    expect(scoreOf(all)).toBe(100);
    expect(scoreOf(["high", "high", "high", "high"])).toBe(100);
  });

  it("picks the band at 29, 30, 59 and 60", () => {
    expect(bandOf(0)).toBe("low");
    expect(bandOf(29)).toBe("low");
    expect(bandOf(30)).toBe("medium");
    expect(bandOf(59)).toBe("medium");
    expect(bandOf(60)).toBe("high");
    expect(bandOf(100)).toBe("high");
  });

  it("reaches each boundary with real findings", () => {
    expect(bandOf(scoreOf(["medium", "low", "low", "low"]))).toBe("medium"); // 30
    expect(bandOf(scoreOf(["medium", "medium", "low", "low", "low"]))).toBe("medium"); // 45
    expect(bandOf(scoreOf(["high", "medium", "low", "low"]))).toBe("medium"); // 55
    expect(bandOf(scoreOf(["high", "high"]))).toBe("high"); // 60
    expect(bandOf(scoreOf(["high", "low", "low", "low", "low", "low"]))).toBe("medium"); // 55
  });

  it("has sample findings with unique ids covering every severity", () => {
    expect(new Set(SAMPLE_FINDINGS.map((f) => f.id)).size).toBe(SAMPLE_FINDINGS.length);
    expect(new Set(SAMPLE_FINDINGS.map((f) => f.severity))).toEqual(new Set(["high", "medium", "low", "info"]));
  });
});
