import clsx from "clsx";
import { Check, Loader2, Minus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { pollJob } from "@/lib/api";
import type { Job, StageName, StageStatus } from "@/lib/types";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Notice";
import { Panel } from "../ui/Panel";
import { CaseErrorNotice } from "./CaseStates";

export const STAGE_ORDER: StageName[] = ["ingest", "classify", "extract", "forensics", "rules", "crossdoc", "score"];
/** Plain names for the seven pipeline stages. */
export const STAGE_LABEL: Record<StageName, string> = {
  ingest: "Reading the files",
  classify: "Recognising documents",
  extract: "Extracting figures",
  forensics: "Looking for edits",
  rules: "Running checks",
  crossdoc: "Comparing documents",
  score: "Scoring the case",
};
const STATUS_WORD: Record<StageStatus, string> = { pending: "waiting", running: "running", ok: "done", failed: "failed", skipped: "skipped" };

function StageMark({ s, n }: { s: StageStatus; n: number }) {
  const base = "grid h-6 w-6 shrink-0 place-items-center rounded-full border text-xs font-semibold";
  if (s === "ok") return <span aria-hidden className={clsx(base, "border-brand bg-brand text-on-brand")}><Check className="h-3.5 w-3.5" /></span>;
  if (s === "running") return <span aria-hidden className={clsx(base, "border-brand-2 bg-brand-tint text-brand-2")}><Loader2 className="h-3.5 w-3.5 animate-spin" /></span>;
  if (s === "failed") return <span aria-hidden className={clsx(base, "border-sev-high bg-sev-high-tint text-sev-high")}><X className="h-3.5 w-3.5" /></span>;
  if (s === "skipped") return <span aria-hidden className={clsx(base, "border-rule-strong text-muted")}><Minus className="h-3.5 w-3.5" /></span>;
  return <span aria-hidden className={clsx(base, "num border-rule-strong text-muted")}>{n}</span>;
}

/** The 7 pipeline stages from GET /jobs/{id}. Without a job id (opened from the worklist) it only shows that analysis is running. */
export function ProcessingStepper({ jobId, onFinished, onRerun }: { jobId: string | null; onFinished: (j: Job) => void; onRerun: () => void }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [elapsed, setElapsed] = useState(0);
  const finished = useRef(onFinished);
  useEffect(() => {
    finished.current = onFinished;
  });

  useEffect(() => {
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!jobId) return;
    const ctl = new AbortController();
    pollJob(jobId, setJob, ctl.signal)
      .then((j) => finished.current(j))
      .catch((e) => {
        if (e instanceof DOMException && e.name === "AbortError") return;
        setError(e);
      });
    return () => ctl.abort();
  }, [jobId]);

  const statusOf = (stage: StageName): StageStatus => {
    const rec = job?.stages.find((s) => s.stage === stage);
    if (rec && rec.status !== "pending") return rec.status;
    if (job?.state === "running" && job.progress.current_stage === stage) return "running";
    return rec?.status ?? "pending";
  };
  const idx = job?.progress.stage_index ?? 0;
  const live =
    job?.state === "failed"
      ? "Analysis failed"
      : job?.progress.current_stage
        ? `Stage ${idx} of ${job.progress.stage_count}: ${STAGE_LABEL[job.progress.current_stage]}`
        : job?.state === "queued"
          ? "Waiting to start"
          : "Analysis running";
  const secondsOf = (stage: StageName): number | null => {
    const rec = job?.stages.find((x) => x.stage === stage);
    return rec && rec.status !== "pending" && rec.status !== "running" && rec.seconds > 0 ? rec.seconds : null;
  };

  return (
    <Panel title="Analysing the documents" eyebrow="Please wait">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p role="status" aria-live="polite" className="text-[0.9375rem] font-medium text-ink">
          {live}
        </p>
        <p className="num text-sm text-muted">{elapsed} s elapsed</p>
      </div>
      <p className="mt-0.5 text-sm text-ink-2">The score and findings appear here as soon as the last stage finishes. This page updates by itself.</p>
      <ol aria-label="Analysis progress" className="mt-5 grid gap-x-3 gap-y-3 sm:grid-cols-7">
        {STAGE_ORDER.map((stage, i) => {
          const s = statusOf(stage);
          const secs = secondsOf(stage);
          return (
            <li key={stage} data-status={s} className="relative flex items-center gap-3 sm:flex-col sm:items-start sm:gap-2">
              <div className="flex items-center gap-2 sm:w-full">
                <StageMark s={s} n={i + 1} />
                <span
                  aria-hidden
                  className={clsx("hidden h-0.5 flex-1 rounded-full sm:block", s === "ok" ? "bg-brand" : s === "running" ? "bg-brand-2/50" : "bg-rule")}
                />
              </div>
              <div className="min-w-0 text-sm leading-snug">
                <p className={clsx("font-semibold", s === "pending" ? "text-muted" : "text-ink")}>{STAGE_LABEL[stage]}</p>
                <p className="text-xs text-muted">
                  <span className="sr-only">{STATUS_WORD[s]}</span>
                  <span aria-hidden>{s === "ok" ? (secs !== null ? `${secs < 10 ? secs.toFixed(1) : Math.round(secs)} s` : "done") : STATUS_WORD[s]}</span>
                </p>
              </div>
            </li>
          );
        })}
      </ol>
      {job?.state === "failed" ? (
        <div className="mt-4 space-y-3">
          <Notice variant="error" title="Analysis failed">
            {job.error ? <p>{job.error}</p> : null}
          </Notice>
          <Button onClick={onRerun}>Re-run analysis</Button>
        </div>
      ) : null}
      {error ? (
        <div className="mt-4">
          <CaseErrorNotice error={error} />
        </div>
      ) : null}
    </Panel>
  );
}
