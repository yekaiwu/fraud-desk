import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import LoginPage from "./page";

describe("/login page", () => {
  it("links to the product tour from the about panel and from the form", () => {
    render(<LoginPage />);
    const links = screen.getAllByRole("link", { name: /How it works/ });
    expect(links).toHaveLength(2);
    for (const a of links) expect(a).toHaveAttribute("href", "/welcome");
  });
});
