/** Custom checks have no navigation entry: they live on the Check catalogue page, whatever /me says. */
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
const getMe = vi.fn();
vi.mock("@/lib/api", () => ({ getNotificationSummary: () => Promise.resolve({ unread: 0, open_alerts: 0, badge: 0 }), getMe: () => getMe() }));

import { renderWithSWR } from "@/test/utils";
import { AppShell } from "./AppShell";

const links = () => Array.from(screen.getByRole("navigation", { name: "Main" }).querySelectorAll("a")).map((a) => a.textContent);
const hrefs = () => Array.from(screen.getByRole("navigation", { name: "Main" }).querySelectorAll("a")).map((a) => a.getAttribute("href"));
const settle = () => new Promise((r) => setTimeout(r, 40));
const BASE = ["Worklist", "New case", "Control performance", "Check catalogue"];

beforeEach(() => {
  pathname = "/";
  getMe.mockReset();
});
afterEach(cleanup);

describe("navigation has no entry for custom checks", () => {
  it.each([
    ["the server says custom_rules is true", { reviewer: "alice", admin: false, custom_rules: true }],
    ["the server says custom_rules is false", { reviewer: "alice", admin: false, custom_rules: false }],
    ["an older server does not report custom_rules at all", { reviewer: "alice", admin: false }],
    ["nobody is signed in by account", { reviewer: null, admin: false, custom_rules: false }],
  ])("when %s", async (_why, me) => {
    getMe.mockResolvedValue(me);
    renderWithSWR(<AppShell>x</AppShell>);
    await settle();
    expect(links()).toEqual(BASE);
    expect(hrefs()).not.toContain("/rules/custom");
  });

  it("while /me is still loading and when /me fails", async () => {
    getMe.mockReturnValue(new Promise(() => {}));
    const pending = renderWithSWR(<AppShell>x</AppShell>);
    await settle();
    expect(links()).toEqual(BASE);
    pending.unmount();
    getMe.mockRejectedValue(new Error("down"));
    renderWithSWR(<AppShell>x</AppShell>);
    await settle();
    expect(links()).toEqual(BASE);
  });

  it("a page below /rules still lights only the Check catalogue tab", async () => {
    getMe.mockResolvedValue({ reviewer: "alice", admin: false, custom_rules: true });
    pathname = "/rules/custom";
    renderWithSWR(<AppShell>x</AppShell>);
    expect(await screen.findByRole("link", { name: "Check catalogue" })).toHaveAttribute("aria-current", "page");
    expect(document.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  });

  it("is not asked for on the sign-in and welcome pages", async () => {
    getMe.mockResolvedValue({ reviewer: "alice", admin: false, custom_rules: true });
    for (const p of ["/login", "/welcome"]) {
      pathname = p;
      const v = renderWithSWR(<AppShell>content</AppShell>);
      await settle();
      expect(screen.queryByRole("link", { name: /custom/i })).toBeNull();
      v.unmount();
    }
    expect(getMe).not.toHaveBeenCalled();
  });
});
