import clsx from "clsx";
import type { ReactNode } from "react";
import { Guilloche } from "./Guilloche";

/**
 * "Nothing here" with a reason and a next step. Say what is missing and what the officer can do about it.
 *
 *   <EmptyState icon={<Inbox aria-hidden className="h-6 w-6" />} title="No cases match these filters"
 *     text="Clear a filter to see more." action={<Button variant="secondary">Clear filters</Button>} />
 */
export function EmptyState({ icon, title, text, action, compact = false, className }: { icon?: ReactNode; title: string; text?: ReactNode; action?: ReactNode; compact?: boolean; className?: string }) {
  return (
    <div className={clsx("relative overflow-hidden rounded-lg border border-dashed border-rule-strong/70 bg-paper-2/50 text-center", compact ? "px-4 py-7" : "px-6 py-12", className)}>
      <Guilloche variant="rosette" opacity={0.07} className="absolute -right-16 -top-20 h-56 w-56 text-brand" />
      <div className="relative">
        {icon ? <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full border border-rule bg-surface text-brand-2 shadow-paper">{icon}</div> : null}
        <p className="font-display text-lg font-semibold text-ink">{title}</p>
        {text ? <p className="mx-auto mt-1 max-w-md text-[0.9375rem] text-ink-2">{text}</p> : null}
        {action ? <div className="mt-4 flex justify-center">{action}</div> : null}
      </div>
    </div>
  );
}
