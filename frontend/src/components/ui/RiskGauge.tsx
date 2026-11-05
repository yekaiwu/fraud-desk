import clsx from "clsx";
import type { CSSProperties } from "react";
import { bandLabel, scoreSummary } from "@/lib/labels";
import type { Band } from "@/lib/types";

/**
 * Semicircular risk gauge: the score in large serif numerals, the band word beneath. The hero of the case
 * page. The marks on the rim are a plain 0 to 100 scale (25, 50, 75), not the band cutoffs: the band word says
 * which band the score is in, and the score explanation lists where each band starts. A partial score is a
 * floor ("at least").
 *
 *   <RiskGauge score={90} band="high" size="lg" />
 *   <RiskGauge score={20} band="low" partial size="sm" />
 *
 * Accessible name: "Risk score 90 of 100, high". The arc fills once on mount (a CSS animation, off under
 * prefers-reduced-motion).
 */
const BAND_STROKE: Record<Band, string> = { high: "stroke-sev-high", medium: "stroke-sev-medium", low: "stroke-sev-low" };
const BAND_TEXT: Record<Band, string> = { high: "text-sev-high", medium: "text-sev-medium", low: "text-sev-low" };

const R = 84;
const CX = 100;
const CY = 100;
/** The rim marks: quarters of the 0 to 100 scale. */
const SCALE_MARKS = [25, 50, 75];
/** The large gauge labels its marks, and the label at 50 sits above the arc, so it gets that much headroom. */
const HEADROOM = 18;

/** Point on the arc for a score (0 = left end, 100 = right end), radius offset by `dr`. */
function at(score: number, dr = 0): [number, number] {
  const a = Math.PI * (1 - score / 100);
  return [CX + (R + dr) * Math.cos(a), CY - (R + dr) * Math.sin(a)];
}

export function RiskGauge({ score, band, partial = false, size = "lg", className }: { score: number; band: Band; partial?: boolean; size?: "sm" | "lg"; className?: string }) {
  const value = Math.min(100, Math.max(0, Math.round(score)));
  const large = size === "lg";
  const arc = `M${CX - R} ${CY} A${R} ${R} 0 0 1 ${CX + R} ${CY}`;
  return (
    <div
      role="img"
      aria-label={scoreSummary(value, band, partial)}
      data-band={band}
      className={clsx("relative inline-block", large ? "w-60 sm:w-72" : "w-36", className)}
    >
      <svg viewBox={large ? `0 ${-HEADROOM} 200 ${112 + HEADROOM}` : "0 0 200 112"} className="block w-full overflow-visible" aria-hidden focusable="false" fill="none">
        <path d={arc} className="stroke-paper-2" strokeWidth={large ? 14 : 16} strokeLinecap="round" />
        {value > 0 ? (
          <path
            d={arc}
            pathLength={100}
            className={clsx(BAND_STROKE[band], "[animation:sweep_0.9s_cubic-bezier(0.2,0.7,0.2,1)_both]")}
            style={{ "--sweep-from": value, strokeDasharray: `${value} 200` } as CSSProperties}
            strokeWidth={large ? 14 : 16}
            strokeLinecap="round"
          />
        ) : null}
        {SCALE_MARKS.map((t) => {
          const [x1, y1] = at(t, 12);
          const [x2, y2] = at(t, large ? 20 : 19);
          const [lx, ly] = at(t, 29);
          return (
            <g key={t}>
              <line x1={x1} y1={y1} x2={x2} y2={y2} className="stroke-rule-strong" strokeWidth={1.5} />
              {large ? (
                <text x={lx} y={ly + 3} textAnchor="middle" className="fill-muted" style={{ font: "500 8.5px var(--font-mono)" }}>
                  {t}
                </text>
              ) : null}
            </g>
          );
        })}
        {large
          ? [0, 100].map((t) => {
              const [x, y] = at(t);
              return (
                <text key={t} x={x} y={y + 16} textAnchor="middle" className="fill-muted" style={{ font: "500 9px var(--font-mono)" }}>
                  {t}
                </text>
              );
            })
          : null}
      </svg>
      {/* `bottom` is a share of the box, and the large box is taller by the headroom: 7% of the old height is 6% of the new */}
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center leading-none" style={{ bottom: large ? "6%" : "10%" }}>
        <span className={clsx("font-display font-semibold tabular text-ink", large ? "text-[3.75rem] tracking-tight" : "text-[2.1rem]")}>
          {partial ? <span className={clsx("mr-0.5 font-sans font-medium text-ink-2", large ? "text-2xl" : "text-base")}>≥</span> : null}
          {value}
        </span>
        <span className={clsx("mt-1 font-sans font-semibold uppercase tracking-[0.1em]", BAND_TEXT[band], large ? "text-[0.8rem]" : "text-[0.6875rem]")}>
          {bandLabel(band)}
        </span>
        {partial && large ? <span className="mt-1 font-sans text-[0.7rem] font-medium text-muted">Partial score</span> : null}
      </div>
    </div>
  );
}
