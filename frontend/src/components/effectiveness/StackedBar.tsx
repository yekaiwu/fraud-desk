import clsx from "clsx";
import { stackedText } from "@/lib/effectiveness";
import type { RuleEffectiveness } from "@/lib/types";

type Counts = Pick<RuleEffectiveness, "confirmed" | "dismissed" | "pending" | "open">;

// Not risk colours: confirmed is the house green, dismissed a neutral hatch, pending brass (it needs a second person), open plain paper.
const SEGMENTS = [
  { key: "confirmed", cls: "bg-brand" },
  { key: "dismissed", cls: "bg-rule-strong [background-image:repeating-linear-gradient(135deg,rgb(255_253_248/0.45)_0_2px,transparent_2px_5px)]" },
  { key: "pending", cls: "bg-brass" },
  { key: "open", cls: "bg-paper-2" },
] as const;

/** Plain bar with a text equivalent; no chart library (frontend.md 14). */
export function StackedBar({ r, showText = true, className }: { r: Counts; showText?: boolean; className?: string }) {
  const total = r.confirmed + r.dismissed + r.pending + r.open;
  return (
    <div className={className}>
      <div aria-hidden className="flex h-2.5 w-full min-w-28 overflow-hidden rounded-full border border-rule bg-paper-2">
        {total > 0 ? SEGMENTS.map((s) => (r[s.key] > 0 ? <span key={s.key} className={clsx("h-full", s.cls)} style={{ width: `${(r[s.key] / total) * 100}%` }} /> : null)) : null}
      </div>
      {showText ? <p className="tabular mt-1 text-xs text-ink-2">{stackedText(r)}</p> : null}
    </div>
  );
}

/** Key to the bar colours, shown once above the table. */
export function BarLegend() {
  const items = [
    { label: "Confirmed", cls: SEGMENTS[0].cls },
    { label: "Dismissed", cls: SEGMENTS[1].cls },
    { label: "Waiting for a second reviewer", cls: SEGMENTS[2].cls },
    { label: "Not decided yet", cls: SEGMENTS[3].cls },
  ];
  return (
    <ul aria-label="Bar colours" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span aria-hidden className={clsx("h-2.5 w-4 rounded-full border border-rule", i.cls)} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}
