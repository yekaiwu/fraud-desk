"use client";

import { useRef, useState } from "react";
import { postReview } from "@/lib/api";
import { formatRelative, formatUtc } from "@/lib/format";
import { sameReviewer, validateReview } from "@/lib/reviewValidation";
import type { CaseDetail } from "@/lib/types";
import { useReviewer } from "@/lib/useReviewer";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";
import { DecisionError } from "./DecisionError";
import { ReviewFields } from "./ReviewFields";

function ApprovalCard({ c, p, canAct, onDone, onSelectFlag, onReload }: { c: CaseDetail; p: CaseDetail["pending_second_approvals"][number]; canAct: boolean; onDone: (id: string) => void; onSelectFlag: (id: string) => void; onReload: () => void }) {
  const [reviewer, setReviewer, remember] = useReviewer();
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState<{ reviewer?: boolean; reason?: boolean }>({});
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [clash, setClash] = useState(false);
  const alertRef = useRef<HTMLDivElement>(null);
  const reviewerRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const entry = c.review.find((e) => e.id === p.entry_id);
  const rules = [...new Set(p.flag_ids.map((id) => c.flags.find((f) => f.id === id)?.rule ?? id))];
  const all = validateReview({ reviewer, reason });
  const same = sameReviewer(reviewer, p.reviewer);

  async function decide(decision: "approve" | "decline") {
    setSubmitted(true);
    if (same) {
      setClash(true);
      reviewerRef.current?.focus();
      return;
    }
    if (all.reviewer) return void reviewerRef.current?.focus();
    if (all.reason) return void reasonRef.current?.focus();
    setBusy(true);
    setError(null);
    try {
      const res = await postReview(c.id, { kind: "second_approval", decision, target_entry_id: p.entry_id, reviewer: reviewer.trim(), reason: reason.trim(), case_version: c.version });
      remember(reviewer.trim());
      onDone(res.entry.id);
    } catch (e) {
      setError(e);
      setTimeout(() => alertRef.current?.focus(), 0);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-lg border border-brass/60 bg-surface p-4 shadow-paper" data-entry={p.entry_id}>
      <p className="text-[0.9375rem]">
        <strong>{p.reviewer}</strong> dismissed {p.flag_ids.length} {p.flag_ids.length === 1 ? "flag" : "flags"}:{" "}
        {rules.map((r) => (
          <code key={r} className="num mr-1 text-xs text-ink-2">
            {r}
          </code>
        ))}
      </p>
      {entry ? <p className="mt-1.5 text-[0.9375rem] text-ink">Reason: {entry.reason}</p> : null}
      <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <Badge variant="attention">Needs a different reviewer</Badge>
        <span className="num">
          {formatUtc(p.created_at)}, {formatRelative(p.created_at)}
        </span>
      </p>
      {canAct ? (
        <div className="mt-4 space-y-3 border-t border-rule pt-4">
          <ReviewFields
            reviewer={reviewer}
            reason={reason}
            onReviewer={(v) => {
              setReviewer(v);
              setClash(false);
            }}
            onReason={setReason}
            errors={{
              reviewer: clash || (same && (submitted || touched.reviewer)) ? "A different reviewer must countersign" : submitted || touched.reviewer ? all.reviewer : undefined,
              reason: submitted || touched.reason ? all.reason : undefined,
            }}
            onBlurReviewer={() => setTouched((t) => ({ ...t, reviewer: true }))}
            onBlurReason={() => setTouched((t) => ({ ...t, reason: true }))}
            reasonLabel="Reason for your decision (required, at least 5 characters)"
            reviewerRef={reviewerRef}
            reasonRef={reasonRef}
            reasonRows={3}
          />
          {error ? <DecisionError ref={alertRef} error={error} onSelectFlag={onSelectFlag} onReload={onReload} /> : null}
          <div className="flex flex-wrap gap-2">
            <Button loading={busy} onClick={() => void decide("approve")}>
              Approve the dismissal
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => void decide("decline")}>
              Decline
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  );
}

export function PendingApprovals({ c, canAct, onDone, onSelectFlag, onReload }: { c: CaseDetail; canAct: boolean; onDone: (id: string) => void; onSelectFlag: (id: string) => void; onReload: () => void }) {
  if (c.pending_second_approvals.length === 0) return null;
  return (
    <Panel
      tone="attention"
      title={`Waiting for a second reviewer (${c.pending_second_approvals.length})`}
      eyebrow="Needs a different person"
      headingLevel={3}
    >
      <p className="mb-3 text-[0.9375rem] text-ink-2">Dismissing a high-concern flag needs a second reviewer, and it must be someone other than the person who dismissed it.</p>
      <ul className="space-y-3">
        {c.pending_second_approvals.map((p) => (
          <ApprovalCard key={p.entry_id} c={c} p={p} canAct={canAct} onDone={onDone} onSelectFlag={onSelectFlag} onReload={onReload} />
        ))}
      </ul>
    </Panel>
  );
}
