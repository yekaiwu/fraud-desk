"use client";

import { HelpCircle, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { RiskGauge } from "@/components/ui/RiskGauge";
import { severityLabel } from "@/lib/labels";
import type { Severity } from "@/lib/types";
import { HIGH_FROM, MEDIUM_FROM, SCORE_CAP, WEIGHTS, bandOf, scoreOf } from "./demo";

const ORDER: Severity[] = ["high", "medium", "low", "info"];
/** The sample case on this card: one high and one medium concern. */
const CASE_FINDINGS: Severity[] = ["high", "medium"];

/**
 * A mock of the case score with the "?" that opens a short explanation of the formula (static, built from the kit).
 * Closes on Escape, on a click outside, and from its own button. Focus stays on the "?" button and the card follows it
 * in the tab order, so a keyboard user can read on and close it.
 */
export function ScoreExplainer() {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const score = scoreOf(CASE_FINDINGS);
  const band = bandOf(score);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    const onPointer = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  return (
    <div ref={root} className="relative rounded-xl border border-rule bg-surface p-5 shadow-lift sm:p-6">
      <div className="flex items-center gap-1.5">
        <p className="label-caps">Risk score</p>
        <button
          ref={button}
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label="How the risk score is calculated"
          onClick={() => setOpen((v) => !v)}
          className="grid h-11 w-11 place-items-center rounded-full text-brand-2 hover:bg-brand-tint -my-3"
        >
          <HelpCircle aria-hidden className="h-[22px] w-[22px]" />
        </button>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-8 gap-y-4">
        <RiskGauge score={score} band={band} />
        <div className="min-w-0 flex-1 basis-48 text-[0.9375rem] text-ink-2">
          <p className="label-caps">Why</p>
          <ul className="mt-2 space-y-2">
            <li>Running balance does not add up from this row</li>
            <li>The invoice customer is not the party that paid</li>
          </ul>
        </div>
      </div>

      {open ? (
        <div id={panelId} role="group" aria-label="How the risk score is calculated" className="absolute right-3 top-14 z-10 w-[min(23rem,calc(100%-1.5rem))] rounded-lg border border-rule-strong bg-surface p-4 shadow-sheet sm:right-5">
          <div className="flex items-start justify-between gap-3">
            <p className="font-display text-lg font-semibold leading-snug text-ink">How the risk score is calculated</p>
            <button type="button" onClick={() => { setOpen(false); button.current?.focus(); }} aria-label="Close explanation" className="-mr-2 -mt-2 grid h-11 w-11 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-paper-2 hover:text-brand">
              <X aria-hidden className="h-5 w-5" />
            </button>
          </div>
          <p className="mt-1 text-sm text-ink-2">Each finding adds points by how serious it is. A finding counts once, however many documents show it.</p>
          <table className="mt-3 w-full border-collapse text-sm">
            <caption className="sr-only">Points by concern level</caption>
            <tbody>
              {ORDER.map((s) => (
                <tr key={s} className="border-t border-rule">
                  <th scope="row" className="py-1.5 text-left font-medium text-ink-2">{severityLabel(s)}</th>
                  <td className="num py-1.5 text-right text-ink">{WEIGHTS[s]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 rounded-md bg-paper-2/70 p-2.5 text-sm text-ink-2">
            This case: <span className="num">{WEIGHTS.high}</span> (high) + <span className="num">{WEIGHTS.medium}</span> (medium) = <span className="num font-medium text-ink">{score}</span>, which is Medium. The score stops at {SCORE_CAP}. Low is under {MEDIUM_FROM}, High is {HIGH_FROM} and over.
          </p>
        </div>
      ) : null}
    </div>
  );
}
