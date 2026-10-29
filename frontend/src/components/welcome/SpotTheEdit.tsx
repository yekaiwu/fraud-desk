"use client";

import clsx from "clsx";
import { Check, FileSearch, Info, Play, RotateCcw, ShieldOff } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { MeterBar } from "@/components/ui/MeterBar";
import { RiskGauge } from "@/components/ui/RiskGauge";
import { HARD_STOP_LABEL, bandLabel, coverageText, scoreSummary } from "@/lib/labels";
import {
  DEMO_COVERAGE,
  RUNNING_BALANCE_FINDING,
  WEIGHTS,
  bandOf,
  checkRunningBalance,
  findingsFor,
  formatCents,
  sampleStatement,
  scoreOf,
  type RowCheck,
  type Statement,
} from "./demo";

type Version = "edited" | "clean";

const VERSIONS: { id: Version; label: string }[] = [
  { id: "edited", label: "Statement as received" },
  { id: "clean", label: "Clean version" },
];

function StatementTable({ statement, checks, ran }: { statement: Statement; checks: RowCheck[]; ran: boolean }) {
  const byRow = new Map(checks.map((c) => [c.rowId, c]));
  return (
    <div role="region" aria-label="Sample bank statement" tabIndex={0} className="overflow-x-auto rounded-md border border-rule bg-surface">
      <div className="min-w-[19rem]">
        <div className="flex items-start justify-between gap-3 border-b border-ink/70 px-3 py-3 sm:px-4">
          <div>
            <p className="font-display text-base font-semibold leading-tight text-brand sm:text-lg">{statement.company}</p>
            <p className="mt-0.5 text-xs text-muted">{statement.account}</p>
          </div>
          <p className="label-caps text-right">{statement.period}</p>
        </div>
        <table className="w-full border-collapse text-[0.8125rem] sm:text-sm">
          <caption className="sr-only">Seven entries with the balance printed after each one.</caption>
          <thead>
            <tr className="label-caps bg-paper-2/70 text-left">
              <th scope="col" className="px-3 py-2 font-semibold sm:px-4">Entry</th>
              <th scope="col" className="px-1.5 py-2 text-right font-semibold">Amount</th>
              <th scope="col" className="px-1.5 py-2 text-right font-semibold">Balance</th>
              <th scope="col" className="w-14 py-2 pl-2 pr-3 text-center font-semibold sm:pr-4">
                <span className="sr-only">Result of the check</span>
                <span aria-hidden>Check</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-rule text-muted">
              <td className="px-3 py-2 sm:px-4">Opening balance</td>
              <td />
              <td className="num px-1.5 py-2 text-right">{formatCents(statement.opening)}</td>
              <td />
            </tr>
            {statement.rows.map((row) => {
              const result = byRow.get(row.id);
              const failed = ran && result && !result.ok;
              const change = row.credit - row.debit;
              return (
                <tr key={row.id} data-failed={failed ? "true" : undefined} className={clsx("border-t border-rule align-top", failed && "bg-sev-high-tint")}>
                  <td className={clsx("px-3 py-2 sm:px-4", failed && "border-l-[3px] border-l-sev-high pl-[calc(0.75rem-3px)] sm:pl-[calc(1rem-3px)]")}>
                    <span className="block font-medium text-ink">{row.description}</span>
                    <span className="num block text-xs text-muted">{row.date}</span>
                  </td>
                  <td className="num whitespace-nowrap px-1.5 py-2 text-right text-ink">
                    {change < 0 ? "-" : "+"}
                    {formatCents(Math.abs(change))}
                  </td>
                  <td className="num whitespace-nowrap px-1.5 py-2 text-right text-ink">{formatCents(row.printed)}</td>
                  <td className="py-2 pr-3 text-center sm:pr-4">
                    {!ran || !result ? (
                      <span aria-hidden className="text-muted">
                        -
                      </span>
                    ) : result.ok ? (
                      <>
                        <Check aria-hidden className="mx-auto h-4 w-4 text-sev-low" />
                        <span className="sr-only">Adds up</span>
                      </>
                    ) : (
                      <>
                        <span aria-hidden className="mx-auto grid h-5 w-5 place-items-center rounded bg-sev-high text-[0.7rem] font-bold leading-none text-surface">
                          1
                        </span>
                        <span className="sr-only">Flagged: the balance does not add up</span>
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EvidenceRow({ label, value, strong = false, tone }: { label: string; value: string; strong?: boolean; tone?: "bad" }) {
  return (
    <div className={clsx("flex items-baseline justify-between gap-3", strong && "border-t border-ink/60 pt-1.5", tone === "bad" && "text-sev-high")}>
      <dt className={clsx("text-ink-2", strong && "font-semibold text-ink", tone === "bad" && "font-semibold text-sev-high")}>{label}</dt>
      <span aria-hidden className="leader hidden sm:block" />
      <dd className={clsx("num whitespace-nowrap text-ink", strong && "font-medium", tone === "bad" && "font-medium text-sev-high")}>{value}</dd>
    </div>
  );
}

export function SpotTheEdit() {
  const [version, setVersion] = useState<Version>("edited");
  const [run, setRun] = useState(0); // 0 = not run; each press adds one so the gauge sweeps again
  const statement = sampleStatement(version === "edited");
  const checks = checkRunningBalance(statement);
  const findings = findingsFor(checks);
  const score = scoreOf(findings.map((f) => f.severity));
  const band = bandOf(score);
  const ran = run > 0;
  const failedCheck = checks.find((c) => !c.ok);
  const failedRow = failedCheck ? statement.rows.find((r) => r.id === failedCheck.rowId) : undefined;
  const coverage = coverageText(DEMO_COVERAGE.ran, DEMO_COVERAGE.applicable);

  const announcement = !ran
    ? ""
    : findings.length
      ? `Checks complete. One finding: ${RUNNING_BALANCE_FINDING.toLowerCase()}, on the ${failedRow?.date} entry. It adds ${WEIGHTS.high} points. ${scoreSummary(score, band)}. ${HARD_STOP_LABEL}.`
      : `Checks complete. No concerns found. ${coverage}. ${scoreSummary(score, band)}.`;

  function pick(next: Version) {
    if (next === version) return;
    setVersion(next);
    setRun(0);
  }

  return (
    <div className="mt-12 overflow-hidden rounded-xl border border-on-brand/25 bg-paper shadow-sheet">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-rule bg-surface px-4 py-3 sm:px-6">
        <div role="group" aria-label="Which version of the statement" className="inline-flex rounded-md border border-rule-strong bg-paper-2 p-0.5">
          {VERSIONS.map((v) => (
            <button
              key={v.id}
              type="button"
              aria-pressed={version === v.id}
              onClick={() => pick(v.id)}
              className={clsx(
                "min-h-10 rounded-[5px] px-3 text-sm font-semibold transition-colors sm:px-4",
                version === v.id ? "bg-brand text-on-brand shadow-paper" : "text-ink-2 hover:bg-surface hover:text-brand",
              )}
            >
              {v.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setRun((n) => n + 1)}
          className="inline-flex min-h-11 items-center gap-2 rounded-md border border-brand bg-brand px-5 text-[0.9375rem] font-semibold text-on-brand shadow-paper transition-colors hover:border-brand-2 hover:bg-brand-2"
        >
          {ran ? <RotateCcw aria-hidden className="h-4 w-4" /> : <Play aria-hidden className="h-4 w-4" />}
          {ran ? "Run again" : "Run the checks"}
        </button>
      </div>

      <div className="grid gap-6 p-4 sm:p-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:gap-8 lg:p-8">
        <div>
          <p className="mb-3 text-[0.9375rem] text-ink-2">
            {version === "edited"
              ? "One figure on this statement was changed after the bank issued it. Can you see which? Then let the desk look."
              : "The same statement as the bank issued it, before anyone touched it."}
          </p>
          <StatementTable statement={statement} checks={checks} ran={ran} />
        </div>

        <div className="lg:min-h-[34rem]">
          <p role="status" className="sr-only">
            {announcement}
          </p>
          {!ran ? (
            <div className="flex h-full min-h-60 flex-col items-center justify-center rounded-lg border-2 border-dashed border-rule-strong bg-paper-2/50 px-6 py-10 text-center">
              <FileSearch aria-hidden className="h-8 w-8 text-brand-2" />
              <p className="mt-3 font-display text-xl font-semibold text-ink">Nothing has been checked yet</p>
              <p className="mt-1 max-w-xs text-[0.9375rem] text-ink-2">Press &ldquo;Run the checks&rdquo;. The desk recomputes every balance from the line above it and shows its working.</p>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-lg border border-rule bg-surface p-4 shadow-paper [&_[role=img]]:w-44 sm:[&_[role=img]]:w-52">
                <RiskGauge key={`${version}-${run}`} score={score} band={band} />
                <div className="min-w-0 flex-1 basis-44 text-[0.9375rem] text-ink-2">
                  <p className="label-caps">Risk score</p>
                  <p className="mt-1 font-display text-xl font-semibold leading-tight text-ink">{bandLabel(band)}</p>
                  <p className="mt-1.5">
                    {findings.length ? (
                      <>
                        One high concern, <span className="num">{WEIGHTS.high}</span> points. Below 30 is low, 30 to 59 medium.
                      </>
                    ) : (
                      "No findings, so no points."
                    )}
                  </p>
                </div>
              </div>

              {findings.length && failedCheck && failedRow ? (
                <article aria-label="Finding" className="rounded-lg border border-rule border-l-[3px] border-l-sev-high bg-surface p-4 shadow-paper sm:p-5">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="high">High concern</Badge>
                    <Badge variant="neutral" icon={ShieldOff}>
                      {HARD_STOP_LABEL}
                    </Badge>
                    <span className="ml-auto text-xs text-muted">
                      Adds <span className="num">{WEIGHTS.high}</span> to the risk score
                    </span>
                  </div>
                  <h3 className="mt-3 flex items-start gap-2.5 text-[1.125rem] leading-snug">
                    <span aria-hidden className="mt-0.5 grid h-6 min-w-6 place-items-center rounded bg-sev-high px-1 font-sans text-xs font-bold leading-none text-surface">
                      1
                    </span>
                    {RUNNING_BALANCE_FINDING}
                  </h3>
                  <p className="mt-1.5 text-sm text-muted">
                    Bank statement, {failedRow.date} · {failedRow.description}
                  </p>
                  <dl className="mt-4 space-y-1.5 rounded-md bg-paper-2/70 px-3 py-3 text-[0.9375rem]">
                    <EvidenceRow label="Balance printed above" value={formatCents(failedCheck.previous)} />
                    <EvidenceRow label={failedCheck.change < 0 ? "Money out on this row" : "Money in on this row"} value={`${failedCheck.change < 0 ? "-" : "+"} ${formatCents(Math.abs(failedCheck.change))}`} />
                    <EvidenceRow label="Balance it should show" value={formatCents(failedCheck.expected)} strong />
                    <EvidenceRow label="Balance actually printed" value={formatCents(failedCheck.printed)} tone="bad" />
                  </dl>
                  <p className="mt-3 text-sm text-ink-2">
                    The printed balance is <span className="num font-medium text-ink">{formatCents(Math.abs(failedCheck.difference))}</span> {failedCheck.difference < 0 ? "lower" : "higher"} than the arithmetic allows, so one of the figures on this row was changed. Every later row still agrees with the printed balance, which is why only this row is marked.
                  </p>
                </article>
              ) : (
                <div className="rounded-lg border border-sev-low/35 bg-sev-low-tint p-4 sm:p-5">
                  <p className="flex items-center gap-2 font-display text-xl font-semibold text-ink">
                    <Check aria-hidden className="h-5 w-5 shrink-0 text-sev-low" />
                    No concerns found. {coverage}.
                  </p>
                  <p className="mt-2 text-[0.9375rem] text-ink-2">Every balance on this statement follows from the line above it. That is a result for these checks only; the coverage line below says how many ran.</p>
                </div>
              )}

              <MeterBar label="Checks completed" value={DEMO_COVERAGE.ran} max={DEMO_COVERAGE.applicable} valueText={coverage} marker={0.7} />
              <p className="flex items-start gap-2 text-xs text-muted">
                <Info aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                The marker is the 70% line below which a case is shown as low coverage.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
