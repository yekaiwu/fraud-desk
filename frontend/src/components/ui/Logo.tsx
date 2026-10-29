import clsx from "clsx";
import { useId } from "react";

/**
 * Green Link Digital Bank mark: two interlocking chain links that together form a "G", plus the wordmark and
 * the product name. Original artwork, drawn in code (no image request).
 *
 *   <Logo tone="light" />            on the deep-green band
 *   <Logo tone="dark" showProduct={false} />   on paper
 *   <LogoMark className="h-10 w-10" />
 */
export function LogoMark({ tone = "light", className }: { tone?: "light" | "dark"; className?: string }) {
  const id = useId();
  const main = tone === "light" ? "#f6f3ec" : "#0f3d2e";
  const accent = tone === "light" ? "#e3c27f" : "#b8893b";
  // Link A is the body of the G: a ring open at the upper right. Link B is a short link that hooks the end of the ring and
  // forms the crossbar. B passes over A at the bottom crossing and under A at the top crossing, so they read as chained.
  const ring = "M31.8 11.1 A17 17 0 1 0 38 19.2";
  return (
    <svg viewBox="0 0 48 48" aria-hidden focusable="false" className={clsx("shrink-0", className)} fill="none" strokeLinecap="round" strokeLinejoin="round">
      <defs>
        <clipPath id={`${id}-over`}>
          <rect x="33" y="14" width="10" height="9" />
        </clipPath>
      </defs>
      <path d={ring} stroke={main} strokeWidth="5" />
      <rect x="19" y="20.5" width="26" height="10" rx="5" stroke={accent} strokeWidth="4.5" />
      <path d={ring} stroke={main} strokeWidth="5" clipPath={`url(#${id}-over)`} />
    </svg>
  );
}

export function Logo({ tone = "light", showProduct = true, className }: { tone?: "light" | "dark"; showProduct?: boolean; className?: string }) {
  const light = tone === "light";
  return (
    <span className={clsx("inline-flex items-center gap-3", className)}>
      <LogoMark tone={tone} className="h-9 w-9" />
      <span className="flex flex-col leading-none">
        <span className={clsx("font-display text-[1.125rem] font-semibold tracking-tight", light ? "text-on-brand" : "text-brand")}>Green Link</span>
        <span className={clsx("mt-1 text-[0.625rem] font-semibold uppercase tracking-[0.2em]", light ? "text-on-brand-muted" : "text-muted")}>Digital Bank</span>
      </span>
      {showProduct ? (
        <>
          <span aria-hidden className={clsx("ml-1 hidden h-7 w-px min-[400px]:block", light ? "bg-on-brand/25" : "bg-rule-strong")} />
          <span className={clsx("hidden text-sm font-semibold min-[400px]:block", light ? "text-brass-light" : "text-brass-deep")}>Fraud Desk</span>
        </>
      ) : null}
    </span>
  );
}
