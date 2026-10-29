import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Logo, LogoMark } from "./Logo";

describe("Logo", () => {
  it("shows the bank wordmark and the product name; the mark is decorative", () => {
    const { container } = render(<Logo />);
    expect(screen.getByText("Green Link")).toBeInTheDocument();
    expect(screen.getByText("Digital Bank")).toBeInTheDocument();
    expect(screen.getByText("Fraud Desk")).toBeInTheDocument();
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("can hide the product name and render on paper", () => {
    render(<Logo tone="dark" showProduct={false} />);
    expect(screen.queryByText("Fraud Desk")).not.toBeInTheDocument();
    expect(screen.getByText("Green Link")).toHaveClass("text-brand");
  });

  it("LogoMark gives each instance unique clip ids", () => {
    const { container } = render(
      <>
        <LogoMark />
        <LogoMark />
      </>,
    );
    const ids = Array.from(container.querySelectorAll("clipPath")).map((c) => c.id);
    expect(new Set(ids).size).toBe(2);
  });
});
