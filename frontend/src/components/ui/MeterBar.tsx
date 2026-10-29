import clsx from "clsx";
import { useId } from "react";

/**
 * Labelled horizontal meter for coverage and progress. Always show the value as words too.
 *
 *   <MeterBar label="Checks completed" value={41} max={54} valueText="41 of 54 checks completed" marker={0.7} />
 *
 * `marker` (0..1) draws a tick for a threshold, e.g. the low-coverage line.
 */
export type MeterTone = "brand" | "high" | "medium" | "low" | "info";
const FILL: Record<MeterTone, string> = { brand: "bg-brand-2", high: "bg-sev-high", medium: "bg-sev-medium", low: "bg-sev-low", info: "bg-sev-info" };

export function MeterBar({
  label,
  value,
  max = 100,
  valueText,
  tone = "brand",
  marker,
  hideLabel = false,
  labelAddon,
  className,
}: {
  label: string;
  value: number;
  max?: number;
  /** Visible and spoken value, e.g. "41 of 54 checks completed". Defaults to a percentage. */
  valueText?: string;
  tone?: MeterTone;
  marker?: number;
  hideLabel?: boolean;
  /** Small control after the label, e.g. an <Explain> "?". Hidden together with the label. */
  labelAddon?: React.ReactNode;
  className?: string;
}) {
  const id = useId();
  const safeMax = max > 0 ? max : 1;
  const pct = Math.min(100, Math.max(0, (value / safeMax) * 100));
  const text = valueText ?? `${Math.round(pct)}%`;
  return (
    <div className={className}>
      <div className={clsx("mb-1.5 flex items-baseline justify-between gap-3 text-sm", hideLabel && "sr-only")}>
        <span className="inline-flex items-center gap-1.5">
          <span id={id} className="font-medium text-ink-2">
            {label}
          </span>
          {labelAddon}
        </span>
        <span className="tabular text-ink">{text}</span>
      </div>
      <div
        role="progressbar"
        aria-labelledby={id}
        aria-valuemin={0}
        aria-valuemax={safeMax}
        aria-valuenow={Math.min(safeMax, Math.max(0, value))}
        aria-valuetext={text}
        className="relative h-2.5 w-full overflow-hidden rounded-full border border-rule bg-paper-2"
      >
        <div className={clsx("h-full rounded-full transition-[width] duration-500", FILL[tone])} style={{ width: `${pct}%` }} />
        {marker !== undefined ? <span aria-hidden className="absolute inset-y-0 w-px bg-ink/70" style={{ left: `${Math.min(1, Math.max(0, marker)) * 100}%` }} /> : null}
      </div>
    </div>
  );
}
