import clsx from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";
import { Guilloche } from "@/components/ui/Guilloche";

/** Page container (a copy of the shell's: the shell is a client module and cannot export values to server components). */
export const WIDE = "mx-auto w-full max-w-[1240px] px-4 sm:px-6 lg:px-10";

type Tone = "paper" | "sunken" | "dark";

/**
 * One full-width band of the page. Dark bands carry guilloche; every band is a labelled region
 * (`aria-labelledby` points at its h2).
 */
export function Section({ id, tone = "paper", labelledBy, className, children }: { id?: string; tone?: Tone; labelledBy: string; className?: string; children: ReactNode }) {
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={clsx(
        "relative",
        tone === "dark" && "band-dark overflow-hidden bg-brand",
        tone === "sunken" && "border-y border-rule bg-paper-2",
        tone === "paper" && "bg-paper",
        className,
      )}
    >
      {tone === "dark" ? (
        <>
          <Guilloche variant="band" opacity={0.09} className="absolute inset-x-0 top-0 h-40 w-full text-on-brand" />
          <Guilloche variant="rosette" opacity={0.07} className="absolute -bottom-52 -right-40 h-[40rem] w-[40rem] text-brass-light" />
        </>
      ) : null}
      <div className={clsx(WIDE, "relative py-16 sm:py-20 lg:py-28")}>{children}</div>
    </section>
  );
}

/** Serial-number style label: "No. 03". */
export function SerialLabel({ no, children, dark = false }: { no: string; children: ReactNode; dark?: boolean }) {
  return (
    <p className={clsx("flex items-center gap-3 text-xs font-semibold uppercase tracking-[0.18em]", dark ? "text-brass-light" : "text-brass-deep")}>
      <span className="num tracking-[0.1em]">No. {no}</span>
      <span aria-hidden className={clsx("h-px w-8", dark ? "bg-brass-light/60" : "bg-brass")} />
      <span>{children}</span>
    </p>
  );
}

/** Editorial section head: serial label, large serif title, short intro on the right, double rule underneath. */
export function SectionHead({ no, label, id, title, intro, dark = false }: { no: string; label: string; id: string; title: ReactNode; intro?: ReactNode; dark?: boolean }) {
  return (
    <div className={clsx("reveal rule-statement pb-8", dark && "rule-on-dark")}>
      <SerialLabel no={no} dark={dark}>
        {label}
      </SerialLabel>
      <div className="mt-4 grid gap-5 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] lg:items-end lg:gap-14">
        <h2 id={id} className={clsx("font-display text-[clamp(2rem,4.6vw,3.6rem)] font-semibold leading-[1.04] tracking-tight", dark && "text-on-brand")}>
          {title}
        </h2>
        {intro ? <p className={clsx("max-w-xl text-[1.0625rem] leading-relaxed sm:text-lg", dark ? "text-on-brand-muted" : "text-ink-2")}>{intro}</p> : null}
      </div>
    </div>
  );
}

const DARK_BASE =
  "inline-flex min-h-12 items-center justify-center gap-2 rounded-md border px-6 text-base font-semibold leading-none whitespace-nowrap transition-colors duration-150 active:translate-y-px";

/**
 * Link styled as a button for the deep-green band. The kit's primary button is green and would vanish there,
 * so "solid" is a paper button and "outline" a hairline one. Focus ring is light brass (the band sets --ring).
 */
export function DarkLink({ href, kind = "solid", icon, className, children }: { href: string; kind?: "solid" | "outline"; icon?: ReactNode; className?: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className={clsx(
        DARK_BASE,
        kind === "solid" ? "border-surface bg-surface text-brand shadow-lift hover:border-brass-light hover:bg-brass-tint" : "border-on-brand/45 text-on-brand hover:border-on-brand hover:bg-on-brand/10",
        className,
      )}
    >
      {icon}
      {children}
    </Link>
  );
}
