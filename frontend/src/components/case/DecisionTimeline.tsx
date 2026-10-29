import clsx from "clsx";
import { VERDICT_LABELS } from "@/lib/constants";
import { formatRelative, formatUtc } from "@/lib/format";
import { CONFIRM_LABEL, DISMISS_LABEL, plural } from "@/lib/labels";
import type { CaseDetail, ReviewEntry, Verdict } from "@/lib/types";
import { Badge } from "../ui/Badge";

/** One line saying what the reviewer did, in plain words. */
export function entryAction(e: ReviewEntry): string {
  if (e.kind === "verdict") return `Decided: ${VERDICT_LABELS[e.decision as Verdict] ?? e.decision}`;
  if (e.kind === "second_approval") {
    const target = e.target_entry_id ? ` (entry ${e.target_entry_id})` : "";
    return e.decision === "approve" ? `Approved the dismissal${target}` : e.decision === "decline" ? `Declined the dismissal${target}` : `Second approval: ${e.decision}${target}`;
  }
  const what = e.decision === "dismiss" ? DISMISS_LABEL : e.decision === "confirm" ? CONFIRM_LABEL : e.decision;
  const scope = e.finding_id ? "a finding" : e.flag_ids.length ? `${e.flag_ids.length} ${plural(e.flag_ids.length, "flag")}` : "";
  return scope ? `${what}: ${scope}` : what;
}

const STATUS: Record<ReviewEntry["status"], { text: string; variant: "neutral" | "attention" | "status" }> = {
  effective: { text: "In effect", variant: "neutral" },
  pending_second_approval: { text: "Needs a second reviewer", variant: "attention" },
  declined: { text: "Declined", variant: "status" },
};

/** Newest first. The array order is oldest first, so ties on the timestamp keep the later entry on top. */
export function newestFirst(entries: ReviewEntry[]): ReviewEntry[] {
  return entries
    .map((e, i) => ({ e, i }))
    .sort((a, b) => {
      const ta = Date.parse(a.e.created_at);
      const tb = Date.parse(b.e.created_at);
      if (Number.isFinite(ta) && Number.isFinite(tb) && ta !== tb) return tb - ta;
      return b.i - a.i;
    })
    .map((x) => x.e);
}

/**
 * The audit trail as a ledger: who, what, when and why, newest first, grouped under the case version it was recorded
 * on. Reasons are untrusted text and are only ever rendered as text.
 */
export function DecisionTimeline({ entries, currentVersion }: { entries: CaseDetail["review"]; currentVersion: number }) {
  if (entries.length === 0) {
    return <p className="rounded-md border border-dashed border-rule-strong px-4 py-6 text-center text-[0.9375rem] text-muted">No decisions yet. Everything you and others record on this case will be listed here.</p>;
  }
  const sorted = newestFirst(entries);
  const versions = [...new Set(sorted.map((e) => e.case_version))].sort((a, b) => b - a);
  return (
    <div className="space-y-6">
      {versions.map((v) => (
        <section key={v} aria-label={`Version ${v}`}>
          {versions.length > 1 || v !== currentVersion ? (
            <h4 className="label-caps rule-statement-soft mb-3 pb-1.5">
              Version {v}
              {v === currentVersion ? " (current)" : ""}
            </h4>
          ) : null}
          <ol className="relative space-y-0">
            {sorted
              .filter((e) => e.case_version === v)
              .map((e, i, list) => {
                const pending = e.status === "pending_second_approval";
                const s = STATUS[e.status];
                return (
                  <li key={e.id} className="relative grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-3 pb-5 last:pb-0 sm:grid-cols-[7.5rem_1.25rem_minmax(0,1fr)]">
                    <p className="num col-span-2 col-start-1 mb-1 text-xs leading-5 text-muted sm:col-span-1 sm:row-start-1 sm:mb-0 sm:pt-0.5 sm:text-right">
                      <time dateTime={e.created_at}>{formatUtc(e.created_at)}</time>
                      <span className="block font-sans text-muted">{formatRelative(e.created_at)}</span>
                    </p>
                    <div aria-hidden className="relative sm:col-start-2 sm:row-start-1">
                      <span className={clsx("absolute left-1/2 top-1.5 h-2.5 w-2.5 -translate-x-1/2 rounded-full border-2 bg-surface", pending ? "border-brass" : "border-brand-2")} />
                      {i < list.length - 1 ? <span className="absolute bottom-[-1.25rem] left-1/2 top-4 w-px -translate-x-1/2 bg-rule-strong/50" /> : null}
                    </div>
                    <div className={clsx("min-w-0 rounded-md border p-3 text-[0.9375rem] sm:col-start-3 sm:row-start-1", pending ? "border-brass/60 bg-brass-tint/50" : "border-rule bg-surface")}>
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <strong className="text-ink">{e.reviewer}</strong>
                        <span className="text-ink-2">{entryAction(e)}</span>
                        <Badge variant={s.variant} icon={null} className="sm:ml-auto">
                          {s.text}
                        </Badge>
                      </div>
                      <p className="mt-1.5 whitespace-pre-wrap break-words text-ink">{e.reason}</p>
                      <p className="num mt-1.5 text-xs text-muted">
                        {e.id} · version {e.case_version}
                      </p>
                    </div>
                  </li>
                );
              })}
          </ol>
        </section>
      ))}
    </div>
  );
}
