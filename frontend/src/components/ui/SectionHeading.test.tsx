import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SectionHeading } from "./SectionHeading";

describe("SectionHeading", () => {
  it("renders the requested level with eyebrow, description and actions", () => {
    render(
      <SectionHeading level={1} size="page" eyebrow="Worklist" description="Highest risk first." actions={<button>Refresh</button>}>
        Cases to review
      </SectionHeading>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Cases to review" })).toBeInTheDocument();
    expect(screen.getByText("Worklist")).toBeInTheDocument();
    expect(screen.getByText("Highest risk first.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  });

  it("draws the statement rule by default and not for the minor size", () => {
    const { container, rerender } = render(<SectionHeading>Title</SectionHeading>);
    expect(screen.getByRole("heading", { level: 2 })).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("rule-statement");
    rerender(<SectionHeading size="minor" level={3}>Title</SectionHeading>);
    expect(container.firstElementChild).not.toHaveClass("rule-statement");
  });
});
