"use client";

import clsx from "clsx";
import { Ban, Check, CheckCircle2, FileQuestion, Scale, ShieldAlert, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { postReview } from "@/lib/api";
import { APPROVE_MIN_COVERAGE, DISCLAIMER, VERDICT_LABELS } from "@/lib/constants";
import { ApiErrorException } from "@/lib/errors";
import { formatRatio, formatUtc } from "@/lib/format";
import { laneLabel } from "@/lib/labels";
import { validateReview, type ReviewErrors } from "@/lib/reviewValidation";
import type { CaseDetail, Lane, Verdict } from "@/lib/types";
import { useReviewer } from "@/lib/useReviewer";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";
import { DecisionError } from "./DecisionError";
import { ReviewFields } from "./ReviewFields";

export const VERDICT_CONSEQUENCE: Record<Verdict, string> = {
  approve: "Closes the case; every high flag must be decided first",
  request_documents: "Case waits for more documents; nothing is sent to the applicant",
  refer_edd: "Closes the case for the EDD team",
  escalate_aml: "Closes the case and records the suspicion-formed time now; compliance decides on an STR outside this tool",
  reject_or_escalate: "Closes the case; the credit decision stays with the bank",
};
export const LANE_VERDICT: Record<Lane, Verdict> = { edd: "refer_edd", aml: "escalate_aml", loan: "reject_or_escalate", fast: "approve" };

/** What happens after the decision is recorded, in plain words (shown on the success screen). */
export const VERDICT_NEXT: Record<Verdict, string> = {
  approve: "The case is closed as approved. It leaves your worklist and stays in the review log.",
  request_documents: "The case now waits for documents. Nothing was sent to the applicant; ask for them yourself in neutral terms.",
  refer_edd: "The case is closed and handed to the EDD team. It leaves your worklist.",
  escalate_aml: "The case is closed and the suspicion-formed time is recorded. Compliance decides on an STR outside this tool; this tool does not file one.",
  reject_or_escalate: "The case is closed. The credit decision stays with the bank; this tool does not send anything to the applicant.",
};

const VERDICT_ICON: Record<Verdict, LucideIcon> = {
  approve: CheckCircle2,
  request_documents: FileQuestion,
  refer_edd: Scale,
  escalate_aml: ShieldAlert,
  reject_or_escalate: Ban,
};

const REASON_PROMPTS: Record<Verdict, string[]> = {
  approve: ["What evidence did you rely on?", "Why are the open concerns acceptable?"],
  request_documents: ["What evidence did you rely on?", "Which documents are missing or unclear?"],
  refer_edd: ["What evidence did you rely on?", "What should the EDD team look at first?"],
  escalate_aml: ["What evidence did you rely on?", "What made you suspect something, and since when?"],
  reject_or_escalate: ["What evidence did you rely on?", "Which findings drove this outcome?"],
};

/**
 * The lane falls back to the use case whenever a case is not fast lane, so a clean AML case has lane "aml".
 * Only suggest a verdict when the risk supports it: a hard-stop finding or a medium/high band.
 * A low-risk case gets a suggestion only when it is in the fast lane (approve).
 */
export function suggestedVerdict(c: CaseDetail): Verdict | null {
  if (!c.lane || !c.score) return null;
  const hardStop = c.findings.some((f) => f.hard_stop);
  if (c.lane === "fast") return !hardStop && c.score.band === "low" ? LANE_VERDICT.fast : null;
  return hardStop || c.score.band !== "low" ? LANE_VERDICT[c.lane] : null;
}

/** Reasons a person should think twice before approving. Empty when the analysis is complete and low risk. */
export function approveCautions(c: CaseDetail): string[] {
  const out: string[] = [];
  if (c.score?.partial || c.partial) out.push("the analysis is partial");
  if (c.coverage && c.coverage.ratio < APPROVE_MIN_COVERAGE) out.push(`only ${formatRatio(c.coverage.ratio)} of applicable checks ran`);
  if (c.score && c.score.band !== "low") out.push(`the risk band is ${c.score.band}`);
  if (c.not_checked_documents.length > 0) out.push(`${c.not_checked_documents.length} received ${c.not_checked_documents.length === 1 ? "document was" : "documents were"} not checked`);
  return out;
}

type Step = 1 | 2 | 3;
const STEPS: { n: Step; label: string }[] = [
  { n: 1, label: "Outcome" },
  { n: 2, label: "Reason" },
  { n: 3, label: "Confirm" },
];

function StepStrip({ step }: { step: Step }) {
  return (
    <ol aria-label="Steps" className="flex items-center gap-2 text-sm">
      {STEPS.map(({ n, label }, i) => {
        const done = n < step;
        const on = n === step;
        return (
          <li key={n} aria-current={on ? "step" : undefined} className="flex flex-1 items-center gap-2 last:flex-none">
            <span className={clsx("flex items-center gap-2 font-semibold", on ? "text-brand" : done ? "text-ink-2" : "text-muted")}>
              <span
                aria-hidden
                className={clsx(
                  "num grid h-6 w-6 place-items-center rounded-full border text-xs",
                  on ? "border-brand bg-brand text-on-brand" : done ? "border-brand-2 bg-brand-tint text-brand" : "border-rule-strong text-muted",
                )}
              >
                {done ? <Check className="h-3.5 w-3.5" /> : n}
              </span>
              <span>
                <span className="sr-only">Step {n} of 3: </span>
                {label}
                {done ? <span className="sr-only"> (done)</span> : null}
              </span>
            </span>
            {i < STEPS.length - 1 ? <span aria-hidden className={clsx("h-px flex-1", done ? "bg-brand-2" : "bg-rule")} /> : null}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Record a verdict in three steps: choose an outcome, give a reason, review and confirm. Options follow
 * `c.allowed_verdicts` exactly; nothing is preselected; the lane-suggested outcome only carries a badge.
 */
export function VerdictForm({ c, onDone, onSelectFlag, onReload }: { c: CaseDetail; onDone: (entryId: string, verdict: Verdict) => void; onSelectFlag: (id: string) => void; onReload: () => void }) {
  const [reviewer, setReviewer, remember] = useReviewer();
  const [reason, setReason] = useState("");
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [step, setStep] = useState<Step>(1);
  const [touched, setTouched] = useState<{ reviewer?: boolean; reason?: boolean }>({});
  const [tried, setTried] = useState<{ 1?: boolean; 2?: boolean }>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [ack, setAck] = useState(false);
  const [stamp, setStamp] = useState("");
  const alertRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const reviewerRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const focusAfterStep = useRef<"reason" | "reviewer" | null>(null);
  const firstRender = useRef(true);

  const all = validateReview({ reviewer, reason });
  const errors: ReviewErrors = {
    reviewer: tried[2] || touched.reviewer ? all.reviewer : undefined,
    reason: tried[2] || touched.reason ? all.reason : undefined,
  };
  const suggested = suggestedVerdict(c);
  const cautions = verdict === "approve" ? approveCautions(c) : [];
  const needsConfirm = verdict === "escalate_aml" || (verdict === "approve" && cautions.length > 0);

  // Move focus to the new step (heading) or to the field the server complained about.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    if (focusAfterStep.current === "reason") reasonRef.current?.focus();
    else if (focusAfterStep.current === "reviewer") reviewerRef.current?.focus();
    else headingRef.current?.focus();
    focusAfterStep.current = null;
  }, [step]);

  useEffect(() => {
    if (error) alertRef.current?.focus();
  }, [error]);

  function goTo(next: Step) {
    setError(null);
    setStep(next);
  }

  async function send() {
    if (!verdict) return;
    setBusy(true);
    setError(null);
    try {
      const res = await postReview(c.id, { kind: "verdict", decision: verdict, reviewer: reviewer.trim(), reason: reason.trim(), case_version: c.version });
      remember(reviewer.trim());
      onDone(res.entry.id, verdict);
    } catch (e) {
      setError(e);
      if (e instanceof ApiErrorException) {
        if (e.error.code === "reason_required") {
          focusAfterStep.current = "reason";
          setStep(2);
        } else if (e.error.code === "reviewer_required") {
          focusAfterStep.current = "reviewer";
          setStep(2);
        }
      }
    } finally {
      setBusy(false);
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (step === 1) {
      setTried((t) => ({ ...t, 1: true }));
      if (verdict) goTo(2);
      return;
    }
    if (step === 2) {
      setTried((t) => ({ ...t, 2: true }));
      if (all.reviewer) return void reviewerRef.current?.focus();
      if (all.reason) return void reasonRef.current?.focus();
      setStamp(formatUtc(new Date().toISOString()));
      setAck(false);
      goTo(3);
      return;
    }
    if (!verdict || (needsConfirm && !ack)) return;
    void send();
  }

  const label = verdict ? VERDICT_LABELS[verdict] : "";
  return (
    <form onSubmit={submit} noValidate aria-label="Record decision" className="flex min-h-0 flex-1 flex-col">
      <div className="space-y-5 px-4 pb-4 pt-4 sm:px-6">
        <StepStrip step={step} />

        {step === 1 ? (
          <fieldset className="space-y-3">
            <legend className="w-full">
              <h3 ref={headingRef} tabIndex={-1} data-autofocus className="text-xl focus:outline-none">
                Choose an outcome
              </h3>
              <span className="mt-1 block text-[0.9375rem] font-normal text-ink-2">Nothing is decided until you confirm. The tool only recommends; you decide.</span>
            </legend>
            <div className="space-y-2.5">
              {c.allowed_verdicts.map((v) => {
                const Icon = VERDICT_ICON[v];
                return (
                  <label
                    key={v}
                    className={clsx(
                      "group/card relative flex min-h-14 cursor-pointer items-start gap-3 rounded-lg border bg-surface p-3.5 transition-colors sm:p-4",
                      "border-rule-strong hover:border-brand-2 hover:bg-brand-tint/50",
                      "has-[input:checked]:border-brand has-[input:checked]:bg-brand-tint has-[input:checked]:shadow-[inset_0_0_0_1px_var(--brand)]",
                      "has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-brand-2",
                    )}
                  >
                    <input type="radio" name="verdict" value={v} checked={verdict === v} onChange={() => setVerdict(v)} className="peer sr-only" />
                    <Icon aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-brand-2" />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-base font-semibold text-ink">
                        {VERDICT_LABELS[v]}
                        {suggested === v ? (
                          <Badge variant="lane" icon={null} title="Suggested from the case lane. You decide.">
                            Suggested
                          </Badge>
                        ) : null}
                      </span>
                      <span className="mt-0.5 block text-[0.9375rem] leading-snug text-ink-2">{VERDICT_CONSEQUENCE[v]}</span>
                    </span>
                    <span
                      aria-hidden
                      className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border border-rule-strong bg-surface text-on-brand group-has-[input:checked]/card:border-brand group-has-[input:checked]/card:bg-brand"
                    >
                      <Check className="h-3 w-3 opacity-0 group-has-[input:checked]/card:opacity-100" />
                    </span>
                  </label>
                );
              })}
            </div>
            {tried[1] && !verdict ? <p className="text-sm font-medium text-sev-high">Choose a verdict.</p> : null}
          </fieldset>
        ) : null}

        {step === 2 && verdict ? (
          <div className="space-y-4">
            <div>
              <h3 ref={headingRef} tabIndex={-1} data-autofocus className="text-xl focus:outline-none">
                Give your reason
              </h3>
              <p className="mt-1 text-[0.9375rem] text-ink-2">
                Outcome: <strong className="text-ink">{label}</strong>. Your reason goes in the review log next to your name.
              </p>
            </div>
            {verdict === "escalate_aml" ? (
              <Notice variant="warning" title="This starts the suspicion-formed clock">
                The time of this decision is recorded as when suspicion formed. The tool does not file an STR; compliance decides outside the tool.
              </Notice>
            ) : null}
            {verdict === "request_documents" ? <Notice variant="info">Ask for documents in neutral terms. Do not say which checks fired.</Notice> : null}
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
              prompts={REASON_PROMPTS[verdict]}
              reasonRows={6}
            />
            {error ? <DecisionError ref={alertRef} error={error} onSelectFlag={onSelectFlag} onReload={onReload} /> : null}
          </div>
        ) : null}

        {step === 3 && verdict ? (
          <div className="space-y-4">
            <div>
              <h3 ref={headingRef} tabIndex={-1} data-autofocus className="text-xl focus:outline-none">
                Review and confirm
              </h3>
              <p className="mt-1 text-[0.9375rem] text-ink-2">Check what will be recorded. You can go back and change anything.</p>
            </div>
            <dl className="divide-y divide-rule rounded-lg border border-rule bg-paper-2/50 text-[0.9375rem]">
              <div className="flex items-start justify-between gap-4 p-3.5">
                <div className="min-w-0">
                  <dt className="label-caps">Outcome</dt>
                  <dd className="mt-0.5 font-semibold text-ink">{label}</dd>
                  <dd className="text-ink-2">{VERDICT_CONSEQUENCE[verdict]}</dd>
                </div>
                <Button size="sm" variant="ghost" onClick={() => goTo(1)}>
                  Change
                </Button>
              </div>
              <div className="flex items-start justify-between gap-4 p-3.5">
                <div className="min-w-0">
                  <dt className="label-caps">Your reason</dt>
                  <dd className="mt-0.5 whitespace-pre-wrap break-words text-ink">{reason.trim()}</dd>
                </div>
                <Button size="sm" variant="ghost" onClick={() => goTo(2)}>
                  Change
                </Button>
              </div>
              <div className="p-3.5">
                <dt className="label-caps">Recorded by</dt>
                <dd className="mt-0.5 font-semibold text-ink">{reviewer.trim()}</dd>
              </div>
              <div className="p-3.5">
                <dt className="label-caps">Applies to</dt>
                <dd className="mt-0.5 text-ink">
                  {c.applicant.name ?? "Unnamed applicant"} <span className="text-muted">· version {c.version}{c.lane ? ` · ${laneLabel(c.lane)}` : ""}</span>
                </dd>
              </div>
            </dl>
            {verdict === "escalate_aml" ? (
              <Notice variant="warning" title="Suspicion-formed time">
                The suspicion-formed time will be recorded as <strong className="tabular">{stamp}</strong>. The tool does not file an STR; compliance decides outside the tool.
              </Notice>
            ) : null}
            {verdict === "approve" && cautions.length > 0 ? (
              <Notice variant="warning" title="Check before you approve">
                Approving with incomplete or elevated-risk analysis: {cautions.join("; ")}. Continue?
              </Notice>
            ) : null}
            {needsConfirm ? (
              <label className="flex min-h-11 cursor-pointer items-start gap-3 text-[0.9375rem] font-medium text-ink">
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1 h-5 w-5 shrink-0 accent-[var(--brand)]" />
                <span>I understand and want to record this decision.</span>
              </label>
            ) : null}
            {error ? <DecisionError ref={alertRef} error={error} onSelectFlag={onSelectFlag} onReload={onReload} /> : null}
            <p className="text-xs text-muted">{DISCLAIMER}</p>
          </div>
        ) : null}
      </div>

      <div className="mt-auto flex flex-col-reverse gap-2 border-t border-rule bg-paper-2/60 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        {step > 1 ? (
          <Button variant="ghost" onClick={() => goTo((step - 1) as Step)} disabled={busy}>
            Back
          </Button>
        ) : (
          <span />
        )}
        {step === 1 ? (
          <Button type="submit" size="lg">
            Continue
          </Button>
        ) : step === 2 ? (
          <Button type="submit" size="lg">
            Continue to review
          </Button>
        ) : (
          <Button type="submit" size="lg" loading={busy} disabled={needsConfirm && !ack}>
            {busy ? "Recording..." : label}
          </Button>
        )}
      </div>
    </form>
  );
}
