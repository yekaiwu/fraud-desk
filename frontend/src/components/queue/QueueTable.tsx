"use client";

import clsx from "clsx";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { PAGE_SIZE } from "@/lib/constants";
import type { QueueItem, QueueQuery } from "@/lib/types";
import type { ReactNode } from "react";
import { ExplainChecksColumn, ExplainRiskColumn } from "../explain/ListExplainers";
import { ExplainLanes } from "../explain/NextStepExplainer";
import { QueueCard, QueueTableRow } from "./QueueRow";

export type SortKey = "score" | "coverage" | "age";

function ariaSort(q: QueueQuery, key: SortKey): "ascending" | "descending" | "none" {
  if (q.sort !== key) return "none";
  return (q.order ?? "desc") === "asc" ? "ascending" : "descending";
}

const head = "label-caps px-3 py-3 text-left align-bottom whitespace-nowrap first:pl-5";

function SortHeader({ label, k, q, onSort, className, addon }: { label: string; k: SortKey; q: QueueQuery; onSort: (k: SortKey) => void; className?: string; addon?: ReactNode }) {
  const s = ariaSort(q, k);
  const Icon = s === "ascending" ? ArrowUp : s === "descending" ? ArrowDown : ChevronsUpDown;
  return (
    <th scope="col" aria-sort={s} className={clsx(head, "py-1", className)}>
      <span className="inline-flex items-center gap-1">
        <button
          type="button"
          onClick={() => onSort(k)}
          className={clsx("label-caps -ml-2 inline-flex min-h-11 items-center gap-1 rounded-md px-2 transition-colors hover:bg-paper hover:text-ink", s !== "none" && "text-ink")}
        >
          {label}
          <Icon aria-hidden className={clsx("h-3.5 w-3.5", s === "none" && "opacity-60")} />
        </button>
        {addon}
      </span>
    </th>
  );
}

export function rankBase(query: QueueQuery): number {
  return ((query.page ?? 1) - 1) * (query.page_size ?? PAGE_SIZE);
}

/** Rank numbers only mean something in the default priority order. */
export function showsRank(query: QueueQuery): boolean {
  return (query.sort ?? "priority") === "priority" && !query.order;
}

export function QueueTable({ items, query, ageNow, onSort }: { items: QueueItem[]; query: QueueQuery; ageNow: number; onSort: (k: SortKey) => void }) {
  const rank = showsRank(query);
  const base = rankBase(query);
  return (
    <div role="region" aria-label="Cases, scrolls sideways on narrow screens" tabIndex={0} className="overflow-x-auto focus-visible:outline-offset-[-2px]">
      <table className="w-full min-w-[60rem] border-collapse text-sm">
        <caption className="sr-only">Cases ranked by priority: open cases first, then highest risk, least checked and longest waiting.</caption>
        <thead className="bg-paper-2">
          <tr className="border-y border-rule">
            {rank ? (
              <th scope="col" className={head}>
                <span aria-hidden>#</span>
                <span className="sr-only">Rank</span>
              </th>
            ) : null}
            <SortHeader label="Risk" k="score" q={query} onSort={onSort} addon={<ExplainRiskColumn />} />
            <th scope="col" className={head}>
              Applicant
            </th>
            <th scope="col" className={head}>
              Why it is here
            </th>
            <th scope="col" className={head}>
              <span className="inline-flex items-center gap-1.5">
                Lane and status
                <ExplainLanes />
              </span>
            </th>
            <SortHeader label="Checks completed" k="coverage" q={query} onSort={onSort} addon={<ExplainChecksColumn />} />
            <SortHeader label="Waiting" k="age" q={query} onSort={onSort} className="pr-5" />
          </tr>
        </thead>
        <tbody>
          {items.map((it, i) => (
            <QueueTableRow key={it.case_id} item={it} rank={rank ? base + i + 1 : null} ageNow={ageNow} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function QueueCards({ items, query, ageNow }: { items: QueueItem[]; query: QueueQuery; ageNow: number }) {
  const rank = showsRank(query);
  const base = rankBase(query);
  return (
    <ul aria-label="Cases ranked by priority" className="grid gap-3 lg:grid-cols-2 lg:gap-4">
      {items.map((it, i) => (
        <QueueCard key={it.case_id} item={it} rank={rank ? base + i + 1 : null} ageNow={ageNow} />
      ))}
    </ul>
  );
}
