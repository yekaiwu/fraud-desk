import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { deriveEntityGraph } from "./graph";
import * as mock from "./mock/handlers";
import { resetMockState, state } from "./mock/state";
import { numberFlags, pageCounts } from "./flags";
import { clampRegion } from "./regions";
import type { CaseDetail } from "./types";

// Real API responses written by the backend tests (api.md section 12, CR-51). Skipped when the folder is absent.
const dir = join(__dirname, "..", "..", "..", "backend", "tests", "_out", "api_fixtures");
const have = existsSync(dir);
const load = (name: string) => JSON.parse(readFileSync(join(dir, `${name}.json`), "utf8"));
const hasKeys = (o: object, keys: string[]) => keys.forEach((k) => expect(o, `missing key ${k}`).toHaveProperty(k));

describe.skipIf(!have)("API fixtures match lib/types.ts", () => {
  it("queue page", () => {
    const q = load("queue_page");
    hasKeys(q, ["items", "total", "page", "page_size", "has_next"]);
    for (const i of q.items) {
      hasKeys(i, ["case_id", "reference", "use_case", "status", "applicant_name", "version", "created_at", "updated_at", "age_seconds", "score", "band", "coverage", "lane", "top_finding", "findings_count", "high_count", "partial", "decision", "pending_second_approvals", "file_count"]);
      if (i.coverage) hasKeys(i.coverage, ["applicable", "ran", "ratio", "skipped", "pages_total", "pages_unchecked"]);
    }
  });

  it("case detail, and the UI helpers cope with it", () => {
    const c = load("case_detail") as CaseDetail;
    hasKeys(c, ["id", "use_case", "status", "version", "today", "applicant", "files", "documents", "flags", "findings", "check_runs", "partial", "partial_reasons", "stages", "costs", "review", "pending_second_approvals", "allowed_verdicts", "not_checked_documents", "cost_summary"]);
    for (const f of c.flags) {
      hasKeys(f, ["id", "rule", "stage", "severity", "document_ids", "extra_regions", "evidence", "explanation", "disposition"]);
      for (const r of [f.region, ...f.extra_regions]) if (r) expect(clampRegion(r), `flag ${f.id} region`).not.toBeNull();
    }
    for (const fnd of c.findings) hasKeys(fnd, ["id", "group_key", "title", "severity", "flag_ids", "document_ids", "rules", "weight", "summary", "hard_stop"]);
    expect(() => numberFlags(c.findings, c.flags)).not.toThrow();
    expect(() => deriveEntityGraph(c)).not.toThrow();
    expect(() => pageCounts(c)).not.toThrow();
  });

  it("review response, job, effectiveness, error", () => {
    const r = load("review_response");
    hasKeys(r.entry, ["id", "kind", "reviewer", "decision", "reason", "flag_ids", "status", "case_version", "created_at"]);
    hasKeys(r.case, ["id", "status", "decision", "escalated_at", "version", "pending_second_approvals"]);
    hasKeys(load("job"), ["id", "case_id", "kind", "state", "created_at", "started_at", "finished_at", "progress", "stages", "error"]);
    const e = load("effectiveness");
    hasKeys(e, ["generated_for", "cases_total", "cases_decided", "median_seconds_to_verdict", "rules"]);
    for (const rule of e.rules) hasKeys(rule, ["rule", "stage", "default_severity", "description", "cases_fired", "times_fired", "confirmed", "dismissed", "pending", "open", "dismissal_rate", "median_seconds_to_decide", "cases_ran", "cases_skipped"]);
    hasKeys(load("error_response").error, ["code", "message"]);
  });
});

// The Notifications page's endpoints (api.md section 9b): the real answers, and the mock data that stands in for them.
const keysOf = (o: object) => Object.keys(o).sort();
/** Every key the real API sends is a key the mock sends too (the mock may carry more only where the API omits an empty one). */
const mockCovers = (real: object, fake: object, where: string) => {
  const have = new Set(keysOf(fake));
  for (const k of keysOf(real)) expect(have.has(k), `${where}: mock lacks ${k}`).toBe(true);
};

describe.skipIf(!have)("monitoring API fixtures match lib/monitoring.ts and the mock data", () => {
  beforeEach(() => {
    resetMockState();
    state.latencyMs = 0;
  });

  it("notification feed, summary and read response", async () => {
    const feed = load("monitoring_notifications");
    hasKeys(feed, ["as_of", "notifications", "unread", "next_before"]);
    expect(feed.notifications.length).toBeGreaterThan(0);
    const mocked = await mock.getNotifications();
    mockCovers(feed, mocked, "feed");
    for (const n of feed.notifications) {
      hasKeys(n, ["id", "ts", "kind", "category", "key", "severity", "title", "text", "data", "read", "read_at", "read_by"]);
      mockCovers(n, mocked.notifications[0], "notification");
      expect(["alert", "reminder", "resolved", "incident", "test"]).toContain(n.kind);
      expect(["alerts", "incidents", "system"]).toContain(n.category);
      expect(["info", "attention", "high"]).toContain(n.severity);
    }
    const sum = load("monitoring_notification_summary");
    hasKeys(sum, ["unread", "open_alerts", "badge"]);
    mockCovers(sum, await mock.getNotificationSummary(), "summary");
    const read = load("monitoring_notification_read");
    hasKeys(read, ["marked", "unread"]);
    mockCovers(read, await mock.markNotificationsRead({ all: true, reviewer: "a.tan" }), "read");
  });

  it("alerts, test alert, readiness and incidents", async () => {
    const alerts = load("monitoring_alerts");
    hasKeys(alerts, ["alerts", "deliveries", "sinks", "last_test", "scheduler", "scheduler_note", "interval_minutes", "last_evaluated_at", "last_evaluated_by", "monitor_stale"]);
    const mockedAlerts = await mock.getAlerts();
    mockCovers(alerts, mockedAlerts, "alerts");
    for (const a of alerts.alerts) {
      hasKeys(a, ["key", "status", "first_seen", "last_seen", "last_notified", "notify_count", "notified", "value", "threshold", "meaning", "action", "acknowledged_by", "acknowledged_at", "note", "resolved_at"]);
      mockCovers(a, mockedAlerts.alerts[0], "alert");
    }
    hasKeys(alerts.last_test, ["at", "by", "results", "delivered_to"]);
    mockCovers(alerts.last_test.results[0], mockedAlerts.last_test!.results[0], "sink result");
    const test = load("monitoring_test_alert");
    hasKeys(test, ["sent_at", "sinks_configured", "results", "delivered", "message"]);
    mockCovers(test, await mock.sendTestAlert(), "test alert");
    const ready = load("monitoring_readiness");
    hasKeys(ready, ["as_of", "ready", "statement", "items"]);
    const mockedReady = await mock.getReadiness();
    mockCovers(ready, mockedReady, "readiness");
    expect(ready.items.map((i: { id: string }) => i.id)).toEqual(["5.23(a)", "5.23(b)", "5.23(c)"]);
    ready.items.forEach((i: object, n: number) => {
      hasKeys(i, ["id", "name", "status", "detail", "missing"]);
      hasKeys(mockedReady.items[n], ["id", "name", "status", "detail", "missing"]);
    });
    mockCovers(ready.items[2], mockedReady.items[2], "readiness (c)");
    const inc = load("monitoring_incidents");
    expect(inc.incidents.length).toBeGreaterThan(0);
    const mockedInc = await mock.getIncidents();
    for (const i of inc.incidents) {
      hasKeys(i, ["id", "reported_at", "reported_by", "severity", "summary", "status"]);
      mockCovers(i, mockedInc.incidents.find((m) => m.acknowledged_by) ?? mockedInc.incidents[0], "incident");
    }
  });
});

describe.skipIf(have)("API fixtures", () => {
  it.skip("are not present; run the backend API tests to write backend/tests/_out/api_fixtures", () => {});
});
