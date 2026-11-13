/**
 * Typed client for the GLDB HTTP API (docs/api.md). Hand-written from the contract.
 *
 * - Every failure throws ApiErrorException carrying ApiError["error"]; a failed fetch is code "network_error".
 * - With NEXT_PUBLIC_USE_MOCK=1 every call is served by lib/mock/handlers.ts (loaded with a dynamic import,
 *   so a normal production bundle never pulls the fixtures in). The flag is read once, at module load.
 */
import { API_BASE_URL } from "./constants";
import { ApiErrorException, networkError } from "./errors";
import type {
  AcknowledgeAlertRequest,
  AlertState,
  AlertsResponse,
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
} from "./monitoring";
import { queueQueryToSearchParams } from "./queueQuery";
import type {
  AdoptedRuleView,
  ApiError,
  CaseDetail,
  CreateCaseResponse,
  CustomCapabilities,
  CustomRuleChange,
  CustomRuleView,
  CustomTicket,
  CustomTicketRequest,
  CustomTicketResponse,
  CustomTurnRequest,
  DraftResponse,
  DryRunResult,
  AskRequest,
  AskResponse,
  EffectivenessResponse,
  ExplainMethod,
  Health,
  Job,
  Page,
  QueueItem,
  QueueQuery,
  ReviewEntry,
  ReviewRequest,
  ReviewResponse,
  RuleChange,
  RuleInfo,
  RuleOverrideRequest,
  SaveCustomRuleRequest,
  SharedRuleView,
  Spending,
  UseCase,
  Band,
  CustomSeverity,
  TicketRef,
} from "./types";

export type VersionInfo = {
  version: number;
  created_at: string;
  score: number | null;
  band: Band | null;
  reason: "created" | "rerun" | "documents_added";
  current: boolean;
};
export type VersionsResponse = { versions: VersionInfo[] };
export type NewVersionResponse = { case_id: string; job_id: string; version: number };

// Read once, in this module, so the bundler folds `if (USE_MOCK)` to false in a normal build and drops
// the dynamic imports (and with it every fixture). Each import() sits directly inside an `if (USE_MOCK)`
// guard for that reason; do not hoist them into a helper.
const USE_MOCK = process.env.NEXT_PUBLIC_USE_MOCK === "1";

function isErrorBody(x: unknown): x is ApiError {
  if (!x || typeof x !== "object") return false;
  const e = (x as { error?: unknown }).error;
  return !!e && typeof e === "object" && typeof (e as { code?: unknown }).code === "string";
}

function url(path: string, params?: URLSearchParams): string {
  const qs = params && [...params.keys()].length ? `?${params.toString()}` : "";
  return `${API_BASE_URL}${path}${qs}`;
}

async function request<T>(path: string, init: RequestInit = {}, params?: URLSearchParams): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url(path, params), { cache: "no-store", ...init });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw networkError(e);
  }
  const requestId = res.headers.get("X-Request-Id");
  // Session ended (the gate in front of the API answers 401): go back to the login page.
  if (res.status === 401 && !USE_MOCK && typeof window !== "undefined" && window.location.pathname !== "/login") {
    // Deliberate full navigation: drop all client state and let the proxy set the login redirect target.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
  }
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    if (isErrorBody(body)) throw new ApiErrorException(body.error, res.status, requestId);
    throw new ApiErrorException(
      res.status >= 500
        ? { code: "internal_error", message: `The server returned ${res.status}.` }
        : { code: "bad_request", message: `The server returned ${res.status}.` },
      res.status,
      requestId,
    );
  }
  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch (e) {
    throw new ApiErrorException(
      { code: "internal_error", message: "The server sent a response that is not valid JSON.", details: { cause: String(e) } },
      res.status,
      requestId,
    );
  }
}

function jsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export async function getHealth(): Promise<Health> {
  if (USE_MOCK) return (await import("./mock/handlers")).getHealth();
  return request<Health>("/health");
}

export interface Me {
  reviewer: string | null;
  admin: boolean;
  /** The custom rules feature is switched on and an account is signed in (the navigation entry follows it). */
  custom_rules: boolean;
  /** Ask this case is switched on and an account is signed in. */
  case_assistant?: boolean;
}

export async function getMe(): Promise<Me> {
  return request<Me>("/me");
}

export async function getSpending(): Promise<Spending> {
  return request<Spending>("/spending");
}

export async function getQueue(q: QueueQuery = {}): Promise<Page<QueueItem>> {
  if (USE_MOCK) return (await import("./mock/handlers")).getQueue(q);
  return request<Page<QueueItem>>("/cases", {}, queueQueryToSearchParams(q));
}

/** Multipart upload; do not set Content-Type (the browser adds the boundary). */
export async function createCase(form: FormData): Promise<CreateCaseResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).createCase(form);
  return request<CreateCaseResponse>("/cases", { method: "POST", body: form });
}

export async function getCase(id: string, version?: number): Promise<CaseDetail> {
  if (USE_MOCK) return (await import("./mock/handlers")).getCase(id, version);
  const p = new URLSearchParams();
  if (version !== undefined) p.set("version", String(version));
  return request<CaseDetail>(`/cases/${encodeURIComponent(id)}`, {}, p);
}

export async function getVersions(id: string): Promise<VersionsResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).getVersions(id);
  return request<VersionsResponse>(`/cases/${encodeURIComponent(id)}/versions`);
}

/** Synchronous URL for an <img src>. Mock mode returns a bundled SVG page. */
export function pageUrl(caseId: string, fileId: string, page: number, w?: number): string {
  if (USE_MOCK) return mockPageUrl(fileId);
  const p = new URLSearchParams({ file_id: fileId });
  if (w !== undefined) p.set("w", String(w));
  return url(`/cases/${encodeURIComponent(caseId)}/pages/${page}`, p);
}

// Kept inline (no mock import) so pageUrl stays synchronous and the mock module stays out of the bundle.
function mockPageUrl(fileId: string): string {
  const kinds: Record<string, string> = { f1: "statement", f2: "invoice", f3: "ownership" };
  return `/mock/pages/${kinds[fileId] ?? "statement"}.svg`;
}

export async function getJob(id: string): Promise<Job> {
  if (USE_MOCK) return (await import("./mock/handlers")).getJob(id);
  return request<Job>(`/jobs/${encodeURIComponent(id)}`);
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const t = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(t);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export const POLL_FAST_MS = 1000;
export const POLL_SLOW_MS = 3000;
export const POLL_BACKOFF_AFTER_MS = 30_000;

export const POLL_MAX_FAILURES = 5;
export const POLL_RETRY_MS = [1000, 2000, 4000, 8000];

function isPermanent(e: unknown): boolean {
  return e instanceof ApiErrorException && (e.status === 401 || e.status === 403 || e.status === 404);
}

/**
 * Poll a job every 1 s, then every 3 s once 30 s have passed (api.md section 7), until it
 * succeeds or fails. Calls onUpdate with every snapshot. A failed poll (a 502, a dropped
 * connection) is retried after 1, 2, 4, then 8 s; it only rejects, with the last error, after
 * 5 failures in a row (a success resets the count) or at once for 401, 403 and 404. Rejects with
 * AbortError when aborted.
 */
export async function pollJob(id: string, onUpdate: (j: Job) => void, signal: AbortSignal): Promise<Job> {
  const started = Date.now();
  let failures = 0;
  for (;;) {
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    let job: Job;
    try {
      job = await getJob(id);
    } catch (e) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      if (isPermanent(e)) throw e;
      failures += 1;
      if (failures >= POLL_MAX_FAILURES) throw e;
      await sleep(POLL_RETRY_MS[Math.min(failures, POLL_RETRY_MS.length) - 1], signal);
      continue;
    }
    failures = 0;
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    onUpdate(job);
    if (job.state === "succeeded" || job.state === "failed") return job;
    await sleep(Date.now() - started >= POLL_BACKOFF_AFTER_MS ? POLL_SLOW_MS : POLL_FAST_MS, signal);
  }
}

/** Ask a question about the documents already on the case. Read-only: it never changes a flag or the score. */
export async function askCase(id: string, req: AskRequest): Promise<AskResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).askCase(id, req);
  return request<AskResponse>(`/cases/${encodeURIComponent(id)}/ask`, jsonInit("POST", req));
}

export async function postReview(id: string, req: ReviewRequest): Promise<ReviewResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).postReview(id, req);
  return request<ReviewResponse>(`/cases/${encodeURIComponent(id)}/review`, jsonInit("POST", req));
}

export async function getReviewLog(id: string): Promise<{ entries: ReviewEntry[] }> {
  if (USE_MOCK) return (await import("./mock/handlers")).getReviewLog(id);
  return request<{ entries: ReviewEntry[] }>(`/cases/${encodeURIComponent(id)}/review`);
}

export async function rerunCase(id: string): Promise<NewVersionResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).rerunCase(id);
  return request<NewVersionResponse>(`/cases/${encodeURIComponent(id)}/rerun`, { method: "POST" });
}

export async function addFiles(id: string, files: File[]): Promise<NewVersionResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).addFiles(id, files);
  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  return request<NewVersionResponse>(`/cases/${encodeURIComponent(id)}/files`, { method: "POST", body: form });
}

export function packUrl(id: string, format: "json" | "html", version?: number): string {
  if (USE_MOCK) return "/mock/sample-pack.html";
  const p = new URLSearchParams({ format });
  if (version !== undefined) p.set("version", String(version));
  return url(`/cases/${encodeURIComponent(id)}/pack`, p);
}

export async function deleteCase(id: string, reviewer: string, reason: string): Promise<void> {
  if (USE_MOCK) return (await import("./mock/handlers")).deleteCase(id, reviewer, reason);
  await request<void>(`/cases/${encodeURIComponent(id)}`, { method: "DELETE" }, new URLSearchParams({ reviewer, reason }));
}

export async function getEffectiveness(q: { use_case?: UseCase; since?: string } = {}): Promise<EffectivenessResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).getEffectiveness(q);
  const p = new URLSearchParams();
  if (q.use_case) p.set("use_case", q.use_case);
  if (q.since) p.set("since", q.since);
  return request<EffectivenessResponse>("/rules/effectiveness", {}, p);
}

export async function getRules(): Promise<{ rules: RuleInfo[] }> {
  if (USE_MOCK) return (await import("./mock/handlers")).getRules();
  return request<{ rules: RuleInfo[] }>("/rules");
}

/** The scoring and lane method, for the "?" explainers. 404 on a backend that predates it: callers show the case numbers without it. */
export async function getExplainMethod(): Promise<ExplainMethod> {
  if (USE_MOCK) return (await import("./mock/handlers")).getExplainMethod();
  return request<ExplainMethod>("/explain/method");
}

const ruleOverridePath = (rule: string) => `/rules/${encodeURIComponent(rule)}/override`;

/** Change one check's switch, severity or wording. Needs the reviewer's name and a reason; returns the updated check. */
export async function updateRuleOverride(rule: string, req: RuleOverrideRequest): Promise<RuleInfo> {
  if (USE_MOCK) return (await import("./mock/handlers")).updateRuleOverride(rule, req);
  return request<RuleInfo>(ruleOverridePath(rule), jsonInit("PUT", req));
}

/** Put a check back to its defaults. The change stays in the log. */
export async function resetRuleOverride(rule: string, req: { reviewer: string; reason: string }): Promise<RuleInfo> {
  if (USE_MOCK) return (await import("./mock/handlers")).resetRuleOverride(rule, req);
  return request<RuleInfo>(ruleOverridePath(rule), jsonInit("DELETE", req));
}

/** The change log, newest first. Accepts `{changes}` (what the backend sends), `{items}` or a bare array. */
export async function getRuleChanges(q: { rule?: string; limit?: number } = {}): Promise<RuleChange[]> {
  if (USE_MOCK) return (await import("./mock/handlers")).getRuleChanges(q);
  const p = new URLSearchParams();
  if (q.rule) p.set("rule", q.rule);
  if (q.limit) p.set("limit", String(q.limit));
  const body = await request<{ changes?: RuleChange[]; items?: RuleChange[] } | RuleChange[]>("/rules/changes", {}, p);
  return Array.isArray(body) ? body : (body?.changes ?? body?.items ?? []);
}

// ------------------------------------------------------------------ monitoring and notifications (api.md section 9b)

/** The in-app feed, newest first. `before` pages back; the unread count covers the whole feed. */
export async function getNotifications(q: NotificationQuery = {}): Promise<NotificationsPage> {
  if (USE_MOCK) return (await import("./mock/handlers")).getNotifications(q);
  const p = new URLSearchParams();
  if (q.category) p.set("category", q.category);
  if (q.before) p.set("before", String(q.before));
  if (q.limit) p.set("limit", String(q.limit));
  if (q.unread) p.set("unread", "true");
  if (q.since) p.set("since", q.since);
  return request<NotificationsPage>("/monitoring/notifications", {}, p);
}

/** Cheap counts for the navigation badge. */
export async function getNotificationSummary(): Promise<NotificationSummary> {
  if (USE_MOCK) return (await import("./mock/handlers")).getNotificationSummary();
  return request<NotificationSummary>("/monitoring/notifications/summary");
}

/** Mark some (or all) notifications read under the signed-in account, or the typed name. Audited. */
export async function markNotificationsRead(req: MarkReadRequest): Promise<MarkReadResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).markNotificationsRead(req);
  return request<MarkReadResponse>("/monitoring/notifications/read", jsonInit("POST", req));
}

export async function getAlerts(): Promise<AlertsResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).getAlerts();
  return request<AlertsResponse>("/monitoring/alerts");
}

/** Acknowledge an open alert. A reason is required; a signed-in account replaces the typed name. */
export async function acknowledgeAlert(key: string, req: AcknowledgeAlertRequest): Promise<{ alert: AlertState }> {
  if (USE_MOCK) return (await import("./mock/handlers")).acknowledgeAlert(key, req);
  return request<{ alert: AlertState }>(`/monitoring/alerts/${encodeURIComponent(key)}/acknowledge`, jsonInit("POST", req));
}

/** A labelled test alert through every sink; at most one every 30 seconds. */
export async function sendTestAlert(): Promise<TestAlertResponse> {
  if (USE_MOCK) return (await import("./mock/handlers")).sendTestAlert();
  return request<TestAlertResponse>("/monitoring/alerts/test", { method: "POST" });
}

export async function getReadiness(): Promise<Readiness> {
  if (USE_MOCK) return (await import("./mock/handlers")).getReadiness();
  return request<Readiness>("/monitoring/readiness");
}

export async function getIncidents(): Promise<{ incidents: Incident[] }> {
  if (USE_MOCK) return (await import("./mock/handlers")).getIncidents();
  return request<{ incidents: Incident[] }>("/monitoring/incidents");
}

export async function reportIncident(req: IncidentReport): Promise<{ id: number; reported_by: string; severity: string }> {
  if (USE_MOCK) return (await import("./mock/handlers")).reportIncident(req);
  return request<{ id: number; reported_by: string; severity: string }>("/monitoring/incidents", jsonInit("POST", req));
}

export async function acknowledgeIncident(id: number, req: IncidentAck): Promise<{ id: number; incident: number; acknowledged_by: string }> {
  if (USE_MOCK) return (await import("./mock/handlers")).acknowledgeIncident(id, req);
  return request<{ id: number; incident: number; acknowledged_by: string }>(`/monitoring/incidents/${id}/acknowledge`, jsonInit("POST", req));
}

// ------------------------------------------------------------------ custom rules (api.md section 9c)
// Mock mode has no custom rules (useMe answers null there, so nothing links to them): every call says the feature is off.

function featureOff(): never {
  throw new ApiErrorException({ code: "feature_off", message: "Custom rules are switched off on this deployment (GLDB_CUSTOM_RULES)." }, 404);
}

const customPath = (id: string, tail = "") => `/custom-rules/${encodeURIComponent(id)}${tail}`;

/** What a rule can be built from, and what is impossible. The same catalogue the assistant is given. */
export async function getCustomCapabilities(): Promise<CustomCapabilities> {
  if (USE_MOCK) return featureOff();
  return request<CustomCapabilities>("/custom-rules/capabilities");
}

/** Start an authoring conversation. One model call, charged to the signed-in account's daily spend. */
export async function startCustomDraft(text: string, severity?: CustomSeverity, editOf?: string): Promise<DraftResponse> {
  if (USE_MOCK) return featureOff();
  const body: { text: string; severity?: CustomSeverity; edit_of?: string } = { text };
  if (severity) body.severity = severity;
  if (editOf) body.edit_of = editOf;
  return request<DraftResponse>("/custom-rules/drafts", jsonInit("POST", body));
}

export async function getCustomDraft(id: string): Promise<DraftResponse> {
  if (USE_MOCK) return featureOff();
  return request<DraftResponse>(`/custom-rules/drafts/${encodeURIComponent(id)}`);
}

/** Continue a draft with more words (a model call), an answer to a question or a chosen candidate (no model call). */
export async function sendCustomTurn(id: string, req: CustomTurnRequest): Promise<DraftResponse> {
  if (USE_MOCK) return featureOff();
  return request<DraftResponse>(`/custom-rules/drafts/${encodeURIComponent(id)}/turns`, jsonInit("POST", req));
}

/** Run the draft's rule over stored cases. No model call. */
export async function dryRunCustomDraft(id: string): Promise<DryRunResult> {
  if (USE_MOCK) return featureOff();
  return request<DryRunResult>(`/custom-rules/drafts/${encodeURIComponent(id)}/dry-run`, { method: "POST" });
}

export async function saveCustomRule(req: SaveCustomRuleRequest): Promise<{ rule: CustomRuleView; ticket: TicketRef | null }> {
  if (USE_MOCK) return featureOff();
  return request<{ rule: CustomRuleView; ticket: TicketRef | null }>("/custom-rules", jsonInit("POST", req));
}

export async function listCustomRules(): Promise<{ mine: CustomRuleView[]; adopted: AdoptedRuleView[] }> {
  if (USE_MOCK) return featureOff();
  return request<{ mine: CustomRuleView[]; adopted: AdoptedRuleView[] }>("/custom-rules");
}

/** Every account's public rules (author shown). Nothing here changes anyone's results until they adopt. */
export async function listSharedCustomRules(): Promise<{ rules: SharedRuleView[] }> {
  if (USE_MOCK) return featureOff();
  return request<{ rules: SharedRuleView[] }>("/custom-rules/shared");
}

export async function getCustomRule(id: string): Promise<CustomRuleView> {
  if (USE_MOCK) return featureOff();
  return request<CustomRuleView>(customPath(id));
}

export async function setCustomRuleEnabled(id: string, enabled: boolean, reason: string): Promise<{ rule: CustomRuleView }> {
  if (USE_MOCK) return featureOff();
  return request<{ rule: CustomRuleView }>(customPath(id, "/enabled"), jsonInit("POST", { enabled, reason }));
}

export async function publishCustomRule(id: string, reason: string): Promise<{ rule: CustomRuleView }> {
  if (USE_MOCK) return featureOff();
  return request<{ rule: CustomRuleView }>(customPath(id, "/publish"), jsonInit("POST", { reason }));
}

export async function unpublishCustomRule(id: string, reason: string): Promise<{ rule: CustomRuleView }> {
  if (USE_MOCK) return featureOff();
  return request<{ rule: CustomRuleView }>(customPath(id, "/unpublish"), jsonInit("POST", { reason }));
}

export async function adoptCustomRule(id: string): Promise<{ adoption: AdoptedRuleView }> {
  if (USE_MOCK) return featureOff();
  return request<{ adoption: AdoptedRuleView }>(customPath(id, "/adopt"), jsonInit("POST", {}));
}

export async function unadoptCustomRule(id: string): Promise<{ rule_id: string; adopted: false }> {
  if (USE_MOCK) return featureOff();
  return request<{ rule_id: string; adopted: false }>(customPath(id, "/unadopt"), jsonInit("POST", {}));
}

/** "Delete": the rule is retired. Past runs stay explainable; the rule can no longer run. */
export async function retireCustomRule(id: string, reason: string): Promise<{ rule: CustomRuleView }> {
  if (USE_MOCK) return featureOff();
  return request<{ rule: CustomRuleView }>(customPath(id, "/retire"), jsonInit("POST", { reason }));
}

export async function getCustomRuleChanges(q: { rule_id?: string; limit?: number } = {}): Promise<CustomRuleChange[]> {
  if (USE_MOCK) return featureOff();
  const p = new URLSearchParams();
  if (q.rule_id) p.set("rule_id", q.rule_id);
  if (q.limit) p.set("limit", String(q.limit));
  const body = await request<{ changes?: CustomRuleChange[] }>("/custom-rules/changes", {}, p);
  return body?.changes ?? [];
}

export async function listCustomTickets(): Promise<{ tickets: CustomTicket[] }> {
  if (USE_MOCK) return featureOff();
  return request<{ tickets: CustomTicket[] }>("/custom-rules/tickets");
}

/** Send an idea (or the missing part of a draft) to the tech team. Needs no model, so it works when the budget is used up. */
export async function submitCustomTicket(body: CustomTicketRequest): Promise<CustomTicketResponse> {
  if (USE_MOCK) return featureOff();
  return request<CustomTicketResponse>("/custom-rules/tickets", jsonInit("POST", body));
}
