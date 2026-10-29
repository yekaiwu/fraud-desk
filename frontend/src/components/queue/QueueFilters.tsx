"use client";

import clsx from "clsx";
import { AlertOctagon, AlertTriangle, CheckCircle2, Search, SlidersHorizontal, X, type LucideIcon } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { caseStatusLabel, laneDescription, laneLabel } from "@/lib/labels";
import { DEFAULT_QUEUE_STATUS } from "@/lib/queueQuery";
import type { Band, CaseStatus, Lane, QueueQuery, UseCase } from "@/lib/types";
import { Modal } from "../Modal";
import { ExplainLanes } from "../explain/NextStepExplainer";
import { Button, Tabs, inputClasses, panelDomId, tabDomId } from "../ui";
import type { LaneKey } from "./worklist";

export const SEARCH_ID = "worklist-search";
export const LANE_GROUP = "lane";

const STATUSES: CaseStatus[] = ["processing", "open", "awaiting_documents", "closed", "error"];
const LANES: Lane[] = ["loan", "edd", "aml", "fast"];
const BANDS: { v: Band; word: string; Icon: LucideIcon; off: string; on: string }[] = [
  { v: "high", word: "High", Icon: AlertOctagon, off: "text-sev-high", on: "border-sev-high bg-sev-high-tint text-sev-high" },
  { v: "medium", word: "Medium", Icon: AlertTriangle, off: "text-sev-medium", on: "border-sev-medium bg-sev-medium-tint text-sev-medium" },
  { v: "low", word: "Low", Icon: CheckCircle2, off: "text-sev-low", on: "border-sev-low bg-sev-low-tint text-sev-low" },
];

export const SORT_LABELS: Record<NonNullable<QueueQuery["sort"]>, string> = {
  priority: "Riskiest first",
  score: "Risk score",
  age: "Longest waiting",
  coverage: "Least checked",
  created: "Newest",
};

function toggle<T>(list: T[] | undefined, v: T): T[] {
  const cur = list ?? [];
  return cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v];
}

function statusChanged(q: QueueQuery): boolean {
  const status = q.status ?? DEFAULT_QUEUE_STATUS;
  return status.length !== DEFAULT_QUEUE_STATUS.length || status.some((s) => !DEFAULT_QUEUE_STATUS.includes(s));
}

export function activeFilterCount(q: QueueQuery): number {
  return [q.use_case?.length, statusChanged(q), q.lane?.length, q.band?.length, q.partial !== undefined, q.min_score !== undefined, q.q, q.sort && q.sort !== "priority"].filter(Boolean).length;
}

/** Which lane tab is selected: All, one lane, or "" when several lanes were picked in More filters. */
export function laneTabValue(q: QueueQuery): string {
  if (!q.lane?.length) return "all";
  return q.lane.length === 1 ? q.lane[0] : "";
}

/** One line under the tabs saying what the selected tab means, for touch screens that have no tooltip. */
export function laneTabDescription(q: QueueQuery): { name: string; text: string } {
  const v = laneTabValue(q);
  if (v === "all") return { name: "All cases", text: "Every case in your worklist, riskiest first." };
  if (v === "") return { name: "Several lanes", text: q.lane?.map(laneLabel).join(", ") ?? "" };
  return { name: laneLabel(v as Lane), text: laneDescription(v as Lane) };
}

/** Ids of the lane tab and of the list panel it controls, so assistive tech can link them. */
export const lanePanelId = (q: QueueQuery) => panelDomId(LANE_GROUP, laneTabValue(q) || "all");
export const laneTabId = (q: QueueQuery) => tabDomId(LANE_GROUP, laneTabValue(q) || "all");

const chip = "inline-flex min-h-11 items-center gap-2 rounded-full border px-3.5 text-sm font-semibold transition-colors";
const chipOff = "border-rule-strong bg-surface text-ink-2 hover:border-brand-2 hover:bg-brand-tint";
const chipOn = "border-brand bg-brand text-on-brand hover:bg-brand-2";
const checkRow = "inline-flex min-h-11 items-center gap-2 pr-4 text-[0.9375rem] text-ink";

function Checks<T extends string>({ legend, options, selected, onToggle }: { legend: string; options: { v: T; l: string }[]; selected: T[]; onToggle: (v: T) => void }) {
  return (
    <fieldset>
      <legend className="label-caps mb-1">{legend}</legend>
      <div className="flex flex-wrap">
        {options.map((o) => (
          <label key={o.v} className={checkRow}>
            <input type="checkbox" className="h-5 w-5 accent-brand-2" checked={selected.includes(o.v)} onChange={() => onToggle(o.v)} />
            {o.l}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** The less common filters: use case, status, several lanes at once, and how to sort. */
function MoreControls({ query, onChange, inline }: { query: QueueQuery; onChange: (q: QueueQuery) => void; inline?: boolean }) {
  const sortId = useId();
  const set = (patch: Partial<QueueQuery>) => onChange({ ...query, ...patch, page: undefined });
  const status = query.status ?? DEFAULT_QUEUE_STATUS;
  const useCases: { v: UseCase | "all"; l: string }[] = [
    { v: "all", l: "All" },
    { v: "kyc", l: "KYC" },
    { v: "aml", l: "AML" },
    { v: "loan", l: "Loan" },
  ];
  const current = query.use_case?.length === 1 ? query.use_case[0] : "all";
  return (
    <div className={inline ? "flex flex-wrap items-start gap-x-10 gap-y-4" : "space-y-5"}>
      <div role="group" aria-label="Use case">
        <span className="label-caps mb-1 block">Use case</span>
        <div className="inline-flex overflow-hidden rounded-md border border-rule-strong bg-surface">
          {useCases.map((u) => (
            <button
              key={u.v}
              type="button"
              aria-pressed={current === u.v}
              onClick={() => set({ use_case: u.v === "all" ? undefined : [u.v] })}
              className={clsx("min-h-11 min-w-14 px-3 text-sm font-semibold transition-colors", current === u.v ? "bg-brand text-on-brand" : "text-ink-2 hover:bg-brand-tint")}
            >
              {u.l}
            </button>
          ))}
        </div>
      </div>
      <Checks legend="Status" options={STATUSES.map((v) => ({ v, l: caseStatusLabel(v) }))} selected={status} onToggle={(v) => set({ status: toggle(status, v) })} />
      <Checks legend="Lane (pick several)" options={LANES.map((v) => ({ v, l: laneLabel(v) }))} selected={query.lane ?? []} onToggle={(v) => set({ lane: toggle(query.lane, v) })} />
      <div>
        <label htmlFor={sortId} className="label-caps mb-1 block">
          Sort by
        </label>
        <select
          id={sortId}
          className={clsx(inputClasses, "w-auto min-w-44")}
          value={query.sort ?? "priority"}
          onChange={(e) => set({ sort: e.target.value === "priority" ? undefined : (e.target.value as QueueQuery["sort"]), order: undefined })}
        >
          {(Object.keys(SORT_LABELS) as (keyof typeof SORT_LABELS)[]).map((k) => (
            <option key={k} value={k}>
              {SORT_LABELS[k]}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

export type WorklistCounts = { lane?: Record<LaneKey, number>; band?: Record<Band, number> };

export function QueueFilters({ query, onChange, counts }: { query: QueueQuery; onChange: (q: QueueQuery) => void; counts: WorklistCounts }) {
  const [text, setText] = useState(query.q ?? "");
  const [sheet, setSheet] = useState(false);
  const [more, setMore] = useState(false);
  const count = activeFilterCount(query);
  const showClosed = (query.status ?? DEFAULT_QUEUE_STATUS).includes("closed");
  const moreActive = Boolean(query.use_case?.length) || (query.lane?.length ?? 0) > 1 || (query.sort !== undefined && query.sort !== "priority") || (statusChanged(query) && !showClosed);
  const moreOpen = more || moreActive;
  const description = laneTabDescription(query);

  const [prevQ, setPrevQ] = useState(query.q);
  if (query.q !== prevQ) {
    // the URL changed (clear filters, back button): follow it
    setPrevQ(query.q);
    setText(query.q ?? "");
  }
  useEffect(() => {
    if (text === (query.q ?? "")) return;
    const t = setTimeout(() => onChange({ ...query, q: text.trim() || undefined, page: undefined }), 300);
    return () => clearTimeout(t);
  }, [text, query, onChange]);

  const laneTabs = [
    { id: "all", label: "All", count: counts.lane?.all, title: "Every case in your worklist" },
    ...LANES.map((l) => ({ id: l, label: laneLabel(l), count: counts.lane?.[l], title: laneDescription(l) })),
  ];

  return (
    <section aria-label="Worklist filters" className="rounded-lg border border-rule bg-surface p-3 shadow-paper sm:p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-full">
          <Tabs
            group={LANE_GROUP}
            label="Lane"
            variant="pill"
            tabs={laneTabs}
            value={laneTabValue(query)}
            onValueChange={(id) => onChange({ ...query, lane: id === "all" ? undefined : [id as Lane], page: undefined })}
          />
          <p className="mt-2 text-sm text-ink-2">
            <span className="font-semibold text-ink">{description.name}.</span> {description.text} <ExplainLanes className="ml-0.5" />
          </p>
        </div>
        <div className="relative w-full sm:w-80">
          <label htmlFor={SEARCH_ID} className="sr-only">
            Search applicant name or reference
          </label>
          <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
          <input id={SEARCH_ID} type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search applicant or reference" className={clsx(inputClasses, "pl-9 pr-10")} />
        </div>
      </div>

      <div className="mt-3 border-t border-rule pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Risk band" className="flex flex-wrap items-center gap-2">
            <span aria-hidden className="label-caps mr-1">
              Risk
            </span>
            {BANDS.map(({ v, word, Icon, off, on }) => {
              const pressed = query.band?.includes(v) ?? false;
              return (
                <button key={v} type="button" aria-pressed={pressed} onClick={() => onChange({ ...query, band: toggle(query.band, v), page: undefined })} className={clsx(chip, pressed ? on : chipOff)}>
                  <Icon aria-hidden className={clsx("h-4 w-4", !pressed && off)} />
                  {word}
                  {counts.band ? <span className="tabular font-normal">{counts.band[v]}</span> : null}
                </button>
              );
            })}
          </div>
          <span aria-hidden className="mx-1 hidden h-6 w-px bg-rule sm:block" />
          <button
            type="button"
            aria-pressed={showClosed}
            onClick={() => {
              const cur = query.status ?? DEFAULT_QUEUE_STATUS;
              onChange({ ...query, status: showClosed ? cur.filter((s) => s !== "closed") : [...cur, "closed"], page: undefined });
            }}
            className={clsx(chip, showClosed ? chipOn : chipOff)}
          >
            Show closed
          </button>
          <button type="button" onClick={() => setSheet(true)} className={clsx(chip, chipOff, "md:hidden")}>
            <SlidersHorizontal aria-hidden className="h-4 w-4" />
            Filters ({count})
          </button>
          <button type="button" aria-expanded={moreOpen} aria-controls="worklist-more" onClick={() => setMore(!more)} className={clsx(chip, "max-md:hidden border-transparent text-brand-2 hover:bg-brand-tint")}>
            <SlidersHorizontal aria-hidden className="h-4 w-4" />
            More filters
          </button>
          {count > 0 ? (
            <Button variant="ghost" icon={<X aria-hidden className="h-4 w-4" />} onClick={() => onChange({})} className="sm:ml-auto">
              Clear all filters
            </Button>
          ) : null}
        </div>
        {moreOpen ? (
          <div id="worklist-more" className="mt-3 hidden border-t border-rule pt-3 md:block">
            <MoreControls query={query} onChange={onChange} inline />
          </div>
        ) : null}
      </div>

      <Modal open={sheet} onClose={() => setSheet(false)} title="Filters">
        <MoreControls query={query} onChange={onChange} />
        <div className="mt-6 flex gap-2">
          <Button variant="secondary" fullWidth onClick={() => onChange({})}>
            Clear filters
          </Button>
          <Button fullWidth onClick={() => setSheet(false)}>
            Show results
          </Button>
        </div>
      </Modal>
    </section>
  );
}
