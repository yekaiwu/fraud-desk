"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef } from "react";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Native <dialog> with showModal() (focus trap, Escape). Focus moves inside on open and returns to the
 * opener on close. Children are only rendered while open. Below `sm` the sheet is full screen (globals.css).
 */
export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      if (typeof dlg.showModal === "function") dlg.showModal();
      else dlg.setAttribute("open", "");
      const target = dlg.querySelector<HTMLElement>("[data-autofocus]") ?? dlg.querySelector<HTMLElement>(FOCUSABLE);
      target?.focus();
    } else if (!open && dlg.open) {
      if (typeof dlg.close === "function") dlg.close();
      else dlg.removeAttribute("open");
      opener.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    const handleClose = () => {
      opener.current?.focus();
      onCloseRef.current();
    };
    const handleKey = (e: KeyboardEvent) => {
      // jsdom and old browsers have no native Escape handling
      if (e.key === "Escape" && typeof dlg.showModal !== "function") handleClose();
    };
    dlg.addEventListener("close", handleClose);
    dlg.addEventListener("keydown", handleKey);
    return () => {
      dlg.removeEventListener("close", handleClose);
      dlg.removeEventListener("keydown", handleKey);
    };
  }, []);

  return (
    <dialog ref={ref} className="sheet" aria-labelledby={titleId} style={wide ? { maxWidth: "min(48rem, calc(100vw - 2rem))" } : undefined}>
      {open ? (
        <div className="flex max-h-[100dvh] flex-col sm:max-h-[85dvh]">
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
            <h2 id={titleId} className="font-display text-lg font-semibold">
              {title}
            </h2>
            <button type="button" onClick={onClose} aria-label="Close" className="inline-flex h-11 w-11 items-center justify-center rounded hover:bg-surface">
              <X aria-hidden className="h-5 w-5" />
            </button>
          </div>
          <div className="overflow-y-auto px-4 py-4">{children}</div>
        </div>
      ) : null}
    </dialog>
  );
}
