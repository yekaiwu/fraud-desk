"use client";

import { Send } from "lucide-react";
import { useState } from "react";
import { STATUS_TITLE, statusTone, TURN_LIMIT, UNSUPPORTED_TEXT } from "@/lib/customRules";
import type { DraftResponse, DryRunResult } from "@/lib/types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { inputClasses } from "../ui/Field";
import { Notice } from "../ui/Notice";
import { CandidateList } from "./CandidateList";
import { DryRunPanel } from "./DryRunPanel";
import { DuplicateNotes } from "./DuplicateNotes";
import { IdeasList } from "./IdeasList";
import { QuestionCard } from "./QuestionCard";
import { SaveRuleForm, type SaveValues } from "./SaveRuleForm";
import { WillWillNot } from "./WillWillNot";

export interface DryRunState {
  result: DryRunResult | null;
  loading: boolean;
  error: string | null;
  run: () => void;
  confirmed: boolean;
  setConfirmed: (v: boolean) => void;
}

function Readback({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-brand-2/30 bg-brand-tint/50 p-4">
      <p className="label-caps mb-1">The rule in plain words</p>
      <p className="break-words text-[1.0625rem] font-semibold leading-snug text-ink">{text}</p>
    </div>
  );
}

function UnsupportedList({ draft }: { draft: DraftResponse }) {
  if (draft.unsupported_parts.length === 0) return null;
  return (
    <ul aria-label="Parts that cannot be checked" className="space-y-1.5 text-[0.9375rem]">
      {draft.unsupported_parts.map((u, i) => (
        <li key={i} className="break-words rounded-md border border-rule bg-surface px-3 py-2">
          <span className="font-semibold text-ink">&ldquo;{u.phrase}&rdquo;</span>
          <span className="text-ink-2">: {UNSUPPORTED_TEXT[u.reason] ?? u.reason}.</span>
          {u.detail ? <span className="block text-sm text-muted">{u.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function HowRead({ draft }: { draft: DraftResponse }) {
  if (draft.phrase_map.length === 0) return null;
  return (
    <details className="text-sm text-ink-2">
      <summary className="cursor-pointer font-medium text-brand-2 underline underline-offset-2">How your words were read</summary>
      <ul className="mt-1.5 space-y-0.5 pl-1">
        {draft.phrase_map.map((p, i) => (
          <li key={i} className="break-words">
            &ldquo;{p.phrase}&rdquo; <span aria-hidden>&rarr;</span>
            {" "}<span className="sr-only">was read as</span> {p.slot === null ? <span className="font-medium text-sev-medium">not understood</span> : p.slot === "filler" ? "no meaning for the rule" : p.slot.replace(/_/g, " ")}
          </li>
        ))}
      </ul>
    </details>
  );
}

function TicketBlock({ draft, busy, onSubmit }: { draft: DraftResponse; busy: boolean; onSubmit: () => void }) {
  if (draft.ticket) {
    return (
      <Notice variant="info" title="Sent to the tech team">
        <p className="break-words">{draft.ticket.message}</p>
      </Notice>
    );
  }
  if (draft.unsupported_parts.length === 0 || draft.status === "refused") return null;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-rule bg-paper-2/60 p-3.5">
      <p className="min-w-0 flex-1 text-[0.9375rem] text-ink-2">The part a rule cannot check can be sent to the tech team as a request.</p>
      <Button variant="secondary" icon={<Send aria-hidden className="h-4 w-4" />} loading={busy} onClick={onSubmit}>
        Send the missing part to the tech team
      </Button>
    </div>
  );
}

function Reply({ busy, onSend }: { busy: boolean; onSend: (t: string) => void }) {
  const [text, setText] = useState("");
  return (
    <form
      aria-label="Reply in your own words"
      className="space-y-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) {
          onSend(text.trim());
          setText("");
        }
      }}
    >
      <label htmlFor="draft-reply" className="block text-sm font-semibold text-ink">
        Or answer in your own words
      </label>
      <textarea id="draft-reply" rows={2} className={`${inputClasses} h-auto py-2`} value={text} onChange={(e) => setText(e.target.value)} />
      <Button type="submit" variant="secondary" disabled={!text.trim() || busy} loading={busy}>
        Send reply
      </Button>
    </form>
  );
}

/**
 * One turn of the conversation, laid out by status. The sentences that matter (the rule in words, what it will and
 * will not check, the ticket message, the refusal) are written by the server from the rule, not by the model.
 */
export function DraftResult({
  draft,
  busy,
  ticketBusy,
  saving,
  dryRun,
  onAnswer,
  onChoose,
  onReply,
  onTry,
  onSubmitTicket,
  onSave,
}: {
  draft: DraftResponse;
  busy: boolean;
  ticketBusy: boolean;
  saving: boolean;
  dryRun: DryRunState;
  onAnswer: (questionId: string, optionId: string) => void;
  onChoose: (index: number) => void;
  onReply: (text: string) => void;
  onTry: (text: string) => void;
  onSubmitTicket: () => void;
  onSave: (v: SaveValues) => void;
}) {
  const open = draft.questions.length > 0;
  const hasRule = draft.spec !== null && draft.readback !== null;
  const showSave = (draft.status === "ready" || draft.status === "partially_supported") && hasRule && !open;
  return (
    <section aria-label="The assistant's answer" className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Badge variant={statusTone(draft.status)} icon={null}>
          {STATUS_TITLE[draft.status]}
        </Badge>
        <span className="tabular text-sm text-muted">
          Turn {draft.turn} of {TURN_LIMIT}
        </span>
      </div>

      {draft.understood ? (
        <p className="break-words text-[0.9375rem] text-ink-2">
          <span className="label-caps mr-2">What the assistant understood</span>
          {draft.understood}
        </p>
      ) : null}

      {draft.status === "refused" && draft.refusal ? (
        <Notice variant="error" title="This request was refused">
          <p className="break-words">{draft.refusal.message}</p>
          <p className="mt-1 text-sm">The attempt is logged. It is not sent to the tech team.</p>
        </Notice>
      ) : null}

      {draft.notes.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-sm text-ink-2">
          {draft.notes.map((n, i) => (
            <li key={i} className="break-words">
              {n}
            </li>
          ))}
        </ul>
      ) : null}

      {draft.status === "unsupported" ? (
        <>
          <UnsupportedList draft={draft} />
          <TicketBlock draft={draft} busy={ticketBusy} onSubmit={onSubmitTicket} />
        </>
      ) : null}

      {draft.status === "ambiguous" ? <CandidateList candidates={draft.candidates} busy={busy} onChoose={onChoose} /> : null}

      {hasRule && draft.status !== "refused" && draft.status !== "unsupported" ? <Readback text={draft.readback ?? ""} /> : null}

      {draft.status === "partially_supported" ? <WillWillNot willCheck={draft.will_check} willNotCheck={draft.will_not_check} /> : null}
      {draft.status === "ready" && draft.will_check.length > 0 ? (
        <ul aria-label="What this rule will check" className="list-disc space-y-0.5 pl-5 text-[0.9375rem] text-ink-2">
          {draft.will_check.map((t, i) => (
            <li key={i} className="break-words">
              {t}
            </li>
          ))}
        </ul>
      ) : null}

      {draft.assumptions.length > 0 ? (
        <div>
          <p className="label-caps mb-0.5">Assumptions the assistant made</p>
          <ul className="list-disc space-y-0.5 pl-5 text-[0.9375rem] text-ink-2">
            {draft.assumptions.map((a, i) => (
              <li key={i} className="break-words">
                {a}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {open ? (
        <div className="space-y-3">
          {draft.questions.map((q) => (
            <QuestionCard key={`${draft.draft_id}-${draft.turn}-${q.id}`} q={q} busy={busy} onAnswer={onAnswer} />
          ))}
        </div>
      ) : null}
      {draft.status === "needs_clarification" ? <Reply busy={busy} onSend={onReply} /> : null}

      {draft.status === "partially_supported" ? <TicketBlock draft={draft} busy={ticketBusy} onSubmit={onSubmitTicket} /> : null}

      <DuplicateNotes notes={draft.duplicates} />
      <IdeasList ideas={draft.ideas} onTry={onTry} />
      <HowRead draft={draft} />

      {showSave && draft.save_allowed ? (
        <>
          <DryRunPanel result={dryRun.result} loading={dryRun.loading} error={dryRun.error} onRun={dryRun.run} confirmed={dryRun.confirmed} onConfirm={dryRun.setConfirmed} />
          <SaveRuleForm draft={draft} dryRun={dryRun.result} warningsConfirmed={dryRun.confirmed} saving={saving} onSave={onSave} />
        </>
      ) : null}
      {showSave && !draft.save_allowed ? (
        <Notice variant="warning" title="This rule cannot be saved as it stands">
          See the notes above. Change what it checks, or use the rule that already exists.
        </Notice>
      ) : null}
      {open && (draft.status === "ready" || draft.status === "partially_supported") ? <p className="text-sm text-ink-2">Answer the question above to carry on. Nothing can be saved until you do.</p> : null}
    </section>
  );
}
