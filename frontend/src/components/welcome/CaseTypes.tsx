import { Banknote, Building2, Landmark, type LucideIcon } from "lucide-react";
import { ConcernBadge, LaneBadge } from "@/components/ui/Badge";
import type { Lane, Severity } from "@/lib/types";
import { Section, SectionHead } from "./parts";

const CASES: { kind: string; icon: LucideIcon; asks: string; lane: Lane; findings: [Severity, string][] }[] = [
  {
    kind: "Loan application",
    icon: Banknote,
    asks: "Do the documents support the figures in the application?",
    lane: "loan",
    findings: [
      ["high", "The printed gross profit does not equal the sum of its lines."],
      ["medium", "Revenue is far above bank deposits: deposits support only 37% of what is reported."],
      ["medium", "An invoice is marked paid, but no matching deposit appears in the statements."],
    ],
  },
  {
    kind: "Corporate onboarding (KYC)",
    icon: Building2,
    asks: "Is the company, and everyone behind it, who the file says?",
    lane: "edd",
    findings: [
      ["high", "A director named in the application is not in the company registry."],
      ["medium", "Shareholdings in the register add up to more than 100%."],
      ["low", "The company address is spelled two different ways across documents."],
    ],
  },
  {
    kind: "Source of funds (AML)",
    icon: Landmark,
    asks: "Does the money move the way the business says it does?",
    lane: "aml",
    findings: [
      ["medium", "The declared business is logistics, but the statements show crypto counterparties."],
      ["medium", "The invoice customer is not the party that paid it."],
      ["high", "Money received is passed on to one new account within a day."],
    ],
  },
];

export function CaseTypes() {
  return (
    <Section id="cases" labelledBy="cases-title">
      <SectionHead
        no="06"
        label="Three kinds of case"
        id="cases-title"
        title="Same desk, three different questions."
        intro="The case type decides which checks matter most and where a risky case is routed. The findings below are examples, in plain words, as an officer would read them."
      />
      <div className="mt-12 grid gap-6 lg:grid-cols-3">
        {CASES.map((c, i) => (
          <article key={c.kind} className={`reveal flex flex-col rounded-lg border border-rule bg-surface p-5 shadow-paper sm:p-6 ${i === 1 ? "reveal-late" : ""}`} aria-labelledby={`case-${i}`}>
            <div className="flex items-center justify-between gap-3">
              <span className="grid h-11 w-11 place-items-center rounded-md border border-brand/20 bg-brand-tint text-brand">
                <c.icon aria-hidden className="h-5 w-5" />
              </span>
              <span className="num text-xs text-muted">0{i + 1}</span>
            </div>
            <h3 id={`case-${i}`} className="mt-4 text-[1.5rem] leading-tight">
              {c.kind}
            </h3>
            <p className="mt-2 text-[1.0625rem] leading-snug text-ink-2">{c.asks}</p>
            <div className="rule-statement rule-statement-soft mt-5 pb-3">
              <p className="label-caps">Findings it might raise</p>
            </div>
            <ul className="mt-4 flex-1 space-y-4">
              {c.findings.map(([sev, text]) => (
                <li key={text} className="space-y-1.5">
                  <ConcernBadge severity={sev} />
                  <p className="text-[0.9375rem] leading-snug text-ink">{text}</p>
                </li>
              ))}
            </ul>
            <p className="mt-6 flex flex-wrap items-center gap-2 border-t border-rule pt-4 text-sm text-muted">
              Suggested route <LaneBadge lane={c.lane} />
            </p>
          </article>
        ))}
      </div>
    </Section>
  );
}
