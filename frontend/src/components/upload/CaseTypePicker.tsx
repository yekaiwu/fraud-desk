"use client";

import clsx from "clsx";
import { ArrowRightLeft, Banknote, Building2, CheckCircle2, type LucideIcon } from "lucide-react";
import { laneLabel } from "@/lib/labels";
import type { UseCase } from "@/lib/types";
import { CASE_TYPES } from "@/lib/uploadChecklist";

const ICON: Record<UseCase, LucideIcon> = { loan: Banknote, kyc: Building2, aml: ArrowRightLeft };

/** Three large radio cards. Native radios (arrow keys move between them), styled through :has(). */
export function CaseTypePicker({ value, onChange }: { value: UseCase; onChange: (v: UseCase) => void }) {
  return (
    <fieldset>
      <legend className="sr-only">What kind of case is this?</legend>
      <div className="grid gap-3 md:grid-cols-3">
        {CASE_TYPES.map((t) => {
          const Icon = ICON[t.id];
          const selected = value === t.id;
          return (
            <label
              key={t.id}
              className={clsx(
                "relative flex min-h-11 cursor-pointer flex-col gap-2 rounded-lg border p-4 transition-colors",
                "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[var(--ring)]",
                selected ? "border-brand bg-brand-tint shadow-[inset_0_0_0_1px_var(--brand)]" : "border-rule-strong bg-surface hover:border-brand-2 hover:bg-brand-tint/50",
              )}
            >
              <input type="radio" name="use_case" value={t.id} checked={selected} onChange={() => onChange(t.id)} className="peer sr-only" />
              <span className="flex items-start justify-between gap-2">
                <span className={clsx("grid h-10 w-10 place-items-center rounded-full border", selected ? "border-brand bg-brand text-on-brand" : "border-rule bg-paper text-brand-2")}>
                  <Icon aria-hidden className="h-5 w-5" />
                </span>
                {selected ? (
                  <span className="inline-flex items-center gap-1 text-xs font-semibold text-brand">
                    <CheckCircle2 aria-hidden className="h-4 w-4" />
                    Selected
                  </span>
                ) : (
                  <span aria-hidden className="mt-1 h-4 w-4 rounded-full border-2 border-rule-strong" />
                )}
              </span>
              <span className="font-display text-[1.1875rem] font-semibold leading-snug text-ink">{t.title}</span>
              <span className="text-[0.9375rem] leading-snug text-ink-2">{t.blurb}</span>
              <span className="mt-auto border-t border-rule pt-2 text-sm text-ink-2">
                <span className="label-caps block">Usually goes to</span>
                <span className="font-semibold text-brand">{laneLabel(t.lane)}</span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
