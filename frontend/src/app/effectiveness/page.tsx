"use client";

import { BarChart3, ChevronDown, ChevronRight, Download } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { BarLegend } from "@/components/effectiveness/StackedBar";
import { PerformanceSummary } from "@/components/effectiveness/PerformanceSummary";
import { RuleTable, type SortState } from "@/components/effectiveness/RuleTable";
import { ErrorState } from "@/components/StateViews";
import { Button, EmptyState, Panel, SectionHeading, Skeleton, Stagger, inputClasses } from "@/components/ui";
import { getEffectiveness } from "@/lib/api";
import { useCheckName } from "@/lib/useCheckNames";
import { DEFAULT_SORT_DIR, rulesToCsv, sortRules, splitNeverFired, summarise, type RuleSort } from "@/lib/effectiveness";
import { formatRatio } from "@/lib/format";
import { plural } from "@/lib/labels";
import type { UseCase } from "@/lib/types";

function download(name: string, text: string) {
  try {
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    /* no Blob URLs in this environment */
  }
}

const SORT_LABEL: Record<RuleSort, string> = { times_fired: "Times fired", dismissal_rate: "Dismissal rate", median_time: "Time to decide", rule: "Check name" };

function FilterField({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div>
      <label htmlFor={id} className="label-caps mb-1 block">
        {label}
      </label>
      {children}
    </div>
  );
}

export default function EffectivenessPage() {
  const nameOf = useCheckName();
  const [useCase, setUseCase] = useState<UseCase | "">("");
  const [since, setSince] = useState("");
  const [sortState, setSortState] = useState<SortState>({ sort: "times_fired", dir: "desc" });
  const [neverOpen, setNeverOpen] = useState(false);
  const { data, error, isLoading, mutate } = useSWR(["effectiveness", useCase, since], () => getEffectiveness({ use_case: useCase || undefined, since: since || undefined }));

  const onSort = (sort: RuleSort) =>
    setSortState((cur) => (cur.sort === sort ? { sort, dir: cur.dir === "asc" ? "desc" : "asc" } : { sort, dir: DEFAULT_SORT_DIR[sort] }));

  const sorted = data ? sortRules(data.rules, sortState.sort, sortState.dir) : [];
  const { fired, never } = splitNeverFired(sorted);
  const noisy = data ? summarise(data.rules).noisy : [];

  return (
    <Stagger className="space-y-6">
      <SectionHeading level={1} size="page" description="How often each check fires and what analysts decided.">
        Control performance
      </SectionHeading>

      <Panel tone="sunken" padding="sm">
        <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
          <FilterField id="eff-uc" label="Case type">
            <select id="eff-uc" className={inputClasses} value={useCase} onChange={(e) => setUseCase(e.target.value as UseCase | "")}>
              <option value="">All</option>
              <option value="kyc">Onboarding (KYC)</option>
              <option value="aml">Source of funds (AML)</option>
              <option value="loan">Loan application</option>
            </select>
          </FilterField>
          <FilterField id="eff-since" label="Created since">
            <input id="eff-since" type="date" className={inputClasses} value={since} onChange={(e) => setSince(e.target.value)} />
          </FilterField>
          <FilterField id="eff-sort" label="Sort by">
            <select id="eff-sort" className={inputClasses} value={sortState.sort} onChange={(e) => setSortState({ sort: e.target.value as RuleSort, dir: DEFAULT_SORT_DIR[e.target.value as RuleSort] })}>
              {(Object.keys(SORT_LABEL) as RuleSort[]).map((k) => (
                <option key={k} value={k}>
                  {SORT_LABEL[k]}
                </option>
              ))}
            </select>
          </FilterField>
          <Button
            variant="secondary"
            className="ml-auto"
            disabled={!data}
            icon={<Download aria-hidden className="h-4 w-4" />}
            onClick={() => download("rule-effectiveness.csv", rulesToCsv([...fired, ...never]))}
          >
            Download CSV
          </Button>
        </div>
      </Panel>

      <div className="space-y-6" aria-live="polite">
        {isLoading && !data ? (
          <div role="status" aria-busy="true" className="space-y-4">
            <span className="sr-only">Loading control performance...</span>
            <Skeleton className="h-28 rounded-lg" />
            <Skeleton className="h-72 rounded-lg" />
          </div>
        ) : null}
        {error && !data ? <ErrorState error={error} onRetry={() => void mutate()} /> : null}
        {data ? (
          <>
            <PerformanceSummary data={data} />
            {data.cases_decided === 0 ? (
              <EmptyState
                icon={<BarChart3 aria-hidden className="h-6 w-6" />}
                title="No decisions recorded yet"
                text="Rule statistics appear after reviewers confirm or dismiss flags. Until then every check shows zero decisions."
                action={<Button href="/" variant="secondary">Go to the worklist</Button>}
              />
            ) : null}
            {noisy.length > 0 ? (
              <Panel tone="attention" title="Worth a closer look" eyebrow={`${noisy.length} ${plural(noisy.length, "check")} may be too sensitive`}>
                <p className="text-[0.9375rem] text-ink-2">Analysts dismissed at least half of what these checks raised. Consider whether they need tuning.</p>
                <ul className="mt-3 divide-y divide-rule">
                  {noisy.map((r) => (
                    <li key={r.rule} className="flex flex-wrap items-baseline justify-between gap-x-4 py-2">
                      <span className="font-semibold text-ink">{nameOf(r.rule)}</span>
                      <span className="tabular text-sm text-ink-2">
                        {r.dismissal_rate === null ? "-" : formatRatio(r.dismissal_rate)} dismissed of {r.confirmed + r.dismissed} decided
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            ) : null}
            <Panel
              title={`Checks that fired (${fired.length})`}
              padding="none"
              footer={fired.length > 0 ? <BarLegend /> : undefined}
            >
              <RuleTable rules={fired.length || never.length === 0 ? fired : []} caption="Checks that fired" state={sortState} onSort={onSort} />
              {fired.length === 0 ? <p className="px-5 py-6 text-[0.9375rem] text-ink-2">None of the checks has fired in this view.</p> : null}
            </Panel>
            {never.length > 0 ? (
              <section>
                <button
                  type="button"
                  aria-expanded={neverOpen}
                  onClick={() => setNeverOpen((o) => !o)}
                  className="flex min-h-11 w-full items-center gap-2 rounded-lg border border-rule bg-surface px-4 text-left text-[0.9375rem] font-semibold text-ink shadow-paper hover:bg-brand-tint/50"
                >
                  {neverOpen ? <ChevronDown aria-hidden className="h-4 w-4" /> : <ChevronRight aria-hidden className="h-4 w-4" />}
                  Never fired ({never.length})
                  <span className="ml-auto hidden text-sm font-normal text-muted sm:inline">Checks that ran but raised nothing</span>
                </button>
                {neverOpen ? (
                  <Panel padding="none" className="mt-2">
                    <RuleTable rules={never} caption="Checks that never fired" state={sortState} onSort={onSort} />
                  </Panel>
                ) : null}
              </section>
            ) : null}
          </>
        ) : null}
      </div>
    </Stagger>
  );
}
