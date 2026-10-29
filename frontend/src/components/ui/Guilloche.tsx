import clsx from "clsx";

/**
 * Banknote-style guilloche line work as an inline SVG (no image request, so it works under default-src 'self').
 * It takes its colour from `currentColor` and is drawn at very low opacity: it is texture, never content.
 * Always aria-hidden.
 *
 *   <Guilloche variant="band" className="absolute inset-0 h-full w-full text-on-brand" />
 *   <Guilloche variant="rosette" opacity={0.1} className="h-72 w-72 text-brand" />
 */
export type GuillocheVariant = "band" | "rosette";

const f = (n: number) => n.toFixed(1);

/** Interlaced sine families, like the fine wave bands on a banknote. Deterministic: same markup on server and client. */
function buildBand(): string[] {
  const lines: string[] = [];
  const width = 1200;
  const mid = 80;
  for (let k = 0; k < 24; k++) {
    let d = "";
    for (let x = 0; x <= width; x += 8) {
      const swell = 0.5 + 0.5 * Math.cos(x * 0.0049 + k * 0.17);
      const y = mid + (22 + 26 * swell) * Math.sin(x * 0.0165 + k * 0.33) * Math.cos(x * 0.0021 - k * 0.09);
      d += `${x === 0 ? "M" : "L"}${f(x)} ${f(y)}`;
    }
    lines.push(d);
  }
  return lines;
}

/** Hypotrochoid rosettes plus rotated ellipses: the "seal" in Ledger & Seal. */
function buildRosette(): { curves: string[]; ellipseAngles: number[] } {
  const hypo = (R: number, r: number, dist: number, turns: number) => {
    let d = "";
    const steps = 720;
    for (let i = 0; i <= steps; i++) {
      const t = (i / steps) * Math.PI * 2 * turns;
      const x = (R - r) * Math.cos(t) + dist * Math.cos(((R - r) / r) * t);
      const y = (R - r) * Math.sin(t) - dist * Math.sin(((R - r) / r) * t);
      d += `${i === 0 ? "M" : "L"}${f(200 + x)} ${f(200 + y)}`;
    }
    return d + "Z";
  };
  return {
    curves: [hypo(150, 40, 46, 4), hypo(150, 56, 70, 7), hypo(112, 28, 30, 2)],
    ellipseAngles: Array.from({ length: 24 }, (_, i) => i * 7.5),
  };
}

const BAND = buildBand();
const ROSETTE = buildRosette();

export function Guilloche({ variant = "band", opacity = 0.14, className }: { variant?: GuillocheVariant; opacity?: number; className?: string }) {
  if (variant === "rosette") {
    return (
      <svg viewBox="0 0 400 400" aria-hidden focusable="false" className={clsx("pointer-events-none select-none", className)} style={{ opacity }} fill="none" stroke="currentColor" strokeWidth={0.6}>
        {ROSETTE.ellipseAngles.map((a) => (
          <ellipse key={a} cx={200} cy={200} rx={190} ry={62} transform={`rotate(${a} 200 200)`} />
        ))}
        {ROSETTE.curves.map((d, i) => (
          <path key={i} d={d} />
        ))}
        <circle cx={200} cy={200} r={196} />
        <circle cx={200} cy={200} r={186} />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 1200 160" preserveAspectRatio="xMidYMid slice" aria-hidden focusable="false" className={clsx("pointer-events-none select-none", className)} style={{ opacity }} fill="none" stroke="currentColor" strokeWidth={0.7}>
      {BAND.map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  );
}
