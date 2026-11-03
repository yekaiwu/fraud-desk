/**
 * Mock handlers for the Notifications page (docs/api.md section 9b). In-memory, resets on reload.
 * Mirrors the shapes and error codes of the real endpoints: reviewer and reason checks, `not_found`,
 * `alert_not_open`, the 30 second test limit, paging by `before`, and the read marks. It sends nothing anywhere.
 */
import { ApiErrorException } from "../errors";
import type {
  AcknowledgeAlertRequest,
  AlertState,
  AlertsResponse,
  AppNotification,
  Incident,
  IncidentAck,
  IncidentReport,
  MarkReadRequest,
  MarkReadResponse,
  NotificationQuery,
  NotificationSummary,
  NotificationsPage,
  Readiness,
  TestAlertResponse,
} from "../monitoring";
import { readinessFixture, MOCK_INTERVAL_MINUTES } from "./fixtures/monitoring";
import { state } from "./state";

const err = (status: number, code: string, message: string, details?: Record<string, unknown>) => new ApiErrorException({ code, message, details }, status);
const stamp = (ms = Date.now()) => new Date(ms).toISOString().replace(/\.(\d{3})Z$/, ".$1000Z");

async function latency(): Promise<void> {
  if (state.latencyMs > 0) await new Promise((r) => setTimeout(r, state.latencyMs));
}

function reviewerOf(v: unknown): string {
  const r = typeof v === "string" ? v.trim() : "";
  if (r.length < 2 || r.length > 64) throw err(422, "reviewer_required", "A reviewer name of 2 to 64 characters is required.", { field: "reviewer" });
  return r;
}
function reasonOf(v: unknown, min = 5): string {
  const r = typeof v === "string" ? v.trim() : "";
  if (r.length < min) throw err(422, "reason_required", `A reason of at least ${min} characters is required.`, { field: "reason" });
  if (r.length > 1000) throw err(422, "validation_error", "The reason is too long.", { errors: [{ field: "reason", message: "at most 1000 characters" }] });
  return r;
}

function summaryNow(): NotificationSummary {
  const m = state.monitor;
  const unread = m.notifications.filter((n) => !n.read);
  const open = m.alerts.filter((a) => a.status === "open");
  const shadow = unread.filter((n) => (n.kind === "alert" || n.kind === "reminder") && open.some((a) => a.key === n.key)).length;
  return { unread: unread.length, open_alerts: open.length, badge: open.length + unread.length - shadow };
}

function addNotification(n: Omit<AppNotification, "id" | "ts" | "read" | "read_at" | "read_by">): void {
  const m = state.monitor;
  m.notifications.push({ ...n, id: m.nextNotification++, ts: stamp(), read: false, read_at: null, read_by: null });
}

export async function getNotifications(q: NotificationQuery = {}): Promise<NotificationsPage> {
  await latency();
  const limit = Math.min(200, Math.max(1, q.limit ?? 50));
  let rows = [...state.monitor.notifications].sort((a, b) => b.id - a.id);
  if (q.category) rows = rows.filter((n) => n.category === q.category);
  if (q.unread) rows = rows.filter((n) => !n.read);
  if (q.before) rows = rows.filter((n) => n.id < q.before!);
  if (q.since) rows = rows.filter((n) => n.ts > q.since!);
  const page = rows.slice(0, limit);
  return {
    as_of: stamp(),
    notifications: structuredClone(page),
    unread: summaryNow().unread,
    next_before: rows.length > limit ? page[page.length - 1].id : null,
  };
}

export async function getNotificationSummary(): Promise<NotificationSummary> {
  await latency();
  return summaryNow();
}

export async function markNotificationsRead(req: MarkReadRequest): Promise<MarkReadResponse> {
  await latency();
  const who = reviewerOf(req.reviewer);
  const ids = "ids" in req ? new Set(req.ids) : null;
  if (ids && (ids.size < 1 || ids.size > 200)) throw err(422, "validation_error", "Send 1 to 200 ids, or all.");
  let marked = 0;
  for (const n of state.monitor.notifications) {
    if (n.read || (ids && !ids.has(n.id))) continue;
    n.read = true;
    n.read_at = stamp();
    n.read_by = who;
    marked += 1;
  }
  return { marked, unread: summaryNow().unread };
}

export async function getAlerts(): Promise<AlertsResponse> {
  await latency();
  const m = state.monitor;
  const off = m.scheduler === "off";
  return {
    alerts: structuredClone(m.alerts),
    deliveries: [],
    sinks: ["in-app"],
    last_test: structuredClone(m.lastTest),
    scheduler: m.scheduler,
    scheduler_note: off ? "Off: nothing evaluates the thresholds or sends alerts by itself." : m.stale ? "Stale: no evaluation for more than 2 intervals." : "On: evaluations are running on schedule.",
    interval_minutes: off ? null : MOCK_INTERVAL_MINUTES,
    last_evaluated_at: m.lastEvaluatedAt,
    last_evaluated_by: m.lastEvaluatedAt ? "scheduler" : null,
    monitor_stale: !off && m.stale,
  };
}

export async function acknowledgeAlert(key: string, req: AcknowledgeAlertRequest): Promise<{ alert: AlertState }> {
  await latency();
  const who = reviewerOf(req.reviewer);
  const reason = reasonOf(req.reason);
  const a = state.monitor.alerts.find((x) => x.key === key);
  if (!a) throw err(404, "not_found", "No alert with this key.", { kind: "alert", key });
  if (a.status !== "open") throw err(409, "alert_not_open", `This alert is ${a.status}; only an open alert can be acknowledged.`, { status: a.status });
  a.status = "acknowledged";
  a.acknowledged_by = who;
  a.acknowledged_at = stamp();
  a.note = reason;
  for (const n of state.monitor.notifications) {
    if ((n.kind === "alert" || n.kind === "reminder") && n.key === key && !n.read) Object.assign(n, { read: true, read_at: stamp(), read_by: who });
  }
  return { alert: structuredClone(a) };
}

export async function sendTestAlert(): Promise<TestAlertResponse> {
  await latency();
  const m = state.monitor;
  if (m.lastTestMs !== null && Date.now() - m.lastTestMs < 30_000) {
    throw err(429, "rate_limited", "A test alert was sent a moment ago; wait a moment.", { retry_after_s: 30 });
  }
  const at = stamp();
  m.lastTestMs = Date.now();
  const results = [{ sink: "in-app", ok: true, attempts: 1, error: null, at }];
  m.lastTest = { at, by: "operator", results, delivered_to: ["in-app"] };
  addNotification({ kind: "test", category: "system", key: "test", severity: "info", title: "Test alert (not a real alert)", text: "Sent to check that alerts reach this page. No action is needed.", data: {} });
  return { sent_at: at, sinks_configured: ["in-app"], results, delivered: true, message: null };
}

export async function getReadiness(): Promise<Readiness> {
  await latency();
  return readinessFixture(Date.now(), state.monitor.lastTest);
}

export async function getIncidents(): Promise<{ incidents: Incident[] }> {
  await latency();
  return { incidents: structuredClone([...state.monitor.incidents].sort((a, b) => b.id - a.id)) };
}

export async function reportIncident(req: IncidentReport): Promise<{ id: number; reported_by: string; severity: string }> {
  await latency();
  const who = reviewerOf(req.reviewer);
  const summary = typeof req.summary === "string" ? req.summary.trim() : "";
  const errors: { field: string; message: string }[] = [];
  if (!["low", "medium", "high"].includes(req.severity)) errors.push({ field: "severity", message: "must be low, medium or high" });
  if (summary.length < 10 || summary.length > 500) errors.push({ field: "summary", message: "10 to 500 characters" });
  if (errors.length) throw err(422, "validation_error", "Some fields are not valid.", { errors });
  const m = state.monitor;
  const id = m.nextIncident++;
  m.incidents.push({ id, reported_at: stamp(), reported_by: who, severity: req.severity, summary, case_ref: req.case_id?.trim() || null, status: "open", resolves: null });
  if (req.severity !== "low") {
    addNotification({
      kind: "incident", category: "incidents", key: `incident:${id}`, severity: req.severity === "high" ? "high" : "attention",
      title: `AI incident #${id} reported (${req.severity})`, data: { incident_id: id, severity: req.severity, reported_by: who },
      text: `An AI incident of severity ${req.severity} was reported by ${who}. Its summary is in the incident list on this page.`,
    });
  }
  return { id, reported_by: who, severity: req.severity };
}

export async function acknowledgeIncident(id: number, req: IncidentAck): Promise<{ id: number; incident: number; acknowledged_by: string }> {
  await latency();
  const who = reviewerOf(req.reviewer);
  const note = reasonOf(req.note);
  const inc = state.monitor.incidents.find((i) => i.id === id && i.status === "open");
  if (!inc) throw err(404, "not_found", "No open incident with this id.", { kind: "incident", id });
  inc.acknowledged_by = who;
  inc.acknowledged_at = stamp();
  inc.ack_note = note;
  inc.assigned_to = req.assigned_to?.trim() || null;
  for (const n of state.monitor.notifications) {
    if (n.kind === "incident" && n.key === `incident:${id}` && !n.read) Object.assign(n, { read: true, read_at: stamp(), read_by: who });
  }
  return { id: state.monitor.nextNotification, incident: id, acknowledged_by: who };
}
