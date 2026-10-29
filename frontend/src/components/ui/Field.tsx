import clsx from "clsx";
import { useId, type InputHTMLAttributes, type ReactNode } from "react";

/**
 * Labelled text input with optional hint and error, wired with aria-describedby / aria-invalid. 16 px text
 * (no iOS zoom) and a 44 px target. `trailing` slots a control (e.g. show/hide) inside the field.
 *
 *   <Field label="Reviewer name" hint="Shown in the review log." value={name} onChange={...} error={err} />
 */
export const inputClasses =
  "h-11 w-full rounded-md border border-rule-strong bg-surface px-3 text-base text-ink shadow-[inset_0_1px_2px_rgb(20_32_27/0.06)] placeholder:text-muted " +
  "transition-colors hover:border-brand-2 focus:border-brand-2 aria-[invalid=true]:border-sev-high disabled:bg-paper-2 disabled:opacity-70";

export function Field({ label, hint, error, trailing, className, inputClassName, id, ...input }: { label: string; hint?: ReactNode; error?: ReactNode; trailing?: ReactNode; className?: string; inputClassName?: string } & Omit<InputHTMLAttributes<HTMLInputElement>, "className">) {
  const auto = useId();
  const inputId = id ?? auto;
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const describedBy = [hint ? hintId : null, error ? errorId : null, input["aria-describedby"]].filter(Boolean).join(" ") || undefined;
  return (
    <div className={className}>
      <label htmlFor={inputId} className="mb-1 block text-sm font-semibold text-ink">
        {label}
      </label>
      {hint ? (
        <p id={hintId} className="mb-1.5 text-sm text-muted">
          {hint}
        </p>
      ) : null}
      <div className="relative">
        <input {...input} id={inputId} aria-invalid={error || input["aria-invalid"] ? true : undefined} aria-describedby={describedBy} className={clsx(inputClasses, trailing && "pr-12", inputClassName)} />
        {trailing ? <div className="absolute inset-y-0 right-0 flex items-center pr-0.5">{trailing}</div> : null}
      </div>
      {error ? (
        <p id={errorId} className="mt-1.5 text-sm font-medium text-sev-high">
          {error}
        </p>
      ) : null}
    </div>
  );
}
