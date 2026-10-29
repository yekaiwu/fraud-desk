"use client";

import clsx from "clsx";
import { AlertOctagon, AlertTriangle, Info, ListTree, Minus, Stamp, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { LOW_COVERAGE_RATIO, VERDICT_LABELS } from "@/lib/constants";
import { formatUtc } from "@/lib/format";
import { pageCounts, SEVERITY_ORDER } from "@/lib/flags";
import { caseStatusDescription, coverageText, laneDescription, laneLabel, plural, severityLabel, unreviewedPagesText } from "@/lib/labels";
import type { CaseDetail, Finding, Severity } from "@/lib/types";
import { ExplainChecks } from "../explain/CoverageExplainer";
import { ExplainNextStep } from "../explain/NextStepExplainer";
import { ExplainScore } from "../explain/ScoreExplainer";
import { Button } from "../ui/Button";
import { Guilloche } from "../ui/Guilloche";
import { MeterBar } from "../ui/MeterBar";
import { Panel } from "../ui/Panel";
import { RiskGauge } from "../ui/RiskGauge";
import { suggestedVerdict } from "./VerdictForm";

const SEV_ICON: Record<Severity, LucideIcon> = { high: AlertOctagon, medium: AlertTriangle, low: Minus, info: Info };
const SEV_TEXT: Record<Severity, string> = { high: "text-sev-high", medium: "text-sev-medium", low: "text-sev-low", info: "text-sev-info" };

/** The findings that matter most, in plain words: by severity, then by how much each adds to the score. Informational ones are left out. */
export function topConcerns(findings: Finding[], limit = 3): { shown: Finding[]; concerns: number } {
  const concerns = findings
    .map((f, i) => ({ f, i }))
    .filter(({ f }) => f.severity !== "info")
    .sort((a, b) => SEVERITY_ORDER.indexOf(a.f.severity) - SEVERITY_ORDER.indexOf(b.f.severity) || b.f.weight - a.f.weight || a.i - b.i)
    .map((x) => x.f);
  return { shown: concerns.slice(0, limit), concerns: concerns.length };
}

/**
 * The first thing an officer sees: how risky, why, what the tool suggests and the one obvious way to record the
 * decision. The suggestion is always labelled as a suggestion; no outcome is ever preselected.
 */
export function RecommendationHero({ c, isCurrent, why, onRecord, onShowFindings, onShowUnreviewed, onOpenActivity, onRerun, onAddDocs, onOpenTrail, actions }: {
  c: CaseDetail; isCurrent: boolean; why: string | null; onRecord: () => void; onShowFindings: () => void; onOpenActivity: () => void;
  onRerun: () => void; onAddDocs: () => void; actions: ReactNode;
  /** Jumps to the list of pages no check looked at. Falls back to the findings. */
  onShowUnreviewed?: () => void;
  /** Opens the "How this was analysed" sheet. Without it the link is not shown. */
  onOpenTrail?: () => void;
}) {
  const score = c.score ?? null;
  const partial = c.partial || !!score?.partial;
  const high = score?.band === "high";
  const suggestion = suggestedVerdict(c);
  const { shown } = topConcerns(c.findings);
  const moreFindings = c.findings.length - shown.length;
  const cov = c.coverage ?? null;
  const pages = pageCounts(c);
  const canDecide = why === null;
  const closed = c.status === "closed";
  const closing = closed && c.decision ? [...c.review].reverse().find((e) => e.kind === "verdict" && e.status === "effective") : undefined;
  const calm = c.lane === "fast" && suggestion === "approve" && !partial;

  let eyebrow = "Suggested next step (you decide)";
  let isSuggestion = true;
  let heading: string;
  let blurb: string;
  if (c.status === "error") {
    isSuggestion = false;
    eyebrow = "Status";
    heading = "This case could not be analysed";
    blurb = caseStatusDescription("error");
  } else if (closed) {
    isSuggestion = false;
    eyebrow = "Decision recorded";
    heading = c.decision ? VERDICT_LABELS[c.decision] : "Closed";
    blurb = closing ? `By ${closing.reviewer}, ${formatUtc(closing.created_at)}. Reason: ${closing.reason}` : caseStatusDescription("closed");
  } else if (c.status === "awaiting_documents") {
    isSuggestion = false;
    eyebrow = "Status";
    heading = "Waiting for documents";
    blurb = caseStatusDescription("awaiting_documents");
  } else if (calm && c.lane) {
    heading = laneLabel(c.lane);
    blurb = "Low risk, high coverage. A person still confirms.";
  } else if (suggestion && c.lane) {
    // The outcome the tool would suggest (the same one the decision sheet badges "Suggested"), not just the queue name.
    heading = VERDICT_LABELS[suggestion];
    blurb = laneDescription(c.lane);
  } else {
    heading = "Nothing stands out yet";
    blurb = "Look through the documents and findings, then record your decision. The tool does not decide for you.";
  }

  return (
    <Panel tone={high && c.status !== "closed" ? "attention" : "default"} className="relative z-20">
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden rounded-lg">
        <Guilloche variant="rosette" opacity={0.06} className="absolute -right-24 -top-28 h-80 w-80 text-brand" />
      </div>
      <div className="relative grid gap-x-8 gap-y-6 md:grid-cols-[auto_minmax(0,1fr)] lg:grid-cols-[auto_minmax(0,1fr)_15.5rem]">
        <div className="flex flex-col items-center justify-start md:col-start-1 md:row-start-1 md:pt-1">
          {score ? (
            <>
              <RiskGauge score={score.value} band={score.band} partial={partial} size="lg" />
              <p className="mt-1.5 flex items-center gap-1.5 text-sm text-ink-2">
                Risk score
                <ExplainScore c={c} />
              </p>
            </>
          ) : (
            <div className="grid h-32 w-60 place-items-center rounded-lg border border-dashed border-rule-strong text-center text-sm text-muted sm:w-72">
              No score yet
            </div>
          )}
        </div>

        <div className="min-w-0 space-y-4 md:col-start-2 md:row-span-2 md:row-start-1">
          <div>
            <p className="label-caps flex items-center gap-1.5">
              {eyebrow}
              {isSuggestion && score ? <ExplainNextStep c={c} /> : null}
            </p>
            <h2 className="mt-1 text-[1.625rem] leading-tight sm:text-[1.875rem]">{heading}</h2>
            <p className="mt-1.5 max-w-prose text-[0.9375rem] text-ink-2">{blurb}</p>
            {partial && !closed ? (
              <p className="mt-2 flex max-w-prose items-start gap-2 text-[0.9375rem] font-medium text-sev-medium">
                <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                <span>Partial score: some checks or pages did not run, so the real score may be higher.</span>
              </p>
            ) : null}
          </div>

          {c.status !== "error" ? (
            <div>
              <p className="label-caps rule-statement-soft pb-1.5">Why</p>
              {shown.length > 0 ? (
                <ul className="mt-2.5 space-y-2">
                  {shown.map((f) => {
                    const Icon = SEV_ICON[f.severity];
                    return (
                      <li key={f.id} className="flex items-start gap-2.5">
                        <Icon aria-hidden className={clsx("mt-[0.2rem] h-4 w-4 shrink-0", SEV_TEXT[f.severity])} />
                        <p className="min-w-0 text-[0.9375rem] leading-snug text-ink">
                          <span className="sr-only">{severityLabel(f.severity)}: </span>
                          <span className="line-clamp-2">{f.title}</span>
                        </p>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="mt-2.5 text-[0.9375rem] text-ink-2">
                  {c.findings.length === 0 ? "The checks that ran raised no findings." : `No concerns, only ${c.findings.length} ${plural(c.findings.length, "item")} for information.`}
                </p>
              )}
              {moreFindings > 0 ? (
                <button type="button" onClick={onShowFindings} className="mt-2 inline-flex min-h-11 items-center rounded-md px-1 text-sm font-semibold text-brand-2 underline decoration-brand-2/40 underline-offset-4 hover:bg-brand-tint hover:decoration-brand-2 sm:min-h-9">
                  +{moreFindings} more {plural(moreFindings, "finding")}
                </button>
              ) : null}
            </div>
          ) : null}

          {onOpenTrail && (c.stages.length > 0 || score) ? (
            <button
              type="button"
              onClick={onOpenTrail}
              className="-ml-1 inline-flex min-h-11 items-center gap-1.5 rounded-md px-1 text-sm font-semibold text-brand-2 underline decoration-brand-2/40 underline-offset-4 hover:bg-brand-tint hover:decoration-brand-2 sm:min-h-9"
            >
              <ListTree aria-hidden className="h-4 w-4" />
              How this was analysed
            </button>
          ) : null}
        </div>

        {cov && c.status !== "error" ? (
          <div className="w-full max-w-72 space-y-2.5 justify-self-center md:col-start-1 md:row-start-2 md:justify-self-start">
            <MeterBar
              label="Checks completed"
              value={cov.ran}
              max={cov.applicable}
              valueText={coverageText(cov.ran, cov.applicable)}
              marker={LOW_COVERAGE_RATIO}
              labelAddon={<ExplainChecks c={c} />}
              tone={cov.ratio < LOW_COVERAGE_RATIO ? "medium" : "brand"}
            />
            {pages.unchecked > 0 ? (
              <p className="flex items-start gap-2 rounded-md bg-sev-medium-tint px-3 py-2 text-sm text-ink">
                <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-sev-medium" />
                <span>
                  <strong>{unreviewedPagesText(pages.unchecked)}</strong> (of {pages.total}). No check looked at {pages.unchecked === 1 ? "it" : "them"}.{" "}
                  <button type="button" onClick={onShowUnreviewed ?? onShowFindings} className="font-semibold text-brand-2 underline underline-offset-2">
                    See which
                  </button>
                </span>
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="space-y-2.5 md:col-span-2 md:row-start-3 lg:col-span-1 lg:col-start-3 lg:row-span-2 lg:row-start-1 lg:border-l lg:border-rule lg:pl-6">
          {closed ? (
            <Button size="lg" fullWidth variant="secondary" onClick={onOpenActivity}>
              See decision history
            </Button>
          ) : (
            <>
              <Button
                size="lg"
                fullWidth
                className="max-sm:hidden"
                disabled={!canDecide}
                icon={<Stamp aria-hidden className="h-4 w-4" />}
                onClick={onRecord}
                title={canDecide ? undefined : "Decisions are not possible in this state"}
              >
                Record decision
              </Button>
              {canDecide ? null : <p className="text-sm font-medium text-ink-2">{why}</p>}
              {c.status === "error" && isCurrent ? (
                <Button fullWidth variant="secondary" onClick={onRerun}>
                  Re-run analysis
                </Button>
              ) : null}
              {c.status === "awaiting_documents" && isCurrent ? (
                <Button fullWidth variant="secondary" onClick={onAddDocs}>
                  Add documents
                </Button>
              ) : null}
            </>
          )}
          {actions}
        </div>
      </div>
    </Panel>
  );
}
