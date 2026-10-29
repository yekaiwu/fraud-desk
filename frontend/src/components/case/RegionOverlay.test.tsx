import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { numberFlags } from "@/lib/flags";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import type { Flag } from "@/lib/types";
import { RegionOverlay } from "./RegionOverlay";

// data-model.md section 7: page 2, x .62, y .41, w .12, h .015 on an A4 page
const flag = (over: Partial<Flag> = {}): Flag => ({
  id: "bs_running_balance.d3.transactions.17", rule: "bs_running_balance", stage: "rules", severity: "high", document_ids: ["d3"],
  table: "transactions", field: "balance", row: 17, region: { file_id: "f1", page: 2, x: 0.62, y: 0.41, w: 0.12, h: 0.015 },
  extra_regions: [], evidence: "e", explanation: "x", disposition: "open", ...over,
});

function inWrapper(widthPx: number) {
  const numbered = numberFlags([], [flag()]);
  return render(
    <div data-testid={`w${widthPx}`} style={{ position: "relative", width: widthPx, aspectRatio: "595.28 / 841.89" }}>
      <RegionOverlay flags={numbered.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} />
    </div>,
  );
}

describe("RegionOverlay", () => {
  it("uses identical CSS percentages at 700 px and 328 px wide", () => {
    const a = inWrapper(700);
    const wide = screen.getByRole("button").getAttribute("style");
    a.unmount();
    inWrapper(328);
    const narrow = screen.getByRole("button").getAttribute("style");
    expect(narrow).toBe(wide);
    expect(screen.getByRole("button")).toHaveStyle({ left: "62.000%", top: "41.000%", width: "12.000%", height: "1.500%" });
    // computed pixel positions are proportional: 700 * .62 = 434 and 328 * .62 = 203.36
    expect(700 * 0.62).toBeCloseTo(434, 1);
    expect(328 * 0.62).toBeCloseTo(203.36, 2);
  });

  it("is a labelled button in flag-number order, only for the page shown", () => {
    const nf = numberFlags([], [flag(), flag({ id: "b", rule: "inv_dates", severity: "low", region: { file_id: "f1", page: 3, x: 0.1, y: 0.1, w: 0.1, h: 0.1 } })]);
    const { rerender } = render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} />);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Flag 1, bs_running_balance, high severity, page 2" })).toBeInTheDocument();
    rerender(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 3 }} selectedFlagId="b" onSelect={() => {}} />);
    expect(screen.getByRole("button", { name: "Flag 2, inv_dates, low severity, page 3" })).toHaveAttribute("aria-pressed", "true");
  });

  it("puts the number chip beside the box, never on the figure, and skips invalid regions", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const nf = numberFlags([], [flag({ region: { file_id: "f1", page: 2, x: 0.1, y: 0.01, w: 0.1, h: 0.02 } }), flag({ id: "bad", region: { file_id: "f1", page: 2, x: NaN, y: 0, w: 1, h: 1 } })]);
    render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} />);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    const chip = screen.getByRole("button").querySelector(".region-chip");
    expect(chip).toHaveStyle({ right: "100%" });
    expect(chip).not.toHaveStyle({ bottom: "100%" });
    warn.mockRestore();
  });

  it("flips the chip to the right of a box at the left page edge", () => {
    const nf = numberFlags([], [flag({ region: { file_id: "f1", page: 2, x: 0.01, y: 0.3, w: 0.1, h: 0.02 } })]);
    render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} />);
    expect(screen.getByRole("button").querySelector(".region-chip")).toHaveStyle({ left: "100%" });
  });

  it("draws no number chips when they are switched off, but keeps the boxes", () => {
    const nf = numberFlags([], [flag({ region: { file_id: "f1", page: 2, x: 0.3, y: 0.3, w: 0.1, h: 0.02 } })]);
    const { container } = render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} showChips={false} />);
    expect(container.querySelectorAll(".region-chip")).toHaveLength(0);
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("draws extra regions on the same page dashed and not clickable", () => {
    const nf = numberFlags([], [flag({ extra_regions: [{ file_id: "f1", page: 2, x: 0.2, y: 0.2, w: 0.1, h: 0.02, label: "Related" }] })]);
    const { container } = render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} />);
    expect(container.querySelectorAll(".region-extra")).toHaveLength(1);
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("every fixture region renders for its own page", () => {
    for (const spec of SPECS) {
      const c = buildCase(spec);
      const nf = numberFlags(c.findings, c.flags);
      for (const { flag: f } of nf.all) {
        if (!f.region) continue;
        const { unmount } = render(<RegionOverlay flags={nf.all} current={{ fileId: f.region.file_id, page: f.region.page }} selectedFlagId={null} onSelect={() => {}} />);
        expect(screen.getAllByRole("button").length).toBeGreaterThan(0);
        unmount();
      }
    }
  });
});
