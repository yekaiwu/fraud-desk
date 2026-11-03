"use client";

import { Bell, CheckCircle2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import useSWR from "swr";
import { Badge, Button, EmptyState, Notice, Panel } from "../ui";
import { ActionError, ErrorState } from "../StateViews";
import { LoadingBlock } from "./LoadingBlock";
import { useRefreshOn } from "./useRefreshOn";
import { ReviewFields } from "../case/ReviewFields";
import { acknowledgeAlert, getAlerts } from "@/lib/api";
import { metricLabel, valueVersusLimit, whenText, type AlertState, type AlertsResponse } from "@/lib/monitoring";
import { validateReview } from "@/lib/reviewValidation";
import { useActingName } from "@/lib/useActingName";

export const ALERTS_KEY = "monitoring-alerts";
export const REFRESH_MS = 60_000;

/** The alert state, refreshed about once a minute. Shared by the open alerts and the status panel. */
export function useAlerts() {
  return useSWR<AlertsResponse>(ALERTS_KEY, getAlerts, { refreshInterval: REFRESH_MS });
}

function AcknowledgeForm({ alert, onCancel, onDone }: { alert: AlertState; onCancel: () => void; onDone: (a: AlertState) => void }) {
  const { signedIn, setTyped, remember, who } = useActingName();
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const [touched, setTouched] = useState<{ reviewer?: boolean; reason?: boolean }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const reviewerRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const all = validateReview({ reviewer: who, reason });
  const errors = { reviewer: tried || touched.reviewer ? all.reviewer : undefined, reason: tried || touched.reason ? all.reason : undefined };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTried(true);
    if (all.reviewer) return void reviewerRef.current?.focus();
    if (all.reason) return void reasonRef.current?.focus();
    setBusy(true);
    setError(null);
    try {
      const res = await acknowledgeAlert(alert.key, { reviewer: who, reason: reason.trim() });
      if (!signedIn) remember(who);
      onDone(res.alert);
    } catch (err) {
      setError(err);
      setTimeout(() => alertRef.current?.focus(), 0);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Acknowledge ${metricLabel(alert.key)}`} className="mt-4 space-y-4 rounded-md border border-rule bg-paper-2/60 p-3 sm:p-4">
      <p id={hintId} className="text-sm text-ink-2">
        Acknowledging records that you have seen this alert, and why. It does not fix anything: the alert stays on this page until the number is back within its limit.
      </p>
      <ReviewFields
        reviewer={who}
        reason={reason}
        onReviewer={setTyped}
        onReason={setReason}
        errors={errors}
        onBlurReviewer={() => setTouched((t) => ({ ...t, reviewer: true }))}
        onBlurReason={() => setTouched((t) => ({ ...t, reason: true }))}
        reviewerRef={reviewerRef}
        reasonRef={reasonRef}
        reasonLabel="Reason (required, at least 5 characters)"
        prompts={["What did you look at?", "Who is following it up, and what happens next?"]}
        reasonRows={3}
      />
      {error ? (
        <div ref={alertRef} tabIndex={-1} className="focus:outline-none">
          <ActionError error={error} />
        </div>
      ) : null}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="submit" loading={busy} className="w-full sm:w-auto" aria-describedby={hintId}>
          Record acknowledgement
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy} className="w-full sm:w-auto">
          Cancel
        </Button>
      </div>
    </form>
  );
}

function OpenAlertCard({ alert, onAcknowledged }: { alert: AlertState; onAcknowledged: (a: AlertState) => void }) {
  const [asking, setAsking] = useState(false);
  const titleId = useId();
  const formId = useId();
  return (
    <li>
      <article aria-labelledby={titleId} className="rounded-lg border border-rule border-l-[3px] border-l-brass bg-surface p-4 shadow-paper sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <div className="min-w-0">
            <h3 id={titleId} className="text-base font-semibold leading-snug text-ink">
              {metricLabel(alert.key)}
            </h3>
            <p className="mt-0.5 text-xs text-muted">
              <code>{alert.key}</code>
            </p>
          </div>
          <Badge variant="attention">Open, not acknowledged</Badge>
        </div>
        <dl className="mt-3 grid gap-x-6 gap-y-2.5 text-[0.9375rem] sm:grid-cols-2">
          <div className="sm:col-span-2">
            <dt className="label-caps">Now against the limit</dt>
            <dd className="tabular font-semibold text-ink">{valueVersusLimit(alert.key, alert.value, alert.threshold)}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="label-caps">What it means</dt>
            <dd className="text-ink-2">{alert.meaning}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="label-caps">Recommended action</dt>
            <dd className="text-ink-2">{alert.action}</dd>
          </div>
          <div>
            <dt className="label-caps">First seen</dt>
            <dd className="text-ink-2">{whenText(alert.first_seen)}</dd>
          </div>
          <div>
            <dt className="label-caps">Last seen</dt>
            <dd className="text-ink-2">{whenText(alert.last_seen)}</dd>
          </div>
        </dl>
        {asking ? (
          <div id={formId}>
            <AcknowledgeForm alert={alert} onCancel={() => setAsking(false)} onDone={(a) => (setAsking(false), onAcknowledged(a))} />
          </div>
        ) : (
          <div className="mt-4">
            <Button variant="secondary" icon={<CheckCircle2 aria-hidden className="h-4 w-4" />} onClick={() => setAsking(true)} aria-expanded={false} aria-controls={formId} className="w-full sm:w-auto">
              Acknowledge
              <span className="sr-only"> {metricLabel(alert.key)}</span>
            </Button>
          </div>
        )}
      </article>
    </li>
  );
}

/** Alerts nobody has acknowledged, with what to do about each; below them, the ones someone has seen. */
export function OpenAlerts({ onChanged, tick = 0 }: { onChanged: () => void; tick?: number }) {
  const { data, error, isLoading, mutate } = useAlerts();
  useRefreshOn(tick, mutate);
  const [done, setDone] = useState<AlertState | null>(null);
  const open = data?.alerts.filter((a) => a.status === "open") ?? [];
  const seen = data?.alerts.filter((a) => a.status === "acknowledged") ?? [];
  const off = data?.scheduler === "off";

  return (
    <Panel title="Open alerts" eyebrow={data ? `${open.length} not acknowledged` : undefined} tone={open.length > 0 ? "attention" : "default"} headingLevel={2}>
      <p className="mb-4 max-w-prose text-[0.9375rem] text-ink-2">An alert means a number went past its limit. It raises attention only; a person looks at the cases and chooses what to do.</p>
      <div aria-live="polite">
        {done ? (
          <Notice variant="success" title={`Acknowledged: ${metricLabel(done.key)}`} onDismiss={() => setDone(null)} className="mb-4">
            Recorded under {done.acknowledged_by}. It stays listed below until the number is back within its limit.
          </Notice>
        ) : null}
      </div>
      {isLoading && !data ? (
        <LoadingBlock label="Loading open alerts" className="h-40" />
      ) : null}
      {error && !data ? <ErrorState error={error} onRetry={() => void mutate()} /> : null}
      {data ? (
        <>
          {open.length === 0 ? (
            <EmptyState
              compact
              icon={<Bell aria-hidden className="h-6 w-6" />}
              title="No open alerts"
              text={off ? "The monitor is off, so nothing is checking the limits. No alerts here does not mean all is well." : "Every number checked is within its limit, or has been acknowledged."}
            />
          ) : (
            <ul className="space-y-4">
              {open.map((a) => (
                <OpenAlertCard
                  key={a.key}
                  alert={a}
                  onAcknowledged={(acked) => {
                    setDone(acked);
                    void mutate();
                    onChanged();
                  }}
                />
              ))}
            </ul>
          )}
          {seen.length > 0 ? (
            <section aria-label="Acknowledged alerts" className="mt-5">
              <h3 className="label-caps mb-2">Acknowledged, still above the limit ({seen.length})</h3>
              <ul className="divide-y divide-rule rounded-md border border-rule">
                {seen.map((a) => (
                  <li key={a.key} className="px-3 py-2.5 text-[0.9375rem] sm:px-4">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-4">
                      <span className="font-semibold text-ink">{metricLabel(a.key)}</span>
                      <span className="tabular text-sm text-ink-2">{valueVersusLimit(a.key, a.value, a.threshold)}</span>
                    </div>
                    <p className="mt-0.5 text-sm text-ink-2">
                      Seen by {a.acknowledged_by} on {whenText(a.acknowledged_at)}: <span className="whitespace-pre-line">{a.note}</span>
                    </p>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </Panel>
  );
}

