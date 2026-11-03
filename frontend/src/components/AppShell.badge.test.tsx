import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let pathname = "/";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));
const getNotificationSummary = vi.fn();
vi.mock("@/lib/api", () => ({ getNotificationSummary: () => getNotificationSummary() }));

import { AppShell, badgeLabel } from "./AppShell";

const answer = (badge: number, over: Partial<{ unread: number; open_alerts: number }> = {}) =>
  getNotificationSummary.mockResolvedValue({ unread: over.unread ?? badge, open_alerts: over.open_alerts ?? 0, badge });

function shell(children: React.ReactNode = "x") {
  return (
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <AppShell>{children}</AppShell>
    </SWRConfig>
  );
}

beforeEach(() => {
  pathname = "/";
  getNotificationSummary.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Notifications tab badge", () => {
  it("draws nothing at zero: the link is just 'Notifications'", async () => {
    answer(0);
    render(shell());
    await waitFor(() => expect(getNotificationSummary).toHaveBeenCalled());
    const link = screen.getByRole("link", { name: "Notifications" });
    expect(link).toHaveAttribute("href", "/notifications");
    expect(link.textContent).toBe("Notifications");
    expect(screen.queryByTestId("notifications-badge")).toBeNull();
  });

  it("shows the count and puts it in the link's accessible name", async () => {
    answer(3, { unread: 2, open_alerts: 2 });
    render(shell());
    const link = await screen.findByRole("link", { name: "Notifications, 3 to look at" });
    expect(screen.getByTestId("notifications-badge")).toHaveTextContent("3");
    expect(screen.getByTestId("notifications-badge")).toHaveAttribute("aria-hidden", "true"); // read once, in the name
    expect(link).toHaveAttribute("href", "/notifications");
  });

  it("caps a big number", () => {
    expect(badgeLabel(7)).toBe("7");
    expect(badgeLabel(99)).toBe("99");
    expect(badgeLabel(100)).toBe("99+");
  });

  it("shows no badge when the answer fails or is nonsense, and the tab still works", async () => {
    getNotificationSummary.mockRejectedValue(new Error("down"));
    render(shell());
    await waitFor(() => expect(getNotificationSummary).toHaveBeenCalled());
    expect(screen.getByRole("link", { name: "Notifications" })).toBeInTheDocument();
    expect(screen.queryByTestId("notifications-badge")).toBeNull();
    cleanup();
    getNotificationSummary.mockResolvedValue({ unread: -4, open_alerts: 0, badge: -4 });
    render(shell());
    await waitFor(() => expect(getNotificationSummary).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("notifications-badge")).toBeNull();
  });

  it("asks again when the page changes", async () => {
    answer(1);
    const { rerender } = render(shell());
    await screen.findByRole("link", { name: "Notifications, 1 to look at" });
    expect(getNotificationSummary).toHaveBeenCalledTimes(1);
    answer(5);
    pathname = "/upload";
    rerender(shell());
    await screen.findByRole("link", { name: "Notifications, 5 to look at" });
    expect(getNotificationSummary).toHaveBeenCalledTimes(2);
  });

  it("asks again about once a minute", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    answer(0);
    render(shell());
    await waitFor(() => expect(getNotificationSummary).toHaveBeenCalledTimes(1));
    answer(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });
    await screen.findByRole("link", { name: "Notifications, 2 to look at" });
    expect(getNotificationSummary.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("is not asked for on the public tour or the sign-in page, which have no navigation", () => {
    answer(4);
    pathname = "/login";
    render(shell("sign in"));
    cleanup();
    pathname = "/welcome";
    render(shell("tour"));
    expect(getNotificationSummary).not.toHaveBeenCalled();
  });

  it("marks the tab as the current page on /notifications", async () => {
    answer(0);
    pathname = "/notifications";
    render(shell());
    expect(screen.getByRole("link", { name: "Notifications" })).toHaveAttribute("aria-current", "page");
  });
});
