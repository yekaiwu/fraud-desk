/**
 * The whole journey of a case as an ordered list of steps, built only from what the case already stores: the
 * stage records, the check runs, the flags (each knows the stage that raised it), the model calls, the review
 * team's trail and the reviewers' decisions. Nothing here is guessed or recomputed: a step says what its stage
 * recorded, so every number on the timeline can be traced to the case.
 */
import { STAGE_LABEL, STAGE_ORDER } from "@/components/case/ProcessingStepper";
import { formatSeconds } from "./format";
import { plural } from "./labels";
import type { AgentReport, CaseDetail, CheckRun, Cost, Flag, ReviewEntry, Severity, StageName, StageRecord } from "./types";

export type StepKind = "received" | StageName | "team" | "review";
export type StepStatus = "ok" | "failed" | "skipped" | "info";

export interface SeverityTally {
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface TimelineStep {
  key: string;
  kind: StepKind;
  title: string;
  /** When the step began (UTC), when the case recorded it. */
  at: string | null;
  /** Seconds after the case was received, when both times are known. */
  offsetSeconds: number | null;
  seconds: number | null;
  costUsd: number | null;
  status: StepStatus;
  /** One sentence: what came out of this step. */
  headline: string;
  /** The stage's own numbers, labelled. */
  facts: [string, string][];
  warnings: string[];
  error: string | null;
  checks: CheckRun[];
  /** Flags this step raised. */
  flags: Flag[];
  /** Model calls this step made. */
  calls: Cost[];
  /** Flags raised up to and including this step, by severity (as the case stands now). */
  cumulative: SeverityTally;
  /** The review team's trail, on the team step. */
  agents: AgentReport[];
  /** Reviewers' decisions, on the review step. */
  entries: ReviewEntry[];
}

/** The review team's two model calls are recorded under the cross-document stage; they belong to the team step. */
const isTeamCall = (x: Cost): boolean => /^agent:/i.test(x.purpose);

const emptyTally = (): SeverityTally => ({ high: 0, medium: 0, low: 0, info: 0 });

export function tallyOf(flags: Pick<Flag, "severity">[]): SeverityTally {
  const t = emptyTally();
  for (const f of flags) t[f.severity as Severity] += 1;
  return t;
}

export function tallyText(t: SeverityTally): string {
  const parts = ([["high", t.high], ["medium", t.medium], ["low", t.low], ["info", t.info]] as const).filter(([, n]) => n > 0).map(([s, n]) => `${n} ${s}`);
  return parts.length ? parts.join(", ") : "none";
}

/** "text_layer_docs" -> "text layer docs". */
export function humaniseKey(key: string): string {
  return key.replace(/_/g, " ").trim();
}

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v)) ? Number(v) : 0);
const has = (d: StageRecord["detail"], k: string): boolean => d[k] !== undefined && d[k] !== null;

function stageHeadline(stage: StageName, d: StageRecord["detail"], c: CaseDetail): string {
  switch (stage) {
    case "ingest":
      return `${num(d.files)} ${plural(num(d.files), "file")}, ${num(d.pages)} ${plural(num(d.pages), "page")}: ${num(d.text_pages)} with a text layer, ${num(d.scan_pages)} scanned; ${num(d.unreadable_pages)} could not be read.`;
    case "classify":
      return `${num(d.documents)} ${plural(num(d.documents), "document")} recognised from ${num(d.pages)} ${plural(num(d.pages), "page")}; ${num(d.low_confidence_pages)} low-confidence, ${num(d.other_pages)} not recognised${num(d.injection_suspected) > 0 ? `, ${num(d.injection_suspected)} with instruction-like text` : ""}.`;
    case "extract": {
      const model = num(d.llm_cheap_pages) + num(d.llm_strong_pages);
      return `${num(d.documents)} ${plural(num(d.documents), "document")} read: ${num(d.text_layer_docs)} from the text layer${model > 0 ? `, ${model} ${plural(model, "page")} with a model` : ""}; ${num(d.rows_annotated)} ${plural(num(d.rows_annotated), "row")} labelled.`;
    }
    case "forensics":
    case "rules":
    case "crossdoc":
      return `${num(d.ran)} of ${num(d.checks)} checks ran, ${num(d.skipped)} skipped${num(d.errors) > 0 ? `, ${num(d.errors)} failed` : ""}; ${num(d.flags)} ${plural(num(d.flags), "flag")} raised.${num(d.custom_checks) > 0 ? ` Custom checks: ${num(d.custom_checks)} ${plural(num(d.custom_checks), "check")} looked at the case, ${num(d.custom_flags)} ${plural(num(d.custom_flags), "flag")}.` : ""}`;
    case "score":
      return `Score ${c.score?.value ?? num(d.score)}${c.score ? ` (${c.score.band})` : ""}${c.lane ? `, lane ${c.lane}` : ""}, from ${num(d.flags)} ${plural(num(d.flags), "flag")} in ${num(d.findings)} ${plural(num(d.findings), "finding")}; ${num(d.ran)} of ${num(d.applicable)} checks ran.`;
  }
}

function statusOf(rec: StageRecord): StepStatus {
  return rec.status === "failed" ? "failed" : rec.status === "skipped" ? "skipped" : "ok";
}

export function buildTimeline(c: CaseDetail): TimelineStep[] {
  const created = Date.parse(c.created_at);
  const offset = (iso: string | null | undefined): number | null => {
    const t = iso ? Date.parse(iso) : NaN;
    return Number.isFinite(t) && Number.isFinite(created) ? Math.max(0, (t - created) / 1000) : null;
  };
  const steps: TimelineStep[] = [];
  const raised: Flag[] = [];
  const push = (s: Omit<TimelineStep, "cumulative">) => steps.push({ ...s, cumulative: tallyOf(raised) });
  const base = { warnings: [] as string[], error: null as string | null, checks: [] as CheckRun[], flags: [] as Flag[], calls: [] as Cost[], agents: [] as AgentReport[], entries: [] as ReviewEntry[], facts: [] as [string, string][] };

  const pages = c.files.reduce((n, f) => n + f.pages.length, 0);
  push({
    ...base,
    key: "received",
    kind: "received",
    title: "Case received",
    at: c.created_at,
    offsetSeconds: 0,
    seconds: null,
    costUsd: null,
    status: "info",
    headline: `${c.files.length} ${plural(c.files.length, "file")} (${pages} ${plural(pages, "page")}) for a ${c.use_case.toUpperCase()} case${c.applicant.name ? ` about ${c.applicant.name}` : ""}.`,
    facts: [
      ["use case", c.use_case],
      ["applicant", c.applicant.name ?? "not given"],
      ["UEN", c.applicant.uen ?? "not given"],
      ["applicant from", c.applicant.source === "upload" ? "typed on upload" : "read from the documents"],
      ["version", String(c.version)],
    ],
  });

  for (const stage of STAGE_ORDER) {
    const rec = c.stages.find((s) => s.stage === stage);
    if (!rec) continue;
    const flags = c.flags.filter((f) => f.stage === stage);
    raised.push(...flags);
    push({
      ...base,
      key: stage,
      kind: stage,
      title: STAGE_LABEL[stage],
      at: rec.started_at ?? null,
      offsetSeconds: offset(rec.started_at),
      seconds: rec.seconds,
      costUsd: num(rec.cost_usd),
      status: statusOf(rec),
      headline: stageHeadline(stage, rec.detail, c),
      facts: Object.entries(rec.detail).filter(([k]) => has(rec.detail, k)).map(([k, v]) => [humaniseKey(k), String(v)]),
      warnings: rec.warnings,
      error: rec.error ?? null,
      checks: c.check_runs.filter((r) => r.stage === stage),
      flags,
      calls: c.costs.filter((x) => x.stage === stage && !isTeamCall(x)),
    });
    if (stage === "crossdoc" && (c.agents ?? []).length > 0) {
      const agents = c.agents ?? [];
      const lowered = agents.flatMap((a) => a.verdicts).filter((v) => v.action === "lowered").length;
      const judged = agents.flatMap((a) => a.verdicts).length;
      push({
        ...base,
        key: "team",
        kind: "team",
        title: "The review team",
        at: null,
        offsetSeconds: null,
        seconds: agents.reduce((n, a) => n + a.seconds, 0),
        costUsd: agents.reduce((n, a) => n + num(a.cost_usd), 0),
        status: agents.some((a) => a.status === "failed") ? "failed" : "ok",
        headline: `Six members looked at the case after the checks: the specialists reported what their checks found, the challenger ruled on ${judged} ${plural(judged, "flag")} (lowered ${lowered}), the case officer wrote a summary.`,
        agents,
        calls: c.costs.filter(isTeamCall),
      });
    }
  }

  if (c.review.length > 0) {
    push({
      ...base,
      key: "review",
      kind: "review",
      title: "Reviewers' decisions",
      at: c.review[0].created_at,
      offsetSeconds: offset(c.review[0].created_at),
      seconds: null,
      costUsd: null,
      status: "info",
      headline: `${c.review.length} ${plural(c.review.length, "entry", "entries")} recorded by people${c.decision ? `; the case is closed with a verdict` : "; the case is still open"}.`,
      entries: c.review,
    });
  }
  return steps;
}

/** "+4.2 s after receipt" style text for a step's start. */
export function offsetText(s: Pick<TimelineStep, "offsetSeconds">): string {
  if (s.offsetSeconds === null) return "";
  if (s.offsetSeconds < 60) return `+${s.offsetSeconds.toFixed(1)} s`;
  return `+${formatSeconds(s.offsetSeconds)}`;
}

/** The timeline as plain data for download: small, no document text, one object per step. */
export function timelineExport(c: CaseDetail, steps: TimelineStep[]): unknown {
  return {
    case_id: c.id,
    version: c.version,
    use_case: c.use_case,
    score: c.score?.value ?? null,
    band: c.score?.band ?? null,
    lane: c.lane ?? null,
    steps: steps.map((s) => ({
      step: s.key,
      title: s.title,
      started_at: s.at,
      seconds: s.seconds,
      cost_usd: s.costUsd,
      status: s.status,
      headline: s.headline,
      facts: Object.fromEntries(s.facts),
      warnings: s.warnings,
      error: s.error,
      checks: s.checks.map((r) => ({ rule: r.rule, status: r.status, flags: r.flags, reason: r.reason ?? null, note: r.note ?? null, seconds: r.seconds, custom: r.custom ?? null })),
      flags_raised: s.flags.map((f) => ({ id: f.id, rule: f.rule, severity: f.severity, explanation: f.explanation, evidence: f.evidence, documents: f.document_ids })),
      flags_so_far: s.cumulative,
      model_calls: s.calls.map((x) => ({ purpose: x.purpose, model: x.model, prompt_tokens: x.prompt_tokens, completion_tokens: x.completion_tokens, usd: x.usd, seconds: x.seconds, cache_hit: x.cache_hit })),
      review_team: s.agents.map((a) => ({ agent: a.agent, status: a.status, checks_run: a.checks_run, flags: a.flag_ids, summary: a.summary, verdicts: a.verdicts, next_steps: a.next_steps, handoff: a.handoff, cost_usd: a.cost_usd, seconds: a.seconds })),
      decisions: s.entries.map((e) => ({ id: e.id, kind: e.kind, reviewer: e.reviewer, decision: e.decision, reason: e.reason, status: e.status, at: e.created_at })),
    })),
  };
}
