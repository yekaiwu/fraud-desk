"use client";

import clsx from "clsx";
import { Clock, FileWarning, Loader2 } from "lucide-react";
import Link from "next/link";
import { LOW_COVERAGE_RATIO, VERDICT_LABELS } from "@/lib/constants";
import { caseStatusLabel, plural, scoreSummary, useCaseLabel } from "@/lib/labels";
import { openRowLink, ROW_LINK_ATTR } from "@/lib/rowLink";
import type { Band, Coverage, QueueItem } from "@/lib/types";
import { ExplainChecksColumn, ExplainRiskColumn } from "../explain/ListExplainers";
import { Badge, RiskBadge, StatusBadge } from "../ui";
import { isLongWait, laneBadgeFor, needsAttention, splitTopFinding, waitingText } from "./worklist";

export { ROW_LINK_ATTR };

const NUMERAL: Record<Band, string> = { high: "text-sev-high", medium: "text-sev-medium", low: "text-ink-2" };
const BAR: Record<Band, string> = { high: "bg-sev-high", medium: "bg-sev-medium", low: "bg-sev-low" };

function isAnalysing(i: QueueItem): boolean {
  return i.status === "processing" || i.score === null;
}

export function applicantOf(i: QueueItem): string {
  return i.applicant_name ?? "Unnamed applicant";
}

/**
 * The applicant name is the headline and the one link of the row. With `stretch` its ::after covers the whole card
 * (the card is `relative`), so the entire card is clickable without nesting interactive elements. A table row cannot
 * use that: browsers disagree on whether a `<tr>` is a containing block, and where it is not every row's overlay
 * grows to the nearest positioned ancestor, stacks with the others and the last row catches every click. Table rows
 * pass `stretch={false}` and forward row clicks to this link instead (`openRowLink` in lib/rowLink.ts).
 */
export function ApplicantLink({ item, className, stretch = true }: { item: QueueItem; className?: string; stretch?: boolean }) {
  return (
    <Link
      href={`/cases/${item.case_id}`}
      {...{ [ROW_LINK_ATTR]: "" }}
      className={clsx(
        "group block rounded-sm",
        stretch
          ? "focus-visible:outline-none! after:absolute after:inset-0 after:content-[''] focus-visible:after:rounded-[inherit] focus-visible:after:outline-2 focus-visible:after:-outline-offset-2 focus-visible:after:outline-brand-2"
          : "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-2",
        className,
      )}
    >
      <span className={clsx("block text-[1.0625rem] font-semibold leading-snug text-ink group-hover:underline", item.applicant_name === null && "font-normal text-ink-2")}>{applicantOf(item)}</span>
      {item.reference ? <span className="num mt-0.5 block text-xs text-muted">{item.reference}</span> : null}
    </Link>
  );
}

export function CaseMeta({ item }: { item: QueueItem }) {
  return (
    <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
      <Badge variant="neutral" icon={null}>
        {useCaseLabel(item.use_case)}
      </Badge>
      {item.version > 1 ? <span className="font-semibold text-ink-2">Version {item.version}</span> : null}
      <span>
        {item.file_count} {plural(item.file_count, "file")}
      </span>
    </span>
  );
}

export function RiskCell({ item, compact = false }: { item: QueueItem; compact?: boolean }) {
  if (isAnalysing(item)) {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm font-medium text-sev-info">
        <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
        Score pending
      </span>
    );
  }
  const band = item.band ?? "low";
  const score = item.score ?? 0;
  return (
    <span className={clsx("flex flex-col", compact ? "items-end gap-1" : "items-start gap-1.5")}>
      <span className="flex items-center gap-2.5">
        <span role="img" aria-label={scoreSummary(score, band, item.partial)} className={clsx("font-display text-[1.875rem] font-semibold leading-none tabular", NUMERAL[band])}>
          {item.partial ? "≥ " : ""}
          {score}
        </span>
        <span aria-hidden className="h-1.5 w-14 overflow-hidden rounded-full border border-rule bg-paper-2">
          <span className={clsx("block h-full rounded-full", BAR[band])} style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
        </span>
      </span>
      {compact ? null : <RiskBadge band={band} partial={item.partial} />}
    </span>
  );
}

/** The one-line reason in plain English plus "+N more". Never a verdict: it says what the checks found. */
export function ReasonCell({ item }: { item: QueueItem }) {
  if (item.status === "processing") return <span className="text-sm text-muted">Still being analysed. The score appears when this finishes.</span>;
  if (item.status === "error") return <span className="text-sm text-ink-2">The analysis could not finish. Open the case to run it again.</span>;
  const top = splitTopFinding(item.top_finding);
  if (!top) return <span className="text-sm text-muted">No summary yet</span>;
  const more = Math.max(top.more, item.findings_count > 1 ? item.findings_count - 1 : 0);
  return (
    <span className="block">
      <span className="line-clamp-2 text-[0.9375rem] leading-snug text-ink" title={top.text}>
        {top.text}
      </span>
      {more > 0 || item.high_count > 0 ? (
        <span className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-2">
          {more > 0 ? (
            <Badge variant="neutral" icon={null} title={`${more} more ${plural(more, "finding")} in this case`}>
              +{more} more
            </Badge>
          ) : null}
          {item.high_count > 0 ? (
            <span className="font-semibold text-sev-high">
              {item.high_count} high {plural(item.high_count, "concern")}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}

export function isLowCoverage(c: Coverage | null): boolean {
  return c !== null && c.ratio < LOW_COVERAGE_RATIO;
}

/** "41 of 54" checks completed as a slim meter, with the pages nobody reviewed called out in brass. */
export function ChecksCell({ item, withLabel = false }: { item: QueueItem; withLabel?: boolean }) {
  const c = item.coverage;
  if (!c) return <span className="text-muted">-</span>;
  const low = isLowCoverage(c);
  const pct = c.applicable > 0 ? Math.min(100, (c.ran / c.applicable) * 100) : 0;
  return (
    <span className="block min-w-0">
      <span className="flex flex-wrap items-baseline gap-x-1.5 text-[0.8125rem] text-ink-2 tabular">
        {withLabel ? (
          <span className="inline-flex items-center gap-1.5">
            Checks completed
            <ExplainChecksColumn item={item} />
          </span>
        ) : null}
        <span className="font-semibold text-ink">
          {c.ran} of {c.applicable}
        </span>
        {low ? <span className="font-semibold text-brass-deep">Low coverage</span> : null}
      </span>
      <span aria-hidden className="relative mt-1.5 block h-1.5 w-full max-w-36 overflow-hidden rounded-full border border-rule bg-paper-2">
        <span className={clsx("block h-full rounded-full", low ? "bg-brass" : "bg-brand-2")} style={{ width: `${pct}%` }} />
        <span className="absolute inset-y-0 w-px bg-ink/60" style={{ left: `${LOW_COVERAGE_RATIO * 100}%` }} />
      </span>
      {c.pages_unchecked > 0 ? (
        <span className="mt-1.5 flex items-start gap-1 text-xs font-semibold text-brass-deep">
          <FileWarning aria-hidden className="mt-px h-3.5 w-3.5 shrink-0" />
          {c.pages_unchecked} {plural(c.pages_unchecked, "page")} not reviewed
        </span>
      ) : null}
    </span>
  );
}

export function WaitingCell({ item, ageNow }: { item: QueueItem; ageNow: number }) {
  const seconds = item.age_seconds + ageNow;
  const long = isLongWait(seconds);
  return (
    <span className={clsx("inline-flex items-center gap-1.5 whitespace-nowrap text-[0.9375rem] tabular", long ? "font-semibold text-brass-deep" : "text-ink-2")} title={long ? "Waiting a day or more" : undefined}>
      <Clock aria-hidden className="h-3.5 w-3.5 shrink-0" />
      {waitingText(seconds)}
    </span>
  );
}

export function LaneStatusCell({ item, showStatus = true }: { item: QueueItem; showStatus?: boolean }) {
  const laneBadge = laneBadgeFor(item);
  return (
    <span className="flex flex-col items-start gap-1.5">
      {laneBadge ? (
        <Badge variant={laneBadge.variant} title={laneBadge.title}>
          {laneBadge.label}
        </Badge>
      ) : (
        <span className="text-sm text-muted">No lane yet</span>
      )}
      {showStatus ? <StatusLine item={item} /> : null}
    </span>
  );
}

/** Where the case stands. A case that is simply ready to review reads as plain text so the exceptions stand out. */
export function StatusLine({ item }: { item: QueueItem }) {
  return (
    <span className="flex flex-col items-start gap-1.5">
      {item.status === "open" ? <span className="text-sm text-ink-2">{caseStatusLabel("open")}</span> : <StatusBadge status={item.status} />}
      {item.pending_second_approvals > 0 ? <Badge variant="attention">Second approval pending</Badge> : null}
      {item.status === "closed" && item.decision ? <span className="text-xs text-muted">{VERDICT_LABELS[item.decision]}</span> : null}
    </span>
  );
}

/** Attention is shown as a brass edge on the row; everything else is the paper. */
export const EDGE = "before:absolute before:inset-y-0 before:left-0 before:w-[3px] before:bg-brass before:content-['']";

export function QueueTableRow({ item, rank, ageNow }: { item: QueueItem; rank: number | null; ageNow: number }) {
  const edge = needsAttention(item);
  const first = "relative px-3 py-4 align-top first:pl-5";
  return (
    <tr onClick={openRowLink} className="cursor-pointer border-t border-rule bg-surface transition-colors hover:bg-brand-tint/60 focus-within:bg-brand-tint/80">
      {rank !== null ? (
        <td className={clsx(first, "w-12 font-display text-lg text-muted tabular", edge && EDGE)}>{rank}</td>
      ) : null}
      <td className={clsx("px-3 py-4 align-top", rank === null && ["relative first:pl-5", edge && EDGE])}>
        <RiskCell item={item} />
      </td>
      <th scope="row" className="min-w-[14rem] px-3 py-4 text-left align-top font-normal">
        <ApplicantLink item={item} stretch={false} />
        <span className="mt-2 block">
          <CaseMeta item={item} />
        </span>
      </th>
      <td className="min-w-[15rem] max-w-md px-3 py-4 align-top">
        <ReasonCell item={item} />
      </td>
      <td className="px-3 py-4 align-top">
        <LaneStatusCell item={item} />
      </td>
      <td className="w-44 px-3 py-4 align-top">
        <ChecksCell item={item} />
      </td>
      <td className="px-3 py-4 pr-5 align-top">
        <WaitingCell item={item} ageNow={ageNow} />
      </td>
    </tr>
  );
}

export function QueueCard({ item, rank, ageNow }: { item: QueueItem; rank: number | null; ageNow: number }) {
  const laneBadge = laneBadgeFor(item);
  const edge = needsAttention(item);
  const analysing = isAnalysing(item);
  return (
    <li className={clsx("relative rounded-lg border border-rule bg-surface p-4 shadow-paper transition-colors hover:bg-brand-tint/40 focus-within:bg-brand-tint/60", edge && "border-l-[3px] border-l-brass")}>
      <div className="grid grid-cols-[1fr_auto] items-start gap-x-4 gap-y-3">
        <div className="min-w-0">
          <ApplicantLink item={item} />
          <span className="mt-1.5 block">
            <CaseMeta item={item} />
          </span>
        </div>
        <div className="flex flex-col items-end gap-1 text-right">
          <RiskCell item={item} compact />
          {analysing ? null : <ExplainRiskColumn item={item} />}
        </div>
        <div className="col-span-2 flex flex-wrap items-center gap-2">
          {rank !== null ? <span className="font-display text-base font-semibold text-muted tabular">#{rank}</span> : null}
          {!analysing && item.band ? <RiskBadge band={item.band} partial={item.partial} /> : null}
          {laneBadge ? (
            <Badge variant={laneBadge.variant} title={laneBadge.title}>
              {laneBadge.label}
            </Badge>
          ) : null}
        </div>
        <div className="col-span-2">
          <ReasonCell item={item} />
        </div>
      </div>
      <div className="mt-4 grid grid-cols-[1fr_auto] items-start gap-x-4 gap-y-2 border-t border-rule pt-3">
        <ChecksCell item={item} withLabel />
        <WaitingCell item={item} ageNow={ageNow} />
        {item.status !== "open" || item.pending_second_approvals > 0 ? (
          <div className="col-span-2">
            <StatusLine item={item} />
          </div>
        ) : null}
      </div>
    </li>
  );
}
