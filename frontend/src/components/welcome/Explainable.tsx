import { Eye, Hand, ShieldQuestion, Gauge, FileWarning, type LucideIcon } from "lucide-react";
import { EDITED_CREDIT, EDITED_ROW_ID, checkRunningBalance, formatCents, sampleStatement } from "./demo";
import { Section, SectionHead } from "./parts";
import { ScoreExplainer } from "./ScoreExplainer";

const COMMITMENTS: { icon: LucideIcon; head: string; text: string }[] = [
  { icon: Eye, head: "Explainable", text: "Every flag shows its evidence on the page it came from." },
  { icon: Hand, head: "A human decides", text: "Nothing is approved or rejected automatically." },
  { icon: ShieldQuestion, head: "Silence is not safety", text: "Coverage sits beside every score." },
  { icon: FileWarning, head: "Documents are untrusted input", text: "Text inside a file is read as data. It cannot give the system instructions." },
  { icon: Gauge, head: "Measured, not claimed", text: "How each check performs is measured on the decisions officers record, not asserted." },
];

/** A flagged figure on a page, marked the way the case page marks it. */
function EvidenceOnPage() {
  const s = sampleStatement(true);
  const i = s.rows.findIndex((r) => r.id === EDITED_ROW_ID);
  const bad = checkRunningBalance(s)[i];
  return (
    <figure className="rounded-xl border border-rule bg-surface p-4 shadow-lift sm:p-6">
      <div aria-hidden className="rounded-md border border-rule bg-paper p-3 text-[0.8125rem] sm:p-4 sm:text-sm">
        <p className="label-caps">Bank statement · page 1</p>
        <div className="mt-3 space-y-2.5">
          {[
            ["09 Sep", "Harbour Fuel Pte. Ltd.", "-3,275.50", "11,574.50"],
            ["12 Sep", "Receipt INV-2211", `+${formatCents(EDITED_CREDIT)}`, formatCents(bad.printed)],
            ["16 Sep", "Office rent", "-4,200.00", "11,374.50"],
          ].map(([d, name, amt, bal], k) => (
            <div key={d} className="grid grid-cols-[minmax(0,1fr)_auto_4.6rem] items-center gap-x-2 sm:grid-cols-[3.2rem_minmax(0,1fr)_auto_5.5rem] sm:gap-x-3">
              <span className="num hidden text-xs text-muted sm:inline">{d}</span>
              <span className="truncate text-ink-2">{name}</span>
              <span className={k === 1 ? "edit-ring num relative px-1.5 py-0.5 text-ink" : "num px-1.5 py-0.5 text-ink-2"}>
                {amt}
                {k === 1 ? <span className="absolute -left-2.5 -top-3 grid h-5 min-w-5 place-items-center rounded bg-sev-high px-1 font-sans text-[0.7rem] font-bold leading-none text-surface">1</span> : null}
              </span>
              <span className="num text-right text-ink">{bal}</span>
            </div>
          ))}
        </div>
      </div>
      <figcaption className="mt-4 text-[0.9375rem] leading-relaxed text-ink-2">
        <span className="font-semibold text-ink">Flag 1</span> on the finding list is the same marker on the page. The officer sees the figure, the rule that fired and the sum, side by side, and can mark the flag as a real concern or not a concern.
      </figcaption>
    </figure>
  );
}

export function Explainable() {
  return (
    <Section id="explain" labelledBy="explain-title">
      <SectionHead
        no="08"
        label="Explainable by design"
        id="explain-title"
        title="If it can’t be explained on the page, it isn’t a flag."
        intro="Next to every number there is a way to ask where it came from. Open the question mark on the score to see how the sample case below reaches its 45."
      />
      <div className="mt-12 grid gap-8 lg:grid-cols-2 lg:gap-10">
        <div className="reveal min-w-0">
          <EvidenceOnPage />
        </div>
        <div className="reveal reveal-late min-w-0">
          <ScoreExplainer />
          <p className="mt-4 text-sm text-ink-2">Try it: press the question mark, or tab to it and press Enter.</p>
        </div>
      </div>
      <ul className="mt-14 grid gap-x-8 gap-y-8 border-t border-rule pt-8 sm:grid-cols-2 lg:grid-cols-5">
        {COMMITMENTS.map((c) => (
          <li key={c.head} className="reveal">
            <c.icon aria-hidden className="h-5 w-5 text-brand-2" />
            <h3 className="mt-3 text-[1.0625rem] leading-snug">{c.head}</h3>
            <p className="mt-1 text-sm leading-relaxed text-ink-2">{c.text}</p>
          </li>
        ))}
      </ul>
    </Section>
  );
}
