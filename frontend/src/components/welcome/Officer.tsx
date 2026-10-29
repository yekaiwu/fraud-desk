import { ArrowDown, Check, ClipboardList, Mail, ShieldCheck, Users } from "lucide-react";
import { Badge, LaneBadge, RiskBadge } from "@/components/ui/Badge";
import { Panel } from "@/components/ui/Panel";
import { VERDICT_LABELS } from "@/lib/constants";
import { coverageText, laneDescription } from "@/lib/labels";
import type { Band, Lane } from "@/lib/types";
import { Section, SectionHead } from "./parts";

const WORKLIST: { name: string; score: number; band: Band; lane: Lane; ran: number; of: number }[] = [
  { name: "Orchard Quay Trading Pte. Ltd.", score: 82, band: "high", lane: "aml", ran: 47, of: 54 },
  { name: "Telok Ayer Imports Pte. Ltd.", score: 64, band: "high", lane: "edd", ran: 39, of: 54 },
  { name: "Kallang Freight Services Pte. Ltd.", score: 30, band: "medium", lane: "loan", ran: 41, of: 54 },
  { name: "Bukit Timah Bakeries Pte. Ltd.", score: 8, band: "low", lane: "fast", ran: 52, of: 54 },
];

const TRAIL: [string, string, string][] = [
  ["10:42", "R. Tan", "Opened the case."],
  ["10:51", "R. Tan", "Marked flag 1 as a real concern. Reason: the bank confirms the printed balance, not the deposit."],
  ["10:58", "R. Tan", "Asked to dismiss flag 3 (high concern). Waiting for a second approver."],
  ["11:03", "J. Lim", "Second approver: agreed to dismiss flag 3. Reason: matches the signed supplier contract."],
];

const POINTS = [
  { icon: ClipboardList, head: "Ranked, not decided", text: "The worklist puts the riskiest case first. Ranking tells you where to start; it never closes a case." },
  { icon: Check, head: "A suggestion you can ignore", text: "The desk recommends a next step with its reasons. It is never preselected: you choose, and you can choose differently." },
  { icon: Users, head: "Two people for a high concern", text: "Dismissing a high concern needs a second, different reviewer, who sees the first person’s reason before agreeing." },
  { icon: ShieldCheck, head: "Every step on the record", text: "Who did what, when and why, kept with the case in order, so a decision can be explained months later." },
  { icon: Mail, head: "Nothing goes to the customer", text: "Even “request documents” only records the request. Any contact with the applicant stays with you." },
] as const;

export function Officer() {
  const verdicts = ["approve", "request_documents", "refer_edd", "escalate_aml", "reject_or_escalate"] as const;
  return (
    <Section id="officer" tone="dark" labelledBy="officer-title">
      <SectionHead
        dark
        no="07"
        label="The officer stays in charge"
        id="officer-title"
        title="The desk recommends. A person decides."
        intro="The last step is deliberately slow: a choice, a written reason, and a second pair of eyes where the stakes are highest."
      />
      <div className="mt-12 grid gap-10 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-14">
        <ul className="reveal space-y-6">
          {POINTS.map((p) => (
            <li key={p.head} className="flex gap-4 border-t border-on-brand/20 pt-5">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-md border border-brass-light/50 text-brass-light">
                <p.icon aria-hidden className="h-[18px] w-[18px]" />
              </span>
              <div>
                <h3 className="font-display text-[1.25rem] leading-snug text-on-brand">{p.head}</h3>
                <p className="mt-1 text-[0.9375rem] leading-relaxed text-on-brand-muted">{p.text}</p>
              </div>
            </li>
          ))}
        </ul>

        <div className="reveal reveal-late space-y-5" aria-label="Sample screens (fictitious data)" role="group">
          <Panel title="Worklist" eyebrow="Highest risk first" headingLevel={3} padding="none" className="overflow-hidden text-ink">
            <ol className="divide-y divide-rule text-ink">
              {WORKLIST.map((w, i) => (
                <li key={w.name} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3 sm:px-5">
                  <span className="num w-5 text-sm text-muted">{i + 1}</span>
                  <span className="w-10 font-display text-2xl font-semibold tabular text-ink">{w.score}</span>
                  <div className="min-w-0 flex-1 basis-48">
                    <p className="truncate text-[0.9375rem] font-semibold text-ink">{w.name}</p>
                    <p className="text-xs text-muted">{coverageText(w.ran, w.of)}</p>
                  </div>
                  <span className="flex flex-wrap items-center gap-1.5">
                    <RiskBadge band={w.band} />
                    <LaneBadge lane={w.lane} />
                  </span>
                </li>
              ))}
            </ol>
          </Panel>

          <Panel tone="attention" eyebrow="Suggested next step (you decide)" title="Refer to enhanced due diligence" headingLevel={3} className="text-ink">
            <p className="text-[0.9375rem] text-ink-2">Why: a high concern blocks the fast lane. {laneDescription("edd")}</p>
            <ol className="mt-4 grid gap-3 sm:grid-cols-3">
              <li className="rounded-md border border-rule bg-paper p-3">
                <p className="label-caps flex items-center gap-1.5">
                  <span className="num grid h-5 w-5 place-items-center rounded-full bg-brand text-[0.7rem] text-on-brand">1</span> Choose
                </p>
                <ul className="mt-2 space-y-1 text-xs text-ink-2" aria-label="Decisions, none preselected">
                  {verdicts.map((v) => (
                    <li key={v} className="flex items-center gap-1.5">
                      <span aria-hidden className="h-3 w-3 shrink-0 rounded-full border border-rule-strong bg-surface" />
                      {VERDICT_LABELS[v]}
                    </li>
                  ))}
                </ul>
              </li>
              <li className="rounded-md border border-rule bg-paper p-3">
                <p className="label-caps flex items-center gap-1.5">
                  <span className="num grid h-5 w-5 place-items-center rounded-full bg-brand text-[0.7rem] text-on-brand">2</span> Give a reason
                </p>
                <p className="mt-2 rounded-sm border border-rule-strong bg-surface px-2 py-1.5 text-xs leading-snug text-muted">Required, at least 5 characters. A decision cannot be recorded without one.</p>
              </li>
              <li className="rounded-md border border-rule bg-paper p-3">
                <p className="label-caps flex items-center gap-1.5">
                  <span className="num grid h-5 w-5 place-items-center rounded-full bg-brand text-[0.7rem] text-on-brand">3</span> Confirm
                </p>
                <p className="mt-2 text-xs leading-snug text-ink-2">Review the summary, then record. A second approver is asked for if a high concern is dismissed.</p>
              </li>
            </ol>
          </Panel>

          <Panel title="Audit trail" eyebrow="Kept with the case" headingLevel={3} className="text-ink">
            <ol className="space-y-3">
              {TRAIL.map(([time, who, what]) => (
                <li key={time} className="grid grid-cols-[3rem_minmax(0,1fr)] gap-3 text-[0.9375rem]">
                  <span className="num pt-0.5 text-xs text-muted">{time}</span>
                  <span className="text-ink-2">
                    <span className="font-semibold text-ink">{who}.</span> {what}
                  </span>
                </li>
              ))}
            </ol>
            <p className="mt-4 flex items-center gap-2 border-t border-rule pt-3 text-xs text-muted">
              <Badge variant="neutral" icon={ArrowDown}>
                Newest last
              </Badge>
              Sample entries; names are fictitious.
            </p>
          </Panel>
        </div>
      </div>
    </Section>
  );
}
