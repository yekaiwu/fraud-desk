"use client";

import { VERDICT_LABELS } from "@/lib/constants";
import { amlRoutedFindings, effectiveCoverage, evaluateRequirements, hardStopFindings, laneInfo } from "@/lib/explain";
import { formatRatio } from "@/lib/format";
import { bandLabel, HARD_STOP_LABEL, laneDescription, laneLabel, LANES, plural, useCaseLabel as caseTypeLabel } from "@/lib/labels";
import { useCheckName } from "@/lib/useCheckNames";
import { useExplainMethod } from "@/lib/useExplain";
import type { CaseDetail, ExplainMethod, Finding } from "@/lib/types";
import { suggestedVerdict } from "../case/VerdictForm";
import { Explain } from "./Explain";
import { ExplainFacts, ExplainLead, ExplainNote, ExplainSection, ExplainTable, Loading, MethodUnavailable, TechnicalDetail, Tick } from "./parts";

export const NEXT_STEP_LABEL = "How the suggested next step was reached";
export const BLOCKS_LABEL = "Why this blocks the fast lane";
export const LANES_LABEL = "What the lanes mean";

/** The fast-lane list with a tick or cross for this case. */
export function FastLaneChecklist({ c, method }: { c: CaseDetail; method: ExplainMethod }) {
  const reqs = evaluateRequirements(c, method);
  return (
    <ul className="space-y-1.5" aria-label="What the fast lane requires">
      {reqs.map((r) => (
        <li key={r.text} className="flex items-start gap-2">
          <span className="mt-0.5">
            <Tick ok={r.met} />
          </span>
          <span className="min-w-0">
            <span className="text-ink">{r.text}</span>
            {r.detail ? <span className="tabular block text-sm text-muted">{r.detail}</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Hard-stop checks by their plain names; the ones behind this case's findings are marked. */
function HardStopList({ method, mine }: { method: ExplainMethod; mine: Set<string> }) {
  const nameOf = useCheckName();
  return (
    <ul className="grid gap-x-3 gap-y-0.5 text-sm sm:grid-cols-2">
      {method.hard_stop_rules.map((r) => (
        <li key={r} title={r} className={mine.has(r) ? "font-semibold text-ink" : undefined}>
          {nameOf(r)}
          {mine.has(r) ? <span className="font-normal text-muted"> (this case)</span> : null}
        </li>
      ))}
    </ul>
  );
}

function stopRules(stops: Finding[]): Set<string> {
  return new Set(stops.flatMap((f) => f.rules));
}

/** The suggested next step, step by step, with this case's facts. */
export function NextStepExplainer({ c }: { c: CaseDetail }) {
  const { method, loading, unavailable } = useExplainMethod();
  const stops = hardStopFindings(c);
  const aml = amlRoutedFindings(c, method);
  const eff = effectiveCoverage(c.coverage);
  const verdict = suggestedVerdict(c);
  const info = laneInfo(method, c.lane);
  const partial = c.partial || !!c.score?.partial;
  const rows = [
    { key: "type", cells: ["Case type", caseTypeLabel(c.use_case)] },
    { key: "band", cells: ["Risk band", c.score ? `${bandLabel(c.score.band, partial)}, score ${partial ? "at least " : ""}${c.score.value}` : "No score yet"] },
    {
      key: "stops",
      cells: [HARD_STOP_LABEL, stops.length === 0 ? "None of the findings" : `${stops.length} ${plural(stops.length, "finding")}`],
    },
    {
      key: "cov",
      cells: [
        "Checks completed",
        c.coverage
          ? `${formatRatio(c.coverage.ratio)}${eff !== null && c.coverage.pages_unchecked > 0 ? `, ${formatRatio(eff)} counting pages not reviewed` : ""}${method ? `; the fast lane needs ${formatRatio(method.coverage.fast_lane_min_coverage)}` : ""}`
          : "Not counted yet",
      ],
    },
    { key: "lane", cells: ["Lane", c.lane ? laneLabel(c.lane) : "None yet"], strong: true },
    { key: "verdict", cells: ["Suggested outcome", verdict ? VERDICT_LABELS[verdict] : "None suggested"], strong: true },
  ];
  return (
    <>
      <ExplainLead>
        The tool looks at the type of case, the risk band, whether any finding blocks the fast lane and how much of the case could be checked, and puts the case in a lane. The lane suggests an outcome. It is a suggestion only.
      </ExplainLead>
      <ExplainSection title="This case">
        <ExplainTable caption="How the suggestion was reached for this case" columns={[{ label: "Step" }, { label: "This case" }]} rows={rows} />
        {c.lane === "aml" && c.use_case !== "aml" && aml.length > 0 ? (
          <p>
            It is in the AML lane although the case is {caseTypeLabel(c.use_case)}, because {aml.length === 1 ? "this finding comes" : "these findings come"} from a check that routes a case to AML investigation: {aml.map((f) => f.title).join("; ")}.
          </p>
        ) : null}
        {info ? <p>{info.how}</p> : c.lane ? <p>{laneDescription(c.lane)}</p> : null}
        {!verdict && c.lane ? <p>No outcome is suggested: the risk is low and the case is not in the fast lane, so nothing stands out. Look through the documents and decide.</p> : null}
      </ExplainSection>
      <ExplainSection title="Fast lane, for this case">
        {loading ? <Loading /> : null}
        {method ? <FastLaneChecklist c={c} method={method} /> : unavailable ? <MethodUnavailable /> : null}
      </ExplainSection>
      <ExplainNote>You decide; nothing is approved or rejected automatically.</ExplainNote>
      <TechnicalDetail>
        <ExplainFacts
          items={[
            { label: "Lane", value: <span className="num text-xs">{c.lane ?? "none"}</span> },
            { label: "Lane outcome", value: <span className="num text-xs">{info?.suggested_verdict ?? "not available"}</span> },
            ...(method ? [{ label: "Fast-lane minimum", value: <span className="num text-xs">{method.coverage.fast_lane_min_coverage}</span> }] : []),
            { label: "Blocking rules", value: <span className="num break-words text-xs">{[...stopRules(stops)].join(", ") || "none"}</span> },
          ]}
        />
      </TechnicalDetail>
    </>
  );
}

export function ExplainNextStep({ c, className }: { c: CaseDetail; className?: string }) {
  return (
    <Explain label={NEXT_STEP_LABEL} title="Suggested next step" className={className}>
      <NextStepExplainer c={c} />
    </Explain>
  );
}

// ---------------------------------------------------------------- "Blocks the fast lane"

/** Why one finding blocks the fast lane, and what the fast lane needs. */
export function BlocksFastLaneExplainer({ c, finding }: { c: CaseDetail; finding: Finding }) {
  const { method, loading, unavailable } = useExplainMethod();
  const mine = new Set(finding.rules);
  return (
    <>
      <ExplainLead>
        The fast lane is for low-risk cases that were checked thoroughly. Some kinds of finding are serious enough that a case cannot go there, whatever its score. This finding is one of them. It does not change the score.
      </ExplainLead>
      <ExplainSection title="The fast lane needs, for this case">
        {loading ? <Loading /> : null}
        {method ? <FastLaneChecklist c={c} method={method} /> : unavailable ? <MethodUnavailable /> : null}
      </ExplainSection>
      {method ? (
        <ExplainSection title="Checks that block it">
          <HardStopList method={method} mine={mine} />
        </ExplainSection>
      ) : null}
      <ExplainNote>You decide; nothing is approved or rejected automatically.</ExplainNote>
      <TechnicalDetail>
        <p className="num break-words text-xs">
          {finding.id} · {finding.rules.join(", ")} · {finding.severity}
        </p>
      </TechnicalDetail>
    </>
  );
}

export function ExplainBlocksFastLane({ c, finding, className }: { c: CaseDetail; finding: Finding; className?: string }) {
  return (
    <Explain label={BLOCKS_LABEL} title={HARD_STOP_LABEL} className={className}>
      <BlocksFastLaneExplainer c={c} finding={finding} />
    </Explain>
  );
}

// ---------------------------------------------------------------- the worklist: lanes

/** What each lane is, the fast lane's conditions and what "Routine review" means. No case is involved. */
export function LanesExplainer() {
  const { method, loading, unavailable } = useExplainMethod();
  return (
    <>
      <ExplainLead>Every analysed case sits in one lane. The lane says which kind of review it needs and what outcome the tool would suggest. A person always decides.</ExplainLead>
      <ExplainSection title="The lanes">
        <dl className="space-y-2">
          {LANES.map((l) => {
            const info = laneInfo(method, l);
            return (
              <div key={l}>
                <dt className="font-semibold text-ink">{laneLabel(l)}</dt>
                <dd className="text-sm">{info?.how ?? laneDescription(l)}</dd>
              </div>
            );
          })}
          <div>
            <dt className="font-semibold text-ink">Routine review</dt>
            <dd className="text-sm">A label on a case whose risk is low and that has no high concern. It stays in its queue and a person still reviews it.</dd>
          </div>
        </dl>
      </ExplainSection>
      <ExplainSection title="What the fast lane requires">
        {loading ? <Loading /> : null}
        {method ? (
          <ul className="list-disc space-y-0.5 pl-5">
            {method.fast_lane.requires.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        ) : unavailable ? (
          <MethodUnavailable />
        ) : null}
      </ExplainSection>
      <ExplainNote>You decide; nothing is approved or rejected automatically.</ExplainNote>
    </>
  );
}

export function ExplainLanes({ className }: { className?: string }) {
  return (
    <Explain label={LANES_LABEL} title="Lanes" className={className}>
      <LanesExplainer />
    </Explain>
  );
}
