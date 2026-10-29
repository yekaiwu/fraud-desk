"use client";

import clsx from "clsx";
import { ChevronDown, Download, FilePlus, Printer, RefreshCw, Trash2, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { packUrl } from "@/lib/api";
import type { CaseDetail } from "@/lib/types";
import { buttonClasses } from "../ui/Button";

type Item = {
  key: string;
  label: string;
  icon: LucideIcon;
  /** Why it is not available right now; the item stays visible so the officer knows it exists. */
  unavailable?: string;
  href?: string;
  external?: boolean;
  download?: boolean;
  onSelect?: () => void;
  danger?: boolean;
};

/**
 * The secondary actions of a case in one tidy menu: case pack, JSON export, re-run, add documents, delete.
 * Same availability rules as before: pack and export need finished analysis; re-run and add documents only on
 * the current version of an open or waiting case (otherwise shown disabled, with the reason).
 */
export function CaseActions({ c, isCurrent, onRerun, onAddDocs, onDelete, align = "right", variant = "secondary" }: {
  c: CaseDetail; isCurrent: boolean; onRerun: () => void; onAddDocs: () => void; onDelete: () => void; align?: "left" | "right"; variant?: "secondary" | "ghost";
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  const analysing = c.status === "processing";
  const canChange = isCurrent && (c.status === "open" || c.status === "awaiting_documents");
  const packVersion = isCurrent ? undefined : c.version;
  const whyNot = !isCurrent ? "Only on the current version." : c.status === "closed" ? "Not on a closed case." : c.status === "error" ? "Use Re-run on the banner." : "Only on an open case.";

  const items: Item[] = [];
  if (!analysing) {
    items.push({ key: "pack", label: "Case pack", icon: Printer, href: packUrl(c.id, "html", packVersion), external: true });
    items.push({ key: "json", label: "Export JSON", icon: Download, href: packUrl(c.id, "json", packVersion), download: true });
    items.push({ key: "rerun", label: "Re-run analysis", icon: RefreshCw, onSelect: onRerun, unavailable: canChange || (c.status === "error" && isCurrent) ? undefined : whyNot });
    items.push({ key: "docs", label: "Add documents", icon: FilePlus, onSelect: onAddDocs, unavailable: canChange ? undefined : whyNot });
  }
  items.push({ key: "delete", label: "Delete case...", icon: Trash2, onSelect: onDelete, danger: true });

  const close = (restoreFocus: boolean) => {
    setOpen(false);
    if (restoreFocus) trigger.current?.focus();
  };

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
    };
  }, [open]);

  function menuItems(): HTMLElement[] {
    return Array.from(root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([aria-disabled="true"])') ?? []);
  }
  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      close(true);
      return;
    }
    if (e.key === "Tab") return close(false);
    const list = menuItems();
    if (!list.length) return;
    const i = list.indexOf(document.activeElement as HTMLElement);
    let next = -1;
    if (e.key === "ArrowDown") next = i < 0 || i === list.length - 1 ? 0 : i + 1;
    else if (e.key === "ArrowUp") next = i <= 0 ? list.length - 1 : i - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = list.length - 1;
    if (next >= 0) {
      e.preventDefault();
      list[next].focus();
    }
  }
  function openMenu(focusFirst: boolean) {
    setOpen(true);
    if (focusFirst) requestAnimationFrame(() => menuItems()[0]?.focus());
  }

  const itemCls = "flex w-full items-start gap-3 rounded-md px-3 py-2.5 text-left text-[0.9375rem] font-medium hover:bg-brand-tint focus-visible:bg-brand-tint aria-disabled:cursor-not-allowed aria-disabled:opacity-60 aria-disabled:hover:bg-transparent";

  return (
    <div ref={root} className="relative" onKeyDown={open ? onKeyDown : undefined}>
      <button
        ref={trigger}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close(false) : openMenu(false))}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            openMenu(true);
          }
        }}
        className={buttonClasses({ variant, size: "md", className: "w-full" })}
      >
        More actions
        <ChevronDown aria-hidden className={clsx("h-4 w-4 transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label="Case actions"
          className={clsx("absolute z-40 mt-1.5 w-64 max-w-[calc(100vw-2rem)] rounded-lg border border-rule bg-surface p-1.5 shadow-lift", align === "right" ? "right-0" : "left-0")}
        >
          {items.map((it, idx) => {
            const Icon = it.icon;
            const disabled = !!it.unavailable;
            const body = (
              <>
                <Icon aria-hidden className={clsx("mt-0.5 h-4 w-4 shrink-0", it.danger ? "text-sev-high" : "text-brand-2")} />
                <span className="min-w-0">
                  <span className={it.danger ? "text-sev-high" : "text-ink"}>{it.label}</span>
                  {disabled ? <span className="block text-xs font-normal text-muted">{it.unavailable}</span> : null}
                </span>
              </>
            );
            return (
              <div key={it.key} role="none" className={clsx(it.danger && idx > 0 && "mt-1 border-t border-rule pt-1")}>
                {it.href && !disabled ? (
                  <a
                    role="menuitem"
                    href={it.href}
                    {...(it.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                    {...(it.download ? { download: true } : {})}
                    tabIndex={-1}
                    onClick={() => close(false)}
                    className={itemCls}
                  >
                    {body}
                  </a>
                ) : (
                  <button
                    type="button"
                    role="menuitem"
                    tabIndex={-1}
                    aria-disabled={disabled || undefined}
                    onClick={() => {
                      if (disabled) return;
                      close(true);
                      it.onSelect?.();
                    }}
                    className={itemCls}
                  >
                    {body}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
