"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { useIsSm, usePrefersReducedMotion } from "@/lib/useMedia";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A native <dialog> that slides in from the right (full screen on phones), the same pattern as the Record decision
 * sheet: focus is trapped while it is open, Escape closes it, and focus goes back to whatever opened it. The title
 * takes focus first so a screen reader announces where you are. Children are only rendered while open, so every
 * opening starts from fresh state.
 */
export function SideSheet({ open, onClose, eyebrow, title, meta, children }: {
  open: boolean; onClose: () => void; eyebrow: string; title: ReactNode; meta?: ReactNode; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const isSm = useIsSm();
  const reduced = usePrefersReducedMotion();
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
      if (!reduced && isSm && typeof dlg.animate === "function") {
        dlg.animate([{ transform: "translateX(2.5rem)", opacity: 0 }, { transform: "none", opacity: 1 }], { duration: 240, easing: "cubic-bezier(0.2, 0.7, 0.2, 1)" });
      }
    } else if (!open && dlg.open) {
      if (typeof dlg.close === "function") dlg.close();
      else dlg.removeAttribute("open");
      opener.current?.focus();
    }
  }, [open, reduced, isSm]);

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
    <dialog
      ref={ref}
      className="sheet"
      aria-labelledby={titleId}
      style={isSm ? { margin: "0 0 0 auto", height: "100dvh", maxHeight: "100dvh", borderRadius: "14px 0 0 14px", maxWidth: "min(38rem, 100vw)", borderRightWidth: 0 } : undefined}
    >
      {open ? (
        <div className="flex h-dvh max-h-dvh flex-col">
          <div className="band-dark relative bg-brand px-4 py-3.5 sm:px-6">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-[0.09em] text-on-brand-muted">{eyebrow}</p>
                <h2 id={titleId} data-autofocus tabIndex={-1} className="mt-0.5 break-words text-[1.375rem] font-semibold leading-tight text-on-brand focus:outline-none">
                  {title}
                </h2>
                {meta ? <div className="mt-1.5 text-sm text-on-brand-muted">{meta}</div> : null}
              </div>
              <button type="button" onClick={onClose} aria-label="Close" className="-mr-2 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-on-brand hover:bg-on-brand/10">
                <X aria-hidden className="h-5 w-5" />
              </button>
            </div>
          </div>
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
        </div>
      ) : null}
    </dialog>
  );
}
