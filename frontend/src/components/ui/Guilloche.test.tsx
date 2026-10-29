import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Guilloche } from "./Guilloche";

describe("Guilloche", () => {
  it("is decorative, takes its colour from currentColor and honours opacity", () => {
    const { container } = render(<Guilloche opacity={0.1} className="text-brass-light" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveAttribute("stroke", "currentColor");
    expect(svg).toHaveStyle({ opacity: "0.1" });
    expect(svg.querySelectorAll("path").length).toBeGreaterThan(10);
  });

  it("renders the rosette variant", () => {
    const { container } = render(<Guilloche variant="rosette" />);
    expect(container.querySelectorAll("ellipse").length).toBeGreaterThan(10);
  });
});
