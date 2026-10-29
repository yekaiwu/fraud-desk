"use client";

import { AlertOctagon } from "lucide-react";
import { forwardRef } from "react";
import { ApiErrorException, explainError } from "@/lib/errors";
import { Button } from "../ui/Button";

/**
 * A failed review call, in the Ledger style. Same behaviour as ReviewError: it is the alert (and takes focus), high_flags_open
 * lists the blocking flags as buttons that select them, stale_version offers a reload, a 5xx shows the request reference.
 */
export const DecisionError = forwardRef<HTMLDivElement, { error: unknown; onSelectFlag?: (id: string) => void; onReload?: () => void; flagLabel?: (id: string) => string }>(
  function DecisionError({ error, onSelectFlag, onReload, flagLabel }, ref) {
    const body = error instanceof ApiErrorException ? error.error : { code: "internal_error", message: error instanceof Error ? error.message : "Unexpected error." };
    const { title, detail } = explainError(body);
    const ids = Array.isArray(body.details?.flag_ids) ? (body.details.flag_ids as unknown[]).filter((x): x is string => typeof x === "string") : [];
    const requestId = error instanceof ApiErrorException ? error.requestId : null;
    return (
      <div
        ref={ref}
        tabIndex={-1}
        role="alert"
        data-code={body.code}
        className="flex gap-3 rounded-md border border-l-[3px] border-sev-high/30 border-l-sev-high bg-sev-high-tint px-3.5 py-3 text-ink focus:outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sev-high"
      >
        <AlertOctagon aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-sev-high" />
        <div className="min-w-0 flex-1 text-[0.9375rem]">
          <p className="font-semibold">{title}</p>
          {detail ? <p className="mt-0.5 text-ink-2">{detail}</p> : null}
          {body.code === "high_flags_open" && ids.length > 0 ? (
            <ul className="mt-2.5 flex flex-wrap gap-2">
              {ids.map((id) => (
                <li key={id}>
                  <Button size="sm" variant="secondary" onClick={() => onSelectFlag?.(id)}>
                    {flagLabel ? flagLabel(id) : id}
                  </Button>
                </li>
              ))}
            </ul>
          ) : null}
          {body.code === "stale_version" && onReload ? (
            <Button size="sm" variant="secondary" className="mt-2.5" onClick={onReload}>
              This case changed. Reload
            </Button>
          ) : null}
          {requestId ? <p className="mt-1 text-xs text-muted">Reference: {requestId}</p> : null}
        </div>
      </div>
    );
  },
);
