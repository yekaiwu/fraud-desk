import { AlertTriangle, SlidersHorizontal } from "lucide-react";
import { defaultSeverity, effectiveSeverity, isCustomised, isSwitchedOff } from "@/lib/checkCatalogue";
import { severityLabel } from "@/lib/labels";
import type { RuleInfo, Severity } from "@/lib/types";
import { Badge, ConcernBadge } from "../ui/Badge";

const SHORT: Record<Severity, string> = { high: "High", medium: "Medium", low: "Low", info: "For information" };

/** "High", "Medium", "Low", "For information": the default shown in small print next to a changed severity. */
export const severityShort = (s: Severity): string => SHORT[s];

export function SwitchedOffBadge() {
  return (
    <Badge variant="status" icon={AlertTriangle} title="This check does not run. Cases show it as not completed.">
      Switched off
    </Badge>
  );
}

export function CustomisedBadge() {
  return (
    <Badge variant="lane" icon={SlidersHorizontal} title="Some settings differ from the defaults.">
      Customised
    </Badge>
  );
}

/** The badges that say how a check differs from its defaults; renders nothing for an untouched check. */
export function CheckStateBadges({ r }: { r: RuleInfo }) {
  const off = isSwitchedOff(r);
  const custom = isCustomised(r);
  if (!off && !custom) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {off ? <SwitchedOffBadge /> : null}
      {custom ? <CustomisedBadge /> : null}
    </span>
  );
}

/** The severity analyses use now, with the default in small text when it has been changed. */
export function EffectiveConcern({ r }: { r: RuleInfo }) {
  const eff = effectiveSeverity(r);
  const def = defaultSeverity(r);
  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <ConcernBadge severity={eff} />
      {eff !== def ? <span className="text-xs text-muted">default: {severityShort(def)}</span> : null}
      <span className="sr-only">{eff !== def ? ` (default is ${severityLabel(def).toLowerCase()})` : ""}</span>
    </span>
  );
}
