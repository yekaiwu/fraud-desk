"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import useSWR from "swr";
import { QueueFilters, SORT_LABELS, activeFilterCount, lanePanelId, laneTabId } from "@/components/queue/QueueFilters";
import { NoMatches, NothingWaiting, QueueError, QueueSkeleton } from "@/components/queue/QueueStates";
import { QueueCards, QueueTable } from "@/components/queue/QueueTable";
import { ShortcutsDialog } from "@/components/queue/ShortcutsDialog";
import { WorklistHeader } from "@/components/queue/WorklistHeader";
import { bandCounts, laneCounts, pickNext, summarise } from "@/components/queue/worklist";
import { useWorklistKeys } from "@/components/queue/useWorklistKeys";
import { Button, Notice, Stagger } from "@/components/ui";
import { getQueue } from "@/lib/api";
import { API_BASE_URL, PAGE_SIZE } from "@/lib/constants";
import { ApiErrorException } from "@/lib/errors";
import { queueQueryToSearchParams, scopeQuery, searchParamsToQueueQuery } from "@/lib/queueQuery";
import type { QueueQuery } from "@/lib/types";
import { useMedia } from "@/lib/useMedia";
import { formatAge } from "@/lib/format";

const freshClock = () => ({ fetched: Date.now(), now: Date.now() });

/** "Longest waiting first": the sort in plain words, including the direction. */
function sortDescription(q: QueueQuery): string {
  const sort = q.sort ?? "priority";
  if (sort === "priority") return SORT_LABELS.priority;
  const desc = (q.order ?? "desc") === "desc";
  switch (sort) {
    case "score":
      return desc ? "Highest risk first" : "Lowest risk first";
    case "coverage":
      return desc ? "Most checks completed first" : "Fewest checks completed first";
    case "age":
      return desc ? "Longest waiting first" : "Shortest waiting first";
    default:
      return desc ? "Newest first" : "Oldest first";
  }
}

function QueueView() {
  const router = useRouter();
  const params = useSearchParams();
  // The table needs about 1100 px; below that every case is a card (two across from 1024 px).
  const wide = useMedia("(min-width: 1280px)");
  const query = searchParamsToQueueQuery(new URLSearchParams(params.toString()));
  const key = ["queue", queueQueryToSearchParams(query).toString()];
  const { data, error, isLoading, isValidating, mutate } = useSWR(key, () => getQueue(query), { refreshInterval: 10_000, keepPreviousData: false, onSuccess: () => setClock(freshClock()) });

  // The same cases without the lane and band filters: lane and band counts, the summary sentence and "next case".
  const scope = scopeQuery(query);
  const { data: scopeData, mutate: mutateScope } = useSWR(["queue-scope", queueQueryToSearchParams(scope).toString()], () => getQueue(scope), { refreshInterval: 10_000, keepPreviousData: true });

  // seconds since the last successful fetch, added to age_seconds so waiting times keep moving between refreshes
  const [clock, setClock] = useState(freshClock);
  useEffect(() => {
    const t = setInterval(() => setClock((c) => ({ ...c, now: Date.now() })), 30_000);
    return () => clearInterval(t);
  }, []);
  const sinceFetch = Math.max(0, (clock.now - clock.fetched) / 1000);

  const [help, setHelp] = useState(false);
  useWorklistKeys(useCallback(() => setHelp(true), []));

  const change = useCallback(
    (next: QueueQuery) => {
      const qs = queueQueryToSearchParams(next).toString();
      router.replace(qs ? `/?${qs}` : "/");
    },
    [router],
  );
  const onSort = (k: "score" | "coverage" | "age") => {
    const nextOrder = query.sort === k && (query.order ?? "desc") === "desc" ? "asc" : "desc";
    change({ ...query, sort: k, order: nextOrder, page: undefined });
  };
  const refresh = () => {
    void mutate();
    void mutateScope();
  };

  const sample = scopeData?.items ?? (activeFilterCount({ ...query, lane: undefined, band: undefined }) === 0 ? data?.items : undefined);
  const narrowed = Boolean(query.q) || Boolean(query.use_case?.length) || query.min_score !== undefined || query.partial !== undefined;
  const summary = sample ? summarise(sample, sinceFetch, scopeData?.has_next ?? false, narrowed) : null;
  const counts = sample ? { lane: laneCounts(sample, query.band), band: bandCounts(sample, query.lane) } : {};
  const next = sample ? pickNext(sample, { lane: query.lane, band: query.band }) : null;
  const secondsSince = Math.floor(sinceFetch);
  const updatedText = data ? (secondsSince < 60 ? "Updated just now" : `Updated ${formatAge(secondsSince)} ago`) : null;

  const filtered = activeFilterCount(query) > 0;
  const page = query.page ?? 1;
  const closedHidden = !(query.status ?? []).includes("closed");

  let body: React.ReactNode;
  if (isLoading && !data) body = <QueueSkeleton />;
  else if (error && !data) body = <QueueError error={error} onRetry={refresh} />;
  else if (data && data.items.length === 0) {
    body = filtered ? <NoMatches onClear={() => change({})} closedHidden={closedHidden} /> : <NothingWaiting />;
  } else if (data) {
    const cases = `${data.total} ${data.total === 1 ? "case" : "cases"}`;
    body = (
      <>
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-4 px-1 text-sm text-ink-2">
          <p>
            <span className="font-semibold text-ink tabular">{cases}</span> in this view
          </p>
          <p>
            Sorted: <span className="font-semibold text-ink">{sortDescription(query)}</span>
          </p>
        </div>
        {wide ? (
          <div className="overflow-hidden rounded-lg border border-rule bg-surface shadow-paper">
            <QueueTable items={data.items} query={query} ageNow={sinceFetch} onSort={onSort} />
          </div>
        ) : (
          <QueueCards items={data.items} query={query} ageNow={sinceFetch} />
        )}
        {data.has_next || page > 1 ? (
          <nav aria-label="Pagination" className="mt-4 flex items-center justify-between gap-3">
            <Button variant="secondary" disabled={page <= 1} onClick={() => change({ ...query, page: page - 1 })}>
              Previous
            </Button>
            <span className="text-sm text-muted tabular">
              Page {page} of {Math.max(1, Math.ceil(data.total / (query.page_size ?? PAGE_SIZE)))}
            </span>
            <Button variant="secondary" disabled={!data.has_next} onClick={() => change({ ...query, page: page + 1 })}>
              Next
            </Button>
          </nav>
        ) : null}
      </>
    );
  }

  return (
    <Stagger className="space-y-5">
      <WorklistHeader summary={summary} failed={Boolean(error && !data)} next={next} loading={isLoading && !data} refreshing={isValidating} updatedText={updatedText} onRefresh={refresh} />
      <QueueFilters query={query} onChange={change} counts={counts} />
      <div role="tabpanel" id={lanePanelId(query)} aria-labelledby={laneTabId(query)} aria-busy={isLoading && !data ? true : undefined} className="space-y-3">
        {error && data ? (
          <Notice variant="warning" title="Last refresh failed" action={<Button size="sm" variant="secondary" onClick={refresh}>Try again</Button>}>
            {error instanceof ApiErrorException && error.error.code === "network_error" ? `Cannot reach the API at ${API_BASE_URL}. ` : ""}Showing the previous rows.
          </Notice>
        ) : null}
        <div>{body}</div>
      </div>
      <ShortcutsDialog open={help} onClose={() => setHelp(false)} />
    </Stagger>
  );
}

export default function QueuePage() {
  return (
    <Suspense fallback={<QueueSkeleton />}>
      <QueueView />
    </Suspense>
  );
}
