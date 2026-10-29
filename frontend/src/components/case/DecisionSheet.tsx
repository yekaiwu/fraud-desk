"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { VERDICT_LABELS } from "@/lib/constants";
import { useIsSm, usePrefersReducedMotion } from "@/lib/useMedia";
import type { CaseDetail, Verdict } from "@/lib/types";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";
import { VERDICT_NEXT, VerdictForm } from "./VerdictForm";
import { formUnavailableReason } from "./ReviewPanel";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Shown instead of the form once the decision is in: what was recorded, what happens next, the way back. */
export function DecisionRecorded({ entryId, verdict, onClose }: { entryId: string; verdict: Verdict; onClose: () => void }) {
  return (
    <div className="space-y-5 px-4 py-5 sm:px-6">
      <Notice variant="success" title="Decision recorded">
        {VERDICT_LABELS[verdict]}, saved to the review log as <span className="num text-sm">{entryId}</span>.
      </Notice>
      <section aria-label="What happens next">
        <h3 data-autofocus tabIndex={-1} className="text-xl focus:outline-none">
          What happens next
        </h3>
        <p className="mt-1.5 text-[0.9375rem] text-ink-2">{VERDICT_NEXT[verdict]}</p>
      </section>
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-between">
        <Button variant="secondary" onClick={onClose}>
          Stay on this case
        </Button>
        <Button href="/" size="lg">
          Back to worklist
        </Button>
      </div>
    </div>
  );
}

/**
 * The "Record decision" side sheet. A native <dialog> (focus trap, Escape, focus returns to the opener): a panel
 * sliding in from the right on larger screens, a full-screen sheet on phones. The form stays mounted after the first
 * open so an accidental close does not lose a half-written reason.
 */
export function DecisionSheet({ open, onClose, c, isCurrent, onDone, onSelectFlag, onReload }: {
  open: boolean; onClose: () => void; c: CaseDetail; isCurrent: boolean;
  onDone: (entryId: string) => void; onSelectFlag: (id: string) => void; onReload: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const isSm = useIsSm();
  const reduced = usePrefersReducedMotion();
  const onCloseRef = useRef(onClose);
  const [everOpened, setEverOpened] = useState(false);
  const [result, setResult] = useState<{ entryId: string; verdict: Verdict } | null>(null);
  if (open && !everOpened) setEverOpened(true);

  // A recorded decision is dropped when the sheet is closed; the next opening shows the (now closed) case.
  const close = () => {
    setResult(null);
    onClose();
  };
  useEffect(() => {
    onCloseRef.current = close;
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

  const why = formUnavailableReason(c, isCurrent);

  return (
    <dialog
      ref={ref}
      className="sheet"
      aria-labelledby={titleId}
      style={isSm ? { margin: "0 0 0 auto", height: "100dvh", maxHeight: "100dvh", borderRadius: "14px 0 0 14px", maxWidth: "min(34rem, 100vw)", borderRightWidth: 0 } : undefined}
    >
      {open || everOpened ? (
        <div className="flex h-dvh max-h-dvh flex-col">
          <div className="band-dark relative bg-brand px-4 py-3.5 sm:px-6">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-[0.09em] text-on-brand-muted">Record decision</p>
                <h2 id={titleId} className="mt-0.5 break-words text-[1.375rem] font-semibold leading-tight text-on-brand">
                  {c.applicant.name ?? "Unnamed applicant"}
                </h2>
              </div>
              <button
                type="button"
                onClick={close}
                aria-label="Close"
                className="-mr-2 inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-on-brand hover:bg-on-brand/10"
              >
                <X aria-hidden className="h-5 w-5" />
              </button>
            </div>
          </div>
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
            {result ? (
              <DecisionRecorded entryId={result.entryId} verdict={result.verdict} onClose={close} />
            ) : why === null ? (
              <VerdictForm
                c={c}
                onDone={(entryId, verdict) => {
                  setResult({ entryId, verdict });
                  onDone(entryId);
                }}
                onSelectFlag={(id) => {
                  close();
                  onSelectFlag(id);
                }}
                onReload={onReload}
              />
            ) : (
              <div className="space-y-4 px-4 py-5 sm:px-6">
                <Notice variant="info" title="No decision can be recorded now">
                  {why}
                </Notice>
                <Button variant="secondary" onClick={close}>
                  Close
                </Button>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </dialog>
  );
}
