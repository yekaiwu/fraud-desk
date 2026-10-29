import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Stat } from "./Stat";

describe("Stat", () => {
  it("shows label, value and hint", () => {
    render(<Stat label="Cases waiting" value={12} hint="3 are high risk" tone="high" />);
    expect(screen.getByText("Cases waiting")).toBeInTheDocument();
    expect(screen.getByText("12")).toHaveClass("text-sev-high");
    expect(screen.getByText("3 are high risk")).toBeInTheDocument();
  });
});
