import type { Metadata } from "next";
import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { LoginForm } from "@/components/auth/LoginForm";
import { Guilloche } from "@/components/ui/Guilloche";
import { Logo } from "@/components/ui/Logo";
import { Stagger } from "@/components/ui/Stagger";
import { DISCLAIMER } from "@/lib/constants";

export const metadata: Metadata = { title: "Sign in", robots: { index: false, follow: false } };

const PRINCIPLES = [
  ["See why", "Each case shows its risk and the reasons, on the page they came from."],
  ["Know what to do", "Cases are ranked, so the riskiest work comes first."],
  ["Explain it later", "Every decision is recorded with a reason you can defend."],
] as const;

export default function LoginPage() {
  return (
    <div className="grid flex-1 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
      <section aria-label="About Fraud Desk" className="band-dark relative overflow-hidden bg-brand px-6 py-8 sm:px-12 lg:flex lg:min-h-dvh lg:flex-col lg:justify-between lg:px-16 lg:py-14">
        <Guilloche variant="rosette" opacity={0.12} className="absolute -bottom-40 -right-40 h-[44rem] w-[44rem] text-brass-light" />
        <Guilloche variant="band" opacity={0.1} className="absolute inset-x-0 top-0 h-48 w-full text-on-brand" />
        <div className="pointer-events-none absolute inset-y-0 right-0 hidden w-[3px] bg-brass lg:block" />
        <Stagger className="relative flex flex-col gap-8 lg:gap-0 lg:contents">
          <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
            <Logo tone="light" />
            <Link href="/welcome" className="inline-flex min-h-11 items-center gap-1.5 rounded-md px-2 text-sm font-semibold text-brass-light underline-offset-4 hover:underline">
              How it works
              <ArrowRight aria-hidden className="h-4 w-4" />
            </Link>
          </div>
          <div className="max-w-xl lg:my-auto lg:py-16">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-brass-light">Staff sign-in</p>
            <p className="mt-3 font-display text-[2rem] font-semibold leading-[1.1] tracking-tight text-on-brand sm:text-5xl">
              Every document read.
              <br />
              Every decision explained.
            </p>
            <p className="mt-4 max-w-md text-base text-on-brand-muted lg:mt-6 lg:text-lg">
              Fraud Desk helps our credit and compliance officers see which company cases carry risk, why, and what to do next.
            </p>
            <ul className="mt-8 hidden space-y-4 lg:block">
              {PRINCIPLES.map(([title, text]) => (
                <li key={title} className="flex gap-4 border-t border-on-brand/20 pt-4">
                  <span className="w-44 shrink-0 font-display text-lg font-semibold text-on-brand">{title}</span>
                  <span className="text-[0.9375rem] text-on-brand-muted">{text}</span>
                </li>
              ))}
            </ul>
          </div>
          <p className="hidden max-w-md text-xs leading-relaxed text-on-brand-muted lg:block">{DISCLAIMER}</p>
        </Stagger>
      </section>
      <section aria-label="Sign in" className="flex items-center justify-center px-4 py-10 sm:px-8 lg:py-16">
        <div className="w-full max-w-md">
          <LoginForm />
          <p className="mt-6 text-center text-xs text-muted">All data in this tool is fictitious.</p>
        </div>
      </section>
    </div>
  );
}
