"use client";

import { Inbox, SearchX } from "lucide-react";
import { Button, EmptyState, Notice, Skeleton } from "../ui";
import { ApiErrorException, explainError, type ApiErrorBody } from "@/lib/errors";

/** Placeholder rows shaped like the real ones (applicant, reason, risk). Decorative; the status line speaks. */
export function QueueSkeleton() {
  return (
    <div>
      <p role="status" className="sr-only">
        Loading cases
      </p>
      <ul aria-busy="true" aria-label="Loading the queue" className="space-y-3">
        {Array.from({ length: 6 }, (_, i) => (
          <li key={i} className="flex items-center gap-4 rounded-lg border border-rule bg-surface p-4 shadow-paper">
            <div className="w-16 shrink-0 space-y-2">
              <Skeleton className="h-8 w-14" />
              <Skeleton className="h-2 w-12" />
            </div>
            <div className="min-w-0 flex-1 space-y-2.5">
              <Skeleton className="h-5 w-2/3 max-w-72" />
              <Skeleton className="h-3.5 w-full max-w-xl" />
            </div>
            <div className="hidden w-40 shrink-0 space-y-2 lg:block">
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-2.5 w-28" />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function NothingWaiting() {
  return (
    <EmptyState
      icon={<Inbox aria-hidden className="h-6 w-6" />}
      title="Nothing needs attention right now"
      text="There are no cases waiting for you. New cases appear here as soon as their documents have been uploaded."
      action={
        <Button href="/upload" variant="secondary">
          Start a new case
        </Button>
      }
    />
  );
}

export function NoMatches({ onClear, closedHidden }: { onClear: () => void; closedHidden: boolean }) {
  return (
    <EmptyState
      icon={<SearchX aria-hidden className="h-6 w-6" />}
      title="No cases match these filters"
      text={closedHidden ? "Try a different search or lane. Closed cases are hidden unless you choose Show closed." : "Try a different search or lane."}
      action={<Button variant="secondary" onClick={onClear}>Clear filters</Button>}
    />
  );
}

export function QueueError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const body: ApiErrorBody = error instanceof ApiErrorException ? error.error : { code: "internal_error", message: error instanceof Error ? error.message : "Unexpected error." };
  const { title, detail, retryable } = explainError(body);
  const requestId = error instanceof ApiErrorException ? error.requestId : null;
  return (
    <Notice
      variant="error"
      title={title}
      action={
        retryable ? (
          <Button size="sm" variant="secondary" onClick={onRetry}>
            Retry
          </Button>
        ) : undefined
      }
    >
      {detail}
      {requestId ? <span className="mt-1 block text-xs text-muted">Reference: {requestId}</span> : null}
    </Notice>
  );
}
