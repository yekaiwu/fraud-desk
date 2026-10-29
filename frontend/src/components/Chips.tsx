import clsx from "clsx";
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, Loader2, Minus, Zap } from "lucide-react";
import { LANE_LABELS, LOW_COVERAGE_RATIO } from "@/lib/constants";
import { formatRatio } from "@/lib/format";
import type { Band, CaseStatus, Coverage, Lane, Severity, UseCase } from "@/lib/types";

const chip = "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold leading-5 whitespace-nowrap";

const BAND_STYLE: Record<Band, string> = {
  high: "border-sev-high/40 bg-red-50 text-sev-high",
  medium: "border-sev-medium/40 bg-amber-50 text-sev-medium",
  low: "border-border bg-card text-sev-low",
};
const BAND_TEXT: Record<Band, string> = { high: "High", medium: "Medium", low: "Low" };

/** Band as text first (never colour alone). Partial scores read "Medium (partial)". */
export function BandChip({ band, partial }: { band: Band; partial?: boolean }) {
  const Icon = band === "high" ? AlertOctagon : band === "medium" ? AlertTriangle : CheckCircle2;
  return (
    <span className={clsx(chip, BAND_STYLE[band])} data-band={band}>
      <Icon aria-hidden className="h-3.5 w-3.5" />
      {BAND_TEXT[band]}
      {partial ? " (partial)" : ""}
    </span>
  );
}

const SEV_STYLE: Record<Severity, string> = {
  high: "border-sev-high/40 bg-red-50 text-sev-high",
  medium: "border-sev-medium/40 bg-amber-50 text-sev-medium",
  low: "border-border bg-card text-sev-low",
  info: "border-sev-info/30 bg-blue-50 text-sev-info",
};
export function SeverityBadge({ severity }: { severity: Severity }) {
  const Icon = severity === "high" ? AlertOctagon : severity === "medium" ? AlertTriangle : severity === "low" ? Minus : Info;
  return (
    <span className={clsx(chip, SEV_STYLE[severity])}>
      <Icon aria-hidden className="h-3.5 w-3.5" />
      {severity === "info" ? "Info" : severity[0].toUpperCase() + severity.slice(1)}
    </span>
  );
}

const LANE_TIP: Partial<Record<Lane, string>> = { fast: "Low risk, high coverage, no hard stops. A person still confirms." };
export function LaneChip({ lane }: { lane: Lane }) {
  return (
    <span
      className={clsx(chip, lane === "fast" ? "border-emerald-700/30 bg-emerald-50 text-emerald-900" : "border-accent/30 bg-blue-50 text-accent")}
      title={LANE_TIP[lane]}
    >
      {lane === "fast" ? <Zap aria-hidden className="h-3.5 w-3.5" /> : null}
      {LANE_LABELS[lane]}
    </span>
  );
}

export function UseCaseChip({ useCase }: { useCase: UseCase }) {
  return <span className={clsx(chip, "border-border bg-surface text-foreground")}>{useCase === "loan" ? "Loan" : useCase.toUpperCase()}</span>;
}

const STATUS_TEXT: Record<CaseStatus, string> = {
  processing: "Analysing",
  open: "Open",
  awaiting_documents: "Awaiting documents",
  closed: "Closed",
  error: "Error",
};
const STATUS_STYLE: Record<CaseStatus, string> = {
  processing: "border-sev-info/30 bg-blue-50 text-sev-info",
  open: "border-border bg-card text-foreground",
  awaiting_documents: "border-sev-medium/40 bg-amber-50 text-sev-medium",
  closed: "border-border bg-surface text-muted",
  error: "border-sev-high/40 bg-red-50 text-sev-high",
};
export function StatusChip({ status }: { status: CaseStatus }) {
  return (
    <span className={clsx(chip, STATUS_STYLE[status])}>
      {status === "processing" ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : null}
      {STATUS_TEXT[status]}
    </span>
  );
}

/** Score number with a thin bar. Partial shows "≥ n"; a case still processing shows "Analysing" and no number. */
export function ScoreBadge({ score, band, partial, analysing, large }: { score: number | null; band: Band | null; partial?: boolean; analysing?: boolean; large?: boolean }) {
  if (analysing || score === null) {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm font-medium text-sev-info">
        <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
        Analysing
      </span>
    );
  }
  const barColor = band === "high" ? "bg-sev-high" : band === "medium" ? "bg-sev-medium" : "bg-sev-low";
  return (
    <span className="inline-flex flex-col gap-1">
      <span className="inline-flex items-baseline gap-2">
        <span className={clsx("font-display font-bold tabular leading-none", large ? "text-4xl" : "text-2xl")}>
          {partial ? <span aria-label="at least">≥ </span> : null}
          {score}
        </span>
        {band ? <BandChip band={band} partial={partial} /> : null}
      </span>
      <span aria-hidden className="block h-1 w-full max-w-28 overflow-hidden rounded bg-border">
        <span className={clsx("block h-full rounded", barColor)} style={{ width: `${Math.min(100, Math.max(0, score))}%` }} />
      </span>
    </span>
  );
}

export function isLowCoverage(c: Coverage | null | undefined): boolean {
  return !!c && c.ratio < LOW_COVERAGE_RATIO;
}

export function CoverageMeter({ coverage, className }: { coverage: Coverage | null; className?: string }) {
  if (!coverage) return <span className="text-muted">-</span>;
  const low = isLowCoverage(coverage);
  return (
    <span className={clsx("inline-flex flex-col text-sm", className)}>
      <span className={clsx("tabular font-medium", low && "text-sev-medium")}>
        {coverage.ran}/{coverage.applicable} checks ({formatRatio(coverage.ratio)})
        {low ? <span className="ml-1 font-semibold">low coverage</span> : null}
      </span>
      {coverage.pages_unchecked > 0 ? <span className="text-xs text-sev-medium">+ {coverage.pages_unchecked} {coverage.pages_unchecked === 1 ? "page" : "pages"} not checked</span> : null}
    </span>
  );
}
