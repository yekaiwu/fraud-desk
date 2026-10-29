"use client";

import clsx from "clsx";
import { AlertOctagon, AlertTriangle, Check, CheckCircle2, Info, Undo2, type LucideIcon } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useSWRConfig } from "swr";
import { resetRuleOverride, updateRuleOverride } from "@/lib/api";
import { checkName, DESCRIPTION_MAX, defaultSeverity, effectiveName, isCustomised, NAME_MAX, NOTE_MAX, stageLabel } from "@/lib/checkCatalogue";
import { severityLabel } from "@/lib/labels";
import { validateReview, type ReviewErrors } from "@/lib/reviewValidation";
import type { RuleInfo, Severity } from "@/lib/types";
import { useExplainMethod } from "@/lib/useExplain";
import { useReviewer } from "@/lib/useReviewer";
import { ReviewFields } from "../case/ReviewFields";
import { Button } from "../ui/Button";
import { inputClasses } from "../ui/Field";
import { Notice } from "../ui/Notice";
import { ChangeHistory, isChangeKey } from "./ChangeLog";
import { CheckStateBadges } from "./CheckStatus";
import { buildPatch, CATALOGUE_REASON_MAX, describeEffects, draftFrom, hasChanges, NOTHING_CHANGED, SAVE_TIMING, validateWording, type Draft } from "./editModel";
import { saveError, type SaveError } from "./saveError";
import { SideSheet } from "./SideSheet";

export type SavedKind = "saved" | "reset";

const SEVERITIES: Severity[] = ["high", "medium", "low", "info"];
const SEV_ICON: Record<Severity, { Icon: LucideIcon; cls: string }> = {
  high: { Icon: AlertOctagon, cls: "text-sev-high" },
  medium: { Icon: AlertTriangle, cls: "text-sev-medium" },
  low: { Icon: CheckCircle2, cls: "text-sev-low" },
  info: { Icon: Info, cls: "text-sev-info" },
};

function SectionTitle({ id, children, hint }: { id: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div>
      <h3 id={id} className="text-[1.0625rem]">
        {children}
      </h3>
      {hint ? <p className="mt-0.5 text-[0.9375rem] text-ink-2">{hint}</p> : null}
    </div>
  );
}

/** A switch that says its state in words and shape as well as colour. */
function SwitchRow({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  const uid = useId();
  const labelId = `${uid}-label`;
  const descId = `${uid}-desc`;
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-4 rounded-lg border border-rule-strong bg-surface p-3.5">
        <span id={labelId} className="text-base font-semibold text-ink">
          Switched on
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-labelledby={labelId}
          aria-describedby={descId}
          onClick={() => onChange(!on)}
          className={clsx(
            "relative inline-flex h-11 shrink-0 items-center gap-2.5 rounded-full border pl-1.5 pr-3.5",
            on ? "border-brand bg-brand-tint text-brand" : "border-rule-strong bg-paper-2 text-ink-2",
          )}
        >
          <span aria-hidden className={clsx("grid h-8 w-8 place-items-center rounded-full border", on ? "border-brand bg-brand text-on-brand" : "border-rule-strong bg-surface text-muted")}>
            {on ? <Check className="h-4 w-4" /> : <span className="h-0.5 w-3 rounded bg-current" />}
          </span>
          <span className="w-7 text-sm font-semibold">{on ? "On" : "Off"}</span>
        </button>
      </div>
      <p id={descId} className={clsx("text-[0.9375rem]", on ? "text-ink-2" : "font-medium text-ink")}>
        {on
          ? "This check runs on every analysis it applies to."
          : "This check is switched off. It will not run on new analyses."}
      </p>
    </div>
  );
}

function SeverityChoice({ value, onChange, def, weights }: { value: Severity; onChange: (s: Severity) => void; def: Severity; weights?: Record<Severity, number> }) {
  return (
    <fieldset>
      <legend className="sr-only">Severity</legend>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {SEVERITIES.map((s) => {
          const { Icon, cls } = SEV_ICON[s];
          return (
            <label
              key={s}
              className={clsx(
                "relative flex min-h-[4.25rem] cursor-pointer flex-col gap-1 rounded-lg border bg-surface p-2.5 transition-colors",
                "border-rule-strong hover:border-brand-2 hover:bg-brand-tint/50",
                "has-[input:checked]:border-brand has-[input:checked]:bg-brand-tint has-[input:checked]:shadow-[inset_0_0_0_1px_var(--brand)]",
                "has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-brand-2",
              )}
            >
              <input type="radio" name="severity" value={s} checked={value === s} onChange={() => onChange(s)} className="peer sr-only" />
              <span className="flex items-center gap-1.5 text-[0.9375rem] font-semibold leading-tight text-ink">
                <Icon aria-hidden className={clsx("h-4 w-4 shrink-0", cls)} />
                {severityLabel(s)}
              </span>
              <span className="tabular flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
                {weights ? <span>{weights[s]} points</span> : null}
                {s === def ? <span className="rounded-[4px] border border-rule-strong/60 bg-paper-2 px-1.5 font-semibold text-ink-2">Default</span> : null}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function WordingField({ label, value, onChange, placeholder, max, error, multiline, rows = 3, hint, inputRef }: {
  label: string; value: string; onChange: (v: string) => void; placeholder: string; max: number; error?: string; multiline?: boolean; rows?: number; hint?: string;
  inputRef?: React.Ref<HTMLInputElement & HTMLTextAreaElement>;
}) {
  const uid = useId();
  const n = value.trim().length;
  const over = n > max;
  const describedBy = [hint ? `${uid}-hint` : null, `${uid}-count`, error ? `${uid}-err` : null].filter(Boolean).join(" ");
  const common = {
    id: uid,
    ref: inputRef,
    value,
    placeholder,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(e.target.value),
    "aria-invalid": over || !!error || undefined,
    "aria-describedby": describedBy,
  };
  return (
    <div>
      <div className="flex items-end justify-between gap-3">
        <label htmlFor={uid} className="block text-sm font-semibold text-ink">
          {label}
        </label>
        {value !== "" ? (
          <button type="button" onClick={() => onChange("")} className="inline-flex min-h-8 items-center gap-1 rounded text-sm font-medium text-brand-2 underline underline-offset-2 pointer-coarse:min-h-11">
            <Undo2 aria-hidden className="h-3.5 w-3.5" />
            Use default wording<span className="sr-only">: {label}</span>
          </button>
        ) : null}
      </div>
      {hint ? (
        <p id={`${uid}-hint`} className="mb-1 text-sm text-muted">
          {hint}
        </p>
      ) : null}
      {multiline ? (
        <textarea {...(common as React.TextareaHTMLAttributes<HTMLTextAreaElement>)} rows={rows} className={clsx(inputClasses, "mt-1 block h-auto min-h-20 py-2 leading-relaxed")} />
      ) : (
        <input {...(common as React.InputHTMLAttributes<HTMLInputElement>)} type="text" autoComplete="off" className={clsx(inputClasses, "mt-1")} />
      )}
      <div className="mt-1.5 flex items-start justify-between gap-3 text-sm">
        <span id={`${uid}-err`} className="font-medium text-sev-high">
          {error}
        </span>
        <span id={`${uid}-count`} className={clsx("tabular ml-auto shrink-0", over ? "font-medium text-sev-high" : "text-muted")}>
          {over ? <span className="sr-only">Too long. </span> : null}
          {n}/{max}
        </span>
      </div>
    </div>
  );
}

function Effects({ items, changed }: { items: ReturnType<typeof describeEffects>; changed: boolean }) {
  return (
    <div className="space-y-2 rounded-lg border border-rule bg-paper-2/60 p-3.5">
      {changed ? (
        <ul className="space-y-2">
          {items.map((e) => {
            const warn = e.tone === "warning";
            const Icon = warn ? AlertTriangle : Info;
            return (
              <li key={e.id} data-tone={e.tone} className="flex items-start gap-2.5 text-[0.9375rem]">
                <Icon aria-hidden className={clsx("mt-0.5 h-4 w-4 shrink-0", warn ? "text-sev-medium" : "text-sev-info")} />
                <span className={clsx(warn ? "font-medium text-ink" : "text-ink-2")}>{e.text}</span>
              </li>
            );
          })}
        </ul>
      ) : (
        <>
          <p className="text-[0.9375rem] text-ink-2">{NOTHING_CHANGED}</p>
          <p className="flex items-start gap-2.5 text-[0.9375rem] text-ink-2">
            <Info aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-sev-info" />
            {SAVE_TIMING}
          </p>
        </>
      )}
    </div>
  );
}

function EditorForm({ rule, catalogue, onClose, onSaved }: { rule: RuleInfo; catalogue: RuleInfo[]; onClose: () => void; onSaved: (updated: RuleInfo, kind: SavedKind) => void }) {
  const { mutate } = useSWRConfig();
  const { method } = useExplainMethod();
  const [draft, setDraft] = useState<Draft>(() => draftFrom(rule));
  const [reviewer, setReviewer, remember] = useReviewer();
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState<{ reviewer?: boolean; reason?: boolean }>({});
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState<"save" | "reset" | null>(null);
  const [error, setError] = useState<SaveError | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const alertRef = useRef<HTMLDivElement>(null);
  const reviewerRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const nameRef = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const ids = { why: useId(), status: useId(), sev: useId(), word: useId(), effects: useId(), hist: useId(), reset: useId() };

  const patch = useMemo(() => buildPatch(rule, draft), [rule, draft]);
  const changed = hasChanges(patch);
  const effects = useMemo(
    () => describeEffects(rule, patch, { weights: method?.score.weights, hardStops: method?.hard_stop_rules, catalogue }),
    [rule, patch, method, catalogue],
  );
  const all = validateReview({ reviewer, reason });
  if (!all.reason && reason.trim().length > CATALOGUE_REASON_MAX) all.reason = `The reason can be at most ${CATALOGUE_REASON_MAX} characters.`;
  const wordingErrors = validateWording(draft);
  const wordingBad = Object.keys(wordingErrors).length > 0;
  const errors: ReviewErrors = { reviewer: tried || touched.reviewer ? all.reviewer : undefined, reason: tried || touched.reason ? all.reason : undefined };
  const reviewOk = !all.reviewer && !all.reason;
  const canSave = changed && reviewOk && !wordingBad && busy === null;
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => {
    setDraft((d) => ({ ...d, [k]: v }));
    setError(null);
  };

  useEffect(() => {
    if (!error) return;
    if (error.field === "reviewer") reviewerRef.current?.focus();
    else if (error.field === "reason") reasonRef.current?.focus();
    else alertRef.current?.focus();
  }, [error]);

  async function finish(updated: RuleInfo, kind: SavedKind) {
    remember(reviewer.trim());
    await mutate(isChangeKey);
    onSaved(updated, kind);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setTried(true);
    if (all.reviewer) return void reviewerRef.current?.focus();
    if (all.reason) return void reasonRef.current?.focus();
    if (wordingBad) return void nameRef.current?.focus();
    if (!changed) return;
    setBusy("save");
    setError(null);
    try {
      const updated = await updateRuleOverride(rule.rule, { ...patch, reviewer: reviewer.trim(), reason: reason.trim() });
      await finish(updated, "saved");
    } catch (err) {
      const se = saveError(err, "save");
      if (se.reload) void mutate("rules");
      setError(se);
    } finally {
      setBusy(null);
    }
  }

  function askReset() {
    setTried(true);
    if (all.reviewer) return void reviewerRef.current?.focus();
    if (all.reason) return void reasonRef.current?.focus();
    setConfirmReset(true);
  }

  async function reset() {
    setBusy("reset");
    setError(null);
    try {
      const updated = await resetRuleOverride(rule.rule, { reviewer: reviewer.trim(), reason: reason.trim() });
      await finish(updated, "reset");
    } catch (err) {
      const se = saveError(err, "reset");
      if (se.reload) void mutate("rules");
      setConfirmReset(false);
      setError(se);
    } finally {
      setBusy(null);
    }
  }

  const hint = !changed ? "Change something to save." : !reviewOk ? (reason.trim().length > CATALOGUE_REASON_MAX ? `Shorten the reason to ${CATALOGUE_REASON_MAX} characters or fewer.` : "Add your name and a reason of at least 5 characters to save.") : wordingBad ? "Shorten the wording that is too long." : "";
  const defName = checkName(rule.rule);

  return (
    <form onSubmit={save} noValidate aria-label={`Edit ${effectiveName(rule.rule, rule.override)}`} className="flex flex-1 flex-col">
      <div className="space-y-7 px-4 pb-5 pt-5 sm:px-6">
        <section aria-labelledby={ids.status} className="space-y-3">
          <SectionTitle id={ids.status}>Status</SectionTitle>
          <SwitchRow on={draft.enabled} onChange={(v) => set("enabled", v)} />
        </section>

        <section aria-labelledby={ids.sev} className="space-y-3">
          <SectionTitle id={ids.sev} hint="How much a problem found by this check adds to a case&rsquo;s risk score.">
            Severity
          </SectionTitle>
          <SeverityChoice value={draft.severity} onChange={(s) => set("severity", s)} def={defaultSeverity(rule)} weights={method?.score.weights} />
        </section>

        <section aria-labelledby={ids.word} className="space-y-4">
          <SectionTitle id={ids.word} hint="Leave a box empty to keep the default wording. The text in the box is the default.">
            Wording
          </SectionTitle>
          <WordingField label="Name" value={draft.name} onChange={(v) => set("name", v)} placeholder={defName} max={NAME_MAX} error={wordingErrors.name} inputRef={nameRef} />
          <WordingField label="What it checks" value={draft.description} onChange={(v) => set("description", v)} placeholder={rule.description} max={DESCRIPTION_MAX} error={wordingErrors.description} multiline rows={3} />
          <WordingField label="Internal note" value={draft.note} onChange={(v) => set("note", v)} placeholder="For example, why this check is set up this way" max={NOTE_MAX} error={wordingErrors.note} multiline rows={3} hint="Shown in the check catalogue only. It never appears on a case." />
        </section>

        <section aria-labelledby={ids.why} className="space-y-3">
          <SectionTitle id={ids.why} hint="Your name and reason go into the permanent change log next to what you changed.">
            Why are you changing it?
          </SectionTitle>
          <ReviewFields
            reviewer={reviewer}
            reason={reason}
            onReviewer={(v) => {
              setReviewer(v);
              setError(null);
            }}
            onReason={(v) => {
              setReason(v);
              setError(null);
            }}
            errors={errors}
            onBlurReviewer={() => setTouched((t) => ({ ...t, reviewer: true }))}
            onBlurReason={() => setTouched((t) => ({ ...t, reason: true }))}
            reviewerRef={reviewerRef}
            reasonRef={reasonRef}
            reasonRows={3}
            reasonMax={CATALOGUE_REASON_MAX}
            prompts={["Why does this check need to change?", "What should the next reader of the change log know?"]}
          />
        </section>

        <section aria-labelledby={ids.effects} className="space-y-3">
          <SectionTitle id={ids.effects}>What will happen</SectionTitle>
          <Effects items={effects} changed={changed} />
        </section>

        <section aria-labelledby={ids.hist} className="space-y-3">
          <SectionTitle id={ids.hist}>History for this check</SectionTitle>
          <ChangeHistory rule={rule} />
        </section>

        {isCustomised(rule) ? (
          <section aria-labelledby={ids.reset} className="space-y-3 rounded-lg border border-rule bg-paper-2/50 p-4">
            <SectionTitle id={ids.reset} hint="Switches the check on, uses the default severity and wording, and removes your note. This is recorded in the change log. It uses the name and reason above.">
              Reset to default
            </SectionTitle>
            {confirmReset ? (
              <Notice
                variant="warning"
                title="Put this check back to its defaults?"
                action={
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" variant="primary" loading={busy === "reset"} onClick={() => void reset()}>
                      {busy === "reset" ? "Resetting..." : "Yes, reset to default"}
                    </Button>
                    <Button type="button" size="sm" variant="ghost" disabled={busy === "reset"} onClick={() => setConfirmReset(false)}>
                      Keep editing
                    </Button>
                  </div>
                }
              >
                {changed ? "The edits you have not saved will be discarded. " : ""}It applies to analyses run from now on; cases already analysed keep their results until they are re-run.
              </Notice>
            ) : (
              <Button type="button" variant="secondary" icon={<Undo2 aria-hidden className="h-4 w-4" />} onClick={askReset} disabled={busy !== null}>
                Reset to default
              </Button>
            )}
          </section>
        ) : null}
      </div>

      <div className="sticky bottom-0 mt-auto space-y-2 border-t border-rule bg-paper-2 px-4 py-3 sm:px-6">
        {error ? (
          <div ref={alertRef} tabIndex={-1} className="focus:outline-none">
            <Notice variant="error" title={error.title}>
              {error.detail}
            </Notice>
          </div>
        ) : null}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-muted empty:hidden sm:max-w-[16rem]" aria-live="polite">
            {hint}
          </p>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={busy !== null}>
              Cancel
            </Button>
            <Button type="submit" size="lg" className="flex-1 sm:flex-none" loading={busy === "save"} disabled={!canSave}>
              {busy === "save" ? "Saving..." : "Save changes"}
            </Button>
          </div>
        </div>
      </div>
    </form>
  );
}

/**
 * The right-hand editor for one check: switch it on or off, change its severity, reword it. Every save needs the
 * reviewer's name and a reason and lands in the change log. The sheet stays open, with the draft, when saving fails.
 */
export function CheckEditor({ rule, rules, open, onClose, onSaved }: { rule: RuleInfo | null; rules: RuleInfo[]; open: boolean; onClose: () => void; onSaved: (updated: RuleInfo, kind: SavedKind) => void }) {
  const title = rule ? effectiveName(rule.rule, rule.override) : "Check";
  return (
    <SideSheet
      open={open && !!rule}
      onClose={onClose}
      eyebrow="Edit check"
      title={title}
      meta={
        rule ? (
          <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <span className="num text-xs">{rule.rule}</span>
            <span aria-hidden>·</span>
            <span>{stageLabel(rule.stage)}</span>
            <CheckStateBadges r={rule} />
          </span>
        ) : null
      }
    >
      {rule ? <EditorForm key={rule.rule} rule={rule} catalogue={rules} onClose={onClose} onSaved={onSaved} /> : null}
    </SideSheet>
  );
}

