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

  it("clamps out-of-range scores, draws no arc for zero, and supports the small size", () => {
    const { container, rerender } = render(<RiskGauge score={140} band="high" size="sm" />);
    expect(screen.getByRole("img", { name: "Risk score 100 of 100, high" })).toBeInTheDocument();
    expect(container.querySelectorAll("path")).toHaveLength(2);
    rerender(<RiskGauge score={0} band="low" size="sm" />);
    expect(screen.getByRole("img", { name: "Risk score 0 of 100, low" })).toBeInTheDocument();
    expect(container.querySelectorAll("path")).toHaveLength(1);
  });
});
