"use client";

import { CircleDot, Send } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { ActionError, ErrorState } from "../StateViews";
import { Badge, Button, Notice, Panel } from "../ui";
import { getReadiness, sendTestAlert } from "@/lib/api";
import { schedulerView, sinkLabel, whenText, type Readiness, type ReadinessItem, type TestAlertResponse } from "@/lib/monitoring";
import { REFRESH_MS, useAlerts } from "./AlertCards";
import { LoadingBlock } from "./LoadingBlock";
import { useRefreshOn } from "./useRefreshOn";

/** Scheduler line (off, running, last evaluated, stale) and the test button. */
export function MonitorStatus({ onChanged, tick = 0 }: { onChanged: () => void; tick?: number }) {
  const { data, error, isLoading, mutate } = useAlerts();
  useRefreshOn(tick, mutate);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<TestAlertResponse | null>(null);
  const [failure, setFailure] = useState<unknown>(null);

  async function test() {
    setBusy(true);
    setFailure(null);
    setSent(null);
    try {
      setSent(await sendTestAlert());
      await mutate();
      onChanged();
    } catch (e) {
      setFailure(e);
    } finally {
      setBusy(false);
    }
  }

  const view = data ? schedulerView(data) : null;
  return (
    <Panel title="Is the monitor running?" headingLevel={2}>
      {isLoading && !data ? <LoadingBlock label="Loading monitor status" className="h-16" /> : null}
      {error && !data ? <ErrorState error={error} onRetry={() => void mutate()} /> : null}
      {data && view ? <SchedulerBlock view={view} /> : null}
      <div className="mt-4 border-t border-rule pt-4">
        <p className="mb-3 text-sm text-ink-2">
          A test alert is clearly labelled, changes nothing and needs no action. It shows that an alert reaches this page
          {data && data.sinks.some((s) => s !== "in-app") ? ` and the other places set up (${data.sinks.filter((s) => s !== "in-app").join(", ")})` : ""}.
        </p>
        <Button onClick={() => void test()} loading={busy} icon={<Send aria-hidden className="h-4 w-4" />} className="w-full sm:w-auto">
          Send test alert
        </Button>
        <div aria-live="polite" className="mt-3 space-y-2">
          {sent ? (
            <Notice variant={sent.delivered ? "success" : "warning"} title={sent.delivered ? "Test alert sent" : "Test alert not delivered"}>
              {sent.message ?? (sent.delivered ? `Delivered to ${sent.results.filter((r) => r.ok).map((r) => sinkLabel(r.sink)).join(", ")}. Look for it in the list.` : "No place received it.")}
            </Notice>
          ) : null}
          {failure ? <ActionError error={failure} /> : null}
        </div>
        {data?.last_test ? (
          <p className="mt-3 text-sm text-ink-2">
            Last test: {whenText(data.last_test.at)} by {data.last_test.by}.
          </p>
        ) : data ? (
          <p className="mt-3 text-sm text-ink-2">No test alert has been sent yet.</p>
        ) : null}
      </div>
    </Panel>
  );
}

function SchedulerBlock({ view }: { view: ReturnType<typeof schedulerView> }) {
  const variant = view.tone === "running" ? "low" : view.tone === "stale" ? "high" : "attention";
  return (
    <div data-scheduler={view.tone}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={variant}>{view.badge}</Badge>
      </div>
      <p className="mt-2 text-[0.9375rem] text-ink-2">{view.line}</p>
    </div>
  );
}

function itemView(i: ReadinessItem): { short: string; line: string } {
  if (i.id === "5.23(a)") {
    return i.status === "met"
      ? { short: "Named", line: `${i.value ?? "Named"}. The software cannot tell whether this is the right person; the bank confirms that.` }
      : { short: "Not named, decided by the bank", line: "Nobody is named as accountable for this AI system. The software never fills this in; the bank names the person." };
  }
  if (i.id === "5.23(b)") {
    if (i.status === "met") return { short: "Recorded", line: `Thresholds recorded as approved${i.approved_by ? ` by ${i.approved_by}${i.approver_role ? `, ${i.approver_role}` : ""}` : ""}${i.approved_at ? ` on ${whenText(i.approved_at)}` : ""}. They still match today's values.` };
    if (/changed since approval/i.test(i.detail)) return { short: "Thresholds changed since approval", line: `The limits were approved${i.approved_by ? ` by ${i.approved_by}` : ""}${i.approved_at ? ` on ${whenText(i.approved_at)}` : ""}, but the values are different now.` };
    return { short: "No approval recorded", line: "The limits are starting values, not calibrated on real cases, and nobody has recorded approving them." };
  }
  if (i.status === "met") {
    const where = (i.delivered_by ?? []).map(sinkLabel).join(", ") || "a place";
    return {
      short: i.page_only ? "Reaches this page" : "Reaches a place",
      line: `The last test reached ${where}${i.last_test_at ? ` on ${whenText(i.last_test_at)}` : ""}.${i.page_only ? " Delivery to this page is not the same as someone watching it." : ""}`,
    };
  }
  return { short: "Not shown to work yet", line: i.last_test_at ? "The last test was not delivered, is too old or the setup changed since." : "No test alert has been sent yet." };
}

/** The three 5.23 items, each with what is missing. Not alarming: an unmet item is an open item, not an error. */
export function ReadinessPanel({ tick = 0 }: { tick?: number }) {
  const { data, error, isLoading, mutate } = useSWR<Readiness>("monitoring-readiness", getReadiness, { refreshInterval: REFRESH_MS * 5 });
  useRefreshOn(tick, mutate);
  return (
    <Panel title="Readiness" eyebrow="MAS AI guideline 5.23" headingLevel={2}>
      <p className="mb-3 text-sm text-ink-2">Three things should be in place before monitoring means anything. They are decisions for the bank; this page only shows what is recorded.</p>
      {isLoading && !data ? <LoadingBlock label="Loading readiness" className="h-40" /> : null}
      {error && !data ? <ErrorState error={error} onRetry={() => void mutate()} /> : null}
      {data ? (
        <ul className="divide-y divide-rule rounded-md border border-rule">
          {data.items.map((i) => {
            const v = itemView(i);
            const met = i.status === "met";
            return (
              <li key={i.id} data-status={i.status} className="px-3 py-3 sm:px-4">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <h3 className="text-[0.9375rem] font-semibold text-ink">
                    <span className="num text-xs font-medium text-muted">{i.id}</span> {i.name}
                  </h3>
                  {met ? <Badge variant="low">Met</Badge> : <Badge variant="attention" icon={CircleDot}>Open item</Badge>}
                </div>
                <p className="mt-1 text-sm font-semibold text-ink-2">{v.short}</p>
                <p className="mt-0.5 text-sm text-ink-2">{v.line}</p>
                {i.missing.length > 0 ? (
                  <div className="mt-2">
                    <p className="label-caps">What is missing</p>
                    <ul className="mt-0.5 list-disc space-y-0.5 pl-5 text-sm text-ink-2">
                      {i.missing.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      {data ? <p className="mt-3 text-xs text-muted">{data.statement}</p> : null}
    </Panel>
  );
}
