import clsx from "clsx";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { isNoisy, type RuleSort, type SortDir } from "@/lib/effectiveness";
import { stageLabel } from "@/lib/checkCatalogue";
import { useCheckDescription, useCheckName } from "@/lib/useCheckNames";
import { formatRatio, formatSeconds } from "@/lib/format";
import type { RuleEffectiveness } from "@/lib/types";
import { Badge, ConcernBadge } from "../ui/Badge";
import { ExplainColumn, type ColumnTopic } from "../explain/PerformanceExplainers";
import { StackedBar } from "./StackedBar";

export type SortState = { sort: RuleSort; dir: SortDir };

function RuleId({ id }: { id: string }) {
  // "Check: " as text, the id itself in its own element so it can be quoted exactly
  return (
    <span className="num text-xs text-muted">
      Check: <code className="font-mono">{id}</code>
    </span>
  );
}

function NoisyHint({ r }: { r: RuleEffectiveness }) {
  return isNoisy(r) ? (
    <Badge variant="attention" title="Most of the flags analysts reviewed were dismissed">
      Noisy?
    </Badge>
  ) : null;
}

const timesText = (r: RuleEffectiveness) => `${r.times_fired} in ${r.cases_fired} ${r.cases_fired === 1 ? "case" : "cases"}`;
const rateText = (r: RuleEffectiveness) => (r.dismissal_rate === null ? "-" : formatRatio(r.dismissal_rate));

function SortHeader({ label, sort, state, onSort, className, addon }: { label: string; sort: RuleSort; state: SortState; onSort: (s: RuleSort) => void; className?: string; addon?: React.ReactNode }) {
  const active = state.sort === sort;
  const Icon = !active ? ChevronsUpDown : state.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th scope="col" aria-sort={active ? (state.dir === "asc" ? "ascending" : "descending") : "none"} className={clsx("px-3 py-1 text-left font-semibold", className)}>
      <span className="inline-flex items-center gap-1">
        <button type="button" onClick={() => onSort(sort)} className={clsx("-mx-2 inline-flex min-h-11 items-center gap-1.5 rounded-md px-2 uppercase tracking-[0.09em] hover:bg-ink/5", active ? "text-brand" : "text-muted")}>
          {label}
          <Icon aria-hidden className="h-3.5 w-3.5" />
          <span className="sr-only">{active ? `, sorted ${state.dir === "asc" ? "ascending" : "descending"}` : ", click to sort"}</span>
        </button>
        {addon}
      </span>
    </th>
  );
}

export function RuleTable({ rules, caption, state, onSort }: { rules: RuleEffectiveness[]; caption: string; state: SortState; onSort: (s: RuleSort) => void }) {
  const explain = (topic: ColumnTopic) => <ExplainColumn topic={topic} rules={rules} />;
  const nameOf = useCheckName();
  const describe = useCheckDescription();
  return (
    <>
      <div className="hidden overflow-x-auto md:block" role="region" aria-label={caption} tabIndex={0}>
        <table className="w-full min-w-[52rem] text-left text-sm">
          <caption className="sr-only">{caption}</caption>
          <thead className="bg-paper-2 text-[0.72rem]">
            <tr>
              <SortHeader label="Check" sort="rule" state={state} onSort={onSort} className="w-[34%]" />
              <SortHeader label="Times fired" sort="times_fired" state={state} onSort={onSort} addon={explain("times_fired")} />
              <th scope="col" className="px-3 py-1 text-left text-[0.72rem] font-semibold uppercase tracking-[0.09em] text-muted">
                <span className="inline-flex items-center gap-1.5">
                  What analysts decided
                  {explain("decisions")}
                </span>
              </th>
              <SortHeader label="Dismissed" sort="dismissal_rate" state={state} onSort={onSort} addon={explain("dismissed")} />
              <SortHeader label="Time to decide" sort="median_time" state={state} onSort={onSort} addon={explain("time_to_decide")} />
              <th scope="col" className="px-3 py-1 text-left text-[0.72rem] font-semibold uppercase tracking-[0.09em] text-muted">
                <span className="inline-flex items-center gap-1.5">
                  Ran / skipped
                  {explain("ran_skipped")}
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.rule} className="border-t border-rule align-top hover:bg-paper-2/40">
                <th scope="row" className="px-3 py-3 text-left font-normal">
                  <span className="block text-[0.9375rem] font-semibold leading-snug text-ink">{nameOf(r.rule)}</span>
                  <span className="mt-0.5 block max-w-md break-words text-sm leading-snug text-ink-2">{describe(r.rule, r.description)}</span>
                  <span className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1">
                    <RuleId id={r.rule} />
                    <span className="text-xs text-muted">{stageLabel(r.stage)}</span>
                    <ConcernBadge severity={r.default_severity} />
                    <NoisyHint r={r} />
                  </span>
                </th>
                <td className="tabular px-3 py-3 text-ink">{timesText(r)}</td>
                <td className="min-w-48 px-3 py-3">
                  <StackedBar r={r} />
                </td>
                <td className="tabular px-3 py-3 font-semibold text-ink">{rateText(r)}</td>
                <td className="tabular px-3 py-3 text-ink">{formatSeconds(r.median_seconds_to_decide)}</td>
                <td className="tabular px-3 py-3 text-ink-2">
                  {r.cases_ran} / {r.cases_skipped}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="space-y-3 p-3 md:hidden" aria-label={caption}>
        {rules.map((r) => (
          <li key={r.rule} className="rounded-lg border border-rule bg-surface p-3.5">
            <p className="text-[1rem] font-semibold leading-snug text-ink">{nameOf(r.rule)}</p>
            <p className="mt-0.5 break-words text-sm text-ink-2">{describe(r.rule, r.description)}</p>
            <div className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <RuleId id={r.rule} />
              <ConcernBadge severity={r.default_severity} />
              <NoisyHint r={r} />
            </div>
            <dl className="tabular mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
              <div>
                <dt className="label-caps">Times fired</dt>
                <dd className="text-ink">{timesText(r)}</dd>
              </div>
              <div>
                <dt className="label-caps">Dismissed</dt>
                <dd className="text-ink">{rateText(r)}</dd>
              </div>
              <div>
                <dt className="label-caps">Time to decide</dt>
                <dd className="text-ink">{formatSeconds(r.median_seconds_to_decide)}</dd>
              </div>
              <div>
                <dt className="label-caps">Ran / skipped</dt>
                <dd className="text-ink">
                  {r.cases_ran} / {r.cases_skipped}
                </dd>
              </div>
            </dl>
            <StackedBar r={r} className="mt-3" />
          </li>
        ))}
      </ul>
    </>
  );
}
