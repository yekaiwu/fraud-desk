"use client";

import { forwardRef } from "react";
import { ApiErrorException, explainError } from "@/lib/errors";

/** A failed review call. high_flags_open lists the blocking flags as buttons; stale_version offers a reload. */
export const ReviewError = forwardRef<HTMLDivElement, { error: unknown; onSelectFlag?: (id: string) => void; onReload?: () => void; flagLabel?: (id: string) => string }>(
  function ReviewError({ error, onSelectFlag, onReload, flagLabel }, ref) {
    const body = error instanceof ApiErrorException ? error.error : { code: "internal_error", message: error instanceof Error ? error.message : "Unexpected error." };
    const { title, detail } = explainError(body);
    const ids = Array.isArray(body.details?.flag_ids) ? (body.details.flag_ids as unknown[]).filter((x): x is string => typeof x === "string") : [];
    const requestId = error instanceof ApiErrorException ? error.requestId : null;
    return (
      <div ref={ref} tabIndex={-1} role="alert" className="rounded border border-sev-high/40 bg-red-50 px-3 py-2 text-sm">
        <p className="font-semibold text-sev-high">{title}</p>
        {detail ? <p className="mt-0.5">{detail}</p> : null}
        {body.code === "high_flags_open" && ids.length > 0 ? (
          <ul className="mt-2 flex flex-wrap gap-2">
            {ids.map((id) => (
              <li key={id}>
                <button type="button" onClick={() => onSelectFlag?.(id)} className="min-h-11 rounded border border-sev-high/40 bg-white px-3 font-medium text-sev-high underline underline-offset-2">
                  {flagLabel ? flagLabel(id) : id}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {body.code === "stale_version" && onReload ? (
          <button type="button" onClick={onReload} className="mt-2 min-h-11 rounded border border-border bg-white px-4 font-medium">
            This case changed. Reload
          </button>
        ) : null}
        {requestId ? <p className="mt-1 text-xs text-muted">Reference: {requestId}</p> : null}
      </div>
    );
  },
);
