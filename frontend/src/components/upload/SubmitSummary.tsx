import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { Button } from "../ui/Button";
import { Panel } from "../ui/Panel";
import { formatBytes } from "@/lib/format";
import { laneDescription, laneLabel, plural } from "@/lib/labels";
import { caseTypeOf, type Recognition } from "@/lib/uploadChecklist";
import type { UseCase } from "@/lib/types";

export type SubmitPhase = { kind: "idle" } | { kind: "uploading"; count: number } | { kind: "created"; caseId: string; jobId: string; count: number };

function Row({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[6.5rem_1fr] gap-x-3 py-2">
      <dt className="label-caps pt-0.5">{term}</dt>
      <dd className="min-w-0 text-[0.9375rem] text-ink">{children}</dd>
    </div>
  );
}

const NEXT_STEPS = ["We read every page and pull out the figures and names.", "We run the checks and rank the case by risk.", "The case appears for you to review. Nothing is sent to the customer."];

/** Step 3: what will be submitted, what is missing, what happens next, and the one primary button. */
export function SubmitSummary({
  useCase,
  fileCount,
  totalBytes,
  recognition,
  blockMessage,
  phase,
}: {
  useCase: UseCase;
  fileCount: number;
  totalBytes: number;
  recognition: Recognition;
  blockMessage: string | null;
  phase: SubmitPhase;
}) {
  const type = caseTypeOf(useCase);
  const total = recognition.rows.length;
  const busy = phase.kind !== "idle";
  return (
    <Panel eyebrow="Step 3 of 3" title="Review and start">
      <dl className="divide-y divide-rule">
        <Row term="Case type">{type.title}</Row>
        <Row term="Goes to">
          <span className="font-semibold text-brand">{laneLabel(type.lane)}</span>
          <span className="block text-sm text-ink-2">{laneDescription(type.lane)}</span>
        </Row>
        <Row term="Documents">{fileCount === 0 ? <span className="text-ink-2">None added yet</span> : <span className="tabular">{fileCount} {plural(fileCount, "file")}, {formatBytes(totalBytes)}</span>}</Row>
        <Row term="Usual set">
          {fileCount === 0 ? (
            <span className="text-ink-2">{total} kinds usually needed</span>
          ) : (
            <span>
              {recognition.foundCount} of {total} added
              {recognition.missing.length > 0 ? <span className="block text-sm text-ink-2">Not yet added: {recognition.missing.map((m) => m.label).join(", ")}</span> : null}
            </span>
          )}
        </Row>
      </dl>
      {fileCount > 0 && recognition.missing.length > 0 ? (
        <p className="mt-3 rounded-md bg-paper-2 px-3 py-2 text-sm text-ink-2">
          You can still start. Checks that need a missing document will show as not run, and the score will say it is partial.
        </p>
      ) : null}

      <div className="mt-5">
        {phase.kind === "created" ? (
          <div role="status" className="rounded-lg border border-rule bg-paper-2/60 p-4">
            <p className="font-display text-[1.0625rem] font-semibold">Case created</p>
            <ol className="mt-2 space-y-1.5 text-[0.9375rem]">
              <li className="flex items-center gap-2 text-ink">
                <CheckCircle2 aria-hidden className="h-4 w-4 text-sev-low" />
                Uploaded {phase.count} {plural(phase.count, "file")}
              </li>
              <li className="flex items-center gap-2 text-ink">
                <Loader2 aria-hidden className="h-4 w-4 animate-spin text-brand-2" />
                Reading documents...
              </li>
              <li className="flex items-center gap-2 text-ink-2">
                <Circle aria-hidden className="h-4 w-4 text-rule-strong" />
                Running checks...
              </li>
            </ol>
            <Button href={`/cases/${phase.caseId}?job=${phase.jobId}`} variant="secondary" fullWidth className="mt-3">
              Open the case
            </Button>
          </div>
        ) : (
          <>
            <Button type="submit" size="lg" fullWidth loading={phase.kind === "uploading"} disabled={!busy && !!blockMessage} aria-describedby={blockMessage && !busy ? "start-block" : undefined}>
              {phase.kind === "uploading" ? `Uploading ${phase.count} ${plural(phase.count, "file")}...` : "Start analysis"}
            </Button>
            {blockMessage && !busy ? (
              <p id="start-block" className="mt-2 text-sm text-ink-2">
                {blockMessage}
              </p>
            ) : null}
          </>
        )}
      </div>

      <div className="mt-5 border-t border-rule pt-4">
        <p className="label-caps">What happens next</p>
        <ol className="mt-2 space-y-2">
          {NEXT_STEPS.map((s, i) => (
            <li key={s} className="flex gap-3 text-[0.9375rem] text-ink-2">
              <span aria-hidden className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border border-rule-strong font-display text-xs font-semibold text-brand">
                {i + 1}
              </span>
              <span>{s}</span>
            </li>
          ))}
        </ol>
      </div>
    </Panel>
  );
}
