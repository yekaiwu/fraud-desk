import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("shows title, text, icon and action, with the decoration hidden from assistive tech", () => {
    const { container } = render(<EmptyState icon={<svg data-testid="i" />} title="Nothing here" text="Try again later." action={<button>Reset</button>} />);
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
    expect(screen.getByText("Try again later.")).toBeInTheDocument();
    expect(screen.getByTestId("i")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset" })).toBeInTheDocument();
    expect(container.querySelector("svg[aria-hidden='true']")).not.toBeNull();
  });

  it("works with only a title", () => {
    render(<EmptyState title="Empty" compact />);
    expect(screen.getByText("Empty")).toBeInTheDocument();
  });
});
