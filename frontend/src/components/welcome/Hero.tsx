import { ArrowDown, LogIn, ArrowRight } from "lucide-react";
import { Guilloche } from "@/components/ui/Guilloche";
import { RiskGauge } from "@/components/ui/RiskGauge";
import { Stagger } from "@/components/ui/Stagger";
import { HARD_STOP_LABEL } from "@/lib/labels";
import { EDITED_CREDIT, EDITED_ROW_ID, checkRunningBalance, formatCents, sampleStatement } from "./demo";
import { DarkLink, WIDE } from "./parts";
import { entrance } from "./TopBar";

const PRINCIPLES = ["Explainable", "A human decides", "Silence is not safety", "Documents are untrusted input", "Measured, not claimed"] as const;

/** Three rows around the edited deposit, taken from the same sample statement the demo below uses. */
function HeroStatement() {
  const statement = sampleStatement(true);
  const checks = checkRunningBalance(statement);
  const at = statement.rows.findIndex((r) => r.id === EDITED_ROW_ID);
  const rows = statement.rows.slice(at - 1, at + 2);
  const bad = checks[at];
  return (
    <div className="relative">
      {/* two loose sheets underneath, to read as a stack of paper */}
      <div aria-hidden className="absolute inset-0 -rotate-[4deg] rounded-lg border border-rule bg-paper-2 shadow-paper" />
      <div aria-hidden className="absolute inset-0 rotate-[2.5deg] rounded-lg border border-rule bg-paper shadow-paper" />
      <div className="relative -rotate-[1deg] rounded-lg border border-rule bg-surface p-4 shadow-sheet sm:p-6">
        <div className="flex items-start justify-between gap-4 border-b border-ink/70 pb-3">
          <div>
            <p className="font-display text-base font-semibold leading-tight text-brand sm:text-lg">{statement.company}</p>
            <p className="mt-0.5 text-xs text-muted">{statement.account}</p>
          </div>
          <p className="label-caps text-right">
            Statement
            <br />
            {statement.period}
          </p>
        </div>
        <table className="mt-2 w-full border-collapse text-[0.8125rem] sm:text-sm">
          <thead>
            <tr className="label-caps text-left">
              <th scope="col" className="py-2 pr-2 font-semibold">Entry</th>
              <th scope="col" className="px-2 py-2 text-right font-semibold">Amount</th>
              <th scope="col" className="py-2 pl-2 text-right font-semibold">Balance</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const edited = r.id === EDITED_ROW_ID;
              const change = r.credit - r.debit;
              return (
                <tr key={r.id} className="border-t border-rule align-top">
                  <td className="py-2.5 pr-2">
                    <span className="block font-medium text-ink">{r.description}</span>
                    <span className="num block text-xs text-muted">{r.date}</span>
                  </td>
                  <td className="px-2 py-2.5 text-right">
                    <span className={edited ? "edit-ring num relative inline-block px-1.5 py-0.5 font-medium text-ink" : "num inline-block px-1.5 py-0.5 text-ink-2"}>
                      {change < 0 ? "-" : "+"}
                      {formatCents(Math.abs(change))}
                      {edited ? <span className="absolute -left-2.5 -top-3 grid h-5 min-w-5 place-items-center rounded bg-sev-high px-1 font-sans text-[0.7rem] font-bold leading-none text-surface">1</span> : null}
                    </span>
                  </td>
                  <td className="num py-2.5 pl-2 text-right text-ink">{formatCents(r.printed)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mt-3 rounded-md border border-sev-high/30 bg-sev-high-tint px-3 py-2.5 text-xs leading-relaxed text-ink-2 sm:text-[0.8125rem]">
          <p className="font-semibold text-sev-high">Running balance does not add up from this row</p>
          <p className="num mt-1 text-[0.75rem]">
            {formatCents(bad.previous)} + {formatCents(EDITED_CREDIT)} = {formatCents(bad.expected)}
            <br />
            printed: {formatCents(bad.printed)}
          </p>
        </div>
      </div>
      <div className="relative -mt-3 ml-auto w-fit max-w-full rotate-[1.5deg] sm:mr-[-0.5rem] sm:mt-2">
        <div className="flex items-center gap-4 rounded-lg border border-rule bg-surface p-3 shadow-lift sm:gap-5 sm:p-4 [&_[role=img]]:w-36 sm:[&_[role=img]]:w-44">
          <RiskGauge score={30} band="medium" />
          <div className="max-w-[9.5rem] text-xs leading-snug text-ink-2 sm:text-[0.8125rem]">
            <p className="label-caps">One finding</p>
            <p className="mt-1 font-semibold text-ink">Adds 30 to the score</p>
            <p className="mt-1">{HARD_STOP_LABEL}</p>
          </div>
        </div>
      </div>
    </div>
  );
}

export function Hero({ signedIn }: { signedIn: boolean }) {
  const { href, label } = entrance(signedIn);
  return (
    <section aria-labelledby="hero-title" className="band-dark relative overflow-hidden bg-brand">
      <Guilloche variant="band" opacity={0.16} className="absolute inset-x-0 top-0 h-72 w-full text-brass-light" />
      <Guilloche variant="rosette" opacity={0.1} className="absolute -right-52 top-1/2 h-[56rem] w-[56rem] -translate-y-1/2 text-brass-light" />
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-gradient-to-b from-brand/0 via-brand/10 to-brand/70" />
      <div className={`${WIDE} relative pb-10 pt-14 sm:pt-20 lg:pb-14 lg:pt-28`}>
        <div className="grid items-center gap-14 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)] lg:gap-10 xl:gap-20">
          <Stagger className="max-w-3xl">
            <p className="flex items-center gap-3 text-xs font-semibold uppercase tracking-[0.2em] text-brass-light">
              <span aria-hidden className="h-px w-8 bg-brass-light/70" />
              Green Link Digital Bank · Fraud Desk
            </p>
            <h1 id="hero-title" className="mt-5 font-display text-[clamp(2.6rem,5.6vw,4.75rem)] font-semibold leading-[0.96] tracking-[-0.025em] text-on-brand">
              Every document read.
              <span className="block text-brass-light">Every decision explained.</span>
            </h1>
            <p className="mt-7 max-w-xl text-lg leading-relaxed text-on-brand-muted sm:text-xl">
              Fraud Desk reads every page of a company&rsquo;s loan, onboarding or source-of-funds file, runs the checks, and shows our officers where the risk is and why. The officer decides.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <DarkLink href={href} icon={signedIn ? <ArrowRight aria-hidden className="h-4 w-4" /> : <LogIn aria-hidden className="h-4 w-4" />}>
                {label}
              </DarkLink>
              <DarkLink href="#flow" kind="outline" icon={<ArrowDown aria-hidden className="h-4 w-4" />}>
                See how it works
              </DarkLink>
            </div>
          </Stagger>
          <figure className="mx-auto w-full max-w-[34rem] pb-6 lg:mx-0 lg:justify-self-end">
            <div aria-hidden>
              <HeroStatement />
            </div>
            <figcaption className="sr-only">
              Sample bank statement with one edited deposit. 11,574.50 plus 14,000.00 does not equal the printed balance of 15,574.50, so the check raises one high concern. Risk score 30 of 100, medium.
            </figcaption>
          </figure>
        </div>
        <ul aria-label="What the desk stands for" className="mt-14 grid gap-x-8 gap-y-3 border-t border-on-brand/25 pt-5 text-xs font-semibold uppercase tracking-[0.14em] text-on-brand-muted sm:grid-cols-2 lg:mt-20 lg:grid-cols-5">
          {PRINCIPLES.map((p) => (
            <li key={p} className="flex items-center gap-2.5">
              <span aria-hidden className="h-1.5 w-1.5 shrink-0 rotate-45 bg-brass-light" />
              {p}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
