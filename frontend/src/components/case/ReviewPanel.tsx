"use client";

import { Stamp } from "lucide-react";
import { VERDICT_LABELS } from "@/lib/constants";
import { formatRelative, formatUtc } from "@/lib/format";
import type { CaseDetail } from "@/lib/types";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";
import { Panel } from "../ui/Panel";
import { AddDocuments } from "./AddDocuments";
import { DecisionTimeline } from "./DecisionTimeline";
import { PendingApprovals } from "./PendingApprovals";

/** Why the verdict form is not shown, or null when it can be. */
export function formUnavailableReason(c: CaseDetail, isCurrent: boolean): string | null {
  if (!isCurrent) return "Viewing an old version. Decisions are only possible on the current version.";
  if (c.status === "processing") return "Analysis running. Decisions are possible when it finishes.";
  if (c.status === "error") return "Analysis failed. Re-run the analysis to continue.";
  if (c.status === "closed") return "Closed.";
  if (c.status === "awaiting_documents") return "Waiting for documents.";
  if (c.allowed_verdicts.length === 0) return "No verdict is available for this case right now.";
  return null;
}

/** The "Decision & activity" tab: the escalation clock, pending second approvals, how to decide, and the audit trail. */
export function ReviewPanel({ c, isCurrent, onDone, onSelectFlag, onReload, onDocsStarted, onRerun, onRecord }: {
  c: CaseDetail; isCurrent: boolean; onDone: (entryId: string) => void; onSelectFlag: (id: string) => void; onReload: () => void;
  onDocsStarted: (r: { job_id: string; version: number }) => void; onRerun: () => void; onRecord: () => void;
}) {
  const current = isCurrent;
  const why = formUnavailableReason(c, isCurrent);
  const closing = c.status === "closed" && c.decision ? [...c.review].reverse().find((e) => e.kind === "verdict" && e.status === "effective") : undefined;
  return (
    <div className="space-y-5">
      {c.escalated_at ? (
        <Notice variant="warning" title="Suspicion-formed clock running">
          Suspicion-formed clock started <strong className="tabular">{formatUtc(c.escalated_at)}</strong>, {formatRelative(c.escalated_at)}. The tool does not file an STR; compliance decides outside the tool.
        </Notice>
      ) : null}
      <PendingApprovals c={c} canAct={current && c.status === "open"} onDone={onDone} onSelectFlag={onSelectFlag} onReload={onReload} />
      <Panel title="Your decision" eyebrow="Verdict" headingLevel={3} tone={why === null ? "attention" : "default"}>
        {why === null ? (
          <div className="space-y-3">
            <p className="text-[0.9375rem] text-ink-2">
              Choose an outcome, give a reason and confirm. The tool only recommends; nothing is approved or rejected until you record it.
            </p>
            <Button icon={<Stamp aria-hidden className="h-4 w-4" />} onClick={onRecord}>
              Record decision
            </Button>
          </div>
        ) : c.status === "closed" && c.decision ? (
          <p className="text-[0.9375rem]">
            <strong>Closed: {VERDICT_LABELS[c.decision]}</strong>
            {closing ? ` by ${closing.reviewer}, ${formatUtc(closing.created_at)}. Reason: ${closing.reason}` : "."}
          </p>
        ) : (
          <p className="text-[0.9375rem] font-medium">{why}</p>
        )}
        {c.status === "error" && current ? (
          <Button className="mt-3" onClick={onRerun}>
            Re-run analysis
          </Button>
        ) : null}
      </Panel>
      {c.status === "awaiting_documents" && current ? (
        <>
          <AddDocuments caseId={c.id} onStarted={onDocsStarted} />
          <Button variant="secondary" onClick={onRerun}>
            Re-run analysis without new documents
          </Button>
        </>
      ) : null}
      <Panel title="Decision history" eyebrow="Newest first" headingLevel={3}>
        <DecisionTimeline entries={c.review} currentVersion={c.version} />
      </Panel>
    </div>
  );
}
