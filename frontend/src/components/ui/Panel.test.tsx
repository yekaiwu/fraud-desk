import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Panel } from "./Panel";

describe("Panel", () => {
  it("with a title is a labelled region with a heading, actions and footer", () => {
    render(
      <Panel title="Findings" eyebrow="3 to check" actions={<button>Expand</button>} footer="Footer text">
        body
      </Panel>,
    );
    const region = screen.getByRole("region", { name: "Findings" });
    expect(region).toHaveTextContent("body");
    expect(screen.getByRole("heading", { level: 2, name: "Findings" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Expand" })).toBeInTheDocument();
    expect(screen.getByText("Footer text")).toBeInTheDocument();
    expect(screen.getByText("3 to check")).toBeInTheDocument();
  });

  it("honours headingLevel, tone and a header-less layout", () => {
    const { container } = render(
      <Panel headingLevel={3} title="Sub" tone="attention">
        x
      </Panel>,
    );
    expect(screen.getByRole("heading", { level: 3 })).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("border-l-brass");
    render(<Panel>no header</Panel>);
    expect(screen.getByText("no header")).toBeInTheDocument();
    expect(screen.getAllByRole("heading")).toHaveLength(1);
  });
});
