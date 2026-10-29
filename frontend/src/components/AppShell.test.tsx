import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

import { AppShell } from "./AppShell";

beforeEach(() => {
  pathname = "/";
});

describe("AppShell", () => {
  it("has landmarks, a skip link, the four nav items and the disclaimer line", () => {
    render(<AppShell>content</AppShell>);
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent("content");
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(Array.from(nav.querySelectorAll("a")).map((a) => a.textContent)).toEqual(["Worklist", "New case", "Control performance", "Check catalogue"]);
    expect(Array.from(nav.querySelectorAll("a")).map((a) => a.getAttribute("href"))).toEqual(["/", "/upload", "/effectiveness", "/rules"]);
    expect(screen.getByText(/Decision support only; a human decides/)).toBeInTheDocument();
  });

  it("marks the current page and treats case pages as part of the queue", () => {
    pathname = "/upload";
    const { rerender } = render(<AppShell>x</AppShell>);
    expect(screen.getByRole("link", { name: "New case" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Worklist" })).not.toHaveAttribute("aria-current");
    pathname = "/cases/c_1";
    rerender(<AppShell>x</AppShell>);
    expect(screen.getByRole("link", { name: "Worklist" })).toHaveAttribute("aria-current", "page");
  });

  it("has a logout form that posts to the logout route, and a bare layout on /login", () => {
    const { container, rerender } = render(<AppShell>x</AppShell>);
    const form = container.querySelector('form[action="/api/auth/logout"]');
    expect(form).toHaveAttribute("method", "post");
    expect(screen.getByRole("button", { name: "Log out" })).toBeInTheDocument();
    pathname = "/login";
    rerender(<AppShell>sign in</AppShell>);
    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Log out" })).not.toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent("sign in");
  });

  it("carries the disclaimer and the fictitious-data line, and not the removed internal-use line", () => {
    render(<AppShell>x</AppShell>);
    const footer = screen.getByRole("contentinfo");
    expect(footer).toHaveTextContent("All data in this tool is fictitious.");
    expect(footer).not.toHaveTextContent(/internal use only/i);
  });

  it("has a skip link on the sign-in page too", () => {
    pathname = "/login";
    render(<AppShell>sign in</AppShell>);
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
  });

  it("shows no mock banner unless mock mode is on", () => {
    render(<AppShell>x</AppShell>);
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});
