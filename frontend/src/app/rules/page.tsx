"use client";

import { History, SearchX } from "lucide-react";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { ChangeLogSheet } from "@/components/catalogue/ChangeLog";
import { CheckEditor, type SavedKind } from "@/components/catalogue/CheckEditor";
import { CheckCards, CheckTable } from "@/components/rules/CheckEntries";
import { ErrorState } from "@/components/StateViews";
import { Button, EmptyState, Field, Notice, Panel, SectionHeading, Skeleton, Stagger, inputClasses } from "@/components/ui";
import { getRules } from "@/lib/api";
import { checkName, effectiveName, stageHint, stageLabel, STAGES } from "@/lib/checkCatalogue";
import { catalogueCounts, documentTypesOf, filterChecks, isFiltered, NO_FILTERS, orderChecks, type CheckFilters, type SettingsFilter } from "@/lib/checkFilters";
import { plural, severityLabel } from "@/lib/labels";
import type { FlagStage, RuleInfo, Severity, UseCase } from "@/lib/types";

const SEVERITIES: Severity[] = ["high", "medium", "low", "info"];

function Select({ id, label, value, onChange, children }: { id: string; label: string; value: string; onChange: (v: string) => void; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="mb-1 block text-sm font-semibold text-ink">
        {label}
      </label>
      <select id={id} className={inputClasses} value={value} onChange={(e) => onChange(e.target.value)}>
        {children}
      </select>
    </div>
  );
}

export default function RulesPage() {
  const { data, error, isLoading, mutate } = useSWR("rules", () => getRules());
  const [f, setF] = useState<CheckFilters>(NO_FILTERS);
  const set = <K extends keyof CheckFilters>(k: K, v: CheckFilters[K]) => setF((cur) => ({ ...cur, [k]: v }));

  const all = useMemo(() => data?.rules ?? [], [data]);
  const docTypes = useMemo(() => documentTypesOf(all), [all]);
  const rules = useMemo(() => orderChecks(filterChecks(all, f, checkName)), [all, f]);
  const counts = useMemo(() => catalogueCounts(all), [all]);
  const [editing, setEditing] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [saved, setSaved] = useState<{ kind: SavedKind; name: string } | null>(null);
  const editRule = useMemo(() => all.find((r) => r.rule === editing) ?? null, [all, editing]);

  async function onSaved(updated: RuleInfo, kind: SavedKind) {
    setEditing(null);
    setSaved({ kind, name: effectiveName(updated.rule, updated.override) });
    await mutate((cur) => (cur ? { rules: cur.rules.map((r) => (r.rule === updated.rule ? { ...r, ...updated } : r)) } : cur), { revalidate: true });
  }

  return (
    <Stagger className="space-y-6">
      <SectionHeading
        level={1}
        size="page"
        description="Every check the system can run and the kind of tampering it is meant to catch. You can switch a check off, change how serious its findings are, and reword it."
        actions={
          <Button variant="secondary" icon={<History aria-hidden className="h-4 w-4" />} onClick={() => setLogOpen(true)}>
            Change log
          </Button>
        }
      >
        Check catalogue
      </SectionHeading>

      {saved ? (
        <Notice variant="success" title={saved.kind === "reset" ? "Reset to default. Applies to analyses run from now on" : "Saved. Applies to analyses run from now on"} onDismiss={() => setSaved(null)}>
          <span className="break-words font-medium text-ink">{saved.name}.</span> Cases already analysed keep their results until they are re-run.
        </Notice>
      ) : null}

      <Panel tone="sunken" padding="sm">
        <div className="grid grid-cols-2 items-end gap-3 lg:grid-cols-[minmax(13rem,1.5fr)_repeat(5,minmax(0,1fr))]">
          <Field label="Search" type="search" value={f.q} onChange={(e) => set("q", e.target.value)} placeholder="Name or wording" className="col-span-2 min-w-0 lg:col-span-1" />
          <Select id="r-doc" label="Document type" value={f.docType} onChange={(v) => set("docType", v)}>
            <option value="">All</option>
            {docTypes.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </Select>
          <Select id="r-sev" label="Severity" value={f.severity} onChange={(v) => set("severity", v as Severity | "")}>
            <option value="">All</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {severityLabel(s)}
              </option>
            ))}
          </Select>
          <Select id="r-stage" label="Stage" value={f.stage} onChange={(v) => set("stage", v as FlagStage | "")}>
            <option value="">All</option>
            {STAGES.map((s) => (
              <option key={s} value={s}>
                {stageLabel(s)}
              </option>
            ))}
          </Select>
          <Select id="r-uc" label="Case type" value={f.useCase} onChange={(v) => set("useCase", v as UseCase | "")}>
            <option value="">All</option>
            <option value="loan">Loan application</option>
            <option value="kyc">Onboarding (KYC)</option>
            <option value="aml">Source of funds (AML)</option>
          </Select>
          <Select id="r-settings" label="Settings" value={f.settings} onChange={(v) => set("settings", v as SettingsFilter)}>
            <option value="">All checks</option>
            <option value="customised">Customised</option>
            <option value="off">Switched off</option>
          </Select>
        </div>
        <p className="mt-3 hidden text-sm text-muted sm:block">
          {STAGES.map((s) => `${stageLabel(s)}: ${stageHint(s).toLowerCase()}`).join(" ")}
        </p>
      </Panel>

      {isLoading && !data ? (
        <div role="status" aria-busy="true" className="space-y-3">
          <span className="sr-only">Loading the check catalogue...</span>
          <Skeleton className="h-16 rounded-lg" />
          <Skeleton className="h-64 rounded-lg" />
        </div>
      ) : null}
      {error && !data ? <ErrorState error={error} onRetry={() => void mutate()} /> : null}
      {data ? (
        <section aria-labelledby="catalogue-count">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p id="catalogue-count" role="status" className="tabular text-[0.9375rem] font-semibold text-ink-2">
              {rules.length} of {all.length} {plural(all.length, "check")}, {counts.customised} customised, {counts.off} switched off
            </p>
            {isFiltered(f) ? (
              <Button variant="ghost" size="md" onClick={() => setF(NO_FILTERS)}>
                Clear filters
              </Button>
            ) : null}
          </div>
          {rules.length === 0 ? (
            <EmptyState
              icon={<SearchX aria-hidden className="h-6 w-6" />}
              title="No checks match"
              text="Try a shorter search or clear a filter."
              action={
                <Button variant="secondary" onClick={() => setF(NO_FILTERS)}>
                  Clear filters
                </Button>
              }
            />
          ) : (
            <Panel padding="none">
              <CheckTable rules={rules} onEdit={setEditing} />
              <CheckCards rules={rules} onEdit={setEditing} />
            </Panel>
          )}
        </section>
      ) : null}

      <CheckEditor rule={editRule} rules={all} open={editing !== null} onClose={() => setEditing(null)} onSaved={(u, k) => void onSaved(u, k)} />
      <ChangeLogSheet open={logOpen} onClose={() => setLogOpen(false)} rules={all} />
    </Stagger>
  );
}
