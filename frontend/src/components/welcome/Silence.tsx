import { AlertTriangle, Check, Minus } from "lucide-react";
import { MeterBar } from "@/components/ui/MeterBar";
import { Panel } from "@/components/ui/Panel";
import { LOW_COVERAGE_RATIO } from "@/lib/constants";
import { UNCHECKED_PAGES_LABEL, coverageText, unreviewedPagesText } from "@/lib/labels";
import { NOT_EXAMINED } from "@/lib/wording";
import { DEMO_COVERAGE } from "./demo";
import { Section, SectionHead } from "./parts";

const NOT_RUN = DEMO_COVERAGE.applicable - DEMO_COVERAGE.ran;

const REASONS = [
  ["No tax filing was supplied", "so the checks that compare against it had nothing to compare."],
  ["One page could not be read", "so no check ran on it."],
  ["A statement covers 5 of 6 months", "so a month-to-month check could not complete."],
] as const;

function CaseTile({ title, ran, applicable, flags, worry }: { title: string; ran: number; applicable: number; flags: number; worry: boolean }) {
  return (
    <div className={`rounded-lg border p-4 shadow-paper sm:p-5 ${worry ? "border-brass/70 border-l-[3px] border-l-brass bg-surface" : "border-rule bg-surface"}`}>
      <p className="label-caps">{title}</p>
      <p className="mt-2 flex items-baseline gap-2 font-display text-[2rem] font-semibold leading-none text-ink">
        {flags} <span className="font-sans text-base font-medium text-ink-2">flags</span>
      </p>
      <MeterBar className="mt-4" label="Checks completed" value={ran} max={applicable} valueText={coverageText(ran, applicable)} marker={LOW_COVERAGE_RATIO} tone={worry ? "medium" : "brand"} />
      <p className="mt-3 flex items-start gap-2 text-[0.9375rem] font-medium text-ink">
        {worry ? (
          <>
            <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-sev-medium" />
            <span>Not cleared. {applicable - ran} checks did not run, so there was little to find.</span>
          </>
        ) : (
          <>
            <Check aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-sev-low" />
            <span>No concerns found, and most checks ran.</span>
          </>
        )}
      </p>
    </div>
  );
}

export function Silence() {
  return (
    <Section id="silence" tone="sunken" labelledBy="silence-title">
      <SectionHead
        no="05"
        label="Silence is not safety"
        id="silence-title"
        title="An empty result is never a clearance."
        intro="A case with no flags can mean the documents are sound. It can equally mean the checks had nothing to read. The desk always says which."
      />
      <div className="mt-12 grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-16">
        <div className="reveal">
          <p className="label-caps">What was checked on this case</p>
          <p className="mt-3 font-display font-semibold leading-[0.9] tracking-tight text-brand">
            <span className="text-[clamp(5rem,14vw,9.5rem)] tabular">{DEMO_COVERAGE.ran}</span>
            <span className="ml-3 text-[clamp(1.5rem,3.4vw,2.5rem)] text-ink-2">of {DEMO_COVERAGE.applicable} checks</span>
          </p>
          <MeterBar className="mt-6 max-w-lg" label="Checks completed" hideLabel value={DEMO_COVERAGE.ran} max={DEMO_COVERAGE.applicable} valueText={coverageText(DEMO_COVERAGE.ran, DEMO_COVERAGE.applicable)} marker={LOW_COVERAGE_RATIO} />
          <p className="mt-6 max-w-lg text-[1.0625rem] leading-relaxed text-ink-2">
            <span className="num font-medium text-ink">{NOT_RUN}</span> checks did not run. They are listed on the case with the reason, and they are never counted as a pass. The score on the case carries this coverage beside it, always.
          </p>
          <ul className="mt-5 max-w-lg space-y-2.5 text-[0.9375rem] text-ink-2">
            {REASONS.map(([head, tail]) => (
              <li key={head} className="flex items-start gap-2.5">
                <Minus aria-hidden className="mt-1 h-4 w-4 shrink-0 text-brass-deep" />
                <span>
                  <span className="font-semibold text-ink">{head}</span>, {tail}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="reveal reveal-late space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <CaseTile title="Case A" ran={41} applicable={54} flags={0} worry={false} />
            <CaseTile title="Case B" ran={12} applicable={54} flags={0} worry />
          </div>
          <p className="text-sm text-ink-2">Both cases have no flags. Only one of them has been looked at properly. The marker is the 70% line below which a case is shown as low coverage.</p>
          <Panel tone="attention" title={`${UNCHECKED_PAGES_LABEL} (1)`} eyebrow="Not cleared" headingLevel={3}>
            <p className="text-[0.9375rem] text-ink-2">
              {NOT_EXAMINED} They are not cleared; read them yourself if they matter to your decision.
            </p>
            <p className="mt-3 rounded-md border border-rule bg-paper-2/60 p-3 text-sm text-ink-2">
              <span className="font-semibold text-ink">Scanned page 2 of 3, supplier letter.</span> We could not read this page, so none of our checks ran on it. {unreviewedPagesText(1)} (of 46).
            </p>
          </Panel>
        </div>
      </div>
    </Section>
  );
}
