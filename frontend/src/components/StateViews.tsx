"use client";

import { AlertOctagon, Loader2, RotateCw } from "lucide-react";
import { ApiErrorException, explainError, type ApiErrorBody } from "@/lib/errors";
import { Button } from "./ui/Button";
import { EmptyState } from "./ui/EmptyState";

/** Page-level loading line: spinner plus words, announced politely. */
export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-busy="true" className="flex items-center gap-2.5 py-8 text-ink-2">
      <Loader2 aria-hidden className="h-5 w-5 animate-spin text-brand-2" />
      <span>{label}...</span>
    </div>
  );
}

/** "Nothing here", with an optional link to the next step. Same props as ever; restyled as paper with a seal. */
export function Empty({ title, children, href, linkLabel, icon }: { title: string; children?: React.ReactNode; href?: string; linkLabel?: string; icon?: React.ReactNode }) {
  return <EmptyState icon={icon} title={title} text={children} action={href && linkLabel ? <Button href={href}>{linkLabel}</Button> : undefined} />;
}

/** Renders any thrown value through explainError (frontend.md 7.2). Retry only when retryable. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const body: ApiErrorBody =
    error instanceof ApiErrorException
      ? error.error
      : { code: "internal_error", message: error instanceof Error ? error.message : "Unexpected error." };
  const { title, detail, retryable } = explainError(body);
  const requestId = error instanceof ApiErrorException ? error.requestId : null;
  return (
    <div role="alert" className="flex gap-3 rounded-lg border border-sev-high/30 border-l-[3px] border-l-sev-high bg-sev-high-tint px-4 py-3.5 text-ink">
      <AlertOctagon aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-sev-high" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-sev-high">{title}</p>
        {detail ? <p className="mt-0.5 text-[0.9375rem] text-ink-2">{detail}</p> : null}
        {requestId ? <p className="num mt-1 text-xs text-muted">Reference: {requestId}</p> : null}
        {retryable && onRetry ? (
          <Button variant="secondary" size="sm" className="mt-3" icon={<RotateCw aria-hidden className="h-4 w-4" />} onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </div>
    </div>
  );
}
