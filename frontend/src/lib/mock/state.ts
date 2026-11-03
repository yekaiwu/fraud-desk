import type { AlertState, AppNotification, Incident, LastTest } from "../monitoring";
import type { CaseDetail, Job, RuleChange, RuleOverride } from "../types";
import { buildCase, jobFor, SPECS, STAGES } from "./fixtures";
import { alertFixtures, incidentFixtures, lastTestFixture, notificationFixtures } from "./fixtures/monitoring";

/** Time for a created case to move through the 7 stages (frontend.md section 11: about 6 s). */
export const MOCK_ANALYSIS_MS = 6000;

export type MockJobMeta = {
  id: string;
  caseId: string;
  kind: Job["kind"];
  /** ms since epoch when the job "started"; null until first looked at (fixture jobs). */
  startedAt: number | null;
  /** fixed outcome for fixture jobs that are not driven by the clock. */
  fixed?: Job;
};

export type MockVersionReason = "created" | "rerun" | "documents_added";
export type MockSnapshot = { version: number; reason: MockVersionReason; detail: CaseDetail };

export type MockState = {
  /** older versions of a case, oldest first (read-only snapshots). */
  history: Map<string, MockSnapshot[]>;
  /** why each case's current version exists. */
  currentReason: Map<string, MockVersionReason>;
  cases: Map<string, CaseDetail>;
  jobs: Map<string, MockJobMeta>;
  /** case id -> when its running job completes and the case opens. */
  readyAt: Map<string, number>;
  nextCase: number;
  nextJob: number;
  nextEntry: Map<string, number>;
  loadedAt: number;
  /** check-catalogue settings an officer changed, by rule id (absent = defaults). */
  overrides: Map<string, RuleOverride>;
  /** the permanent change log, oldest first. */
  changes: RuleChange[];
  /** The monitor, as the Notifications page sees it (oldest notification first). */
  monitor: MockMonitor;
  /** artificial latency in ms; tests set it to 0. */
  latencyMs: number;
};

export type MockMonitor = {
  notifications: AppNotification[];
  alerts: AlertState[];
  incidents: Incident[];
  lastTest: LastTest | null;
  /** "on" shows the scheduler as running; tests flip it to see off and stale. */
  scheduler: "off" | "in_process" | "external";
  stale: boolean;
  lastEvaluatedAt: string | null;
  nextNotification: number;
  nextIncident: number;
  /** Set when a test alert was sent, for the 30 second limit. */
  lastTestMs: number | null;
};

function freshMonitor(): MockMonitor {
  const now = Date.now();
  const notifications = notificationFixtures(now);
  const incidents = incidentFixtures(now);
  return {
    notifications,
    alerts: alertFixtures(now),
    incidents,
    lastTest: lastTestFixture(now),
    scheduler: "in_process",
    stale: false,
    lastEvaluatedAt: new Date(now - 4 * 60_000).toISOString().replace(/\.(\d{3})Z$/, ".$1000Z"),
    nextNotification: Math.max(...notifications.map((n) => n.id)) + 1,
    nextIncident: Math.max(...incidents.map((i) => i.id)) + 1,
    lastTestMs: null,
  };
}

function fresh(): MockState {
  const cases = new Map<string, CaseDetail>();
  const jobs = new Map<string, MockJobMeta>();
  const history = new Map<string, MockSnapshot[]>();
  const currentReason = new Map<string, MockVersionReason>();
  for (const spec of SPECS) {
    cases.set(spec.id, buildCase(spec));
    currentReason.set(spec.id, spec.history ? "rerun" : "created");
    if (spec.history) {
      history.set(
        spec.id,
        spec.history.map((h, i) => ({ version: i + 1, reason: h.reason, detail: buildCase({ ...h.spec, version: i + 1 }) })),
      );
    }
    const jobId = `j_${spec.id}`;
    if (spec.status === "processing") jobs.set(jobId, { id: jobId, caseId: spec.id, kind: "analyse", startedAt: null });
    else if (spec.status === "error") jobs.set(jobId, { id: jobId, caseId: spec.id, kind: "analyse", startedAt: null, fixed: { ...jobFor(jobId, spec.id, "failed", 4, "interrupted") } });
    else jobs.set(jobId, { id: jobId, caseId: spec.id, kind: "analyse", startedAt: null, fixed: jobFor(jobId, spec.id, "succeeded", STAGES.length) });
  }
  return {
    history, currentReason, cases, jobs, readyAt: new Map(), nextCase: 1, nextJob: 1, nextEntry: new Map(),
    loadedAt: Date.now(),
    overrides: new Map(),
    changes: [],
    monitor: freshMonitor(),
    latencyMs: 120,
  };
}

export let state: MockState = fresh();

/** In-memory only; resets on page reload. Tests call this between cases. */
export function resetMockState(): void {
  state = fresh();
}
