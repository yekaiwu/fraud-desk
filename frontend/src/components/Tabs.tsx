"use client";

import clsx from "clsx";
import { useRef } from "react";

export type TabDef = { id: string; label: string };
export const tabId = (group: string, id: string) => `${group}-tab-${id}`;
export const panelId = (group: string, id: string) => `${group}-panel-${id}`;

/** ARIA tabs pattern: roving tabindex, arrow keys, Home/End. Panels are rendered by the caller with panelId(). */
export function TabList({ group, label, tabs, value, onChange, className }: { group: string; label: string; tabs: TabDef[]; value: string; onChange: (id: string) => void; className?: string }) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  function onKey(e: React.KeyboardEvent, i: number) {
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
    else if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = tabs.length - 1;
    if (next < 0) return;
    e.preventDefault();
    const t = tabs[next];
    onChange(t.id);
    refs.current[t.id]?.focus();
  }
  return (
    <div role="tablist" aria-label={label} className={clsx("flex gap-1 overflow-x-auto border-b border-border", className)}>
      {tabs.map((t, i) => {
        const on = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            id={tabId(group, t.id)}
            role="tab"
            type="button"
            aria-selected={on}
            aria-controls={panelId(group, t.id)}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => onKey(e, i)}
            className={clsx("-mb-px min-h-11 whitespace-nowrap border-b-2 px-3 text-sm font-semibold", on ? "border-ink text-foreground" : "border-transparent text-muted hover:text-foreground")}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
