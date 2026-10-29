"use client";

import clsx from "clsx";
import { useRef, type ReactNode } from "react";

/**
 * Accessible tabs (WAI-ARIA tabs pattern): one tab stop, Left/Right (and Up/Down) move and select, Home/End jump.
 * You render the panels; give each `<TabPanel group id value>` so ids line up.
 *
 *   const [tab, setTab] = useState("findings");
 *   <Tabs group="case" label="Case sections" value={tab} onValueChange={setTab}
 *         tabs={[{ id: "findings", label: "Findings", count: 4 }, { id: "docs", label: "Documents" }]} />
 *   <TabPanel group="case" id="findings" value={tab}>...</TabPanel>
 *
 * Use `variant="pill"` for a compact segmented control. The tab row scrolls sideways on narrow screens.
 */
export type TabItem = { id: string; label: string; icon?: ReactNode; count?: number; /** Native tooltip, e.g. a one-line description of the tab. */ title?: string };

export const tabDomId = (group: string, id: string) => `${group}-tab-${id}`;
export const panelDomId = (group: string, id: string) => `${group}-panel-${id}`;

export function Tabs({ group, label, tabs, value, onValueChange, variant = "underline", className }: { group: string; label: string; tabs: TabItem[]; value: string; onValueChange: (id: string) => void; variant?: "underline" | "pill"; className?: string }) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  // When `value` matches no tab (a filter combination none of them represents) the first tab stays reachable.
  const hasSelection = tabs.some((t) => t.id === value);

  function onKeyDown(e: React.KeyboardEvent, i: number) {
    const last = tabs.length - 1;
    let next = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = i === last ? 0 : i + 1;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = i === 0 ? last : i - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    if (next < 0) return;
    e.preventDefault();
    const t = tabs[next];
    onValueChange(t.id);
    refs.current[t.id]?.focus();
  }

  return (
    <div
      role="tablist"
      aria-label={label}
      className={clsx("flex gap-1 overflow-x-auto [scrollbar-width:thin]", variant === "underline" ? "border-b border-rule" : "w-fit max-w-full rounded-lg border border-rule bg-paper-2 p-1", className)}
    >
      {tabs.map((t, i) => {
        const on = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            id={tabDomId(group, t.id)}
            role="tab"
            type="button"
            aria-selected={on}
            aria-controls={panelDomId(group, t.id)}
            title={t.title}
            tabIndex={on || (!hasSelection && i === 0) ? 0 : -1}
            onClick={() => onValueChange(t.id)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={clsx(
              "inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap px-3.5 text-sm font-semibold transition-colors",
              variant === "underline"
                ? clsx("-mb-px border-b-2", on ? "border-brand text-brand" : "border-transparent text-ink-2 hover:text-brand")
                : clsx("rounded-md", on ? "bg-surface text-brand shadow-paper" : "text-ink-2 hover:text-brand"),
            )}
          >
            {t.icon}
            {t.label}
            {t.count !== undefined ? (
              <span className={clsx("tabular rounded-full px-1.5 text-xs leading-5", on ? "bg-brand text-on-brand" : "bg-paper-2 text-ink-2")}>{t.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** Panel for a tab. Hidden (not unmounted) when inactive; pass `keepMounted={false}` to render nothing instead. */
export function TabPanel({ group, id, value, keepMounted = true, className, children }: { group: string; id: string; value: string; keepMounted?: boolean; className?: string; children: ReactNode }) {
  const on = id === value;
  if (!on && !keepMounted) return null;
  return (
    <div role="tabpanel" id={panelDomId(group, id)} aria-labelledby={tabDomId(group, id)} hidden={!on} tabIndex={0} className={className}>
      {children}
    </div>
  );
}
