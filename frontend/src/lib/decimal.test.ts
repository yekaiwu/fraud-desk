import { describe, expect, it } from "vitest";
import { fmtCents, formatDecimalString, toCents } from "./decimal";

describe("decimal strings", () => {
  it("groups the integer part and keeps every decimal", () => {
    expect(formatDecimalString("1234567.8")).toBe("1,234,567.8");
    expect(formatDecimalString("-0.50")).toBe("-0.50");
    expect(formatDecimalString("0.00")).toBe("0.00");
    expect(formatDecimalString("12345678901234567890.123456")).toBe("12,345,678,901,234,567,890.123456");
  });
  it("returns non-decimals unchanged", () => {
    expect(formatDecimalString("n/a")).toBe("n/a");
  });
  it("round-trips cents without loss", () => {
    for (const s of ["12345.67", "0.05", "-9.10", "100", "9007199254740993.99"]) {
      const c = toCents(s);
      expect(toCents(fmtCents(c).replace(/,/g, ""))).toBe(c);
    }
    expect(toCents("12345.67")).toBe(1234567n);
    expect(fmtCents(1234567n)).toBe("12,345.67");
    expect(fmtCents(-5n)).toBe("-0.05");
  });
  it("rejects more than two decimals and junk", () => {
    expect(() => toCents("12.345")).toThrow();
    expect(() => toCents("abc")).toThrow();
    expect(() => toCents("")).toThrow();
  });
});
