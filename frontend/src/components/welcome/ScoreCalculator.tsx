"use client";

import clsx from "clsx";
import { useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { RiskGauge } from "@/components/ui/RiskGauge";
import { bandLabel, scoreSummary, severityLabel } from "@/lib/labels";
import type { Band, Severity } from "@/lib/types";
import { HIGH_FROM, MEDIUM_FROM, SAMPLE_FINDINGS, SCORE_CAP, WEIGHTS, bandOf, rawScore, scoreOf } from "./demo";

const ORDER: Severity[] = ["high", "medium", "low", "info"];

const BANDS: { band: Band; range: string }[] = [
  { band: "low", range: `Under ${MEDIUM_FROM}` },
  { band: "medium", range: `${MEDIUM_FROM} to ${HIGH_FROM - 1}` },
  { band: "high", range: `${HIGH_FROM} and over` },
];

const BAND_ACTIVE: Record<Band, string> = {
  low: "border-sev-low bg-sev-low-tint text-sev-low",
  medium: "border-sev-medium bg-sev-medium-tint text-sev-medium",
  high: "border-sev-high bg-sev-high-tint text-sev-high",
};

export function ScoreCalculator() {
  const [ticked, setTicked] = useState<ReadonlySet<string>>(() => new Set(["f1"]));
  const chosen = SAMPLE_FINDINGS.filter((f) => ticked.has(f.id));
  const severities = chosen.map((f) => f.severity);
  const raw = rawScore(severities);
  const score = scoreOf(severities);
  const band = bandOf(score);
  const capped = raw > SCORE_CAP;

  function toggle(id: string) {
    setTicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const parts = chosen.filter((f) => WEIGHTS[f.severity] > 0).map((f) => WEIGHTS[f.severity]);
  const sum = parts.length ? parts.join(" + ") : "0";
  const status = `${scoreSummary(score, band)}. ${chosen.length} of ${SAMPLE_FINDINGS.length} findings ticked, ${raw} points${capped ? `, capped at ${SCORE_CAP}` : ""}.`;

  return (
    <div className="mt-12 grid gap-8 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] lg:gap-12">
      <div role="group" aria-labelledby="calc-legend" className="min-w-0">
        <div className="mb-3 flex w-full flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <span id="calc-legend" className="text-[0.9375rem] font-semibold text-ink">Tick the findings a case might have</span>
          <span className="flex gap-1 text-sm">
            <button type="button" onClick={() => setTicked(new Set(SAMPLE_FINDINGS.map((f) => f.id)))} className="min-h-11 rounded-md px-2.5 font-semibold text-brand-2 hover:bg-brand-tint">
              Tick all
            </button>
            <button type="button" onClick={() => setTicked(new Set())} className="min-h-11 rounded-md px-2.5 font-semibold text-brand-2 hover:bg-brand-tint">
              Clear
            </button>
          </span>
        </div>
        <ul className="space-y-2.5">
          {SAMPLE_FINDINGS.map((f) => {
            const on = ticked.has(f.id);
            return (
              <li key={f.id}>
                <label
                  className={clsx(
                    "flex min-h-14 cursor-pointer items-start gap-3 rounded-lg border bg-surface p-3.5 shadow-paper transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-brand-2 sm:gap-4 sm:p-4",
                    on ? "border-brand-2 bg-brand-tint/60" : "border-rule hover:border-rule-strong",
                  )}
                >
                  <input type="checkbox" checked={on} onChange={() => toggle(f.id)} className="mt-1 h-5 w-5 shrink-0 cursor-pointer accent-brand" />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
                      <Badge variant={f.severity}>{severityLabel(f.severity)}</Badge>
                      <span className="text-xs text-muted">{f.seen}</span>
                    </span>
                    <span className="mt-1.5 block text-[0.9375rem] font-medium leading-snug text-ink">{f.text}</span>
                  </span>
                  <span className="num shrink-0 pt-0.5 text-sm font-medium text-ink-2" aria-label={`${WEIGHTS[f.severity]} points`}>
                    +{WEIGHTS[f.severity]}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
        <p
          aria-hidden
          className="sticky bottom-3 z-10 mt-4 flex items-center justify-between gap-3 rounded-lg border border-rule-strong bg-surface px-4 py-2.5 shadow-lift lg:hidden"
        >
          <span className="text-sm font-semibold text-ink-2">
            Score <span className="font-display text-2xl tabular text-ink">{score}</span>
          </span>
          <Badge variant={band}>{bandLabel(band)}</Badge>
        </p>
      </div>

      <div className="lg:sticky lg:top-24 lg:self-start">
        <p role="status" className="sr-only">
          {status}
        </p>
        <div className="rounded-xl border border-rule bg-surface p-5 shadow-lift sm:p-6">
          <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-2">
            <RiskGauge key={score} score={score} band={band} />
          </div>
          <p className="mt-2 text-center font-display text-2xl font-semibold tabular text-ink" aria-hidden>
            {capped ? `${raw} points, capped at ${SCORE_CAP}` : `${raw} ${raw === 1 ? "point" : "points"}`}
          </p>
          <p className="mt-1 text-center text-sm text-ink-2">
            <span className="num">{sum}</span>
            {capped ? <span> = {raw}, and the score never goes above {SCORE_CAP}</span> : <span> = {raw}</span>}
          </p>

          <ul className="mt-5 grid grid-cols-3 gap-2" aria-label="Bands">
            {BANDS.map((b) => (
              <li key={b.band} aria-current={b.band === band ? "true" : undefined} className={clsx("rounded-md border px-2 py-2 text-center", b.band === band ? BAND_ACTIVE[b.band] : "border-rule bg-paper text-muted")}>
                <span className="block text-xs font-semibold uppercase tracking-[0.08em]">{bandLabel(b.band).replace(" risk", "")}</span>
                <span className="num block text-xs">{b.range}</span>
              </li>
            ))}
          </ul>

          <table className="mt-5 w-full border-collapse text-sm">
            <caption className="sr-only">Points by concern level</caption>
            <thead>
              <tr className="label-caps text-left">
                <th scope="col" className="py-1.5 font-semibold">Concern</th>
                <th scope="col" className="py-1.5 text-right font-semibold">Points each</th>
                <th scope="col" className="py-1.5 text-right font-semibold">Ticked</th>
                <th scope="col" className="py-1.5 text-right font-semibold">Adds</th>
              </tr>
            </thead>
            <tbody>
              {ORDER.map((sev) => {
                const n = chosen.filter((f) => f.severity === sev).length;
                return (
                  <tr key={sev} className="border-t border-rule">
                    <th scope="row" className="py-1.5 text-left font-medium text-ink-2">{severityLabel(sev)}</th>
                    <td className="num py-1.5 text-right text-ink-2">{WEIGHTS[sev]}</td>
                    <td className="num py-1.5 text-right text-ink-2">{n}</td>
                    <td className="num py-1.5 text-right font-medium text-ink">{n * WEIGHTS[sev]}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="mt-4 text-sm leading-relaxed text-ink-2">
          These are the product&rsquo;s documented defaults, not tuned per case. A finding counts once, however many documents show it, so repeating a flag never inflates the score.
        </p>
      </div>
    </div>
  );
}
