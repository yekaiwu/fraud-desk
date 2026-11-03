import "@/test/mockEnv";
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";
import type { AlertsResponse, NotificationsPage as FeedPage } from "@/lib/monitoring";
import { readinessFixture } from "@/lib/mock/fixtures/monitoring";
import { resetMockState, state } from "@/lib/mock/state";
import { renderWithSWR } from "@/test/utils";
import { useNotificationBadge } from "@/lib/useNotificationSummary";
import NotificationsPage from "./notifications/page";

/** The page beside the navigation badge, so a refresh of the badge is visible (the real shell mounts the same hook). */
function WithBadge() {
  const n = useNotificationBadge("/notifications");
  return (
    <>
      <p data-testid="badge">{n}</p>
      <NotificationsPage />
    </>
  );
}

beforeEach(() => {
  resetMockState();
  state.latencyMs = 0;
  window.localStorage.clear();
});
afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

const emptyFeed: FeedPage = { as_of: "2026-10-27T10:30:00.000000Z", notifications: [], unread: 0, next_before: null };
const calmAlerts = (over: Partial<AlertsResponse> = {}): AlertsResponse => ({
  alerts: [], deliveries: [], sinks: ["in-app"], last_test: null, scheduler: "in_process", scheduler_note: "On", interval_minutes: 15,
  last_evaluated_at: "2026-10-27T10:26:00.000000Z", last_evaluated_by: "scheduler", monitor_stale: false, ...over,
});

const region = (name: string | RegExp) => screen.findByRole("region", { name });

describe("notifications page: what it shows", () => {
  it("has one h1 and the four sections", async () => {
    renderWithSWR(<NotificationsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Notifications" })).toBeInTheDocument();
    for (const name of ["Open alerts", "Recent notifications", "AI incidents", "Readiness", "Is the monitor running?"]) {
      expect(await region(name)).toBeInTheDocument();
    }
  });

  it("lists each open alert with the metric, the number against the limit, meaning, action and first and last seen", async () => {
    renderWithSWR(<NotificationsPage />);
    const open = await region("Open alerts");
    const cards = await within(open).findAllByRole("article");
    expect(cards).toHaveLength(2);
    const partial = cards.find((c) => within(c).queryByRole("heading", { name: "Cases only partly analysed" }))!;
    expect(partial).toHaveTextContent("partial_rate");
    expect(partial).toHaveTextContent("42% against a limit of 30%");
    expect(partial).toHaveTextContent("Share of cases where a stage failed");
    expect(within(partial).getByText("Recommended action").nextElementSibling).toHaveTextContent("Open the partial cases");
    expect(within(partial).getByText("First seen").nextElementSibling).toHaveTextContent(/UTC \(3 h ago\)/);
    expect(within(partial).getByText("Last seen").nextElementSibling).toHaveTextContent(/UTC \(4 min ago\)/);
    expect(within(partial).getByRole("button", { name: /Acknowledge/ })).toBeInTheDocument();
    expect(within(open).getByText("Acknowledged, still above the limit (1)")).toBeInTheDocument();
    expect(open).toHaveTextContent("Seen by j.lim");
  });

  it("shows the feed newest first with unread entries marked in words, and read ones with who read them", async () => {
    renderWithSWR(<NotificationsPage />);
    const feed = await region("Recent notifications");
    const list = await within(feed).findByRole("list", { name: /Notifications, newest first/ });
    const items = within(list).getAllByRole("article");
    expect(items[0]).toHaveTextContent("AI incident #42 reported (high)");
    expect(items[0]).toHaveTextContent("Unread");
    expect(items[0]).toHaveTextContent("High severity");
    expect(items.at(-1)).toHaveTextContent("Alert raised: A check fires much more or less often");
    expect(items.at(-1)).toHaveTextContent("Read by j.lim");
    expect(items.at(-1)).not.toHaveTextContent("Unread");
    expect(feed).toHaveTextContent("4 unread");
  });

  it("filters the feed: all, alerts, incidents, system", async () => {
    const user = userEvent.setup();
    renderWithSWR(<NotificationsPage />);
    const feed = await region("Recent notifications");
    await within(feed).findByRole("list", { name: /newest first/ });
    const tabs = within(feed).getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["All", "Alerts", "Incidents", "System"]);
    await user.click(within(feed).getByRole("tab", { name: "Incidents" }));
    await waitFor(() => expect(within(feed).getAllByRole("article")).toHaveLength(2));
    for (const a of within(feed).getAllByRole("article")) expect(a).toHaveTextContent(/AI incident #/);
    await user.click(within(feed).getByRole("tab", { name: "System" }));
    await waitFor(() => expect(within(feed).getAllByRole("article")).toHaveLength(1));
    expect(within(feed).getByRole("article")).toHaveTextContent("Test alert (not a real alert)");
    await user.click(within(feed).getByRole("tab", { name: "Alerts" }));
    await waitFor(() => expect(within(feed).getAllByRole("article")).toHaveLength(5));
  });

  it("pages the feed with Show older", async () => {
    const user = userEvent.setup();
    const mk = (ids: number[], next: number | null): FeedPage => ({
      ...emptyFeed, unread: 0, next_before: next,
      notifications: ids.map((id) => ({ id, ts: "2026-10-27T10:00:00.000000Z", kind: "test", category: "system", key: "test", severity: "info", title: `Test number ${id}`, text: "t", data: {}, read: true, read_at: "2026-10-27T10:01:00.000000Z", read_by: "a.tan" })),
    });
    const spy = vi.spyOn(api, "getNotifications").mockImplementation(async (q) => (q?.before ? mk([1], null) : mk([3, 2], 2)));
    renderWithSWR(<NotificationsPage />);
    const feed = await region("Recent notifications");
    await within(feed).findByText("Test number 3");
    await user.click(within(feed).getByRole("button", { name: "Show older" }));
    expect(await within(feed).findByText("Test number 1")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ before: 2 }));
    expect(within(feed).queryByRole("button", { name: "Show older" })).toBeNull();
  });

  it("shows the readiness items with exactly what is missing, and the owner as not named without alarm styling", async () => {
    renderWithSWR(<NotificationsPage />);
    const panel = await region("Readiness");
    const items = await within(panel).findAllByRole("listitem");
    const top = items.filter((li) => li.hasAttribute("data-status"));
    expect(top).toHaveLength(3);
    expect(top[0]).toHaveTextContent("Not named, decided by the bank");
    expect(top[0]).toHaveTextContent("Open item");
    expect(top[0]).toHaveTextContent("Set GLDB_AI_OWNER");
    expect(top[0].innerHTML).not.toMatch(/sev-high/); // the existing open-item style, not an error colour
    expect(top[1]).toHaveTextContent("No approval recorded");
    expect(top[2]).toHaveTextContent("Met");
    expect(top[2]).toHaveTextContent("The last test reached the Notifications page");
    expect(top[2]).toHaveTextContent("Delivery to this page is not the same as someone watching it");
    expect(top[2]).toHaveTextContent(/Last|on \d{4}-\d{2}-\d{2}/);
  });

  it("says 'thresholds changed since approval' when the approval is for different values", async () => {
    vi.spyOn(api, "getReadiness").mockResolvedValue(readinessFixture(Date.now(), null, "changed"));
    renderWithSWR(<NotificationsPage />);
    const panel = await region("Readiness");
    expect(await within(panel).findByText("Thresholds changed since approval")).toBeInTheDocument();
    expect(panel).toHaveTextContent("approved by M. Lim");
    expect(panel).toHaveTextContent("No test alert has been sent yet");
  });

  it("shows an approved set of thresholds with who and when", async () => {
    vi.spyOn(api, "getReadiness").mockResolvedValue(readinessFixture(Date.now(), null, "approved"));
    renderWithSWR(<NotificationsPage />);
    const panel = await region("Readiness");
    expect(await within(panel).findByText("Recorded")).toBeInTheDocument();
    expect(panel).toHaveTextContent("M. Lim, Chief Risk Officer");
  });

  it("lists incidents with who has seen them, and hides the resolution rows", async () => {
    renderWithSWR(<NotificationsPage />);
    const panel = await region("AI incidents");
    const list = await within(panel).findByRole("list", { name: /Incidents, newest first/ });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("#42");
    expect(rows[0]).toHaveTextContent("Nobody has acknowledged this yet.");
    expect(rows[1]).toHaveTextContent("Seen by j.lim");
    expect(rows[1]).toHaveTextContent("assigned to platform team");
    expect(rows[2]).toHaveTextContent("Resolved");
    expect(within(rows[2]).queryByRole("button")).toBeNull();
  });
});

describe("notifications page: scheduler line", () => {
  it("says running with the last check", async () => {
    renderWithSWR(<NotificationsPage />);
    const panel = await region("Is the monitor running?");
    const block = await waitFor(() => {
      const b = panel.querySelector("[data-scheduler]");
      expect(b).not.toBeNull();
      return b as HTMLElement;
    });
    expect(block).toHaveAttribute("data-scheduler", "running");
    expect(block).toHaveTextContent("Running");
    expect(block).toHaveTextContent("every 15 min inside this service");
    expect(block).toHaveTextContent(/Last checked .* \(4 min ago\)/);
  });

  it("says off plainly and never as healthy", async () => {
    state.monitor.scheduler = "off";
    state.monitor.alerts = [];
    renderWithSWR(<NotificationsPage />);
    const panel = await region("Is the monitor running?");
    await waitFor(() => expect(panel.querySelector("[data-scheduler='off']")).not.toBeNull());
    expect(panel).toHaveTextContent("Off");
    expect(panel).toHaveTextContent("Nothing is checking the limits");
    const open = await region("Open alerts");
    expect(await within(open).findByText("No open alerts")).toBeInTheDocument();
    expect(open).toHaveTextContent("No alerts here does not mean all is well");
  });

  it("says stale when the monitor has gone quiet", async () => {
    state.monitor.stale = true;
    renderWithSWR(<NotificationsPage />);
    const panel = await region("Is the monitor running?");
    await waitFor(() => expect(panel.querySelector("[data-scheduler='stale']")).not.toBeNull());
    expect(panel).toHaveTextContent("Stale");
    expect(panel).toHaveTextContent("Alerts may have been missed");
  });
});

describe("notifications page: loading, empty and error", () => {
  it("shows busy placeholders while loading", async () => {
    const never = new Promise<never>(() => {});
    vi.spyOn(api, "getAlerts").mockReturnValue(never);
    vi.spyOn(api, "getNotifications").mockReturnValue(never);
    vi.spyOn(api, "getIncidents").mockReturnValue(never);
    vi.spyOn(api, "getReadiness").mockReturnValue(never);
    renderWithSWR(<NotificationsPage />);
    for (const label of ["Loading open alerts", "Loading notifications", "Loading incidents", "Loading readiness", "Loading monitor status"]) {
      expect(await screen.findByLabelText(label)).toHaveAttribute("aria-busy", "true");
    }
  });

  it("says what is missing when there is nothing to show", async () => {
    vi.spyOn(api, "getAlerts").mockResolvedValue(calmAlerts());
    vi.spyOn(api, "getNotifications").mockResolvedValue(emptyFeed);
    vi.spyOn(api, "getIncidents").mockResolvedValue({ incidents: [] });
    renderWithSWR(<NotificationsPage />);
    expect(await screen.findByText("No open alerts")).toBeInTheDocument();
    expect(await screen.findByText("Nothing yet")).toBeInTheDocument();
    expect(await screen.findByText("No incidents reported")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark all as read" })).toBeDisabled();
    expect(screen.getByText("All read")).toBeInTheDocument();
  });

  it("shows an error with a retry per section, and the other sections still work", async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(api, "getAlerts").mockRejectedValueOnce(new ApiErrorException({ code: "network_error", message: "x" }));
    renderWithSWR(<NotificationsPage />);
    const open = await region("Open alerts");
    const alert = await within(open).findByRole("alert");
    expect(alert).toHaveTextContent("Cannot reach the API");
    expect(await screen.findByText("AI incident #42 reported (high)")).toBeInTheDocument(); // the feed is unaffected
    spy.mockRestore();
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await within(open).findAllByRole("article")).toHaveLength(2);
  });
});

describe("notifications page: acknowledging", () => {
  const card = async (name: RegExp) => {
    const open = await region("Open alerts");
    return (await within(open).findAllByRole("article")).find((c) => within(c).queryByRole("heading", { name }))!;
  };

  it("needs a reason: with a name but no reason nothing is sent and the field says why", async () => {
    const user = userEvent.setup();
    const ack = vi.spyOn(api, "acknowledgeAlert");
    renderWithSWR(<NotificationsPage />);
    const c = await card(/partly analysed/);
    await user.click(within(c).getByRole("button", { name: /Acknowledge/ }));
    const form = within(c).getByRole("form", { name: "Acknowledge Cases only partly analysed" });
    await user.type(within(form).getByLabelText(/Reviewer name/), "a.tan");
    await user.click(within(form).getByRole("button", { name: "Record acknowledgement" }));
    expect(ack).not.toHaveBeenCalled();
    expect(within(form).getByText("Enter a reason of at least 5 characters.")).toBeInTheDocument();
    expect(within(form).getByLabelText(/Reason \(required/)).toHaveAttribute("aria-invalid", "true");
    await user.type(within(form).getByLabelText(/Reason \(required/), "abc");
    await user.click(within(form).getByRole("button", { name: "Record acknowledgement" }));
    expect(ack).not.toHaveBeenCalled();
  });

  it("needs a name too", async () => {
    const user = userEvent.setup();
    const ack = vi.spyOn(api, "acknowledgeAlert");
    renderWithSWR(<NotificationsPage />);
    const c = await card(/partly analysed/);
    await user.click(within(c).getByRole("button", { name: /Acknowledge/ }));
    const form = within(c).getByRole("form");
    await user.type(within(form).getByLabelText(/Reason \(required/), "Looking at it now");
    await user.click(within(form).getByRole("button", { name: "Record acknowledgement" }));
    expect(ack).not.toHaveBeenCalled();
    expect(within(form).getByText(/Reviewer name must be 2 to 64 characters/)).toBeInTheDocument();
  });

  it("with a name and a reason records it, remembers the name, moves the alert out of the open list and refreshes the badge", async () => {
    const user = userEvent.setup();
    const ack = vi.spyOn(api, "acknowledgeAlert");
    const summary = vi.spyOn(api, "getNotificationSummary");
    renderWithSWR(<WithBadge />);
    expect(await screen.findByText("4", { selector: "[data-testid='badge']" })).toBeInTheDocument(); // 2 open + 4 unread, minus the 2 that are the same events
    const c = await card(/Model spend today/);
    await user.click(within(c).getByRole("button", { name: /Acknowledge/ }));
    const form = within(c).getByRole("form");
    await user.type(within(form).getByLabelText(/Reviewer name/), "a.tan");
    await user.type(within(form).getByLabelText(/Reason \(required/), "Raised the cap with finance");
    await user.click(within(form).getByRole("button", { name: "Record acknowledgement" }));
    await waitFor(() => expect(ack).toHaveBeenCalledWith("daily_spend_ratio", { reviewer: "a.tan", reason: "Raised the cap with finance" }));
    const open = await region("Open alerts");
    expect(await within(open).findByText("Acknowledged: Model spend today")).toBeInTheDocument();
    await waitFor(() => expect(within(open).getAllByRole("article")).toHaveLength(1));
    expect(open).toHaveTextContent("Acknowledged, still above the limit (2)");
    expect(open).toHaveTextContent("Raised the cap with finance");
    expect(window.localStorage.getItem("gldb.reviewer")).toBe("a.tan");
    await waitFor(() => expect(summary.mock.calls.length).toBeGreaterThan(1));
    expect(await screen.findByText("3", { selector: "[data-testid='badge']" })).toBeInTheDocument();
    expect(state.monitor.alerts.find((a) => a.key === "daily_spend_ratio")?.status).toBe("acknowledged");
  });

  it("explains a refusal in plain words and keeps the form open", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "acknowledgeAlert").mockRejectedValue(new ApiErrorException({ code: "alert_not_open", message: "x" }, 409));
    renderWithSWR(<NotificationsPage />);
    const c = await card(/partly analysed/);
    await user.click(within(c).getByRole("button", { name: /Acknowledge/ }));
    const form = within(c).getByRole("form");
    await user.type(within(form).getByLabelText(/Reviewer name/), "a.tan");
    await user.type(within(form).getByLabelText(/Reason \(required/), "Seen already by me");
    await user.click(within(form).getByRole("button", { name: "Record acknowledgement" }));
    expect(await within(form).findByText("Already handled")).toBeInTheDocument();
    expect(within(c).getByRole("form")).toBeInTheDocument();
  });

  it("can be cancelled without sending anything", async () => {
    const user = userEvent.setup();
    const ack = vi.spyOn(api, "acknowledgeAlert");
    renderWithSWR(<NotificationsPage />);
    const c = await card(/partly analysed/);
    await user.click(within(c).getByRole("button", { name: /Acknowledge/ }));
    await user.click(within(c).getByRole("button", { name: "Cancel" }));
    expect(within(c).queryByRole("form")).toBeNull();
    expect(ack).not.toHaveBeenCalled();
  });
});

describe("notifications page: other actions", () => {
  it("sends a test alert, which then appears in the feed, and refuses a second one straight away", async () => {
    const user = userEvent.setup();
    renderWithSWR(<NotificationsPage />);
    const status = await region("Is the monitor running?");
    await user.click(within(status).getByRole("button", { name: "Send test alert" }));
    expect(await within(status).findByText("Test alert sent")).toBeInTheDocument();
    expect(status).toHaveTextContent("Delivered to the Notifications page");
    const feed = await region("Recent notifications");
    await waitFor(() => expect(within(feed).getAllByText("Test alert (not a real alert)")).toHaveLength(2));
    await user.click(within(status).getByRole("button", { name: "Send test alert" }));
    expect(await within(status).findByText("Slow down")).toBeInTheDocument();
  });

  it("marks one notification read after asking for a name once, then remembers it", async () => {
    const user = userEvent.setup();
    const mark = vi.spyOn(api, "markNotificationsRead");
    renderWithSWR(<NotificationsPage />);
    const feed = await region("Recent notifications");
    const first = (await within(feed).findAllByRole("article"))[0];
    await user.click(within(first).getByRole("button", { name: /Mark as read/ }));
    expect(mark).not.toHaveBeenCalled();
    const form = within(feed).getByRole("form", { name: "Your name for the read mark" });
    await user.click(within(form).getByRole("button", { name: "Mark as read" }));
    expect(within(form).getByText(/Enter a name of 2 to 64 characters/)).toBeInTheDocument();
    await user.type(within(form).getByLabelText("Your name"), "a.tan");
    await user.click(within(form).getByRole("button", { name: "Mark as read" }));
    await waitFor(() => expect(mark).toHaveBeenCalledWith({ ids: [8], reviewer: "a.tan" }));
    await waitFor(() => expect(feed).toHaveTextContent("3 unread"));
    // the name is remembered: the next one is sent at once
    const next = within(feed).getAllByRole("article").find((a) => a.hasAttribute("data-unread"))!;
    await user.click(within(next).getByRole("button", { name: /Mark as read/ }));
    await waitFor(() => expect(mark).toHaveBeenCalledTimes(2));
    expect(within(feed).queryByRole("form", { name: "Your name for the read mark" })).toBeNull();
  });

  it("marks everything read", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem("gldb.reviewer", "a.tan");
    const mark = vi.spyOn(api, "markNotificationsRead");
    renderWithSWR(<NotificationsPage />);
    const feed = await region("Recent notifications");
    await within(feed).findAllByRole("article");
    await user.click(within(feed).getByRole("button", { name: "Mark all as read" }));
    await waitFor(() => expect(mark).toHaveBeenCalledWith({ all: true, reviewer: "a.tan" }));
    await waitFor(() => expect(feed).toHaveTextContent("All read"));
    expect(within(feed).getByRole("button", { name: "Mark all as read" })).toBeDisabled();
  });

  it("reports an incident: severity, a summary of at least 10 characters, a name", async () => {
    const user = userEvent.setup();
    const report = vi.spyOn(api, "reportIncident");
    renderWithSWR(<NotificationsPage />);
    const panel = await region("AI incidents");
    await user.click(await within(panel).findByRole("button", { name: "Report an incident" }));
    const form = within(panel).getByRole("form", { name: "Report an AI incident" });
    await user.selectOptions(within(form).getByLabelText("Severity"), "high");
    await user.type(within(form).getByLabelText(/Summary/), "too short");
    await user.click(within(form).getByRole("button", { name: "Report incident" }));
    expect(report).not.toHaveBeenCalled();
    expect(within(form).getByText(/at least 10 characters/, { selector: "p" })).toBeInTheDocument();
    await user.type(within(form).getByLabelText(/Summary/), " and more words");
    await user.type(within(form).getByLabelText(/Your name/), "a.tan");
    await user.click(within(form).getByRole("button", { name: "Report incident" }));
    await waitFor(() => expect(report).toHaveBeenCalledWith({ severity: "high", summary: "too short and more words", case_id: undefined, reviewer: "a.tan" }));
    expect(await within(panel).findByText("Incident #43 reported")).toBeInTheDocument();
    expect(within(panel).getByText("too short and more words")).toBeInTheDocument();
  });

  it("acknowledges an incident with a note and an assignee", async () => {
    const user = userEvent.setup();
    const ack = vi.spyOn(api, "acknowledgeIncident");
    renderWithSWR(<NotificationsPage />);
    const panel = await region("AI incidents");
    const row = (await within(panel).findAllByRole("listitem")).find((li) => li.textContent?.includes("#42"))!;
    await user.click(within(row).getByRole("button", { name: /Acknowledge/ }));
    const form = within(row).getByRole("form", { name: "Acknowledge incident 42" });
    await user.type(within(form).getByLabelText(/Reviewer name/), "j.lim");
    await user.type(within(form).getByLabelText(/Note \(required/), "On it");
    await user.type(within(form).getByLabelText(/Assigned to/), "platform team");
    await user.click(within(form).getByRole("button", { name: "Record acknowledgement" }));
    await waitFor(() => expect(ack).toHaveBeenCalledWith(42, { note: "On it", assigned_to: "platform team", reviewer: "j.lim" }));
    await waitFor(() => expect(within(panel).getAllByText(/Seen by j.lim/).length).toBeGreaterThan(1));
  });
});

describe("notifications page: wording and layout", () => {
  it("never asks anyone to approve or decide an alert", async () => {
    renderWithSWR(<NotificationsPage />);
    for (const name of ["Open alerts", "Recent notifications", "Is the monitor running?"]) {
      const r = await region(name);
      await waitFor(() => expect(r.querySelector("[aria-busy='true']")).toBeNull());
      expect(r.textContent ?? "", name).not.toMatch(/\b(approve|approved|decide|decides|decision|authentic|genuine|verified)\b/i);
    }
  });

  it("is a phone-first single column that becomes two columns on a wide screen", async () => {
    const { container } = renderWithSWR(<NotificationsPage />);
    await region("Open alerts");
    const grid = container.querySelector(".lg\\:grid")!;
    expect(grid.className).toContain("lg:grid-cols-[minmax(0,1fr)_24rem]");
    for (const child of Array.from(grid.children)) expect(child.className).toContain("min-w-0");
    // touch targets: the main actions stretch to the full width on a phone
    const status = await region("Is the monitor running?");
    expect(within(status).getByRole("button", { name: "Send test alert" }).className).toContain("w-full");
    expect(within(status).getByRole("button", { name: "Send test alert" }).className).toContain("sm:w-auto");
    const open = await region("Open alerts");
    const ackBtn = (await within(open).findAllByRole("button", { name: /Acknowledge/ }))[0];
    expect(ackBtn.className).toContain("w-full");
    expect(ackBtn.className).toContain("min-h-11");
  });
});
