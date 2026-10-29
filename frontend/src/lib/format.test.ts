import { describe, expect, it } from "vitest";
import { formatAge, formatBytes, formatRatio, formatRelative, formatSeconds, formatUtc } from "./format";

describe("format", () => {
  it("ages", () => {
    expect([45, 720, 10800, 172800].map(formatAge)).toEqual(["45 s", "12 min", "3 h", "2 d"]);
    expect(formatAge(-3)).toBe("0 s");
  });
  it("utc and relative", () => {
    expect(formatUtc("2026-10-27T10:02:11Z")).toBe("2026-10-27 10:02 UTC");
    expect(formatUtc("garbage")).toBe("garbage");
    expect(formatRelative("2026-10-27T08:02:00Z", Date.parse("2026-10-27T10:02:00Z"))).toBe("2 h ago");
  });
  it("ratio, seconds, bytes", () => {
    expect(formatRatio(0.857)).toBe("86%");
    expect(formatSeconds(540)).toBe("9 min");
    expect(formatSeconds(null)).toBe("-");
    expect(formatBytes(26 * 1024 * 1024)).toBe("26.0 MB");
  });
});
