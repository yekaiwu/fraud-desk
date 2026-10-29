"use client";

import { SEVERITY_ORDER } from "@/lib/flags";
import { formatRatio } from "@/lib/format";
import { bandLabel, coverageText, plural, severityLabel } from "@/lib/labels";
import { useExplainMethod } from "@/lib/useExplain";
import type { Band, ExplainMethod, QueueItem } from "@/lib/types";
import { Explain } from "./Explain";
import { ExplainLead, ExplainNote, ExplainSection, ExplainTable, Loading, MethodUnavailable } from "./parts";

export const RISK_COLUMN_LABEL = "How the risk score is calculated";
export const CHECKS_COLUMN_LABEL = "What checks completed means";

function weightRows(method: ExplainMethod) {
  return SEVERITY_ORDER.map((s) => ({ key: s, cells: [severityLabel(s), method.score.weights[s]] }));
}

function bandWords(method: ExplainMethod): { band: Band; text: string }[] {
  const { medium_from: m, high_from: h } = method.score.bands;
  return [
    { band: "low", text: `0 to ${m - 1}` },
    { band: "medium", text: `${m} to ${h - 1}` },
    { band: "high", text: `${h} to ${method.score.cap}` },
  ];
}

/**
 * The Risk column. In a table header it explains the method in general; on a card (no header) the row's own numbers
 * are added, from the queue item: the worklist does not carry the findings, so the full breakdown is on the case.
 */
export function RiskColumnExplainer({ item }: { item?: QueueItem }) {
  const { method, loading, unavailable } = useExplainMethod();
  return (
    <>
      <ExplainLead>
        The risk score adds up points for what the checks found. Each finding counts once and adds points by how serious it is. A higher score means the case needs a closer look sooner.
      </ExplainLead>
      {item && item.score !== null && item.band ? (
        <ExplainSection title="This case">
          <p className="tabular">
            Score <strong className="text-ink">{item.partial ? "at least " : ""}{item.score}</strong>, {bandLabel(item.band, item.partial).toLowerCase()}. {item.findings_count} {plural(item.findings_count, "finding")} counted
            {item.high_count > 0 ? `, ${item.high_count} of high concern` : ""}.
          </p>
          {item.partial ? <p>The score is partial: some checks or pages did not run, so the real score may be higher.</p> : null}
          <p className="text-sm text-muted">Open the case to see the points finding by finding.</p>
        </ExplainSection>
      ) : null}
      <ExplainSection title="Points and bands">
        {loading ? <Loading /> : null}
        {method ? (
          <>
            <ExplainTable caption="Points each finding adds, by severity" columns={[{ label: "Concern" }, { label: "Points", numeric: true }]} rows={weightRows(method)} />
            <ExplainTable
              caption="Score bands"
              columns={[{ label: "Band" }, { label: "Score", numeric: true }]}
              rows={bandWords(method).map((b) => ({ key: b.band, cells: [bandLabel(b.band), b.text], highlight: item?.band === b.band, strong: item?.band === b.band }))}
            />
            <p className="text-sm">The score stops at {method.score.cap}.</p>
          </>
        ) : unavailable ? (
          <MethodUnavailable />
        ) : null}
      </ExplainSection>
      <ExplainNote>A high score means stronger or more concerns, not proof of fraud.</ExplainNote>
    </>
  );
}

export function ExplainRiskColumn({ item, className }: { item?: QueueItem; className?: string }) {
  return (
    <Explain label={RISK_COLUMN_LABEL} title="Risk score" className={className}>
      <RiskColumnExplainer item={item} />
    </Explain>
  );
}

/** The Checks completed column or card line. */
export function ChecksColumnExplainer({ item }: { item?: QueueItem }) {
  const { method, loading, unavailable } = useExplainMethod();
  const cov = item?.coverage ?? null;
  return (
    <>
      <ExplainLead>{method?.coverage.definition ?? "Checks completed shows how many of the checks that apply to a case were able to run."}</ExplainLead>
      {loading ? <Loading /> : null}
      {unavailable ? <MethodUnavailable /> : null}
      {cov ? (
        <ExplainSection title="This case">
          <p className="tabular">
            <strong className="text-ink">{coverageText(cov.ran, cov.applicable)}</strong> ({formatRatio(cov.ratio)}).
            {cov.pages_unchecked > 0 ? ` ${cov.pages_unchecked} of ${cov.pages_total} ${plural(cov.pages_total, "page")} not reviewed by any check.` : ""}
          </p>
          <p className="text-sm text-muted">Open the case to see which checks did not run and why.</p>
        </ExplainSection>
      ) : null}
      <ExplainNote>A check that did not run is not a clearance.</ExplainNote>
    </>
  );
}

export function ExplainChecksColumn({ item, className }: { item?: QueueItem; className?: string }) {
  return (
    <Explain label={CHECKS_COLUMN_LABEL} title="Checks completed" className={className}>
      <ChecksColumnExplainer item={item} />
    </Explain>
  );
}
