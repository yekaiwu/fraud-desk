"use client";

import { ArrowRight } from "lucide-react";
import { useId } from "react";
import clsx from "clsx";
import type { AgentReport } from "@/lib/types";
import { GENERATED_NOTE } from "../explain/AnalysisTrail";
import { Notice } from "../ui/Notice";
import { Disclosure } from "./EvidenceDisclosure";

const LINK = "-mx-1 inline-flex min-h-9 items-center gap-1 rounded-md px-1 text-sm font-semibold text-brand-2 hover:bg-brand-tint pointer-coarse:min-h-11";

/**
 * The case officer's summary and next steps, shown straight under the case's tab bar so they are in view
 * whichever tab is open (they used to live only in the Timeline). The summary is always visible; the next steps
 * are one click away (with their count), so the card stays short above the findings on every tab. It is the
 * review team's generated text: dashed outline and a note say so, and it never changes a flag, the score or the
 * lane.
 *
 * Shows nothing when there is no officer report (a case from before the review team) or the officer had nothing
 * to do (no flag at medium or above). When the officer could not run it says so in one line, because a missing
 * summary should not be mistaken for "nothing to say"; every flag stands either way. `onOpenTimeline` adds a link
 * to the full trail (leave it out on the Timeline tab itself).
 */
export function OfficerBrief({ agents, onOpenTimeline, className }: { agents?: AgentReport[] | null; onOpenTimeline?: () => void; className?: string }) {
  const titleId = useId();
  const officer = agents?.find((a) => a.agent === "case_officer");
  if (!officer || officer.status === "skipped") return null;

  const trail = onOpenTimeline ? (
    <button type="button" onClick={onOpenTimeline} className={LINK}>
      Review team
      <ArrowRight aria-hidden className="h-4 w-4" />
    </button>
  ) : null;

  if (officer.status === "failed") {
    return (
      <Notice variant="warning" title="The case officer could not write a summary" className={className} action={trail}>
        Every flag stands as it was.
      </Notice>
    );
  }

  return (
    <section aria-labelledby={titleId} className={clsx("rounded-lg border border-dashed border-rule-strong bg-paper/70 px-4 py-3", className)}>
      <div className="flex items-center justify-between gap-3">
        <p id={titleId} className="label-caps">
          Case officer&rsquo;s summary
        </p>
        {trail}
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words text-[0.9375rem] leading-normal text-ink">{officer.summary}</p>
      {officer.next_steps.length > 0 ? (
        <Disclosure label="Suggested next steps" meta={`(${officer.next_steps.length})`} className="mt-1">
          <ol className="list-decimal space-y-1 pl-5 text-[0.9375rem] leading-snug text-ink-2">
            {officer.next_steps.map((s, i) => (
              <li key={i} className="break-words">
                {s}
              </li>
            ))}
          </ol>
        </Disclosure>
      ) : null}
      <p className="mt-1.5 text-xs text-muted">{GENERATED_NOTE}</p>
    </section>
  );
}
