"use client";

import { Send, Sparkles } from "lucide-react";
import { useRef, useState } from "react";
import { dryRunCustomDraft, saveCustomRule, sendCustomTurn, startCustomDraft, submitCustomTicket } from "@/lib/api";
import { CUSTOM_SEVERITIES, REQUEST_MAX_CHARS, REQUEST_MIN_CHARS, TURN_LIMIT } from "@/lib/customRules";
import { severityLabel } from "@/lib/labels";
import type { CustomRuleView, CustomSeverity, CustomTurnRequest, DraftResponse, DryRunResult, TicketRef } from "@/lib/types";
import { Button } from "../ui/Button";
import { inputClasses } from "../ui/Field";
import { Notice } from "../ui/Notice";
import { CustomError, bodyOf, isAuthoringDown } from "./CustomError";
import { DraftResult, type DryRunState } from "./DraftResult";
import type { SaveValues } from "./SaveRuleForm";

export type EditTarget = { id: string; name: string };

function lastTurn(d: DraftResponse): boolean {
  return d.turns_left <= 0;
}

/**
 * The inside of the "Create a custom check" popup. The officer describes a check in plain English; every request is
 * classified first and the answer is shown by status. A conversation is one draft (at most six turns). Nothing is saved
 * until the officer saves: the test on stored cases and the confirmations come first.
 */
export function RuleAssistant({ edit, onSaved, onCancelEdit, onTicket, onOpenRules }: { edit: EditTarget | null; onSaved: (rule: CustomRuleView) => void; onCancelEdit: () => void; onTicket: () => void; onOpenRules: () => void }) {
  const [text, setText] = useState("");
  const [severity, setSeverity] = useState<CustomSeverity | "">("");
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [asked, setAsked] = useState("");
  const [busy, setBusy] = useState(false);
  const [ticketBusy, setTicketBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [dry, setDry] = useState<{ result: DryRunResult | null; loading: boolean; error: string | null }>({ result: null, loading: false, error: null });
  const [confirmed, setConfirmed] = useState(false);
  const [saved, setSaved] = useState<{ rule: CustomRuleView; ticket: TicketRef | null } | null>(null);
  const [ideaTicket, setIdeaTicket] = useState<TicketRef | string | null>(null);
  // the draft the dry run belongs to: an answer that arrives after the officer moved on is dropped
  const current = useRef<string | null>(null);

  const tooShort = text.trim().length < REQUEST_MIN_CHARS;

  function apply(d: DraftResponse) {
    current.current = `${d.draft_id}:${d.turn}`;
    setDraft(d);
    setConfirmed(false);
    setDry({ result: d.dry_run, loading: false, error: null });
    const testable = (d.status === "ready" || d.status === "partially_supported") && d.spec !== null && d.questions.length === 0 && d.save_allowed;
    if (testable && d.dry_run?.spec_hash !== d.spec_hash) void runDry(d);
  }

  async function runDry(d: DraftResponse) {
    const key = `${d.draft_id}:${d.turn}`;
    setDry((x) => ({ ...x, loading: true, error: null }));
    try {
      const r = await dryRunCustomDraft(d.draft_id);
      if (current.current === key) setDry({ result: r, loading: false, error: null });
    } catch (e) {
      if (current.current === key) setDry({ result: null, loading: false, error: bodyOf(e).message });
    }
  }

  async function guard(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const start = () =>
    guard(async () => {
      setSaved(null);
      setIdeaTicket(null);
      setAsked(text.trim());
      apply(await startCustomDraft(text.trim(), severity || undefined, edit?.id));
    });

  const turn = (req: CustomTurnRequest) =>
    guard(async () => {
      if (!draft) return;
      const reply = await sendCustomTurn(draft.draft_id, req);
      // the officer may have started a new request while this one was away: drop the old answer
      if (current.current?.startsWith(`${draft.draft_id}:`)) apply(reply);
    });

  async function submitTicket() {
    if (!draft) return;
    setTicketBusy(true);
    setError(null);
    try {
      const r = await submitCustomTicket({ draft_id: draft.draft_id });
      setDraft({ ...draft, ticket: { id: r.ticket.id, status: "submitted", message: r.message } });
      onTicket();
    } catch (e) {
      setError(e);
    } finally {
      setTicketBusy(false);
    }
  }

  async function sendIdea() {
    setTicketBusy(true);
    try {
      const r = await submitCustomTicket({ text: (asked || text).trim(), severity: severity || undefined });
      setIdeaTicket(r.message);
      onTicket();
    } catch (e) {
      setError(e);
    } finally {
      setTicketBusy(false);
    }
  }

  async function save(v: SaveValues) {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const r = await saveCustomRule({
        draft_id: draft.draft_id,
        name: v.name,
        description: v.description || undefined,
        reason: v.reason,
        accept_narrower: v.accept_narrower,
        confirm_warnings: confirmed,
        submit_ticket: v.submit_ticket,
      });
      setSaved(r);
      setDraft(null);
      onSaved(r.rule);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    current.current = null;
    setDraft(null);
    setSaved(null);
    setIdeaTicket(null);
    setError(null);
    setText("");
    setSeverity("");
    setAsked("");
  }

  const fallback =
    error && (isAuthoringDown(error) || bodyOf(error).code === "draft_turn_limit") && !ideaTicket ? (
      <Button size="sm" variant="secondary" icon={<Send aria-hidden className="h-4 w-4" />} loading={ticketBusy} onClick={() => void sendIdea()} disabled={(asked || text).trim().length < REQUEST_MIN_CHARS}>
        Send this idea to the tech team
      </Button>
    ) : undefined;

  if (saved) {
    return (
      <div className="space-y-4">
        <Notice variant="success" title={`Saved: ${saved.rule.name}`}>
          <p className="break-words">{saved.rule.readback}</p>
          <p className="mt-1">
            It is private to you and runs on analyses you start from now on. Cases already analysed keep their results until they are run again. Make it public from My checks if others should be able to adopt it.
          </p>
          {saved.ticket ? <p className="mt-1 break-words font-medium text-ink">{saved.ticket.message}</p> : null}
        </Notice>
        <div className="flex flex-wrap gap-2">
          <Button onClick={onOpenRules}>Go to my checks</Button>
          <Button
            variant="secondary"
            onClick={() => {
              onCancelEdit();
              reset();
            }}
          >
            Write another check
          </Button>
        </div>
      </div>
    );
  }

  const dryState: DryRunState = {
    result: dry.result,
    loading: dry.loading,
    error: dry.error,
    run: () => draft && void runDry(draft),
    confirmed,
    setConfirmed,
  };

  return (
    <div className="space-y-5">
      {edit ? (
        <Notice variant="info" title={`New version of ${edit.name}`} action={<Button size="sm" variant="secondary" onClick={() => { onCancelEdit(); reset(); }}>Cancel the change</Button>}>
          Describe the whole rule as you want it now. It is saved as a new version; the current one keeps running until then.
        </Notice>
      ) : null}

      {draft === null ? (
        <form
          aria-label="Describe a rule"
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!tooShort && !busy) void start();
          }}
        >
          <div>
            <label htmlFor="rule-request" className="mb-1 block text-sm font-semibold text-ink">
              Describe the check in plain English
            </label>
            <p className="mb-1.5 text-sm text-muted">
              For example: &ldquo;flag as low if an invoice total is more than 100,000&rdquo;. Describe the rule, not a case: do not paste names, numbers or details of a customer. Your words go to the model and are kept with the request.
            </p>
            <textarea id="rule-request" rows={4} maxLength={REQUEST_MAX_CHARS} className={`${inputClasses} h-auto py-2`} value={text} onChange={(e) => setText(e.target.value)} aria-describedby="rule-request-count" />
            <p id="rule-request-count" className="tabular mt-1 text-xs text-muted">
              {text.length} of {REQUEST_MAX_CHARS} characters
            </p>
          </div>
          <div className="max-w-xs">
            <label htmlFor="rule-severity" className="mb-1 block text-sm font-semibold text-ink">
              How serious should a flag be?
            </label>
            <select id="rule-severity" className={inputClasses} value={severity} onChange={(e) => setSeverity(e.target.value as CustomSeverity | "")}>
              <option value="">As I wrote it above</option>
              {CUSTOM_SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {severityLabel(s)}
                </option>
              ))}
            </select>
            <p className="mt-1 text-sm text-muted">Never higher than medium. A custom rule can never stop a case on its own.</p>
          </div>
          <Button type="submit" size="lg" icon={<Sparkles aria-hidden className="h-4 w-4" />} loading={busy} disabled={tooShort}>
            Ask the assistant
          </Button>
        </form>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-rule bg-paper-2/60 p-3.5">
            <p className="min-w-0 flex-1 break-words text-[0.9375rem] text-ink">
              <span className="label-caps mr-2">Your request</span>
              {asked}
            </p>
            <Button size="sm" variant="secondary" onClick={reset}>
              Start a new request
            </Button>
          </div>
          <DraftResult
            draft={draft}
            busy={busy}
            ticketBusy={ticketBusy}
            saving={saving}
            dryRun={dryState}
            onAnswer={(q, o) => void turn({ answer: { question_id: q, option_id: o } })}
            onChoose={(i) => void turn({ choose_candidate: i })}
            onReply={(t) => void turn({ text: t })}
            onTry={(t) => {
              reset();
              setText(t);
            }}
            onSubmitTicket={() => void submitTicket()}
            onSave={(v) => void save(v)}
          />
          {lastTurn(draft) && draft.status !== "refused" && (draft.questions.length > 0 || draft.status === "needs_clarification" || draft.status === "ambiguous") ? (
            <Notice variant="warning" title={`That was the last of ${TURN_LIMIT} turns`}>
              Start again with the whole idea in one message, or send it to the tech team.
            </Notice>
          ) : null}
        </div>
      )}

      {error ? <CustomError error={error} action={fallback} /> : null}
      {ideaTicket ? (
        <Notice variant="info" title="Sent to the tech team">
          <p className="break-words">{typeof ideaTicket === "string" ? ideaTicket : ideaTicket.message}</p>
        </Notice>
      ) : null}
    </div>
  );
}
