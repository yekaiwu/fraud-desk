import { describe, expect, it } from "vitest";
import type { Region } from "../../types";
import { bandFor, buildCase, SPECS, STAGES } from "./index";

/** Local mirror of the section 6 validity rule (clampRegion belongs to frontend-app). */
function regionValid(r: Region): boolean {
  const { x, y, w, h } = r;
  if (![x, y, w, h].every(Number.isFinite)) return false;
  if (w <= 0 || h <= 0 || x < -0.0001 || y < -0.0001 || x >= 1 || y >= 1) return false;
  return x + w <= 1.0001 && y + h <= 1.0001;
}

describe("mock fixtures", () => {
  const cases = SPECS.map(buildCase);

  it("has 12 cases covering the main shapes", () => {
    expect(cases.map((c) => c.status).sort()).toEqual(["awaiting_documents", "closed", "error", "open", "open", "open", "open", "open", "open", "open", "open", "processing"]);
    expect(new Set(cases.map((c) => c.use_case))).toEqual(new Set(["kyc", "aml", "loan"]));
  });

  it.each(cases.filter((c) => c.score).map((c) => [c.id, c] as const))("%s: score follows the formula", (_id, c) => {
    const sum = c.findings.reduce((n, f) => n + f.weight, 0);
    expect(c.score!.value).toBe(Math.min(100, sum));
    expect(c.score!.band).toBe(bandFor(c.score!.value));
    expect(c.coverage!.ratio).toBeCloseTo(c.coverage!.ran / c.coverage!.applicable, 3);
    expect(c.partial).toBe(c.partial_reasons.length > 0);
  });

  it("every finding's weight matches its severity and every flag id is known", () => {
    const w = { high: 30, medium: 15, low: 5, info: 0 };
    for (const c of cases) {
      const ids = new Set(c.flags.map((f) => f.id));
      for (const f of c.findings) {
        expect(f.weight).toBe(w[f.severity]);
        for (const id of f.flag_ids) expect(ids.has(id)).toBe(true);
      }
    }
  });

  it("every fixture region is valid and points at a real file and page", () => {
    for (const c of cases) {
      for (const f of c.flags) {
        for (const r of [f.region, ...f.extra_regions]) {
          if (!r) continue;
          expect(regionValid(r), `${c.id} ${f.id}`).toBe(true);
          const file = c.files.find((x) => x.id === r.file_id);
          expect(file, `${c.id} ${f.id} file ${r.file_id}`).toBeDefined();
          expect(r.page).toBeLessThanOrEqual(file!.pages.length);
        }
      }
    }
  });

  it("the clean control has no flags and says so", () => {
    const clean = cases.find((c) => c.id === "c_loan_clean")!;
    expect(clean.flags).toEqual([]);
    expect(clean.score!.value).toBe(0);
    expect(clean.lane).toBe("fast");
    expect(clean.top_finding).toBe("No flags raised; 21 of 21 checks ran");
  });

  it("the partial case is never fast lane and carries reasons", () => {
    const p = cases.find((c) => c.id === "c_loan_partial")!;
    expect(p.partial).toBe(true);
    expect(p.lane).not.toBe("fast");
    expect(p.coverage!.pages_unchecked).toBe(3);
    expect(p.score!.value).toBe(20);
  });

  it("the processing case has no score and the job has 7 stages", () => {
    const p = cases.find((c) => c.status === "processing")!;
    expect(p.score).toBeNull();
    expect(STAGES).toHaveLength(7);
  });
});
