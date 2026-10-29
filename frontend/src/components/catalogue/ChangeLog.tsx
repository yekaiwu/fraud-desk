"use client";

import { History } from "lucide-react";
import { useMemo, useState } from "react";
import useSWR from "swr";
import { getRuleChanges } from "@/lib/api";
import { defaultSeverity, effectiveName } from "@/lib/checkCatalogue";
import { saveError } from "./saveError";
import { formatRelative, formatUtc } from "@/lib/format";
import { plural } from "@/lib/labels";
import type { RuleChange, RuleInfo, Severity } from "@/lib/types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { inputClasses } from "../ui/Field";
import { Notice } from "../ui/Notice";
import { Skeleton } from "../ui/Skeleton";
import { describeChange } from "./changeText";
import { SideSheet } from "./SideSheet";

const KEY = "rule-changes";
/** SWR key matcher: every cached change list (one check or all). */
export const isChangeKey = (k: unknown): boolean => Array.isArray(k) && k[0] === KEY;

export function useRuleChanges(rule: string | undefined, limit: number) {
  return useSWR([KEY, rule ?? "all", limit], () => getRuleChanges({ rule, limit }), { revalidateOnFocus: false });
}

/** One entry of the change log. Everything typed by people is rendered as plain text. */
export function ChangeItem({ change, check, defaultSeverity: def }: { change: RuleChange; check?: { name: string; defaultSeverity?: Severity }; defaultSeverity?: Severity }) {
  const lines = describeChange(change, def ?? check?.defaultSeverity);
  return (
    <li className="space-y-1.5 py-3.5 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge variant={change.action === "reset" ? "attention" : "lane"} icon={null}>
          {change.action === "reset" ? "Reset to default" : "Changed"}
        </Badge>
        <span className="text-sm font-semibold text-ink break-words">{change.reviewer}</span>
        <span className="tabular text-sm text-muted">
          <time dateTime={change.at}>{formatUtc(change.at)}</time>
          {formatRelative(change.at) ? ` (${formatRelative(change.at)})` : ""}
        </span>
      </div>
      {check ? (
        <p className="text-[0.9375rem] font-semibold leading-snug text-ink break-words">
          {check.name} <span className="num text-xs font-normal text-muted">{change.rule}</span>
        </p>
      ) : null}
      {lines.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-[0.9375rem] text-ink marker:text-rule-strong">
          {lines.map((l, i) => (
            <li key={i} className="break-words">
              {l}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted">No visible change.</p>
      )}
      <p className="whitespace-pre-wrap break-words text-sm text-ink-2">
        <span className="label-caps mr-1.5">Reason</span>
        {change.reason}
      </p>
    </li>
  );
}

function ListState({ error, loading, retry }: { error: unknown; loading: boolean; retry: () => void }) {
  if (error) {
    const e = saveError(error, "load");
    return (
      <Notice variant="error" title="The change log could not be loaded" action={<Button size="sm" variant="secondary" onClick={retry}>Try again</Button>}>
        {e.detail}
      </Notice>
    );
  }
  if (loading) {
    return (
      <div role="status" aria-busy="true" className="space-y-3">
        <span className="sr-only">Loading the change log...</span>
        <Skeleton className="h-16" />
        <Skeleton className="h-16" />
      </div>
    );
  }
  return null;
}

const FIRST = 5;

/** The editor's "History for this check", newest first. */
export function ChangeHistory({ rule }: { rule: RuleInfo }) {
  const { data, error, isLoading, mutate } = useRuleChanges(rule.rule, 50);
  const [all, setAll] = useState(false);
  const shown = data ? (all ? data : data.slice(0, FIRST)) : [];
  return (
    <div>
      <ListState error={error} loading={isLoading && !data} retry={() => void mutate()} />
      {data && data.length === 0 ? <p className="text-[0.9375rem] text-ink-2">No changes yet. This check has always used its default settings.</p> : null}
      {shown.length > 0 ? (
        <ul aria-label="Changes to this check" className="divide-y divide-rule">
          {shown.map((c) => (
            <ChangeItem key={c.id} change={c} defaultSeverity={defaultSeverity(rule)} />
          ))}
        </ul>
      ) : null}
      {data && data.length > FIRST ? (
        <Button size="sm" variant="ghost" className="mt-2" onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${data.length} changes`}
        </Button>
      ) : null}
    </div>
  );
}

/** The right-hand sheet with every change to every check. */
export function ChangeLogSheet({ open, onClose, rules }: { open: boolean; onClose: () => void; rules: RuleInfo[] }) {
  return (
    <SideSheet open={open} onClose={onClose} eyebrow="Check catalogue" title="Change log" meta="Every change is kept permanently, with who made it and why.">
      <ChangeLogBody rules={rules} />
    </SideSheet>
  );
}

function ChangeLogBody({ rules }: { rules: RuleInfo[] }) {
  const { data, error, isLoading, mutate } = useRuleChanges(undefined, 200);
  const [rule, setRule] = useState("");
  const byId = useMemo(() => new Map(rules.map((r) => [r.rule, r])), [rules]);
  const nameOf = (id: string) => effectiveName(id, byId.get(id)?.override, undefined);
  const touched = useMemo(() => [...new Set((data ?? []).map((c) => c.rule))].sort((a, b) => nameOf(a).localeCompare(nameOf(b))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, byId]);
  const shown = (data ?? []).filter((c) => !rule || c.rule === rule);
  return (
    <div className="space-y-4 px-4 py-4 sm:px-6">
      <div>
        <label htmlFor="changelog-check" className="mb-1 block text-sm font-semibold text-ink">
          Show changes to
        </label>
        <select id="changelog-check" className={inputClasses} value={rule} onChange={(e) => setRule(e.target.value)} disabled={!data || data.length === 0}>
          <option value="">All checks</option>
          {touched.map((id) => (
            <option key={id} value={id}>
              {nameOf(id)}
            </option>
          ))}
        </select>
      </div>
      <ListState error={error} loading={isLoading && !data} retry={() => void mutate()} />
      {data ? (
        <p role="status" className="tabular text-sm font-semibold text-ink-2">
          {shown.length} {plural(shown.length, "change")}
        </p>
      ) : null}
      {data && data.length === 0 ? (
        <div className="rounded-lg border border-dashed border-rule-strong bg-paper-2/50 p-5 text-center">
          <History aria-hidden className="mx-auto h-6 w-6 text-muted" />
          <p className="mt-2 font-semibold text-ink">No changes yet</p>
          <p className="mt-0.5 text-sm text-ink-2">When someone edits a check, the change appears here with their name and reason.</p>
        </div>
      ) : null}
      {shown.length > 0 ? (
        <ul aria-label="Changes to checks" className="divide-y divide-rule">
          {shown.map((c) => (
            <ChangeItem key={c.id} change={c} check={{ name: nameOf(c.rule), defaultSeverity: byId.has(c.rule) ? defaultSeverity(byId.get(c.rule)!) : undefined }} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}
