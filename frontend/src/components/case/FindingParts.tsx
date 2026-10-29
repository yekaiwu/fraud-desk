"use client";

import { Ban, CheckCircle2, CircleDot, Hourglass } from "lucide-react";
import { splitAmounts } from "@/lib/evidence";
import type { Disposition, Severity } from "@/lib/types";
import { Badge, type BadgeVariant } from "../ui";
import { SEVERITY_COLOR } from "./RegionOverlay";

const DISP_LABEL: Record<Disposition, string> = {
  open: "Open",
  confirmed: "Confirmed",
  dismissed: "Dismissed",
  pending_second_approval: "Needs 2nd approval",
};
const DISP_VARIANT: Record<Disposition, BadgeVariant> = { open: "status", confirmed: "lane", dismissed: "neutral", pending_second_approval: "attention" };
const DISP_ICON = { open: CircleDot, confirmed: CheckCircle2, dismissed: Ban, pending_second_approval: Hourglass };

/** Where a concern stands: open, confirmed (a real concern), dismissed (not a concern) or waiting for a second approver. */
export function DispositionChip({ d }: { d: Disposition }) {
  return (
    <Badge variant={DISP_VARIANT[d]} icon={DISP_ICON[d]}>
      {DISP_LABEL[d]}
    </Badge>
  );
}

/** The flag number, in the same colour as its chip on the page, so the list and the document agree. */
export function Marker({ n, severity }: { n: number; severity: Severity }) {
  return (
    <>
      <span
        aria-hidden
        className="tabular inline-flex h-6 min-w-6 shrink-0 items-center justify-center rounded px-1.5 text-xs font-bold text-surface"
        style={{ background: SEVERITY_COLOR[severity] }}
      >
        {n}
      </span>
      <span className="sr-only">Flag {n}</span>
    </>
  );
}

/** Text in which money-shaped numbers get thousands separators and the mono face. The characters never change. */
export function Amounts({ text, mono = true }: { text: string; mono?: boolean }) {
  return (
    <>
      {splitAmounts(text).map((p, i) =>
        p.amount ? (
          <span key={i} className={mono ? "num whitespace-nowrap" : "tabular whitespace-nowrap"}>
            {p.text}
          </span>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  );
}
