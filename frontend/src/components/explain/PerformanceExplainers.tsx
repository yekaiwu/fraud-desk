"use client";

import { summarise } from "@/lib/effectiveness";
import { formatRatio, formatSeconds } from "@/lib/format";
import { plural } from "@/lib/labels";
import type { EffectivenessResponse, RuleEffectiveness } from "@/lib/types";
import { Explain } from "./Explain";
import { ExplainLead, ExplainNote, ExplainSection } from "./parts";

/** One sentence, inside every performance explainer, on what these figures can and cannot say. */
const CAVEAT = "These figures measure what reviewers decided, not whether a flag was truly fraud.";

export type TileTopic = "decided" | "fired" | "share" | "median";
export type ColumnTopic = "times_fired" | "decisions" | "dismissed" | "time_to_decide" | "ran_skipped";

type Entry = { title: string; lead: string; thisView: (d: EffectivenessResponse) => string };

const TILE: Record<TileTopic, Entry> = {
  decided: {
    title: "Cases decided",
    lead: "A case is decided once a reviewer has recorded a verdict on it. Cases still being reviewed are not counted.",
    thisView: (d) => `${d.cases_decided} of ${d.cases_total} ${plural(d.cases_total, "case")} in this view have a recorded verdict.`,
  },
  fired: {
    title: "Checks that fired",
    lead: "A check has fired when it raised at least one flag in the cases in this view. A check that ran and found nothing has not fired.",
    thisView: (d) => {
      const s = summarise(d.rules);
      return `${s.fired} of ${s.registered} ${plural(s.registered, "check")} fired, raising ${s.flagsRaised} ${plural(s.flagsRaised, "flag")} between them.`;
    },
  },
  share: {
    title: "Confirmed vs dismissed",
    lead: "Each flag a reviewer decides on is marked a real concern (confirmed) or not a concern (dismissed). The share confirmed is confirmed divided by confirmed plus dismissed. Flags still open, or waiting for a second approval, are left out.",
    thisView: (d) => {
      const s = summarise(d.rules);
      return s.decisions === 0 ? "No flags have been decided yet." : `${s.confirmed} confirmed and ${s.dismissed} dismissed, so ${formatRatio(s.confirmedShare ?? 0)} confirmed.`;
    },
  },
  median: {
    title: "Median time to decide",
    lead: "For each decided case, the time from the analysis being created to the verdict being recorded. The median is the middle value: half the cases were decided faster, half slower.",
    thisView: (d) => (d.median_seconds_to_verdict === null ? "No case has been decided yet." : `${formatSeconds(d.median_seconds_to_verdict)} across ${d.cases_decided} decided ${plural(d.cases_decided, "case")}.`),
  },
};

const COLUMN: Record<ColumnTopic, { title: string; lead: string }> = {
  times_fired: { title: "Times fired", lead: "How many flags this check raised, and in how many cases. One case can contain several flags from the same check." },
  decisions: { title: "What analysts decided", lead: "How the flags from this check were decided: confirmed as a real concern, dismissed as not a concern, waiting for a second reviewer, or not decided yet." },
  dismissed: { title: "Dismissed", lead: "Dismissed flags divided by confirmed plus dismissed flags. It is left blank until at least one flag from this check has been decided." },
  time_to_decide: { title: "Time to decide", lead: "The median, over flags that were confirmed or dismissed, of the time from the analysis being created to the deciding entry. A dismissal that needed a second approval is timed to the second approval." },
  ran_skipped: { title: "Ran / skipped", lead: "In how many cases this check ran, and in how many it did not (skipped because a document it needs was missing, or stopped with an error)." },
};

export function TileExplainer({ topic, data }: { topic: TileTopic; data: EffectivenessResponse }) {
  const e = TILE[topic];
  return (
    <>
      <ExplainLead>{e.lead}</ExplainLead>
      <ExplainSection title="In this view">
        <p className="tabular">{e.thisView(data)}</p>
      </ExplainSection>
      <ExplainNote>{CAVEAT}</ExplainNote>
    </>
  );
}

export function ExplainTile({ topic, data }: { topic: TileTopic; data: EffectivenessResponse }) {
  return (
    <Explain label={`What “${TILE[topic].title}” means`} title={TILE[topic].title}>
      <TileExplainer topic={topic} data={data} />
    </Explain>
  );
}

export function ExplainColumn({ topic, rules }: { topic: ColumnTopic; rules: RuleEffectiveness[] }) {
  const e = COLUMN[topic];
  const s = summarise(rules);
  const view =
    topic === "times_fired"
      ? `${s.flagsRaised} ${plural(s.flagsRaised, "flag")} raised by ${s.fired} ${plural(s.fired, "check")} in this view.`
      : topic === "dismissed" || topic === "decisions"
        ? s.decisions === 0
          ? "No flags have been decided yet."
          : `${s.confirmed} confirmed and ${s.dismissed} dismissed across all checks.`
        : null;
  return (
    <Explain label={`What “${e.title}” means`} title={e.title}>
      <ExplainLead>{e.lead}</ExplainLead>
      {view ? (
        <ExplainSection title="In this view">
          <p className="tabular">{view}</p>
        </ExplainSection>
      ) : null}
      <ExplainNote>{CAVEAT}</ExplainNote>
    </Explain>
  );
}
