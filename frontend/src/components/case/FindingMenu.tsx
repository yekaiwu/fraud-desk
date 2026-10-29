"use client";

import clsx from "clsx";
import { ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Reveal } from "./EvidenceDisclosure";

export type MenuItem = { label: string; icon?: ReactNode; onSelect: () => void };

/**
 * A quiet "Apply to all 4" menu. Opens on click, closes on Escape, outside click or Tab; arrow keys move
 * between items and focus returns to the button. Items are real buttons with role="menuitem".
 */
export function FindingMenu({ label, items, className }: { label: string; items: MenuItem[]; className?: string }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  useEffect(() => {
    if (open) root.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open]);

  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) button.current?.focus();
  };

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    const els = Array.from(root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const i = els.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.preventDefault();
      close(true);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      els[(i + 1) % els.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      els[(i - 1 + els.length) % els.length]?.focus();
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <div ref={root} className={clsx("relative", className)} onKeyDown={open ? onKey : undefined}>
      <button
        ref={button}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-sm font-medium text-ink-2 hover:bg-paper-2 hover:text-ink pointer-coarse:min-h-11"
      >
        {label}
        <ChevronDown aria-hidden className={clsx("h-4 w-4 transition-transform duration-150", open && "rotate-180")} />
      </button>
      {open ? (
        <Reveal className="absolute right-0 z-20 mt-1 min-w-64 max-w-[calc(100vw-2rem)] rounded-lg border border-rule bg-surface p-1 shadow-lift">
          <div id={menuId} role="menu" aria-label={label}>
            {items.map((it) => (
              <button
                key={it.label}
                type="button"
                role="menuitem"
                onClick={() => {
                  close(false);
                  it.onSelect();
                }}
                className="flex min-h-10 w-full items-center gap-2 rounded-md px-3 text-left text-sm font-medium text-ink hover:bg-brand-tint focus-visible:bg-brand-tint pointer-coarse:min-h-11"
              >
                {it.icon}
                {it.label}
              </button>
            ))}
          </div>
        </Reveal>
      ) : null}
    </div>
  );
}
