"use client";

import { humanisePartialReason, SEVERITY_ORDER } from "@/lib/flags";
import { bandLabel, plural, severityLabel } from "@/lib/labels";
import { scoreBreakdown, totalText } from "@/lib/explain";
import { useExplainMethod } from "@/lib/useExplain";
import type { Band, CaseDetail, ExplainMethod } from "@/lib/types";
import { Explain } from "./Explain";
import { ExplainFacts, ExplainLead, ExplainNote, ExplainSection, ExplainTable, Loading, MethodUnavailable, TechnicalDetail, type ExplainRow } from "./parts";

export const SCORE_LABEL = "How the risk score is calculated";

function bandRows(method: ExplainMethod, current: Band, partial: boolean): ExplainRow[] {
  const { medium_from: m, high_from: h } = method.score.bands;
  const top = method.score.cap;
  const rows: { band: Band; range: string }[] = [
    { band: "low", range: `0 to ${m - 1}` },
    { band: "medium", range: `${m} to ${h - 1}` },
    { band: "high", range: `${h} to ${top}` },
  ];
  return rows.map((r) => ({
    key: r.band,
    highlight: r.band === current,
    strong: r.band === current,
    cells: [bandLabel(r.band), r.band === current ? `${r.range} (this case${partial ? ", at least" : ""})` : r.range],
  }));
}

/** The whole score explanation for one case: the plain paragraph, this case's points, the bands, the technical detail. */
export function ScoreExplainer({ c }: { c: CaseDetail }) {
  const { method, loading, unavailable } = useExplainMethod();
  const b = scoreBreakdown(c, method);
  if (!b) {
    return <ExplainLead>There is no risk score yet. It appears when the analysis finishes: points are added up from the findings the checks raise.</ExplainLead>;
  }
  const weights = method ? SEVERITY_ORDER.map((s) => `${severityLabel(s)} ${method.score.weights[s]}`).join(", ") : null;
  return (
    <>
      <ExplainLead>
        The risk score adds up points for what the checks found. Each finding counts once, however many flags sit behind it, and adds points by how serious it is
        {weights ? `: ${weights}.` : "."} {method ? `The score stops at ${method.score.cap}.` : ""}
      </ExplainLead>

      <ExplainSection title="This case">
        {b.rows.length === 0 ? (
          <p>
            No findings were raised, so the score is {b.value}
            {b.partial ? " so far" : ""}.
          </p>
        ) : (
          <>
            <ExplainTable
              caption="Points added by this case's findings, by severity"
              columns={[{ label: "Concern" }, { label: "Findings", numeric: true }, { label: "Points each", numeric: true }, { label: "Points", numeric: true }]}
              rows={b.rows.map((r) => ({
                key: r.severity,
                cells: [severityLabel(r.severity), r.findings, r.pointsEach === null ? "varies" : r.pointsEach, r.subtotal],
              }))}
              foot={[
                { key: "total", cells: ["Total", b.findingsCounted, "", b.rawTotal] },
                ...(b.capped && b.cap !== null ? [{ key: "cap", cells: [`Capped at ${b.cap}`, "", "", b.value] }] : []),
              ]}
            />
            <p className="tabular">
              {totalText(b)}
              {b.capped ? "." : `, so the score is ${b.value}.`}{" "}
              <span className="text-muted">
                ({b.findingsCounted} {plural(b.findingsCounted, "finding")} from {b.flagsBehind} {plural(b.flagsBehind, "flag")}.)
              </span>
            </p>
            {!b.consistent ? <ExplainNote>The recorded score of {b.value} does not equal the points above. It is shown as recorded.</ExplainNote> : null}
          </>
        )}
        {b.partial ? (
          <ExplainNote>
            This score is partial: some checks or pages did not run, so it is <strong>at least {b.value}</strong> and the real score may be higher.
            {c.score && c.score.partial_reasons.length > 0 ? ` (${c.score.partial_reasons.map(humanisePartialReason).join("; ")}.)` : ""}
          </ExplainNote>
        ) : null}
      </ExplainSection>

      <ExplainSection title="Bands">
        {loading ? <Loading /> : null}
        {method ? (
          <ExplainTable caption="Score bands" columns={[{ label: "Band" }, { label: "Score", numeric: true }]} rows={bandRows(method, b.band, b.partial)} />
        ) : unavailable ? (
          <>
            <MethodUnavailable />
            <p>
              This case is in the <strong>{bandLabel(b.band, b.partial)}</strong> band.
            </p>
          </>
        ) : null}
      </ExplainSection>

      <ExplainNote>A high score means stronger or more concerns, not proof of fraud.</ExplainNote>

      <TechnicalDetail>
        <p>
          Score = the sum of the points of every finding{method ? `, limited to ${method.score.cap}` : ""}. A hard stop does not change the score; it only blocks the fast lane.
        </p>
        {method ? <p>{method.score.counting}</p> : null}
        <ExplainFacts
          items={[
            { label: "Ruleset", value: <span className="num text-xs">{method?.ruleset_version ?? c.provenance?.ruleset_version ?? "not recorded"}</span> },
            { label: "Recorded score", value: <span className="num text-xs">{b.value}</span> },
            ...(method ? [{ label: "Weights", value: <span className="num text-xs">{SEVERITY_ORDER.map((s) => `${s}=${method.score.weights[s]}`).join(" ")}</span> }] : []),
          ]}
        />
        {c.findings.length > 0 ? (
          <ul className="space-y-0.5">
            {c.findings.map((f) => (
              <li key={f.id} className="num break-words text-xs">
                {f.id} · {f.severity} · {f.weight} · {f.group_key}
              </li>
            ))}
          </ul>
        ) : null}
      </TechnicalDetail>
    </>
  );
}

/** The "?" for a case's risk score. */
export function ExplainScore({ c, className }: { c: CaseDetail; className?: string }) {
  return (
    <Explain label={SCORE_LABEL} title={SCORE_LABEL} className={className}>
      <ScoreExplainer c={c} />
    </Explain>
  );
}
