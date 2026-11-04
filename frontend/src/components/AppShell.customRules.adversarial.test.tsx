/** The Custom rules entry in the navigation appears only when /me says so, and never as a guess. */
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
const getMe = vi.fn();
vi.mock("@/lib/api", () => ({ getNotificationSummary: () => Promise.resolve({ unread: 0, open_alerts: 0, badge: 0 }), getMe: () => getMe() }));

import { renderWithSWR } from "@/test/utils";
import { AppShell } from "./AppShell";

const links = () => Array.from(screen.getByRole("navigation", { name: "Main" }).querySelectorAll("a")).map((a) => a.textContent);
const settle = () => new Promise((r) => setTimeout(r, 40));

beforeEach(() => {
  pathname = "/";
  getMe.mockReset();
});
afterEach(cleanup);

describe("navigation entry for custom rules", () => {
  it.each([
    ["the server says custom_rules is false", { reviewer: "alice", admin: false, custom_rules: false }],
    ["an older server does not report custom_rules at all", { reviewer: "alice", admin: false }],
    ["nobody is signed in by account", { reviewer: null, admin: false, custom_rules: false }],
  ])("is hidden when %s", async (_why, me) => {
    getMe.mockResolvedValue(me);
    renderWithSWR(<AppShell>x</AppShell>);
    await settle();
    expect(links()).not.toContain("Custom rules");
  });

  it("is hidden while /me is still loading and when /me fails", async () => {
    getMe.mockReturnValue(new Promise(() => {}));
    const pending = renderWithSWR(<AppShell>x</AppShell>);
    await settle();
    expect(links()).toEqual(["Worklist", "New case", "Control performance", "Check catalogue"]);
    pending.unmount();
    getMe.mockRejectedValue(new Error("down"));
    renderWithSWR(<AppShell>x</AppShell>);
    await settle();
    expect(links()).toEqual(["Worklist", "New case", "Control performance", "Check catalogue"]);
  });

  it("is shown for a signed-in account that has the feature, and points at the page", async () => {
    getMe.mockResolvedValue({ reviewer: "alice", admin: false, custom_rules: true });
    renderWithSWR(<AppShell>x</AppShell>);
    expect(await screen.findByRole("link", { name: "Custom rules" })).toHaveAttribute("href", "/rules/custom");
  });

  it("the tab stays lit on a page below /rules/custom, and the catalogue tab does not light with it", async () => {
    getMe.mockResolvedValue({ reviewer: "alice", admin: false, custom_rules: true });
    pathname = "/rules/custom/anything";
    renderWithSWR(<AppShell>x</AppShell>);
    expect(await screen.findByRole("link", { name: "Custom rules" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Check catalogue" })).not.toHaveAttribute("aria-current");
    expect(document.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  });

  it("a path that only starts with the same letters does not light it", async () => {
    getMe.mockResolvedValue({ reviewer: "alice", admin: false, custom_rules: true });
    pathname = "/rules/customised";
    renderWithSWR(<AppShell>x</AppShell>);
    await screen.findByRole("link", { name: "Custom rules" });
    expect(screen.getByRole("link", { name: "Custom rules" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Check catalogue" })).toHaveAttribute("aria-current", "page");
  });

  it("is not asked for, or shown, on the sign-in and welcome pages", async () => {
    getMe.mockResolvedValue({ reviewer: "alice", admin: false, custom_rules: true });
    for (const p of ["/login", "/welcome"]) {
      pathname = p;
      const v = renderWithSWR(<AppShell>content</AppShell>);
      await settle();
      expect(screen.queryByRole("link", { name: "Custom rules" })).toBeNull();
      v.unmount();
    }
    expect(getMe).not.toHaveBeenCalled();
  });
});
