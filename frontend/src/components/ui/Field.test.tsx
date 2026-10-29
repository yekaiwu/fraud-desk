import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Field } from "./Field";

describe("Field", () => {
  it("links label, hint and error to the input", async () => {
    render(<Field label="Reviewer name" hint="Shown in the log." error="Required." />);
    const input = screen.getByLabelText("Reviewer name");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const described = input.getAttribute("aria-describedby")!.split(" ");
    expect(described).toHaveLength(2);
    expect(document.getElementById(described[0])).toHaveTextContent("Shown in the log.");
    expect(document.getElementById(described[1])).toHaveTextContent("Required.");
    await userEvent.type(input, "A");
    expect(input).toHaveValue("A");
  });

  it("has no aria-invalid without an error, and renders a trailing control", () => {
    render(<Field label="Name" trailing={<button type="button">Show</button>} />);
    expect(screen.getByLabelText("Name")).not.toHaveAttribute("aria-invalid");
    expect(screen.getByRole("button", { name: "Show" })).toBeInTheDocument();
  });
});
