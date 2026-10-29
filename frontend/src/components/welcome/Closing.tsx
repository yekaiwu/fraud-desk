import { ArrowRight, LogIn } from "lucide-react";
import { LogoMark } from "@/components/ui/Logo";
import { DISCLAIMER } from "@/lib/constants";
import { DarkLink, WIDE } from "./parts";
import { Guilloche } from "@/components/ui/Guilloche";
import { entrance } from "./TopBar";

export function Closing({ signedIn }: { signedIn: boolean }) {
  const { href, label } = entrance(signedIn);
  return (
      <section aria-labelledby="closing-title" className="band-dark relative overflow-hidden border-t-[3px] border-brass bg-brand">
        <Guilloche variant="rosette" opacity={0.12} className="absolute -left-48 top-1/2 h-[46rem] w-[46rem] -translate-y-1/2 text-brass-light" />
        <Guilloche variant="band" opacity={0.1} className="absolute inset-x-0 bottom-0 h-40 w-full text-on-brand" />
        <div className={`${WIDE} relative py-20 text-center sm:py-28`}>
          <h2 id="closing-title" className="mx-auto max-w-3xl font-display text-[clamp(2.25rem,5.4vw,4.5rem)] font-semibold leading-[1.02] tracking-tight text-on-brand">
            See the evidence. Make the call.
          </h2>
          <p className="mx-auto mt-5 max-w-xl text-lg text-on-brand-muted">{signedIn ? "Your worklist is waiting, riskiest case first." : "Sign in to open the worklist and walk through a case end to end."}</p>
          <div className="mt-9 flex justify-center">
            <DarkLink href={href} icon={signedIn ? <ArrowRight aria-hidden className="h-4 w-4" /> : <LogIn aria-hidden className="h-4 w-4" />}>
              {label}
            </DarkLink>
          </div>
          <p className="mt-6 text-sm text-on-brand-muted">All data in this tool is fictitious.</p>
        </div>
      </section>
  );
}

export function Footer() {
  return (
    <footer className="border-t border-rule bg-paper-2/60">
      <div className={`${WIDE} flex items-start gap-3 py-5 text-xs leading-relaxed text-muted`}>
        <LogoMark tone="dark" className="mt-0.5 h-5 w-5 opacity-60" />
        <p>{DISCLAIMER}</p>
      </div>
    </footer>
  );
}
