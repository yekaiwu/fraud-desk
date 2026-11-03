"use client";

import { BellRing, Check, CheckCheck } from "lucide-react";
import { useId, useState } from "react";
import useSWRInfinite from "swr/infinite";
import { ActionError, ErrorState } from "../StateViews";
import { Badge, Button, EmptyState, Field, Panel, TabPanel, Tabs, type BadgeVariant } from "../ui";
import { getNotifications, markNotificationsRead } from "@/lib/api";
import {
  CATEGORY_LABEL,
  metricLabel,
  whenText,
  type AppNotification,
  type NotificationCategory,
  type NotificationsPage,
} from "@/lib/monitoring";
import { REVIEWER_MAX, REVIEWER_MIN } from "@/lib/reviewValidation";
import { useActingName } from "@/lib/useActingName";
import { REFRESH_MS } from "./AlertCards";
import { LoadingBlock } from "./LoadingBlock";
import { useRefreshOn } from "./useRefreshOn";

type Filter = NotificationCategory | "all";
const FILTERS: Filter[] = ["all", "alerts", "incidents", "system"];
const GROUP = "notif-feed";

const KIND_LABEL: Record<AppNotification["kind"], { label: string; variant: BadgeVariant }> = {
  alert: { label: "Alert", variant: "attention" },
  reminder: { label: "Reminder", variant: "attention" },
  resolved: { label: "Back to normal", variant: "low" },
  incident: { label: "Incident", variant: "medium" },
  test: { label: "Test", variant: "neutral" },
};

/** The headline: the plain name of the metric for alert messages, the server's title otherwise. */
export function headline(n: AppNotification): string {
  if (n.kind === "alert") return `Alert raised: ${metricLabel(n.key)}`;
  if (n.kind === "reminder") return `Still open: ${metricLabel(n.key)}`;
  if (n.kind === "resolved") return `Back within the limit: ${metricLabel(n.key)}`;
  return n.title;
}

function Item({ n, asOfMs, onRead, busy }: { n: AppNotification; asOfMs: number; onRead: (id: number) => void; busy: boolean }) {
  const titleId = useId();
  const kind = KIND_LABEL[n.kind];
  return (
    <li>
      <article aria-labelledby={titleId} data-unread={n.read ? undefined : "true"} className={n.read ? "px-3 py-3 sm:px-4" : "border-l-[3px] border-l-brass bg-brass-tint/30 px-3 py-3 sm:px-4"}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          {n.read ? null : <Badge variant="attention" icon={null}>Unread</Badge>}
          <Badge variant={kind.variant} icon={null}>{kind.label}</Badge>
          {n.severity === "high" ? <Badge variant="high">High severity</Badge> : null}
          <time dateTime={n.ts} className="tabular text-xs text-muted">
            {whenText(n.ts, asOfMs)}
          </time>
        </div>
        <h3 id={titleId} className="mt-1.5 text-[0.9375rem] font-semibold leading-snug text-ink">
          {headline(n)}
        </h3>
        <p className="mt-0.5 whitespace-pre-line text-sm text-ink-2">{n.text}</p>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
          {n.read ? (
            <span>Read by {n.read_by} {n.read_at ? `on ${whenText(n.read_at, asOfMs)}` : ""}</span>
          ) : (
            <Button variant="ghost" size="sm" icon={<Check aria-hidden className="h-4 w-4" />} disabled={busy} onClick={() => onRead(n.id)} className="-ml-3">
              Mark as read<span className="sr-only">: {headline(n)}</span>
            </Button>
          )}
        </div>
      </article>
    </li>
  );
}

/** Asks for a name only when one is needed to record who read a notification (a signed-in account is never asked). */
function NameAsk({ value, onChange, onSubmit, onCancel, error }: { value: string; onChange: (v: string) => void; onSubmit: () => void; onCancel: () => void; error?: string }) {
  return (
    <form
      onSubmit={(e) => (e.preventDefault(), onSubmit())}
      noValidate
      aria-label="Your name for the read mark"
      className="mb-4 flex flex-col gap-3 rounded-md border border-rule bg-paper-2/60 p-3 sm:flex-row sm:items-end"
    >
      <Field label="Your name" hint="Recorded with the read mark, so the team can see who has looked." value={value} onChange={(e) => onChange(e.target.value)} maxLength={REVIEWER_MAX} autoComplete="off" error={error} className="sm:flex-1" />
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="submit" className="w-full sm:w-auto">Mark as read</Button>
        <Button variant="secondary" onClick={onCancel} className="w-full sm:w-auto">Cancel</Button>
      </div>
    </form>
  );
}

export function Feed({ onChanged, tick = 0 }: { onChanged: () => void; tick?: number }) {
  const [filter, setFilter] = useState<Filter>("all");
  const { signedIn, typed, setTyped, remember, who, valid } = useActingName();
  const [pending, setPending] = useState<{ ids: number[] } | { all: true } | null>(null);
  const [nameError, setNameError] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  const { data, error, isLoading, isValidating, size, setSize, mutate } = useSWRInfinite<NotificationsPage>(
    (index, prev) => {
      if (index > 0 && !prev?.next_before) return null;
      return ["notifications", filter, index === 0 ? null : prev!.next_before] as [string, Filter, number | null];
    },
    ([, cat, before]: [string, Filter, number | null]) => getNotifications({ category: cat === "all" ? undefined : cat, before: before || undefined, limit: 20 }),
    { refreshInterval: REFRESH_MS },
  );

  useRefreshOn(tick, mutate);
  const pages = data ?? [];
  const items = pages.flatMap((p) => p.notifications);
  const unread = pages[0]?.unread ?? 0;
  const asOfMs = pages[0] ? Date.parse(pages[0].as_of) : 0;
  const more = pages.length > 0 && pages[pages.length - 1].next_before !== null;

  async function run(target: { ids: number[] } | { all: true }) {
    setBusy(true);
    setFailure(null);
    try {
      await markNotificationsRead({ ...target, reviewer: who });
      if (!signedIn) remember(who);
      setPending(null);
      await mutate();
      onChanged();
    } catch (e) {
      setFailure(e);
    } finally {
      setBusy(false);
    }
  }

  function request(target: { ids: number[] } | { all: true }) {
    setFailure(null);
    if (!valid) {
      setPending(target);
      setNameError(undefined);
      return;
    }
    void run(target);
  }

  function submitName() {
    if (!valid) return void setNameError(`Enter a name of ${REVIEWER_MIN} to ${REVIEWER_MAX} characters.`);
    if (pending) void run(pending);
  }

  return (
    <Panel
      title="Recent notifications"
      eyebrow={data ? (unread > 0 ? `${unread} unread` : "All read") : undefined}
      actions={
        <Button variant="secondary" size="sm" icon={<CheckCheck aria-hidden className="h-4 w-4" />} disabled={busy || unread === 0} onClick={() => request({ all: true })}>
          Mark all as read
        </Button>
      }
      padding="sm"
    >
      <p className="mb-3 max-w-prose px-1 text-sm text-ink-2">
        What the monitor has told this page, newest first: alerts raised and cleared, incidents, tests and the monitor going quiet. Delivery to this page is not the same as someone reading it, which is why each entry can be marked as read.
      </p>
      <Tabs
        group={GROUP}
        label="Filter notifications"
        variant="pill"
        value={filter}
        onValueChange={(id) => setFilter(id as Filter)}
        tabs={FILTERS.map((f) => ({ id: f, label: CATEGORY_LABEL[f] }))}
        className="mb-3"
      />
      {pending ? <NameAsk value={typed} onChange={setTyped} onSubmit={submitName} onCancel={() => setPending(null)} error={nameError} /> : null}
      {failure ? (
        <div className="mb-3">
          <ActionError error={failure} />
        </div>
      ) : null}
      <TabPanel group={GROUP} id={filter} value={filter} keepMounted={false}>
        {isLoading && !data ? (
          <LoadingBlock label="Loading notifications" rows={2} className="h-20" />
        ) : null}
        {error && !data ? <ErrorState error={error} onRetry={() => void mutate()} /> : null}
        {data && items.length === 0 ? (
          <EmptyState
            compact
            icon={<BellRing aria-hidden className="h-6 w-6" />}
            title={filter === "all" ? "Nothing yet" : `No ${CATEGORY_LABEL[filter].toLowerCase()} notifications`}
            text="Alerts, incidents and tests appear here as they happen. Send a test alert to see how one looks."
          />
        ) : null}
        {items.length > 0 ? (
          <ul aria-label="Notifications, newest first" className="divide-y divide-rule overflow-hidden rounded-md border border-rule bg-surface">
            {items.map((n) => (
              <Item key={n.id} n={n} asOfMs={asOfMs} busy={busy} onRead={(id) => request({ ids: [id] })} />
            ))}
          </ul>
        ) : null}
        {more ? (
          <div className="mt-3 flex justify-center">
            <Button variant="secondary" loading={isValidating && size > pages.length} onClick={() => void setSize(size + 1)}>
              Show older
            </Button>
          </div>
        ) : null}
      </TabPanel>
    </Panel>
  );
}
