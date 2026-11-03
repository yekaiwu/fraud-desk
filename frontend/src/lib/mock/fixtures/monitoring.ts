/**
 * Mock-mode data for the Notifications page. Everything here is fictitious. Typed against lib/monitoring.ts
 * (written from docs/api.md section 9b), so the build fails when a fixture drifts from the contract.
 *
 * Times are relative to the moment the mock state is built, so the page looks live: "4 min ago" stays true.
 * Texts follow what the backend writes (app/notifications.py): metric names, numbers, thresholds and ids only,
 * never a case's content.
 */
import type { AlertState, AppNotification, Incident, LastTest, Readiness } from "../../monitoring";

const iso = (nowMs: number, minutesAgo: number): string => new Date(nowMs - minutesAgo * 60_000).toISOString().replace(/\.(\d{3})Z$/, ".$1000Z");

export const MOCK_INTERVAL_MINUTES = 15;

export function alertFixtures(nowMs: number): AlertState[] {
  return [
    {
      key: "partial_rate", status: "open", first_seen: iso(nowMs, 190), last_seen: iso(nowMs, 4), last_notified: iso(nowMs, 190), notify_count: 1, notified: true,
      value: 0.42, threshold: 0.3,
      meaning: "Share of cases where a stage failed, a page could not be read or the budget ran out.",
      action: "Open the partial cases; check the model service and the budget settings.",
      acknowledged_by: null, acknowledged_at: null, note: null, resolved_at: null,
    },
    {
      key: "daily_spend_ratio", status: "open", first_seen: iso(nowMs, 55), last_seen: iso(nowMs, 4), last_notified: iso(nowMs, 55), notify_count: 1, notified: true,
      value: 0.86, threshold: 0.8,
      meaning: "Today's model spend against the daily cap.",
      action: "Expect refusals soon; raise the cap on purpose or wait for the next UTC day.",
      acknowledged_by: null, acknowledged_at: null, note: null, resolved_at: null,
    },
    {
      key: "rule_drift", status: "acknowledged", first_seen: iso(nowMs, 60 * 30), last_seen: iso(nowMs, 4), last_notified: iso(nowMs, 60 * 30), notify_count: 1, notified: true,
      value: 0.31, threshold: 0.25,
      meaning: "Largest change in how often one rule fires, this window against the last (rule: bs_pass_through).",
      action: "A sudden swing means a new kind of document, a changed reader or a changed rule; compare a few cases by hand.",
      acknowledged_by: "j.lim", acknowledged_at: iso(nowMs, 60 * 28), note: "Seen. A batch of one broker's statements arrived on Monday; watching it.", resolved_at: null,
    },
  ];
}

export function notificationFixtures(nowMs: number): AppNotification[] {
  // Oldest first here; ids ascend with time, the handlers sort newest first.
  const mk = (id: number, minutesAgo: number, p: Partial<AppNotification> & Pick<AppNotification, "kind" | "category" | "key" | "title" | "text">): AppNotification => ({
    id, ts: iso(nowMs, minutesAgo), severity: "attention", data: {}, read: false, read_at: null, read_by: null, ...p,
  });
  const readBy = (by: string, minutesAgo: number) => ({ read: true, read_at: iso(nowMs, minutesAgo), read_by: by });
  return [
    mk(1, 60 * 30, { kind: "alert", category: "alerts", key: "rule_drift", title: "Alert raised: rule_drift", data: { metric: "rule_drift", value: 0.31, threshold: 0.25 }, ...readBy("j.lim", 60 * 28),
      text: "Value 0.31 is above the threshold 0.25.\nMeaning: Largest change in how often one rule fires, this window against the last (rule: bs_pass_through).\nAction: A sudden swing means a new kind of document, a changed reader or a changed rule; compare a few cases by hand." }),
    mk(2, 60 * 26, { kind: "test", category: "system", key: "test", severity: "info", title: "Test alert (not a real alert)", text: "Sent to check that alerts reach this page. No action is needed.", ...readBy("m.ong", 60 * 26 - 1) }),
    mk(3, 60 * 20, { kind: "incident", category: "incidents", key: "incident:41", severity: "attention", title: "AI incident #41 reported (medium)", data: { incident_id: 41, severity: "medium", reported_by: "a.tan" },
      text: "An AI incident of severity medium was reported by a.tan. Its summary is in the incident list on this page.", ...readBy("j.lim", 60 * 19) }),
    mk(4, 60 * 8, { kind: "alert", category: "alerts", key: "check_error_rate", title: "Alert raised: check_error_rate", data: { metric: "check_error_rate", value: 0.08, threshold: 0.05 }, ...readBy("a.tan", 60 * 7),
      text: "Value 0.08 is above the threshold 0.05.\nMeaning: Share of cases where a check raised an error instead of running.\nAction: Read the check_error warnings on those cases and raise an incident." }),
    mk(5, 60 * 7, { kind: "resolved", category: "alerts", key: "check_error_rate", severity: "info", title: "Back within the limit: check_error_rate", data: { metric: "check_error_rate", value: 0.02, threshold: 0.05 },
      text: "Value 0.02 is back within the threshold 0.05." }),
    mk(6, 190, { kind: "alert", category: "alerts", key: "partial_rate", title: "Alert raised: partial_rate", data: { metric: "partial_rate", value: 0.42, threshold: 0.3 },
      text: "Value 0.42 is above the threshold 0.3.\nMeaning: Share of cases where a stage failed, a page could not be read or the budget ran out.\nAction: Open the partial cases; check the model service and the budget settings." }),
    mk(7, 55, { kind: "alert", category: "alerts", key: "daily_spend_ratio", title: "Alert raised: daily_spend_ratio", data: { metric: "daily_spend_ratio", value: 0.86, threshold: 0.8 },
      text: "Value 0.86 is above the threshold 0.8.\nMeaning: Today's model spend against the daily cap.\nAction: Expect refusals soon; raise the cap on purpose or wait for the next UTC day." }),
    mk(8, 22, { kind: "incident", category: "incidents", key: "incident:42", severity: "high", title: "AI incident #42 reported (high)", data: { incident_id: 42, severity: "high", reported_by: "a.tan" },
      text: "An AI incident of severity high was reported by a.tan. Its summary is in the incident list on this page." }),
  ];
}

export function incidentFixtures(nowMs: number): Incident[] {
  return [
    { id: 42, reported_at: iso(nowMs, 22), reported_by: "a.tan", severity: "high", summary: "A flag on a bank statement pointed at the wrong page, so the reviewer could not find the row it described.", case_ref: null, status: "open", resolves: null },
    {
      id: 41, reported_at: iso(nowMs, 60 * 20), reported_by: "a.tan", severity: "medium", summary: "The review team summary repeated one finding twice; no decision was affected.", case_ref: null, status: "open", resolves: null,
      acknowledged_by: "j.lim", acknowledged_at: iso(nowMs, 60 * 19), ack_note: "Seen; assigned to the platform team.", assigned_to: "platform team",
    },
    { id: 37, reported_at: iso(nowMs, 60 * 24 * 6), reported_by: "j.lim", severity: "low", summary: "A date on an invoice was read with day and month swapped.", case_ref: null, status: "resolved", resolves: null },
  ];
}

export function lastTestFixture(nowMs: number): LastTest {
  const at = iso(nowMs, 60 * 26);
  return { at, by: "m.ong", delivered_to: ["in-app"], results: [{ sink: "in-app", ok: true, attempts: 1, error: null, at }] };
}

/** Readiness as the backend would answer with the page as the only sink: the owner is not named, nothing approved. */
export function readinessFixture(nowMs: number, lastTest: LastTest | null, thresholds: "none" | "changed" | "approved" = "none"): Readiness {
  const asOf = iso(nowMs, 0);
  const a: Readiness["items"][number] = {
    id: "5.23(a)", name: "Accountable person named", status: "not_met",
    detail: "GLDB_AI_OWNER is empty. The bank must name the person (name and role) accountable for this AI system.",
    missing: ["Set GLDB_AI_OWNER to the accountable person's name and role."],
  };
  const b: Readiness["items"][number] =
    thresholds === "approved"
      ? { id: "5.23(b)", name: "Thresholds approved", status: "met", detail: "The approval on record matches the current thresholds. The software cannot judge whether the thresholds are the right ones.", missing: [], approved_by: "M. Lim", approver_role: "Chief Risk Officer", approved_at: iso(nowMs, 60 * 24 * 3) }
      : thresholds === "changed"
        ? { id: "5.23(b)", name: "Thresholds approved", status: "not_met", detail: "Thresholds changed since approval: the approval on record was for different values.", missing: ["Review the new thresholds and approve them again: POST /monitoring/thresholds/approve."], approved_by: "M. Lim", approver_role: "Chief Risk Officer", approved_at: iso(nowMs, 60 * 24 * 40) }
        : { id: "5.23(b)", name: "Thresholds approved", status: "not_met", detail: "No approval is recorded. The thresholds are starting values, not calibrated on real cases.", missing: ["The bank agrees the thresholds, then an approver records it: POST /monitoring/thresholds/approve."] };
  const delivered = lastTest?.delivered_to ?? [];
  const c: Readiness["items"][number] = lastTest
    ? {
        id: "5.23(c)", name: "Alerts reach a watched place", status: "met", missing: [], sinks_configured: ["in-app"], delivered_by: delivered, last_test_at: lastTest.at, last_test_results: lastTest.results, page_only: true, watching_confirmed: false, test_max_age_days: 30,
        detail: "A test alert was delivered by in-app within 30 days. In-app means the test appeared on the Notifications page. That is not the same as someone watching the page: the bank decides who looks at it, how often and what they do next.",
      }
    : {
        id: "5.23(c)", name: "Alerts reach a watched place", status: "not_met", sinks_configured: ["in-app"], last_test_at: null, test_max_age_days: 30,
        detail: "No test alert has been sent yet.", missing: ["Send a test alert and confirm someone receives it: POST /monitoring/alerts/test."],
      };
  return {
    as_of: asOf, ready: false,
    statement: "These three items are decisions for the bank to make: who is accountable, which thresholds are right, and where alerts are watched. The software only records them and checks that they are recorded and still current; it cannot judge whether the person, the thresholds or the place are the right ones.",
    items: [a, b, c],
  };
}
