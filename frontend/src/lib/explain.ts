/**
 * Numbers and plain facts behind the "?" explainers. Pure functions over data the API already sent
 * (the case, and GET /explain/method when the backend has it). Nothing here knows how the score is
 * built: weights, the cap, band limits and the fast-lane list come from the method response, and
 * when it is missing the case's own numbers (each finding's weight, the recorded score) are used and
 * the parts that need the method come back as null. The UI then says so instead of guessing.
 */
import { SEVERITY_ORDER } from "./flags";
import type { Band, CaseDetail, Classification, Coverage, Document, ExplainMethod, Finding, Lane, Severity, StageName, StageRecord } from "./types";

// ---------------------------------------------------------------- the risk score

export type SeverityRow = { severity: Severity; findings: number; pointsEach: number | null; subtotal: number };
export type ScoreBreakdown = {
  rows: SeverityRow[];
  findingsCounted: number;
  flagsBehind: number;
  /** Sum of every finding's points before any limit. */
  rawTotal: number;
  /** The score the case actually carries. */
  value: number;
  capped: boolean;
  /** The limit, from the method response, or (when only the case is known) the recorded score when it is below the raw total. */
  cap: number | null;
  band: Band;
  partial: boolean;
  bands: { mediumFrom: number; highFrom: number } | null;
  /** False when the recorded score cannot be reproduced from the findings (it is then shown as recorded). */
  consistent: boolean;
};

/** Findings grouped by severity with the points each adds, using each finding's own recorded weight. Null while there is no score. */
export function scoreBreakdown(c: Pick<CaseDetail, "findings" | "score" | "partial">, method?: ExplainMethod | null): ScoreBreakdown | null {
  const score = c.score;
  if (!score) return null;
  const rows: SeverityRow[] = [];
  for (const severity of SEVERITY_ORDER) {
    const list = c.findings.filter((f) => f.severity === severity);
    if (list.length === 0) continue;
    const weights = new Set(list.map((f) => f.weight));
    rows.push({ severity, findings: list.length, pointsEach: weights.size === 1 ? list[0].weight : null, subtotal: list.reduce((n, f) => n + f.weight, 0) });
  }
  const rawTotal = rows.reduce((n, r) => n + r.subtotal, 0);
  const capped = rawTotal > score.value;
  const cap = method?.score.cap ?? (capped ? score.value : null);
  const expected = cap === null ? rawTotal : Math.min(cap, rawTotal);
  return {
    rows,
    findingsCounted: c.findings.length,
    flagsBehind: c.findings.reduce((n, f) => n + f.flag_ids.length, 0),
    rawTotal,
    value: score.value,
    capped,
    cap,
    band: score.band,
    partial: c.partial || score.partial,
    bands: method ? { mediumFrom: method.score.bands.medium_from, highFrom: method.score.bands.high_from } : null,
    consistent: expected === score.value || (cap === null && capped),
  };
}

/** "Total 135, capped at 100". */
export function totalText(b: ScoreBreakdown): string {
  if (b.capped && b.cap !== null) return `Total ${b.rawTotal}, capped at ${b.cap}`;
  return `Total ${b.rawTotal}`;
}

// ---------------------------------------------------------------- checks completed

/** Checks completed, then reduced by the share of pages no check looked at. This is the figure the fast lane is measured against. */
export function effectiveCoverage(cov: Coverage | null | undefined): number | null {
  if (!cov) return null;
  if (cov.pages_total > 0) return cov.ratio * (1 - cov.pages_unchecked / cov.pages_total);
  return cov.ratio;
}

// ---------------------------------------------------------------- lane, fast lane, suggested next step

export type Requirement = { text: string; met: boolean | null; detail: string | null };

const pct = (r: number) => `${Math.round(r * 100)}%`;

export function hardStopFindings(c: Pick<CaseDetail, "findings">): Finding[] {
  return c.findings.filter((f) => f.hard_stop);
}

/** Findings that add points and come from a rule the method lists as routing a case to AML. */
export function amlRoutedFindings(c: Pick<CaseDetail, "findings">, method?: ExplainMethod | null): Finding[] {
  const rules = new Set(method?.aml_routing_rules ?? []);
  if (rules.size === 0) return [];
  return c.findings.filter((f) => f.weight > 0 && f.rules.some((r) => rules.has(r)));
}

/**
 * Ticks or crosses for the fast-lane list. The wording of each requirement belongs to the backend, so each one is
 * matched to the figure it is about; one that cannot be matched is listed without a verdict (met = null).
 */
export function evaluateRequirements(c: Pick<CaseDetail, "findings" | "score" | "partial" | "coverage">, method: ExplainMethod): Requirement[] {
  const partial = c.partial || !!c.score?.partial;
  const stops = hardStopFindings(c);
  const eff = effectiveCoverage(c.coverage);
  const min = method.coverage.fast_lane_min_coverage;
  return method.fast_lane.requires.map((text): Requirement => {
    if (!c.score) return { text, met: null, detail: null };
    if (/partial|complete\b|incomplete/i.test(text)) {
      return { text, met: !partial, detail: partial ? "This analysis is partial." : "This analysis is complete." };
    }
    if (/hard.?stop|block/i.test(text)) {
      return { text, met: stops.length === 0, detail: stops.length === 0 ? "No finding blocks it." : `${stops.length} ${stops.length === 1 ? "finding blocks" : "findings block"} it.` };
    }
    if (/\bscore\b|\brisk\b/i.test(text)) {
      const limit = /(\d+)(?!\s*%)/.exec(text);
      if (!limit) return { text, met: null, detail: `Score ${c.score.value}.` };
      return { text, met: c.score.value <= Number(limit[1]), detail: `Score ${c.score.value}.` };
    }
    if (/coverage|checks|completed|\d+\s*%/i.test(text)) {
      if (eff === null) return { text, met: null, detail: null };
      return { text, met: eff >= min, detail: `${pct(eff)} here, ${pct(min)} needed.` };
    }
    return { text, met: null, detail: null };
  });
}

export function laneInfo(method: ExplainMethod | null | undefined, lane: Lane | null | undefined) {
  return (lane && method?.lanes.find((l) => l.lane === lane)) || null;
}

// ---------------------------------------------------------------- how it was analysed

export function stageOf(c: Pick<CaseDetail, "stages">, name: StageName): StageRecord | undefined {
  return c.stages.find((s) => s.stage === name);
}

/** Seconds as a person reads them: "0.4 s", "12 s", "3 min". */
export function secondsText(s: number): string {
  if (!Number.isFinite(s) || s <= 0) return "0 s";
  if (s < 10) return `${Math.round(s * 10) / 10} s`;
  if (s < 60) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

export type ReadSummary = { files: number; pages: number; textPages: number; scanPages: number; unreadable: number; ocrCalls: number | null; seconds: number | null };

/** Files and pages read, from the files themselves; the ingest stage adds OCR calls and the time taken. */
export function readSummary(c: Pick<CaseDetail, "files" | "stages">): ReadSummary {
  const pages = c.files.flatMap((f) => f.pages);
  const stage = stageOf(c, "ingest");
  const ocr = stage?.detail?.ocr_calls;
  return {
    files: c.files.length,
    pages: pages.length,
    textPages: pages.filter((p) => p.has_text_layer && !p.is_scan).length,
    scanPages: pages.filter((p) => p.is_scan).length,
    unreadable: pages.filter((p) => p.error).length,
    ocrCalls: typeof ocr === "number" ? ocr : null,
    seconds: stage ? stage.seconds : null,
  };
}

export type Decider = { kind: "rules" | "model" | "other"; text: string };

/** Who chose a document's type: the rules classifier, or a model. */
export function decider(cl: Pick<Classification, "classifier" | "version" | "rationale">): Decider {
  const name = cl.classifier.trim();
  if (/^rules?\b/i.test(name)) return { kind: "rules", text: `Rules classifier, version ${cl.version}` };
  if (cl.rationale || /llm|model|claude|gpt|haiku|sonnet|opus|hosted/i.test(name)) return { kind: "model", text: `A model (${name}, ${cl.version})` };
  return { kind: "other", text: `${name}, version ${cl.version}` };
}

/** The labels next in line, most likely first, without the one that was chosen. */
export function alternatives(cl: Pick<Classification, "label" | "distribution">, limit = 3): { label: string; probability: number }[] {
  return Object.entries(cl.distribution ?? {})
    .filter(([label]) => label !== cl.label)
    .map(([label, probability]) => ({ label, probability }))
    .sort((a, b) => b.probability - a.probability || a.label.localeCompare(b.label))
    .slice(0, limit);
}

export type ExtractorKind = "text_layer" | "model" | "mixed" | "none" | "other";
export type ExtractorInfo = { kind: ExtractorKind; text: string };

const STEP_WORD: Record<string, string> = { text_layer: "the text layer", llm_cheap: "a smaller model", llm_strong: "a stronger model" };

/** How figures were read, from `Document.extractor` ("text_layer@1", "llm:name", "cascade(text_layer>llm_cheap)"). */
export function describeExtractor(extractor: string | null | undefined): ExtractorInfo {
  const e = (extractor ?? "").trim();
  if (!e) return { kind: "none", text: "No figures were read from it." };
  if (/^text_layer/.test(e)) return { kind: "text_layer", text: "Read from the document's own text layer." };
  const llm = /^llm:(.+)$/.exec(e);
  if (llm) return { kind: "model", text: `Read from the page image by a model (${llm[1]}).` };
  const cascade = /^cascade\((.+)\)$/.exec(e);
  if (cascade) {
    const steps = cascade[1].split(">").map((s) => STEP_WORD[s.trim()] ?? s.trim());
    return { kind: "mixed", text: `Read in steps: ${steps.join(", then ")}.` };
  }
  return { kind: "other", text: `Read by ${e}.` };
}

export type SecondReading = { page: number; reason: string; result: string };

/** Pages where the case data shows a second reading ("escalated: page=2 reason=... result=..."). */
export function secondReadings(doc: Pick<Document, "warnings">): SecondReading[] {
  const out: SecondReading[] = [];
  for (const w of doc.warnings ?? []) {
    const m = /^escalated:\s*page=(\d+)\s+reason=(\S+)\s+result=(\S+)/.exec(w.trim());
    if (m) out.push({ page: Number(m[1]), reason: m[2], result: m[3] });
  }
  return out;
}

/** Pages a document's reader gave up on, as the backend worded them. */
export function unreadableNotes(doc: Pick<Document, "warnings">): string[] {
  return (doc.warnings ?? []).filter((w) => /^(unreadable|escalation_failed)\b/i.test(w.trim()));
}

// ---------------------------------------------------------------- reproducibility footer

/** "0.004200" -> "0.0042"; "0.000000" -> "0.00". Kept as text: an amount is never turned into a float. */
export function costText(usd: string): string {
  const [whole, frac = ""] = usd.trim().split(".");
  const trimmed = frac.replace(/0+$/, "");
  return `${whole || "0"}.${trimmed.padEnd(2, "0")}`;
}

export function isZeroCost(usd: string | null | undefined): boolean {
  return !usd || /^0*\.?0*$/.test(usd.trim());
}

/** "$0.00, no paid model used" or "$0.0042 across 3 model calls". */
export function costSummaryText(c: Pick<CaseDetail, "cost_summary">): string {
  const t = c.cost_summary?.total_usd;
  if (isZeroCost(t)) return "$0.00, no paid model used";
  const calls = c.cost_summary.calls;
  return `$${costText(t)}${calls > 0 ? ` across ${calls} model ${calls === 1 ? "call" : "calls"}` : ""}`;
}

export function shortHash(h: string, n = 12): string {
  return h.length > n ? h.slice(0, n) : h;
}

const CHECK_STAGES: StageName[] = ["forensics", "rules", "crossdoc", "score"];

/**
 * True only when the case data supports "the same documents give the same checks and flags": a ruleset version and a
 * flag digest were recorded, every checking stage finished, and no paid model took part in any of them.
 */
export function checksAreReproducible(c: Pick<CaseDetail, "provenance" | "stages" | "cost_summary">): boolean {
  const p = c.provenance;
  if (!p || !p.ruleset_version || !p.flags_digest) return false;
  for (const name of CHECK_STAGES) {
    const s = stageOf(c, name);
    if (!s || s.status !== "ok") return false;
    if (!isZeroCost(s.cost_usd)) return false;
    if (!isZeroCost(c.cost_summary?.usd_by_stage?.[name])) return false;
  }
  return true;
}
