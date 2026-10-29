import { Section, SectionHead } from "./parts";

const LIMITS: [string, string][] = [
  ["It does not decide for you.", "It ranks, recommends and explains. Approving, referring or rejecting is always a person’s act, with a reason on the record."],
  ["It cannot prove a document is real.", "It can only say that no check found a problem. A clean result means these checks passed, nothing more."],
  ["It does not detect fully AI-generated documents.", "Nor can it judge whether a signature is real. It makes no claim to do either."],
  ["It does not file suspicious transaction reports.", "Escalating to AML records the suspicion. Whether to file stays with compliance, outside this tool."],
  ["Reviewer names are typed, not proven.", "Anyone can type any name. A second approver is a different name, not a different proven identity."],
  ["This demo runs on fictitious data only.", "Every company, person and figure on this page and in the tool is invented."],
  ["The thresholds are calibrated on synthetic data.", "Checks and cut-offs need validation on the bank’s own documents before anyone relies on them."],
];

export function Limits() {
  return (
    <Section id="limits" tone="sunken" labelledBy="limits-title">
      <SectionHead
        no="09"
        label="What it does not do"
        id="limits-title"
        title="Honest limits, stated up front."
        intro="A tool that hides what it cannot do is more dangerous than one that is slow. These are the boundaries, in plain words."
      />
      <ol className="mt-12 grid gap-x-14 gap-y-0 lg:grid-cols-2">
        {LIMITS.map(([head, text], i) => (
          <li key={head} className="reveal flex gap-5 border-t border-rule-strong/60 py-6">
            <span className="num w-7 shrink-0 pt-1 text-sm text-brass-deep">{String(i + 1).padStart(2, "0")}</span>
            <div>
              <h3 className="text-[1.3125rem] leading-snug">{head}</h3>
              <p className="mt-1.5 text-[0.9375rem] leading-relaxed text-ink-2">{text}</p>
            </div>
          </li>
        ))}
      </ol>
    </Section>
  );
}
