"use client";

import { useId, useState } from "react";
import type { Question } from "@/lib/types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";

/**
 * One question from the assistant. The proposed default is marked "Suggested" but is never selected for the officer and
 * is never sent unless they pick it: the button stays off until a choice is made.
 */
export function QuestionCard({ q, busy, onAnswer }: { q: Question; busy: boolean; onAnswer: (questionId: string, optionId: string) => void }) {
  const [choice, setChoice] = useState<string | null>(null);
  const name = useId();
  const suggested = q.options.find((o) => o.id === q.default);
  return (
    <fieldset className="rounded-lg border border-rule bg-surface p-4 shadow-paper">
      <legend className="px-1 text-[1rem] font-semibold text-ink">{q.text}</legend>
      {suggested ? (
        <p className="mb-2 text-sm text-ink-2">
          The assistant suggests <span className="font-medium text-ink">{suggested.label}</span>. Nothing is applied until you choose.
        </p>
      ) : null}
      <div className="space-y-1.5">
        {q.options.map((o) => (
          <label key={o.id} className="flex min-h-11 cursor-pointer items-center gap-3 rounded-md border border-rule px-3 py-2 text-[0.9375rem] text-ink hover:border-brand-2 hover:bg-brand-tint/40 has-[:checked]:border-brand-2 has-[:checked]:bg-brand-tint">
            <input type="radio" name={name} value={o.id} checked={choice === o.id} onChange={() => setChoice(o.id)} className="h-4 w-4 accent-[var(--brand-2)]" />
            <span className="min-w-0 flex-1 break-words">{o.label}</span>
            {o.id === q.default ? <Badge variant="neutral" icon={null}>Suggested</Badge> : null}
          </label>
        ))}
      </div>
      <div className="mt-3">
        <Button size="md" disabled={choice === null || busy} loading={busy} onClick={() => choice && onAnswer(q.id, choice)}>
          Use this answer
        </Button>
      </div>
    </fieldset>
  );
}
