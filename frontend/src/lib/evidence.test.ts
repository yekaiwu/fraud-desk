import { describe, expect, it } from "vitest";
import { groupThousands, humanLabel, parseEvidence, splitAmounts } from "./evidence";

describe("evidence formatting", () => {
  it("adds thousands separators without changing a digit", () => {
    expect(groupThousands("1234567.89")).toBe("1,234,567.89");
    expect(groupThousands("-1500000.00")).toBe("-1,500,000.00");
    expect(groupThousands("999.99")).toBe("999.99");
    expect(groupThousands("0.37")).toBe("0.37");
    expect(groupThousands("0123456.00")).toBe("0123456.00");
    expect(groupThousands("INV-2025")).toBe("INV-2025");
  });

  it("marks only money-shaped numbers and keeps the text round-trippable", () => {
    const s = "invoice INV-2025-0104 total 146496.00 due 2025-09-03; tolerance -1.00/+35.00; MAD 0.025; ratio 59.8% of 1000000.00";
    const parts = splitAmounts(s);
    expect(parts.filter((p) => p.amount).map((p) => p.text)).toEqual(["146,496.00", "-1.00", "+35.00", "1,000,000.00"]);
    expect(parts.map((p) => (p.amount ? p.text.replace(/,/g, "") : p.text)).join("")).toBe(s);
  });

  it("does not touch dates, ids or longer decimals", () => {
    const parts = splitAmounts("C-2025-001 on 2025-08-07, chi2 23.0, p 0.025, 12.345");
    expect(parts.some((p) => p.amount)).toBe(false);
  });

  it("splits evidence into label/value rows and notes, in order", () => {
    const rows = parseEvidence("gross_profit: printed 281920.00; revenue 2400000.00 - cost_of_sales 618080.00 = 1781920.00; diff 1500000.00; MAD 0.025");
    expect(rows).toEqual([
      { kind: "fact", label: "Gross profit", value: "printed 281920.00" },
      { kind: "note", text: "revenue 2400000.00 - cost_of_sales 618080.00 = 1781920.00" },
      { kind: "fact", label: "Diff", value: "1500000.00" },
      { kind: "fact", label: "MAD", value: "0.025" },
    ]);
  });

  it("keeps text that does not fit a pattern whole, including markup, as plain notes", () => {
    expect(parseEvidence("<script>alert(1)</script>")).toEqual([{ kind: "note", text: "<script>alert(1)</script>" }]);
    expect(parseEvidence("")).toEqual([]);
    expect(humanLabel("cost_of_sales")).toBe("Cost of sales");
  });
});
