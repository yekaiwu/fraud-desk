import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RiskGauge } from "./RiskGauge";

describe("RiskGauge", () => {
  it("is an image with a spoken score and band", () => {
    render(<RiskGauge score={90} band="high" />);
    const g = screen.getByRole("img", { name: "Risk score 90 of 100, high" });
    expect(g).toHaveTextContent("90");
    expect(g).toHaveTextContent("High risk");
    expect(g).toHaveAttribute("data-band", "high");
  });

  it("says 'at least' for a partial score", () => {
    render(<RiskGauge score={20} band="low" partial size="lg" />);
    expect(screen.getByRole("img", { name: "Risk score at least 20 of 100, low" })).toHaveTextContent("≥");
    expect(screen.getByText("Partial score")).toBeInTheDocument();
  });

  it("marks a plain 0 to 100 scale (25, 50, 75) and does not draw the band cutoffs", () => {
    const { container } = render(<RiskGauge score={42} band="medium" size="lg" />);
    const labels = [...container.querySelectorAll("svg text")].map((t) => t.textContent);
    expect(labels.slice(0, 3)).toEqual(["25", "50", "75"]);
    expect([...labels].sort()).toEqual(["0", "100", "25", "50", "75"].sort());
    expect(labels).not.toContain("30");
    expect(labels).not.toContain("60");
    expect(container.querySelectorAll("svg line")).toHaveLength(3);
  });

  it("gives the large gauge room above the arc for the label at 50, and the small one the same marks without numbers", () => {
    const large = render(<RiskGauge score={42} band="medium" size="lg" />);
    const [, top, , height] = large.container.querySelector("svg")!.getAttribute("viewBox")!.split(" ").map(Number);
    expect(top).toBeLessThan(-12);
    expect(top + height).toBe(112);
    large.unmount();
    const small = render(<RiskGauge score={42} band="medium" size="sm" />);
    expect(small.container.querySelector("svg")).toHaveAttribute("viewBox", "0 0 200 112");
    expect(small.container.querySelectorAll("svg line")).toHaveLength(3);
    expect(small.container.querySelectorAll("svg text")).toHaveLength(0);
  });

  it("clamps out-of-range scores, draws no arc for zero, and supports the small size", () => {
    const { container, rerender } = render(<RiskGauge score={140} band="high" size="sm" />);
    expect(screen.getByRole("img", { name: "Risk score 100 of 100, high" })).toBeInTheDocument();
    expect(container.querySelectorAll("path")).toHaveLength(2);
    rerender(<RiskGauge score={0} band="low" size="sm" />);
    expect(screen.getByRole("img", { name: "Risk score 0 of 100, low" })).toBeInTheDocument();
    expect(container.querySelectorAll("path")).toHaveLength(1);
  });
});
