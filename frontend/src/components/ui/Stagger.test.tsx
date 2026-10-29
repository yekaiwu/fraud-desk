import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Stagger, riseStyle } from "./Stagger";

describe("Stagger", () => {
  it("adds the stagger class and the step variable", () => {
    render(
      <Stagger step={40} className="space-y-4" data-testid="s">
        <p>one</p>
        <p>two</p>
      </Stagger>,
    );
    const el = screen.getByTestId("s");
    expect(el).toHaveClass("stagger", "space-y-4");
    expect(el.style.getPropertyValue("--stagger-step")).toBe("40ms");
  });

  it("renders as another element", () => {
    render(
      <Stagger as="ul" data-testid="s">
        <li>x</li>
      </Stagger>,
    );
    expect(screen.getByTestId("s").tagName).toBe("UL");
  });

  it("riseStyle caps the index at 9", () => {
    expect(riseStyle(3)).toEqual({ "--i": 3 });
    expect(riseStyle(25)).toEqual({ "--i": 9 });
    expect(riseStyle(-2)).toEqual({ "--i": 0 });
  });
});
