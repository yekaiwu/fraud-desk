"use client";

import { FilePlus2, ShieldAlert } from "lucide-react";
import { useId, useRef, useState, type Ref } from "react";
import useSWR from "swr";
import { ReviewFields } from "../case/ReviewFields";
import { ActionError, ErrorState } from "../StateViews";
import { Badge, Button, EmptyState, Field, Notice, Panel, inputClasses, type BadgeVariant } from "../ui";
import { acknowledgeIncident, getIncidents, reportIncident } from "@/lib/api";
import { whenText, type Incident, type IncidentSeverity } from "@/lib/monitoring";
import { validateReview } from "@/lib/reviewValidation";
import { useActingName } from "@/lib/useActingName";
import { REFRESH_MS } from "./AlertCards";
import { LoadingBlock } from "./LoadingBlock";
import { useRefreshOn } from "./useRefreshOn";

const SEVERITY: Record<IncidentSeverity, { label: string; variant: BadgeVariant }> = {
  low: { label: "Low", variant: "info" },
  medium: { label: "Medium", variant: "medium" },
  high: { label: "High", variant: "high" },
};
export const INCIDENTS_KEY = "monitoring-incidents";
const SUMMARY_MIN = 10;
const SUMMARY_MAX = 500;

function ReportForm({ onCancel, onDone }: { onCancel: () => void; onDone: (id: number) => void }) {
  const { signedIn, setTyped, remember, who } = useActingName();
  const [severity, setSeverity] = useState<IncidentSeverity>("medium");
  const [summary, setSummary] = useState("");
  const [caseId, setCaseId] = useState("");
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const reviewerRef = useRef<HTMLInputElement>(null);
  const summaryRef = useRef<HTMLTextAreaElement>(null);
  const sevId = useId();
  const sumId = useId();
  const nameErr = validateReview({ reviewer: who, reason: "ok-enough" }).reviewer;
  const sumLen = summary.trim().length;
  const sumErr = sumLen < SUMMARY_MIN ? `Describe what happened in at least ${SUMMARY_MIN} characters.` : sumLen > SUMMARY_MAX ? `At most ${SUMMARY_MAX} characters.` : undefined;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTried(true);
    if (nameErr) return void reviewerRef.current?.focus();
    if (sumErr) return void summaryRef.current?.focus();
    setBusy(true);
    setError(null);
    try {
      const res = await reportIncident({ severity, summary: summary.trim(), case_id: caseId.trim() || undefined, reviewer: who });
      if (!signedIn) remember(who);
      onDone(res.id);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label="Report an AI incident" className="mb-4 space-y-4 rounded-md border border-rule bg-paper-2/60 p-3 sm:p-4">
      <p className="text-sm text-ink-2">
        Report anything the AI got wrong in a way that could have mattered, or a control that failed. Do not paste customer details here: the summary is kept in the audit log, and a medium or high report is announced on this page without it.
      </p>
      <div>
        <label htmlFor={sevId} className="block text-sm font-semibold text-ink">Severity</label>
        <select id={sevId} className={`${inputClasses} mt-1`} value={severity} onChange={(e) => setSeverity(e.target.value as IncidentSeverity)}>
          <option value="low">Low: noticed, no effect on a decision</option>
          <option value="medium">Medium: could have changed a decision</option>
          <option value="high">High: a decision, data or a control was affected</option>
        </select>
      </div>
      <div>
        <label htmlFor={sumId} className="block text-sm font-semibold text-ink">Summary (required, at least {SUMMARY_MIN} characters)</label>
        <textarea
          id={sumId}
          ref={summaryRef}
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          rows={3}
          aria-invalid={tried && !!sumErr}
          aria-describedby={tried && sumErr ? `${sumId}-err` : undefined}
          className={`${inputClasses} mt-1 block h-auto min-h-24 py-2 leading-relaxed`}
        />
        {tried && sumErr ? (
          <p id={`${sumId}-err`} className="mt-1.5 text-sm font-medium text-sev-high">{sumErr}</p>
        ) : null}
      </div>
      <Field label="Case reference (optional)" value={caseId} onChange={(e) => setCaseId(e.target.value)} maxLength={64} autoComplete="off" />
      <ReportName who={who} signedIn={signedIn} onChange={setTyped} error={tried ? nameErr : undefined} inputRef={reviewerRef} />
      {error ? <ActionError error={error} /> : null}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="submit" loading={busy} className="w-full sm:w-auto">Report incident</Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy} className="w-full sm:w-auto">Cancel</Button>
      </div>
    </form>
  );
}

function ReportName({ who, signedIn, onChange, error, inputRef }: { who: string; signedIn: string | null; onChange: (v: string) => void; error?: string; inputRef: Ref<HTMLInputElement> }) {
  return signedIn ? (
    <p className="text-sm text-muted">Signed in as {signedIn}. This name goes in the audit log.</p>
  ) : (
    <Field label="Your name (required)" value={who} onChange={(e) => onChange(e.target.value)} maxLength={64} autoComplete="off" error={error} ref={inputRef} />
  );
}

function AckForm({ incident, onCancel, onDone }: { incident: Incident; onCancel: () => void; onDone: () => void }) {
  const { signedIn, setTyped, remember, who } = useActingName();
  const [note, setNote] = useState("");
  const [assignee, setAssignee] = useState("");
  const [tried, setTried] = useState(false);
  const [touched, setTouched] = useState<{ reviewer?: boolean; reason?: boolean }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const reviewerRef = useRef<HTMLInputElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const all = validateReview({ reviewer: who, reason: note });
  const errors = { reviewer: tried || touched.reviewer ? all.reviewer : undefined, reason: tried || touched.reason ? all.reason : undefined };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setTried(true);
    if (all.reviewer) return void reviewerRef.current?.focus();
    if (all.reason) return void noteRef.current?.focus();
    setBusy(true);
    setError(null);
    try {
      await acknowledgeIncident(incident.id, { note: note.trim(), assigned_to: assignee.trim() || undefined, reviewer: who });
      if (!signedIn) remember(who);
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-label={`Acknowledge incident ${incident.id}`} className="mt-3 space-y-4 rounded-md border border-rule bg-paper-2/60 p-3 sm:p-4">
      <p className="text-sm text-ink-2">Records that you have seen this incident, and optionally who it is assigned to. The incident stays open until a report says it is resolved.</p>
      <ReviewFields
        reviewer={who}
        reason={note}
        onReviewer={setTyped}
        onReason={setNote}
        errors={errors}
        onBlurReviewer={() => setTouched((t) => ({ ...t, reviewer: true }))}
        onBlurReason={() => setTouched((t) => ({ ...t, reason: true }))}
        reviewerRef={reviewerRef}
        reasonRef={noteRef}
        reasonLabel="Note (required, at least 5 characters)"
        reasonRows={2}
        reasonMax={500}
      />
      <Field label="Assigned to (optional)" value={assignee} onChange={(e) => setAssignee(e.target.value)} maxLength={80} autoComplete="off" />
      {error ? <ActionError error={error} /> : null}
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="submit" loading={busy} className="w-full sm:w-auto">Record acknowledgement</Button>
        <Button variant="secondary" onClick={onCancel} disabled={busy} className="w-full sm:w-auto">Cancel</Button>
      </div>
    </form>
  );
}

function IncidentRow({ incident, onChanged }: { incident: Incident; onChanged: () => void }) {
  const [asking, setAsking] = useState(false);
  const sev = SEVERITY[incident.severity];
  const open = incident.status === "open";
  return (
    <li className="px-3 py-3 sm:px-4">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <span className="num text-sm font-semibold text-ink">#{incident.id}</span>
        <Badge variant={sev.variant}>{sev.label}</Badge>
        <Badge variant={open ? "attention" : "low"} icon={null}>{open ? "Open" : "Resolved"}</Badge>
        <span className="text-xs text-muted">Reported {whenText(incident.reported_at)} by {incident.reported_by}</span>
      </div>
      <p className="mt-1.5 whitespace-pre-line text-[0.9375rem] text-ink">{incident.summary}</p>
      {incident.case_ref ? <p className="mt-0.5 text-sm text-ink-2">Case: <code>{incident.case_ref}</code></p> : null}
      {incident.acknowledged_by ? (
        <p className="mt-1 text-sm text-ink-2">
          Seen by {incident.acknowledged_by} on {whenText(incident.acknowledged_at ?? null)}
          {incident.assigned_to ? `, assigned to ${incident.assigned_to}` : ""}
          {incident.ack_note ? `: ${incident.ack_note}` : "."}
        </p>
      ) : open ? (
        <p className="mt-1 text-sm text-muted">Nobody has acknowledged this yet.</p>
      ) : null}
      {open && !asking ? (
        <Button variant="secondary" size="sm" className="mt-2" onClick={() => setAsking(true)}>
          {incident.acknowledged_by ? "Add a note" : "Acknowledge"}
          <span className="sr-only"> incident {incident.id}</span>
        </Button>
      ) : null}
      {asking ? <AckForm incident={incident} onCancel={() => setAsking(false)} onDone={() => (setAsking(false), onChanged())} /> : null}
    </li>
  );
}

/** The AI incident log: read it, report one, say you have seen one. */
export function Incidents({ onChanged, tick = 0 }: { onChanged: () => void; tick?: number }) {
  const { data, error, isLoading, mutate } = useSWR(INCIDENTS_KEY, getIncidents, { refreshInterval: REFRESH_MS });
  useRefreshOn(tick, mutate);
  const [reporting, setReporting] = useState(false);
  const [done, setDone] = useState<number | null>(null);
  const list = (data?.incidents ?? []).filter((i) => i.status !== "resolution");
  const openCount = list.filter((i) => i.status === "open").length;

  function changed() {
    void mutate();
    onChanged();
  }

  return (
    <Panel
      title="AI incidents"
      eyebrow={data ? `${openCount} open` : undefined}
      actions={
        !reporting ? (
          <Button variant="secondary" size="sm" icon={<FilePlus2 aria-hidden className="h-4 w-4" />} onClick={() => (setReporting(true), setDone(null))}>
            Report an incident
          </Button>
        ) : undefined
      }
    >
      <p className="mb-4 max-w-prose text-[0.9375rem] text-ink-2">
        An incident is anything the AI did that was wrong in a way that could have mattered. Reports go in the permanent audit log under your name.
      </p>
      <div aria-live="polite">
        {done !== null ? (
          <Notice variant="success" title={`Incident #${done} reported`} onDismiss={() => setDone(null)} className="mb-4">
            It is in the list below. A medium or high report also appears in the notifications.
          </Notice>
        ) : null}
      </div>
      {reporting ? <ReportForm onCancel={() => setReporting(false)} onDone={(id) => (setReporting(false), setDone(id), changed())} /> : null}
      {isLoading && !data ? <LoadingBlock label="Loading incidents" /> : null}
      {error && !data ? <ErrorState error={error} onRetry={() => void mutate()} /> : null}
      {data && list.length === 0 ? (
        <EmptyState compact icon={<ShieldAlert aria-hidden className="h-6 w-6" />} title="No incidents reported" text="If the AI gets something wrong, report it here so it is investigated and kept on record." />
      ) : null}
      {list.length > 0 ? (
        <ul aria-label="Incidents, newest first" className="divide-y divide-rule rounded-md border border-rule bg-surface">
          {list.map((i) => (
            <IncidentRow key={i.id} incident={i} onChanged={changed} />
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
