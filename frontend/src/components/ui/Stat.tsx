import clsx from "clsx";
import type { ReactNode } from "react";

/**
 * One headline figure: small caps label, big serif number, optional hint.
 *
 *   <Stat label="Cases waiting" value={12} hint="3 are high risk" tone="high" />
 */
const TONE = { default: "text-ink", brand: "text-brand", high: "text-sev-high", medium: "text-sev-medium", low: "text-sev-low", info: "text-sev-info" } as const;

export function Stat({ label, value, hint, icon, labelAddon, tone = "default", className }: { label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode; labelAddon?: ReactNode; tone?: keyof typeof TONE; className?: string }) {
  return (
    <div className={clsx("min-w-0", className)}>
      <p className="label-caps flex items-center gap-1.5">
        {icon}
        {label}
        {labelAddon}
      </p>
      <p className={clsx("mt-1 font-display text-[2rem] font-semibold leading-none tabular tracking-tight", TONE[tone])}>{value}</p>
      {hint ? <p className="mt-1.5 text-sm text-ink-2">{hint}</p> : null}
    </div>
  );
}
