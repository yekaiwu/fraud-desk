import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
// The badge asks the API; here it always answers "nothing to look at" (AppShell.badge.test.tsx covers the counts).
let me = { reviewer: "user1" as string | null, admin: false };
vi.mock("@/lib/api", () => ({ getNotificationSummary: () => Promise.resolve({ unread: 0, open_alerts: 0, badge: 0 }), getMe: () => Promise.resolve(me) }));

import { renderWithSWR } from "@/test/utils";
import { AppShell } from "./AppShell";

beforeEach(() => {
  pathname = "/";
});

describe("AppShell", () => {
  it("adds a Spending tab for an administrator account and for nobody else", async () => {
    me = { reviewer: "boss", admin: true };
    const admin = renderWithSWR(<AppShell>x</AppShell>);
    expect(await screen.findByRole("link", { name: "Spending" })).toHaveAttribute("href", "/spending");
    admin.unmount();
    me = { reviewer: "user1", admin: false };
    renderWithSWR(<AppShell>x</AppShell>);
    await screen.findByRole("link", { name: "Worklist" });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("link", { name: "Spending" })).toBeNull();
  });

  it("has landmarks, a skip link, the four nav items, the notifications bell and the disclaimer line", () => {
    render(<AppShell>content</AppShell>);
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent("content");
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(Array.from(nav.querySelectorAll("a")).map((a) => a.textContent)).toEqual(["Worklist", "New case", "Control performance", "Check catalogue"]);
    expect(Array.from(nav.querySelectorAll("a")).map((a) => a.getAttribute("href"))).toEqual(["/", "/upload", "/effectiveness", "/rules"]);
    // notifications is not a tab: it is a bell button in the header, outside the main navigation
    expect(nav.querySelector('a[href="/notifications"]')).toBeNull();
    const bell = screen.getByRole("link", { name: "Notifications" });
    expect(bell).toHaveAttribute("href", "/notifications");
    expect(screen.getByRole("banner")).toContainElement(bell);
    expect(bell.querySelector("svg")).not.toBeNull();
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
