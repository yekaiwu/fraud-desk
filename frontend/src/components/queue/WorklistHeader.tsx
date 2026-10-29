"use client";

import clsx from "clsx";
import { ArrowRight, RefreshCw } from "lucide-react";
import type { QueueItem } from "@/lib/types";
import { Button, SectionHeading } from "../ui";
import { applicantOf } from "./QueueRow";
import { summarySegments, type WorklistSummary } from "./worklist";

/** The sentence, with the numbers in type and "need your attention" in brass-deep (never brass: it is text). */
function Summary({ summary, failed }: { summary: WorklistSummary | null; failed: boolean }) {
  if (!summary) return failed ? <span>The worklist could not be loaded.</span> : <span aria-hidden className="skeleton block h-5 w-72 max-w-full" />;
  return (
    <>
      {summarySegments(summary).map((s, i) => (
        <span key={i} className={clsx(s.tone === "strong" && "font-semibold text-ink", s.tone === "attention" && "font-semibold text-brass-deep")}>
          {s.text}
        </span>
      ))}
    </>
  );
}

export function WorklistHeader({
  summary,
  failed,
  next,
  loading,
  refreshing,
  updatedText,
  onRefresh,
}: {
  summary: WorklistSummary | null;
  failed: boolean;
  next: QueueItem | null;
  loading: boolean;
  refreshing: boolean;
  updatedText: string | null;
  onRefresh: () => void;
}) {
  return (
    <div>
      <SectionHeading
        level={1}
        size="page"
        description={<Summary summary={summary} failed={failed} />}
        className="[&_p]:max-w-2xl [&_p]:text-base sm:[&_p]:text-[1.0625rem]"
        actions={
          next ? (
            <div className="flex w-full flex-col items-start gap-1.5 sm:w-auto lg:items-end">
              <Button href={`/cases/${next.case_id}`} size="lg" icon={<ArrowRight aria-hidden className="h-5 w-5" />} className="w-full sm:w-auto">
                Open next case
              </Button>
              <p className="max-w-[22rem] text-xs text-ink-2 sm:text-right">
                Next up: <span className="font-semibold">{applicantOf(next)}</span>
                {next.score !== null ? <span className="tabular"> (risk {next.score})</span> : null}
              </p>
            </div>
          ) : loading ? (
            <Button size="lg" disabled icon={<ArrowRight aria-hidden className="h-5 w-5" />} className="w-full sm:w-auto">
              Open next case
            </Button>
          ) : null
        }
      >
        Worklist
      </SectionHeading>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-1">
        <p className="ml-auto flex items-center gap-1 text-sm text-muted">
          {updatedText ? <span className="tabular">{updatedText}</span> : null}
          <button
            type="button"
            onClick={onRefresh}
            aria-label="Refresh the queue"
            className="inline-flex h-11 w-11 items-center justify-center rounded-md text-ink-2 transition-colors hover:bg-brand-tint hover:text-brand"
          >
            <RefreshCw aria-hidden className={clsx("h-4 w-4", refreshing && "animate-spin")} />
          </button>
        </p>
      </div>
    </div>
  );
}
