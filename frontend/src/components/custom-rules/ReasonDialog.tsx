"use client";

import { useState } from "react";
import { REASON_MAX, REASON_MIN } from "@/lib/customRules";
import { Modal } from "../Modal";
import { Button } from "../ui/Button";
import { inputClasses } from "../ui/Field";
import { CustomError } from "./CustomError";

/**
 * Asks for the reason every change to a custom rule needs (it goes in the change log), then runs the action. The
 * dialog stays open on a failure and explains it.
 */
export function ReasonDialog({
  open,
  title,
  intro,
  confirmLabel,
  danger = false,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  intro: React.ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const ok = reason.trim().length >= REASON_MIN;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(reason.trim());
      setReason("");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={title}>
      <form onSubmit={submit} className="space-y-3">
        <div className="text-[0.9375rem] text-ink-2">{intro}</div>
        <div>
          <label htmlFor="change-reason" className="mb-1 block text-sm font-semibold text-ink">
            Reason
          </label>
          <p className="mb-1.5 text-sm text-muted">At least {REASON_MIN} characters. It is kept in the change log.</p>
          <textarea id="change-reason" data-autofocus rows={3} maxLength={REASON_MAX} className={`${inputClasses} h-auto py-2`} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        {error ? <CustomError error={error} /> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant={danger ? "danger" : "primary"} loading={busy} disabled={!ok}>
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
