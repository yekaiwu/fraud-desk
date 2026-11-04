"use client";

import { useState } from "react";
import { DESCRIPTION_MAX, NAME_MAX, NAME_MIN, REASON_MAX, REASON_MIN } from "@/lib/customRules";
import type { DraftResponse, DryRunResult } from "@/lib/types";
import { Button } from "../ui/Button";
import { Field, inputClasses } from "../ui/Field";

export interface SaveValues {
  name: string;
  description: string;
  reason: string;
  accept_narrower: boolean;
  submit_ticket: boolean;
}

/**
 * Name, reason and the confirmations the draft needs. Save stays off until every one is in place: a name, a reason of
 * at least 5 characters, a test on stored cases, the "checks less than I asked" tick for a partial rule and "Save
 * anyway" when the test warned.
 */
export function SaveRuleForm({
  draft,
  dryRun,
  warningsConfirmed,
  saving,
  onSave,
  initialName = "",
}: {
  draft: DraftResponse;
  dryRun: DryRunResult | null;
  warningsConfirmed: boolean;
  saving: boolean;
  onSave: (v: SaveValues) => void;
  initialName?: string;
}) {
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState("");
  const [reason, setReason] = useState("");
  const [narrower, setNarrower] = useState(false);
  const [ticket, setTicket] = useState(false);
  const nameOk = name.trim().length >= NAME_MIN && name.trim().length <= NAME_MAX;
  const reasonOk = reason.trim().length >= REASON_MIN;
  const tested = dryRun !== null && dryRun.spec_hash === draft.spec_hash;
  const warned = tested && dryRun.needs_confirmation;
  const missing: string[] = [];
  if (!nameOk) missing.push("a name");
  if (!reasonOk) missing.push("a reason");
  if (!tested) missing.push("the test on stored cases");
  if (warned && !warningsConfirmed) missing.push("\"Save anyway\" under the test");
  if (draft.needs_narrower_ack && !narrower) missing.push("the tick that this rule checks less than you asked");
  const canSave = draft.save_allowed && missing.length === 0;
  const canTicket = draft.unsupported_parts.length > 0 && draft.ticket === null;

  return (
    <form
      aria-label="Save this rule"
      className="space-y-4 rounded-lg border border-rule bg-surface p-4 shadow-paper"
      onSubmit={(e) => {
        e.preventDefault();
        if (canSave) onSave({ name: name.trim(), description: description.trim(), reason: reason.trim(), accept_narrower: narrower, submit_ticket: ticket });
      }}
    >
      <h4 className="text-[1rem] font-semibold text-ink">Save this rule</h4>
      <Field label="Name" hint={`${NAME_MIN} to ${NAME_MAX} characters. Other accounts see it if you make the rule public.`} value={name} maxLength={NAME_MAX} onChange={(e) => setName(e.target.value)} />
      <div>
        <label htmlFor="rule-description" className="mb-1 block text-sm font-semibold text-ink">
          Description <span className="font-normal text-muted">(optional)</span>
        </label>
        <textarea id="rule-description" rows={2} maxLength={DESCRIPTION_MAX} className={`${inputClasses} h-auto py-2`} value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div>
        <label htmlFor="rule-reason" className="mb-1 block text-sm font-semibold text-ink">
          Why are you adding this rule?
        </label>
        <p className="mb-1.5 text-sm text-muted">At least {REASON_MIN} characters. It goes in the change log. Do not paste case details.</p>
        <textarea id="rule-reason" rows={2} maxLength={REASON_MAX} className={`${inputClasses} h-auto py-2`} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>

      {draft.needs_narrower_ack ? (
        <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-md border border-sev-medium/40 bg-sev-medium-tint px-3 py-2.5 text-[0.9375rem] text-ink">
          <input type="checkbox" checked={narrower} onChange={(e) => setNarrower(e.target.checked)} className="mt-1 h-4 w-4 accent-[var(--brand-2)]" />
          <span>I understand this rule checks less than I asked for.</span>
        </label>
      ) : null}
      {canTicket ? (
        <label className="flex min-h-11 cursor-pointer items-start gap-3 px-1 py-1 text-[0.9375rem] text-ink">
          <input type="checkbox" checked={ticket} onChange={(e) => setTicket(e.target.checked)} className="mt-1 h-4 w-4 accent-[var(--brand-2)]" />
          <span>Also send the part it cannot check to the tech team.</span>
        </label>
      ) : null}

      {missing.length > 0 ? <p className="text-sm text-ink-2">Still needed to save: {missing.join(", ")}.</p> : null}
      <Button type="submit" loading={saving} disabled={!canSave}>
        Save rule
      </Button>
    </form>
  );
}
