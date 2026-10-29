"use client";

import { humanisePartialReason } from "@/lib/flags";
import type { CaseDetail } from "@/lib/types";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";

export const PARTIAL_TEXT = "Partial analysis. Some checks or pages did not run, so the real score may be higher. A partial case is never fast lane.";

/** Page-level messages above the hero: failed analysis, old version, partial score, waiting second approvals. */
export function CaseBanners({ c, isCurrent, jobError, onGoCoverage, onGoReview, onRerun, currentHref }: {
  c: CaseDetail; isCurrent: boolean; jobError?: string | null; onGoCoverage: () => void; onGoReview: () => void; onRerun: () => void; currentHref: string;
}) {
  const partial = c.partial || !!c.score?.partial;
  const reasons = c.partial_reasons.length ? c.partial_reasons : c.score?.partial_reasons ?? [];
  const pending = c.pending_second_approvals.length;
  const failed = c.status === "error";
  const showPartial = partial && !failed && c.status !== "processing";
  if (!failed && isCurrent && !showPartial && pending === 0) return null;
  return (
    <div className="space-y-3">
      {failed ? (
        <Notice
          variant="error"
          title={`Analysis failed${jobError ? `: ${jobError}` : "."}`}
          action={
            isCurrent ? (
              <Button size="sm" onClick={onRerun}>
                Re-run
              </Button>
            ) : undefined
          }
        >
          This case could not be analysed, so there is no score yet. Re-running keeps all earlier history.
        </Notice>
      ) : null}
      {!isCurrent ? (
        <Notice
          variant="info"
          title={`Viewing version ${c.version}, read-only.`}
          action={
            <Button size="sm" variant="secondary" href={currentHref}>
              Go to current
            </Button>
          }
        >
          Decisions can only be recorded on the current version.
        </Notice>
      ) : null}
      {showPartial ? (
        <Notice
          variant="warning"
          title={PARTIAL_TEXT}
          action={
            <Button size="sm" variant="secondary" onClick={onGoCoverage}>
              See the coverage panel
            </Button>
          }
        >
          {reasons.length > 0 ? (
            <ul className="list-disc pl-5">
              {reasons.map((r) => (
                <li key={r}>{humanisePartialReason(r)}</li>
              ))}
            </ul>
          ) : null}
        </Notice>
      ) : null}
      {pending > 0 ? (
        <Notice
          variant="warning"
          title={`${pending} ${pending === 1 ? "dismissal is" : "dismissals are"} waiting for a second reviewer.`}
          action={
            <Button size="sm" variant="secondary" onClick={onGoReview}>
              Open Decision &amp; activity
            </Button>
          }
        >
          A different reviewer must countersign before {pending === 1 ? "it takes" : "they take"} effect.
        </Notice>
      ) : null}
    </div>
  );
}
