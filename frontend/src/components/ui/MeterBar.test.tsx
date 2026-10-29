import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MeterBar } from "./MeterBar";

describe("MeterBar", () => {
  it("exposes label, value and text as a progressbar", () => {
    render(<MeterBar label="Checks completed" value={41} max={54} valueText="41 of 54 checks completed" />);
    const bar = screen.getByRole("progressbar", { name: "Checks completed" });
    expect(bar).toHaveAttribute("aria-valuenow", "41");
    expect(bar).toHaveAttribute("aria-valuemax", "54");
    expect(bar).toHaveAttribute("aria-valuetext", "41 of 54 checks completed");
    expect(screen.getByText("41 of 54 checks completed")).toBeVisible();
  });

  it("defaults to a percentage, clamps, and draws a threshold marker", () => {
    const { container } = render(<MeterBar label="Progress" value={150} marker={0.7} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(container.querySelector('span[aria-hidden="true"]')).toHaveStyle({ left: "70%" });
  });
});
