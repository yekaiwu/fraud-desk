"use client";

import { ArrowLeft } from "lucide-react";
import { ApiErrorException, explainError, type ApiErrorBody } from "@/lib/errors";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";
import { Panel } from "../ui/Panel";
import { Skeleton, SkeletonText } from "../ui/Skeleton";

/** Any thrown value as an error Notice (explainError text, request reference for 5xx). Retry only when retryable. */
export function CaseErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const body: ApiErrorBody =
    error instanceof ApiErrorException ? error.error : { code: "internal_error", message: error instanceof Error ? error.message : "Unexpected error." };
  const { title, detail, retryable } = explainError(body);
  const requestId = error instanceof ApiErrorException ? error.requestId : null;
  return (
    <Notice
      variant="error"
      title={title}
      action={
        retryable && onRetry ? (
          <Button size="sm" variant="secondary" onClick={onRetry}>
            Retry
          </Button>
        ) : undefined
      }
    >
      {detail ? <p>{detail}</p> : null}
      {requestId ? <p className="mt-1 text-xs text-muted">Reference: {requestId}</p> : null}
    </Notice>
  );
}

/** The case could not be loaded: say why, offer a retry and the way back. */
export function CaseLoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div className="mx-auto max-w-2xl space-y-4 py-6">
      <h1 className="sr-only">The case could not be loaded</h1>
      <CaseErrorNotice error={error} onRetry={onRetry} />
      <Button href="/" variant="secondary" icon={<ArrowLeft aria-hidden className="h-4 w-4" />}>
        Back to worklist
      </Button>
    </div>
  );
}

/** Shaped like the page: header, hero (gauge, text, actions), then the two work columns. */
export function CaseSkeleton() {
  const box = (w: string, h: string, extra = "") => (
    <div className={`${w} ${extra}`}>
      <Skeleton className={h} />
    </div>
  );
  return (
    <div aria-busy="true" className="space-y-6">
      <p role="status" className="sr-only">
        Loading the case
      </p>
      <div className="space-y-3">
        {box("w-32", "h-4")}
        {box("w-full max-w-md", "h-10")}
        <div className="flex gap-2">
          {box("w-28", "h-6")}
          {box("w-32", "h-6")}
          {box("w-40", "h-6")}
        </div>
      </div>
      <Panel padding="md">
        <div className="grid gap-6 lg:grid-cols-[auto_minmax(0,1fr)_14rem]">
          {box("mx-auto w-60 sm:w-72 lg:mx-0", "h-36")}
          <div className="space-y-3">
            {box("w-48", "h-4")}
            {box("w-64 max-w-full", "h-8")}
            <SkeletonText lines={3} />
            {box("w-full max-w-sm", "h-3")}
          </div>
          <div className="space-y-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-11" />
          </div>
        </div>
      </Panel>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,58fr)_minmax(0,42fr)]">
        <Skeleton className="h-96" />
        <div className="space-y-3">
          <Skeleton className="h-11" />
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
        </div>
      </div>
    </div>
  );
}
