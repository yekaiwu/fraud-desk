"use client";

import { Inbox, Send } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { listCustomTickets, submitCustomTicket } from "@/lib/api";
import { CUSTOM_SEVERITIES, ORIGIN_TEXT, REQUEST_MAX_CHARS, REQUEST_MIN_CHARS, TICKET_STATUS_TEXT, UNSUPPORTED_TEXT } from "@/lib/customRules";
import { formatUtc } from "@/lib/format";
import { severityLabel } from "@/lib/labels";
import type { CustomSeverity, CustomTicket } from "@/lib/types";
import { ErrorState } from "../StateViews";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { inputClasses } from "../ui/Field";
import { Notice } from "../ui/Notice";
import { Skeleton } from "../ui/Skeleton";
import { CustomError } from "./CustomError";

export const TICKETS_KEY = "custom-rule-tickets";

function TicketCard({ t, showAccount }: { t: CustomTicket; showAccount: boolean }) {
  return (
    <li className="rounded-lg border border-rule bg-surface p-4 shadow-paper">
      <div className="flex flex-wrap items-center gap-2">
        <span className="num text-sm font-semibold text-ink">{t.id}</span>
        <Badge variant={t.status === "declined" ? "status" : t.status === "done" ? "low" : "attention"} icon={null}>
          {TICKET_STATUS_TEXT[t.status] ?? t.status}
        </Badge>
        <Badge variant="neutral" icon={null}>
          {ORIGIN_TEXT[t.origin] ?? t.origin}
        </Badge>
        {t.desired_severity ? (
          <Badge variant="neutral" icon={null}>
            Wanted: {severityLabel(t.desired_severity)}
          </Badge>
        ) : null}
      </div>
      <p className="mt-1.5 break-words text-[0.9375rem] text-ink">{t.intent}</p>
      {t.missing.length > 0 ? (
        <ul aria-label="What could not be built" className="mt-1.5 list-disc space-y-0.5 pl-5 text-sm text-ink-2">
          {t.missing.map((m, i) => (
            <li key={i} className="break-words">
              &ldquo;{m.phrase}&rdquo;: {UNSUPPORTED_TEXT[m.reason] ?? m.reason}
            </li>
          ))}
        </ul>
      ) : null}
      <details className="mt-1.5 text-sm text-ink-2">
        <summary className="cursor-pointer font-medium text-brand-2 underline underline-offset-2">Your original words</summary>
        <p className="mt-1 whitespace-pre-wrap break-words">{t.original_words}</p>
      </details>
      <p className="mt-1.5 text-sm text-muted">
        {showAccount ? `${t.account}, ` : ""}
        <time dateTime={t.created_at}>{formatUtc(t.created_at)}</time>
        {t.doc_types.length > 0 ? `, about ${t.doc_types.map((d) => d.replace(/_/g, " ")).join(", ")}` : ""}
      </p>
    </li>
  );
}

function IdeaForm({ onSent }: { onSent: () => void }) {
  const [text, setText] = useState("");
  const [severity, setSeverity] = useState<CustomSeverity | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [sent, setSent] = useState<string | null>(null);
  const ok = text.trim().length >= REQUEST_MIN_CHARS;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      const r = await submitCustomTicket({ text: text.trim(), severity: severity || undefined });
      setSent(r.message);
      setText("");
      onSent();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} aria-label="Send an idea to the tech team" className="space-y-3 rounded-lg border border-rule bg-paper-2/60 p-4">
      <h3 className="text-[1.0625rem] font-semibold text-ink">Send an idea to the tech team</h3>
      <p className="text-sm text-ink-2">For a check the rule assistant cannot build. It goes to the tech team as written; no model reads it. Do not paste case details.</p>
      <div>
        <label htmlFor="idea-text" className="mb-1 block text-sm font-semibold text-ink">
          What should be checked?
        </label>
        <textarea id="idea-text" rows={3} maxLength={REQUEST_MAX_CHARS} className={`${inputClasses} h-auto py-2`} value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      <div className="max-w-xs">
        <label htmlFor="idea-severity" className="mb-1 block text-sm font-semibold text-ink">
          How serious should a flag be?
        </label>
        <select id="idea-severity" className={inputClasses} value={severity} onChange={(e) => setSeverity(e.target.value as CustomSeverity | "")}>
          <option value="">No preference</option>
          {CUSTOM_SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {severityLabel(s)}
            </option>
          ))}
        </select>
      </div>
      {error ? <CustomError error={error} /> : null}
      {sent ? (
        <Notice variant="success" title="Sent">
          {sent}
        </Notice>
      ) : null}
      <Button type="submit" icon={<Send aria-hidden className="h-4 w-4" />} loading={busy} disabled={!ok}>
        Send to the tech team
      </Button>
    </form>
  );
}

/** Requests routed to the tech team, with their status. An administrator sees every account's; others see their own. */
export function CustomTickets({ admin }: { admin: boolean }) {
  const { data, error, isLoading, mutate } = useSWR(TICKETS_KEY, listCustomTickets);
  const tickets = data?.tickets ?? [];
  return (
    <div className="space-y-5">
      <IdeaForm onSent={() => void mutate()} />
      {isLoading && !data ? (
        <div role="status" aria-busy="true">
          <span className="sr-only">Loading requests...</span>
          <Skeleton className="h-24 rounded-lg" />
        </div>
      ) : null}
      {error && !data ? <ErrorState error={error} onRetry={() => void mutate()} /> : null}
      {data && tickets.length === 0 ? (
        <EmptyState icon={<Inbox aria-hidden className="h-6 w-6" />} compact title="No requests yet" text="When an idea is too complex for a custom rule, it is sent to the tech team and listed here with its status." />
      ) : null}
      {tickets.length > 0 ? (
        <ul aria-label="Requests to the tech team" className="space-y-3">
          {tickets.map((t) => (
            <TicketCard key={t.id} t={t} showAccount={admin} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}
