"use client";

import { Hourglass } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { postReview } from "@/lib/api";
import { makeDocNamer } from "@/lib/docNames";
import { ApiErrorException } from "@/lib/errors";
import { findingHeadline, flagWhere, isHighFlag, numberFlags } from "@/lib/flags";
import { validateReview, type ReviewErrors } from "@/lib/reviewValidation";
import type { CaseDetail } from "@/lib/types";
import { useReviewer } from "@/lib/useReviewer";
import { Modal } from "../Modal";
import { Button, ConcernBadge } from "../ui";
import { Amounts, Marker } from "./FindingParts";
import type { DecideTarget } from "./FindingsPanel";
import { ReviewError } from "./ReviewError";
import { ReviewFields } from "./ReviewFields";

export const SECOND_REVIEWER_NOTICE = "A second reviewer must approve this dismissal";

export function FlagDecisionDialog({ c, target, onClose, onDone, onSelectFlag, onReload }: { c: CaseDetail; target: DecideTarget | null; onClose: () => void; onDone: (entryId: string) => void; onSelectFlag: (id: string) => void; onReload: () => void }) {
  const [reviewer, setReviewer, remember] = useReviewer();
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState<{ reviewer?: boolean; reason?: boolean }>({});
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const reviewerRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  const [prevTarget, setPrevTarget] = useState<DecideTarget | null>(null);
  if (target !== prevTarget) {
    // a new target starts with a clean form
    setPrevTarget(target);
    if (target) {
      setReason("");
      setTouched({});
      setSubmitted(false);
      setError(null);
    }
  }
  useEffect(() => {
    if (error) alertRef.current?.focus();
  }, [error]);

  const flagIds = target?.flagIds ?? c.findings.find((f) => f.id === target?.findingId)?.flag_ids ?? [];
  const flags = flagIds.map((id) => c.flags.find((f) => f.id === id)).filter((f): f is NonNullable<typeof f> => !!f);
  const numbered = useMemo(() => numberFlags(c.findings, c.flags), [c.findings, c.flags]);
  const names = useMemo(() => makeDocNamer(c.documents, c.files), [c.documents, c.files]);
  const flagLabel = (id: string) => {
    const n = numbered.byId.get(id)?.n;
    return n ? `Concern ${n}` : "A concern";
  };
  const highDismiss = target?.decision === "dismiss" && flags.some(isHighFlag);
  const all = validateReview({ reviewer, reason });
  const errors: ReviewErrors = { reviewer: submitted || touched.reviewer ? all.reviewer : undefined, reason: submitted || touched.reason ? all.reason : undefined };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!target) return;
    setSubmitted(true);
    if (all.reviewer) return void reviewerRef.current?.focus();
    if (all.reason) return void reasonRef.current?.focus();
    setBusy(true);
    setError(null);
    try {
      const base = { kind: "flag_decision" as const, decision: target.decision, reviewer: reviewer.trim(), reason: reason.trim(), case_version: c.version };
      const res = await postReview(c.id, target.findingId ? { ...base, finding_id: target.findingId } : { ...base, flag_ids: target.flagIds ?? [] });
      remember(reviewer.trim());
      onDone(res.entry.id);
      onClose();
    } catch (err) {
      setError(err);
      if (err instanceof ApiErrorException && err.error.code === "reason_required") reasonRef.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  const confirm = target?.decision === "confirm";
  const title = decisionTitle(confirm, !!target?.findingId, flagIds.length);
  return (
    <Modal open={!!target} onClose={onClose} title={title}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <p className="text-[0.9375rem] text-ink-2">
          {confirm
            ? "You are recording that you looked at the evidence and this is a real concern. It stays on the case and counts toward your decision."
            : "You are recording that you looked at the evidence and this is not a concern. The reason you give is kept in the review log."}
        </p>
        <ul className="max-h-56 space-y-2 overflow-y-auto" aria-label="Concerns this applies to">
          {flags.map((f) => {
            const nf = numbered.byId.get(f.id);
            const finding = c.findings.find((x) => x.flag_ids.includes(f.id));
            return (
              <li key={f.id} className="flex items-start gap-2.5 rounded-md border border-rule bg-paper/70 px-3 py-2 text-sm">
                {nf ? (
                  <span className="mt-0.5">
                    <Marker n={nf.n} severity={f.severity} />
                  </span>
                ) : null}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <ConcernBadge severity={f.severity} />
                  </div>
                  <p className="mt-1 break-words text-ink">
                    <Amounts text={names.text(finding ? findingHeadline(finding, [f]) : f.explanation)} mono={false} />
                  </p>
                  <p className="mt-0.5 break-words text-muted">{flagWhere(f, names)}</p>
                </div>
              </li>
            );
          })}
        </ul>
        {highDismiss ? (
          <p role="note" className="flex items-start gap-2 rounded-md border border-brass/60 border-l-[3px] border-l-brass bg-brass-tint px-3 py-2.5 text-sm font-medium text-brass-deep">
            <Hourglass aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              {SECOND_REVIEWER_NOTICE}. Until then the concern stays unresolved and the case cannot be approved.
            </span>
          </p>
        ) : null}
        <ReviewFields
          reviewer={reviewer}
          reason={reason}
          onReviewer={setReviewer}
          onReason={setReason}
          errors={errors}
          onBlurReviewer={() => setTouched((t) => ({ ...t, reviewer: true }))}
          onBlurReason={() => setTouched((t) => ({ ...t, reason: true }))}
          reviewerRef={reviewerRef}
          reasonRef={reasonRef}
        />
        {error ? <ReviewError ref={alertRef} error={error} onSelectFlag={onSelectFlag} onReload={onReload} flagLabel={flagLabel} /> : null}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={busy}>
            {busy ? "Recording..." : title}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** "Mark this as not a concern", "Mark 3 as real concerns", "Mark all 4 as not a concern". */
export function decisionTitle(confirm: boolean, wholeFinding: boolean, count: number): string {
  const what = confirm ? "a real concern" : "not a concern";
  if (wholeFinding) return count > 1 ? `Mark all ${count} as ${what}` : `Mark this as ${what}`;
  if (count === 1) return `Mark this as ${what}`;
  return `Mark ${count} as ${confirm ? "real concerns" : what}`;
}
