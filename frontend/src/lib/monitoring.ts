/**
 * Shapes and small helpers for the Notifications page (docs/api.md section 9b: /monitoring/...).
 * Alerts raise attention; a person looks and acts. Nothing here decides anything.
 */
import { formatRelative, formatUtc } from "./format";

export type NotificationKind = "alert" | "reminder" | "resolved" | "incident" | "test";
export type NotificationCategory = "alerts" | "incidents" | "system";
export type NotificationSeverity = "info" | "attention" | "high";

export interface AppNotification {
  id: number;
  ts: string;
  kind: NotificationKind;
  category: NotificationCategory;
  /** The alert key (a metric name or `monitor_stale`), `incident:<id>` or `test`. */
  key: string;
  severity: NotificationSeverity;
  title: string;
  text: string;
  data: { metric?: string; value?: number; threshold?: number; incident_id?: number; severity?: IncidentSeverity; reported_by?: string };
  read: boolean;
  read_at: string | null;
  read_by: string | null;
}

export interface NotificationsPage {
  as_of: string;
  notifications: AppNotification[];
  /** Unread over the whole feed, whatever the filter. */
  unread: number;
  /** Pass as `before` for the next (older) page; null on the last page. */
  next_before: number | null;
}

export interface NotificationSummary {
  unread: number;
  open_alerts: number;
  /** What the navigation badge shows: an open alert and its own unread notification count once. */
  badge: number;
}

export type NotificationQuery = { category?: NotificationCategory; before?: number; limit?: number; unread?: boolean; since?: string };
export type MarkReadRequest = { ids: number[]; reviewer?: string } | { all: true; reviewer?: string };
export type MarkReadResponse = { marked: number; unread: number };

export type AlertStatus = "open" | "acknowledged" | "resolved";
export interface AlertState {
  key: string;
  status: AlertStatus;
  first_seen: string;
  last_seen: string;
  last_notified: string | null;
  notify_count: number;
  notified: boolean;
  value: number | null;
  threshold: number | null;
  meaning: string;
  action: string;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  note: string | null;
  resolved_at: string | null;
}

export interface SinkResult {
  sink: string;
  ok: boolean;
  attempts: number;
  error: string | null;
  at: string;
}
export interface LastTest {
  at: string;
  by: string;
  results: SinkResult[];
  delivered_to: string[];
}
export type SchedulerMode = "off" | "in_process" | "external";
export interface AlertsResponse {
  alerts: AlertState[];
  deliveries: { ts: string; sink: string; kind: string; alert_key: string | null; ok: boolean; attempts: number; error: string | null }[];
  sinks: string[];
  last_test: LastTest | null;
  scheduler: SchedulerMode;
  scheduler_note: string;
  interval_minutes: number | null;
  last_evaluated_at: string | null;
  last_evaluated_by: string | null;
  monitor_stale: boolean;
}

export interface AcknowledgeAlertRequest {
  reason: string;
  reviewer?: string;
}

export interface TestAlertResponse {
  sent_at: string;
  sinks_configured: string[];
  results: SinkResult[];
  delivered: boolean;
  message: string | null;
}

export type ReadinessId = "5.23(a)" | "5.23(b)" | "5.23(c)";
export interface ReadinessItem {
  id: ReadinessId;
  name: string;
  status: "met" | "not_met";
  detail: string;
  missing: string[];
  value?: string;
  approved_by?: string | null;
  approver_role?: string | null;
  approved_at?: string;
  last_test_at?: string | null;
  last_test_results?: SinkResult[];
  delivered_by?: string[];
  sinks_configured?: string[];
  page_only?: boolean;
  watching_confirmed?: boolean;
  test_max_age_days?: number;
}
export interface Readiness {
  as_of: string;
  ready: boolean;
  statement: string;
  items: ReadinessItem[];
}

export type IncidentSeverity = "low" | "medium" | "high";
export interface Incident {
  id: number;
  reported_at: string;
  reported_by: string;
  severity: IncidentSeverity;
  summary: string;
  case_ref: string | null;
  /** `resolution` rows close an earlier report; the report they close becomes `resolved`. */
  status: "open" | "resolution" | "resolved";
  resolves: number | null;
  acknowledged_by?: string;
  acknowledged_at?: string;
  ack_note?: string | null;
  assigned_to?: string | null;
}
export type IncidentReport = { severity: IncidentSeverity; summary: string; case_id?: string; reviewer?: string };
export type IncidentAck = { note: string; assigned_to?: string; reviewer?: string };

// ------------------------------------------------------------------ plain words

const METRIC_LABEL: Record<string, string> = {
  partial_rate: "Cases only partly analysed",
  check_error_rate: "Checks that failed to run",
  review_team_failure_rate: "Review team could not run",
  daily_spend_ratio: "Model spend today",
  rule_drift: "A check fires much more or less often",
  monitor_stale: "The monitor went quiet",
};

/** The plain name of an alert key, falling back to the key itself. */
export function metricLabel(key: string): string {
  return METRIC_LABEL[key] ?? key;
}

/** Rates and ratios are fractions (0.42 is 42%); a stale monitor is minutes. */
export function formatMetricValue(key: string, v: number | null): string {
  if (v === null || !Number.isFinite(v)) return "-";
  if (key === "monitor_stale") return `${v} min`;
  if (key === "rule_drift") return `${Math.round(v * 100)} points`;
  return `${Math.round(v * 1000) / 10}%`;
}

export function valueVersusLimit(key: string, value: number | null, threshold: number | null): string {
  return `${formatMetricValue(key, value)} against a limit of ${formatMetricValue(key, threshold)}`;
}

/** "2026-10-27 10:02 UTC (4 min ago)". */
export function whenText(iso: string | null, nowMs?: number): string {
  if (!iso) return "never";
  const rel = formatRelative(iso, nowMs || undefined);
  return rel ? `${formatUtc(iso)} (${rel})` : formatUtc(iso);
}

export const CATEGORY_LABEL: Record<NotificationCategory | "all", string> = { all: "All", alerts: "Alerts", incidents: "Incidents", system: "System" };

export type SchedulerView = { tone: "off" | "running" | "stale"; badge: string; line: string };

/** The scheduler status line: off, running (with the last evaluation) or stale. Off is never shown as healthy. */
export function schedulerView(a: Pick<AlertsResponse, "scheduler" | "interval_minutes" | "last_evaluated_at" | "monitor_stale">, nowMs?: number): SchedulerView {
  if (a.scheduler === "off") {
    return { tone: "off", badge: "Off", line: "Nothing is checking the limits, so no alerts appear by themselves. Absence of alerts here does not mean all is well." };
  }
  const where = a.scheduler === "in_process" ? "inside this service" : "by the bank's scheduler";
  const every = a.interval_minutes ? `every ${a.interval_minutes} min` : "on a schedule";
  const last = a.last_evaluated_at ? `Last checked ${whenText(a.last_evaluated_at, nowMs)}.` : "Not checked yet.";
  if (a.monitor_stale) {
    return { tone: "stale", badge: "Stale", line: `Checks run ${every} ${where}, but none has run for more than two intervals. ${last} Alerts may have been missed.` };
  }
  return { tone: "running", badge: "Running", line: `Checks run ${every} ${where}. ${last}` };
}

/** Where a test alert went, for the readiness panel: "the Notifications page" for in-app. */
export function sinkLabel(name: string): string {
  return name === "in-app" ? "the Notifications page" : name;
}
