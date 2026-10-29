import { describe, expect, it, vi } from "vitest";
import { clampRegion, pickImageWidth, regionStyle } from "./regions";
import type { Region } from "./types";

const R = (x: number, y: number, w: number, h: number): Region => ({ file_id: "f1", page: 1, x, y, w, h });

describe("regionStyle (frontend.md section 6 table)", () => {
  it("maps fractions to percentages", () => {
    expect(regionStyle(R(0.62, 0.41, 0.12, 0.015))).toEqual({ left: "62.000%", top: "41.000%", width: "12.000%", height: "1.500%" });
    expect(regionStyle(R(0, 0, 1, 1))).toEqual({ left: "0.000%", top: "0.000%", width: "100.000%", height: "100.000%" });
  });
  it("clamps width and height to the page", () => {
    expect(regionStyle(R(0.95, 0.5, 0.1, 0.1))?.width).toBe("5.000%");
    expect(regionStyle(R(0.5, 0.95, 0.1, 0.1))?.height).toBe("5.000%");
  });
  it("rejects boxes that cannot be drawn", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(regionStyle(R(0.2, 0.2, 0, 0.1))).toBeNull();
    expect(regionStyle(R(1, 0.2, 0.1, 0.1))).toBeNull();
    expect(regionStyle(R(NaN, 0.2, 0.1, 0.1))).toBeNull();
    expect(regionStyle(R(-0.5, 0.2, 0.1, 0.1))).toBeNull();
    expect(clampRegion(R(0.2, 0.2, 0.1, -1))).toBeNull();
    warn.mockRestore();
  });
  it("warns once per invalid region", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const bad = R(5, 5, 1, 1);
    clampRegion(bad);
    clampRegion(bad);
    expect(warn).toHaveBeenCalledTimes(1);
    warn.mockRestore();
  });
  it("tolerates -0.0001 and clamps a tiny negative to 0", () => {
    expect(clampRegion(R(-0.00005, 0.1, 0.1, 0.1))?.x).toBe(0);
  });
});

describe("pickImageWidth", () => {
  it("rounds up to a multiple of 200 within [600, 2000]", () => {
    expect(pickImageWidth(328, 3)).toBe(1000);
    expect(pickImageWidth(700, 1)).toBe(800);
    expect(pickImageWidth(2000, 2)).toBe(2000);
    expect(pickImageWidth(100, 1)).toBe(600);
  });
});
