"use client";

import useSWR from "swr";
import { getCustomRule, getCustomRuleChanges } from "@/lib/api";
import { dryRunSentence, TEMPLATE_LABEL } from "@/lib/customRules";
import { formatUtc } from "@/lib/format";
import { severityLabel } from "@/lib/labels";
import { SideSheet } from "../catalogue/SideSheet";
import { Badge } from "../ui/Badge";
import { Skeleton } from "../ui/Skeleton";
import { CustomBadge } from "./CustomBadge";
import { CustomError } from "./CustomError";

const ACTION_TEXT: Record<string, string> = {
  create: "Created",
  new_version: "New version",
  enable: "Switched on",
  disable: "Switched off",
  publish: "Made public",
  unpublish: "Taken back",
  admin_unpublish: "Taken back by an administrator",
  retire: "Deleted",
  adopt: "Adopted",
  unadopt: "Stopped using",
};

/** One rule in full: what it checks, every version, the last test on stored cases and its own change log. */
export function RuleDetailSheet({ ruleId, onClose }: { ruleId: string | null; onClose: () => void }) {
  const rule = useSWR(ruleId ? ["custom-rule", ruleId] : null, () => getCustomRule(ruleId as string), { revalidateOnFocus: false });
  const changes = useSWR(ruleId ? ["custom-rule-changes", ruleId] : null, () => getCustomRuleChanges({ rule_id: ruleId as string, limit: 30 }), { revalidateOnFocus: false });
  const r = rule.data;
  return (
    <SideSheet open={ruleId !== null} onClose={onClose} eyebrow="Custom rule" title={r?.name ?? "Custom rule"} meta={r ? <span className="num">{r.rule_id}</span> : undefined}>
      <div className="space-y-5 px-4 py-4 sm:px-6">
        {rule.error ? <CustomError error={rule.error} /> : null}
        {!r && !rule.error ? (
          <div role="status" aria-busy="true">
            <span className="sr-only">Loading the rule...</span>
            <Skeleton className="h-24" />
          </div>
        ) : null}
        {r ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <CustomBadge info={{ author: r.owner, version: r.version }} />
              <Badge variant={r.severity} icon={null}>
                {severityLabel(r.severity)}
              </Badge>
              <Badge variant="neutral" icon={null}>
                {TEMPLATE_LABEL[r.template]}
              </Badge>
            </div>
            <p className="break-words text-[1rem] font-semibold text-ink">{r.readback}</p>
            {r.description ? <p className="whitespace-pre-wrap break-words text-[0.9375rem] text-ink-2">{r.description}</p> : null}
            {r.last_dry_run ? (
              <p className="text-sm text-ink-2">
                <span className="label-caps mr-2">Last test on stored cases</span>
                {dryRunSentence(r.last_dry_run)}
              </p>
            ) : null}
            {r.versions && r.versions.length > 0 ? (
              <section aria-labelledby="versions-title">
                <h3 id="versions-title" className="label-caps mb-1.5">
                  Versions
                </h3>
                <ol className="space-y-2">
                  {[...r.versions]
                    .sort((a, b) => b.version - a.version)
                    .map((v) => (
                      <li key={v.version} className="rounded-md border border-rule bg-surface px-3 py-2 text-sm">
                        <p className="font-semibold text-ink">
                          Version {v.version}
                          {v.version === r.version ? <span className="ml-2 font-normal text-muted">(current)</span> : null}
                          {v.version === r.published_version ? <span className="ml-2 font-normal text-muted">(public)</span> : null}
                        </p>
                        <p className="break-words text-ink">{v.readback}</p>
                        <p className="break-words text-ink-2">
                          {v.created_by}, <time dateTime={v.created_at}>{formatUtc(v.created_at)}</time>: {v.reason}
                        </p>
                      </li>
                    ))}
                </ol>
              </section>
            ) : null}
          </>
        ) : null}
        <section aria-labelledby="changes-title">
          <h3 id="changes-title" className="label-caps mb-1.5">
            Change log
          </h3>
          {changes.error ? <CustomError error={changes.error} /> : null}
          {changes.data && changes.data.length === 0 ? <p className="text-sm text-muted">Nothing logged yet.</p> : null}
          <ul className="space-y-2">
            {(changes.data ?? []).map((c) => (
              <li key={c.id} className="rounded-md border border-rule bg-surface px-3 py-2 text-sm">
                <p className="text-ink">
                  <span className="font-semibold">{ACTION_TEXT[c.action] ?? c.action}</span>
                  {c.version ? ` (version ${c.version})` : ""} by {c.actor},{" "}
                  <time dateTime={c.at} className="text-muted">
                    {formatUtc(c.at)}
                  </time>
                </p>
                <p className="whitespace-pre-wrap break-words text-ink-2">{c.reason}</p>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </SideSheet>
  );
}
