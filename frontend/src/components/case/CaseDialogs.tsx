"use client";

import { useRef, useState } from "react";
import { deleteCase } from "@/lib/api";
import { validateReview } from "@/lib/reviewValidation";
import type { CaseDetail } from "@/lib/types";
import { useReviewer } from "@/lib/useReviewer";
import { Modal } from "../Modal";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";
import { DecisionError } from "./DecisionError";
import { ReviewFields } from "./ReviewFields";

export function RerunDialog({ open, nextVersion, onClose, onConfirm, busy }: { open: boolean; nextVersion: number; onClose: () => void; onConfirm: () => void; busy: boolean }) {
  return (
    <Modal open={open} onClose={onClose} title="Re-run the analysis?">
      <p className="text-[0.9375rem] text-ink-2">Creates version {nextVersion} and keeps all decisions as history; decisions do not carry over to the new version.</p>
      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button variant="secondary" onClick={onClose}>
          Cancel
        </Button>
        <Button data-autofocus onClick={onConfirm} loading={busy}>
          {busy ? "Starting..." : `Create version ${nextVersion}`}
        </Button>
      </div>
    </Modal>
  );
}

export function DeleteDialog({ open, c, onClose, onDeleted }: { open: boolean; c: CaseDetail; onClose: () => void; onDeleted: () => void }) {
  const [reviewer, setReviewer, remember] = useReviewer();
  const [reason, setReason] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const reviewerRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const all = validateReview({ reviewer, reason });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitted(true);
    if (all.reviewer) return void reviewerRef.current?.focus();
    if (all.reason) return void reasonRef.current?.focus();
    setBusy(true);
    setError(null);
    try {
      await deleteCase(c.id, reviewer.trim(), reason.trim());
      remember(reviewer.trim());
      onDeleted();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal open={open} onClose={onClose} title="Delete this case?">
      <form onSubmit={submit} noValidate className="space-y-4">
        <p className="text-[0.9375rem] text-ink-2">Removes all data and duplicate-detection fingerprints; do not type names or ID numbers in the reason.</p>
        {c.escalated_at ? <Notice variant="error" role="note">This case was escalated to AML. Deleting it removes the record of that escalation.</Notice> : null}
        <ReviewFields reviewer={reviewer} reason={reason} onReviewer={setReviewer} onReason={setReason} errors={{ reviewer: submitted ? all.reviewer : undefined, reason: submitted ? all.reason : undefined }} onBlurReviewer={() => {}} onBlurReason={() => {}} reviewerRef={reviewerRef} reasonRef={reasonRef} />
        {error ? <DecisionError error={error} /> : null}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="danger" loading={busy}>
            {busy ? "Deleting..." : "Delete the case"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
