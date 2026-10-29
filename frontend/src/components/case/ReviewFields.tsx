"use client";

import clsx from "clsx";
import { CheckCircle2, Circle } from "lucide-react";
import { useId } from "react";
import { REASON_MAX, REASON_MIN } from "@/lib/reviewValidation";
import { inputClasses } from "../ui/Field";

/**
 * Reviewer + reason fields shared by every review form. Errors are linked with aria-describedby.
 * The reason shows a live counter and the minimum-length rule as it is met. `prompts` (optional) adds a short
 * "think about" list under the label; `reasonRows` sizes the textarea. Both are additive.
 */
export function ReviewFields({
  reviewer,
  reason,
  onReviewer,
  onReason,
  errors,
  onBlurReviewer,
  onBlurReason,
  reasonLabel = "Reason (required, at least 5 characters)",
  reviewerRef,
  reasonRef,
  prompts,
  reasonRows = 4,
  reasonPlaceholder,
  reasonMax = REASON_MAX,
}: {
  reviewer: string;
  reason: string;
  onReviewer: (v: string) => void;
  onReason: (v: string) => void;
  errors: { reviewer?: string; reason?: string };
  onBlurReviewer: () => void;
  onBlurReason: () => void;
  reasonLabel?: string;
  reviewerRef?: React.Ref<HTMLInputElement>;
  reasonRef?: React.Ref<HTMLTextAreaElement>;
  prompts?: string[];
  reasonRows?: number;
  reasonPlaceholder?: string;
  /** Upper limit shown in the counter; defaults to the review reason limit. */
  reasonMax?: number;
}) {
  const uid = useId();
  const rid = `${uid}-reviewer`;
  const tid = `${uid}-reason`;
  const used = reason.trim().length;
  const metMin = used >= REASON_MIN;
  const over = reason.length > reasonMax;
  return (
    <div className="space-y-4">
      <div>
        <label htmlFor={rid} className="block text-sm font-semibold text-ink">
          Reviewer name (required)
        </label>
        <input
          id={rid}
          ref={reviewerRef}
          value={reviewer}
          onChange={(e) => onReviewer(e.target.value)}
          onBlur={onBlurReviewer}
          autoComplete="off"
          maxLength={120}
          aria-invalid={!!errors.reviewer}
          aria-describedby={errors.reviewer ? `${rid}-err` : undefined}
          className={clsx(inputClasses, "mt-1")}
        />
        {errors.reviewer ? (
          <p id={`${rid}-err`} className="mt-1.5 text-sm font-medium text-sev-high">
            {errors.reviewer}
          </p>
        ) : null}
      </div>
      <div>
        <label htmlFor={tid} className="block text-sm font-semibold text-ink">
          {reasonLabel}
        </label>
        {prompts && prompts.length > 0 ? (
          <ul id={`${tid}-prompts`} className="mt-1 space-y-0.5 text-sm text-muted">
            {prompts.map((p) => (
              <li key={p} className="flex gap-2">
                <span aria-hidden className="mt-[0.55em] h-1 w-1 shrink-0 rounded-full bg-rule-strong" />
                {p}
              </li>
            ))}
          </ul>
        ) : null}
        <textarea
          id={tid}
          ref={reasonRef}
          value={reason}
          onChange={(e) => onReason(e.target.value)}
          onBlur={onBlurReason}
          rows={reasonRows}
          placeholder={reasonPlaceholder}
          aria-invalid={!!errors.reason}
          aria-describedby={`${tid}-count${errors.reason ? ` ${tid}-err` : ""}${prompts?.length ? ` ${tid}-prompts` : ""}`}
          className={clsx(inputClasses, "mt-1.5 block h-auto min-h-24 py-2 leading-relaxed")}
        />
        <div className="mt-1.5 flex items-start justify-between gap-3 text-sm">
          <span id={`${tid}-err`} className="font-medium text-sev-high">
            {errors.reason}
          </span>
          <span id={`${tid}-count`} className={clsx("tabular flex shrink-0 items-center gap-1.5", over ? "font-medium text-sev-high" : "text-muted")}>
            {metMin ? <CheckCircle2 aria-hidden className="h-3.5 w-3.5 text-sev-low" /> : <Circle aria-hidden className="h-3.5 w-3.5" />}
            <span className="sr-only">{metMin ? "Minimum length met. " : `At least ${REASON_MIN} characters needed. `}</span>
            {metMin ? null : <span aria-hidden>At least {REASON_MIN} characters ·</span>}
            <span>
              {reason.length}/{reasonMax}
            </span>
          </span>
        </div>
      </div>
    </div>
  );
}
