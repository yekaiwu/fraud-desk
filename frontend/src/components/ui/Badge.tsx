import clsx from "clsx";
import { AlertOctagon, AlertTriangle, CheckCircle2, CircleDot, Info, Loader2, Zap, type LucideIcon } from "lucide-react";
import { bandLabel, caseStatusLabel, laneLabel, severityLabel } from "@/lib/labels";
import type { Band, CaseStatus, Lane, Severity } from "@/lib/types";

/**
 * Small stamp-like label. Colour is never the only signal: every badge carries text, and the risk
 * variants carry an icon with a different shape per level.
 *
 *   <Badge variant="high">High concern</Badge>
 *   <RiskBadge band="medium" partial />   <LaneBadge lane="edd" />   <StatusBadge status="open" />
 */
export type BadgeVariant = "high" | "medium" | "low" | "info" | "lane" | "fast" | "status" | "attention" | "neutral";

const VARIANT: Record<BadgeVariant, string> = {
  high: "border-sev-high/35 bg-sev-high-tint text-sev-high",
  medium: "border-sev-medium/35 bg-sev-medium-tint text-sev-medium",
  low: "border-sev-low/35 bg-sev-low-tint text-sev-low",
  info: "border-sev-info/35 bg-sev-info-tint text-sev-info",
  lane: "border-brand/25 bg-brand-tint text-brand",
  fast: "border-sev-low/35 bg-sev-low-tint text-sev-low",
  status: "border-rule-strong/60 bg-paper-2 text-ink-2",
  attention: "border-brass/60 bg-brass-tint text-brass-deep",
  neutral: "border-rule bg-surface text-ink-2",
};

const DEFAULT_ICON: Partial<Record<BadgeVariant, LucideIcon>> = {
  high: AlertOctagon,
  medium: AlertTriangle,
  low: CheckCircle2,
  info: Info,
  fast: Zap,
  attention: CircleDot,
};

export function Badge({
  variant = "neutral",
  icon,
  className,
  children,
  title,
  spin = false,
}: {
  variant?: BadgeVariant;
  /** Overrides the default icon for the variant; pass `null` for none. */
  icon?: LucideIcon | null;
  className?: string;
  children: React.ReactNode;
  title?: string;
  /** Spins the icon (a job in progress). Motion stops under prefers-reduced-motion. */
  spin?: boolean;
}) {
  const Icon = icon === undefined ? DEFAULT_ICON[variant] : icon;
  return (
    <span
      data-variant={variant}
      title={title}
      className={clsx("inline-flex items-center gap-1 rounded-[5px] border px-2 py-0.5 text-xs font-semibold leading-5 whitespace-nowrap", VARIANT[variant], className)}
    >
      {Icon ? <Icon aria-hidden className={clsx("h-3.5 w-3.5 shrink-0", spin && "animate-spin")} /> : null}
      {children}
    </span>
  );
}

/** Whole-case risk band: "High risk", or "Low risk (partial score)". */
export function RiskBadge({ band, partial = false }: { band: Band; partial?: boolean }) {
  return (
    <Badge variant={band}>
      {bandLabel(band, partial)}
    </Badge>
  );
}

/** One finding or flag: "High concern" ... "For information". */
export function ConcernBadge({ severity }: { severity: Severity }) {
  return <Badge variant={severity}>{severityLabel(severity)}</Badge>;
}

export function LaneBadge({ lane }: { lane: Lane }) {
  return <Badge variant={lane === "fast" ? "fast" : "lane"}>{laneLabel(lane)}</Badge>;
}

const STATUS_VARIANT: Record<CaseStatus, BadgeVariant> = {
  processing: "info",
  open: "status",
  awaiting_documents: "attention",
  closed: "neutral",
  error: "high",
};

export function StatusBadge({ status }: { status: CaseStatus }) {
  const icon = status === "processing" ? Loader2 : status === "open" || status === "closed" ? null : undefined;
  return (
    <Badge variant={STATUS_VARIANT[status]} icon={status === "error" ? AlertTriangle : icon} spin={status === "processing"}>
      {caseStatusLabel(status)}
    </Badge>
  );
}
