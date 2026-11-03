import { beforeEach, describe, expect, it } from "vitest";
import { ApiErrorException } from "../errors";
import * as mock from "./handlers";
import { resetMockState, state } from "./state";

beforeEach(() => {
  resetMockState();
  state.latencyMs = 0;
});

const code = async (p: Promise<unknown>) => {
  const e = await p.then(() => null, (x) => x);
  expect(e).toBeInstanceOf(ApiErrorException);
  return (e as ApiErrorException).error.code;
};

describe("mock notifications", () => {
  it("lists newest first, pages by before, filters by category and unread", async () => {
    const all = await mock.getNotifications();
    expect(all.notifications.map((n) => n.id)).toEqual([8, 7, 6, 5, 4, 3, 2, 1]);
    expect(all.unread).toBe(4);
    const p1 = await mock.getNotifications({ limit: 3 });
    expect(p1.notifications.map((n) => n.id)).toEqual([8, 7, 6]);
    expect(p1.next_before).toBe(6);
    const p3 = await mock.getNotifications({ limit: 3, before: 3 });
    expect(p3.notifications.map((n) => n.id)).toEqual([2, 1]);
    expect(p3.next_before).toBeNull();
    expect((await mock.getNotifications({ category: "incidents" })).notifications.map((n) => n.id)).toEqual([8, 3]);
    expect((await mock.getNotifications({ unread: true })).notifications.map((n) => n.id)).toEqual([8, 7, 6, 5]);
    expect((await mock.getNotifications({ category: "system", unread: true })).unread).toBe(4); // the count ignores the filter
  });

  it("counts an open alert and its own notification once in the badge", async () => {
    expect(await mock.getNotificationSummary()).toEqual({ unread: 4, open_alerts: 2, badge: 4 });
    await mock.acknowledgeAlert("partial_rate", { reviewer: "a.tan", reason: "Looking at it" });
    expect(await mock.getNotificationSummary()).toEqual({ unread: 3, open_alerts: 1, badge: 3 });
  });

  it("marks read by id or all, under a name, and refuses a missing name", async () => {
    expect(await code(mock.markNotificationsRead({ all: true }))).toBe("reviewer_required");
    expect(await mock.markNotificationsRead({ ids: [8, 999], reviewer: "a.tan" })).toEqual({ marked: 1, unread: 3 });
    expect((await mock.getNotifications()).notifications[0]).toMatchObject({ read: true, read_by: "a.tan" });
    expect(await mock.markNotificationsRead({ all: true, reviewer: "a.tan" })).toEqual({ marked: 3, unread: 0 });
    expect(await mock.markNotificationsRead({ all: true, reviewer: "a.tan" })).toEqual({ marked: 0, unread: 0 });
  });
});

describe("mock alerts", () => {
  it("acknowledging needs a name and a reason and only works on an open alert", async () => {
    expect(await code(mock.acknowledgeAlert("partial_rate", { reason: "enough reason" }))).toBe("reviewer_required");
    expect(await code(mock.acknowledgeAlert("partial_rate", { reviewer: "a.tan", reason: "abc" }))).toBe("reason_required");
    expect(await code(mock.acknowledgeAlert("nope", { reviewer: "a.tan", reason: "enough reason" }))).toBe("not_found");
    expect(await code(mock.acknowledgeAlert("rule_drift", { reviewer: "a.tan", reason: "enough reason" }))).toBe("alert_not_open");
    const { alert } = await mock.acknowledgeAlert("partial_rate", { reviewer: "a.tan", reason: "enough reason" });
    expect(alert).toMatchObject({ status: "acknowledged", acknowledged_by: "a.tan", note: "enough reason" });
  });

  it("reports the scheduler as the page needs it", async () => {
    expect(await mock.getAlerts()).toMatchObject({ scheduler: "in_process", interval_minutes: 15, monitor_stale: false, sinks: ["in-app"] });
    state.monitor.scheduler = "off";
    expect(await mock.getAlerts()).toMatchObject({ scheduler: "off", interval_minutes: null, monitor_stale: false });
  });

  it("a test alert lands in the feed, updates the last test, and is limited to one every 30 seconds", async () => {
    const r = await mock.sendTestAlert();
    expect(r).toMatchObject({ delivered: true, sinks_configured: ["in-app"], message: null });
    expect((await mock.getNotifications()).notifications[0]).toMatchObject({ kind: "test", category: "system", read: false });
    expect((await mock.getAlerts()).last_test?.at).toBe(r.sent_at);
    expect(await code(mock.sendTestAlert())).toBe("rate_limited");
  });
});

describe("mock readiness and incidents", () => {
  it("leaves the owner and the approval open and says the page is not a person watching", async () => {
    const r = await mock.getReadiness();
    expect(r.ready).toBe(false);
    expect(r.items.map((i) => [i.id, i.status])).toEqual([["5.23(a)", "not_met"], ["5.23(b)", "not_met"], ["5.23(c)", "met"]]);
    expect(r.items[2].detail).toMatch(/not the same as someone watching/);
    expect(r.items[2].page_only).toBe(true);
  });

  it("reports an incident (announced from medium up) and acknowledges it with an assignee", async () => {
    expect(await code(mock.reportIncident({ severity: "low", summary: "short", reviewer: "a.tan" }))).toBe("validation_error");
    const low = await mock.reportIncident({ severity: "low", summary: "a low report here", reviewer: "a.tan" });
    expect((await mock.getNotifications()).notifications[0].id).toBe(8); // nothing new
    const high = await mock.reportIncident({ severity: "high", summary: "a high report here", reviewer: "a.tan" });
    expect(high.id).toBe(low.id + 1);
    expect((await mock.getNotifications()).notifications[0]).toMatchObject({ kind: "incident", severity: "high", key: `incident:${high.id}` });
    expect(await code(mock.acknowledgeIncident(9999, { note: "enough note", reviewer: "a.tan" }))).toBe("not_found");
    expect(await code(mock.acknowledgeIncident(37, { note: "enough note", reviewer: "a.tan" }))).toBe("not_found"); // resolved
    await mock.acknowledgeIncident(high.id, { note: "enough note", assigned_to: "platform", reviewer: "j.lim" });
    const inc = (await mock.getIncidents()).incidents.find((i) => i.id === high.id)!;
    expect(inc).toMatchObject({ acknowledged_by: "j.lim", assigned_to: "platform", status: "open" });
  });
});
