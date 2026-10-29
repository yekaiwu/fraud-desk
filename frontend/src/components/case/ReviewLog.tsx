"use client";

import clsx from "clsx";
import { VERDICT_LABELS } from "@/lib/constants";
import { formatUtc } from "@/lib/format";
import type { CaseDetail, ReviewEntry, Verdict } from "@/lib/types";

function decisionText(e: ReviewEntry): string {
  if (e.kind === "verdict") return `Verdict: ${VERDICT_LABELS[e.decision as Verdict] ?? e.decision}`;
  if (e.kind === "second_approval") return `Second approval: ${e.decision}${e.target_entry_id ? ` of ${e.target_entry_id}` : ""}`;
  return `Flag decision: ${e.decision}${e.finding_id ? ` (finding ${e.finding_id})` : e.flag_ids.length ? ` (${e.flag_ids.length} ${e.flag_ids.length === 1 ? "flag" : "flags"})` : ""}`;
}
const STATUS_TEXT: Record<ReviewEntry["status"], string> = { effective: "Effective", pending_second_approval: "Needs 2nd approval", declined: "Declined" };

/** Oldest first; entries from earlier versions are grouped under "Version n". Reasons are untrusted text. */
export function ReviewLog({ entries, currentVersion }: { entries: CaseDetail["review"]; currentVersion: number }) {
  if (entries.length === 0) return <p className="text-sm text-muted">No decisions yet.</p>;
  const versions = [...new Set(entries.map((e) => e.case_version))].sort((a, b) => a - b);
  return (
    <div className="space-y-4">
      {versions.map((v) => (
        <section key={v} aria-label={`Version ${v}`}>
          {versions.length > 1 || v !== currentVersion ? <h4 className="label-caps mb-1">Version {v}{v === currentVersion ? " (current)" : ""}</h4> : null}
          <ol className="space-y-2">
            {entries.filter((e) => e.case_version === v).map((e) => (
              <li key={e.id} className="rounded-lg border border-border bg-card p-3 text-sm">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-mono text-xs font-semibold">{e.id}</span>
                  <strong>{e.reviewer}</strong>
                  <span>{decisionText(e)}</span>
                  <span className={clsx("rounded-full border px-2 py-0.5 text-xs font-semibold", e.status === "effective" ? "border-border bg-surface" : "border-sev-medium/40 bg-amber-50 text-sev-medium")}>{STATUS_TEXT[e.status]}</span>
                  <span className="ml-auto text-xs text-muted tabular">v{e.case_version} · {formatUtc(e.created_at)}</span>
                </div>
                <p className="mt-1.5 whitespace-pre-wrap break-words">{e.reason}</p>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}
