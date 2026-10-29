import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Notice } from "./Notice";

describe("Notice", () => {
  it("errors are announced as alerts, the rest as status", () => {
    render(
      <>
        <Notice variant="error" title="Could not save">Try again.</Notice>
        <Notice variant="success" title="Saved" />
        <Notice variant="warning" title="Careful" />
        <Notice title="FYI" />
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not save");
    expect(screen.getAllByRole("status")).toHaveLength(3);
  });

  it("renders an action and lets the role be overridden", () => {
    render(
      <Notice variant="info" role="note" action={<button>Undo</button>}>
        Body
      </Notice>,
    );
    expect(screen.getByRole("note")).toHaveTextContent("Body");
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
  });

  it("can be closed", async () => {
    const onDismiss = vi.fn();
    render(<Notice onDismiss={onDismiss}>x</Notice>);
    await userEvent.click(screen.getByRole("button", { name: "Close message" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
