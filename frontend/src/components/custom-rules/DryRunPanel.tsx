"use client";

import { FlaskConical } from "lucide-react";
import { dryRunSentence, percent } from "@/lib/customRules";
import type { DryRunResult } from "@/lib/types";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";
import { Skeleton } from "../ui/Skeleton";

function Num({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div>
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="tabular text-[1.375rem] font-semibold leading-tight text-ink">{value}</dd>
      {hint ? <dd className="text-xs text-muted">{hint}</dd> : null}
    </div>
  );
}

/**
 * What the rule would have done on cases already stored (nothing is written to any case). Warnings (fires on no case,
 * or on more than 30%) need an explicit "Save anyway" tick before the rule can be saved.
 */
export function DryRunPanel({
  result,
  loading,
  error,
  onRun,
  confirmed,
  onConfirm,
}: {
  result: DryRunResult | null;
  loading: boolean;
  error: string | null;
  onRun: () => void;
  confirmed: boolean;
  onConfirm: (v: boolean) => void;
}) {
  return (
    <section aria-labelledby="dry-run-title" className="space-y-3 rounded-lg border border-rule bg-surface p-4 shadow-paper">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 id="dry-run-title" className="flex items-center gap-2 text-[1rem] font-semibold text-ink">
          <FlaskConical aria-hidden className="h-4 w-4 text-brand-2" />
          Test on stored cases
        </h4>
        <Button size="sm" variant="secondary" loading={loading} onClick={onRun}>
          {result ? "Test again" : "Run the test"}
        </Button>
      </div>
      {loading && !result ? (
        <div role="status" aria-busy="true">
          <span className="sr-only">Testing the rule on stored cases...</span>
          <Skeleton className="h-16" />
        </div>
      ) : null}
      {error ? (
        <Notice variant="error" title="The test could not be run">
          {error}
        </Notice>
      ) : null}
      {result ? (
        <>
          <p className="text-[0.9375rem] text-ink-2">{dryRunSentence(result)} Nothing was changed on any case.</p>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Num label="Evaluated" value={result.evaluated} hint={`of ${result.cases_considered} looked at`} />
            <Num label="Fired" value={result.fired} hint={result.evaluated > 0 ? percent(result.fire_rate) : undefined} />
            <Num label="Skipped, field missing" value={result.skipped_missing} />
            <Num label="On scans or photos" value={result.scanned_evaluated} hint={`${result.scanned_fired} fired`} />
          </dl>
          {result.warnings.length > 0 ? (
            <div className="space-y-2">
              {result.warnings.map((w) => (
                <Notice key={w.code} variant="warning" title="Check this before saving">
                  {w.message}
                </Notice>
              ))}
              <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-md px-1 py-2 text-[0.9375rem] text-ink">
                <input type="checkbox" checked={confirmed} onChange={(e) => onConfirm(e.target.checked)} className="mt-1 h-4 w-4 accent-[var(--brand-2)]" />
                <span>Save anyway. I have read the warning and still want this rule.</span>
              </label>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
