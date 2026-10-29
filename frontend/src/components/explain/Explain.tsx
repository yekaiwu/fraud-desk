"use client";

import clsx from "clsx";
import { X } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useIsSm, usePrefersReducedMotion } from "@/lib/useMedia";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
/** Only one explainer is open at a time: opening one tells the others to close. */
const OPEN_EVENT = "gldb:explain-open";

const MARGIN = 8;
const MAX_WIDTH = 384; // 24rem

/** Where a desktop popover goes: under the button when there is room, else above; always inside the viewport. */
function place(btn: HTMLElement): CSSProperties {
  const r = btn.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = Math.min(MAX_WIDTH, vw - MARGIN * 2);
  const left = Math.max(MARGIN, Math.min(r.left + r.width / 2 - 24, vw - width - MARGIN));
  const below = vh - r.bottom - MARGIN - 6;
  const above = r.top - MARGIN - 6;
  if (below >= 280 || below >= above) return { left, width, top: r.bottom + 6, maxHeight: Math.max(160, below) };
  return { left, width, bottom: vh - r.top + 6, maxHeight: Math.max(160, above) };
}

/**
 * A small, quiet "?" that opens an explanation of the label or number beside it.
 *
 *   <Explain label="How the risk score is calculated" title="How the risk score is calculated">...</Explain>
 *
 * A 20 px circular button (with a larger touch target) opens a non-modal popover anchored to it on desktop and a bottom
 * sheet under 640 px. It closes on Esc, on a click outside and with its close button; focus moves into the panel and
 * returns to the button. The panel is rendered in a portal (or inside the open dialog that holds the button), so no
 * overflow container clips it. The children are only built when it opens, so an explainer that fetches does so lazily.
 */
export function Explain({ label, title, children, className }: { label: string; title: string; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const [host, setHost] = useState<HTMLElement | null>(null);
  const [style, setStyle] = useState<CSSProperties>({});
  const isSm = useIsSm();
  const reduced = usePrefersReducedMotion();
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const uid = useId();
  const panelId = `${uid}-panel`;
  const titleId = `${uid}-title`;

  const close = useCallback((restoreFocus: boolean) => {
    setOpen(false);
    if (restoreFocus) btnRef.current?.focus();
  }, []);

  const toggle = () => {
    if (open) return close(true);
    const btn = btnRef.current;
    // Inside an open modal dialog the page behind is inert, so the panel must live inside that dialog.
    setHost((btn?.closest("dialog[open]") as HTMLElement | null) ?? document.body);
    window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: uid }));
    setOpen(true);
  };

  // follow the button on desktop
  useLayoutEffect(() => {
    if (!open || !isSm || !btnRef.current) return;
    const btn = btnRef.current;
    const update = () => setStyle(place(btn));
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, isSm]);

  // focus into the panel once, when it opens
  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus({ preventScroll: true });
    const el = panelRef.current;
    if (el && !reduced && !isSm && typeof el.animate === "function") {
      el.animate([{ transform: "translateY(1.5rem)", opacity: 0.6 }, { transform: "none", opacity: 1 }], { duration: 180, easing: "cubic-bezier(0.2, 0.7, 0.2, 1)" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Esc, outside press, another explainer opening
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      close(true);
    };
    const onPointer = (e: Event) => {
      const t = e.target as Node | null;
      if (t && (panelRef.current?.contains(t) || btnRef.current?.contains(t))) return;
      close(false);
    };
    const onOther = (e: Event) => {
      if ((e as CustomEvent).detail !== uid) close(false);
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onPointer, true);
    window.addEventListener(OPEN_EVENT, onOther);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onPointer, true);
      window.removeEventListener(OPEN_EVENT, onOther);
    };
  }, [open, close, uid]);

  // keep Tab inside the panel while it is open
  const onPanelKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab") return;
    const items = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    if (items.length === 0) return e.preventDefault();
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === panelRef.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          toggle();
        }}
        className={clsx(
          "relative z-10 inline-flex h-5 w-5 shrink-0 cursor-help items-center justify-center rounded-full border border-rule-strong bg-surface align-middle text-[0.6875rem] font-semibold leading-none text-muted transition-colors print:hidden",
          "hover:border-brand-2 hover:bg-brand-tint hover:text-brand-2 aria-expanded:border-brand-2 aria-expanded:bg-brand-tint aria-expanded:text-brand-2",
          "after:absolute after:-inset-1.5 after:content-[''] pointer-coarse:after:-inset-3",
          className,
        )}
      >
        <span aria-hidden>?</span>
      </button>
      {open && host
        ? createPortal(
            <>
              {isSm ? null : <div aria-hidden className="fixed inset-0 z-[65] bg-ink/40 print:hidden" />}
              <div
                ref={panelRef}
                id={panelId}
                role="dialog"
                aria-labelledby={titleId}
                tabIndex={-1}
                onKeyDown={onPanelKey}
                style={isSm ? { position: "fixed", ...style } : undefined}
                className={clsx(
                  "z-[70] flex flex-col border border-rule bg-surface text-ink shadow-lift focus:outline-none print:hidden",
                  isSm ? "rounded-lg" : "fixed inset-x-0 bottom-0 max-h-[85dvh] rounded-t-xl border-b-0 pb-[env(safe-area-inset-bottom)]",
                )}
              >
                <div className="shrink-0 px-4 pt-3 sm:px-4">
                  <div className="rule-statement rule-statement-soft flex items-start justify-between gap-3">
                    <h2 id={titleId} className="text-[1.0625rem] font-semibold leading-snug">
                      {title}
                    </h2>
                    <button
                      type="button"
                      onClick={() => close(true)}
                      aria-label={`Close: ${label}`}
                      className="-mr-2 -mt-1.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-ink-2 hover:bg-brand-tint hover:text-brand pointer-coarse:h-11 pointer-coarse:w-11"
                    >
                      <X aria-hidden className="h-4 w-4" />
                    </button>
                  </div>
                </div>
                <div className="min-h-0 flex-1 space-y-3.5 overflow-y-auto overscroll-contain px-4 pb-4 pt-3 text-[0.9375rem] leading-relaxed text-ink-2">{children}</div>
              </div>
            </>,
            host,
          )
        : null}
    </>
  );
}
