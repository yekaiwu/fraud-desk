import { LOW_COVERAGE_RATIO } from "@/lib/constants";
import { formatAge } from "@/lib/format";
import { laneDescription, laneLabel, plural } from "@/lib/labels";
import type { Band, Lane, QueueItem } from "@/lib/types";

/**
 * Pure helpers behind the worklist: which cases need attention, the summary sentence, lane and band
 * counts, the "next case" and small wording bits. No React here, so each rule is tested on its own.
 * The tool ranks and recommends; none of this decides anything about a case.
 */

export const LONG_WAIT_SECONDS = 24 * 3600;

/** Cases that deserve a person's eyes first: not low risk, an incomplete score, a high concern, thin coverage or a decision waiting for a second reviewer. */
export function needsAttention(i: QueueItem): boolean {
  if (i.status !== "open") return false;
  return i.band !== "low" || i.partial || i.high_count > 0 || i.pending_second_approvals > 0 || (i.coverage !== null && i.coverage.ratio < LOW_COVERAGE_RATIO);
}

export type SummarySegment = { text: string; tone?: "strong" | "attention" };

export type WorklistSummary = {
  open: number;
  attention: number;
  routine: number;
  processing: number;
  awaiting: number;
  /** Seconds the longest-waiting open case has waited, or null when nothing is open. */
  oldestSeconds: number | null;
  /** True when the sample holds fewer cases than exist, so the numbers are a floor. */
  truncated: boolean;
  /** True when a search or filter is narrowing the sample, so "no cases" means "no matches". */
  narrowed: boolean;
};

export function summarise(items: readonly QueueItem[], extraAgeSeconds = 0, truncated = false, narrowed = false): WorklistSummary {
  const open = items.filter((i) => i.status === "open");
  const attention = open.filter(needsAttention).length;
  const oldest = open.reduce<number | null>((m, i) => (m === null ? i.age_seconds : Math.max(m, i.age_seconds)), null);
  return {
    open: open.length,
    attention,
    routine: open.length - attention,
    processing: items.filter((i) => i.status === "processing").length,
    awaiting: items.filter((i) => i.status === "awaiting_documents").length,
    oldestSeconds: oldest === null ? null : oldest + extraAgeSeconds,
    truncated,
    narrowed,
  };
}

/** "2 days", "3 hours", "20 minutes": the long form of formatAge for sentences. */
export function formatWaitLong(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return "under a minute";
  if (s < 3600) return `${Math.floor(s / 60)} ${plural(Math.floor(s / 60), "minute")}`;
  if (s < 86400) return `${Math.floor(s / 3600)} ${plural(Math.floor(s / 3600), "hour")}`;
  return `${Math.floor(s / 86400)} ${plural(Math.floor(s / 86400), "day")}`;
}

/**
 * One calm sentence, as segments so the page can set the numbers in type:
 * "10 cases open: 3 need your attention, 7 look routine. Oldest has waited 2 days."
 */
export function summarySegments(s: WorklistSummary): SummarySegment[] {
  const out: SummarySegment[] = [];
  const floor = s.truncated ? "At least " : "";
  if (s.narrowed && s.open + s.processing + s.awaiting === 0) {
    return [{ text: "No cases match your search or filters.", tone: "strong" }];
  }
  if (s.open === 0) {
    out.push({ text: "Nothing needs attention right now.", tone: "strong" });
  } else {
    out.push({ text: `${floor}${s.open} ${plural(s.open, "case")} open`, tone: "strong" }, { text: ": " });
    if (s.attention === 0) out.push({ text: s.open === 1 ? "it looks routine" : "all look routine" });
    else if (s.routine === 0) out.push({ text: s.open === 1 ? "it needs your attention" : `all ${s.attention} need your attention`, tone: "attention" });
    else {
      out.push({ text: `${s.attention} ${s.attention === 1 ? "needs" : "need"} your attention`, tone: "attention" }, { text: ", " });
      out.push({ text: `${s.routine} ${s.routine === 1 ? "looks" : "look"} routine` });
    }
    out.push({ text: "." });
    if (s.oldestSeconds !== null) out.push({ text: ` Oldest has waited ${formatWaitLong(s.oldestSeconds)}.` });
  }
  const others = [s.processing > 0 ? `${s.processing} being analysed` : null, s.awaiting > 0 ? `${s.awaiting} waiting for documents` : null].filter(Boolean);
  if (others.length) out.push({ text: ` Also ${others.join(" and ")}.` });
  return out;
}

export function summaryText(s: WorklistSummary): string {
  return summarySegments(s)
    .map((x) => x.text)
    .join("");
}

export type LaneKey = Lane | "all";

/** Cases per lane tab, honouring the band filter so a tab's number is what you would see after clicking it. */
export function laneCounts(items: readonly QueueItem[], bands?: readonly Band[]): Record<LaneKey, number> {
  const counts: Record<LaneKey, number> = { all: 0, loan: 0, edd: 0, aml: 0, fast: 0 };
  for (const i of items) {
    if (bands?.length && (i.band === null || !bands.includes(i.band))) continue;
    counts.all += 1;
    if (i.lane) counts[i.lane] += 1;
  }
  return counts;
}

/** Cases per risk band chip, honouring the lane filter. Cases without a score yet are in no band. */
export function bandCounts(items: readonly QueueItem[], lanes?: readonly Lane[]): Record<Band, number> {
  const counts: Record<Band, number> = { high: 0, medium: 0, low: 0 };
  for (const i of items) {
    if (lanes?.length && (i.lane === null || !lanes.includes(i.lane))) continue;
    if (i.band) counts[i.band] += 1;
  }
  return counts;
}

/** The highest ranked case a person can open and decide now. `items` must already be in priority order. */
export function pickNext(items: readonly QueueItem[], filter: { lane?: readonly Lane[]; band?: readonly Band[] } = {}): QueueItem | null {
  return (
    items.find(
      (i) =>
        i.status === "open" &&
        (!filter.lane?.length || (i.lane !== null && filter.lane.includes(i.lane))) &&
        (!filter.band?.length || (i.band !== null && filter.band.includes(i.band))),
    ) ?? null
  );
}

/** The API appends "(+5 more)" to the top finding when there are further findings. Split it out so the page can style it. */
export function splitTopFinding(text: string | null): { text: string; more: number } | null {
  if (!text) return null;
  const none = /^No flags raised;\s*(\d+) of (\d+) checks ran/.exec(text);
  // The API's "nothing found" sentence is system-speak ("No flags raised; 41 of 54 checks ran (8 informational notes)").
  if (none) return { text: `No concerns found. ${none[1]} of ${none[2]} checks completed.`, more: 0 };
  const m = /\s*\(\+(\d+) more\)\s*$/.exec(text);
  return m ? { text: text.slice(0, m.index), more: Number(m[1]) } : { text, more: 0 };
}

/**
 * The lane badge. A lane is the queue a case sits in, but for a case with no concern it is only the default queue of
 * its case type: calling it "AML investigation" would read as an escalation. Low-risk cases without a high concern
 * therefore show "Routine review" (the fast lane keeps its own badge).
 */
export function laneBadgeFor(item: Pick<QueueItem, "lane" | "band" | "high_count">): { label: string; title: string; variant: "lane" | "fast" | "neutral" } | null {
  if (!item.lane) return null;
  if (item.lane === "fast") return { label: laneLabel("fast"), title: laneDescription("fast"), variant: "fast" };
  if (item.band === "low" && !item.high_count) {
    return { label: "Routine review", title: `Low risk, nothing to escalate. It sits in the ${laneLabel(item.lane)} queue; a person still reviews it.`, variant: "neutral" };
  }
  return { label: laneLabel(item.lane), title: laneDescription(item.lane), variant: "lane" };
}

/** "Waiting 2 h". Only the wording lives here; the colour is the caller's job (see isLongWait). */
export function waitingText(seconds: number): string {
  return `Waiting ${formatAge(seconds)}`;
}

/** A case that has waited a day or more gets the ageing colour. */
export function isLongWait(seconds: number): boolean {
  return seconds >= LONG_WAIT_SECONDS;
}
