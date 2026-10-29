import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button, buttonClasses } from "./Button";

describe("Button", () => {
  it("is a type=button by default and fires onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);
    const b = screen.getByRole("button", { name: "Save" });
    expect(b).toHaveAttribute("type", "button");
    await userEvent.click(b);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("supports every variant and size with a 44px target by default", () => {
    for (const variant of ["primary", "secondary", "ghost", "danger"] as const) {
      const { unmount } = render(<Button variant={variant}>{variant}</Button>);
      expect(screen.getByRole("button")).toBeInTheDocument();
      unmount();
    }
    render(<Button>Default</Button>);
    expect(screen.getByRole("button")).toHaveClass("min-h-11");
    expect(buttonClasses({ size: "sm" })).toContain("pointer-coarse:min-h-11");
    expect(buttonClasses({ size: "lg", fullWidth: true })).toContain("w-full");
  });

  it("when loading it is busy, disabled and does not click", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Saving
      </Button>,
    );
    const b = screen.getByRole("button", { name: "Saving" });
    expect(b).toBeDisabled();
    expect(b).toHaveAttribute("aria-busy", "true");
    await userEvent.click(b);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("renders the icon slot", () => {
    render(<Button icon={<svg data-testid="icon" />}>With icon</Button>);
    expect(screen.getByTestId("icon")).toBeInTheDocument();
  });

  it("renders a link when href is given, and an external anchor for http urls", () => {
    render(
      <>
        <Button href="/upload">New case</Button>
        <Button href="https://example.test/x">Outside</Button>
      </>,
    );
    expect(screen.getByRole("link", { name: "New case" })).toHaveAttribute("href", "/upload");
    expect(screen.getByRole("link", { name: "Outside" })).toHaveAttribute("href", "https://example.test/x");
  });

  it("a loading link is aria-disabled, out of the tab order and does not navigate", async () => {
    const onClick = vi.fn();
    render(
      <Button href="/x" loading onClick={onClick}>
        Go
      </Button>,
    );
    const a = screen.getByRole("link", { name: "Go" });
    expect(a).toHaveAttribute("aria-disabled", "true");
    expect(a).toHaveAttribute("tabindex", "-1");
    await userEvent.click(a);
    expect(onClick).not.toHaveBeenCalled();
  });
});
