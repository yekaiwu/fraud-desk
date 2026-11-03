import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { numberFlags } from "@/lib/flags";
import { buildCase, SPECS } from "@/lib/mock/fixtures";
import type { Flag } from "@/lib/types";
import { placeChips, RegionOverlay } from "./RegionOverlay";

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
    // the box is the region grown by a fixed margin on every side, in pixels, so it surrounds the figure
    const style = screen.getByRole("button").getAttribute("style") ?? "";
    expect(style).toContain("left: calc(62.000% - var(--pad-x))");
    expect(style).toContain("top: calc(41.000% - var(--pad-y))");
    expect(style).toContain("width: calc(12.000% + 2 * var(--pad-x))");
    expect(style).toContain("height: calc(1.500% + 2 * var(--pad-y))");
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

  it("puts the number in the page margin, level with its box and never on the figure, and skips invalid regions", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const nf = numberFlags([], [flag({ region: { file_id: "f1", page: 2, x: 0.1, y: 0.01, w: 0.1, h: 0.02 } }), flag({ id: "bad", region: { file_id: "f1", page: 2, x: NaN, y: 0, w: 1, h: 1 } })]);
    const { container } = render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} />);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button").querySelector(".region-margin-chip, .region-chip")).toBeNull(); // not inside the box
    const chip = container.querySelector(".region-margin-chip");
    expect(chip).toHaveStyle({ left: "0.6%" });
    expect(chip).toHaveTextContent("1");
    warn.mockRestore();
  });

  it("uses the right margin for a box on the right half of the page", () => {
    const nf = numberFlags([], [flag({ region: { file_id: "f1", page: 2, x: 0.7, y: 0.3, w: 0.2, h: 0.02 } })]);
    const { container } = render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} />);
    expect(container.querySelector(".region-margin-chip")).toHaveStyle({ right: "0.6%" });
  });

  it("keeps chips of neighbouring rows apart when the page height is known", () => {
    const tops = placeChips(
      [
        { id: "a", side: "left", centre: 0.5 },
        { id: "b", side: "left", centre: 0.502 },
        { id: "c", side: "left", centre: 0.504 },
        { id: "r", side: "right", centre: 0.502 },
      ],
      1000,
    );
    const px = (id: string) => parseFloat(tops.get(id) ?? "NaN");
    expect(px("b") - px("a")).toBeGreaterThanOrEqual(20);
    expect(px("c") - px("b")).toBeGreaterThanOrEqual(20);
    expect(px("r")).toBeCloseTo(502, 0); // a different margin is not pushed
    expect(placeChips([{ id: "a", side: "left", centre: 0.25 }], null).get("a")).toBe("25.000%");
  });

  it("draws a flag about a whole page as a pill, and as a labelled frame once it is selected", () => {
    const whole = flag({ id: "u", rule: "doc_unclassified", severity: "info", region: { file_id: "f1", page: 2, x: 0, y: 0, w: 1, h: 1, label: "unclassified" } });
    const nf = numberFlags([], [whole]);
    const { container, rerender } = render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} />);
    expect(container.querySelector(".region-box")).toBeNull(); // no page-sized box hiding at the edge of the page
    expect(container.querySelector(".region-frame")).toBeNull();
    expect(screen.getByRole("button", { name: /Flag 1, doc_unclassified, info severity, whole page/ })).toBeInTheDocument();
    rerender(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId="u" onSelect={() => {}} />);
    const frame = screen.getByTestId("region-frame");
    expect(frame).toHaveAttribute("data-kind", "page");
    expect(frame).toHaveTextContent("applies to the whole page");
  });

  it("frames every page of a document for a flag with no region, dashed, and names it a whole-file flag", () => {
    const docs = [{ id: "d1", file_id: "f1", page_start: 1, page_end: 3 }];
    const meta = flag({ id: "m", rule: "pdf_editor_software", region: null, table: null, field: null, row: null, document_ids: ["d1"] });
    const nf = numberFlags([], [meta]);
    const { rerender } = render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 3 }} selectedFlagId="m" onSelect={() => {}} documents={docs} />);
    expect(screen.getByTestId("region-frame")).toHaveAttribute("data-kind", "document");
    expect(screen.getByTestId("region-frame")).toHaveTextContent("applies to the whole file");
    rerender(<RegionOverlay flags={nf.all} current={{ fileId: "f9", page: 1 }} selectedFlagId="m" onSelect={() => {}} documents={docs} />);
    expect(screen.queryByTestId("region-frame")).toBeNull(); // another file: nothing to frame
  });

  it("draws no number chips when they are switched off, but keeps the boxes", () => {
    const nf = numberFlags([], [flag({ region: { file_id: "f1", page: 2, x: 0.3, y: 0.3, w: 0.1, h: 0.02 } })]);
    const { container } = render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} showChips={false} />);
    expect(container.querySelectorAll(".region-margin-chip")).toHaveLength(0);
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });

  it("draws related evidence only for the selected flag: dashed, not clickable, a letter in the margin and no text on the page", () => {
    const nf = numberFlags([], [flag({ extra_regions: [{ file_id: "f1", page: 2, x: 0.2, y: 0.2, w: 0.1, h: 0.02, label: "opening balance" }] })]);
    const { container, rerender } = render(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={null} onSelect={() => {}} />);
    expect(container.querySelectorAll(".region-extra")).toHaveLength(0); // nothing selected: no clutter
    rerender(<RegionOverlay flags={nf.all} current={{ fileId: "f1", page: 2 }} selectedFlagId={nf.all[0].flag.id} onSelect={() => {}} />);
    expect(container.querySelectorAll(".region-extra")).toHaveLength(1);
    expect(screen.getAllByRole("button")).toHaveLength(1); // the extra box is not a control
    expect(container.querySelector("[data-extra-letter='a']")).toHaveTextContent("a");
    // the name is listed in the caption and is a tooltip; drawing it under the box hid the figure on the next line
    expect(container.textContent).not.toContain("opening balance");
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
