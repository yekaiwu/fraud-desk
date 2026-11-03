"use client";

import { useRef, useState } from "react";
import { deleteCase } from "@/lib/api";
import { ApiErrorException } from "@/lib/errors";
import { validateReview } from "@/lib/reviewValidation";
import { useReviewer } from "@/lib/useReviewer";
import { Modal } from "../Modal";
import { ReviewFields } from "../case/ReviewFields";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";

export type BatchResult = { deleted: string[]; failed: { id: string; why: string }[] };

function whyNot(err: unknown): string {
  if (err instanceof ApiErrorException) return err.error.code === "case_processing" ? "still being analysed" : err.error.message;
  return "the request failed";
}

/**
 * One confirmation for a whole selection: the reviewer and the reason are asked once and written to the audit log
 * for every case. Cases are deleted one after another with the same call as the single-case delete; a case that
 * cannot be deleted (one still being analysed) is named and the rest still go.
 */
export function BatchDeleteDialog({ open, ids, onClose, onDone }: { open: boolean; ids: string[]; onClose: () => void; onDone: (r: BatchResult) => void }) {
  const [reviewer, setReviewer, remember] = useReviewer();
  const [reason, setReason] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState<BatchResult["failed"]>([]);
  const reviewerRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const all = validateReview({ reviewer, reason });
  const n = ids.length;
  const noun = n === 1 ? "case" : "cases";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    if (all.reviewer) return void reviewerRef.current?.focus();
    if (all.reason) return void reasonRef.current?.focus();
    setBusy(true);
    setFailed([]);
    setProgress(0);
    const result: BatchResult = { deleted: [], failed: [] };
    for (const id of ids) {
      try {
        await deleteCase(id, reviewer.trim(), reason.trim());
        result.deleted.push(id);
      } catch (err) {
        result.failed.push({ id, why: whyNot(err) });
      }
      setProgress(result.deleted.length + result.failed.length);
    }
    setBusy(false);
    if (result.deleted.length > 0) remember(reviewer.trim());
    if (result.failed.length === 0) {
      setReason("");
      setSubmitted(false);
    } else {
      setFailed(result.failed);
    }
    onDone(result);
  }

  return (
    <Modal open={open} onClose={busy ? () => {} : onClose} title={`Delete ${n} ${noun}?`}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <p className="text-[0.9375rem] text-ink-2">
          Removes all data and duplicate-detection fingerprints of the {n} selected {noun}; do not type names or ID numbers in the reason. The same name and reason are recorded in the audit log for each case.
        </p>
        <ReviewFields reviewer={reviewer} reason={reason} onReviewer={setReviewer} onReason={setReason} errors={{ reviewer: submitted ? all.reviewer : undefined, reason: submitted ? all.reason : undefined }} onBlurReviewer={() => {}} onBlurReason={() => {}} reviewerRef={reviewerRef} reasonRef={reasonRef} />
        {failed.length > 0 ? (
          <Notice variant="warning" title={`${failed.length} could not be deleted`} role="alert">
            {failed.map((f) => `${f.id} (${f.why})`).join(", ")}. They stay selected; try again once they have finished.
          </Notice>
        ) : null}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant="danger" loading={busy}>
            {busy ? `Deleting ${progress} of ${n}...` : `Delete ${n} ${noun}`}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
